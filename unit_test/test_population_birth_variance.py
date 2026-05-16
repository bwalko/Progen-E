"""Conception probability and annual birth eligibility (no biennial parity)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.generator import generate_person_random
from library.population_growth_runner import _eligible_for_birth
from library.reproduction import annual_conception_probability
from library.simulation_context import SimulationContext


class TestPopulationBirthVariance(unittest.TestCase):
    def test_annual_conception_higher_when_less_prosperous(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            m = replace(
                generate_person_random(
                    gender="Female",
                    age=25,
                    simulation_year=2000,
                    db_path=cfg,
                ),
                genome={"mating drive": 0.0},
                job=None,
            )
            f = replace(
                generate_person_random(
                    gender="Male",
                    age=25,
                    simulation_year=2000,
                    db_path=cfg,
                ),
                genome={"mating drive": 0.0},
                job=None,
            )
            p_poor = annual_conception_probability(m, f, pressure=2.0)
            m_rich = replace(m, job="engineer")
            f_rich = replace(f, job="clerk")
            p_rich = annual_conception_probability(m_rich, f_rich, pressure=0.0)
            self.assertGreater(p_poor, p_rich)

    def test_annual_conception_higher_mating_drive(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            m_lo = replace(
                generate_person_random(
                    gender="Female",
                    age=25,
                    simulation_year=2000,
                    db_path=cfg,
                ),
                genome={"mating drive": -40.0},
            )
            f_lo = replace(
                generate_person_random(
                    gender="Male",
                    age=25,
                    simulation_year=2000,
                    db_path=cfg,
                ),
                genome={"mating drive": -40.0},
            )
            m_hi = replace(m_lo, genome={"mating drive": 55.0})
            f_hi = replace(f_lo, genome={"mating drive": 55.0})
            p_lo = annual_conception_probability(m_lo, f_lo, pressure=0.5)
            p_hi = annual_conception_probability(m_hi, f_hi, pressure=0.5)
            self.assertGreater(p_hi, p_lo)

    def test_eligible_for_birth_not_biennial_parity(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            cfg = root / "config.sqlite"
            sav = root / "save.sqlite"
            load_all_csvs_into_sqlite(cfg)
            with SimulationContext.create(
                db_path=cfg,
                save_db_path=sav,
                world_id="default",
                world="default",
                start_year=1000,
                refresh_config=False,
                placename_rng_salt=1,
            ) as ctx:
                p = replace(
                    generate_person_random(
                        gender="Female",
                        age=21,
                        simulation_year=2000,
                        simulation_context=ctx,
                    ),
                    birthyear=1979,
                    min_fertility_age=18,
                    max_fertility_age=50,
                )
                rec = ctx.add_person(person=p, is_founder=False)
                self.assertTrue(_eligible_for_birth(rec, 2000))


if __name__ == "__main__":
    unittest.main()
