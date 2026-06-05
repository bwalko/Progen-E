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
    _point_in_polygon,
    _point_segment_distance,
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
class _RoadPath:
    points: list[Point]
    cost: float


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
    region_id: str,
) -> MicroRegionCell | None:
    candidates = [c for c in geometry.micro_cells if c.region_id == region_id] or list(geometry.micro_cells)
    for cell in candidates:
        if _point_in_polygon(point, cell.polygon):
            return cell
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda c: (
            (c.center_x - point[0]) ** 2 + (c.center_y - point[1]) ** 2,
            0 if c.region_id == region_id else 1,
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


def _route_between_nodes(
    geometry: WorldMapGeometry,
    a: RoadMapNode,
    b: RoadMapNode,
    ford_points: list[Point],
) -> _RoadPath | None:
    start_point = (a.x, a.y)
    end_point = (b.x, b.y)
    if not geometry.micro_cells:
        distance = math.hypot(a.x - b.x, a.y - b.y)
        return _RoadPath(points=_dedupe_path_points([start_point, end_point]), cost=distance)
    start = _cell_for_point(geometry, start_point, region_id=a.region_id)
    end = _cell_for_point(geometry, end_point, region_id=b.region_id)
    if start is None or end is None:
        return None
    if start.continent_id != end.continent_id:
        return None
    if start.micro_id == end.micro_id:
        return _RoadPath(
            points=_dedupe_path_points([start_point, end_point]),
            cost=max(0.0001, math.hypot(a.x - b.x, a.y - b.y)),
        )
    by_id = {cell.micro_id: cell for cell in geometry.micro_cells}
    adjacency, shared_midpoints = _micro_adjacency(geometry.micro_cells)
    queue: list[tuple[float, str]] = [(0.0, start.micro_id)]
    distances = {start.micro_id: 0.0}
    previous: dict[str, str] = {}
    while queue:
        cost, micro_id = heapq.heappop(queue)
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
                heapq.heappush(queue, (nd, neighbor_id))
    if end.micro_id not in distances:
        return None
    micro_path = [end.micro_id]
    while micro_path[-1] != start.micro_id:
        prior = previous.get(micro_path[-1])
        if prior is None:
            return None
        micro_path.append(prior)
    micro_path.reverse()
    points: list[Point] = [start_point]
    for first_id, second_id in zip(micro_path, micro_path[1:]):
        first = by_id[first_id]
        second = by_id[second_id]
        pair = tuple(sorted((first_id, second_id)))  # type: ignore[assignment]
        midpoint = shared_midpoints.get(
            pair,
            ((first.center_x + second.center_x) / 2.0, (first.center_y + second.center_y) / 2.0),
        )
        ford = None
        if _river_crossing_penalty(first, second, ford_points) > 0.0:
            ford = _nearest_ford_for_segment(
                (first.center_x, first.center_y),
                (second.center_x, second.center_y),
                ford_points,
                max_distance=_ford_snap_threshold(first, second),
            )
        points.append(ford if ford is not None else midpoint)
    points.append(end_point)
    return _RoadPath(points=_dedupe_path_points(points), cost=max(0.0001, distances[end.micro_id]))


def _path_length(points: list[Point]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:]))


def _point_polyline_distance(point: Point, points: list[Point]) -> float:
    if len(points) < 2:
        return math.hypot(point[0] - points[0][0], point[1] - points[0][1]) if points else float("inf")
    return min(_point_segment_distance(point, a, b) for a, b in zip(points, points[1:]))


def _network_route(
    segments: dict[tuple[str, str], _RoadSegment],
    start: str,
    end: str,
) -> tuple[float, list[tuple[str, str]]] | None:
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for (a, b), segment in segments.items():
        adjacency.setdefault(a, []).append((b, segment.cost))
        adjacency.setdefault(b, []).append((a, segment.cost))
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
        for other, step in adjacency.get(node_id, []):
            nd = cost + step
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
    return distances[end], pairs


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
    )
    return key


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
    max_offset = max(0.035, direct_line_distance * 0.16)
    best: tuple[float, str] | None = None
    for b_id, node in nodes.items():
        if b_id in direct_key:
            continue
        if _point_polyline_distance((node.x, node.y), direct_route.points) > max_offset:
            continue
        first = routes.get(_pair_key(a_id, b_id))
        second = routes.get(_pair_key(b_id, c_id))
        if first is None or second is None:
            continue
        ratio = (first.cost + second.cost) / max(0.0001, direct_route.cost)
        candidate = (ratio, b_id)
        if best is None or candidate < best:
            best = candidate
    if best is None:
        return None
    return (best[1], best[0])


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
        strong_actual = demand.actual_usage >= max(3.0, max_actual * 0.25)
        network = _network_route(segments, key[0], key[1])
        if network is not None:
            network_cost, network_segments = network
            network_ratio = network_cost / max(0.0001, direct_route.cost)
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
                first = _ensure_segment(segments, routes, geometry, nodes, ford_points, key[0], via_id)
                second = _ensure_segment(segments, routes, geometry, nodes, ford_points, via_id, key[1])
                if first is not None and second is not None:
                    _add_usage_to_segments(segments, (first, second), demand)
                    continue

        direct = _ensure_segment(segments, routes, geometry, nodes, ford_points, key[0], key[1])
        if direct is not None:
            _add_usage_to_segments(segments, (direct,), demand)
    return _finalize_edges(segments)
