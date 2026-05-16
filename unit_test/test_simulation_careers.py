"""Tests for genome_jobs-driven career assignment."""

from __future__ import annotations

import sqlite3
import importlib.util
import sys
import tempfile
import types
import unittest
import csv
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

if "numpy" not in sys.modules and importlib.util.find_spec("numpy") is None:
    class _NumpyRandomStub:
        def seed(self, *_):
            return None

        def normal(self, loc=0.0, scale=1.0, size=None):
            if size is None:
                return float(loc)
            return [float(loc)] * int(size)

    sys.modules["numpy"] = types.SimpleNamespace(
        random=_NumpyRandomStub(),
        clip=lambda value, lo, hi: max(lo, min(hi, value)),
    )

from library.config_import import load_all_csvs_into_sqlite
from library.genome_composites import (
    GENOME_COMPOSITE_MIN_SCORE,
    normalize_composite_band,
    score_composite_row,
    significant_composite_names,
)
from library.person import Person
from library.simulation_export import split_person_for_export
from library.simulation_careers import (
    PREMIUM_JOB_FITNESS_THRESHOLD,
    PREMIUM_JOB_MAX_PROB,
    assign_career_if_eligible,
    career_fitness,
    career_fitness_score,
    choose_career_assignment,
    career_desperation_score,
    job_category_fitness_for_title,
    job_category_fitness_score,
    job_eligibility_age,
    job_loss_probability,
    lose_job,
    maybe_migrate_job_seeker_household,
    premium_job_roll_probability,
    rehire_probability,
    resolve_job_era,
    score_genome_job_row,
    simulation_careers_annual_tick,
    _job_allowed_for_person,
    _parse_job_token,
)
from library.simulation_context import SimulationContext, SimulationPersonRecord
from library.world_save import checkpoint_simulation_to_save, try_load_simulation_checkpoint

_ROOT = Path(__file__).resolve().parents[1]


def _create_config_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE genome_jobs (
                trait TEXT,
                deviation_band TEXT,
                descriptor TEXT,
                status_tendency TEXT,
                leader_quality TEXT,
                leader_tendency TEXT,
                prehistoric_jobs TEXT,
                prehistoric_premium_jobs TEXT,
                bronze_age_jobs TEXT,
                bronze_age_premium_jobs TEXT,
                iron_age_jobs TEXT,
                iron_age_premium_jobs TEXT,
                medieval_jobs TEXT,
                medieval_premium_jobs TEXT,
                modern_jobs TEXT,
                modern_premium_jobs TEXT,
                strong_pairings TEXT,
                role_cluster TEXT,
                overlap_notes TEXT,
                design_notes TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO genome_jobs VALUES (
                'focus', 'optimal', 'focused', 'high', 'strong', 'medium',
                'tracker; toolmaker', NULL,
                'scribe; engineer', NULL,
                'engineer; physician', NULL,
                'copyist; architect', NULL,
                'software engineer; analyst', 'fixture premium job',
                'ignored strong pairings', 'ignored role', 'ignored overlap', 'ignored design'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO genome_jobs VALUES (
                'courage', 'excess', 'reckless', 'volatile', 'poor', 'medium',
                'raider; dangerous hunter', NULL,
                'mercenary; brawler', NULL,
                'gladiator; duelist', NULL,
                'duelist; raider', NULL,
                'stunt performer; storm chaser', NULL,
                'ignored strong pairings', 'ignored role', 'ignored overlap', 'ignored design'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE genome_composites (
                composite_id TEXT,
                composite_name TEXT,
                component_1_trait TEXT,
                component_1_position TEXT,
                component_2_trait TEXT,
                component_2_position TEXT,
                component_3_trait TEXT,
                component_3_position TEXT,
                disqualifier_1_trait TEXT,
                disqualifier_1_position TEXT,
                disqualifier_2_trait TEXT,
                disqualifier_2_position TEXT,
                composite_family TEXT,
                short_definition TEXT,
                conversion_note TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO genome_composites VALUES (
                'TEST1', 'Fixture composite',
                'focus', 'peak', 'courage', 'deficient',
                '', '', '', '', '', '', '', '', ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE job_economics (
                job_key TEXT,
                era TEXT,
                row_kind TEXT,
                pool_draw REAL,
                wage_yield REAL,
                value_add REAL,
                tax_rate REAL
            )
            """
        )
        for era, pool, wage, va, tax in (
            ("prehistoric", 0.3, 0.2, 0.18, 0.035),
            ("bronze_age", 0.27, 0.24, 0.22, 0.048),
            ("iron_age", 0.25, 0.27, 0.25, 0.058),
            ("medieval", 0.23, 0.29, 0.27, 0.068),
            ("modern", 0.21, 0.31, 0.29, 0.078),
        ):
            conn.execute(
                """
                INSERT INTO job_economics (job_key, era, row_kind, pool_draw, wage_yield, value_add, tax_rate)
                VALUES ('*', ?, 'base', ?, ?, ?, ?)
                """,
                (era, pool, wage, va, tax),
            )
        conn.execute(
            """
            INSERT INTO job_economics (job_key, era, row_kind, pool_draw, wage_yield, value_add, tax_rate)
            VALUES ('*', '*', 'base', 0.21, 0.31, 0.29, 0.078)
            """
        )
        conn.execute(
            """
            INSERT INTO job_economics (job_key, era, row_kind, pool_draw, wage_yield, value_add, tax_rate)
            VALUES ('software engineer', 'modern', 'deviation', '', '2.5', '2.6', '')
            """
        )
        conn.execute(
            """
            INSERT INTO job_economics (job_key, era, row_kind, pool_draw, wage_yield, value_add, tax_rate)
            VALUES ('analyst', 'modern', 'deviation', '', '2.2', '2.3', '')
            """
        )
        conn.execute(
            """
            CREATE TABLE genome (
                trait TEXT,
                "deficient deviation" TEXT,
                "optimal centerpoint" TEXT,
                "excess deviation" TEXT,
                gender_skew_high TEXT,
                gender_skew_low TEXT,
                "deficient description" TEXT,
                "optimal description" TEXT,
                "excess description" TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO genome VALUES (
                'focus', 'scatterbrained', 'focused', 'fixated', 'none', 'none',
                'scatterbrained', 'focused', 'fixated'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO genome VALUES (
                'courage', 'coward', 'courageous', 'reckless', 'male', 'female',
                'cowardly', 'brave', 'reckless'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _person(*, birthyear: int, genome: dict[str, float], min_fertility_age: int = 18) -> Person:
    return Person(
        first_name="Test",
        last_name="Person",
        gender="Male",
        ethnic="Human",
        species="Human",
        birthyear=birthyear,
        min_fertility_age=min_fertility_age,
        genome=genome,
    )


def _full_genome(value: float) -> dict[str, float]:
    return {
        "physical": value,
        "intellect": value,
        "symmetry": value,
        "mating drive": value,
        "neurochemical": value,
        "courage": value,
        "temperance": value,
        "patience": value,
        "wit": value,
        "friendliness": value,
        "modesty": value,
        "ambition": value,
        "frugality": value,
        "persuasion": value,
        "curiosity": value,
        "justice": value,
        "humility": value,
        "generosity": value,
        "empathy": value,
        "discipline": value,
        "adaptability": value,
        "resilience": value,
        "focus": value,
        "honesty": value,
        "creativity": value,
        "assertiveness": value,
        "loyalty": value,
        "nurturance": value,
        "perception": value,
        "civics": value,
    }


def _ctx(cfg: Path, sav: Path, *, year: int, history_start: int) -> SimulationContext:
    return SimulationContext(
        db_path=cfg,
        save_db_path=sav,
        world="default",
        simulation_start_year=year,
        history_equivalent_start_year=history_start,
        current_year=year,
        placename_rng_salt=11,
    )


class TestSimulationCareers(unittest.TestCase):
    def test_normalize_composite_band_maps_peak_and_excessive(self) -> None:
        self.assertEqual(normalize_composite_band("peak"), "optimal")
        self.assertEqual(normalize_composite_band("excessive"), "excess")
        self.assertEqual(normalize_composite_band("deficient"), "deficient")

    def test_score_composite_row_respects_disqualifiers(self) -> None:
        row_plain = {
            "composite_id": "X",
            "component_1_trait": "focus",
            "component_1_position": "peak",
            "component_2_trait": "courage",
            "component_2_position": "deficient",
            "component_3_trait": "",
            "component_3_position": "",
            "disqualifier_1_trait": "",
            "disqualifier_1_position": "",
            "disqualifier_2_trait": "",
            "disqualifier_2_position": "",
        }
        row_dq = {
            **row_plain,
            "disqualifier_1_trait": "friendliness",
            "disqualifier_1_position": "deficient",
        }
        g = {"focus": 0.0, "courage": -80.0, "friendliness": -95.0}
        p = _person(birthyear=2000, genome=g)
        base = score_composite_row(p, row_plain)
        penalized = score_composite_row(p, row_dq)
        self.assertIsNotNone(base)
        self.assertIsNotNone(penalized)
        assert base is not None and penalized is not None
        self.assertGreater(base, GENOME_COMPOSITE_MIN_SCORE)
        self.assertLess(penalized, GENOME_COMPOSITE_MIN_SCORE)
        self.assertGreater(base, penalized)

    def test_significant_composite_names_sorted_by_score_desc(self) -> None:
        hi = {
            "composite_id": "Z_HI",
            "composite_name": "Z Hi",
            "short_definition": "Ignored longer text.",
            "component_1_trait": "focus",
            "component_1_position": "peak",
            "component_2_trait": "courage",
            "component_2_position": "deficient",
            "component_3_trait": "wit",
            "component_3_position": "peak",
            "disqualifier_1_trait": "",
            "disqualifier_1_position": "",
            "disqualifier_2_trait": "",
            "disqualifier_2_position": "",
        }
        lo = {
            "composite_id": "A_LO",
            "composite_name": "A Lo",
            "short_definition": "Ignored longer text.",
            "component_1_trait": "focus",
            "component_1_position": "peak",
            "component_2_trait": "courage",
            "component_2_position": "deficient",
            "component_3_trait": "",
            "component_3_position": "",
            "disqualifier_1_trait": "",
            "disqualifier_1_position": "",
            "disqualifier_2_trait": "",
            "disqualifier_2_position": "",
        }
        p = _person(
            birthyear=2000,
            genome={"focus": 0.0, "courage": -80.0, "wit": 0.0},
        )
        labels = significant_composite_names(p, (lo, hi), threshold=0.4)
        self.assertEqual(labels[0], "z hi")
        self.assertEqual(labels[1], "a lo")

    def test_split_person_export_life_stage_and_composites(self) -> None:
        p = replace(
            _person(birthyear=2000, genome={"focus": 0.0}),
            life_stage="teen",
            genome_composite_names=("Quiet Beauty",),
            genome_trait_phrases=("very cowardly",),
        )
        fixed, overlay = split_person_for_export(p)
        self.assertEqual(overlay.get("life_stage"), "teen")
        self.assertNotIn("life_stage", fixed)
        self.assertEqual(fixed.get("genome_composite_names"), ("Quiet Beauty",))
        self.assertNotIn("genome_composite_names", overlay)
        self.assertEqual(fixed.get("genome_trait_phrases"), ("very cowardly",))
        self.assertNotIn("genome_trait_phrases", overlay)
        self.assertNotIn("age", overlay)
        fixed2, overlay2 = split_person_for_export(p, as_of_simulation_year=2010)
        self.assertEqual(overlay2.get("age"), 10)

    def test_parse_job_token_strips_sex_markers(self) -> None:
        self.assertEqual(_parse_job_token("smith"), ("smith", None))
        self.assertEqual(_parse_job_token("guard [M]"), ("guard", "male"))
        self.assertEqual(_parse_job_token("midwife [F]"), ("midwife", "female"))

    def test_job_allowed_respects_male_female_tags_and_exceptions(self) -> None:
        base_g = {"focus": 0.0, "mating drive": -50.0, "physical": -25.0}
        female_ok = replace(
            _person(birthyear=1980, genome=dict(base_g)),
            gender="Female",
            gender_mind="masculine",
        )
        self.assertTrue(_job_allowed_for_person(female_ok, "male"))
        female_bad_mind = replace(female_ok, gender_mind="feminine")
        self.assertFalse(_job_allowed_for_person(female_bad_mind, "male"))
        male_ok = replace(
            _person(birthyear=1980, genome={**base_g, "physical": -35.0}),
            gender="Male",
            gender_mind="feminine",
        )
        self.assertTrue(_job_allowed_for_person(male_ok, "female"))
        male_high_phys = replace(male_ok, genome={**base_g, "physical": -10.0})
        self.assertFalse(_job_allowed_for_person(male_high_phys, "female"))

    def test_female_blocked_from_male_only_job_without_cross_gender_exception(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            _create_config_db(cfg)
            conn = sqlite3.connect(cfg)
            conn.execute(
                "UPDATE genome_jobs SET modern_jobs = ?, modern_premium_jobs = NULL WHERE trait = 'focus'",
                ("solo career [M]",),
            )
            conn.commit()
            conn.close()
            person = replace(
                _person(birthyear=1980, genome={"focus": 0.0}),
                gender="Female",
                gender_mind="feminine",
            )
            self.assertIsNone(
                choose_career_assignment(
                    person,
                    person_id=1,
                    db_path=cfg,
                    era="modern",
                    year=2000,
                    historical_year=2000,
                    salt=0,
                )
            )

    def test_female_cross_gender_exception_can_draw_male_only_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            _create_config_db(cfg)
            conn = sqlite3.connect(cfg)
            conn.execute(
                "UPDATE genome_jobs SET modern_jobs = ?, modern_premium_jobs = NULL WHERE trait = 'focus'",
                ("solo career [M]",),
            )
            conn.commit()
            conn.close()
            person = replace(
                _person(
                    birthyear=1980,
                    genome={
                        "focus": 0.0,
                        "mating drive": -50.0,
                        "physical": -25.0,
                    },
                ),
                gender="Female",
                gender_mind="masculine",
            )
            assignment = choose_career_assignment(
                person,
                person_id=1,
                db_path=cfg,
                era="modern",
                year=2000,
                historical_year=2000,
                salt=0,
            )
            self.assertIsNotNone(assignment)
            assert assignment is not None
            self.assertEqual(assignment.job, "solo career")
            self.assertEqual(assignment.job_sex_restriction, "male")
            self.assertTrue(assignment.cross_gender_job_exception)

    def test_male_cross_gender_exception_can_draw_female_only_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            _create_config_db(cfg)
            conn = sqlite3.connect(cfg)
            conn.execute(
                "UPDATE genome_jobs SET modern_jobs = ?, modern_premium_jobs = NULL WHERE trait = 'focus'",
                ("weaver role [F]",),
            )
            conn.commit()
            conn.close()
            person = replace(
                _person(
                    birthyear=1980,
                    genome={
                        "focus": 0.0,
                        "mating drive": -50.0,
                        "physical": -35.0,
                    },
                ),
                gender="Male",
                gender_mind="feminine",
            )
            assignment = choose_career_assignment(
                person,
                person_id=1,
                db_path=cfg,
                era="modern",
                year=2000,
                historical_year=2000,
                salt=0,
            )
            self.assertIsNotNone(assignment)
            assert assignment is not None
            self.assertEqual(assignment.job, "weaver role")
            self.assertEqual(assignment.job_sex_restriction, "female")
            self.assertTrue(assignment.cross_gender_job_exception)

    def test_scoring_matches_genome_band_semantics(self) -> None:
        self.assertGreater(score_genome_job_row(0, "optimal"), score_genome_job_row(80, "optimal"))
        self.assertGreater(score_genome_job_row(-80, "deficient"), score_genome_job_row(80, "deficient"))
        self.assertGreater(score_genome_job_row(80, "excess"), score_genome_job_row(-80, "excess"))
        self.assertGreater(score_genome_job_row(-50, "deficient"), score_genome_job_row(-100, "deficient"))
        self.assertGreater(score_genome_job_row(50, "excess"), score_genome_job_row(100, "excess"))
        self.assertGreater(score_genome_job_row(0, "optimal"), score_genome_job_row(50, "optimal"))

    def test_genome_jobs_society_need_is_bounded_and_strict(self) -> None:
        with (_ROOT / "config" / "genome_jobs.csv").open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        self.assertTrue(rows)
        self.assertIn("society_need", rows[0])
        self.assertIn("selfish_desperate", rows[0])
        by_key = {
            (row["trait"], row["deviation_band"]): (
                float(row["society_need"]),
                float(row["selfish_desperate"]),
            )
            for row in rows
        }
        self.assertTrue(
            all(
                0.0 <= society_need <= 1.0
                and 0.0 <= selfish_desperate <= 1.0
                for society_need, selfish_desperate in by_key.values()
            )
        )
        self.assertEqual(by_key[("physical", "optimal")][0], 1.0)
        self.assertEqual(by_key[("justice", "deficient")][0], 0.05)
        self.assertLess(
            by_key[("nurturance", "excess")][0],
            by_key[("nurturance", "optimal")][0],
        )
        self.assertGreater(by_key[("justice", "deficient")][1], 0.9)
        self.assertLess(by_key[("justice", "optimal")][1], 0.2)
        self.assertGreater(by_key[("intellect", "deficient")][1], 0.7)

    def test_refresh_current_people_life_stages_updates_from_age(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=2000,
                refresh_config=False,
                flush_run_store=False,
            ) as ctx:
                st = ctx.ensure_active_settlement_for_region("aeria_north")
                p = replace(
                    _person(birthyear=1985, genome={"focus": 0.0}),
                    ethnic="Anglo-Norman",
                    life_stage="child",
                    birthplace_region_id="aeria_north",
                    birthplace_settlement_id=st.settlement_id,
                    current_settlement_id=st.settlement_id,
                )
                rec = ctx.add_person(person=p, is_founder=True)
                ctx.refresh_current_people_life_stages(2000)
                self.assertEqual(rec.person.life_stage, "child")
                ctx.refresh_current_people_life_stages(2001)
                self.assertEqual(rec.person.life_stage, "mature")

    def test_career_fitness_rewards_near_perfect_and_penalizes_high_deviation(self) -> None:
        fit = _person(birthyear=1980, genome=_full_genome(0.0))
        shaky = _person(birthyear=1980, genome=_full_genome(90.0))

        self.assertGreater(career_fitness_score(fit), 0.95)
        self.assertLess(career_fitness_score(shaky), 0.15)
        details = career_fitness(shaky)
        self.assertIn("physical", details.high_deviation_traits)
        self.assertGreater(details.weighted_high_deviation_count, 30.0)

    def test_job_category_fitness_blends_general_score_with_selected_trait(self) -> None:
        person = _person(
            birthyear=1980,
            genome={"focus": 0.0, "courage": 50.0},
        )

        focus_fit, focus_match = job_category_fitness_score(
            person,
            career_score=0.4,
            trait="focus",
            deviation_band="optimal",
        )
        courage_fit, courage_match = job_category_fitness_score(
            person,
            career_score=0.4,
            trait="courage",
            deviation_band="excess",
        )
        bad_fit, bad_match = job_category_fitness_score(
            person,
            career_score=0.4,
            trait="courage",
            deviation_band="deficient",
        )

        self.assertEqual(focus_match, 1.0)
        self.assertEqual(courage_match, 1.0)
        self.assertGreater(focus_fit, bad_fit)
        self.assertGreater(courage_fit, bad_fit)
        self.assertEqual(bad_match, 0.0)

    def test_job_category_fitness_for_existing_title_uses_catalog_row(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            _create_config_db(cfg)
            person = _person(
                birthyear=1980,
                genome={"focus": 0.0, "courage": -80.0},
            )

            score, match, trait, band = job_category_fitness_for_title(
                person,
                career_score=0.4,
                job_title="analyst",
                era="modern",
                db_path=cfg,
            )

        self.assertEqual(match, 1.0)
        self.assertEqual(score, 0.7)
        self.assertEqual(trait, "focus")
        self.assertEqual(band, "optimal")

    def test_suggested_traits_weigh_more_in_fitness(self) -> None:
        base = _full_genome(0.0)
        high_weight_bad = dict(base)
        low_weight_bad = dict(base)
        high_weight_bad["physical"] = 90.0
        low_weight_bad["mating drive"] = 90.0

        self.assertLess(
            career_fitness_score(_person(birthyear=1980, genome=high_weight_bad)),
            career_fitness_score(_person(birthyear=1980, genome=low_weight_bad)),
        )

    def test_probability_helpers_follow_fitness_and_pressure(self) -> None:
        self.assertLess(
            job_loss_probability(0.9, 0.4),
            job_loss_probability(0.2, 1.3),
        )
        self.assertGreater(
            rehire_probability(0.9, 0.4, 2),
            rehire_probability(0.2, 1.3, 0),
        )

    def test_era_and_eligibility_age_rules(self) -> None:
        self.assertEqual(resolve_job_era(-5000), "prehistoric")
        self.assertEqual(resolve_job_era(-2000), "bronze_age")
        self.assertEqual(resolve_job_era(-800), "iron_age")
        self.assertEqual(resolve_job_era(1000), "medieval")
        self.assertEqual(resolve_job_era(2020), "modern")
        self.assertLess(job_eligibility_age(18, "prehistoric"), job_eligibility_age(18, "modern"))
        self.assertEqual(job_eligibility_age(18, "modern"), 18)

    def test_assignment_uses_era_jobs_and_ignores_annotation_columns(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            _create_config_db(cfg)
            person = _person(birthyear=1980, genome={"focus": 0.0, "courage": -80.0})

            assignment = choose_career_assignment(
                person,
                person_id=1,
                db_path=cfg,
                era="modern",
                year=2000,
                historical_year=2000,
                salt=3,
            )

        self.assertIsNotNone(assignment)
        assert assignment is not None
        self.assertIn(assignment.job, {"software engineer", "analyst"})
        self.assertEqual(assignment.trait, "focus")
        self.assertEqual(assignment.status_tendency, "high")
        self.assertEqual(assignment.leader_quality, "strong")
        self.assertEqual(assignment.job_tier, "common")

    def test_premium_job_selected_when_fitness_high(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            _create_config_db(cfg)
            person = _person(birthyear=1980, genome={"focus": 0.0, "courage": -80.0})
            with (
                patch(
                    "library.simulation_careers.PREMIUM_JOB_FITNESS_THRESHOLD",
                    0.0,
                ),
                patch("library.simulation_careers.PREMIUM_JOB_MAX_PROB", 1.0),
            ):
                assignment = choose_career_assignment(
                    person,
                    person_id=1,
                    db_path=cfg,
                    era="modern",
                    year=2000,
                    historical_year=2000,
                    salt=3,
                    fitness_score=0.99,
                )
        self.assertIsNotNone(assignment)
        assert assignment is not None
        self.assertEqual(assignment.job, "fixture premium job")
        self.assertEqual(assignment.job_tier, "premium")

    def test_premium_never_selected_when_fitness_low(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            _create_config_db(cfg)
            person = _person(birthyear=1980, genome={"focus": 0.0, "courage": -80.0})
            for salt in range(40):
                assignment = choose_career_assignment(
                    person,
                    person_id=1,
                    db_path=cfg,
                    era="modern",
                    year=2000,
                    historical_year=2000,
                    salt=salt,
                    fitness_score=0.2,
                )
                self.assertIsNotNone(assignment)
                assert assignment is not None
                self.assertEqual(assignment.job_tier, "common")
                self.assertIn(assignment.job, {"software engineer", "analyst"})

    def test_job_market_favors_simple_jobs_in_small_settlements(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            conn = sqlite3.connect(cfg)
            conn.execute(
                """
                CREATE TABLE genome_jobs (
                    trait TEXT, deviation_band TEXT, descriptor TEXT,
                    status_tendency TEXT, leader_quality TEXT, leader_tendency TEXT,
                    modern_jobs TEXT, modern_premium_jobs TEXT,
                    society_need REAL, selfish_desperate REAL
                )
                """
            )
            conn.execute(
                "INSERT INTO genome_jobs VALUES ('focus','optimal','steady','middle','moderate','medium','farmer',NULL,1.0,0.1)"
            )
            conn.execute(
                "INSERT INTO genome_jobs VALUES ('curiosity','optimal','specialist','high','strong','medium','observatory scholar',NULL,0.75,0.1)"
            )
            conn.execute(
                """
                CREATE TABLE job_economics (
                    job_key TEXT, era TEXT, row_kind TEXT,
                    pool_draw REAL, wage_yield REAL, value_add REAL, tax_rate REAL
                )
                """
            )
            conn.execute("INSERT INTO job_economics VALUES ('*','modern','base',0.2,0.3,0.25,0.05)")
            conn.execute("INSERT INTO job_economics VALUES ('farmer','modern','deviation','','0.8','','')")
            conn.execute("INSERT INTO job_economics VALUES ('observatory scholar','modern','deviation','','3.5','','')")
            conn.commit()
            conn.close()

            person = _person(birthyear=1980, genome={"focus": 0.0, "curiosity": 0.0})
            small = choose_career_assignment(
                person,
                person_id=1,
                db_path=cfg,
                era="modern",
                year=2000,
                historical_year=2000,
                salt=1,
                top_n=1,
                settlement_resident_count=80,
            )
            large = choose_career_assignment(
                person,
                person_id=1,
                db_path=cfg,
                era="modern",
                year=2000,
                historical_year=2000,
                salt=1,
                top_n=1,
                settlement_resident_count=25_000,
            )

        self.assertIsNotNone(small)
        self.assertIsNotNone(large)
        assert small is not None and large is not None
        self.assertEqual(small.job, "farmer")
        self.assertEqual(large.job, "observatory scholar")
        self.assertLess(small.job_market_demand_score, large.job_market_demand_score)

    def test_job_market_saturation_suppresses_overfilled_local_job(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            conn = sqlite3.connect(cfg)
            conn.execute(
                """
                CREATE TABLE genome_jobs (
                    trait TEXT, deviation_band TEXT, descriptor TEXT,
                    status_tendency TEXT, leader_quality TEXT, leader_tendency TEXT,
                    modern_jobs TEXT, modern_premium_jobs TEXT,
                    society_need REAL, selfish_desperate REAL
                )
                """
            )
            conn.execute(
                "INSERT INTO genome_jobs VALUES ('focus','optimal','steady','middle','moderate','medium','smith',NULL,0.8,0.1)"
            )
            conn.execute(
                "INSERT INTO genome_jobs VALUES ('curiosity','optimal','trade','middle','moderate','medium','merchant',NULL,0.8,0.1)"
            )
            conn.execute(
                """
                CREATE TABLE job_economics (
                    job_key TEXT, era TEXT, row_kind TEXT,
                    pool_draw REAL, wage_yield REAL, value_add REAL, tax_rate REAL
                )
                """
            )
            conn.execute("INSERT INTO job_economics VALUES ('*','modern','base',0.2,0.4,0.25,0.05)")
            conn.commit()
            conn.close()

            person = _person(birthyear=1980, genome={"focus": 0.0, "curiosity": 0.0})
            open_market = choose_career_assignment(
                person,
                person_id=1,
                db_path=cfg,
                era="modern",
                year=2000,
                historical_year=2000,
                salt=2,
                top_n=1,
                settlement_resident_count=90,
            )
            saturated = choose_career_assignment(
                person,
                person_id=1,
                db_path=cfg,
                era="modern",
                year=2000,
                historical_year=2000,
                salt=2,
                top_n=1,
                settlement_resident_count=90,
                current_job_counts={"smith": 8},
                current_family_counts={"craft": 12},
            )

        self.assertIsNotNone(open_market)
        self.assertIsNotNone(saturated)
        assert open_market is not None and saturated is not None
        self.assertEqual(open_market.job, "smith")
        self.assertEqual(saturated.job, "merchant")

    def test_desperation_signal_rises_with_unemployment_and_low_savings(self) -> None:
        stable = career_desperation_score(
            resource_pressure=0.4,
            unemployment_years=0,
            household_prosperity=8.0,
        )
        desperate = career_desperation_score(
            resource_pressure=1.6,
            unemployment_years=5,
            household_prosperity=0.1,
        )
        self.assertGreater(desperate, stable)

    def test_premium_roll_probability_scales_with_fitness(self) -> None:
        self.assertEqual(premium_job_roll_probability(None), 0.0)
        self.assertEqual(
            premium_job_roll_probability(PREMIUM_JOB_FITNESS_THRESHOLD - 0.01),
            0.0,
        )
        self.assertAlmostEqual(
            premium_job_roll_probability(1.0),
            PREMIUM_JOB_MAX_PROB,
            places=6,
        )

    def test_annual_tick_assigns_at_era_specific_age(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            _create_config_db(cfg)
            ctx = _ctx(cfg, sav, year=-5000, history_start=-5000)
            rec = SimulationPersonRecord(
                person_id=1,
                person=_person(
                    birthyear=-5009,
                    genome={"focus": 0.0, "courage": -80.0},
                    min_fertility_age=18,
                ),
                is_founder=True,
            )
            ctx.people = [rec]
            ctx.id_to_record = {1: rec}
            ctx.current_people_ids = {1}

            with patch("library.simulation_careers.rehire_probability", return_value=1.0):
                simulation_careers_annual_tick(ctx, -5000)

        self.assertIsNotNone(rec.person.job)
        self.assertEqual(rec.person.job_era, "prehistoric")
        self.assertEqual(rec.person.job_assigned_year, -5000)
        event_types = [event_type for _year, event_type, _payload in ctx._pending_simulation_events]
        self.assertIn("career_fitness_updated", event_types)
        self.assertIn("job_assigned", event_types)

    def test_assign_career_sets_genome_composite_names(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            _create_config_db(cfg)
            ctx = _ctx(cfg, sav, year=2000, history_start=2000)
            rec = SimulationPersonRecord(
                person_id=1,
                person=_person(
                    birthyear=1982,
                    genome={"focus": 0.0, "courage": -80.0},
                    min_fertility_age=18,
                ),
                is_founder=True,
            )
            ctx.people = [rec]
            ctx.id_to_record = {1: rec}
            ctx.current_people_ids = {1}

            assign_career_if_eligible(ctx, rec, 2000)
            self.assertIsNotNone(rec.person.job)
            self.assertIn("fixture composite", rec.person.genome_composite_names)
            self.assertIn("cowardly", rec.person.genome_trait_phrases)
            self.assertIn("incredibly focused", rec.person.genome_trait_phrases)
            job_payload = next(
                pl
                for _y, et, pl in ctx._pending_simulation_events
                if et == "job_assigned"
            )
            self.assertEqual(
                job_payload.get("genome_composite_names"),
                ["fixture composite"],
            )
            self.assertEqual(
                list(job_payload.get("genome_trait_phrases") or ()),
                list(rec.person.genome_trait_phrases),
            )
            self.assertIn("career_fitness_score", job_payload)
            self.assertIn("job_trait_match_score", job_payload)
            self.assertNotEqual(
                job_payload["fitness_score"],
                job_payload["career_fitness_score"],
            )

    def test_modern_waits_until_maturity(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            _create_config_db(cfg)
            ctx = _ctx(cfg, sav, year=2000, history_start=2000)
            rec = SimulationPersonRecord(
                person_id=1,
                person=_person(
                    birthyear=1983,
                    genome={"focus": 0.0, "courage": -80.0},
                    min_fertility_age=18,
                ),
                is_founder=True,
            )
            ctx.people = [rec]
            ctx.id_to_record = {1: rec}
            ctx.current_people_ids = {1}

            assign_career_if_eligible(ctx, rec, 2000)
            self.assertIsNone(rec.person.job)
            assign_career_if_eligible(ctx, rec, 2001)

        self.assertIsNotNone(rec.person.job)
        self.assertEqual(rec.person.job_era, "modern")
        self.assertIn("fixture composite", rec.person.genome_composite_names)
        self.assertIn("cowardly", rec.person.genome_trait_phrases)
        self.assertIn("incredibly focused", rec.person.genome_trait_phrases)

    def test_checkpoint_roundtrip_preserves_career_fields(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            _create_config_db(cfg)
            ctx = _ctx(cfg, sav, year=2000, history_start=2000)
            rec = SimulationPersonRecord(
                person_id=1,
                person=_person(
                    birthyear=1982,
                    genome={"focus": 0.0, "courage": -80.0},
                    min_fertility_age=18,
                ),
                is_founder=True,
            )
            ctx.people = [rec]
            ctx.id_to_record = {1: rec}
            ctx.current_people_ids = {1}
            assign_career_if_eligible(ctx, rec, 2000)
            checkpoint_simulation_to_save(ctx)
            with sqlite3.connect(sav) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS world_state (
                        world TEXT PRIMARY KEY,
                        start_year INTEGER NOT NULL,
                        current_year INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO world_state (world, start_year, current_year)
                    VALUES (?, ?, ?)
                    """,
                    ("default", 2000, 2000),
                )
                conn.commit()

            loaded = _ctx(cfg, sav, year=2000, history_start=2000)
            self.assertTrue(try_load_simulation_checkpoint(loaded))

        self.assertEqual(len(loaded.people), 1)
        self.assertEqual(loaded.people[0].person.job, rec.person.job)
        self.assertEqual(loaded.people[0].person.status_tendency, "high")
        self.assertEqual(loaded.people[0].person.leader_quality, "strong")
        self.assertEqual(
            loaded.people[0].person.genome_composite_names,
            ("fixture composite",),
        )
        self.assertEqual(
            loaded.people[0].person.genome_trait_phrases,
            ("incredibly focused", "cowardly"),
        )

    def test_job_loss_and_rehire_are_logged_as_events(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            _create_config_db(cfg)
            ctx = _ctx(cfg, sav, year=2000, history_start=2000)
            rec = SimulationPersonRecord(
                person_id=1,
                person=replace(
                    _person(birthyear=1980, genome={"focus": 0.0, "courage": -80.0}),
                    job="analyst",
                    job_era="modern",
                    employment_status="employed",
                ),
                is_founder=True,
            )
            ctx.people = [rec]
            ctx.id_to_record = {1: rec}
            ctx.current_people_ids = {1}
            fitness = career_fitness(rec.person)

            lose_job(
                ctx,
                rec,
                2000,
                reason="low_fitness",
                pressure=1.2,
                fitness=fitness,
            )
            assign_career_if_eligible(ctx, rec, 2001)

        event_types = [event_type for _year, event_type, _payload in ctx._pending_simulation_events]
        self.assertIn("job_lost", event_types)
        self.assertIn("unemployment_started", event_types)
        self.assertIn("job_assigned", event_types)
        self.assertIn("unemployment_ended", event_types)
        self.assertEqual(rec.person.employment_status, "employed")
        self.assertIsNone(rec.person.unemployment_started_year)
        job_lost_payload = next(
            payload
            for _year, event_type, payload in ctx._pending_simulation_events
            if event_type == "job_lost"
        )
        unemployment_payload = next(
            payload
            for _year, event_type, payload in ctx._pending_simulation_events
            if event_type == "unemployment_started"
        )
        self.assertEqual(job_lost_payload["trait"], "focus")
        self.assertEqual(job_lost_payload["deviation_band"], "optimal")
        self.assertIn("career_fitness_score", job_lost_payload)
        self.assertIn("job_trait_match_score", job_lost_payload)
        self.assertEqual(job_lost_payload["job_trait_match_score"], 1.0)
        self.assertNotEqual(
            job_lost_payload["fitness_score"],
            job_lost_payload["career_fitness_score"],
        )
        self.assertEqual(
            unemployment_payload["fitness_score"],
            job_lost_payload["fitness_score"],
        )

    def test_job_loss_and_rehire_fields_survive_checkpoint_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            _create_config_db(cfg)
            ctx = _ctx(cfg, sav, year=2000, history_start=2000)
            rec = SimulationPersonRecord(
                person_id=1,
                person=replace(
                    _person(birthyear=1980, genome={"focus": 0.0, "courage": -80.0}),
                    job="analyst",
                    job_era="modern",
                    employment_status="employed",
                ),
                is_founder=True,
            )
            ctx.people = [rec]
            ctx.id_to_record = {1: rec}
            ctx.current_people_ids = {1}
            lose_job(
                ctx,
                rec,
                2000,
                reason="resource_scarcity",
                pressure=1.3,
                fitness=career_fitness(rec.person),
            )
            checkpoint_simulation_to_save(ctx)
            with sqlite3.connect(sav) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS world_state (
                        world TEXT PRIMARY KEY,
                        start_year INTEGER NOT NULL,
                        current_year INTEGER NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO world_state (world, start_year, current_year)
                    VALUES (?, ?, ?)
                    """,
                    ("default", 2000, 2000),
                )
                conn.commit()

            loaded = _ctx(cfg, sav, year=2000, history_start=2000)
            self.assertTrue(try_load_simulation_checkpoint(loaded))

        p = loaded.people[0].person
        self.assertEqual(p.employment_status, "unemployed")
        self.assertEqual(p.last_job, "analyst")
        self.assertEqual(p.job_lost_year, 2000)
        self.assertEqual(p.unemployment_started_year, 2000)
        self.assertIsNotNone(p.career_fitness_score)

    def test_job_seeker_migration_moves_household_and_logs_events(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=1000,
                refresh_config=False,
                placename_rng_salt=42,
                flush_run_store=False,
            ) as ctx:
                st_n = ctx.ensure_active_settlement_for_region("aeria_north")
                ctx.ensure_active_settlement_for_region("aeria_granite_range")
                m = _person(birthyear=970, genome=_full_genome(90.0))
                f = replace(
                    _person(birthyear=970, genome=_full_genome(0.0)),
                    gender="Female",
                )
                c = replace(
                    _person(birthyear=994, genome=_full_genome(0.0)),
                    gender="Female",
                )
                m = replace(
                    m,
                    birthplace_region_id="aeria_north",
                    birthplace_settlement_id=st_n.settlement_id,
                    current_settlement_id=st_n.settlement_id,
                    employment_status="unemployed",
                    unemployment_started_year=998,
                    job=None,
                )
                f = replace(
                    f,
                    birthplace_region_id="aeria_north",
                    birthplace_settlement_id=st_n.settlement_id,
                    current_settlement_id=st_n.settlement_id,
                )
                c = replace(
                    c,
                    birthplace_region_id="aeria_north",
                    birthplace_settlement_id=st_n.settlement_id,
                    current_settlement_id=st_n.settlement_id,
                )
                mr = ctx.add_person(person=m, is_founder=False)
                fr = ctx.add_person(person=f, is_founder=False)
                ctx.add_couple(mr.person_id, fr.person_id)
                cr = ctx.add_person(
                    person=c,
                    is_founder=False,
                    father_id=mr.person_id,
                    mother_id=fr.person_id,
                )
                with patch(
                    "library.simulation_careers.job_seeker_migration_probability",
                    return_value=1.0,
                ):
                    moved = maybe_migrate_job_seeker_household(
                        ctx,
                        mr,
                        1000,
                        fitness=career_fitness(mr.person),
                        pressure=1.4,
                    )

                self.assertTrue(moved)
                moved_sids = {
                    ctx.id_to_record[pid].person.current_settlement_id
                    for pid in (mr.person_id, fr.person_id, cr.person_id)
                }
                self.assertEqual(len(moved_sids), 1)
                self.assertEqual(next(iter(moved_sids)), st_n.settlement_id)
                self.assertGreaterEqual(len(ctx.pending_settlement_moves), 3)
                ctx.apply_pending_settlement_moves(1001)
                moved_sids = {
                    ctx.id_to_record[pid].person.current_settlement_id
                    for pid in (mr.person_id, fr.person_id, cr.person_id)
                }
                self.assertEqual(len(moved_sids), 1)
                self.assertNotEqual(next(iter(moved_sids)), st_n.settlement_id)
                event_types = [
                    event_type for _year, event_type, _payload in ctx._pending_simulation_events
                ]
                self.assertIn("job_seeker_migration", event_types)
                self.assertIn("settlement_move_planned", event_types)
                move_events = [
                    payload
                    for _year, event_type, payload in ctx._pending_simulation_events
                    if event_type == "settlement_moved"
                    and payload.get("move_reason") == "job_seeker_migration"
                ]
                self.assertGreaterEqual(len(move_events), 3)


if __name__ == "__main__":
    unittest.main()
