import contextlib
import io
import random
import re
import sqlite3
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from library.innovation_catalog import (
    Innovation,
    InnovationCatalog,
    InnovationCategoryRule,
    InnovationEra,
)
from library.config_import import load_all_csvs_into_sqlite
from library.simulation_innovation import (
    INNOVATION_PROPENSITY_THRESHOLD,
    _spread_polity_knowledge,
    innovation_candidate_allowed,
    portable_innovation_score_for_region,
    seed_starting_innovations_for_save,
    update_innovation_era_state_for_save,
)
from library.simulation_incidents import KNOWLEDGE_PROPENSITY_THRESHOLD
from library.polity import TerritoryOpenRow
from library.world_save import append_simulation_event_rows, ensure_checkpoint_schema
from utils.util_parse_inventions_wiki import clean_wiki_markup, normalize_date_text


def _innovation(
    innovation_id: str,
    *,
    category: str = "craft",
    domain: str = "craft",
    era_id: str = "medieval",
    history_year: int = 1000,
    rank: int = 1,
    starter_prevalence: float = 0.5,
) -> Innovation:
    return Innovation(
        innovation_id=innovation_id,
        source_id="src",
        source_link="source:1",
        source_title=innovation_id,
        analogue_name=innovation_id.replace("_", " ").title(),
        category=category,
        domain=domain,
        era_id=era_id,
        history_year=history_year,
        history_year_from=history_year,
        history_year_to=history_year,
        rank=rank,
        spreadability=0.6,
        complexity=0.4,
        starter_prevalence=starter_prevalence,
        prerequisite_ids=(),
        curation_status="active",
    )


class InnovationTimelineTests(unittest.TestCase):
    def test_parser_normalizes_ago_bce_century_and_markup(self) -> None:
        self.assertEqual(
            normalize_date_text("1.75 Mya - 150 kya")[:4],
            (-1747974, -147974, -947974, "range"),
        )
        self.assertEqual(
            normalize_date_text("10,000 BC - 9000 BC")[:4],
            (-10000, -9000, -9500, "range"),
        )
        self.assertEqual(
            normalize_date_text("6th millennium BC")[:4],
            (-6000, -5001, -5500, "millennium"),
        )
        self.assertEqual(
            normalize_date_text("2nd century BC")[:4],
            (-200, -101, -150, "century"),
        )
        cleaned = clean_wiki_markup(
            "[[Control of fire by early humans|control of fire]] and "
            "[[cooking]]<ref>noise</ref> {{cite web|x=y}}"
        )
        self.assertEqual(cleaned, "control of fire and cooking")

    def test_catalog_ignores_unreviewed_and_inactive_rows(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "config.sqlite"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE innovation_eras (
                    era_id TEXT, sort_order TEXT, history_year_from TEXT,
                    history_year_to TEXT, advancement_threshold TEXT, notes TEXT
                );
                CREATE TABLE innovation_category_rules (
                    category TEXT, max_rank_jump TEXT, max_log_gap TEXT,
                    base_discovery_chance TEXT, spread_multiplier TEXT,
                    polity_spread_multiplier TEXT, wealth_weight TEXT, notes TEXT
                );
                CREATE TABLE innovations (
                    innovation_id TEXT, source_id TEXT, source_link TEXT,
                    source_title TEXT, analogue_name TEXT, category TEXT, domain TEXT,
                    era_id TEXT, history_year TEXT, history_year_from TEXT,
                    history_year_to TEXT, rank TEXT, spreadability TEXT, complexity TEXT,
                    starter_prevalence TEXT, prerequisite_ids TEXT,
                    curation_status TEXT, notes TEXT
                );
                """
            )
            conn.executemany(
                "INSERT INTO innovations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        "active_item",
                        "src",
                        "source:1",
                        "Active Item",
                        "Active Item",
                        "craft",
                        "craft",
                        "medieval",
                        "1000",
                        "1000",
                        "1000",
                        "1",
                        "0.5",
                        "0.4",
                        "0.5",
                        "",
                        "active",
                        "",
                    ),
                    (
                        "draft_item",
                        "src",
                        "source:2",
                        "Draft Item",
                        "Draft Item",
                        "craft",
                        "craft",
                        "medieval",
                        "1001",
                        "1001",
                        "1001",
                        "2",
                        "0.5",
                        "0.4",
                        "0.5",
                        "",
                        "unreviewed",
                        "",
                    ),
                ],
            )
            conn.commit()
            conn.close()

            catalog = InnovationCatalog.load(db)
            self.assertEqual([i.innovation_id for i in catalog.active_innovations()], ["active_item"])

    def test_checked_in_catalog_has_curated_curation_balance(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "config.sqlite"
            with contextlib.redirect_stdout(io.StringIO()):
                load_all_csvs_into_sqlite(db)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            try:
                all_rows = conn.execute(
                    """
                    SELECT innovation_id, analogue_name, curation_status, notes,
                           prerequisite_ids, history_year
                    FROM innovations
                    """
                ).fetchall()
                active_rows = conn.execute(
                    """
                    SELECT innovation_id, analogue_name, curation_status, notes,
                           prerequisite_ids, history_year
                    FROM innovations
                    WHERE curation_status IN ('active', 'reviewed', 'seed')
                    """
                ).fetchall()
                prereq_rows = conn.execute(
                    """
                    SELECT count(1) AS n
                    FROM innovations
                    WHERE trim(coalesce(prerequisite_ids, '')) <> ''
                    """
                ).fetchone()
                status_rows = conn.execute(
                    """
                    SELECT curation_status, count(1) AS n
                    FROM innovations
                    GROUP BY curation_status
                    """
                ).fetchall()
            finally:
                conn.close()
            catalog = InnovationCatalog.load(db)

        statuses = {str(row["curation_status"]): int(row["n"]) for row in status_rows}
        by_id = {str(row["innovation_id"]): row for row in all_rows}
        runtime_statuses = {"active", "reviewed", "seed"}
        self.assertGreaterEqual(int(prereq_rows["n"]), 300)
        self.assertEqual(statuses.get("active", 0), 0)
        self.assertGreaterEqual(statuses.get("reviewed", 0), 500)
        self.assertGreaterEqual(statuses.get("unreviewed", 0), 30)
        self.assertFalse(any("auto-generated" in str(row["notes"]) for row in active_rows))
        self.assertFalse(any("needs row-level analogue review" in str(row["notes"]) for row in active_rows))
        parser_artifact = re.compile(
            r"\b(invented|developed|patented|first|demonstrated|commercially|called|launched|pioneered|approved)\b",
            re.IGNORECASE,
        )
        self.assertFalse(any(parser_artifact.search(str(row["analogue_name"])) for row in active_rows))
        for row in all_rows:
            for prereq_id in [part.strip() for part in str(row["prerequisite_ids"] or "").split(";") if part.strip()]:
                self.assertIn(prereq_id, by_id)
                self.assertIn(str(by_id[prereq_id]["curation_status"]), runtime_statuses)
                self.assertLessEqual(int(by_id[prereq_id]["history_year"]), int(row["history_year"]))
        self.assertLessEqual(max(item.rank for item in catalog.active_innovations()), 10)
        self.assertIsNone(
            catalog.innovation_by_id("openai_demonstrated_an_artificial_intelligence_model_called_gpt_3")
        )
        self.assertIsNone(catalog.innovation_by_id("charles_babbage_2"))
        printing_press = catalog.innovation_by_id("printing_press")
        self.assertIsNotNone(printing_press)
        self.assertEqual(set(printing_press.prerequisite_ids), {"movable_type", "paper"})
        steam_engine = catalog.innovation_by_id("thomas_newcomen")
        self.assertIsNotNone(steam_engine)
        self.assertEqual(steam_engine.analogue_name, "atmospheric mine engines")
        microprocessor = catalog.innovation_by_id("single_chip_microprocessor_the_intel_4004_is_invented")
        self.assertIsNotNone(microprocessor)
        self.assertEqual(microprocessor.category, "computing")
        early_military = catalog.innovation_by_id("schoningen_spears")
        self.assertIsNotNone(early_military)
        self.assertGreaterEqual(early_military.starter_prevalence, 0.22)
        steam_hammer = catalog.innovation_by_id("james_nasmyth_invents_the_steam_hammer")
        self.assertIsNotNone(steam_hammer)
        self.assertEqual(steam_hammer.analogue_name, "steam hammers")
        self.assertEqual(steam_hammer.prerequisite_ids, ("thomas_newcomen",))
        web = catalog.innovation_by_id("world_wide_web_is_invented")
        self.assertIsNotNone(web)
        self.assertEqual(web.analogue_name, "hypertext web protocols")
        self.assertEqual(web.prerequisite_ids, ("transmission_control_program",))
        wheelbarrow = catalog.innovation_by_id("wheelbarrow")
        self.assertIsNotNone(wheelbarrow)
        self.assertEqual(wheelbarrow.analogue_name, "single-wheel handcarts")
        self.assertEqual(wheelbarrow.category, "transport")
        ramjet = catalog.innovation_by_id("fr_maurice_roy")
        self.assertIsNotNone(ramjet)
        self.assertEqual(ramjet.analogue_name, "subsonic ramjets")
        quartz_clock = catalog.innovation_by_id("quartz_clock")
        self.assertIsNotNone(quartz_clock)
        self.assertEqual(quartz_clock.prerequisite_ids, ("crystal_oscillator_is_invented_by_alexander_m",))
        liquid_rocket = catalog.innovation_by_id("robert_h")
        self.assertIsNotNone(liquid_rocket)
        self.assertEqual(liquid_rocket.prerequisite_ids, ("rocket",))
        ballistic_rocket = catalog.innovation_by_id("v_2_rocket")
        self.assertIsNotNone(ballistic_rocket)
        self.assertEqual(ballistic_rocket.prerequisite_ids, ("robert_h",))

    def test_startup_seeds_only_eligible_common_innovations(self) -> None:
        @dataclass
        class Settlement:
            settlement_id: str
            region_id: str
            status: str = "active"

        class FakeContext:
            db_path = Path("unused.sqlite")
            save_db_path = Path("unused-save.sqlite")
            world = "default"
            simulation_start_year = 0
            history_equivalent_start_year = 1000
            settlements_by_id = {"r1:s1": Settlement("r1:s1", "r1")}

            def get_historical_year(self, simulation_year: int | None = None) -> int:
                return self.history_equivalent_start_year + int(simulation_year or 0)

        eras = (InnovationEra("medieval", 5, 500, 1499, 1),)
        catalog = InnovationCatalog(
            (
                _innovation("eligible", history_year=900, starter_prevalence=0.5),
                _innovation("too_late", history_year=1500, starter_prevalence=0.5),
                _innovation("too_rare", history_year=800, starter_prevalence=0.05),
            ),
            eras,
            {"craft": InnovationCategoryRule("craft", 2, 7.5, 0.5, 1.0, 1.2, 0.02)},
        )
        with tempfile.TemporaryDirectory() as td:
            save = Path(td) / "save.sqlite"
            conn = sqlite3.connect(save)
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            count = seed_starting_innovations_for_save(conn, FakeContext(), catalog, 0)
            self.assertGreater(count, 0)
            rows = conn.execute(
                "SELECT DISTINCT innovation_id FROM simulation_innovation_knowledge"
            ).fetchall()
            self.assertEqual({str(row["innovation_id"]) for row in rows}, {"eligible"})
            conn.close()

    def test_military_rank_gate_blocks_leapfrogging(self) -> None:
        base = InnovationCatalog.load(Path("__missing_innovation_catalog__.sqlite"))
        catalog = InnovationCatalog(
            (
                _innovation(
                    "adjacent",
                    category="military",
                    domain="warfare",
                    era_id="medieval",
                    history_year=1010,
                    rank=2,
                ),
                _innovation(
                    "leap",
                    category="military",
                    domain="warfare",
                    era_id="medieval",
                    history_year=1020,
                    rank=4,
                ),
            ),
            base.eras,
            base.category_rules,
        )
        rule = catalog.category_rule("military")
        common = {
            "known_ids": {"rank_one"},
            "category_frontiers": {"military": (1, 1000)},
            "effective_era_rank": catalog.era_rank("medieval"),
            "historical_year": 1025,
            "rule": rule,
            "catalog": catalog,
        }
        self.assertTrue(innovation_candidate_allowed(catalog.innovations[0], **common))
        self.assertFalse(innovation_candidate_allowed(catalog.innovations[1], **common))

    def test_innovation_propensity_gate_allows_ordinary_knowledge_actors(self) -> None:
        self.assertLessEqual(INNOVATION_PROPENSITY_THRESHOLD, KNOWLEDGE_PROPENSITY_THRESHOLD)

    def test_same_polity_diffusion_reaches_multi_region_polity(self) -> None:
        class FakeContext:
            gov_territory_rows = (
                TerritoryOpenRow(1, "region", "r1", 1),
                TerritoryOpenRow(1, "region", "r2", 1),
            )
            settlements_by_id = {}

        base = InnovationCatalog.load(Path("__missing_innovation_catalog__.sqlite"))
        catalog = InnovationCatalog(
            (
                _innovation(
                    "shared_craft",
                    category="craft",
                    domain="craft",
                    era_id="medieval",
                    history_year=1000,
                    rank=1,
                ),
            ),
            base.eras,
            base.category_rules,
        )
        with tempfile.TemporaryDirectory() as td:
            save = Path(td) / "save.sqlite"
            conn = sqlite3.connect(save)
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            conn.executemany(
                "INSERT INTO simulation_region_lookup (region_id) VALUES (?)",
                [("r1",), ("r2",)],
            )
            conn.execute(
                """
                INSERT INTO simulation_innovation_knowledge (
                    innovation_id, innovation_name, category, domain, era_id,
                    scope_kind, scope_key, status, adoption_score,
                    first_known_year, latest_known_year, source_kind,
                    polity_id, details_json, created_at, updated_at
                )
                VALUES (
                    'shared_craft', 'Shared Craft', 'craft', 'craft', 'medieval',
                    'polity', 'polity:1', 'adopted', 1.0,
                    1, 1, 'test', 1, '{}', 'now', 'now'
                )
                """
            )

            spread = _spread_polity_knowledge(conn, FakeContext(), catalog, 2)
            rows = conn.execute(
                """
                SELECT scope_kind, source_kind, count(1) AS n
                FROM simulation_innovation_knowledge
                WHERE source_kind = 'same_polity_diffusion'
                GROUP BY scope_kind, source_kind
                """
            ).fetchall()
            conn.close()

        self.assertEqual(spread, 2)
        self.assertEqual([(str(row["scope_kind"]), int(row["n"])) for row in rows], [("region", 2)])

    def test_innovation_event_writes_attribution_knowledge_and_domain_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            save = Path(td) / "save.sqlite"
            conn = sqlite3.connect(save)
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            event_ids = append_simulation_event_rows(
                conn,
                "default",
                [
                    (
                        12,
                        "knowledge_culture",
                        {
                            "creator_person_id": 101,
                            "patron_person_id": 202,
                            "settlement_id": "r1:s1",
                            "region_id": "r1",
                            "polity_id": 7,
                            "innovation_id": "water_clock",
                            "innovation_analogue_name": "Water Clock",
                            "innovation_category": "science",
                            "innovation_era_id": "classical",
                            "knowledge_domain": "scholarship",
                            "historical_year": -300,
                            "novelty_value": 0.2,
                            "consequences": {
                                "knowledge_state": {
                                    "domain": "scholarship",
                                    "state_delta": 0.09,
                                },
                                "innovation_adoption": {
                                    "innovation_id": "water_clock",
                                    "adoption_score": 0.5,
                                },
                            },
                        },
                    )
                ],
            )
            self.assertEqual(len(event_ids), 1)
            discovery = conn.execute(
                "SELECT * FROM simulation_innovation_discoveries_readable"
            ).fetchone()
            self.assertEqual(str(discovery["innovation_id"]), "water_clock")
            self.assertEqual(int(discovery["discoverer_person_id"]), 101)
            knowledge = conn.execute(
                "SELECT scope_kind, innovation_id FROM simulation_innovation_knowledge_readable"
            ).fetchall()
            self.assertEqual(
                {str(row["scope_kind"]) for row in knowledge},
                {"settlement", "region", "polity"},
            )
            domain = conn.execute(
                "SELECT domain_score FROM simulation_domain_states_readable"
            ).fetchone()
            self.assertGreater(float(domain["domain_score"]), 0.0)
            conn.close()

    def test_era_state_advances_after_threshold_adoptions(self) -> None:
        eras = (
            InnovationEra("paleolithic", 0, -999999, -10001, 1),
            InnovationEra("neolithic", 1, -10000, -3301, 2),
        )
        catalog = InnovationCatalog((), eras, {})
        with tempfile.TemporaryDirectory() as td:
            save = Path(td) / "save.sqlite"
            conn = sqlite3.connect(save)
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            conn.execute(
                """
                INSERT INTO simulation_innovation_knowledge (
                    innovation_id, innovation_name, category, domain, era_id,
                    scope_kind, scope_key, adoption_score,
                    first_known_year, latest_known_year, source_kind,
                    details_json, created_at, updated_at
                )
                VALUES
                    ('i1','I1','craft','craft','neolithic','region','region:1',0.6,1,1,'test','{}','now','now'),
                    ('i2','I2','craft','craft','neolithic','region','region:1',0.6,1,1,'test','{}','now','now')
                """
            )
            updates = update_innovation_era_state_for_save(conn, catalog, 1)
            self.assertEqual(updates, 1)
            state = conn.execute(
                "SELECT era_id, era_rank FROM simulation_innovation_era_state"
            ).fetchone()
            self.assertEqual(str(state["era_id"]), "neolithic")
            self.assertEqual(int(state["era_rank"]), 1)
            conn.close()

    def test_portable_innovation_score_does_not_saturate_from_few_common_rows(self) -> None:
        class FakeContext:
            def __init__(self, save_db_path: Path) -> None:
                self.save_db_path = save_db_path

        with tempfile.TemporaryDirectory() as td:
            save = Path(td) / "save.sqlite"
            conn = sqlite3.connect(save)
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            conn.execute("INSERT INTO simulation_region_lookup (region_id) VALUES ('r1')")
            region_key = int(
                conn.execute(
                    "SELECT region_key FROM simulation_region_lookup WHERE region_id = 'r1'"
                ).fetchone()["region_key"]
            )
            conn.executemany(
                """
                INSERT INTO simulation_innovation_knowledge (
                    innovation_id, innovation_name, category, domain, era_id,
                    scope_kind, scope_key, adoption_score,
                    first_known_year, latest_known_year, source_kind,
                    region_key, details_json, created_at, updated_at
                )
                VALUES (?, ?, 'craft', 'navigation', 'paleolithic',
                        'region', ?, 1.0, 1, 1, 'test', ?, '{}', 'now', 'now')
                """,
                [(f"portable_{idx}", f"Portable {idx}", f"region:{region_key}", region_key) for idx in range(10)],
            )
            conn.commit()
            conn.close()

            score = portable_innovation_score_for_region(FakeContext(save), "r1")

        self.assertGreater(score, 0.0)
        self.assertLess(score, 0.5)


if __name__ == "__main__":
    random.seed(1)
    unittest.main()
