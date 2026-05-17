"""Tests for single-save simulation clock behavior."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from library.generator import _resolve_birthyear
from library.world_time import (
    advance_world_time,
    ensure_world_state,
    reset_world_time,
    resolve_world_current_year,
)


def _seed_world_start(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE world_start (world TEXT PRIMARY KEY, start_year TEXT)")
        conn.execute(
            "INSERT INTO world_start (world, start_year) VALUES (?, ?)",
            ("default", "1000"),
        )
        conn.execute(
            "INSERT INTO world_start (world, start_year) VALUES (?, ?)",
            ("alt", "1200"),
        )
        conn.commit()
    finally:
        conn.close()


class TestWorldTime(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.sqlite"
        _seed_world_start(self.db_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_missing_world_state_falls_back_to_world_start(self) -> None:
        start, current = ensure_world_state(db_path=self.db_path, world="default")
        self.assertEqual((start, current), (1000, 1000))
        self.assertEqual(
            resolve_world_current_year(db_path=self.db_path, world="default"), 1000
        )

    def test_save_clock_is_single_world(self) -> None:
        ensure_world_state(db_path=self.db_path, world="default")
        self.assertEqual(ensure_world_state(db_path=self.db_path, world="alt"), (1000, 1000))

        self.assertEqual(advance_world_time(db_path=self.db_path, world="default"), 1001)
        self.assertEqual(
            resolve_world_current_year(db_path=self.db_path, world="default"), 1001
        )
        self.assertEqual(resolve_world_current_year(db_path=self.db_path, world="alt"), 1001)

    def test_world_time_persists_across_calls(self) -> None:
        self.assertEqual(advance_world_time(db_path=self.db_path, world="default"), 1001)
        self.assertEqual(advance_world_time(db_path=self.db_path, world="default"), 1002)
        self.assertEqual(
            resolve_world_current_year(db_path=self.db_path, world="default"), 1002
        )

    def test_reset_world_time_accepts_explicit_earlier_start_year(self) -> None:
        ensure_world_state(db_path=self.db_path, world="default")

        self.assertEqual(
            reset_world_time(db_path=self.db_path, world="default", start_year=-1000),
            (-1000, -1000),
        )
        self.assertEqual(ensure_world_state(db_path=self.db_path, world="default"), (-1000, -1000))
        self.assertEqual(advance_world_time(db_path=self.db_path, world="default"), -999)

    def test_resolve_birthyear_determinism(self) -> None:
        self.assertEqual(
            _resolve_birthyear(
                birthyear=915,
                birth_reference_year=None,
                current_year=1000,
                age=20,
            ),
            915,
        )
        self.assertEqual(
            _resolve_birthyear(
                birthyear=None,
                birth_reference_year=None,
                current_year=1000,
                age=20,
            ),
            980,
        )
        self.assertEqual(
            _resolve_birthyear(
                birthyear=None,
                birth_reference_year=998,
                current_year=1000,
                age=20,
            ),
            978,
        )


if __name__ == "__main__":
    unittest.main()
