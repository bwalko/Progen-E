"""Maritime mercantile trade-network scoring and outpost behavior."""

from __future__ import annotations

import sqlite3
import shutil
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.geography import Route
from library.person import Person
from library.settlements import SettlementState, make_settlement_id
from library.simulation_context import SimulationContext
from library.simulation_trade_networks import (
    score_port_network,
    simulation_trade_networks_annual_tick,
)
from library.world_save import (
    append_simulation_event_rows,
    checkpoint_simulation_to_save,
    ensure_checkpoint_schema_for_file,
    try_load_simulation_checkpoint,
)


class TestSimulationTradeNetworks(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._template_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._template_config = Path(cls._template_dir.name) / "config.sqlite"
        load_all_csvs_into_sqlite(cls._template_config)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template_dir.cleanup()

    def _context(self, root: Path, *, salt: int = 31) -> SimulationContext:
        cfg = root / "config.sqlite"
        sav = root / "save.sqlite"
        shutil.copyfile(self._template_config, cfg)
        ensure_checkpoint_schema_for_file(sav)
        ctx = SimulationContext(
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
        self._install_fast_settlement_factories(ctx)
        return ctx

    def _bare_settlement(self, ctx: SimulationContext, region_id: str) -> SettlementState:
        rid = (region_id or "").strip()
        existing = [sid for sid, st in ctx.settlements_by_id.items() if st.region_id == rid]
        seq = len(existing) + 1
        st = SettlementState(
            region_id=rid,
            region_display_name=rid.replace("_", " ").title(),
            settlement_id=make_settlement_id(rid, seq),
            site_slot=seq,
            founded_sim_year=int(ctx.current_year or ctx.simulation_start_year),
            status="active",
        )
        ctx.settlements_by_id[st.settlement_id] = st
        ctx.rebuild_settlement_region_index()
        return st

    def _install_fast_settlement_factories(self, ctx: SimulationContext) -> None:
        def ensure(region_id: str) -> SettlementState:
            active = ctx.active_settlements_in_region(region_id)
            if active:
                return active[0]
            return self._bare_settlement(ctx, region_id)

        def create(region_id: str) -> SettlementState:
            return self._bare_settlement(ctx, region_id)

        ctx.ensure_active_settlement_for_region = ensure  # type: ignore[method-assign]
        ctx.create_additional_active_settlement = create  # type: ignore[method-assign]

    def _add_person(
        self,
        ctx: SimulationContext,
        *,
        region_id: str,
        settlement_id: str,
        name: str,
        gender: str = "Male",
        age: int = 30,
        job: str = "merchant",
        father_id: int | None = None,
        mother_id: int | None = None,
    ):
        return ctx.add_person(
            person=Person(
                first_name=name,
                last_name="Port",
                gender=gender,
                ethnic="Human",
                species="Human",
                birthyear=1000 - int(age),
                birthplace_region_id=region_id,
                birthplace_settlement_id=settlement_id,
                current_settlement_id=settlement_id,
                job=job,
                min_fertility_age=18,
                job_prosperity_01=0.85,
            ),
            is_founder=False,
            father_id=father_id,
            mother_id=mother_id,
        )

    def _seed_trade_port(
        self,
        ctx: SimulationContext,
        region_id: str,
        *,
        count: int = 30,
        clear_events: bool = True,
    ) -> SettlementState:
        st = ctx.ensure_active_settlement_for_region(region_id)
        st.prosperity_pool = 2.0
        st.market_pull = 1.0
        jobs = (
            "merchant",
            "sailor",
            "dock worker",
            "scribe",
            "accountant",
            "carpenter",
            "artisan",
        )
        for i in range(int(count)):
            self._add_person(
                ctx,
                region_id=region_id,
                settlement_id=st.settlement_id,
                name=f"{region_id}_{i}",
                gender="Male" if i % 2 == 0 else "Female",
                job=jobs[i % len(jobs)],
            )
        ctx.sync_settlement_resident_counts()
        if clear_events:
            ctx._pending_simulation_events.clear()
        return st

    def _seed_domain(
        self,
        ctx: SimulationContext,
        *,
        region_id: str,
        settlement_id: str,
        domain: str = "navigation",
        delta: float = 0.30,
    ) -> None:
        with closing(sqlite3.connect(ctx.save_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            append_simulation_event_rows(
                conn,
                ctx.world,
                [
                    (
                        999,
                        "knowledge_culture",
                        {
                            "creator_person_id": 1,
                            "incident_kind": f"{domain}_probe",
                            "knowledge_domain": domain,
                            "settlement_id": settlement_id,
                            "region_id": region_id,
                            "consequences": {
                                "knowledge_state": {
                                    "domain": domain,
                                    "state_delta": float(delta),
                                }
                            },
                        },
                    )
                ],
                created_at="2026-01-01T00:00:00+00:00",
            )
            conn.commit()

    def test_port_regions_score_higher_than_inland_regions(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            port = ctx.ensure_active_settlement_for_region("aeria_port")
            inland = ctx.ensure_active_settlement_for_region("aeria_granite_range")

            port_score = score_port_network(ctx, port.settlement_id, 1000)
            inland_score = score_port_network(ctx, inland.settlement_id, 1000)

            self.assertGreater(port_score.score, inland_score.score)
            self.assertGreater(port_score.drivers["sea_route_centrality"], 0.0)
            self.assertEqual(inland_score.drivers["sea_route_centrality"], 0.0)

    def test_disabled_sea_route_era_removes_route_component(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            port = ctx.ensure_active_settlement_for_region("aeria_port")

            enabled = score_port_network(ctx, port.settlement_id, 1000)
            disabled = score_port_network(ctx, port.settlement_id, -60000)

            self.assertGreater(enabled.drivers["sea_route_centrality"], 0.0)
            self.assertEqual(disabled.drivers["sea_route_centrality"], 0.0)
            self.assertLess(disabled.score, enabled.score)

    def test_qualifying_port_founds_outpost_and_respects_cooldown(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td), salt=41)
            mother = self._seed_trade_port(ctx, "boreas_port")
            self._seed_domain(
                ctx,
                region_id=mother.region_id,
                settlement_id=mother.settlement_id,
                domain="navigation",
            )

            simulation_trade_networks_annual_tick(ctx, 1000)
            founded = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "settlement_commercial_outpost_founded"
            ]
            self.assertEqual(len(founded), 1)
            outpost = ctx.settlements_by_id[str(founded[0]["settlement_id"])]
            self.assertEqual(outpost.founding_reason, "commercial_outpost")
            self.assertEqual(outpost.mother_settlement_id, mother.settlement_id)
            self.assertEqual(outpost.trade_network_id, mother.trade_network_id)
            self.assertEqual(outpost.autonomy_level, "dependent")

            ctx._pending_simulation_events.clear()
            simulation_trade_networks_annual_tick(ctx, 1001)
            founded_again = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "settlement_commercial_outpost_founded"
            ]
            self.assertFalse(founded_again)

    def test_world_year_cap_limits_outpost_foundings(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td), salt=42)
            for rid in ("aeria_port", "boreas_port", "cyrene_port"):
                st = self._seed_trade_port(ctx, rid, clear_events=False)
                self._seed_domain(
                    ctx,
                    region_id=st.region_id,
                    settlement_id=st.settlement_id,
                    domain="navigation",
                )
            ctx._pending_simulation_events.clear()

            simulation_trade_networks_annual_tick(ctx, 1000)

            founded = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "settlement_commercial_outpost_founded"
            ]
            self.assertEqual(len(founded), 2)

    def test_outposts_skip_non_coastal_and_duplicate_destinations(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td), salt=43)
            mother = self._seed_trade_port(ctx, "aeria_port")
            self._seed_domain(
                ctx,
                region_id=mother.region_id,
                settlement_id=mother.settlement_id,
                domain="navigation",
            )
            inland_route = Route(
                from_region_id="aeria_port",
                to_region_id="aeria_granite_range",
                route_type="sea",
                friction=1.0,
            )
            with patch(
                "library.simulation_trade_networks._sea_routes",
                return_value=[inland_route],
            ):
                simulation_trade_networks_annual_tick(ctx, 1000)
            founded = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "settlement_commercial_outpost_founded"
            ]
            self.assertFalse(founded)

            ctx._pending_simulation_events.clear()
            duplicate = ctx.create_additional_active_settlement("boreas_port")
            ctx.settlements_by_id[duplicate.settlement_id] = replace(
                duplicate,
                founding_reason="commercial_outpost",
                mother_settlement_id=mother.settlement_id,
                trade_network_id=mother.trade_network_id,
                autonomy_level="dependent",
            )
            with patch("library.simulation_trade_networks.PORT_NETWORK_OUTPOST_COOLDOWN_YEARS", 0):
                simulation_trade_networks_annual_tick(ctx, 1001)
            founded_duplicate = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "settlement_commercial_outpost_founded"
            ]
            self.assertFalse(founded_duplicate)

    def test_household_founder_move_includes_partner_and_dependent_minor(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td), salt=44)
            origin = ctx.ensure_active_settlement_for_region("aeria_port")
            target = ctx.ensure_active_settlement_for_region("boreas_port")
            father = self._add_person(
                ctx,
                region_id=origin.region_id,
                settlement_id=origin.settlement_id,
                name="Father",
                gender="Male",
                job="merchant",
            )
            mother = self._add_person(
                ctx,
                region_id=origin.region_id,
                settlement_id=origin.settlement_id,
                name="Mother",
                gender="Female",
                job="scribe",
            )
            ctx.add_couple(father.person_id, mother.person_id)
            child = self._add_person(
                ctx,
                region_id=origin.region_id,
                settlement_id=origin.settlement_id,
                name="Child",
                gender="Female",
                age=8,
                job="",
                father_id=father.person_id,
                mother_id=mother.person_id,
            )
            ctx._pending_simulation_events.clear()

            moved = ctx.queue_household_move_to_settlement(
                father.person_id,
                target.settlement_id,
                move_reason="unit_test_household",
                requested_year=1000,
                apply_year=1001,
            )

            self.assertEqual(set(moved), {father.person_id, mother.person_id, child.person_id})
            self.assertEqual(len(ctx.pending_settlement_moves), 3)

    def test_settlement_trade_network_fields_roundtrip_through_save_load(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root, salt=45)
            mother = ctx.ensure_active_settlement_for_region("aeria_port")
            self._add_person(
                ctx,
                region_id=mother.region_id,
                settlement_id=mother.settlement_id,
                name="Anchor",
                job="merchant",
            )
            outpost = ctx.create_additional_active_settlement("boreas_port")
            ctx.settlements_by_id[outpost.settlement_id] = replace(
                outpost,
                founding_reason="commercial_outpost",
                mother_settlement_id=mother.settlement_id,
                trade_network_id=mother.trade_network_id,
                autonomy_level="dependent",
            )
            checkpoint_simulation_to_save(ctx, full_snapshot=True)

            loaded = SimulationContext(
                db_path=root / "config.sqlite",
                save_db_path=root / "save.sqlite",
                world="default",
                simulation_start_year=1000,
                history_equivalent_start_year=1000,
                current_year=1000,
                file_store=None,
            )
            self.assertTrue(try_load_simulation_checkpoint(loaded))
            loaded_outpost = loaded.settlements_by_id[outpost.settlement_id]
            self.assertEqual(loaded_outpost.founding_reason, "commercial_outpost")
            self.assertEqual(loaded_outpost.mother_settlement_id, mother.settlement_id)
            self.assertEqual(loaded_outpost.trade_network_id, mother.trade_network_id)
            self.assertEqual(loaded_outpost.autonomy_level, "dependent")

    def test_autonomy_and_successor_events_fire_when_mother_declines(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td), salt=46)
            mother = ctx.ensure_active_settlement_for_region("aeria_port")
            ctx.settlements_by_id[mother.settlement_id] = replace(
                mother,
                status="abandoned",
                abandoned_sim_year=990,
            )
            outpost = self._seed_trade_port(ctx, "boreas_port")
            ctx.settlements_by_id[outpost.settlement_id] = replace(
                outpost,
                founding_reason="commercial_outpost",
                mother_settlement_id=mother.settlement_id,
                trade_network_id=mother.trade_network_id,
                autonomy_level="dependent",
                founded_sim_year=940,
            )
            self._seed_domain(
                ctx,
                region_id=outpost.region_id,
                settlement_id=outpost.settlement_id,
                domain="navigation",
            )
            ctx._pending_simulation_events.clear()

            simulation_trade_networks_annual_tick(ctx, 1000)

            event_types = [event_type for _year, event_type, _payload in ctx._pending_simulation_events]
            self.assertIn("settlement_outpost_autonomized", event_types)
            self.assertIn("trade_network_recentered", event_types)
            self.assertEqual(
                ctx.settlements_by_id[outpost.settlement_id].autonomy_level,
                "successor",
            )


if __name__ == "__main__":
    unittest.main()
