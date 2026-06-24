"""CLI default tests for production population simulation runs."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
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

    def test_default_detailed_cap_is_disabled_not_ratio_derived(self) -> None:
        args = self._parse("--years", "1")

        with patch.object(
            run_population_simulation,
            "_estimated_target_nondetailed_count",
            return_value=50_000,
        ):
            cap, mode, target = run_population_simulation._resolve_detailed_soft_cap(
                args,
                SimpleNamespace(),
            )

        self.assertIsNone(cap)
        self.assertEqual(mode, "disabled_default")
        self.assertEqual(target, 50_000)

    def test_explicit_detailed_cap_is_the_only_runtime_cap(self) -> None:
        explicit = self._parse("--years", "1", "--detailed-active-soft-cap", "1200")
        disabled = self._parse("--years", "1", "--detailed-active-soft-cap", "0")

        with patch.object(
            run_population_simulation,
            "_estimated_target_nondetailed_count",
            return_value=50_000,
        ):
            cap, mode, _target = run_population_simulation._resolve_detailed_soft_cap(
                explicit,
                SimpleNamespace(),
            )
            zero_cap, zero_mode, _target = run_population_simulation._resolve_detailed_soft_cap(
                disabled,
                SimpleNamespace(),
            )

        self.assertEqual(cap, 1200)
        self.assertEqual(mode, "explicit")
        self.assertIsNone(zero_cap)
        self.assertEqual(zero_mode, "disabled_explicit")


if __name__ == "__main__":
    unittest.main()
