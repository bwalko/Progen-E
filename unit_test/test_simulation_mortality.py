import unittest

import numpy as np

from library.simulation_mortality import (
    _age_adjusted_annual_mortality,
    _age_adjusted_annual_mortality_array,
)


class TestSimulationMortality(unittest.TestCase):
    def test_lifespan_pressure_ramps_near_species_lifespan(self):
        base_kwargs = {
            "infant_annual": 0.08,
            "child_annual": 0.02,
            "adult_annual": 0.006,
            "birth_litter_size": 1,
            "lifespan": 100,
        }

        age_80 = _age_adjusted_annual_mortality(age=80, **base_kwargs)
        age_100 = _age_adjusted_annual_mortality(age=100, **base_kwargs)
        age_112 = _age_adjusted_annual_mortality(age=112, **base_kwargs)
        age_116 = _age_adjusted_annual_mortality(age=116, **base_kwargs)
        age_120 = _age_adjusted_annual_mortality(age=120, **base_kwargs)

        self.assertLess(age_80, 0.05)
        self.assertGreater(age_100, age_80)
        self.assertGreater(age_112, age_100)
        self.assertGreater(age_116, age_112)
        self.assertGreater(age_116, 0.9)
        self.assertLess(age_116, 0.95)
        self.assertGreater(age_120, 0.9)
        self.assertLess(age_120, 0.95)

    def test_vectorized_lifespan_pressure_matches_scalar_helper(self):
        ages = np.array([70, 90, 100, 112, 116, 120], dtype=np.int64)
        lifespans = np.full_like(ages, 100)
        litter_sizes = np.ones_like(ages)
        kwargs = {
            "infant_annual": 0.08,
            "child_annual": 0.02,
            "adult_annual": 0.006,
        }

        vectorized = _age_adjusted_annual_mortality_array(
            ages=ages,
            birth_litter_sizes=litter_sizes,
            lifespans=lifespans,
            **kwargs,
        )
        scalar = np.array(
            [
                _age_adjusted_annual_mortality(
                    age=int(age),
                    birth_litter_size=1,
                    lifespan=100,
                    **kwargs,
                )
                for age in ages
            ]
        )

        np.testing.assert_allclose(vectorized, scalar)

    def test_long_lived_species_keeps_later_lifespan_window(self):
        base_kwargs = {
            "infant_annual": 0.08,
            "child_annual": 0.02,
            "adult_annual": 0.006,
            "birth_litter_size": 1,
        }

        human_age_120 = _age_adjusted_annual_mortality(
            age=120,
            lifespan=100,
            **base_kwargs,
        )
        long_lived_age_120 = _age_adjusted_annual_mortality(
            age=120,
            lifespan=200,
            **base_kwargs,
        )

        self.assertGreater(human_age_120, 0.9)
        self.assertLess(human_age_120, 0.95)
        self.assertLess(long_lived_age_120, 0.05)


if __name__ == "__main__":
    unittest.main()
