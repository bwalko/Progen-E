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

from library.fontawesome_free_icons import FONT_AWESOME_FREE_SOLID, FontAwesomeIcon
from library.world_map_roads import RoadMapEdge, build_settlement_road_overlays
from library.world_map_geometry import (
    MicroRegionCell,
    Point,
    RegionCell,
    RegionFeature,
    RiverPath,
    WaterCell,
    WorldMapGeometry,
    _micro_boundary_edges,
    project_feature_point_to_region_footprint,
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
    source_region_feature_id: str | None = None


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
    roads: list[RoadMapEdge] = field(default_factory=list)


_CELL_COLORS = {
    "coast": "#c8b889",
    "riverland": "#6fa65f",
    "highlands": "#bfc1aa",
    "forest": "#2f7d55",
    "drylands": "#b1a070",
    "plains": "#86ad67",
}

_FEATURE_COLORS = {
    "coast": "#4e5558",
    "water": "#3f7891",
    "relief": "#c9c7bd",
    "vegetation": "#3f6f52",
    "settlement_support": "#75684c",
    "landform": "#6b6657",
}


def world_map_zoom_sync_script(svg_var: str, *, map_width: int = 1200) -> str:
    """Return inline JS that keeps map labels and icons screen-readable while zooming."""
    return (
        f"const vb={svg_var}.viewBox.baseVal;const z=Math.max(.001,{map_width}/vb.width);"
        "const m=Math.max(.88,Math.min(2.05,Math.pow(z,.35)));"
        f"const rect={svg_var}.getBoundingClientRect();"
        "const pxu=Math.max(rect.width/vb.width,rect.height/vb.height,.001);"
        "const maxIcon=0.02*Math.max(rect.width,rect.height)/pxu;"
        f"{svg_var}.querySelectorAll('.region-label').forEach(e=>e.style.fontSize=(11*m/z)+'px');"
        f"{svg_var}.querySelectorAll('.feature-label').forEach(e=>e.style.fontSize=(9*m/z)+'px');"
        f"{svg_var}.querySelectorAll('.settlement-label').forEach(e=>e.style.fontSize=(9.5*m/z)+'px');"
        f"{svg_var}.querySelectorAll('.feature-label[data-point-x],.settlement-label[data-point-x]').forEach(e=>{{"
        "const px=+e.dataset.pointX,py=+e.dataset.pointY,dx=+e.dataset.dx||0,dy=+e.dataset.dy||0;"
        "e.setAttribute('x',px+dx/z);e.setAttribute('y',py+dy/z);});"
        f"{svg_var}.querySelectorAll('.settlement').forEach(e=>{{"
        "const b=+e.dataset.baseR||4;const maxR=maxIcon/2;"
        "e.setAttribute('r',Math.min(maxR,Math.max(2.4,Math.min(7.0,b*m/z))));});"
        f"{svg_var}.querySelectorAll('.feature').forEach(e=>{{"
        "const base=+e.dataset.baseSize||((+e.dataset.baseR||3)*2.55);"
        "const iw=+e.dataset.iconW||512,ih=+e.dataset.iconH||512;"
        "const x=+e.dataset.pointX||0,y=+e.dataset.pointY||0;"
        "const size=Math.min(maxIcon,Math.max(3.0,base*m/z));"
        "const scale=size/Math.max(iw,ih);"
        "const tx=x-iw*scale/2,ty=y-ih*scale/2;"
        "const t=`translate(${tx.toFixed(2)} ${ty.toFixed(2)}) scale(${scale.toFixed(5)})`;"
        "e.querySelectorAll('.feature-fa-underlay,.feature-fa-shape').forEach(p=>p.setAttribute('transform',t));});"
    )

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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


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
        color = _mix_color(color, "#f4efd2", grain * 0.10)
    else:
        color = _mix_color(color, "#314436", abs(grain) * 0.09)
    if cell.elevation >= 0.58:
        color = _mix_color(color, "#ddd9c4", min(0.22, (cell.elevation - 0.58) * 0.40))
    if cell.elevation <= 0.36 and not cell.is_coastal:
        color = _mix_color(color, "#4f7f57", min(0.10, (0.36 - cell.elevation) * 0.26))
    if cell.moisture >= 0.66 and not cell.is_coastal:
        color = _mix_color(color, "#1f6f52", min(0.17, (cell.moisture - 0.66) * 0.30))
    return color


def _scale(point: Point, width: int, height: int, pad: int) -> tuple[float, float]:
    x, y = point
    return (pad + x * (width - pad * 2), pad + y * (height - pad * 2))


def _unscale(point: tuple[float, float], width: int, height: int, pad: int) -> tuple[float, float]:
    x, y = point
    return (
        _clamp((x - pad) / max(1e-9, width - pad * 2), 0.0, 1.0),
        _clamp((y - pad) / max(1e-9, height - pad * 2), 0.0, 1.0),
    )


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


def _open_poly_path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    first = points[0]
    rest = " ".join(f"L {x:.1f} {y:.1f}" for x, y in points[1:])
    return f"M {first[0]:.1f} {first[1]:.1f} {rest} Z"


def _multi_poly_path(polys: list[list[tuple[float, float]]]) -> str:
    return " ".join(_open_poly_path(poly) for poly in polys if len(poly) >= 3)


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


def _tapered_river_polygon(
    points: list[tuple[float, float]],
    *,
    start_width: float,
    end_width: float,
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return []
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
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
        length = max(1e-6, math.hypot(tx, ty))
        nx = -ty / length
        ny = tx / length
        downstream = (i / max(1, last)) ** 0.78
        width = start_width + (end_width - start_width) * downstream
        half = width / 2.0
        left.append((point[0] + nx * half, point[1] + ny * half))
        right.append((point[0] - nx * half, point[1] - ny * half))
    return left + list(reversed(right))


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


def _river_corridor_fill(
    river_micro_ids: set[str],
    micro_by_id: dict[str, MicroRegionCell],
    overlays: WorldMapOverlays | None,
) -> str:
    cells = [micro_by_id[mid] for mid in river_micro_ids if mid in micro_by_id]
    if not cells:
        return "#6f9271"
    if overlays is not None and any(c.region_id in overlays.polities_by_region_id for c in cells):
        return "#73856e"
    avg_moisture = sum(c.moisture for c in cells) / len(cells)
    avg_elevation = sum(c.elevation for c in cells) / len(cells)
    coastal_share = sum(1 for c in cells if c.is_coastal) / len(cells)
    families = {c.terrain_family for c in cells}
    if coastal_share >= 0.35:
        base = "#9daa7d"
    elif avg_elevation >= 0.66:
        base = "#87957f"
    elif "drylands" in families and avg_moisture < 0.46:
        base = "#aa9e6d"
    elif "forest" in families or avg_moisture >= 0.66:
        base = "#487d57"
    else:
        base = "#779964"
    return _mix_color(base, "#b7ad82", 0.16)


def _river_mouth_polygons(
    pts: list[tuple[float, float]],
    *,
    width: float,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    if len(pts) < 2:
        return ([], [])
    ax, ay = pts[-2]
    bx, by = pts[-1]
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length <= 1e-6:
        return ([], [])
    nx = -dy / length
    ny = dx / length
    start = _lerp_point(pts[-2], pts[-1], 0.26)
    mouth_tip = (pts[-1][0] + dx / length * width * 0.64, pts[-1][1] + dy / length * width * 0.64)
    mid = _lerp_point(start, mouth_tip, 0.62)
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
    return (bank, water)


def _offset_line_points(
    points: list[tuple[float, float]],
    *,
    amount: float,
) -> list[tuple[float, float]]:
    if len(points) < 2 or abs(amount) <= 1e-6:
        return points
    out: list[tuple[float, float]] = []
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
        length = max(1e-6, math.hypot(tx, ty))
        out.append((point[0] - ty / length * amount, point[1] + tx / length * amount))
    return out


def _cell_fill(cell: RegionCell, overlays: WorldMapOverlays | None) -> str:
    if overlays is not None and cell.region_id in overlays.polities_by_region_id:
        return overlays.polities_by_region_id[cell.region_id].color
    return _CELL_COLORS.get(cell.terrain_family, _CELL_COLORS["plains"])


def _micro_cell_fill(cell: MicroRegionCell, overlays: WorldMapOverlays | None) -> str:
    if overlays is not None and cell.region_id in overlays.polities_by_region_id:
        polity = overlays.polities_by_region_id[cell.region_id].color
        return _terrain_tint(_mix_color(polity, "#d8d0ad", 0.24), cell)
    if cell.elevation >= 0.78:
        base = "#ecebdc" if cell.moisture >= 0.52 else "#c9c3a3"
    elif cell.elevation >= 0.64:
        base = "#b9b897" if cell.moisture < 0.42 else "#aeb78f"
    elif cell.is_coastal:
        base = "#c3b17f" if cell.moisture < 0.54 else "#9fb076"
    elif cell.moisture >= 0.78:
        base = "#2c7a59"
    elif cell.moisture >= 0.58:
        base = "#5d965d"
    elif cell.moisture <= 0.28:
        base = "#b6a56f"
    else:
        base = _CELL_COLORS.get(cell.terrain_family, _CELL_COLORS["plains"])
    river_flow = max(0.0, float(getattr(cell, "river_flow", 0.0) or 0.0))
    river_distance = max(0.0, float(getattr(cell, "river_distance", 1.0) or 1.0))
    if getattr(cell, "is_floodplain", False) or river_flow > 0.0:
        influence = _clamp(1.0 - river_distance / max(0.001, 0.034 + river_flow * 0.010), 0.0, 1.0)
        if influence > 0.0:
            alluvial = "#829a6a"
            if cell.moisture >= 0.72:
                alluvial = "#4f8060"
            elif cell.moisture <= 0.38:
                alluvial = "#aaa06e"
            base = _mix_color(base, alluvial, min(0.42, 0.18 + influence * 0.24))
            if getattr(cell, "is_channel", False):
                base = _mix_color(base, "#4f8da0", 0.16)
    return _terrain_tint(base, cell)


def _water_cell_fill(cell: WaterCell) -> str:
    if cell.kind == "lake":
        return _mix_color("#3c9ab6", "#174964", min(0.45, cell.depth * 0.38))
    return _mix_color("#5f8eaa", "#243d59", min(0.55, cell.depth * 0.42))


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


def _micro_hillshade(
    micro_cells: list[MicroRegionCell],
    blend_edges: list[tuple[Point, Point, MicroRegionCell, MicroRegionCell]],
) -> dict[str, tuple[str, float]]:
    """Return deterministic Swiss-style light/dark relief washes for micro-cells."""

    slopes: dict[str, list[tuple[float, float, float]]] = {c.micro_id: [] for c in micro_cells}
    for _, _, first, second in blend_edges:
        dx = second.center_x - first.center_x
        dy = second.center_y - first.center_y
        dist = max(1e-6, math.hypot(dx, dy))
        delta = second.elevation - first.elevation
        slopes[first.micro_id].append((dx / dist, dy / dist, delta / dist))
        slopes[second.micro_id].append((-dx / dist, -dy / dist, -delta / dist))

    light_x = -0.62
    light_y = -0.78
    shade: dict[str, tuple[str, float]] = {}
    for cell in micro_cells:
        samples = slopes.get(cell.micro_id, [])
        if not samples or cell.is_coastal:
            continue
        gx = sum(dx * delta for dx, _, delta in samples) / len(samples)
        gy = sum(dy * delta for _, dy, delta in samples) / len(samples)
        facing_light = -(gx * light_x + gy * light_y)
        steepness = min(1.0, math.hypot(gx, gy) * 0.30 + max(0.0, cell.elevation - 0.52) * 0.65)
        amount = min(0.24, abs(facing_light) * 0.16 + steepness * 0.09)
        if amount < 0.018:
            continue
        if facing_light >= 0.0:
            shade[cell.micro_id] = ("#f7f0cf", amount)
        else:
            shade[cell.micro_id] = ("#233044", amount * 0.95)
    return shade


def _feature_radius(feature: RegionFeature) -> float:
    if feature.feature_class in {"coast", "water"}:
        return 2.7
    if feature.feature_class == "relief":
        return 2.8
    if feature.feature_class == "vegetation":
        return 2.8
    return 2.6


def _feature_icon_name(kind: str, feature_class: str) -> str:
    k = (kind or "").strip().lower()
    if k in {"harbor", "bay", "coast", "coastline"}:
        return "anchor"
    if k in {"marsh", "bog", "swamp", "fen", "wetland"}:
        return "spa"
    if k in {"lake", "spring", "oasis", "well", "wadi"}:
        return "droplet"
    if k in {"river", "stream", "fishery"} or feature_class == "water":
        return "water"
    if k in {"ford", "bridge"}:
        return "bridge-water"
    if k in {"volcano"}:
        return "volcano"
    if k in {"snowfield", "glacier", "icefield"}:
        return "snowflake"
    if k in {"ice", "icicle", "icicles"}:
        return "icicles"
    if k in {"mountain", "ridge", "pass", "cliff", "mesa"} or feature_class == "relief":
        return "mountain"
    if k in {"hill", "mound", "barrow"}:
        return "mound"
    if k in {"forest", "grove", "wood", "woods"}:
        return "tree"
    if k in {"clearing", "meadow", "pasture", "orchard", "heath"}:
        return "leaf"
    if k in {"grassland", "scrub", "shrubland"} or feature_class == "vegetation":
        return "seedling"
    if k in {"wilt", "wilted", "flower", "flowers"}:
        return "plant-wilt"
    if k in {"mine", "quarry"}:
        return "gem"
    if k in {"marker", "ore", "deposit"}:
        return "diamond"
    if k in {"ruin", "gate", "arch"}:
        return "archway"
    if k in {"monument", "obelisk"}:
        return "monument"
    if k in {"cave", "dungeon"}:
        return "dungeon"
    if k in {"danger", "grave", "bones"}:
        return "skull-crossbones"
    if k in {"record", "archive", "scroll"}:
        return "scroll"
    if k in {"spiral", "whorl"}:
        return "spiral"
    if k in {"forest", "grove", "clearing", "meadow", "pasture", "orchard"} or feature_class == "vegetation":
        return "tree"
    if k in {"road", "route"}:
        return "bridge"
    if k in {"engineering", "workshop", "mine", "quarry"}:
        return "gears"
    if k in {"sacred", "shrine", "temple"}:
        return "landmark"
    if k in {"star", "wonder"}:
        return "star"
    return "location-dot"


def _fontawesome_icon_path(
    *,
    icon: FontAwesomeIcon,
    x: float,
    y: float,
    size: float,
) -> tuple[str, str]:
    scale = size / max(icon.width, icon.height)
    tx = x - icon.width * scale / 2.0
    ty = y - icon.height * scale / 2.0
    transform = f"translate({tx:.2f} {ty:.2f}) scale({scale:.5f})"
    return (icon.path, transform)


def _feature_symbol_svg(
    *,
    feature_class: str,
    kind: str,
    attrs: str,
    x: float,
    y: float,
    radius: float,
    color: str,
    named: bool = False,
) -> str:
    r = radius * (1.10 if named else 1.0)
    icon_name = _feature_icon_name(kind, feature_class)
    icon = FONT_AWESOME_FREE_SOLID.get(icon_name, FONT_AWESOME_FREE_SOLID["location-dot"])
    base_size = r * 2.55
    path_d, transform = _fontawesome_icon_path(icon=icon, x=x, y=y, size=base_size)
    return (
        f'<g class="feature {"named-feature " if named else ""}{html.escape(feature_class)}" '
        f'data-base-r="{r:.1f}" data-base-size="{base_size:.2f}" '
        f'data-point-x="{x:.1f}" data-point-y="{y:.1f}" '
        f'data-icon-name="{html.escape(icon_name)}" data-icon-w="{icon.width:.1f}" data-icon-h="{icon.height:.1f}">'
        f'<g {attrs}>'
        f'<path class="feature-fa-underlay" d="{html.escape(path_d)}" transform="{transform}" />'
        f'<path class="feature-fa-shape" d="{html.escape(path_d)}" transform="{transform}" '
        f'fill="{html.escape(color)}" stroke="{html.escape(color)}" />'
        f'</g>'
        f'</g>'
    )


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


def _marker_box(x: float, y: float, radius: float, *, padding: float = 2.0) -> _LabelBox:
    size = radius + padding
    return (x - size, y - size, x + size, y + size)


def _claim_marker(occupied: list[_LabelBox], box: _LabelBox, *, bounds: tuple[float, float]) -> bool:
    width, height = bounds
    if box[0] < 0.0 or box[1] < 0.0 or box[2] > width or box[3] > height:
        return False
    if any(_boxes_intersect(box, other) for other in occupied):
        return False
    occupied.append(box)
    return True


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return True
    x, y = point
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            crossing_x = (xj - xi) * (y - yi) / max(1e-9, yj - yi) + xi
            if x < crossing_x:
                inside = not inside
        j = i
    return inside


def _point_segment_distance(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    nearest = _nearest_point_on_segment(point, a, b)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _thing_polygon_id(
    geometry: WorldMapGeometry,
    *,
    x: float,
    y: float,
    width: int,
    height: int,
    pad: int,
    region_id: str | None = None,
) -> str | None:
    world_point = _unscale((x, y), width, height, pad)
    region = (region_id or "").strip()
    candidates = [
        cell
        for cell in geometry.micro_cells
        if not region or cell.region_id == region
    ]
    for cell in candidates:
        if _point_in_polygon(world_point, cell.polygon):
            return cell.micro_id
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda cell: (
            min(
                _point_segment_distance(world_point, a, b)
                for a, b in zip(cell.polygon, cell.polygon[1:] + cell.polygon[:1])
            ),
            (cell.center_x - world_point[0]) ** 2 + (cell.center_y - world_point[1]) ** 2,
            cell.micro_id,
        ),
    ).micro_id


def _claim_active_thing_polygon(
    occupied_polygon_ids: set[str],
    geometry: WorldMapGeometry,
    *,
    x: float,
    y: float,
    width: int,
    height: int,
    pad: int,
    region_id: str | None = None,
) -> bool:
    polygon_id = _thing_polygon_id(
        geometry,
        x=x,
        y=y,
        width=width,
        height=height,
        pad=pad,
        region_id=region_id,
    )
    if polygon_id is None:
        return True
    if polygon_id in occupied_polygon_ids:
        return False
    occupied_polygon_ids.add(polygon_id)
    return True


def _marker_position_allowed(
    x: float,
    y: float,
    radius: float,
    *,
    allowed_polygon: list[tuple[float, float]] | None,
) -> bool:
    if not allowed_polygon:
        return True
    inset = max(1.5, radius * 0.65)
    return all(
        _point_in_polygon(point, allowed_polygon)
        for point in (
            (x, y),
            (x - inset, y),
            (x + inset, y),
            (x, y - inset),
            (x, y + inset),
        )
    )


def _place_marker(
    x: float,
    y: float,
    radius: float,
    occupied: list[_LabelBox],
    *,
    bounds: tuple[float, float],
    seed: object,
    max_offset: float = 34.0,
    allowed_polygon: list[tuple[float, float]] | None = None,
) -> tuple[float, float]:
    if _marker_position_allowed(x, y, radius, allowed_polygon=allowed_polygon) and _claim_marker(
        occupied, _marker_box(x, y, radius), bounds=bounds
    ):
        return (x, y)
    rng = random.Random(_stable_seed("marker-placement", seed))
    angles = [i * math.pi / 4.0 for i in range(8)]
    rng.shuffle(angles)
    for distance in (10.0, 16.0, 23.0, 30.0, max_offset):
        for angle in angles:
            cx = x + math.cos(angle) * distance
            cy = y + math.sin(angle) * distance
            if not _marker_position_allowed(cx, cy, radius, allowed_polygon=allowed_polygon):
                continue
            if _claim_marker(occupied, _marker_box(cx, cy, radius), bounds=bounds):
                return (cx, cy)
    clamped_x = _clamp(x, radius + 2.0, bounds[0] - radius - 2.0)
    clamped_y = _clamp(y, radius + 2.0, bounds[1] - radius - 2.0)
    if not _marker_position_allowed(clamped_x, clamped_y, radius, allowed_polygon=allowed_polygon):
        clamped_x = x
        clamped_y = y
    occupied.append(_marker_box(clamped_x, clamped_y, radius))
    return (clamped_x, clamped_y)


def _place_coastline_marker(
    geometry: WorldMapGeometry,
    *,
    region_id: str,
    kind: str,
    x: float,
    y: float,
    radius: float,
    occupied: list[_LabelBox],
    bounds: tuple[float, float],
    seed: object,
    width: int,
    height: int,
    pad: int,
    allowed_polygon: list[tuple[float, float]] | None = None,
    max_offset: float = 34.0,
) -> tuple[float, float]:
    if (kind or "").strip().lower() not in {"harbor", "bay", "coast"}:
        return _place_marker(
            x,
            y,
            radius,
            occupied,
            bounds=bounds,
            seed=seed,
            max_offset=max_offset,
            allowed_polygon=allowed_polygon,
        )

    def snapped(candidate_x: float, candidate_y: float) -> tuple[float, float]:
        return _coastline_marker_screen_point(
            geometry,
            region_id=region_id,
            kind=kind,
            world_point=_unscale((candidate_x, candidate_y), width, height, pad),
            radius=radius,
            width=width,
            height=height,
            pad=pad,
            allowed_polygon=allowed_polygon,
        )

    sx, sy = snapped(x, y)
    if _claim_marker(occupied, _marker_box(sx, sy, radius), bounds=bounds):
        return (sx, sy)
    rng = random.Random(_stable_seed("marker-placement", seed))
    angles = [i * math.pi / 4.0 for i in range(8)]
    rng.shuffle(angles)
    for distance in (8.0, 12.0, 18.0, 24.0, max_offset):
        for angle in angles:
            sx, sy = snapped(x + math.cos(angle) * distance, y + math.sin(angle) * distance)
            if _claim_marker(occupied, _marker_box(sx, sy, radius), bounds=bounds):
                return (sx, sy)
    occupied.append(_marker_box(sx, sy, radius))
    return (sx, sy)


def _nearest_point_on_segment(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float]:
    px, py = point
    ax, ay = a
    bx, by = b
    vx = bx - ax
    vy = by - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return a
    t = _clamp(((px - ax) * vx + (py - ay) * vy) / denom, 0.0, 1.0)
    return (ax + vx * t, ay + vy * t)


def _polygon_bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    if not points:
        return (0.0, 0.0, 1.0, 1.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _representative_point_in_polygon(points: list[tuple[float, float]]) -> tuple[float, float]:
    if not points:
        return (0.5, 0.5)
    center = (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )
    if _point_in_polygon(center, points):
        return center
    x0, y0, x1, y1 = _polygon_bounds(points)
    candidates = [
        (x0 + (x1 - x0) * ix / 8.0, y0 + (y1 - y0) * iy / 8.0)
        for ix in range(1, 8)
        for iy in range(1, 8)
    ]
    candidates.extend(
        ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        for a, b in zip(points, points[1:] + points[:1])
    )
    for candidate in sorted(
        candidates,
        key=lambda p: ((p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2, p[0], p[1]),
    ):
        if _point_in_polygon(candidate, points):
            return candidate
    return center


def _nearest_screen_point_inside_polygon(
    point: tuple[float, float],
    polygon: list[tuple[float, float]],
    *,
    min_distance: float,
) -> tuple[float, float] | None:
    if not polygon:
        return None
    x0, y0, x1, y1 = _polygon_bounds(polygon)
    candidates = [
        (x0 + (x1 - x0) * ix / 18.0, y0 + (y1 - y0) * iy / 18.0)
        for ix in range(1, 18)
        for iy in range(1, 18)
    ]
    candidates.extend(
        ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
        for a, b in zip(polygon, polygon[1:] + polygon[:1])
    )
    usable = [
        p
        for p in candidates
        if _point_in_polygon(p, polygon)
        and math.hypot(p[0] - point[0], p[1] - point[1]) >= min_distance
    ]
    if not usable:
        usable = [p for p in candidates if _point_in_polygon(p, polygon)]
    if not usable:
        return None
    return min(
        usable,
        key=lambda p: (
            math.hypot(p[0] - point[0], p[1] - point[1]),
            p[0],
            p[1],
        ),
    )


def _coastline_marker_screen_point(
    geometry: WorldMapGeometry,
    *,
    region_id: str,
    kind: str,
    world_point: tuple[float, float],
    radius: float,
    width: int,
    height: int,
    pad: int,
    allowed_polygon: list[tuple[float, float]] | None,
) -> tuple[float, float]:
    if (kind or "").strip().lower() not in {"harbor", "bay", "coast"}:
        return _scale(world_point, width, height, pad)
    boundary_edges = _micro_boundary_edges(geometry.micro_cells)
    best: tuple[float, tuple[float, float]] | None = None
    for cell in geometry.micro_cells:
        if cell.region_id != region_id or not cell.is_coastal:
            continue
        for a, b in boundary_edges.get(cell.micro_id, []):
            coast = _nearest_point_on_segment(world_point, a, b)
            dist2 = (coast[0] - world_point[0]) ** 2 + (coast[1] - world_point[1]) ** 2
            candidate = (dist2, coast)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        return _scale(world_point, width, height, pad)
    _dist2, coast_world = best
    return _scale(coast_world, width, height, pad)


def _feature_marker_allowed_polygon(
    geometry: WorldMapGeometry,
    *,
    region_id: str,
    kind: str,
    world_point: tuple[float, float],
    region_screen_polygons: dict[str, list[tuple[float, float]]],
    width: int,
    height: int,
    pad: int,
) -> list[tuple[float, float]] | None:
    k = (kind or "").strip().lower()
    if k in {"harbor", "bay", "coast"}:
        return region_screen_polygons.get(region_id)
    for cell in geometry.micro_cells:
        if cell.region_id != region_id or cell.is_coastal:
            continue
        if _point_in_polygon(world_point, cell.polygon):
            return [_scale(point, width, height, pad) for point in cell.polygon]
    return region_screen_polygons.get(region_id)


def _cell_bbox(cell: RegionCell) -> tuple[float, float, float, float]:
    if not cell.polygon:
        return (cell.center_x, cell.center_y, cell.center_x, cell.center_y)
    xs = [p[0] for p in cell.polygon]
    ys = [p[1] for p in cell.polygon]
    return (min(xs), min(ys), max(xs), max(ys))


def _dissolved_region_boundary_paths(
    cells: list[RegionCell],
    *,
    width: int,
    height: int,
    pad: int,
) -> list[str]:
    paths: list[str] = []
    for cell in cells:
        if len(cell.polygon) < 3:
            continue
        scaled = [_scale(p, width, height, pad) for p in cell.polygon]
        d = _poly_path(scaled)
        if not d:
            continue
        paths.append(
            f'<path class="region-boundary" data-region-id="{html.escape(cell.region_id)}" '
            f'd="{d}" fill="none" />'
        )
    return paths


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
        kind = str(feature.get("kind") or "feature").strip() or "feature"
        try:
            if feature.get("source_world_x") is not None and feature.get("source_world_y") is not None:
                world_xy = (float(feature["source_world_x"]), float(feature["source_world_y"]))
                wx, wy = project_world_point_to_region_footprint(geometry, region_id, world_xy)
            else:
                lx = max(0.04, min(0.96, float(feature.get("x", 0.5))))
                ly = max(0.04, min(0.96, float(feature.get("y", 0.5))))
                wx, wy = project_local_point_to_region_footprint(geometry, region_id, (lx, ly))
            wx, wy = project_feature_point_to_region_footprint(
                geometry,
                region_id,
                (wx, wy),
                kind=kind,
            )
        except (TypeError, ValueError):
            continue
        out.append(
            FeatureMapOverlay(
                feature_id=fid,
                region_id=region_id,
                kind=kind,
                display_name=name,
                x=wx,
                y=wy,
                etymology=str(feature.get("etymology") or "").strip() or None,
                source_region_feature_id=(
                    str(feature.get("source_region_feature_id") or "").strip() or None
                ),
            )
        )
    return out


def load_world_map_overlays(
    *,
    geometry: WorldMapGeometry,
    save_db_path: Path | str | None,
    max_settlements: int = DEFAULT_MAX_SETTLEMENT_OVERLAYS,
    include_inactive_settlements: bool = False,
    include_roads: bool = True,
) -> WorldMapOverlays:
    """Load optional settlement and polity overlays from ``save.sqlite``."""
    if save_db_path is None:
        return WorldMapOverlays(settlements=[], polities_by_region_id={}, features=[], roads=[])
    path = Path(save_db_path)
    if not path.exists():
        return WorldMapOverlays(settlements=[], polities_by_region_id={}, features=[], roads=[])
    settlement_limit = max(0, int(max_settlements))
    cells = geometry.cell_by_region_id()
    settlements: list[SettlementMapOverlay] = []
    features_by_id: dict[str, FeatureMapOverlay] = {}
    polities: dict[str, PolityMapOverlay] = {}
    roads: list[RoadMapEdge] = (
        build_settlement_road_overlays(
            geometry=geometry,
            save_db_path=path,
            max_nodes=settlement_limit,
        )
        if include_roads
        else []
    )
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
                local = _site_xy(row["local_geography_json"], row["site_slot"])
                world_xy = _site_world_xy(row["local_geography_json"], row["site_slot"])
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
        roads=roads,
    )


def render_world_map_svg(
    geometry: WorldMapGeometry,
    *,
    width: int = 1200,
    height: int = 800,
    noisy_edges: bool = True,
    labels: bool = True,
    overlays: WorldMapOverlays | None = None,
    max_feature_labels: int = 64,
    max_settlement_labels: int = 24,
    max_settlements: int = DEFAULT_MAX_SETTLEMENT_OVERLAYS,
) -> str:
    """Render generated world geometry to a self-contained SVG string."""
    pad = 36
    zoom_script = (
        f"const zf=(svg)=>{{{world_map_zoom_sync_script('svg', map_width=width)}}};"
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
        '<filter id="river-corridor-soften" x="-20%" y="-20%" width="140%" height="140%">',
        '<feGaussianBlur stdDeviation="1.5" />',
        "</filter>",
        '<filter id="terrain-grain" x="0" y="0" width="100%" height="100%">',
        f'<feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="{_stable_seed(geometry.version, "terrain-grain") % 997}" result="grain" />',
        '<feColorMatrix in="grain" type="matrix" values="0 0 0 0 0.50 0 0 0 0 0.47 0 0 0 0 0.38 0 0 0 .30 0" />',
        "</filter>",
        '<linearGradient id="ocean-gradient" x1="0" y1="0" x2="1" y2="1">',
        '<stop offset="0" stop-color="#5e7fa0" />',
        '<stop offset=".52" stop-color="#466986" />',
        '<stop offset="1" stop-color="#344a68" />',
        "</linearGradient>",
        "</defs>",
        "<style>",
        ".cell{stroke:#74694f;stroke-width:1.0;stroke-linejoin:round}.micro-cell{stroke:none}.water-cell{stroke:#2f607c;stroke-width:.25;stroke-linejoin:round;pointer-events:none}.water-cell.lake{stroke:#d7f3f1;stroke-width:.38}.terrain-blend{stroke-linecap:butt;stroke-linejoin:round;pointer-events:none}.coast-shelf,.coast-beach,.coast-shadow{stroke-linecap:butt;stroke-linejoin:round;pointer-events:none}.terrain-mottle,.terrain-texture{mix-blend-mode:soft-light;pointer-events:none}.terrain-shade{mix-blend-mode:multiply;pointer-events:none}.terrain-shade-light{mix-blend-mode:screen;pointer-events:none}.region-boundary{stroke:#151b2d;stroke-width:.45;stroke-linecap:round;stroke-linejoin:round}.coast-shelf{stroke:#8fb7c2;stroke-width:8.0}.coast-beach{stroke:#d0c096;stroke-width:3.4}.coast-shadow{stroke:#25344d;stroke-width:2.6}.coast-line{stroke:#1d2938;stroke-width:1.35;stroke-linecap:butt;stroke-linejoin:round}.river-corridor,.river-bank,.river-water,.river-mouth-bank,.river-mouth{stroke:none;fill-rule:evenodd}.river-corridor{mix-blend-mode:multiply}.river-highlight{stroke:#8cc7cf;stroke-linecap:round;stroke-linejoin:round;fill:none}.road{fill:none;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke;pointer-events:none}.road-underlay{stroke:#fff2c8;mix-blend-mode:screen}.road-line{stroke:#6f5533}.feature,.settlement{vector-effect:non-scaling-stroke}.feature{cursor:pointer}.feature-fa-underlay{fill:none;stroke:#fff8e6;stroke-width:3.2;stroke-linejoin:round;opacity:.92;vector-effect:non-scaling-stroke}.feature-fa-shape{stroke-width:.2;stroke-linejoin:round;vector-effect:non-scaling-stroke}.named-feature .feature-fa-underlay{stroke-width:3.6}.settlement{stroke:#ffffff;stroke-width:.9}.settlement.abandoned{opacity:.28}.feature-label,.region-label,.settlement-label{font-family:Arial,Helvetica,sans-serif;paint-order:stroke;stroke:#fff8e6;stroke-linejoin:round;vector-effect:non-scaling-stroke}.feature-label{font-size:9px;fill:#172033;font-weight:800;stroke-width:2.8px}.region-label{font-size:11px;fill:#1f2332;font-weight:600;stroke-width:2.6px}.settlement-label{font-size:9.5px;fill:#111111;font-weight:700;stroke-width:2.0px}",
        "</style>",
        f'<rect x="{-width * 20}" y="{-height * 20}" width="{width * 41}" height="{height * 41}" fill="url(#ocean-gradient)" />',
    ]
    occupied_labels: list[_LabelBox] = []
    deferred_labels: list[str] = []
    micro_by_id = {c.micro_id: c for c in geometry.micro_cells}
    channel_by_river_id = {c.river_id: c for c in geometry.river_channels}
    ocean_cells = [w for w in geometry.water_cells if w.kind == "ocean"]
    lake_cells = [w for w in geometry.water_cells if w.kind == "lake"]

    if ocean_cells:
        parts.append('<g class="water-cell-layer ocean-water-cells">')
        for water in ocean_cells:
            scaled = [_scale(p, width, height, pad) for p in water.polygon]
            parts.append(
                f'<path class="water-cell {html.escape(water.kind)}" '
                f'data-water-id="{html.escape(water.water_id)}" data-water-kind="{html.escape(water.kind)}" '
                f'data-region-id="{html.escape(water.region_id)}" data-depth="{water.depth:.4f}" '
                f'd="{_poly_path(scaled)}" fill="{_water_cell_fill(water)}" opacity="0.56" />'
            )
        parts.append("</g>")

    if channel_by_river_id:
        mask_paths: list[str] = []
        for channel in channel_by_river_id.values():
            for poly in (channel.bank_polygon, channel.mouth_bank_polygon):
                if len(poly) >= 3:
                    mask_paths.append(_open_poly_path([_scale(p, width, height, pad) for p in poly]))
        if mask_paths:
            parts.append(
                f'<mask id="river-cut-mask" maskUnits="userSpaceOnUse" x="0" y="0" width="{width}" height="{height}">'
            )
            parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" />')
            for path_d in mask_paths:
                parts.append(f'<path class="river-cut-mask-shape" d="{path_d}" fill="#000000" />')
            parts.append("</mask>")
        parts.append('<g class="river-cut-layer">')
        for river in geometry.rivers:
            channel = channel_by_river_id.get(river.river_id)
            if channel is None:
                continue
            river_micro_ids = {mid for segment in river.segments for mid in segment.micro_ids}
            corridor_color = _river_corridor_fill(river_micro_ids, micro_by_id, overlays)
            bank_fill = _mix_color(corridor_color, "#2e88a6", 0.42)
            bank_poly = [_scale(p, width, height, pad) for p in channel.bank_polygon]
            water_poly = [_scale(p, width, height, pad) for p in channel.water_polygon]
            if bank_poly:
                parts.append(
                    f'<path class="river-bank {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                    f'd="{_open_poly_path(bank_poly)}" fill="{bank_fill}" />'
                )
            if water_poly:
                parts.append(
                    f'<path class="river-water {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                    f'd="{_open_poly_path(water_poly)}" fill="#2f8dab" />'
                )
            if channel.highlight_points:
                water_width = 0.72 + math.sqrt(max(0.0, river.flow)) * 2.35
                highlight_width = max(0.24, water_width * 0.12)
                highlight_points = [_scale(p, width, height, pad) for p in channel.highlight_points]
                parts.append(
                    f'<path class="river river-highlight {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                    f'd="{_smooth_line_path(highlight_points)}" stroke-width="{highlight_width:.2f}" opacity="0.72" />'
                )
            if channel.mouth_bank_polygon and channel.mouth_water_polygon:
                mouth_bank = [_scale(p, width, height, pad) for p in channel.mouth_bank_polygon]
                mouth_water = [_scale(p, width, height, pad) for p in channel.mouth_water_polygon]
                parts.append(
                    f'<path class="river-mouth-bank {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                    f'd="{_open_poly_path(mouth_bank)}" fill="{_mix_color(corridor_color, "#2f8dab", 0.34)}" />'
                )
                parts.append(
                    f'<path class="river-mouth {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                    f'd="{_open_poly_path(mouth_water)}" fill="#3f95ad" />'
                )
        parts.append("</g>")

    if geometry.micro_cells:
        _region_edges, coast_edges, blend_edges = _micro_edge_segments(geometry.micro_cells)
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
        terrain_mask = ' mask="url(#river-cut-mask)"' if channel_by_river_id else ""
        terrain_filter = ""
        parts.append(f'<g class="terrain-layer"{terrain_filter}{terrain_mask}>')
        scaled_micro_paths: dict[str, str] = {}
        for cell in geometry.micro_cells:
            land_polygons = getattr(cell, "land_polygons", []) or []
            if land_polygons:
                scaled_land_polygons = [
                    [_scale(p, width, height, pad) for p in poly]
                    for poly in land_polygons
                    if len(poly) >= 3
                ]
                path_d = _multi_poly_path(scaled_land_polygons)
            else:
                scaled = (
                    _micro_noisy_polygon_points(cell, noisy_edge_paths, width=width, height=height, pad=pad)
                    if noisy_edges
                    else [_scale(p, width, height, pad) for p in cell.polygon]
                )
                path_d = _poly_path(scaled)
            scaled_micro_paths[cell.micro_id] = path_d
            if not path_d:
                continue
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
                f'd="{path_d}" fill="{_micro_cell_fill(cell, overlays)}" opacity="0.96" fill-rule="evenodd" />'
            )
        parts.append("</g>")
        if noisy_edges and blend_edges:
            parts.append(f'<g class="terrain-mottle-layer" filter="url(#terrain-mottle-soften)" opacity="0.36"{terrain_mask}>')
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
            parts.append(f'<g class="terrain-blend-layer" opacity="0.34"{terrain_mask}>')
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
                f'height="{height - pad * 2}" fill="#88806a" filter="url(#terrain-grain)" opacity="0.22"{terrain_mask} />'
            )
            hillshade = _micro_hillshade(geometry.micro_cells, blend_edges)
            if hillshade:
                parts.append(f'<g class="terrain-shade-layer" opacity="1.0"{terrain_mask}>')
                for cell in geometry.micro_cells:
                    shade = hillshade.get(cell.micro_id)
                    if shade is None:
                        continue
                    color, opacity = shade
                    cls = "terrain-shade-light" if color == "#f7f0cf" else "terrain-shade"
                    parts.append(
                        f'<path class="{cls}" d="{scaled_micro_paths[cell.micro_id]}" '
                        f'fill="{color}" opacity="{opacity:.3f}" />'
                    )
                parts.append("</g>")
        boundary_paths = _dissolved_region_boundary_paths(
            geometry.cells,
            width=width,
            height=height,
            pad=pad,
        )
        if boundary_paths:
            parts.append(
                '<g class="region-boundary-layer dissolved-region-boundaries" '
                'data-boundary-source="dissolved-region-cell" opacity="0.12">'
            )
            parts.extend(boundary_paths)
            parts.append("</g>")
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
                f'<path class="coast-shelf" d="{path}" fill="none" opacity="0.26" />'
            )
        for path in coast_paths:
            parts.append(
                f'<path class="coast-shadow" d="{path}" fill="none" opacity="0.38" />'
            )
        for path in coast_paths:
            parts.append(
                f'<path class="coast-beach" d="{path}" fill="none" opacity="0.76" />'
            )
        for path in coast_paths:
            parts.append(
                f'<path class="coast-line" d="{path}" fill="none" opacity="0.78" />'
            )
        if lake_cells:
            parts.append('<g class="water-cell-layer lake-water-cells">')
            for water in lake_cells:
                scaled = [_scale(p, width, height, pad) for p in water.polygon]
                parts.append(
                    f'<path class="water-cell {html.escape(water.kind)}" '
                    f'data-water-id="{html.escape(water.water_id)}" data-water-kind="{html.escape(water.kind)}" '
                    f'data-region-id="{html.escape(water.region_id)}" data-depth="{water.depth:.4f}" '
                    f'd="{_poly_path(scaled)}" fill="{_water_cell_fill(water)}" opacity="0.82" />'
                )
            parts.append("</g>")
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

    rendered_rivers: list[tuple[RiverPath, list[tuple[float, float]], str, float, set[str], str]] = []
    for river in geometry.rivers:
        if river.river_id in channel_by_river_id:
            continue
        pts = _river_render_points(
            [_scale(p, width, height, pad) for p in river.points],
            river.river_id,
            flow=river.flow,
        )
        if len(pts) < 2:
            continue
        water_width = 0.72 + math.sqrt(max(0.0, river.flow)) * 2.35
        line_d = _smooth_line_path(pts)
        river_micro_ids = {mid for segment in river.segments for mid in segment.micro_ids}
        corridor_color = _river_corridor_fill(river_micro_ids, micro_by_id, overlays)
        rendered_rivers.append((river, pts, line_d, water_width, river_micro_ids, corridor_color))
        channel = channel_by_river_id.get(river.river_id)
        if channel is None:
            corridor_poly = _tapered_river_polygon(
                pts,
                start_width=max(1.9, water_width * 0.72 + 2.3),
                end_width=water_width + 5.2,
            )
            parts.append(
                f'<path class="river-corridor {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                f'd="{_open_poly_path(corridor_poly)}" fill="{corridor_color}" filter="url(#river-corridor-soften)" opacity="0.46" />'
            )

    for river_obj, pts, line_d, water_width, _river_micro_ids, _corridor_color in rendered_rivers:
        river = river_obj
        channel = channel_by_river_id.get(river.river_id)
        bank_fill = _mix_color(_corridor_color, "#2e88a6", 0.42)
        bank_poly = (
            [_scale(p, width, height, pad) for p in channel.bank_polygon]
            if channel is not None and channel.bank_polygon
            else _tapered_river_polygon(
                pts,
                start_width=max(0.78, water_width * 0.48 + 0.52),
                end_width=water_width + 0.68,
            )
        )
        water_poly = (
            [_scale(p, width, height, pad) for p in channel.water_polygon]
            if channel is not None and channel.water_polygon
            else _tapered_river_polygon(
                pts,
                start_width=max(0.6, water_width * 0.40),
                end_width=water_width,
            )
        )
        parts.append(
            f'<path class="river-bank {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
            f'd="{_open_poly_path(bank_poly)}" fill="{bank_fill}" />'
        )
        parts.append(
            f'<path class="river-water {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
            f'd="{_open_poly_path(water_poly)}" fill="#2f8dab" />'
        )
        if len(pts) >= 3:
            highlight_width = max(0.24, water_width * 0.12)
            highlight_points = (
                [_scale(p, width, height, pad) for p in channel.highlight_points]
                if channel is not None and channel.highlight_points
                else _offset_line_points(pts, amount=max(0.20, water_width * -0.12))
            )
            parts.append(
                f'<path class="river river-highlight {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                f'd="{_smooth_line_path(highlight_points)}" stroke-width="{highlight_width:.2f}" opacity="0.72" />'
            )
        if len(pts) >= 2 and river.points[-1] != river.points[-2]:
            if channel is not None and channel.mouth_bank_polygon and channel.mouth_water_polygon:
                mouth_bank = [_scale(p, width, height, pad) for p in channel.mouth_bank_polygon]
                mouth_water = [_scale(p, width, height, pad) for p in channel.mouth_water_polygon]
            else:
                mouth_bank, mouth_water = _river_mouth_polygons(pts, width=water_width)
            parts.append(
                f'<path class="river-mouth-bank {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                f'd="{_open_poly_path(mouth_bank)}" fill="{_mix_color(_corridor_color, "#2f8dab", 0.34)}" />'
            )
            parts.append(
                f'<path class="river-mouth {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
                f'd="{_open_poly_path(mouth_water)}" fill="#3f95ad" />'
            )

    overlay_roads = overlays.roads if overlays is not None else []
    if overlay_roads:
        max_usage = max((float(road.usage) for road in overlay_roads), default=0.0)
        parts.append('<g class="road-layer settlement-roads">')
        for road in sorted(overlay_roads, key=lambda r: (float(r.usage), r.from_settlement_id, r.to_settlement_id)):
            scaled = [_scale(p, width, height, pad) for p in road.points]
            if len(scaled) < 2:
                continue
            normalized = math.sqrt(float(road.usage) / max_usage) if max_usage > 0.0 else 0.0
            stroke_width = 1.05 + normalized * 1.75
            path_d = _smooth_line_path(scaled)
            attrs = (
                f'data-road-from-settlement-id="{html.escape(road.from_settlement_id)}" '
                f'data-road-to-settlement-id="{html.escape(road.to_settlement_id)}" '
                f'data-road-usage="{road.usage:.4f}" '
                f'data-road-actual="{road.actual_usage:.4f}" '
                f'data-road-implied="{road.implied_usage:.4f}"'
            )
            parts.append(
                f'<path class="road road-underlay" {attrs} d="{path_d}" '
                f'stroke-width="{stroke_width + 1.35:.2f}" opacity="{min(0.34, road.opacity * 0.55):.3f}" />'
            )
            parts.append(
                f'<path class="road road-line" {attrs} d="{path_d}" '
                f'stroke-width="{stroke_width:.2f}" opacity="{road.opacity:.3f}" />'
            )
        parts.append("</g>")

    overlay_settlements = overlays.settlements if overlays is not None else []
    named_feature_overlays = overlays.features if overlays is not None else []
    region_screen_polygons = {
        cell.region_id: [_scale(point, width, height, pad) for point in cell.polygon]
        for cell in geometry.cells
    }
    settlements = (
        sorted(
            overlay_settlements,
            key=lambda s: ((s.status or "").strip().lower() != "active", -s.population, s.settlement_id),
        )[: max(0, int(max_settlements))]
        if overlays is not None
        else []
    )
    occupied_markers: list[_LabelBox] = []
    occupied_thing_polygons: set[str] = set()
    settlement_positions: dict[str, tuple[float, float, float]] = {}
    displayed_settlements: list[SettlementMapOverlay] = []
    for settlement in settlements:
        sx, sy = _scale((settlement.x, settlement.y), width, height, pad)
        sr = max(3.6, min(6.8, 3.6 + math.sqrt(max(0, settlement.population)) * 0.060))
        active_status = settlement.status.strip().lower() != "abandoned"
        if active_status and not _claim_active_thing_polygon(
            occupied_thing_polygons,
            geometry,
            x=sx,
            y=sy,
            width=width,
            height=height,
            pad=pad,
            region_id=settlement.region_id,
        ):
            continue
        displayed_settlements.append(settlement)
        settlement_positions[settlement.settlement_id] = (sx, sy, sr)
        _claim_marker(occupied_markers, _marker_box(sx, sy, sr, padding=4.0), bounds=(width, height))
    settlement_label_parts: dict[str, str] = {}
    if labels:
        for settlement in displayed_settlements:
            if settlement.status.strip().lower() == "abandoned":
                continue
            x, y, radius = settlement_positions.get(
                settlement.settlement_id,
                (*_scale((settlement.x, settlement.y), width, height, pad), 2.0),
            )
            shown = ((settlement.display_name or "").strip() or settlement.settlement_id)[:24]
            chosen: tuple[float, float, str] | None = None
            for dx, dy, anchor in (
                (radius + 2.2, 3.0, "start"),
                (-(radius + 2.2), 3.0, "end"),
                (0.0, -(radius + 3.0), "middle"),
                (0.0, radius + 10.0, "middle"),
            ):
                box = _label_box(x + dx, y + dy, shown, 9.5, anchor=anchor)
                if _claim_label(occupied_labels, box, bounds=(width, height)):
                    chosen = (dx, dy, anchor)
                    break
            if chosen is None:
                chosen = (radius + 2.2, 3.0, "start")
            dx, dy, anchor = chosen
            label_x = x + dx
            label_y = y + dy
            settlement_label_parts[settlement.settlement_id] = (
                f'<text class="settlement-label" data-settlement-id="{html.escape(settlement.settlement_id)}" '
                f'data-region-id="{html.escape(settlement.region_id)}" '
                f'data-point-x="{x:.1f}" data-point-y="{y:.1f}" data-dx="{dx:.2f}" data-dy="{dy:.2f}" '
                f'x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{anchor}">{html.escape(shown)}</text>'
            )
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

    def append_feature_label(
        *,
        feature_id: str,
        region_id: str,
        name: str,
        kind: str,
        etymology: str,
        x: float,
        y: float,
        preferred_font_size: float = 9.2,
        force: bool = False,
    ) -> bool:
        nonlocal feature_labels
        shown = name[:24]
        placements = (
            (4.8, -3.2, "start"),
            (4.8, 8.2, "start"),
            (-4.8, -3.2, "end"),
            (-4.8, 8.2, "end"),
            (0.0, -7.4, "middle"),
            (0.0, 11.0, "middle"),
        )
        chosen: tuple[float, float, str, _LabelBox] | None = None
        for dx, dy, anchor in placements:
            box = _label_box(x + dx, y + dy, shown, preferred_font_size, anchor=anchor)
            if _claim_label(occupied_labels, box, bounds=(width, height)):
                chosen = (dx, dy, anchor, box)
                break
        if chosen is None:
            if not force:
                return False
            dx, dy, anchor = (4.8, 8.2, "start")
            box = _label_box(x + dx, y + dy, shown, preferred_font_size, anchor=anchor)
            chosen = (dx, dy, anchor, box)
        label_dx, label_dy, label_anchor, label_box = chosen
        label_x = x + label_dx
        label_y = y + label_dy
        parts.append(
            f'<text class="feature-label" data-feature-id="{html.escape(feature_id)}" '
            f'data-region-id="{html.escape(region_id)}" data-feature-name="{html.escape(name)}" '
            f'data-feature-kind="{html.escape(kind)}" data-feature-etymology="{html.escape(etymology)}" '
            f'data-point-x="{x:.1f}" data-point-y="{y:.1f}" data-dx="{label_dx:.2f}" data-dy="{label_dy:.2f}" '
            f'x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{label_anchor}">{html.escape(shown)}</text>'
        )
        feature_labels += 1
        return True

    if overlay_settlements and not named_feature_overlays:
        renderable_features = [
            f for f in geometry.features if _feature_is_near_settlement(f, overlay_settlements)
        ]
    elif overlay_settlements:
        named_ids = {f.feature_id for f in named_feature_overlays}
        named_source_ids = {
            f.source_region_feature_id
            for f in named_feature_overlays
            if f.source_region_feature_id
        }
        renderable_features = [
            f
            for f in geometry.features
            if f.feature_id not in named_source_ids
            and (
                f.feature_id in named_ids
                or _feature_is_near_settlement(f, overlay_settlements, max_distance=0.025)
            )
        ]
    else:
        named_source_ids = {
            f.source_region_feature_id
            for f in named_feature_overlays
            if f.source_region_feature_id
        }
        renderable_features = [
            f for f in geometry.features if f.feature_id not in named_source_ids
        ]
    sorted_features = sorted(renderable_features, key=lambda f: (-f.importance, f.region_id, f.feature_id))
    drawn_named_ids: set[str] = set()
    for named in named_feature_overlays:
        feature_class = _FEATURE_CLASS_BY_KIND.get(named.kind.strip().lower(), "landform")
        color = _FEATURE_COLORS.get(feature_class, _FEATURE_COLORS["landform"])
        radius = 3.0
        allowed_polygon = _feature_marker_allowed_polygon(
            geometry,
            region_id=named.region_id,
            kind=named.kind,
            world_point=(named.x, named.y),
            region_screen_polygons=region_screen_polygons,
            width=width,
            height=height,
            pad=pad,
        )
        x, y = _coastline_marker_screen_point(
            geometry,
            region_id=named.region_id,
            kind=named.kind,
            world_point=(named.x, named.y),
            radius=radius,
            width=width,
            height=height,
            pad=pad,
            allowed_polygon=allowed_polygon,
        )
        trial_occupied_markers = list(occupied_markers)
        x, y = _place_coastline_marker(
            geometry,
            region_id=named.region_id,
            kind=named.kind,
            x=x,
            y=y,
            radius=radius,
            occupied=trial_occupied_markers,
            bounds=(width, height),
            seed=(named.feature_id, named.display_name),
            width=width,
            height=height,
            pad=pad,
            max_offset=42.0,
            allowed_polygon=allowed_polygon,
        )
        if not _claim_active_thing_polygon(
            occupied_thing_polygons,
            geometry,
            x=x,
            y=y,
            width=width,
            height=height,
            pad=pad,
            region_id=named.region_id,
        ):
            continue
        occupied_markers[:] = trial_occupied_markers
        drawn_named_ids.add(named.feature_id)
        attrs = (
            f'data-feature-id="{html.escape(named.feature_id)}" '
            f'data-region-id="{html.escape(named.region_id)}" data-feature-name="{html.escape(named.display_name)}" '
            f'data-feature-kind="{html.escape(named.kind)}" data-feature-etymology="{html.escape(named.etymology or "")}" '
            f'data-source-region-feature-id="{html.escape(named.source_region_feature_id or "")}" '
            'data-feature-named="1" '
        )
        parts.append(
            _feature_symbol_svg(
                feature_class=feature_class,
                kind=named.kind,
                attrs=attrs,
                x=x,
                y=y,
                radius=radius,
                color=color,
                named=True,
            )
        )
        if labels:
            append_feature_label(
                feature_id=named.feature_id,
                region_id=named.region_id,
                name=named.display_name,
                kind=named.kind,
                etymology=named.etymology or "",
                x=x,
                y=y,
                force=True,
            )
    for feature in sorted_features:
        if feature.feature_id in drawn_named_ids:
            continue
        color = _FEATURE_COLORS.get(feature.feature_class, _FEATURE_COLORS["landform"])
        radius = _feature_radius(feature)
        allowed_polygon = _feature_marker_allowed_polygon(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            world_point=(feature.x, feature.y),
            region_screen_polygons=region_screen_polygons,
            width=width,
            height=height,
            pad=pad,
        )
        x, y = _coastline_marker_screen_point(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            world_point=(feature.x, feature.y),
            radius=radius,
            width=width,
            height=height,
            pad=pad,
            allowed_polygon=allowed_polygon,
        )
        trial_occupied_markers = list(occupied_markers)
        x, y = _place_coastline_marker(
            geometry,
            region_id=feature.region_id,
            kind=feature.kind,
            x=x,
            y=y,
            radius=radius,
            occupied=trial_occupied_markers,
            bounds=(width, height),
            seed=(feature.feature_id, feature.label),
            width=width,
            height=height,
            pad=pad,
            max_offset=36.0,
            allowed_polygon=allowed_polygon,
        )
        if not _claim_active_thing_polygon(
            occupied_thing_polygons,
            geometry,
            x=x,
            y=y,
            width=width,
            height=height,
            pad=pad,
            region_id=feature.region_id,
        ):
            continue
        occupied_markers[:] = trial_occupied_markers
        attrs = (
            f'data-feature-id="{html.escape(feature.feature_id)}" '
            f'data-region-id="{html.escape(feature.region_id)}" data-feature-name="{html.escape(feature.label)}" '
            f'data-feature-kind="{html.escape(feature.kind)}" data-feature-etymology="" '
            'data-feature-named="0" '
        )
        parts.append(
            _feature_symbol_svg(
                feature_class=feature.feature_class,
                kind=feature.kind,
                attrs=attrs,
                x=x,
                y=y,
                radius=radius,
                color=color,
            )
        )
        if labels:
            append_feature_label(
                feature_id=feature.feature_id,
                region_id=feature.region_id,
                name=feature.label,
                kind=feature.kind,
                etymology="",
                x=x,
                y=y,
                force=True,
            )

    if overlays is not None:
        for settlement in displayed_settlements:
            x, y, radius = settlement_positions.get(
                settlement.settlement_id,
                (*_scale((settlement.x, settlement.y), width, height, pad), 2.0),
            )
            status_class = "abandoned" if settlement.status.strip().lower() == "abandoned" else "active"
            parts.append(
                f'<circle class="settlement {status_class}" data-settlement-id="{html.escape(settlement.settlement_id)}" '
                f'data-region-id="{html.escape(settlement.region_id)}" cx="{x:.1f}" cy="{y:.1f}" '
                f'r="{radius:.1f}" data-base-r="{radius:.1f}" fill="#111111" />'
            )
            label = settlement_label_parts.get(settlement.settlement_id)
            if label:
                parts.append(label)

    parts.extend(deferred_labels)

    parts.append("</svg>")
    return "\n".join(parts) + "\n"

