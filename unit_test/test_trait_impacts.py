"""Tests for genome trait banding and practical impact profiles."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.trait_impacts import (
    TRAIT_IMPACT_RULES,
    build_trait_impact_profile,
    center_signal_01,
    classify_trait,
    classify_traits,
    load_trait_definitions_from_sqlite,
    strong_deviation_signal_01,
    traits_missing_impact_rules,
)


class TestTraitImpacts(unittest.TestCase):
    def test_trait_bands_keep_center_ordinary_and_extremes_distinct(self) -> None:
        self.assertEqual(classify_trait("focus", 0).band, "center")
        self.assertEqual(classify_trait("focus", 25).band, "mild_deviation")
        self.assertEqual(classify_trait("focus", -50).band, "ordinary")
        self.assertEqual(classify_trait("focus", 75).band, "strong_deviation")
        self.assertEqual(classify_trait("focus", -95).band, "extreme_deviation")

        self.assertGreater(center_signal_01(8), 0.70)
        self.assertEqual(center_signal_01(50), 0.0)
        self.assertEqual(strong_deviation_signal_01(50), 0.0)
        self.assertGreater(strong_deviation_signal_01(95), 0.99)

    def test_practical_profile_ignores_ordinary_midpoints(self) -> None:
        ordinary = {
            "physical": 50,
            "neurochemical": 50,
            "justice": -50,
            "empathy": -50,
            "frugality": 50,
            "discipline": 50,
        }

        profile = build_trait_impact_profile(ordinary)

        self.assertEqual(profile.pressure("violence"), 0.0)
        self.assertEqual(profile.pressure("mortality_health"), 0.0)
        self.assertEqual(profile.pressure("finances"), 0.0)
        self.assertEqual(profile.benefit("work_capacity"), 0.0)

    def test_center_and_extreme_profiles_create_practical_consequences(self) -> None:
        centered = {
            "physical": 0,
            "discipline": 0,
            "empathy": 0,
            "justice": 0,
        }
        extreme = {
            "physical": -95,
            "neurochemical": 95,
            "justice": -95,
            "empathy": -95,
            "frugality": -95,
        }

        center_profile = build_trait_impact_profile(centered)
        extreme_profile = build_trait_impact_profile(extreme)

        self.assertGreater(center_profile.benefit("work_capacity"), 0.35)
        self.assertGreater(center_profile.benefit("legal_fallout"), 0.20)
        self.assertEqual(center_profile.pressure("violence"), 0.0)
        self.assertGreater(extreme_profile.pressure("violence"), 0.70)
        self.assertGreater(extreme_profile.pressure("mortality_health"), 0.70)
        self.assertGreater(extreme_profile.pressure("finances"), 0.30)
        self.assertGreater(extreme_profile.pressure("care_burden"), 0.35)

    def test_checked_in_genome_config_has_trait_impact_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_db = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(config_db)
            definitions = load_trait_definitions_from_sqlite(config_db)

        self.assertIn("neurochemical", definitions)
        self.assertEqual(traits_missing_impact_rules(definitions), ())
        self.assertEqual(set(definitions), set(TRAIT_IMPACT_RULES))

        classified = classify_traits(
            {trait: 0.0 for trait in definitions},
            definitions=definitions,
        )
        self.assertEqual(len(classified), len(definitions))
        self.assertEqual(
            classified["physical"].definition.optimal_centerpoint,
            "peak fitness",
        )
        self.assertIn("beneficial_center", classified["justice"].impact_kinds)
        self.assertIn("harmful_extreme", classified["neurochemical"].impact_kinds)
        self.assertIn("context_dependent_extreme", classified["ambition"].impact_kinds)


if __name__ == "__main__":
    unittest.main()
