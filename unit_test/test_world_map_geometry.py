"""Generated world map geometry regressions."""

from __future__ import annotations

import json
import math
import re
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
    build_world_map_debug_data,
    build_world_map_geometry,
    micro_cell_id_for_world_point,
    project_feature_point_to_region_footprint,
    project_local_point_to_region_footprint,
    project_world_point_to_region_footprint,
    region_id_for_world_point,
)
from library.world_save import read_world_map_seed, write_world_map_seed
from library.world_map_svg import (
    FeatureMapOverlay,
    PolityMapOverlay,
    SettlementMapOverlay,
    WorldMapOverlays,
    _coastline_marker_screen_point,
    _feature_marker_allowed_polygon,
    _place_coastline_marker,
    _place_marker,
    _point_in_polygon as _svg_point_in_polygon,
    _thing_polygon_id,
    build_world_map_overlay_debug_data,
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
            self.assertTrue(0.0 <= cell.river_distance <= 1.0)
            self.assertGreaterEqual(cell.river_flow, 0.0)
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
        self.assertTrue(geometry.river_channels)
        self.assertTrue(all(c.water_polygon for c in geometry.river_channels))
        self.assertTrue(all(c.bank_polygon for c in geometry.river_channels))
        self.assertTrue(all(c.corridor_polygon for c in geometry.river_channels))
        self.assertTrue(all(len(c.mouth_water_polygon) >= 8 for c in geometry.river_channels))
        self.assertTrue(all(len(c.mouth_bank_polygon) >= 8 for c in geometry.river_channels))
        self.assertTrue(geometry.water_cells)
        self.assertTrue(any(w.kind == "ocean" for w in geometry.water_cells))
        self.assertTrue(any(w.kind == "lake" for w in geometry.water_cells))
        self.assertTrue(any(c.elevation >= 0.6 for c in geometry.micro_cells))
        self.assertTrue(any(c.moisture >= 0.8 for c in geometry.micro_cells))
        self.assertGreaterEqual(len(geometry.rivers), 4)
        self.assertTrue(any(c.is_channel for c in geometry.micro_cells))
        self.assertTrue(any(c.is_floodplain and c.river_flow > 0.0 for c in geometry.micro_cells))
        carved = [c for c in geometry.micro_cells if c.land_polygons]
        self.assertTrue(carved)
        self.assertTrue(all(all(len(poly) >= 3 for poly in c.land_polygons) for c in carved))
        coastline_edges = _micro_boundary_edges(geometry.micro_cells)
        micro_by_region = {}
        for cell in geometry.micro_cells:
            micro_by_region.setdefault(cell.region_id, []).append(cell)
        for feature in geometry.features:
            if feature.kind not in {"harbor", "bay", "coast"}:
                continue
            region_coast_edges = [
                edge
                for cell in micro_by_region.get(feature.region_id, [])
                for edge in coastline_edges.get(cell.micro_id, [])
                if cell.is_coastal
            ]
            self.assertTrue(region_coast_edges, feature.feature_id)
            distance_to_coast = min(
                _point_segment_distance((feature.x, feature.y), a, b)
                for a, b in region_coast_edges
            )
            self.assertLessEqual(distance_to_coast, 0.00001, feature.feature_id)
        micro_by_id = {cell.micro_id: cell for cell in geometry.micro_cells}
        for feature in geometry.features:
            if feature.kind in {"harbor", "bay", "coast"}:
                continue
            region_micro = [c for c in geometry.micro_cells if c.region_id == feature.region_id]
            if not any(not c.is_coastal for c in region_micro):
                continue
            micro_id = micro_cell_id_for_world_point(
                geometry,
                (feature.x, feature.y),
                region_id=feature.region_id,
            )
            self.assertIsNotNone(micro_id, feature.feature_id)
            self.assertFalse(micro_by_id[micro_id].is_coastal, feature.feature_id)

    def test_feature_projection_respects_coastline_and_interior_rules(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        boundary_edges = _micro_boundary_edges(geometry.micro_cells)
        coastal_region_id = next(
            cell.region_id
            for cell in geometry.micro_cells
            if cell.is_coastal
            and any(
                other.region_id == cell.region_id and not other.is_coastal
                for other in geometry.micro_cells
            )
        )
        coastal_cell = next(c for c in geometry.micro_cells if c.region_id == coastal_region_id and c.is_coastal)
        inland_by_id = {c.micro_id: c for c in geometry.micro_cells}

        harbor = project_feature_point_to_region_footprint(
            geometry,
            coastal_region_id,
            (coastal_cell.center_x, coastal_cell.center_y),
            kind="harbor",
        )
        coast_edges = [
            edge
            for cell in geometry.micro_cells
            if cell.region_id == coastal_region_id and cell.is_coastal
            for edge in boundary_edges.get(cell.micro_id, [])
        ]
        self.assertLessEqual(
            min(_point_segment_distance(harbor, a, b) for a, b in coast_edges),
            0.00001,
        )

        meadow = project_feature_point_to_region_footprint(
            geometry,
            coastal_region_id,
            harbor,
            kind="meadow",
        )
        meadow_micro_id = micro_cell_id_for_world_point(
            geometry,
            meadow,
            region_id=coastal_region_id,
        )
        self.assertIsNotNone(meadow_micro_id)
        self.assertFalse(inland_by_id[meadow_micro_id].is_coastal)

    def test_map_seed_debug_fixtures_capture_stable_comparison_metrics(self) -> None:
        campaign_a = build_world_map_geometry(world="default", db_path=self.cfg, map_seed="campaign-a")
        campaign_b = build_world_map_geometry(world="default", db_path=self.cfg, map_seed="campaign-b")
        terrain_river_c = build_world_map_geometry(
            world="default",
            db_path=self.cfg,
            map_seed="terrain-river-c",
        )
        debug_a = build_world_map_debug_data(campaign_a)
        debug_b = build_world_map_debug_data(campaign_b)
        debug_c = build_world_map_debug_data(terrain_river_c)

        self.assertEqual(debug_a["graph_backend"]["decision"], "keep_lightweight_micro_cell_graph")
        self.assertEqual(debug_a["counts"]["regions"], debug_b["counts"]["regions"])
        self.assertGreaterEqual(debug_a["counts"]["micro_cells"], debug_a["counts"]["regions"] * 20)
        self.assertGreaterEqual(debug_a["counts"]["water_cells"], 30)
        self.assertGreaterEqual(debug_a["counts"]["rivers"], 4)
        self.assertLessEqual(debug_a["counts"]["rivers"], 20)
        self.assertNotEqual(debug_a["counts"]["rivers"], debug_b["counts"]["rivers"])
        self.assertIn("lake", debug_a["water_counts"])
        self.assertIn("ocean", debug_a["water_counts"])
        self.assertNotEqual(debug_a["terrain_counts"], debug_b["terrain_counts"])
        self.assertNotEqual(debug_a["river_lengths"], debug_b["river_lengths"])
        for debug in (debug_a, debug_b, debug_c):
            self.assertGreaterEqual(debug["moisture"]["avg"], 0.68)
            self.assertLessEqual(debug["moisture"]["avg"], 0.82)
            self.assertIn("drylands", debug["terrain_counts"])
            self.assertGreaterEqual(debug["terrain_counts"].get("drylands", 0), 40)
            self.assertIn("plains", debug["terrain_counts"])
            self.assertIn("forest", debug["terrain_counts"])
            self.assertGreater(debug["counts"]["channel_cells"], 0)
            self.assertGreaterEqual(
                debug["counts"]["floodplain_cells"],
                debug["counts"]["channel_cells"],
            )
            self.assertLess(
                debug["counts"]["floodplain_cells"],
                debug["counts"]["micro_cells"] * 0.45,
            )
            self.assertEqual(
                debug["river_mouth_shapes"]["mouths"],
                debug["counts"]["river_channels"],
            )
            self.assertGreaterEqual(debug["river_mouth_shapes"]["min_water_points"], 8)
            self.assertGreaterEqual(debug["river_mouth_shapes"]["min_bank_points"], 8)
            self.assertGreater(debug["river_mouth_shapes"]["avg_water_area"], 0.0)
            self.assertGreater(debug["elevation"]["bands"]["gte_0_64"], 0)
            self.assertGreater(debug["elevation"]["bands"]["gte_0_78"], 0)
            self.assertGreaterEqual(
                debug["elevation"]["bands"]["gte_0_58"],
                debug["elevation"]["bands"]["gte_0_64"],
            )
            self.assertEqual(debug["qa"]["missing_coastal_feature_edges"], 0)
            self.assertLessEqual(debug["qa"]["max_coastal_feature_distance"], 0.010)
            self.assertEqual(debug["qa"]["missing_river_mouth_edges"], 0)
            self.assertLessEqual(debug["qa"]["max_river_mouth_distance"], 0.00001)
            self.assertTrue(debug["coastal_feature_distances"])
            self.assertTrue(debug["river_mouth_distances"])

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
        self.assertIn('id="river-corridor-soften"', svg)
        self.assertIn('id="river-cut-mask"', svg)
        self.assertIn('class="river-cut-mask-shape"', svg)
        self.assertIn('class="terrain-blend"', svg)
        self.assertIn('class="terrain-mottle"', svg)
        self.assertIn('data-texture-kind="canopy"', svg)
        self.assertIn('data-texture-kind="ridge"', svg)
        self.assertIn('data-texture-kind="scrub"', svg)
        self.assertIn('data-texture-kind="alluvial"', svg)
        self.assertIn('data-texture-kind="meadow"', svg)
        self.assertIn("data-terrain-family=", svg)
        self.assertGreater(svg.count('class="terrain-mottle"'), 300)
        self.assertLess(svg.count('class="terrain-mottle"'), len(geometry.micro_cells) * 0.55)
        self.assertIn('class="terrain-texture"', svg)
        self.assertIn('class="terrain-shade', svg)
        self.assertIn('class="terrain-contour ', svg)
        self.assertIn('class="terrain-contour-layer"', svg)
        self.assertIn("data-contour-level=", svg)
        self.assertLess(svg.count('class="terrain-contour '), len(geometry.micro_cells) * 0.5)
        self.assertIn('class="region-boundary"', svg)
        self.assertIn('class="region-boundary-layer dissolved-region-boundaries"', svg)
        self.assertIn('data-boundary-source="dissolved-region-cell"', svg)
        self.assertIn(".region-boundary{stroke:#151b2d", svg)
        boundary_paths = re.findall(
            r'<path class="region-boundary" data-region-id="([^"]+)"',
            svg,
        )
        self.assertEqual(
            sorted(boundary_paths),
            sorted(cell.region_id for cell in geometry.cells),
        )
        self.assertEqual(svg.count('class="region-boundary"'), len(geometry.cells))
        self.assertNotIn('<path class="region-boundary" d="', svg)
        self.assertLess(
            svg.index('class="region-boundary-layer dissolved-region-boundaries"'),
            svg.index('<path class="coast-line"'),
        )
        self.assertIn(".coast-line{stroke:#1d2938", svg)
        self.assertIn('class="coast-shelf"', svg)
        self.assertIn('class="coast-beach"', svg)
        self.assertIn('class="coast-shadow"', svg)
        self.assertLess(svg.count('class="coast-beach"'), 50)
        self.assertIn(".river-corridor,.river-bank,.river-water", svg)
        self.assertIn('class="river-bank ', svg)
        self.assertIn('class="river-water ', svg)
        self.assertIn('class="river-mouth-bank ', svg)
        self.assertIn('class="river-mouth ', svg)
        self.assertIn('data-map-layer="river"', svg)
        self.assertIn(".river-water,.river-highlight,.river-mouth{pointer-events:visiblePainted;cursor:help}", svg)
        self.assertIn("<title>River</title>", svg)
        self.assertIn('class="water-cell ocean"', svg)
        self.assertIn('class="water-cell lake"', svg)
        self.assertIn("data-water-id=", svg)
        self.assertNotIn('<ellipse class="river-mouth ', svg)
        self.assertNotIn('class="route ', svg)
        self.assertIn('class="feature ', svg)
        self.assertIn('class="feature-fa-shape"', svg)
        self.assertIn("data-icon-name=", svg)
        self.assertIn("const maxIcon=0.02*Math.max(rect.width,rect.height)/pxu;", svg)
        self.assertIn("data-base-size=", svg)
        self.assertIn("data-icon-w=", svg)
        self.assertIn('data-feature-named="0"', svg)
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
        region_micro = [m for m in geometry.micro_cells if m.region_id == cell.region_id]
        town_micro = region_micro[0]
        feature_micro = region_micro[-1]
        overlays = WorldMapOverlays(
            settlements=[
                SettlementMapOverlay(
                    settlement_id=f"{cell.region_id}:s1",
                    region_id=cell.region_id,
                    display_name="Test Town",
                    x=town_micro.center_x,
                    y=town_micro.center_y,
                    population=42,
                    status="active",
                )
            ],
            polities_by_region_id={
                cell.region_id: PolityMapOverlay(
                    region_id=cell.region_id,
                    polity_id="p1",
                    polity_name="Test Realm",
                    polity_type_id="realm",
                    color="#755d95",
                )
            },
            features=[
                FeatureMapOverlay(
                    feature_id=f"{cell.region_id}:f1",
                    region_id=cell.region_id,
                    kind="stream",
                    display_name="Bluewater",
                    x=feature_micro.center_x,
                    y=feature_micro.center_y,
                    etymology="blue · stream",
                )
            ],
        )

        svg = render_world_map_svg(geometry, overlays=overlays, max_feature_labels=0)

        self.assertIn('class="settlement active"', svg)
        self.assertIn('class="polity-layer map-overlay-layer" data-map-overlay-layer="polities"', svg)
        self.assertIn('class="polity-territory"', svg)
        self.assertIn('data-polity-id="p1"', svg)
        self.assertIn('data-polity-name="Test Realm"', svg)
        self.assertIn("<title>Test Realm (realm)</title>", svg)
        self.assertIn('class="polity-label"', svg)
        self.assertIn(">Test Realm</text>", svg)
        first_micro_line = next(line for line in svg.splitlines() if 'class="micro-cell ' in line)
        self.assertNotIn("data-polity-id", first_micro_line)
        self.assertIn(f'data-settlement-id="{cell.region_id}:s1"', svg)
        self.assertIn(f'data-feature-id="{cell.region_id}:f1"', svg)
        self.assertIn('data-feature-name="Bluewater"', svg)
        self.assertIn('data-feature-named="1"', svg)
        self.assertIn('data-icon-name="water"', svg)
        self.assertIn('data-base-r="4.0"', svg)
        self.assertIn("data-base-size=", svg)
        self.assertIn(">Test Town</text>", svg)
        self.assertIn(">Bluewater</text>", svg)

    def test_settlement_label_wins_when_anchor_feature_is_named(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        region_micro = [m for m in geometry.micro_cells if m.region_id == cell.region_id]
        town_micro = region_micro[0]
        feature_micro = region_micro[-1]
        overlays = WorldMapOverlays(
            settlements=[
                SettlementMapOverlay(
                    settlement_id=f"{cell.region_id}:s1",
                    region_id=cell.region_id,
                    display_name="Anchor Hamlet",
                    x=town_micro.center_x,
                    y=town_micro.center_y,
                    population=10,
                    status="active",
                )
            ],
            polities_by_region_id={},
            features=[
                FeatureMapOverlay(
                    feature_id=f"{cell.region_id}:f1",
                    region_id=cell.region_id,
                    kind="spring",
                    display_name="Anchor Spring",
                    x=feature_micro.center_x,
                    y=feature_micro.center_y,
                    etymology="anchor · spring",
                )
            ],
        )

        svg = render_world_map_svg(geometry, overlays=overlays, max_feature_labels=0)

        self.assertIn(">Anchor Hamlet</text>", svg)
        self.assertIn(">Anchor Spring</text>", svg)

    def test_named_local_feature_suppresses_source_world_feature(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        source = next(f for f in geometry.features if f.kind == "spring")
        overlays = WorldMapOverlays(
            settlements=[],
            polities_by_region_id={},
            features=[
                FeatureMapOverlay(
                    feature_id=f"{source.region_id}:f1",
                    region_id=source.region_id,
                    kind=source.kind,
                    display_name="Named Spring",
                    x=source.x,
                    y=source.y,
                    etymology="named · spring",
                    source_region_feature_id=source.feature_id,
                )
            ],
        )

        svg = render_world_map_svg(geometry, overlays=overlays)

        self.assertIn(f'data-feature-id="{source.region_id}:f1"', svg)
        self.assertIn(f'data-source-region-feature-id="{source.feature_id}"', svg)
        self.assertNotIn(f'data-feature-id="{source.feature_id}"', svg)
        self.assertIn(">Named Spring</text>", svg)

    def test_active_things_claim_one_marker_per_micro_polygon(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        micro = geometry.micro_cells[0]
        overlays = WorldMapOverlays(
            settlements=[
                SettlementMapOverlay(
                    settlement_id=f"{micro.region_id}:active",
                    region_id=micro.region_id,
                    display_name="Active Town",
                    x=micro.center_x,
                    y=micro.center_y,
                    population=42,
                    status="active",
                ),
                SettlementMapOverlay(
                    settlement_id=f"{micro.region_id}:abandoned",
                    region_id=micro.region_id,
                    display_name="Old Town",
                    x=micro.center_x,
                    y=micro.center_y,
                    population=42,
                    status="abandoned",
                ),
            ],
            polities_by_region_id={},
            features=[
                FeatureMapOverlay(
                    feature_id=f"{micro.region_id}:spring",
                    region_id=micro.region_id,
                    kind="spring",
                    display_name="Crowded Spring",
                    x=micro.center_x,
                    y=micro.center_y,
                )
            ],
        )

        svg = render_world_map_svg(geometry, overlays=overlays)

        self.assertIn(f'data-settlement-id="{micro.region_id}:active"', svg)
        self.assertIn(f'data-settlement-id="{micro.region_id}:abandoned"', svg)
        self.assertNotIn(f'data-feature-id="{micro.region_id}:spring"', svg)

    def test_rendered_active_things_have_unique_micro_polygons(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        width = 1200
        height = 800
        pad = 36
        svg = render_world_map_svg(geometry, width=width, height=height, overlays=None)
        polygon_ids: list[str] = []
        for match in re.finditer(
            r'<g class="feature [^"]*" (?P<outer>[^>]*)>\s*<g (?P<inner>[^>]*)>',
            svg,
        ):
            outer_attrs = match.group("outer")
            inner_attrs = match.group("inner")
            region_match = re.search(r'data-region-id="([^"]+)"', inner_attrs)
            x_match = re.search(r'data-point-x="([^"]+)"', outer_attrs)
            y_match = re.search(r'data-point-y="([^"]+)"', outer_attrs)
            self.assertIsNotNone(region_match)
            self.assertIsNotNone(x_match)
            self.assertIsNotNone(y_match)
            polygon_id = _thing_polygon_id(
                geometry,
                x=float(x_match.group(1)),
                y=float(y_match.group(1)),
                width=width,
                height=height,
                pad=pad,
                region_id=region_match.group(1),
            )
            self.assertIsNotNone(polygon_id)
            polygon_ids.append(str(polygon_id))

        self.assertTrue(polygon_ids)
        self.assertEqual(len(polygon_ids), len(set(polygon_ids)))

    def test_overlapping_active_settlements_render_one_per_micro_polygon(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        settlements = [
            SettlementMapOverlay(
                settlement_id=f"{cell.region_id}:s{i}",
                region_id=cell.region_id,
                display_name=f"Town {i}",
                x=cell.center_x,
                y=cell.center_y,
                population=10 + i,
                status="active",
            )
            for i in range(32)
        ]
        overlays = WorldMapOverlays(settlements=settlements, polities_by_region_id={})

        svg = render_world_map_svg(
            geometry,
            overlays=overlays,
            max_settlement_labels=1,
            max_feature_labels=0,
        )

        rendered_labels = re.findall(r">Town \d+</text>", svg)
        rendered_icons = re.findall(r'class="settlement active"', svg)
        self.assertEqual(len(rendered_labels), 1)
        self.assertEqual(len(rendered_icons), 1)
        self.assertIn(">Town 31</text>", svg)

    def test_feature_marker_nudge_stays_inside_allowed_region_polygon(self) -> None:
        occupied = [(44.0, 44.0, 56.0, 56.0)]
        land_polygon = [(0.0, 0.0), (54.0, 0.0), (54.0, 100.0), (0.0, 100.0)]

        x, y = _place_marker(
            50.0,
            50.0,
            4.0,
            occupied,
            bounds=(120.0, 120.0),
            seed="coastal-feature",
            max_offset=34.0,
            allowed_polygon=land_polygon,
        )

        self.assertTrue(_svg_point_in_polygon((x, y), land_polygon))
        self.assertLessEqual(x, 54.0)

    def test_coastline_feature_marker_draws_landward_of_geographic_point(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        feature = next(f for f in geometry.features if f.kind in {"harbor", "bay", "coast"})
        allowed = _feature_marker_allowed_polygon(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            world_point=(feature.x, feature.y),
            region_screen_polygons={
                cell.region_id: [(36 + x * (640 - 72), 36 + y * (420 - 72)) for x, y in cell.polygon]
                for cell in geometry.cells
            },
            width=640,
            height=420,
            pad=36,
        )
        coast_x = 36 + feature.x * (640 - 72)
        coast_y = 36 + feature.y * (420 - 72)

        marker_x, marker_y = _coastline_marker_screen_point(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            world_point=(feature.x, feature.y),
            radius=3.0,
            width=640,
            height=420,
            pad=36,
            allowed_polygon=allowed,
        )

        self.assertLessEqual(((marker_x - coast_x) ** 2 + (marker_y - coast_y) ** 2) ** 0.5, 2.7)
        self.assertTrue(_svg_point_in_polygon((marker_x, marker_y), allowed or []))

    def test_coastline_marker_collision_nudge_stays_near_coastline(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        feature = next(f for f in geometry.features if f.kind in {"harbor", "bay", "coast"})
        width = 640
        height = 420
        pad = 36
        radius = 4.0
        allowed = _feature_marker_allowed_polygon(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            world_point=(feature.x, feature.y),
            region_screen_polygons={
                cell.region_id: [
                    (pad + x * (width - pad * 2), pad + y * (height - pad * 2))
                    for x, y in cell.polygon
                ]
                for cell in geometry.cells
            },
            width=width,
            height=height,
            pad=pad,
        )
        start_x, start_y = _coastline_marker_screen_point(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            world_point=(feature.x, feature.y),
            radius=radius,
            width=width,
            height=height,
            pad=pad,
            allowed_polygon=allowed,
        )
        occupied = [(start_x - radius - 2.0, start_y - radius - 2.0, start_x + radius + 2.0, start_y + radius + 2.0)]

        marker_x, marker_y = _place_coastline_marker(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            x=start_x,
            y=start_y,
            radius=radius,
            occupied=occupied,
            bounds=(width, height),
            seed=(feature.feature_id, "collision"),
            width=width,
            height=height,
            pad=pad,
            allowed_polygon=allowed,
        )
        marker_screen = (marker_x, marker_y)
        coast_edges = [
            (
                (pad + a[0] * (width - pad * 2), pad + a[1] * (height - pad * 2)),
                (pad + b[0] * (width - pad * 2), pad + b[1] * (height - pad * 2)),
            )
            for cell in geometry.micro_cells
            if cell.region_id == feature.region_id and cell.is_coastal
            for a, b in _micro_boundary_edges(geometry.micro_cells).get(cell.micro_id, [])
        ]

        self.assertLessEqual(
            min(_point_segment_distance(marker_screen, a, b) for a, b in coast_edges),
            2.7,
        )

    def test_rendered_coastal_landmarks_stay_within_two_coast_strokes(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        width = 1200
        height = 800
        pad = 36
        coast_stroke_width = 1.35
        max_distance = coast_stroke_width * 2.0
        coastal_feature_ids = {
            feature.feature_id
            for feature in geometry.features
            if feature.kind in {"bay", "harbor", "coast"}
        }

        svg = render_world_map_svg(
            geometry,
            width=width,
            height=height,
            labels=True,
            overlays=None,
        )
        rendered: list[tuple[str, str, float, float]] = []
        for match in re.finditer(
            r'<g class="feature [^"]*" (?P<outer>[^>]*)>\s*<g (?P<inner>[^>]*)>',
            svg,
        ):
            outer_attrs = match.group("outer")
            inner_attrs = match.group("inner")
            kind_match = re.search(r'data-feature-kind="([^"]+)"', inner_attrs)
            if kind_match is None or kind_match.group(1) not in {"bay", "harbor", "coast"}:
                continue
            feature_id_match = re.search(r'data-feature-id="([^"]+)"', inner_attrs)
            x_match = re.search(r'data-point-x="([^"]+)"', outer_attrs)
            y_match = re.search(r'data-point-y="([^"]+)"', outer_attrs)
            self.assertIsNotNone(feature_id_match)
            self.assertIsNotNone(x_match)
            self.assertIsNotNone(y_match)
            rendered.append(
                (
                    feature_id_match.group(1),
                    kind_match.group(1),
                    float(x_match.group(1)),
                    float(y_match.group(1)),
                )
            )

        self.assertTrue(coastal_feature_ids)
        self.assertTrue(rendered)
        self.assertTrue({feature_id for feature_id, _kind, _x, _y in rendered}.issubset(coastal_feature_ids))
        boundary_edges = _micro_boundary_edges(geometry.micro_cells)
        for feature_id, kind, x, y in rendered:
            region_id = feature_id.split(":")[0]
            coast_edges = [
                (
                    (pad + a[0] * (width - pad * 2), pad + a[1] * (height - pad * 2)),
                    (pad + b[0] * (width - pad * 2), pad + b[1] * (height - pad * 2)),
                )
                for cell in geometry.micro_cells
                if cell.region_id == region_id and cell.is_coastal
                for a, b in boundary_edges.get(cell.micro_id, [])
            ]
            self.assertTrue(coast_edges, feature_id)
            distance = min(_point_segment_distance((x, y), a, b) for a, b in coast_edges)
            self.assertLessEqual(distance, max_distance, f"{feature_id} {kind} distance={distance:.3f}")

    def test_feature_icons_use_earth_tone_category_colors(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        overlays = WorldMapOverlays(
            settlements=[],
            polities_by_region_id={},
            features=[
                FeatureMapOverlay(
                    feature_id=f"{cell.region_id}:forest",
                    region_id=cell.region_id,
                    kind="forest",
                    display_name="Greenwood",
                    x=cell.center_x,
                    y=cell.center_y,
                ),
                FeatureMapOverlay(
                    feature_id=f"{cell.region_id}:spring",
                    region_id=cell.region_id,
                    kind="spring",
                    display_name="Blue Spring",
                    x=cell.center_x + 0.02,
                    y=cell.center_y,
                ),
                FeatureMapOverlay(
                    feature_id=f"{cell.region_id}:ridge",
                    region_id=cell.region_id,
                    kind="ridge",
                    display_name="Grey Ridge",
                    x=cell.center_x - 0.02,
                    y=cell.center_y,
                ),
            ],
        )

        svg = render_world_map_svg(geometry, overlays=overlays, max_feature_labels=0)

        self.assertIn('data-icon-name="tree"', svg)
        self.assertIn('data-icon-name="droplet"', svg)
        self.assertIn('data-icon-name="mountain"', svg)
        self.assertIn('fill="#3f6f52"', svg)
        self.assertIn('fill="#3f7891"', svg)
        self.assertIn('fill="#c9c7bd"', svg)

    def test_all_rendered_feature_icons_get_labels(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)

        svg = render_world_map_svg(geometry, labels=True, max_feature_labels=0)

        icon_count = svg.count('data-icon-name="')
        label_count = svg.count('class="feature-label"')
        self.assertGreater(icon_count, 0)
        self.assertEqual(label_count, icon_count)

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

    def test_world_map_overlays_render_outlaw_refuges_with_debug_context(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        cell = geometry.cells[0]
        save_path = Path(self._td.name) / "outlaw-refuge-save.sqlite"
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
                "insert into simulation_settlements values (?, ?, ?, ?, ?, ?, ?)",
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
                """
                create table simulation_outlaw_refuges (
                    refuge_id text primary key,
                    display_name text,
                    region_id text,
                    near_settlement_id text,
                    status text,
                    founded_year integer,
                    discovered_year integer,
                    abandoned_year integer,
                    band_size integer,
                    concealment_01 real,
                    support_01 real,
                    last_activity_year integer,
                    active_case_count integer,
                    details_json text
                )
                """
            )
            con.execute(
                """
                insert into simulation_outlaw_refuges values (
                    'outlaw_refuge:test:1', 'The Blackthorn Crag', ?, ?, 'active',
                    1001, 1003, null, 3, 0.64, 0.12, 1004, 1, '{}'
                )
                """,
                (cell.region_id, f"{cell.region_id}:s1"),
            )
            con.execute(
                """
                create view simulation_outlaw_refuges_readable as
                select refuge_id, display_name, region_id, near_settlement_id, status,
                       founded_year, discovered_year, abandoned_year, band_size,
                       concealment_01, support_01, last_activity_year,
                       active_case_count, details_json
                from simulation_outlaw_refuges
                """
            )
            con.commit()

        overlays = load_world_map_overlays(geometry=geometry, save_db_path=save_path)
        self.assertEqual(len(overlays.outlaw_refuges), 1)
        refuge = overlays.outlaw_refuges[0]
        self.assertEqual(refuge.display_name, "The Blackthorn Crag")
        self.assertEqual(refuge.near_settlement_name, "Test Town")
        self.assertEqual(refuge.active_case_count, 1)
        self.assertNotEqual((refuge.x, refuge.y), (overlays.settlements[0].x, overlays.settlements[0].y))
        self.assertLess(
            math.hypot(refuge.x - overlays.settlements[0].x, refuge.y - overlays.settlements[0].y),
            0.08,
        )

        svg = render_world_map_svg(geometry, overlays=overlays)
        self.assertIn('class="outlaw-refuge active"', svg)
        self.assertIn('data-outlaw-refuge-id="outlaw_refuge:test:1"', svg)
        self.assertIn('data-outlaw-refuge-name="The Blackthorn Crag"', svg)
        self.assertIn("The Blackthorn Crag", svg)
        self.assertNotIn(">outlaw_refuge:test:1<", svg)

        debug = build_world_map_overlay_debug_data(geometry, overlays)
        self.assertEqual(debug["counts"]["outlaw_refuges"], 1)
        self.assertEqual(debug["outlaw_refuges"]["rows"][0]["display_name"], "The Blackthorn Crag")
        self.assertEqual(debug["outlaw_refuges"]["rows"][0]["near_settlement_name"], "Test Town")
        self.assertLess(debug["outlaw_refuges"]["rows"][0]["distance_to_near_settlement"], 0.08)

    def test_local_anchor_settlement_and_feature_share_projection(self) -> None:
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
                "insert into simulation_settlements values (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{cell.region_id}:s1",
                    cell.region_id,
                    "Anchor Town",
                    42,
                    "active",
                    1,
                    json.dumps(
                        {
                            "features": [
                                {
                                    "feature_id": f"{cell.region_id}:f1",
                                    "kind": "stream",
                                    "x": 0.5,
                                    "y": 0.5,
                                    "display_name": "Anchor Stream",
                                }
                            ],
                            "settlements": [
                                {
                                    "settlement_slot": 0,
                                    "x": 0.5,
                                    "y": 0.5,
                                    "anchor_feature_id": f"{cell.region_id}:f1",
                                    "world_x": 0.01,
                                    "world_y": 0.01,
                                }
                            ],
                        }
                    ),
                ),
            )
            con.commit()

        overlays = load_world_map_overlays(geometry=geometry, save_db_path=save_path)

        self.assertEqual(len(overlays.settlements), 1)
        self.assertEqual(len(overlays.features), 1)
        self.assertAlmostEqual(overlays.settlements[0].x, overlays.features[0].x, places=6)
        self.assertAlmostEqual(overlays.settlements[0].y, overlays.features[0].y, places=6)

    def test_local_named_feature_overlays_respect_coastline_and_interior_rules(self) -> None:
        geometry = build_world_map_geometry(world="default", db_path=self.cfg)
        boundary_edges = _micro_boundary_edges(geometry.micro_cells)
        coastal_region_id = next(
            cell.region_id
            for cell in geometry.micro_cells
            if cell.is_coastal
            and any(
                other.region_id == cell.region_id and not other.is_coastal
                for other in geometry.micro_cells
            )
        )
        save_path = Path(self._td.name) / "coastal-features-save.sqlite"
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
                "insert into simulation_settlements values (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{coastal_region_id}:s1",
                    coastal_region_id,
                    "Coast Town",
                    42,
                    "active",
                    1,
                    json.dumps(
                        {
                            "features": [
                                {
                                    "feature_id": f"{coastal_region_id}:harbor",
                                    "kind": "harbor",
                                    "x": 0.5,
                                    "y": 0.5,
                                    "display_name": "Test Harbor",
                                },
                                {
                                    "feature_id": f"{coastal_region_id}:meadow",
                                    "kind": "meadow",
                                    "x": 0.5,
                                    "y": 0.5,
                                    "display_name": "Test Meadow",
                                },
                            ],
                            "settlements": [{"settlement_slot": 0, "x": 0.5, "y": 0.5}],
                        }
                    ),
                ),
            )
            con.commit()

        overlays = load_world_map_overlays(geometry=geometry, save_db_path=save_path)
        by_name = {feature.display_name: feature for feature in overlays.features}
        harbor = by_name["Test Harbor"]
        meadow = by_name["Test Meadow"]
        coast_edges = [
            edge
            for cell in geometry.micro_cells
            if cell.region_id == coastal_region_id and cell.is_coastal
            for edge in boundary_edges.get(cell.micro_id, [])
        ]
        self.assertLessEqual(
            min(_point_segment_distance((harbor.x, harbor.y), a, b) for a, b in coast_edges),
            0.00001,
        )
        meadow_micro_id = micro_cell_id_for_world_point(
            geometry,
            (meadow.x, meadow.y),
            region_id=coastal_region_id,
        )
        self.assertIsNotNone(meadow_micro_id)
        self.assertFalse({c.micro_id: c for c in geometry.micro_cells}[meadow_micro_id].is_coastal)

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

