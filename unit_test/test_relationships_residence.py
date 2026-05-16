"""Residence movement and partner/paramour relationship APIs."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.simulation_context import SimulationContext
from library.world_save import checkpoint_simulation_to_save


class TestRelationshipsResidence(unittest.TestCase):
    def test_move_person_logs_event_and_updates_residence(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="iso",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                p = generate_person_random(simulation_context=ctx, simulation_year=1000)
                sid0 = (p.current_settlement_id or p.birthplace_settlement_id or "").strip()
                self.assertTrue(sid0)
                rec0 = ctx.add_person(person=p, is_founder=True)
                pid = rec0.person_id
                rid = (p.birthplace_region_id or "").strip()
                other = ctx.create_additional_active_settlement(rid)
                ctx.move_person_to_settlement(pid, other.settlement_id)
                rec = ctx.id_to_record[pid]
                self.assertEqual(rec.person.current_settlement_id, other.settlement_id)
                types = [t for _, t, _ in ctx._pending_simulation_events]
                self.assertIn("settlement_moved", types)

    def test_pending_settlement_move_survives_checkpoint_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="iso-pending",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                p = generate_person_random(simulation_context=ctx, simulation_year=1000)
                sid0 = (p.current_settlement_id or p.birthplace_settlement_id or "").strip()
                rec0 = ctx.add_person(person=p, is_founder=True)
                other = ctx.create_additional_active_settlement(
                    (p.birthplace_region_id or "").strip()
                )
                ctx.queue_person_move_to_settlement(
                    rec0.person_id,
                    other.settlement_id,
                    move_reason="test_deferred_move",
                    requested_year=1000,
                    apply_year=1001,
                )
                checkpoint_simulation_to_save(ctx, full_snapshot=True)

            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="iso-pending",
                world="default",
                start_year=None,
                refresh_config=False,
                flush_run_store=False,
            ) as loaded:
                self.assertEqual(len(loaded.pending_settlement_moves), 1)
                self.assertEqual(
                    loaded.id_to_record[rec0.person_id].person.current_settlement_id,
                    sid0,
                )
                loaded.apply_pending_settlement_moves(1001)
                self.assertEqual(
                    loaded.id_to_record[rec0.person_id].person.current_settlement_id,
                    other.settlement_id,
                )

    def test_dissolve_couple_clears_partner_fields(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="iso2",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                m = generate_person_random(
                    gender="Male", simulation_context=ctx, simulation_year=1000
                )
                f = generate_person_random(
                    gender="Female", simulation_context=ctx, simulation_year=1000
                )
                mr = ctx.add_person(person=m, is_founder=True)
                fr = ctx.add_person(person=f, is_founder=True)
                ctx.add_couple(mr.person_id, fr.person_id)
                self.assertEqual(
                    ctx.id_to_record[mr.person_id].person.partner_person_id, fr.person_id
                )
                ctx.dissolve_couple(mr.person_id, fr.person_id)
                self.assertIsNone(ctx.id_to_record[mr.person_id].person.partner_person_id)
                self.assertIsNone(ctx.id_to_record[fr.person_id].person.partner_person_id)

    def test_paramour_roundtrip_distinct_from_partner(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="iso3",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                people = []
                for i, g in enumerate(["Male", "Female", "Male"]):
                    p = generate_person_random(
                        gender=g,
                        age=30,
                        simulation_context=ctx,
                        simulation_year=1000,
                    )
                    people.append(ctx.add_person(person=p, is_founder=True))
                ctx.add_couple(people[0].person_id, people[1].person_id)
                ctx.add_paramour_relationship(people[1].person_id, people[2].person_id)
                self.assertEqual(ctx.id_to_record[2].person.paramour_person_id, 3)
                ctx.end_paramour_relationship(2, 3)
                self.assertIsNone(ctx.id_to_record[2].person.paramour_person_id)

    def test_paramour_rejected_when_both_under_age_floor(self) -> None:
        from dataclasses import replace

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="iso_pam",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                ctx.current_year = 1005
                m = generate_person_random(
                    gender="Male",
                    age=3,
                    simulation_context=ctx,
                    simulation_year=1005,
                )
                f = generate_person_random(
                    gender="Female",
                    age=5,
                    simulation_context=ctx,
                    simulation_year=1005,
                )
                mr = ctx.add_person(person=m, is_founder=True)
                fr = ctx.add_person(person=f, is_founder=True)
                sid = (
                    mr.person.current_settlement_id
                    or mr.person.birthplace_settlement_id
                    or ""
                ).strip()
                self.assertTrue(sid)
                fr.person = replace(fr.person, current_settlement_id=sid)
                ctx.id_to_record[fr.person_id].person = fr.person
                with self.assertRaises(ValueError):
                    ctx.add_paramour_relationship(mr.person_id, fr.person_id)

    def test_death_clears_active_relationship_and_career_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="iso4",
                world="default",
                start_year=1000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                m = generate_person_random(
                    gender="Male",
                    age=30,
                    simulation_context=ctx,
                    simulation_year=1000,
                )
                f = generate_person_random(
                    gender="Female",
                    age=30,
                    simulation_context=ctx,
                    simulation_year=1000,
                )
                x = generate_person_random(
                    gender="Male",
                    age=30,
                    simulation_context=ctx,
                    simulation_year=1000,
                )
                mr = ctx.add_person(person=m, is_founder=True)
                fr = ctx.add_person(person=f, is_founder=True)
                xr = ctx.add_person(person=x, is_founder=True)
                ctx.add_couple(mr.person_id, fr.person_id)
                ctx.add_paramour_relationship(fr.person_id, xr.person_id)
                before = ctx.id_to_record[fr.person_id].person
                ctx.id_to_record[fr.person_id].person = replace(
                    before,
                    job="judge",
                    job_assigned_year=1000,
                    job_era="medieval",
                    status_tendency="high",
                    leader_quality="strong",
                    leader_tendency="medium",
                    employment_status="employed",
                    job_lost_year=999,
                    unemployment_started_year=999,
                    last_job="scribe",
                    career_fitness_score=0.87,
                    last_birth_event_year=1000,
                )

                ctx.mark_dead({fr.person_id}, deathyear=1001)

                dead = ctx.id_to_record[fr.person_id].person
                self.assertEqual(dead.deathyear, 1001)
                self.assertIsNone(dead.current_settlement_id)
                self.assertIsNone(dead.partner_person_id)
                self.assertIsNone(dead.paramour_person_id)
                self.assertIsNone(dead.last_birth_event_year)
                self.assertIsNone(dead.job)
                self.assertIsNone(dead.job_assigned_year)
                self.assertIsNone(dead.job_era)
                self.assertIsNone(dead.status_tendency)
                self.assertIsNone(dead.leader_quality)
                self.assertIsNone(dead.leader_tendency)
                self.assertIsNone(dead.employment_status)
                self.assertIsNone(dead.job_lost_year)
                self.assertIsNone(dead.unemployment_started_year)
                self.assertIsNone(dead.last_job)
                self.assertIsNone(dead.career_fitness_score)
                self.assertIsNone(ctx.id_to_record[mr.person_id].person.partner_person_id)
                self.assertIsNone(ctx.id_to_record[xr.person_id].person.paramour_person_id)
                event_payloads = [
                    payload
                    for _year, event_type, payload in ctx._pending_simulation_events
                    if event_type == "death" and payload.get("person_id") == fr.person_id
                ]
                self.assertTrue(event_payloads)
                self.assertIn("job", event_payloads[-1]["cleared_current_state_fields"])


if __name__ == "__main__":
    unittest.main()
