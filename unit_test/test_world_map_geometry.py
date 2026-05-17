"""Generated world map geometry regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.geography import list_regions, list_routes_from
from library.settlement_local_geography import (
    build_local_region_graph,
    make_region_geography_rng,
)
from library.world_map_geometry import MAP_GEOMETRY_VERSION, build_world_map_geometry
from library.world_map_svg import (
    SettlementMapOverlay,
    WorldMapOverlays,
    load_world_map_overlays,
    render_world_map_svg,
)


class TestWorldMapGeometry(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name)
        self.cfg = root / "config.sqlite"
        load_all_csvs_into_sqlite(self.cfg)

    def test_world_geometry_is_deterministic(self) -> None:
        first = build_world_map_geometry(world="default", db_path=self.cfg).to_json_obj()
        second = build_world_map_geometry(world="default", db_path=self.cfg).to_json_obj()

        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertEqual(first["version"], MAP_GEOMETRY_VERSION)

    def test_every_region_has_valid_cell_and_features(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        regions = list_regions(world="default", db_path=self.cfg)
        cells = geometry.cell_by_region_id()
        features = geometry.features_by_region_id()

        self.assertEqual(set(cells), {r.region_id for r in regions})
        for region in regions:
            cell = cells[region.region_id]
            self.assertGreaterEqual(len(cell.polygon), 3)
            self.assertTrue(0.0 <= cell.center_x <= 1.0)
            self.assertTrue(0.0 <= cell.center_y <= 1.0)
            self.assertTrue(features.get(region.region_id))
            for x, y in cell.polygon:
                self.assertTrue(0.0 <= x <= 1.0)
                self.assertTrue(0.0 <= y <= 1.0)
            self.assertIn(
                cell.terrain_family,
                {"coast", "riverland", "highlands", "forest", "drylands", "plains"},
            )

    def test_routes_have_renderable_edge_points(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        region_ids = {r.region_id for r in list_regions(world="default", db_path=self.cfg)}
        expected_pairs = set()
        for rid in region_ids:
            for route in list_routes_from(rid, world="default", db_path=self.cfg):
                if route.to_region_id in region_ids:
                    expected_pairs.add(tuple(sorted((rid, route.to_region_id))))
        actual_pairs = {
            tuple(sorted((edge.from_region_id, edge.to_region_id)))
            for edge in geometry.edges
        }

        self.assertTrue(expected_pairs)
        self.assertTrue(expected_pairs.issubset(actual_pairs))
        for edge in geometry.edges:
            self.assertEqual(len(edge.points), 2)
            self.assertIn(edge.edge_class, {"land_route", "sea_route"})

    def test_local_geography_consumes_world_region_features(self) -> None:
        region = next(
            r for r in list_regions(world="default", db_path=self.cfg)
            if r.region_id == "boreas_peat_river"
        )
        rng = make_region_geography_rng("default", region.region_id, slot=0)

        graph = build_local_region_graph(
            world="default",
            region=region,
            rng=rng,
            settlement_slots=3,
            primary_meaning="ford",
            primary_category="Topography",
            db_path=self.cfg,
        )
        data = graph.to_json_obj()

        self.assertEqual(data["source_geometry_version"], MAP_GEOMETRY_VERSION)
        self.assertTrue(data["region_cell_polygon"])
        self.assertTrue(any(f.source_region_feature_id for f in graph.features))
        self.assertEqual(len(graph.settlements), 3)

    def test_features_have_svg_style_semantics(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)

        self.assertTrue(
            any(f.feature_class in {"coast", "water", "relief", "vegetation"} for f in geometry.features)
        )
        self.assertTrue(all(f.label for f in geometry.features))
        self.assertTrue(all(r.river_class in {"major_river", "minor_river"} for r in geometry.rivers))

    def test_debug_svg_renderer_outputs_noisy_map_layers(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)

        svg = render_world_map_svg(geometry, width=640, height=420)

        self.assertIn("<svg", svg)
        self.assertIn('class="cell terrain-', svg)
        self.assertIn('class="route ', svg)
        self.assertIn('class="feature ', svg)
        self.assertIn("data-region-label=", svg)
        first_cell = geometry.cells[0]
        first_path = next(
            line for line in svg.splitlines()
            if f'data-region-id="{first_cell.region_id}"' in line
        )
        self.assertGreater(first_path.count(" L "), len(first_cell.polygon))

    def test_debug_svg_renderer_outputs_settlement_and_polity_overlays(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        overlays = WorldMapOverlays(
            settlements=[
                SettlementMapOverlay(
                    settlement_id=f"{cell.region_id}:s1",
                    region_id=cell.region_id,
                    display_name="Test Town",
                    x=cell.center_x,
                    y=cell.center_y,
                    population=42,
                    status="active",
                )
            ],
            polities_by_region_id={},
        )

        svg = render_world_map_svg(geometry, overlays=overlays)

        self.assertIn('class="settlement active"', svg)
        self.assertIn(f'data-settlement-id="{cell.region_id}:s1"', svg)

    def test_load_world_map_overlays_reads_save_tables(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        save_path = Path(self._td.name) / "save.sqlite"
        import sqlite3

        with closing(sqlite3.connect(save_path)) as con:
            con.execute(
                """
                create table simulation_settlements (
                    settlement_id text, region_id text, display_name text,
                    population_cap integer, status text, site_slot integer,
                    local_geography_json text
                )
                """
            )
            con.execute(
                """
                insert into simulation_settlements values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{cell.region_id}:s1",
                    cell.region_id,
                    "Test Town",
                    42,
                    "active",
                    1,
                    json.dumps({"settlements": [{"settlement_slot": 0, "x": 0.5, "y": 0.5}]}),
                ),
            )
            con.execute(
                "create table simulation_polities (polity_id text, name text, polity_type_id text)"
            )
            con.execute(
                """
                create table simulation_polity_territory (
                    polity_id text, target_kind text, target_id text, until_sim_year integer
                )
                """
            )
            con.execute("insert into simulation_polities values ('p1', 'Test Realm', 'realm')")
            con.execute(
                "insert into simulation_polity_territory values ('p1', 'region', ?, null)",
                (cell.region_id,),
            )
            con.commit()

        overlays = load_world_map_overlays(geometry=geometry, save_db_path=save_path)

        self.assertEqual(len(overlays.settlements), 1)
        self.assertEqual(overlays.settlements[0].display_name, "Test Town")
        self.assertEqual(overlays.polities_by_region_id[cell.region_id].polity_name, "Test Realm")

    def test_load_world_map_overlays_limits_settlements(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        save_path = Path(self._td.name) / "save.sqlite"
        import sqlite3

        with closing(sqlite3.connect(save_path)) as con:
            con.execute(
                """
                create table simulation_settlements (
                    settlement_id text, region_id text, display_name text,
                    population_cap integer, status text, site_slot integer,
                    local_geography_json text
                )
                """
            )
            for i in range(8):
                con.execute(
                    "insert into simulation_settlements values (?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"{cell.region_id}:s{i}",
                        cell.region_id,
                        f"Town {i}",
                        i,
                        "active",
                        1,
                        json.dumps({"settlements": [{"settlement_slot": 0, "x": 0.5, "y": 0.5}]}),
                    ),
                )
            con.commit()

        overlays = load_world_map_overlays(
            geometry=geometry,
            save_db_path=save_path,
            max_settlements=3,
        )

        self.assertEqual([s.display_name for s in overlays.settlements], ["Town 0", "Town 1", "Town 2"])


if __name__ == "__main__":
    unittest.main()

