"""Tests for genome-driven personal incident generation."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.simulation_context import SimulationContext
from library.simulation_incidents import (
    _murder_annual_event_cap,
    _murder_settlement_trial_count,
    knowledge_culture_propensity,
    property_crime_propensity,
    public_virtue_propensity,
    scandal_exposure_propensity,
    simulation_incidents_annual_tick,
    violent_actor_propensity,
)
from library.world_save import checkpoint_simulation_to_save


_GENOME_TRAITS = (
    "physical",
    "intellect",
    "symmetry",
    "mating drive",
    "neurochemical",
    "courage",
    "temperance",
    "patience",
    "wit",
    "friendliness",
    "modesty",
    "ambition",
    "frugality",
    "persuasion",
    "curiosity",
    "justice",
    "humility",
    "generosity",
    "empathy",
    "discipline",
    "adaptability",
    "resilience",
    "focus",
    "honesty",
    "creativity",
    "assertiveness",
    "loyalty",
    "nurturance",
    "perception",
    "civics",
)


def _genome(**overrides: float) -> dict[str, float]:
    out = {trait: 0.0 for trait in _GENOME_TRAITS}
    out.update({str(k): float(v) for k, v in overrides.items()})
    return out


VIOLENT_GENOME = _genome(
    justice=-95,
    empathy=-95,
    patience=-90,
    temperance=-80,
    courage=90,
    assertiveness=90,
    neurochemical=90,
    ambition=85,
)

PEACEFUL_GENOME = _genome()

PROPERTY_CRIMINAL_GENOME = _genome(
    justice=-95,
    honesty=-95,
    empathy=-75,
    persuasion=90,
    ambition=90,
    frugality=90,
)

SCANDAL_PRONE_GENOME = _genome(
    **{
        "mating drive": 95,
        "loyalty": -95,
        "modesty": -90,
        "honesty": -75,
        "neurochemical": 85,
        "assertiveness": 80,
        "persuasion": 75,
    }
)

PUBLIC_VIRTUE_GENOME = _genome(
    empathy=0,
    justice=0,
    nurturance=0,
    civics=0,
    honesty=0,
    courage=95,
    assertiveness=75,
    discipline=0,
    resilience=0,
    frugality=-80,
)

SELFISH_GENOME = _genome(
    empathy=-95,
    justice=-95,
    nurturance=-90,
    civics=-85,
    honesty=-80,
    courage=-80,
    discipline=-80,
    resilience=-80,
    frugality=95,
)

KNOWLEDGE_CREATOR_GENOME = _genome(
    curiosity=95,
    creativity=95,
    intellect=90,
    focus=85,
    perception=80,
    discipline=0,
    civics=70,
    wit=65,
    adaptability=0,
)

DULL_GENOME = _genome(
    curiosity=-95,
    creativity=-95,
    intellect=-90,
    focus=-85,
    perception=-80,
    discipline=-75,
    civics=-70,
    wit=-65,
    adaptability=-60,
)


class TestSimulationIncidents(unittest.TestCase):
    def _context(self, root: Path) -> SimulationContext:
        cfg = root / "config.sqlite"
        sav = root / "save.sqlite"
        load_all_csvs_into_sqlite(cfg)
        return SimulationContext.create(
            db_path=cfg,
            save_db_path=sav,
            world_id="incidents",
            world="default",
            start_year=1000,
            refresh_config=False,
            flush_run_store=False,
        )

    def _add_adult(
        self,
        ctx: SimulationContext,
        *,
        genome: dict[str, float],
        gender: str,
        settlement_id: str,
        region_id: str,
    ):
        person = generate_person_random(
            simulation_context=ctx,
            simulation_year=1000,
            age=30,
            gender=gender,
            genome=genome,
            birthplace_region_id=region_id,
            birthplace_settlement_id=settlement_id,
        )
        return ctx.add_person(person=person, is_founder=True)

    def test_murder_population_rate_helpers_scale_above_review_sample_cap(self) -> None:
        residents = [object()] * 20_000
        settlements = [("large_city", residents)]

        self.assertEqual(_murder_settlement_trial_count(residents), 24)
        self.assertGreaterEqual(_murder_annual_event_cap(settlements), 16)

    def test_violent_actor_propensity_separates_extreme_and_stable_genomes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            violent = self._add_adult(
                ctx,
                genome=VIOLENT_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            peaceful = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )

            self.assertGreater(violent_actor_propensity(violent), 0.8)
            self.assertLess(violent_actor_propensity(peaceful), 0.05)
            self.assertGreater(property_crime_propensity(violent), 0.2)

    def test_property_crime_propensity_separates_extreme_and_stable_genomes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            actor = self._add_adult(
                ctx,
                genome=PROPERTY_CRIMINAL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            peaceful = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )

            self.assertGreater(property_crime_propensity(actor), 0.75)
            self.assertLess(property_crime_propensity(peaceful), 0.05)

    def test_scandal_exposure_propensity_separates_extreme_and_stable_genomes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            actor = self._add_adult(
                ctx,
                genome=SCANDAL_PRONE_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            peaceful = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )

            self.assertGreater(scandal_exposure_propensity(actor), 0.55)
            self.assertLess(scandal_exposure_propensity(peaceful), 0.05)

    def test_public_virtue_propensity_separates_heroic_and_selfish_genomes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            hero = self._add_adult(
                ctx,
                genome=PUBLIC_VIRTUE_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            selfish = self._add_adult(
                ctx,
                genome=SELFISH_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )

            self.assertGreater(public_virtue_propensity(hero), 0.7)
            self.assertLess(public_virtue_propensity(selfish), 0.05)

    def test_knowledge_culture_propensity_separates_creator_and_dull_genomes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            creator = self._add_adult(
                ctx,
                genome=KNOWLEDGE_CREATOR_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            dull = self._add_adult(
                ctx,
                genome=DULL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )

            self.assertGreater(knowledge_culture_propensity(creator), 0.7)
            self.assertLess(knowledge_culture_propensity(dull), 0.05)

    def test_forced_murder_tick_records_event_kills_victim_and_persists_rumor(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            settlement.food_pressure = 2.0
            killer = self._add_adult(
                ctx,
                genome=VIOLENT_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            victim = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 1.0), patch(
                "library.simulation_incidents.MURDER_SETTLEMENT_CHANCE_CAP", 1.0
            ), patch("library.simulation_incidents.MURDER_PROPENSITY_THRESHOLD", 0.2), patch(
                "library.simulation_incidents.MURDER_MAX_EVENTS_PER_YEAR", 1
            ):
                simulation_incidents_annual_tick(ctx, 1001)

            murder_events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "murder"
            ]
            self.assertEqual(len(murder_events), 1)
            murder = murder_events[0]
            self.assertEqual(int(murder["killer_person_id"]), killer.person_id)
            self.assertEqual(int(murder["victim_person_id"]), victim.person_id)
            self.assertEqual(str(murder["settlement_id"]), settlement.settlement_id)
            self.assertIn(
                str(murder["incident_kind"]),
                {"murder", "predatory_murder", "rash_brawl_killing", "feud_killing"},
            )
            self.assertNotIn(victim.person_id, ctx.current_people_ids)
            self.assertEqual(ctx.id_to_record[victim.person_id].person.deathyear, 1001)

            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(root / "save.sqlite")) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT e.primary_person_id, e.secondary_person_id,
                           er.settlement_id, er.region_id, e.payload_json,
                           r.record_type, r.visibility_state, r.confidence,
                           r.public_actor_person_id, r.public_victim_person_id,
                           r.prose_variant_key
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    JOIN simulation_event_records_readable r ON r.event_id = e.id
                    WHERE e.event_type = 'murder'
                    """
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(int(row["primary_person_id"]), killer.person_id)
            self.assertEqual(int(row["secondary_person_id"]), victim.person_id)
            self.assertEqual(str(row["settlement_id"]), settlement.settlement_id)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            payload = json.loads(str(row["payload_json"]))
            self.assertEqual(int(payload["killer_person_id"]), killer.person_id)
            self.assertEqual(int(payload["victim_person_id"]), victim.person_id)
            self.assertEqual(str(row["record_type"]), "violent_crime_record")
            self.assertEqual(str(row["visibility_state"]), "rumored")
            self.assertAlmostEqual(float(row["confidence"]), 0.55)
            self.assertEqual(int(row["public_actor_person_id"]), killer.person_id)
            self.assertEqual(int(row["public_victim_person_id"]), victim.person_id)
            self.assertEqual(
                str(row["prose_variant_key"]),
                "violent_crime_record.rumored.default",
            )

    def test_forced_murder_tick_allows_population_scaled_multiple_events(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            settlement.food_pressure = 2.0
            self._add_adult(
                ctx,
                genome=VIOLENT_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            for idx in range(5):
                self._add_adult(
                    ctx,
                    genome=PEACEFUL_GENOME,
                    gender="Female" if idx % 2 else "Male",
                    settlement_id=settlement.settlement_id,
                    region_id="aeria_north",
                )
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 1.0), patch(
                "library.simulation_incidents.MURDER_SETTLEMENT_CHANCE_CAP", 1.0
            ), patch("library.simulation_incidents.MURDER_PROPENSITY_THRESHOLD", 0.2), patch(
                "library.simulation_incidents.MURDER_TARGET_PER_10K_PER_YEAR", 10000.0
            ), patch("library.simulation_incidents.MURDER_SETTLEMENT_TRIAL_POPULATION", 2), patch(
                "library.simulation_incidents.MURDER_MAX_EVENTS_PER_YEAR", 3
            ):
                simulation_incidents_annual_tick(ctx, 1001)

            murder_events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "murder"
            ]
            victim_ids = {int(event["victim_person_id"]) for event in murder_events}
            self.assertEqual(len(murder_events), 3)
            self.assertEqual(len(victim_ids), 3)
            self.assertEqual(
                sum(
                    1
                    for rec in ctx.id_to_record.values()
                    if rec.person.deathyear == 1001
                ),
                3,
            )

    def test_forced_murder_tick_skips_stable_low_risk_adults(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            a = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            b = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 1.0), patch(
                "library.simulation_incidents.MURDER_SETTLEMENT_CHANCE_CAP", 1.0
            ), patch("library.simulation_incidents.MURDER_PROPENSITY_THRESHOLD", 0.2):
                simulation_incidents_annual_tick(ctx, 1001)

            self.assertFalse(
                any(
                    event_type == "murder"
                    for _year, event_type, _payload in ctx._pending_simulation_events
                )
            )
            self.assertIn(a.person_id, ctx.current_people_ids)
            self.assertIn(b.person_id, ctx.current_people_ids)

    def test_forced_property_crime_records_nonlethal_rumor(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            settlement.food_pressure = 1.5
            perpetrator = self._add_adult(
                ctx,
                genome=PROPERTY_CRIMINAL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            target = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            target.person = replace(target.person, job_prosperity_01=0.9)
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 1.0
            ), patch("library.simulation_incidents.THEFT_SETTLEMENT_CHANCE_CAP", 1.0), patch(
                "library.simulation_incidents.THEFT_PROPENSITY_THRESHOLD", 0.2
            ), patch("library.simulation_incidents.THEFT_MAX_EVENTS_PER_YEAR", 1):
                simulation_incidents_annual_tick(ctx, 1001)

            events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "property_crime"
            ]
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(int(event["perpetrator_person_id"]), perpetrator.person_id)
            self.assertEqual(int(event["target_person_id"]), target.person_id)
            self.assertIn(
                str(event["incident_kind"]),
                {"theft", "fraud", "extortion", "hoarding_theft"},
            )
            self.assertIn(perpetrator.person_id, ctx.current_people_ids)
            self.assertIn(target.person_id, ctx.current_people_ids)

            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(root / "save.sqlite")) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT e.primary_person_id, e.secondary_person_id,
                           er.settlement_id, er.region_id, e.payload_json,
                           r.record_type, r.visibility_state, r.confidence,
                           r.public_actor_person_id, r.public_victim_person_id,
                           r.prose_variant_key
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    JOIN simulation_event_records_readable r ON r.event_id = e.id
                    WHERE e.event_type = 'property_crime'
                    """
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(int(row["primary_person_id"]), perpetrator.person_id)
            self.assertEqual(int(row["secondary_person_id"]), target.person_id)
            self.assertEqual(str(row["settlement_id"]), settlement.settlement_id)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            payload = json.loads(str(row["payload_json"]))
            self.assertEqual(
                int(payload["perpetrator_person_id"]), perpetrator.person_id
            )
            self.assertEqual(int(payload["target_person_id"]), target.person_id)
            self.assertIn("loss_value", payload)
            self.assertEqual(str(row["record_type"]), "property_crime_record")
            self.assertEqual(str(row["visibility_state"]), "rumored")
            self.assertAlmostEqual(float(row["confidence"]), 0.5)
            self.assertEqual(int(row["public_actor_person_id"]), perpetrator.person_id)
            self.assertEqual(int(row["public_victim_person_id"]), target.person_id)
            self.assertEqual(
                str(row["prose_variant_key"]),
                "property_crime_record.rumored.default",
            )

    def test_forced_property_crime_skips_stable_low_risk_adults(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 1.0
            ), patch("library.simulation_incidents.THEFT_SETTLEMENT_CHANCE_CAP", 1.0), patch(
                "library.simulation_incidents.THEFT_PROPENSITY_THRESHOLD", 0.2
            ):
                simulation_incidents_annual_tick(ctx, 1001)

            self.assertFalse(
                any(
                    event_type == "property_crime"
                    for _year, event_type, _payload in ctx._pending_simulation_events
                )
            )

    def test_forced_affair_scandal_records_rumored_household_scandal(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            spouse = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            accused = self._add_adult(
                ctx,
                genome=SCANDAL_PRONE_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            paramour = self._add_adult(
                ctx,
                genome=SCANDAL_PRONE_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            witness = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            ctx.add_couple(accused.person_id, spouse.person_id)
            ctx.add_paramour_relationship(accused.person_id, paramour.person_id)
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.SCANDAL_BASE_SETTLEMENT_CHANCE", 1.0), patch(
                "library.simulation_incidents.SCANDAL_SETTLEMENT_CHANCE_CAP", 1.0
            ), patch("library.simulation_incidents.SCANDAL_PROPENSITY_THRESHOLD", 0.1), patch(
                "library.simulation_incidents.SCANDAL_MAX_EVENTS_PER_YEAR", 1
            ):
                simulation_incidents_annual_tick(ctx, 1001)

            events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "affair_scandal"
            ]
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(int(event["accused_person_id"]), accused.person_id)
            self.assertEqual(int(event["paramour_person_id"]), paramour.person_id)
            self.assertEqual(
                int(event["betrayed_partner_person_id"]), spouse.person_id
            )
            self.assertEqual(
                [int(pid) for pid in event["betrayed_partner_person_ids"]],
                [spouse.person_id],
            )
            self.assertIn(
                int(witness.person_id),
                [int(pid) for pid in event["witness_person_ids"]],
            )
            self.assertIn(
                str(event["incident_kind"]),
                {
                    "affair_exposed",
                    "affair_witnessed",
                    "confessed_affair",
                    "double_affair_exposed",
                },
            )
            self.assertEqual(
                ctx.id_to_record[accused.person_id].person.paramour_person_id,
                paramour.person_id,
            )
            self.assertEqual(
                ctx.id_to_record[accused.person_id].person.partner_person_id,
                spouse.person_id,
            )

            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(root / "save.sqlite")) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT e.primary_person_id, e.secondary_person_id,
                           er.settlement_id, er.region_id, e.payload_json,
                           r.record_type, r.visibility_state, r.confidence,
                           r.public_actor_person_id, r.public_victim_person_id,
                           r.prose_variant_key
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    JOIN simulation_event_records_readable r ON r.event_id = e.id
                    WHERE e.event_type = 'affair_scandal'
                    """
                ).fetchone()
                roles = {
                    str(r["role"]): int(r["person_id"])
                    for r in conn.execute(
                        """
                        SELECT person_id, role
                        FROM simulation_event_people
                        WHERE event_id = (
                            SELECT id FROM simulation_events
                            WHERE event_type = 'affair_scandal'
                            LIMIT 1
                        )
                        """
                    )
                }

            self.assertIsNotNone(row)
            self.assertEqual(int(row["primary_person_id"]), accused.person_id)
            self.assertEqual(int(row["secondary_person_id"]), spouse.person_id)
            self.assertEqual(str(row["settlement_id"]), settlement.settlement_id)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            payload = json.loads(str(row["payload_json"]))
            self.assertEqual(int(payload["accused_person_id"]), accused.person_id)
            self.assertEqual(int(payload["paramour_person_id"]), paramour.person_id)
            self.assertEqual(
                int(payload["betrayed_partner_person_id"]), spouse.person_id
            )
            self.assertEqual(str(row["record_type"]), "scandal_record")
            self.assertEqual(str(row["visibility_state"]), "rumored")
            self.assertAlmostEqual(float(row["confidence"]), 0.55)
            self.assertEqual(int(row["public_actor_person_id"]), accused.person_id)
            self.assertEqual(int(row["public_victim_person_id"]), spouse.person_id)
            self.assertEqual(
                str(row["prose_variant_key"]), "scandal_record.rumored.default"
            )
            self.assertEqual(roles["accused"], accused.person_id)
            self.assertEqual(roles["betrayed_partner"], spouse.person_id)
            self.assertEqual(roles["paramour"], paramour.person_id)

    def test_forced_affair_scandal_skips_stable_paramours(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            spouse = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            accused = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            paramour = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            ctx.add_couple(accused.person_id, spouse.person_id)
            ctx.add_paramour_relationship(accused.person_id, paramour.person_id)
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.SCANDAL_BASE_SETTLEMENT_CHANCE", 1.0), patch(
                "library.simulation_incidents.SCANDAL_SETTLEMENT_CHANCE_CAP", 1.0
            ), patch("library.simulation_incidents.SCANDAL_PROPENSITY_THRESHOLD", 0.1):
                simulation_incidents_annual_tick(ctx, 1001)

            self.assertFalse(
                any(
                    event_type == "affair_scandal"
                    for _year, event_type, _payload in ctx._pending_simulation_events
                )
            )

    def test_forced_public_virtue_records_public_known_good_deed(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            settlement.food_pressure = 1.4
            benefactor = self._add_adult(
                ctx,
                genome=PUBLIC_VIRTUE_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            beneficiary = self._add_adult(
                ctx,
                genome=SELFISH_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            beneficiary.person = replace(
                beneficiary.person,
                job_prosperity_01=0.05,
                unemployment_started_year=1000,
            )
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.SCANDAL_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.VIRTUE_BASE_SETTLEMENT_CHANCE", 1.0
            ), patch("library.simulation_incidents.VIRTUE_SETTLEMENT_CHANCE_CAP", 1.0), patch(
                "library.simulation_incidents.VIRTUE_PROPENSITY_THRESHOLD", 0.3
            ), patch("library.simulation_incidents.VIRTUE_MAX_EVENTS_PER_YEAR", 1):
                simulation_incidents_annual_tick(ctx, 1001)

            events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "public_virtue"
            ]
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(int(event["benefactor_person_id"]), benefactor.person_id)
            self.assertEqual(int(event["beneficiary_person_id"]), beneficiary.person_id)
            self.assertIn(
                str(event["incident_kind"]),
                {
                    "heroic_rescue",
                    "public_mercy",
                    "public_arbitration",
                    "loyal_service",
                },
            )
            self.assertIn("relief_value", event)

            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(root / "save.sqlite")) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT e.primary_person_id, e.secondary_person_id,
                           er.settlement_id, er.region_id, e.payload_json,
                           r.record_type, r.visibility_state, r.confidence,
                           r.public_actor_person_id, r.public_victim_person_id,
                           r.prose_variant_key
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    JOIN simulation_event_records_readable r ON r.event_id = e.id
                    WHERE e.event_type = 'public_virtue'
                    """
                ).fetchone()
                roles = {
                    str(r["role"]): int(r["person_id"])
                    for r in conn.execute(
                        """
                        SELECT person_id, role
                        FROM simulation_event_people
                        WHERE event_id = (
                            SELECT id FROM simulation_events
                            WHERE event_type = 'public_virtue'
                            LIMIT 1
                        )
                        """
                    )
                }

            self.assertIsNotNone(row)
            self.assertEqual(int(row["primary_person_id"]), benefactor.person_id)
            self.assertEqual(int(row["secondary_person_id"]), beneficiary.person_id)
            self.assertEqual(str(row["settlement_id"]), settlement.settlement_id)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            payload = json.loads(str(row["payload_json"]))
            self.assertEqual(
                int(payload["benefactor_person_id"]), benefactor.person_id
            )
            self.assertEqual(
                int(payload["beneficiary_person_id"]), beneficiary.person_id
            )
            self.assertEqual(str(row["record_type"]), "public_virtue_record")
            self.assertEqual(str(row["visibility_state"]), "public_known")
            self.assertAlmostEqual(float(row["confidence"]), 0.85)
            self.assertEqual(int(row["public_actor_person_id"]), benefactor.person_id)
            self.assertEqual(
                int(row["public_victim_person_id"]), beneficiary.person_id
            )
            self.assertEqual(
                str(row["prose_variant_key"]),
                "public_virtue_record.public_known.default",
            )
            self.assertEqual(roles["benefactor"], benefactor.person_id)
            self.assertEqual(roles["beneficiary"], beneficiary.person_id)

    def test_forced_public_virtue_skips_low_prosocial_adults(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            self._add_adult(
                ctx,
                genome=SELFISH_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            self._add_adult(
                ctx,
                genome=SELFISH_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.SCANDAL_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.VIRTUE_BASE_SETTLEMENT_CHANCE", 1.0
            ), patch("library.simulation_incidents.VIRTUE_SETTLEMENT_CHANCE_CAP", 1.0), patch(
                "library.simulation_incidents.VIRTUE_PROPENSITY_THRESHOLD", 0.3
            ):
                simulation_incidents_annual_tick(ctx, 1001)

            self.assertFalse(
                any(
                    event_type == "public_virtue"
                    for _year, event_type, _payload in ctx._pending_simulation_events
                )
            )

    def test_forced_knowledge_culture_records_public_known_breakthrough(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            creator = self._add_adult(
                ctx,
                genome=KNOWLEDGE_CREATOR_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            patron = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            patron.person = replace(patron.person, job_prosperity_01=0.9)
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.SCANDAL_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.VIRTUE_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.KNOWLEDGE_BASE_SETTLEMENT_CHANCE", 1.0), patch(
                "library.simulation_incidents.KNOWLEDGE_SETTLEMENT_CHANCE_CAP", 1.0
            ), patch("library.simulation_incidents.KNOWLEDGE_PROPENSITY_THRESHOLD", 0.25), patch(
                "library.simulation_incidents.KNOWLEDGE_MAX_EVENTS_PER_YEAR", 1
            ):
                simulation_incidents_annual_tick(ctx, 1001)

            events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "knowledge_culture"
            ]
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(int(event["creator_person_id"]), creator.person_id)
            self.assertEqual(int(event["patron_person_id"]), patron.person_id)
            self.assertIn(
                str(event["incident_kind"]),
                {
                    "invention",
                    "discovery",
                    "legal_precedent",
                    "artistic_triumph",
                    "scholarly_breakthrough",
                },
            )
            self.assertIn(
                str(event["knowledge_domain"]),
                {
                    "art",
                    "calendar",
                    "craft",
                    "law",
                    "medicine",
                    "natural_history",
                    "performance",
                    "scholarship",
                    "toolmaking",
                },
            )
            self.assertIn("novelty_value", event)

            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(root / "save.sqlite")) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT e.primary_person_id, e.secondary_person_id,
                           er.settlement_id, er.region_id, e.payload_json,
                           r.record_type, r.visibility_state, r.confidence,
                           r.public_actor_person_id, r.public_victim_person_id,
                           r.prose_variant_key
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    JOIN simulation_event_records_readable r ON r.event_id = e.id
                    WHERE e.event_type = 'knowledge_culture'
                    """
                ).fetchone()
                roles = {
                    str(r["role"]): int(r["person_id"])
                    for r in conn.execute(
                        """
                        SELECT person_id, role
                        FROM simulation_event_people
                        WHERE event_id = (
                            SELECT id FROM simulation_events
                            WHERE event_type = 'knowledge_culture'
                            LIMIT 1
                        )
                        """
                    )
                }

            self.assertIsNotNone(row)
            self.assertEqual(int(row["primary_person_id"]), creator.person_id)
            self.assertEqual(int(row["secondary_person_id"]), patron.person_id)
            self.assertEqual(str(row["settlement_id"]), settlement.settlement_id)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            payload = json.loads(str(row["payload_json"]))
            self.assertEqual(int(payload["creator_person_id"]), creator.person_id)
            self.assertEqual(int(payload["patron_person_id"]), patron.person_id)
            self.assertEqual(str(row["record_type"]), "knowledge_record")
            self.assertEqual(str(row["visibility_state"]), "public_known")
            self.assertAlmostEqual(float(row["confidence"]), 0.8)
            self.assertEqual(int(row["public_actor_person_id"]), creator.person_id)
            self.assertEqual(int(row["public_victim_person_id"]), patron.person_id)
            self.assertEqual(
                str(row["prose_variant_key"]),
                "knowledge_record.public_known.default",
            )
            self.assertEqual(roles["creator"], creator.person_id)
            self.assertEqual(roles["patron"], patron.person_id)

    def test_forced_knowledge_culture_skips_low_aptitude_adults(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            self._add_adult(
                ctx,
                genome=DULL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            self._add_adult(
                ctx,
                genome=DULL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            ctx._pending_simulation_events.clear()

            with patch("library.simulation_incidents.MURDER_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.THEFT_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.SCANDAL_BASE_SETTLEMENT_CHANCE", 0.0), patch(
                "library.simulation_incidents.VIRTUE_BASE_SETTLEMENT_CHANCE", 0.0
            ), patch("library.simulation_incidents.KNOWLEDGE_BASE_SETTLEMENT_CHANCE", 1.0), patch(
                "library.simulation_incidents.KNOWLEDGE_SETTLEMENT_CHANCE_CAP", 1.0
            ), patch("library.simulation_incidents.KNOWLEDGE_PROPENSITY_THRESHOLD", 0.25):
                simulation_incidents_annual_tick(ctx, 1001)

            self.assertFalse(
                any(
                    event_type == "knowledge_culture"
                    for _year, event_type, _payload in ctx._pending_simulation_events
                )
            )


if __name__ == "__main__":
    unittest.main()
