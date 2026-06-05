"""Tests for genome-driven personal incident generation."""

from __future__ import annotations

import json
import random
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.incident_rates import (
    IncidentRateParams,
    clear_incident_rate_cache,
    incident_rate_for_year,
)
from library.simulation_context import SimulationContext
from library.simulation_incidents import (
    _build_incident_scoring_facts,
    _incident_context_map,
    _knowledge_culture_kind,
    _knowledge_domain,
    _murder_annual_event_cap,
    _murder_settlement_trial_count,
    knowledge_culture_propensity,
    property_crime_propensity,
    public_virtue_propensity,
    scandal_exposure_propensity,
    simulation_incidents_annual_tick,
    violent_actor_propensity,
)
from library.polity import OfficeSeatState
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

    def _add_child(
        self,
        ctx: SimulationContext,
        *,
        genome: dict[str, float],
        gender: str,
        settlement_id: str,
        region_id: str,
        father_id: int | None = None,
        mother_id: int | None = None,
    ):
        person = generate_person_random(
            simulation_context=ctx,
            simulation_year=1000,
            age=6,
            gender=gender,
            genome=genome,
            birthplace_region_id=region_id,
            birthplace_settlement_id=settlement_id,
        )
        return ctx.add_person(
            person=person,
            is_founder=False,
            father_id=father_id,
            mother_id=mother_id,
        )

    def test_murder_population_rate_helpers_scale_above_review_sample_cap(self) -> None:
        residents = [object()] * 20_000
        settlements = [("large_city", residents)]

        self.assertEqual(_murder_settlement_trial_count(residents), 24)
        self.assertGreaterEqual(_murder_annual_event_cap(settlements), 16)
        rate = IncidentRateParams(
            incident_key="murder",
            target_per_10k_per_year=8.0,
            annual_cap_multiplier=1.0,
        )
        self.assertEqual(_murder_annual_event_cap(settlements, rate), 24)

    def test_incident_rates_csv_resolves_medieval_crime_knobs(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))

            murder = incident_rate_for_year(
                db_path=ctx.db_path,
                world=ctx.world,
                incident_key="murder",
                historical_year=1000,
            )
            property_crime = incident_rate_for_year(
                db_path=ctx.db_path,
                world=ctx.world,
                incident_key="property_crime",
                historical_year=1000,
            )
            scandal = incident_rate_for_year(
                db_path=ctx.db_path,
                world=ctx.world,
                incident_key="affair_scandal",
                historical_year=1000,
            )

            self.assertEqual(murder.target_per_10k_per_year, 4.0)
            self.assertEqual(property_crime.chance_multiplier, 12.0)
            self.assertEqual(property_crime.annual_cap_multiplier, 10.0)
            self.assertEqual(scandal.chance_multiplier, 5.0)

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

    def test_incident_context_map_uses_pressure_offices_and_family_indexes(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            settlement.food_pressure = 1.35
            settlement.stability = 0.25
            settlement.prosperity_pool = 1.4
            actor = self._add_adult(
                ctx,
                genome=_genome(
                    justice=-45,
                    honesty=-45,
                    persuasion=50,
                    ambition=45,
                    frugality=45,
                ),
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            witness = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
            )
            self._add_child(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
                father_id=actor.person_id,
            )
            actor.person = replace(
                actor.person,
                job="market trader",
                unemployment_started_year=1000,
                job_prosperity_01=0.10,
                household_prosperity=0.40,
                status_tendency="low",
                household_purseholder_person_id=actor.person_id,
            )
            ctx.gov_office_seats[1] = OfficeSeatState(
                seat_id=1,
                polity_id=1,
                title_id="head_chief",
                scope_settlement_id=settlement.settlement_id,
                holder_person_id=actor.person_id,
            )

            facts = _build_incident_scoring_facts(ctx, 1001)
            contexts = _incident_context_map(
                ctx,
                facts,
                year=1001,
                settlement_id=settlement.settlement_id,
                records=[actor, witness],
                event_family="property_crime",
                pressure=settlement.food_pressure,
            )
            scoring_context = contexts[int(actor.person_id)]

            self.assertIn("parent", scoring_context.role_tags)
            self.assertIn("ruler", scoring_context.role_tags)
            self.assertIn("title_holder", scoring_context.role_tags)
            self.assertIn("trader", scoring_context.role_tags)
            self.assertIn("scarcity", scoring_context.pressure_tags)
            self.assertIn("debt", scoring_context.pressure_tags)
            self.assertIn("status_fall", scoring_context.pressure_tags)
            self.assertIn("market_day", scoring_context.opportunity_tags)
            self.assertIn("storehouse_access", scoring_context.opportunity_tags)
            self.assertIn("court", scoring_context.opportunity_tags)
            self.assertIn("shared_household", scoring_context.opportunity_tags)
            self.assertGreater(
                property_crime_propensity(actor, context=scoring_context),
                property_crime_propensity(actor),
            )

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

    def test_maritime_and_mercantile_jobs_select_portable_knowledge_domains(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            ctx = self._context(Path(td))
            port = ctx.ensure_active_settlement_for_region("aeria_port")
            inland = ctx.ensure_active_settlement_for_region("aeria_north")
            shipwright = self._add_adult(
                ctx,
                genome=KNOWLEDGE_CREATOR_GENOME,
                gender="Male",
                settlement_id=port.settlement_id,
                region_id=port.region_id,
            )
            shipwright.person = replace(
                shipwright.person,
                job="ship carpenter",
                current_settlement_id=port.settlement_id,
            )
            scribe = self._add_adult(
                ctx,
                genome=KNOWLEDGE_CREATOR_GENOME,
                gender="Female",
                settlement_id=inland.settlement_id,
                region_id=inland.region_id,
            )
            scribe.person = replace(
                scribe.person,
                job="scribe",
                current_settlement_id=inland.settlement_id,
            )
            merchant = self._add_adult(
                ctx,
                genome=KNOWLEDGE_CREATOR_GENOME,
                gender="Male",
                settlement_id=inland.settlement_id,
                region_id=inland.region_id,
            )
            merchant.person = replace(
                merchant.person,
                job="merchant",
                current_settlement_id=inland.settlement_id,
            )
            jurist = self._add_adult(
                ctx,
                genome=KNOWLEDGE_CREATOR_GENOME,
                gender="Female",
                settlement_id=inland.settlement_id,
                region_id=inland.region_id,
            )
            jurist.person = replace(
                jurist.person,
                job="trade law judge",
                current_settlement_id=inland.settlement_id,
            )

            ship_kind = _knowledge_culture_kind(ctx, shipwright, random.Random(1))
            scribe_kind = _knowledge_culture_kind(ctx, scribe, random.Random(2))
            merchant_kind = _knowledge_culture_kind(ctx, merchant, random.Random(3))
            jurist_kind = _knowledge_culture_kind(ctx, jurist, random.Random(4))

            self.assertEqual(ship_kind, "shipbuilding_advance")
            self.assertEqual(_knowledge_domain(ship_kind, shipwright), "shipbuilding")
            self.assertEqual(scribe_kind, "writing_system")
            self.assertEqual(_knowledge_domain(scribe_kind, scribe), "writing")
            self.assertEqual(merchant_kind, "accounting_method")
            self.assertEqual(_knowledge_domain(merchant_kind, merchant), "accounting")
            self.assertEqual(jurist_kind, "trade_law_precedent")
            self.assertEqual(_knowledge_domain(jurist_kind, jurist), "trade_law")

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
            faction_memory = murder["consequences"]["faction_memory"]
            self.assertEqual(len(faction_memory), 1)
            self.assertIn(
                str(faction_memory[0]["memory_type"]),
                {"blood_feud", "violent_grievance"},
            )
            self.assertEqual(int(faction_memory[0]["principal_person_id"]), victim.person_id)
            self.assertEqual(int(faction_memory[0]["opposing_person_id"]), killer.person_id)
            self.assertIn(
                str(murder["incident_kind"]),
                {
                    "murder",
                    "predatory_murder",
                    "ambush_killing",
                    "rash_brawl_killing",
                    "feud_killing",
                    "feud_murder",
                    "domestic_murder",
                    "kin_killing",
                },
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
                "library.simulation_incidents.MURDER_SETTLEMENT_TRIAL_POPULATION", 2
            ), patch(
                "library.simulation_incidents.MURDER_MAX_EVENTS_PER_YEAR", 3
            ):
                with closing(sqlite3.connect(ctx.db_path)) as conn:
                    conn.execute(
                        """
                        UPDATE incident_rates
                        SET target_per_10k_per_year = ?
                        WHERE incident_key = ?
                        """,
                        ("10000", "murder"),
                    )
                    conn.commit()
                clear_incident_rate_cache()
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
            perpetrator.person = replace(perpetrator.person, household_prosperity=1.0)
            target.person = replace(target.person, household_prosperity=1.0)
            settlement.prosperity_pool = 1.0
            settlement.stability = 0.6
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
                {
                    "theft",
                    "livestock_theft",
                    "storehouse_robbery",
                    "market_stall_theft",
                    "fraud",
                    "debt_fraud",
                    "inheritance_fraud",
                    "extortion",
                    "market_extortion",
                    "hoarding_theft",
                },
            )
            self.assertIn(perpetrator.person_id, ctx.current_people_ids)
            self.assertIn(target.person_id, ctx.current_people_ids)
            self.assertLess(
                ctx.id_to_record[target.person_id].person.household_prosperity or 0.0,
                1.0,
            )
            self.assertGreater(
                ctx.id_to_record[perpetrator.person_id].person.household_prosperity
                or 0.0,
                1.0,
            )
            self.assertLess(
                ctx.settlements_by_id[settlement.settlement_id].prosperity_pool,
                1.0,
            )
            self.assertLess(
                ctx.settlements_by_id[settlement.settlement_id].stability,
                0.6,
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
            consequences = payload["consequences"]
            self.assertLess(
                consequences["property_loss"]["target"]["prosperity_after"],
                consequences["property_loss"]["target"]["prosperity_before"],
            )
            self.assertGreater(
                consequences["property_loss"]["perpetrator"]["prosperity_after"],
                consequences["property_loss"]["perpetrator"]["prosperity_before"],
            )
            self.assertLess(
                consequences["settlement"]["prosperity_pool_after"],
                consequences["settlement"]["prosperity_pool_before"],
            )
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
            ), patch(
                "library.simulation_incidents._scandal_kind",
                return_value="heir_legitimacy_rumor",
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
            self.assertEqual(str(event["incident_kind"]), "heir_legitimacy_rumor")
            self.assertIsNone(
                ctx.id_to_record[accused.person_id].person.paramour_person_id
            )
            self.assertIsNone(
                ctx.id_to_record[paramour.person_id].person.paramour_person_id
            )
            self.assertIsNone(
                ctx.id_to_record[accused.person_id].person.partner_person_id
            )
            self.assertIsNone(
                ctx.id_to_record[spouse.person_id].person.partner_person_id
            )
            self.assertTrue(event["consequences"]["ended_paramour"])
            self.assertEqual(
                event["consequences"]["dissolved_couples"],
                [
                    {
                        "person_a_id": spouse.person_id,
                        "person_b_id": accused.person_id,
                    }
                ],
            )
            fallout = event["consequences"]["legal_fallout"]
            self.assertEqual(len(fallout), 1)
            self.assertEqual(
                str(fallout[0]["fallout_type"]), "heir_legitimacy_challenge"
            )
            self.assertEqual(
                int(fallout[0]["principal_person_id"]), accused.person_id
            )
            self.assertEqual(
                int(fallout[0]["opposing_person_id"]), spouse.person_id
            )
            self.assertEqual(
                int(fallout[0]["related_person_id"]), paramour.person_id
            )
            self.assertTrue(
                any(
                    event_type == "paramour_ended"
                    and payload.get("source_event") == "affair_scandal"
                    for _year, event_type, payload in ctx._pending_simulation_events
                )
            )
            self.assertTrue(
                any(
                    event_type == "couple_dissolved"
                    and payload.get("source_event") == "affair_scandal"
                    for _year, event_type, payload in ctx._pending_simulation_events
                )
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
                fallout_row = conn.execute(
                    """
                    SELECT source_event_id, source_event_year, source_event_type,
                           fallout_key, fallout_type, status,
                           principal_person_id, opposing_person_id,
                           related_person_id, region_id, settlement_id, severity,
                           start_year, expected_resolution_year, details_json
                    FROM simulation_legal_fallout_readable
                    WHERE source_event_id = (
                        SELECT id FROM simulation_events
                        WHERE event_type = 'affair_scandal'
                        LIMIT 1
                    )
                    """
                ).fetchone()

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
            self.assertTrue(payload["consequences"]["ended_paramour"])
            self.assertEqual(
                payload["consequences"]["dissolved_couples"][0]["person_a_id"],
                spouse.person_id,
            )
            self.assertIsNotNone(fallout_row)
            self.assertEqual(int(fallout_row["source_event_year"]), 1001)
            self.assertEqual(str(fallout_row["source_event_type"]), "affair_scandal")
            self.assertEqual(
                str(fallout_row["fallout_key"]),
                f"heir_legitimacy:{accused.person_id}:{paramour.person_id}:{spouse.person_id}",
            )
            self.assertEqual(
                str(fallout_row["fallout_type"]), "heir_legitimacy_challenge"
            )
            self.assertEqual(str(fallout_row["status"]), "active")
            self.assertEqual(
                int(fallout_row["principal_person_id"]), accused.person_id
            )
            self.assertEqual(
                int(fallout_row["opposing_person_id"]), spouse.person_id
            )
            self.assertEqual(
                int(fallout_row["related_person_id"]), paramour.person_id
            )
            self.assertEqual(str(fallout_row["region_id"]), "aeria_north")
            self.assertEqual(
                str(fallout_row["settlement_id"]), settlement.settlement_id
            )
            self.assertGreater(float(fallout_row["severity"]), 0.0)
            self.assertEqual(int(fallout_row["start_year"]), 1001)
            self.assertEqual(int(fallout_row["expected_resolution_year"]), 1019)
            self.assertEqual(
                json.loads(str(fallout_row["details_json"])),
                {
                    "source_role": "affair_scandal_legal_fallout",
                    "incident_kind": "heir_legitimacy_rumor",
                    "betrayed_partner_person_ids": [spouse.person_id],
                },
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
                household_prosperity=1.0,
            )
            benefactor.person = replace(
                benefactor.person,
                household_prosperity=1.0,
                leader_tendency="low",
            )
            settlement.prosperity_pool = 1.0
            settlement.stability = 0.5
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
                    "river_rescue",
                    "fire_rescue",
                    "public_mercy",
                    "famine_mercy",
                    "public_arbitration",
                    "boundary_arbitration",
                    "succession_arbitration",
                    "loyal_service",
                    "oath_kept_under_pressure",
                },
            )
            self.assertIn("relief_value", event)
            self.assertGreater(
                ctx.id_to_record[beneficiary.person_id].person.household_prosperity
                or 0.0,
                1.0,
            )
            self.assertLess(
                ctx.id_to_record[benefactor.person_id].person.household_prosperity
                or 0.0,
                1.0,
            )
            self.assertEqual(
                ctx.id_to_record[benefactor.person_id].person.leader_tendency,
                "medium",
            )
            self.assertGreater(
                ctx.settlements_by_id[settlement.settlement_id].prosperity_pool,
                1.0,
            )
            self.assertGreater(
                ctx.settlements_by_id[settlement.settlement_id].stability,
                0.5,
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
                    WHERE e.event_type = 'public_virtue'
                    """
                ).fetchone()
                obligation_row = conn.execute(
                    """
                    SELECT source_event_type, obligation_key, obligation_type,
                           status, owed_by_person_id, owed_to_person_id,
                           region_id, settlement_id, strength, start_year,
                           expected_end_year
                    FROM simulation_obligations_readable
                    WHERE source_event_id = (
                        SELECT id FROM simulation_events
                        WHERE event_type = 'public_virtue'
                        LIMIT 1
                    )
                    """
                ).fetchone()
                reputation_row = conn.execute(
                    """
                    SELECT source_event_type, mark_key, person_id,
                           reputation_axis, reputation_before, reputation_after,
                           direction, mark_strength, region_id, settlement_id,
                           mark_year
                    FROM simulation_reputation_marks_readable
                    WHERE source_event_id = (
                        SELECT id FROM simulation_events
                        WHERE event_type = 'public_virtue'
                        LIMIT 1
                    )
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
            self.assertGreater(
                payload["consequences"]["relief"]["beneficiary"]["prosperity_after"],
                payload["consequences"]["relief"]["beneficiary"]["prosperity_before"],
            )
            self.assertLess(
                payload["consequences"]["relief"]["benefactor"]["prosperity_after"],
                payload["consequences"]["relief"]["benefactor"]["prosperity_before"],
            )
            self.assertEqual(
                payload["consequences"]["public_reputation"][
                    "leader_tendency_after"
                ],
                "medium",
            )
            obligations = payload["consequences"]["obligations"]
            self.assertEqual(len(obligations), 1)
            self.assertEqual(obligations[0]["obligation_type"], "relief_debt")
            self.assertEqual(
                int(obligations[0]["owed_by_person_id"]), beneficiary.person_id
            )
            self.assertEqual(
                int(obligations[0]["owed_to_person_id"]), benefactor.person_id
            )
            self.assertIsNotNone(obligation_row)
            self.assertEqual(str(obligation_row["source_event_type"]), "public_virtue")
            self.assertEqual(
                str(obligation_row["obligation_key"]),
                "beneficiary_to_benefactor",
            )
            self.assertEqual(str(obligation_row["obligation_type"]), "relief_debt")
            self.assertEqual(str(obligation_row["status"]), "active")
            self.assertEqual(
                int(obligation_row["owed_by_person_id"]), beneficiary.person_id
            )
            self.assertEqual(
                int(obligation_row["owed_to_person_id"]), benefactor.person_id
            )
            self.assertEqual(str(obligation_row["region_id"]), "aeria_north")
            self.assertEqual(
                str(obligation_row["settlement_id"]), settlement.settlement_id
            )
            self.assertGreater(float(obligation_row["strength"]), 0.0)
            self.assertEqual(int(obligation_row["start_year"]), 1001)
            self.assertEqual(int(obligation_row["expected_end_year"]), 1013)
            self.assertIsNotNone(reputation_row)
            self.assertEqual(str(reputation_row["source_event_type"]), "public_virtue")
            self.assertEqual(str(reputation_row["mark_key"]), f"leadership:{benefactor.person_id}")
            self.assertEqual(int(reputation_row["person_id"]), benefactor.person_id)
            self.assertEqual(str(reputation_row["reputation_axis"]), "leadership")
            self.assertEqual(str(reputation_row["reputation_before"]), "low")
            self.assertEqual(str(reputation_row["reputation_after"]), "medium")
            self.assertEqual(str(reputation_row["direction"]), "positive")
            self.assertGreater(float(reputation_row["mark_strength"]), 0.0)
            self.assertEqual(str(reputation_row["region_id"]), "aeria_north")
            self.assertEqual(
                str(reputation_row["settlement_id"]), settlement.settlement_id
            )
            self.assertEqual(int(reputation_row["mark_year"]), 1001)
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
            creator.person = replace(
                creator.person,
                household_prosperity=1.0,
                status_tendency="low",
            )
            patron.person = replace(
                patron.person,
                job_prosperity_01=0.9,
                household_prosperity=1.0,
            )
            settlement.prosperity_pool = 1.0
            settlement.stability = 0.5
            ctx.region_prosperity_pool["aeria_north"] = 1.0
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
                    "improved_plow",
                    "water_lift",
                    "kiln_improvement",
                    "dye_recipe",
                    "shipbuilding_advance",
                    "navigation_discovery",
                    "writing_system",
                    "accounting_method",
                    "trade_law_precedent",
                    "standard_container",
                    "luxury_dye_recipe",
                    "discovery",
                    "medicinal_discovery",
                    "new_star_record",
                    "legal_precedent",
                    "boundary_judgment",
                    "inheritance_judgment",
                    "succession_precedent",
                    "artistic_triumph",
                    "famous_performance",
                    "scholarly_breakthrough",
                    "calendar_reform",
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
                    "navigation",
                    "performance",
                    "scholarship",
                    "shipbuilding",
                    "toolmaking",
                    "writing",
                    "accounting",
                    "trade_law",
                },
            )
            self.assertIn("novelty_value", event)
            self.assertGreater(
                ctx.id_to_record[creator.person_id].person.household_prosperity
                or 0.0,
                1.0,
            )
            self.assertLess(
                ctx.id_to_record[patron.person_id].person.household_prosperity
                or 0.0,
                1.0,
            )
            self.assertEqual(
                ctx.id_to_record[creator.person_id].person.status_tendency,
                "middle-high",
            )
            self.assertGreater(
                ctx.settlements_by_id[settlement.settlement_id].prosperity_pool,
                1.0,
            )
            self.assertGreater(ctx.region_prosperity_pool["aeria_north"], 1.0)

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
                domain_row = conn.execute(
                    """
                    SELECT region_id, domain, domain_score, breakthrough_count,
                           first_event_year, latest_event_year,
                           first_event_id, latest_event_id, latest_incident_kind,
                           latest_creator_person_id, latest_settlement_id
                    FROM simulation_domain_states_readable
                    WHERE latest_event_id = (
                        SELECT id FROM simulation_events
                        WHERE event_type = 'knowledge_culture'
                        LIMIT 1
                    )
                    """
                ).fetchone()
                obligation_row = conn.execute(
                    """
                    SELECT source_event_type, obligation_key, obligation_type,
                           status, owed_by_person_id, owed_to_person_id,
                           region_id, settlement_id, strength, start_year,
                           expected_end_year
                    FROM simulation_obligations_readable
                    WHERE source_event_id = (
                        SELECT id FROM simulation_events
                        WHERE event_type = 'knowledge_culture'
                        LIMIT 1
                    )
                    """
                ).fetchone()
                reputation_row = conn.execute(
                    """
                    SELECT source_event_type, mark_key, person_id,
                           reputation_axis, reputation_before, reputation_after,
                           direction, mark_strength, region_id, settlement_id,
                           mark_year
                    FROM simulation_reputation_marks_readable
                    WHERE source_event_id = (
                        SELECT id FROM simulation_events
                        WHERE event_type = 'knowledge_culture'
                        LIMIT 1
                    )
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
            self.assertEqual(
                payload["consequences"]["knowledge_state"]["domain"],
                payload["knowledge_domain"],
            )
            self.assertTrue(payload["consequences"]["institutions"])
            self.assertIsNotNone(domain_row)
            self.assertEqual(str(domain_row["region_id"]), "aeria_north")
            self.assertEqual(str(domain_row["domain"]), payload["knowledge_domain"])
            self.assertGreater(float(domain_row["domain_score"]), 0.0)
            self.assertEqual(int(domain_row["breakthrough_count"]), 1)
            self.assertEqual(int(domain_row["first_event_year"]), 1001)
            self.assertEqual(int(domain_row["latest_event_year"]), 1001)
            self.assertEqual(
                int(domain_row["first_event_id"]),
                int(domain_row["latest_event_id"]),
            )
            self.assertEqual(
                str(domain_row["latest_incident_kind"]),
                str(payload["incident_kind"]),
            )
            self.assertEqual(
                int(domain_row["latest_creator_person_id"]), creator.person_id
            )
            self.assertEqual(
                str(domain_row["latest_settlement_id"]), settlement.settlement_id
            )
            self.assertGreater(
                payload["consequences"]["patronage"]["creator"]["prosperity_after"],
                payload["consequences"]["patronage"]["creator"]["prosperity_before"],
            )
            self.assertLess(
                payload["consequences"]["patronage"]["patron"]["prosperity_after"],
                payload["consequences"]["patronage"]["patron"]["prosperity_before"],
            )
            obligations = payload["consequences"]["obligations"]
            self.assertEqual(len(obligations), 1)
            self.assertEqual(obligations[0]["obligation_type"], "patronage_debt")
            self.assertEqual(int(obligations[0]["owed_by_person_id"]), creator.person_id)
            self.assertEqual(int(obligations[0]["owed_to_person_id"]), patron.person_id)
            self.assertIsNotNone(obligation_row)
            self.assertEqual(
                str(obligation_row["source_event_type"]), "knowledge_culture"
            )
            self.assertEqual(str(obligation_row["obligation_key"]), "creator_to_patron")
            self.assertEqual(
                str(obligation_row["obligation_type"]), "patronage_debt"
            )
            self.assertEqual(str(obligation_row["status"]), "active")
            self.assertEqual(int(obligation_row["owed_by_person_id"]), creator.person_id)
            self.assertEqual(int(obligation_row["owed_to_person_id"]), patron.person_id)
            self.assertEqual(str(obligation_row["region_id"]), "aeria_north")
            self.assertEqual(
                str(obligation_row["settlement_id"]), settlement.settlement_id
            )
            self.assertGreater(float(obligation_row["strength"]), 0.0)
            self.assertEqual(int(obligation_row["start_year"]), 1001)
            self.assertEqual(int(obligation_row["expected_end_year"]), 1021)
            self.assertIsNotNone(reputation_row)
            self.assertEqual(
                str(reputation_row["source_event_type"]), "knowledge_culture"
            )
            self.assertEqual(
                str(reputation_row["mark_key"]), f"status:{creator.person_id}"
            )
            self.assertEqual(int(reputation_row["person_id"]), creator.person_id)
            self.assertEqual(str(reputation_row["reputation_axis"]), "status")
            self.assertEqual(str(reputation_row["reputation_before"]), "low")
            self.assertEqual(str(reputation_row["reputation_after"]), "middle-high")
            self.assertEqual(str(reputation_row["direction"]), "positive")
            self.assertGreater(float(reputation_row["mark_strength"]), 0.0)
            self.assertEqual(str(reputation_row["region_id"]), "aeria_north")
            self.assertEqual(
                str(reputation_row["settlement_id"]), settlement.settlement_id
            )
            self.assertEqual(int(reputation_row["mark_year"]), 1001)
            self.assertEqual(
                payload["consequences"]["public_reputation"][
                    "status_tendency_after"
                ],
                "middle-high",
            )
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

    def test_maritime_knowledge_diffuses_domain_state_to_sea_route_destinations(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_port")
            creator = self._add_adult(
                ctx,
                genome=KNOWLEDGE_CREATOR_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            patron = self._add_adult(
                ctx,
                genome=PEACEFUL_GENOME,
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id=settlement.region_id,
            )
            creator.person = replace(
                creator.person,
                job="ship carpenter",
                current_settlement_id=settlement.settlement_id,
                household_prosperity=1.0,
                status_tendency="low",
            )
            patron.person = replace(
                patron.person,
                job="merchant",
                current_settlement_id=settlement.settlement_id,
                job_prosperity_01=0.9,
                household_prosperity=1.0,
            )
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
            self.assertEqual(str(event["incident_kind"]), "shipbuilding_advance")
            self.assertEqual(str(event["knowledge_domain"]), "shipbuilding")
            diffusion = event["consequences"]["knowledge_state_diffusion"]
            self.assertTrue(diffusion)
            self.assertEqual(diffusion[0]["region_id"], "boreas_port")

            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(root / "save.sqlite")) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT region_id, domain, domain_score
                    FROM simulation_domain_states_readable
                    WHERE domain = 'shipbuilding'
                    ORDER BY region_id
                    """
                ).fetchall()
            states = {str(row["region_id"]): float(row["domain_score"]) for row in rows}
            self.assertIn("aeria_port", states)
            self.assertIn("boreas_port", states)
            self.assertGreater(states["aeria_port"], states["boreas_port"])

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
