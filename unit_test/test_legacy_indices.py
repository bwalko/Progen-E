from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest


def _load_module():
    path = pathlib.Path(__file__).resolve().parents[1] / "library" / "legacy_indices.py"
    spec = importlib.util.spec_from_file_location("_test_legacy_indices", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


legacy_indices = _load_module()


class TestLegacyIndices(unittest.TestCase):
    def test_scores_are_bounded_and_named(self) -> None:
        scores = legacy_indices.legacy_index_scores({})

        self.assertTrue(scores)
        self.assertTrue(all(0.0 <= row.score <= 1.0 for row in scores))
        self.assertTrue(all(row.key and row.label and row.description for row in scores))

    def test_scholar_sage_uses_multiple_intellectual_traits(self) -> None:
        only_intellect = {
            "intellect": 0.0,
            "curiosity": 99.0,
            "focus": 99.0,
            "discipline": 99.0,
        }
        rounded_person = {
            "intellect": 0.0,
            "curiosity": 0.0,
            "focus": 0.0,
            "discipline": 0.0,
        }

        weak = {row.key: row.score for row in legacy_indices.legacy_index_scores(only_intellect)}
        strong = {row.key: row.score for row in legacy_indices.legacy_index_scores(rounded_person)}

        self.assertLess(weak["scholar_sage"], strong["scholar_sage"])
        self.assertGreater(strong["scholar_sage"], 0.95)

    def test_infamy_needs_more_than_one_bad_trait(self) -> None:
        single_bad = {
            "empathy": -100.0,
            "justice": 0.0,
            "honesty": 0.0,
            "persuasion": 0.0,
            "ambition": 0.0,
        }
        stacked_bad = {
            "empathy": -100.0,
            "justice": -100.0,
            "honesty": -100.0,
            "persuasion": 100.0,
            "ambition": 100.0,
        }

        weak = {row.key: row.score for row in legacy_indices.legacy_index_scores(single_bad)}
        strong = {row.key: row.score for row in legacy_indices.legacy_index_scores(stacked_bad)}

        self.assertLess(weak["infamous_predator"], strong["infamous_predator"])
        self.assertGreater(strong["infamous_predator"], 0.95)


if __name__ == "__main__":
    unittest.main()
