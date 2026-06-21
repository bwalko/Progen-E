"""Tests for generic city-state political pattern mechanics."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.person import Person
from library.polity import OfficeSeatState, PolityState, TerritoryOpenRow
from library.settlements import SettlementState, make_settlement_id
from library.simulation_city_states import (
    summarize_city_state_patterns,
    simulation_city_states_annual_tick,
)
from library.simulation_context import SimulationContext
from library.world_save import append_simulation_event_rows, ensure_checkpoint_schema_for_file
from utils.gradio_data_browser import _city_state_note_items


class TestSimulationCityStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._template_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._template_config = Path(cls._template_dir.name) / "config.sqlite"
        load_all_csvs_into_sqlite(cls._template_config)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_dir.cleanup()

    def _context(self, root: Path, *, salt: int = 17) -> SimulationContext:
        cfg = root / "config.sqlite"
        sav = root / "save.sqlite"
        shutil.copyfile(self._template_config, cfg)
        ensure_checkpoint_schema_for_file(sav)
        return SimulationContext(
            db_path=cfg,
            save_db_path=sav,
            world="default",
            simulation_start_year=1000,
            history_equivalent_start_year=1000,
            current_year=1000,
            placename_rng_salt=salt,
            checkpoint_full_snapshot_every_n_years=0,
            file_store=None,
            world_map_seed=str(salt),
        )

    def _settlement(
        self,
        ctx: SimulationContext,
        region_id: str,
        *,
        seq: int,
        display_name: str,
        prosperity: float = 1.0,
        stability: float = 0.7,
        market: float = 0.2,
        pressure: float = 0.2,
        founding_reason: str = "organic",
        mother_settlement_id: str | None = None,
        autonomy_level: str = "autonomous",
    ) -> SettlementState:
        sid = make_settlement_id(region_id, seq)
        st = SettlementState(
            region_id=region_id,
            region_display_name=region_id.replace("_", " ").title(),
            settlement_id=sid,
            display_name=display_name,
            site_slot=seq,
            founded_sim_year=int(ctx.current_year or ctx.simulation_start_year),
            status="active",
            prosperity_pool=prosperity,
            stability=stability,
            market_pull=market,
            food_pressure=pressure,
            founding_reason=founding_reason,
            mother_settlement_id=mother_settlement_id,
            trade_network_id=mother_settlement_id or sid,
            autonomy_level=autonomy_level,
        )
        ctx.settlements_by_id[sid] = st
        ctx.rebuild_settlement_region_index()
        return st

    def _add_people(
        self,
        ctx: SimulationContext,
        *,
        settlement: SettlementState,
        count: int,
    ) -> list[int]:
        ids: list[int] = []
        for i in range(count):
            rec = ctx.add_person(
                person=Person(
                    first_name=f"P{i}",
                    last_name="City",
                    gender="Male" if i % 2 == 0 else "Female",
                    ethnic="Human",
                    species="Human",
                    birthyear=970,
                    birthplace_region_id=settlement.region_id,
                    birthplace_settlement_id=settlement.settlement_id,
                    current_settlement_id=settlement.settlement_id,
                    job="merchant" if i % 3 == 0 else "laborer",
                    min_fertility_age=18,
                ),
                is_founder=False,
            )
            ids.append(int(rec.person_id))
        ctx.sync_settlement_resident_counts()
        return ids

    def _add_city_polity(
        self,
        ctx: SimulationContext,
        *,
        polity_id: int,
        settlement: SettlementState,
        name: str,
        leader_person_id: int | None = None,
        parent_polity_id: int | None = None,
    ) -> PolityState:
        pol = PolityState(
            polity_id=polity_id,
            polity_type_id="city_state",
            parent_polity_id=parent_polity_id,
            name=name,
            capital_settlement_id=settlement.settlement_id,
            founding_dynasty_id=None,
            founded_sim_year=1000,
            notes={},
        )
        ctx.gov_polities[polity_id] = pol
        ctx.gov_territory_rows.append(
            TerritoryOpenRow(
                polity_id=polity_id,
                target_kind="settlement",
                target_id=settlement.settlement_id,
                since_sim_year=1000,
            )
        )
        if leader_person_id is not None:
            seat_id = ctx.next_gov_seat_id
            ctx.next_gov_seat_id += 1
            ctx.gov_office_seats[seat_id] = OfficeSeatState(
                seat_id=seat_id,
                polity_id=polity_id,
                title_id="king_of_city",
                holder_person_id=leader_person_id,
            )
        ctx.next_gov_polity_id = max(ctx.next_gov_polity_id, polity_id + 1)
        return pol

    def _seed_two_rival_cities(self, ctx: SimulationContext) -> tuple[SettlementState, SettlementState]:
        strong = self._settlement(
            ctx,
            "delta",
            seq=1,
            display_name="Harbor City",
            prosperity=2.0,
            stability=0.82,
            market=1.0,
            pressure=1.0,
        )
        small = self._settlement(
            ctx,
            "delta",
            seq=2,
            display_name="Hill City",
            prosperity=0.6,
            stability=0.52,
            market=0.1,
            pressure=0.3,
        )
        leader = self._add_people(ctx, settlement=strong, count=30)[0]
        self._add_people(ctx, settlement=small, count=6)
        self._add_city_polity(ctx, polity_id=1, settlement=strong, name="Harbor City", leader_person_id=leader)
        self._add_city_polity(ctx, polity_id=2, settlement=small, name="Hill City")
        ctx._pending_simulation_events.clear()
        return strong, small

    def test_city_state_tick_records_league_hegemony_public_works_and_dispute(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            self._seed_two_rival_cities(ctx)

            simulation_city_states_annual_tick(ctx, 1000)

            event_types = [event_type for _year, event_type, _payload in ctx._pending_simulation_events]
            self.assertGreaterEqual(event_types.count("city_state_urban_consolidation"), 2)
            self.assertIn("city_state_public_works", event_types)
            self.assertIn("city_state_league_formed", event_types)
            self.assertIn("city_state_hegemony_declared", event_types)
            self.assertIn("city_state_resource_dispute", event_types)
            self.assertEqual(ctx.gov_alliances[0].kind, "city_state_league")
            strong_note = ctx.gov_polities[1].notes["city_state"]
            small_note = ctx.gov_polities[2].notes["city_state"]
            self.assertEqual(strong_note["autonomy_state"], "hegemon")
            self.assertEqual(small_note["hegemon_polity_id"], 1)
            self.assertIn("Latest civic work: storehouse", _city_state_note_items(strong_note))

    def test_civic_crisis_can_be_followed_by_reform(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            stressed = self._settlement(
                ctx,
                "upland",
                seq=1,
                display_name="Upland City",
                prosperity=0.5,
                stability=0.2,
                market=0.1,
                pressure=0.4,
            )
            self._add_people(ctx, settlement=stressed, count=8)
            self._add_city_polity(ctx, polity_id=1, settlement=stressed, name="Upland City")
            ctx._pending_simulation_events.clear()

            simulation_city_states_annual_tick(ctx, 1000)
            self.assertIn(
                "city_state_civic_crisis",
                [event_type for _year, event_type, _payload in ctx._pending_simulation_events],
            )
            note = ctx.gov_polities[1].notes["city_state"]
            self.assertEqual(note["unresolved_civic_crisis_reason"], "elite_faction_deadlock")

            ctx.settlements_by_id[stressed.settlement_id].stability = 0.7
            ctx.settlements_by_id[stressed.settlement_id].food_pressure = 0.4
            ctx._pending_simulation_events.clear()
            simulation_city_states_annual_tick(ctx, 1001)

            event_types = [event_type for _year, event_type, _payload in ctx._pending_simulation_events]
            self.assertIn("city_state_civic_reform", event_types)
            self.assertNotIn("unresolved_civic_crisis_reason", ctx.gov_polities[1].notes["city_state"])

    def test_city_state_events_persist_public_records_institutions_and_report_counts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            self._seed_two_rival_cities(ctx)
            simulation_city_states_annual_tick(ctx, 1000)

            with closing(sqlite3.connect(ctx.save_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                event_ids = append_simulation_event_rows(
                    conn,
                    ctx.world,
                    ctx._pending_simulation_events,
                    created_at="2026-01-01T00:00:00+00:00",
                )
                conn.commit()

                works_id = conn.execute(
                    "SELECT id FROM simulation_events WHERE event_type = 'city_state_public_works'"
                ).fetchone()["id"]
                records = {
                    str(row["record_key"]): row
                    for row in conn.execute(
                        """
                        SELECT record_key, record_type, visibility_state
                        FROM simulation_event_records
                        WHERE event_id = ?
                        """,
                        (works_id,),
                    )
                }
                institutions = conn.execute(
                    """
                    SELECT institution_type, focus_domain, founding_event_id
                    FROM simulation_institutions_readable
                    WHERE latest_event_id = ?
                    """,
                    (works_id,),
                ).fetchall()
                reputation_marks = conn.execute(
                    """
                    SELECT mark_key, reputation_axis, direction
                    FROM simulation_reputation_marks
                    WHERE source_event_id = ?
                    """,
                    (works_id,),
                ).fetchall()
                faction_rows = conn.execute(
                    """
                    SELECT memory_type, polarity
                    FROM simulation_faction_memory
                    WHERE source_event_id IN ({})
                    """.format(",".join("?" for _ in event_ids)),
                    tuple(event_ids),
                ).fetchall()
                counts = summarize_city_state_patterns(conn)

            self.assertEqual(str(records["default"]["record_type"]), "city_chronicle")
            self.assertEqual(str(records["default"]["visibility_state"]), "public_known")
            self.assertEqual(
                str(records["public_city_state_terms_unclear"]["visibility_state"]),
                "public_unknown",
            )
            self.assertEqual(str(records["public_city_state_rumor"]["visibility_state"]), "rumored")
            self.assertEqual(len(institutions), 1)
            self.assertEqual(str(institutions[0]["institution_type"]), "storehouse")
            self.assertEqual(str(institutions[0]["focus_domain"]), "civic_order")
            self.assertEqual(int(institutions[0]["founding_event_id"]), works_id)
            self.assertEqual(len(reputation_marks), 1)
            self.assertEqual(str(reputation_marks[0]["reputation_axis"]), "leadership")
            self.assertGreaterEqual(len(faction_rows), 1)
            self.assertGreaterEqual(counts["civic_public_works_or_institution"], 1)
            self.assertGreaterEqual(counts["league_or_hegemony"], 2)
            self.assertGreaterEqual(counts["rivalry_or_resource_dispute"], 1)
            self.assertGreaterEqual(counts["total_city_state_pattern_events"], 5)

    def test_commercial_outpost_city_records_colony_status(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            mother = self._settlement(ctx, "coast", seq=1, display_name="Mother City", prosperity=2.0, market=1.0)
            colony = self._settlement(
                ctx,
                "island",
                seq=1,
                display_name="Island Outpost",
                prosperity=1.5,
                stability=0.65,
                market=0.8,
                founding_reason="commercial_outpost",
                mother_settlement_id=mother.settlement_id,
                autonomy_level="dependent",
            )
            self._add_people(ctx, settlement=mother, count=12)
            self._add_people(ctx, settlement=colony, count=12)
            self._add_city_polity(ctx, polity_id=1, settlement=mother, name="Mother City")
            self._add_city_polity(ctx, polity_id=2, settlement=colony, name="Island Outpost")
            ctx._pending_simulation_events.clear()

            simulation_city_states_annual_tick(ctx, 1000)

            colony_events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "city_state_colony_status_changed"
            ]
            self.assertEqual(len(colony_events), 1)
            self.assertEqual(colony_events[0]["mother_polity_id"], 1)
            self.assertEqual(colony_events[0]["autonomy_state"], "tributary")
            note = ctx.gov_polities[2].notes["city_state"]
            self.assertEqual(note["colony_autonomy_level"], "dependent")
            self.assertEqual(note["mother_settlement_id"], mother.settlement_id)


if __name__ == "__main__":
    unittest.main()
