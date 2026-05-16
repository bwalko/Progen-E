"""Tests for per-person fertility limits used during reproduction."""

from __future__ import annotations

import random
import unittest
from dataclasses import replace

from library.generator import generate_person_random
from library.random_traits import choose_species_row
from library.reproduction import having_sex


class TestFertilityRules(unittest.TestCase):
    def test_gender_based_fertility_assignment(self) -> None:
        random.seed(11)
        male = generate_person_random(gender="Male", age=25, simulation_year=1000)
        female = generate_person_random(gender="Female", age=25, simulation_year=1000)

        self.assertIsNotNone(male.min_fertility_age)
        self.assertIsNotNone(female.min_fertility_age)
        self.assertIsNone(male.max_fertility_age)
        self.assertIsNotNone(female.max_fertility_age)

        male_species = choose_species_row(species=male.species, ethnic=male.ethnic)
        male_maturity = int(male_species["maturity"])
        assert male.min_fertility_age is not None
        self.assertGreaterEqual(male.min_fertility_age, male_maturity - 2)
        self.assertLessEqual(male.min_fertility_age, male_maturity + 2)

        female_species = choose_species_row(species=female.species, ethnic=female.ethnic)
        female_maturity = int(female_species["maturity"])
        species_middleaged = int(female_species["middleaged"])
        assert female.min_fertility_age is not None
        self.assertGreaterEqual(female.min_fertility_age, female_maturity - 2)
        self.assertLessEqual(female.min_fertility_age, female_maturity + 2)
        assert female.max_fertility_age is not None
        self.assertGreaterEqual(female.max_fertility_age, species_middleaged - 5)
        self.assertLessEqual(female.max_fertility_age, species_middleaged + 5)

    def test_having_sex_returns_none_below_min_fertility_age(self) -> None:
        random.seed(13)
        father = generate_person_random(gender="Male", age=15, simulation_year=1000)
        mother = generate_person_random(gender="Female", age=20, simulation_year=1000)
        father = replace(father, min_fertility_age=18)

        child = having_sex(
            father,
            mother,
            simulation_year=1000,
            birthyear=1000,
            age=0,
            life_stage="child",
        )
        self.assertIsNone(child)

    def test_having_sex_returns_none_above_max_fertility_age(self) -> None:
        random.seed(17)
        father = generate_person_random(gender="Male", age=35, simulation_year=1000)
        mother = generate_person_random(gender="Female", age=20, simulation_year=1000)
        assert mother.max_fertility_age is not None

        blocked_year = int(mother.birthyear) + int(mother.max_fertility_age) + 1
        child = having_sex(
            father,
            mother,
            simulation_year=blocked_year,
            birthyear=blocked_year,
            age=0,
            life_stage="child",
        )
        self.assertIsNone(child)

    def test_having_sex_returns_child_within_fertility_window(self) -> None:
        random.seed(19)
        father = generate_person_random(gender="Male", age=30, simulation_year=1000)
        mother = generate_person_random(gender="Female", age=24, simulation_year=1000)

        child = having_sex(
            father,
            mother,
            simulation_year=1000,
            birthyear=1000,
            age=0,
            life_stage="child",
        )
        self.assertIsNotNone(child)


if __name__ == "__main__":
    unittest.main()
