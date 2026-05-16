"""Mind/body layer: careers read phenotypes; attractiveness and aging."""

from __future__ import annotations

import unittest

from library.mind_body import (
    attractiveness_01,
    clamp_mind_body_value,
    mind_body_from_genome,
    work_trait_values,
)
from library.person import Person
from library.simulation_careers import career_fitness


class TestMindBody(unittest.TestCase):
    def test_clamp_mind_body_value(self) -> None:
        self.assertEqual(clamp_mind_body_value(200.0), 99.99)
        self.assertEqual(clamp_mind_body_value(-200.0), -99.99)

    def test_career_fitness_prefers_mind_body_over_genome(self) -> None:
        g = {"focus": 0.0, "physical": 0.0, "intellect": 0.0}
        mb = dict(g)
        mb["focus"] = 95.0  # poor work focus in current body only
        p = Person(
            first_name="A",
            last_name="B",
            gender="Male",
            ethnic="Human",
            species="Human",
            birthyear=2000,
            genome=g,
            mind_body=mb,
        )
        fit_with = career_fitness(p).score
        p_genome_only = Person(
            first_name="A",
            last_name="B",
            gender="Male",
            ethnic="Human",
            species="Human",
            birthyear=2000,
            genome=g,
            mind_body=mind_body_from_genome(g),
        )
        fit_without = career_fitness(p_genome_only).score
        self.assertLess(fit_with, fit_without)

    def test_attractiveness_elderly_multiplier_reduces_score(self) -> None:
        traits = {
            "mating drive": 0.0,
            "persuasion": 0.0,
            "symmetry": 0.0,
            "wit": 0.0,
            "neurochemical": 0.0,
        }
        young = Person(
            first_name="Y",
            last_name="Z",
            gender="Female",
            ethnic="Human",
            species="Human",
            birthyear=2000,
            max_fertility_age=45,
            genome=traits,
            mind_body=dict(traits),
        )
        old = Person(
            first_name="Y",
            last_name="Z",
            gender="Female",
            ethnic="Human",
            species="Human",
            birthyear=1880,
            max_fertility_age=45,
            genome=traits,
            mind_body=dict(traits),
        )
        y_score = attractiveness_01(young, 2020)
        o_score = attractiveness_01(old, 2020)
        self.assertGreater(y_score, o_score)

    def test_work_trait_values_backfills_from_genome(self) -> None:
        p = Person(
            first_name="A",
            last_name="B",
            gender="Male",
            ethnic="Human",
            species="Human",
            birthyear=1990,
            genome={"focus": 3.0},
            mind_body={},
        )
        w = work_trait_values(p)
        self.assertEqual(w.get("focus"), 3.0)
