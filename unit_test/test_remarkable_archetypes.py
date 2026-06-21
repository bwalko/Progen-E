"""Tests for rare remarkable-archetype event generation."""

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
from library.event_history_report import build_event_history_report
from library.event_scoring import TraitFactor
from library.generator import generate_person_random
from library.incident_rates import IncidentRateParams
from library.remarkable_archetypes import (
    RemarkableArchetype,
    RemarkableEventOption,
    clear_remarkable_archetype_cache,
    remarkable_archetypes,
)
from library.simulation_context import SimulationContext
from library.simulation_remarkable_archetypes import (
    _annual_opportunity_count,
    simulation_remarkable_archetypes_annual_tick,
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


REMARKABLE_CREATOR_GENOME = _genome(
    curiosity=95,
    creativity=95,
    intellect=95,
    focus=90,
    perception=85,
)


class TestRemarkableArchetypes(unittest.TestCase):
    def _context(self, root: Path) -> SimulationContext:
        cfg = root / "config.sqlite"
        sav = root / "save.sqlite"
        load_all_csvs_into_sqlite(cfg)
        return SimulationContext.create(
            db_path=cfg,
            save_db_path=sav,
            world_id="remarkable-archetypes",
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
        job: str = "",
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
        if job:
            person = replace(person, job=job)
        return ctx.add_person(person=person, is_founder=True)

    def test_config_loads_authored_archetype_distribution(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            clear_remarkable_archetype_cache()

            entries = remarkable_archetypes(db_path=cfg)
            by_key = {entry.key: entry for entry in entries}

            self.assertIn("knowledge_maker", by_key)
            self.assertIn("outlaw_conspirator", by_key)
            self.assertAlmostEqual(sum(entry.share_weight for entry in entries), 100.0)
            self.assertGreater(by_key["knowledge_maker"].minimum_score, 0.0)
            self.assertTrue(by_key["knowledge_maker"].event_options)
            self.assertFalse(by_key["outlaw_conspirator"].promotion_allowed)

    def test_opportunity_count_is_rare_not_population_percentage(self) -> None:
        rate = IncidentRateParams(incident_key="remarkable_archetype")
        sparse_counts = [
            _annual_opportunity_count(
                mixed_population=10_000,
                rate=rate,
                rng=random.Random(seed),
            )
            for seed in range(200)
        ]
        self.assertLess(sum(sparse_counts) / len(sparse_counts), 0.30)
        self.assertLessEqual(max(sparse_counts), 1)

        boosted = IncidentRateParams(
            incident_key="remarkable_archetype",
            chance_multiplier=100.0,
            annual_cap_multiplier=100.0,
        )
        self.assertGreaterEqual(
            _annual_opportunity_count(
                mixed_population=10_000,
                rate=boosted,
                rng=random.Random(1),
            ),
            10,
        )

    def test_annual_tick_emits_checkpointed_reportable_archetype_event(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            ctx = self._context(root)
            settlement = ctx.ensure_active_settlement_for_region("aeria_north")
            creator = self._add_adult(
                ctx,
                genome=REMARKABLE_CREATOR_GENOME,
                gender="Female",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
                job="scribe engineer",
            )
            patron = self._add_adult(
                ctx,
                genome=_genome(),
                gender="Male",
                settlement_id=settlement.settlement_id,
                region_id="aeria_north",
                job="merchant patron",
            )
            creator.person = replace(creator.person, status_tendency="low")
            patron.person = replace(patron.person, household_prosperity=1.0)
            ctx._pending_simulation_events.clear()

            test_archetype = RemarkableArchetype(
                key="test_scholar",
                bucket="knowledge_makers",
                display_name="Test Scholar",
                share_weight=100.0,
                trait_factors=(
                    TraitFactor("curiosity", "positive_extreme", 0.35),
                    TraitFactor("creativity", "positive_extreme", 0.30),
                    TraitFactor("intellect", "positive_extreme", 0.25),
                ),
                composite_weights={},
                role_weights={"artisan": 0.05},
                pressure_weights={},
                opportunity_weights={"archive": 0.05},
                event_options=(
                    RemarkableEventOption(
                        event_type="knowledge_culture",
                        incident_kind="scholarly_breakthrough",
                        weight=1.0,
                        domain="scholarship",
                    ),
                ),
                minimum_score=0.10,
                importance_min=0.50,
                importance_max=0.95,
                promotion_allowed=True,
                notes="test fixture",
            )

            with patch(
                "library.simulation_remarkable_archetypes.remarkable_archetypes",
                return_value=(test_archetype,),
            ), patch(
                "library.simulation_remarkable_archetypes._annual_opportunity_count",
                return_value=1,
            ):
                simulation_remarkable_archetypes_annual_tick(ctx, 1001)

            events = [
                payload
                for _year, event_type, payload in ctx._pending_simulation_events
                if event_type == "knowledge_culture"
            ]
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["archetype_key"], "test_scholar")
            self.assertEqual(event["candidate_basis"], "detailed_sample")
            self.assertEqual(int(event["creator_person_id"]), creator.person_id)
            self.assertEqual(int(event["patron_person_id"]), patron.person_id)
            self.assertEqual(event["knowledge_domain"], "scholarship")
            self.assertGreater(float(event["archetype_score"]), 0.10)
            self.assertEqual(
                event["opportunity_context"]["mixed_population"],
                2,
            )
            self.assertIn("curiosity", event["genome_signals"])

            checkpoint_simulation_to_save(ctx, full_snapshot=False)
            with closing(sqlite3.connect(root / "save.sqlite")) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT e.primary_person_id, e.secondary_person_id,
                           er.settlement_id, er.region_id, e.payload_json,
                           r.record_type, r.visibility_state
                    FROM simulation_events e
                    JOIN simulation_events_readable er ON er.id = e.id
                    JOIN simulation_event_records_readable r ON r.event_id = e.id
                    WHERE e.event_type = 'knowledge_culture'
                    """
                ).fetchone()
                report = build_event_history_report(
                    conn,
                    sample_limit=0,
                    sample_event_types=(),
                )

            self.assertIsNotNone(row)
            self.assertEqual(int(row["primary_person_id"]), creator.person_id)
            self.assertEqual(int(row["secondary_person_id"]), patron.person_id)
            self.assertEqual(str(row["settlement_id"]), settlement.settlement_id)
            self.assertEqual(str(row["region_id"]), "aeria_north")
            self.assertEqual(str(row["record_type"]), "knowledge_record")
            self.assertEqual(str(row["visibility_state"]), "public_known")
            stored_payload = json.loads(str(row["payload_json"]))
            self.assertEqual(stored_payload["archetype_key"], "test_scholar")
            metrics = {
                (metric.event_type, metric.metric): metric
                for metric in report.metric_summaries
            }
            self.assertIn(("knowledge_culture", "archetype_score"), metrics)
            self.assertIn(("knowledge_culture", "archetype_share_weight"), metrics)


if __name__ == "__main__":
    unittest.main()
