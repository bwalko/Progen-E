"""Generated world-map geometry for SVG and settlement-local geography.

The canonical geography remains the config region/route graph. This module derives
stable polygon cells and feature anchors from that graph so renderers and town
placement can share one geography layer.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from library.geography import (
    Region,
    list_continents,
    list_regions,
    list_routes_from,
    region_environment,
)


MAP_GEOMETRY_VERSION = "polygonal-v1"
Point = tuple[float, float]


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
class RiverPath:
    river_id: str
    from_region_id: str
    to_region_id: str
    points: list[Point]
    flow: float
    river_class: str


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
class WorldMapGeometry:
    world: str
    version: str
    width: float
    height: float
    cells: list[RegionCell]
    features: list[RegionFeature]
    edges: list[RegionEdge]
    rivers: list[RiverPath]

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
            "features": [asdict(f) for f in self.features],
            "edges": [asdict(e) for e in self.edges],
            "rivers": [asdict(r) for r in self.rivers],
        }


def _stable_seed(*parts: object) -> int:
    h = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


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
    if k in {"river", "ford", "wadi", "lake", "marsh", "spring"}:
        return "water"
    if k in {"ridge", "mountain", "pass", "mesa"}:
        return "relief"
    if k in {"forest", "clearing"}:
        return "vegetation"
    if k in {"well", "meadow", "hill"}:
        return "settlement_support"
    return "landform"


def _initial_region_point(region: Region, box: tuple[float, float, float, float]) -> Point:
    x0, y0, x1, y1 = box
    rng = random.Random(_stable_seed(MAP_GEOMETRY_VERSION, region.world, region.region_id, "seed-point"))
    x = x0 + (x1 - x0) * rng.uniform(0.22, 0.78)
    y = y0 + (y1 - y0) * rng.uniform(0.22, 0.78)
    text = _region_text(region)
    if "coast" in text or "port" in text or "shore" in text or "littoral" in text:
        side = _stable_seed(region.region_id, "coast-side") % 4
        if side == 0:
            y = y0 + (y1 - y0) * rng.uniform(0.08, 0.22)
        elif side == 1:
            x = x1 - (x1 - x0) * rng.uniform(0.08, 0.22)
        elif side == 2:
            y = y1 - (y1 - y0) * rng.uniform(0.08, 0.22)
        else:
            x = x0 + (x1 - x0) * rng.uniform(0.08, 0.22)
    if "highland" in text or "mountain" in text or "range" in text or "ridge" in text:
        y = y0 + (y1 - y0) * _clamp((y - y0) / max(1e-9, y1 - y0) * 0.78, 0.18, 0.72)
    if "river" in text or "delta" in text:
        x = x0 + (x1 - x0) * _clamp((x - x0) / max(1e-9, x1 - x0), 0.24, 0.76)
    return (x, y)


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
    box: tuple[float, float, float, float],
) -> dict[str, Point]:
    if len(regions) <= 1:
        return points
    x0, y0, x1, y1 = box
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
            points[rid] = (
                _clamp(x + dx[rid], x0 + pad_x, x1 - pad_x),
                _clamp(y + dy[rid], y0 + pad_y, y1 - pad_y),
            )
    return points


def _voronoi_cells(
    regions: list[Region],
    points: dict[str, Point],
    box: tuple[float, float, float, float],
) -> dict[str, list[Point]]:
    cells: dict[str, list[Point]] = {}
    base = _bbox_polygon(*box)
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
            d = min(box[2] - box[0], box[3] - box[1]) * 0.035
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
        kinds.extend(["river", "ford"])
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


def _features_for_region(region: Region, poly: list[Point], center: Point) -> list[RegionFeature]:
    rng = random.Random(_stable_seed(MAP_GEOMETRY_VERSION, region.world, region.region_id, "features"))
    vertices = poly or [center]
    features: list[RegionFeature] = []
    for i, kind in enumerate(_region_feature_kinds(region)):
        target = vertices[(i * 2 + _stable_seed(region.region_id, kind) % len(vertices)) % len(vertices)]
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


def _edge_key(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))  # type: ignore[return-value]


def _build_region_edges(
    regions: Iterable[Region],
    points: dict[str, Point],
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
            out.append(
                RegionEdge(
                    from_region_id=key[0],
                    to_region_id=key[1],
                    route_type=route.route_type,
                    friction=route.friction,
                    points=[a, b],
                    edge_class="sea_route" if route.route_type.strip().lower() == "sea" else "land_route",
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
                flow=flow,
                river_class="major_river" if flow >= 0.7 else "minor_river",
            )
        )
    return rivers


def build_world_map_geometry(
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> WorldMapGeometry:
    """Derive stable polygon-map geometry from configured regions and routes."""
    world_id = (world or "").strip() or "default"
    continents = list_continents(world=world_id, db_path=db_path)
    all_regions = list_regions(world=world_id, db_path=db_path)
    boxes = _continent_boxes([c.continent_id for c in continents])
    points: dict[str, Point] = {}
    polys: dict[str, list[Point]] = {}

    for continent in continents:
        regs = [r for r in all_regions if r.continent_id == continent.continent_id]
        if not regs:
            continue
        box = boxes[continent.continent_id]
        c_points = {r.region_id: _initial_region_point(r, box) for r in regs}
        c_edges = _undirected_intra_edges(regs, world=world_id, db_path=db_path)
        c_points = _relax_points(regs, c_points, c_edges, box)
        c_polys = _voronoi_cells(regs, c_points, box)
        points.update(c_points)
        polys.update(c_polys)

    features: list[RegionFeature] = []
    cells: list[RegionCell] = []
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
        r_features = _features_for_region(region, poly, (cx, cy))
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
    return WorldMapGeometry(
        world=world_id,
        version=MAP_GEOMETRY_VERSION,
        width=1.0,
        height=1.0,
        cells=sorted(cells, key=lambda c: (c.continent_id, c.region_id)),
        features=sorted(features, key=lambda f: (f.region_id, f.feature_id)),
        edges=_build_region_edges(all_regions, points, world=world_id, db_path=db_path),
        rivers=_build_rivers(cell_map, world=world_id, db_path=db_path),
    )

