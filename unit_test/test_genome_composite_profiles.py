import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.genome_composites import refresh_genome_composite_profile
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


def _person_with_traits(**overrides: float) -> Person:
    traits = {trait: 0.0 for trait in _TRAITS}
    traits.update({str(k): float(v) for k, v in overrides.items()})
    return Person(
        first_name="Ada",
        last_name="Profile",
        gender="Female",
        ethnic="Alemannic",
        species="Human",
        birthyear=970,
        genome=traits,
        mind_body=dict(traits),
        attractiveness_01=0.7,
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
            all(0.0 <= score <= 1.0 for score in strong.genome_composite_scores.values())
        )
        self.assertGreater(
            strong.genome_composite_scores["physical_strength"],
            frail.genome_composite_scores["physical_strength"],
        )

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
