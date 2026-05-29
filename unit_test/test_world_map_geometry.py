"""Generated world map geometry regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from contextlib import closing
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.geography import list_continents, list_regions, list_routes_from
from library.settlement_local_geography import (
    build_local_region_graph,
    make_region_geography_rng,
)
from library.simulation_context import SimulationContext
from library.world_map_geometry import (
    MAP_GEOMETRY_VERSION,
    _continent_hulls,
    _micro_adjacency,
    _micro_boundary_edges,
    _point_in_polygon,
    _point_segment_distance,
    _polygon_bounds,
    build_world_map_geometry,
    project_local_point_to_region_footprint,
    project_world_point_to_region_footprint,
    region_id_for_world_point,
)
from library.world_save import read_world_map_seed, write_world_map_seed
from library.world_map_svg import (
    FeatureMapOverlay,
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
        self.assertTrue(first["micro_cells"])

    def test_world_geometry_varies_by_save_map_seed(self) -> None:
        save_a = Path(self._td.name) / "save-a.sqlite"
        save_b = Path(self._td.name) / "save-b.sqlite"
        write_world_map_seed(save_a, "campaign-a")
        write_world_map_seed(save_b, "campaign-b")

        first = build_world_map_geometry(
            world="default",
            db_path=self.cfg,
            save_db_path=save_a,
        ).to_json_obj()
        second = build_world_map_geometry(
            world="default",
            db_path=self.cfg,
            save_db_path=save_a,
        ).to_json_obj()
        other = build_world_map_geometry(
            world="default",
            db_path=self.cfg,
            save_db_path=save_b,
        ).to_json_obj()

        self.assertEqual(read_world_map_seed(save_a), "campaign-a")
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))
        self.assertNotEqual(
            [c["polygon"] for c in first["cells"]],
            [c["polygon"] for c in other["cells"]],
        )

    def test_new_simulation_context_persists_save_map_seed(self) -> None:
        save_path = Path(self._td.name) / "context-save.sqlite"
        with SimulationContext.create(
            db_path=self.cfg,
            save_db_path=save_path,
            world_id="default",
            world="default",
            start_year=1000,
            refresh_config=False,
            flush_run_store=False,
            placename_rng_salt=123456,
        ) as ctx:
            self.assertEqual(ctx.world_map_seed, "123456")

        self.assertEqual(read_world_map_seed(save_path), "123456")

    def test_continent_footprints_are_not_rectangular_boxes(self) -> None:
        continents = list_continents(world="default", db_path=self.cfg)
        hulls = _continent_hulls(continents)

        self.assertEqual(set(hulls), {c.continent_id for c in continents})
        for hull in hulls.values():
            self.assertGreaterEqual(len(hull), 80)
            x0, y0, x1, y1 = _polygon_bounds(hull)
            bbox_corners = {(x0, y0), (x1, y0), (x1, y1), (x0, y1)}
            hull_points = {(round(x, 6), round(y, 6)) for x, y in hull}
            axis_aligned_edges = sum(
                1
                for a, b in zip(hull, hull[1:] + hull[:1])
                if abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6
            )
            area = abs(
                sum(
                    hull[i][0] * hull[(i + 1) % len(hull)][1]
                    - hull[(i + 1) % len(hull)][0] * hull[i][1]
                    for i in range(len(hull))
                )
                / 2.0
            )
            perimeter = sum(
                ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
                for a, b in zip(hull, hull[1:] + hull[:1])
            )
            compactness = 4.0 * 3.141592653589793 * area / max(1e-9, perimeter * perimeter)

            self.assertNotEqual(hull_points, bbox_corners)
            self.assertGreater(len({round(x, 3) for x, _ in hull}), 4)
            self.assertGreater(len({round(y, 3) for _, y in hull}), 4)
            self.assertLess(axis_aligned_edges, len(hull) * 0.08)
            self.assertGreater(compactness, 0.18)

    def test_continent_footprints_do_not_overlap(self) -> None:
        continents = list_continents(world="default", db_path=self.cfg)
        hulls = _continent_hulls(continents)
        bounds = {cid: _polygon_bounds(hull) for cid, hull in hulls.items()}

        for i, aid in enumerate(sorted(bounds)):
            ax0, ay0, ax1, ay1 = bounds[aid]
            for bid in sorted(bounds)[i + 1:]:
                bx0, by0, bx1, by1 = bounds[bid]
                self.assertTrue(
                    ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0,
                    (aid, bounds[aid], bid, bounds[bid]),
                )

    def test_point_in_polygon_preserves_edge_direction(self) -> None:
        poly = [(0.68, 0.36), (0.70, 0.36), (0.69, 0.35)]

        self.assertFalse(_point_in_polygon((0.64, 0.359), poly))
        self.assertTrue(_point_in_polygon((0.69, 0.357), poly))

    def test_every_region_has_valid_cell_and_features(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        regions = list_regions(world="default", db_path=self.cfg)
        cells = geometry.cell_by_region_id()
        features = geometry.features_by_region_id()
        micro_region_ids = {c.region_id for c in geometry.micro_cells}
        micro_counts = Counter(c.region_id for c in geometry.micro_cells)

        self.assertEqual(set(cells), {r.region_id for r in regions})
        self.assertEqual(micro_region_ids, {r.region_id for r in regions})
        self.assertGreaterEqual(min(micro_counts.values()), 12)
        self.assertGreaterEqual(len(geometry.micro_cells), len(regions) * 20)
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
        adjacency, _ = _micro_adjacency(geometry.micro_cells)
        for region in regions:
            owned = {c.micro_id for c in geometry.micro_cells if c.region_id == region.region_id}
            self.assertTrue(owned, region.region_id)
            remaining = set(owned)
            start = next(iter(remaining))
            stack = [start]
            remaining.remove(start)
            while stack:
                mid = stack.pop()
                for nid in adjacency.get(mid, set()):
                    if nid in remaining:
                        remaining.remove(nid)
                        stack.append(nid)
            self.assertFalse(remaining, region.region_id)
        for cell in geometry.micro_cells:
            self.assertGreaterEqual(len(cell.polygon), 3)
            self.assertTrue(0.0 <= cell.center_x <= 1.0)
            self.assertTrue(0.0 <= cell.center_y <= 1.0)
            self.assertTrue(0.0 <= cell.elevation <= 1.0)
            self.assertTrue(0.0 <= cell.moisture <= 1.0)
        for region in regions:
            text = f"{region.region_id} {region.region_name} {region.terrain} {region.keywords}".lower()
            region_micro = [c for c in geometry.micro_cells if c.region_id == region.region_id]
            if any(t in text for t in ("port", "coast", "shore", "littoral", "delta")):
                self.assertTrue(any(c.is_coastal for c in region_micro), region.region_id)
            if "river" in text or "channel" in text or "fork" in text:
                self.assertTrue(any(c.moisture >= 0.78 for c in region_micro), region.region_id)

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
            self.assertGreaterEqual(len(edge.points), 2)
            self.assertIn(edge.edge_class, {"land_route", "sea_route"})
            for x, y in edge.points:
                self.assertTrue(0.0 <= x <= 1.0)
                self.assertTrue(0.0 <= y <= 1.0)
        self.assertTrue(any(edge.edge_class == "land_route" and len(edge.points) > 2 for edge in geometry.edges))
        self.assertTrue(any(edge.edge_class == "sea_route" and len(edge.points) > 2 for edge in geometry.edges))

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
        self.assertEqual(graph.edges, [])

        feature_by_id = {f.feature_id: f for f in graph.features}
        anchored = [
            feature_by_id.get(pin.anchor_feature_id or "")
            for pin in graph.settlements
        ]
        self.assertTrue(any(f is not None and f.source_region_feature_id for f in anchored))
        self.assertTrue(
            any(
                f is not None and f.kind in {"harbor", "bay", "coast", "river", "ford", "stream"}
                for f in anchored
            )
        )

    def test_many_local_settlements_keep_distinct_world_sites(self) -> None:
        region = next(
            r for r in list_regions(world="default", db_path=self.cfg)
            if r.region_id == "aeria_eastwater_river"
        )
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        rng = make_region_geography_rng("default", region.region_id, slot=0)

        graph = build_local_region_graph(
            world="default",
            region=region,
            rng=rng,
            settlement_slots=17,
            primary_meaning="ford",
            primary_category="Topography",
            db_path=self.cfg,
            world_geometry=geometry,
        )

        world_sites = [
            (pin.world_x, pin.world_y)
            for pin in graph.settlements
            if pin.world_x is not None and pin.world_y is not None
        ]
        self.assertGreaterEqual(len(world_sites), 12)
        for site in world_sites:
            self.assertEqual(region_id_for_world_point(geometry, site), region.region_id)

        close_pairs = 0
        for i, first in enumerate(world_sites):
            for second in world_sites[i + 1:]:
                if ((first[0] - second[0]) ** 2 + (first[1] - second[1]) ** 2) ** 0.5 < 0.004:
                    close_pairs += 1
        self.assertLess(close_pairs, 8)

    def test_features_have_svg_style_semantics(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)

        self.assertTrue(
            any(f.feature_class in {"coast", "water", "relief", "vegetation"} for f in geometry.features)
        )
        self.assertTrue(all(f.label for f in geometry.features))
        self.assertTrue(all(r.river_class in {"major_river", "minor_river"} for r in geometry.rivers))
        self.assertTrue(any(r.points for r in geometry.rivers))
        self.assertTrue(any(c.elevation >= 0.6 for c in geometry.micro_cells))
        self.assertTrue(any(c.moisture >= 0.8 for c in geometry.micro_cells))

    def test_river_segments_keep_local_region_ownership(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        micro_by_id = {c.micro_id: c for c in geometry.micro_cells}

        self.assertTrue(any(len({rid for s in r.segments for rid in s.region_ids}) > 1 for r in geometry.rivers))
        for river in geometry.rivers:
            self.assertEqual(len(river.segments), len(river.points) - 1)
            self.assertTrue(river.segments)
            for segment in river.segments:
                self.assertEqual(len(segment.points), 2)
                expected_region_ids = sorted(
                    {
                        micro_by_id[micro_id].region_id
                        for micro_id in segment.micro_ids
                        if micro_id in micro_by_id
                    }
                )
                if expected_region_ids:
                    self.assertEqual(segment.region_ids, expected_region_ids)

    def test_coastal_rivers_reach_coastline_boundary(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        micro_by_id = {c.micro_id: c for c in geometry.micro_cells}
        boundary_edges = _micro_boundary_edges(geometry.micro_cells)
        coastal_rivers = 0

        for river in geometry.rivers:
            if not river.segments or not river.segments[-1].micro_ids:
                continue
            sink = micro_by_id[river.segments[-1].micro_ids[0]]
            if not sink.is_coastal:
                continue
            coastal_rivers += 1
            mouth = river.points[-1]
            distance_to_coast = min(
                _point_segment_distance(mouth, a, b)
                for a, b in boundary_edges.get(sink.micro_id, [])
            )
            self.assertLessEqual(distance_to_coast, 1e-5, river.river_id)

        self.assertGreater(coastal_rivers, 0)

    def test_rivers_do_not_stack_on_same_channel(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        channel_sets = [
            {mid for segment in river.segments for mid in segment.micro_ids}
            for river in geometry.rivers
        ]

        for i, first in enumerate(channel_sets):
            for second in channel_sets[i + 1:]:
                shared = len(first & second)
                smaller = max(1, min(len(first), len(second)))
                self.assertLess(shared / smaller, 0.45)

    def test_local_settlement_projection_stays_inside_region_footprint(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)

        for cell in geometry.cells:
            owned = [c for c in geometry.micro_cells if c.region_id == cell.region_id]
            self.assertTrue(owned, cell.region_id)
            for local in ((0.04, 0.04), (0.5, 0.5), (0.96, 0.96)):
                point = project_local_point_to_region_footprint(geometry, cell.region_id, local)
                containing = [micro for micro in owned if _point_in_polygon(point, micro.polygon)]
                self.assertTrue(containing, (cell.region_id, local, point))

    def test_world_point_projection_stays_inside_declared_region(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        target = next(c for c in geometry.cells if c.region_id == "boreas_boreal_deep")
        outside = next(c for c in geometry.cells if c.region_id != target.region_id)
        point = (outside.center_x, outside.center_y)

        projected = project_world_point_to_region_footprint(
            geometry,
            target.region_id,
            point,
        )

        self.assertEqual(region_id_for_world_point(geometry, projected), target.region_id)

    def test_debug_svg_renderer_outputs_noisy_map_layers(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)

        svg = render_world_map_svg(geometry, width=640, height=420)

        self.assertIn("<svg", svg)
        self.assertIn('class="micro-cell terrain-', svg)
        self.assertIn(".micro-cell{stroke:none}", svg)
        self.assertIn('id="terrain-warp"', svg)
        self.assertIn('id="ocean-gradient"', svg)
        self.assertIn('class="terrain-blend"', svg)
        self.assertIn('class="terrain-mottle"', svg)
        self.assertIn('class="terrain-texture"', svg)
        self.assertIn('class="terrain-shade', svg)
        self.assertIn('class="region-boundary"', svg)
        self.assertIn(".region-boundary{stroke:#151b2d", svg)
        self.assertIn(".coast-line{stroke:#1d2938", svg)
        self.assertIn('class="coast-shelf"', svg)
        self.assertIn('class="coast-beach"', svg)
        self.assertIn('class="coast-shadow"', svg)
        self.assertLess(svg.count('class="coast-beach"'), 50)
        self.assertIn(".river{stroke:#2f93c4", svg)
        self.assertNotIn('class="route ', svg)
        self.assertIn('class="feature ', svg)
        first_feature = geometry.features[0]
        self.assertIn(f'data-feature-id="{first_feature.feature_id}"', svg)
        self.assertIn(f'data-feature-name="{first_feature.label}"', svg)
        self.assertIn(f'data-feature-kind="{first_feature.kind}"', svg)
        self.assertIn("data-region-label=", svg)
        first_cell = geometry.micro_cells[0]
        first_path = next(
            line for line in svg.splitlines()
            if f'data-micro-id="{first_cell.micro_id}"' in line
        )
        self.assertGreaterEqual(first_path.count(" L "), len(first_cell.polygon) - 1)

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
            features=[
                FeatureMapOverlay(
                    feature_id=f"{cell.region_id}:f1",
                    region_id=cell.region_id,
                    kind="river",
                    display_name="Bluewater",
                    x=cell.center_x + 0.01,
                    y=cell.center_y,
                    etymology="blue · river",
                )
            ],
        )

        svg = render_world_map_svg(geometry, overlays=overlays)

        self.assertIn('class="settlement active"', svg)
        self.assertIn(f'data-settlement-id="{cell.region_id}:s1"', svg)
        self.assertIn(f'data-feature-id="{cell.region_id}:f1"', svg)
        self.assertIn('data-feature-name="Bluewater"', svg)
        self.assertIn(">Test Town</text>", svg)
        self.assertIn(">Bluewater</text>", svg)

    def test_world_map_features_filter_to_nearby_settlements_when_overlaid(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        overlays = WorldMapOverlays(
            settlements=[
                SettlementMapOverlay(
                    settlement_id=f"{cell.region_id}:s1",
                    region_id="not_a_real_region",
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
        self.assertNotIn('class="feature ', svg)

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
                    json.dumps(
                        {
                            "features": [
                                {
                                    "feature_id": f"{cell.region_id}:f1",
                                    "kind": "river",
                                    "x": 0.45,
                                    "y": 0.5,
                                    "display_name": "Bluewater",
                                    "etymology": "blue · river",
                                }
                            ],
                            "settlements": [{"settlement_slot": 0, "x": 0.5, "y": 0.5}],
                        }
                    ),
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
        self.assertEqual(len(overlays.features), 1)
        self.assertEqual(overlays.features[0].display_name, "Bluewater")
        self.assertEqual(overlays.features[0].kind, "river")
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

    def test_load_world_map_overlays_excludes_inactive_settlements_by_default(self) -> None:
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
            for suffix, status in (("active", "active"), ("old", "abandoned")):
                con.execute(
                    "insert into simulation_settlements values (?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"{cell.region_id}:{suffix}",
                        cell.region_id,
                        f"{suffix.title()} Town",
                        42,
                        status,
                        1,
                        json.dumps({"settlements": [{"settlement_slot": 0, "x": 0.5, "y": 0.5}]}),
                    ),
                )
            con.commit()

        overlays = load_world_map_overlays(geometry=geometry, save_db_path=save_path)
        overlays_with_inactive = load_world_map_overlays(
            geometry=geometry,
            save_db_path=save_path,
            include_inactive_settlements=True,
        )

        self.assertEqual([s.display_name for s in overlays.settlements], ["Active Town"])
        self.assertEqual(
            [s.display_name for s in overlays_with_inactive.settlements],
            ["Active Town", "Old Town"],
        )


if __name__ == "__main__":
    unittest.main()

