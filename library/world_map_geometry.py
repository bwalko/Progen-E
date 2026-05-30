"""Generated world-map geometry for SVG and settlement-local geography.

The canonical geography remains the config region/route graph. This module derives
stable polygon cells and feature anchors from that graph so renderers and town
placement can share one geography layer.
"""

from __future__ import annotations

import hashlib
import heapq
import math
import random
import sqlite3
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable

from shapely.geometry import GeometryCollection
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import unary_union

from library.geography import (
    Region,
    list_continents,
    list_regions,
    list_routes_from,
    region_environment,
)


MAP_GEOMETRY_VERSION = "polygonal-v10"
Point = tuple[float, float]


@dataclass(frozen=True)
class _MicroSeed:
    region_id: str


@dataclass(frozen=True)
class _ContinentMapHint:
    size: str = "medium"
    placement: str = ""
    shape: str = ""


@dataclass(frozen=True)
class _RegionMapHint:
    features: frozenset[str] = frozenset()
    placement: str = ""


@dataclass(frozen=True)
class RegionFeature:
    feature_id: str
    region_id: str
    kind: str
    x: float
    y: float
    feature_class: str
    label: str
    importance: float = 1.0


@dataclass(frozen=True)
class RegionEdge:
    from_region_id: str
    to_region_id: str
    route_type: str
    friction: float
    points: list[Point]
    edge_class: str


@dataclass(frozen=True)
class RiverSegment:
    points: list[Point]
    micro_ids: list[str]
    region_ids: list[str]


@dataclass(frozen=True)
class RiverPath:
    river_id: str
    from_region_id: str
    to_region_id: str
    points: list[Point]
    segments: list[RiverSegment]
    flow: float
    river_class: str


@dataclass(frozen=True)
class RiverChannel:
    river_id: str
    river_class: str
    corridor_polygon: list[Point]
    bank_polygon: list[Point]
    water_polygon: list[Point]
    mouth_bank_polygon: list[Point]
    mouth_water_polygon: list[Point]
    highlight_points: list[Point]
    flow: float


@dataclass(frozen=True)
class RegionCell:
    region_id: str
    continent_id: str
    center_x: float
    center_y: float
    polygon: list[Point]
    elevation: float
    moisture: float
    ruggedness: float
    terrain_family: str
    is_coastal: bool
    feature_ids: list[str]


@dataclass(frozen=True)
class MicroRegionCell:
    micro_id: str
    region_id: str
    continent_id: str
    center_x: float
    center_y: float
    polygon: list[Point]
    elevation: float
    moisture: float
    terrain_family: str
    is_coastal: bool
    river_distance: float = 1.0
    river_flow: float = 0.0
    river_side: float = 0.0
    is_floodplain: bool = False
    is_channel: bool = False
    land_polygons: list[list[Point]] = field(default_factory=list)


@dataclass(frozen=True)
class WaterCell:
    water_id: str
    kind: str
    region_id: str
    continent_id: str
    center_x: float
    center_y: float
    polygon: list[Point]
    depth: float
    moisture_source: float = 1.0


@dataclass(frozen=True)
class WorldMapGeometry:
    world: str
    version: str
    width: float
    height: float
    cells: list[RegionCell]
    micro_cells: list[MicroRegionCell]
    features: list[RegionFeature]
    edges: list[RegionEdge]
    rivers: list[RiverPath]
    river_channels: list[RiverChannel] = field(default_factory=list)
    water_cells: list[WaterCell] = field(default_factory=list)

    def cell_by_region_id(self) -> dict[str, RegionCell]:
        return {c.region_id: c for c in self.cells}

    def features_by_region_id(self) -> dict[str, list[RegionFeature]]:
        out: dict[str, list[RegionFeature]] = {}
        for feature in self.features:
            out.setdefault(feature.region_id, []).append(feature)
        return out

    def to_json_obj(self) -> dict[str, object]:
        return {
            "world": self.world,
            "version": self.version,
            "width": self.width,
            "height": self.height,
            "cells": [asdict(c) for c in self.cells],
            "micro_cells": [asdict(c) for c in self.micro_cells],
            "features": [asdict(f) for f in self.features],
            "edges": [asdict(e) for e in self.edges],
            "rivers": [asdict(r) for r in self.rivers],
            "river_channels": [asdict(r) for r in self.river_channels],
            "water_cells": [asdict(w) for w in self.water_cells],
        }


def _stable_seed(*parts: object) -> int:
    h = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _split_flags(value: object) -> frozenset[str]:
    raw = str(value or "").replace(",", ";")
    return frozenset(p.strip().lower() for p in raw.split(";") if p.strip())


def _load_map_hints(
    *,
    world: str,
    db_path: Path | str | None,
) -> tuple[dict[str, _ContinentMapHint], dict[str, _RegionMapHint]]:
    if db_path is None:
        return {}, {}
    path = Path(db_path)
    if not path.exists():
        return {}, {}
    continent_hints: dict[str, _ContinentMapHint] = {}
    region_hints: dict[str, _RegionMapHint] = {}
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        tables = {
            str(r["name"])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        if "world_geography_continents" in tables:
            cols = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(world_geography_continents)").fetchall()
            }
            if {"map_size", "map_placement", "map_shape"}.intersection(cols):
                select_cols = [
                    "continent_id",
                    "map_size" if "map_size" in cols else "'' AS map_size",
                    "map_placement" if "map_placement" in cols else "'' AS map_placement",
                    "map_shape" if "map_shape" in cols else "'' AS map_shape",
                ]
                for row in conn.execute(
                    f"""
                    SELECT {", ".join(select_cols)}
                    FROM world_geography_continents
                    WHERE world = ?
                    """,
                    (world,),
                ):
                    cid = str(row["continent_id"] or "").strip()
                    if cid:
                        continent_hints[cid] = _ContinentMapHint(
                            size=str(row["map_size"] or "").strip().lower(),
                            placement=str(row["map_placement"] or "").strip().lower(),
                            shape=str(row["map_shape"] or "").strip().lower(),
                        )
        if "world_geography_regions" in tables:
            cols = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(world_geography_regions)").fetchall()
            }
            if {"map_features", "map_placement"}.intersection(cols):
                select_cols = [
                    "region_id",
                    "map_features" if "map_features" in cols else "'' AS map_features",
                    "map_placement" if "map_placement" in cols else "'' AS map_placement",
                ]
                for row in conn.execute(
                    f"""
                    SELECT {", ".join(select_cols)}
                    FROM world_geography_regions
                    WHERE world = ?
                    """,
                    (world,),
                ):
                    rid = str(row["region_id"] or "").strip()
                    if rid:
                        region_hints[rid] = _RegionMapHint(
                            features=_split_flags(row["map_features"]),
                            placement=str(row["map_placement"] or "").strip().lower(),
                        )
    return continent_hints, region_hints


def _world_map_seed(
    *,
    world: str,
    db_path: Path | str | None,
    save_db_path: Path | str | None = None,
    map_seed: object | None = None,
) -> str:
    if map_seed is not None and str(map_seed).strip():
        return str(map_seed).strip()
    if save_db_path is not None:
        save_path = Path(save_db_path)
        if save_path.exists():
            try:
                with sqlite3.connect(save_path) as conn:
                    row = conn.execute(
                        """
                        SELECT meta_value FROM simulation_meta
                        WHERE meta_key = 'world_map_seed'
                        """
                    ).fetchone()
                    if row is not None and row[0] is not None and str(row[0]).strip():
                        return str(row[0]).strip()
            except sqlite3.Error:
                pass
    if db_path is None:
        return world
    path = Path(db_path)
    if not path.exists():
        return world
    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            cols = {
                str(r["name"])
                for r in conn.execute("PRAGMA table_info(world_start)").fetchall()
            }
            row = conn.execute("SELECT * FROM world_start WHERE world = ? LIMIT 1", (world,)).fetchone()
            if row is not None and "map_seed" in cols and row["map_seed"]:
                return str(row["map_seed"])
    except sqlite3.Error:
        pass
    return world


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _smoothstep(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _noise_seed(*parts: object) -> int:
    return _stable_seed(MAP_GEOMETRY_VERSION, "noise", *parts) & 0xFFFFFFFF


def _mixed_unit_float(seed: int, x: int, y: int) -> float:
    n = (seed + x * 0x9E3779B1 + y * 0x85EBCA77) & 0xFFFFFFFF
    n ^= n >> 16
    n = (n * 0x7FEB352D) & 0xFFFFFFFF
    n ^= n >> 15
    n = (n * 0x846CA68B) & 0xFFFFFFFF
    n ^= n >> 16
    return n / 0xFFFFFFFF


def _value_noise_2d(seed: object, x: float, y: float) -> float:
    """Stable grid value noise in [0, 1], smoothed between integer lattice points."""
    base_seed = seed if isinstance(seed, int) else _noise_seed(seed)
    x0 = math.floor(x)
    y0 = math.floor(y)
    tx = _smoothstep(x - x0)
    ty = _smoothstep(y - y0)

    def lattice(ix: int, iy: int) -> float:
        return _mixed_unit_float(base_seed, ix, iy)

    a = _lerp(lattice(x0, y0), lattice(x0 + 1, y0), tx)
    b = _lerp(lattice(x0, y0 + 1), lattice(x0 + 1, y0 + 1), tx)
    return _lerp(a, b, ty)


def _fbm_noise_2d(
    seed: object,
    x: float,
    y: float,
    *,
    octaves: int = 4,
    lacunarity: float = 2.0,
    gain: float = 0.52,
) -> float:
    base_seed = seed if isinstance(seed, int) else _noise_seed(seed)
    value = 0.0
    amplitude = 1.0
    total = 0.0
    freq = 1.0
    for octave in range(max(1, octaves)):
        octave_seed = (base_seed + octave * 0x9E3779B9) & 0xFFFFFFFF
        value += _value_noise_2d(octave_seed, x * freq, y * freq) * amplitude
        total += amplitude
        amplitude *= gain
        freq *= lacunarity
    return value / max(1e-9, total)


def _centroid(poly: list[Point]) -> Point:
    if not poly:
        return (0.5, 0.5)
    return (
        sum(p[0] for p in poly) / len(poly),
        sum(p[1] for p in poly) / len(poly),
    )


def _clip_half_plane(poly: list[Point], a: float, b: float, c: float) -> list[Point]:
    """Clip ``poly`` to points satisfying ``a*x + b*y <= c``."""
    if not poly:
        return []
    out: list[Point] = []

    def inside(p: Point) -> bool:
        return a * p[0] + b * p[1] <= c + 1e-9

    def intersection(p: Point, q: Point) -> Point:
        pv = a * p[0] + b * p[1] - c
        qv = a * q[0] + b * q[1] - c
        denom = pv - qv
        if abs(denom) < 1e-12:
            return q
        t = _clamp(pv / denom, 0.0, 1.0)
        return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)

    prev = poly[-1]
    prev_inside = inside(prev)
    for cur in poly:
        cur_inside = inside(cur)
        if cur_inside:
            if not prev_inside:
                out.append(intersection(prev, cur))
            out.append(cur)
        elif prev_inside:
            out.append(intersection(prev, cur))
        prev = cur
        prev_inside = cur_inside
    return out


def _bbox_polygon(x0: float, y0: float, x1: float, y1: float) -> list[Point]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _polygon_bounds(poly: list[Point]) -> tuple[float, float, float, float]:
    if not poly:
        return (0.0, 0.0, 1.0, 1.0)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (min(xs), min(ys), max(xs), max(ys))


def _point_in_polygon(point: Point, poly: list[Point]) -> bool:
    if len(poly) < 3:
        return False
    x, y = point
    inside = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            denom = yj - yi
            if abs(denom) > 1e-12 and x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def _representative_point_in_polygon(poly: list[Point]) -> Point:
    center = _centroid(poly)
    if not poly or _point_in_polygon(center, poly):
        return center
    x0, y0, x1, y1 = _polygon_bounds(poly)
    candidates: list[Point] = []
    for ix in range(1, 8):
        for iy in range(1, 8):
            candidates.append((x0 + (x1 - x0) * ix / 8.0, y0 + (y1 - y0) * iy / 8.0))
    candidates.extend(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0) for a, b in zip(poly, poly[1:] + poly[:1]))
    for point in sorted(
        candidates,
        key=lambda p: ((p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2, p[0], p[1]),
    ):
        if _point_in_polygon(point, poly):
            return point
    return center


def _point_segment_distance(p: Point, a: Point, b: Point) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    vx = bx - ax
    vy = by - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = _clamp(((px - ax) * vx + (py - ay) * vy) / denom, 0.0, 1.0)
    qx = ax + vx * t
    qy = ay + vy * t
    return math.hypot(px - qx, py - qy)


def _nearest_point_on_segment(p: Point, a: Point, b: Point) -> Point:
    px, py = p
    ax, ay = a
    bx, by = b
    vx = bx - ax
    vy = by - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return a
    t = _clamp(((px - ax) * vx + (py - ay) * vy) / denom, 0.0, 1.0)
    return (ax + vx * t, ay + vy * t)


def _nearest_point_on_polygon_edge(point: Point, poly: list[Point]) -> Point:
    if len(poly) < 2:
        return point
    return min(
        (_nearest_point_on_segment(point, poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))),
        key=lambda p: ((p[0] - point[0]) ** 2 + (p[1] - point[1]) ** 2, p[0], p[1]),
    )


def _distance_to_polygon_edge(point: Point, poly: list[Point]) -> float:
    if len(poly) < 2:
        return 0.0
    return min(
        _point_segment_distance(point, poly[i], poly[(i + 1) % len(poly)])
        for i in range(len(poly))
    )


def _convex_hull(points: list[Point]) -> list[Point]:
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[Point] = []
    for p in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[Point] = []
    for p in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _pull_inside(point: Point, poly: list[Point], center: Point | None = None) -> Point:
    if not poly or _point_in_polygon(point, poly):
        return point
    cx, cy = center if center is not None else _centroid(poly)
    x, y = point
    for _ in range(18):
        x = (x + cx) / 2.0
        y = (y + cy) / 2.0
        if _point_in_polygon((x, y), poly):
            return (x, y)
    return (cx, cy)


def _nudge_inside_polygon(point: Point, poly: list[Point], *, min_edge_distance: float = 0.004) -> Point:
    if not poly or not _point_in_polygon(point, poly):
        return point
    if _distance_to_polygon_edge(point, poly) >= min_edge_distance:
        return point
    anchor = _representative_point_in_polygon(poly)
    x, y = point
    best = point
    best_distance = _distance_to_polygon_edge(point, poly)
    for _ in range(12):
        x = x * 0.68 + anchor[0] * 0.32
        y = y * 0.68 + anchor[1] * 0.32
        candidate = (x, y)
        if not _point_in_polygon(candidate, poly):
            continue
        distance = _distance_to_polygon_edge(candidate, poly)
        if distance > best_distance:
            best = candidate
            best_distance = distance
        if distance >= min_edge_distance:
            return candidate
    return best


def _interior_point_near_polygon(point: Point, poly: list[Point]) -> Point:
    if not poly:
        return point
    anchor = _representative_point_in_polygon(poly)
    if _point_in_polygon(point, poly):
        return _nudge_inside_polygon(point, poly)
    edge_point = _nearest_point_on_polygon_edge(point, poly)
    interior = (edge_point[0] * 0.62 + anchor[0] * 0.38, edge_point[1] * 0.62 + anchor[1] * 0.38)
    if _point_in_polygon(interior, poly):
        return _nudge_inside_polygon(interior, poly)
    if _point_in_polygon(anchor, poly):
        return _nudge_inside_polygon(anchor, poly)
    return _nudge_inside_polygon(_pull_inside(interior, poly, anchor), poly)


def _best_region_interior_point(point: Point, cells: list[MicroRegionCell]) -> Point:
    candidates: list[tuple[float, float, Point]] = []
    for cell in cells:
        candidate = _interior_point_near_polygon(point, cell.polygon)
        if not _point_in_polygon(candidate, cell.polygon):
            continue
        edge_distance = _distance_to_polygon_edge(candidate, cell.polygon)
        travel_distance = (candidate[0] - point[0]) ** 2 + (candidate[1] - point[1]) ** 2
        safety_penalty = max(0.0, 0.004 - edge_distance) ** 2 * 96.0
        candidates.append((travel_distance + safety_penalty, -edge_distance, candidate))
    if not candidates:
        return point
    return min(candidates, key=lambda c: (c[0], c[1], c[2][0], c[2][1]))[2]


def _sample_point_in_polygon(
    rng: random.Random,
    poly: list[Point],
    *,
    center: Point | None = None,
) -> Point:
    x0, y0, x1, y1 = _polygon_bounds(poly)
    for _ in range(80):
        p = (rng.uniform(x0, x1), rng.uniform(y0, y1))
        if _point_in_polygon(p, poly):
            return p
    return center if center is not None else _centroid(poly)


def _continent_boxes(continent_ids: list[str]) -> dict[str, tuple[float, float, float, float]]:
    n = max(1, len(continent_ids))
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    margin = 0.04
    gap = 0.04
    usable_w = 1.0 - margin * 2.0 - gap * max(0, cols - 1)
    usable_h = 1.0 - margin * 2.0 - gap * max(0, rows - 1)
    cell_w = usable_w / cols
    cell_h = usable_h / rows
    boxes: dict[str, tuple[float, float, float, float]] = {}
    for i, cid in enumerate(continent_ids):
        col = i % cols
        row = i // cols
        x0 = margin + col * (cell_w + gap)
        y0 = margin + row * (cell_h + gap)
        boxes[cid] = (x0, y0, x0 + cell_w, y0 + cell_h)
    return boxes


def _box_center(box: tuple[float, float, float, float]) -> Point:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _move_box(
    box: tuple[float, float, float, float],
    dx: float,
    dy: float,
    *,
    lo: float = 0.04,
    hi: float = 0.96,
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    w = x1 - x0
    h = y1 - y0
    nx0 = _clamp(x0 + dx, lo, hi - w)
    ny0 = _clamp(y0 + dy, lo, hi - h)
    return (nx0, ny0, nx0 + w, ny0 + h)


def _smooth_closed_polygon(poly: list[Point], *, iterations: int = 1) -> list[Point]:
    """Round off a closed polygon with Chaikin corner cutting."""
    out = list(poly)
    for _ in range(max(0, iterations)):
        if len(out) < 3:
            return out
        smoothed: list[Point] = []
        for a, b in zip(out, out[1:] + out[:1]):
            smoothed.append((a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25))
            smoothed.append((a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75))
        out = smoothed
    return out


def _fit_polygon_to_box(
    poly: list[Point],
    box: tuple[float, float, float, float],
    *,
    margin_ratio: float = 0.035,
) -> list[Point]:
    if not poly:
        return poly
    x0, y0, x1, y1 = box
    bw = x1 - x0
    bh = y1 - y0
    lo_x = x0 + bw * margin_ratio
    hi_x = x1 - bw * margin_ratio
    lo_y = y0 + bh * margin_ratio
    hi_y = y1 - bh * margin_ratio
    px0, py0, px1, py1 = _polygon_bounds(poly)
    scale = min(
        1.0,
        (hi_x - lo_x) / max(1e-9, px1 - px0),
        (hi_y - lo_y) / max(1e-9, py1 - py0),
    )
    cx, cy = _centroid(poly)
    fit = [(cx + (x - cx) * scale, cy + (y - cy) * scale) for x, y in poly]
    fx0, fy0, fx1, fy1 = _polygon_bounds(fit)
    dx = 0.0
    dy = 0.0
    if fx0 < lo_x:
        dx = lo_x - fx0
    elif fx1 > hi_x:
        dx = hi_x - fx1
    if fy0 < lo_y:
        dy = lo_y - fy0
    elif fy1 > hi_y:
        dy = hi_y - fy1
    return [(x + dx, y + dy) for x, y in fit]


def _separate_continent_boxes(
    boxes: dict[str, tuple[float, float, float, float]],
    *,
    gap: float = 0.035,
) -> dict[str, tuple[float, float, float, float]]:
    out = dict(boxes)
    ids = sorted(out)
    for _ in range(80):
        moved = False
        for i, aid in enumerate(ids):
            for bid in ids[i + 1:]:
                ax0, ay0, ax1, ay1 = out[aid]
                bx0, by0, bx1, by1 = out[bid]
                overlap_x = min(ax1, bx1) - max(ax0, bx0) + gap
                overlap_y = min(ay1, by1) - max(ay0, by0) + gap
                if overlap_x <= 0.0 or overlap_y <= 0.0:
                    continue
                acx, acy = _box_center(out[aid])
                bcx, bcy = _box_center(out[bid])
                if overlap_x < overlap_y:
                    direction = -1.0 if acx <= bcx else 1.0
                    shift = overlap_x / 2.0 + 0.001
                    out[aid] = _move_box(out[aid], direction * shift, 0.0)
                    out[bid] = _move_box(out[bid], -direction * shift, 0.0)
                else:
                    direction = -1.0 if acy <= bcy else 1.0
                    shift = overlap_y / 2.0 + 0.001
                    out[aid] = _move_box(out[aid], 0.0, direction * shift)
                    out[bid] = _move_box(out[bid], 0.0, -direction * shift)
                moved = True
        if not moved:
            break
    return out


def _continent_layout_boxes(
    continents: list[object],
    hints: dict[str, _ContinentMapHint],
    map_seed: str,
) -> dict[str, tuple[float, float, float, float]]:
    size_scale = {
        "huge": 0.56,
        "large": 0.46,
        "medium": 0.35,
        "small": 0.26,
        "island": 0.18,
    }
    anchors = {
        "northwest": (0.28, 0.27),
        "north": (0.50, 0.24),
        "northeast": (0.73, 0.25),
        "west": (0.24, 0.50),
        "central": (0.50, 0.50),
        "east": (0.76, 0.50),
        "southwest": (0.33, 0.72),
        "south": (0.53, 0.75),
        "southeast": (0.73, 0.72),
    }
    boxes: dict[str, tuple[float, float, float, float]] = {}
    used_centers: list[Point] = []
    for i, continent in enumerate(continents):
        cid = str(getattr(continent, "continent_id", ""))
        hint = hints.get(cid, _ContinentMapHint())
        rng = random.Random(_stable_seed(MAP_GEOMETRY_VERSION, map_seed, cid, "layout"))
        cx, cy = anchors.get(hint.placement, (0.24 + 0.52 * rng.random(), 0.22 + 0.56 * rng.random()))
        cx += rng.uniform(-0.055, 0.055)
        cy += rng.uniform(-0.055, 0.055)
        for ox, oy in used_centers:
            dx = cx - ox
            dy = cy - oy
            d = max(1e-6, math.hypot(dx, dy))
            if d < 0.28:
                push = (0.28 - d) * 0.72
                cx += dx / d * push
                cy += dy / d * push
        scale = size_scale.get(hint.size, size_scale["medium"])
        aspect = rng.uniform(0.78, 1.38)
        if "rift" in hint.shape or "littoral" in hint.shape:
            aspect *= 1.25
        if "fjord" in hint.shape or "shield" in hint.shape:
            aspect *= 1.08
        w = scale * math.sqrt(aspect)
        h = scale / math.sqrt(aspect) * rng.uniform(0.78, 1.08)
        cx = _clamp(cx, 0.08 + w / 2, 0.92 - w / 2)
        cy = _clamp(cy, 0.08 + h / 2, 0.92 - h / 2)
        used_centers.append((cx, cy))
        boxes[cid] = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        _ = i
    return _separate_continent_boxes(boxes)


def _mask_continent_hull(
    *,
    world: str,
    continent_id: str,
    box: tuple[float, float, float, float],
    map_seed: str,
    hint: _ContinentMapHint,
) -> list[Point]:
    """Build a Red Blob-style land/water mask contour instead of perturbing a radius."""
    from shapely.geometry import MultiPolygon, box as shapely_box
    from shapely.ops import unary_union

    x0, y0, x1, y1 = box
    bw = x1 - x0
    bh = y1 - y0
    grid = 58
    dx = bw / grid
    dy = bh / grid
    seed = _noise_seed(map_seed, world, continent_id, "land-water-mask")
    text = f"{hint.shape} {hint.size} {hint.placement}".lower()
    target_area = 0.44
    if "island" in text:
        target_area = 0.36
    elif "huge" in text or "large" in text:
        target_area = 0.48

    def score_at(qx: float, qy: float) -> float:
        warp_x = _fbm_noise_2d((seed + 0xA511E9B3) & 0xFFFFFFFF, qx * 1.35 + 8.0, qy * 1.35 - 5.0, octaves=3)
        warp_y = _fbm_noise_2d((seed + 0x63D83595) & 0xFFFFFFFF, qx * 1.35 - 6.0, qy * 1.35 + 9.0, octaves=3)
        wx = qx + (warp_x - 0.5) * 0.42
        wy = qy + (warp_y - 0.5) * 0.42
        return _fbm_noise_2d(seed, wx * 1.55 + 12.0, wy * 1.55 - 14.0, octaves=5)

    def build_polygon(threshold_bias: float) -> tuple[float, list[Point]]:
        cells = []
        for gy in range(grid):
            qy = -1.0 + 2.0 * (gy + 0.5) / grid
            for gx in range(grid):
                qx = -1.0 + 2.0 * (gx + 0.5) / grid
                if max(abs(qx), abs(qy)) > 0.985:
                    continue
                dist2 = qx * qx + qy * qy
                threshold = 0.30 + 0.31 * dist2 + threshold_bias
                if score_at(qx, qy) <= threshold:
                    continue
                cells.append(shapely_box(x0 + gx * dx, y0 + gy * dy, x0 + (gx + 1) * dx, y0 + (gy + 1) * dy))
        if not cells:
            return (float("inf"), [])
        land = unary_union(cells)
        if isinstance(land, MultiPolygon):
            land = max(land.geoms, key=lambda p: p.area)
        if land.is_empty or land.area <= bw * bh * 0.12:
            return (float("inf"), [])
        soften = min(dx, dy) * 0.72
        # Close tiny straits and one-cell isthmuses before tracing the exterior.
        # Without this, the noise mask can produce C-shaped hooks or necklace-like
        # chains of cells that look more like rendering artifacts than continents.
        land = land.buffer(soften * 2.2, join_style=1).buffer(-soften * 1.7, join_style=1)
        if isinstance(land, MultiPolygon):
            land = max(land.geoms, key=lambda p: p.area)
        if land.is_empty:
            return (float("inf"), [])
        land = land.simplify(min(dx, dy) * 0.44, preserve_topology=True)
        coords = [(float(x), float(y)) for x, y in list(land.exterior.coords)[:-1]]
        if len(coords) < 12:
            return (float("inf"), [])
        px0, py0, px1, py1 = _polygon_bounds(coords)
        area_ratio = land.area / max(1e-9, bw * bh)
        width_ratio = (px1 - px0) / max(1e-9, bw)
        height_ratio = (py1 - py0) / max(1e-9, bh)
        convex_area = max(1e-9, land.convex_hull.area)
        convex_fill = land.area / convex_area
        compactness = 4.0 * math.pi * land.area / max(1e-9, land.length * land.length)
        if convex_fill < 0.52 or compactness < 0.18:
            return (float("inf"), [])
        quality = (
            abs(area_ratio - target_area)
            + max(0.0, 0.66 - width_ratio) * 0.7
            + max(0.0, 0.62 - height_ratio) * 0.7
            + max(0.0, 0.68 - convex_fill) * 1.25
            + max(0.0, 0.28 - compactness) * 1.65
        )
        poly = _fit_polygon_to_box(_smooth_closed_polygon(coords, iterations=1), box)
        return (quality, [(round(x, 6), round(y, 6)) for x, y in poly])

    candidates = [
        build_polygon(bias)
        for bias in (0.00, -0.04, 0.04, -0.08, 0.08, -0.12, 0.12)
    ]
    return min(candidates, key=lambda item: item[0])[1]


def _continent_hull(
    continent: object,
    box: tuple[float, float, float, float],
    map_seed: str,
    hint: _ContinentMapHint | None = None,
) -> list[Point]:
    """Return a stable, noise-shaped land silhouette inside a layout box."""
    cid = str(getattr(continent, "continent_id", ""))
    world = str(getattr(continent, "world", ""))
    name = str(getattr(continent, "continent_name", ""))
    keywords = str(getattr(continent, "keywords", ""))
    hint = hint or _ContinentMapHint()
    text = f"{name} {keywords} {hint.shape}".lower()
    mask_hull = _mask_continent_hull(
        world=world,
        continent_id=cid,
        box=box,
        map_seed=map_seed,
        hint=hint,
    )
    if mask_hull:
        return mask_hull
    rng = random.Random(_stable_seed(MAP_GEOMETRY_VERSION, map_seed, world, cid, "continent-hull"))
    x0, y0, x1, y1 = box
    bw = x1 - x0
    bh = y1 - y0
    cx = (x0 + x1) / 2.0 + rng.uniform(-0.05, 0.05) * bw
    cy = (y0 + y1) / 2.0 + rng.uniform(-0.05, 0.05) * bh
    rx = bw * rng.uniform(0.34, 0.42)
    ry = bh * rng.uniform(0.33, 0.41)
    if "maritime" in text or "coastal" in text or "atlantic" in text:
        rx *= 1.12
        ry *= 0.92
    if "craton" in text or "shield" in text:
        rx *= 0.96
        ry *= 1.08
    if "arid" in text or "aridity" in text:
        rx *= 1.08
        ry *= 0.88
    angle = rng.uniform(-0.58, 0.58)
    if "rift" in text:
        angle += rng.choice([-0.42, 0.42])
    vertices = 96
    pts: list[Point] = []
    phase1 = rng.uniform(0.0, math.tau)
    phase2 = rng.uniform(0.0, math.tau)
    phase3 = rng.uniform(0.0, math.tau)
    coast_seed = _noise_seed(map_seed, world, cid, "continent-coast-noise")
    for i in range(vertices):
        theta = math.tau * i / vertices
        nx = math.cos(theta + phase1)
        ny = math.sin(theta + phase1)
        broad = _fbm_noise_2d(coast_seed, nx * 0.92 + 7.0, ny * 0.92 - 3.0, octaves=3)
        detail = _fbm_noise_2d((coast_seed + 0xA511E9B3) & 0xFFFFFFFF, nx * 1.85 - 11.0, ny * 1.85 + 5.0, octaves=2)
        inlet = _fbm_noise_2d((coast_seed + 0x63D83595) & 0xFFFFFFFF, nx * 2.7 + 17.0, ny * 2.7 - 13.0, octaves=2)
        inlet_cut = max(0.0, inlet - 0.70) * 0.12
        lump = (
            1.0
            + 0.18 * math.sin(theta * 2.0 + phase1)
            + 0.08 * math.sin(theta * 3.0 + phase2)
            + 0.012 * math.sin(theta * 5.0 + phase3)
            + (broad - 0.5) * 0.25
            + (detail - 0.5) * 0.055
            - inlet_cut
        )
        if "fjord" in text and i % 5 == 2:
            lump *= rng.uniform(0.84, 0.94)
        elif "maritime" in text and i % 8 in {2, 5}:
            lump *= rng.uniform(0.88, 0.97)
        elif "rift" in text and math.sin(theta + phase1) > 0.72:
            lump *= rng.uniform(0.86, 0.96)
        lump = _clamp(lump, 0.78, 1.24)
        px = math.cos(theta) * rx * lump
        py = math.sin(theta) * ry * lump
        ca = math.cos(angle)
        sa = math.sin(angle)
        x = cx + px * ca - py * sa
        y = cy + px * sa + py * ca
        pts.append((x, y))
    return [
        (round(x, 6), round(y, 6))
        for x, y in _fit_polygon_to_box(_smooth_closed_polygon(pts, iterations=3), box)
    ]


def _continent_hulls(
    continents: list[object],
    hints: dict[str, _ContinentMapHint] | None = None,
    map_seed: str = "default",
) -> dict[str, list[Point]]:
    hints = hints or {}
    boxes = _continent_layout_boxes(continents, hints, map_seed)
    return {
        str(getattr(c, "continent_id", "")): _continent_hull(
            c,
            boxes[str(getattr(c, "continent_id", ""))],
            map_seed,
            hints.get(str(getattr(c, "continent_id", ""))),
        )
        for c in continents
        if str(getattr(c, "continent_id", "")) in boxes
    }


def _region_text(region: Region) -> str:
    return " ".join(
        str(part or "").lower()
        for part in (region.region_name, region.biome, region.terrain, region.keywords)
    )


def _terrain_family(region: Region) -> str:
    text = _region_text(region)
    terrain = (region.terrain or "").lower()
    biome = (region.biome or "").lower()
    if "coast" in terrain or "coastal" in biome or any(t in text for t in ("shore", "port", "littoral", "fjord", "bay", "delta")):
        return "coast"
    if "river" in terrain or any(t in text for t in ("river", "stream", "floodplain", "channel", "delta")):
        return "riverland"
    if any(t in terrain for t in ("highland", "mountain", "plateau")) or any(t in text for t in ("range", "ridge", "alps", "cordillera", "escarpment")):
        return "highlands"
    if "forest" in terrain or "forest" in text or "taiga" in biome:
        return "forest"
    if "arid" in biome or any(t in text for t in ("desert", "steppe", "oasis", "wadi", "salt")):
        return "drylands"
    return "plains"


def _feature_class(kind: str) -> str:
    k = (kind or "").lower()
    if k in {"coast", "bay", "harbor"}:
        return "coast"
    if k in {"river", "stream", "ford", "wadi", "lake", "marsh", "spring"}:
        return "water"
    if k in {"ridge", "mountain", "pass", "mesa"}:
        return "relief"
    if k in {"forest", "clearing"}:
        return "vegetation"
    if k in {"well", "meadow", "hill"}:
        return "settlement_support"
    return "landform"


def _initial_region_point(region: Region, hull: list[Point], *, map_seed: str) -> Point:
    x0, y0, x1, y1 = _polygon_bounds(hull)
    center = _centroid(hull)
    rng = random.Random(_stable_seed(MAP_GEOMETRY_VERSION, map_seed, region.world, region.region_id, "seed-point"))
    x, y = _sample_point_in_polygon(rng, hull, center=center)
    text = _region_text(region)
    if "coast" in text or "port" in text or "shore" in text or "littoral" in text:
        vertex = hull[_stable_seed(MAP_GEOMETRY_VERSION, map_seed, region.region_id, "coast-vertex") % len(hull)]
        x = center[0] + (vertex[0] - center[0]) * rng.uniform(0.72, 0.92)
        y = center[1] + (vertex[1] - center[1]) * rng.uniform(0.72, 0.92)
    if "highland" in text or "mountain" in text or "range" in text or "ridge" in text:
        y = y0 + (y1 - y0) * _clamp((y - y0) / max(1e-9, y1 - y0) * 0.78, 0.18, 0.72)
    if "river" in text or "delta" in text:
        x = x0 + (x1 - x0) * _clamp((x - x0) / max(1e-9, x1 - x0), 0.24, 0.76)
    return _pull_inside((x, y), hull, center=center)


def _undirected_intra_edges(
    regions: list[Region], *, world: str, db_path: Path | str | None
) -> set[tuple[str, str]]:
    region_ids = {r.region_id for r in regions}
    edges: set[tuple[str, str]] = set()
    for region in regions:
        for route in list_routes_from(region.region_id, world=world, db_path=db_path):
            if route.to_region_id in region_ids:
                a, b = sorted((region.region_id, route.to_region_id))
                if a != b:
                    edges.add((a, b))
    return edges


def _relax_points(
    regions: list[Region],
    points: dict[str, Point],
    edges: set[tuple[str, str]],
    hull: list[Point],
) -> dict[str, Point]:
    if len(regions) <= 1:
        return points
    x0, y0, x1, y1 = _polygon_bounds(hull)
    center = _centroid(hull)
    ids = [r.region_id for r in regions]
    pad_x = (x1 - x0) * 0.08
    pad_y = (y1 - y0) * 0.08
    edge_target = 0.22 * min(x1 - x0, y1 - y0)
    all_pairs = [(ids[i], ids[j]) for i in range(len(ids)) for j in range(i + 1, len(ids))]
    for _ in range(90):
        dx = {rid: 0.0 for rid in ids}
        dy = {rid: 0.0 for rid in ids}
        for a, b in all_pairs:
            ax, ay = points[a]
            bx, by = points[b]
            vx, vy = ax - bx, ay - by
            dist2 = max(1e-6, vx * vx + vy * vy)
            force = 0.00045 / dist2
            dx[a] += vx * force
            dy[a] += vy * force
            dx[b] -= vx * force
            dy[b] -= vy * force
        for a, b in edges:
            ax, ay = points[a]
            bx, by = points[b]
            vx, vy = bx - ax, by - ay
            dist = max(1e-6, math.hypot(vx, vy))
            force = (dist - edge_target) * 0.018
            ux, uy = vx / dist, vy / dist
            dx[a] += ux * force
            dy[a] += uy * force
            dx[b] -= ux * force
            dy[b] -= uy * force
        for rid in ids:
            x, y = points[rid]
            candidate = (
                _clamp(x + dx[rid], x0 + pad_x, x1 - pad_x),
                _clamp(y + dy[rid], y0 + pad_y, y1 - pad_y),
            )
            points[rid] = _pull_inside(candidate, hull, center=center)
    return points


def _voronoi_cells(
    regions: list[Region],
    points: dict[str, Point],
    base_poly: list[Point],
) -> dict[str, list[Point]]:
    cells: dict[str, list[Point]] = {}
    base = list(base_poly)
    bounds = _polygon_bounds(base)
    for region in regions:
        px, py = points[region.region_id]
        poly = list(base)
        for other in regions:
            if other.region_id == region.region_id:
                continue
            ox, oy = points[other.region_id]
            # Keep points closer to p than o: 2*(o-p).q <= |o|^2 - |p|^2
            a = 2.0 * (ox - px)
            b = 2.0 * (oy - py)
            c = ox * ox + oy * oy - px * px - py * py
            poly = _clip_half_plane(poly, a, b, c)
            if len(poly) < 3:
                break
        if len(poly) < 3:
            cx, cy = points[region.region_id]
            d = min(bounds[2] - bounds[0], bounds[3] - bounds[1]) * 0.035
            poly = _bbox_polygon(cx - d, cy - d, cx + d, cy + d)
        cells[region.region_id] = poly
    return cells


def _point_toward(poly: list[Point], center: Point, tx: float, ty: float, amount: float) -> Point:
    cx, cy = center
    return (cx + (tx - cx) * amount, cy + (ty - cy) * amount)


def _region_feature_kinds(region: Region) -> list[str]:
    text = _region_text(region)
    kinds: list[str] = []
    terrain = (region.terrain or "").lower()
    biome = (region.biome or "").lower()
    if "coast" in terrain or "coastal" in biome or any(t in text for t in ("shore", "port", "littoral", "fjord", "bay", "delta")):
        kinds.extend(["coast", "harbor" if "harbor" in text or "port" in text else "bay"])
    if "river" in terrain or "river" in text or "stream" in text or "delta" in text:
        kinds.extend(["stream", "ford"])
    if "highland" in terrain or "mountain" in text or "range" in text or "ridge" in text:
        kinds.extend(["ridge", "spring"])
    if "forest" in terrain or "forest" in text or "taiga" in biome:
        kinds.extend(["forest", "clearing"])
    if "arid" in biome or "oasis" in text or "wadi" in text:
        kinds.extend(["wadi", "well"])
    if "lake" in text or "tarn" in text:
        kinds.append("lake")
    if "bog" in text or "muskeg" in text or "marsh" in text:
        kinds.append("marsh")
    if not kinds:
        kinds.extend(["meadow", "hill"])
    out: list[str] = []
    for kind in kinds:
        if kind not in out:
            out.append(kind)
    return out[:5]


def _feature_anchor_cell(
    kind: str,
    cells: list[MicroRegionCell],
    rng: random.Random,
    used_micro_ids: set[str] | None = None,
) -> MicroRegionCell | None:
    if not cells:
        return None
    used_micro_ids = used_micro_ids or set()
    available = [c for c in cells if c.micro_id not in used_micro_ids] or cells
    k = (kind or "").lower()
    if k == "harbor":
        pool = [c for c in available if c.is_coastal]
        if not pool:
            return None
        return min(pool, key=lambda c: (c.elevation, c.micro_id))
    if k in {"coast", "bay"}:
        pool = [c for c in available if c.is_coastal] or available
        return min(pool, key=lambda c: (c.elevation, c.micro_id))
    if k in {"river", "stream", "ford", "lake", "marsh", "spring"}:
        pool = [c for c in available if c.moisture >= 0.78] or available
        return max(pool, key=lambda c: (c.moisture, -c.elevation, c.micro_id))
    if k in {"ridge", "mountain", "pass", "mesa"}:
        return max(available, key=lambda c: (c.elevation, c.micro_id))
    if k in {"forest", "clearing"}:
        pool = [c for c in available if c.moisture >= 0.58 and c.elevation < 0.74] or available
        return max(pool, key=lambda c: (c.moisture, -abs(c.elevation - 0.45), c.micro_id))
    if k in {"well", "wadi"}:
        pool = [c for c in available if not c.is_coastal] or available
        return min(pool, key=lambda c: (c.moisture, c.micro_id))
    if k in {"meadow", "hill"}:
        pool = [c for c in available if 0.24 <= c.elevation <= 0.68] or available
        return min(pool, key=lambda c: (abs(c.elevation - 0.42), c.micro_id))
    return available[int(rng.random() * len(available)) % len(available)]


def _features_for_region(
    region: Region,
    poly: list[Point],
    center: Point,
    micro_cells: list[MicroRegionCell] | None = None,
    *,
    map_seed: str,
) -> list[RegionFeature]:
    rng = random.Random(_stable_seed(MAP_GEOMETRY_VERSION, map_seed, region.world, region.region_id, "features"))
    vertices = poly or [center]
    features: list[RegionFeature] = []
    used_micro_ids: set[str] = set()
    for i, kind in enumerate(_region_feature_kinds(region)):
        anchor = _feature_anchor_cell(kind, micro_cells or [], rng, used_micro_ids)
        if anchor is None and kind == "harbor":
            continue
        if anchor is not None:
            used_micro_ids.add(anchor.micro_id)
            x, y = anchor.center_x, anchor.center_y
            x += rng.uniform(-0.006, 0.006)
            y += rng.uniform(-0.006, 0.006)
        else:
            target = vertices[(i * 2 + _stable_seed(MAP_GEOMETRY_VERSION, map_seed, region.region_id, kind) % len(vertices)) % len(vertices)]
            amount = 0.34 + 0.28 * rng.random()
            x, y = _point_toward(poly, center, target[0], target[1], amount)
            x += rng.uniform(-0.015, 0.015)
            y += rng.uniform(-0.015, 0.015)
        features.append(
            RegionFeature(
                feature_id=f"{region.region_id}:wf{i}",
                region_id=region.region_id,
                kind=kind,
                x=_clamp(x, 0.0, 1.0),
                y=_clamp(y, 0.0, 1.0),
                feature_class=_feature_class(kind),
                label=kind.replace("_", " ").title(),
                importance=max(0.35, 1.0 - i * 0.12),
            )
        )
    return features


def _generated_terrain_family(*, elevation: float, moisture: float, coastal: bool) -> str:
    if coastal:
        return "coast"
    if elevation >= 0.70 or (elevation >= 0.62 and moisture <= 0.42):
        return "highlands"
    if moisture >= 0.84 and elevation <= 0.40:
        return "riverland"
    if moisture >= 0.68:
        return "forest"
    if moisture <= 0.32 and elevation <= 0.66:
        return "drylands"
    return "plains"


def _finite_voronoi_regions(points: object, radius: float) -> tuple[list[list[int]], object]:
    import numpy as np
    from scipy.spatial import Voronoi

    pts = np.asarray(points, dtype=float)
    vor = Voronoi(pts)
    new_regions: list[list[int]] = []
    new_vertices = vor.vertices.tolist()
    center = pts.mean(axis=0)
    all_ridges: dict[int, list[tuple[int, int, int]]] = {}
    for (p1, p2), (v1, v2) in zip(vor.ridge_points, vor.ridge_vertices):
        all_ridges.setdefault(int(p1), []).append((int(p2), int(v1), int(v2)))
        all_ridges.setdefault(int(p2), []).append((int(p1), int(v1), int(v2)))

    for p1, region_idx in enumerate(vor.point_region):
        vertices = vor.regions[region_idx]
        if vertices and all(v >= 0 for v in vertices):
            new_regions.append([int(v) for v in vertices])
            continue
        ridges = all_ridges.get(p1, [])
        new_region = [int(v) for v in vertices if v >= 0]
        for p2, v1, v2 in ridges:
            if v2 < 0:
                v1, v2 = v2, v1
            if v1 >= 0:
                continue
            tangent = pts[p2] - pts[p1]
            norm = np.linalg.norm(tangent)
            if norm <= 1e-12:
                continue
            tangent /= norm
            normal = np.array([-tangent[1], tangent[0]])
            midpoint = pts[[p1, p2]].mean(axis=0)
            direction = np.sign(np.dot(midpoint - center, normal)) * normal
            far = vor.vertices[v2] + direction * radius
            new_region.append(len(new_vertices))
            new_vertices.append(far.tolist())
        vs = np.asarray([new_vertices[v] for v in new_region])
        c = vs.mean(axis=0)
        angles = np.arctan2(vs[:, 1] - c[1], vs[:, 0] - c[0])
        new_regions.append([v for _, v in sorted(zip(angles, new_region))])
    return new_regions, np.asarray(new_vertices)


def _build_voronoi_polys_in_hull(
    *,
    points: list[Point],
    hull: list[Point],
) -> list[list[Point]]:
    from shapely.geometry import MultiPolygon, Polygon

    hull_poly = Polygon(hull)
    if not hull_poly.is_valid:
        hull_poly = hull_poly.buffer(0)
    if hull_poly.is_empty:
        return []
    radius = max(hull_poly.bounds[2] - hull_poly.bounds[0], hull_poly.bounds[3] - hull_poly.bounds[1]) * 3.0
    regions, vertices = _finite_voronoi_regions(points, radius)
    out: list[list[Point]] = []
    for region in regions:
        raw = Polygon([tuple(vertices[i]) for i in region])
        if raw.is_empty or not raw.is_valid:
            raw = raw.buffer(0)
        clipped = raw.intersection(hull_poly)
        if clipped.is_empty:
            continue
        if isinstance(clipped, MultiPolygon):
            clipped = max(clipped.geoms, key=lambda p: p.area)
        if clipped.area <= 1e-8:
            continue
        coords = list(clipped.exterior.coords)[:-1]
        if len(coords) >= 3:
            out.append([(round(float(x), 6), round(float(y), 6)) for x, y in coords])
    return out


def _relax_voronoi_points_in_hull(
    *,
    points: list[Point],
    hull: list[Point],
    iterations: int = 1,
) -> list[Point]:
    """Move sample points toward clipped Voronoi centroids for less grid-like cells."""
    relaxed = list(points)
    for _ in range(max(0, iterations)):
        if len(relaxed) < 4:
            break
        polys = _build_voronoi_polys_in_hull(points=relaxed, hull=hull)
        if len(polys) < len(relaxed) * 0.85:
            break
        next_points = [
            _pull_inside(_centroid(poly), hull, center=_centroid(hull))
            for poly in polys
            if len(poly) >= 3
        ]
        if len(next_points) < len(relaxed) * 0.85:
            break
        relaxed = [(round(x, 6), round(y, 6)) for x, y in next_points]
    return relaxed


def _build_physical_micro_cells_for_continent(
    *,
    world: str,
    continent_id: str,
    hull: list[Point],
    region_count: int,
    map_seed: str,
) -> list[MicroRegionCell]:
    if region_count <= 0:
        return []
    rng = random.Random(_stable_seed(MAP_GEOMETRY_VERSION, map_seed, world, continent_id, "micro-cells"))
    center = _centroid(hull)
    x0, y0, x1, y1 = _polygon_bounds(hull)
    bw = x1 - x0
    bh = y1 - y0
    target_count = max(820, min(1200, region_count * 90))
    aspect = max(0.25, bw / max(1e-6, bh))
    cols = max(20, int(math.sqrt(target_count * 1.65 * aspect)))
    rows = max(20, int(math.sqrt(target_count * 1.65 / aspect)))
    jitter = 0.42
    sample_points: list[Point] = []
    for _ in range(4):
        sample_points.clear()
        dx = bw / cols
        dy = bh / rows
        for gy in range(rows):
            for gx in range(cols):
                x = x0 + (gx + 0.5 + rng.uniform(-jitter, jitter)) * dx
                y = y0 + (gy + 0.5 + rng.uniform(-jitter, jitter)) * dy
                if _point_in_polygon((x, y), hull):
                    sample_points.append((round(x, 6), round(y, 6)))
        if len(sample_points) >= target_count:
            break
        cols = int(cols * 1.18) + 1
        rows = int(rows * 1.18) + 1
    if len(sample_points) > target_count:
        sample_points = sorted(
            sample_points,
            key=lambda p: _stable_seed(MAP_GEOMETRY_VERSION, map_seed, continent_id, p[0], p[1]),
        )[:target_count]
    sample_points = _relax_voronoi_points_in_hull(points=sample_points, hull=hull, iterations=1)
    polys_list = _build_voronoi_polys_in_hull(points=sample_points, hull=hull)
    polys: dict[str, list[Point]] = {
        f"{continent_id}:m{i:04d}": poly
        for i, poly in enumerate(polys_list)
    }
    points: dict[str, Point] = {mid: _centroid(poly) for mid, poly in polys.items()}
    max_inland = max(
        1e-6,
        max(_distance_to_polygon_edge(p, hull) for p in points.values()),
    )
    terrain_seed = _noise_seed(map_seed, world, continent_id, "terrain-field")
    out: list[MicroRegionCell] = []
    for mid in sorted(polys):
        poly = polys.get(mid, [])
        if len(poly) < 3:
            continue
        cx, cy = _centroid(poly)
        edge_dist = _distance_to_polygon_edge((cx, cy), hull)
        boundary_dist = min(_distance_to_polygon_edge(p, hull) for p in poly)
        inland = _clamp(edge_dist / max_inland, 0.0, 1.0)
        boundary_inland = _clamp(boundary_dist / max_inland, 0.0, 1.0)
        nx = (cx - x0) / max(1e-6, bw)
        ny = (cy - y0) / max(1e-6, bh)
        broad = _fbm_noise_2d(terrain_seed, nx * 2.05, ny * 2.05, octaves=5)
        detail = _fbm_noise_2d((terrain_seed + 0xA511E9B3) & 0xFFFFFFFF, nx * 8.0 + 19.0, ny * 8.0 - 11.0, octaves=3)
        ridge_noise = _fbm_noise_2d((terrain_seed + 0x63D83595) & 0xFFFFFFFF, nx * 4.8 - 3.0, ny * 4.8 + 13.0, octaves=4)
        basin_noise = _fbm_noise_2d((terrain_seed + 0xC2B2AE35) & 0xFFFFFFFF, nx * 3.6 + 41.0, ny * 3.6 - 29.0, octaves=4)
        ridge = (1.0 - abs(ridge_noise * 2.0 - 1.0)) ** 1.55
        basin = max(0.0, 0.56 - basin_noise) ** 1.35
        interior = inland ** 1.22
        elev = _clamp(
            0.035
            + interior * 0.42
            + broad * 0.31
            + ridge * 0.28
            + detail * 0.07
            - basin * 0.23
            - (1.0 - inland) ** 2.0 * 0.10,
            0.0,
            1.0,
        )
        coastal = inland < 0.22 or (boundary_inland < 0.002 and inland < 0.48)
        if coastal:
            elev = min(elev, 0.24 + inland * 0.34)
        wet_noise = _fbm_noise_2d((terrain_seed + 0xB7E15162) & 0xFFFFFFFF, nx * 3.0 + 5.0, ny * 3.0 - 17.0, octaves=4)
        storm_band = _fbm_noise_2d((terrain_seed + 0x27D4EB2F) & 0xFFFFFFFF, nx * 1.25 - 7.0, ny * 1.25 + 23.0, octaves=3)
        rain_shadow = max(0.0, elev - 0.56) * 0.30
        moisture = _clamp(
            (0.82 if coastal else 0.34)
            - inland * 0.26
            + wet_noise * 0.27
            + storm_band * 0.13
            + basin * 0.18
            - rain_shadow
            + (detail - 0.5) * 0.06,
            0.02,
            1.0,
        )
        family = _generated_terrain_family(elevation=elev, moisture=moisture, coastal=coastal)
        out.append(
            MicroRegionCell(
                micro_id=mid,
                region_id="",
                continent_id=continent_id,
                center_x=round(cx, 6),
                center_y=round(cy, 6),
                polygon=[(round(x, 6), round(y, 6)) for x, y in poly],
                elevation=round(elev, 4),
                moisture=round(moisture, 4),
                terrain_family=family,
                is_coastal=coastal,
            )
        )
    if out:
        coastal_rank = max(1, int(len(out) * 0.24))
        edge_dist_by_id = {
            cell.micro_id: _distance_to_polygon_edge((cell.center_x, cell.center_y), hull)
            for cell in out
        }
        coastal_cutoff = sorted(edge_dist_by_id.values())[coastal_rank - 1]
        revised: list[MicroRegionCell] = []
        for cell in out:
            coastal = edge_dist_by_id[cell.micro_id] <= coastal_cutoff
            moisture = max(cell.moisture, 0.78) if coastal else cell.moisture
            family = "coast" if coastal else _generated_terrain_family(
                elevation=cell.elevation,
                moisture=moisture,
                coastal=False,
            )
            revised.append(
                MicroRegionCell(
                    micro_id=cell.micro_id,
                    region_id=cell.region_id,
                    continent_id=cell.continent_id,
                    center_x=cell.center_x,
                    center_y=cell.center_y,
                    polygon=cell.polygon,
                    elevation=cell.elevation,
                    moisture=round(_clamp(moisture, 0.0, 1.0), 4),
                    terrain_family=family,
                    is_coastal=coastal,
                )
            )
        out = revised
    return out


def _region_seed_score(
    region: Region,
    cell: MicroRegionCell,
    *,
    river_cell_ids: set[str],
    hint: _RegionMapHint | None = None,
) -> float:
    hint = hint or _RegionMapHint()
    text = f"{_region_text(region)} {hint.placement} {' '.join(sorted(hint.features))}"
    score = 0.0
    if "north" in text:
        score += cell.center_y * 2.4
    if "south" in text:
        score += (1.0 - cell.center_y) * 2.4
    if "west" in text:
        score += cell.center_x * 1.8
    if "east" in text:
        score += (1.0 - cell.center_x) * 1.8
    wants_coast = any(t in text for t in ("coast", "coastal", "shore", "port", "harbor", "littoral", "fjord", "delta"))
    wants_river = any(t in text for t in ("river", "stream", "channel", "fork", "delta", "basin"))
    wants_high = any(t in text for t in ("highland", "mountain", "range", "ridge", "alps", "plateau", "rift"))
    wants_forest = any(t in text for t in ("forest", "boreal", "taiga", "deep"))
    wants_dry = any(t in text for t in ("arid", "salt", "steppe", "desert", "inland", "leeward"))
    if wants_coast:
        score += 0.0 if cell.is_coastal else 8.0
        score += cell.elevation * 0.8
    elif cell.is_coastal:
        score += 1.2
    if wants_river:
        score += 0.0 if cell.micro_id in river_cell_ids else 5.0
        score += (1.0 - cell.moisture) * 1.2
    if wants_high:
        score += (1.0 - cell.elevation) * 3.2
    if wants_forest:
        score += (1.0 - cell.moisture) * 2.0
        score += abs(cell.elevation - 0.42) * 0.7
    if wants_dry:
        score += cell.moisture * 2.5
        score += 0.7 if cell.is_coastal and "port" not in text and "littoral" not in text else 0.0
    return score


def _replace_micro_cell_region(cell: MicroRegionCell, region_id: str) -> MicroRegionCell:
    return MicroRegionCell(
        micro_id=cell.micro_id,
        region_id=region_id,
        continent_id=cell.continent_id,
        center_x=cell.center_x,
        center_y=cell.center_y,
        polygon=cell.polygon,
        elevation=cell.elevation,
        moisture=cell.moisture,
        terrain_family=cell.terrain_family,
        is_coastal=cell.is_coastal,
    )


def _assign_regions_to_micro_cells(
    *,
    regions: list[Region],
    micro_cells: list[MicroRegionCell],
    river_cell_ids: set[str],
    region_hints: dict[str, _RegionMapHint] | None = None,
) -> list[MicroRegionCell]:
    if not regions or not micro_cells:
        return micro_cells
    total_cap = sum(max(1, int(r.carrying_capacity)) for r in regions)
    min_target = max(14, len(micro_cells) // max(1, len(regions) * 3))
    raw_targets = {
        r.region_id: max(
            min_target,
            round(len(micro_cells) * max(1, int(r.carrying_capacity)) / max(1, total_cap)),
        )
        for r in regions
    }
    while sum(raw_targets.values()) > len(micro_cells):
        rid = max(raw_targets, key=lambda k: (raw_targets[k], k))
        if raw_targets[rid] <= min_target:
            break
        raw_targets[rid] -= 1
    while sum(raw_targets.values()) < len(micro_cells):
        rid = min(raw_targets, key=lambda k: (raw_targets[k], k))
        raw_targets[rid] += 1

    available = {c.micro_id for c in micro_cells}
    by_id = {c.micro_id: c for c in micro_cells}
    seed_for_region: dict[str, str] = {}
    region_hints = region_hints or {}
    constrained_regions = sorted(
        regions,
        key=lambda r: (
            0 if any(t in f"{_region_text(r)} {region_hints.get(r.region_id, _RegionMapHint()).placement} {' '.join(region_hints.get(r.region_id, _RegionMapHint()).features)}" for t in ("port", "coast", "shore", "river", "range", "north", "south", "east", "west")) else 1,
            r.region_id,
        ),
    )
    for region in constrained_regions:
        pool = [by_id[mid] for mid in available] or list(micro_cells)
        seed = min(
            pool,
            key=lambda c: (
                _region_seed_score(
                    region,
                    c,
                    river_cell_ids=river_cell_ids,
                    hint=region_hints.get(region.region_id),
                ),
                c.micro_id,
            ),
        )
        seed_for_region[region.region_id] = seed.micro_id
        available.discard(seed.micro_id)

    assigned: dict[str, str] = {}
    owned: dict[str, set[str]] = {r.region_id: set() for r in regions}
    for rid, mid in seed_for_region.items():
        assigned[mid] = rid
        owned[rid].add(mid)
    adjacency, _ = _micro_adjacency(micro_cells)
    region_by_id = {r.region_id: r for r in regions}
    unassigned = {c.micro_id for c in micro_cells if c.micro_id not in assigned}
    for _ in range(len(micro_cells) * 2):
        if not unassigned:
            break
        growing = [
            r for r in regions
            if len(owned[r.region_id]) < raw_targets[r.region_id]
        ] or list(regions)
        best: tuple[float, str, str] | None = None
        for region in growing:
            rid = region.region_id
            seed = by_id[seed_for_region[rid]]
            frontier = {
                nid
                for mid in owned[rid]
                for nid in adjacency.get(mid, set())
                if nid in unassigned
            }
            if not frontier:
                continue
            candidate = min(
                (by_id[mid] for mid in frontier),
                key=lambda cell: (
                    (cell.center_x - seed.center_x) ** 2
                    + (cell.center_y - seed.center_y) ** 2
                    + _region_seed_score(
                        region,
                        cell,
                        river_cell_ids=river_cell_ids,
                        hint=region_hints.get(region.region_id),
                    )
                    * 0.002,
                    cell.micro_id,
                ),
            )
            pressure = len(owned[rid]) / max(1, raw_targets[rid])
            item = (
                pressure,
                (candidate.center_x - seed.center_x) ** 2 + (candidate.center_y - seed.center_y) ** 2,
                rid,
                candidate.micro_id,
            )
            if best is None or item < best:
                best = item
        if best is None:
            break
        _, _, rid, mid = best
        assigned[mid] = rid
        owned[rid].add(mid)
        unassigned.remove(mid)
    while unassigned:
        mid = min(unassigned)
        cell = by_id[mid]
        rid = min(
            regions,
            key=lambda region: (
                (cell.center_x - by_id[seed_for_region[region.region_id]].center_x) ** 2
                + (cell.center_y - by_id[seed_for_region[region.region_id]].center_y) ** 2,
                region.region_id,
            ),
        ).region_id
        assigned[mid] = rid
        owned[rid].add(mid)
        unassigned.remove(mid)
    _ = region_by_id

    assigned_cells = [
        _replace_micro_cell_region(cell, assigned.get(cell.micro_id, regions[0].region_id))
        for cell in micro_cells
    ]
    assigned_cells = _repair_region_feature_constraints(
        regions=regions,
        micro_cells=assigned_cells,
        river_cell_ids=river_cell_ids,
        region_hints=region_hints,
        min_target=min_target,
    )
    assigned_cells = _repair_small_region_footprints(
        regions=regions,
        micro_cells=assigned_cells,
        min_target=min_target,
    )
    assigned_cells = _repair_disconnected_region_footprints(
        regions=regions,
        micro_cells=assigned_cells,
        min_target=min_target,
    )
    assigned_cells = _repair_small_region_footprints(
        regions=regions,
        micro_cells=assigned_cells,
        min_target=min_target,
    )
    assigned_cells = _repair_disconnected_region_footprints(
        regions=regions,
        micro_cells=assigned_cells,
        min_target=min_target,
    )
    return assigned_cells


def _region_wants_coast(region: Region, hint: _RegionMapHint | None = None) -> bool:
    hint = hint or _RegionMapHint()
    text = f"{_region_text(region)} {hint.placement} {' '.join(sorted(hint.features))}"
    return any(t in text for t in ("port", "coast", "shore", "littoral", "delta", "harbor", "fjord"))


def _region_wants_river(region: Region, hint: _RegionMapHint | None = None) -> bool:
    hint = hint or _RegionMapHint()
    text = f"{_region_text(region)} {hint.placement} {' '.join(sorted(hint.features))}"
    return any(t in text for t in ("river", "channel", "fork", "basin", "delta", "wetland"))


def _repair_small_region_footprints(
    *,
    regions: list[Region],
    micro_cells: list[MicroRegionCell],
    min_target: int,
) -> list[MicroRegionCell]:
    out = list(micro_cells)
    region_ids = {r.region_id for r in regions}
    adjacency, _ = _micro_adjacency(out)
    for _ in range(min_target * max(1, len(regions))):
        counts: dict[str, int] = {rid: 0 for rid in region_ids}
        by_id = {c.micro_id: c for c in out}
        owned: dict[str, set[str]] = {rid: set() for rid in region_ids}
        for cell in out:
            counts[cell.region_id] = counts.get(cell.region_id, 0) + 1
            owned.setdefault(cell.region_id, set()).add(cell.micro_id)
        under = [rid for rid in region_ids if counts.get(rid, 0) < min_target]
        if not under:
            break
        rid = min(under, key=lambda r: (counts.get(r, 0), r))
        frontier = {
            nid
            for mid in owned.get(rid, set())
            for nid in adjacency.get(mid, set())
            if by_id[nid].region_id != rid
            and counts.get(by_id[nid].region_id, 0) > min_target + 2
        }
        if owned.get(rid):
            cx = sum(by_id[mid].center_x for mid in owned[rid]) / len(owned[rid])
            cy = sum(by_id[mid].center_y for mid in owned[rid]) / len(owned[rid])
        else:
            cx = cy = 0.5
        pool = (
            [by_id[mid] for mid in frontier]
            if frontier
            else [
                c for c in out
                if c.region_id != rid and counts.get(c.region_id, 0) > min_target + 2
            ]
        )
        if not pool:
            break
        donor = min(
            pool,
            key=lambda c: ((c.center_x - cx) ** 2 + (c.center_y - cy) ** 2, c.micro_id),
        )
        idx = out.index(donor)
        out[idx] = _replace_micro_cell_region(donor, rid)
    return out


def _region_components(
    region_id: str,
    owned_ids: set[str],
    adjacency: dict[str, set[str]],
) -> list[set[str]]:
    remaining = set(owned_ids)
    components: list[set[str]] = []
    while remaining:
        start = min(remaining)
        stack = [start]
        remaining.remove(start)
        component = {start}
        while stack:
            current = stack.pop()
            for nid in adjacency.get(current, set()):
                if nid in remaining:
                    remaining.remove(nid)
                    component.add(nid)
                    stack.append(nid)
        components.append(component)
    return sorted(components, key=lambda c: (-len(c), min(c) if c else ""))


def _repair_disconnected_region_footprints(
    *,
    regions: list[Region],
    micro_cells: list[MicroRegionCell],
    min_target: int,
) -> list[MicroRegionCell]:
    out = list(micro_cells)
    region_ids = {r.region_id for r in regions}
    for _ in range(max(1, len(regions)) * 3):
        by_id = {c.micro_id: c for c in out}
        idx_by_id = {c.micro_id: i for i, c in enumerate(out)}
        adjacency, _ = _micro_adjacency(out)
        owned: dict[str, set[str]] = {rid: set() for rid in region_ids}
        counts: dict[str, int] = {rid: 0 for rid in region_ids}
        for cell in out:
            if cell.region_id in region_ids:
                owned[cell.region_id].add(cell.micro_id)
                counts[cell.region_id] = counts.get(cell.region_id, 0) + 1
        changed = False
        for rid in sorted(region_ids):
            components = _region_components(rid, owned.get(rid, set()), adjacency)
            if len(components) <= 1:
                continue
            keep = components[0]
            for fragment in components[1:]:
                if counts.get(rid, 0) - len(fragment) < min_target:
                    queue = [(mid, [mid]) for mid in sorted(fragment)]
                    seen = set(fragment)
                    bridge_path: list[str] = []
                    while queue and not bridge_path:
                        current, path = queue.pop(0)
                        for nid in sorted(adjacency.get(current, set())):
                            if nid in seen:
                                continue
                            next_path = path + [nid]
                            if nid in keep:
                                bridge_path = next_path
                                break
                            seen.add(nid)
                            queue.append((nid, next_path))
                    bridged = False
                    for mid in bridge_path[1:-1]:
                        donor_rid = by_id[mid].region_id
                        if donor_rid == rid or counts.get(donor_rid, 0) <= min_target + 1:
                            continue
                        out[idx_by_id[mid]] = _replace_micro_cell_region(by_id[mid], rid)
                        counts[donor_rid] = counts.get(donor_rid, 0) - 1
                        counts[rid] = counts.get(rid, 0) + 1
                        changed = True
                        bridged = True
                    if bridged:
                        continue
                    continue
                for mid in sorted(fragment):
                    neighbor_regions = [
                        by_id[nid].region_id
                        for nid in adjacency.get(mid, set())
                        if by_id[nid].region_id != rid and by_id[nid].region_id in region_ids
                    ]
                    if neighbor_regions:
                        new_rid = max(
                            sorted(set(neighbor_regions)),
                            key=lambda candidate: (
                                neighbor_regions.count(candidate),
                                counts.get(candidate, 0),
                            ),
                        )
                    else:
                        cell = by_id[mid]
                        candidates = [
                            other for other in region_ids
                            if other != rid and owned.get(other)
                        ]
                        if not candidates:
                            continue
                        new_rid = min(
                            candidates,
                            key=lambda other: (
                                min(
                                    (
                                        (by_id[oid].center_x - cell.center_x) ** 2
                                        + (by_id[oid].center_y - cell.center_y) ** 2
                                    )
                                    for oid in owned.get(other, set())
                                ),
                                other,
                            ),
                        )
                    out[idx_by_id[mid]] = _replace_micro_cell_region(by_id[mid], new_rid)
                    counts[rid] = counts.get(rid, 0) - 1
                    counts[new_rid] = counts.get(new_rid, 0) + 1
                    changed = True
        if not changed:
            break
    return out


def _repair_region_feature_constraints(
    *,
    regions: list[Region],
    micro_cells: list[MicroRegionCell],
    river_cell_ids: set[str],
    region_hints: dict[str, _RegionMapHint],
    min_target: int,
) -> list[MicroRegionCell]:
    out = list(micro_cells)
    region_by_id = {r.region_id: r for r in regions}
    for region in regions:
        hint = region_hints.get(region.region_id)
        needs_coast = _region_wants_coast(region, hint)
        needs_river = _region_wants_river(region, hint)
        owned = [c for c in out if c.region_id == region.region_id]
        has_coast = any(c.is_coastal for c in owned)
        has_river = any(c.micro_id in river_cell_ids or c.moisture >= 0.78 for c in owned)
        if (not needs_coast or has_coast) and (not needs_river or has_river):
            continue
        target = (
            max(owned, key=lambda c: c.moisture)
            if owned
            else min(out, key=lambda c: _region_seed_score(region, c, river_cell_ids=river_cell_ids, hint=hint))
        )
        adjacency, _ = _micro_adjacency(out)
        owned_ids = {c.micro_id for c in owned}
        candidates = out
        if needs_coast and not has_coast:
            candidates = [c for c in candidates if c.is_coastal]
        if needs_river and not has_river:
            riverish = [c for c in candidates if c.micro_id in river_cell_ids or c.moisture >= 0.78]
            candidates = riverish or candidates
        donor_counts: dict[str, int] = {}
        donor_coast_counts: dict[str, int] = {}
        donor_river_counts: dict[str, int] = {}
        for cell in out:
            donor_counts[cell.region_id] = donor_counts.get(cell.region_id, 0) + 1
            if cell.is_coastal:
                donor_coast_counts[cell.region_id] = donor_coast_counts.get(cell.region_id, 0) + 1
            if cell.micro_id in river_cell_ids or cell.moisture >= 0.78:
                donor_river_counts[cell.region_id] = donor_river_counts.get(cell.region_id, 0) + 1

        def can_donate(cell: MicroRegionCell) -> bool:
            if cell.region_id == region.region_id:
                return False
            if donor_counts.get(cell.region_id, 0) <= min_target:
                return False
            donor_region = region_by_id.get(cell.region_id)
            donor_hint = region_hints.get(cell.region_id)
            if donor_region is not None and cell.is_coastal and _region_wants_coast(donor_region, donor_hint):
                if donor_coast_counts.get(cell.region_id, 0) <= 1:
                    return False
            if (
                donor_region is not None
                and (cell.micro_id in river_cell_ids or cell.moisture >= 0.78)
                and _region_wants_river(donor_region, donor_hint)
            ):
                if donor_river_counts.get(cell.region_id, 0) <= 1:
                    return False
            return True

        candidates = [
            c for c in candidates if can_donate(c)
        ] or [c for c in out if c.region_id != region.region_id and donor_counts.get(c.region_id, 0) > min_target]
        adjacent_candidates = [
            c for c in candidates
            if not owned_ids or any(nid in owned_ids for nid in adjacency.get(c.micro_id, set()))
        ]
        candidates = adjacent_candidates or candidates
        if not candidates:
            continue
        donor = min(
            candidates,
            key=lambda c: (
                (c.center_x - target.center_x) ** 2 + (c.center_y - target.center_y) ** 2,
                _region_seed_score(region, c, river_cell_ids=river_cell_ids, hint=hint),
                c.micro_id,
            ),
        )
        idx = out.index(donor)
        out[idx] = _replace_micro_cell_region(donor, region.region_id)
        _ = region_by_id
    return out


def _micro_adjacency(
    micro_cells: list[MicroRegionCell],
) -> tuple[dict[str, set[str]], dict[tuple[str, str], Point]]:
    edge_owner: dict[tuple[Point, Point], str] = {}
    adjacency: dict[str, set[str]] = {c.micro_id: set() for c in micro_cells}
    shared_midpoints: dict[tuple[str, str], Point] = {}
    for cell in micro_cells:
        pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            key = tuple(sorted((a, b)))  # type: ignore[assignment]
            other = edge_owner.get(key)
            if other is None:
                edge_owner[key] = cell.micro_id
                continue
            if other == cell.micro_id:
                continue
            adjacency[cell.micro_id].add(other)
            adjacency[other].add(cell.micro_id)
            pair = tuple(sorted((cell.micro_id, other)))  # type: ignore[assignment]
            shared_midpoints[pair] = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    return adjacency, shared_midpoints


def _micro_boundary_edges(micro_cells: list[MicroRegionCell]) -> dict[str, list[tuple[Point, Point]]]:
    edge_owner: dict[tuple[Point, Point], tuple[str, Point, Point]] = {}
    shared_edges: set[tuple[Point, Point]] = set()
    for cell in micro_cells:
        pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            key = tuple(sorted((a, b)))  # type: ignore[assignment]
            if key in edge_owner:
                shared_edges.add(key)
            else:
                edge_owner[key] = (cell.micro_id, a, b)

    out: dict[str, list[tuple[Point, Point]]] = {}
    for key, (micro_id, a, b) in edge_owner.items():
        if key in shared_edges:
            continue
        out.setdefault(micro_id, []).append((a, b))
    return out


def _coastal_river_mouth_point(
    cell: MicroRegionCell,
    previous: Point,
    boundary_edges: dict[str, list[tuple[Point, Point]]],
) -> Point | None:
    edges = boundary_edges.get(cell.micro_id, [])
    if not edges:
        return None
    center = (cell.center_x, cell.center_y)
    flow_dx = center[0] - previous[0]
    flow_dy = center[1] - previous[1]
    flow_len = math.hypot(flow_dx, flow_dy)
    if flow_len <= 1e-9:
        flow_dx = flow_dy = 0.0
    else:
        flow_dx /= flow_len
        flow_dy /= flow_len

    candidates: list[tuple[float, float, float, Point]] = []
    for a, b in edges:
        point = _nearest_point_on_segment(center, a, b)
        out_dx = point[0] - center[0]
        out_dy = point[1] - center[1]
        out_len = math.hypot(out_dx, out_dy)
        alignment = 0.0 if out_len <= 1e-9 else (out_dx / out_len) * flow_dx + (out_dy / out_len) * flow_dy
        candidates.append((-alignment, out_len, point[0], point))
    return min(candidates, key=lambda c: (c[0], c[1], c[2], c[3][1]))[3]


def _nearest_used_outlet_distance(cell: MicroRegionCell, used_outlets: list[Point]) -> float:
    if not used_outlets:
        return 1.0
    center = (cell.center_x, cell.center_y)
    return min(math.hypot(center[0] - x, center[1] - y) for x, y in used_outlets)


def _center_adjacency(micro_cells: list[MicroRegionCell], *, neighbors: int = 7) -> dict[str, set[str]]:
    by_continent: dict[str, list[MicroRegionCell]] = {}
    for cell in micro_cells:
        by_continent.setdefault(cell.continent_id, []).append(cell)
    out: dict[str, set[str]] = {c.micro_id: set() for c in micro_cells}
    for cells in by_continent.values():
        for cell in cells:
            nearest = sorted(
                (
                    other for other in cells
                    if other.micro_id != cell.micro_id
                ),
                key=lambda other: (
                    (other.center_x - cell.center_x) ** 2
                    + (other.center_y - cell.center_y) ** 2,
                    other.micro_id,
                ),
            )[:neighbors]
            for other in nearest:
                out[cell.micro_id].add(other.micro_id)
                out[other.micro_id].add(cell.micro_id)
    return out


def _build_micro_rivers(micro_cells: list[MicroRegionCell]) -> tuple[list[RiverPath], set[str]]:
    by_id = {c.micro_id: c for c in micro_cells}
    adjacency, shared_midpoints = _micro_adjacency(micro_cells)
    boundary_edges = _micro_boundary_edges(micro_cells)
    river_cells: set[str] = set()
    rivers: list[RiverPath] = []
    by_continent: dict[str, list[MicroRegionCell]] = {}
    for cell in micro_cells:
        by_continent.setdefault(cell.continent_id, []).append(cell)
    for continent_id, cells in sorted(by_continent.items()):
        source_pool = sorted(
            (
                c for c in cells
                if c.elevation >= 0.58 and not c.is_coastal
            ),
            key=lambda c: (-c.elevation, c.micro_id),
        )
        river_count = max(2, min(8, len(cells) // 18))
        used_channels: set[str] = set()
        used_outlets: list[Point] = []
        selected_sources: list[MicroRegionCell] = []
        min_source_spacing = 0.075
        for _ in range(river_count):
            available_sources = [
                c for c in source_pool
                if c.micro_id not in used_channels
                and all(
                    math.hypot(c.center_x - s.center_x, c.center_y - s.center_y) >= min_source_spacing
                    for s in selected_sources
                )
            ]
            if not available_sources:
                available_sources = [c for c in source_pool if c.micro_id not in used_channels]
            if not available_sources:
                break
            source = max(
                available_sources,
                key=lambda c: (
                    c.elevation
                    + _nearest_used_outlet_distance(c, used_outlets) * 0.08
                    - (0.12 if c.micro_id in river_cells else 0.0),
                    c.micro_id,
                ),
            )
            selected_sources.append(source)
            current = source.micro_id
            visited = {current}
            path_micro_ids = [source.micro_id]
            path_points: list[Point] = [(source.center_x, source.center_y)]
            for _ in range(52):
                cell = by_id[current]
                if cell.is_coastal and boundary_edges.get(current):
                    break
                neighbors = [by_id[nid] for nid in adjacency.get(current, set()) if nid not in visited]
                if not neighbors:
                    break
                downhill = [n for n in neighbors if n.elevation <= cell.elevation + 0.035]
                if not downhill:
                    downhill = neighbors
                nxt = min(
                    downhill,
                    key=lambda n: (
                        n.elevation
                        + max(0.0, n.moisture - 0.58) * 0.045
                        + (0.28 if n.micro_id in used_channels else 0.0),
                        0 if n.is_coastal and boundary_edges.get(n.micro_id) else 1,
                        0 if n.is_coastal else 1,
                        (
                            0.0
                            if not (n.is_coastal and boundary_edges.get(n.micro_id))
                            else max(0.0, 0.16 - _nearest_used_outlet_distance(n, used_outlets)) * 4.0
                        ),
                        abs(n.center_x - cell.center_x) + abs(n.center_y - cell.center_y),
                        n.micro_id,
                    ),
                )
                pair = tuple(sorted((current, nxt.micro_id)))  # type: ignore[assignment]
                midpoint = shared_midpoints.get(
                    pair,
                    ((cell.center_x + nxt.center_x) / 2.0, (cell.center_y + nxt.center_y) / 2.0),
                )
                path_points.append(midpoint)
                current = nxt.micro_id
                visited.add(current)
                path_micro_ids.append(current)
                if nxt.is_coastal and boundary_edges.get(nxt.micro_id):
                    path_points.append((nxt.center_x, nxt.center_y))
                    mouth = _coastal_river_mouth_point(nxt, midpoint, boundary_edges)
                    if mouth is not None:
                        path_points.append(mouth)
                    break
            if len(path_points) < 3:
                continue
            river_cells.update(visited)
            used_channels.update(visited)
            sink = by_id[current]
            rounded_points = [(round(x, 6), round(y, 6)) for x, y in path_points]
            if sink.is_coastal:
                used_outlets.append(rounded_points[-1])
            segments: list[RiverSegment] = []
            for i in range(len(rounded_points) - 1):
                micro_id = path_micro_ids[min(i, len(path_micro_ids) - 1)]
                if i + 1 >= len(rounded_points):
                    break
                segments.append(
                    RiverSegment(
                        points=[rounded_points[i], rounded_points[i + 1]],
                        micro_ids=[micro_id],
                        region_ids=[by_id[micro_id].region_id] if by_id[micro_id].region_id else [],
                    )
                )
            rivers.append(
                RiverPath(
                    river_id=f"{continent_id}:micro_river:{len(rivers)}",
                    from_region_id=source.region_id,
                    to_region_id=sink.region_id,
                    points=rounded_points,
                    segments=segments,
                    flow=max(0.25, min(1.0, len(visited) / 12.0)),
                    river_class="major_river" if len(visited) >= 8 else "minor_river",
                )
            )
    return rivers, river_cells


def _moisten_micro_cells(
    micro_cells: list[MicroRegionCell],
    river_cell_ids: set[str],
) -> list[MicroRegionCell]:
    if not micro_cells:
        return micro_cells
    by_id = {c.micro_id: c for c in micro_cells}
    adjacency, _ = _micro_adjacency(micro_cells)
    best_moisture: dict[str, float] = {}
    queue: list[tuple[float, str]] = []
    for cell in micro_cells:
        strength = 0.0
        if cell.is_coastal:
            strength = max(strength, 0.72)
        if cell.micro_id in river_cell_ids:
            strength = max(strength, 0.94)
        if strength > 0.0:
            best_moisture[cell.micro_id] = strength
            heapq.heappush(queue, (-strength, cell.micro_id))
    while queue:
        neg_strength, micro_id = heapq.heappop(queue)
        strength = -neg_strength
        if strength + 1e-9 < best_moisture.get(micro_id, 0.0):
            continue
        cell = by_id[micro_id]
        for neighbor_id in adjacency.get(micro_id, set()):
            neighbor = by_id[neighbor_id]
            uphill = max(0.0, neighbor.elevation - cell.elevation)
            downhill_bonus = max(0.0, cell.elevation - neighbor.elevation) * 0.070
            decay = 0.055 + uphill * 0.28 + max(0.0, neighbor.elevation - 0.66) * 0.070 - downhill_bonus
            next_strength = strength - decay
            if next_strength < 0.44:
                continue
            if next_strength > best_moisture.get(neighbor_id, 0.0) + 0.01:
                best_moisture[neighbor_id] = next_strength
                heapq.heappush(queue, (-next_strength, neighbor_id))
    out: list[MicroRegionCell] = []
    for cell in micro_cells:
        moisture = max(cell.moisture, best_moisture.get(cell.micro_id, 0.0))
        family = _generated_terrain_family(
            elevation=cell.elevation,
            moisture=moisture,
            coastal=cell.is_coastal,
        )
        out.append(
            MicroRegionCell(
                micro_id=cell.micro_id,
                region_id=cell.region_id,
                continent_id=cell.continent_id,
                center_x=cell.center_x,
                center_y=cell.center_y,
                polygon=cell.polygon,
                elevation=cell.elevation,
                moisture=round(_clamp(moisture, 0.0, 1.0), 4),
                terrain_family=family,
                is_coastal=cell.is_coastal,
            )
        )
    return out


def _aggregate_region_polygons(
    all_regions: list[Region],
    micro_cells: list[MicroRegionCell],
    fallback_polys: dict[str, list[Point]],
) -> dict[str, list[Point]]:
    by_region: dict[str, list[ShapelyPolygon]] = {}
    for cell in micro_cells:
        poly = _valid_polygon(cell.polygon)
        if poly is not None:
            by_region.setdefault(cell.region_id, []).append(poly)
    out: dict[str, list[Point]] = {}
    for region in all_regions:
        owned = by_region.get(region.region_id, [])
        ring: list[Point] = []
        if owned:
            dissolved = unary_union(owned)
            candidates = _polygons_from_geometry(dissolved)
            if candidates:
                # Assigned micro-cells are contiguous in normal builds; if a tiny sliver
                # survives as a separate polygon, the largest dissolved outline is the
                # clean display boundary and the micro-cells remain authoritative.
                largest = max(candidates, key=lambda p: p.area)
                simplified = largest.simplify(0.00045, preserve_topology=True)
                if isinstance(simplified, ShapelyPolygon) and not simplified.is_empty:
                    largest = simplified
                ring = _rounded_ring(largest)
        out[region.region_id] = ring if len(ring) >= 3 else fallback_polys.get(region.region_id, [])
    return out


def _edge_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))  # type: ignore[return-value]


def _dedupe_path_points(points: list[Point]) -> list[Point]:
    out: list[Point] = []
    for x, y in points:
        p = (round(x, 6), round(y, 6))
        if out and abs(out[-1][0] - p[0]) < 1e-7 and abs(out[-1][1] - p[1]) < 1e-7:
            continue
        out.append(p)
    return out


def _micro_route_weight(a: MicroRegionCell, b: MicroRegionCell) -> float:
    distance = math.hypot(a.center_x - b.center_x, a.center_y - b.center_y)
    elevation_penalty = max(0.0, (a.elevation + b.elevation) / 2.0 - 0.58) * 0.95
    wet_penalty = max(0.0, (a.moisture + b.moisture) / 2.0 - 0.82) * 0.28
    coast_bonus = -0.10 if a.is_coastal and b.is_coastal else 0.0
    return max(0.0001, distance * (1.0 + elevation_penalty + wet_penalty + coast_bonus))


def _route_through_micro_cells(
    micro_cells: list[MicroRegionCell],
    from_region_id: str,
    to_region_id: str,
) -> list[Point] | None:
    by_id = {c.micro_id: c for c in micro_cells}
    source_cells = [c for c in micro_cells if c.region_id == from_region_id]
    target_cells = [c for c in micro_cells if c.region_id == to_region_id]
    if not source_cells or not target_cells:
        return None
    source_center = _centroid([(c.center_x, c.center_y) for c in source_cells])
    target_center = _centroid([(c.center_x, c.center_y) for c in target_cells])
    target_ids = {c.micro_id for c in target_cells}
    same_continent = source_cells[0].continent_id == target_cells[0].continent_id
    if not same_continent:
        return None

    adjacency, shared_midpoints = _micro_adjacency(micro_cells)
    start = min(
        source_cells,
        key=lambda c: (
            (c.center_x - target_center[0]) ** 2 + (c.center_y - target_center[1]) ** 2,
            (c.center_x - source_center[0]) ** 2 + (c.center_y - source_center[1]) ** 2,
            c.micro_id,
        ),
    )
    queue: list[tuple[float, str]] = [(0.0, start.micro_id)]
    distances: dict[str, float] = {start.micro_id: 0.0}
    previous: dict[str, str] = {}
    end_id: str | None = None
    while queue:
        distance, micro_id = heapq.heappop(queue)
        if distance > distances.get(micro_id, float("inf")):
            continue
        if micro_id in target_ids:
            end_id = micro_id
            break
        cell = by_id[micro_id]
        for neighbor_id in adjacency.get(micro_id, set()):
            neighbor = by_id[neighbor_id]
            if neighbor.continent_id != cell.continent_id:
                continue
            nd = distance + _micro_route_weight(cell, neighbor)
            if nd < distances.get(neighbor_id, float("inf")):
                distances[neighbor_id] = nd
                previous[neighbor_id] = micro_id
                heapq.heappush(queue, (nd, neighbor_id))
    if end_id is None:
        return None

    micro_path = [end_id]
    while micro_path[-1] != start.micro_id:
        prior = previous.get(micro_path[-1])
        if prior is None:
            return None
        micro_path.append(prior)
    micro_path.reverse()
    if len(micro_path) == 1:
        return [(start.center_x, start.center_y)]

    points: list[Point] = [(by_id[micro_path[0]].center_x, by_id[micro_path[0]].center_y)]
    for a, b in zip(micro_path, micro_path[1:]):
        pair = tuple(sorted((a, b)))  # type: ignore[assignment]
        points.append(
            shared_midpoints.get(
                pair,
                (
                    (by_id[a].center_x + by_id[b].center_x) / 2.0,
                    (by_id[a].center_y + by_id[b].center_y) / 2.0,
                ),
            )
        )
    end = by_id[micro_path[-1]]
    points.append((end.center_x, end.center_y))
    return _dedupe_path_points(points)


def _coastal_route_anchor(
    micro_cells: list[MicroRegionCell],
    region_id: str,
    toward: Point,
    fallback: Point,
) -> Point:
    owned = [c for c in micro_cells if c.region_id == region_id]
    if not owned:
        return fallback
    candidates = [c for c in owned if c.is_coastal] or owned
    cell = min(
        candidates,
        key=lambda c: (
            (c.center_x - toward[0]) ** 2 + (c.center_y - toward[1]) ** 2,
            0 if c.is_coastal else 1,
            c.micro_id,
        ),
    )
    edge = _nearest_point_on_polygon_edge(toward, cell.polygon)
    return (
        edge[0] * 0.68 + cell.center_x * 0.32,
        edge[1] * 0.68 + cell.center_y * 0.32,
    )


def _sea_lane_points(
    micro_cells: list[MicroRegionCell],
    from_region_id: str,
    to_region_id: str,
    a: Point,
    b: Point,
) -> list[Point]:
    start = _coastal_route_anchor(micro_cells, from_region_id, b, a)
    end = _coastal_route_anchor(micro_cells, to_region_id, start, b)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = max(1e-6, math.hypot(dx, dy))
    nx = -dy / length
    ny = dx / length
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    map_center = (0.5, 0.5)
    sign = 1.0 if nx * (midpoint[0] - map_center[0]) + ny * (midpoint[1] - map_center[1]) >= 0.0 else -1.0
    bow = min(0.18, max(0.055, length * 0.28))
    first = (start[0] + dx * 0.32 + nx * sign * bow * 0.70, start[1] + dy * 0.32 + ny * sign * bow * 0.70)
    second = (start[0] + dx * 0.68 + nx * sign * bow, start[1] + dy * 0.68 + ny * sign * bow)
    return _dedupe_path_points([
        start,
        (_clamp(first[0], 0.015, 0.985), _clamp(first[1], 0.015, 0.985)),
        (_clamp(second[0], 0.015, 0.985), _clamp(second[1], 0.015, 0.985)),
        end,
    ])


def _cells_for_region_footprint(
    geometry: WorldMapGeometry,
    region_id: str,
) -> list[MicroRegionCell]:
    rid = (region_id or "").strip()
    cells = [c for c in geometry.micro_cells if c.region_id == rid]
    if cells:
        return cells
    cell = geometry.cell_by_region_id().get(rid)
    if cell is None:
        return []
    return [
        MicroRegionCell(
            micro_id=f"{rid}:region-cell",
            region_id=rid,
            continent_id=cell.continent_id,
            center_x=cell.center_x,
            center_y=cell.center_y,
            polygon=cell.polygon,
            elevation=cell.elevation,
            moisture=cell.moisture,
            terrain_family=cell.terrain_family,
            is_coastal=cell.is_coastal,
        )
    ]


def region_id_for_world_point(
    geometry: WorldMapGeometry,
    point: Point,
) -> str | None:
    """Return the region owning the micro-polygon containing ``point``."""
    for cell in geometry.micro_cells:
        if _point_in_polygon(point, cell.polygon):
            return cell.region_id
    for cell in geometry.cells:
        if _point_in_polygon(point, cell.polygon):
            return cell.region_id
    return None


def project_world_point_to_region_footprint(
    geometry: WorldMapGeometry,
    region_id: str,
    point: Point,
) -> Point:
    """Clamp a world-space point to land owned by ``region_id``."""
    cells = _cells_for_region_footprint(geometry, region_id)
    if not cells:
        return (_clamp(point[0], 0.0, 1.0), _clamp(point[1], 0.0, 1.0))
    for cell in cells:
        if _point_in_polygon(point, cell.polygon):
            return _nudge_inside_polygon(point, cell.polygon)
    return _best_region_interior_point(point, cells)


def project_local_point_to_region_footprint(
    geometry: WorldMapGeometry,
    region_id: str,
    local: Point,
) -> Point:
    """Project a local [0, 1] point onto land actually owned by ``region_id``."""
    cells = _cells_for_region_footprint(geometry, region_id)
    if not cells:
        return (_clamp(local[0], 0.0, 1.0), _clamp(local[1], 0.0, 1.0))
    xs = [x for c in cells for x, _ in c.polygon]
    ys = [y for c in cells for _, y in c.polygon]
    if not xs or not ys:
        return (_clamp(local[0], 0.0, 1.0), _clamp(local[1], 0.0, 1.0))
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    candidate = (
        x0 + (x1 - x0) * _clamp(local[0], 0.0, 1.0),
        y0 + (y1 - y0) * _clamp(local[1], 0.0, 1.0),
    )
    for cell in cells:
        if _point_in_polygon(candidate, cell.polygon):
            point = _nudge_inside_polygon(candidate, cell.polygon)
            if _distance_to_polygon_edge(point, cell.polygon) >= 0.003:
                return point
            return _best_region_interior_point(candidate, cells)
    return _best_region_interior_point(candidate, cells)


def _build_region_edges(
    regions: Iterable[Region],
    points: dict[str, Point],
    micro_cells: list[MicroRegionCell],
    *,
    world: str,
    db_path: Path | str | None,
) -> list[RegionEdge]:
    seen: set[tuple[str, str]] = set()
    out: list[RegionEdge] = []
    region_ids = {r.region_id for r in regions}
    for rid in sorted(region_ids):
        for route in list_routes_from(rid, world=world, db_path=db_path):
            if route.to_region_id not in region_ids:
                continue
            key = _edge_key(rid, route.to_region_id)
            if key in seen:
                continue
            seen.add(key)
            a = points.get(rid)
            b = points.get(route.to_region_id)
            if a is None or b is None:
                continue
            route_type = route.route_type.strip().lower()
            if route_type == "sea":
                edge_points = _sea_lane_points(micro_cells, key[0], key[1], a, b)
            else:
                edge_points = _route_through_micro_cells(micro_cells, key[0], key[1]) or [a, b]
            out.append(
                RegionEdge(
                    from_region_id=key[0],
                    to_region_id=key[1],
                    route_type=route.route_type,
                    friction=route.friction,
                    points=edge_points,
                    edge_class="sea_route" if route_type == "sea" else "land_route",
                )
            )
    return out


def _build_rivers(
    cells: dict[str, RegionCell],
    *,
    world: str,
    db_path: Path | str | None,
) -> list[RiverPath]:
    rivers: list[RiverPath] = []
    for rid, cell in sorted(cells.items()):
        try:
            env = region_environment(rid, world=world, db_path=db_path)
        except LookupError:
            continue
        to = (env.drainage_to or "").strip()
        if not to or to not in cells or to == rid:
            continue
        if cell.moisture < 0.35 and cells[to].moisture < 0.35:
            continue
        src = (cell.center_x, cell.center_y)
        dst_cell = cells[to]
        dst = (dst_cell.center_x, dst_cell.center_y)
        mid = ((src[0] + dst[0]) / 2.0, (src[1] + dst[1]) / 2.0)
        flow = max(0.2, (cell.moisture + dst_cell.moisture) / 2.0)
        rivers.append(
            RiverPath(
                river_id=f"{rid}:to:{to}",
                from_region_id=rid,
                to_region_id=to,
                points=[src, mid, dst],
                segments=[
                    RiverSegment(points=[src, mid], micro_ids=[], region_ids=[rid]),
                    RiverSegment(points=[mid, dst], micro_ids=[], region_ids=[to]),
                ],
                flow=flow,
                river_class="major_river" if flow >= 0.7 else "minor_river",
            )
        )
    return rivers


def _apply_river_influence_to_micro_cells(
    micro_cells: list[MicroRegionCell],
    rivers: list[RiverPath],
) -> list[MicroRegionCell]:
    if not micro_cells or not rivers:
        return micro_cells

    river_segments: list[tuple[Point, Point, float]] = []
    channel_ids: dict[str, float] = {}
    for river in rivers:
        flow = max(0.0, float(river.flow))
        for segment in river.segments:
            if len(segment.points) >= 2:
                river_segments.append((segment.points[0], segment.points[-1], flow))
            for mid in segment.micro_ids:
                channel_ids[mid] = max(channel_ids.get(mid, 0.0), flow)

    if not river_segments:
        return micro_cells

    influence_radius = 0.030
    out: list[MicroRegionCell] = []
    for cell in micro_cells:
        center = (cell.center_x, cell.center_y)
        best_distance = 1.0
        best_flow = channel_ids.get(cell.micro_id, 0.0)
        best_side = 0.0
        for a, b, flow in river_segments:
            distance = _point_segment_distance(center, a, b)
            if distance >= best_distance:
                continue
            ax, ay = a
            bx, by = b
            cross = (bx - ax) * (center[1] - ay) - (by - ay) * (center[0] - ax)
            best_distance = distance
            best_flow = max(best_flow, flow)
            best_side = 1.0 if cross >= 0.0 else -1.0

        is_channel = cell.micro_id in channel_ids or best_distance <= 0.0038 + best_flow * 0.0025
        floodplain_radius = influence_radius + best_flow * 0.010
        is_floodplain = is_channel or best_distance <= floodplain_radius
        if not is_floodplain and best_flow <= 0.0:
            out.append(cell)
            continue

        river_strength = _clamp(1.0 - best_distance / max(1e-6, floodplain_radius), 0.0, 1.0)
        moisture = _clamp(cell.moisture + river_strength * (0.20 + best_flow * 0.08), 0.0, 1.0)
        elevation = _clamp(cell.elevation - river_strength * (0.018 + best_flow * 0.010), 0.0, 1.0)
        family = cell.terrain_family
        if is_floodplain and not cell.is_coastal and cell.terrain_family in {"drylands", "plains", "forest"}:
            family = _generated_terrain_family(elevation=elevation, moisture=moisture, coastal=False)
        out.append(
            replace(
                cell,
                elevation=round(elevation, 4),
                moisture=round(moisture, 4),
                terrain_family=family,
                river_distance=round(min(1.0, best_distance), 5),
                river_flow=round(best_flow, 4),
                river_side=best_side,
                is_floodplain=is_floodplain,
                is_channel=is_channel,
            )
        )
    return out


def _dedupe_polyline_points(points: list[Point], *, min_distance: float = 0.001) -> list[Point]:
    out: list[Point] = []
    for point in points:
        if out and math.dist(out[-1], point) < min_distance:
            continue
        out.append(point)
    return out


def _tapered_polyline_polygon(
    points: list[Point],
    *,
    start_width: float,
    end_width: float,
) -> list[Point]:
    points = _dedupe_polyline_points(points)
    if len(points) < 2:
        return []
    left: list[Point] = []
    right: list[Point] = []
    last = len(points) - 1
    for i, point in enumerate(points):
        if i == 0:
            tx = points[1][0] - point[0]
            ty = points[1][1] - point[1]
        elif i == last:
            tx = point[0] - points[i - 1][0]
            ty = point[1] - points[i - 1][1]
        else:
            tx = points[i + 1][0] - points[i - 1][0]
            ty = points[i + 1][1] - points[i - 1][1]
        length = max(1e-9, math.hypot(tx, ty))
        nx = -ty / length
        ny = tx / length
        downstream = (i / max(1, last)) ** 0.78
        width = start_width + (end_width - start_width) * downstream
        half = width / 2.0
        left.append((round(point[0] + nx * half, 6), round(point[1] + ny * half, 6)))
        right.append((round(point[0] - nx * half, 6), round(point[1] - ny * half, 6)))
    return left + list(reversed(right))


def _offset_polyline(points: list[Point], *, amount: float) -> list[Point]:
    points = _dedupe_polyline_points(points)
    if len(points) < 2 or abs(amount) <= 1e-9:
        return points
    out: list[Point] = []
    last = len(points) - 1
    for i, point in enumerate(points):
        if i == 0:
            tx = points[1][0] - point[0]
            ty = points[1][1] - point[1]
        elif i == last:
            tx = point[0] - points[i - 1][0]
            ty = point[1] - points[i - 1][1]
        else:
            tx = points[i + 1][0] - points[i - 1][0]
            ty = points[i + 1][1] - points[i - 1][1]
        length = max(1e-9, math.hypot(tx, ty))
        out.append((round(point[0] - ty / length * amount, 6), round(point[1] + tx / length * amount, 6)))
    return out


def _river_mouth_polygons_for_path(points: list[Point], *, width: float) -> tuple[list[Point], list[Point]]:
    points = _dedupe_polyline_points(points)
    if len(points) < 2:
        return ([], [])
    ax, ay = points[-2]
    bx, by = points[-1]
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return ([], [])
    nx = -dy / length
    ny = dx / length
    start = (ax + (bx - ax) * 0.26, ay + (by - ay) * 0.26)
    mouth_tip = (bx + dx / length * width * 0.64, by + dy / length * width * 0.64)
    mid = (start[0] + (mouth_tip[0] - start[0]) * 0.62, start[1] + (mouth_tip[1] - start[1]) * 0.62)
    inner_neck = width * 0.54
    inner_mid = width * 1.08
    inner_mouth = width * 1.55
    outer_neck = width * 0.98
    outer_mid = width * 1.55
    outer_mouth = width * 2.15
    water = [
        (start[0] + nx * inner_neck, start[1] + ny * inner_neck),
        (mid[0] + nx * inner_mid, mid[1] + ny * inner_mid),
        (mouth_tip[0] + nx * inner_mouth, mouth_tip[1] + ny * inner_mouth),
        (mouth_tip[0] - nx * inner_mouth, mouth_tip[1] - ny * inner_mouth),
        (mid[0] - nx * inner_mid, mid[1] - ny * inner_mid),
        (start[0] - nx * inner_neck, start[1] - ny * inner_neck),
    ]
    bank = [
        (start[0] + nx * outer_neck, start[1] + ny * outer_neck),
        (mid[0] + nx * outer_mid, mid[1] + ny * outer_mid),
        (mouth_tip[0] + nx * outer_mouth, mouth_tip[1] + ny * outer_mouth),
        (mouth_tip[0] - nx * outer_mouth, mouth_tip[1] - ny * outer_mouth),
        (mid[0] - nx * outer_mid, mid[1] - ny * outer_mid),
        (start[0] - nx * outer_neck, start[1] - ny * outer_neck),
    ]
    return (
        [(round(x, 6), round(y, 6)) for x, y in bank],
        [(round(x, 6), round(y, 6)) for x, y in water],
    )


def _build_river_channels(rivers: list[RiverPath]) -> list[RiverChannel]:
    channels: list[RiverChannel] = []
    for river in rivers:
        points = _dedupe_polyline_points(river.points, min_distance=0.0008)
        if len(points) < 2:
            continue
        flow = max(0.0, float(river.flow))
        water_width = 0.00060 + math.sqrt(flow) * 0.00205
        corridor = _tapered_polyline_polygon(
            points,
            start_width=max(0.0018, water_width * 0.72 + 0.0020),
            end_width=water_width + 0.0046,
        )
        bank = _tapered_polyline_polygon(
            points,
            start_width=max(0.0007, water_width * 0.48 + 0.00045),
            end_width=water_width + 0.00058,
        )
        water = _tapered_polyline_polygon(
            points,
            start_width=max(0.0005, water_width * 0.40),
            end_width=water_width,
        )
        mouth_bank, mouth_water = (
            _river_mouth_polygons_for_path(points, width=water_width)
            if len(points) >= 2 and points[-1] != points[-2]
            else ([], [])
        )
        highlight = _offset_polyline(points, amount=max(0.00018, water_width * -0.12))
        channels.append(
            RiverChannel(
                river_id=river.river_id,
                river_class=river.river_class,
                corridor_polygon=corridor,
                bank_polygon=bank,
                water_polygon=water,
                mouth_bank_polygon=mouth_bank,
                mouth_water_polygon=mouth_water,
                highlight_points=highlight,
                flow=round(flow, 4),
            )
        )
    return channels


def _valid_polygon(points: list[Point]) -> ShapelyPolygon | None:
    if len(points) < 3:
        return None
    poly = ShapelyPolygon(points)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or not isinstance(poly, ShapelyPolygon) or poly.area <= 1e-12:
        return None
    return poly


def _polygons_from_geometry(geom: object) -> list[ShapelyPolygon]:
    if isinstance(geom, ShapelyPolygon):
        return [geom] if not geom.is_empty and geom.area > 1e-12 else []
    if isinstance(geom, ShapelyMultiPolygon):
        return [p for p in geom.geoms if not p.is_empty and p.area > 1e-12]
    if isinstance(geom, GeometryCollection):
        return [p for g in geom.geoms for p in _polygons_from_geometry(g)]
    return []


def _rounded_ring(poly: ShapelyPolygon) -> list[Point]:
    pts = list(poly.exterior.coords)
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]
    return [(round(float(x), 6), round(float(y), 6)) for x, y in pts]


def _carve_river_channels_from_micro_cells(
    micro_cells: list[MicroRegionCell],
    channels: list[RiverChannel],
) -> list[MicroRegionCell]:
    if not micro_cells or not channels:
        return micro_cells

    cutters = [
        poly
        for channel in channels
        for points in (channel.bank_polygon, channel.mouth_bank_polygon)
        for poly in [_valid_polygon(points)]
        if poly is not None
    ]
    if not cutters:
        return micro_cells
    water_union = unary_union(cutters)
    if water_union.is_empty:
        return micro_cells

    out: list[MicroRegionCell] = []
    for cell in micro_cells:
        if not (cell.is_channel or cell.is_floodplain):
            out.append(cell)
            continue
        base = _valid_polygon(cell.polygon)
        if base is None or not base.intersects(water_union):
            out.append(cell)
            continue
        carved = base.difference(water_union)
        land = [
            ring
            for poly in _polygons_from_geometry(carved)
            for ring in [_rounded_ring(poly)]
            if len(ring) >= 3
        ]
        if not land:
            out.append(cell)
            continue
        out.append(replace(cell, land_polygons=land))
    return out


def _water_cell_depth(kind: str, cell: MicroRegionCell) -> float:
    if kind == "ocean":
        return round(_clamp(0.58 + cell.moisture * 0.20 - cell.elevation * 0.12, 0.25, 1.0), 4)
    return round(_clamp(0.20 + cell.moisture * 0.38 - cell.elevation * 0.30, 0.08, 0.72), 4)


def _build_lake_water_cells(micro_cells: list[MicroRegionCell]) -> list[WaterCell]:
    if not micro_cells:
        return []
    by_continent: dict[str, list[MicroRegionCell]] = {}
    for cell in micro_cells:
        by_continent.setdefault(cell.continent_id, []).append(cell)
    out: list[WaterCell] = []
    for continent_id, cells in sorted(by_continent.items()):
        candidates = sorted(
            (
                c for c in cells
                if not c.is_coastal
                and not c.is_channel
                and c.moisture >= 0.82
                and c.elevation <= 0.38
            ),
            key=lambda c: (
                c.elevation,
                -c.moisture,
                _stable_seed(MAP_GEOMETRY_VERSION, continent_id, c.micro_id, "lake-cell"),
            ),
        )
        max_lakes = max(2, min(14, len(cells) // 150))
        used: list[Point] = []
        continent_lakes = 0
        for cell in candidates:
            if continent_lakes >= max_lakes:
                break
            center = (cell.center_x, cell.center_y)
            if any(math.hypot(center[0] - x, center[1] - y) < 0.026 for x, y in used):
                continue
            shrink = 0.72 if cell.river_flow <= 0.0 else 0.60
            cx, cy = center
            lake_poly = [
                (round(cx + (x - cx) * shrink, 6), round(cy + (y - cy) * shrink, 6))
                for x, y in cell.polygon
            ]
            out.append(
                WaterCell(
                    water_id=f"{cell.micro_id}:lake",
                    kind="lake",
                    region_id=cell.region_id,
                    continent_id=cell.continent_id,
                    center_x=cell.center_x,
                    center_y=cell.center_y,
                    polygon=lake_poly,
                    depth=_water_cell_depth("lake", cell),
                    moisture_source=round(cell.moisture, 4),
                )
            )
            used.append(center)
            continent_lakes += 1
    return out


def _build_ocean_water_cells(micro_cells: list[MicroRegionCell]) -> list[WaterCell]:
    if not micro_cells:
        return []
    boundary_edges = _micro_boundary_edges(micro_cells)
    by_id = {c.micro_id: c for c in micro_cells}
    continent_centers: dict[str, Point] = {}
    for continent_id in sorted({c.continent_id for c in micro_cells}):
        pts = [(c.center_x, c.center_y) for c in micro_cells if c.continent_id == continent_id]
        continent_centers[continent_id] = _centroid(pts)

    out: list[WaterCell] = []
    for micro_id, edges in sorted(boundary_edges.items()):
        cell = by_id.get(micro_id)
        if cell is None or not cell.is_coastal:
            continue
        continent_center = continent_centers.get(cell.continent_id, (cell.center_x, cell.center_y))
        for i, (a, b) in enumerate(edges):
            mx = (a[0] + b[0]) / 2.0
            my = (a[1] + b[1]) / 2.0
            dx = mx - continent_center[0]
            dy = my - continent_center[1]
            length = max(1e-9, math.hypot(dx, dy))
            edge_length = math.hypot(a[0] - b[0], a[1] - b[1])
            shelf = _clamp(0.007 + edge_length * 0.65, 0.006, 0.018)
            ox = dx / length * shelf
            oy = dy / length * shelf
            polygon = [
                (round(a[0], 6), round(a[1], 6)),
                (round(b[0], 6), round(b[1], 6)),
                (round(b[0] + ox, 6), round(b[1] + oy, 6)),
                (round(a[0] + ox, 6), round(a[1] + oy, 6)),
            ]
            out.append(
                WaterCell(
                    water_id=f"{micro_id}:ocean:{i}",
                    kind="ocean",
                    region_id=cell.region_id,
                    continent_id=cell.continent_id,
                    center_x=round(mx + ox * 0.5, 6),
                    center_y=round(my + oy * 0.5, 6),
                    polygon=polygon,
                    depth=_water_cell_depth("ocean", cell),
                    moisture_source=round(cell.moisture, 4),
                )
            )
    return out


def _build_water_cells(micro_cells: list[MicroRegionCell]) -> list[WaterCell]:
    return sorted(
        _build_ocean_water_cells(micro_cells) + _build_lake_water_cells(micro_cells),
        key=lambda w: (w.continent_id, w.kind, w.water_id),
    )


def build_world_map_debug_data(geometry: WorldMapGeometry) -> dict[str, object]:
    """Return compact map diagnostics that are easier to diff than SVG paths."""
    terrain_counts: dict[str, int] = {}
    for cell in geometry.micro_cells:
        terrain_counts[cell.terrain_family] = terrain_counts.get(cell.terrain_family, 0) + 1
    water_counts: dict[str, int] = {}
    for cell in geometry.water_cells:
        water_counts[cell.kind] = water_counts.get(cell.kind, 0) + 1
    river_lengths = [
        round(
            sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(r.points, r.points[1:])),
            5,
        )
        for r in geometry.rivers
    ]
    return {
        "world": geometry.world,
        "version": geometry.version,
        "graph_backend": {
            "decision": "keep_lightweight_micro_cell_graph",
            "reason": "Voronoi micro-cells already drive settlement projection, routes, river carving, SVG fills, and deterministic tests; a reusable Delaunay/corner graph can be revisited after visuals stabilize.",
        },
        "counts": {
            "regions": len(geometry.cells),
            "micro_cells": len(geometry.micro_cells),
            "water_cells": len(geometry.water_cells),
            "rivers": len(geometry.rivers),
            "river_channels": len(geometry.river_channels),
            "features": len(geometry.features),
        },
        "terrain_counts": dict(sorted(terrain_counts.items())),
        "water_counts": dict(sorted(water_counts.items())),
        "river_lengths": river_lengths,
        "major_rivers": sum(1 for r in geometry.rivers if r.river_class == "major_river"),
        "moisture": {
            "min": round(min((c.moisture for c in geometry.micro_cells), default=0.0), 4),
            "max": round(max((c.moisture for c in geometry.micro_cells), default=0.0), 4),
            "avg": round(
                sum(c.moisture for c in geometry.micro_cells) / max(1, len(geometry.micro_cells)),
                4,
            ),
        },
        "elevation": {
            "min": round(min((c.elevation for c in geometry.micro_cells), default=0.0), 4),
            "max": round(max((c.elevation for c in geometry.micro_cells), default=0.0), 4),
            "avg": round(
                sum(c.elevation for c in geometry.micro_cells) / max(1, len(geometry.micro_cells)),
                4,
            ),
        },
    }


def build_world_map_geometry(
    *,
    world: str = "default",
    db_path: Path | str | None = None,
    save_db_path: Path | str | None = None,
    map_seed: object | None = None,
) -> WorldMapGeometry:
    """Derive stable polygon-map geometry from configured regions and routes."""
    world_id = (world or "").strip() or "default"
    continents = list_continents(world=world_id, db_path=db_path)
    all_regions = list_regions(world=world_id, db_path=db_path)
    continent_hints, region_hints = _load_map_hints(world=world_id, db_path=db_path)
    resolved_map_seed = _world_map_seed(
        world=world_id,
        db_path=db_path,
        save_db_path=save_db_path,
        map_seed=map_seed,
    )
    hulls = _continent_hulls(list(continents), continent_hints, map_seed=resolved_map_seed)
    points: dict[str, Point] = {}
    polys: dict[str, list[Point]] = {}
    micro_cells: list[MicroRegionCell] = []
    micro_rivers: list[RiverPath] = []

    for continent in continents:
        regs = [r for r in all_regions if r.continent_id == continent.continent_id]
        if not regs:
            continue
        hull = hulls[continent.continent_id]
        c_points = {r.region_id: _initial_region_point(r, hull, map_seed=resolved_map_seed) for r in regs}
        c_edges = _undirected_intra_edges(regs, world=world_id, db_path=db_path)
        c_points = _relax_points(regs, c_points, c_edges, hull)
        c_polys = _voronoi_cells(regs, c_points, hull)
        continent_micro = _build_physical_micro_cells_for_continent(
            world=world_id,
            continent_id=continent.continent_id,
            hull=hull,
            region_count=len(regs),
            map_seed=resolved_map_seed,
        )
        _, river_cell_ids = _build_micro_rivers(continent_micro)
        continent_micro = _moisten_micro_cells(continent_micro, river_cell_ids)
        continent_micro = _assign_regions_to_micro_cells(
            regions=regs,
            micro_cells=continent_micro,
            river_cell_ids=river_cell_ids,
            region_hints=region_hints,
        )
        continent_rivers, _ = _build_micro_rivers(continent_micro)
        continent_micro = _apply_river_influence_to_micro_cells(continent_micro, continent_rivers)
        micro_cells.extend(continent_micro)
        micro_rivers.extend(continent_rivers)
        polys.update(c_polys)

    polys = _aggregate_region_polygons(all_regions, micro_cells, polys)
    points = {
        rid: _centroid(poly)
        for rid, poly in polys.items()
        if poly
    }

    features: list[RegionFeature] = []
    cells: list[RegionCell] = []
    micro_by_region: dict[str, list[MicroRegionCell]] = {}
    for micro in micro_cells:
        micro_by_region.setdefault(micro.region_id, []).append(micro)
    for region in all_regions:
        poly = polys.get(region.region_id, [])
        cx, cy = _centroid(poly) if poly else points.get(region.region_id, (0.5, 0.5))
        try:
            env = region_environment(region.region_id, world=world_id, db_path=db_path)
            elev = float(env.local_elev_m)
            moist = float(env.hydro_idx)
            rugged = float(env.ruggedness)
        except LookupError:
            elev, moist, rugged = 0.0, 0.5, 0.35
        r_features = _features_for_region(
            region,
            poly,
            (cx, cy),
            micro_by_region.get(region.region_id, []),
            map_seed=resolved_map_seed,
        )
        features.extend(r_features)
        cells.append(
            RegionCell(
                region_id=region.region_id,
                continent_id=region.continent_id,
                center_x=cx,
                center_y=cy,
                polygon=[(round(x, 6), round(y, 6)) for x, y in poly],
                elevation=round(elev, 4),
                moisture=round(_clamp(moist, 0.0, 1.0), 4),
                ruggedness=round(_clamp(rugged, 0.0, 1.0), 4),
                terrain_family=_terrain_family(region),
                is_coastal=_terrain_family(region) == "coast",
                feature_ids=[f.feature_id for f in r_features],
            )
        )

    cell_map = {c.region_id: c for c in cells}
    rivers = micro_rivers or _build_rivers(cell_map, world=world_id, db_path=db_path)
    river_channels = _build_river_channels(rivers)
    micro_cells = _carve_river_channels_from_micro_cells(micro_cells, river_channels)
    water_cells = _build_water_cells(micro_cells)
    return WorldMapGeometry(
        world=world_id,
        version=MAP_GEOMETRY_VERSION,
        width=1.0,
        height=1.0,
        cells=sorted(cells, key=lambda c: (c.continent_id, c.region_id)),
        micro_cells=sorted(micro_cells, key=lambda c: (c.continent_id, c.micro_id)),
        features=sorted(features, key=lambda f: (f.region_id, f.feature_id)),
        edges=_build_region_edges(all_regions, points, micro_cells, world=world_id, db_path=db_path),
        rivers=rivers,
        river_channels=river_channels,
        water_cells=water_cells,
    )

