"""Tests for birthed-person surname inheritance behavior."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from library.person import Person
from library.random_names import (
    _name_tables_cached,
    choose_random_first_last_from_birth,
)
from library.simulation_context import SimulationContext


def _seed_minimal_name_tables(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE ethnic (
                ethnic TEXT PRIMARY KEY,
                num_first_names TEXT,
                num_last_names TEXT,
                sep_first_names TEXT,
                sep_last_names TEXT,
                sur_kin_rate TEXT,
                sur_hails_rate TEXT,
                sur_lookup_rate TEXT,
                sur_none_rate TEXT,
                kin_m TEXT,
                kin_f TEXT,
                hails_from TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE first_name (
                ethnic TEXT,
                gender TEXT,
                name TEXT,
                rate TEXT,
                name_part TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE last_name (
                ethnic TEXT,
                name TEXT,
                rate TEXT
            )
            """
        )

        # Child ethnic: force sur_lookup roll.
        conn.execute(
            """
            INSERT INTO ethnic (
                ethnic, num_first_names, num_last_names, sep_first_names, sep_last_names,
                sur_kin_rate, sur_hails_rate, sur_lookup_rate, sur_none_rate,
                kin_m, kin_f, hails_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TestEthnic", "1;1", "1;1", "", "", "0", "0", "1", "0", "$son", "$dottir", "of"),
        )
        # Father ethnic row used for kin-form detection.
        conn.execute(
            """
            INSERT INTO ethnic (
                ethnic, num_first_names, num_last_names, sep_first_names, sep_last_names,
                sur_kin_rate, sur_hails_rate, sur_lookup_rate, sur_none_rate,
                kin_m, kin_f, hails_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("FatherEthnic", "1;1", "1;1", "", "", "1", "0", "0", "0", "$son", "$dottir", ""),
        )

        # Single first names to keep kin stem deterministic when needed.
        conn.execute(
            """
            INSERT INTO first_name (ethnic, gender, name, rate, name_part)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("TestEthnic", "Male", "Arne", "1", "0"),
        )
        conn.execute(
            """
            INSERT INTO first_name (ethnic, gender, name, rate, name_part)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("TestEthnic", "Female", "Ada", "1", "0"),
        )
        conn.execute(
            """
            INSERT INTO last_name (ethnic, name, rate)
            VALUES (?, ?, ?)
            """,
            ("TestEthnic", "LookupLast", "1"),
        )
        conn.commit()
    finally:
        conn.close()


class TestBirthSurnameRule(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "names.sqlite"
        _seed_minimal_name_tables(self.db_path)

    def tearDown(self) -> None:
        _name_tables_cached.cache_clear()
        self._tmpdir.cleanup()

    def test_lookup_uses_father_last_name_when_not_kin(self) -> None:
        _, last = choose_random_first_last_from_birth(
            ethnic="TestEthnic",
            gender="Male",
            birthplace="Placeholder",
            father_last_name="Sayers",
            father_ethnic="FatherEthnic",
            father_first_name="Robert",
            db_path=self.db_path,
        )
        self.assertEqual(last, "Sayers")

    def test_lookup_switches_to_kin_when_father_last_is_kin(self) -> None:
        _, last = choose_random_first_last_from_birth(
            ethnic="TestEthnic",
            gender="Male",
            birthplace="Placeholder",
            father_last_name="Oakson",  # matches FatherEthnic kin template `$son`
            father_ethnic="FatherEthnic",
            father_first_name="Robert",
            db_path=self.db_path,
        )
        # Should not inherit literal paternal kin surname on a lookup roll.
        self.assertNotEqual(last, "Oakson")
        # Should use child ethnic kin template with actual father's first name.
        self.assertEqual(last, "Robertson")

    def test_explicit_partnership_convention_overrides_child_roll(self) -> None:
        _, last = choose_random_first_last_from_birth(
            ethnic="TestEthnic",
            gender="Female",
            birthplace="Stonebridge",
            father_last_name="Sayers",
            father_ethnic="FatherEthnic",
            father_first_name="Robert",
            surname_convention="kin",
            db_path=self.db_path,
        )

        self.assertEqual(last, "Robertdottir")

    def test_context_reuses_one_surname_convention_for_partnership(self) -> None:
        ctx = SimulationContext(
            db_path=self.db_path,
            save_db_path=Path(self._tmpdir.name) / "save.sqlite",
            world="test",
            simulation_start_year=1000,
            current_year=1000,
        )
        father = ctx.add_person(
            person=Person(
                first_name="Robert",
                last_name="Sayers",
                gender="Male",
                ethnic="FatherEthnic",
                species="Human",
                birthplace="Stonebridge",
                birthyear=980,
            ),
            is_founder=True,
        )
        mother = ctx.add_person(
            person=Person(
                first_name="Ada",
                last_name="LookupLast",
                gender="Female",
                ethnic="TestEthnic",
                species="Human",
                birthplace="Stonebridge",
                birthyear=982,
            ),
            is_founder=True,
        )

        with patch("library.random_names.choose_birth_surname_convention") as chooser:
            chooser.return_value = "kin"
            ctx.add_couple(father.person_id, mother.person_id)
            first = ctx.surname_convention_for_parents(father.person_id, mother.person_id)
            second = ctx.surname_convention_for_parents(mother.person_id, father.person_id)

        self.assertEqual(first, "kin")
        self.assertEqual(second, "kin")
        chooser.assert_called_once()


if __name__ == "__main__":
    unittest.main()
