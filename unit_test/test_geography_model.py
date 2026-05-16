"""Geography-first model tests: graph integrity, migration, and settlements."""

from __future__ import annotations

import random
import unittest

from library.config_import import refresh_world_config_from_csv
from library.generator import generate_person_random
from library.geography import (
    choose_migration_destination,
    get_region,
    iter_drainage_downstream,
    list_regions,
    list_routes_from,
    parse_poly_hull_vertices,
    region_connectivity_score,
    region_environment,
    region_surface_elevation_m,
    resolve_travel_era,
    travel_friction,
)
from library.settlements import SettlementState, evolve_settlement
from library.simulation_context import SimulationContext


def setUpModule() -> None:
    refresh_world_config_from_csv("default")


class TestGeographyModel(unittest.TestCase):
    def test_region_graph_integrity(self) -> None:
        regions = list_regions(world="default")
        self.assertGreaterEqual(len(regions), 9)
        ids = {r.region_id for r in regions}
        self.assertEqual(len(ids), len(regions))
        continents = {r.continent_id for r in regions}
        self.assertGreaterEqual(len(continents), 3)

        route_targets = set()
        for region in regions:
            for route in list_routes_from(region.region_id, world="default"):
                route_targets.add(route.to_region_id)
                self.assertIn(route.to_region_id, ids)
        self.assertTrue(route_targets.issubset(ids))

        by_id = {r.region_id: r for r in regions}
        for r in regions:
            env = region_environment(r.region_id, world="default")
            self.assertTrue(0.0 <= env.hydro_idx <= 1.0)
            self.assertTrue(0.0 <= env.fertility <= 1.0)
            self.assertTrue(0.0 <= env.ruggedness <= 1.0)
            self.assertGreaterEqual(len(parse_poly_hull_vertices(env.poly_hull)), 3)
            if env.drainage_to:
                self.assertIn(env.drainage_to, ids)
                self.assertEqual(by_id[env.drainage_to].continent_id, r.continent_id)
            chain = iter_drainage_downstream(r.region_id, world="default")
            self.assertGreaterEqual(len(chain), 1)
            self.assertEqual(chain[0], r.region_id)
            tail = region_environment(chain[-1], world="default")
            self.assertFalse(tail.drainage_to)

    def test_travel_era_deep_prehistory_precludes_sea_and_scales_friction(self) -> None:
        """Era before historical -50000 disables sea; land friction follows era multiplier."""
        origin_id = "aeria_port"
        base = list_routes_from(origin_id, world="default")
        self.assertTrue(any(r.route_type.lower() == "sea" for r in base))
        # default world_start: start_year == history_equivalent_start_year → sim year == historical year
        prehistoric = list_routes_from(
            origin_id, world="default", simulation_year=-60000
        )
        self.assertFalse(any(r.route_type.lower() == "sea" for r in prehistoric))
        era = resolve_travel_era(world="default", simulation_year=-60000)
        self.assertIn("sea", era.disabled_route_types)
        by_to = {r.to_region_id: r.friction for r in base if r.route_type.lower() == "land"}
        by_to_pre = {
            r.to_region_id: r.friction for r in prehistoric if r.route_type.lower() == "land"
        }
        for rid, f0 in by_to.items():
            self.assertAlmostEqual(by_to_pre[rid], f0 * era.land_friction_multiplier, places=5)

    def test_travel_era_sea_land_use_separate_multipliers_from_gauges(self) -> None:
        """Roman-era band (-100 to 599 CE): classical land vs Mediterranean grain-freighter sea."""
        origin_id = "aeria_port"
        base = list_routes_from(origin_id, world="default", simulation_year=150)
        era = resolve_travel_era(world="default", simulation_year=150)
        sea_routes = [r for r in base if r.route_type.lower() == "sea"]
        self.assertGreater(len(sea_routes), 0)
        for r in sea_routes:
            raw = travel_friction("aeria_port", r.to_region_id, simulation_year=None)
            self.assertIsNotNone(raw)
            self.assertAlmostEqual(
                float(r.friction),
                float(raw) * era.sea_friction_multiplier,
                places=5,
            )

    def test_region_surface_elevation_joins_continent_base(self) -> None:
        """Elevation inherits continent base_elev plus regional offset."""
        z_port = region_surface_elevation_m("aeria_port", world="default")
        z_inland = region_surface_elevation_m("cyrene_inland", world="default")
        self.assertLess(z_port, z_inland)

    def test_resolve_travel_era_defaults_without_year(self) -> None:
        e = resolve_travel_era(world="default", simulation_year=None)
        self.assertEqual(e.land_friction_multiplier, 1.0)
        self.assertEqual(e.sea_friction_multiplier, 1.0)
        self.assertIsNone(e.cross_continent_weight_multiplier)

    def test_intercontinental_migration_is_rare(self) -> None:
        random.seed(11)
        origin_id = "aeria_port"
        origin = get_region(origin_id, world="default")
        intercontinental = 0
        draws = 600
        for _ in range(draws):
            dst_id = choose_migration_destination(
                origin_region_id=origin_id,
                world="default",
                cross_continent_weight_fallback=0.08,
            )
            dst = get_region(dst_id, world="default")
            if dst.continent_id != origin.continent_id:
                intercontinental += 1
        ratio = intercontinental / draws
        self.assertLess(ratio, 0.2)

    def test_settlement_evolve_uses_census_and_connectivity(self) -> None:
        census = 5000
        state = SettlementState(
            region_id="boreas_west",
            settlement_id="boreas_west:s1",
            resident_count=census,
            household_cap=1100,
        )
        next_state = evolve_settlement(
            state,
            resident_count=census,
            carrying_capacity=17000,
            connectivity_score=region_connectivity_score("boreas_west", world="default"),
        )
        self.assertEqual(next_state.resident_count, census)
        self.assertLess(next_state.food_pressure, 1.0)
        self.assertIn(next_state.level, {"hamlet", "town", "city"})

    def test_random_person_gets_geography_backed_birthplace(self) -> None:
        random.seed(5)
        with SimulationContext.create(
            world="default", start_year=1000, flush_run_store=False
        ) as ctx:
            person = generate_person_random(simulation_context=ctx, simulation_year=1000)
            self.assertIsNotNone(person.birthplace_region_id)
            self.assertIsNotNone(person.birthplace_settlement_id)
            self.assertNotEqual(person.birthplace, "Placeholder")


if __name__ == "__main__":
    unittest.main()
