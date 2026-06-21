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

    def test_legacy_passive_cohort_backend_is_explicit_override(self) -> None:
        args = self._parse("--years", "1", "--use-passive-cohorts")

        self.assertFalse(args.use_nondetailed_directory)

    def test_old_nondetailed_flag_remains_accepted(self) -> None:
        args = self._parse("--years", "1", "--use-nondetailed-directory")

        self.assertTrue(args.use_nondetailed_directory)


if __name__ == "__main__":
    unittest.main()
