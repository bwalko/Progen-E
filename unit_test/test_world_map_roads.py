import json
import math
import re
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.world_map_geometry import (
    MicroRegionCell,
    RegionCell,
    RegionEdge,
    RiverPath,
    RiverSegment,
    WorldMapGeometry,
)
from library.world_map_svg import (
    SettlementMapOverlay,
    WorldMapOverlays,
    _soften_polyline_corners,
    build_world_map_overlay_debug_data,
    load_world_map_overlays,
    render_world_map_svg,
)
from library.world_map_roads import (
    RoadMapEdge,
    SeaRouteMapEdge,
    _clean_road_points,
    build_settlement_road_overlays,
    build_settlement_sea_route_overlays,
)


def _region_cell_for(
    region_id: str,
    polygon: list[tuple[float, float]],
    *,
    continent_id: str = "c1",
    terrain_family: str = "plains",
    is_coastal: bool = False,
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
        terrain_family=terrain_family,
        is_coastal=is_coastal,
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


def _two_port_sea_geometry() -> WorldMapGeometry:
    return WorldMapGeometry(
        world="test",
        version="unit",
        width=1.0,
        height=1.0,
        cells=[
            _region_cell_for(
                "r1",
                [(0.04, 0.28), (0.30, 0.28), (0.30, 0.72), (0.04, 0.72)],
                continent_id="c1",
                terrain_family="coast",
                is_coastal=True,
            ),
            _region_cell_for(
                "r2",
                [(0.70, 0.28), (0.96, 0.28), (0.96, 0.72), (0.70, 0.72)],
                continent_id="c2",
                terrain_family="coast",
                is_coastal=True,
            ),
        ],
        micro_cells=[
            _micro_cell(
                "r1:coast",
                0.04,
                0.28,
                0.30,
                0.72,
                region_id="r1",
                continent_id="c1",
                terrain_family="coast",
                is_coastal=True,
            ),
            _micro_cell(
                "r2:coast",
                0.70,
                0.28,
                0.96,
                0.72,
                region_id="r2",
                continent_id="c2",
                terrain_family="coast",
                is_coastal=True,
            ),
        ],
        features=[],
        edges=[
            RegionEdge(
                from_region_id="r1",
                to_region_id="r2",
                route_type="sea",
                friction=16.0,
                points=[(0.30, 0.52), (0.43, 0.38), (0.58, 0.36), (0.70, 0.50)],
                edge_class="sea_route",
            )
        ],
        rivers=[],
    )


def _two_port_sea_geometry_with_island_blocker() -> WorldMapGeometry:
    geometry = _two_port_sea_geometry()
    island_cell = _region_cell_for(
        "island",
        [(0.44, 0.42), (0.56, 0.42), (0.56, 0.58), (0.44, 0.58)],
        continent_id="c3",
        terrain_family="coast",
        is_coastal=True,
    )
    island_micro = _micro_cell(
        "island:coast",
        0.44,
        0.42,
        0.56,
        0.58,
        region_id="island",
        continent_id="c3",
        terrain_family="coast",
        is_coastal=True,
    )
    return WorldMapGeometry(
        world=geometry.world,
        version=geometry.version,
        width=geometry.width,
        height=geometry.height,
        cells=[*geometry.cells, island_cell],
        micro_cells=[*geometry.micro_cells, island_micro],
        features=[],
        edges=[
            RegionEdge(
                from_region_id="r1",
                to_region_id="r2",
                route_type="sea",
                friction=16.0,
                points=[(0.30, 0.50), (0.50, 0.50), (0.70, 0.50)],
                edge_class="sea_route",
            )
        ],
        rivers=[],
    )


def _coastal_land_and_sea_choice_geometry() -> WorldMapGeometry:
    return WorldMapGeometry(
        world="test",
        version="unit",
        width=1.0,
        height=1.0,
        cells=[
            _region_cell_for(
                "r1",
                [(0.04, 0.28), (0.28, 0.28), (0.28, 0.72), (0.04, 0.72)],
                terrain_family="coast",
                is_coastal=True,
            ),
            _region_cell_for(
                "river",
                [(0.42, 0.70), (0.58, 0.70), (0.58, 0.98), (0.42, 0.98)],
                terrain_family="riverland",
            ),
            _region_cell_for(
                "r2",
                [(0.72, 0.28), (0.96, 0.28), (0.96, 0.72), (0.72, 0.72)],
                terrain_family="coast",
                is_coastal=True,
            ),
        ],
        micro_cells=[],
        features=[],
        edges=[
            RegionEdge(
                from_region_id="r1",
                to_region_id="river",
                route_type="land",
                friction=1.0,
                points=[(0.24, 0.55), (0.18, 0.96), (0.50, 0.96)],
                edge_class="land_route",
            ),
            RegionEdge(
                from_region_id="river",
                to_region_id="r2",
                route_type="land",
                friction=1.0,
                points=[(0.50, 0.96), (0.82, 0.96), (0.76, 0.55)],
                edge_class="land_route",
            ),
            RegionEdge(
                from_region_id="r1",
                to_region_id="r2",
                route_type="sea",
                friction=14.0,
                points=[(0.24, 0.48), (0.50, 0.18), (0.76, 0.48)],
                edge_class="sea_route",
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
    continent_id: str = "c1",
    is_coastal: bool = False,
) -> MicroRegionCell:
    return MicroRegionCell(
        micro_id=micro_id,
        region_id=region_id,
        continent_id=continent_id,
        center_x=(x0 + x1) / 2.0,
        center_y=(y0 + y1) / 2.0,
        polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        elevation=elevation,
        moisture=moisture,
        terrain_family=terrain_family or ("riverland" if is_channel else "plains"),
        is_coastal=is_coastal,
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


def _edge(edges: list[RoadMapEdge] | list[SeaRouteMapEdge], a: str, b: str) -> RoadMapEdge | SeaRouteMapEdge | None:
    wanted = {a, b}
    for road in edges:
        if {road.from_settlement_id, road.to_settlement_id} == wanted:
            return road
    return None


def _svg_path_d(svg: str, class_name: str) -> str:
    match = re.search(rf'<path class="{re.escape(class_name)}"[^>]* d="([^"]+)"', svg)
    if match is None:
        raise AssertionError(f"missing SVG path for class {class_name!r}")
    return match.group(1)


def _svg_path_world_points(path_d: str, *, width: int = 1200, height: int = 800, pad: int = 36) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for x_text, y_text in re.findall(r"[ML]\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", path_d):
        x = (float(x_text) - pad) / max(1e-9, width - pad * 2)
        y = (float(y_text) - pad) / max(1e-9, height - pad * 2)
        out.append((x, y))
    return out


def _svg_circle_world_points(
    svg: str,
    class_name: str,
    *,
    width: int = 1200,
    height: int = 800,
    pad: int = 36,
) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for attrs in re.findall(rf'<circle class="{re.escape(class_name)}"([^>]*)>', svg):
        x_match = re.search(r'cx="(-?\d+(?:\.\d+)?)"', attrs)
        y_match = re.search(r'cy="(-?\d+(?:\.\d+)?)"', attrs)
        if x_match is None or y_match is None:
            continue
        x = (float(x_match.group(1)) - pad) / max(1e-9, width - pad * 2)
        y = (float(y_match.group(1)) - pad) / max(1e-9, height - pad * 2)
        out.append((x, y))
    return out


def _segment_samples_gap(points: list[tuple[float, float]]) -> bool:
    for start, end in zip(points, points[1:]):
        for idx in range(1, 8):
            t = idx / 8.0
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] + (end[1] - start[1]) * t
            if 0.4 < x < 0.6 and 0.2 < y < 0.8:
                return True
    return False


def _segment_samples_rect(
    points: list[tuple[float, float]],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> bool:
    for start, end in zip(points, points[1:]):
        for idx in range(1, 16):
            t = idx / 16.0
            x = start[0] + (end[0] - start[0]) * t
            y = start[1] + (end[1] - start[1]) * t
            if x0 < x < x1 and y0 < y < y1:
                return True
    return False


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _max_segment_fraction(points: list[tuple[float, float]]) -> float:
    length = _path_length(points)
    if length <= 0.0:
        return 0.0
    return max((math.dist(a, b) for a, b in zip(points, points[1:])), default=0.0) / length


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

    def test_minor_road_inside_stronger_corridor_is_suppressed(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {
                    "a": (0.10, 0.50),
                    "b": (0.90, 0.50),
                    "c": (0.20, 0.52),
                    "d": (0.80, 0.52),
                },
                [(10, "a", "b", 30), (10, "c", "d", 1)],
            )

            roads = build_settlement_road_overlays(geometry=_geometry(), save_db_path=save)

        self.assertIsNone(_edge(roads, "c", "d"))
        self.assertTrue(any(road.usage >= 30.0 for road in roads))

    def test_minor_branch_to_distinct_destination_survives_main_corridor(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {
                    "a": (0.10, 0.50),
                    "b": (0.90, 0.50),
                    "c": (0.20, 0.52),
                    "e": (0.45, 0.78),
                },
                [(10, "a", "b", 30), (10, "c", "e", 1)],
            )

            roads = build_settlement_road_overlays(geometry=_geometry(), save_db_path=save)

        self.assertIsNotNone(_edge(roads, "c", "e"))

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

    def test_cross_continent_sea_route_uses_water_polyline_not_land_road(self) -> None:
        geometry = _two_port_sea_geometry()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.20, 0.50), "b": (0.80, 0.50)},
                [(10, "a", "b", 4)],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.20, 0.50), "b": (0.80, 0.50)},
            )

            roads = build_settlement_road_overlays(geometry=geometry, save_db_path=save)
            sea_routes = build_settlement_sea_route_overlays(geometry=geometry, save_db_path=save)
            overlays = load_world_map_overlays(geometry=geometry, save_db_path=save)
            svg = render_world_map_svg(geometry, overlays=overlays)

        self.assertIsNone(_edge(roads, "a", "b"))
        sea_route = _edge(sea_routes, "a", "b")
        self.assertIsNotNone(sea_route)
        self.assertEqual(sea_route.actual_usage, 4)
        self.assertEqual(sea_route.route_regions, ("r1", "r2"))
        self.assertGreaterEqual(len(sea_route.points), 4)
        self.assertTrue(any(0.32 < x < 0.68 for x, _y in sea_route.points))
        direct = math.dist((0.20, 0.50), (0.80, 0.50))
        length = sum(math.dist(a, b) for a, b in zip(sea_route.points, sea_route.points[1:]))
        self.assertLess(length, direct * 1.55)
        self.assertIn('class="sea-route sea-route-line"', svg)
        self.assertIn('class="sea-route-harbor"', svg)
        self.assertIn('data-map-layer="sea-route"', svg)
        self.assertIn("<title>Sea route A to B</title>", svg)
        self.assertIn("<title>Sea route harbor A</title>", svg)
        self.assertIn('data-sea-route-harbor-settlement-id="a"', svg)
        self.assertIn('data-sea-route-actual="4.0000"', svg)
        sea_layer = svg.split('<g class="sea-route-layer settlement-sea-routes">', 1)[1].split("</g>", 1)[0]
        self.assertNotIn(" Q ", sea_layer)
        self.assertNotIn(" T ", sea_layer)
        self.assertIn(".road,.sea-route{fill:none;stroke-linejoin:round;vector-effect:non-scaling-stroke;pointer-events:stroke}", svg)
        self.assertIn(".road-underlay,.sea-route-underlay{pointer-events:none}", svg)
        self.assertIn(".road-line,.sea-route-line,.sea-route-harbor{cursor:help}", svg)
        self.assertIn(".sea-route{stroke-linecap:butt}", svg)
        self.assertIn(".sea-route-line{stroke:#174ea6;stroke-dasharray:8 6}", svg)
        self.assertIn(".sea-route-harbor{fill:#174ea6;stroke:#e6fbff", svg)

    def test_sea_route_visible_points_start_near_settlement_harbors(self) -> None:
        geometry = _two_port_sea_geometry()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.08, 0.32), "b": (0.92, 0.68)},
                [(10, "a", "b", 4)],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.08, 0.32), "b": (0.92, 0.68)},
            )

            sea_routes = build_settlement_sea_route_overlays(geometry=geometry, save_db_path=save)
            overlays = load_world_map_overlays(geometry=geometry, save_db_path=save)
            svg = render_world_map_svg(geometry, overlays=overlays)

        sea_route = _edge(sea_routes, "a", "b")
        self.assertIsNotNone(sea_route)
        self.assertLess(math.dist(sea_route.points[0], (0.08, 0.32)), 0.18)
        self.assertLess(math.dist(sea_route.points[-1], (0.92, 0.68)), 0.18)
        self.assertGreater(math.dist(sea_route.points[0], (0.30, 0.52)), 0.08)
        self.assertGreater(math.dist(sea_route.points[-1], (0.70, 0.50)), 0.08)
        rendered_points = _svg_path_world_points(_svg_path_d(svg, "sea-route sea-route-line"))
        self.assertLess(math.dist(rendered_points[0], (0.08, 0.32)), 0.18)
        self.assertLess(math.dist(rendered_points[-1], (0.92, 0.68)), 0.18)
        self.assertGreater(math.dist(rendered_points[0], (0.30, 0.52)), 0.08)
        self.assertGreater(math.dist(rendered_points[-1], (0.70, 0.50)), 0.08)
        harbor_points = _svg_circle_world_points(svg, "sea-route-harbor")
        self.assertEqual(len(harbor_points), 2)
        self.assertLess(math.dist(harbor_points[0], (0.08, 0.32)), 0.18)
        self.assertLess(math.dist(harbor_points[-1], (0.92, 0.68)), 0.18)

    def test_overlay_debug_reports_sea_route_endpoint_and_crossing_qa(self) -> None:
        geometry = _two_port_sea_geometry()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.08, 0.32), "b": (0.92, 0.68)},
                [(10, "a", "b", 4)],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.08, 0.32), "b": (0.92, 0.68)},
            )

            overlays = load_world_map_overlays(geometry=geometry, save_db_path=save)

        debug = build_world_map_overlay_debug_data(geometry, overlays)

        self.assertEqual(debug["counts"]["sea_routes"], 1)
        self.assertEqual(debug["sea_routes"]["land_crossing_segments"], 0)
        self.assertEqual(debug["qa"]["sea_route_land_crossing_segments"], 0)
        self.assertLess(debug["qa"]["max_sea_route_endpoint_distance"], 0.18)
        self.assertGreater(debug["qa"]["max_sea_route_segment_fraction"], 0.0)
        self.assertIn("shape", debug["sea_routes"]["routes"][0])
        self.assertGreater(debug["sea_routes"]["shape"]["max_segment_fraction"], 0.0)
        self.assertEqual(debug["sea_routes"]["routes"][0]["endpoint_distances"], [0.06, 0.06])
        self.assertEqual(debug["sea_routes"]["routes"][0]["land_crossing_segments"], 0)

    def test_overlay_debug_reports_road_shape_after_render_chamfer(self) -> None:
        overlays = WorldMapOverlays(
            settlements=[],
            polities_by_region_id={},
            roads=[
                RoadMapEdge(
                    from_settlement_id="a",
                    to_settlement_id="b",
                    points=[(0.10, 0.10), (0.40, 0.10), (0.40, 0.40), (0.70, 0.40)],
                    usage=4.0,
                    actual_usage=4.0,
                    implied_usage=0.0,
                    opacity=0.72,
                )
            ],
        )

        debug = build_world_map_overlay_debug_data(_geometry(), overlays)

        route = debug["roads"]["routes"][0]
        self.assertEqual(route["shape"]["right_angle_like_turns"], 2)
        self.assertEqual(route["rendered_shape"]["right_angle_like_turns"], 0)
        self.assertEqual(debug["roads"]["shape"]["right_angle_like_turns"], 2)
        self.assertEqual(debug["roads"]["shape"]["rendered_right_angle_like_turns"], 0)
        self.assertGreaterEqual(debug["qa"]["road_rendered_sharp_turn_reduction"], 2)

    def test_configured_sea_neighbor_gets_implied_route_without_moves(self) -> None:
        geometry = _two_port_sea_geometry()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.20, 0.50), "b": (0.80, 0.50)},
                [],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.20, 0.50), "b": (0.80, 0.50)},
            )

            sea_routes = build_settlement_sea_route_overlays(geometry=geometry, save_db_path=save)

        sea_route = _edge(sea_routes, "a", "b")
        self.assertIsNotNone(sea_route)
        self.assertEqual(sea_route.actual_usage, 0)
        self.assertGreater(sea_route.implied_usage, 0)

    def test_sea_route_bends_around_land_blocker(self) -> None:
        geometry = _two_port_sea_geometry_with_island_blocker()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.20, 0.50), "b": (0.80, 0.50)},
                [(10, "a", "b", 4)],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.20, 0.50), "b": (0.80, 0.50)},
            )

            sea_routes = build_settlement_sea_route_overlays(geometry=geometry, save_db_path=save)

        sea_route = _edge(sea_routes, "a", "b")
        self.assertIsNotNone(sea_route)
        self.assertFalse(_segment_samples_rect(sea_route.points, 0.44, 0.42, 0.56, 0.58))
        self.assertGreater(max(abs(y - 0.50) for _x, y in sea_route.points), 0.07)

    def test_ocean_route_replaces_excessively_long_inland_river_road(self) -> None:
        geometry = _coastal_land_and_sea_choice_geometry()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.20, 0.50), "b": (0.80, 0.50)},
                [(10, "a", "b", 5)],
                region_by_settlement={"a": "r1", "b": "r2"},
                world_points={"a": (0.20, 0.50), "b": (0.80, 0.50)},
            )

            roads = build_settlement_road_overlays(geometry=geometry, save_db_path=save)
            sea_routes = build_settlement_sea_route_overlays(geometry=geometry, save_db_path=save)

        self.assertIsNone(_edge(roads, "a", "b"))
        sea_route = _edge(sea_routes, "a", "b")
        self.assertIsNotNone(sea_route)
        self.assertEqual(sea_route.actual_usage, 5)
        self.assertLess(
            sum(math.dist(a, b) for a, b in zip(sea_route.points, sea_route.points[1:])),
            0.9,
        )

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

    def test_adjacent_micro_road_keeps_visible_bend_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.18, 0.50), "b": (0.78, 0.50)},
                [(10, "a", "b", 4)],
            )
            geometry = _geometry(
                micro_cells=[
                    _micro_cell("left", 0.0, 0.0, 0.5, 1.0),
                    _micro_cell("right", 0.5, 0.0, 1.0, 1.0),
                ]
            )

            roads = build_settlement_road_overlays(geometry=geometry, save_db_path=save)

        road = _edge(roads, "a", "b")
        self.assertIsNotNone(road)
        self.assertGreaterEqual(len(road.points), 3)
        self.assertGreater(max(abs(y - 0.50) for _x, y in road.points), 0.01)
        self.assertNotEqual(road.points, [(0.18, 0.50), (0.78, 0.50)])

    def test_long_direct_road_gets_broad_land_safe_waypoints(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            save = _make_save(
                Path(tmp),
                {"a": (0.12, 0.50), "b": (0.88, 0.50)},
                [(10, "a", "b", 4)],
            )
            geometry = _geometry(
                micro_cells=[
                    _micro_cell("wide", 0.0, 0.0, 1.0, 1.0),
                ]
            )

            roads = build_settlement_road_overlays(geometry=geometry, save_db_path=save)

        road = _edge(roads, "a", "b")
        self.assertIsNotNone(road)
        self.assertGreaterEqual(len(road.points), 4)
        self.assertLess(_max_segment_fraction(road.points), 0.42)
        self.assertGreater(max(abs(y - 0.50) for _x, y in road.points), 0.04)

    def test_road_point_cleanup_prunes_tiny_hairpin_before_svg_smoothing(self) -> None:
        points = [(0.1, 0.1), (0.125, 0.102), (0.1008, 0.1006), (0.18, 0.12)]

        cleaned = _clean_road_points(points)

        self.assertEqual(cleaned, [(0.1, 0.1), (0.18, 0.12)])

    def test_road_point_cleanup_prunes_tiny_preserved_spur(self) -> None:
        start = (0.160647, 0.41096)
        spur = (0.156489, 0.411953)
        end = (0.168, 0.418)
        points = [start, spur, start, end]

        cleaned = _clean_road_points(points, preserve_points=[spur])

        self.assertEqual(cleaned, [start, end])

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

    def test_road_render_corner_softening_keeps_polyline_but_reduces_grid_corners(self) -> None:
        points = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (40.0, 20.0)]

        softened = _soften_polyline_corners(points, max_cut=5.0)

        self.assertNotIn((20.0, 0.0), softened)
        self.assertNotIn((20.0, 20.0), softened)
        self.assertIn((15.0, 0.0), softened)
        self.assertIn((20.0, 5.0), softened)
        self.assertEqual(softened[0], points[0])
        self.assertEqual(softened[-1], points[-1])

    def test_road_render_corner_softening_prunes_tiny_endpoint_hook(self) -> None:
        points = [
            (195.359328, 317.132488),
            (194.260656, 317.03712),
            (207.881991, 328.920619),
            (220.0, 340.0),
        ]

        softened = _soften_polyline_corners(points, max_cut=5.6, min_turn_degrees=10.0)

        self.assertEqual(softened[0], points[0])
        self.assertNotIn(points[1], softened)
        self.assertEqual(softened[-1], points[-1])

    def test_rendered_road_path_chamfers_raw_grid_corner(self) -> None:
        overlays = WorldMapOverlays(
            settlements=[],
            polities_by_region_id={},
            roads=[
                RoadMapEdge(
                    from_settlement_id="a",
                    to_settlement_id="b",
                    points=[(0.10, 0.10), (0.40, 0.10), (0.40, 0.40), (0.70, 0.40)],
                    usage=4.0,
                    actual_usage=4.0,
                    implied_usage=0.0,
                    opacity=0.72,
                )
            ]
        )

        svg = render_world_map_svg(_geometry(), overlays=overlays)

        road_d = _svg_path_d(svg, "road road-line")
        self.assertNotIn("L 487.2 108.8", road_d)
        self.assertNotIn("L 487.2 327.2", road_d)
        self.assertIn("L 481.6 108.8", road_d)
        self.assertIn("L 487.2 114.4", road_d)
        self.assertIn("<title>Road route a to b</title>", svg)

    def test_river_title_uses_nearby_settlement_name(self) -> None:
        geometry = WorldMapGeometry(
            world="test",
            version="unit",
            width=1.0,
            height=1.0,
            cells=[_region_cell()],
            micro_cells=[],
            features=[],
            edges=[],
            rivers=[
                RiverPath(
                    river_id="r1:river:1",
                    from_region_id="r1",
                    to_region_id="r1",
                    points=[(0.20, 0.48), (0.52, 0.50), (0.80, 0.52)],
                    segments=[
                        RiverSegment(
                            points=[(0.20, 0.48), (0.52, 0.50), (0.80, 0.52)],
                            micro_ids=[],
                            region_ids=["r1"],
                        )
                    ],
                    flow=0.55,
                    river_class="minor",
                )
            ],
        )
        overlays = WorldMapOverlays(
            settlements=[
                SettlementMapOverlay(
                    settlement_id="r1:s1",
                    region_id="r1",
                    display_name="Fordham",
                    x=0.52,
                    y=0.50,
                    population=120,
                    status="active",
                )
            ],
            polities_by_region_id={},
        )

        svg = render_world_map_svg(geometry, overlays=overlays)

        self.assertIn("<title>Fordham River</title>", svg)
        self.assertIn("<title>Fordham River mouth</title>", svg)

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
        self.assertIn(".road-underlay{stroke:#fffdf3", svg)
        self.assertIn(".road-line{stroke:#b21f3a}", svg)
        self.assertIn('data-map-layer="road"', svg)
        self.assertIn("<title>Road route A to C</title>", svg)
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
