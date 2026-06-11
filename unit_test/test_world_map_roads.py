import json
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.world_map_geometry import MicroRegionCell, RegionCell, RegionEdge, WorldMapGeometry
from library.world_map_svg import load_world_map_overlays, render_world_map_svg
from library.world_map_roads import RoadMapEdge, _clean_road_points, build_settlement_road_overlays


def _region_cell_for(
    region_id: str,
    polygon: list[tuple[float, float]],
    *,
    continent_id: str = "c1",
) -> RegionCell:
    center_x = sum(x for x, _ in polygon) / len(polygon)
    center_y = sum(y for _, y in polygon) / len(polygon)
    return RegionCell(
        region_id=region_id,
        continent_id=continent_id,
        center_x=center_x,
        center_y=center_y,
        polygon=polygon,
        elevation=0.2,
        moisture=0.5,
        ruggedness=0.2,
        terrain_family="plains",
        is_coastal=False,
        feature_ids=[],
    )


def _region_cell() -> RegionCell:
    return _region_cell_for("r1", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])


def _geometry(*, micro_cells: list[MicroRegionCell] | None = None) -> WorldMapGeometry:
    return WorldMapGeometry(
        world="test",
        version="unit",
        width=1.0,
        height=1.0,
        cells=[_region_cell()],
        micro_cells=micro_cells or [],
        features=[],
        edges=[],
        rivers=[],
    )


def _three_region_route_geometry() -> WorldMapGeometry:
    return WorldMapGeometry(
        world="test",
        version="unit",
        width=1.0,
        height=1.0,
        cells=[
            _region_cell_for("r1", [(0.02, 0.25), (0.32, 0.25), (0.32, 0.75), (0.02, 0.75)]),
            _region_cell_for("r2", [(0.34, 0.25), (0.66, 0.25), (0.66, 0.75), (0.34, 0.75)]),
            _region_cell_for("r3", [(0.68, 0.25), (0.98, 0.25), (0.98, 0.75), (0.68, 0.75)]),
        ],
        micro_cells=[],
        features=[],
        edges=[
            RegionEdge(
                from_region_id="r1",
                to_region_id="r2",
                route_type="land",
                friction=1.0,
                points=[(0.30, 0.52), (0.42, 0.30), (0.50, 0.30)],
                edge_class="land_route",
            ),
            RegionEdge(
                from_region_id="r2",
                to_region_id="r3",
                route_type="land",
                friction=1.0,
                points=[(0.50, 0.30), (0.62, 0.70), (0.70, 0.55)],
                edge_class="land_route",
            ),
        ],
        rivers=[],
    )


def _micro_cell(
    micro_id: str,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    is_channel: bool = False,
    river_flow: float = 0.0,
    river_side: float = 0.0,
    elevation: float = 0.25,
    moisture: float = 0.55,
    terrain_family: str | None = None,
    region_id: str = "r1",
) -> MicroRegionCell:
    return MicroRegionCell(
        micro_id=micro_id,
        region_id=region_id,
        continent_id="c1",
        center_x=(x0 + x1) / 2.0,
        center_y=(y0 + y1) / 2.0,
        polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        elevation=elevation,
        moisture=moisture,
        terrain_family=terrain_family or ("riverland" if is_channel else "plains"),
        is_coastal=False,
        river_distance=0.0 if is_channel else 0.2,
        river_flow=river_flow,
        river_side=river_side,
        is_floodplain=is_channel,
        is_channel=is_channel,
    )


def _grid_with_channel() -> list[MicroRegionCell]:
    cells: list[MicroRegionCell] = []
    step = 0.2
    for iy in range(5):
        for ix in range(5):
            x0 = ix * step
            y0 = iy * step
            x1 = x0 + step
            y1 = y0 + step
            is_channel = ix == 2
            if ix < 2:
                side = -1.0
            elif ix > 2:
                side = 1.0
            else:
                side = 0.0
            cells.append(
                _micro_cell(
                    f"m{ix}_{iy}",
                    x0,
                    y0,
                    x1,
                    y1,
                    is_channel=is_channel,
                    river_flow=1.0 if is_channel else 0.0,
                    river_side=side,
                )
            )
    return cells


def _fine_grid_with_channel(*, river_flow: float = 1.0) -> list[MicroRegionCell]:
    cells: list[MicroRegionCell] = []
    step = 0.05
    for iy in range(20):
        for ix in range(20):
            x0 = ix * step
            y0 = iy * step
            x1 = x0 + step
            y1 = y0 + step
            is_channel = ix in {9, 10}
            if ix < 9:
                side = -1.0
            elif ix > 10:
                side = 1.0
            else:
                side = 0.0
            cells.append(
                _micro_cell(
                    f"fm{ix}_{iy}",
                    x0,
                    y0,
                    x1,
                    y1,
                    is_channel=is_channel,
                    river_flow=river_flow if is_channel else 0.0,
                    river_side=side,
                )
            )
    return cells


def _two_by_two_grid() -> list[MicroRegionCell]:
    cells: list[MicroRegionCell] = []
    step = 0.5
    for iy in range(2):
        for ix in range(2):
            x0 = ix * step
            y0 = iy * step
            cells.append(_micro_cell(f"q{ix}_{iy}", x0, y0, x0 + step, y0 + step))
    return cells


def _adjacent_region_grid_geometry() -> WorldMapGeometry:
    cells: list[MicroRegionCell] = []
    step = 0.25
    for iy in range(4):
        for ix in range(4):
            region_id = "r1" if ix < 2 else "r2"
            x0 = ix * step
            y0 = iy * step
            cells.append(_micro_cell(f"ar{ix}_{iy}", x0, y0, x0 + step, y0 + step, region_id=region_id))
    return WorldMapGeometry(
        world="test",
        version="unit",
        width=1.0,
        height=1.0,
        cells=[
            _region_cell_for("r1", [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]),
            _region_cell_for("r2", [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)]),
        ],
        micro_cells=cells,
        features=[],
        edges=[
            RegionEdge(
                from_region_id="r1",
                to_region_id="r2",
                route_type="land",
                friction=1.0,
                points=[(0.12, 0.12), (0.88, 0.88)],
                edge_class="land_route",
            )
        ],
        rivers=[],
    )


def _gap_route_geometry() -> WorldMapGeometry:
    cells: list[MicroRegionCell] = []
    step = 0.2
    for iy in range(5):
        for ix in range(5):
            if ix == 2 and iy in {1, 2, 3}:
                continue
            region_id = "r1" if ix < 2 else "r2"
            x0 = ix * step
            y0 = iy * step
            cells.append(_micro_cell(f"gap{ix}_{iy}", x0, y0, x0 + step, y0 + step, region_id=region_id))
    return WorldMapGeometry(
        world="test",
        version="unit",
        width=1.0,
        height=1.0,
        cells=[
            _region_cell_for("r1", [(0.0, 0.0), (0.5, 0.0), (0.5, 1.0), (0.0, 1.0)]),
            _region_cell_for("r2", [(0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)]),
        ],
        micro_cells=cells,
        features=[],
        edges=[
            RegionEdge(
                from_region_id="r1",
                to_region_id="r2",
                route_type="land",
                friction=1.0,
                points=[(0.10, 0.50), (0.90, 0.50)],
                edge_class="land_route",
            )
        ],
        rivers=[],
    )


def _grid_with_bad_middle_corridor() -> list[MicroRegionCell]:
    cells: list[MicroRegionCell] = []
    step = 0.1
    for iy in range(10):
        for ix in range(10):
            x0 = ix * step
            y0 = iy * step
            x1 = x0 + step
            y1 = y0 + step
            awkward_middle = iy in {4, 5} and ix not in {0, 9}
            cells.append(
                _micro_cell(
                    f"bm{ix}_{iy}",
                    x0,
                    y0,
                    x1,
                    y1,
                    is_channel=awkward_middle,
                    river_flow=0.75 if awkward_middle else 0.0,
                    terrain_family="riverland" if awkward_middle else "plains",
                    elevation=0.2 if not awkward_middle else 0.3,
                    moisture=0.5 if not awkward_middle else 0.95,
                )
            )
    return cells


def _settlement_json(
    x: float,
    y: float,
    *,
    ford: tuple[float, float] | None = None,
    world: tuple[float, float] | None = None,
) -> str:
    features = []
    if ford is not None:
        features.append(
            {
                "feature_id": "r1:ford",
                "kind": "ford",
                "x": ford[0],
                "y": ford[1],
                "display_name": "North Ford",
            }
        )
    site: dict[str, object] = {"settlement_slot": 0, "x": x, "y": y}
    if world is not None:
        site["world_x"] = world[0]
        site["world_y"] = world[1]
    return json.dumps(
        {
            "features": features,
            "settlements": [site],
        }
    )


def _make_save(
    root: Path,
    settlements: dict[str, tuple[float, float]],
    moves: list[tuple[int, str, str, int]],
    *,
    current_year: int = 10,
    ford: tuple[float, float] | None = None,
    world_points: dict[str, tuple[float, float]] | None = None,
    region_by_settlement: dict[str, str] | None = None,
) -> Path:
    path = root / "save.sqlite"
    with closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        con.execute("create table world_state (id integer primary key, current_year integer)")
        con.execute("insert into world_state values (1, ?)", (current_year,))
        con.execute(
            """
            create table simulation_settlements (
                settlement_id text,
                region_id text,
                display_name text,
                population_cap integer,
                status text,
                site_slot integer,
                local_geography_json text,
                market_pull real,
                prosperity_pool real,
                founding_reason text,
                mother_settlement_id text,
                trade_network_id text,
                autonomy_level text
            )
            """
        )
        for idx, (sid, (x, y)) in enumerate(settlements.items(), start=1):
            region_id = (region_by_settlement or {}).get(sid, "r1")
            con.execute(
                """
                insert into simulation_settlements values (
                    ?, ?, ?, ?, 'active', 1, ?, 0.05, 0.1,
                    'organic', null, ?, 'autonomous'
                )
                """,
                (
                    sid,
                    region_id,
                    sid.upper(),
                    20 + idx,
                    _settlement_json(
                        x,
                        y,
                        ford=ford if idx == 1 else None,
                        world=(world_points or {}).get(sid),
                    ),
                    sid,
                ),
            )
        con.execute(
            """
            create table simulation_event_moves_readable (
                event_id integer,
                sim_year integer,
                event_type text,
                moved_person_id integer,
                from_settlement_id text,
                to_settlement_id text,
                move_reason text
            )
            """
        )
        event_id = 1
        for year, src, dst, count in moves:
            for offset in range(count):
                con.execute(
                    """
                    insert into simulation_event_moves_readable values (
                        ?, ?, 'settlement_moved', ?, ?, ?, 'unit_test'
                    )
                    """,
                    (event_id, year, event_id * 100 + offset, src, dst),
                )
            event_id += 1
        con.commit()
    return path


def _edge(edges: list[RoadMapEdge], a: str, b: str) -> RoadMapEdge | None:
    wanted = {a, b}
    for road in edges:
        if {road.from_settlement_id, road.to_settlement_id} == wanted:
            return road
    return None


def _segment_samples_gap(points: list[tuple[float, float]]) -> bool:
    for start, end in zip(points, points[1:]):
        for idx in range(1, 8):
            t = idx / 8.0
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] + (end[1] - start[1]) * t
            if 0.4 < x < 0.6 and 0.2 < y < 0.8:
                return True
    return False


class TestWorldMapRoads(unittest.TestCase):
    def test_world_map_settlement_markers_use_saved_world_anchor_like_roads(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.1), "b": (0.2, 0.2)},
                [(10, "a", "b", 2)],
                world_points={"a": (0.8, 0.75), "b": (0.65, 0.6)},
            )

            overlays = load_world_map_overlays(geometry=_geometry(), save_db_path=save)

        marker_a = next(s for s in overlays.settlements if s.settlement_id == "a")
        road = _edge(overlays.roads, "a", "b")
        self.assertAlmostEqual(marker_a.x, 0.8, places=6)
        self.assertAlmostEqual(marker_a.y, 0.75, places=6)
        self.assertIsNotNone(road)
        self.assertAlmostEqual(road.points[0][0], marker_a.x, places=6)
        self.assertAlmostEqual(road.points[0][1], marker_a.y, places=6)

    def test_latest_year_movement_only(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.5), "b": (0.5, 0.5), "c": (0.9, 0.5)},
                [(9, "a", "c", 20), (10, "a", "b", 3)],
            )

            roads = build_settlement_road_overlays(geometry=_geometry(), save_db_path=save)

        self.assertEqual(_edge(roads, "a", "b").actual_usage, 3)
        ac = _edge(roads, "a", "c")
        if ac is not None:
            self.assertEqual(ac.actual_usage, 0)

    def test_actual_usage_has_higher_opacity_than_implied_only(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.5), "b": (0.45, 0.5), "c": (0.75, 0.5)},
                [(10, "a", "b", 4)],
            )

            roads = build_settlement_road_overlays(geometry=_geometry(), save_db_path=save)

        actual = max(road.opacity for road in roads if road.actual_usage > 0)
        implied = max(road.opacity for road in roads if road.actual_usage == 0)
        self.assertGreater(actual, implied)

    def test_population_counts_use_latest_readable_cohorts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.5), "b": (0.5, 0.5)},
                [],
            )
            with closing(sqlite3.connect(save)) as con:
                con.execute(
                    """
                    create table simulation_cohorts_readable (
                        sim_year integer,
                        settlement_id text,
                        population_count integer
                    )
                    """
                )
                con.executemany(
                    "insert into simulation_cohorts_readable values (?, ?, ?)",
                    [
                        (9, "a", 5),
                        (9, "b", 5),
                        (10, "a", 100),
                        (10, "b", 400),
                    ],
                )
                con.commit()

            roads = build_settlement_road_overlays(geometry=_geometry(), save_db_path=save)

        road = _edge(roads, "a", "b")
        self.assertIsNotNone(road)
        self.assertGreater(road.implied_usage, 2.5)

    def test_reuses_midpoint_town_when_circuity_is_low(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.5), "b": (0.5, 0.5), "c": (0.9, 0.5)},
                [(10, "a", "c", 2)],
            )

            roads = build_settlement_road_overlays(geometry=_geometry(), save_db_path=save)

        self.assertIsNone(_edge(roads, "a", "c"))
        self.assertIsNotNone(_edge(roads, "a", "b"))
        self.assertIsNotNone(_edge(roads, "b", "c"))

    def test_reuses_midpoint_town_by_geometric_circuity_when_terrain_path_bends(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.05, 0.5), "b": (0.5, 0.5), "c": (0.95, 0.5)},
                [(10, "a", "c", 2)],
            )

            roads = build_settlement_road_overlays(
                geometry=_geometry(micro_cells=_grid_with_bad_middle_corridor()),
                save_db_path=save,
            )

        self.assertIsNone(_edge(roads, "a", "c"))
        self.assertIsNotNone(_edge(roads, "a", "b"))
        self.assertIsNotNone(_edge(roads, "b", "c"))

    def test_cross_region_actual_road_follows_configured_land_route(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.10, 0.50), "c": (0.90, 0.50)},
                [(10, "a", "c", 4)],
                region_by_settlement={"a": "r1", "c": "r3"},
            )

            roads = build_settlement_road_overlays(
                geometry=_three_region_route_geometry(),
                save_db_path=save,
            )

        road = _edge(roads, "a", "c")
        self.assertIsNotNone(road)
        ys = [point[1] for point in road.points]
        self.assertGreaterEqual(len(road.points), 5)
        self.assertLess(min(ys), 0.36)
        self.assertGreater(max(ys), 0.64)

    def test_adjacent_region_road_prefers_micro_edge_path_over_route_chord(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.12, 0.12), "b": (0.88, 0.88)},
                [(10, "a", "b", 4)],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.12, 0.12), "b": (0.88, 0.88)},
            )

            roads = build_settlement_road_overlays(
                geometry=_adjacent_region_grid_geometry(),
                save_db_path=save,
            )

        road = _edge(roads, "a", "b")
        self.assertIsNotNone(road)
        self.assertGreaterEqual(len(road.points), 6)
        self.assertGreater(
            sum(math.dist(a, b) for a, b in zip(road.points, road.points[1:])),
            math.dist((0.12, 0.12), (0.88, 0.88)) * 1.25,
        )
        self.assertNotEqual(road.points, [(0.12, 0.12), (0.88, 0.88)])

    def test_cross_region_road_routes_around_non_land_gap(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.10, 0.50), "b": (0.90, 0.50)},
                [(10, "a", "b", 4)],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.10, 0.50), "b": (0.90, 0.50)},
            )

            roads = build_settlement_road_overlays(
                geometry=_gap_route_geometry(),
                save_db_path=save,
            )

        road = _edge(roads, "a", "b")
        self.assertIsNotNone(road)
        self.assertFalse(_segment_samples_gap(road.points))
        self.assertGreater(
            sum(math.dist(a, b) for a, b in zip(road.points, road.points[1:])),
            math.dist((0.10, 0.50), (0.90, 0.50)) * 1.2,
        )

    def test_road_point_cleanup_prunes_tiny_hairpin_before_svg_smoothing(self) -> None:
        points = [(0.1, 0.1), (0.125, 0.102), (0.1008, 0.1006), (0.18, 0.12)]

        cleaned = _clean_road_points(points)

        self.assertEqual(cleaned, [(0.1, 0.1), (0.18, 0.12)])

    def test_road_point_cleanup_prunes_returned_polygon_loop(self) -> None:
        points = [
            (0.10, 0.10),
            (0.20, 0.10),
            (0.22, 0.16),
            (0.18, 0.19),
            (0.20, 0.10),
            (0.30, 0.12),
        ]

        cleaned = _clean_road_points(points)

        self.assertEqual(cleaned, [(0.10, 0.10), (0.20, 0.10), (0.30, 0.12)])

    def test_direct_road_when_indirect_circuity_is_high(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.1), "b": (0.1, 0.9), "c": (0.9, 0.1)},
                [(10, "a", "c", 2)],
            )

            roads = build_settlement_road_overlays(geometry=_geometry(), save_db_path=save)

        self.assertIsNotNone(_edge(roads, "a", "c"))

    def test_ford_crossing_is_preferred_over_unbridged_channel_shortcut(self) -> None:
        ford = (0.5, 0.9)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.1), "c": (0.9, 0.1)},
                [(10, "a", "c", 2)],
                ford=ford,
            )

            roads = build_settlement_road_overlays(
                geometry=_geometry(micro_cells=_grid_with_channel()),
                save_db_path=save,
            )

        road = _edge(roads, "a", "c")
        self.assertIsNotNone(road)
        self.assertLess(
            min(((x - ford[0]) ** 2 + (y - ford[1]) ** 2) ** 0.5 for x, y in road.points),
            0.13,
        )
        self.assertEqual(
            len([(x, y) for x, y in road.points if 0.42 <= x <= 0.58 and y < 0.75]),
            0,
        )

    def test_terrain_route_uses_polygon_boundary_corner_waypoints(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.1), "c": (0.9, 0.9)},
                [(10, "a", "c", 2)],
            )
            geometry = _geometry(micro_cells=_two_by_two_grid())

            roads = build_settlement_road_overlays(geometry=geometry, save_db_path=save)
            overlays = load_world_map_overlays(geometry=geometry, save_db_path=save)
            svg = render_world_map_svg(geometry, overlays=overlays)

        road = _edge(roads, "a", "c")
        self.assertIsNotNone(road)
        self.assertIn((0.5, 0.5), road.points)
        road_layer = svg.split('<g class="road-layer settlement-roads">', 1)[1].split("</g>", 1)[0]
        self.assertNotIn(" Q ", road_layer)
        self.assertNotIn(" T ", road_layer)

    def test_distant_ford_does_not_create_hairpin_detour(self) -> None:
        distant_ford = (0.5, 0.62)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.44, 0.5), "c": (0.56, 0.5)},
                [(10, "a", "c", 2)],
                ford=distant_ford,
            )

            roads = build_settlement_road_overlays(
                geometry=_geometry(micro_cells=_fine_grid_with_channel(river_flow=0.25)),
                save_db_path=save,
            )

        road = _edge(roads, "a", "c")
        self.assertIsNotNone(road)
        self.assertGreater(
            min(((x - distant_ford[0]) ** 2 + (y - distant_ford[1]) ** 2) ** 0.5 for x, y in road.points),
            0.08,
        )

    def test_route_to_ford_endpoint_does_not_loop_around_micro_cells(self) -> None:
        ford = (0.5, 0.62)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.44, 0.5), "b": ford},
                [(10, "a", "b", 2)],
                ford=ford,
            )

            roads = build_settlement_road_overlays(
                geometry=_geometry(micro_cells=_fine_grid_with_channel(river_flow=0.9)),
                save_db_path=save,
            )

        road = _edge(roads, "a", "b")
        self.assertIsNotNone(road)
        direct = ((0.44 - ford[0]) ** 2 + (0.5 - ford[1]) ** 2) ** 0.5
        length = sum(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 for (ax, ay), (bx, by) in zip(road.points, road.points[1:]))
        self.assertLess(length / direct, 1.25)

    def test_reuses_ford_town_when_terrain_route_passes_through_it(self) -> None:
        ford = (0.5, 0.9)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.1), "b": ford, "c": (0.9, 0.1)},
                [(10, "a", "c", 2)],
                ford=ford,
            )

            roads = build_settlement_road_overlays(
                geometry=_geometry(micro_cells=_grid_with_channel()),
                save_db_path=save,
            )

        self.assertIsNone(_edge(roads, "a", "c"))
        self.assertIsNotNone(_edge(roads, "a", "b"))
        self.assertIsNotNone(_edge(roads, "b", "c"))

    def test_implied_only_extreme_detour_is_omitted_without_reuse(self) -> None:
        ford = (0.5, 0.9)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.1, 0.1), "c": (0.9, 0.1)},
                [],
                ford=ford,
            )

            roads = build_settlement_road_overlays(
                geometry=_geometry(micro_cells=_grid_with_channel()),
                save_db_path=save,
            )

        self.assertIsNone(_edge(roads, "a", "c"))


if __name__ == "__main__":
    unittest.main()
