import random
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
from library.simulation_innovation import (
    innovation_candidate_allowed,
    seed_starting_innovations_for_save,
    update_innovation_era_state_for_save,
)
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


if __name__ == "__main__":
    random.seed(1)
    unittest.main()
