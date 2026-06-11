"""Settlement-to-settlement road overlay generation for world maps."""

from __future__ import annotations

import heapq
import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from library.world_map_geometry import (
    MicroRegionCell,
    Point,
    WorldMapGeometry,
    _dedupe_path_points,
    _micro_adjacency,
    _nearest_point_on_segment,
    _point_in_polygon,
    _point_segment_distance,
    _clamp,
    project_local_point_to_region_footprint,
    project_world_point_to_region_footprint,
)


@dataclass(frozen=True)
class RoadMapNode:
    settlement_id: str
    region_id: str
    display_name: str
    x: float
    y: float
    population: int
    market_pull: float
    prosperity_pool: float
    founding_reason: str
    mother_settlement_id: str
    trade_network_id: str
    autonomy_level: str


@dataclass(frozen=True)
class RoadMapEdge:
    from_settlement_id: str
    to_settlement_id: str
    points: list[Point]
    usage: float
    actual_usage: float
    implied_usage: float
    opacity: float


@dataclass(frozen=True)
class SeaRouteMapEdge:
    from_settlement_id: str
    to_settlement_id: str
    points: list[Point]
    route_regions: tuple[str, ...]
    usage: float
    actual_usage: float
    implied_usage: float
    opacity: float


@dataclass(frozen=True)
class _RoadPath:
    points: list[Point]
    cost: float
    length: float


@dataclass(frozen=True)
class _SeaPath:
    points: list[Point]
    route_regions: tuple[str, ...]
    cost: float
    length: float


@dataclass
class _RoadDemand:
    actual_usage: float = 0.0
    implied_usage: float = 0.0

    @property
    def usage(self) -> float:
        return max(0.0, float(self.actual_usage) + float(self.implied_usage))


@dataclass
class _RoadSegment:
    from_settlement_id: str
    to_settlement_id: str
    points: list[Point]
    cost: float
    length: float
    actual_usage: float = 0.0
    implied_usage: float = 0.0

    @property
    def usage(self) -> float:
        return max(0.0, float(self.actual_usage) + float(self.implied_usage))


_TERRAIN_MULTIPLIERS = {
    "riverland": 0.92,
    "plains": 1.00,
    "coast": 1.05,
    "forest": 1.28,
    "drylands": 1.24,
    "highlands": 1.75,
}
_MICRO_GRAPH_CACHE: dict[
    int,
    tuple[int, dict[str, set[str]], dict[tuple[str, str], tuple[Point, Point]]],
] = {}
_WATER_GRID_SIZE = 48
_WATER_GRAPH_CACHE: dict[
    int,
    tuple[int, int, list[Point], dict[tuple[int, int], int], dict[int, list[tuple[int, float]]]],
] = {}
_LAND_BOUNDS_CACHE: dict[int, tuple[int, list[tuple[float, float, float, float, MicroRegionCell]]]] = {}
_SEA_REGION_ROUTE_CACHE: dict[
    tuple[int, int, str, str],
    tuple[tuple[str, ...], list[Point], float] | None,
] = {}
_COAST_DISTANCE_CACHE: dict[int, tuple[int, dict[Point, float]]] = {}


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _relations(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }


def _table_columns(conn: sqlite3.Connection, relation: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(relation)})")}
    except sqlite3.Error:
        return set()


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _site_anchor_point(local_geography_json: object, site_slot: object) -> tuple[Point, bool] | None:
    if not local_geography_json:
        return None
    try:
        data = json.loads(str(local_geography_json))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    sites = data.get("settlements")
    if not isinstance(sites, list):
        return None
    slot = _coerce_int(site_slot, 1) - 1
    for site in sites:
        if not isinstance(site, dict):
            continue
        if _coerce_int(site.get("settlement_slot"), 0) != slot:
            continue
        wx = site.get("world_x")
        wy = site.get("world_y")
        if wx is not None and wy is not None:
            return ((_coerce_float(wx, 0.5), _coerce_float(wy, 0.5)), True)
        return ((_coerce_float(site.get("x"), 0.5), _coerce_float(site.get("y"), 0.5)), False)
    return None


def _feature_world_point(
    geometry: WorldMapGeometry,
    region_id: str,
    feature: dict[str, object],
) -> Point | None:
    if feature.get("source_world_x") is not None and feature.get("source_world_y") is not None:
        return project_world_point_to_region_footprint(
            geometry,
            region_id,
            (
                _coerce_float(feature.get("source_world_x"), 0.5),
                _coerce_float(feature.get("source_world_y"), 0.5),
            ),
        )
    if feature.get("world_x") is not None and feature.get("world_y") is not None:
        return project_world_point_to_region_footprint(
            geometry,
            region_id,
            (
                _coerce_float(feature.get("world_x"), 0.5),
                _coerce_float(feature.get("world_y"), 0.5),
            ),
        )
    if feature.get("x") is None or feature.get("y") is None:
        return None
    return project_local_point_to_region_footprint(
        geometry,
        region_id,
        (
            max(0.04, min(0.96, _coerce_float(feature.get("x"), 0.5))),
            max(0.04, min(0.96, _coerce_float(feature.get("y"), 0.5))),
        ),
    )


def _local_ford_points(
    geometry: WorldMapGeometry,
    rows: Iterable[sqlite3.Row],
) -> list[Point]:
    out: list[Point] = []
    for row in rows:
        if "local_geography_json" not in row.keys():
            continue
        rid = str(row["region_id"] or "").strip() if "region_id" in row.keys() else ""
        if not rid:
            continue
        try:
            data = json.loads(str(row["local_geography_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        features = data.get("features")
        if not isinstance(features, list):
            continue
        for feature in features:
            if not isinstance(feature, dict):
                continue
            kind = str(feature.get("kind") or "").strip().lower()
            name = str(feature.get("display_name") or "").strip().lower()
            if kind not in {"ford", "fords", "shallow", "crossing"} and "ford" not in name:
                continue
            try:
                point = _feature_world_point(geometry, rid, feature)
            except (TypeError, ValueError, LookupError):
                point = None
            if point is not None:
                out.append((round(point[0], 6), round(point[1], 6)))
    for feature in geometry.features:
        kind = str(feature.kind or "").strip().lower()
        label = str(feature.label or "").strip().lower()
        if kind in {"ford", "fords", "shallow", "crossing"} or "ford" in label:
            out.append((round(float(feature.x), 6), round(float(feature.y), 6)))
    seen: set[Point] = set()
    unique: list[Point] = []
    for point in out:
        if point in seen:
            continue
        seen.add(point)
        unique.append(point)
    return unique


def _settlement_source(relations: set[str]) -> str | None:
    if "simulation_settlements_readable" in relations:
        return "simulation_settlements_readable"
    if "simulation_settlements" in relations:
        return "simulation_settlements"
    return None


def _load_settlement_rows(
    conn: sqlite3.Connection,
    geometry: WorldMapGeometry,
    *,
    max_nodes: int,
) -> list[sqlite3.Row]:
    relations = _relations(conn)
    source = _settlement_source(relations)
    if source is None:
        return []
    cells = geometry.cell_by_region_id()
    if not cells:
        return []
    columns = _table_columns(conn, source)
    if not {"settlement_id", "region_id"}.issubset(columns):
        return []
    status_clause = "status = 'active'" if "status" in columns else "1 = 1"
    order = "population_cap DESC, settlement_id" if "population_cap" in columns else "settlement_id"
    rows = conn.execute(
        f"""
        SELECT *
        FROM {_quote_identifier(source)}
        WHERE region_id IN ({", ".join("?" for _ in cells)})
          AND {status_clause}
        ORDER BY {order}
        LIMIT ?
        """,
        (*sorted(cells), max(1, int(max_nodes))),
    ).fetchall()
    return rows


def _person_residence_sql(conn: sqlite3.Connection) -> str | None:
    columns = _table_columns(conn, "simulation_people")
    if not columns:
        return None
    if "current_settlement_id" in columns or "birthplace_settlement_id" in columns:
        current = "nullif(current_settlement_id, '')" if "current_settlement_id" in columns else "null"
        birth = "nullif(birthplace_settlement_id, '')" if "birthplace_settlement_id" in columns else "null"
        return f"coalesce({current}, {birth})"
    if (
        ("current_settlement_key" in columns or "birthplace_settlement_key" in columns)
        and "simulation_settlement_lookup" in _relations(conn)
    ):
        current = (
            "(select settlement_id from simulation_settlement_lookup "
            "where settlement_key = simulation_people.current_settlement_key)"
            if "current_settlement_key" in columns
            else "null"
        )
        birth = (
            "(select settlement_id from simulation_settlement_lookup "
            "where settlement_key = simulation_people.birthplace_settlement_key)"
            if "birthplace_settlement_key" in columns
            else "null"
        )
        return f"coalesce(nullif({current}, ''), nullif({birth}, ''))"
    if "person_json" in columns:
        return (
            "coalesce("
            "nullif(json_extract(person_json, '$.current_settlement_id'), ''), "
            "nullif(json_extract(person_json, '$.birthplace_settlement_id'), '')"
            ")"
        )
    return None


def _alive_where(conn: sqlite3.Connection, table: str = "simulation_people") -> str:
    columns = _table_columns(conn, table)
    if "is_alive" in columns:
        return "is_alive = 1"
    if "deathyear" in columns:
        return "deathyear IS NULL"
    return "1 = 1"


def _latest_cohort_year(conn: sqlite3.Connection) -> int | None:
    relations = _relations(conn)
    for source in ("simulation_cohorts_readable", "simulation_cohorts"):
        if source not in relations:
            continue
        try:
            row = conn.execute(f"SELECT max(sim_year) AS y FROM {_quote_identifier(source)}").fetchone()
        except sqlite3.Error:
            continue
        if row and row["y"] is not None:
            return int(row["y"])
    return None


def _live_population_by_settlement(
    conn: sqlite3.Connection,
    settlement_ids: Iterable[str],
) -> tuple[dict[str, int], bool]:
    ids = [sid for sid in settlement_ids if sid]
    if not ids:
        return {}, False
    counts = {sid: 0 for sid in ids}
    found_source = False
    relations = _relations(conn)
    placeholders = ", ".join("?" for _ in ids)
    if "simulation_people" in relations:
        residence = _person_residence_sql(conn)
        if residence:
            found_source = True
            try:
                rows = conn.execute(
                    f"""
                    SELECT {residence} AS settlement_id, count(1) AS n
                    FROM simulation_people
                    WHERE {_alive_where(conn, "simulation_people")}
                      AND {residence} IN ({placeholders})
                    GROUP BY settlement_id
                    """,
                    tuple(ids),
                ).fetchall()
                for row in rows:
                    sid = str(row["settlement_id"] or "").strip()
                    if sid:
                        counts[sid] = counts.get(sid, 0) + int(row["n"] or 0)
            except sqlite3.Error:
                pass
    if "simulation_people_light_readable" in relations:
        found_source = True
        try:
            rows = conn.execute(
                f"""
                SELECT coalesce(current_settlement_id, birthplace_settlement_id) AS settlement_id,
                       count(1) AS n
                FROM simulation_people_light_readable
                WHERE is_alive = 1
                  AND coalesce(current_settlement_id, birthplace_settlement_id) IN ({placeholders})
                GROUP BY settlement_id
                """,
                tuple(ids),
            ).fetchall()
            for row in rows:
                sid = str(row["settlement_id"] or "").strip()
                if sid:
                    counts[sid] = counts.get(sid, 0) + int(row["n"] or 0)
        except sqlite3.Error:
            pass
    cohort_year = _latest_cohort_year(conn)
    if cohort_year is not None:
        found_source = True
        cohort_queries: list[tuple[str, tuple[object, ...]]] = []
        if "simulation_cohorts_readable" in relations:
            cohort_queries.append(
                (
                    f"""
                    SELECT settlement_id, sum(population_count) AS n
                    FROM simulation_cohorts_readable
                    WHERE sim_year = ?
                      AND settlement_id IN ({placeholders})
                    GROUP BY settlement_id
                    """,
                    (cohort_year, *ids),
                )
            )
        elif {"simulation_cohorts", "simulation_settlement_lookup"}.issubset(relations):
            cohort_queries.append(
                (
                    f"""
                    SELECT sl.settlement_id, sum(c.population_count) AS n
                    FROM simulation_cohorts c
                    JOIN simulation_settlement_lookup sl ON sl.settlement_key = c.settlement_key
                    WHERE c.sim_year = ?
                      AND sl.settlement_id IN ({placeholders})
                    GROUP BY sl.settlement_id
                    """,
                    (cohort_year, *ids),
                )
            )
        for query, params in cohort_queries:
            try:
                rows = conn.execute(query, params).fetchall()
                for row in rows:
                    sid = str(row["settlement_id"] or "").strip()
                    if sid:
                        counts[sid] = counts.get(sid, 0) + int(row["n"] or 0)
            except sqlite3.Error:
                pass
    return counts, found_source


def _load_nodes(
    conn: sqlite3.Connection,
    geometry: WorldMapGeometry,
    *,
    max_nodes: int,
) -> tuple[dict[str, RoadMapNode], list[sqlite3.Row], list[Point]]:
    rows = _load_settlement_rows(conn, geometry, max_nodes=max_nodes)
    settlement_ids = [str(row["settlement_id"] or "").strip() for row in rows]
    live_counts, live_source_found = _live_population_by_settlement(conn, settlement_ids)
    out: dict[str, RoadMapNode] = {}
    for row in rows:
        sid = str(row["settlement_id"] or "").strip()
        rid = str(row["region_id"] or "").strip()
        if not sid or not rid:
            continue
        raw_anchor = _site_anchor_point(
            row["local_geography_json"] if "local_geography_json" in row.keys() else None,
            row["site_slot"] if "site_slot" in row.keys() else 1,
        )
        if raw_anchor is None:
            raw_point = (0.5, 0.5)
            is_world_point = False
        else:
            raw_point, is_world_point = raw_anchor
        try:
            if is_world_point:
                x, y = project_world_point_to_region_footprint(geometry, rid, raw_point)
            else:
                x, y = project_local_point_to_region_footprint(geometry, rid, raw_point)
        except (LookupError, TypeError, ValueError):
            cell = geometry.cell_by_region_id().get(rid)
            if cell is None:
                continue
            x, y = cell.center_x, cell.center_y
        population = (
            int(live_counts.get(sid, 0))
            if live_source_found
            else max(0, _coerce_int(row["population_cap"] if "population_cap" in row.keys() else 0))
        )
        out[sid] = RoadMapNode(
            settlement_id=sid,
            region_id=rid,
            display_name=str(row["display_name"] if "display_name" in row.keys() else sid or sid),
            x=float(x),
            y=float(y),
            population=population,
            market_pull=_coerce_float(row["market_pull"] if "market_pull" in row.keys() else 0.0),
            prosperity_pool=_coerce_float(row["prosperity_pool"] if "prosperity_pool" in row.keys() else 0.0),
            founding_reason=str(row["founding_reason"] if "founding_reason" in row.keys() else "").strip(),
            mother_settlement_id=str(row["mother_settlement_id"] if "mother_settlement_id" in row.keys() else "").strip(),
            trade_network_id=str(row["trade_network_id"] if "trade_network_id" in row.keys() else "").strip(),
            autonomy_level=str(row["autonomy_level"] if "autonomy_level" in row.keys() else "").strip(),
        )
    return out, rows, _local_ford_points(geometry, rows)


def _current_year(conn: sqlite3.Connection, world: str) -> int | None:
    relations = _relations(conn)
    if "world_state" in relations:
        columns = _table_columns(conn, "world_state")
        try:
            if "world" in columns:
                row = conn.execute(
                    "SELECT current_year FROM world_state WHERE world = ?",
                    (world,),
                ).fetchone()
            else:
                row = conn.execute("SELECT current_year FROM world_state WHERE id = 1").fetchone()
        except sqlite3.Error:
            row = None
        if row and row["current_year"] is not None:
            return int(row["current_year"])
    if "simulation_event_moves_readable" in relations:
        row = conn.execute("SELECT max(sim_year) AS y FROM simulation_event_moves_readable").fetchone()
        if row and row["y"] is not None:
            return int(row["y"])
    return None


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((str(a), str(b))))  # type: ignore[return-value]


def _add_implied(
    demands: dict[tuple[str, str], _RoadDemand],
    a: str,
    b: str,
    amount: float,
) -> None:
    if not a or not b or a == b or amount <= 0.0:
        return
    demand = demands.setdefault(_pair_key(a, b), _RoadDemand())
    demand.implied_usage += float(amount)


def _load_actual_demands(
    conn: sqlite3.Connection,
    nodes: dict[str, RoadMapNode],
    *,
    latest_year: int | None,
) -> dict[tuple[str, str], _RoadDemand]:
    demands: dict[tuple[str, str], _RoadDemand] = {}
    if latest_year is None or "simulation_event_moves_readable" not in _relations(conn):
        return demands
    try:
        rows = conn.execute(
            """
            SELECT from_settlement_id, to_settlement_id, count(1) AS n
            FROM simulation_event_moves_readable
            WHERE sim_year = ?
              AND from_settlement_id IS NOT NULL
              AND to_settlement_id IS NOT NULL
              AND from_settlement_id <> ''
              AND to_settlement_id <> ''
              AND from_settlement_id <> to_settlement_id
            GROUP BY from_settlement_id, to_settlement_id
            """,
            (int(latest_year),),
        ).fetchall()
    except sqlite3.Error:
        return demands
    for row in rows:
        a = str(row["from_settlement_id"] or "").strip()
        b = str(row["to_settlement_id"] or "").strip()
        if a not in nodes or b not in nodes:
            continue
        demand = demands.setdefault(_pair_key(a, b), _RoadDemand())
        demand.actual_usage += max(0.0, float(row["n"] or 0))
    return demands


def _region_land_neighbors(geometry: WorldMapGeometry) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for edge in geometry.edges:
        route_type = str(edge.route_type or "").strip().lower()
        if route_type == "sea" or str(edge.edge_class or "").strip().lower() == "sea_route":
            continue
        a = str(edge.from_region_id or "").strip()
        b = str(edge.to_region_id or "").strip()
        if not a or not b:
            continue
        out.setdefault(a, set()).add(b)
        out.setdefault(b, set()).add(a)
    return out


def _configured_region_route_points(
    geometry: WorldMapGeometry,
    from_region_id: str,
    to_region_id: str,
) -> list[Point] | None:
    """Return the configured land-route polyline between two regions."""
    start = str(from_region_id or "").strip()
    end = str(to_region_id or "").strip()
    if not start or not end or start == end:
        return None

    cells = geometry.cell_by_region_id()
    if start in cells and end in cells and cells[start].continent_id != cells[end].continent_id:
        return None

    edge_by_pair: dict[tuple[str, str], object] = {}
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for edge in geometry.edges:
        route_type = str(edge.route_type or "").strip().lower()
        if route_type == "sea" or str(edge.edge_class or "").strip().lower() == "sea_route":
            continue
        a = str(edge.from_region_id or "").strip()
        b = str(edge.to_region_id or "").strip()
        if not a or not b or a == b:
            continue
        if a in cells and b in cells and cells[a].continent_id != cells[b].continent_id:
            continue
        key = _pair_key(a, b)
        points = list(edge.points or [])
        if len(points) < 2:
            ca = cells.get(a)
            cb = cells.get(b)
            if ca is None or cb is None:
                continue
            points = [(ca.center_x, ca.center_y), (cb.center_x, cb.center_y)]
        distance = max(0.0001, _path_length(points))
        friction = max(0.1, _coerce_float(edge.friction, 1.0))
        weight = distance * friction
        edge_by_pair[key] = edge
        adjacency.setdefault(a, []).append((b, weight))
        adjacency.setdefault(b, []).append((a, weight))

    if start not in adjacency or end not in adjacency:
        return None

    queue: list[tuple[float, str]] = [(0.0, start)]
    distances = {start: 0.0}
    previous: dict[str, str] = {}
    while queue:
        cost, region_id = heapq.heappop(queue)
        if cost > distances.get(region_id, float("inf")):
            continue
        if region_id == end:
            break
        for other, step_cost in adjacency.get(region_id, []):
            nd = cost + step_cost
            if nd < distances.get(other, float("inf")):
                distances[other] = nd
                previous[other] = region_id
                heapq.heappush(queue, (nd, other))
    if end not in distances:
        return None

    region_path = [end]
    while region_path[-1] != start:
        prior = previous.get(region_path[-1])
        if prior is None:
            return None
        region_path.append(prior)
    region_path.reverse()

    points: list[Point] = []
    for a, b in zip(region_path, region_path[1:]):
        edge = edge_by_pair.get(_pair_key(a, b))
        if edge is None:
            return None
        segment = list(getattr(edge, "points", None) or [])
        if len(segment) < 2:
            ca = cells.get(a)
            cb = cells.get(b)
            if ca is None or cb is None:
                return None
            segment = [(ca.center_x, ca.center_y), (cb.center_x, cb.center_y)]
        if str(getattr(edge, "from_region_id", "") or "").strip() != a:
            segment.reverse()
        if not points:
            points.extend(segment)
        else:
            if math.hypot(points[-1][0] - segment[0][0], points[-1][1] - segment[0][1]) > 1e-7:
                points.append(segment[0])
            points.extend(segment[1:])
    return _dedupe_path_points(points) if len(points) >= 2 else None


def _is_sea_edge(edge: object) -> bool:
    route_type = str(getattr(edge, "route_type", "") or "").strip().lower()
    edge_class = str(getattr(edge, "edge_class", "") or "").strip().lower()
    return route_type == "sea" or edge_class == "sea_route"


def _land_cell_bounds(
    geometry: WorldMapGeometry,
) -> list[tuple[float, float, float, float, MicroRegionCell]]:
    key = id(geometry.micro_cells)
    cached = _LAND_BOUNDS_CACHE.get(key)
    if cached is not None and cached[0] == len(geometry.micro_cells):
        return cached[1]
    bounds: list[tuple[float, float, float, float, MicroRegionCell]] = []
    for cell in geometry.micro_cells:
        xs = [x for x, _y in cell.polygon]
        ys = [y for _x, y in cell.polygon]
        if not xs or not ys:
            continue
        bounds.append((min(xs), min(ys), max(xs), max(ys), cell))
    _LAND_BOUNDS_CACHE[key] = (len(geometry.micro_cells), bounds)
    return bounds


def _land_cell_containing_point(
    geometry: WorldMapGeometry,
    point: Point,
) -> MicroRegionCell | None:
    x, y = point
    for x0, y0, x1, y1, cell in _land_cell_bounds(geometry):
        if x < x0 or x > x1 or y < y0 or y > y1:
            continue
        if _point_in_polygon(point, cell.polygon):
            return cell
    return None


def _point_too_close_to_land(
    geometry: WorldMapGeometry,
    point: Point,
    *,
    clearance: float,
) -> bool:
    x, y = point
    for x0, y0, x1, y1, cell in _land_cell_bounds(geometry):
        if x < x0 - clearance or x > x1 + clearance or y < y0 - clearance or y > y1 + clearance:
            continue
        if _point_in_polygon(point, cell.polygon):
            return True
        pts = cell.polygon
        if clearance > 0.0 and len(pts) >= 2:
            if min(_point_segment_distance(point, a, b) for a, b in zip(pts, pts[1:] + pts[:1])) <= clearance:
                return True
    return False


def _coast_distance(
    geometry: WorldMapGeometry,
    point: Point,
    *,
    max_distance: float = 0.24,
) -> float:
    key = id(geometry.micro_cells)
    cached = _COAST_DISTANCE_CACHE.get(key)
    if cached is None or cached[0] != len(geometry.micro_cells):
        cached = (len(geometry.micro_cells), {})
        _COAST_DISTANCE_CACHE[key] = cached
    cache = cached[1]
    p = (round(point[0], 5), round(point[1], 5))
    if p in cache:
        return cache[p]
    if _land_cell_containing_point(geometry, p) is not None:
        cache[p] = 0.0
        return 0.0
    best = max_distance
    x, y = p
    for x0, y0, x1, y1, cell in _land_cell_bounds(geometry):
        if x < x0 - best or x > x1 + best or y < y0 - best or y > y1 + best:
            continue
        pts = cell.polygon
        if len(pts) < 2:
            continue
        distance = min(_point_segment_distance(p, a, b) for a, b in zip(pts, pts[1:] + pts[:1]))
        if distance < best:
            best = distance
    cache[p] = best
    return best


def _water_step_weight(
    geometry: WorldMapGeometry,
    a: Point,
    b: Point,
) -> float:
    distance = math.hypot(a[0] - b[0], a[1] - b[1])
    coast = min(_coast_distance(geometry, a), _coast_distance(geometry, b))
    if coast < 0.010:
        multiplier = 2.15
    elif coast <= 0.075:
        multiplier = 0.78 + abs(coast - 0.035) * 1.55
    elif coast <= 0.145:
        multiplier = 0.98 + (coast - 0.075) * 2.05
    else:
        multiplier = 1.14 + min(0.68, (coast - 0.145) * 3.0)
    return max(0.0001, distance * multiplier)


def _water_segment_crosses_land(
    geometry: WorldMapGeometry,
    start: Point,
    end: Point,
    *,
    samples: int = 12,
) -> bool:
    if not geometry.micro_cells:
        return False
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    steps = max(samples, int(distance / 0.010))
    for idx in range(0, steps + 1):
        t = idx / max(1, steps)
        point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        if _point_too_close_to_land(geometry, point, clearance=0.0045):
            return True
    return False


def _coastal_water_point(
    geometry: WorldMapGeometry,
    point: Point,
    toward: Point | None = None,
) -> Point:
    p = (_coerce_float(point[0], 0.5), _coerce_float(point[1], 0.5))
    cell = _land_cell_containing_point(geometry, p)
    if cell is None and not _point_too_close_to_land(geometry, p, clearance=0.0055):
        return (_clamp(p[0], 0.006, 0.994), _clamp(p[1], 0.006, 0.994))
    directions: list[Point] = []
    if toward is not None:
        dx = toward[0] - p[0]
        dy = toward[1] - p[1]
        length = math.hypot(dx, dy)
        if length > 1e-7:
            directions.append((dx / length, dy / length))
    if cell is not None:
        dx = p[0] - cell.center_x
        dy = p[1] - cell.center_y
        length = math.hypot(dx, dy)
        if length > 1e-7:
            directions.append((dx / length, dy / length))
        nearest = _nearest_cell_edge(cell, p)
        if nearest is not None:
            projected = nearest[1]
            dx = projected[0] - p[0]
            dy = projected[1] - p[1]
            length = math.hypot(dx, dy)
            if length > 1e-7:
                directions.append((dx / length, dy / length))
    for idx in range(32):
        angle = math.tau * idx / 32.0
        directions.append((math.cos(angle), math.sin(angle)))

    unique_dirs: list[Point] = []
    seen: set[tuple[int, int]] = set()
    for dx, dy in directions:
        key = (round(dx * 1000), round(dy * 1000))
        if key in seen:
            continue
        seen.add(key)
        unique_dirs.append((dx, dy))

    best: tuple[float, Point] | None = None
    for radius in (0.006, 0.010, 0.016, 0.026, 0.040, 0.060, 0.085, 0.115, 0.150, 0.195, 0.250):
        for dx, dy in unique_dirs:
            candidate = (
                _clamp(p[0] + dx * radius, 0.006, 0.994),
                _clamp(p[1] + dy * radius, 0.006, 0.994),
            )
            if _point_too_close_to_land(geometry, candidate, clearance=0.0055):
                continue
            distance = math.hypot(candidate[0] - p[0], candidate[1] - p[1])
            item = (distance, candidate)
            if best is None or item < best:
                best = item
        if best is not None:
            return best[1]
    return (_clamp(p[0], 0.006, 0.994), _clamp(p[1], 0.006, 0.994))


def _water_nav_graph(
    geometry: WorldMapGeometry,
) -> tuple[list[Point], dict[tuple[int, int], int], dict[int, list[tuple[int, float]]]]:
    key = id(geometry.micro_cells)
    cached = _WATER_GRAPH_CACHE.get(key)
    if cached is not None and cached[0] == len(geometry.micro_cells) and cached[1] == _WATER_GRID_SIZE:
        return cached[2], cached[3], cached[4]
    nodes: list[Point] = []
    by_grid: dict[tuple[int, int], int] = {}
    denom = max(1, _WATER_GRID_SIZE - 1)
    for iy in range(_WATER_GRID_SIZE):
        y = 0.012 + (0.976 * iy / denom)
        for ix in range(_WATER_GRID_SIZE):
            x = 0.012 + (0.976 * ix / denom)
            point = (round(x, 6), round(y, 6))
            if _land_cell_containing_point(geometry, point) is not None:
                continue
            by_grid[(ix, iy)] = len(nodes)
            nodes.append(point)
    adjacency: dict[int, list[tuple[int, float]]] = {idx: [] for idx in range(len(nodes))}
    offsets = (
        (-1, -1), (0, -1), (1, -1),
        (-1, 0), (1, 0),
        (-1, 1), (0, 1), (1, 1),
    )
    for (ix, iy), idx in by_grid.items():
        point = nodes[idx]
        for ox, oy in offsets:
            other_idx = by_grid.get((ix + ox, iy + oy))
            if other_idx is None:
                continue
            other = nodes[other_idx]
            if _water_segment_crosses_land(geometry, point, other, samples=3):
                continue
            adjacency[idx].append((other_idx, _water_step_weight(geometry, point, other)))
    _WATER_GRAPH_CACHE[key] = (len(geometry.micro_cells), _WATER_GRID_SIZE, nodes, by_grid, adjacency)
    return nodes, by_grid, adjacency


def _visible_water_links(
    geometry: WorldMapGeometry,
    point: Point,
    nodes: list[Point],
    *,
    limit: int = 16,
) -> list[tuple[int, float]]:
    nearest = sorted(
        (
            (math.hypot(point[0] - node[0], point[1] - node[1]), idx)
            for idx, node in enumerate(nodes)
        ),
        key=lambda item: (item[0], item[1]),
    )
    links: list[tuple[int, float]] = []
    for distance, idx in nearest[: max(80, limit * 10)]:
        if links and distance > 0.22:
            break
        if _water_segment_crosses_land(geometry, point, nodes[idx], samples=10):
            continue
        links.append((idx, _water_step_weight(geometry, point, nodes[idx])))
        if len(links) >= limit:
            break
    return links


def _simplify_water_points(
    geometry: WorldMapGeometry,
    points: list[Point],
) -> list[Point]:
    points = _dedupe_path_points(points)
    if len(points) <= 2:
        return points
    out: list[Point] = [points[0]]
    idx = 0
    while idx < len(points) - 1:
        next_idx = len(points) - 1
        while next_idx > idx + 1 and _water_segment_crosses_land(geometry, points[idx], points[next_idx], samples=14):
            next_idx -= 1
        out.append(points[next_idx])
        idx = next_idx
    return _dedupe_path_points(out)


def _water_nav_path_around_land(
    geometry: WorldMapGeometry,
    start: Point,
    end: Point,
    *,
    prefer_coast: bool = False,
) -> list[Point] | None:
    if not geometry.micro_cells:
        return [start, end]
    crosses_land = _water_segment_crosses_land(geometry, start, end, samples=16)
    if not crosses_land and not prefer_coast:
        return [start, end]
    nodes, _by_grid, adjacency = _water_nav_graph(geometry)
    if not nodes:
        return None
    start_links = _visible_water_links(geometry, start, nodes)
    end_links = _visible_water_links(geometry, end, nodes)
    if not start_links or not end_links:
        return None
    end_link_by_idx = {idx: distance for idx, distance in end_links}

    def point_for(node_id: int) -> Point:
        if node_id == -1:
            return start
        if node_id == -2:
            return end
        return nodes[node_id]

    queue: list[tuple[float, float, int]] = [(math.hypot(end[0] - start[0], end[1] - start[1]), 0.0, -1)]
    distances: dict[int, float] = {-1: 0.0}
    previous: dict[int, int] = {}
    while queue:
        _priority, cost, node_id = heapq.heappop(queue)
        if cost > distances.get(node_id, float("inf")):
            continue
        if node_id == -2:
            break
        if node_id == -1:
            neighbors = start_links
        else:
            neighbors = list(adjacency.get(node_id, ()))
            end_distance = end_link_by_idx.get(node_id)
            if end_distance is not None:
                neighbors.append((-2, end_distance))
        for other_id, step_cost in neighbors:
            nd = cost + step_cost
            if nd < distances.get(other_id, float("inf")):
                distances[other_id] = nd
                previous[other_id] = node_id
                other = point_for(other_id)
                heapq.heappush(
                    queue,
                    (nd + math.hypot(end[0] - other[0], end[1] - other[1]), nd, other_id),
                )
    if -2 not in distances:
        return None
    path_ids = [-2]
    while path_ids[-1] != -1:
        prior = previous.get(path_ids[-1])
        if prior is None:
            return None
        path_ids.append(prior)
    path_ids.reverse()
    path = [point_for(node_id) for node_id in path_ids]
    if crosses_land:
        return _simplify_water_points(geometry, path)
    direct = max(0.0001, math.hypot(end[0] - start[0], end[1] - start[1]))
    if _path_length(path) <= direct * 1.32:
        return _dedupe_path_points(path)
    return [start, end]


def _water_route_points(
    geometry: WorldMapGeometry,
    points: list[Point],
) -> list[Point]:
    if len(points) < 2:
        return points
    water_points: list[Point] = []
    for idx, point in enumerate(points):
        if idx + 1 < len(points):
            toward = points[idx + 1]
        elif idx > 0:
            toward = points[idx - 1]
        else:
            toward = None
        water_points.append(_coastal_water_point(geometry, point, toward))
    out: list[Point] = [water_points[0]]
    for start, end in zip(water_points, water_points[1:]):
        segment = _water_nav_path_around_land(geometry, start, end, prefer_coast=True) or [start, end]
        out.extend(segment[1:])
    return _dedupe_path_points(out)


def _configured_sea_region_route(
    geometry: WorldMapGeometry,
    from_region_id: str,
    to_region_id: str,
) -> tuple[tuple[str, ...], list[Point], float] | None:
    """Return a smooth configured sea-route polyline between two regions."""
    start = str(from_region_id or "").strip()
    end = str(to_region_id or "").strip()
    if not start or not end or start == end:
        return None
    cache_key = (id(geometry), len(geometry.edges), start, end)
    if cache_key in _SEA_REGION_ROUTE_CACHE:
        cached = _SEA_REGION_ROUTE_CACHE[cache_key]
        if cached is None:
            return None
        cached_regions, cached_points, cached_cost = cached
        return cached_regions, list(cached_points), cached_cost

    cells = geometry.cell_by_region_id()
    edge_by_pair: dict[tuple[str, str], object] = {}
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for edge in geometry.edges:
        if not _is_sea_edge(edge):
            continue
        a = str(edge.from_region_id or "").strip()
        b = str(edge.to_region_id or "").strip()
        if not a or not b or a == b:
            continue
        points = list(edge.points or [])
        if len(points) < 2:
            ca = cells.get(a)
            cb = cells.get(b)
            if ca is None or cb is None:
                continue
            points = [(ca.center_x, ca.center_y), (cb.center_x, cb.center_y)]
        distance = max(0.0001, _path_length(points))
        friction = max(0.1, _coerce_float(edge.friction, 1.0))
        weight = distance * friction
        key = _pair_key(a, b)
        edge_by_pair[key] = edge
        adjacency.setdefault(a, []).append((b, weight))
        adjacency.setdefault(b, []).append((a, weight))

    if start not in adjacency or end not in adjacency:
        _SEA_REGION_ROUTE_CACHE[cache_key] = None
        return None

    queue: list[tuple[float, str]] = [(0.0, start)]
    distances = {start: 0.0}
    previous: dict[str, str] = {}
    while queue:
        cost, region_id = heapq.heappop(queue)
        if cost > distances.get(region_id, float("inf")):
            continue
        if region_id == end:
            break
        for other, step_cost in adjacency.get(region_id, []):
            nd = cost + step_cost
            if nd < distances.get(other, float("inf")):
                distances[other] = nd
                previous[other] = region_id
                heapq.heappush(queue, (nd, other))
    if end not in distances:
        _SEA_REGION_ROUTE_CACHE[cache_key] = None
        return None

    region_path = [end]
    while region_path[-1] != start:
        prior = previous.get(region_path[-1])
        if prior is None:
            _SEA_REGION_ROUTE_CACHE[cache_key] = None
            return None
        region_path.append(prior)
    region_path.reverse()

    points: list[Point] = []
    for a, b in zip(region_path, region_path[1:]):
        edge = edge_by_pair.get(_pair_key(a, b))
        if edge is None:
            _SEA_REGION_ROUTE_CACHE[cache_key] = None
            return None
        segment = list(getattr(edge, "points", None) or [])
        if len(segment) < 2:
            ca = cells.get(a)
            cb = cells.get(b)
            if ca is None or cb is None:
                _SEA_REGION_ROUTE_CACHE[cache_key] = None
                return None
            segment = [(ca.center_x, ca.center_y), (cb.center_x, cb.center_y)]
        if str(getattr(edge, "from_region_id", "") or "").strip() != a:
            segment.reverse()
        if not points:
            points.extend(segment)
        else:
            if math.hypot(points[-1][0] - segment[0][0], points[-1][1] - segment[0][1]) > 1e-7:
                points.append(segment[0])
            points.extend(segment[1:])
    points = _dedupe_path_points(points)
    if len(points) < 2:
        _SEA_REGION_ROUTE_CACHE[cache_key] = None
        return None
    points = _water_route_points(geometry, points)
    if len(points) < 2:
        _SEA_REGION_ROUTE_CACHE[cache_key] = None
        return None
    result = (tuple(region_path), points, max(0.0001, distances[end]))
    _SEA_REGION_ROUTE_CACHE[cache_key] = result
    return result


def _sea_connector_points(
    geometry: WorldMapGeometry,
    start_point: Point,
    end_point: Point,
    *,
    region_id: str,
    ford_points: list[Point],
) -> list[Point]:
    if math.hypot(start_point[0] - end_point[0], start_point[1] - end_point[1]) <= 1e-7:
        return [start_point]
    connector = _route_between_points(
        geometry,
        start_point,
        end_point,
        start_region_id=region_id,
        end_region_id=region_id,
        ford_points=ford_points,
    )
    if connector is not None and len(connector.points) >= 2:
        return connector.points
    return [start_point, end_point]


def _sea_route_between_nodes(
    geometry: WorldMapGeometry,
    a: RoadMapNode,
    b: RoadMapNode,
    ford_points: list[Point],
) -> _SeaPath | None:
    sea_route = _configured_sea_region_route(geometry, a.region_id, b.region_id)
    if sea_route is None:
        return None
    route_regions, sea_points, sea_cost = sea_route
    start_point = (a.x, a.y)
    end_point = (b.x, b.y)
    first_connector = _sea_connector_points(
        geometry,
        start_point,
        sea_points[0],
        region_id=a.region_id,
        ford_points=ford_points,
    )
    last_connector = _sea_connector_points(
        geometry,
        sea_points[-1],
        end_point,
        region_id=b.region_id,
        ford_points=ford_points,
    )
    points = _dedupe_path_points(sea_points)
    if len(points) < 2:
        return None
    water_length = _path_length(points)
    access_length = _path_length(first_connector) + _path_length(last_connector)
    return _SeaPath(
        points=points,
        route_regions=route_regions,
        cost=max(0.0001, access_length + water_length * 0.72 + sea_cost * 0.015),
        length=max(0.0001, access_length + water_length),
    )


def _nearby_implied_amount(a: RoadMapNode, b: RoadMapNode) -> float:
    population_signal = math.sqrt(max(1, a.population) * max(1, b.population))
    market_signal = 1.0 + min(1.5, max(0.0, a.market_pull + b.market_pull) * 8.0)
    distance = max(0.015, math.hypot(a.x - b.x, a.y - b.y))
    return min(1.6, (0.16 + population_signal * 0.018) * market_signal / (1.0 + distance * 4.0))


def _add_outpost_and_trade_implied(
    demands: dict[tuple[str, str], _RoadDemand],
    nodes: dict[str, RoadMapNode],
) -> None:
    network_members: dict[str, list[RoadMapNode]] = {}
    for node in nodes.values():
        if node.mother_settlement_id and node.mother_settlement_id in nodes:
            _add_implied(demands, node.settlement_id, node.mother_settlement_id, 2.2)
        if node.trade_network_id:
            network_members.setdefault(node.trade_network_id, []).append(node)
            if node.trade_network_id != node.settlement_id and node.trade_network_id in nodes:
                _add_implied(demands, node.settlement_id, node.trade_network_id, 1.6)
    for network_id, members in network_members.items():
        if len(members) < 2:
            continue
        root = nodes.get(network_id)
        ranked = sorted(members, key=lambda n: (-n.population, n.settlement_id))
        if root is not None:
            for node in ranked[:4]:
                if node.settlement_id != root.settlement_id:
                    _add_implied(demands, root.settlement_id, node.settlement_id, 1.0)
        else:
            for first, second in zip(ranked, ranked[1:3]):
                _add_implied(demands, first.settlement_id, second.settlement_id, 0.8)


def _add_polity_implied(
    conn: sqlite3.Connection,
    demands: dict[tuple[str, str], _RoadDemand],
    nodes: dict[str, RoadMapNode],
) -> None:
    relations = _relations(conn)
    if not {"simulation_polities", "simulation_polity_territory"}.issubset(relations):
        return
    by_region: dict[str, list[RoadMapNode]] = {}
    for node in nodes.values():
        by_region.setdefault(node.region_id, []).append(node)
    try:
        rows = conn.execute(
            """
            SELECT p.capital_settlement_id, t.target_kind, t.target_id
            FROM simulation_polities p
            JOIN simulation_polity_territory t ON t.polity_id = p.polity_id
            WHERE (p.status IS NULL OR p.status = 'active')
              AND t.until_sim_year IS NULL
              AND p.capital_settlement_id IS NOT NULL
              AND p.capital_settlement_id <> ''
            """
        ).fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        capital = str(row["capital_settlement_id"] or "").strip()
        if capital not in nodes:
            continue
        target_kind = str(row["target_kind"] or "").strip().lower()
        target_id = str(row["target_id"] or "").strip()
        if target_kind == "settlement" and target_id in nodes and target_id != capital:
            _add_implied(demands, capital, target_id, 1.4)
        elif target_kind == "region":
            members = sorted(
                by_region.get(target_id, []),
                key=lambda n: (-n.population, math.hypot(n.x - nodes[capital].x, n.y - nodes[capital].y), n.settlement_id),
            )
            for node in members[:2]:
                if node.settlement_id != capital:
                    _add_implied(demands, capital, node.settlement_id, 1.1)


def _add_nearby_implied(
    demands: dict[tuple[str, str], _RoadDemand],
    nodes: dict[str, RoadMapNode],
    geometry: WorldMapGeometry,
) -> None:
    land_neighbors = _region_land_neighbors(geometry)
    all_nodes = list(nodes.values())
    for node in all_nodes:
        candidates: list[tuple[float, RoadMapNode]] = []
        allowed_regions = set(land_neighbors.get(node.region_id, set()))
        allowed_regions.add(node.region_id)
        for other in all_nodes:
            if other.settlement_id == node.settlement_id or other.region_id not in allowed_regions:
                continue
            distance = math.hypot(node.x - other.x, node.y - other.y)
            candidates.append((distance, other))
        for _distance, other in sorted(candidates, key=lambda item: (item[0], -item[1].population, item[1].settlement_id))[:2]:
            _add_implied(demands, node.settlement_id, other.settlement_id, _nearby_implied_amount(node, other))


def _sea_route_region_pairs(geometry: WorldMapGeometry) -> list[tuple[str, str, float]]:
    out: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for edge in geometry.edges:
        if not _is_sea_edge(edge):
            continue
        a = str(edge.from_region_id or "").strip()
        b = str(edge.to_region_id or "").strip()
        if not a or not b or a == b:
            continue
        key = _pair_key(a, b)
        if key in seen:
            continue
        seen.add(key)
        out.append((key[0], key[1], max(0.1, _coerce_float(edge.friction, 1.0))))
    return sorted(out)


def _node_maritime_rank(node: RoadMapNode, geometry: WorldMapGeometry) -> tuple[float, str]:
    cell = geometry.cell_by_region_id().get(node.region_id)
    coastal = 1.0 if cell is not None and (cell.is_coastal or cell.terrain_family == "coast") else 0.0
    founding = str(node.founding_reason or "").strip().lower()
    founding_bonus = 0.0
    if any(token in founding for token in ("outpost", "port", "trade", "maritime", "commercial")):
        founding_bonus = 0.45
    network_bonus = 0.35 if node.trade_network_id else 0.0
    market = max(0.0, node.market_pull) * 4.0 + max(0.0, node.prosperity_pool) * 0.08
    population = math.sqrt(max(1, node.population)) * 0.08
    return (coastal + founding_bonus + network_bonus + market + population, node.settlement_id)


def _sea_implied_amount(a: RoadMapNode, b: RoadMapNode, friction: float) -> float:
    population_signal = math.sqrt(max(1, a.population) * max(1, b.population))
    market_signal = 1.0 + min(1.2, max(0.0, a.market_pull + b.market_pull) * 7.0)
    route_signal = 0.45 + min(0.75, 12.0 / max(1.0, float(friction)))
    distance = max(0.04, math.hypot(a.x - b.x, a.y - b.y))
    return min(2.0, (0.42 + population_signal * 0.012) * market_signal * route_signal / (1.0 + distance * 1.8))


def _add_sea_route_implied(
    demands: dict[tuple[str, str], _RoadDemand],
    nodes: dict[str, RoadMapNode],
    geometry: WorldMapGeometry,
) -> None:
    by_region: dict[str, list[RoadMapNode]] = {}
    for node in nodes.values():
        by_region.setdefault(node.region_id, []).append(node)
    for from_region_id, to_region_id, friction in _sea_route_region_pairs(geometry):
        left = sorted(
            by_region.get(from_region_id, []),
            key=lambda n: (-_node_maritime_rank(n, geometry)[0], n.settlement_id),
        )
        right = sorted(
            by_region.get(to_region_id, []),
            key=lambda n: (-_node_maritime_rank(n, geometry)[0], n.settlement_id),
        )
        for a in left[:2]:
            for b in right[:2]:
                if a.settlement_id == b.settlement_id:
                    continue
                _add_implied(demands, a.settlement_id, b.settlement_id, _sea_implied_amount(a, b, friction))


def _load_demands(
    conn: sqlite3.Connection,
    nodes: dict[str, RoadMapNode],
    geometry: WorldMapGeometry,
) -> tuple[dict[tuple[str, str], _RoadDemand], int | None]:
    latest_year = _current_year(conn, geometry.world)
    demands = _load_actual_demands(conn, nodes, latest_year=latest_year)
    _add_outpost_and_trade_implied(demands, nodes)
    _add_polity_implied(conn, demands, nodes)
    _add_nearby_implied(demands, nodes, geometry)
    return {key: demand for key, demand in demands.items() if demand.usage > 0.0}, latest_year


def _cell_for_point(
    geometry: WorldMapGeometry,
    point: Point,
    *,
    region_id: str | None = None,
) -> MicroRegionCell | None:
    rid = str(region_id or "").strip()
    candidates = [c for c in geometry.micro_cells if c.region_id == rid] if rid else []
    candidates = candidates or list(geometry.micro_cells)
    for cell in candidates:
        if _point_in_polygon(point, cell.polygon):
            return cell
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (
            (c.center_x - point[0]) ** 2 + (c.center_y - point[1]) ** 2,
            0 if rid and c.region_id == rid else 1,
            c.micro_id,
        ),
    )


def _terrain_multiplier(cell: MicroRegionCell) -> float:
    mult = _TERRAIN_MULTIPLIERS.get(str(cell.terrain_family or "").strip().lower(), 1.12)
    mult += max(0.0, float(cell.elevation) - 0.62) * 1.35
    if float(cell.moisture) >= 0.82 and not getattr(cell, "is_channel", False):
        mult += 0.16
    if getattr(cell, "is_floodplain", False):
        mult -= 0.08
    return max(0.6, mult)


def _nearest_ford_distance(point: Point, ford_points: list[Point]) -> float:
    if not ford_points:
        return float("inf")
    return min(math.hypot(point[0] - fx, point[1] - fy) for fx, fy in ford_points)


def _nearest_ford_for_segment(a: Point, b: Point, ford_points: list[Point], *, max_distance: float) -> Point | None:
    if not ford_points:
        return None
    best: tuple[float, Point] | None = None
    for ford in ford_points:
        dist = _point_segment_distance(ford, a, b)
        if dist > max_distance:
            continue
        candidate = (dist, ford)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None else None


def _ford_snap_threshold(a: MicroRegionCell, b: MicroRegionCell) -> float:
    step = math.hypot(a.center_x - b.center_x, a.center_y - b.center_y)
    return max(0.018, min(0.12, step * 0.75 + 0.012))


def _river_crossing_penalty(
    a: MicroRegionCell,
    b: MicroRegionCell,
    ford_points: list[Point],
) -> float:
    a_side = float(getattr(a, "river_side", 0.0) or 0.0)
    b_side = float(getattr(b, "river_side", 0.0) or 0.0)
    flow = max(float(getattr(a, "river_flow", 0.0) or 0.0), float(getattr(b, "river_flow", 0.0) or 0.0))
    near_river = min(
        float(getattr(a, "river_distance", 1.0) or 1.0),
        float(getattr(b, "river_distance", 1.0) or 1.0),
    ) <= 0.07
    crosses_side = a_side * b_side < 0.0 and near_river
    channel = bool(getattr(a, "is_channel", False) or getattr(b, "is_channel", False))
    if not (channel or crosses_side):
        return 0.0
    midpoint = ((a.center_x + b.center_x) / 2.0, (a.center_y + b.center_y) / 2.0)
    near_ford = _nearest_ford_distance(midpoint, ford_points) <= _ford_snap_threshold(a, b)
    if near_ford:
        return 0.015 + flow * 0.025
    if flow <= 0.35:
        return 0.06 + flow * 0.06
    return 0.55 + flow * 0.45


def _road_step_weight(
    a: MicroRegionCell,
    b: MicroRegionCell,
    ford_points: list[Point],
) -> float:
    distance = math.hypot(a.center_x - b.center_x, a.center_y - b.center_y)
    terrain = (_terrain_multiplier(a) + _terrain_multiplier(b)) / 2.0
    return max(0.0001, distance * terrain + _river_crossing_penalty(a, b, ford_points))


def _micro_shared_edges(micro_cells: list[MicroRegionCell]) -> dict[tuple[str, str], tuple[Point, Point]]:
    edge_owner: dict[tuple[Point, Point], tuple[str, Point, Point]] = {}
    out: dict[tuple[str, str], tuple[Point, Point]] = {}
    for cell in micro_cells:
        pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            edge_key = tuple(sorted((a, b)))  # type: ignore[assignment]
            other = edge_owner.get(edge_key)
            if other is None:
                edge_owner[edge_key] = (cell.micro_id, a, b)
                continue
            other_id, other_a, other_b = other
            if other_id == cell.micro_id:
                continue
            pair = tuple(sorted((cell.micro_id, other_id)))  # type: ignore[assignment]
            out[pair] = (other_a, other_b)
    return out


def _road_micro_graph(
    micro_cells: list[MicroRegionCell],
) -> tuple[dict[str, set[str]], dict[tuple[str, str], tuple[Point, Point]]]:
    key = id(micro_cells)
    cached = _MICRO_GRAPH_CACHE.get(key)
    if cached is not None and cached[0] == len(micro_cells):
        return cached[1], cached[2]
    adjacency, _shared_midpoints = _micro_adjacency(micro_cells)
    shared_edges = _micro_shared_edges(micro_cells)
    _MICRO_GRAPH_CACHE[key] = (len(micro_cells), adjacency, shared_edges)
    return adjacency, shared_edges


def _point_on_edge_midpoint(edge: tuple[Point, Point]) -> Point:
    return ((edge[0][0] + edge[1][0]) / 2.0, (edge[0][1] + edge[1][1]) / 2.0)


def _nearest_cell_edge(
    cell: MicroRegionCell,
    point: Point,
) -> tuple[tuple[Point, Point], Point, float] | None:
    pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
    if len(pts) < 3:
        return None
    best: tuple[float, tuple[Point, Point], Point] | None = None
    for idx, a in enumerate(pts):
        b = pts[(idx + 1) % len(pts)]
        nearest = _nearest_point_on_segment(point, a, b)
        distance = math.hypot(point[0] - nearest[0], point[1] - nearest[1])
        candidate = (distance, (a, b), nearest)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    return best[1], best[2], best[0]


def _edge_anchor_for_point(
    cell: MicroRegionCell,
    point: Point,
    *,
    tolerance: float = 0.0045,
) -> tuple[tuple[Point, Point], Point] | None:
    nearest = _nearest_cell_edge(cell, point)
    if nearest is None:
        return None
    edge, projected, distance = nearest
    if distance > tolerance:
        return None
    return edge, projected


def _cell_edge_index(cell: MicroRegionCell, edge: tuple[Point, Point]) -> int | None:
    wanted = tuple(sorted(edge))  # type: ignore[assignment]
    pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
    for idx, a in enumerate(pts):
        b = pts[(idx + 1) % len(pts)]
        if tuple(sorted((a, b))) == wanted:
            return idx
    return None


def _cell_boundary_arc(
    cell: MicroRegionCell,
    entry_edge: tuple[Point, Point],
    exit_edge: tuple[Point, Point],
    entry: Point,
    exit: Point,
) -> list[Point]:
    """Walk the shorter polygon boundary arc between two shared-edge points."""
    pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
    n = len(pts)
    if n < 3:
        return [entry, exit]
    entry_idx = _cell_edge_index(cell, entry_edge)
    exit_idx = _cell_edge_index(cell, exit_edge)
    if entry_idx is None or exit_idx is None:
        return [entry, exit]
    if entry_idx == exit_idx:
        return [entry, exit]

    forward: list[Point] = [entry, pts[(entry_idx + 1) % n]]
    idx = (entry_idx + 1) % n
    while idx != exit_idx:
        idx = (idx + 1) % n
        forward.append(pts[idx])
    forward.append(exit)

    backward: list[Point] = [entry, pts[entry_idx]]
    idx = entry_idx
    target = (exit_idx + 1) % n
    while idx != target:
        idx = (idx - 1) % n
        backward.append(pts[idx])
    backward.append(exit)

    return forward if _path_length(forward) <= _path_length(backward) else backward


def _maybe_boundary_arc_between_points(
    cell: MicroRegionCell,
    start: Point,
    end: Point,
) -> list[Point] | None:
    start_anchor = _edge_anchor_for_point(cell, start)
    end_anchor = _edge_anchor_for_point(cell, end)
    if start_anchor is None or end_anchor is None:
        return None
    start_edge, start_projected = start_anchor
    end_edge, end_projected = end_anchor
    if tuple(sorted(start_edge)) == tuple(sorted(end_edge)):
        return None
    arc = _cell_boundary_arc(cell, start_edge, end_edge, start_projected, end_projected)
    if len(arc) < 2:
        return None
    direct = max(0.0001, math.hypot(end[0] - start[0], end[1] - start[1]))
    if _path_length(arc) > direct * 2.8:
        return None
    out: list[Point] = []
    if math.hypot(start[0] - start_projected[0], start[1] - start_projected[1]) > 1e-7:
        out.append(start)
    out.extend(arc)
    if math.hypot(end[0] - end_projected[0], end[1] - end_projected[1]) > 1e-7:
        out.append(end)
    return out


def _edge_trace_existing_points(
    geometry: WorldMapGeometry,
    points: list[Point],
) -> list[Point]:
    if len(points) < 2 or not geometry.micro_cells:
        return points
    out: list[Point] = [points[0]]
    for start, end in zip(points, points[1:]):
        midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        cell = _cell_for_point(geometry, midpoint)
        arc = _maybe_boundary_arc_between_points(cell, start, end) if cell is not None else None
        segment = arc if arc is not None else [start, end]
        out.extend(segment[1:])
    return _dedupe_path_points(out)


def _point_in_any_micro_cell(
    geometry: WorldMapGeometry,
    point: Point,
) -> bool:
    return any(_point_in_polygon(point, cell.polygon) for cell in geometry.micro_cells)


def _segment_has_non_land_samples(
    geometry: WorldMapGeometry,
    start: Point,
    end: Point,
    *,
    samples: int = 9,
) -> bool:
    if not geometry.micro_cells:
        return False
    for idx in range(1, max(2, samples + 1)):
        t = idx / (samples + 1)
        point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        if not _point_in_any_micro_cell(geometry, point):
            return True
    return False


def _micro_path_edge_points(
    micro_path: list[str],
    by_id: dict[str, MicroRegionCell],
    shared_edges: dict[tuple[str, str], tuple[Point, Point]],
    start_point: Point,
    end_point: Point,
    ford_points: list[Point],
) -> list[Point]:
    if len(micro_path) <= 1:
        return [start_point, end_point]

    transitions: list[tuple[Point, tuple[Point, Point] | None]] = []
    for first_id, second_id in zip(micro_path, micro_path[1:]):
        first = by_id[first_id]
        second = by_id[second_id]
        pair = tuple(sorted((first_id, second_id)))  # type: ignore[assignment]
        shared_edge = shared_edges.get(pair)
        edge_point = (
            _point_on_edge_midpoint(shared_edge)
            if shared_edge is not None
            else ((first.center_x + second.center_x) / 2.0, (first.center_y + second.center_y) / 2.0)
        )
        ford = None
        if _river_crossing_penalty(first, second, ford_points) > 0.0:
            ford = _nearest_ford_for_segment(
                (first.center_x, first.center_y),
                (second.center_x, second.center_y),
                ford_points,
                max_distance=_ford_snap_threshold(first, second),
            )
        transitions.append((ford if ford is not None else edge_point, None if ford is not None else shared_edge))

    first_cell = by_id[micro_path[0]]
    first_point, first_edge = transitions[0]
    first_arc = (
        _maybe_boundary_arc_between_points(first_cell, start_point, first_point)
        if first_edge is not None
        else None
    )
    points: list[Point] = first_arc if first_arc is not None else [start_point, first_point]
    for idx in range(1, len(transitions)):
        prior_point, prior_edge = transitions[idx - 1]
        current_point, current_edge = transitions[idx]
        cell = by_id[micro_path[idx]]
        if prior_edge is not None and current_edge is not None:
            arc = _cell_boundary_arc(cell, prior_edge, current_edge, prior_point, current_point)
            points.extend(arc[1:])
        else:
            points.append(current_point)
    last_cell = by_id[micro_path[-1]]
    last_point, last_edge = transitions[-1]
    last_arc = (
        _maybe_boundary_arc_between_points(last_cell, last_point, end_point)
        if last_edge is not None
        else None
    )
    if last_arc is not None:
        points.extend(last_arc[1:])
    else:
        points.append(end_point)
    return points


def _route_between_points(
    geometry: WorldMapGeometry,
    start_point: Point,
    end_point: Point,
    *,
    start_region_id: str | None,
    end_region_id: str | None,
    ford_points: list[Point],
    allow_ford_shortcut: bool = True,
) -> _RoadPath | None:
    if not geometry.micro_cells:
        distance = math.hypot(start_point[0] - end_point[0], start_point[1] - end_point[1])
        points = _dedupe_path_points([start_point, end_point])
        return _RoadPath(points=points, cost=distance, length=_path_length(points))
    start = _cell_for_point(geometry, start_point, region_id=start_region_id)
    end = _cell_for_point(geometry, end_point, region_id=end_region_id)
    if start is None or end is None:
        return None
    if start.continent_id != end.continent_id:
        return None
    ford_path = _direct_ford_path(start_point, end_point, ford_points) if allow_ford_shortcut else None
    if start.micro_id == end.micro_id:
        points = _maybe_boundary_arc_between_points(start, start_point, end_point) or [start_point, end_point]
        points = _dedupe_path_points(points)
        return _RoadPath(
            points=points,
            cost=max(0.0001, math.hypot(start_point[0] - end_point[0], start_point[1] - end_point[1])),
            length=_path_length(points),
        )
    by_id = {cell.micro_id: cell for cell in geometry.micro_cells}
    adjacency, shared_edges = _road_micro_graph(geometry.micro_cells)
    def heuristic(cell: MicroRegionCell) -> float:
        return math.hypot(cell.center_x - end.center_x, cell.center_y - end.center_y) * 0.58

    queue: list[tuple[float, float, str]] = [(heuristic(start), 0.0, start.micro_id)]
    distances = {start.micro_id: 0.0}
    previous: dict[str, str] = {}
    while queue:
        _priority, cost, micro_id = heapq.heappop(queue)
        if cost > distances.get(micro_id, float("inf")):
            continue
        if micro_id == end.micro_id:
            break
        cell = by_id[micro_id]
        for neighbor_id in adjacency.get(micro_id, set()):
            neighbor = by_id[neighbor_id]
            if neighbor.continent_id != cell.continent_id:
                continue
            nd = cost + _road_step_weight(cell, neighbor, ford_points)
            if nd < distances.get(neighbor_id, float("inf")):
                distances[neighbor_id] = nd
                previous[neighbor_id] = micro_id
                heapq.heappush(queue, (nd + heuristic(neighbor), nd, neighbor_id))
    if end.micro_id not in distances:
        return None
    micro_path = [end.micro_id]
    while micro_path[-1] != start.micro_id:
        prior = previous.get(micro_path[-1])
        if prior is None:
            return None
        micro_path.append(prior)
    micro_path.reverse()
    points = _micro_path_edge_points(
        micro_path,
        by_id,
        shared_edges,
        start_point,
        end_point,
        ford_points,
    )
    points = _clean_road_points(points, ford_points)
    route = _RoadPath(
        points=points,
        cost=max(0.0001, distances[end.micro_id]),
        length=_path_length(points),
    )
    if ford_path is not None:
        direct_distance = max(0.0001, math.hypot(start_point[0] - end_point[0], start_point[1] - end_point[1]))
        if route.length / direct_distance >= 1.55 or ford_path.length <= route.length * 0.86:
            natural_ford_path = None
            if len(ford_path.points) >= 3:
                natural_ford_path = _route_via_ford_point(
                    geometry,
                    start_point,
                    ford_path.points[1],
                    end_point,
                    start_region_id=start_region_id,
                    end_region_id=end_region_id,
                    ford_points=ford_points,
                )
            return natural_ford_path or ford_path
    return route


def _expand_route_points_through_land(
    geometry: WorldMapGeometry,
    points: list[Point],
    ford_points: list[Point],
) -> list[Point] | None:
    """Replace route chords that leave the land mesh with micro-cell edge routes."""
    if len(points) < 2 or not geometry.micro_cells:
        return points
    out: list[Point] = [points[0]]
    for start, end in zip(points, points[1:]):
        direct = math.hypot(end[0] - start[0], end[1] - start[1])
        segment = [start, end]
        crosses_non_land = _segment_has_non_land_samples(geometry, start, end)
        if crosses_non_land:
            routed = _route_between_points(
                geometry,
                start,
                end,
                start_region_id=None,
                end_region_id=None,
                ford_points=ford_points,
                allow_ford_shortcut=False,
            )
            if routed is None:
                return None
            elif routed.length <= max(0.0001, direct) * 4.5:
                segment = routed.points
            else:
                return None
        out.extend(segment[1:])
    return _dedupe_path_points(out)


def _route_via_ford_point(
    geometry: WorldMapGeometry,
    start_point: Point,
    ford_point: Point,
    end_point: Point,
    *,
    start_region_id: str | None,
    end_region_id: str | None,
    ford_points: list[Point],
) -> _RoadPath | None:
    first = _route_between_points(
        geometry,
        start_point,
        ford_point,
        start_region_id=start_region_id,
        end_region_id=None,
        ford_points=ford_points,
        allow_ford_shortcut=False,
    )
    second = _route_between_points(
        geometry,
        ford_point,
        end_point,
        start_region_id=None,
        end_region_id=end_region_id,
        ford_points=ford_points,
        allow_ford_shortcut=False,
    )
    if first is None or second is None:
        return None
    points = _clean_road_points([*first.points, *second.points[1:]], [ford_point])
    if len(points) < 2:
        return None
    length = _path_length(points)
    return _RoadPath(points=points, cost=max(0.0001, first.cost + second.cost), length=length)


def _route_between_nodes(
    geometry: WorldMapGeometry,
    a: RoadMapNode,
    b: RoadMapNode,
    ford_points: list[Point],
) -> _RoadPath | None:
    start_point = (a.x, a.y)
    end_point = (b.x, b.y)
    if a.region_id != b.region_id:
        land_neighbors = _region_land_neighbors(geometry)
        if b.region_id in land_neighbors.get(a.region_id, set()):
            direct_micro_route = _route_between_points(
                geometry,
                start_point,
                end_point,
                start_region_id=a.region_id,
                end_region_id=b.region_id,
                ford_points=ford_points,
            )
            direct_distance = max(0.0001, math.hypot(start_point[0] - end_point[0], start_point[1] - end_point[1]))
            if direct_micro_route is not None and direct_micro_route.length / direct_distance <= 2.8:
                return direct_micro_route
        region_route = _configured_region_route_points(geometry, a.region_id, b.region_id)
        if region_route is not None:
            region_route = _edge_trace_existing_points(geometry, region_route)
            expanded_region_route = _expand_route_points_through_land(geometry, region_route, ford_points)
            if expanded_region_route is None:
                region_route = []
            else:
                region_route = expanded_region_route
        if region_route:
            first_connector = _route_between_points(
                geometry,
                start_point,
                region_route[0],
                start_region_id=a.region_id,
                end_region_id=a.region_id,
                ford_points=ford_points,
            )
            last_connector = _route_between_points(
                geometry,
                region_route[-1],
                end_point,
                start_region_id=b.region_id,
                end_region_id=b.region_id,
                ford_points=ford_points,
            )
            if first_connector is not None and last_connector is not None:
                points = [
                    *first_connector.points,
                    *region_route[1:-1],
                    *last_connector.points[1:],
                ]
                points = _clean_road_points(points, ford_points)
                if len(points) >= 2:
                    length = _path_length(points)
                    return _RoadPath(points=points, cost=max(0.0001, length), length=length)
    return _route_between_points(
        geometry,
        start_point,
        end_point,
        start_region_id=a.region_id,
        end_region_id=b.region_id,
        ford_points=ford_points,
    )


def _path_length(points: list[Point]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _direct_ford_path(start: Point, end: Point, ford_points: list[Point]) -> _RoadPath | None:
    if not ford_points:
        return None
    direct_distance = math.hypot(start[0] - end[0], start[1] - end[1])
    if direct_distance <= 1e-7:
        return None
    best: tuple[float, float, Point] | None = None
    endpoint_threshold = max(0.018, min(0.07, direct_distance * 0.55 + 0.014))
    segment_threshold = max(0.014, min(0.055, direct_distance * 0.36 + 0.01))
    for ford in ford_points:
        endpoint_distance = min(
            math.hypot(ford[0] - start[0], ford[1] - start[1]),
            math.hypot(ford[0] - end[0], ford[1] - end[1]),
        )
        segment_distance = _point_segment_distance(ford, start, end)
        if endpoint_distance > endpoint_threshold and segment_distance > segment_threshold:
            continue
        detour_length = math.hypot(ford[0] - start[0], ford[1] - start[1]) + math.hypot(
            ford[0] - end[0], ford[1] - end[1]
        )
        detour_ratio = detour_length / direct_distance
        if endpoint_distance > endpoint_threshold and detour_ratio > 1.55:
            continue
        candidate = (detour_ratio, segment_distance, ford)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    ford = best[2]
    points = _clean_road_points([start, ford, end], [ford])
    length = _path_length(points)
    return _RoadPath(points=points, cost=max(0.0001, length * 0.94), length=length)


def _is_preserved_point(point: Point, preserve_points: list[Point], *, tolerance: float) -> bool:
    return any(math.hypot(point[0] - x, point[1] - y) <= tolerance for x, y in preserve_points)


def _clean_road_points(points: list[Point], preserve_points: list[Point] | None = None) -> list[Point]:
    """Remove tiny out-and-back artifacts before SVG smoothing amplifies them."""
    preserve_points = preserve_points or []
    out: list[Point] = []
    loop_tolerance = 0.004
    for point in _dedupe_path_points(points):
        repeat_idx = next(
            (
                idx
                for idx, prior in enumerate(out[:-1])
                if math.hypot(point[0] - prior[0], point[1] - prior[1]) <= loop_tolerance
            ),
            None,
        )
        if repeat_idx is not None and not any(
            _is_preserved_point(prior, preserve_points, tolerance=loop_tolerance)
            for prior in out[repeat_idx + 1 :]
        ):
            out = out[: repeat_idx + 1]
            continue
        if (
            len(out) >= 2
            and math.hypot(point[0] - out[-2][0], point[1] - out[-2][1]) <= loop_tolerance
            and not _is_preserved_point(out[-1], preserve_points, tolerance=loop_tolerance)
        ):
            out.pop()
            if math.hypot(point[0] - out[-1][0], point[1] - out[-1][1]) > 1e-7:
                out.append(point)
            continue
        out.append(point)

    if len(out) < 3:
        return out

    simplified = [out[0]]
    collinear_tolerance = 0.0016
    for idx, point in enumerate(out[1:-1], start=1):
        prior = simplified[-1]
        nxt = out[idx + 1]
        if (
            not _is_preserved_point(point, preserve_points, tolerance=loop_tolerance)
            and _point_segment_distance(point, prior, nxt) <= collinear_tolerance
        ):
            continue
        simplified.append(point)
    simplified.append(out[-1])
    return _dedupe_path_points(simplified)


def _point_polyline_distance(point: Point, points: list[Point]) -> float:
    if len(points) < 2:
        return math.hypot(point[0] - points[0][0], point[1] - points[0][1]) if points else float("inf")
    return min(_point_segment_distance(point, a, b) for a, b in zip(points, points[1:]))


def _network_route(
    segments: dict[tuple[str, str], _RoadSegment],
    start: str,
    end: str,
) -> tuple[float, float, list[tuple[str, str]]] | None:
    adjacency: dict[str, list[tuple[str, float, float]]] = {}
    for (a, b), segment in segments.items():
        adjacency.setdefault(a, []).append((b, segment.length, segment.cost))
        adjacency.setdefault(b, []).append((a, segment.length, segment.cost))
    if start not in adjacency or end not in adjacency:
        return None
    queue: list[tuple[float, str]] = [(0.0, start)]
    distances = {start: 0.0}
    previous: dict[str, str] = {}
    while queue:
        cost, node_id = heapq.heappop(queue)
        if cost > distances.get(node_id, float("inf")):
            continue
        if node_id == end:
            break
        for other, length, _step_cost in adjacency.get(node_id, []):
            nd = cost + length
            if nd < distances.get(other, float("inf")):
                distances[other] = nd
                previous[other] = node_id
                heapq.heappush(queue, (nd, other))
    if end not in distances:
        return None
    node_path = [end]
    while node_path[-1] != start:
        prior = previous.get(node_path[-1])
        if prior is None:
            return None
        node_path.append(prior)
    node_path.reverse()
    pairs = [_pair_key(a, b) for a, b in zip(node_path, node_path[1:])]
    route_cost = sum(segments[pair].cost for pair in pairs if pair in segments)
    return route_cost, distances[end], pairs


def _add_usage_to_segments(
    segments: dict[tuple[str, str], _RoadSegment],
    segment_keys: Iterable[tuple[str, str]],
    demand: _RoadDemand,
) -> None:
    for key in segment_keys:
        segment = segments.get(key)
        if segment is None:
            continue
        segment.actual_usage += demand.actual_usage
        segment.implied_usage += demand.implied_usage


def _ensure_segment(
    segments: dict[tuple[str, str], _RoadSegment],
    routes: dict[tuple[str, str], _RoadPath],
    geometry: WorldMapGeometry,
    nodes: dict[str, RoadMapNode],
    ford_points: list[Point],
    a: str,
    b: str,
) -> tuple[str, str] | None:
    key = _pair_key(a, b)
    if key in segments:
        return key
    route = routes.get(key)
    if route is None:
        route = _route_between_nodes(geometry, nodes[a], nodes[b], ford_points)
        if route is None:
            return None
        routes[key] = route
    segments[key] = _RoadSegment(
        from_settlement_id=key[0],
        to_settlement_id=key[1],
        points=route.points,
        cost=route.cost,
        length=route.length,
    )
    return key


def _route_settlement_ratio(
    route: _RoadPath,
    nodes: dict[str, RoadMapNode],
    a: str,
    b: str,
) -> float:
    direct = max(0.0001, math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y))
    return route.length / direct


def _sea_route_should_replace_land_route(
    geometry: WorldMapGeometry,
    land_route: _RoadPath,
    sea_route: _SeaPath | None,
    nodes: dict[str, RoadMapNode],
    a: str,
    b: str,
) -> bool:
    if sea_route is None:
        return False
    direct = max(0.0001, math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y))
    land_ratio = land_route.length / direct
    sea_ratio = sea_route.length / direct
    if sea_route.length <= land_route.length * 0.74:
        return True
    if land_ratio >= 2.05 and sea_ratio <= 1.65:
        return True
    cells = geometry.cell_by_region_id()
    a_cell = cells.get(nodes[a].region_id)
    b_cell = cells.get(nodes[b].region_id)
    both_coastal = bool(
        a_cell is not None
        and b_cell is not None
        and (a_cell.is_coastal or a_cell.terrain_family == "coast")
        and (b_cell.is_coastal or b_cell.terrain_family == "coast")
    )
    return both_coastal and land_ratio >= 1.75 and sea_route.length <= land_route.length * 0.88


def _best_via_node(
    nodes: dict[str, RoadMapNode],
    routes: dict[tuple[str, str], _RoadPath],
    direct_key: tuple[str, str],
    direct_route: _RoadPath,
) -> tuple[str, float] | None:
    a_id, c_id = direct_key
    a = nodes[a_id]
    c = nodes[c_id]
    direct_line_distance = max(0.0001, math.hypot(a.x - c.x, a.y - c.y))
    max_offset = max(0.025, min(0.085, direct_line_distance * 0.18))
    best: tuple[float, float, float, str] | None = None
    for b_id, node in nodes.items():
        if b_id in direct_key:
            continue
        node_point = (node.x, node.y)
        line_offset = _point_segment_distance(node_point, (a.x, a.y), (c.x, c.y))
        route_offset = _point_polyline_distance(node_point, direct_route.points)
        if min(line_offset, route_offset) > max_offset:
            continue
        first = routes.get(_pair_key(a_id, b_id))
        second = routes.get(_pair_key(b_id, c_id))
        if first is None or second is None:
            continue
        settlement_ratio = (
            math.hypot(a.x - node.x, a.y - node.y)
            + math.hypot(node.x - c.x, node.y - c.y)
        ) / direct_line_distance
        routed_ratio = (first.length + second.length) / max(direct_line_distance, direct_route.length, 0.0001)
        absolute_routed_ratio = (first.length + second.length) / direct_line_distance
        if absolute_routed_ratio > 3.2:
            continue
        if routed_ratio > 1.85 and settlement_ratio > 1.08:
            continue
        geometry_ratio = routed_ratio if route_offset <= max_offset else settlement_ratio
        candidate = (geometry_ratio, settlement_ratio, routed_ratio, min(line_offset, route_offset), b_id)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    return (best[4], best[0])


def _finalize_edges(segments: dict[tuple[str, str], _RoadSegment]) -> list[RoadMapEdge]:
    usable = [segment for segment in segments.values() if segment.usage > 0.0 and len(segment.points) >= 2]
    max_usage = max((segment.usage for segment in usable), default=0.0)
    out: list[RoadMapEdge] = []
    for segment in usable:
        if max_usage <= 0.0:
            opacity = 0.0
        else:
            normalized = math.sqrt(max(0.0, segment.usage) / max_usage)
            if segment.actual_usage > 0.0:
                opacity = min(0.72, 0.24 + 0.48 * normalized)
            else:
                opacity = min(0.28, 0.10 + 0.18 * normalized)
        out.append(
            RoadMapEdge(
                from_settlement_id=segment.from_settlement_id,
                to_settlement_id=segment.to_settlement_id,
                points=segment.points,
                usage=round(segment.usage, 4),
                actual_usage=round(segment.actual_usage, 4),
                implied_usage=round(segment.implied_usage, 4),
                opacity=round(opacity, 3),
            )
        )
    return sorted(out, key=lambda e: (-e.usage, e.from_settlement_id, e.to_settlement_id))


def build_settlement_road_overlays(
    *,
    geometry: WorldMapGeometry,
    save_db_path: Path | str | None,
    max_nodes: int = 500,
    max_roads: int = 420,
) -> list[RoadMapEdge]:
    """Build a usage-weighted visual road overlay from the current save."""
    if save_db_path is None:
        return []
    path = Path(save_db_path)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        nodes, _rows, ford_points = _load_nodes(conn, geometry, max_nodes=max_nodes)
        if len(nodes) < 2:
            return []
        demands, _latest_year = _load_demands(conn, nodes, geometry)
    if not demands:
        return []

    routes: dict[tuple[str, str], _RoadPath] = {}
    for key in demands:
        route = _route_between_nodes(geometry, nodes[key[0]], nodes[key[1]], ford_points)
        if route is not None:
            routes[key] = route
    for key in list(demands):
        if key not in routes:
            demands.pop(key, None)
    if not demands:
        return []

    max_actual = max((d.actual_usage for d in demands.values()), default=0.0)
    ordered = sorted(
        demands.items(),
        key=lambda item: (
            0 if item[1].actual_usage > 0.0 else 1,
            -item[1].usage,
            item[0],
        ),
    )[: max(1, int(max_roads))]
    segments: dict[tuple[str, str], _RoadSegment] = {}
    for key, demand in ordered:
        direct_route = routes[key]
        direct_settlement_distance = max(
            0.0001,
            math.hypot(nodes[key[0]].x - nodes[key[1]].x, nodes[key[0]].y - nodes[key[1]].y),
        )
        sea_route = None
        if (
            nodes[key[0]].region_id != nodes[key[1]].region_id
            and direct_route.length / direct_settlement_distance >= 1.45
        ):
            sea_route = _sea_route_between_nodes(geometry, nodes[key[0]], nodes[key[1]], ford_points)
        if _sea_route_should_replace_land_route(geometry, direct_route, sea_route, nodes, key[0], key[1]):
            continue
        strong_actual = demand.actual_usage >= max(3.0, max_actual * 0.25)
        network = _network_route(segments, key[0], key[1])
        if network is not None:
            _network_cost, network_length, network_segments = network
            network_ratio = network_length / direct_settlement_distance
            if network_ratio <= 1.2 or (network_ratio <= 1.45 and not strong_actual):
                _add_usage_to_segments(segments, network_segments, demand)
                continue

        for via_id in nodes:
            if via_id in key:
                continue
            for via_key in (_pair_key(key[0], via_id), _pair_key(via_id, key[1])):
                if via_key not in routes:
                    route = _route_between_nodes(geometry, nodes[via_key[0]], nodes[via_key[1]], ford_points)
                    if route is not None:
                        routes[via_key] = route
        via = _best_via_node(nodes, routes, key, direct_route)
        if via is not None:
            via_id, via_ratio = via
            if via_ratio <= 1.2 or (via_ratio <= 1.45 and not strong_actual):
                first_key = _pair_key(key[0], via_id)
                second_key = _pair_key(via_id, key[1])
                first_route = routes.get(first_key)
                second_route = routes.get(second_key)
                via_legs_reasonable = not (
                    not strong_actual
                    and first_route is not None
                    and second_route is not None
                    and (
                        _route_settlement_ratio(first_route, nodes, first_key[0], first_key[1]) > 3.2
                        or _route_settlement_ratio(second_route, nodes, second_key[0], second_key[1]) > 3.2
                    )
                )
                if via_legs_reasonable:
                    first = _ensure_segment(segments, routes, geometry, nodes, ford_points, key[0], via_id)
                    second = _ensure_segment(segments, routes, geometry, nodes, ford_points, via_id, key[1])
                    if first is not None and second is not None:
                        _add_usage_to_segments(segments, (first, second), demand)
                        continue

        direct_route_ratio = direct_route.length / direct_settlement_distance
        if demand.actual_usage <= 0.0 and direct_route_ratio > 2.2:
            continue
        if not strong_actual and direct_route_ratio > 3.6:
            continue
        direct = _ensure_segment(segments, routes, geometry, nodes, ford_points, key[0], key[1])
        if direct is not None:
            _add_usage_to_segments(segments, (direct,), demand)
    return _finalize_edges(segments)


def build_settlement_sea_route_overlays(
    *,
    geometry: WorldMapGeometry,
    save_db_path: Path | str | None,
    max_nodes: int = 500,
    max_routes: int = 260,
) -> list[SeaRouteMapEdge]:
    """Build usage-weighted settlement sea-lane overlays from configured sea routes."""
    if save_db_path is None:
        return []
    if not any(_is_sea_edge(edge) for edge in geometry.edges):
        return []
    path = Path(save_db_path)
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        nodes, _rows, ford_points = _load_nodes(conn, geometry, max_nodes=max_nodes)
        if len(nodes) < 2:
            return []
        demands, _latest_year = _load_demands(conn, nodes, geometry)
        _add_sea_route_implied(demands, nodes, geometry)
    if not demands:
        return []

    routes: dict[tuple[str, str], _SeaPath] = {}
    for key in demands:
        if key[0] not in nodes or key[1] not in nodes:
            continue
        if nodes[key[0]].region_id == nodes[key[1]].region_id:
            continue
        route = _sea_route_between_nodes(geometry, nodes[key[0]], nodes[key[1]], ford_points)
        if route is not None:
            routes[key] = route
    if not routes:
        return []

    ordered = sorted(
        ((key, demands[key], route) for key, route in routes.items() if demands[key].usage > 0.0),
        key=lambda item: (
            0 if item[1].actual_usage > 0.0 else 1,
            -item[1].usage,
            item[0],
        ),
    )[: max(1, int(max_routes))]
    max_usage = max((demand.usage for _key, demand, _route in ordered), default=0.0)
    out: list[SeaRouteMapEdge] = []
    for key, demand, route in ordered:
        normalized = math.sqrt(max(0.0, demand.usage) / max_usage) if max_usage > 0.0 else 0.0
        if demand.actual_usage > 0.0:
            opacity = min(0.78, 0.28 + 0.50 * normalized)
        else:
            opacity = min(0.42, 0.14 + 0.28 * normalized)
        out.append(
            SeaRouteMapEdge(
                from_settlement_id=key[0],
                to_settlement_id=key[1],
                points=route.points,
                route_regions=route.route_regions,
                usage=round(demand.usage, 4),
                actual_usage=round(demand.actual_usage, 4),
                implied_usage=round(demand.implied_usage, 4),
                opacity=round(opacity, 3),
            )
        )
    return sorted(out, key=lambda e: (-e.usage, e.from_settlement_id, e.to_settlement_id))
