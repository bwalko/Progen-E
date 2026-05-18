"""Read-only Gradio browser for History Project config CSVs and SQLite worlds."""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import logging
import queue
import shlex
import re
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Iterable

import gradio as gr


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
CONFIG_DIR = PROJECT_ROOT / "config"
WORLDS_DIR = PROJECT_ROOT / "worlds"
TEMP_DIR = PROJECT_ROOT / "temp"
APP_LOG_PATH = TEMP_DIR / "gradio_data_browser.log"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SIM_PROGRESS_RE = re.compile(
    r"SIM_PROGRESS\s+year=(?P<year>-?\d+)\s+end_year=(?P<end_year>-?\d+)\s+elapsed=(?P<elapsed>\d{2}:\d{2}:\d{2})"
)
LEGACY_SCORE_COLUMNS = [
    "Beauty",
    "Scholar",
    "Creative",
    "Athlete",
    "Conqueror",
    "Ruler",
    "Mystic",
    "Infamous",
    "Scandal",
    "Martyr",
]
LEGACY_SCORE_KEY_TO_LABEL = {
    "beauty_legend": "Beauty",
    "scholar_sage": "Scholar",
    "creative_genius": "Creative",
    "athletic_hero": "Athlete",
    "conqueror": "Conqueror",
    "founder_ruler": "Ruler",
    "prophet_mystic": "Mystic",
    "infamous_predator": "Infamous",
    "scandal_icon": "Scandal",
    "martyr_reformer": "Martyr",
}
LEGACY_SCORE_LABEL_TO_KEY = {v: k for k, v in LEGACY_SCORE_KEY_TO_LABEL.items()}
LOGGER = logging.getLogger("gradio_data_browser")
REGION_BROWSER_HEADERS = [
    "Name",
    "Alive",
    "Settlements",
    "Food",
    "Stability",
    "Market",
    "Prosperity",
    "Treasury",
    "Top Jobs",
]
POLITY_BROWSER_HEADERS = [
    "Name",
    "Type",
    "Status",
    "Territory",
    "Seats",
    "Holders",
    "Parent",
    "Capital",
    "Founded",
]
SETTLEMENT_BROWSER_HEADERS = [
    "Name",
    "Level",
    "Alive",
    "Region",
    "Status",
    "Food",
    "Stability",
    "Market",
    "Prosperity",
    "Founded",
    "Top Jobs",
]

from library.world_map_geometry import build_world_map_geometry  # noqa: E402
from library.world_map_svg import load_world_map_overlays, render_world_map_svg  # noqa: E402


def configure_app_logging() -> Path:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False
    if not any(isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == APP_LOG_PATH for handler in LOGGER.handlers):
        handler = logging.FileHandler(APP_LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [pid=%(process)d] %(message)s"))
        LOGGER.addHandler(handler)
    return APP_LOG_PATH


def _log_info(message: str, *args: object) -> None:
    if not LOGGER.handlers:
        configure_app_logging()
    LOGGER.info(message, *args)


def _log_exception(message: str, *args: object) -> None:
    if not LOGGER.handlers:
        configure_app_logging()
    LOGGER.exception(message, *args)


APP_CSS = """
.world-browser {
    font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.sim-progress-card {
    --sim-progress-card-bg: #fff;
    --sim-progress-card-border: #d8d8d8;
    --sim-progress-label-color: #2f343b;
    --sim-progress-track-bg: #ececec;
    --sim-progress-fill-start: #2f6fed;
    --sim-progress-fill-end: #55a6ff;
    border: 1px solid var(--sim-progress-card-border);
    border-radius: 8px;
    padding: 8px 10px;
    background: var(--sim-progress-card-bg);
}
.sim-progress-label {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    font-size: 12px;
    color: var(--sim-progress-label-color);
    font-weight: 600;
    margin-bottom: 6px;
}
.sim-progress-track {
    height: 12px;
    border-radius: 999px;
    background: var(--sim-progress-track-bg);
    overflow: hidden;
}
.sim-progress-fill {
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, var(--sim-progress-fill-start), var(--sim-progress-fill-end));
    transition: width .2s linear;
}
.dark .sim-progress-card,
body.dark .sim-progress-card,
[data-theme="dark"] .sim-progress-card {
    --sim-progress-card-bg: #171b22;
    --sim-progress-card-border: #4b5565;
    --sim-progress-label-color: #f3f6fb;
    --sim-progress-track-bg: #303745;
    --sim-progress-fill-start: #60a5fa;
    --sim-progress-fill-end: #93c5fd;
}
#person-table,
#person-table table,
#person-table .table-wrap,
#person-table .dataframe {
    font-size: 12px !important;
}
#person-table th,
#person-table td,
#person-table [role="columnheader"],
#person-table [role="gridcell"] {
    font-size: 12px !important;
    line-height: 1.2 !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
#person-table th,
#person-table [role="columnheader"] {
    min-width: 56px !important;
}
#person-table th:nth-child(1),
#person-table td:nth-child(1) {
    min-width: 130px !important;
}
#person-table th:nth-child(10),
#person-table td:nth-child(10) {
    max-width: 230px !important;
}
.person-sheet {
    --person-sheet-bg-start: #fbf8ef;
    --person-sheet-bg-end: #f4ecd8;
    --person-sheet-border: #d6c7a1;
    --person-sheet-text: #2f2a21;
    --person-sheet-title: #58452a;
    --person-sheet-muted: #6f6046;
    --person-sheet-rule: #dccda9;
    --person-sheet-card-bg: rgba(255, 255, 255, .55);
    --person-sheet-card-border: #e4d7b8;
    --person-sheet-accent: #9d8352;
    --person-sheet-badge-text: #4f3f25;
    --person-sheet-pill-border: #b99f69;
    --person-sheet-pill-bg: rgba(255,255,255,.48);
    --person-sheet-relation-bg: rgba(255,255,255,.45);
    --person-sheet-link: #4d5f35;
    --person-sheet-link-hover: #263515;
    --person-sheet-trait-track: linear-gradient(90deg, #9c4f48 0%, #d8c27a 28%, #4f7f74 50%, #d8c27a 72%, #9c4f48 100%);
    --person-sheet-trait-marker: #211b14;
    --person-sheet-trait-marker-ring: rgba(255,255,255,.8);
    --person-sheet-legacy-track: rgba(111, 96, 70, .20);
    --person-sheet-legacy-fill: #6a7d3e;
    border: 1px solid var(--person-sheet-border) !important;
    background: linear-gradient(180deg, var(--person-sheet-bg-start) 0%, var(--person-sheet-bg-end) 100%) !important;
    color: var(--person-sheet-text) !important;
    padding: 18px;
    border-radius: 8px;
}
.place-sheet {
    --place-bg: #fbf8ef;
    --place-border: #d6c7a1;
    --place-text: #2f2a21;
    --place-muted: #6f6046;
    --place-title: #58452a;
    --place-card-bg: rgba(255, 255, 255, .55);
    --place-card-border: #e4d7b8;
    --place-map-bg: #efe6cd;
    --place-map-land: #d9c79c;
    --place-map-water: #9dbbd2;
    --place-map-feature: #8c7959;
    --place-map-town: #583b22;
    border: 1px solid var(--place-border) !important;
    background: var(--place-bg) !important;
    color: var(--place-text) !important;
    border-radius: 8px;
    padding: 14px;
}
.dark .place-sheet,
body.dark .place-sheet,
[data-theme="dark"] .place-sheet {
    --place-bg: #171b1f;
    --place-border: #5f533d;
    --place-text: #f4ead7;
    --place-muted: #cbb995;
    --place-title: #f3d79b;
    --place-card-bg: rgba(255,255,255,.07);
    --place-card-border: #5a513f;
    --place-map-bg: #20262d;
    --place-map-land: #4d503f;
    --place-map-water: #31546d;
    --place-map-feature: #b9a67f;
    --place-map-town: #f2d59a;
}
.place-sheet,
.place-sheet * {
    box-sizing: border-box;
}
.place-sheet h2 {
    color: var(--place-title) !important;
    margin: 0 0 4px;
    font-size: 24px;
}
.place-sheet h3 {
    color: var(--place-title) !important;
    margin: 16px 0 8px;
    font-size: 16px;
}
.place-subtitle,
.place-muted {
    color: var(--place-muted) !important;
}
.place-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: 8px;
    margin: 12px 0;
}
.place-card {
    background: var(--place-card-bg) !important;
    border: 1px solid var(--place-card-border) !important;
    border-radius: 6px;
    padding: 8px;
}
.place-card .label {
    display: block;
    color: var(--place-muted) !important;
    font-size: 11px;
    text-transform: uppercase;
}
.place-card .value {
    color: var(--place-text) !important;
    font-size: 16px;
    font-weight: 650;
}
.place-columns {
    display: grid;
    grid-template-columns: minmax(240px, 1fr) minmax(240px, 1fr);
    gap: 12px;
}
.place-sheet ul {
    margin: 6px 0 0 18px;
    padding: 0;
}
.place-sheet li {
    margin: 3px 0;
}
.places-browser-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.places-browser-table th,
.places-browser-table td {
    border-bottom: 1px solid var(--place-card-border);
    padding: 7px 8px;
    text-align: left;
    vertical-align: top;
}
.places-browser-table th {
    color: var(--place-title) !important;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: .02em;
}
.places-browser-table tr[data-place-row] {
    cursor: pointer;
}
.places-browser-table tr[data-place-row]:hover {
    background: var(--place-card-bg) !important;
}
.place-map {
    width: 100%;
    max-height: 360px;
    border: 1px solid var(--place-card-border);
    border-radius: 6px;
    background: var(--place-map-bg);
}
.world-map-card svg {
    width: 100%;
    height: auto;
    max-height: 760px;
    border: 1px solid var(--place-card-border);
    border-radius: 6px;
    background: var(--place-map-bg);
}
.world-map-card [data-region-id],
.world-map-card [data-region-label],
.world-map-card [data-settlement-id] {
    cursor: pointer;
}
.world-map-open-controls {
    display: none !important;
}
@media (max-width: 900px) {
    .place-columns {
        grid-template-columns: 1fr;
    }
}
.dark .person-sheet,
body.dark .person-sheet,
[data-theme="dark"] .person-sheet {
    --person-sheet-bg-start: #171b1f;
    --person-sheet-bg-end: #22201a;
    --person-sheet-border: #5f533d;
    --person-sheet-text: #f4ead7;
    --person-sheet-title: #f3d79b;
    --person-sheet-muted: #cbb995;
    --person-sheet-rule: #5f533d;
    --person-sheet-card-bg: rgba(255, 255, 255, .07);
    --person-sheet-card-border: #5a513f;
    --person-sheet-accent: #c5a86a;
    --person-sheet-badge-text: #f3d79b;
    --person-sheet-pill-border: #8d7549;
    --person-sheet-pill-bg: rgba(255,255,255,.08);
    --person-sheet-relation-bg: rgba(255,255,255,.06);
    --person-sheet-link: #bddd89;
    --person-sheet-link-hover: #ddf7b5;
    --person-sheet-trait-track: linear-gradient(90deg, #a85a5a 0%, #c4a95c 28%, #5ba88c 50%, #c4a95c 72%, #a85a5a 100%);
    --person-sheet-trait-marker: #f8f3e8;
    --person-sheet-trait-marker-ring: rgba(0,0,0,.75);
    --person-sheet-legacy-track: rgba(244, 234, 215, .18);
    --person-sheet-legacy-fill: #9fc46b;
}
.person-sheet,
.person-sheet * {
    box-sizing: border-box;
}
.person-sheet :where(h1, h2, h3, h4, h5, h6, p, div, span, strong, em, li, section, article),
.person-sheet :where(.prose, .markdown, .md, .output-html, .gr-html) {
    color: var(--person-sheet-text) !important;
}
.person-title {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    border-bottom: 1px solid var(--person-sheet-rule);
    padding-bottom: 12px;
    margin-bottom: 14px;
}
.person-title h2 {
    color: var(--person-sheet-text) !important;
    font-size: 28px;
    line-height: 1.1;
    margin: 0;
}
.person-title .badge {
    border: 1px solid var(--person-sheet-accent);
    color: var(--person-sheet-badge-text) !important;
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: .04em;
}
.detail-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 10px;
    margin: 12px 0 16px;
}
.detail-card {
    background: var(--person-sheet-card-bg) !important;
    border: 1px solid var(--person-sheet-card-border) !important;
    border-radius: 6px;
    padding: 10px;
}
.detail-label {
    color: var(--person-sheet-muted) !important;
    font-size: 12px;
    text-transform: uppercase;
}
.detail-value {
    color: var(--person-sheet-text) !important;
    font-size: 16px;
    font-weight: 650;
}
.section-title {
    margin: 18px 0 8px;
    color: var(--person-sheet-title) !important;
    font-size: 18px;
}
.trait-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 8px 12px;
}
.trait-row {
    display: grid;
    grid-template-columns: 96px minmax(72px, auto) 1fr;
    align-items: center;
    gap: 8px;
    font-size: 13px;
}
.trait-name {
    color: var(--person-sheet-text) !important;
    text-transform: capitalize;
}
.trait-value {
    color: var(--person-sheet-text) !important;
    font-variant-numeric: tabular-nums;
    text-align: right;
}
.trait-base-value {
    color: var(--person-sheet-muted) !important;
    font-size: 11px;
    font-weight: 500;
}
.trait-track {
    position: relative;
    height: 12px;
    border-radius: 999px;
    background: var(--person-sheet-trait-track);
    overflow: hidden;
}
.trait-marker {
    position: absolute;
    top: -3px;
    width: 4px;
    height: 18px;
    border-radius: 3px;
    background: var(--person-sheet-trait-marker);
    box-shadow: 0 0 0 1px var(--person-sheet-trait-marker-ring);
}
.legacy-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 8px;
}
.legacy-score {
    border: 1px solid var(--person-sheet-card-border) !important;
    background: var(--person-sheet-card-bg) !important;
    border-radius: 6px;
    padding: 8px 9px;
}
.legacy-score-head {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    font-size: 13px;
    font-weight: 700;
}
.legacy-score-desc {
    color: var(--person-sheet-muted) !important;
    font-size: 12px;
    line-height: 1.25;
    margin-top: 4px;
}
.legacy-track {
    height: 7px;
    border-radius: 999px;
    background: var(--person-sheet-legacy-track) !important;
    margin-top: 7px;
    overflow: hidden;
}
.legacy-fill {
    height: 100%;
    border-radius: 999px;
    background: var(--person-sheet-legacy-fill) !important;
}
.pill-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}
.pill {
    border: 1px solid var(--person-sheet-pill-border);
    background: var(--person-sheet-pill-bg);
    color: var(--person-sheet-text) !important;
    border-radius: 999px;
    padding: 4px 9px;
    font-size: 13px;
}
.relation-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 8px;
}
.relation {
    border-left: 3px solid var(--person-sheet-accent);
    background: var(--person-sheet-relation-bg);
    color: var(--person-sheet-text) !important;
    padding: 7px 9px;
}
.muted {
    color: var(--person-sheet-muted) !important;
}
.person-link {
    color: var(--person-sheet-link) !important;
    font-weight: 700;
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
}
.person-link:hover {
    color: var(--person-sheet-link-hover) !important;
}
.sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}
"""

_LEGACY_INDICES_MODULE = None


def _world_names() -> list[str]:
    if not WORLDS_DIR.exists():
        return []
    return sorted(path.name for path in WORLDS_DIR.iterdir() if path.is_dir())


def _csv_names() -> list[str]:
    if not CONFIG_DIR.exists():
        return []
    return sorted(path.name for path in CONFIG_DIR.glob("*.csv"))


def _db_path(world: str, db_kind: str) -> Path:
    filename = "config.sqlite" if db_kind == "Config DB" else "save.sqlite"
    return WORLDS_DIR / (world or "").strip() / filename


def _file_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def render_world_map_html(
    world: str,
    include_overlays: bool = True,
    noisy_edges: bool = True,
    labels: bool = True,
) -> str:
    world_id = (world or "").strip() or "default"
    cfg = _db_path(world_id, "Config DB")
    if not cfg.exists():
        return (
            '<div class="place-sheet">'
            f'<p class="place-muted">No config.sqlite found for {html.escape(world_id)}.</p>'
            "</div>"
        )
    save = _db_path(world_id, "Save DB")
    return _render_world_map_html_cached(
        world_id,
        bool(include_overlays),
        bool(noisy_edges),
        bool(labels),
        str(cfg),
        _file_mtime_ns(cfg),
        str(save),
        _file_mtime_ns(save),
    )


@lru_cache(maxsize=32)
def _render_world_map_html_cached(
    world_id: str,
    include_overlays: bool,
    noisy_edges: bool,
    labels: bool,
    cfg_path: str,
    _cfg_mtime_ns: int,
    save_path: str,
    _save_mtime_ns: int,
) -> str:
    cfg = Path(cfg_path)
    save = Path(save_path)
    try:
        geometry = build_world_map_geometry(world=world_id, db_path=cfg)
        overlays = (
            load_world_map_overlays(geometry=geometry, save_db_path=save)
            if include_overlays
            else None
        )
        svg = render_world_map_svg(
            geometry,
            width=1200,
            height=800,
            noisy_edges=bool(noisy_edges),
            labels=bool(labels),
            overlays=overlays,
        )
    except Exception as exc:
        return (
            '<div class="place-sheet">'
            f'<p class="place-muted">Could not render world map: {html.escape(str(exc))}</p>'
            "</div>"
        )
    overlay_text = "settlements and polities" if include_overlays else "base geography only"
    return (
        f'<div class="place-sheet world-map-card" onclick="{_world_map_click_onclick()}">'
        f"<h2>{html.escape(world_id)} World Map</h2>"
        f'<p class="place-muted">Generated polygon geography; showing {html.escape(overlay_text)}. '
        "Click a region or settlement to open its detail sheet.</p>"
        f"{svg}"
        "</div>"
    )


def _world_map_click_onclick() -> str:
    return html.escape(
        (
            "const target=event.target;"
            "if(!target||!target.closest){return true;}"
            "const town=target.closest('[data-settlement-id]');"
            "const region=town||target.closest('[data-region-id],[data-region-label]');"
            "if(!region){return true;}"
            "const id=town?town.dataset.settlementId:(region.dataset.regionId||region.dataset.regionLabel);"
            "if(!id){return true;}"
            "event.preventDefault();event.stopPropagation();"
            "const input=document.querySelector('#map-open-selection textarea,#map-open-selection input');"
            "const button=document.querySelector('#map-open-button button,#map-open-button');"
            "if(input&&button){"
            "const value=JSON.stringify({view:town?'Towns':'Regions',id:id});"
            "const descriptor=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input),'value');"
            "if(descriptor&&descriptor.set){descriptor.set.call(input,value);}else{input.value=value;}"
            "input.dispatchEvent(new Event('input',{bubbles:true}));"
            "input.dispatchEvent(new Event('change',{bubbles:true}));"
            "button.click();"
            "}"
            "return false;"
        ),
        quote=True,
    )


@contextmanager
def _connect_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.exists():
        raise FileNotFoundError(f"Missing database: {path}")
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


def _safe_int(value: object, default: int, minimum: int = 0, maximum: int = MAX_LIMIT) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Unsafe identifier: {identifier!r}")
    return f'"{identifier}"'


def _table_names(world: str, db_kind: str) -> list[str]:
    path = _db_path(world, db_kind)
    if not path.exists():
        return []
    with _connect_readonly(path) as con:
        rows = con.execute(
            "select name from sqlite_master "
            "where type='table' and name not like 'sqlite_%' "
            "order by name"
        ).fetchall()
    return [row["name"] for row in rows]


def _table_columns(con: sqlite3.Connection, table: str) -> list[str]:
    table_sql = _quote_identifier(table)
    return [row["name"] for row in con.execute(f"pragma table_info({table_sql})")]


def _has_table(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _saved_world_names(con: sqlite3.Connection) -> list[str]:
    worlds: list[str] = []
    seen: set[str] = set()
    for table in ("world_state", "simulation_people"):
        if not _has_table(con, table):
            continue
        columns = _table_columns(con, table)
        if "world" not in columns:
            if "default" not in seen:
                worlds.append("default")
                seen.add("default")
            continue
        for row in con.execute(f"select distinct world from {_quote_identifier(table)} order by world"):
            world = str(row["world"] or "").strip()
            if world and world not in seen:
                worlds.append(world)
                seen.add(world)
    return worlds


def _resolve_saved_world(con: sqlite3.Connection, selected_world: str) -> str:
    selected = (selected_world or "").strip()
    saved_worlds = _saved_world_names(con)
    if selected in saved_worlds:
        return selected
    if len(saved_worlds) == 1:
        return saved_worlds[0]
    if "default" in saved_worlds:
        return "default"
    return selected


def _dataframe(rows: Iterable[sqlite3.Row | dict[str, object]], headers: list[str], *, key: str | None = None) -> gr.Dataframe:
    values = [[row.get(header, "") if isinstance(row, dict) else row[header] for header in headers] for row in rows]
    if key and key.startswith("places-"):
        _log_info("dataframe key=%s rows=%s columns=%s", key, len(values), len(headers))
    return gr.Dataframe(
        value=values,
        headers=headers,
        datatype=["str"] * len(headers),
        column_count=len(headers),
        interactive=False,
        key=key,
    )


def _table_values(rows: Iterable[sqlite3.Row | dict[str, object]], headers: list[str]) -> list[list[object]]:
    return [[row.get(header, "") if isinstance(row, dict) else row[header] for header in headers] for row in rows]


def refresh_sqlite_tables(world: str, db_kind: str) -> tuple[gr.Dropdown, str]:
    path = _db_path(world, db_kind)
    tables = _table_names(world, db_kind)
    selected = tables[0] if tables else None
    status = f"{path} | {len(tables)} tables" if path.exists() else f"{path} is missing"
    return gr.Dropdown(choices=tables, value=selected), status


def load_sqlite_table(
    world: str,
    db_kind: str,
    table: str,
    search: str,
    limit: object,
    offset: object,
) -> tuple[gr.Dataframe, str, str]:
    if not table:
        return gr.Dataframe(value=[], headers=[]), "Choose a table.", ""

    row_limit = _safe_int(limit, DEFAULT_LIMIT, 1)
    row_offset = _safe_int(offset, 0, 0, 10_000_000)
    path = _db_path(world, db_kind)

    with _connect_readonly(path) as con:
        table_sql = _quote_identifier(table)
        columns = _table_columns(con, table)
        if not columns:
            return gr.Dataframe(value=[], headers=[]), f"No columns found for {table}.", ""

        where_sql = ""
        params: list[object] = []
        if search:
            clauses = [f"cast({_quote_identifier(column)} as text) like ?" for column in columns]
            where_sql = " where " + " or ".join(clauses)
            params.extend([f"%{search}%"] * len(columns))

        total = con.execute(f"select count(*) as n from {table_sql}{where_sql}", params).fetchone()["n"]
        rows = con.execute(
            f"select * from {table_sql}{where_sql} limit ? offset ?",
            [*params, row_limit, row_offset],
        ).fetchall()
        schema_rows = con.execute(f"pragma table_info({table_sql})").fetchall()

    schema = "\n".join(
        f"{row['cid']:>2}  {row['name']}  {row['type'] or 'ANY'}"
        + ("  PRIMARY KEY" if row["pk"] else "")
        + ("  NOT NULL" if row["notnull"] else "")
        for row in schema_rows
    )
    status = f"{path.name}.{table}: showing {len(rows)} of {total} rows at offset {row_offset}"
    return _dataframe(rows, columns), status, schema


def run_select_query(world: str, db_kind: str, sql: str, limit: object) -> tuple[gr.Dataframe, str]:
    stripped = (sql or "").strip().rstrip(";")
    if not stripped:
        return gr.Dataframe(value=[], headers=[]), "Enter a read-only SELECT query."
    lowered = stripped.lower()
    if not (lowered.startswith("select ") or lowered.startswith("with ")):
        return gr.Dataframe(value=[], headers=[]), "Only SELECT or WITH read-only queries are allowed."
    if ";" in stripped:
        return gr.Dataframe(value=[], headers=[]), "Multiple statements are not allowed."

    row_limit = _safe_int(limit, DEFAULT_LIMIT, 1)
    path = _db_path(world, db_kind)
    with _connect_readonly(path) as con:
        con.execute("pragma query_only = on")
        cur = con.execute(f"select * from ({stripped}) limit ?", (row_limit,))
        rows = cur.fetchall()
        headers = [desc[0] for desc in cur.description or []]
    return _dataframe(rows, headers), f"Returned {len(rows)} rows from {path.name}."


def _load_json_object(raw: object) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _trait_slots_for_world(world: str) -> tuple[str, ...]:
    cfg_path = _db_path(world, "Config DB")
    if not cfg_path.exists():
        return ()
    try:
        with _connect_readonly(cfg_path) as con:
            if _has_table(con, "genome_save_columns"):
                rows = con.execute(
                    """
                    select trait
                    from genome_save_columns
                    where trait is not null and trim(trait) <> ''
                    order by cast(sort_order as integer), slot
                    """
                ).fetchall()
                traits = tuple(str(r["trait"]).strip() for r in rows if str(r["trait"]).strip())
                if traits:
                    return traits
            if _has_table(con, "genome"):
                rows = con.execute(
                    """
                    select trait
                    from genome
                    where trait is not null and trim(trait) <> ''
                    order by rowid
                    """
                ).fetchall()
                return tuple(str(r["trait"]).strip() for r in rows if str(r["trait"]).strip())
    except sqlite3.Error:
        return ()
    return ()


def _decode_trait_array(values: object, trait_slots: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(values, list):
        return {}
    out: dict[str, float] = {}
    for trait, raw in zip(trait_slots, values):
        if raw is None:
            continue
        try:
            out[str(trait)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


_PERSON_COLUMN_KEYS = (
    "first_name",
    "last_name",
    "gender",
    "ethnic",
    "species",
    "birthyear",
    "deathyear",
    "birthplace",
    "birthplace_region_id",
    "birthplace_settlement_id",
    "current_settlement_id",
    "partner_person_id",
    "paramour_person_id",
    "last_birth_event_year",
    "job",
    "job_assigned_year",
    "job_era",
    "job_tier",
    "status_tendency",
    "leader_quality",
    "leader_tendency",
    "employment_status",
    "job_lost_year",
    "unemployment_started_year",
    "last_job",
    "career_fitness_score",
    "job_prosperity_01",
    "household_prosperity",
    "household_purseholder_person_id",
    "birth_litter_size",
    "life_stage",
    "maturity_height_cm",
    "maturity_weight_kg",
    "skin_tone",
    "hair",
    "eyes",
    "min_fertility_age",
    "max_fertility_age",
    "attractiveness_01",
    "sexual_nature",
    "gender_mind",
    "father_name",
    "mother_name",
)


def _person_from_row(
    row: sqlite3.Row,
    trait_slots: tuple[str, ...] = (),
) -> dict[str, object]:
    person = _load_json_object(row["person_json"]) if "person_json" in row.keys() else {}
    row_slots = tuple(str(x) for x in person.get("ts", []) if str(x).strip())
    slots = row_slots or trait_slots
    if "g" in person and "genome" not in person:
        person["genome"] = _decode_trait_array(person.get("g"), slots)
    if "mb" in person and "mind_body" not in person:
        person["mind_body"] = _decode_trait_array(person.get("mb"), slots)
    for key in _PERSON_COLUMN_KEYS:
        if key in row.keys():
            person[key] = row[key]
    return person


def _person_from_mapping(
    row: dict[str, object],
    trait_slots: tuple[str, ...] = (),
) -> dict[str, object]:
    person = _load_json_object(row.get("person_json"))
    row_slots = tuple(str(x) for x in person.get("ts", []) if str(x).strip())
    slots = row_slots or trait_slots
    if "g" in person and "genome" not in person:
        person["genome"] = _decode_trait_array(person.get("g"), slots)
    if "mb" in person and "mind_body" not in person:
        person["mind_body"] = _decode_trait_array(person.get("mb"), slots)
    for key in _PERSON_COLUMN_KEYS:
        if key in row:
            person[key] = row[key]
    return person


def _person_name(person: dict[str, object]) -> str:
    first = str(person.get("first_name") or "").strip()
    last = str(person.get("last_name") or "").strip()
    return " ".join(part for part in (first, last) if part) or "Unnamed"


def _person_first_name(person: dict[str, object]) -> str:
    return str(person.get("first_name") or "").strip() or _person_name(person)


def _same_person_id(a: object, b: object) -> bool:
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _person_summary_row(
    row: sqlite3.Row,
    current_year: int | None,
    trait_slots: tuple[str, ...] = (),
) -> dict[str, object]:
    person = _person_from_row(row, trait_slots)
    birthyear = person.get("birthyear")
    deathyear = person.get("deathyear")
    age = ""
    if birthyear is not None:
        end_year = deathyear if deathyear is not None else current_year
        if end_year is not None:
            age = int(end_year) - int(birthyear)
    out = {
        "Name": _person_name(person),
        "Life": "Alive" if row["is_alive"] else "Dead",
        "Age": age,
        "Born": birthyear or "",
        "Died": deathyear or "",
        "Gender": person.get("gender") or "",
        "Species": person.get("species") or "",
        "Ethnic": person.get("ethnic") or "",
        "Home": person.get("current_settlement_id") or person.get("birthplace") or "",
        "Traits": ", ".join(person.get("genome_trait_phrases") or person.get("genome_composite_names") or []),
    }
    out.update(_legacy_score_columns(person.get("mind_body") or person.get("genome") or {}))
    return out


def _legacy_score_columns(traits: object) -> dict[str, str]:
    out = {label: "" for label in LEGACY_SCORE_COLUMNS}
    if not isinstance(traits, dict) or not traits:
        return out
    try:
        rows = _legacy_indices_module().legacy_index_scores(traits)
    except Exception:
        return out
    for row in rows:
        label = LEGACY_SCORE_KEY_TO_LABEL.get(str(row.key))
        if label:
            out[label] = f"{float(row.score):.2f}"
    return out


def _legacy_score_value(person: dict[str, object], label: str) -> float:
    key = LEGACY_SCORE_LABEL_TO_KEY.get(label)
    traits = person.get("mind_body") or person.get("genome") or {}
    if key is None or not isinstance(traits, dict) or not traits:
        return float("-inf")
    try:
        for row in _legacy_indices_module().legacy_index_scores(traits):
            if str(row.key) == key:
                return float(row.score)
    except Exception:
        return float("-inf")
    return float("-inf")


def _sort_rows_by_legacy_score(
    rows: Iterable[sqlite3.Row],
    *,
    sort_by: str,
    sort_dir: str,
    trait_slots: tuple[str, ...] = (),
) -> list[sqlite3.Row]:
    reverse = sort_dir != "Ascending"
    return sorted(
        rows,
        key=lambda row: (
            _legacy_score_value(_person_from_row(row, trait_slots), str(sort_by)),
            int(row["person_id"]),
        ),
        reverse=reverse,
    )


def _current_year(con: sqlite3.Connection, world: str) -> int | None:
    columns = _table_columns(con, "world_state") if _has_table(con, "world_state") else []
    if "world" in columns:
        row = con.execute("select current_year from world_state where world = ?", (world,)).fetchone()
    else:
        row = con.execute("select current_year from world_state where id = 1").fetchone()
    return int(row["current_year"]) if row and row["current_year"] is not None else None


def load_people_browser(
    world: str,
    search: str,
    life_filter: str,
    min_age: object,
    max_age: object,
    sort_by: str,
    sort_dir: str,
    limit: object,
) -> tuple[gr.Dataframe, str, list[int]]:
    if not world:
        return gr.Dataframe(value=[], headers=[]), "Choose a world.", []
    row_limit = _safe_int(limit, 50, 1, 250)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return gr.Dataframe(value=[], headers=[]), f"{path} is missing. Run a simulation first.", []

    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        current_year = _current_year(con, saved_world)
    trait_slots = _trait_slots_for_world(saved_world)

    clauses: list[str] = []
    params: list[object] = []
    with _connect_readonly(path) as con:
        people_columns = _table_columns(con, "simulation_people")
        people_has_world = "world" in people_columns
        people_has_compact_columns = "birthyear" in people_columns
    age_sql = (
        "coalesce(deathyear, ?) - birthyear"
        if people_has_compact_columns
        else "coalesce(json_extract(person_json, '$.deathyear'), ?) - json_extract(person_json, '$.birthyear')"
    )
    if people_has_world:
        clauses.append("world = ?")
        params.append(saved_world)
    if life_filter == "Alive":
        clauses.append("is_alive = 1")
    elif life_filter == "Dead":
        clauses.append("is_alive = 0")
    if search:
        if people_has_compact_columns:
            clauses.append(
                "(person_json like ? or first_name like ? or last_name like ? "
                "or current_settlement_id like ? or birthplace like ?)"
            )
            params.extend([f"%{search}%"] * 5)
        else:
            clauses.append("person_json like ?")
            params.append(f"%{search}%")
    if min_age not in (None, ""):
        clauses.append(f"{age_sql} >= ?")
        params.extend([current_year, _safe_int(min_age, 0, 0, 10_000)])
    if max_age not in (None, ""):
        clauses.append(f"{age_sql} <= ?")
        params.extend([current_year, _safe_int(max_age, 10_000, 0, 10_000)])

    where_sql = " and ".join(clauses) if clauses else "1 = 1"
    legacy_sort = sort_by in LEGACY_SCORE_COLUMNS
    sort_map = {
        "ID": "person_id",
        "Name": (
            "last_name collate nocase, first_name collate nocase"
            if people_has_compact_columns
            else "json_extract(person_json, '$.last_name') collate nocase, json_extract(person_json, '$.first_name') collate nocase"
        ),
        "Age": age_sql,
        "Born": "birthyear" if people_has_compact_columns else "json_extract(person_json, '$.birthyear')",
        "Died": "deathyear" if people_has_compact_columns else "json_extract(person_json, '$.deathyear')",
        "Gender": "gender collate nocase" if people_has_compact_columns else "json_extract(person_json, '$.gender') collate nocase",
        "Species": "species collate nocase" if people_has_compact_columns else "json_extract(person_json, '$.species') collate nocase",
        "Ethnic": "ethnic collate nocase" if people_has_compact_columns else "json_extract(person_json, '$.ethnic') collate nocase",
        "Home": "coalesce(current_settlement_id, birthplace) collate nocase" if people_has_compact_columns else "coalesce(json_extract(person_json, '$.current_settlement_id'), json_extract(person_json, '$.birthplace')) collate nocase",
    }
    direction = "asc" if sort_dir == "Ascending" else "desc"
    order_sql_default = sort_map.get("Age" or "", "is_alive desc, person_id desc")
    if legacy_sort:
        order_clause = "person_id asc"
        order_params = []
    elif sort_by in (None, "", "Default"):
        order_clause = f"{order_sql_default} desc, person_id desc"
        order_params = [current_year]
    elif sort_by == "Age":
        order_sql = sort_map.get(sort_by or "", "is_alive desc, person_id desc")
        order_clause = f"{order_sql} {direction}, person_id desc"
        order_params = [current_year]
    else:
        order_sql = sort_map.get(sort_by or "", "is_alive desc, person_id desc")
        order_clause = f"{order_sql} {direction}, person_id desc"
        order_params = []

    with _connect_readonly(path) as con:
        limit_sql = "" if legacy_sort else "limit ?"
        query_params = [*params, *order_params]
        if not legacy_sort:
            query_params.append(row_limit)
        rows = con.execute(
            f"""
            select *
            from simulation_people
            where {where_sql}
            order by {order_clause}
            {limit_sql}
            """,
            query_params,
        ).fetchall()
        total = con.execute(f"select count(*) as n from simulation_people where {where_sql}", params).fetchone()["n"]

    headers = [
        "Name",
        "Life",
        "Age",
        "Born",
        "Died",
        "Gender",
        "Species",
        "Ethnic",
        "Home",
        "Traits",
        *LEGACY_SCORE_COLUMNS,
    ]
    if legacy_sort:
        rows = _sort_rows_by_legacy_score(
            rows,
            sort_by=str(sort_by),
            sort_dir=sort_dir,
            trait_slots=trait_slots,
        )[:row_limit]
    values = [_person_summary_row(row, current_year, trait_slots) for row in rows]
    person_ids = [int(row["person_id"]) for row in rows]
    filter_bits = []
    if min_age not in (None, ""):
        filter_bits.append(f"age >= {_safe_int(min_age, 0, 0, 10_000)}")
    if max_age not in (None, ""):
        filter_bits.append(f"age <= {_safe_int(max_age, 10_000, 0, 10_000)}")
    filter_text = f" | filters: {', '.join(filter_bits)}" if filter_bits else ""
    sort_text = f" | sorted by: {sort_by} {sort_dir.lower()}" if legacy_sort else ""
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {total} people{filter_text}{sort_text}{saved_world_note}. "
        "Click any person row to open their sheet."
    )
    return _dataframe(values, headers), status, person_ids


def _empty_settlements_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=SETTLEMENT_BROWSER_HEADERS)


def _empty_regions_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=REGION_BROWSER_HEADERS)


def _empty_polities_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=POLITY_BROWSER_HEADERS)


def _polity_summary_row(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> dict[str, object]:
    pid = int(row["polity_id"])
    territory = _count_one(
        con,
        "select count(*) from simulation_polity_territory where polity_id = ? and until_sim_year is null",
        (pid,),
    ) if _has_table(con, "simulation_polity_territory") else 0
    seats = _count_one(
        con,
        "select count(*) from simulation_office_seats where polity_id = ? and status = 'active'",
        (pid,),
    ) if _has_table(con, "simulation_office_seats") else 0
    holders = _count_one(
        con,
        "select count(*) from simulation_office_seats where polity_id = ? and status = 'active' and holder_person_id is not null",
        (pid,),
    ) if _has_table(con, "simulation_office_seats") else 0
    return {
        "Name": row["name"] or f"Polity {pid}",
        "Type": row["polity_type_id"] or "",
        "Status": row["status"] or "",
        "Territory": territory,
        "Seats": seats,
        "Holders": holders,
        "Parent": row["parent_polity_id"] or "",
        "Capital": _settlement_name(con, world, row["capital_settlement_id"]),
        "Founded": row["founded_sim_year"] or "",
    }


def load_polities_browser_fresh(
    world: str,
    search: str,
    status_filter: str,
    limit: object,
) -> tuple[gr.Dataframe, str, list[int]]:
    if not world:
        return _empty_polities_frame(), "Choose a world.", []
    row_limit = _safe_int(limit, 50, 1, 250)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _empty_polities_frame(), f"{path} is missing. Run a simulation first.", []

    with _connect_readonly(path) as con:
        if not _has_table(con, "simulation_polities"):
            return _empty_polities_frame(), "No simulation_polities table found.", []
        saved_world = _resolve_saved_world(con, world)
        where_sql, params = _world_where(con, "simulation_polities", saved_world)
        clauses = [where_sql]
        filter_bits: list[str] = []
        if status_filter == "Active":
            clauses.append("status = 'active'")
            filter_bits.append("active")
        elif status_filter == "Inactive":
            clauses.append("status != 'active'")
            filter_bits.append("not active")
        if search:
            clauses.append("(cast(polity_id as text) like ? or name like ? or polity_type_id like ? or status like ?)")
            params.extend([f"%{search}%"] * 4)
            filter_bits.append(f"search={search!r}")
        where_sql = " and ".join(clauses)
        rows = con.execute(
            f"""
            select *
            from simulation_polities
            where {where_sql}
            order by status = 'active' desc, polity_id
            limit ?
            """,
            (*params, row_limit),
        ).fetchall()
        total = _count_one(con, f"select count(*) from simulation_polities where {where_sql}", params)
        values = [_polity_summary_row(con, saved_world, row) for row in rows]
        polity_ids = [int(row["polity_id"]) for row in rows]

    filter_text = f" | filters: {', '.join(filter_bits)}" if filter_bits else ""
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {total} polities{filter_text}{saved_world_note}. "
        "Click any polity row to open its sheet."
    )
    return _dataframe(values, POLITY_BROWSER_HEADERS), status, polity_ids


def _region_alive_and_jobs(con: sqlite3.Connection, world: str, region_id: str) -> tuple[int, str]:
    if not _has_table(con, "simulation_people"):
        return 0, ""
    birth_region_sql = _person_birth_region_sql(con)
    people_where, people_params = _alive_where(con, world)
    alive = _count_one(
        con,
        f"select count(*) from simulation_people where {people_where} and {birth_region_sql} = ?",
        (*people_params, region_id),
    )
    jobs = _top_jobs_for_where(con, world, f"{birth_region_sql} = ?", (region_id,), limit=3)
    return alive, ", ".join(f"{job} ({count})" for job, count in jobs)


def _region_active_settlement_count(con: sqlite3.Connection, world: str, region_id: str) -> int:
    if not _has_table(con, "simulation_settlements"):
        return 0
    settlement_where, settlement_params = _world_where(con, "simulation_settlements", world)
    return _count_one(
        con,
        f"""
        select count(*)
        from simulation_settlements
        where {settlement_where}
          and region_id = ?
          and status = 'active'
        """,
        (*settlement_params, region_id),
    )


def _region_summary_row(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> dict[str, object]:
    region_id = str(row["region_id"])
    alive, jobs = _region_alive_and_jobs(con, world, region_id)
    return {
        "Name": row["region_display_name"] or region_id,
        "Alive": alive,
        "Settlements": _region_active_settlement_count(con, world, region_id),
        "Food": _fmt_number(row["food_pressure"]),
        "Stability": _fmt_number(row["stability"]),
        "Market": _fmt_number(row["market_pull"]),
        "Prosperity": _fmt_number(row["prosperity_pool"]),
        "Treasury": _fmt_number(row["treasury_balance"]),
        "Top Jobs": jobs,
    }


def load_regions_browser_fresh(world: str, search: str, limit: object) -> tuple[gr.Dataframe, str, list[str]]:
    if not world:
        return _empty_regions_frame(), "Choose a world.", []
    row_limit = _safe_int(limit, 50, 1, 250)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _empty_regions_frame(), f"{path} is missing. Run a simulation first.", []

    with _connect_readonly(path) as con:
        if not _has_table(con, "simulation_regions"):
            return _empty_regions_frame(), "No simulation_regions table found.", []
        saved_world = _resolve_saved_world(con, world)
        where_sql, params = _world_where(con, "simulation_regions", saved_world)
        clauses = [where_sql]
        filter_bits: list[str] = []
        if search:
            clauses.append("(region_id like ? or region_display_name like ?)")
            params.extend([f"%{search}%"] * 2)
            filter_bits.append(f"search={search!r}")
        where_sql = " and ".join(clauses)
        rows = con.execute(
            f"""
            select *
            from simulation_regions
            where {where_sql}
            order by total_population_cap desc, region_display_name collate nocase
            limit ?
            """,
            (*params, row_limit),
        ).fetchall()
        total = _count_one(con, f"select count(*) from simulation_regions where {where_sql}", params)
        values = [_region_summary_row(con, saved_world, row) for row in rows]
        region_ids = [str(row["region_id"]) for row in rows]

    filter_text = f" | filters: {', '.join(filter_bits)}" if filter_bits else ""
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {total} regions{filter_text}{saved_world_note}. "
        "Click any region row to open its sheet."
    )
    return _dataframe(values, REGION_BROWSER_HEADERS), status, region_ids


def _settlement_alive_and_jobs(con: sqlite3.Connection, world: str, settlement_id: str) -> tuple[int, str]:
    if not _has_table(con, "simulation_people"):
        return 0, ""
    residence_sql = _person_residence_sql(con)
    people_where, people_params = _alive_where(con, world)
    alive = _count_one(
        con,
        f"select count(*) from simulation_people where {people_where} and {residence_sql} = ?",
        (*people_params, settlement_id),
    )
    jobs = _top_jobs_for_where(con, world, f"{residence_sql} = ?", (settlement_id,), limit=3)
    return alive, ", ".join(f"{job} ({count})" for job, count in jobs)


def _settlement_summary_row(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> dict[str, object]:
    settlement_id = str(row["settlement_id"])
    alive, jobs = _settlement_alive_and_jobs(con, world, settlement_id)
    return {
        "Name": row["display_name"] or settlement_id,
        "Level": row["level"] or "",
        "Alive": alive,
        "Region": row["region_id"] or "",
        "Status": row["status"] or "",
        "Food": _fmt_number(row["food_pressure"]),
        "Stability": _fmt_number(row["stability"]),
        "Market": _fmt_number(row["market_pull"]),
        "Prosperity": _fmt_number(row["prosperity_pool"]),
        "Founded": row["founded_sim_year"] or "",
        "Top Jobs": jobs,
    }


def load_settlements_browser(
    world: str,
    search: str,
    status_filter: str,
    limit: object,
) -> tuple[gr.Dataframe, str, list[str]]:
    if not world:
        return _empty_settlements_frame(), "Choose a world.", []
    row_limit = _safe_int(limit, 50, 1, 250)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _empty_settlements_frame(), f"{path} is missing. Run a simulation first.", []

    with _connect_readonly(path) as con:
        if not _has_table(con, "simulation_settlements"):
            return _empty_settlements_frame(), "No simulation_settlements table found.", []
        saved_world = _resolve_saved_world(con, world)
        where_sql, params = _world_where(con, "simulation_settlements", saved_world)
        clauses = [where_sql]
        filter_bits: list[str] = []
        if status_filter == "Active":
            clauses.append("status = 'active'")
            filter_bits.append("active")
        elif status_filter == "Abandoned":
            clauses.append("status != 'active'")
            filter_bits.append("not active")
        if search:
            clauses.append(
                "(settlement_id like ? or region_id like ? or display_name like ? or level like ? or status like ?)"
            )
            params.extend([f"%{search}%"] * 5)
            filter_bits.append(f"search={search!r}")
        where_sql = " and ".join(clauses)
        rows = con.execute(
            f"""
            select *
            from simulation_settlements
            where {where_sql}
            order by status = 'active' desc, population_cap desc, display_name collate nocase
            limit ?
            """,
            (*params, row_limit),
        ).fetchall()
        total = _count_one(con, f"select count(*) from simulation_settlements where {where_sql}", params)
        values = [_settlement_summary_row(con, saved_world, row) for row in rows]
        settlement_ids = [str(row["settlement_id"]) for row in rows]

    filter_text = f" | filters: {', '.join(filter_bits)}" if filter_bits else ""
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {total} settlements{filter_text}{saved_world_note}. "
        "Click any settlement row to open its sheet."
    )
    return _dataframe(values, SETTLEMENT_BROWSER_HEADERS), status, settlement_ids


def _lookup_person(con: sqlite3.Connection, world: str, person_id: object) -> tuple[sqlite3.Row | None, dict[str, object]]:
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return None, {}
    if "world" in _table_columns(con, "simulation_people"):
        row = con.execute(
            "select * from simulation_people where world = ? and person_id = ?",
            (world, pid),
        ).fetchone()
    else:
        row = con.execute(
            "select * from simulation_people where person_id = ?",
            (pid,),
        ).fetchone()
    return row, _person_from_row(row, _trait_slots_for_world(world)) if row else {}


def _settlement_name(con: sqlite3.Connection, world: str, settlement_id: object) -> str:
    if not settlement_id:
        return ""
    if "world" in _table_columns(con, "simulation_settlements"):
        row = con.execute(
            "select display_name from simulation_settlements where world = ? and settlement_id = ?",
            (world, str(settlement_id)),
        ).fetchone()
    else:
        row = con.execute(
            "select display_name from simulation_settlements where settlement_id = ?",
            (str(settlement_id),),
        ).fetchone()
    return str(row["display_name"] or settlement_id) if row else str(settlement_id)


def _lookup_settlement(con: sqlite3.Connection, world: str, settlement_id: object) -> sqlite3.Row | None:
    sid = str(settlement_id or "").strip()
    if not sid or not _has_table(con, "simulation_settlements"):
        return None
    if "world" in _table_columns(con, "simulation_settlements"):
        return con.execute(
            "select * from simulation_settlements where world = ? and settlement_id = ?",
            (world, sid),
        ).fetchone()
    return con.execute(
        "select * from simulation_settlements where settlement_id = ?",
        (sid,),
    ).fetchone()


def render_settlement_outputs(world: str, settlement_id: object) -> str:
    sid = str(settlement_id or "").strip()
    if not world:
        return '<div class="place-sheet muted">Choose a world.</div>'
    if not sid:
        return '<div class="place-sheet muted">Browse settlements, then click a row to inspect it.</div>'
    path = _db_path(world, "Save DB")
    if not path.exists():
        return f'<div class="place-sheet muted">{html.escape(str(path))} is missing.</div>'
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        row = _lookup_settlement(con, saved_world, sid)
        if not row:
            return f'<div class="place-sheet muted">No settlement named {html.escape(sid)} in {html.escape(saved_world)}.</div>'
        alive, jobs = _settlement_alive_and_jobs(con, saved_world, sid)
        residents: list[str] = []
        if _has_table(con, "simulation_people"):
            residence_sql = _person_residence_sql(con)
            people_where, people_params = _alive_where(con, saved_world)
            career_sql = _person_career_fitness_sql(con)
            rows = con.execute(
                f"""
                select *
                from simulation_people
                where {people_where}
                  and {residence_sql} = ?
                order by coalesce({career_sql}, 0) desc, person_id asc
                limit 8
                """,
                (*people_params, sid),
            ).fetchall()
            trait_slots = _trait_slots_for_world(saved_world)
            for person_row in rows:
                person = _person_from_row(person_row, trait_slots)
                residents.append(f"{_person_name(person)} - {person.get('job') or 'unassigned'}")
        cards = "".join(
            [
                _detail_card("Alive", alive),
                _detail_card("Level", row["level"] or ""),
                _detail_card("Status", row["status"] or ""),
                _detail_card("Region", row["region_id"] or ""),
                _detail_card("Population Cap", row["population_cap"] or ""),
                _detail_card("Households", row["household_cap"] or ""),
                _detail_card("Food Pressure", _fmt_number(row["food_pressure"])),
                _detail_card("Stability", _fmt_number(row["stability"])),
                _detail_card("Market Pull", _fmt_number(row["market_pull"])),
                _detail_card("Prosperity", _fmt_number(row["prosperity_pool"])),
                _detail_card("Founded", row["founded_sim_year"] or "Unknown"),
            ]
        )
        name_bits = [row["etymology"], row["name_category_primary"], row["name_culture_primary"]]
        name_line = " | ".join(str(x) for x in name_bits if x)
        return (
            '<div class="place-sheet">'
            f'<h2>{html.escape(str(row["display_name"] or sid))}</h2>'
            f'<div class="place-subtitle">{html.escape(str(row["level"] or "settlement"))} in {html.escape(str(row["region_id"] or ""))}</div>'
            f'<div class="place-muted">{html.escape(name_line)}</div>'
            f'<div class="place-grid">{cards}</div>'
            '<div class="place-columns">'
            f'<section><h3>Top Jobs</h3>{_ul(jobs.split(", ") if jobs else [])}</section>'
            f'<section><h3>Notable Residents</h3>{_ul(residents)}</section>'
            '</div>'
            '</div>'
        )


def select_settlement_from_table(settlement_ids: object, world: str, evt: gr.SelectData) -> str:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        index = int(row_index)
        settlement_id = settlement_ids[index]  # type: ignore[index]
    except Exception:
        return '<div class="place-sheet muted">Click a settlement row to inspect it.</div>'
    return render_settlement_outputs(world, settlement_id)


def render_region_outputs(world: str, region_id: object) -> str:
    rid = str(region_id or "").strip()
    if not world:
        return '<div class="place-sheet muted">Choose a world.</div>'
    if not rid:
        return '<div class="place-sheet muted">Browse regions, then click a row to inspect it.</div>'
    path = _db_path(world, "Save DB")
    if not path.exists():
        return f'<div class="place-sheet muted">{html.escape(str(path))} is missing.</div>'
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        return _render_region_sheet(con, saved_world, rid)


def select_region_from_fresh_table(region_ids: object, world: str, evt: gr.SelectData) -> str:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        index = int(row_index)
        region_id = region_ids[index]  # type: ignore[index]
    except Exception:
        return '<div class="place-sheet muted">Click a region row to inspect it.</div>'
    return render_region_outputs(world, region_id)


def render_polity_outputs(world: str, polity_id: object) -> str:
    pid = str(polity_id or "").strip()
    if not world:
        return '<div class="place-sheet muted">Choose a world.</div>'
    if not pid:
        return '<div class="place-sheet muted">Browse polities, then click a row to inspect it.</div>'
    path = _db_path(world, "Save DB")
    if not path.exists():
        return f'<div class="place-sheet muted">{html.escape(str(path))} is missing.</div>'
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        return _render_polity_sheet(con, saved_world, pid)


def select_polity_from_fresh_table(polity_ids: object, world: str, evt: gr.SelectData) -> str:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        index = int(row_index)
        polity_id = polity_ids[index]  # type: ignore[index]
    except Exception:
        return '<div class="place-sheet muted">Click a polity row to inspect it.</div>'
    return render_polity_outputs(world, polity_id)


def _person_link_text(con: sqlite3.Connection, world: str, person_id: object) -> str:
    row, person = _lookup_person(con, world, person_id)
    if not row:
        return "Unknown"
    years = f"b. {person.get('birthyear', '?')}"
    if person.get("deathyear") is not None:
        years += f"-{person.get('deathyear')}"
    return f"{_person_name(person)} ({years})"


def _person_link_onclick(person_id: int) -> str:
    return html.escape(
        (
            "event.preventDefault();"
            "const input=document.querySelector('#person-open-id textarea,#person-open-id input');"
            "const button=document.querySelector('#person-open-button button,#person-open-button');"
            "if(input&&button){"
            f"const value='{int(person_id)}';"
            "const descriptor=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input),'value');"
            "if(descriptor&&descriptor.set){descriptor.set.call(input,value);}else{input.value=value;}"
            "input.dispatchEvent(new Event('input',{bubbles:true}));"
            "input.dispatchEvent(new Event('change',{bubbles:true}));"
            "button.click();"
            "}"
            "return false;"
        ),
        quote=True,
    )


def _person_link_html(con: sqlite3.Connection, world: str, person_id: object) -> str:
    row, person = _lookup_person(con, world, person_id)
    if not row:
        return "Unknown"
    name = html.escape(_person_name(person))
    years = f"b. {person.get('birthyear', '?')}"
    if person.get("deathyear") is not None:
        years += f"-{person.get('deathyear')}"
    label = f"{name} <span class=\"muted\">({html.escape(years)})</span>"
    return (
        f'<a href="#" class="person-link" '
        f'aria-label="Open person record for {name}" '
        f'onclick="{_person_link_onclick(int(row["person_id"]))}">{label}</a>'
    )


def _person_event_rows(con: sqlite3.Connection, world: str, person_id: object) -> list[sqlite3.Row]:
    events_has_world = "world" in _table_columns(con, "simulation_events")
    event_people_exists = _has_table(con, "simulation_event_people")
    world_clause = "where world = ? and" if events_has_world else "where"
    prefix_params: list[object] = [world] if events_has_world else []
    if event_people_exists:
        event_people_world_clause = "and e.world = ?" if events_has_world else ""
        event_people_params: list[object] = [person_id]
        if events_has_world:
            event_people_params.append(world)
        return con.execute(
            f"""
            select e.sim_year, e.event_type, e.payload_json
            from simulation_events e
            where exists (
                select 1
                from simulation_event_people ep
                where ep.event_id = e.id
                  and ep.person_id = ?
            )
            {event_people_world_clause}
            order by e.sim_year asc, e.id asc
            """,
            tuple(event_people_params),
        ).fetchall()
    return con.execute(
        f"""
        select sim_year, event_type, payload_json
        from simulation_events
        {world_clause} (
            json_extract(payload_json, '$.person_id') = ?
            or json_extract(payload_json, '$.person_a_id') = ?
            or json_extract(payload_json, '$.person_b_id') = ?
            or json_extract(payload_json, '$.child_id') = ?
            or json_extract(payload_json, '$.victim_person_id') = ?
            or json_extract(payload_json, '$.purseholder_person_id') = ?
            or json_extract(payload_json, '$.moved_person_id') = ?
            or json_extract(payload_json, '$.holder_person_id') = ?
            or json_extract(payload_json, '$.previous_holder_id') = ?
            or json_extract(payload_json, '$.prior_head_person_id') = ?
            or json_extract(payload_json, '$.claimant_id') = ?
            or exists (
                select 1 from json_each(payload_json, '$.household_member_ids')
                where json_each.value = ?
            )
            or exists (
                select 1 from json_each(payload_json, '$.dependent_minor_ids')
                where json_each.value = ?
            )
            or exists (
                select 1 from json_each(payload_json, '$.moved_person_ids')
                where json_each.value = ?
            )
          )
        order by sim_year asc, id asc
        """,
        (
            *prefix_params,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
            person_id,
        ),
    ).fetchall()


def _short_person(con: sqlite3.Connection, world: str, person_id: object) -> str:
    if person_id in (None, ""):
        return "unknown person"
    row, person = _lookup_person(con, world, person_id)
    if not row:
        return "unknown person"
    return _person_name(person)


def _short_person_for_event(
    con: sqlite3.Connection,
    world: str,
    person_id: object,
    focus_person_id: object,
) -> str:
    if person_id in (None, ""):
        return "unknown person"
    row, person = _lookup_person(con, world, person_id)
    if not row:
        return "unknown person"
    return _person_first_name(person) if _same_person_id(person_id, focus_person_id) else _person_name(person)


def _short_person_html(con: sqlite3.Connection, world: str, person_id: object) -> str:
    if person_id in (None, ""):
        return "unknown person"
    row, person = _lookup_person(con, world, person_id)
    if not row:
        return "unknown person"
    name = html.escape(_person_name(person))
    return (
        f'<a href="#" class="person-link" '
        f'aria-label="Open person record for {name}" '
        f'onclick="{_person_link_onclick(int(row["person_id"]))}">{name}</a>'
    )


def _short_person_html_for_event(
    con: sqlite3.Connection,
    world: str,
    person_id: object,
    focus_person_id: object,
) -> str:
    if person_id in (None, ""):
        return "unknown person"
    row, person = _lookup_person(con, world, person_id)
    if not row:
        return "unknown person"
    label = _person_first_name(person) if _same_person_id(person_id, focus_person_id) else _person_name(person)
    if _same_person_id(person_id, focus_person_id):
        return f"<strong>{html.escape(label)}</strong>"
    name = html.escape(label)
    full_name = html.escape(_person_name(person))
    return (
        f'<a href="#" class="person-link" '
        f'aria-label="Open person record for {full_name}" '
        f'onclick="{_person_link_onclick(int(row["person_id"]))}">{name}</a>'
    )


def _other_person_id(payload: dict[str, object], person_id: object) -> object:
    for key in ("person_a_id", "person_b_id"):
        candidate = payload.get(key)
        if candidate not in (None, person_id):
            return candidate
    return None


def _event_fitness_score(payload: dict[str, object]) -> float | None:
    """Historical career fitness recorded on this event payload."""
    value = payload.get("fitness_score")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _job_loss_nuance_parts(payload: dict[str, object]) -> list[str]:
    parts: list[str] = []
    reason = str(payload.get("reason") or "").strip()
    career = _event_float(payload, "career_fitness_score")
    job_fit = _event_float(payload, "fitness_score")
    trait_match = _event_float(payload, "job_trait_match_score")
    pressure = _event_float(payload, "resource_pressure")
    trait = str(payload.get("trait") or "").strip()
    band = str(payload.get("deviation_band") or "").strip().replace("_", " ")

    if reason == "job_market_churn":
        if job_fit is not None and career is not None and job_fit < career - 0.12:
            parts.append("the role was a poor fit despite decent general ability")
        elif job_fit is not None and job_fit >= 0.7:
            parts.append("the role fit them reasonably well, suggesting ordinary market turnover")
        elif trait_match is not None and trait_match < 0.45 and trait:
            parts.append(f"weak match on {trait}")
        else:
            parts.append("ordinary market turnover")
    elif reason == "low_fitness":
        if trait and trait_match is not None:
            parts.append(f"weak {trait} fit ({trait_match:.2f})")
        elif career is not None:
            parts.append(f"low broad work fitness ({career:.2f})")
    elif reason == "resource_scarcity":
        if pressure is not None:
            parts.append(f"local resource pressure {pressure:.2f}")
    elif reason == "mixed_pressure":
        if career is not None:
            parts.append(f"work fitness {career:.2f}")
        if pressure is not None:
            parts.append(f"resource pressure {pressure:.2f}")

    if trait and band and reason != "low_fitness":
        parts.append(f"job keyed to {trait} / {band}")
    if job_fit is not None:
        parts.append(f"job fit {job_fit:.2f}")
    elif career is not None:
        parts.append(f"career fitness {career:.2f}")
    return parts


def _event_float(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _person_list_text(
    con: sqlite3.Connection,
    world: str,
    person_ids: object,
    *,
    limit: int = 5,
    focus_person_id: object = None,
) -> str:
    if not isinstance(person_ids, list) or not person_ids:
        return "none"
    shown = ", ".join(
        _short_person_for_event(con, world, pid, focus_person_id)
        for pid in person_ids[:limit]
    )
    if len(person_ids) > limit:
        shown += f", and {len(person_ids) - limit} more"
    return shown


def _person_list_html(
    con: sqlite3.Connection,
    world: str,
    person_ids: object,
    *,
    limit: int = 5,
    focus_person_id: object = None,
) -> str:
    if not isinstance(person_ids, list) or not person_ids:
        return "none"
    shown = ", ".join(
        _short_person_html_for_event(con, world, pid, focus_person_id)
        for pid in person_ids[:limit]
    )
    if len(person_ids) > limit:
        shown += f", and {len(person_ids) - limit} more"
    return shown


def _event_sentence(con: sqlite3.Connection, world: str, event: sqlite3.Row, focus_person_id: object) -> str:
    payload = _load_json_object(event["payload_json"])
    event_type = str(event["event_type"] or payload.get("event_type") or "").strip()
    person = _short_person_for_event(con, world, payload.get("person_id") or focus_person_id, focus_person_id)
    event_label = event_type.replace("_", " ")

    if event_type in {"birth", "founder_created"}:
        child_id = payload.get("child_id") or payload.get("person_id")
        child = _short_person_for_event(con, world, child_id, focus_person_id)
        if event_type == "founder_created":
            return f"{child} entered the world as a founder."
        parent_a = _short_person_for_event(con, world, payload.get("person_a_id"), focus_person_id)
        parent_b = _short_person_for_event(con, world, payload.get("person_b_id"), focus_person_id)
        return f"{child} was born to {parent_a} and {parent_b}."

    if event_type in {"couple_formed", "couple_dissolved", "paramour_formed", "paramour_ended", "same_sex_couple_formed"}:
        a = _short_person_for_event(con, world, payload.get("person_a_id"), focus_person_id)
        b = _short_person_for_event(con, world, payload.get("person_b_id"), focus_person_id)
        verb = {
            "couple_formed": "formed a household partnership with",
            "couple_dissolved": "ended a household partnership with",
            "paramour_formed": "became paramours with",
            "paramour_ended": "ended a paramour relationship with",
            "same_sex_couple_formed": "formed a same-sex household partnership with",
        }[event_type]
        tail = ""
        if event_type == "couple_dissolved":
            reasons = payload.get("breakup_reasons") or []
            if isinstance(reasons, list) and reasons:
                tail = f" Reasons: {', '.join(str(r).replace('_', ' ') for r in reasons)}."
        elif event_type == "paramour_ended":
            reasons = payload.get("end_reasons") or []
            if isinstance(reasons, list) and reasons:
                tail = f" Reasons: {', '.join(str(r).replace('_', ' ') for r in reasons)}."
        elif event_type == "couple_formed" and payload.get("kinship_exception"):
            relation = str(payload.get("kinship_exception")).replace("_", " ")
            probability = _event_float(payload, "kinship_exception_probability")
            odds = f" at annual exception probability {probability:.6f}" if probability is not None else ""
            tail = f" Rare kinship exception: {relation}{odds}."
        return f"{a} {verb} {b}.{tail}"

    if event_type == "job_assigned":
        job = payload.get("job") or "a job"
        descriptor = payload.get("descriptor")
        fitness = _event_fitness_score(payload)
        bits = [f"{person} became {job}"]
        if descriptor:
            bits.append(f"matched through {descriptor}")
        if fitness is not None:
            bits.append(f"fitness {fitness:.2f}")
        previous = payload.get("previous_job")
        if previous:
            bits.append(f"previously {previous}")
        return "; ".join(bits) + "."

    if event_type == "job_lost":
        old_job = payload.get("old_job") or "their job"
        reason = str(payload.get("reason") or "unknown reason").replace("_", " ")
        nuance = _job_loss_nuance_parts(payload)
        tail = f" ({'; '.join(nuance)})" if nuance else ""
        return f"{person} lost {old_job}; {reason}{tail}."

    if event_type == "unemployment_started":
        reason = str(payload.get("reason") or "unknown reason").replace("_", " ")
        last_job = payload.get("last_job")
        last = f" after {last_job}" if last_job else ""
        return f"{person} became unemployed{last}; reason: {reason}."

    if event_type == "unemployment_ended":
        new_job = payload.get("new_job") or payload.get("job") or "work"
        years = payload.get("unemployment_years")
        span = f" after {years} unemployed year{'s' if years != 1 else ''}" if years is not None else ""
        return f"{person} found work as {new_job}{span}."

    if event_type in {"settlement_moved", "job_seeker_migration"}:
        from_place = _settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or "unknown")
        to_place = _settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or "unknown")
        reason = str(payload.get("move_reason") or event_type).replace("_", " ")
        moved_ids = payload.get("moved_person_ids")
        if isinstance(moved_ids, list) and len(moved_ids) > 1:
            moved = ", ".join(
                _short_person_for_event(con, world, pid, focus_person_id)
                for pid in moved_ids[:6]
            )
            if len(moved_ids) > 6:
                moved += f", and {len(moved_ids) - 6} more"
            return f"{moved} moved from {from_place} to {to_place}; reason: {reason}."
        return f"{person} moved from {from_place} to {to_place}; reason: {reason}."

    if event_type == "partner_residence_reconciled":
        moved = _short_person_for_event(con, world, payload.get("moved_person_id") or focus_person_id, focus_person_id)
        to_place = _settlement_name(con, world, payload.get("target_settlement_id")) or str(payload.get("target_settlement_id") or "unknown")
        return f"{moved} moved to {to_place} to reconcile partner residence."

    if event_type == "orphan_routed_to_largest_settlement":
        child = _short_person_for_event(con, world, payload.get("person_id") or focus_person_id, focus_person_id)
        from_place = _settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or "unknown")
        to_place = _settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or "unknown")
        return f"{child} was routed from {from_place} to {to_place} for local care."

    if event_type == "household_childcare_shortfall":
        minors = payload.get("dependent_minor_ids")
        minor_count = len(minors) if isinstance(minors, list) else int(payload.get("need") or 0)
        supply = _event_float(payload, "supply")
        shortfall = _event_float(payload, "shortfall")
        outcome = str(payload.get("outcome") or "unknown").replace("_", " ")
        victim = _short_person_for_event(con, world, payload.get("victim_person_id") or focus_person_id, focus_person_id)
        bits = [f"Household childcare shortfall affected {minor_count} dependent minor{'s' if minor_count != 1 else ''}"]
        if supply is not None:
            bits.append(f"care supply {supply:.2f}")
        if shortfall is not None:
            bits.append(f"shortfall {shortfall:.2f}")
        bits.append(f"outcome {outcome}")
        bits.append(f"victim {victim}")
        return "; ".join(bits) + "."

    if event_type == "household_prosperity_crisis":
        members = _person_list_text(
            con,
            world,
            payload.get("household_member_ids"),
            focus_person_id=focus_person_id,
        )
        before = _event_float(payload, "prosperity_before")
        after = _event_float(payload, "prosperity_after")
        purseholder = _short_person_for_event(con, world, payload.get("purseholder_person_id") or focus_person_id, focus_person_id)
        bits = [f"{purseholder}'s household entered prosperity crisis"]
        if before is not None and after is not None:
            bits.append(f"savings {before:.2f} -> {after:.2f}")
        bits.append(f"members: {members}")
        return "; ".join(bits) + "."

    if event_type == "office_succession":
        holder = _short_person_for_event(con, world, payload.get("holder_person_id") or focus_person_id, focus_person_id)
        previous = _short_person_for_event(con, world, payload.get("previous_holder_id"), focus_person_id)
        title = payload.get("title_id") or "office"
        via = str(payload.get("via") or "succession").replace("_", " ")
        return f"{holder} succeeded {previous} to {title} by {via}."

    if event_type == "career_fitness_updated":
        fitness = _event_fitness_score(payload)
        near = payload.get("near_perfect_traits") or []
        high = payload.get("high_deviation_traits") or []
        parts = [f"{person}'s career fitness was updated"]
        if fitness is not None:
            parts.append(f"score {fitness:.2f}")
        if near:
            parts.append(f"strengths near ideal: {', '.join(str(x) for x in near)}")
        if high:
            parts.append(f"high-deviation traits: {', '.join(str(x) for x in high)}")
        return "; ".join(parts) + "."

    if event_type == "death":
        return f"{person} died."

    details = payload.get("details")
    if details:
        return str(details)
    other_id = _other_person_id(payload, focus_person_id)
    if other_id is not None:
        return f"{event_label}: {person} and {_short_person_for_event(con, world, other_id, focus_person_id)}."
    return f"{event_label}: {person}."


def _event_sentence_html(con: sqlite3.Connection, world: str, event: sqlite3.Row, focus_person_id: object) -> str:
    payload = _load_json_object(event["payload_json"])
    event_type = str(event["event_type"] or payload.get("event_type") or "").strip()
    person = _short_person_html_for_event(con, world, payload.get("person_id") or focus_person_id, focus_person_id)
    event_label = html.escape(event_type.replace("_", " "))

    if event_type in {"birth", "founder_created"}:
        child_id = payload.get("child_id") or payload.get("person_id")
        child = _short_person_html_for_event(con, world, child_id, focus_person_id)
        if event_type == "founder_created":
            return f"{child} entered the world as a founder."
        parent_a = _short_person_html_for_event(con, world, payload.get("person_a_id"), focus_person_id)
        parent_b = _short_person_html_for_event(con, world, payload.get("person_b_id"), focus_person_id)
        return f"{child} was born to {parent_a} and {parent_b}."

    if event_type in {"couple_formed", "couple_dissolved", "paramour_formed", "paramour_ended", "same_sex_couple_formed"}:
        a = _short_person_html_for_event(con, world, payload.get("person_a_id"), focus_person_id)
        b = _short_person_html_for_event(con, world, payload.get("person_b_id"), focus_person_id)
        verb = {
            "couple_formed": "formed a household partnership with",
            "couple_dissolved": "ended a household partnership with",
            "paramour_formed": "became paramours with",
            "paramour_ended": "ended a paramour relationship with",
            "same_sex_couple_formed": "formed a same-sex household partnership with",
        }[event_type]
        tail = ""
        if event_type == "couple_dissolved":
            reasons = payload.get("breakup_reasons") or []
            if isinstance(reasons, list) and reasons:
                shown = html.escape(", ".join(str(r).replace("_", " ") for r in reasons))
                tail = f" Reasons: {shown}."
        elif event_type == "paramour_ended":
            reasons = payload.get("end_reasons") or []
            if isinstance(reasons, list) and reasons:
                shown = html.escape(", ".join(str(r).replace("_", " ") for r in reasons))
                tail = f" Reasons: {shown}."
        elif event_type == "couple_formed" and payload.get("kinship_exception"):
            relation = html.escape(str(payload.get("kinship_exception")).replace("_", " "))
            probability = _event_float(payload, "kinship_exception_probability")
            odds = f" at annual exception probability {probability:.6f}" if probability is not None else ""
            tail = f" Rare kinship exception: {relation}{odds}."
        return f"{a} {verb} {b}.{tail}"

    if event_type == "job_assigned":
        job = html.escape(str(payload.get("job") or "a job"))
        descriptor = payload.get("descriptor")
        fitness = _event_fitness_score(payload)
        bits = [f"{person} became {job}"]
        if descriptor:
            bits.append(f"matched through {html.escape(str(descriptor))}")
        if fitness is not None:
            bits.append(f"fitness {fitness:.2f}")
        previous = payload.get("previous_job")
        if previous:
            bits.append(f"previously {html.escape(str(previous))}")
        return "; ".join(bits) + "."

    if event_type == "job_lost":
        old_job = html.escape(str(payload.get("old_job") or "their job"))
        reason = html.escape(str(payload.get("reason") or "unknown reason").replace("_", " "))
        nuance = [html.escape(part) for part in _job_loss_nuance_parts(payload)]
        tail = f" ({'; '.join(nuance)})" if nuance else ""
        return f"{person} lost {old_job}; {reason}{tail}."

    if event_type == "unemployment_started":
        reason = html.escape(str(payload.get("reason") or "unknown reason").replace("_", " "))
        last_job = payload.get("last_job")
        last = f" after {html.escape(str(last_job))}" if last_job else ""
        return f"{person} became unemployed{last}; reason: {reason}."

    if event_type == "unemployment_ended":
        new_job = html.escape(str(payload.get("new_job") or payload.get("job") or "work"))
        years = payload.get("unemployment_years")
        span = f" after {years} unemployed year{'s' if years != 1 else ''}" if years is not None else ""
        return f"{person} found work as {new_job}{span}."

    if event_type in {"settlement_moved", "job_seeker_migration"}:
        from_place = html.escape(_settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or "unknown"))
        to_place = html.escape(_settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or "unknown"))
        reason = html.escape(str(payload.get("move_reason") or event_type).replace("_", " "))
        moved_ids = payload.get("moved_person_ids")
        if isinstance(moved_ids, list) and len(moved_ids) > 1:
            moved = ", ".join(
                _short_person_html_for_event(con, world, pid, focus_person_id)
                for pid in moved_ids[:6]
            )
            if len(moved_ids) > 6:
                moved += f", and {len(moved_ids) - 6} more"
            return f"{moved} moved from {from_place} to {to_place}; reason: {reason}."
        return f"{person} moved from {from_place} to {to_place}; reason: {reason}."

    if event_type == "partner_residence_reconciled":
        moved = _short_person_html_for_event(con, world, payload.get("moved_person_id") or focus_person_id, focus_person_id)
        to_place = html.escape(_settlement_name(con, world, payload.get("target_settlement_id")) or str(payload.get("target_settlement_id") or "unknown"))
        return f"{moved} moved to {to_place} to reconcile partner residence."

    if event_type == "orphan_routed_to_largest_settlement":
        child = _short_person_html_for_event(con, world, payload.get("person_id") or focus_person_id, focus_person_id)
        from_place = html.escape(_settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or "unknown"))
        to_place = html.escape(_settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or "unknown"))
        return f"{child} was routed from {from_place} to {to_place} for local care."

    if event_type == "household_childcare_shortfall":
        minors = payload.get("dependent_minor_ids")
        minor_count = len(minors) if isinstance(minors, list) else int(payload.get("need") or 0)
        supply = _event_float(payload, "supply")
        shortfall = _event_float(payload, "shortfall")
        outcome = html.escape(str(payload.get("outcome") or "unknown").replace("_", " "))
        victim = _short_person_html_for_event(con, world, payload.get("victim_person_id") or focus_person_id, focus_person_id)
        bits = [f"Household childcare shortfall affected {minor_count} dependent minor{'s' if minor_count != 1 else ''}"]
        if supply is not None:
            bits.append(f"care supply {supply:.2f}")
        if shortfall is not None:
            bits.append(f"shortfall {shortfall:.2f}")
        bits.append(f"outcome {outcome}")
        bits.append(f"victim {victim}")
        return "; ".join(bits) + "."

    if event_type == "household_prosperity_crisis":
        members = _person_list_html(
            con,
            world,
            payload.get("household_member_ids"),
            focus_person_id=focus_person_id,
        )
        before = _event_float(payload, "prosperity_before")
        after = _event_float(payload, "prosperity_after")
        purseholder = _short_person_html_for_event(con, world, payload.get("purseholder_person_id") or focus_person_id, focus_person_id)
        bits = [f"{purseholder}'s household entered prosperity crisis"]
        if before is not None and after is not None:
            bits.append(f"savings {before:.2f} -&gt; {after:.2f}")
        bits.append(f"members: {members}")
        return "; ".join(bits) + "."

    if event_type == "office_succession":
        holder = _short_person_html_for_event(con, world, payload.get("holder_person_id") or focus_person_id, focus_person_id)
        previous = _short_person_html_for_event(con, world, payload.get("previous_holder_id"), focus_person_id)
        title = html.escape(str(payload.get("title_id") or "office"))
        via = html.escape(str(payload.get("via") or "succession").replace("_", " "))
        return f"{holder} succeeded {previous} to {title} by {via}."

    if event_type == "career_fitness_updated":
        fitness = _event_fitness_score(payload)
        near = payload.get("near_perfect_traits") or []
        high = payload.get("high_deviation_traits") or []
        parts = [f"{person}'s career fitness was updated"]
        if fitness is not None:
            parts.append(f"score {fitness:.2f}")
        if near:
            parts.append(f"strengths near ideal: {html.escape(', '.join(str(x) for x in near))}")
        if high:
            parts.append(f"high-deviation traits: {html.escape(', '.join(str(x) for x in high))}")
        return "; ".join(parts) + "."

    if event_type == "death":
        return f"{person} died."

    details = payload.get("details")
    if details:
        return html.escape(str(details))
    other_id = _other_person_id(payload, focus_person_id)
    if other_id is not None:
        return f"{event_label}: {person} and {_short_person_html_for_event(con, world, other_id, focus_person_id)}."
    return f"{event_label}: {person}."


def _genome_labels(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        str(row["trait"]): row
        for row in con.execute("select * from cfg.genome")
        if row["trait"]
    }


def _trait_phrase(
    trait: str,
    value: float,
    labels: dict[str, sqlite3.Row],
    *,
    prefer_optimal: bool = False,
    soften_typical: bool = False,
) -> str:
    row = labels.get(trait)
    if not row:
        return ""
    if prefer_optimal or abs(value) <= 15:
        return str(row["optimal description"] or row["optimal centerpoint"] or "")
    if value < 0:
        phrase = str(row["deficient description"] or row["deficient deviation"] or "")
    else:
        phrase = str(row["excess description"] or row["excess deviation"] or "")
    if soften_typical and 40 <= abs(value) <= 60 and phrase:
        stripped = phrase.lstrip()
        if not stripped.lower().startswith("slightly "):
            return f"slightly {stripped}"
    return phrase


def _trait_accessibility_label(trait: str, value: float, phrase: str) -> str:
    distance = abs(value)
    if distance <= 15:
        band = "near the ideal center"
    elif distance <= 45:
        band = "moderate deviation from ideal"
    else:
        band = "strong deviation from ideal"
    side = "negative side" if value < 0 else "positive side" if value > 0 else "center"
    phrase_part = f", interpreted as {phrase}" if phrase else ""
    return f"{trait}: {value:+.1f}, {band} on the {side}{phrase_part}."


def _trait_display_values(
    trait: str, current_traits: object, base_traits: object
) -> tuple[float | None, float | None, str]:
    current_raw = current_traits.get(trait) if isinstance(current_traits, dict) else None
    base_raw = base_traits.get(trait) if isinstance(base_traits, dict) else None
    try:
        current = float(current_raw)
    except (TypeError, ValueError):
        current = None
    try:
        base = float(base_raw)
    except (TypeError, ValueError):
        base = None
    if current is None:
        current = base
    if current is None:
        return None, base, "Unknown"
    shown = f"{current:+.1f}"
    if base is not None and abs(current - base) >= 0.05:
        shown += f'<br><span class="trait-base-value">base {base:+.1f}</span>'
    return current, base, shown


def _render_detail_card(label: str, value: object) -> str:
    shown = html.escape(str(value if value not in (None, "") else "Unknown"))
    return f'<div class="detail-card"><div class="detail-label">{html.escape(label)}</div><div class="detail-value">{shown}</div></div>'


def _render_detail_card_html(label: str, value: str) -> str:
    shown = value if value not in (None, "") else "Unknown"
    return f'<div class="detail-card"><div class="detail-label">{html.escape(label)}</div><div class="detail-value">{shown}</div></div>'


def _legacy_indices_module():
    global _LEGACY_INDICES_MODULE
    if _LEGACY_INDICES_MODULE is not None:
        return _LEGACY_INDICES_MODULE
    path = PROJECT_ROOT / "library" / "legacy_indices.py"
    spec = importlib.util.spec_from_file_location("_history_project_legacy_indices", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _LEGACY_INDICES_MODULE = module
    return module


def _render_legacy_scores(traits: object) -> str:
    if not isinstance(traits, dict) or not traits:
        return '<div class="muted">No legacy index data recorded.</div>'
    try:
        rows = _legacy_indices_module().top_legacy_index_scores(traits, limit=6)
    except Exception as exc:
        return f'<div class="muted">Could not calculate legacy indexes: {html.escape(str(exc))}</div>'
    cards: list[str] = []
    for row in rows:
        score = max(0.0, min(1.0, float(row.score)))
        cards.append(
            '<div class="legacy-score">'
            '<div class="legacy-score-head">'
            f'<span>{html.escape(str(row.label))}</span>'
            f'<span>{score:.2f}</span>'
            '</div>'
            f'<div class="legacy-score-desc">{html.escape(str(row.description))}</div>'
            '<div class="legacy-track" aria-hidden="true">'
            f'<div class="legacy-fill" style="width: {score * 100:.1f}%"></div>'
            '</div>'
            '</div>'
        )
    return f'<div class="legacy-grid">{"".join(cards)}</div>'


def _format_01_score(value: object) -> str:
    if value in (None, ""):
        return "Unknown"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "Unknown"


def _render_person_sheet(con: sqlite3.Connection, world: str, row: sqlite3.Row, person: dict[str, object]) -> str:
    current_year = _current_year(con, world)
    name = html.escape(_person_name(person))
    birthyear = person.get("birthyear")
    deathyear = person.get("deathyear")
    end_year = deathyear if deathyear is not None else current_year
    age = int(end_year) - int(birthyear) if birthyear is not None and end_year is not None else "Unknown"
    life = "Alive" if row["is_alive"] else "Dead"
    years = f"{birthyear or '?'}"
    years += f" - {deathyear}" if deathyear is not None else f" - {current_year or '?'}"
    birthplace = person.get("birthplace") or _settlement_name(con, world, person.get("birthplace_settlement_id"))
    current_place = _settlement_name(con, world, person.get("current_settlement_id")) or "None"
    father = _person_link_text(con, world, row["father_id"]) if row["father_id"] else "Unknown"
    mother = _person_link_text(con, world, row["mother_id"]) if row["mother_id"] else "Unknown"
    partner = _person_link_text(con, world, person.get("partner_person_id")) if person.get("partner_person_id") else "None"
    paramour = _person_link_text(con, world, person.get("paramour_person_id")) if person.get("paramour_person_id") else "None"
    father_html = _person_link_html(con, world, row["father_id"]) if row["father_id"] else "Unknown"
    mother_html = _person_link_html(con, world, row["mother_id"]) if row["mother_id"] else "Unknown"
    partner_html = _person_link_html(con, world, person.get("partner_person_id")) if person.get("partner_person_id") else "None"
    paramour_html = _person_link_html(con, world, person.get("paramour_person_id")) if person.get("paramour_person_id") else "None"
    people_has_world = "world" in _table_columns(con, "simulation_people")
    children = con.execute(
        f"""
        select *
        from simulation_people
        where {'world = ? and ' if people_has_world else ''}(father_id = ? or mother_id = ?)
        order by person_id
        limit 12
        """,
        (
            *([world] if people_has_world else []),
            row["person_id"],
            row["person_id"],
        ),
    ).fetchall()
    child_items = [
        f'<div class="relation">{_person_link_html(con, world, child["person_id"])}</div>'
        for child in children
    ]
    if not child_items:
        child_items = ['<div class="relation muted">No recorded children</div>']

    events = _person_event_rows(con, world, row["person_id"])
    event_items: list[str] = []
    for event in events:
        sentence = _event_sentence_html(con, world, event, row["person_id"])
        event_items.append(
            f'<div class="relation"><strong>{html.escape(str(event["sim_year"]))}</strong> '
            f'{html.escape(str(event["event_type"]).replace("_", " "))}<br>'
            f'<span class="muted">{sentence}</span></div>'
        )
    if not event_items:
        event_items = ['<div class="relation muted">No matching events found</div>']

    labels = _genome_labels(con)
    base_genome = person.get("genome") or {}
    current_genome = person.get("mind_body") or base_genome
    legacy_scores_html = _render_legacy_scores(current_genome)
    trait_rows: list[str] = []
    if isinstance(current_genome, dict) or isinstance(base_genome, dict):
        traits = sorted(
            set(current_genome if isinstance(current_genome, dict) else {})
            | set(base_genome if isinstance(base_genome, dict) else {})
        )
        display_rows = [
            (trait, *_trait_display_values(str(trait), current_genome, base_genome))
            for trait in traits
        ]
        for trait, value, base_value, shown_value in sorted(
            display_rows,
            key=lambda item: -abs(float(item[1] if item[1] is not None else item[2] or 0)),
        ):
            if value is None:
                continue
            pos = max(0, min(100, (value + 100) / 2))
            phrase = _trait_phrase(str(trait), value, labels, soften_typical=True)
            aria_label = _trait_accessibility_label(str(trait), value, phrase)
            if base_value is not None and abs(value - base_value) >= 0.05:
                aria_label = f"{aria_label} Base genome value was {base_value:+.1f}."
            trait_rows.append(
                '<div class="trait-row" role="group" '
                f'aria-label="{html.escape(aria_label)}">'
                f'<div class="trait-name" title="{html.escape(phrase)}">{html.escape(str(trait))}</div>'
                f'<div class="trait-value">{shown_value}</div>'
                f'<div class="trait-track" role="meter" aria-label="{html.escape(aria_label)}" '
                'aria-valuemin="-100" aria-valuemax="100" '
                f'aria-valuenow="{value:.1f}" aria-valuetext="{html.escape(aria_label)}">'
                f'<div class="trait-marker" style="left: calc({pos:.1f}% - 2px);" aria-hidden="true"></div>'
                '</div>'
                '</div>'
            )
    if not trait_rows:
        trait_rows.append('<div class="muted">No genome data recorded.</div>')

    phrases = list(person.get("genome_trait_phrases") or [])
    composites = list(person.get("genome_composite_names") or [])
    identity_cards = [
        _render_detail_card("Record ID", row["person_id"]),
        _render_detail_card("Life", f"{life}, age {age}"),
        _render_detail_card("Years", years),
        _render_detail_card("Culture", person.get("ethnic")),
        _render_detail_card("Species", person.get("species")),
        _render_detail_card("Gender", person.get("gender")),
        _render_detail_card("Mind", person.get("gender_mind")),
        _render_detail_card("Home", current_place),
        _render_detail_card("Birthplace", birthplace),
        _render_detail_card_html("Partner", partner_html),
        _render_detail_card_html("Paramour", paramour_html),
        _render_detail_card("Appearance", f"{person.get('skin_tone', '?')} skin, {person.get('hair', '?')} hair, {person.get('eyes', '?')} eyes"),
        _render_detail_card("Build", f"{float(person.get('maturity_height_cm') or 0):.0f} cm, {float(person.get('maturity_weight_kg') or 0):.0f} kg"),
        _render_detail_card("Attractiveness", _format_01_score(person.get("attractiveness_01"))),
        _render_detail_card("Work", person.get("job") or person.get("employment_status") or "None"),
    ]
    pill_html = "".join(f'<span class="pill">{html.escape(str(item))}</span>' for item in [*phrases, *composites])
    if not pill_html:
        pill_html = '<span class="muted">No standout tags recorded.</span>'

    sheet_label = f"Person sheet for {_person_name(person)}, person {row['person_id']}"
    return f"""
    <article class="person-sheet" role="region" aria-label="{html.escape(sheet_label)}">
      <div class="person-title">
        <h2 id="person-{row['person_id']}-title">{name}</h2>
        <span class="badge">#{row['person_id']} · {life}</span>
      </div>
      <section aria-labelledby="person-{row['person_id']}-identity">
        <h3 id="person-{row['person_id']}-identity" class="section-title">Identity</h3>
        <div class="detail-grid">{''.join(identity_cards)}</div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-tags">
        <h3 id="person-{row['person_id']}-tags" class="section-title">Character Tags</h3>
        <div class="pill-list" aria-label="Character tags">{pill_html}</div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-legacy">
        <h3 id="person-{row['person_id']}-legacy" class="section-title">Legacy Indexes</h3>
        {legacy_scores_html}
      </section>
      <section aria-labelledby="person-{row['person_id']}-genome">
        <h3 id="person-{row['person_id']}-genome" class="section-title">Genome</h3>
        <p class="sr-only">Genome bars use green at zero for the ideal center and red at both extremes for high deviation.</p>
        <div class="trait-grid">{''.join(trait_rows)}</div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-family">
        <h3 id="person-{row['person_id']}-family" class="section-title">Family</h3>
        <div class="relation-list">
        <div class="relation"><strong>Father</strong><br>{father_html}</div>
        <div class="relation"><strong>Mother</strong><br>{mother_html}</div>
        <div class="relation"><strong>Partner</strong><br>{partner_html}</div>
        <div class="relation"><strong>Paramour</strong><br>{paramour_html}</div>
        </div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-children">
        <h3 id="person-{row['person_id']}-children" class="section-title">Children</h3>
        <div class="relation-list">{''.join(child_items)}</div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-events">
        <h3 id="person-{row['person_id']}-events" class="section-title">Events</h3>
        <div class="relation-list">{''.join(event_items)}</div>
      </section>
    </article>
    """


def _render_person_share_text(con: sqlite3.Connection, world: str, row: sqlite3.Row, person: dict[str, object]) -> str:
    current_year = _current_year(con, world)
    birthyear = person.get("birthyear")
    deathyear = person.get("deathyear")
    end_year = deathyear if deathyear is not None else current_year
    age = int(end_year) - int(birthyear) if birthyear is not None and end_year is not None else "unknown"
    life = "alive" if row["is_alive"] else "dead"
    current_place = _settlement_name(con, world, person.get("current_settlement_id")) or "no current settlement"
    birthplace = person.get("birthplace") or _settlement_name(con, world, person.get("birthplace_settlement_id")) or "unknown"
    father = _person_link_text(con, world, row["father_id"]) if row["father_id"] else "unknown"
    mother = _person_link_text(con, world, row["mother_id"]) if row["mother_id"] else "unknown"
    partner = _person_link_text(con, world, person.get("partner_person_id")) if person.get("partner_person_id") else "none"
    paramour = _person_link_text(con, world, person.get("paramour_person_id")) if person.get("paramour_person_id") else "none"

    labels = _genome_labels(con)
    genome = person.get("mind_body") or person.get("genome") or {}
    ideal_trait_lines: list[str] = []
    deviation_trait_lines: list[str] = []
    if isinstance(genome, dict):
        sorted_traits = sorted(genome.items(), key=lambda item: abs(float(item[1])))
        for trait, raw_value in sorted_traits[:8]:
            value = float(raw_value)
            phrase = _trait_phrase(str(trait), value, labels, prefer_optimal=True)
            ideal_trait_lines.append(f"- {_trait_accessibility_label(str(trait), value, phrase)}")
        for trait, raw_value in reversed(sorted_traits[-8:]):
            value = float(raw_value)
            phrase = _trait_phrase(str(trait), value, labels)
            deviation_trait_lines.append(f"- {_trait_accessibility_label(str(trait), value, phrase)}")
    if not ideal_trait_lines:
        ideal_trait_lines.append("- No genome data recorded.")
    if not deviation_trait_lines:
        deviation_trait_lines.append("- No genome data recorded.")

    people_has_world = "world" in _table_columns(con, "simulation_people")
    children = con.execute(
        f"""
        select *
        from simulation_people
        where {'world = ? and ' if people_has_world else ''}(father_id = ? or mother_id = ?)
        order by person_id
        limit 12
        """,
        (
            *([world] if people_has_world else []),
            row["person_id"],
            row["person_id"],
        ),
    ).fetchall()
    child_lines = [
        f"- {_person_name(_person_from_row(child, _trait_slots_for_world(world)))}"
        for child in children
    ] or ["- No recorded children."]

    events = _person_event_rows(con, world, row["person_id"])
    event_lines: list[str] = []
    for event in events:
        event_lines.append(f"- {event['sim_year']}: {_event_sentence(con, world, event, row['person_id'])}")
    if not event_lines:
        event_lines.append("- No matching events found.")

    tags = [*list(person.get("genome_trait_phrases") or []), *list(person.get("genome_composite_names") or [])]
    tags_text = ", ".join(str(tag) for tag in tags) if tags else "No standout tags recorded."
    years = f"born {birthyear or 'unknown'}"
    years += f", died {deathyear}" if deathyear is not None else f", current year {current_year or 'unknown'}"

    return "\n".join(
        [
            _person_name(person),
            f"Record ID: {row['person_id']}",
            "",
            f"Status: {life}, age {age}; {years}.",
            f"Culture and species: {person.get('ethnic') or 'unknown'} {person.get('species') or 'unknown'}.",
            f"Gender: {person.get('gender') or 'unknown'}; gender mind: {person.get('gender_mind') or 'unknown'}.",
            f"Home: {current_place}. Birthplace: {birthplace}.",
            (
                "Appearance: "
                f"{person.get('skin_tone') or 'unknown'} skin, "
                f"{person.get('hair') or 'unknown'} hair, "
                f"{person.get('eyes') or 'unknown'} eyes; "
                f"{float(person.get('maturity_height_cm') or 0):.0f} cm, "
                f"{float(person.get('maturity_weight_kg') or 0):.0f} kg."
            ),
            f"Work: {person.get('job') or person.get('employment_status') or 'none'}.",
            f"Character tags: {tags_text}",
            "",
            "Family:",
            f"- Father: {father}",
            f"- Mother: {mother}",
            f"- Partner: {partner}",
            f"- Paramour: {paramour}",
            "",
            "Children:",
            *child_lines,
            "",
            "Genome highlights:",
            "Values are signed deviations from ideal. Zero is the ideal center; traits close to zero are exceptional strengths, while large positive or negative values are stronger deviations.",
            "Closest to ideal:",
            *ideal_trait_lines,
            "Strongest deviations:",
            *deviation_trait_lines,
            "",
            "Events:",
            *event_lines,
        ]
    )


def render_person_from_id(world: str, person_id: object) -> str:
    if not world:
        return '<div class="person-sheet muted" role="status">Choose a world.</div>'
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return '<div class="person-sheet muted" role="status">Click a person row to open their sheet.</div>'
    path = _db_path(world, "Save DB")
    if not path.exists():
        return f'<div class="person-sheet muted" role="status">{html.escape(str(path))} is missing.</div>'
    with _connect_readonly(path) as save_con:
        save_con.row_factory = sqlite3.Row
        save_con.execute("attach database ? as cfg", (str(_db_path(world, "Config DB")),))
        saved_world = _resolve_saved_world(save_con, world)
        row, person = _lookup_person(save_con, saved_world, pid)
        if not row:
            return f'<div class="person-sheet muted" role="status">No person #{html.escape(str(pid))} in {html.escape(world)}.</div>'
        return _render_person_sheet(save_con, saved_world, row, person)


def render_person_share_text(world: str, person_id: object) -> str:
    if not world:
        return "Choose a world."
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return "Click a person row to generate share text."
    path = _db_path(world, "Save DB")
    if not path.exists():
        return f"{path} is missing."
    with _connect_readonly(path) as save_con:
        save_con.row_factory = sqlite3.Row
        save_con.execute("attach database ? as cfg", (str(_db_path(world, "Config DB")),))
        saved_world = _resolve_saved_world(save_con, world)
        row, person = _lookup_person(save_con, saved_world, pid)
        if not row:
            return f"No person #{pid} in {world}."
        return _render_person_share_text(save_con, saved_world, row, person)


def render_person_outputs(world: str, person_id: object) -> tuple[str, str]:
    return render_person_from_id(world, person_id), render_person_share_text(world, person_id)


def select_person_from_table(person_ids: list[int], world: str, evt: gr.SelectData) -> tuple[str, str]:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        person_id = person_ids[int(row_index)]
    except Exception:
        return (
            '<div class="person-sheet muted" role="status">Click a person row to open their sheet.</div>',
            "Click a person row to generate share text.",
        )
    return render_person_outputs(world, person_id)


def _fmt_number(value: object, digits: int = 2) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.{digits}f}"


def _saved_table_has_world(con: sqlite3.Connection, table: str) -> bool:
    return _has_table(con, table) and "world" in _table_columns(con, table)


def _world_where(con: sqlite3.Connection, table: str, world: str) -> tuple[str, list[object]]:
    if _saved_table_has_world(con, table):
        return "world = ?", [world]
    return "1 = 1", []


def _alive_where(con: sqlite3.Connection, world: str) -> tuple[str, list[object]]:
    where, params = _world_where(con, "simulation_people", world)
    return f"{where} and is_alive = 1", params


def _person_expr(con: sqlite3.Connection, column: str, json_key: str | None = None) -> str:
    columns = _table_columns(con, "simulation_people")
    if column in columns:
        return column
    key = json_key or column
    return f"json_extract(person_json, '$.{key}')"


def _person_residence_sql(con: sqlite3.Connection) -> str:
    columns = _table_columns(con, "simulation_people")
    if "current_settlement_id" in columns and "birthplace_settlement_id" in columns:
        return "coalesce(nullif(current_settlement_id, ''), nullif(birthplace_settlement_id, ''))"
    if "current_settlement_id" in columns:
        return "nullif(current_settlement_id, '')"
    if "birthplace_settlement_id" in columns:
        return "nullif(birthplace_settlement_id, '')"
    return (
        "coalesce("
        "nullif(json_extract(person_json, '$.current_settlement_id'), ''), "
        "nullif(json_extract(person_json, '$.birthplace_settlement_id'), '')"
        ")"
    )


def _person_birth_region_sql(con: sqlite3.Connection) -> str:
    return _person_expr(con, "birthplace_region_id")


def _person_job_sql(con: sqlite3.Connection) -> str:
    return _person_expr(con, "job")


def _person_career_fitness_sql(con: sqlite3.Connection) -> str:
    return _person_expr(con, "career_fitness_score")


def _count_one(con: sqlite3.Connection, sql: str, params: Iterable[object] = ()) -> int:
    row = con.execute(sql, tuple(params)).fetchone()
    return int(row[0] or 0) if row else 0


def _open_territory_clause() -> str:
    return "until_sim_year is null"


def _polity_names_for_region(con: sqlite3.Connection, region_id: str) -> str:
    if not (_has_table(con, "simulation_polities") and _has_table(con, "simulation_polity_territory")):
        return ""
    rows = con.execute(
        """
        select distinct p.name
        from simulation_polities p
        join simulation_polity_territory t on t.polity_id = p.polity_id
        left join simulation_settlements s
          on t.target_kind = 'settlement' and s.settlement_id = t.target_id
        where t.until_sim_year is null
          and p.status = 'active'
          and (
            (t.target_kind = 'region' and t.target_id = ?)
            or (t.target_kind = 'settlement' and s.region_id = ?)
          )
        order by p.name
        """,
        (region_id, region_id),
    ).fetchall()
    return ", ".join(str(r["name"] or "").strip() for r in rows if str(r["name"] or "").strip())


def _polity_names_for_settlement(con: sqlite3.Connection, settlement_id: str, region_id: str) -> str:
    if not (_has_table(con, "simulation_polities") and _has_table(con, "simulation_polity_territory")):
        return ""
    rows = con.execute(
        """
        select distinct p.name
        from simulation_polities p
        join simulation_polity_territory t on t.polity_id = p.polity_id
        where t.until_sim_year is null
          and p.status = 'active'
          and (
            (t.target_kind = 'settlement' and t.target_id = ?)
            or (t.target_kind = 'region' and t.target_id = ?)
          )
        order by p.name
        """,
        (settlement_id, region_id),
    ).fetchall()
    return ", ".join(str(r["name"] or "").strip() for r in rows if str(r["name"] or "").strip())


def _top_jobs_for_where(
    con: sqlite3.Connection,
    world: str,
    where_extra: str,
    extra_params: Iterable[object],
    *,
    limit: int = 5,
) -> list[tuple[str, int]]:
    people_where, params = _alive_where(con, world)
    job_sql = _person_job_sql(con)
    rows = con.execute(
        f"""
        select coalesce(nullif({job_sql}, ''), 'Unassigned') as job_name, count(*) as n
        from simulation_people
        where {people_where}
          and {where_extra}
        group by job_name
        order by n desc, job_name collate nocase
        limit ?
        """,
        (*params, *tuple(extra_params), int(limit)),
    ).fetchall()
    return [(str(r["job_name"]), int(r["n"] or 0)) for r in rows]


def _alive_counts_and_top_jobs_by_place(
    con: sqlite3.Connection,
    world: str,
    place_sql: str,
    place_ids: Iterable[str],
    *,
    limit: int = 3,
) -> tuple[dict[str, int], dict[str, list[tuple[str, int]]]]:
    ids = [str(place_id) for place_id in place_ids if str(place_id).strip()]
    if not ids or not _has_table(con, "simulation_people"):
        return {}, {}
    placeholders = ", ".join("?" for _ in ids)
    people_where, people_params = _alive_where(con, world)
    job_sql = _person_job_sql(con)
    rows = con.execute(
        f"""
        select
          {place_sql} as place_id,
          coalesce(nullif({job_sql}, ''), 'Unassigned') as job_name,
          count(*) as n
        from simulation_people
        where {people_where}
          and {place_sql} in ({placeholders})
        group by place_id, job_name
        order by place_id, n desc, job_name collate nocase
        """,
        (*people_params, *ids),
    ).fetchall()
    alive_counts: dict[str, int] = {place_id: 0 for place_id in ids}
    top_jobs: dict[str, list[tuple[str, int]]] = {place_id: [] for place_id in ids}
    for row in rows:
        place_id = str(row["place_id"] or "")
        count = int(row["n"] or 0)
        if not place_id:
            continue
        alive_counts[place_id] = alive_counts.get(place_id, 0) + count
        if len(top_jobs.setdefault(place_id, [])) < limit:
            top_jobs[place_id].append((str(row["job_name"]), count))
    return alive_counts, top_jobs


def _active_settlement_counts_by_region(
    con: sqlite3.Connection,
    world: str,
    region_ids: Iterable[str],
) -> dict[str, int]:
    ids = [str(region_id) for region_id in region_ids if str(region_id).strip()]
    if not ids or not _has_table(con, "simulation_settlements"):
        return {}
    placeholders = ", ".join("?" for _ in ids)
    world_where, world_params = _world_where(con, "simulation_settlements", world)
    rows = con.execute(
        f"""
        select region_id, count(*) as n
        from simulation_settlements
        where {world_where}
          and region_id in ({placeholders})
          and status = 'active'
        group by region_id
        """,
        (*world_params, *ids),
    ).fetchall()
    return {str(row["region_id"]): int(row["n"] or 0) for row in rows}


def _row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def _snapshot_table_rows(con: sqlite3.Connection, table: str, world: str) -> list[dict[str, object]]:
    if not _has_table(con, table):
        return []
    where, params = _world_where(con, table, world)
    rows = con.execute(
        f"select * from {_quote_identifier(table)} where {where}",
        tuple(params),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _load_place_snapshot(con: sqlite3.Connection, world: str) -> dict[str, object]:
    started = time.perf_counter()
    people: list[dict[str, object]] = []
    if _has_table(con, "simulation_people"):
        where, params = _alive_where(con, world)
        people = [
            _row_to_dict(row)
            for row in con.execute(
                f"select * from simulation_people where {where}",
                tuple(params),
            ).fetchall()
        ]
    regions = _snapshot_table_rows(con, "simulation_regions", world)
    settlements = _snapshot_table_rows(con, "simulation_settlements", world)
    polities = _snapshot_table_rows(con, "simulation_polities", world)
    snapshot = {
        "world": world,
        "people": people,
        "regions": regions,
        "settlements": settlements,
        "polities": polities,
        "territory": _snapshot_table_rows(con, "simulation_polity_territory", world),
        "seats": _snapshot_table_rows(con, "simulation_office_seats", world),
    }
    _log_info(
        "place_snapshot_loaded world=%s people=%s regions=%s settlements=%s polities=%s territory=%s seats=%s elapsed=%.4fs",
        world,
        len(people),
        len(regions),
        len(settlements),
        len(polities),
        len(_snapshot_rows(snapshot, "territory")),
        len(_snapshot_rows(snapshot, "seats")),
        time.perf_counter() - started,
    )
    return snapshot


def _snapshot_rows(snapshot: dict[str, object], name: str) -> list[dict[str, object]]:
    rows = snapshot.get(name)
    return rows if isinstance(rows, list) else []


def _snapshot_map(snapshot: dict[str, object], name: str, key: str) -> dict[str, dict[str, object]]:
    return {str(row.get(key)): row for row in _snapshot_rows(snapshot, name) if row.get(key) not in (None, "")}


def _snapshot_person(snapshot: dict[str, object], person_id: object) -> dict[str, object]:
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return {}
    for row in _snapshot_rows(snapshot, "people"):
        try:
            if int(row.get("person_id")) == pid:
                return _person_from_mapping(row, _trait_slots_for_world(str(snapshot.get("world") or "")))
        except (TypeError, ValueError):
            continue
    return {}


def _snapshot_person_link_text(snapshot: dict[str, object], person_id: object) -> str:
    person = _snapshot_person(snapshot, person_id)
    if not person:
        return "Unknown"
    years = f"b. {person.get('birthyear', '?')}"
    if person.get("deathyear") is not None:
        years += f"-{person.get('deathyear')}"
    return f"{_person_name(person)} ({years})"


def _snapshot_settlement_name(snapshot: dict[str, object], settlement_id: object) -> str:
    if not settlement_id:
        return ""
    row = _snapshot_map(snapshot, "settlements", "settlement_id").get(str(settlement_id))
    if not row:
        return str(settlement_id)
    return str(row.get("display_name") or settlement_id)


def _snapshot_person_job(person: dict[str, object]) -> str:
    return str(person.get("job") or "Unassigned")


def _top_jobs_from_people(people: Iterable[dict[str, object]], limit: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for person in people:
        job = _snapshot_person_job(person)
        counts[job] = counts.get(job, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]


def _person_birth_region(person: dict[str, object]) -> str:
    return str(person.get("birthplace_region_id") or "")


def _person_residence(person: dict[str, object]) -> str:
    return str(person.get("current_settlement_id") or person.get("birthplace_settlement_id") or "")


def _snapshot_alive_people(snapshot: dict[str, object]) -> list[dict[str, object]]:
    slots = _trait_slots_for_world(str(snapshot.get("world") or ""))
    return [_person_from_mapping(row, slots) for row in _snapshot_rows(snapshot, "people")]


def _snapshot_people_by_region(snapshot: dict[str, object], region_id: str) -> list[dict[str, object]]:
    return [person for person in _snapshot_alive_people(snapshot) if _person_birth_region(person) == region_id]


def _snapshot_people_by_settlement(snapshot: dict[str, object], settlement_id: str) -> list[dict[str, object]]:
    return [person for person in _snapshot_alive_people(snapshot) if _person_residence(person) == settlement_id]


def _snapshot_region_settlements(snapshot: dict[str, object], region_id: str) -> list[dict[str, object]]:
    rows = [row for row in _snapshot_rows(snapshot, "settlements") if str(row.get("region_id") or "") == region_id]
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("status") or "") != "active",
            -_safe_int(row.get("population_cap"), 0),
            str(row.get("display_name") or "").lower(),
        ),
    )


def _snapshot_open_territory(snapshot: dict[str, object], polity_id: object | None = None) -> list[dict[str, object]]:
    rows = [row for row in _snapshot_rows(snapshot, "territory") if row.get("until_sim_year") is None]
    if polity_id is not None:
        rows = [row for row in rows if str(row.get("polity_id")) == str(polity_id)]
    return rows


def _snapshot_polity_names_for_region(snapshot: dict[str, object], region_id: str) -> str:
    polities = _snapshot_map(snapshot, "polities", "polity_id")
    settlements = _snapshot_map(snapshot, "settlements", "settlement_id")
    names: set[str] = set()
    for terr in _snapshot_open_territory(snapshot):
        polity = polities.get(str(terr.get("polity_id")))
        if not polity or polity.get("status") != "active":
            continue
        target_kind = str(terr.get("target_kind") or "")
        target_id = str(terr.get("target_id") or "")
        if target_kind == "region" and target_id == region_id:
            names.add(str(polity.get("name") or "").strip())
        elif target_kind == "settlement" and str(settlements.get(target_id, {}).get("region_id") or "") == region_id:
            names.add(str(polity.get("name") or "").strip())
    return ", ".join(sorted(name for name in names if name))


def _snapshot_polity_names_for_settlement(snapshot: dict[str, object], settlement_id: str, region_id: str) -> str:
    polities = _snapshot_map(snapshot, "polities", "polity_id")
    names: set[str] = set()
    for terr in _snapshot_open_territory(snapshot):
        polity = polities.get(str(terr.get("polity_id")))
        if not polity or polity.get("status") != "active":
            continue
        target_kind = str(terr.get("target_kind") or "")
        target_id = str(terr.get("target_id") or "")
        if (target_kind == "settlement" and target_id == settlement_id) or (
            target_kind == "region" and target_id == region_id
        ):
            names.add(str(polity.get("name") or "").strip())
    return ", ".join(sorted(name for name in names if name))


def _snapshot_region_map_html(
    snapshot: dict[str, object],
    region_id: str,
    *,
    focus_settlement_id: str | None = None,
) -> str:
    settlements = _snapshot_region_settlements(snapshot, region_id)
    geo: dict[str, object] = {}
    for row in settlements:
        geo = _load_local_geography(row.get("local_geography_json"))
        if geo:
            break
    return _render_local_map(geo, settlements, focus_settlement_id=focus_settlement_id)


def _snapshot_notable_people(people: Iterable[dict[str, object]], limit: int = 8) -> list[str]:
    ranked = sorted(
        people,
        key=lambda person: (-float(person.get("career_fitness_score") or 0.0), int(person.get("person_id") or 0)),
    )
    return [f"{_person_name(person)} — {person.get('job') or 'unassigned'}" for person in ranked[:limit]]


def _top_people_for_where(
    con: sqlite3.Connection,
    world: str,
    where_extra: str,
    extra_params: Iterable[object],
    *,
    limit: int = 8,
) -> list[sqlite3.Row]:
    people_where, params = _alive_where(con, world)
    career_sql = _person_career_fitness_sql(con)
    return con.execute(
        f"""
        select *
        from simulation_people
        where {people_where}
          and {where_extra}
        order by coalesce({career_sql}, 0) desc, person_id
        limit ?
        """,
        (*params, *tuple(extra_params), int(limit)),
    ).fetchall()


def _ul(items: Iterable[str], empty: str = "None yet.") -> str:
    clean = [str(item).strip() for item in items if str(item).strip()]
    if not clean:
        return f'<p class="place-muted">{html.escape(empty)}</p>'
    return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in clean) + "</ul>"


def _detail_card(label: str, value: object) -> str:
    return (
        '<div class="place-card">'
        f'<span class="label">{html.escape(label)}</span>'
        f'<span class="value">{html.escape(str(value if value not in (None, "") else "—"))}</span>'
        "</div>"
    )


def _load_local_geography(raw: object) -> dict[str, object]:
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _feature_color(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in {"coast", "coastline", "river", "river_boundary", "stream", "ford", "lake", "marsh", "bog", "bay", "harbor", "spring", "wadi", "fishery"}:
        return "var(--place-map-water)"
    if k in {"forest", "forest_boundary", "grove", "clearing", "meadow", "pasture", "orchard", "hill", "ridge", "ridge_boundary", "mountain", "pass", "cliff", "mesa", "landmark", "sacred", "engineering"}:
        return "var(--place-map-feature)"
    return "var(--place-map-feature)"


def _render_local_map(
    geography: dict[str, object],
    settlements: Iterable[sqlite3.Row],
    *,
    focus_settlement_id: str | None = None,
) -> str:
    features = geography.get("features")
    sites = geography.get("settlements")
    borders = geography.get("borders")
    if not isinstance(features, list) or not isinstance(sites, list):
        return '<p class="place-muted">No local geography map recorded for this place yet.</p>'
    site_by_slot: dict[int, dict[str, object]] = {}
    for site in sites:
        if isinstance(site, dict):
            try:
                site_by_slot[int(site.get("settlement_slot", 0)) + 1] = site
            except (TypeError, ValueError):
                continue
    parts = [
        '<svg class="place-map" viewBox="0 0 100 100" role="img" aria-label="Local geography map">',
        '<rect x="0" y="0" width="100" height="100" fill="var(--place-map-bg)" />',
        '<rect x="3" y="3" width="94" height="94" rx="6" fill="var(--place-map-land)" opacity=".45" />',
    ]
    if isinstance(borders, list):
        for border in borders:
            if not isinstance(border, dict):
                continue
            points = border.get("points")
            if not isinstance(points, list) or len(points) < 2:
                continue
            xy: list[str] = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    x = max(1.5, min(98.5, float(point[0]) * 100.0))
                    y = max(1.5, min(98.5, float(point[1]) * 100.0))
                except (TypeError, ValueError):
                    continue
                xy.append(f"{x:.1f},{y:.1f}")
            if len(xy) < 2:
                continue
            kind = str(border.get("kind") or "boundary")
            parts.append(
                f'<polyline points="{" ".join(xy)}" fill="none" '
                f'stroke="{_feature_color(kind)}" stroke-width="1.2" opacity=".65" '
                'stroke-linecap="round" stroke-linejoin="round" />'
            )
    for feat in features:
        if not isinstance(feat, dict):
            continue
        try:
            x = max(4.0, min(96.0, float(feat.get("x", 0.5)) * 100.0))
            y = max(4.0, min(96.0, float(feat.get("y", 0.5)) * 100.0))
        except (TypeError, ValueError):
            continue
        kind = str(feat.get("kind") or "feature")
        color = _feature_color(kind)
        if kind.lower() in {"river", "stream", "coast", "bay", "harbor", "wadi", "fishery"}:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}" opacity=".75" />')
        else:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="{color}" opacity=".8" />')
        parts.append(
            f'<text x="{x + 2.5:.1f}" y="{y - 2.5:.1f}" font-size="3" fill="var(--place-muted)">'
            f'{html.escape(kind[:12])}</text>'
        )
    for row in settlements:
        slot = int(row["site_slot"] or 1) if "site_slot" in row.keys() else 1
        site = site_by_slot.get(slot)
        if not site:
            continue
        try:
            x = max(4.0, min(96.0, float(site.get("x", 0.5)) * 100.0))
            y = max(4.0, min(96.0, float(site.get("y", 0.5)) * 100.0))
        except (TypeError, ValueError):
            continue
        sid = str(row["settlement_id"] or "")
        label = str(row["display_name"] or sid)
        focused = sid == (focus_settlement_id or "")
        radius = 4.4 if focused else 3.4
        stroke = "var(--place-text)" if focused else "var(--place-map-bg)"
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" '
            f'fill="var(--place-map-town)" stroke="{stroke}" stroke-width="1.1" />'
        )
        parts.append(
            f'<text x="{x + 3.6:.1f}" y="{y + 1.2:.1f}" font-size="3.4" '
            f'fill="var(--place-text)">{html.escape(label[:18])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _region_settlements(con: sqlite3.Connection, region_id: str) -> list[sqlite3.Row]:
    if not _has_table(con, "simulation_settlements"):
        return []
    return con.execute(
        """
        select *
        from simulation_settlements
        where region_id = ?
        order by status = 'active' desc, population_cap desc, display_name collate nocase
        """,
        (region_id,),
    ).fetchall()


def _region_map_html(con: sqlite3.Connection, region_id: str, *, focus_settlement_id: str | None = None) -> str:
    settlements = _region_settlements(con, region_id)
    geo: dict[str, object] = {}
    for row in settlements:
        geo = _load_local_geography(row["local_geography_json"] if "local_geography_json" in row.keys() else None)
        if geo:
            break
    return _render_local_map(geo, settlements, focus_settlement_id=focus_settlement_id)


PLACE_REGION_HEADERS = [
    "Name",
    "Alive",
    "Settlements",
    "Food",
    "Stability",
    "Market",
    "Prosperity",
    "Treasury",
    "Polities",
    "Top Jobs",
]
PLACE_TOWN_HEADERS = [
    "Name",
    "Level",
    "Alive",
    "Region",
    "Status",
    "Food",
    "Stability",
    "Prosperity",
    "Polity",
    "Top Jobs",
]
PLACE_POLITY_HEADERS = [
    "Name",
    "Type",
    "Status",
    "Territory",
    "Seats",
    "Holders",
    "Parent",
    "Capital",
    "Founded",
]


_PLACE_KEY_SEP = "\x1f"


def _encode_place_key(path_world: str, saved_world: str, item_id: object) -> str:
    return _PLACE_KEY_SEP.join(
        [
            (path_world or "").strip(),
            (saved_world or "").strip(),
            str(item_id or "").strip(),
        ]
    )


def _decode_place_key(current_world: str, key: object) -> tuple[str, str | None, str]:
    raw = str(key or "")
    parts = raw.split(_PLACE_KEY_SEP)
    if len(parts) == 3:
        path_world, saved_world, item_id = parts
        return path_world or current_world, saved_world or None, item_id
    return current_world, None, raw


def _place_headers(view: str) -> list[str]:
    selected = (view or "Regions").strip()
    if selected == "Towns":
        return PLACE_TOWN_HEADERS
    if selected == "Polities":
        return PLACE_POLITY_HEADERS
    return PLACE_REGION_HEADERS


def load_places_browser(
    world: str,
    view: str,
    search: str,
    limit: object,
) -> tuple[gr.Dataframe, str, list[str]]:
    values, headers, status, keys, selected = _places_browser_data(world, view, search, limit)
    return (
        _dataframe(values, headers, key=f"places-{selected.lower()}-{len(headers)}"),
        status,
        keys,
    )


def _load_place_view(view: str, world: str, search: str, limit: object) -> tuple[gr.Dataframe, str, list[str]]:
    values, headers, status, keys, selected = _places_browser_data(world, view, search, limit)
    return (
        _dataframe(values, headers, key=f"places-{selected.lower()}-{len(headers)}"),
        status,
        keys,
    )


def load_regions_browser(world: str, search: str, limit: object) -> tuple[gr.Dataframe, str, list[str]]:
    return _load_place_view("Regions", world, search, limit)


def load_towns_browser(world: str, search: str, limit: object) -> tuple[gr.Dataframe, str, list[str]]:
    return _load_place_view("Towns", world, search, limit)


def load_polities_browser(world: str, search: str, limit: object) -> tuple[gr.Dataframe, str, list[str]]:
    return _load_place_view("Polities", world, search, limit)


def _empty_place_sheet(view: str) -> str:
    selected = (view or "Places").strip().lower()
    return f'<div class="place-sheet muted">Browse {html.escape(selected)}, then click a row to inspect it.</div>'


def _place_row_click_onclick(key: str) -> str:
    value = json.dumps(str(key))
    return (
        "event.preventDefault();"
        "const input=document.querySelector('#place-open-key textarea,#place-open-key input');"
        "const button=document.querySelector('#place-open-button button,#place-open-button');"
        "if(input&&button){"
        f"const value={value};"
        "const descriptor=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input),'value');"
        "if(descriptor&&descriptor.set){descriptor.set.call(input,value);}else{input.value=value;}"
        "input.dispatchEvent(new Event('input',{bubbles:true}));"
        "input.dispatchEvent(new Event('change',{bubbles:true}));"
        "button.click();"
        "}"
        "return false;"
    )


def _places_table_html(values: list[dict[str, object]], headers: list[str], keys: list[str], selected: str) -> str:
    if not values:
        return (
            '<div class="place-sheet">'
            f'<p class="place-muted">No {html.escape(selected.lower())} matched the current filters.</p>'
            "</div>"
        )
    header_html = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    row_html: list[str] = []
    for row, key in zip(values, keys):
        cells = "".join(f"<td>{html.escape(str(row.get(header, '')))}</td>" for header in headers)
        onclick = html.escape(_place_row_click_onclick(key), quote=True)
        row_html.append(f'<tr data-place-row="1" onclick="{onclick}">{cells}</tr>')
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(selected)}</h2>'
        '<div class="place-muted">Click a row to inspect it.</div>'
        '<table class="places-browser-table">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{''.join(row_html)}</tbody>"
        "</table>"
        "</div>"
    )


def load_places_html_browser(world: str, view: str, search: str, limit: object) -> tuple[str, str, str]:
    started = time.perf_counter()
    _log_info(
        "places_html_load_start view=%r world=%r search_len=%s limit=%r",
        view,
        world,
        len(search or ""),
        limit,
    )
    try:
        values, headers, status, keys, selected = _places_browser_data(world, view, search, limit)
        table_html = _places_table_html(values, headers, keys, selected)
        _log_info(
            "places_html_load_done selected=%s rows=%s headers=%s keys=%s html_bytes=%s elapsed=%.4fs status=%r",
            selected,
            len(values),
            len(headers),
            len(keys),
            len(table_html),
            time.perf_counter() - started,
            status,
        )
        return table_html, status, _empty_place_sheet(selected.lower())
    except Exception:
        _log_exception("places_html_load_error view=%r world=%r limit=%r", view, world, limit)
        raise


def load_regions_browser_with_detail_reset(world: str, search: str, limit: object) -> tuple[list[list[object]], str, list[str], str]:
    return _load_place_view_with_detail_reset("Regions", world, search, limit)


def load_towns_browser_with_detail_reset(world: str, search: str, limit: object) -> tuple[list[list[object]], str, list[str], str]:
    return _load_place_view_with_detail_reset("Towns", world, search, limit)


def load_polities_browser_with_detail_reset(world: str, search: str, limit: object) -> tuple[list[list[object]], str, list[str], str]:
    return _load_place_view_with_detail_reset("Polities", world, search, limit)


def _load_place_view_with_detail_reset(view: str, world: str, search: str, limit: object) -> tuple[list[list[object]], str, list[str], str]:
    started = time.perf_counter()
    _log_info(
        "place_load_start view=%s world=%r search_len=%s limit=%r",
        view,
        world,
        len(search or ""),
        limit,
    )
    try:
        values, headers, status, keys, selected = _places_browser_data(world, view, search, limit)
        table_values = _table_values(values, headers)
        elapsed = time.perf_counter() - started
        _log_info(
            "place_load_done view=%s selected=%s rows=%s headers=%s keys=%s elapsed=%.4fs status=%r",
            view,
            selected,
            len(values),
            len(headers),
            len(keys),
            elapsed,
            status,
        )
        return table_values, status, keys, _empty_place_sheet(selected.lower())
    except Exception:
        _log_exception("place_load_error view=%s world=%r limit=%r", view, world, limit)
        raise


def _places_browser_data(
    world: str,
    view: str,
    search: str,
    limit: object,
) -> tuple[list[dict[str, object]], list[str], str, list[str], str]:
    selected = (view or "Regions").strip()
    headers = _place_headers(selected)
    if not world:
        return [], headers, "Choose a world.", [], selected
    row_limit = _safe_int(limit, 50, 1, 250)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return [], headers, f"{path} is missing. Run a simulation first.", [], selected
    values: list[dict[str, object]] = []
    keys: list[str] = []
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        needle = f"%{search.strip()}%" if search else ""
        if selected == "Towns":
            if not _has_table(con, "simulation_settlements"):
                return [], headers, "No simulation_settlements table found.", [], selected
            world_where, params = _world_where(con, "simulation_settlements", saved_world)
            clauses = [world_where]
            if needle:
                clauses.append(
                    "(settlement_id like ? or region_id like ? or display_name like ? or level like ? or status like ?)"
                )
                params.extend([needle] * 5)
            rows = con.execute(
                f"""
                select *
                from simulation_settlements
                where {' and '.join(clauses)}
                order by status = 'active' desc, population_cap desc, display_name collate nocase
                limit ?
                """,
                (*params, row_limit),
            ).fetchall()
            settlement_ids = [str(row["settlement_id"]) for row in rows]
            residence_sql = _person_residence_sql(con)
            alive_counts, top_jobs_by_settlement = _alive_counts_and_top_jobs_by_place(
                con,
                saved_world,
                residence_sql,
                settlement_ids,
                limit=3,
            )
            for row in rows:
                sid = str(row["settlement_id"])
                rid = str(row["region_id"])
                alive = alive_counts.get(sid, 0)
                jobs = ", ".join(f"{job} ({n})" for job, n in top_jobs_by_settlement.get(sid, []))
                values.append(
                    {
                        "Name": row["display_name"] or sid,
                        "Level": row["level"] or "",
                        "Alive": alive,
                        "Region": rid,
                        "Status": row["status"] or "",
                        "Food": _fmt_number(row["food_pressure"]),
                        "Stability": _fmt_number(row["stability"]),
                        "Prosperity": _fmt_number(row["prosperity_pool"]),
                        "Polity": _polity_names_for_settlement(con, sid, rid),
                        "Top Jobs": jobs,
                    }
                )
                keys.append(_encode_place_key(world, saved_world, sid))
        elif selected == "Polities":
            if not _has_table(con, "simulation_polities"):
                return [], headers, "No simulation_polities table found.", [], selected
            world_where, params = _world_where(con, "simulation_polities", saved_world)
            clauses = [world_where]
            if needle:
                clauses.append("(cast(polity_id as text) like ? or name like ? or polity_type_id like ? or status like ?)")
                params.extend([needle] * 4)
            rows = con.execute(
                f"""
                select *
                from simulation_polities
                where {' and '.join(clauses)}
                order by status = 'active' desc, polity_id
                limit ?
                """,
                (*params, row_limit),
            ).fetchall()
            for row in rows:
                pid = int(row["polity_id"])
                terr = _count_one(
                    con,
                    "select count(*) from simulation_polity_territory where polity_id = ? and until_sim_year is null",
                    (pid,),
                ) if _has_table(con, "simulation_polity_territory") else 0
                seats = _count_one(
                    con,
                    "select count(*) from simulation_office_seats where polity_id = ? and status = 'active'",
                    (pid,),
                ) if _has_table(con, "simulation_office_seats") else 0
                holders = _count_one(
                    con,
                    "select count(*) from simulation_office_seats where polity_id = ? and status = 'active' and holder_person_id is not null",
                    (pid,),
                ) if _has_table(con, "simulation_office_seats") else 0
                values.append(
                    {
                        "Name": row["name"] or f"Polity {pid}",
                        "Type": row["polity_type_id"] or "",
                        "Status": row["status"] or "",
                        "Territory": terr,
                        "Seats": seats,
                        "Holders": holders,
                        "Parent": row["parent_polity_id"] or "",
                        "Capital": _settlement_name(con, saved_world, row["capital_settlement_id"]),
                        "Founded": row["founded_sim_year"] or "",
                    }
                )
                keys.append(_encode_place_key(world, saved_world, pid))
        else:
            if not _has_table(con, "simulation_regions"):
                return [], headers, "No simulation_regions table found.", [], selected
            world_where, params = _world_where(con, "simulation_regions", saved_world)
            clauses = [world_where]
            if needle:
                clauses.append("(region_id like ? or region_display_name like ?)")
                params.extend([needle] * 2)
            rows = con.execute(
                f"""
                select *
                from simulation_regions
                where {' and '.join(clauses)}
                order by total_population_cap desc, region_display_name collate nocase
                limit ?
                """,
                (*params, row_limit),
            ).fetchall()
            region_ids = [str(row["region_id"]) for row in rows]
            birth_region_sql = _person_birth_region_sql(con)
            alive_counts, top_jobs_by_region = _alive_counts_and_top_jobs_by_place(
                con,
                saved_world,
                birth_region_sql,
                region_ids,
                limit=3,
            )
            active_settlement_counts = _active_settlement_counts_by_region(con, saved_world, region_ids)
            for row in rows:
                rid = str(row["region_id"])
                alive = alive_counts.get(rid, 0)
                active_settlements = active_settlement_counts.get(rid, 0)
                jobs = ", ".join(f"{job} ({n})" for job, n in top_jobs_by_region.get(rid, []))
                values.append(
                    {
                        "Name": row["region_display_name"] or rid,
                        "Alive": alive,
                        "Settlements": active_settlements,
                        "Food": _fmt_number(row["food_pressure"]),
                        "Stability": _fmt_number(row["stability"]),
                        "Market": _fmt_number(row["market_pull"]),
                        "Prosperity": _fmt_number(row["prosperity_pool"]),
                        "Treasury": _fmt_number(row["treasury_balance"]),
                        "Polities": _polity_names_for_region(con, rid),
                        "Top Jobs": jobs,
                    }
                )
                keys.append(_encode_place_key(world, saved_world, rid))
    headers = list(values[0].keys()) if values else _place_headers(selected)
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = f"{path.name}: showing {len(values)} {selected.lower()}{saved_world_note}. Click a row for details."
    return values, headers, status, keys, selected


def _empty_place_state_json(view: str = "Places") -> str:
    return json.dumps({"view": view, "keys": [], "details": {}})


def _render_region_sheet_from_snapshot(snapshot: dict[str, object], region_id: str) -> str:
    row = _snapshot_map(snapshot, "regions", "region_id").get(region_id)
    if not row:
        return f'<div class="place-sheet muted">No region named {html.escape(region_id)}.</div>'
    people = _snapshot_people_by_region(snapshot, region_id)
    settlements = _snapshot_region_settlements(snapshot, region_id)
    jobs = [f"{job}: {n}" for job, n in _top_jobs_from_people(people, 8)]
    residents = _snapshot_notable_people(people, 8)
    settlement_items = [
        (
            f"{s.get('display_name') or s.get('settlement_id')} "
            f"({s.get('level')}, {s.get('status')}, pop {s.get('population_cap')})"
        )
        for s in settlements[:12]
    ]
    cards = "".join(
        [
            _detail_card("Alive", len(people)),
            _detail_card("Settlements", len(settlements)),
            _detail_card("Food Pressure", _fmt_number(row.get("food_pressure"))),
            _detail_card("Stability", _fmt_number(row.get("stability"))),
            _detail_card("Market Pull", _fmt_number(row.get("market_pull"))),
            _detail_card("Prosperity", _fmt_number(row.get("prosperity_pool"))),
            _detail_card("Treasury", _fmt_number(row.get("treasury_balance"))),
            _detail_card("Polities", _snapshot_polity_names_for_region(snapshot, region_id) or "None"),
        ]
    )
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row.get("region_display_name") or region_id))}</h2>'
        f'<div class="place-subtitle">Region {html.escape(region_id)}</div>'
        f'<div class="place-grid">{cards}</div>'
        f'{_snapshot_region_map_html(snapshot, region_id)}'
        '<div class="place-columns">'
        f'<section><h3>Settlements</h3>{_ul(settlement_items)}</section>'
        f'<section><h3>Top Jobs</h3>{_ul(jobs)}</section>'
        f'<section><h3>Notable Residents</h3>{_ul(residents)}</section>'
        '</div>'
        '</div>'
    )


def _render_town_sheet_from_snapshot(snapshot: dict[str, object], settlement_id: str) -> str:
    row = _snapshot_map(snapshot, "settlements", "settlement_id").get(settlement_id)
    if not row:
        if not _snapshot_rows(snapshot, "settlements"):
            return '<div class="place-sheet muted">No towns are recorded in the current save yet. Browse Towns after the simulation creates one.</div>'
        return (
            '<div class="place-sheet muted">'
            f'Town {html.escape(settlement_id)} is no longer in the current save. '
            'Browse Towns to refresh the list.'
            '</div>'
        )
    sid = str(row.get("settlement_id") or settlement_id)
    rid = str(row.get("region_id") or "")
    people = _snapshot_people_by_settlement(snapshot, sid)
    jobs = [f"{job}: {n}" for job, n in _top_jobs_from_people(people, 8)]
    residents = _snapshot_notable_people(people, 8)
    cards = "".join(
        [
            _detail_card("Alive", len(people)),
            _detail_card("Level", row.get("level") or ""),
            _detail_card("Status", row.get("status") or ""),
            _detail_card("Region", rid),
            _detail_card("Food Pressure", _fmt_number(row.get("food_pressure"))),
            _detail_card("Stability", _fmt_number(row.get("stability"))),
            _detail_card("Market Pull", _fmt_number(row.get("market_pull"))),
            _detail_card("Prosperity", _fmt_number(row.get("prosperity_pool"))),
            _detail_card("Polity", _snapshot_polity_names_for_settlement(snapshot, sid, rid) or "None"),
            _detail_card("Founded", row.get("founded_sim_year") or "Unknown"),
        ]
    )
    name_bits = [row.get("etymology"), row.get("name_category_primary"), row.get("name_culture_primary")]
    name_line = " | ".join(str(x) for x in name_bits if x)
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row.get("display_name") or sid))}</h2>'
        f'<div class="place-subtitle">{html.escape(str(row.get("level") or "settlement"))} in {html.escape(rid)}</div>'
        f'<div class="place-muted">{html.escape(name_line)}</div>'
        f'<div class="place-grid">{cards}</div>'
        f'{_snapshot_region_map_html(snapshot, rid, focus_settlement_id=sid)}'
        '<div class="place-columns">'
        f'<section><h3>Top Jobs</h3>{_ul(jobs)}</section>'
        f'<section><h3>Notable Residents</h3>{_ul(residents)}</section>'
        '</div>'
        '</div>'
    )


def _render_polity_sheet_from_snapshot(snapshot: dict[str, object], polity_id: str) -> str:
    try:
        pid = int(polity_id)
    except (TypeError, ValueError):
        return '<div class="place-sheet muted">Choose a polity row to inspect it.</div>'
    row = _snapshot_map(snapshot, "polities", "polity_id").get(str(pid))
    if not row:
        if not _snapshot_rows(snapshot, "polities"):
            return '<div class="place-sheet muted">No polities are recorded in the current save yet. Browse Polities after the simulation forms one.</div>'
        return f'<div class="place-sheet muted">Polity #{pid} is no longer in the current save. Browse Polities to refresh the list.</div>'
    territories = sorted(
        _snapshot_open_territory(snapshot, pid),
        key=lambda terr: (str(terr.get("target_kind") or ""), str(terr.get("target_id") or "")),
    )
    seats = sorted(
        [
            seat
            for seat in _snapshot_rows(snapshot, "seats")
            if str(seat.get("polity_id")) == str(pid) and seat.get("status") == "active"
        ],
        key=lambda seat: (str(seat.get("title_id") or ""), _safe_int(seat.get("slot_index"), 0)),
    )
    vassals = sorted(
        [
            polity
            for polity in _snapshot_rows(snapshot, "polities")
            if str(polity.get("parent_polity_id")) == str(pid) and polity.get("status") == "active"
        ],
        key=lambda polity: str(polity.get("name") or ""),
    )
    territory_items = []
    for terr in territories[:16]:
        target = str(terr.get("target_id") or "")
        if terr.get("target_kind") == "settlement":
            target = _snapshot_settlement_name(snapshot, target)
        territory_items.append(f"{terr.get('target_kind')}: {target} since {terr.get('since_sim_year')}")
    seat_items = []
    for seat in seats[:16]:
        holder = _snapshot_person_link_text(snapshot, seat.get("holder_person_id")) if seat.get("holder_person_id") else "vacant"
        scope = f" at {_snapshot_settlement_name(snapshot, seat.get('scope_settlement_id'))}" if seat.get("scope_settlement_id") else ""
        seat_items.append(f"{seat.get('title_id')}{scope}: {holder}")
    vassal_items = [f"{v.get('name')} ({v.get('polity_type_id')})" for v in vassals]
    cards = "".join(
        [
            _detail_card("Type", row.get("polity_type_id") or ""),
            _detail_card("Status", row.get("status") or ""),
            _detail_card("Territories", len(territories)),
            _detail_card("Seats", len(seats)),
            _detail_card("Held Seats", sum(1 for seat in seats if seat.get("holder_person_id") is not None)),
            _detail_card("Vassals", len(vassals)),
            _detail_card("Capital", _snapshot_settlement_name(snapshot, row.get("capital_settlement_id")) or "None"),
            _detail_card("Founded", row.get("founded_sim_year") or "Unknown"),
        ]
    )
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row.get("name") or f"Polity {pid}"))}</h2>'
        f'<div class="place-subtitle">Polity #{pid}</div>'
        f'<div class="place-grid">{cards}</div>'
        '<div class="place-columns">'
        f'<section><h3>Territory</h3>{_ul(territory_items)}</section>'
        f'<section><h3>Offices</h3>{_ul(seat_items)}</section>'
        f'<section><h3>Vassals</h3>{_ul(vassal_items)}</section>'
        '</div>'
        '</div>'
    )


def _render_place_sheet_from_snapshot(snapshot: dict[str, object], view: str, item_id: str) -> str:
    selected = (view or "Regions").strip()
    if selected == "Towns":
        return _render_town_sheet_from_snapshot(snapshot, item_id)
    if selected == "Polities":
        return _render_polity_sheet_from_snapshot(snapshot, item_id)
    return _render_region_sheet_from_snapshot(snapshot, item_id)


def _snapshot_matches(row: dict[str, object], columns: Iterable[str], needle: str) -> bool:
    if not needle:
        return True
    lowered = needle.lower()
    return any(lowered in str(row.get(column) or "").lower() for column in columns)


def _places_rows_from_snapshot(
    snapshot: dict[str, object],
    view: str,
    search: str,
    limit: object,
) -> tuple[list[dict[str, object]], list[str], list[str], str]:
    selected = (view or "Regions").strip()
    headers = _place_headers(selected)
    row_limit = _safe_int(limit, 50, 1, 250)
    needle = (search or "").strip()
    values: list[dict[str, object]] = []
    item_ids: list[str] = []
    if selected == "Towns":
        rows = [
            row
            for row in _snapshot_rows(snapshot, "settlements")
            if _snapshot_matches(row, ("settlement_id", "region_id", "display_name", "level", "status"), needle)
        ]
        rows = sorted(
            rows,
            key=lambda row: (
                str(row.get("status") or "") != "active",
                -_safe_int(row.get("population_cap"), 0),
                str(row.get("display_name") or "").lower(),
            ),
        )[:row_limit]
        for row in rows:
            sid = str(row.get("settlement_id") or "")
            rid = str(row.get("region_id") or "")
            people = _snapshot_people_by_settlement(snapshot, sid)
            jobs = ", ".join(f"{job} ({n})" for job, n in _top_jobs_from_people(people, 3))
            values.append(
                {
                    "Name": row.get("display_name") or sid,
                    "Level": row.get("level") or "",
                    "Alive": len(people),
                    "Region": rid,
                    "Status": row.get("status") or "",
                    "Food": _fmt_number(row.get("food_pressure")),
                    "Stability": _fmt_number(row.get("stability")),
                    "Prosperity": _fmt_number(row.get("prosperity_pool")),
                    "Polity": _snapshot_polity_names_for_settlement(snapshot, sid, rid),
                    "Top Jobs": jobs,
                }
            )
            item_ids.append(sid)
    elif selected == "Polities":
        rows = [
            row
            for row in _snapshot_rows(snapshot, "polities")
            if _snapshot_matches(row, ("polity_id", "name", "polity_type_id", "status"), needle)
        ]
        rows = sorted(
            rows,
            key=lambda row: (str(row.get("status") or "") != "active", _safe_int(row.get("polity_id"), 0)),
        )[:row_limit]
        for row in rows:
            pid = str(row.get("polity_id") or "")
            territories = _snapshot_open_territory(snapshot, pid)
            seats = [
                seat
                for seat in _snapshot_rows(snapshot, "seats")
                if str(seat.get("polity_id")) == pid and seat.get("status") == "active"
            ]
            values.append(
                {
                    "Name": row.get("name") or f"Polity {pid}",
                    "Type": row.get("polity_type_id") or "",
                    "Status": row.get("status") or "",
                    "Territory": len(territories),
                    "Seats": len(seats),
                    "Holders": sum(1 for seat in seats if seat.get("holder_person_id") is not None),
                    "Parent": row.get("parent_polity_id") or "",
                    "Capital": _snapshot_settlement_name(snapshot, row.get("capital_settlement_id")),
                    "Founded": row.get("founded_sim_year") or "",
                }
            )
            item_ids.append(pid)
    else:
        rows = [
            row
            for row in _snapshot_rows(snapshot, "regions")
            if _snapshot_matches(row, ("region_id", "region_display_name"), needle)
        ]
        rows = sorted(
            rows,
            key=lambda row: (-_safe_int(row.get("total_population_cap"), 0), str(row.get("region_display_name") or "").lower()),
        )[:row_limit]
        for row in rows:
            rid = str(row.get("region_id") or "")
            people = _snapshot_people_by_region(snapshot, rid)
            jobs = ", ".join(f"{job} ({n})" for job, n in _top_jobs_from_people(people, 3))
            active_settlements = sum(
                1 for settlement in _snapshot_region_settlements(snapshot, rid) if settlement.get("status") == "active"
            )
            values.append(
                {
                    "Name": row.get("region_display_name") or rid,
                    "Alive": len(people),
                    "Settlements": active_settlements,
                    "Food": _fmt_number(row.get("food_pressure")),
                    "Stability": _fmt_number(row.get("stability")),
                    "Market": _fmt_number(row.get("market_pull")),
                    "Prosperity": _fmt_number(row.get("prosperity_pool")),
                    "Treasury": _fmt_number(row.get("treasury_balance")),
                    "Polities": _snapshot_polity_names_for_region(snapshot, rid),
                    "Top Jobs": jobs,
                }
            )
            item_ids.append(rid)
    if values:
        headers = list(values[0].keys())
    return values, headers, item_ids, selected


def _places_browser_data_and_state(
    world: str,
    view: str,
    search: str,
    limit: object,
) -> tuple[list[dict[str, object]], list[str], str, str, str]:
    started = time.perf_counter()
    selected = (view or "Regions").strip()
    headers = _place_headers(selected)
    if not world:
        _log_info("places_data_skip view=%s reason=no_world elapsed=%.4fs", selected, time.perf_counter() - started)
        return [], headers, "Choose a world.", _empty_place_state_json(selected), selected
    path = _db_path(world, "Save DB")
    if not path.exists():
        _log_info(
            "places_data_skip view=%s reason=missing_save path=%s elapsed=%.4fs",
            selected,
            path,
            time.perf_counter() - started,
        )
        return [], headers, f"{path} is missing. Run a simulation first.", _empty_place_state_json(selected), selected
    details: dict[str, str] = {}
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        _log_info("places_data_db_open view=%s path=%s selected_world=%s saved_world=%s", selected, path, world, saved_world)
        snapshot = _load_place_snapshot(con, saved_world)
        values, headers, item_ids, selected = _places_rows_from_snapshot(snapshot, selected, search, limit)
        for item_id in item_ids:
            details[item_id] = _render_place_sheet_from_snapshot(snapshot, selected, item_id)
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = f"{path.name}: showing {len(values)} {selected.lower()}{saved_world_note}. Click a row for details."
    state = json.dumps({"view": selected, "keys": item_ids, "details": details})
    _log_info(
        "places_data_done view=%s rows=%s keys=%s details=%s state_bytes=%s elapsed=%.4fs",
        selected,
        len(values),
        len(item_ids),
        len(details),
        len(state),
        time.perf_counter() - started,
    )
    return values, headers, status, state, selected


def _render_region_sheet(con: sqlite3.Connection, world: str, region_id: str) -> str:
    if _saved_table_has_world(con, "simulation_regions"):
        row = con.execute(
            "select * from simulation_regions where world = ? and region_id = ?",
            (world, region_id),
        ).fetchone()
    else:
        row = con.execute("select * from simulation_regions where region_id = ?", (region_id,)).fetchone()
    if not row:
        return f'<div class="place-sheet muted">No region named {html.escape(region_id)}.</div>'
    people_where, people_params = _alive_where(con, world)
    birth_region_sql = _person_birth_region_sql(con)
    alive = _count_one(
        con,
        f"select count(*) from simulation_people where {people_where} and {birth_region_sql} = ?",
        (*people_params, region_id),
    )
    settlements = _region_settlements(con, region_id)
    jobs = [f"{job}: {n}" for job, n in _top_jobs_for_where(con, world, f"{birth_region_sql} = ?", (region_id,), limit=8)]
    people = []
    for p in _top_people_for_where(con, world, f"{birth_region_sql} = ?", (region_id,), limit=8):
        person = _person_from_row(p, _trait_slots_for_world(world))
        people.append(f"{_person_name(person)} — {person.get('job') or 'unassigned'}")
    settlement_items = [
        f"{s['display_name'] or s['settlement_id']} ({s['level']}, {s['status']}, pop {s['population_cap']})"
        for s in settlements[:12]
    ]
    cards = "".join(
        [
            _detail_card("Alive", alive),
            _detail_card("Settlements", len(settlements)),
            _detail_card("Food Pressure", _fmt_number(row["food_pressure"])),
            _detail_card("Stability", _fmt_number(row["stability"])),
            _detail_card("Market Pull", _fmt_number(row["market_pull"])),
            _detail_card("Prosperity", _fmt_number(row["prosperity_pool"])),
            _detail_card("Treasury", _fmt_number(row["treasury_balance"])),
            _detail_card("Polities", _polity_names_for_region(con, region_id) or "None"),
        ]
    )
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row["region_display_name"] or region_id))}</h2>'
        f'<div class="place-subtitle">Region {html.escape(region_id)}</div>'
        f'<div class="place-grid">{cards}</div>'
        f'{_region_map_html(con, region_id)}'
        '<div class="place-columns">'
        f'<section><h3>Settlements</h3>{_ul(settlement_items)}</section>'
        f'<section><h3>Top Jobs</h3>{_ul(jobs)}</section>'
        f'<section><h3>Notable Residents</h3>{_ul(people)}</section>'
        '</div>'
        '</div>'
    )


def _render_town_sheet(con: sqlite3.Connection, world: str, settlement_id: str) -> str:
    if _saved_table_has_world(con, "simulation_settlements"):
        row = con.execute(
            "select * from simulation_settlements where world = ? and settlement_id = ?",
            (world, settlement_id),
        ).fetchone()
    else:
        row = con.execute("select * from simulation_settlements where settlement_id = ?", (settlement_id,)).fetchone()
    if not row:
        if _has_table(con, "simulation_settlements"):
            where, params = _world_where(con, "simulation_settlements", world)
            total = _count_one(con, f"select count(*) from simulation_settlements where {where}", params)
            if total == 0:
                return '<div class="place-sheet muted">No towns are recorded in the current save yet. Browse Towns after the simulation creates one.</div>'
        return (
            '<div class="place-sheet muted">'
            f'Town {html.escape(settlement_id)} is no longer in the current save. '
            'Browse Towns to refresh the list.'
            '</div>'
        )
    sid = str(row["settlement_id"])
    rid = str(row["region_id"])
    people_where, people_params = _alive_where(con, world)
    residence_sql = _person_residence_sql(con)
    alive = _count_one(
        con,
        f"select count(*) from simulation_people where {people_where} and {residence_sql} = ?",
        (*people_params, sid),
    )
    jobs = [f"{job}: {n}" for job, n in _top_jobs_for_where(con, world, f"{residence_sql} = ?", (sid,), limit=8)]
    residents = []
    for p in _top_people_for_where(con, world, f"{residence_sql} = ?", (sid,), limit=8):
        person = _person_from_row(p, _trait_slots_for_world(world))
        residents.append(f"{_person_name(person)} — {person.get('job') or 'unassigned'}")
    cards = "".join(
        [
            _detail_card("Alive", alive),
            _detail_card("Level", row["level"] or ""),
            _detail_card("Status", row["status"] or ""),
            _detail_card("Region", rid),
            _detail_card("Food Pressure", _fmt_number(row["food_pressure"])),
            _detail_card("Stability", _fmt_number(row["stability"])),
            _detail_card("Market Pull", _fmt_number(row["market_pull"])),
            _detail_card("Prosperity", _fmt_number(row["prosperity_pool"])),
            _detail_card("Polity", _polity_names_for_settlement(con, sid, rid) or "None"),
            _detail_card("Founded", row["founded_sim_year"] or "Unknown"),
        ]
    )
    name_bits = [row["etymology"], row["name_category_primary"], row["name_culture_primary"]]
    name_line = " | ".join(str(x) for x in name_bits if x)
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row["display_name"] or sid))}</h2>'
        f'<div class="place-subtitle">{html.escape(str(row["level"] or "settlement"))} in {html.escape(rid)}</div>'
        f'<div class="place-muted">{html.escape(name_line)}</div>'
        f'<div class="place-grid">{cards}</div>'
        f'{_region_map_html(con, rid, focus_settlement_id=sid)}'
        '<div class="place-columns">'
        f'<section><h3>Top Jobs</h3>{_ul(jobs)}</section>'
        f'<section><h3>Notable Residents</h3>{_ul(residents)}</section>'
        '</div>'
        '</div>'
    )


def _render_polity_sheet(con: sqlite3.Connection, world: str, polity_id: str) -> str:
    try:
        pid = int(polity_id)
    except (TypeError, ValueError):
        return '<div class="place-sheet muted">Choose a polity row to inspect it.</div>'
    if _saved_table_has_world(con, "simulation_polities"):
        row = con.execute(
            "select * from simulation_polities where world = ? and polity_id = ?",
            (world, pid),
        ).fetchone()
    else:
        row = con.execute("select * from simulation_polities where polity_id = ?", (pid,)).fetchone()
    if not row:
        if _has_table(con, "simulation_polities"):
            where, params = _world_where(con, "simulation_polities", world)
            total = _count_one(con, f"select count(*) from simulation_polities where {where}", params)
            if total == 0:
                return '<div class="place-sheet muted">No polities are recorded in the current save yet. Browse Polities after the simulation forms one.</div>'
        return f'<div class="place-sheet muted">Polity #{pid} is no longer in the current save. Browse Polities to refresh the list.</div>'
    territories = con.execute(
        """
        select target_kind, target_id, since_sim_year
        from simulation_polity_territory
        where polity_id = ? and until_sim_year is null
        order by target_kind, target_id
        """,
        (pid,),
    ).fetchall() if _has_table(con, "simulation_polity_territory") else []
    seats = con.execute(
        """
        select seat_id, title_id, scope_settlement_id, holder_person_id, term_expires_sim_year
        from simulation_office_seats
        where polity_id = ? and status = 'active'
        order by title_id, slot_index
        """,
        (pid,),
    ).fetchall() if _has_table(con, "simulation_office_seats") else []
    vassals = con.execute(
        """
        select polity_id, name, polity_type_id
        from simulation_polities
        where parent_polity_id = ? and status = 'active'
        order by name
        """,
        (pid,),
    ).fetchall() if _has_table(con, "simulation_polities") else []
    territory_items = []
    for terr in territories[:16]:
        target = str(terr["target_id"])
        if terr["target_kind"] == "settlement":
            target = _settlement_name(con, world, target)
        territory_items.append(f"{terr['target_kind']}: {target} since {terr['since_sim_year']}")
    seat_items = []
    for seat in seats[:16]:
        holder = _person_link_text(con, world, seat["holder_person_id"]) if seat["holder_person_id"] else "vacant"
        scope = f" at {_settlement_name(con, world, seat['scope_settlement_id'])}" if seat["scope_settlement_id"] else ""
        seat_items.append(f"{seat['title_id']}{scope}: {holder}")
    vassal_items = [f"{v['name']} ({v['polity_type_id']})" for v in vassals]
    cards = "".join(
        [
            _detail_card("Type", row["polity_type_id"] or ""),
            _detail_card("Status", row["status"] or ""),
            _detail_card("Territories", len(territories)),
            _detail_card("Seats", len(seats)),
            _detail_card("Held Seats", sum(1 for s in seats if s["holder_person_id"] is not None)),
            _detail_card("Vassals", len(vassals)),
            _detail_card("Capital", _settlement_name(con, world, row["capital_settlement_id"]) or "None"),
            _detail_card("Founded", row["founded_sim_year"] or "Unknown"),
        ]
    )
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row["name"] or f"Polity {pid}"))}</h2>'
        f'<div class="place-subtitle">Polity #{pid}</div>'
        f'<div class="place-grid">{cards}</div>'
        '<div class="place-columns">'
        f'<section><h3>Territory</h3>{_ul(territory_items)}</section>'
        f'<section><h3>Offices</h3>{_ul(seat_items)}</section>'
        f'<section><h3>Vassals</h3>{_ul(vassal_items)}</section>'
        '</div>'
        '</div>'
    )


def render_place_detail(world: str, view: str, key: object) -> str:
    selected = (view or "Regions").strip()
    if not key:
        return '<div class="place-sheet muted">Click a region, town, or polity row to inspect it.</div>'
    path_world, key_saved_world, item_id = _decode_place_key(world, key)
    path = _db_path(path_world, "Save DB")
    if not path.exists():
        return f'<div class="place-sheet muted">{html.escape(str(path))} is missing.</div>'
    with _connect_readonly(path) as con:
        saved_world = key_saved_world or _resolve_saved_world(con, path_world)
        if selected == "Towns":
            html_out = _render_town_sheet(con, saved_world, item_id)
        elif selected == "Polities":
            html_out = _render_polity_sheet(con, saved_world, item_id)
        else:
            html_out = _render_region_sheet(con, saved_world, item_id)
    return html_out


def render_places_html_selection(world: str, view: str, key: object) -> str:
    started = time.perf_counter()
    try:
        detail = render_place_detail(world, view, key)
        _log_info(
            "places_html_select_done view=%r world=%r key=%r detail_bytes=%s elapsed=%.4fs",
            view,
            world,
            key,
            len(detail),
            time.perf_counter() - started,
        )
        return detail
    except Exception:
        _log_exception("places_html_select_error view=%r world=%r key=%r", view, world, key)
        raise


def render_world_map_selection_detail(world: str, selection_json: str) -> str:
    if not selection_json:
        return '<div class="place-sheet muted">Click a region or settlement on the map to inspect it.</div>'
    try:
        selection = json.loads(selection_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return '<div class="place-sheet muted">Click a region or settlement on the map to inspect it.</div>'
    view = str(selection.get("view") or "Regions")
    item_id = str(selection.get("id") or "").strip()
    if view not in {"Regions", "Towns"} or not item_id:
        return '<div class="place-sheet muted">Click a region or settlement on the map to inspect it.</div>'
    return render_place_detail(world, view, _encode_place_key(world, "", item_id))


def render_world_map_with_detail_reset(
    world: str,
    include_overlays: bool = True,
    noisy_edges: bool = True,
    labels: bool = True,
) -> tuple[str, str]:
    return (
        render_world_map_html(world, include_overlays, noisy_edges, labels),
        '<div class="place-sheet muted">Click a region or settlement on the map to inspect it.</div>',
    )


def _place_detail_from_state(state: object, row_index: int) -> str | None:
    if isinstance(state, str):
        try:
            parsed = json.loads(state)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        keys = parsed.get("keys") if isinstance(parsed, dict) else None
        details = parsed.get("details") if isinstance(parsed, dict) else None
        if isinstance(keys, list) and isinstance(details, dict):
            try:
                key = str(keys[row_index])
            except (IndexError, TypeError):
                return None
            detail = details.get(key)
            return str(detail) if detail is not None else None
    return None


def select_place_from_table(keys: object, world: str, view: str, evt: gr.SelectData) -> str:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        index = int(row_index)
    except Exception:
        return '<div class="place-sheet muted">Click a region, town, or polity row to inspect it.</div>'
    detail = _place_detail_from_state(keys, index)
    if detail is not None:
        return detail
    try:
        key = keys[index]  # type: ignore[index]
    except Exception:
        return '<div class="place-sheet muted">Click a region, town, or polity row to inspect it.</div>'
    return render_place_detail(world, view, key)


def _select_place_view(view: str, keys: object, world: str, evt: gr.SelectData) -> str:
    started = time.perf_counter()
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        index = int(row_index)
    except Exception:
        _log_exception("place_select_bad_event view=%s world=%r", view, world)
        return '<div class="place-sheet muted">Click a region, town, or polity row to inspect it.</div>'
    detail = _place_detail_from_state(keys, index)
    if detail is not None:
        _log_info(
            "place_select_from_state view=%s world=%r row=%s detail_bytes=%s elapsed=%.4fs",
            view,
            world,
            index,
            len(detail),
            time.perf_counter() - started,
        )
        return detail
    try:
        key = keys[index]  # type: ignore[index]
    except Exception:
        _log_exception("place_select_missing_key view=%s world=%r row=%s keys_type=%s", view, world, index, type(keys).__name__)
        return '<div class="place-sheet muted">Click a region, town, or polity row to inspect it.</div>'
    detail = render_place_detail(world, view, key)
    _log_info(
        "place_select_rendered view=%s world=%r row=%s key=%r detail_bytes=%s elapsed=%.4fs",
        view,
        world,
        index,
        key,
        len(detail),
        time.perf_counter() - started,
    )
    return detail


def select_region_from_table(keys: object, world: str, evt: gr.SelectData) -> str:
    return _select_place_view("Regions", keys, world, evt)


def select_town_from_table(keys: object, world: str, evt: gr.SelectData) -> str:
    return _select_place_view("Towns", keys, world, evt)


def select_polity_from_table(keys: object, world: str, evt: gr.SelectData) -> str:
    return _select_place_view("Polities", keys, world, evt)


def load_csv_file(filename: str, search: str, limit: object, offset: object) -> tuple[gr.Dataframe, str, str]:
    if not filename:
        return gr.Dataframe(value=[], headers=[]), "Choose a CSV file.", ""

    row_limit = _safe_int(limit, DEFAULT_LIMIT, 1)
    row_offset = _safe_int(offset, 0, 0, 10_000_000)
    path = CONFIG_DIR / filename
    if not path.exists():
        return gr.Dataframe(value=[], headers=[]), f"Missing CSV: {path}", ""

    matched: list[dict[str, str]] = []
    total_matches = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        for row in reader:
            haystack = " ".join(str(row.get(header, "")) for header in headers)
            if search and search.lower() not in haystack.lower():
                continue
            if total_matches >= row_offset and len(matched) < row_limit:
                matched.append(row)
            total_matches += 1

    schema = "\n".join(headers)
    status = f"{filename}: showing {len(matched)} of {total_matches} matching rows at offset {row_offset}"
    return _dataframe(matched, headers), status, schema


def _positive_int(value: object, name: str) -> int:
    number = _safe_int(value, 0, 1, 10_000_000)
    if number < 1:
        raise ValueError(f"{name} must be >= 1")
    return number


def build_sim_command(
    world_id: str,
    years: object,
    starting_couples: object,
    start_year: object,
    seed: object,
    flush_batch_years: object,
    reset_world: bool,
    skip_timing_log: bool,
    extra_args: str,
    progress: bool = False,
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "utils" / "run_population_simulation.py"),
        "--years",
        str(_positive_int(years, "years")),
        "--world-id",
        (world_id or "default").strip(),
        "--starting-couples",
        str(_positive_int(starting_couples, "starting_couples")),
        "--start-year",
        str(int(start_year)),
    ]

    if seed not in (None, ""):
        command.extend(["--seed", str(int(seed))])
    if flush_batch_years not in (None, ""):
        command.extend(["--flush-batch-years", str(_positive_int(flush_batch_years, "flush_batch_years"))])
    if reset_world:
        command.append("--reset-world")
    if skip_timing_log:
        command.append("--skip-timing-log")
    if progress:
        command.append("--progress")
    if extra_args:
        command.extend(shlex.split(extra_args, posix=False))
    return command


def preview_sim_command(
    world_id: str,
    years: object,
    starting_couples: object,
    start_year: object,
    seed: object,
    flush_batch_years: object,
    reset_world: bool,
    skip_timing_log: bool,
    extra_args: str,
) -> str:
    try:
        command = build_sim_command(
            world_id,
            years,
            starting_couples,
            start_year,
            seed,
            flush_batch_years,
            reset_world,
            skip_timing_log,
            extra_args,
        )
    except Exception as exc:
        return f"Invalid inputs: {exc}"
    return subprocess.list2cmdline(command)


def _elapsed_hhmmss(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _sim_progress_html(
    current_year: int, end_year: int, elapsed: str, *, start_year: int
) -> str:
    current = int(current_year)
    end = int(end_year)
    start = int(start_year)
    if end == start:
        pct = 100.0 if current >= end else 0.0
    elif end > start:
        pct = ((current - start) / float(end - start)) * 100.0
    else:
        pct = 100.0
    shown_pct = max(0.0, min(100.0, pct))
    return (
        '<div class="sim-progress-card" role="status" aria-live="polite">'
        '<div class="sim-progress-label">'
        f'<span>Year {html.escape(str(current))} / {html.escape(str(end))}</span>'
        f'<span>Elapsed {html.escape(elapsed)}</span>'
        '</div>'
        '<div class="sim-progress-track" aria-hidden="true">'
        f'<div class="sim-progress-fill" style="width: {shown_pct:.1f}%"></div>'
        '</div>'
        '</div>'
    )


def _read_pipe_to_queue(pipe, stream_name: str, out_q: "queue.Queue[tuple[str, str]]") -> None:
    try:
        for line in pipe:
            out_q.put((stream_name, line))
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def run_simulation_from_ui(
    world_id: str,
    years: object,
    starting_couples: object,
    start_year: object,
    seed: object,
    flush_batch_years: object,
    reset_world: bool,
    skip_timing_log: bool,
    extra_args: str,
) -> Iterator[tuple[str, str, str, str, str]]:
    try:
        start_int = int(start_year)
        years_int = _positive_int(years, "years")
        end_year = start_int + years_int - 1
        command = build_sim_command(
            world_id,
            years_int,
            starting_couples,
            start_int,
            seed,
            flush_batch_years,
            reset_world,
            skip_timing_log,
            extra_args,
            progress=True,
        )
    except Exception as exc:
        yield "Invalid inputs", str(exc), "", "", ""
        return

    command_text = subprocess.list2cmdline(command)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    start_t = time.perf_counter()
    yield (
        f"Simulation starting. Elapsed 00:00:00. Year {start_int} / {end_year}.",
        command_text,
        "",
        "",
        _sim_progress_html(start_int, end_year, "00:00:00", start_year=start_int),
    )

    proc = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stream_q: queue.Queue[tuple[str, str]] = queue.Queue()
    if proc.stdout is not None:
        threading.Thread(
            target=_read_pipe_to_queue,
            args=(proc.stdout, "stdout", stream_q),
            daemon=True,
        ).start()
    if proc.stderr is not None:
        threading.Thread(
            target=_read_pipe_to_queue,
            args=(proc.stderr, "stderr", stream_q),
            daemon=True,
        ).start()

    current_year = start_int
    expected_end = end_year
    last_emit = 0.0
    while proc.poll() is None or not stream_q.empty():
        changed = False
        try:
            stream_name, line = stream_q.get(timeout=0.25)
        except queue.Empty:
            stream_name, line = "", ""
        if line:
            changed = True
            if stream_name == "stderr":
                stderr_lines.append(line)
            else:
                stdout_lines.append(line)
                match = SIM_PROGRESS_RE.search(line)
                if match:
                    current_year = int(match.group("year"))
                    expected_end = int(match.group("end_year"))
        now = time.perf_counter()
        elapsed = _elapsed_hhmmss(now - start_t)
        if changed or now - last_emit >= 1.0:
            last_emit = now
            yield (
                f"Running. Elapsed {elapsed}. Year {current_year} / {expected_end}.",
                command_text,
                "".join(reversed(stdout_lines)),
                "".join(stderr_lines),
                _sim_progress_html(
                    current_year, expected_end, elapsed, start_year=start_int
                ),
            )

    return_code = proc.wait()
    elapsed = _elapsed_hhmmss(time.perf_counter() - start_t)
    final_status = (
        f"Simulation finished. Elapsed {elapsed}. Year {end_year} / {end_year}."
        if return_code == 0
        else f"Simulation failed with exit code {return_code}. Elapsed {elapsed}."
    )
    yield (
        final_status,
        command_text,
        "".join(reversed(stdout_lines)),
        "".join(stderr_lines),
        _sim_progress_html(
            end_year if return_code == 0 else current_year,
            end_year,
            elapsed,
            start_year=start_int,
        ),
    )


def build_app(default_world: str = "default") -> gr.Blocks:
    configure_app_logging()
    started = time.perf_counter()
    _log_info("build_app_start default_world=%r", default_world)
    worlds = _world_names()
    csvs = _csv_names()
    initial_world = default_world if default_world in worlds else (worlds[0] if worlds else "")
    initial_tables = _table_names(initial_world, "Config DB") if initial_world else []
    initial_world_map = render_world_map_html(initial_world, True, True, True) if initial_world else ""
    _log_info(
        "build_app_initial_data worlds=%s csvs=%s initial_world=%r initial_tables=%s map_bytes=%s elapsed=%.4fs",
        len(worlds),
        len(csvs),
        initial_world,
        len(initial_tables),
        len(initial_world_map),
        time.perf_counter() - started,
    )

    with gr.Blocks(title="History Project Data Browser") as app:
        gr.HTML(f"<style>{APP_CSS}</style>")
        gr.Markdown("# History Project Data Browser")

        with gr.Tab("People") as people_tab:
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=5):
                    with gr.Row():
                        person_world = gr.Dropdown(worlds, value=initial_world, label="World")
                        person_life = gr.Radio(["All", "Alive", "Dead"], value="Alive", label="People")
                        person_limit = gr.Number(value=75, label="Limit", precision=0)
                    with gr.Row():
                        person_min_age = gr.Textbox(value="", label="Min Age", placeholder="Any")
                        person_max_age = gr.Textbox(value="", label="Max Age", placeholder="Any")
                        person_sort_by = gr.Dropdown(
                            [
                                "Default",
                                "ID",
                                "Name",
                                "Age",
                                "Born",
                                "Died",
                                "Gender",
                                "Species",
                                "Ethnic",
                                "Home",
                                *LEGACY_SCORE_COLUMNS,
                            ],
                            value="Default",
                            label="Sort By",
                        )
                        person_sort_dir = gr.Radio(["Descending", "Ascending"], value="Descending", label="Sort")
                    person_search = gr.Textbox(
                        label="Search People",
                        placeholder="Name, culture, birthplace, trait, job, settlement...",
                    )
                    person_load = gr.Button("Browse People", variant="primary")
                    person_status = gr.Textbox(label="Status", interactive=False)
                    person_table = gr.Dataframe(
                        label="People",
                        interactive=False,
                        wrap=False,
                        elem_id="person-table",
                    )
                    person_ids_state = gr.State([])
                with gr.Column(scale=6):
                    with gr.Row():
                        person_open_id = gr.Textbox(
                            value="",
                            label="Open Record ID",
                            placeholder="Optional",
                            elem_id="person-open-id",
                        )
                        person_open_button = gr.Button("Open Record", elem_id="person-open-button")
                    person_sheet = gr.HTML(
                        value='<div class="person-sheet muted">Browse people, then click a row to open a person sheet.</div>',
                        label="Person Sheet",
                    )
                    person_share_text = gr.Textbox(
                        value="Click a person row to generate share text.",
                        label="Copyable Gmail Text",
                        lines=14,
                        max_lines=24,
                        interactive=False,
                        buttons=["copy"],
                    )

        with gr.Tab("Settlements") as settlements_tab:
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=5):
                    with gr.Row():
                        settlement_world = gr.Dropdown(worlds, value=initial_world, label="World")
                        settlement_status_filter = gr.Radio(["All", "Active", "Abandoned"], value="Active", label="Settlements")
                        settlement_limit = gr.Number(value=50, label="Limit", precision=0)
                    settlement_search = gr.Textbox(
                        label="Search Settlements",
                        placeholder="Name, id, region, level, or status...",
                    )
                    settlement_load = gr.Button("Browse Settlements", variant="primary")
                    settlement_status = gr.Textbox(label="Status", interactive=False)
                    settlement_table = gr.Dataframe(
                        label="Settlements",
                        interactive=False,
                        wrap=False,
                        elem_id="settlement-table",
                    )
                    settlement_ids_state = gr.State([])
                with gr.Column(scale=6):
                    settlement_sheet = gr.HTML(
                        value='<div class="place-sheet muted">Browse settlements, then click a row to inspect it.</div>',
                        label="Settlement Sheet",
                    )

        with gr.Tab("Regions") as regions_tab:
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=5):
                    with gr.Row():
                        region_world = gr.Dropdown(worlds, value=initial_world, label="World")
                        region_limit = gr.Number(value=50, label="Limit", precision=0)
                    region_search = gr.Textbox(
                        label="Search Regions",
                        placeholder="Name or id...",
                    )
                    region_load = gr.Button("Browse Regions", variant="primary")
                    region_status = gr.Textbox(label="Status", interactive=False)
                    region_table = gr.Dataframe(
                        label="Regions",
                        interactive=False,
                        wrap=False,
                        elem_id="region-table",
                    )
                    region_ids_state = gr.State([])
                with gr.Column(scale=6):
                    region_sheet = gr.HTML(
                        value='<div class="place-sheet muted">Browse regions, then click a row to inspect it.</div>',
                        label="Region Sheet",
                    )

        with gr.Tab("Polities") as polities_tab:
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=5):
                    with gr.Row():
                        polity_world = gr.Dropdown(worlds, value=initial_world, label="World")
                        polity_status_filter = gr.Radio(["All", "Active", "Inactive"], value="Active", label="Polities")
                        polity_limit = gr.Number(value=50, label="Limit", precision=0)
                    polity_search = gr.Textbox(
                        label="Search Polities",
                        placeholder="Name, id, type, or status...",
                    )
                    polity_load = gr.Button("Browse Polities", variant="primary")
                    polity_status = gr.Textbox(label="Status", interactive=False)
                    polity_table = gr.Dataframe(
                        label="Polities",
                        interactive=False,
                        wrap=False,
                        elem_id="polity-table",
                    )
                    polity_ids_state = gr.State([])
                with gr.Column(scale=6):
                    polity_sheet = gr.HTML(
                        value='<div class="place-sheet muted">Browse polities, then click a row to inspect it.</div>',
                        label="Polity Sheet",
                    )

        with gr.Tab("World Map") as world_map_tab:
            with gr.Row(elem_classes=["world-browser"]):
                map_world = gr.Dropdown(worlds, value=initial_world, label="World")
                map_include_overlays = gr.Checkbox(value=True, label="Settlements and Polities")
                map_noisy_edges = gr.Checkbox(value=True, label="Noisy Edges")
                map_labels = gr.Checkbox(value=True, label="Labels")
                map_refresh = gr.Button("Render Map", variant="primary")
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=7):
                    world_map_html = gr.HTML(value=initial_world_map, label="Generated World Map")
                with gr.Column(scale=5):
                    map_open_selection = gr.Textbox(
                        value="",
                        label="Map Selection",
                        elem_id="map-open-selection",
                        elem_classes=["world-map-open-controls"],
                    )
                    map_open_button = gr.Button(
                        "Open Map Selection",
                        elem_id="map-open-button",
                        elem_classes=["world-map-open-controls"],
                    )
                    map_sheet = gr.HTML(
                        value='<div class="place-sheet muted">Click a region or settlement on the map to inspect it.</div>',
                        label="Map Detail Sheet",
                    )

        with gr.Tab("Raw Data Browser"):
            with gr.Tab("SQLite"):
                with gr.Row():
                    world = gr.Dropdown(worlds, value=initial_world, label="World")
                    db_kind = gr.Radio(["Config DB", "Save DB"], value="Config DB", label="Database")
                    table = gr.Dropdown(initial_tables, value=initial_tables[0] if initial_tables else None, label="Table")
                sqlite_status = gr.Textbox(label="Status", interactive=False)
                with gr.Row():
                    sqlite_search = gr.Textbox(label="Search", placeholder="Search all columns")
                    sqlite_limit = gr.Number(value=DEFAULT_LIMIT, label="Limit", precision=0)
                    sqlite_offset = gr.Number(value=0, label="Offset", precision=0)
                sqlite_load = gr.Button("Load Table", variant="primary")
                sqlite_data = gr.Dataframe(label="Rows", interactive=False, wrap=True)
                sqlite_schema = gr.Textbox(label="Schema", lines=8, interactive=False)

                gr.Markdown("## Custom SELECT")
                sql = gr.Code(
                    value="select * from world_state",
                    language="sql",
                    label="SQL",
                    lines=5,
                )
                sql_run = gr.Button("Run SELECT")
                sql_data = gr.Dataframe(label="Query Results", interactive=False, wrap=True)
                sql_status = gr.Textbox(label="Query Status", interactive=False)

            with gr.Tab("Config CSV"):
                with gr.Row():
                    csv_file = gr.Dropdown(csvs, value=csvs[0] if csvs else None, label="CSV")
                    csv_search = gr.Textbox(label="Search", placeholder="Search all columns")
                    csv_limit = gr.Number(value=DEFAULT_LIMIT, label="Limit", precision=0)
                    csv_offset = gr.Number(value=0, label="Offset", precision=0)
                csv_load = gr.Button("Load CSV", variant="primary")
                csv_data = gr.Dataframe(label="Rows", interactive=False, wrap=True)
                csv_status = gr.Textbox(label="Status", interactive=False)
                csv_schema = gr.Textbox(label="Columns", lines=8, interactive=False)

        with gr.Tab("Run Simulation"):
            gr.Markdown("Run the maintained population simulation CLI and capture its output.")
            with gr.Row():
                sim_world = gr.Textbox(value=initial_world or "default", label="World ID", scale=1, min_width=110)
                sim_years = gr.Number(value=100, label="Years", precision=0, scale=1, min_width=90)
                sim_starting_couples = gr.Number(value=10, label="Couples", precision=0, scale=1, min_width=90)
                sim_start_year = gr.Number(value=1000, label="Start", precision=0, scale=1, min_width=90)
                sim_seed = gr.Textbox(value="", label="Seed", placeholder="Blank", scale=1, min_width=100)
                sim_flush = gr.Number(value=10, label="Flush", precision=0, scale=1, min_width=90)
                sim_reset = gr.Checkbox(value=False, label="Reset", scale=0, min_width=75)
                sim_skip_timing = gr.Checkbox(value=False, label="No Timing", scale=0, min_width=95)
                sim_run_button = gr.Button("Run", variant="primary", scale=0, min_width=70)
            with gr.Accordion("Advanced command options", open=False):
                sim_extra_args = gr.Textbox(
                    value="",
                    label="Extra CLI Args",
                    placeholder="Optional, for newly added flags",
                )
                sim_preview = gr.Textbox(label="Command Preview", lines=2, interactive=False)
            with gr.Row():
                sim_progress = gr.HTML(value="", scale=2, min_width=220)
                sim_status = gr.Textbox(label="Status", interactive=False, scale=5)
            sim_stdout = gr.Textbox(label="Output (newest first)", lines=8, max_lines=10, interactive=False)
            sim_stderr = gr.Textbox(label="Errors", lines=8, interactive=False)

        person_load.click(
            load_people_browser,
            [
                person_world,
                person_search,
                person_life,
                person_min_age,
                person_max_age,
                person_sort_by,
                person_sort_dir,
                person_limit,
            ],
            [person_table, person_status, person_ids_state],
        )
        person_browser_inputs = [
            person_world,
            person_search,
            person_life,
            person_min_age,
            person_max_age,
            person_sort_by,
            person_sort_dir,
            person_limit,
        ]
        for person_input in person_browser_inputs:
            person_input.change(load_people_browser, person_browser_inputs, [person_table, person_status, person_ids_state])
        person_search.submit(
            load_people_browser,
            person_browser_inputs,
            [person_table, person_status, person_ids_state],
        )
        person_table.select(select_person_from_table, [person_ids_state, person_world], [person_sheet, person_share_text])
        person_open_button.click(render_person_outputs, [person_world, person_open_id], [person_sheet, person_share_text])
        person_open_id.submit(render_person_outputs, [person_world, person_open_id], [person_sheet, person_share_text])
        people_tab.select(
            load_people_browser,
            person_browser_inputs,
            [person_table, person_status, person_ids_state],
        )
        app.load(
            load_people_browser,
            person_browser_inputs,
            [person_table, person_status, person_ids_state],
        )
        settlement_browser_inputs = [
            settlement_world,
            settlement_search,
            settlement_status_filter,
            settlement_limit,
        ]
        settlement_browser_outputs = [settlement_table, settlement_status, settlement_ids_state]
        settlement_load.click(load_settlements_browser, settlement_browser_inputs, settlement_browser_outputs)
        for settlement_input in settlement_browser_inputs:
            settlement_input.change(load_settlements_browser, settlement_browser_inputs, settlement_browser_outputs)
        settlement_search.submit(load_settlements_browser, settlement_browser_inputs, settlement_browser_outputs)
        settlement_table.select(select_settlement_from_table, [settlement_ids_state, settlement_world], settlement_sheet)
        region_browser_inputs = [region_world, region_search, region_limit]
        region_browser_outputs = [region_table, region_status, region_ids_state]
        region_load.click(load_regions_browser_fresh, region_browser_inputs, region_browser_outputs)
        for region_input in region_browser_inputs:
            region_input.change(load_regions_browser_fresh, region_browser_inputs, region_browser_outputs)
        region_search.submit(load_regions_browser_fresh, region_browser_inputs, region_browser_outputs)
        region_table.select(select_region_from_fresh_table, [region_ids_state, region_world], region_sheet)
        polity_browser_inputs = [
            polity_world,
            polity_search,
            polity_status_filter,
            polity_limit,
        ]
        polity_browser_outputs = [polity_table, polity_status, polity_ids_state]
        polity_load.click(load_polities_browser_fresh, polity_browser_inputs, polity_browser_outputs)
        for polity_input in polity_browser_inputs:
            polity_input.change(load_polities_browser_fresh, polity_browser_inputs, polity_browser_outputs)
        polity_search.submit(load_polities_browser_fresh, polity_browser_inputs, polity_browser_outputs)
        polity_table.select(select_polity_from_fresh_table, [polity_ids_state, polity_world], polity_sheet)
        map_inputs = [map_world, map_include_overlays, map_noisy_edges, map_labels]
        map_outputs = [world_map_html, map_sheet]
        map_refresh.click(render_world_map_with_detail_reset, map_inputs, map_outputs)
        for map_input in map_inputs:
            map_input.change(render_world_map_with_detail_reset, map_inputs, map_outputs)
        map_open_button.click(render_world_map_selection_detail, [map_world, map_open_selection], map_sheet)
        world.change(refresh_sqlite_tables, [world, db_kind], [table, sqlite_status])
        db_kind.change(refresh_sqlite_tables, [world, db_kind], [table, sqlite_status])
        sqlite_load.click(
            load_sqlite_table,
            [world, db_kind, table, sqlite_search, sqlite_limit, sqlite_offset],
            [sqlite_data, sqlite_status, sqlite_schema],
        )
        table.change(
            load_sqlite_table,
            [world, db_kind, table, sqlite_search, sqlite_limit, sqlite_offset],
            [sqlite_data, sqlite_status, sqlite_schema],
        )
        sql_run.click(run_select_query, [world, db_kind, sql, sqlite_limit], [sql_data, sql_status])
        csv_load.click(load_csv_file, [csv_file, csv_search, csv_limit, csv_offset], [csv_data, csv_status, csv_schema])
        csv_file.change(load_csv_file, [csv_file, csv_search, csv_limit, csv_offset], [csv_data, csv_status, csv_schema])
        sim_inputs = [
            sim_world,
            sim_years,
            sim_starting_couples,
            sim_start_year,
            sim_seed,
            sim_flush,
            sim_reset,
            sim_skip_timing,
            sim_extra_args,
        ]
        for sim_input in sim_inputs:
            sim_input.change(preview_sim_command, sim_inputs, sim_preview)
        sim_run_button.click(
            run_simulation_from_ui,
            sim_inputs,
            [sim_status, sim_preview, sim_stdout, sim_stderr, sim_progress],
        )

    _log_info("build_app_done elapsed=%.4fs", time.perf_counter() - started)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="default", help="Initial world selection.")
    parser.add_argument("--host", default="127.0.0.1", help="Gradio host.")
    parser.add_argument("--port", type=int, default=7860, help="Gradio port.")
    args = parser.parse_args()
    log_path = configure_app_logging()
    _log_info(
        "main_start argv=%r executable=%s gradio_version=%s log_path=%s",
        sys.argv,
        sys.executable,
        getattr(gr, "__version__", "unknown"),
        log_path,
    )
    build_app(args.world).launch(server_name=args.host, server_port=args.port, show_error=True)


if __name__ == "__main__":
    main()
