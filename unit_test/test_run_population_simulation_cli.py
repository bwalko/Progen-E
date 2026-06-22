"""CLI default tests for production population simulation runs."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from utils import run_population_simulation


class TestRunPopulationSimulationCli(unittest.TestCase):
    def _parse(self, *args: str):
        with patch.object(sys, "argv", ["run_population_simulation.py", *args]):
            return run_population_simulation._parse_args()

    def test_nondetailed_directory_is_default_backend(self) -> None:
        args = self._parse("--years", "1")

        self.assertTrue(args.use_nondetailed_directory)
        self.assertIsNone(args.detailed_active_soft_cap)
        self.assertEqual(args.target_nondetailed_detailed_ratio, 50.0)

    def test_legacy_passive_cohort_backend_is_explicit_override(self) -> None:
        args = self._parse("--years", "1", "--use-passive-cohorts")

        self.assertFalse(args.use_nondetailed_directory)

    def test_old_nondetailed_flag_remains_accepted(self) -> None:
        args = self._parse("--years", "1", "--use-nondetailed-directory")

        self.assertTrue(args.use_nondetailed_directory)

    def test_explicit_detailed_cap_and_disabled_cap_remain_available(self) -> None:
        explicit = self._parse("--years", "1", "--detailed-active-soft-cap", "1200")
        disabled = self._parse("--years", "1", "--detailed-active-soft-cap", "0")

        self.assertEqual(explicit.detailed_active_soft_cap, 1200)
        self.assertEqual(disabled.detailed_active_soft_cap, 0)

    def test_auto_detailed_cap_helper_targets_fifty_to_one(self) -> None:
        self.assertEqual(
            run_population_simulation._detailed_soft_cap_from_ratio(50_000, 50.0),
            1000,
        )
        self.assertEqual(
            run_population_simulation._detailed_soft_cap_from_ratio(51, 50.0),
            1,
        )


if __name__ == "__main__":
    unittest.main()
