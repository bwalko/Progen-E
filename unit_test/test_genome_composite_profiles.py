import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.genome_composites import (
    GENOME_COMPOSITE_REVEAL_ORDER,
    composite_score_age,
    genome_composite_rating_reveal_age,
    refresh_genome_composite_profile,
)
from library.passive_population import PassivePerson
from library.person import Person
from library.simulation_context import SimulationContext


_TRAITS = (
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


EXPECTED_RATINGS = {
    "sexual_magnetism",
    "sexual_object",
    "insanity",
    "physical_strength",
    "practical_intellect",
    "creative_intellect",
    "make_friends",
    "make_enemies",
    "ruthless_ambition",
    "good_done_desire",
    "evil_done_desire",
    "convince_people",
    "disguise_motive",
    "honest_work_desire",
    "enrich_self_desire",
    "revenge_desire",
    "force_get_way_desire",
    "psychopathy",
    "lie_or_cheat_willingness",
    "lead_others_ability",
    "isolation_preference",
}


def _person_with_traits(
    *,
    gender: str = "Female",
    gender_mind: str | None = None,
    attractiveness_01: float = 0.7,
    birthyear: int = 970,
    deathyear: int | None = None,
    **overrides: float,
) -> Person:
    traits = {trait: 0.0 for trait in _TRAITS}
    traits.update({str(k): float(v) for k, v in overrides.items()})
    return Person(
        first_name="Ada",
        last_name="Profile",
        gender=gender,
        ethnic="Alemannic",
        species="Human",
        birthyear=birthyear,
        deathyear=deathyear,
        genome=traits,
        mind_body=dict(traits),
        attractiveness_01=attractiveness_01,
        gender_mind=gender_mind,
    )


class GenomeCompositeProfileTests(unittest.TestCase):
    def test_refresh_scores_all_configured_ratings_and_penalizes_weak_components(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            strong = refresh_genome_composite_profile(
                _person_with_traits(
                    physical=50,
                    discipline=0,
                    resilience=0,
                    courage=0,
                    focus=0,
                ),
                cfg,
            )
            frail = refresh_genome_composite_profile(
                _person_with_traits(
                    physical=-50,
                    discipline=0,
                    resilience=0,
                    courage=0,
                    focus=0,
                ),
                cfg,
            )

        self.assertEqual(EXPECTED_RATINGS, set(strong.genome_composite_scores))
        self.assertTrue(
            all(score >= 0.0 for score in strong.genome_composite_scores.values())
        )
        self.assertGreater(
            strong.genome_composite_scores["physical_strength"],
            frail.genome_composite_scores["physical_strength"],
        )

    def test_insanity_deviation_curve_uses_extreme_deviation_not_moderate_spread(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)

            moderate = refresh_genome_composite_profile(
                _person_with_traits(
                    neurochemical=35,
                    temperance=35,
                    focus=35,
                    curiosity=35,
                    perception=35,
                ),
                cfg,
            )
            normal_max = refresh_genome_composite_profile(
                _person_with_traits(
                    neurochemical=80,
                    temperance=80,
                    focus=80,
                    curiosity=80,
                    perception=80,
                ),
                cfg,
            )
            outlier = refresh_genome_composite_profile(
                _person_with_traits(
                    neurochemical=100,
                    temperance=100,
                    focus=100,
                    curiosity=100,
                    perception=100,
                ),
                cfg,
            )

        self.assertEqual(moderate.genome_composite_scores["insanity"], 0.0)
        self.assertAlmostEqual(normal_max.genome_composite_scores["insanity"], 1.0)
        self.assertGreater(outlier.genome_composite_scores["insanity"], 1.0)

    def test_nonlinear_rating_blend_requires_multiple_aligned_components(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            single_high = refresh_genome_composite_profile(
                _person_with_traits(
                    gender="Female",
                    gender_mind="feminine",
                    physical=100,
                    discipline=-100,
                    resilience=-100,
                    courage=-100,
                    focus=-100,
                ),
                cfg,
            )
            aligned = refresh_genome_composite_profile(
                _person_with_traits(
                    gender="Female",
                    gender_mind="feminine",
                    physical=100,
                    discipline=0,
                    resilience=0,
                    courage=0,
                    focus=0,
                ),
                cfg,
            )

        self.assertLess(single_high.genome_composite_scores["physical_strength"], 0.10)
        self.assertGreater(aligned.genome_composite_scores["physical_strength"], 1.0)

    def test_numeric_scores_can_exceed_one_without_clamping(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            scored = refresh_genome_composite_profile(
                _person_with_traits(
                    gender="Male",
                    gender_mind="masculine",
                    physical=100,
                    discipline=0,
                    resilience=0,
                    courage=0,
                    focus=0,
                ),
                cfg,
            )

        self.assertGreater(scored.genome_composite_scores["physical_strength"], 1.0)

    def test_body_and_mind_bonuses_apply_to_requested_ratings(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)

            feminine_object = refresh_genome_composite_profile(
                _person_with_traits(gender="Female", gender_mind="feminine"),
                cfg,
            )
            masculine_object = refresh_genome_composite_profile(
                _person_with_traits(gender="Female", gender_mind="masculine"),
                cfg,
            )
            masculine_magnetism = refresh_genome_composite_profile(
                _person_with_traits(gender="Male", gender_mind="masculine"),
                cfg,
            )
            feminine_magnetism = refresh_genome_composite_profile(
                _person_with_traits(gender="Male", gender_mind="feminine"),
                cfg,
            )
            male_strength = refresh_genome_composite_profile(
                _person_with_traits(gender="Male", gender_mind="feminine"),
                cfg,
            )
            female_strength = refresh_genome_composite_profile(
                _person_with_traits(gender="Female", gender_mind="feminine"),
                cfg,
            )
            masculine_mind_strength = refresh_genome_composite_profile(
                _person_with_traits(gender="Female", gender_mind="masculine"),
                cfg,
            )

        self.assertAlmostEqual(
            feminine_object.genome_composite_scores["sexual_object"]
            - masculine_object.genome_composite_scores["sexual_object"],
            0.05,
        )
        self.assertGreater(
            masculine_magnetism.genome_composite_scores["sexual_magnetism"]
            - feminine_magnetism.genome_composite_scores["sexual_magnetism"],
            0.0,
        )
        self.assertGreater(
            male_strength.genome_composite_scores["physical_strength"]
            - female_strength.genome_composite_scores["physical_strength"],
            0.05,
        )
        self.assertGreater(
            masculine_mind_strength.genome_composite_scores["physical_strength"]
            - female_strength.genome_composite_scores["physical_strength"],
            0.0,
        )

    def test_numeric_scores_roll_out_two_per_year_from_birth(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            newborn = refresh_genome_composite_profile(
                _person_with_traits(birthyear=1000),
                cfg,
                current_year=1000,
            )
            age_two = refresh_genome_composite_profile(
                _person_with_traits(birthyear=1000),
                cfg,
                current_year=1002,
            )
            dead_age_one = refresh_genome_composite_profile(
                _person_with_traits(birthyear=1000, deathyear=1001),
                cfg,
                current_year=1010,
            )

        self.assertEqual(
            list(newborn.genome_composite_scores),
            list(GENOME_COMPOSITE_REVEAL_ORDER[:2]),
        )
        self.assertEqual(
            list(age_two.genome_composite_scores),
            list(GENOME_COMPOSITE_REVEAL_ORDER[:6]),
        )
        self.assertEqual(
            list(dead_age_one.genome_composite_scores),
            list(GENOME_COMPOSITE_REVEAL_ORDER[:4]),
        )
        self.assertEqual(composite_score_age(dead_age_one, current_year=1010), 1)
        self.assertEqual(genome_composite_rating_reveal_age("sexual_magnetism"), 10)

    def test_passive_promotion_refreshes_genome_composite_profile(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            save = Path(td) / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            ctx = SimulationContext(
                db_path=cfg,
                save_db_path=save,
                world="default",
                simulation_start_year=1000,
                current_year=1000,
            )
            passive = ctx.add_passive_person(
                PassivePerson(
                    name="Ada Profile",
                    birthyear=970,
                    gender="Female",
                    species="Human",
                    ethnic="Alemannic",
                    birthplace_region_id="aeria_north",
                    birthplace_settlement_id="aeria_north:s1",
                    current_settlement_id="aeria_north:s1",
                )
            )

            promoted = ctx.promote_passive_person(
                passive.person_id,
                year=1000,
                reason="unit_test",
            )

        self.assertEqual(EXPECTED_RATINGS, set(promoted.person.genome_composite_scores))


if __name__ == "__main__":
    unittest.main()
