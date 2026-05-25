"""SVG rendering for generated world-map geometry."""

from __future__ import annotations

import hashlib
import html
import json
import math
import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from library.world_map_geometry import (
    MicroRegionCell,
    Point,
    RegionCell,
    RegionFeature,
    WorldMapGeometry,
    project_local_point_to_region_footprint,
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


def _feature_radius(feature: RegionFeature) -> float:
    if feature.feature_class in {"coast", "water"}:
        return 3.7
    if feature.feature_class == "relief":
        return 3.3
    return 2.8


def _label_box(x: float, y: float, text: str, font_size: float, *, anchor: str = "start") -> _LabelBox:
    width = max(10.0, len(text) * font_size * 0.54)
    height = font_size * 1.25
    if anchor == "middle":
        x0 = x - width / 2.0
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
) -> tuple[list[tuple[Point, Point]], list[tuple[Point, Point]]]:
    edge_owner: dict[tuple[Point, Point], MicroRegionCell] = {}
    region_edges: list[tuple[Point, Point]] = []
    coast_edges: list[tuple[Point, Point]] = []
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
    coast_edges.extend(edge_owner.keys())
    return region_edges, coast_edges


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


def load_world_map_overlays(
    *,
    geometry: WorldMapGeometry,
    save_db_path: Path | str | None,
    max_settlements: int = DEFAULT_MAX_SETTLEMENT_OVERLAYS,
) -> WorldMapOverlays:
    """Load optional settlement and polity overlays from ``save.sqlite``."""
    if save_db_path is None:
        return WorldMapOverlays(settlements=[], polities_by_region_id={})
    path = Path(save_db_path)
    if not path.exists():
        return WorldMapOverlays(settlements=[], polities_by_region_id={})
    settlement_limit = max(0, int(max_settlements))
    cells = geometry.cell_by_region_id()
    settlements: list[SettlementMapOverlay] = []
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
            if len(rows) < settlement_limit:
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
                if local is None:
                    rng = random.Random(_stable_seed(geometry.version, row["settlement_id"], "overlay"))
                    local = (0.5 + rng.uniform(-0.16, 0.16), 0.5 + rng.uniform(-0.16, 0.16))
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
    return WorldMapOverlays(settlements=settlements, polities_by_region_id=polities)


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
        "const m=Math.max(.82,Math.min(1.55,Math.pow(z,.28)));"
        "svg.querySelectorAll('.region-label').forEach(e=>e.style.fontSize=(11*m/z)+'px');"
        "svg.querySelectorAll('.feature-label').forEach(e=>e.style.fontSize=(9*m/z)+'px');"
        "svg.querySelectorAll('.settlement-label').forEach(e=>e.style.fontSize=(10*m/z)+'px');"
        "svg.querySelectorAll('.feature').forEach(e=>{const b=+e.dataset.baseR||3;e.setAttribute('r',Math.max(1.6,Math.min(5.6,b*m/z)));});"
        "svg.querySelectorAll('.settlement').forEach(e=>{const b=+e.dataset.baseR||4;e.setAttribute('r',Math.max(2.2,Math.min(7.4,b*m/z)));});};"
    )
    zoom_handlers = (
        f"data-original-viewbox='0 0 {width} {height}' "
        f"onwheel=\"{zoom_script}const s=this.viewBox.baseVal;const k=event.deltaY>0?1.16:.86;"
        "const r=this.getBoundingClientRect();const px=(event.clientX-r.left)/r.width;"
        "const py=(event.clientY-r.top)/r.height;const nx=s.width*k,ny=s.height*k;"
        "s.x+=s.width*px-nx*px;s.y+=s.height*py-ny*py;s.width=nx;s.height=ny;"
        "zf(this);event.preventDefault();event.stopPropagation();\" "
        "onpointerdown=\"if(event.target.closest('[data-settlement-id],[data-region-id],[data-region-label]')){this.dataset.pan='0';this.dataset.dragged='0';return;}this.dataset.pan='1';this.dataset.dragged='0';this.dataset.px=event.clientX;this.dataset.py=event.clientY;"
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
        "<style>",
        ".cell{stroke:#74694f;stroke-width:1.0;stroke-linejoin:round}.micro-cell{stroke:none}.region-boundary{stroke:#151b2d;stroke-width:.72;stroke-linecap:round}.coast-line{stroke:#20263d;stroke-width:1.45;stroke-linecap:round}.route.land_route{stroke:#725b42}.route.sea_route{stroke:#467aa2;stroke-dasharray:6 5}.river{stroke:#2a8bc8;stroke-linecap:round;stroke-linejoin:round}.feature,.settlement{vector-effect:non-scaling-stroke}.settlement{stroke:#f3ead4;stroke-width:1.4}.settlement.abandoned{opacity:.35}.feature-label,.region-label,.settlement-label{font-family:Georgia,serif;paint-order:stroke;stroke:#f3ead4;stroke-width:3px;stroke-linejoin:round;vector-effect:non-scaling-stroke}.feature-label{font-size:9px;fill:#3d3427}.region-label{font-size:11px;fill:#1f2332;font-weight:600}.settlement-label{font-size:10px;fill:#2c2118}",
        "</style>",
        f'<rect x="{-width * 20}" y="{-height * 20}" width="{width * 41}" height="{height * 41}" fill="#454a78" />',
    ]
    occupied_labels: list[_LabelBox] = []
    deferred_labels: list[str] = []

    if geometry.micro_cells:
        for cell in geometry.micro_cells:
            scaled = [_scale(p, width, height, pad) for p in cell.polygon]
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
        region_edges, coast_edges = _micro_edge_segments(geometry.micro_cells)
        for a, b in coast_edges:
            pts = [_scale(a, width, height, pad), _scale(b, width, height, pad)]
            parts.append(
                f'<path class="coast-line" d="{_line_path(pts)}" fill="none" opacity="0.78" />'
            )
        for a, b in region_edges:
            pts = [_scale(a, width, height, pad), _scale(b, width, height, pad)]
            parts.append(
                f'<path class="region-boundary" d="{_line_path(pts)}" fill="none" opacity="0.22" />'
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

    for edge in geometry.edges:
        pts = [_scale(p, width, height, pad) for p in edge.points]
        parts.append(
            f'<path class="route {html.escape(edge.edge_class)}" data-from="{html.escape(edge.from_region_id)}" '
            f'data-to="{html.escape(edge.to_region_id)}" d="{_line_path(pts)}" fill="none" '
            f'stroke-width="{max(0.65, 2.15 / (1.0 + edge.friction / 5.0)):.2f}" opacity="0.28" />'
        )

    for river in geometry.rivers:
        pts = [_scale(p, width, height, pad) for p in river.points]
        parts.append(
            f'<path class="river {html.escape(river.river_class)}" data-river-id="{html.escape(river.river_id)}" '
            f'd="{_line_path(pts)}" fill="none" stroke-width="{1.1 + river.flow * 2.35:.2f}" opacity="0.82" />'
        )

    if labels:
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
    sorted_features = sorted(geometry.features, key=lambda f: (-f.importance, f.region_id, f.feature_id))
    for feature in sorted_features:
        x, y = _scale((feature.x, feature.y), width, height, pad)
        color = _FEATURE_COLORS.get(feature.feature_class, _FEATURE_COLORS["landform"])
        parts.append(
            f'<circle class="feature {html.escape(feature.feature_class)}" data-feature-id="{html.escape(feature.feature_id)}" '
            f'cx="{x:.1f}" cy="{y:.1f}" r="{_feature_radius(feature):.1f}" data-base-r="{_feature_radius(feature):.1f}" fill="{color}" opacity="0.9" />'
        )
        if labels and feature.importance >= 0.76 and feature_labels < max_feature_labels:
            box = _label_box(x + 5.0, y - 4.0, feature.label, 9.0)
            if not _claim_label(occupied_labels, box, bounds=(width, height)):
                continue
            parts.append(
                f'<text class="feature-label" x="{x + 5.0:.1f}" y="{y - 4.0:.1f}">{html.escape(feature.label)}</text>'
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
            radius = max(3.4, min(8.0, 3.4 + math.sqrt(max(0, settlement.population)) * 0.12))
            parts.append(
                f'<circle class="settlement {status_class}" data-settlement-id="{html.escape(settlement.settlement_id)}" '
                f'data-region-id="{html.escape(settlement.region_id)}" cx="{x:.1f}" cy="{y:.1f}" '
                f'r="{radius:.1f}" data-base-r="{radius:.1f}" fill="#5a3824" />'
            )
            if labels and settlement.status.strip().lower() != "abandoned" and settlement_labels < max_settlement_labels:
                shown = settlement.display_name[:24]
                box = _label_box(x + radius + 2.0, y + 3.0, shown, 10.0)
                if not _claim_label(occupied_labels, box, bounds=(width, height)):
                    continue
                parts.append(
                    f'<text class="settlement-label" data-settlement-id="{html.escape(settlement.settlement_id)}" '
                    f'data-region-id="{html.escape(settlement.region_id)}" '
                    f'x="{x + radius + 2.0:.1f}" y="{y + 3.0:.1f}">{html.escape(shown)}</text>'
                )
                settlement_labels += 1

    parts.extend(deferred_labels)

    parts.append("</svg>")
    return "\n".join(parts) + "\n"

