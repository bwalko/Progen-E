"""Route augmentation: bidirectional closure and continental land connectivity."""

from __future__ import annotations

import unittest

from library.route_inference import augment_routes_with_inference


class TestRouteInference(unittest.TestCase):
    def test_bidirectional_adds_missing_reverse(self) -> None:
        rows = [
            {
                "from_region_id": "a",
                "to_region_id": "b",
                "route_type": "land",
                "friction": 2,
                "bidirectional": 1,
            }
        ]
        reg = {"a": "C1", "b": "C1"}
        edges = augment_routes_with_inference(rows, region_continent_by_id=reg)
        by_ft = {(e.from_region_id, e.to_region_id, e.route_type): e for e in edges}
        self.assertIn(("a", "b", "land"), by_ft)
        self.assertFalse(by_ft[("a", "b", "land")].inferred)
        self.assertIn(("b", "a", "land"), by_ft)
        self.assertTrue(by_ft[("b", "a", "land")].inferred)
        self.assertEqual(by_ft[("b", "a", "land")].friction, 2.0)

    def test_bridges_isolated_land_components_same_continent(self) -> None:
        rows = [
            {
                "from_region_id": "a",
                "to_region_id": "b",
                "route_type": "land",
                "friction": 2,
                "bidirectional": 0,
            },
            {
                "from_region_id": "c",
                "to_region_id": "d",
                "route_type": "land",
                "friction": 2,
                "bidirectional": 0,
            },
        ]
        reg = {"a": "X", "b": "X", "c": "X", "d": "X"}
        edges = augment_routes_with_inference(
            rows, region_continent_by_id=reg, bridge_land_friction=9.0
        )
        pairs = {
            tuple(sorted((e.from_region_id, e.to_region_id)))
            for e in edges
            if e.route_type == "land"
        }
        self.assertIn(("a", "b"), pairs)
        self.assertIn(("c", "d"), pairs)
        # Bridge links {a,b} to {c,d} (min id a to min id c in this fixture)
        self.assertIn(("a", "c"), pairs)
        self.assertTrue(
            any(
                e.inferred
                and e.route_type == "land"
                and tuple(sorted((e.from_region_id, e.to_region_id))) == ("a", "c")
                and e.friction == 9.0
                for e in edges
            )
        )

    def test_no_land_bridge_across_continents(self) -> None:
        rows: list[dict] = []
        reg = {"left": "Z1", "right": "Z2"}
        edges = augment_routes_with_inference(rows, region_continent_by_id=reg)
        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
