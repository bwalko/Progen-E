"""SVG rendering for generated world-map geometry."""

from __future__ import annotations

import hashlib
import html
import json
import math
import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from library.world_map_geometry import (
    MicroRegionCell,
    Point,
    RegionCell,
    RegionFeature,
    WorldMapGeometry,
    project_local_point_to_region_footprint,
    project_world_point_to_region_footprint,
)


@dataclass(frozen=True)
class SettlementMapOverlay:
    settlement_id: str
    region_id: str
    display_name: str
    x: float
    y: float
    population: int
    status: str


@dataclass(frozen=True)
class FeatureMapOverlay:
    feature_id: str
    region_id: str
    kind: str
    display_name: str
    x: float
    y: float
    etymology: str | None = None


@dataclass(frozen=True)
class PolityMapOverlay:
    region_id: str
    polity_id: str
    polity_name: str
    polity_type_id: str
    color: str


@dataclass(frozen=True)
class WorldMapOverlays:
    settlements: list[SettlementMapOverlay]
    polities_by_region_id: dict[str, PolityMapOverlay]
    features: list[FeatureMapOverlay] = field(default_factory=list)


_CELL_COLORS = {
    "coast": "#b6a58a",
    "riverland": "#76a766",
    "highlands": "#d8d9cf",
    "forest": "#2d7c61",
    "drylands": "#a99c80",
    "plains": "#86ad67",
}

_FEATURE_COLORS = {
    "coast": "#4f83a2",
    "water": "#3f8fbc",
    "relief": "#7b6757",
    "vegetation": "#3f7f4c",
    "settlement_support": "#8b6f3d",
    "landform": "#6f684d",
}

_FEATURE_CLASS_BY_KIND = {
    "bay": "coast",
    "coast": "coast",
    "harbor": "coast",
    "river": "water",
    "stream": "water",
    "ford": "water",
    "lake": "water",
    "marsh": "water",
    "spring": "water",
    "wadi": "water",
    "ridge": "relief",
    "mountain": "relief",
    "pass": "relief",
    "forest": "vegetation",
    "grove": "vegetation",
    "clearing": "vegetation",
}

_POLITY_COLORS = (
    "#ba6f4d",
    "#8f7047",
    "#6c8a54",
    "#4f7c8e",
    "#755d95",
    "#9a5d75",
    "#92864f",
    "#5f8368",
)

DEFAULT_MAX_SETTLEMENT_OVERLAYS = 500

_LabelBox = tuple[float, float, float, float]


def _stable_seed(*parts: object) -> int:
    h = hashlib.sha256(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:16], 16)


def _stable_color(value: object) -> str:
    return _POLITY_COLORS[_stable_seed(value) % len(_POLITY_COLORS)]


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    raw = color.strip().lstrip("#")
    if len(raw) != 6:
        return (128, 128, 128)
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in rgb)


def _mix_color(color: str, target: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    r, g, b = _hex_to_rgb(color)
    tr, tg, tb = _hex_to_rgb(target)
    return _rgb_to_hex(
        (
            round(r + (tr - r) * amount),
            round(g + (tg - g) * amount),
            round(b + (tb - b) * amount),
        )
    )


def _terrain_tint(color: str, cell: MicroRegionCell) -> str:
    grain = ((_stable_seed("micro-fill", cell.micro_id) % 1000) / 999.0) - 0.5
    if grain >= 0.0:
        color = _mix_color(color, "#f4efd2", grain * 0.13)
    else:
        color = _mix_color(color, "#314436", abs(grain) * 0.10)
    if cell.elevation >= 0.62:
        color = _mix_color(color, "#ddd9c4", min(0.18, (cell.elevation - 0.62) * 0.36))
    if cell.moisture >= 0.66 and not cell.is_coastal:
        color = _mix_color(color, "#1f6f52", min(0.16, (cell.moisture - 0.66) * 0.28))
    return color


def _scale(point: Point, width: int, height: int, pad: int) -> tuple[float, float]:
    x, y = point
    return (pad + x * (width - pad * 2), pad + y * (height - pad * 2))


def _jagged_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    rng: random.Random,
    *,
    levels: int,
    amplitude: float,
) -> list[tuple[float, float]]:
    if levels <= 0:
        return [a, b]
    ax, ay = a
    bx, by = b
    mx = (ax + bx) / 2.0
    my = (ay + by) / 2.0
    dx = bx - ax
    dy = by - ay
    length = max(1e-9, math.hypot(dx, dy))
    nx = -dy / length
    ny = dx / length
    offset = rng.uniform(-amplitude, amplitude) * length
    mid = (mx + nx * offset, my + ny * offset)
    left = _jagged_segment(a, mid, rng, levels=levels - 1, amplitude=amplitude * 0.62)
    right = _jagged_segment(mid, b, rng, levels=levels - 1, amplitude=amplitude * 0.62)
    return left[:-1] + right


def _noisy_closed_points(
    points: list[tuple[float, float]],
    seed: int,
    *,
    levels: int = 2,
    amplitude: float = 0.038,
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points
    rng = random.Random(seed)
    out: list[tuple[float, float]] = []
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        seg = _jagged_segment(a, b, rng, levels=levels, amplitude=amplitude)
        out.extend(seg[:-1])
    return out


def _lerp_point(a: tuple[float, float], b: tuple[float, float], t: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _edge_key(a: Point, b: Point) -> tuple[Point, Point]:
    return tuple(sorted(((round(a[0], 5), round(a[1], 5)), (round(b[0], 5), round(b[1], 5)))))  # type: ignore[return-value]


def _constrained_noisy_segment(
    a: tuple[float, float],
    b: tuple[float, float],
    p: tuple[float, float],
    q: tuple[float, float],
    rng: random.Random,
    *,
    min_length: float,
    amplitude: float,
) -> list[tuple[float, float]]:
    """Build a mapgen2-style noisy edge constrained inside the quad a-p-b-q."""

    def recur(
        start: tuple[float, float],
        end: tuple[float, float],
        left: tuple[float, float],
        right: tuple[float, float],
    ) -> list[tuple[float, float]]:
        if math.dist(start, end) < min_length:
            return [end]
        start_left = _lerp_point(start, left, 0.5)
        end_left = _lerp_point(end, left, 0.5)
        start_right = _lerp_point(start, right, 0.5)
        end_right = _lerp_point(end, right, 0.5)
        division = 0.5 * (1.0 - amplitude) + rng.random() * amplitude
        center = _lerp_point(left, right, division)
        return recur(start, center, start_left, start_right) + recur(center, end, end_left, end_right)

    return [a] + recur(a, b, p, q)


def _poly_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    first = points[0]
    rest = " ".join(f"L {x:.1f} {y:.1f}" for x, y in points[1:])
    return f"M {first[0]:.1f} {first[1]:.1f} {rest} Z"


def _line_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    first = points[0]
    rest = " ".join(f"L {x:.1f} {y:.1f}" for x, y in points[1:])
    return f"M {first[0]:.1f} {first[1]:.1f} {rest}"


def _smooth_line_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    if len(points) < 3:
        return _line_path(points)
    parts = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for i in range(1, len(points)):
        current = points[i]
        if i < len(points) - 1:
            mid = ((current[0] + points[i + 1][0]) / 2.0, (current[1] + points[i + 1][1]) / 2.0)
            parts.append(f"Q {current[0]:.1f} {current[1]:.1f} {mid[0]:.1f} {mid[1]:.1f}")
        else:
            parts.append(f"T {current[0]:.1f} {current[1]:.1f}")
    return " ".join(parts)


def _dedupe_render_points(points: list[tuple[float, float]], *, min_distance: float = 0.55) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for point in points:
        if out and math.dist(out[-1], point) < min_distance:
            continue
        out.append(point)
    return out


def _river_render_points(
    points: list[tuple[float, float]],
    river_id: str,
    *,
    flow: float,
) -> list[tuple[float, float]]:
    points = _dedupe_render_points(points, min_distance=0.8)
    if len(points) < 2:
        return points
    rng = random.Random(_stable_seed("river-meander", river_id))
    out: list[tuple[float, float]] = [points[0]]
    amplitude = 1.2 + flow * 2.8
    for i, (a, b) in enumerate(zip(points, points[1:])):
        ax, ay = a
        bx, by = b
        dx = bx - ax
        dy = by - ay
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            continue
        nx = -dy / length
        ny = dx / length
        steps = max(1, min(5, int(length / 22.0) + 1))
        for step in range(1, steps + 1):
            t = step / steps
            x = ax + dx * t
            y = ay + dy * t
            if step < steps:
                fade = math.sin(math.pi * t)
                wiggle = rng.uniform(-amplitude, amplitude) * fade
                x += nx * wiggle
                y += ny * wiggle
            elif i < len(points) - 2:
                bend = rng.uniform(-amplitude * 0.55, amplitude * 0.55)
                x += nx * bend
                y += ny * bend
            out.append((x, y))
    return _dedupe_render_points(out, min_distance=0.7)


def _cell_fill(cell: RegionCell, overlays: WorldMapOverlays | None) -> str:
    if overlays is not None and cell.region_id in overlays.polities_by_region_id:
        return overlays.polities_by_region_id[cell.region_id].color
    return _CELL_COLORS.get(cell.terrain_family, _CELL_COLORS["plains"])


def _micro_cell_fill(cell: MicroRegionCell, overlays: WorldMapOverlays | None) -> str:
    if overlays is not None and cell.region_id in overlays.polities_by_region_id:
        return overlays.polities_by_region_id[cell.region_id].color
    if cell.elevation >= 0.72:
        base = "#f0f1ea" if cell.moisture >= 0.52 else "#c5c2ad"
    elif cell.is_coastal:
        base = "#a99b83"
    elif cell.moisture >= 0.78:
        base = "#1f7660"
    elif cell.moisture >= 0.58:
        base = "#3d8d62"
    elif cell.moisture <= 0.28:
        base = "#9f957b"
    else:
        base = _CELL_COLORS.get(cell.terrain_family, _CELL_COLORS["plains"])
    return _terrain_tint(base, cell)


def _micro_blend_fill(
    first: MicroRegionCell,
    second: MicroRegionCell,
    overlays: WorldMapOverlays | None,
) -> str:
    return _mix_color(_micro_cell_fill(first, overlays), _micro_cell_fill(second, overlays), 0.5)


def _micro_mottle_fill(cell: MicroRegionCell, overlays: WorldMapOverlays | None) -> str:
    base = _micro_cell_fill(cell, overlays)
    grain = (_stable_seed("terrain-mottle", cell.micro_id) % 1000) / 999.0
    if grain < 0.42:
        return _mix_color(base, "#223f35", 0.18 + grain * 0.12)
    if grain > 0.72:
        return _mix_color(base, "#d8d5b9", 0.10 + (grain - 0.72) * 0.18)
    return _mix_color(base, "#6b8558", 0.10)


def _feature_radius(feature: RegionFeature) -> float:
    if feature.feature_class in {"coast", "water"}:
        return 3.7
    if feature.feature_class == "relief":
        return 3.3
    return 2.8


def _feature_is_near_settlement(
    feature: RegionFeature,
    settlements: list[SettlementMapOverlay],
    *,
    max_distance: float = 0.065,
) -> bool:
    if not settlements:
        return True
    max_d2 = float(max_distance) * float(max_distance)
    for settlement in settlements:
        if settlement.region_id != feature.region_id:
            continue
        dx = settlement.x - feature.x
        dy = settlement.y - feature.y
        if dx * dx + dy * dy <= max_d2:
            return True
    return False


def _label_box(x: float, y: float, text: str, font_size: float, *, anchor: str = "start") -> _LabelBox:
    width = max(10.0, len(text) * font_size * 0.54)
    height = font_size * 1.25
    if anchor == "middle":
        x0 = x - width / 2.0
    elif anchor == "end":
        x0 = x - width
    else:
        x0 = x
    return (x0 - 2.0, y - height + 1.0, x0 + width + 2.0, y + 3.0)


def _boxes_intersect(a: _LabelBox, b: _LabelBox) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _claim_label(occupied: list[_LabelBox], box: _LabelBox, *, bounds: tuple[float, float]) -> bool:
    width, height = bounds
    if box[0] < 0.0 or box[1] < 0.0 or box[2] > width or box[3] > height:
        return False
    if any(_boxes_intersect(box, other) for other in occupied):
        return False
    occupied.append(box)
    return True


def _cell_bbox(cell: RegionCell) -> tuple[float, float, float, float]:
    if not cell.polygon:
        return (cell.center_x, cell.center_y, cell.center_x, cell.center_y)
    xs = [p[0] for p in cell.polygon]
    ys = [p[1] for p in cell.polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def _micro_edge_segments(
    micro_cells: list[MicroRegionCell],
) -> tuple[
    list[tuple[Point, Point]],
    list[tuple[Point, Point]],
    list[tuple[Point, Point, MicroRegionCell, MicroRegionCell]],
]:
    edge_owner: dict[tuple[Point, Point], MicroRegionCell] = {}
    region_edges: list[tuple[Point, Point]] = []
    coast_edges: list[tuple[Point, Point]] = []
    blend_edges: list[tuple[Point, Point, MicroRegionCell, MicroRegionCell]] = []
    for cell in micro_cells:
        pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            key = tuple(sorted((a, b)))  # type: ignore[assignment]
            other = edge_owner.pop(key, None)
            if other is None:
                edge_owner[key] = cell
            elif other.region_id != cell.region_id:
                region_edges.append((a, b))
                blend_edges.append((a, b, other, cell))
            else:
                blend_edges.append((a, b, other, cell))
    coast_edges.extend(edge_owner.keys())
    return region_edges, coast_edges, blend_edges


def _build_micro_noisy_edge_paths(
    micro_cells: list[MicroRegionCell],
    *,
    width: int,
    height: int,
    pad: int,
    seed: object,
) -> dict[tuple[Point, Point], list[tuple[float, float]]]:
    edge_owner: dict[tuple[Point, Point], MicroRegionCell] = {}
    edge_pairs: dict[tuple[Point, Point], tuple[MicroRegionCell, MicroRegionCell | None]] = {}
    for cell in micro_cells:
        pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
        for i, a in enumerate(pts):
            b = pts[(i + 1) % len(pts)]
            key = _edge_key(a, b)
            other = edge_owner.pop(key, None)
            if other is None:
                edge_owner[key] = cell
            else:
                edge_pairs[key] = (other, cell)
    for key, cell in edge_owner.items():
        edge_pairs[key] = (cell, None)

    paths: dict[tuple[Point, Point], list[tuple[float, float]]] = {}
    for key, (first, second) in edge_pairs.items():
        a, b = key
        scaled_a = _scale(a, width, height, pad)
        scaled_b = _scale(b, width, height, pad)
        first_center = _scale((first.center_x, first.center_y), width, height, pad)
        if second is None:
            mx = (scaled_a[0] + scaled_b[0]) / 2.0
            my = (scaled_a[1] + scaled_b[1]) / 2.0
            second_center = (mx + (mx - first_center[0]) * 1.25, my + (my - first_center[1]) * 1.25)
            min_length = 3.8
            amplitude = 0.78
        else:
            second_center = _scale((second.center_x, second.center_y), width, height, pad)
            if first.region_id != second.region_id:
                min_length = 5.0
                amplitude = 0.58
            else:
                min_length = 7.5
                amplitude = 0.34
        paths[key] = _constrained_noisy_segment(
            scaled_a,
            scaled_b,
            first_center,
            second_center,
            random.Random(_stable_seed(seed, "micro-noisy-edge", key)),
            min_length=min_length,
            amplitude=amplitude,
        )
    return paths


def _oriented_noisy_edge_path(
    paths: dict[tuple[Point, Point], list[tuple[float, float]]],
    a: Point,
    b: Point,
    *,
    width: int,
    height: int,
    pad: int,
) -> list[tuple[float, float]]:
    rounded_a = (round(a[0], 5), round(a[1], 5))
    rounded_b = (round(b[0], 5), round(b[1], 5))
    key = _edge_key(rounded_a, rounded_b)
    path = paths.get(key)
    if path is None:
        return [_scale(rounded_a, width, height, pad), _scale(rounded_b, width, height, pad)]
    return path if key[0] == rounded_a else list(reversed(path))


def _micro_noisy_polygon_points(
    cell: MicroRegionCell,
    paths: dict[tuple[Point, Point], list[tuple[float, float]]],
    *,
    width: int,
    height: int,
    pad: int,
) -> list[tuple[float, float]]:
    pts = [(round(x, 5), round(y, 5)) for x, y in cell.polygon]
    out: list[tuple[float, float]] = []
    for i, a in enumerate(pts):
        b = pts[(i + 1) % len(pts)]
        segment = _oriented_noisy_edge_path(paths, a, b, width=width, height=height, pad=pad)
        if not out:
            out.extend(segment)
        else:
            out.extend(segment[1:])
    return out


def _stitch_edge_chains(edges: list[tuple[Point, Point]]) -> list[list[Point]]:
    normalized = {_edge_key(a, b) for a, b in edges}
    adjacency: dict[Point, set[Point]] = {}
    for a, b in normalized:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    remaining = set(normalized)
    chains: list[list[Point]] = []

    def walk(start: Point, next_point: Point) -> list[Point]:
        chain = [start, next_point]
        remaining.remove(_edge_key(start, next_point))
        previous = start
        current = next_point
        while True:
            candidates = [
                p
                for p in sorted(adjacency.get(current, ()))
                if p != previous and _edge_key(current, p) in remaining
            ]
            if not candidates:
                candidates = [
                    p for p in sorted(adjacency.get(current, ())) if _edge_key(current, p) in remaining
                ]
            if not candidates:
                break
            next_candidate = candidates[0]
            remaining.remove(_edge_key(current, next_candidate))
            chain.append(next_candidate)
            previous, current = current, next_candidate
            if current == start or len(adjacency.get(current, ())) != 2:
                break
        return chain

    for start in sorted(adjacency):
        if len(adjacency[start]) == 2:
            continue
        for next_point in sorted(adjacency[start]):
            if _edge_key(start, next_point) in remaining:
                chains.append(walk(start, next_point))

    while remaining:
        start, next_point = min(remaining)
        chains.append(walk(start, next_point))

    return chains


def _stroke_path(points: list[tuple[float, float]]) -> str:
    if len(points) > 2 and math.dist(points[0], points[-1]) < 0.75:
        return _poly_path(points[:-1])
    return _line_path(points)


def _stitched_noisy_paths(
    edge_chains: list[list[Point]],
    noisy_edge_paths: dict[tuple[Point, Point], list[tuple[float, float]]],
    *,
    width: int,
    height: int,
    pad: int,
    noisy_edges: bool,
) -> list[str]:
    paths: list[str] = []
    for chain in edge_chains:
        stitched: list[tuple[float, float]] = []
        for a, b in zip(chain, chain[1:]):
            segment = (
                _oriented_noisy_edge_path(noisy_edge_paths, a, b, width=width, height=height, pad=pad)
                if noisy_edges
                else [_scale(a, width, height, pad), _scale(b, width, height, pad)]
            )
            if not stitched:
                stitched.extend(segment)
            else:
                stitched.extend(segment[1:])
        if stitched:
            paths.append(_stroke_path(stitched))
    return paths


def _site_xy(local_geography_json: object, site_slot: object) -> tuple[float, float] | None:
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
    try:
        slot = int(site_slot or 1) - 1
    except (TypeError, ValueError):
        slot = 0
    for site in sites:
        if not isinstance(site, dict):
            continue
        try:
            if int(site.get("settlement_slot", 0)) != slot:
                continue
            return (float(site.get("x", 0.5)), float(site.get("y", 0.5)))
        except (TypeError, ValueError):
            continue
    return None


def _site_world_xy(local_geography_json: object, site_slot: object) -> tuple[float, float] | None:
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
    try:
        slot = int(site_slot or 1) - 1
    except (TypeError, ValueError):
        slot = 0
    for site in sites:
        if not isinstance(site, dict):
            continue
        try:
            if int(site.get("settlement_slot", 0)) != slot:
                continue
            wx = site.get("world_x")
            wy = site.get("world_y")
            if wx is None or wy is None:
                return None
            return (float(wx), float(wy))
        except (TypeError, ValueError):
            continue
    return None


def _named_feature_overlays_from_local_geography(
    *,
    geometry: WorldMapGeometry,
    region_id: str,
    local_geography_json: object,
) -> list[FeatureMapOverlay]:
    if not local_geography_json:
        return []
    try:
        data = json.loads(str(local_geography_json))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    features = data.get("features")
    if not isinstance(features, list):
        return []
    seen: set[str] = set()
    out: list[FeatureMapOverlay] = []
    for idx, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        name = str(feature.get("display_name") or "").strip()
        if not name:
            continue
        fid = str(feature.get("feature_id") or f"{region_id}:local:{idx}").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        try:
            if feature.get("source_world_x") is not None and feature.get("source_world_y") is not None:
                world_xy = (float(feature["source_world_x"]), float(feature["source_world_y"]))
                wx, wy = project_world_point_to_region_footprint(geometry, region_id, world_xy)
            else:
                lx = max(0.04, min(0.96, float(feature.get("x", 0.5))))
                ly = max(0.04, min(0.96, float(feature.get("y", 0.5))))
                wx, wy = project_local_point_to_region_footprint(geometry, region_id, (lx, ly))
        except (TypeError, ValueError):
            continue
        out.append(
            FeatureMapOverlay(
                feature_id=fid,
                region_id=region_id,
                kind=str(feature.get("kind") or "feature"),
                display_name=name,
                x=wx,
                y=wy,
                etymology=str(feature.get("etymology") or "").strip() or None,
            )
        )
    return out


def load_world_map_overlays(
    *,
    geometry: WorldMapGeometry,
    save_db_path: Path | str | None,
    max_settlements: int = DEFAULT_MAX_SETTLEMENT_OVERLAYS,
    include_inactive_settlements: bool = False,
) -> WorldMapOverlays:
    """Load optional settlement and polity overlays from ``save.sqlite``."""
    if save_db_path is None:
        return WorldMapOverlays(settlements=[], polities_by_region_id={}, features=[])
    path = Path(save_db_path)
    if not path.exists():
        return WorldMapOverlays(settlements=[], polities_by_region_id={}, features=[])
    settlement_limit = max(0, int(max_settlements))
    cells = geometry.cell_by_region_id()
    settlements: list[SettlementMapOverlay] = []
    features_by_id: dict[str, FeatureMapOverlay] = {}
    polities: dict[str, PolityMapOverlay] = {}
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        relations = {
            str(r["name"])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        }
        settlement_source = (
            "simulation_settlements_readable"
            if "simulation_settlements_readable" in relations
            else "simulation_settlements"
        )
        if "simulation_settlements" in relations and settlement_limit > 0 and cells:
            region_ids = sorted(cells)
            placeholders = ", ".join("?" for _ in region_ids)
            select_sql = f"""
                SELECT settlement_id, region_id, display_name, population_cap, status,
                       site_slot, local_geography_json
                FROM {settlement_source}
                WHERE region_id IN ({placeholders})
                  AND {{status_clause}}
                LIMIT ?
                """
            rows = conn.execute(
                select_sql.format(status_clause="status = 'active'"),
                (*region_ids, settlement_limit),
            ).fetchall()
            if include_inactive_settlements and len(rows) < settlement_limit:
                rows.extend(
                    conn.execute(
                        select_sql.format(status_clause="(status IS NULL OR status != 'active')"),
                        (*region_ids, settlement_limit - len(rows)),
                    ).fetchall()
                )
            for row in rows:
                rid = str(row["region_id"] or "").strip()
                cell = cells.get(rid)
                if cell is None:
                    continue
                world_xy = _site_world_xy(row["local_geography_json"], row["site_slot"])
                local = _site_xy(row["local_geography_json"], row["site_slot"])
                if world_xy is None and local is None:
                    rng = random.Random(_stable_seed(geometry.version, row["settlement_id"], "overlay"))
                    local = (0.5 + rng.uniform(-0.16, 0.16), 0.5 + rng.uniform(-0.16, 0.16))
                if world_xy is not None:
                    world_x, world_y = project_world_point_to_region_footprint(
                        geometry, rid, world_xy
                    )
                else:
                    assert local is not None
                    lx = max(0.04, min(0.96, local[0]))
                    ly = max(0.04, min(0.96, local[1]))
                    world_x, world_y = project_local_point_to_region_footprint(geometry, rid, (lx, ly))
                settlements.append(
                    SettlementMapOverlay(
                        settlement_id=str(row["settlement_id"] or ""),
                        region_id=rid,
                        display_name=str(row["display_name"] or row["settlement_id"] or ""),
                        x=world_x,
                        y=world_y,
                        population=max(0, int(row["population_cap"] or 0)),
                        status=str(row["status"] or ""),
                    )
                )
                for feature in _named_feature_overlays_from_local_geography(
                    geometry=geometry,
                    region_id=rid,
                    local_geography_json=row["local_geography_json"],
                ):
                    features_by_id.setdefault(feature.feature_id, feature)
        if {"simulation_polity_territory", "simulation_polities"}.issubset(relations):
            rows = conn.execute(
                """
                SELECT t.target_id AS region_id, p.polity_id, p.name AS polity_name,
                       p.polity_type_id
                FROM simulation_polity_territory t
                JOIN simulation_polities p ON p.polity_id = t.polity_id
                WHERE t.until_sim_year IS NULL
                  AND t.target_kind = 'region'
                ORDER BY t.target_id, p.polity_id
                """
            ).fetchall()
            for row in rows:
                rid = str(row["region_id"] or "").strip()
                pid = str(row["polity_id"] or "").strip()
                if not rid or rid in polities:
                    continue
                polities[rid] = PolityMapOverlay(
                    region_id=rid,
                    polity_id=pid,
                    polity_name=str(row["polity_name"] or pid),
                    polity_type_id=str(row["polity_type_id"] or ""),
                    color=_stable_color(pid),
                )
    return WorldMapOverlays(
        settlements=settlements,
        polities_by_region_id=polities,
        features=sorted(features_by_id.values(), key=lambda f: (f.region_id, f.feature_id)),
    )


def render_world_map_svg(
    geometry: WorldMapGeometry,
    *,
    width: int = 1200,
    height: int = 800,
    noisy_edges: bool = True,
    labels: bool = True,
    overlays: WorldMapOverlays | None = None,
    max_feature_labels: int = 18,
    max_settlement_labels: int = 24,
    max_settlements: int = DEFAULT_MAX_SETTLEMENT_OVERLAYS,
) -> str:
    """Render generated world geometry to a self-contained SVG string."""
    pad = 36
    zoom_script = (
        f"const zf=(svg)=>{{const s=svg.viewBox.baseVal;const z=Math.max(.001,{width}/s.width);"
        "const m=Math.max(.88,Math.min(2.05,Math.pow(z,.35)));"
        "svg.querySelectorAll('.region-label').forEach(e=>e.style.fontSize=(11*m/z)+'px');"
        "svg.querySelectorAll('.feature-label').forEach(e=>e.style.fontSize=(9*m/z)+'px');"
        "svg.querySelectorAll('.settlement-label').forEach(e=>e.style.fontSize=(9.5*m/z)+'px');"
        "svg.querySelectorAll('.feature-label[data-point-x],.settlement-label[data-point-x]').forEach(e=>{"
        "const px=+e.dataset.pointX,py=+e.dataset.pointY,dx=+e.dataset.dx||0,dy=+e.dataset.dy||0;"
        "e.setAttribute('x',px+dx/z);e.setAttribute('y',py+dy/z);});"
        "svg.querySelectorAll('.feature').forEach(e=>{const b=+e.dataset.baseR||2;e.setAttribute('r',Math.max(.35,Math.min(3.8,b*m/z)));});"
        "svg.querySelectorAll('.settlement').forEach(e=>{const b=+e.dataset.baseR||2;e.setAttribute('r',Math.max(.38,Math.min(3.2,b*m/z)));});};"
    )
    zoom_handlers = (
        f"data-original-viewbox='0 0 {width} {height}' "
        f"onwheel=\"{zoom_script}const s=this.viewBox.baseVal;const k=event.deltaY>0?1.16:.86;"
        "const r=this.getBoundingClientRect();const px=(event.clientX-r.left)/r.width;"
        "const py=(event.clientY-r.top)/r.height;const nx=s.width*k,ny=s.height*k;"
        "s.x+=s.width*px-nx*px;s.y+=s.height*py-ny*py;s.width=nx;s.height=ny;"
        "zf(this);event.preventDefault();event.stopPropagation();\" "
        "onpointerdown=\"if(event.target.closest('[data-settlement-id],[data-feature-id],[data-region-id],[data-region-label]')){this.dataset.pan='0';this.dataset.dragged='0';return;}this.dataset.pan='1';this.dataset.dragged='0';this.dataset.px=event.clientX;this.dataset.py=event.clientY;"
        "this.dataset.vx=this.viewBox.baseVal.x;this.dataset.vy=this.viewBox.baseVal.y;"
        "this.setPointerCapture(event.pointerId);\" "
        f"onpointermove=\"{zoom_script}if(this.dataset.pan!=='1')return;const dx=event.clientX-this.dataset.px;const dy=event.clientY-this.dataset.py;"
        "if(Math.hypot(dx,dy)>3)this.dataset.dragged='1';const s=this.viewBox.baseVal;"
        "const r=this.getBoundingClientRect();s.x=+this.dataset.vx-(event.clientX-this.dataset.px)*s.width/r.width;"
        "s.y=+this.dataset.vy-(event.clientY-this.dataset.py)*s.height/r.height;zf(this);\" "
        "onpointerup=\"this.dataset.pan='0';\" onpointerleave=\"this.dataset.pan='0';\""
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Generated world map" {zoom_handlers}>',
        "<defs>",
        '<filter id="terrain-warp" x="-8%" y="-8%" width="116%" height="116%">',
        f'<feTurbulence type="fractalNoise" baseFrequency="0.014 0.022" numOctaves="4" seed="{_stable_seed(geometry.version, "terrain-warp") % 997}" result="warpNoise" />',
        '<feDisplacementMap in="SourceGraphic" in2="warpNoise" scale="7" xChannelSelector="R" yChannelSelector="G" />',
        "</filter>",
        '<filter id="terrain-soften" x="-14%" y="-14%" width="128%" height="128%">',
        '<feGaussianBlur stdDeviation="2.6" />',
        "</filter>",
        '<filter id="terrain-mottle-soften" x="-20%" y="-20%" width="140%" height="140%">',
        '<feGaussianBlur stdDeviation="5.0" />',
        "</filter>",
        '<filter id="terrain-grain" x="0" y="0" width="100%" height="100%">',
        f'<feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="{_stable_seed(geometry.version, "terrain-grain") % 997}" result="grain" />',
        '<feColorMatrix in="grain" type="matrix" values="0 0 0 0 0.50 0 0 0 0 0.47 0 0 0 0 0.38 0 0 0 .30 0" />',
        "</filter>",
        "</defs>",
        "<style>",
        ".cell{stroke:#74694f;stroke-width:1.0;stroke-linejoin:round}.micro-cell{stroke:none}.terrain-blend{stroke-linecap:butt;stroke-linejoin:round;pointer-events:none}.coast-beach,.coast-shadow{stroke-linecap:butt;stroke-linejoin:round;pointer-events:none}.terrain-mottle,.terrain-texture{mix-blend-mode:soft-light;pointer-events:none}.region-boundary{stroke:#151b2d;stroke-width:.45;stroke-linecap:butt}.coast-beach{stroke:#b8aa8d;stroke-width:3.2}.coast-shadow{stroke:#2d3557;stroke-width:2.6}.coast-line{stroke:#20263d;stroke-width:1.55;stroke-linecap:butt;stroke-linejoin:round}.river{stroke:#2a8bc8;stroke-linecap:round;stroke-linejoin:round;fill:none}.river-bank{stroke:#175f83;opacity:.42}.river-water{stroke:#2787bd;opacity:.98}.river-highlight{stroke:#7cc6e7;opacity:.22}.river-mouth{fill:#2a8bc8;stroke:#174f72;stroke-width:.55;opacity:.42}.feature,.settlement{vector-effect:non-scaling-stroke}.settlement{stroke:#ffffff;stroke-width:.9}.settlement.abandoned{opacity:.28}.feature-label,.region-label,.settlement-label{font-family:Arial,Helvetica,sans-serif;paint-order:stroke;stroke:#ffffff;stroke-linejoin:round;vector-effect:non-scaling-stroke}.feature-label{font-size:9px;fill:#3d3427;stroke-width:2.2px}.region-label{font-size:11px;fill:#1f2332;font-weight:600;stroke-width:2.6px}.settlement-label{font-size:9.5px;fill:#111111;font-weight:700;stroke-width:2.0px}",
        "</style>",
        f'<rect x="{-width * 20}" y="{-height * 20}" width="{width * 41}" height="{height * 41}" fill="#454a78" />',
    ]
    occupied_labels: list[_LabelBox] = []
    deferred_labels: list[str] = []

    if geometry.micro_cells:
        region_edges, coast_edges, blend_edges = _micro_edge_segments(geometry.micro_cells)
        noisy_edge_paths = (
            _build_micro_noisy_edge_paths(
                geometry.micro_cells,
                width=width,
                height=height,
                pad=pad,
                seed=geometry.version,
            )
            if noisy_edges
            else {}
        )
        terrain_filter = ""
        parts.append(f'<g class="terrain-layer"{terrain_filter}>')
        for cell in geometry.micro_cells:
            scaled = (
                _micro_noisy_polygon_points(cell, noisy_edge_paths, width=width, height=height, pad=pad)
                if noisy_edges
                else [_scale(p, width, height, pad) for p in cell.polygon]
            )
            polity = overlays.polities_by_region_id.get(cell.region_id) if overlays else None
            polity_attrs = (
                f' data-polity-id="{html.escape(polity.polity_id)}" data-polity-name="{html.escape(polity.polity_name)}"'
                if polity is not None
                else ""
            )
            parts.append(
                f'<path class="micro-cell terrain-{html.escape(cell.terrain_family)}" '
                f'data-micro-id="{html.escape(cell.micro_id)}" data-region-id="{html.escape(cell.region_id)}"{polity_attrs} '
                f'data-elevation="{cell.elevation:.4f}" data-moisture="{cell.moisture:.4f}" '
                f'd="{_poly_path(scaled)}" fill="{_micro_cell_fill(cell, overlays)}" opacity="0.94" />'
            )
        parts.append("</g>")
        if noisy_edges and blend_edges:
            parts.append('<g class="terrain-mottle-layer" filter="url(#terrain-mottle-soften)" opacity="0.36">')
            for cell in geometry.micro_cells:
                if cell.is_coastal:
                    continue
                seed = _stable_seed("terrain-mottle", geometry.version, cell.micro_id)
                if seed % 3 == 0:
                    continue
                rng = random.Random(seed)
                cx, cy = _scale((cell.center_x, cell.center_y), width, height, pad)
                rx = rng.uniform(8.0, 20.0)
                ry = rng.uniform(5.0, 15.0)
                angle = rng.uniform(-35.0, 35.0)
                parts.append(
                    f'<ellipse class="terrain-mottle" cx="{cx:.1f}" cy="{cy:.1f}" '
                    f'rx="{rx:.1f}" ry="{ry:.1f}" transform="rotate({angle:.1f} {cx:.1f} {cy:.1f})" '
                    f'fill="{_micro_mottle_fill(cell, overlays)}" opacity="{rng.uniform(0.20, 0.38):.2f}" />'
                )
            parts.append("</g>")
            parts.append('<g class="terrain-blend-layer" opacity="0.34">')
            parts.append('<g filter="url(#terrain-soften)">')
            for a, b, first, second in blend_edges:
                pts = _oriented_noisy_edge_path(
                    noisy_edge_paths,
                    a,
                    b,
                    width=width,
                    height=height,
                    pad=pad,
                )
                parts.append(
                    f'<path class="terrain-blend" d="{_line_path(pts)}" fill="none" '
                    f'stroke="{_micro_blend_fill(first, second, overlays)}" stroke-width="6.0" />'
                )
            parts.append("</g>")
            parts.append("</g>")
            parts.append(
                f'<rect class="terrain-texture" x="{pad}" y="{pad}" width="{width - pad * 2}" '
                f'height="{height - pad * 2}" fill="#88806a" filter="url(#terrain-grain)" opacity="0.22" />'
            )
        coast_paths = _stitched_noisy_paths(
            _stitch_edge_chains(coast_edges),
            noisy_edge_paths,
            width=width,
            height=height,
            pad=pad,
            noisy_edges=noisy_edges,
        )
        for path in coast_paths:
            parts.append(
                f'<path class="coast-shadow" d="{path}" fill="none" opacity="0.42" />'
            )
        for path in coast_paths:
            parts.append(
                f'<path class="coast-beach" d="{path}" fill="none" opacity="0.70" />'
            )
        for path in coast_paths:
            parts.append(
                f'<path class="coast-line" d="{path}" fill="none" opacity="0.82" />'
            )
        for a, b in region_edges:
            pts = (
                _oriented_noisy_edge_path(noisy_edge_paths, a, b, width=width, height=height, pad=pad)
                if noisy_edges
                else [_scale(a, width, height, pad), _scale(b, width, height, pad)]
            )
            parts.append(
                f'<path class="region-boundary" d="{_line_path(pts)}" fill="none" opacity="0.10" />'
            )
    else:
        for cell in geometry.cells:
            scaled = [_scale(p, width, height, pad) for p in cell.polygon]
            if noisy_edges:
                scaled = _noisy_closed_points(scaled, _stable_seed(geometry.version, cell.region_id, "noisy-cell"))
            polity = overlays.polities_by_region_id.get(cell.region_id) if overlays else None
            polity_attrs = (
                f' data-polity-id="{html.escape(polity.polity_id)}" data-polity-name="{html.escape(polity.polity_name)}"'
                if polity is not None
                else ""
            )
            parts.append(
                f'<path class="cell terrain-{html.escape(cell.terrain_family)}" data-region-id="{html.escape(cell.region_id)}"{polity_attrs} '
                f'd="{_poly_path(scaled)}" fill="{_cell_fill(cell, overlays)}" opacity="0.82" />'
            )

    for river in geometry.rivers:
        pts = _river_render_points(
            [_scale(p, width, height, pad) for p in river.points],
            river.river_id,
            flow=river.flow,
        )
        if len(pts) < 2:
            continue
        water_width = 1.15 + math.sqrt(max(0.0, river.flow)) * 3.15
        d = _smooth_line_path(pts)
        parts.append(
            f'<path class="river river-bank {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
            f'd="{d}" stroke-width="{water_width + 2.15:.2f}" />'
        )
        parts.append(
            f'<path class="river river-water {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
            f'd="{d}" stroke-width="{water_width:.2f}" />'
        )
        if len(pts) >= 3:
            highlight_width = max(0.45, water_width * 0.28)
            parts.append(
                f'<path class="river river-highlight {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                f'd="{d}" stroke-width="{highlight_width:.2f}" />'
            )
        if len(river.points) >= 2 and river.points[-1] != river.points[-2]:
            mouth_x, mouth_y = pts[-1]
            parts.append(
                f'<ellipse class="river-mouth {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                f'cx="{mouth_x:.1f}" cy="{mouth_y:.1f}" rx="{water_width * 0.58:.2f}" ry="{water_width * 0.34:.2f}" />'
            )

    overlay_settlements = overlays.settlements if overlays is not None else []
    named_feature_overlays = overlays.features if overlays is not None else []
    show_region_labels = labels and not overlay_settlements
    if show_region_labels:
        for cell in geometry.cells:
            x, y = _scale((cell.center_x, cell.center_y), width, height, pad)
            shown = cell.region_id.replace("_", " ").title()
            box = _label_box(x, y, shown, 11.0, anchor="middle")
            if _claim_label(occupied_labels, box, bounds=(width, height)):
                deferred_labels.append(
                    f'<text class="region-label" data-region-id="{html.escape(cell.region_id)}" '
                    f'data-region-label="{html.escape(cell.region_id)}" '
                    f'x="{x:.1f}" y="{y:.1f}" text-anchor="middle">{html.escape(shown)}</text>'
                )

    feature_labels = 0
    if overlay_settlements and not named_feature_overlays:
        renderable_features = [
            f for f in geometry.features if _feature_is_near_settlement(f, overlay_settlements)
        ]
    elif overlay_settlements:
        named_ids = {f.feature_id for f in named_feature_overlays}
        renderable_features = [
            f
            for f in geometry.features
            if f.feature_id in named_ids or _feature_is_near_settlement(f, overlay_settlements, max_distance=0.025)
        ]
    else:
        renderable_features = list(geometry.features)
    sorted_features = sorted(renderable_features, key=lambda f: (-f.importance, f.region_id, f.feature_id))
    drawn_named_ids: set[str] = set()
    for named in named_feature_overlays:
        drawn_named_ids.add(named.feature_id)
        x, y = _scale((named.x, named.y), width, height, pad)
        feature_class = _FEATURE_CLASS_BY_KIND.get(named.kind.strip().lower(), "landform")
        color = _FEATURE_COLORS.get(feature_class, _FEATURE_COLORS["landform"])
        parts.append(
            f'<circle class="feature named-feature {html.escape(feature_class)}" data-feature-id="{html.escape(named.feature_id)}" '
            f'data-region-id="{html.escape(named.region_id)}" data-feature-name="{html.escape(named.display_name)}" '
            f'data-feature-kind="{html.escape(named.kind)}" data-feature-etymology="{html.escape(named.etymology or "")}" '
            f'cx="{x:.1f}" cy="{y:.1f}" r="3.4" data-base-r="3.4" fill="{color}" opacity="0.96" />'
        )
    for feature in sorted_features:
        if feature.feature_id in drawn_named_ids:
            continue
        x, y = _scale((feature.x, feature.y), width, height, pad)
        color = _FEATURE_COLORS.get(feature.feature_class, _FEATURE_COLORS["landform"])
        parts.append(
            f'<circle class="feature {html.escape(feature.feature_class)}" data-feature-id="{html.escape(feature.feature_id)}" '
            f'data-region-id="{html.escape(feature.region_id)}" data-feature-name="{html.escape(feature.label)}" '
            f'data-feature-kind="{html.escape(feature.kind)}" data-feature-etymology="" '
            f'cx="{x:.1f}" cy="{y:.1f}" r="{_feature_radius(feature):.1f}" data-base-r="{_feature_radius(feature):.1f}" fill="{color}" opacity="0.9" />'
        )
        if labels and not overlay_settlements and feature.importance >= 0.76 and feature_labels < max_feature_labels:
            box = _label_box(x + 5.0, y - 4.0, feature.label, 9.0)
            if _claim_label(occupied_labels, box, bounds=(width, height)):
                parts.append(
                    f'<text class="feature-label" data-feature-id="{html.escape(feature.feature_id)}" '
                    f'data-region-id="{html.escape(feature.region_id)}" data-feature-name="{html.escape(feature.label)}" '
                    f'data-feature-kind="{html.escape(feature.kind)}" data-feature-etymology="" '
                    f'x="{x + 5.0:.1f}" y="{y - 4.0:.1f}">{html.escape(feature.label)}</text>'
                )
                feature_labels += 1

    if overlays is not None:
        settlements = sorted(
            overlays.settlements,
            key=lambda s: ((s.status or "").strip().lower() != "active", -s.population, s.settlement_id),
        )[: max(0, int(max_settlements))]
        settlement_labels = 0
        for settlement in settlements:
            x, y = _scale((settlement.x, settlement.y), width, height, pad)
            status_class = "abandoned" if settlement.status.strip().lower() == "abandoned" else "active"
            radius = max(1.25, min(3.2, 1.25 + math.sqrt(max(0, settlement.population)) * 0.036))
            parts.append(
                f'<circle class="settlement {status_class}" data-settlement-id="{html.escape(settlement.settlement_id)}" '
                f'data-region-id="{html.escape(settlement.region_id)}" cx="{x:.1f}" cy="{y:.1f}" '
                f'r="{radius:.1f}" data-base-r="{radius:.1f}" fill="#111111" />'
            )
            if labels and settlement.status.strip().lower() != "abandoned" and settlement_labels < max_settlement_labels:
                shown = settlement.display_name[:24]
                label_dx = radius + 2.2
                label_dy = 3.0
                label_anchor = "start"
                for dx, dy, anchor in (
                    (radius + 2.2, 3.0, "start"),
                    (-(radius + 2.2), 3.0, "end"),
                    (0.0, -(radius + 3.0), "middle"),
                    (0.0, radius + 10.0, "middle"),
                ):
                    box = _label_box(x + dx, y + dy, shown, 9.5, anchor=anchor)
                    if _claim_label(occupied_labels, box, bounds=(width, height)):
                        label_dx = dx
                        label_dy = dy
                        label_anchor = anchor
                        break
                else:
                    continue
                label_x = x + label_dx
                label_y = y + label_dy
                parts.append(
                    f'<text class="settlement-label" data-settlement-id="{html.escape(settlement.settlement_id)}" '
                    f'data-region-id="{html.escape(settlement.region_id)}" '
                    f'data-point-x="{x:.1f}" data-point-y="{y:.1f}" data-dx="{label_dx:.2f}" data-dy="{label_dy:.2f}" '
                    f'x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{label_anchor}">{html.escape(shown)}</text>'
                )
                settlement_labels += 1

    if labels and named_feature_overlays:
        for named in sorted(named_feature_overlays, key=lambda f: (f.region_id, f.display_name, f.feature_id)):
            if feature_labels >= max_feature_labels:
                break
            x, y = _scale((named.x, named.y), width, height, pad)
            shown = named.display_name[:24]
            label_dx = 5.0
            label_dy = -4.0
            label_anchor = "start"
            for dx, dy, anchor in (
                (5.0, -4.0, "start"),
                (5.0, 10.0, "start"),
                (-5.0, -4.0, "end"),
                (-5.0, 10.0, "end"),
                (0.0, -8.0, "middle"),
                (0.0, 12.0, "middle"),
            ):
                box = _label_box(x + dx, y + dy, shown, 9.0, anchor=anchor)
                if _claim_label(occupied_labels, box, bounds=(width, height)):
                    label_dx = dx
                    label_dy = dy
                    label_anchor = anchor
                    break
            else:
                label_dx = 5.0
                label_dy = 10.0
                label_anchor = "start"
            label_x = x + label_dx
            label_y = y + label_dy
            parts.append(
                f'<text class="feature-label" data-feature-id="{html.escape(named.feature_id)}" '
                f'data-region-id="{html.escape(named.region_id)}" data-feature-name="{html.escape(named.display_name)}" '
                f'data-feature-kind="{html.escape(named.kind)}" data-feature-etymology="{html.escape(named.etymology or "")}" '
                f'data-point-x="{x:.1f}" data-point-y="{y:.1f}" data-dx="{label_dx:.2f}" data-dy="{label_dy:.2f}" '
                f'x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{label_anchor}">{html.escape(shown)}</text>'
            )
            feature_labels += 1

    parts.extend(deferred_labels)

    parts.append("</svg>")
    return "\n".join(parts) + "\n"

