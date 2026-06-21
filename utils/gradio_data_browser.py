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
from typing import Callable, Iterator, Iterable

import gradio as gr


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from library.simulation_outlaws import outlaw_refuge_display_name

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
LEGACY_SCORE_MIN_DISPLAY = 0.70
ARCHIVE_SCORE_DEFINITIONS = {
    "Narrative Heat": "0-100: how much visible story material this person has accumulated from events, relationships, rarity, and consequences.",
    "ARI": "Archive Recognition Index, 0-100: how likely this person is to be identifiable in later records instead of surviving only as background context.",
    "Hidden Heat": "0-100: story significance that exists in private, sealed, rumored, or currently obscure records.",
    "Violet Marginalia": "Whether the person crosses the threshold for later annotators to flag them as unusually archive-worthy.",
    "Archive Quadrant": "Bucketed cross of Narrative Heat and Archive Recognition Index.",
    "Recognition": "Bucketed explanation of the Archive Recognition Index.",
    "Narrative": "Bucketed explanation of the total Narrative Heat score.",
    "Events": "Narrative Heat from direct event participation.",
    "Contradictions": "Narrative Heat from tension, reversals, and conflicting public/private traces.",
    "Consequences": "Narrative Heat from obligations, reputation marks, legal fallout, or domain effects.",
    "Social": "Narrative Heat from relationship, household, office, and social-network entanglement.",
    "Rarity": "Narrative Heat from unusual traits, roles, circumstances, or low-frequency combinations.",
    "Volatility": "Narrative Heat from instability, risk, or fast-changing life context.",
    "Legacy": "Narrative Heat contributed by high legacy-index potential.",
}
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
OUTLAW_CASE_BROWSER_HEADERS = [
    "Case",
    "Status",
    "Offense",
    "Accused",
    "Refuge",
    "Custody",
    "Region",
    "Settlement",
    "Started",
    "Last Seen",
    "Forget Year",
    "Resolved",
    "Severity",
    "Knownness",
    "Pursuit",
    "Buyoff",
    "Resolution",
]
OUTLAW_REFUGE_BROWSER_HEADERS = [
    "Refuge",
    "Status",
    "Region",
    "Near Settlement",
    "Band Size",
    "Active Cases",
    "Founded",
    "Discovered",
    "Abandoned",
    "Concealment",
    "Support",
    "Last Activity",
]
HISTORY_VIEW_CHOICES = [
    "Admin Truth",
    "Public Chronicle",
    "Public Unknown",
    "Public Rumors",
    "Public Known",
    "Lost History",
    "Rediscoveries",
]
HISTORY_VIEW_ALIASES = {
    "Rumors": "Public Rumors",
}
HISTORY_BROWSER_HEADERS = [
    "Year",
    "Event ID",
    "Record ID",
    "Event Type",
    "Visibility",
    "Public Stage",
    "Record Type",
    "Template",
    "Prose",
    "Admin Summary",
]
HISTORY_SUMMARY_HEADERS = ["Section", "Key", "Count", "Value"]
HISTORY_LENS_CHOICES = ["Person", "Family", "Household", "Settlement", "Region", "Polity"]
HISTORY_LENS_HEADERS = [
    "Year",
    "Event ID",
    "Record ID",
    "Lens",
    "Focus",
    "Event Type",
    "Visibility",
    "Public Stage",
    "Record Type",
    "Role",
    "Prose",
    "Admin Summary",
]
REDISCOVERY_DETAIL_HEADERS = [
    "Year",
    "Event ID",
    "Record ID",
    "Event Type",
    "Visibility",
    "Public Stage",
    "Confidence",
    "Source",
    "Preserved At",
    "Distortion",
    "Rediscovery Summary",
    "Admin Summary",
]

from library.world_map_geometry import (  # noqa: E402
    WorldMapGeometry,
    build_world_map_geometry,
    project_local_point_to_region_footprint,
    project_world_point_to_region_footprint,
)
from library.event_history_report import (  # noqa: E402
    INCIDENT_EVENT_TYPES,
    build_event_history_report,
)
from library.event_prose import (  # noqa: E402
    EventAdminSummary,
    EventRecordProse,
    load_admin_event_summaries,
    load_event_record_prose_rows,
    load_public_chronicle_prose,
    load_public_known_prose,
    load_public_rumor_prose,
    load_public_unknown_prose,
)
from library.fontawesome_free_icons import FONT_AWESOME_FREE_SOLID  # noqa: E402
from library.job_archetypes import normalize_job_catalog_key  # noqa: E402
from library.person_almanack import (  # noqa: E402
    metric_definition_choices,
    metric_categories,
    metric_choices,
    person_almanack_cache_status,
    query_person_almanack,
    query_person_almanack_duel,
    query_person_almanack_evidence,
    refresh_person_almanack_for_file,
)
from library.world_map_svg import (  # noqa: E402
    load_world_map_overlays,
    render_world_map_svg,
    world_map_zoom_sync_script,
)

ALMANACK_HEADERS = [
    "Rank",
    "Value",
    "Count",
    "Name",
    "Person ID",
    "Life",
    "Age",
    "Years",
    "Home",
    "Metric",
    "Context",
    "Evidence",
    "Source",
]
ALMANACK_SELECTED_HEADERS = [
    "Rank",
    "Value",
    "Count",
    "Name",
    "Person ID",
    "Life",
    "Age",
    "Years",
    "Home",
    "Context",
    "Evidence",
    "Source",
]
ALMANACK_EVIDENCE_HEADERS = [
    "Rank",
    "Year",
    "Source",
    "Source ID",
    "Role",
    "Contribution",
    "Summary",
    "Payload Path",
    "Related People",
]
ALMANACK_METRIC_CHOICES = ["All Metrics", *[label for label, _key in metric_definition_choices()]]
ALMANACK_METRIC_LABEL_TO_KEY = {label: key for label, key in metric_choices()}
ALMANACK_CATEGORY_CHOICES = metric_categories()
ALMANACK_SOURCE_CHOICES = ["Both", "Detailed", "Passive explicit"]
ALMANACK_RANK_MODE_CHOICES = [
    "Raw Value",
    "World Percentile",
    "Era Abnormality",
    "Regional Rank",
]


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
#almanack-table,
#almanack-table table,
#almanack-table .table-wrap,
#almanack-table .dataframe {
    font-size: 12px !important;
}
#almanack-table th,
#almanack-table td,
#almanack-table [role="columnheader"],
#almanack-table [role="gridcell"] {
    font-size: 12px !important;
    line-height: 1.2 !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
#almanack-table th:nth-child(1),
#almanack-table td:nth-child(1) {
    width: 44px !important;
    min-width: 44px !important;
}
#almanack-table th:nth-child(2),
#almanack-table td:nth-child(2),
#almanack-table th:nth-child(3),
#almanack-table td:nth-child(3) {
    width: 72px !important;
    min-width: 64px !important;
}
#almanack-table th:nth-child(4),
#almanack-table td:nth-child(4) {
    min-width: 150px !important;
}
#almanack-table th:nth-child(10),
#almanack-table td:nth-child(10),
#almanack-table th:nth-child(11),
#almanack-table td:nth-child(11),
#almanack-table th:nth-child(12),
#almanack-table td:nth-child(12) {
    max-width: 260px !important;
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
    --relationship-partner-bar: #6a7d3e;
    --relationship-paramour-bar: #a85f68;
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
    touch-action: none;
    cursor: grab;
}
.world-map-card svg:active {
    cursor: grabbing;
}
.map-controls {
    display: flex;
    gap: 8px;
    margin: 8px 0 10px;
}
.map-controls button {
    border: 1px solid var(--place-card-border);
    background: var(--place-card-bg);
    color: var(--place-text);
    border-radius: 6px;
    padding: 5px 10px;
    cursor: pointer;
}
.world-map-card [data-feature-id],
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
    --relationship-partner-bar: #9fc46b;
    --relationship-paramour-bar: #e38a96;
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
.detail-label[title] {
    cursor: help;
    text-decoration: underline dotted;
    text-underline-offset: 3px;
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
.subsection-title {
    margin: 10px 0 6px;
    color: var(--person-sheet-title) !important;
    font-size: 14px;
}
.consequence-section {
    border-top: 1px solid var(--person-sheet-rule);
    border-bottom: 1px solid var(--person-sheet-rule);
    padding: 2px 0 14px;
    margin: 4px 0 16px;
}
.consequence-summary {
    margin-bottom: 10px;
}
.consequence-groups {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 10px 14px;
}
.consequence-row strong {
    color: var(--person-sheet-title) !important;
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
.relation-compact {
    min-width: 0;
}
.history-summary {
    color: var(--person-sheet-muted) !important;
    margin: 0 0 8px;
}
.history-list {
    width: 100%;
}
.history-lifespan-grid {
    display: grid;
    grid-template-columns: minmax(92px, 150px) minmax(0, 1fr);
    column-gap: 8px;
    row-gap: 4px;
    width: 100%;
    max-width: 100%;
    min-width: 0;
    overflow-x: hidden;
    padding-bottom: 2px;
    box-sizing: border-box;
}
.history-axis-spacer {
    min-height: 40px;
}
.history-axis {
    position: relative;
    min-width: 0;
    min-height: 40px;
    border-bottom: 1px solid var(--person-sheet-border);
    overflow: hidden;
}
.history-axis-tick {
    position: absolute;
    bottom: 4px;
    transform: translateX(-50%);
    color: var(--person-sheet-muted) !important;
    font-size: 11px;
    line-height: 1;
    white-space: nowrap;
}
.history-axis-tick-up {
    bottom: calc(4px + 1em);
}
.history-axis-tick-edge-start {
    transform: translateX(0);
}
.history-axis-tick-edge-end {
    transform: translateX(-100%);
}
.history-row-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--person-sheet-text) !important;
    background: var(--person-sheet-relation-bg);
    border-left: 3px solid var(--person-sheet-accent);
    padding: 6px 8px;
    box-sizing: border-box;
}
.history-row-track {
    position: relative;
    min-width: 0;
    min-height: 30px;
    background: var(--person-sheet-card-bg);
    border-left: 1px solid var(--person-sheet-border);
    border-right: 1px solid var(--person-sheet-border);
    overflow: hidden;
}
.history-gridline {
    position: absolute;
    inset: 0 auto 0 0;
    width: 1px;
    background: var(--person-sheet-border);
    opacity: .72;
    pointer-events: none;
}
.history-bar {
    position: absolute;
    top: 8px;
    left: var(--history-bar-left);
    width: var(--history-bar-width);
    min-width: 4px;
    height: 14px;
    border-radius: 2px;
    background: var(--person-sheet-accent);
    box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .12);
    cursor: help;
}
.history-bar-partner {
    background: var(--relationship-partner-bar);
}
.history-bar-paramour {
    background: var(--relationship-paramour-bar);
}
.history-bar:hover,
.history-bar:focus {
    background: var(--person-sheet-link-hover);
    box-shadow: 0 2px 10px rgba(60, 45, 20, .22);
    outline: 2px solid rgba(47, 102, 122, .25);
    outline-offset: 2px;
    z-index: 1;
}
.event-card-title {
    display: block;
    color: var(--person-sheet-title) !important;
    font-size: 14px;
    line-height: 1.15;
    margin-bottom: 3px;
}
.event-card-body {
    display: block;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
}
.event-card :where(a, a.person-link),
.event-card-body :where(a, a.person-link),
.event-card-details :where(a, a.person-link) {
    padding: 0 !important;
}
.relationship-history-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin: 0 0 6px;
    color: var(--person-sheet-muted) !important;
    font-size: 12px;
}
.relationship-history-key {
    display: inline-flex;
    align-items: center;
    gap: 5px;
}
.relationship-history-swatch {
    width: 18px;
    height: 8px;
    border-radius: 2px;
    box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .16);
}
.relationship-history-swatch-partner {
    background: var(--relationship-partner-bar);
}
.relationship-history-swatch-paramour {
    background: var(--relationship-paramour-bar);
}
@media (max-width: 720px) {
    .history-lifespan-grid {
        grid-template-columns: minmax(76px, 110px) minmax(0, 1fr);
    }
}
.event-card-details {
    margin-top: 5px;
    color: var(--person-sheet-muted) !important;
    font-size: 12px;
}
.event-card-details summary {
    cursor: pointer;
    font-weight: 650;
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


def _sqlite_file_fingerprint(path: Path) -> str:
    pieces: list[str] = []
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        try:
            stat = candidate.stat()
        except OSError:
            pieces.append("0:0")
        else:
            pieces.append(f"{stat.st_mtime_ns}:{stat.st_size}")
    return "|".join(pieces)


@lru_cache(maxsize=16)
def _cached_world_map_geometry(
    world_id: str,
    cfg_path: str,
    _cfg_fingerprint: str,
    save_path: str,
    _save_fingerprint: str,
) -> WorldMapGeometry:
    return build_world_map_geometry(
        world=world_id,
        db_path=Path(cfg_path),
        save_db_path=Path(save_path),
    )


def render_world_map_html(
    world: str,
    include_overlays: bool = True,
    noisy_edges: bool = True,
    labels: bool = True,
    include_inactive_settlements: bool = False,
    include_roads: bool = True,
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
        bool(include_inactive_settlements),
        bool(include_roads),
        bool(noisy_edges),
        bool(labels),
        str(cfg),
        _sqlite_file_fingerprint(cfg),
        str(save),
        _sqlite_file_fingerprint(save),
    )


@lru_cache(maxsize=32)
def _render_world_map_html_cached(
    world_id: str,
    include_overlays: bool,
    include_inactive_settlements: bool,
    include_roads: bool,
    noisy_edges: bool,
    labels: bool,
    cfg_path: str,
    cfg_fingerprint: str,
    save_path: str,
    _save_fingerprint: str,
) -> str:
    cfg = Path(cfg_path)
    save = Path(save_path)
    try:
        geometry = _cached_world_map_geometry(
            world_id,
            str(cfg),
            cfg_fingerprint,
            str(save),
            _save_fingerprint,
        )
        overlays = (
            load_world_map_overlays(
                geometry=geometry,
                save_db_path=save,
                include_inactive_settlements=include_inactive_settlements,
                include_roads=include_roads,
            )
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
    if include_overlays and include_inactive_settlements:
        overlay_text = "active and inactive settlements plus polities"
    else:
        overlay_text = "active settlements and polities" if include_overlays else "base geography only"
    if include_overlays and include_roads:
        overlay_text = overlay_text.replace("settlements", "settlements, roads and sea lanes")
    if include_overlays:
        overlay_text = overlay_text.replace("polities", "polities and outlaw refuges")
    zoom_sync = world_map_zoom_sync_script("s")
    controls = (
        '<div class="map-controls">'
        '<button type="button" onclick="const s=this.closest(\'.world-map-card\').querySelector(\'svg\');'
        f'const v=s.viewBox.baseVal;v.x+=v.width*.1;v.y+=v.height*.1;v.width*=.8;v.height*=.8;{zoom_sync}event.stopPropagation();">Zoom In</button>'
        '<button type="button" onclick="const s=this.closest(\'.world-map-card\').querySelector(\'svg\');'
        f'const v=s.viewBox.baseVal;v.x-=v.width*.125;v.y-=v.height*.125;v.width*=1.25;v.height*=1.25;{zoom_sync}event.stopPropagation();">Zoom Out</button>'
        '<button type="button" onclick="const s=this.closest(\'.world-map-card\').querySelector(\'svg\');'
        'const b=(s.dataset.originalViewbox||\'0 0 1200 800\').split(\' \').map(Number);'
        f's.setAttribute(\'viewBox\',b.join(\' \'));{zoom_sync}event.stopPropagation();">Reset</button>'
        '</div>'
    )
    return (
        f'<div class="place-sheet world-map-card" onclick="{_world_map_click_onclick()}">'
        f"<h2>{html.escape(world_id)} World Map</h2>"
        f'<p class="place-muted">Generated polygon geography; showing {html.escape(overlay_text)}. '
        "Click a region, settlement, outlaw refuge, or named feature to open its detail sheet. Use the mouse wheel or drag to zoom and pan.</p>"
        f"{controls}"
        f"{svg}"
        "</div>"
    )


def _world_map_click_onclick() -> str:
    return html.escape(
        (
            "const target=event.target;"
            "const svg=target&&target.closest?target.closest('svg'):null;"
            "if(svg&&svg.dataset.dragged==='1'){svg.dataset.dragged='0';return true;}"
            "if(!target||!target.closest){return true;}"
            "const town=target.closest('[data-settlement-id]');"
            "const refuge=town?null:target.closest('[data-outlaw-refuge-id]');"
            "const feature=(town||refuge)?null:target.closest('[data-feature-id]');"
            "const route=(town||refuge||feature)?null:target.closest('[data-map-layer]');"
            "const region=town||refuge||feature||route?null:target.closest('[data-region-id],[data-region-label]');"
            "const routeValue=route?{view:'Map Routes',layer:route.dataset.mapLayer||'',river_id:route.dataset.riverId||'',class_name:route.getAttribute('class')||'',from_settlement_id:route.dataset.roadFromSettlementId||route.dataset.seaRouteFromSettlementId||'',to_settlement_id:route.dataset.roadToSettlementId||route.dataset.seaRouteToSettlementId||'',regions:route.dataset.seaRouteRegions||'',usage:route.dataset.roadUsage||route.dataset.seaRouteUsage||'',actual_usage:route.dataset.roadActual||route.dataset.seaRouteActual||'',implied_usage:route.dataset.roadImplied||route.dataset.seaRouteImplied||''}:null;"
            "if(!region&&!route&&!refuge){return true;}"
            "const id=town?town.dataset.settlementId:(refuge?refuge.dataset.outlawRefugeId:(feature?feature.dataset.featureId:(route?(routeValue.river_id||((routeValue.from_settlement_id||'?')+'->'+(routeValue.to_settlement_id||'?'))):(region.dataset.regionId||region.dataset.regionLabel))));"
            "if(!id){return true;}"
            "event.preventDefault();event.stopPropagation();"
            "const input=document.querySelector('#map-open-selection textarea,#map-open-selection input');"
            "const button=document.querySelector('#map-open-button button,#map-open-button');"
            "if(input&&button){"
            "const value=JSON.stringify(route?Object.assign({id:id},routeValue):(refuge?{view:'Outlaw Refuges',id:id,region_id:refuge.dataset.regionId||'',name:refuge.dataset.outlawRefugeName||'',near_settlement_id:refuge.dataset.nearSettlementId||''}:(feature?{view:'Features',id:id,region_id:feature.dataset.regionId||'',name:feature.dataset.featureName||'',kind:feature.dataset.featureKind||'',etymology:feature.dataset.featureEtymology||'',named:feature.dataset.featureNamed||'0'}:{view:town?'Towns':'Regions',id:id})));"
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


def _has_relation(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "select 1 from sqlite_master where type in ('table', 'view') and name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _place_read_relation(con: sqlite3.Connection, table: str) -> str:
    readable = f"{table}_readable"
    return readable if _has_relation(con, readable) else table


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


def _display_year_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_year(value: object, *, unknown_text: str = "unknown") -> str:
    if value in (None, ""):
        return unknown_text
    year = _display_year_int(value)
    if year is None:
        return str(value)
    if year < 0:
        return f"{abs(year)} BCE"
    return str(year)


def _format_year_blank(value: object) -> str:
    return "" if value in (None, "") else _format_year(value)


def _format_year_span(start_year: object, end_year: object = None) -> str:
    if start_year in (None, "") and end_year in (None, ""):
        return "Unknown years"
    if end_year in (None, ""):
        return _format_year(start_year)
    if start_year in (None, ""):
        return f"until {_format_year(end_year)}"
    return f"{_format_year(start_year)}-{_format_year(end_year)}"


def _dataframe_display_value(header: str, value: object) -> object:
    if header in {"Year", "Born", "Died", "Founded"}:
        return _format_year_blank(value)
    return value


def _dataframe(rows: Iterable[sqlite3.Row | dict[str, object]], headers: list[str], *, key: str | None = None) -> gr.Dataframe:
    values = [
        [
            _dataframe_display_value(
                header,
                row.get(header, "") if isinstance(row, dict) else row[header],
            )
            for header in headers
        ]
        for row in rows
    ]
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


def _history_event_type_filter(event_type_text: object) -> set[str] | None:
    text = str(event_type_text or "").strip()
    if not text or text.lower() == "all":
        return None
    parts = [part.strip() for part in re.split(r"[,;\s]+", text) if part.strip()]
    return set(parts) if parts else None


def _history_admin_row(summary: EventAdminSummary) -> dict[str, object]:
    return {
        "Year": summary.sim_year,
        "Event ID": summary.event_id,
        "Record ID": "",
        "Event Type": summary.event_type,
        "Visibility": "admin_truth",
        "Public Stage": "admin_truth",
        "Record Type": "factual_event",
        "Template": summary.template_key,
        "Prose": summary.prose,
        "Admin Summary": summary.prose,
    }


def _history_record_row(row: EventRecordProse) -> dict[str, object]:
    return {
        "Year": row.sim_year,
        "Event ID": row.event_id,
        "Record ID": row.record_id,
        "Event Type": row.event_type,
        "Visibility": row.visibility_state,
        "Public Stage": row.public_knowledge_stage,
        "Record Type": row.record_type,
        "Template": row.prose_variant_key,
        "Prose": row.public_prose,
        "Admin Summary": row.admin_summary,
    }


def _history_lens_empty_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=HISTORY_LENS_HEADERS)


def _rediscovery_detail_empty_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=REDISCOVERY_DETAIL_HEADERS)


def _history_empty_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=HISTORY_BROWSER_HEADERS)


def _history_summary_empty_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=HISTORY_SUMMARY_HEADERS)


def _tracked_incident_summary_rows(report: object) -> list[dict[str, object]]:
    counts = {row.keys[0]: row.count for row in report.event_counts_by_type}
    return [
        {
            "Section": "Tracked Incidents",
            "Key": event_type,
            "Count": int(counts.get(event_type, 0)),
            "Value": "",
        }
        for event_type in sorted(INCIDENT_EVENT_TYPES)
    ]


def _history_summary_rows(report: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {
            "Section": "Overview",
            "Key": "total_events",
            "Count": int(report.total_events),
            "Value": "",
        },
        {
            "Section": "Overview",
            "Key": "total_records",
            "Count": int(report.total_records),
            "Value": "",
        },
    ]
    if report.save_size_bytes is not None:
        rows.append(
            {
                "Section": "Overview",
                "Key": "save_size_bytes",
                "Count": int(report.save_size_bytes),
                "Value": "",
            }
        )
    rows.extend(_tracked_incident_summary_rows(report))
    for row in sorted(report.event_counts_by_type, key=lambda item: (-item.count, item.keys)):
        rows.append(
            {
                "Section": "Event Types",
                "Key": row.keys[0],
                "Count": int(row.count),
                "Value": "",
            }
        )
    for row in sorted(report.visibility_counts, key=lambda item: (item.keys, item.count)):
        rows.append(
            {
                "Section": "Visibility",
                "Key": " / ".join(row.keys),
                "Count": int(row.count),
                "Value": "",
            }
        )
    for metric in sorted(report.metric_summaries, key=lambda item: (item.event_type, item.metric)):
        rows.append(
            {
                "Section": "Metrics",
                "Key": f"{metric.event_type} {metric.metric}",
                "Count": int(metric.count),
                "Value": (
                    f"avg={metric.average:.4f} "
                    f"min={metric.minimum:.4f} max={metric.maximum:.4f}"
                ),
            }
        )
    for row in sorted(
        getattr(report, "consequence_counts", ()),
        key=lambda item: (item.keys, item.count),
    ):
        rows.append(
            {
                "Section": "Consequences",
                "Key": " / ".join(row.keys),
                "Count": int(row.count),
                "Value": "",
            }
        )
    for metric in sorted(
        getattr(report, "consequence_metric_summaries", ()),
        key=lambda item: (item.section, item.key, item.metric),
    ):
        rows.append(
            {
                "Section": "Consequence Metrics",
                "Key": f"{metric.section} / {metric.key} / {metric.metric}",
                "Count": int(metric.count),
                "Value": (
                    f"avg={metric.average:.4f} "
                    f"min={metric.minimum:.4f} max={metric.maximum:.4f}"
                ),
            }
        )
    return rows


def load_history_summary(world: str) -> tuple[gr.Dataframe, str]:
    if not world:
        return _history_summary_empty_frame(), "Choose a world."
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _history_summary_empty_frame(), f"{path} is missing. Run a simulation first."

    with _connect_readonly(path) as con:
        if not _has_relation(con, "simulation_events_readable"):
            return (
                _history_summary_empty_frame(),
                "No simulation_events_readable view found. Ensure or rebuild the save schema.",
            )
        if not _has_relation(con, "simulation_event_records_readable"):
            return (
                _history_summary_empty_frame(),
                "No simulation_event_records_readable view found. Ensure or rebuild the save schema.",
            )
        saved_world = _resolve_saved_world(con, world)
        report = build_event_history_report(con, save_path=path, sample_limit=0)

    rows = _history_summary_rows(report)
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(rows)} history summary rows "
        f"| events={report.total_events} records={report.total_records}{saved_world_note}."
    )
    return _dataframe(rows, HISTORY_SUMMARY_HEADERS), status


def _load_history_records_for_view(
    con: sqlite3.Connection,
    view: str,
    *,
    event_types: set[str] | None,
    search: str,
    limit: int,
    offset: int,
) -> list[EventRecordProse]:
    if view == "Public Chronicle":
        return load_public_chronicle_prose(
            con,
            event_types=event_types,
            search=search,
            limit=limit,
            offset=offset,
        )
    if view == "Public Unknown":
        return load_public_unknown_prose(
            con,
            event_types=event_types,
            search=search,
            limit=limit,
            offset=offset,
        )
    if view == "Public Rumors":
        return load_public_rumor_prose(
            con,
            event_types=event_types,
            search=search,
            limit=limit,
            offset=offset,
        )
    if view == "Public Known":
        return load_public_known_prose(
            con,
            event_types=event_types,
            search=search,
            limit=limit,
            offset=offset,
        )
    if view == "Lost History":
        return load_event_record_prose_rows(
            con,
            visibility_states={"lost"},
            event_types=event_types,
            search=search,
            limit=limit,
            offset=offset,
        )
    if view == "Rediscoveries":
        fetch_limit = min(MAX_LIMIT, limit + offset)
        rows = load_event_record_prose_rows(
            con,
            visibility_states={"rediscovered"},
            event_types=event_types,
            search=search,
            limit=fetch_limit,
            offset=0,
        )
        if event_types is None or "event_rediscovered" in event_types:
            rows.extend(
                load_event_record_prose_rows(
                    con,
                    event_types={"event_rediscovered"},
                    search=search,
                    limit=fetch_limit,
                    offset=0,
                )
            )
        rows.sort(key=lambda row: (row.sim_year, row.event_id, row.record_id))
        return rows[offset : offset + limit]
    return []


def load_history_browser(
    world: str,
    view: str,
    event_type_text: object,
    search: str,
    limit: object,
    offset: object,
) -> tuple[gr.Dataframe, str]:
    selected = HISTORY_VIEW_ALIASES.get(str(view or ""), str(view or ""))
    selected = selected if selected in HISTORY_VIEW_CHOICES else "Public Chronicle"
    if not world:
        return _history_empty_frame(), "Choose a world."
    row_limit = _safe_int(limit, 100, 1, 500)
    row_offset = _safe_int(offset, 0, 0, 10_000_000)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _history_empty_frame(), f"{path} is missing. Run a simulation first."

    event_types = _history_event_type_filter(event_type_text)
    with _connect_readonly(path) as con:
        if not _has_relation(con, "simulation_events_readable"):
            return (
                _history_empty_frame(),
                "No simulation_events_readable view found. Ensure or rebuild the save schema.",
            )
        if selected != "Admin Truth" and not _has_relation(
            con, "simulation_event_records_readable"
        ):
            return (
                _history_empty_frame(),
                "No simulation_event_records_readable view found. Ensure or rebuild the save schema.",
            )
        saved_world = _resolve_saved_world(con, world)
        if selected == "Admin Truth":
            summaries = load_admin_event_summaries(
                con,
                event_types=event_types,
                search=search,
                limit=row_limit,
                offset=row_offset,
            )
            values = [_history_admin_row(summary) for summary in summaries]
        else:
            record_rows = _load_history_records_for_view(
                con,
                selected,
                event_types=event_types,
                search=search,
                limit=row_limit,
                offset=row_offset,
            )
            values = [_history_record_row(row) for row in record_rows]

    filter_bits: list[str] = [selected]
    if event_types:
        filter_bits.append("types=" + ", ".join(sorted(event_types)))
    if search:
        filter_bits.append(f"search={search!r}")
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} history rows at offset {row_offset} "
        f"| filters: {', '.join(filter_bits)}{saved_world_note}."
    )
    return _dataframe(values, HISTORY_BROWSER_HEADERS), status


def _coerce_int_or_none(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _display_token(value: object) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(_display_token(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(
            f"{_display_token(key)}: {_display_token(val)}"
            for key, val in sorted(value.items(), key=lambda item: str(item[0]))
        )
    return str(value).replace("_", " ")


def _display_title(value: object) -> str:
    return " ".join(part.capitalize() for part in _display_token(value).split())


def _history_settlement_label(
    con: sqlite3.Connection,
    world: str,
    settlement_id: object,
) -> str:
    sid = str(settlement_id or "").strip()
    if not sid:
        return ""
    label = _settlement_name(con, world, sid) if _has_table(con, "simulation_settlements") else sid
    if label and label != sid:
        return label
    marker = ":settlement:"
    if marker in sid:
        region, slot = sid.split(marker, 1)
        return f"Settlement {slot} of {_display_title(region)}"
    return _display_title(sid)


def _history_region_label(
    con: sqlite3.Connection,
    world: str,
    region_id: object,
) -> str:
    rid = str(region_id or "").strip()
    if not rid:
        return ""
    region_table = _place_read_relation(con, "simulation_regions")
    if _has_relation(con, region_table):
        columns = _table_columns(con, region_table)
        if "region_id" in columns and "region_display_name" in columns:
            where_sql = "region_id = ?"
            params: tuple[object, ...] = (rid,)
            if "world" in columns:
                where_sql = "world = ? and region_id = ?"
                params = (world, rid)
            row = con.execute(
                f"""
                SELECT region_display_name
                FROM {_quote_identifier(region_table)}
                WHERE {where_sql}
                """,
                params,
            ).fetchone()
            if row and str(row["region_display_name"] or "").strip():
                return str(row["region_display_name"]).strip()
    if _has_relation(con, "simulation_regions_readable") and region_table != "simulation_regions_readable":
        row = con.execute(
            """
            SELECT region_display_name
            FROM simulation_regions_readable
            WHERE region_id = ?
            """,
            (rid,),
        ).fetchone()
        if row and str(row["region_display_name"] or "").strip():
            return str(row["region_display_name"]).strip()
    config_name = _config_region_display_name(world, rid)
    return config_name or _display_title(rid)


def _history_polity_label(
    con: sqlite3.Connection,
    world: str,
    polity_id: object,
) -> str:
    pid = _coerce_int_or_none(polity_id)
    if pid is None:
        return str(polity_id or "").strip()
    if _has_table(con, "simulation_polities"):
        where, params = _world_where(con, "simulation_polities", world)
        row = con.execute(
            f"""
            SELECT name, polity_type_id
            FROM simulation_polities
            WHERE {where} AND polity_id = ?
            """,
            (*params, pid),
        ).fetchone()
        if row:
            name = str(row["name"] or "").strip()
            if name:
                return f"{name} (#{pid})"
            ptype = str(row["polity_type_id"] or "").strip()
            if ptype:
                return f"{_display_title(ptype)} #{pid}"
    return f"Polity #{pid}"


def _history_sql_in_placeholders(values: Iterable[object]) -> tuple[str, list[object]]:
    clean = list(values)
    return ", ".join("?" for _ in clean), clean


def _history_event_ids_from_person_ids(
    con: sqlite3.Connection,
    world: str,
    person_ids: set[int],
) -> set[int]:
    if not person_ids or not _has_table(con, "simulation_events"):
        return set()
    event_ids: set[int] = set()
    placeholders, person_params = _history_sql_in_placeholders(sorted(person_ids))
    events_where, events_params = _world_where(con, "simulation_events", world)

    if _has_table(con, "simulation_event_people"):
        rows = con.execute(
            f"""
            SELECT DISTINCT ep.event_id
            FROM simulation_event_people ep
            JOIN simulation_events e ON e.id = ep.event_id
            WHERE ep.person_id IN ({placeholders})
              AND {events_where}
            """,
            (*person_params, *events_params),
        ).fetchall()
        event_ids.update(int(row["event_id"]) for row in rows if row["event_id"] is not None)

    scalar_fields = (
        "person_id",
        "person_a_id",
        "person_b_id",
        "child_id",
        "victim_person_id",
        "killer_person_id",
        "perpetrator_person_id",
        "target_person_id",
        "accused_person_id",
        "paramour_person_id",
        "benefactor_person_id",
        "beneficiary_person_id",
        "creator_person_id",
        "patron_person_id",
        "source_person_id",
        "purseholder_person_id",
        "moved_person_id",
        "holder_person_id",
        "previous_holder_id",
        "prior_head_person_id",
        "claimant_id",
        "related_child_id",
    )
    array_fields = (
        "household_member_ids",
        "dependent_minor_ids",
        "moved_person_ids",
        "witness_person_ids",
        "betrayed_partner_person_ids",
        "related_child_ids",
    )
    scalar_clauses: list[str] = []
    scalar_params: list[object] = []
    for field in scalar_fields:
        scalar_clauses.append(f"json_extract(payload_json, '$.{field}') IN ({placeholders})")
        scalar_params.extend(person_params)
    for field in array_fields:
        scalar_clauses.append(
            "EXISTS ("
            f"SELECT 1 FROM json_each(payload_json, '$.{field}') "
            f"WHERE json_each.value IN ({placeholders})"
            ")"
        )
        scalar_params.extend(person_params)
    rows = con.execute(
        f"""
        SELECT DISTINCT id
        FROM simulation_events
        WHERE {events_where}
          AND ({' OR '.join(scalar_clauses)})
        """,
        (*events_params, *scalar_params),
    ).fetchall()
    event_ids.update(int(row["id"]) for row in rows if row["id"] is not None)

    if _has_relation(con, "simulation_event_records_readable"):
        record_clauses = [
            f"source_person_id IN ({placeholders})",
            f"public_actor_person_id IN ({placeholders})",
            f"public_victim_person_id IN ({placeholders})",
        ]
        record_params: list[object] = []
        for _ in record_clauses:
            record_params.extend(person_params)
        rows = con.execute(
            f"""
            SELECT DISTINCT r.event_id
            FROM simulation_event_records_readable r
            JOIN simulation_events e ON e.id = r.event_id
            WHERE {events_where}
              AND ({' OR '.join(record_clauses)})
            """,
            (*events_params, *record_params),
        ).fetchall()
        event_ids.update(int(row["event_id"]) for row in rows if row["event_id"] is not None)
    return event_ids


def _history_event_ids_from_settlement(
    con: sqlite3.Connection,
    world: str,
    settlement_id: str,
) -> set[int]:
    sid = str(settlement_id or "").strip()
    if not sid or not _has_table(con, "simulation_events"):
        return set()
    event_ids: set[int] = set()
    events_where, events_params = _world_where(con, "simulation_events", world)
    settlement_fields = (
        "settlement_id",
        "birthplace_settlement_id",
        "current_settlement_id",
        "from_settlement_id",
        "to_settlement_id",
        "preserving_settlement_id",
        "capital_settlement_id",
        "scope_settlement_id",
        "target_settlement_id",
    )
    clauses = [f"json_extract(payload_json, '$.{field}') = ?" for field in settlement_fields]
    readable_clauses = [
        f"json_extract(er.payload_json, '$.{field}') = ?"
        for field in settlement_fields
    ]
    params: list[object] = [sid for _ in settlement_fields]
    if _has_relation(con, "simulation_events_readable"):
        rows = con.execute(
            f"""
            SELECT DISTINCT er.id
            FROM simulation_events_readable er
            JOIN simulation_events e ON e.id = er.id
            WHERE {events_where}
              AND (
                er.settlement_id = ?
                OR {' OR '.join(readable_clauses)}
              )
            """,
            (*events_params, sid, *params),
        ).fetchall()
    else:
        rows = con.execute(
            f"""
            SELECT DISTINCT id
            FROM simulation_events
            WHERE {events_where}
              AND ({' OR '.join(clauses)})
            """,
            (*events_params, *params),
        ).fetchall()
    event_ids.update(int(row[0]) for row in rows if row[0] is not None)

    if _has_relation(con, "simulation_event_moves_readable"):
        rows = con.execute(
            """
            SELECT DISTINCT event_id
            FROM simulation_event_moves_readable
            WHERE from_settlement_id = ? OR to_settlement_id = ?
            """,
            (sid, sid),
        ).fetchall()
        event_ids.update(int(row["event_id"]) for row in rows if row["event_id"] is not None)

    if _has_relation(con, "simulation_event_records_readable"):
        rows = con.execute(
            """
            SELECT DISTINCT event_id
            FROM simulation_event_records_readable
            WHERE preserving_settlement_id = ?
            """,
            (sid,),
        ).fetchall()
        event_ids.update(int(row["event_id"]) for row in rows if row["event_id"] is not None)
    return event_ids


def _history_event_ids_from_region(
    con: sqlite3.Connection,
    world: str,
    region_id: str,
) -> set[int]:
    rid = str(region_id or "").strip()
    if not rid or not _has_table(con, "simulation_events"):
        return set()
    event_ids: set[int] = set()
    events_where, events_params = _world_where(con, "simulation_events", world)
    region_fields = (
        "region_id",
        "birthplace_region_id",
        "current_region_id",
        "from_region_id",
        "to_region_id",
        "preserving_region_id",
        "target_region_id",
    )
    clauses = [f"json_extract(payload_json, '$.{field}') = ?" for field in region_fields]
    readable_clauses = [
        f"json_extract(er.payload_json, '$.{field}') = ?"
        for field in region_fields
    ]
    params: list[object] = [rid for _ in region_fields]
    if _has_relation(con, "simulation_events_readable"):
        rows = con.execute(
            f"""
            SELECT DISTINCT er.id
            FROM simulation_events_readable er
            JOIN simulation_events e ON e.id = er.id
            WHERE {events_where}
              AND (
                er.region_id = ?
                OR {' OR '.join(readable_clauses)}
              )
            """,
            (*events_params, rid, *params),
        ).fetchall()
    else:
        rows = con.execute(
            f"""
            SELECT DISTINCT id
            FROM simulation_events
            WHERE {events_where}
              AND ({' OR '.join(clauses)})
            """,
            (*events_params, *params),
        ).fetchall()
    event_ids.update(int(row[0]) for row in rows if row[0] is not None)

    if _has_relation(con, "simulation_event_moves_readable"):
        rows = con.execute(
            """
            SELECT DISTINCT event_id
            FROM simulation_event_moves_readable
            WHERE from_region_id = ? OR to_region_id = ?
            """,
            (rid, rid),
        ).fetchall()
        event_ids.update(int(row["event_id"]) for row in rows if row["event_id"] is not None)

    if _has_relation(con, "simulation_event_records_readable"):
        rows = con.execute(
            """
            SELECT DISTINCT event_id
            FROM simulation_event_records_readable
            WHERE preserving_region_id = ?
            """,
            (rid,),
        ).fetchall()
        event_ids.update(int(row["event_id"]) for row in rows if row["event_id"] is not None)
    return event_ids


def _history_event_ids_from_polity(
    con: sqlite3.Connection,
    world: str,
    polity_id: int,
) -> set[int]:
    if not _has_table(con, "simulation_events"):
        return set()
    event_ids: set[int] = set()
    events_where, events_params = _world_where(con, "simulation_events", world)
    polity_fields = (
        "polity_id",
        "parent_polity_id",
        "child_polity_id",
        "attacker_polity_id",
        "defender_polity_id",
        "prior_polity_id",
        "new_polity_id",
        "suzerain_polity_id",
        "vassal_polity_id",
        "polity_a_id",
        "polity_b_id",
    )
    clauses = [f"json_extract(payload_json, '$.{field}') = ?" for field in polity_fields]
    params: list[object] = [polity_id for _ in polity_fields]
    rows = con.execute(
        f"""
        SELECT DISTINCT id
        FROM simulation_events
        WHERE {events_where}
          AND ({' OR '.join(clauses)})
        """,
        (*events_params, *params),
    ).fetchall()
    event_ids.update(int(row["id"]) for row in rows if row["id"] is not None)

    campaign_ids: set[int] = set()
    if _has_table(con, "simulation_campaigns"):
        rows = con.execute(
            """
            SELECT campaign_id
            FROM simulation_campaigns
            WHERE attacker_polity_id = ? OR defender_polity_id = ?
            """,
            (polity_id, polity_id),
        ).fetchall()
        campaign_ids.update(int(row["campaign_id"]) for row in rows if row["campaign_id"] is not None)
    if campaign_ids:
        placeholders, campaign_params = _history_sql_in_placeholders(sorted(campaign_ids))
        rows = con.execute(
            f"""
            SELECT DISTINCT id
            FROM simulation_events
            WHERE {events_where}
              AND json_extract(payload_json, '$.campaign_id') IN ({placeholders})
            """,
            (*events_params, *campaign_params),
        ).fetchall()
        event_ids.update(int(row["id"]) for row in rows if row["id"] is not None)
    return event_ids


def _person_current_settlement_id(
    con: sqlite3.Connection,
    row: sqlite3.Row | None,
    person: dict[str, object],
) -> str:
    value = person.get("current_settlement_id") or person.get("birthplace_settlement_id")
    if value:
        return str(value)
    if row is not None and "current_settlement_id" in row.keys() and row["current_settlement_id"]:
        return str(row["current_settlement_id"])
    key = None
    if row is not None and "current_settlement_key" in row.keys():
        key = row["current_settlement_key"]
    if key is None and row is not None and "birthplace_settlement_key" in row.keys():
        key = row["birthplace_settlement_key"]
    if key is not None and _has_table(con, "simulation_settlement_lookup"):
        lookup = con.execute(
            "SELECT settlement_id FROM simulation_settlement_lookup WHERE settlement_key = ?",
            (key,),
        ).fetchone()
        if lookup and lookup["settlement_id"]:
            return str(lookup["settlement_id"])
    return ""


def _history_family_person_ids(
    con: sqlite3.Connection,
    world: str,
    focus_person_id: int,
) -> tuple[set[int], str]:
    row, person = _lookup_person(con, world, focus_person_id)
    if not row:
        return set(), f"person {focus_person_id}"
    ids = {focus_person_id}
    for key in ("father_id", "mother_id"):
        if key in row.keys():
            related = _coerce_int_or_none(row[key])
            if related is not None:
                ids.add(related)
    for key in ("partner_person_id", "paramour_person_id"):
        related = _coerce_int_or_none(person.get(key))
        if related is not None:
            ids.add(related)
    if _has_table(con, "simulation_people"):
        people_where, people_params = _world_where(con, "simulation_people", world)
        rows = con.execute(
            f"""
            SELECT person_id
            FROM simulation_people
            WHERE {people_where}
              AND (father_id = ? OR mother_id = ?)
            """,
            (*people_params, focus_person_id, focus_person_id),
        ).fetchall()
        ids.update(int(child["person_id"]) for child in rows if child["person_id"] is not None)
    return ids, _person_name(person)


def _history_household_person_ids(
    con: sqlite3.Connection,
    world: str,
    focus_person_id: int,
) -> tuple[set[int], str]:
    row, person = _lookup_person(con, world, focus_person_id)
    if not row:
        return set(), f"person {focus_person_id}"
    focus_home = _person_current_settlement_id(con, row, person)
    family_ids, focus_name = _history_family_person_ids(con, world, focus_person_id)
    if not focus_home:
        return family_ids or {focus_person_id}, focus_name
    household_ids: set[int] = {focus_person_id}
    for pid in family_ids:
        relative_row, relative = _lookup_person(con, world, pid)
        if relative_row and _person_current_settlement_id(con, relative_row, relative) == focus_home:
            household_ids.add(pid)
    return household_ids, focus_name


def _history_lens_event_ids(
    con: sqlite3.Connection,
    world: str,
    lens: str,
    focus: object,
) -> tuple[set[int], set[int], str, str]:
    selected = lens if lens in HISTORY_LENS_CHOICES else "Person"
    raw_focus = str(focus or "").strip()
    if selected == "Settlement":
        if not raw_focus:
            return set(), set(), "Settlement", "Enter a settlement id."
        label = _history_settlement_label(con, world, raw_focus)
        return (
            _history_event_ids_from_settlement(con, world, raw_focus),
            set(),
            f"Settlement: {label}",
            "",
        )
    if selected == "Region":
        if not raw_focus:
            return set(), set(), "Region", "Enter a region id."
        label = _history_region_label(con, world, raw_focus)
        return (
            _history_event_ids_from_region(con, world, raw_focus),
            set(),
            f"Region: {label}",
            "",
        )
    if selected == "Polity":
        focus_polity_id = _coerce_int_or_none(raw_focus)
        if focus_polity_id is None:
            return set(), set(), "Polity", "Enter a polity id."
        label = _history_polity_label(con, world, focus_polity_id)
        return (
            _history_event_ids_from_polity(con, world, focus_polity_id),
            set(),
            f"Polity: {label}",
            "",
        )

    focus_person_id = _coerce_int_or_none(raw_focus)
    if focus_person_id is None:
        return set(), set(), selected, "Enter a person id."
    if selected == "Person":
        row, person = _lookup_person(con, world, focus_person_id)
        if not row:
            return set(), set(), f"Person {focus_person_id}", "No matching person."
        person_ids = {focus_person_id}
        label = f"Person: {_person_name(person)} (#{focus_person_id})"
    elif selected == "Family":
        person_ids, focus_name = _history_family_person_ids(con, world, focus_person_id)
        if not person_ids:
            return set(), set(), f"Family of person {focus_person_id}", "No matching person."
        label = f"Family of {focus_name} (#{focus_person_id}, {len(person_ids)} people)"
    else:
        person_ids, focus_name = _history_household_person_ids(con, world, focus_person_id)
        if not person_ids:
            return set(), set(), f"Household of person {focus_person_id}", "No matching person."
        label = f"Household of {focus_name} (#{focus_person_id}, {len(person_ids)} people)"
    return _history_event_ids_from_person_ids(con, world, person_ids), person_ids, label, ""


def _history_roles_for_events(
    con: sqlite3.Connection,
    event_ids: set[int],
    person_ids: set[int],
) -> dict[int, str]:
    if not event_ids or not person_ids or not _has_table(con, "simulation_event_people"):
        return {}
    event_placeholders, event_params = _history_sql_in_placeholders(sorted(event_ids))
    person_placeholders, person_params = _history_sql_in_placeholders(sorted(person_ids))
    rows = con.execute(
        f"""
        SELECT event_id, person_id, role
        FROM simulation_event_people
        WHERE event_id IN ({event_placeholders})
          AND person_id IN ({person_placeholders})
        ORDER BY event_id, person_id, role
        """,
        (*event_params, *person_params),
    ).fetchall()
    grouped: dict[int, list[str]] = {}
    for row in rows:
        event_id = int(row["event_id"])
        role = str(row["role"] or "participant").replace("_", " ")
        grouped.setdefault(event_id, []).append(f"{role} #{row['person_id']}")
    return {event_id: ", ".join(parts) for event_id, parts in grouped.items()}


def _history_lens_row(
    base: dict[str, object],
    *,
    lens_label: str,
    role: str,
) -> dict[str, object]:
    return {
        "Year": base.get("Year", ""),
        "Event ID": base.get("Event ID", ""),
        "Record ID": base.get("Record ID", ""),
        "Lens": base.get("Visibility", ""),
        "Focus": lens_label,
        "Event Type": base.get("Event Type", ""),
        "Visibility": base.get("Visibility", ""),
        "Public Stage": base.get("Public Stage", ""),
        "Record Type": base.get("Record Type", ""),
        "Role": role,
        "Prose": base.get("Prose", ""),
        "Admin Summary": base.get("Admin Summary", ""),
    }


def load_history_lens(
    world: str,
    lens: str,
    focus: object,
    event_type_text: object,
    search: str,
    limit: object,
    offset: object,
) -> tuple[gr.Dataframe, str]:
    selected = lens if str(lens or "") in HISTORY_LENS_CHOICES else "Person"
    if not world:
        return _history_lens_empty_frame(), "Choose a world."
    row_limit = _safe_int(limit, 100, 1, 500)
    row_offset = _safe_int(offset, 0, 0, 10_000_000)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _history_lens_empty_frame(), f"{path} is missing. Run a simulation first."

    event_types = _history_event_type_filter(event_type_text)
    with _connect_readonly(path) as con:
        if not _has_relation(con, "simulation_events_readable"):
            return (
                _history_lens_empty_frame(),
                "No simulation_events_readable view found. Ensure or rebuild the save schema.",
            )
        if not _has_relation(con, "simulation_event_records_readable"):
            return (
                _history_lens_empty_frame(),
                "No simulation_event_records_readable view found. Ensure or rebuild the save schema.",
            )
        saved_world = _resolve_saved_world(con, world)
        event_ids, person_ids, lens_label, warning = _history_lens_event_ids(
            con, saved_world, selected, focus
        )
        if warning:
            return _history_lens_empty_frame(), warning
        if not event_ids:
            return (
                _history_lens_empty_frame(),
                f"{path.name}: no events matched {lens_label}.",
            )
        role_by_event = _history_roles_for_events(con, event_ids, person_ids)
        fetch_limit = min(MAX_LIMIT, max(row_limit + row_offset, row_limit) * 4)
        place_chronicle_lens = selected in {"Settlement", "Region", "Polity"}
        admin_rows = []
        if not place_chronicle_lens:
            admin_rows = [
                _history_lens_row(
                    _history_admin_row(summary),
                    lens_label=lens_label,
                    role=role_by_event.get(summary.event_id, "matched event"),
                )
                for summary in load_admin_event_summaries(
                    con,
                    event_ids=event_ids,
                    event_types=event_types,
                    search=search,
                    limit=fetch_limit,
                    offset=0,
                )
            ]
        record_loader = load_public_chronicle_prose if place_chronicle_lens else load_event_record_prose_rows
        record_rows = [
            _history_lens_row(
                _history_record_row(record),
                lens_label=lens_label,
                role=role_by_event.get(
                    record.event_id,
                    "visible memory" if place_chronicle_lens else "matched event",
                ),
            )
            for record in record_loader(
                con,
                event_ids=event_ids,
                event_types=event_types,
                search=search,
                limit=fetch_limit,
                offset=0,
            )
        ]

    rows = sorted(
        [*admin_rows, *record_rows],
        key=lambda row: (
            int(row["Year"] or 0),
            int(row["Event ID"] or 0),
            str(row["Visibility"]),
            int(row["Record ID"] or 0),
        ),
    )
    values = rows[row_offset : row_offset + row_limit]
    filter_bits: list[str] = [selected, lens_label]
    if event_types:
        filter_bits.append("types=" + ", ".join(sorted(event_types)))
    if search:
        filter_bits.append(f"search={search!r}")
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {len(rows)} lens rows at offset {row_offset} "
        f"| filters: {', '.join(filter_bits)}{saved_world_note}."
    )
    return _dataframe(values, HISTORY_LENS_HEADERS), status


def _rediscovery_distortion_text(raw: object) -> str:
    distortion = _load_json_object(raw)
    if not distortion:
        return "None recorded"
    parts = []
    for key, value in sorted(distortion.items(), key=lambda item: str(item[0])):
        parts.append(f"{_display_token(key)}: {_display_token(value)}")
    return "; ".join(parts)


def _rediscovery_source_text(
    con: sqlite3.Connection,
    world: str,
    row: sqlite3.Row,
) -> str:
    parts: list[str] = []
    source_person_id = _coerce_int_or_none(row["source_person_id"])
    if source_person_id is not None:
        parts.append(_short_person(con, world, source_person_id))
    institution = str(row["source_institution_id"] or "").strip()
    if institution:
        parts.append(_display_token(institution))
    return "; ".join(parts) if parts else "Source not recorded"


def _rediscovery_preserved_at_text(
    con: sqlite3.Connection,
    world: str,
    row: sqlite3.Row,
) -> str:
    settlement_id = str(row["preserving_settlement_id"] or "").strip()
    if settlement_id:
        return _history_settlement_label(con, world, settlement_id)
    region_id = str(row["preserving_region_id"] or "").strip()
    return _display_token(region_id) if region_id else "Place not recorded"


def _rediscovery_detail_row(
    con: sqlite3.Connection,
    world: str,
    row: sqlite3.Row,
) -> dict[str, object]:
    source = _rediscovery_source_text(con, world, row)
    preserved_at = _rediscovery_preserved_at_text(con, world, row)
    distortion = _rediscovery_distortion_text(row["distortion_json"])
    confidence = ""
    if row["confidence"] not in (None, ""):
        confidence = f"{float(row['confidence']):.2f}"
    summary_year = row["rediscovered_year"] if row["rediscovered_year"] not in (None, "") else row["sim_year"]
    summary = (
        f"{_format_year(summary_year)}: {source} recovered "
        f"{_display_token(row['event_type'])} record {row['record_key'] or row['record_id']} "
        f"with confidence {confidence or 'unknown'}; preserved at {preserved_at}; "
        f"distortion: {distortion}."
    )
    rendered = load_event_record_prose_rows(
        con,
        event_ids={row["event_id"]},
        visibility_states={"rediscovered"},
        limit=50,
        offset=0,
    )
    admin_summary = ""
    for record in rendered:
        if record.record_id == row["record_id"]:
            admin_summary = record.admin_summary
            break
    return {
        "Year": row["sim_year"],
        "Event ID": row["event_id"],
        "Record ID": row["record_id"],
        "Event Type": row["event_type"],
        "Visibility": row["visibility_state"],
        "Public Stage": row["public_knowledge_stage"],
        "Confidence": confidence,
        "Source": source,
        "Preserved At": preserved_at,
        "Distortion": distortion,
        "Rediscovery Summary": summary,
        "Admin Summary": admin_summary,
    }


def load_rediscovery_details(
    world: str,
    event_type_text: object,
    search: str,
    limit: object,
    offset: object,
) -> tuple[gr.Dataframe, str]:
    if not world:
        return _rediscovery_detail_empty_frame(), "Choose a world."
    row_limit = _safe_int(limit, 100, 1, 500)
    row_offset = _safe_int(offset, 0, 0, 10_000_000)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _rediscovery_detail_empty_frame(), f"{path} is missing. Run a simulation first."

    event_types = _history_event_type_filter(event_type_text)
    with _connect_readonly(path) as con:
        if not _has_relation(con, "simulation_events_readable"):
            return (
                _rediscovery_detail_empty_frame(),
                "No simulation_events_readable view found. Ensure or rebuild the save schema.",
            )
        if not _has_relation(con, "simulation_event_records_readable"):
            return (
                _rediscovery_detail_empty_frame(),
                "No simulation_event_records_readable view found. Ensure or rebuild the save schema.",
            )
        saved_world = _resolve_saved_world(con, world)
        clauses = ["r.visibility_state = 'rediscovered'"]
        params: list[object] = []
        if event_types:
            placeholders, type_params = _history_sql_in_placeholders(sorted(event_types))
            clauses.append(f"e.event_type IN ({placeholders})")
            params.extend(type_params)
        if str(search or "").strip():
            like = f"%{str(search).strip()}%"
            clauses.append(
                "("
                "e.event_type LIKE ? OR e.payload_json LIKE ? OR "
                "r.record_type LIKE ? OR r.source_institution_id LIKE ? OR "
                "r.distortion_json LIKE ?"
                ")"
            )
            params.extend([like] * 5)
        params.extend([row_limit, row_offset])
        rows = con.execute(
            f"""
            SELECT
                e.id AS event_id,
                e.sim_year,
                e.event_type,
                e.payload_json,
                r.record_id,
                r.record_key,
                r.record_type,
                r.visibility_state,
                r.public_knowledge_stage,
                r.rediscovered_year,
                r.confidence,
                r.source_person_id,
                r.source_institution_id,
                r.preserving_settlement_id,
                r.preserving_region_id,
                r.distortion_json
            FROM simulation_event_records_readable r
            JOIN simulation_events_readable e ON e.id = r.event_id
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(r.rediscovered_year, e.sim_year), e.sim_year, e.id, r.record_id
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        values = [_rediscovery_detail_row(con, saved_world, row) for row in rows]

    filter_bits: list[str] = ["rediscovered records"]
    if event_types:
        filter_bits.append("types=" + ", ".join(sorted(event_types)))
    if search:
        filter_bits.append(f"search={search!r}")
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} rediscovery detail rows at offset {row_offset} "
        f"| filters: {', '.join(filter_bits)}{saved_world_note}."
    )
    return _dataframe(values, REDISCOVERY_DETAIL_HEADERS), status


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
    "birth_relationship_type",
    "born_out_of_wedlock",
    "legitimacy_status",
    "job",
    "job_assigned_year",
    "job_era",
    "job_tier",
    "job_market_type",
    "housing_status",
    "household_role",
    "host_person_id",
    "employer_person_id",
    "social_class_band",
    "social_standing_01",
    "societal_impact_01",
    "perceived_worth_01",
    "status_tendency",
    "leader_quality",
    "leader_tendency",
    "outlaw_status",
    "outlaw_case_key",
    "outlaw_refuge_id",
    "outlaw_since_year",
    "last_free_settlement_id",
    "outlaw_custody_id",
    "outlaw_custody_status",
    "outlaw_custody_start_year",
    "outlaw_custody_expected_release_year",
    "outlaw_custody_release_year",
    "outlaw_custody_site_settlement_id",
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
        "Born": _format_year_blank(birthyear),
        "Died": _format_year_blank(deathyear),
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
    if not _has_table(con, "world_state"):
        return None
    columns = _table_columns(con, "world_state")
    if "world" in columns:
        row = con.execute("select current_year from world_state where world = ?", (world,)).fetchone()
    else:
        row = con.execute("select current_year from world_state where id = 1").fetchone()
    return int(row["current_year"]) if row and row["current_year"] is not None else None


def _people_browser_source_sql(con: sqlite3.Connection) -> tuple[str, list[str]]:
    columns = _table_columns(con, "simulation_people")
    projected = set(columns)
    select_parts = ["p.*"]
    joins: list[str] = []
    if (
        "birthplace_region_id" not in projected
        and "birthplace_region_key" in projected
        and _has_table(con, "simulation_region_lookup")
    ):
        select_parts.append("br.region_id as birthplace_region_id")
        joins.append("left join simulation_region_lookup br on br.region_key = p.birthplace_region_key")
        projected.add("birthplace_region_id")
    if (
        "birthplace_settlement_id" not in projected
        and "birthplace_settlement_key" in projected
        and _has_table(con, "simulation_settlement_lookup")
    ):
        select_parts.append("bs.settlement_id as birthplace_settlement_id")
        joins.append("left join simulation_settlement_lookup bs on bs.settlement_key = p.birthplace_settlement_key")
        projected.add("birthplace_settlement_id")
    if (
        "current_settlement_id" not in projected
        and "current_settlement_key" in projected
        and _has_table(con, "simulation_settlement_lookup")
    ):
        select_parts.append("cs.settlement_id as current_settlement_id")
        joins.append("left join simulation_settlement_lookup cs on cs.settlement_key = p.current_settlement_key")
        projected.add("current_settlement_id")
    if not joins:
        return "simulation_people", columns
    joined = "\n            ".join(joins)
    return (
        f"""(
                select {", ".join(select_parts)}
                from simulation_people p
                {joined}
            ) simulation_people""",
        sorted(projected),
    )


def _people_browser_search_sql(columns: Iterable[str]) -> tuple[str, int]:
    searchable = [
        column
        for column in (
            "person_json",
            "first_name",
            "last_name",
            "current_settlement_id",
            "birthplace_settlement_id",
            "birthplace_region_id",
            "birthplace",
            "job",
        )
        if column in columns
    ]
    if not searchable:
        return "1 = 0", 0
    return " or ".join(f"{_quote_identifier(column)} like ?" for column in searchable), len(searchable)


def _people_browser_home_sort_sql(columns: Iterable[str], people_has_compact_columns: bool) -> str:
    if people_has_compact_columns:
        place_columns = [
            column
            for column in ("current_settlement_id", "birthplace_settlement_id", "birthplace")
            if column in columns
        ]
        if place_columns:
            return f"coalesce({', '.join(_quote_identifier(column) for column in place_columns)}) collate nocase"
        return "person_id"
    return (
        "coalesce("
        "json_extract(person_json, '$.current_settlement_id'), "
        "json_extract(person_json, '$.birthplace_settlement_id'), "
        "json_extract(person_json, '$.birthplace')"
        ") collate nocase"
    )


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
        people_source_sql, people_source_columns = _people_browser_source_sql(con)
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
            search_sql, search_param_count = _people_browser_search_sql(people_source_columns)
            clauses.append(f"({search_sql})")
            params.extend([f"%{search}%"] * search_param_count)
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
        "Home": _people_browser_home_sort_sql(people_source_columns, people_has_compact_columns),
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
            from {people_source_sql}
            where {where_sql}
            order by {order_clause}
            {limit_sql}
            """,
            query_params,
        ).fetchall()
        total = con.execute(f"select count(*) as n from {people_source_sql} where {where_sql}", params).fetchone()["n"]

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


def _almanack_headers_for_metric(selection: object) -> list[str]:
    return ALMANACK_SELECTED_HEADERS if _almanack_metric_key(selection) else ALMANACK_HEADERS


def _almanack_empty_frame(metric: object = None) -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=_almanack_headers_for_metric(metric))


def _almanack_empty_evidence_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=ALMANACK_EVIDENCE_HEADERS)


def _almanack_metric_key(selection: object) -> str | None:
    text = str(selection or "").strip()
    if not text or text == "All Metrics":
        return None
    if text in ALMANACK_METRIC_LABEL_TO_KEY:
        return ALMANACK_METRIC_LABEL_TO_KEY[text]
    if text in set(ALMANACK_METRIC_LABEL_TO_KEY.values()):
        return text
    return ALMANACK_METRIC_LABEL_TO_KEY.get(ALMANACK_METRIC_CHOICES[1])


def _almanack_year_span(row: dict[str, object]) -> str:
    first = row.get("first_year")
    last = row.get("last_year")
    if first in (None, "") and last in (None, ""):
        return ""
    if first == last or last in (None, ""):
        return _format_year(first)
    if first in (None, ""):
        return _format_year(last)
    return _format_year_span(first, last)


def _shorten_table_text(value: object, limit: int = 96) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _almanack_context_text(row: dict[str, object]) -> str:
    bits: list[str] = []
    if row.get("world_rank") not in (None, ""):
        bits.append(f"world #{row.get('world_rank')}")
    if row.get("era_rank") not in (None, ""):
        era = row.get("era_bucket")
        era_tail = f" ({era}s)" if era not in (None, "") else ""
        bits.append(f"era #{row.get('era_rank')}{era_tail}")
    if row.get("region_rank") not in (None, ""):
        bits.append(f"region #{row.get('region_rank')}")
    if row.get("percentile") not in (None, ""):
        bits.append(f"pct {_fmt_number(row.get('percentile'), 1)}")
    if row.get("z_score") not in (None, ""):
        bits.append(f"z {_fmt_number(row.get('z_score'), 2)}")
    return "; ".join(bits)


def _almanack_state_key(row: dict[str, object]) -> str:
    return json.dumps(
        {
            "source_kind": str(row.get("source_kind") or "detailed"),
            "person_id": int(row.get("person_id") or 0),
            "metric_key": str(row.get("metric_key") or ""),
        },
        separators=(",", ":"),
    )


def _almanack_table_row(row: dict[str, object]) -> dict[str, object]:
    return {
        "Rank": row.get("rank", ""),
        "Value": _fmt_number(row.get("metric_value"), 3),
        "Count": row.get("metric_count", ""),
        "Name": row.get("name", ""),
        "Person ID": row.get("person_id", ""),
        "Life": row.get("life", ""),
        "Age": row.get("age", ""),
        "Years": _almanack_year_span(row),
        "Home": row.get("home", ""),
        "Metric": row.get("metric_label", ""),
        "Context": _almanack_context_text(row),
        "Evidence": _shorten_table_text(row.get("evidence_summary", "")),
        "Source": "Passive explicit" if row.get("source_kind") == "passive" else "Detailed",
    }


def _almanack_evidence_table(rows: list[dict[str, object]]) -> gr.Dataframe:
    values = []
    for row in rows:
        values.append(
            {
                "Rank": row.get("evidence_rank", ""),
                "Year": row.get("source_year", ""),
                "Source": row.get("source_table", ""),
                "Source ID": row.get("source_id", ""),
                "Role": row.get("role", ""),
                "Contribution": _fmt_number(row.get("contribution_value"), 3),
                "Summary": row.get("summary", ""),
                "Payload Path": row.get("payload_path", ""),
                "Related People": ", ".join(str(pid) for pid in row.get("related_people", [])[:8]),
            }
        )
    return _dataframe(values, ALMANACK_EVIDENCE_HEADERS)


def _almanack_status_text(
    path: Path, cache: dict[str, object], row_count: int, rank_mode: str
) -> str:
    bits = [f"{path.name}: showing {row_count} Almanack ranking rows"]
    bits.append(f"rank mode={rank_mode or 'Raw Value'}")
    cache_rows = int(cache.get("row_count") or 0)
    bits.append(f"cache rows={cache_rows}")
    current_event = int(cache.get("current_event_max_id") or 0)
    source_event = int(cache.get("source_event_max_id") or 0)
    if current_event or source_event:
        bits.append(f"events={source_event}/{current_event}")
    updated_year = cache.get("updated_year")
    if updated_year not in (None, ""):
        bits.append(f"updated year={updated_year}")
    if cache.get("stale"):
        bits.append("cache may be stale; click Refresh Almanack")
    return " | ".join(bits) + ". Click a detailed row to open its person sheet."


def load_almanack_browser(
    world: str,
    category: str,
    metric: object,
    life_filter: str,
    source_filter: str,
    search: str,
    min_value: object,
    limit: object,
    rank_mode: str = "Raw Value",
) -> tuple[gr.Dataframe, str, list[str], str, str]:
    if not world:
        return (
            _almanack_empty_frame(metric),
            "Choose a world.",
            [],
            '<div class="person-sheet muted">Load The Almanack, then click a row.</div>',
            "Load The Almanack, then click a row.",
        )
    row_limit = _safe_int(limit, 50, 1, 500)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return (
            _almanack_empty_frame(metric),
            f"{path} is missing. Run a simulation first.",
            [],
            '<div class="person-sheet muted">No save DB found.</div>',
            "No save DB found.",
        )
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        cache = person_almanack_cache_status(con)
        if not cache.get("exists") or int(cache.get("row_count") or 0) == 0:
            return (
                _almanack_empty_frame(metric),
                f"{path.name}: Almanack cache is empty. Click Refresh Almanack to build it.",
                [],
                '<div class="person-sheet muted">Refresh The Almanack, then load rankings.</div>',
                "Refresh The Almanack, then load rankings.",
            )
        rows = query_person_almanack(
            con,
            metric_key=_almanack_metric_key(metric),
            category=str(category or "All"),
            life_filter=str(life_filter or "All"),
            source_filter=str(source_filter or "Both"),
            search=str(search or ""),
            min_value=min_value,
            limit=row_limit,
            rank_mode=str(rank_mode or "Raw Value"),
        )
    values = [_almanack_table_row(row) for row in rows]
    headers = _almanack_headers_for_metric(metric)
    keys = [_almanack_state_key(row) for row in rows]
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = _almanack_status_text(path, cache, len(values), str(rank_mode or "Raw Value")) + saved_world_note
    return (
        _dataframe(values, headers),
        status,
        keys,
        '<div class="person-sheet muted">Click a detailed row to open its person sheet. Passive rows show a compact record.</div>',
        "Click a detailed row to generate share text.",
    )


def refresh_almanack_browser(
    world: str,
    category: str,
    metric: object,
    life_filter: str,
    source_filter: str,
    search: str,
    min_value: object,
    limit: object,
    rank_mode: str = "Raw Value",
) -> tuple[gr.Dataframe, str, list[str], str, str]:
    if not world:
        return (
            _almanack_empty_frame(metric),
            "Choose a world.",
            [],
            '<div class="person-sheet muted">Choose a world.</div>',
            "Choose a world.",
        )
    path = _db_path(world, "Save DB")
    if not path.exists():
        return (
            _almanack_empty_frame(metric),
            f"{path} is missing. Run a simulation first.",
            [],
            '<div class="person-sheet muted">No save DB found.</div>',
            "No save DB found.",
        )
    from library.world_save import ensure_checkpoint_schema_for_file

    ensure_checkpoint_schema_for_file(path)
    count = refresh_person_almanack_for_file(path)
    table, status, keys, sheet, share = load_almanack_browser(
        world,
        category,
        metric,
        life_filter,
        source_filter,
        search,
        min_value,
        limit,
        rank_mode,
    )
    return table, f"Refreshed {count} Almanack metric rows. {status}", keys, sheet, share


def _empty_settlements_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=SETTLEMENT_BROWSER_HEADERS)


def _empty_regions_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=REGION_BROWSER_HEADERS)


def _empty_polities_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=POLITY_BROWSER_HEADERS)


def _empty_outlaw_cases_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=OUTLAW_CASE_BROWSER_HEADERS)


def _empty_outlaw_refuges_frame() -> gr.Dataframe:
    return gr.Dataframe(value=[], headers=OUTLAW_REFUGE_BROWSER_HEADERS)


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
    birth_region_sql = _person_birth_region_sql(con)
    alive_counts, top_jobs = _alive_counts_and_top_jobs_by_place(
        con,
        world,
        birth_region_sql,
        [region_id],
        limit=3,
    )
    alive = alive_counts.get(str(region_id), 0)
    jobs = top_jobs.get(str(region_id), [])
    return alive, ", ".join(f"{job} ({count})" for job, count in jobs)


def _region_active_settlement_count(con: sqlite3.Connection, world: str, region_id: str) -> int:
    if not _has_table(con, "simulation_settlements"):
        return 0
    settlement_table = _place_read_relation(con, "simulation_settlements")
    settlement_where, settlement_params = _world_where(con, settlement_table, world)
    return _count_one(
        con,
        f"""
        select count(*)
        from {_quote_identifier(settlement_table)}
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
        region_table = _place_read_relation(con, "simulation_regions")
        where_sql, params = _world_where(con, region_table, saved_world)
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
            from {_quote_identifier(region_table)}
            where {where_sql}
            order by total_population_cap desc, region_display_name collate nocase
            limit ?
            """,
            (*params, row_limit),
        ).fetchall()
        total = _count_one(con, f"select count(*) from {_quote_identifier(region_table)} where {where_sql}", params)
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
    residence_sql = _person_residence_sql(con)
    alive_counts, top_jobs = _alive_counts_and_top_jobs_by_place(
        con,
        world,
        residence_sql,
        [settlement_id],
        limit=3,
    )
    alive = alive_counts.get(str(settlement_id), 0)
    jobs = top_jobs.get(str(settlement_id), [])
    return alive, ", ".join(f"{job} ({count})" for job, count in jobs)


def _settlement_summary_row(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> dict[str, object]:
    settlement_id = str(row["settlement_id"])
    region_id = str(row["region_id"] or "")
    alive, jobs = _settlement_alive_and_jobs(con, world, settlement_id)
    return {
        "Name": row["display_name"] or settlement_id,
        "Level": row["level"] or "",
        "Alive": alive,
        "Region": _history_region_label(con, world, region_id),
        "Status": row["status"] or "",
        "Food": _fmt_number(row["food_pressure"]),
        "Stability": _fmt_number(row["stability"]),
        "Market": _fmt_number(row["market_pull"]),
        "Prosperity": _fmt_number(row["prosperity_pool"]),
        "Founded": row["founded_sim_year"] or "",
        "Top Jobs": jobs,
    }


def _outlaw_relation(con: sqlite3.Connection, table: str) -> str:
    readable = f"{table}_readable"
    return readable if _has_relation(con, readable) else table


def _outlaw_row_value(row: sqlite3.Row, key: str, default: object = "") -> object:
    return row[key] if key in row.keys() else default


def _outlaw_refuge_active_case_count(con: sqlite3.Connection, refuge_id: object) -> int:
    rid = str(refuge_id or "").strip()
    if not rid or not _has_table(con, "simulation_outlaw_cases"):
        return 0
    try:
        return _count_one(
            con,
            "select count(*) from simulation_outlaw_cases where refuge_id = ? and status = 'active'",
            (rid,),
        )
    except sqlite3.Error:
        return 0


def _outlaw_refuge_region_id(row: sqlite3.Row) -> str:
    return str(
        _outlaw_row_value(row, "region_id")
        or _outlaw_row_value(row, "region_key")
        or ""
    ).strip()


def _outlaw_refuge_near_settlement_id(row: sqlite3.Row) -> str:
    return str(
        _outlaw_row_value(row, "near_settlement_id")
        or _outlaw_row_value(row, "settlement_id")
        or _outlaw_row_value(row, "near_settlement_key")
        or ""
    ).strip()


def _safe_settlement_name(con: sqlite3.Connection, world: str, settlement_id: object) -> str:
    if not settlement_id:
        return ""
    try:
        return _settlement_name(con, world, settlement_id)
    except sqlite3.Error:
        return str(settlement_id)


def _safe_region_label(con: sqlite3.Connection, world: str, region_id: object) -> str:
    if not region_id:
        return ""
    try:
        return _history_region_label(con, world, region_id)
    except sqlite3.Error:
        return str(region_id)


def _outlaw_refuge_display_name(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> str:
    refuge_id = str(_outlaw_row_value(row, "refuge_id") or "").strip()
    explicit = str(_outlaw_row_value(row, "display_name") or "").strip()
    if explicit:
        return explicit
    near_sid = _outlaw_refuge_near_settlement_id(row)
    region_id = _outlaw_refuge_region_id(row)
    near_label = _safe_settlement_name(con, world, near_sid) if near_sid else ""
    return outlaw_refuge_display_name(
        refuge_id,
        region_id=region_id,
        near_place_label=near_label,
        year=_outlaw_row_value(row, "founded_year", ""),
    )


def _lookup_outlaw_refuge_display_name(
    con: sqlite3.Connection,
    world: str,
    refuge_id: object,
    *,
    region_id: object = "",
    near_settlement_id: object = "",
) -> str:
    rid = str(refuge_id or "").strip()
    if not rid:
        return ""
    if _has_table(con, "simulation_outlaw_refuges"):
        relation = _outlaw_relation(con, "simulation_outlaw_refuges")
        try:
            row = con.execute(
                f"select * from {_quote_identifier(relation)} where refuge_id = ?",
                (rid,),
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None:
            return _outlaw_refuge_display_name(con, world, row)
    near_sid = str(near_settlement_id or "").strip()
    near_label = _safe_settlement_name(con, world, near_sid) if near_sid else ""
    return outlaw_refuge_display_name(rid, region_id=region_id, near_place_label=near_label)


def _outlaw_case_refuge_label(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> str:
    explicit = str(
        _row_value(row, "refuge_display_name")
        or _row_value(row, "display_name")
        or ""
    ).strip()
    if explicit:
        return explicit
    refuge_id = _row_value(row, "refuge_id")
    if refuge_id in (None, ""):
        return ""
    return _lookup_outlaw_refuge_display_name(
        con,
        world,
        refuge_id,
        region_id=_row_value(row, "region_id"),
        near_settlement_id=_row_value(row, "settlement_id"),
    )


def _person_current_outlaw_refuge_label(
    con: sqlite3.Connection,
    world: str,
    person: dict[str, object],
) -> str:
    refuge_id = person.get("outlaw_refuge_id")
    if refuge_id in (None, ""):
        return "none"
    return _lookup_outlaw_refuge_display_name(con, world, refuge_id)


def _person_current_outlaw_custody_label(
    con: sqlite3.Connection,
    world: str,
    person: dict[str, object],
) -> str:
    custody_id = str(person.get("outlaw_custody_id") or "").strip()
    status = str(person.get("outlaw_custody_status") or "").strip()
    site_id = str(person.get("outlaw_custody_site_settlement_id") or "").strip()
    expected = person.get("outlaw_custody_expected_release_year")
    if not custody_id and not status and not site_id:
        return "none"
    bits: list[str] = []
    if status:
        bits.append(status.replace("_", " "))
    if site_id:
        bits.append(f"at {_safe_settlement_name(con, world, site_id)}")
    if expected not in (None, ""):
        bits.append(f"expected release {_format_year(expected)}")
    return "; ".join(bits) or custody_id or "custody"


def _legacy_outlaw_refuge_place_name(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> str:
    """Kept only for stale tests or direct callers that want the old generic form."""
    refuge_id = str(_outlaw_row_value(row, "refuge_id") or "").strip()
    near_sid = _outlaw_refuge_near_settlement_id(row)
    region_id = _outlaw_refuge_region_id(row)
    if near_sid:
        return f"Outlaw refuge near {_safe_settlement_name(con, world, near_sid)}"
    region_name = _safe_region_label(con, world, region_id) if region_id else ""
    if region_name:
        return f"Outlaw refuge in {region_name}"
    return refuge_id or "Outlaw refuge"


def _outlaw_refuge_summary_row(
    con: sqlite3.Connection,
    world: str,
    row: sqlite3.Row,
) -> dict[str, object]:
    refuge_id = str(_outlaw_row_value(row, "refuge_id") or "").strip()
    region_id = _outlaw_refuge_region_id(row)
    active_cases = _outlaw_row_value(row, "active_case_count", None)
    if active_cases in (None, ""):
        active_cases = _outlaw_refuge_active_case_count(con, refuge_id)
    band_size = _outlaw_row_value(row, "band_size", "")
    return {
        "Name": _outlaw_refuge_display_name(con, world, row),
        "Level": "outlaw refuge",
        "Alive": band_size or active_cases or "",
        "Region": _safe_region_label(con, world, region_id) if region_id else "",
        "Status": _outlaw_row_value(row, "status", ""),
        "Food": "",
        "Stability": "",
        "Market": "",
        "Prosperity": _fmt_number(_outlaw_row_value(row, "support_01", "")),
        "Polity": "",
        "Founded": _outlaw_row_value(row, "founded_year", ""),
        "Top Jobs": f"outlaws ({active_cases})" if active_cases not in (None, "") else "outlaws",
    }


def _outlaw_refuge_search_columns(columns: set[str]) -> list[str]:
    wanted = [
        "refuge_id",
        "display_name",
        "region_id",
        "settlement_id",
        "near_settlement_id",
        "status",
        "details_json",
    ]
    return [column for column in wanted if column in columns]


def _query_outlaw_refuges(
    con: sqlite3.Connection,
    *,
    search: str = "",
    status_filter: str = "All",
    limit: int = 50,
) -> tuple[list[sqlite3.Row], int]:
    if not _has_table(con, "simulation_outlaw_refuges"):
        return [], 0
    relation = _outlaw_relation(con, "simulation_outlaw_refuges")
    columns = set(_table_columns(con, relation))
    clauses: list[str] = []
    params: list[object] = []
    selected_status = str(status_filter or "All").strip()
    if selected_status == "Active" and "status" in columns:
        clauses.append("status = 'active'")
    elif selected_status == "Abandoned" and "status" in columns:
        clauses.append("status != 'active'")
    if search:
        search_columns = _outlaw_refuge_search_columns(columns)
        if search_columns:
            clauses.append(
                "("
                + " or ".join(f"cast({_quote_identifier(column)} as text) like ?" for column in search_columns)
                + ")"
            )
            params.extend([f"%{search}%"] * len(search_columns))
    where_sql = f"where {' and '.join(clauses)}" if clauses else ""
    rows = con.execute(
        f"""
        select *
        from {_quote_identifier(relation)}
        {where_sql}
        order by status = 'active' desc, coalesce(founded_year, 0) desc, refuge_id
        limit ?
        """,
        (*params, int(limit)),
    ).fetchall()
    total = _count_one(
        con,
        f"select count(*) from {_quote_identifier(relation)} {where_sql}",
        params,
    )
    return rows, total


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
        settlement_table = _place_read_relation(con, "simulation_settlements")
        where_sql, params = _world_where(con, settlement_table, saved_world)
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
            from {_quote_identifier(settlement_table)}
            where {where_sql}
            order by status = 'active' desc, population_cap desc, display_name collate nocase
            limit ?
            """,
            (*params, row_limit),
        ).fetchall()
        total = _count_one(con, f"select count(*) from {_quote_identifier(settlement_table)} where {where_sql}", params)
        values = [_settlement_summary_row(con, saved_world, row) for row in rows]
        settlement_ids = [str(row["settlement_id"]) for row in rows]
        refuge_rows, refuge_total = _query_outlaw_refuges(
            con,
            search=search or "",
            status_filter=status_filter,
            limit=row_limit,
        )
        if refuge_rows:
            values.extend(_outlaw_refuge_summary_row(con, saved_world, row) for row in refuge_rows)
            settlement_ids.extend(str(row["refuge_id"]) for row in refuge_rows)
        total += refuge_total

    filter_text = f" | filters: {', '.join(filter_bits)}" if filter_bits else ""
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {total} settlements/refuges{filter_text}{saved_world_note}. "
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
    settlement_table = _place_read_relation(con, "simulation_settlements")
    if "world" in _table_columns(con, settlement_table):
        row = con.execute(
            f"select display_name from {_quote_identifier(settlement_table)} where world = ? and settlement_id = ?",
            (world, str(settlement_id)),
        ).fetchone()
    else:
        row = con.execute(
            f"select display_name from {_quote_identifier(settlement_table)} where settlement_id = ?",
            (str(settlement_id),),
        ).fetchone()
    return str(row["display_name"] or settlement_id) if row else str(settlement_id)


def _lookup_settlement(con: sqlite3.Connection, world: str, settlement_id: object) -> sqlite3.Row | None:
    sid = str(settlement_id or "").strip()
    if not sid or not _has_table(con, "simulation_settlements"):
        return None
    settlement_table = _place_read_relation(con, "simulation_settlements")
    if "world" in _table_columns(con, settlement_table):
        return con.execute(
            f"select * from {_quote_identifier(settlement_table)} where world = ? and settlement_id = ?",
            (world, sid),
        ).fetchone()
    return con.execute(
        f"select * from {_quote_identifier(settlement_table)} where settlement_id = ?",
        (sid,),
    ).fetchone()


def _settlement_prestige_metrics(
    con: sqlite3.Connection, world: str, settlement_id: str
) -> dict[str, int]:
    metrics = {
        "elite_residents": 0,
        "prestige_jobs": 0,
        "domestic_service": 0,
        "patronage_ties": 0,
        "elite_investments": 0,
    }
    if _has_table(con, "simulation_people"):
        residence_sql = _person_residence_sql(con)
        people_where, people_params = _alive_where(con, world)
        cols = set(_table_columns(con, "simulation_people"))
        standing_sql = (
            "coalesce(social_standing_01, 0)"
            if "social_standing_01" in cols
            else (
                "coalesce(cast(json_extract(person_json, '$.social_standing_01') as real), 0)"
                if "person_json" in cols
                else "0"
            )
        )
        class_sql = (
            "lower(coalesce(social_class_band, ''))"
            if "social_class_band" in cols
            else (
                "lower(coalesce(json_extract(person_json, '$.social_class_band'), ''))"
                if "person_json" in cols
                else "''"
            )
        )
        market_sql = (
            "lower(coalesce(job_market_type, ''))"
            if "job_market_type" in cols
            else (
                "lower(coalesce(json_extract(person_json, '$.job_market_type'), ''))"
                if "person_json" in cols
                else "''"
            )
        )
        row = con.execute(
            f"""
            select
                sum(case
                    when {standing_sql} >= 0.60
                      or {class_sql} in ('notable', 'upper', 'elite', 'ruling')
                    then 1 else 0 end) as elite_residents,
                sum(case
                    when {market_sql} = 'office'
                      or {standing_sql} >= 0.60
                    then 1 else 0 end) as prestige_jobs,
                sum(case
                    when {market_sql} = 'domestic_service'
                    then 1 else 0 end) as domestic_service
            from simulation_people
            where {people_where}
              and {residence_sql} = ?
            """,
            (*people_params, settlement_id),
        ).fetchone()
        if row is not None:
            metrics["elite_residents"] = int(row["elite_residents"] or 0)
            metrics["prestige_jobs"] = int(row["prestige_jobs"] or 0)
            metrics["domestic_service"] = int(row["domestic_service"] or 0)
    if _has_table(con, "simulation_patronage_ties"):
        row = con.execute(
            """
            select count(*) as c
            from simulation_patronage_ties
            where status = 'active' and settlement_id = ?
            """,
            (settlement_id,),
        ).fetchone()
        metrics["patronage_ties"] = int(row["c"] or 0) if row is not None else 0
    if _has_relation(con, "simulation_events_readable"):
        try:
            row = con.execute(
                """
                select count(*) as c
                from simulation_events_readable
                where event_type = 'elite_household_investment'
                  and settlement_id = ?
                """,
                (settlement_id,),
            ).fetchone()
            metrics["elite_investments"] = int(row["c"] or 0) if row is not None else 0
        except sqlite3.OperationalError:
            pass
    return metrics


def render_settlement_outputs(world: str, settlement_id: object) -> str:
    sid = str(settlement_id or "").strip()
    if not world:
        return '<div class="place-sheet muted">Choose a world.</div>'
    if not sid:
        return '<div class="place-sheet muted">Browse settlements, then click a row to inspect it.</div>'
    if sid.startswith("outlaw_refuge:"):
        return render_outlaw_refuge_detail(world, sid)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return f'<div class="place-sheet muted">{html.escape(str(path))} is missing.</div>'
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        row = _lookup_settlement(con, saved_world, sid)
        if not row:
            return f'<div class="place-sheet muted">No settlement named {html.escape(sid)} in {html.escape(saved_world)}.</div>'
        alive, jobs = _settlement_alive_and_jobs(con, saved_world, sid)
        region_name = _history_region_label(con, saved_world, row["region_id"])
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
                residents.append(_notable_person_label(person))
        prestige_metrics = _settlement_prestige_metrics(con, saved_world, sid)
        cards = "".join(
            [
                _detail_card("Alive", alive),
                _detail_card("Level", row["level"] or ""),
                _detail_card("Status", row["status"] or ""),
                _detail_card("Region", region_name),
                _detail_card("Population Cap", row["population_cap"] or ""),
                _detail_card("Households", row["household_cap"] or ""),
                _detail_card("Food Pressure", _fmt_number(row["food_pressure"])),
                _detail_card("Stability", _fmt_number(row["stability"])),
                _detail_card("Market Pull", _fmt_number(row["market_pull"])),
                _detail_card("Prosperity", _fmt_number(row["prosperity_pool"])),
                _detail_card("Elite Residents", prestige_metrics["elite_residents"]),
                _detail_card("Prestige Jobs", prestige_metrics["prestige_jobs"]),
                _detail_card("Patronage Ties", prestige_metrics["patronage_ties"]),
                _detail_card("Domestic Service", prestige_metrics["domestic_service"]),
                _detail_card("Elite Investments", prestige_metrics["elite_investments"]),
                _detail_card("Founded", _format_year(row["founded_sim_year"], unknown_text="Unknown")),
            ]
        )
        name_bits = [row["etymology"], row["name_category_primary"], row["name_culture_primary"]]
        name_line = " | ".join(str(x) for x in name_bits if x)
        return (
            '<div class="place-sheet">'
            f'<h2>{html.escape(str(row["display_name"] or sid))}</h2>'
            f'<div class="place-subtitle">{html.escape(str(row["level"] or "settlement"))} in {html.escape(region_name)}</div>'
            f'<div class="place-muted">{html.escape(name_line)}</div>'
            f'<div class="place-grid">{cards}</div>'
            '<div class="place-columns">'
            f'<section><h3>Top Jobs</h3>{_ul(jobs.split(", ") if jobs else [])}</section>'
            f'<section><h3>Notable Residents</h3>{_ul(residents)}</section>'
            '</div>'
            '</div>'
        )


def _outlaw_case_search_columns(columns: set[str]) -> list[str]:
    wanted = [
        "case_key",
        "accused_person_id",
        "accused_name",
        "offense_type",
        "offense_kind",
        "status",
        "resolution",
        "refuge_id",
        "refuge_display_name",
        "region_id",
        "settlement_id",
        "details_json",
    ]
    return [column for column in wanted if column in columns]


def _query_outlaw_cases(
    con: sqlite3.Connection,
    *,
    search: str = "",
    status_filter: str = "Active",
    limit: int = 50,
) -> tuple[list[sqlite3.Row], int]:
    if not _has_table(con, "simulation_outlaw_cases"):
        return [], 0
    relation = _outlaw_relation(con, "simulation_outlaw_cases")
    columns = set(_table_columns(con, relation))
    clauses: list[str] = []
    params: list[object] = []
    selected_status = str(status_filter or "Active").strip()
    if selected_status == "Active" and "status" in columns:
        clauses.append("status = 'active'")
    elif selected_status == "Resolved" and "status" in columns:
        clauses.append("status != 'active'")
    if search:
        search_columns = _outlaw_case_search_columns(columns)
        if search_columns:
            clauses.append(
                "("
                + " or ".join(f"cast({_quote_identifier(column)} as text) like ?" for column in search_columns)
                + ")"
            )
            params.extend([f"%{search}%"] * len(search_columns))
    where_sql = f"where {' and '.join(clauses)}" if clauses else ""
    rows = con.execute(
        f"""
        select *
        from {_quote_identifier(relation)}
        {where_sql}
        order by status = 'active' desc, coalesce(start_year, 0) desc, case_key
        limit ?
        """,
        (*params, int(limit)),
    ).fetchall()
    total = _count_one(
        con,
        f"select count(*) from {_quote_identifier(relation)} {where_sql}",
        params,
    )
    return rows, total


def _outlaw_case_accused_label(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> str:
    accused_id = _outlaw_row_value(row, "accused_person_id", "")
    name = str(_outlaw_row_value(row, "accused_name", "") or "").strip()
    if not name and accused_id not in (None, ""):
        name = _person_link_text(con, world, accused_id).split(" (", 1)[0]
    if accused_id not in (None, ""):
        return f"{name or 'Unknown'} #{accused_id}"
    return name or "Unknown"


def _outlaw_case_summary_row(con: sqlite3.Connection, world: str, row: sqlite3.Row) -> dict[str, object]:
    offense_kind = str(_outlaw_row_value(row, "offense_kind", "") or "").replace("_", " ")
    offense_type = str(_outlaw_row_value(row, "offense_type", "") or "").replace("_", " ")
    offense = offense_kind or offense_type
    if offense_kind and offense_type and offense_kind != offense_type:
        offense = f"{offense_kind} ({offense_type})"
    custody_status = str(_outlaw_row_value(row, "custody_status", "") or "").strip()
    custody_site = str(_outlaw_row_value(row, "custody_site_settlement_id", "") or "").strip()
    custody = ""
    if custody_status:
        custody = custody_status.replace("_", " ")
        if custody_site:
            custody += f" at {_safe_settlement_name(con, world, custody_site)}"
    return {
        "Case": _outlaw_row_value(row, "case_key", ""),
        "Status": _outlaw_row_value(row, "status", ""),
        "Offense": offense,
        "Accused": _outlaw_case_accused_label(con, world, row),
        "Refuge": _outlaw_case_refuge_label(con, world, row),
        "Custody": custody,
        "Region": _safe_region_label(con, world, _outlaw_row_value(row, "region_id", "")),
        "Settlement": _safe_settlement_name(con, world, _outlaw_row_value(row, "settlement_id", "")),
        "Started": _outlaw_row_value(row, "start_year", ""),
        "Last Seen": _outlaw_row_value(row, "last_seen_year", ""),
        "Forget Year": _outlaw_row_value(row, "expected_forget_year", ""),
        "Resolved": _outlaw_row_value(row, "resolved_year", ""),
        "Severity": _fmt_number(_outlaw_row_value(row, "severity_01", "")),
        "Knownness": _fmt_number(_outlaw_row_value(row, "knownness_01", "")),
        "Pursuit": _fmt_number(_outlaw_row_value(row, "pursuit_pressure_01", "")),
        "Buyoff": _fmt_number(_outlaw_row_value(row, "buyoff_power_01", "")),
        "Resolution": _outlaw_row_value(row, "resolution", ""),
    }


def _encode_outlaw_case_key(row: sqlite3.Row) -> str:
    return json.dumps(
        {
            "case_key": _outlaw_row_value(row, "case_key", ""),
            "person_id": _outlaw_row_value(row, "accused_person_id", ""),
        },
        sort_keys=True,
    )


def _decode_outlaw_case_key(value: object) -> dict[str, object]:
    try:
        decoded = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def load_outlaw_cases_browser(
    world: str,
    status_filter: str,
    search: str,
    limit: object,
) -> tuple[gr.Dataframe, str, list[str]]:
    if not world:
        return _empty_outlaw_cases_frame(), "Choose a world.", []
    row_limit = _safe_int(limit, 50, 1, 250)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _empty_outlaw_cases_frame(), f"{path} is missing. Run a simulation first.", []
    with _connect_readonly(path) as con:
        if not _has_table(con, "simulation_outlaw_cases"):
            return _empty_outlaw_cases_frame(), "No simulation_outlaw_cases table found.", []
        saved_world = _resolve_saved_world(con, world)
        rows, total = _query_outlaw_cases(
            con,
            search=search or "",
            status_filter=status_filter,
            limit=row_limit,
        )
        values = [_outlaw_case_summary_row(con, saved_world, row) for row in rows]
        keys = [_encode_outlaw_case_key(row) for row in rows]
    filter_text = f" | filters: {status_filter or 'Active'}"
    if search:
        filter_text += f", search={search!r}"
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {total} outlaw cases{filter_text}{saved_world_note}. "
        "Click a row to open the accused person's sheet."
    )
    return _dataframe(values, OUTLAW_CASE_BROWSER_HEADERS), status, keys


def select_outlaw_case_from_table(
    keys: object,
    world: str,
    evt: gr.SelectData,
) -> tuple[str, str]:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        encoded = keys[int(row_index)]  # type: ignore[index]
        person_id = _decode_outlaw_case_key(encoded).get("person_id")
        if person_id in (None, ""):
            raise ValueError("missing person id")
    except Exception:
        return (
            '<div class="person-sheet muted" role="status">Click an outlaw case row to open the accused person.</div>',
            "Click an outlaw case row to generate share text.",
        )
    return render_person_outputs(world, person_id)


def _outlaw_html_list(items: Iterable[str], empty: str = "None yet.") -> str:
    clean = [str(item).strip() for item in items if str(item).strip()]
    if not clean:
        return f'<p class="place-muted">{html.escape(empty)}</p>'
    return "<ul>" + "".join(f"<li>{item}</li>" for item in clean) + "</ul>"


def _render_outlaw_refuge_sheet(con: sqlite3.Connection, world: str, refuge_id: object) -> str:
    rid = str(refuge_id or "").strip()
    if not rid:
        return '<div class="place-sheet muted">Click an outlaw refuge row to inspect it.</div>'
    if not _has_table(con, "simulation_outlaw_refuges"):
        return '<div class="place-sheet muted">No outlaw refuges are recorded in this save.</div>'
    relation = _outlaw_relation(con, "simulation_outlaw_refuges")
    row = con.execute(
        f"select * from {_quote_identifier(relation)} where refuge_id = ?",
        (rid,),
    ).fetchone()
    if row is None:
        return f'<div class="place-sheet muted">No outlaw refuge named {html.escape(rid)}.</div>'
    region_id = _outlaw_refuge_region_id(row)
    region_name = _safe_region_label(con, world, region_id) if region_id else ""
    near_sid = _outlaw_refuge_near_settlement_id(row)
    near_name = _safe_settlement_name(con, world, near_sid) if near_sid else ""
    active_cases = _outlaw_row_value(row, "active_case_count", None)
    if active_cases in (None, ""):
        active_cases = _outlaw_refuge_active_case_count(con, rid)
    cards = "".join(
        [
            _detail_card("Status", _outlaw_row_value(row, "status", "")),
            _detail_card("Region", region_name or region_id or "Unknown"),
            _detail_card("Near", near_name or near_sid or "Unrecorded"),
            _detail_card("Band Size", _outlaw_row_value(row, "band_size", "")),
            _detail_card("Active Cases", active_cases),
            _detail_card("Founded", _format_year(_outlaw_row_value(row, "founded_year", ""), unknown_text="Unknown")),
            _detail_card("Discovered", _format_year(_outlaw_row_value(row, "discovered_year", ""), unknown_text="Not yet")),
            _detail_card("Abandoned", _format_year(_outlaw_row_value(row, "abandoned_year", ""), unknown_text="No")),
            _detail_card("Concealment", _fmt_number(_outlaw_row_value(row, "concealment_01", ""))),
            _detail_card("Support", _fmt_number(_outlaw_row_value(row, "support_01", ""))),
            _detail_card("Last Activity", _format_year(_outlaw_row_value(row, "last_activity_year", ""), unknown_text="Unknown")),
        ]
    )
    case_items: list[str] = []
    if _has_table(con, "simulation_outlaw_cases"):
        case_relation = _outlaw_relation(con, "simulation_outlaw_cases")
        for case_row in con.execute(
            f"""
            select *
            from {_quote_identifier(case_relation)}
            where refuge_id = ?
            order by status = 'active' desc, coalesce(start_year, 0) desc, case_key
            limit 12
            """,
            (rid,),
        ).fetchall():
            accused = _short_person_html(con, world, _outlaw_row_value(case_row, "accused_person_id", ""))
            offense = html.escape(
                str(
                    _outlaw_row_value(case_row, "offense_kind", "")
                    or _outlaw_row_value(case_row, "offense_type", "")
                    or "offense"
                ).replace("_", " ")
            )
            status = html.escape(str(_outlaw_row_value(case_row, "status", "") or ""))
            resolution = str(_outlaw_row_value(case_row, "resolution", "") or "").strip()
            resolution_text = f", {html.escape(resolution)}" if resolution else ""
            custody = str(_outlaw_row_value(case_row, "custody_status", "") or "").strip()
            custody_text = f", custody {html.escape(custody)}" if custody else ""
            case_items.append(f"{accused}: {offense} ({status}{resolution_text}{custody_text})")
    event_items: list[str] = []
    if _has_table(con, "simulation_events"):
        events_has_world = "world" in _table_columns(con, "simulation_events")
        where_world = "world = ? and " if events_has_world else ""
        params: list[object] = [world] if events_has_world else []
        params.extend([rid, rid])
        try:
            event_rows = con.execute(
                f"""
                select id as event_id, sim_year, event_type, payload_json
                from simulation_events
                where {where_world}event_type like 'outlaw_%'
                  and (
                    json_extract(payload_json, '$.outlaw_refuge_id') = ?
                    or json_extract(payload_json, '$.refuge_id') = ?
                  )
                order by sim_year desc, id desc
                limit 8
                """,
                tuple(params),
            ).fetchall()
            for event in reversed(event_rows):
                event_items.append(
                    f"{html.escape(_format_year(event['sim_year']))}: "
                    f"{_event_sentence_html(con, world, event, None)}"
                )
        except sqlite3.Error:
            event_items = []
    title = _outlaw_refuge_display_name(con, world, row)
    subtitle_bits: list[str] = []
    if region_name:
        subtitle_bits.append(region_name)
    if near_name:
        subtitle_bits.append(f"near {near_name}")
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(title)}</h2>'
        f'<div class="place-subtitle">{html.escape(" | ".join(subtitle_bits))}</div>'
        f'<div class="place-grid">{cards}</div>'
        '<div class="place-columns">'
        f'<section><h3>Outlaw Cases</h3>{_outlaw_html_list(case_items)}</section>'
        f'<section><h3>Recent Refuge Events</h3>{_outlaw_html_list(event_items)}</section>'
        '</div>'
        '</div>'
    )


def render_outlaw_refuge_detail(world: str, refuge_id: object) -> str:
    rid = str(refuge_id or "").strip()
    if not world:
        return '<div class="place-sheet muted">Choose a world.</div>'
    if not rid:
        return '<div class="place-sheet muted">Click an outlaw refuge row to inspect it.</div>'
    path = _db_path(world, "Save DB")
    if not path.exists():
        return f'<div class="place-sheet muted">{html.escape(str(path))} is missing.</div>'
    with _connect_readonly(path) as con:
        saved_world = _resolve_saved_world(con, world)
        return _render_outlaw_refuge_sheet(con, saved_world, rid)


def load_outlaw_refuges_browser(
    world: str,
    status_filter: str,
    search: str,
    limit: object,
) -> tuple[gr.Dataframe, str, list[str]]:
    if not world:
        return _empty_outlaw_refuges_frame(), "Choose a world.", []
    row_limit = _safe_int(limit, 50, 1, 250)
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _empty_outlaw_refuges_frame(), f"{path} is missing. Run a simulation first.", []
    with _connect_readonly(path) as con:
        if not _has_table(con, "simulation_outlaw_refuges"):
            return _empty_outlaw_refuges_frame(), "No simulation_outlaw_refuges table found.", []
        saved_world = _resolve_saved_world(con, world)
        rows, total = _query_outlaw_refuges(
            con,
            search=search or "",
            status_filter=status_filter,
            limit=row_limit,
        )
        values = []
        keys = []
        for row in rows:
            refuge_id = str(row["refuge_id"])
            region_id = _outlaw_refuge_region_id(row)
            near_sid = _outlaw_refuge_near_settlement_id(row)
            active_cases = _outlaw_row_value(row, "active_case_count", None)
            if active_cases in (None, ""):
                active_cases = _outlaw_refuge_active_case_count(con, refuge_id)
            values.append(
                {
                    "Refuge": _outlaw_refuge_display_name(con, saved_world, row),
                    "Status": _outlaw_row_value(row, "status", ""),
                    "Region": _safe_region_label(con, saved_world, region_id) if region_id else "",
                    "Near Settlement": _safe_settlement_name(con, saved_world, near_sid) if near_sid else "",
                    "Band Size": _outlaw_row_value(row, "band_size", ""),
                    "Active Cases": active_cases,
                    "Founded": _outlaw_row_value(row, "founded_year", ""),
                    "Discovered": _outlaw_row_value(row, "discovered_year", ""),
                    "Abandoned": _outlaw_row_value(row, "abandoned_year", ""),
                    "Concealment": _fmt_number(_outlaw_row_value(row, "concealment_01", "")),
                    "Support": _fmt_number(_outlaw_row_value(row, "support_01", "")),
                    "Last Activity": _outlaw_row_value(row, "last_activity_year", ""),
                }
            )
            keys.append(refuge_id)
    filter_text = f" | filters: {status_filter or 'Active'}"
    if search:
        filter_text += f", search={search!r}"
    saved_world_note = f" | saved world: {saved_world}" if saved_world != (world or "").strip() else ""
    status = (
        f"{path.name}: showing {len(values)} of {total} outlaw refuges{filter_text}{saved_world_note}. "
        "Click a row to inspect the refuge."
    )
    return _dataframe(values, OUTLAW_REFUGE_BROWSER_HEADERS), status, keys


def select_outlaw_refuge_from_table(keys: object, world: str, evt: gr.SelectData) -> str:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        refuge_id = keys[int(row_index)]  # type: ignore[index]
    except Exception:
        return '<div class="place-sheet muted">Click an outlaw refuge row to inspect it.</div>'
    return render_outlaw_refuge_detail(world, refuge_id)


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
    return f"{_person_name(person)} ({_person_years_label(person)})"


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
    years = _person_years_label(person)
    label = f"{name} <span class=\"muted\">({html.escape(years)})</span>"
    return (
        f'<a href="#" class="person-link" '
        f'aria-label="Open person record for {name}" '
        f'onclick="{_person_link_onclick(int(row["person_id"]))}">{label}</a>'
    )


def _person_years_label(person: dict[str, object]) -> str:
    years = f"b. {_format_year(person.get('birthyear'), unknown_text='?')}"
    if person.get("deathyear") is not None:
        years += f"-{_format_year(person.get('deathyear'))}"
    return years


def _person_link_html_compact(con: sqlite3.Connection, world: str, person_id: object) -> str:
    row, person = _lookup_person(con, world, person_id)
    if not row:
        return "Unknown"
    full_name = _person_name(person)
    shown = _person_first_name(person)
    title = f"{full_name} ({_person_years_label(person)})"
    return (
        f'<a href="#" class="person-link" '
        f'title="{html.escape(title, quote=True)}" '
        f'aria-label="Open person record for {html.escape(full_name, quote=True)}" '
        f'onclick="{_person_link_onclick(int(row["person_id"]))}">{html.escape(shown)}</a>'
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
            select e.id as event_id, e.sim_year, e.event_type, e.payload_json
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
        select id as event_id, sim_year, event_type, payload_json
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


def _person_children_rows(
    con: sqlite3.Connection, world: str, person_id: object
) -> list[sqlite3.Row]:
    people_has_world = "world" in _table_columns(con, "simulation_people")
    return con.execute(
        f"""
        select *
        from simulation_people
        where {'world = ? and ' if people_has_world else ''}(father_id = ? or mother_id = ?)
        order by person_id
        """,
        (
            *([world] if people_has_world else []),
            person_id,
            person_id,
        ),
    ).fetchall()


def _row_alive(row: sqlite3.Row) -> bool:
    if "is_alive" not in row.keys():
        return False
    try:
        return bool(int(row["is_alive"]))
    except (TypeError, ValueError):
        return False


def _children_summary_text(children: list[sqlite3.Row]) -> str:
    total = len(children)
    if total == 0:
        return "No recorded children."
    child_word = "child" if total == 1 else "children"
    return f"{total} recorded {child_word}."


def _child_years_label(child: sqlite3.Row, person: dict[str, object]) -> str:
    birthyear = person.get("birthyear")
    birth_text = _format_year(birthyear)
    deathyear = person.get("deathyear")
    if deathyear not in (None, ""):
        return f"b. {birth_text}, d. {_format_year(deathyear)}"
    if _row_alive(child):
        return f"b. {birth_text}, alive"
    return f"b. {birth_text}, d. unknown"


def _truthy_marker(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _person_birth_status_label(person: dict[str, object]) -> str:
    status = str(person.get("legitimacy_status") or "").strip().replace("_", " ")
    relationship = (
        str(person.get("birth_relationship_type") or "").strip().replace("_", " ")
    )
    born_out = _truthy_marker(person.get("born_out_of_wedlock"))
    if status:
        if relationship:
            return f"{status} ({relationship})"
        return status
    if born_out:
        return "out of wedlock"
    return ""


def _person_child_items_html(
    con: sqlite3.Connection,
    world: str,
    children: list[sqlite3.Row],
    trait_slots: tuple[str, ...],
) -> list[str]:
    if not children:
        return ['<div class="relation muted">No recorded children</div>']
    items: list[str] = []
    for child in children:
        person = _person_from_row(child, trait_slots)
        title = f"{_person_name(person)} ({_person_years_label(person)})"
        years = _child_years_label(child, person)
        birth_status = _person_birth_status_label(person)
        status_html = (
            f'<br><span class="muted">{html.escape(birth_status)}</span>'
            if birth_status
            else ""
        )
        items.append(
            '<div class="relation relation-compact" '
            f'title="{html.escape(title, quote=True)}">'
            f'{_person_link_html_compact(con, world, child["person_id"])}'
            f'<br><span class="muted">{html.escape(years)}</span>'
            f'{status_html}'
            '</div>'
        )
    return items


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
    label = _person_first_name(person)
    full_name = _person_name(person)
    title = f"{full_name} ({_person_years_label(person)})"
    if _same_person_id(person_id, focus_person_id):
        return f'<strong title="{html.escape(title, quote=True)}">{html.escape(label)}</strong>'
    return (
        f'<a href="#" class="person-link" '
        f'title="{html.escape(title, quote=True)}" '
        f'aria-label="Open person record for {html.escape(full_name, quote=True)}" '
        f'onclick="{_person_link_onclick(int(row["person_id"]))}">{html.escape(label)}</a>'
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


def _is_household_childcare_payload(payload: dict[str, object], *job_keys: str) -> bool:
    jobs = {
        normalize_job_catalog_key(str(payload.get(key) or ""))
        for key in job_keys
        if payload.get(key)
    }
    if "child rearer" not in jobs:
        return False
    return (
        str(payload.get("job_market_type") or "").strip().lower() == "household_care"
        or str(payload.get("household_role") or "").strip().lower() == "primary_child_rearer"
        or str(payload.get("placement_reason") or "").strip().lower()
        == "primary_child_rearing"
    )


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


def _person_list_full_text(
    con: sqlite3.Connection,
    world: str,
    person_ids: object,
    *,
    limit: int = 5,
) -> str:
    if not isinstance(person_ids, list) or not person_ids:
        return "none"
    names: list[str] = []
    for pid in person_ids[:limit]:
        _row, person = _lookup_person(con, world, pid)
        names.append(_person_name(person) if person else "Unknown")
    shown = ", ".join(names)
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


def _event_id(event: sqlite3.Row) -> int | None:
    for key in ("event_id", "id"):
        if key not in event.keys():
            continue
        try:
            return int(event[key])
        except (TypeError, ValueError):
            return None
    return None


def _event_move_payload(
    con: sqlite3.Connection,
    event: sqlite3.Row,
    payload: dict[str, object],
    focus_person_id: object,
) -> dict[str, object]:
    event_id = _event_id(event)
    if event_id is None or not _has_relation(con, "simulation_event_moves_readable"):
        return payload
    try:
        rows = con.execute(
            """
            select *
            from simulation_event_moves_readable
            where event_id = ?
            order by
              case when moved_person_id = ? then 0 else 1 end,
              moved_person_id
            """,
            (event_id, focus_person_id),
        ).fetchall()
    except sqlite3.Error:
        return payload
    if not rows:
        return payload

    merged = dict(payload)
    first = rows[0]
    for key in (
        "moved_person_id",
        "from_settlement_id",
        "to_settlement_id",
        "from_region_id",
        "to_region_id",
        "move_reason",
    ):
        if merged.get(key) in (None, "") and key in first.keys():
            merged[key] = first[key]
    if not isinstance(merged.get("moved_person_ids"), list):
        merged["moved_person_ids"] = [row["moved_person_id"] for row in rows if row["moved_person_id"] is not None]
    return merged


def _event_readable_place_payload(
    con: sqlite3.Connection,
    event: sqlite3.Row,
    payload: dict[str, object],
) -> dict[str, object]:
    event_id = _event_id(event)
    if event_id is None or not _has_relation(con, "simulation_events_readable"):
        return payload
    try:
        row = con.execute(
            """
            select settlement_id, region_id
            from simulation_events_readable
            where id = ?
            """,
            (event_id,),
        ).fetchone()
    except sqlite3.Error:
        return payload
    if not row:
        return payload
    merged = dict(payload)
    if merged.get("to_settlement_id") in (None, "") and row["settlement_id"]:
        merged["to_settlement_id"] = row["settlement_id"]
    if merged.get("to_region_id") in (None, "") and row["region_id"]:
        merged["to_region_id"] = row["region_id"]
    return merged


def _event_job_seeker_move_payload(
    con: sqlite3.Connection,
    event: sqlite3.Row,
    payload: dict[str, object],
    focus_person_id: object,
) -> dict[str, object]:
    merged = dict(payload)
    if merged.get("from_settlement_id") not in (None, "") and merged.get("to_settlement_id") not in (None, ""):
        return merged
    event_year = _event_year(event)
    rows: list[sqlite3.Row] = []
    if event_year is not None and _has_relation(con, "simulation_event_moves_readable"):
        person_id = payload.get("person_id") or focus_person_id
        group_id = f"job_seeker:{person_id}:{event_year}" if person_id not in (None, "") else ""
        try:
            if group_id:
                rows = con.execute(
                    """
                    select *
                    from simulation_event_moves_readable
                    where source_event = 'job_seeker_migration'
                      and group_id = ?
                    order by
                      case when moved_person_id = ? then 0 else 1 end,
                      moved_person_id
                    """,
                    (group_id, focus_person_id),
                ).fetchall()
            if not rows:
                moved_ids = payload.get("moved_person_ids")
                if isinstance(moved_ids, list):
                    person_ids = [
                        int(pid)
                        for pid in moved_ids
                        if str(pid).strip().lstrip("-").isdigit()
                    ]
                else:
                    person_ids = []
                if not person_ids and person_id not in (None, ""):
                    person_ids = [int(person_id)]
                if person_ids:
                    placeholders = ",".join("?" for _ in person_ids)
                    rows = con.execute(
                        f"""
                        select *
                        from simulation_event_moves_readable
                        where source_event = 'job_seeker_migration'
                          and requested_year = ?
                          and moved_person_id in ({placeholders})
                        order by
                          case when moved_person_id = ? then 0 else 1 end,
                          planned_apply_year,
                          event_id,
                          moved_person_id
                        """,
                        (int(event_year), *person_ids, focus_person_id),
                    ).fetchall()
        except (sqlite3.Error, TypeError, ValueError):
            rows = []
    if rows:
        first = rows[0]
        for key in (
            "from_settlement_id",
            "to_settlement_id",
            "from_region_id",
            "to_region_id",
            "move_reason",
        ):
            if merged.get(key) in (None, "") and key in first.keys():
                merged[key] = first[key]
        if not isinstance(merged.get("moved_person_ids"), list):
            merged["moved_person_ids"] = [row["moved_person_id"] for row in rows if row["moved_person_id"] is not None]
        return merged
    return _event_readable_place_payload(con, event, merged)


def _movement_phrase(action: str, from_place: str, to_place: str) -> str:
    if from_place and to_place:
        return f"{action} from {from_place} to {to_place}"
    if to_place:
        return f"{action} to {to_place}"
    if from_place:
        return f"{action} from {from_place}"
    return action


def _event_murder_sentence(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    focus_person_id: object,
) -> str:
    killer_id = payload.get("killer_person_id") or payload.get("perpetrator_person_id") or payload.get("person_id")
    victim_id = payload.get("victim_person_id") or payload.get("target_person_id")
    killer = _short_person_for_event(con, world, killer_id, focus_person_id)
    victim = _short_person_for_event(con, world, victim_id, focus_person_id)
    incident_kind = str(payload.get("incident_kind") or "").strip().replace("_", " ")
    motive = str(payload.get("motive") or "").strip().replace("_", " ")
    context = [part for part in (incident_kind, f"motive: {motive}" if motive else "") if part]
    tail = f"; {'; '.join(context)}" if context else ""
    if _same_person_id(victim_id, focus_person_id):
        return f"{victim} was killed by {killer}{tail}."
    if _same_person_id(killer_id, focus_person_id):
        return f"{killer} killed {victim}{tail}."
    return f"{killer} killed {victim}{tail}."


def _event_murder_sentence_html(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    focus_person_id: object,
) -> str:
    killer_id = payload.get("killer_person_id") or payload.get("perpetrator_person_id") or payload.get("person_id")
    victim_id = payload.get("victim_person_id") or payload.get("target_person_id")
    killer = _short_person_html_for_event(con, world, killer_id, focus_person_id)
    victim = _short_person_html_for_event(con, world, victim_id, focus_person_id)
    incident_kind = str(payload.get("incident_kind") or "").strip().replace("_", " ")
    motive = str(payload.get("motive") or "").strip().replace("_", " ")
    details = _event_details_html(
        ("kind", incident_kind),
        ("motive", motive),
        ("place", _event_place_text(con, world, payload)),
    )
    if _same_person_id(victim_id, focus_person_id):
        return f"{victim} was killed by {killer}." + details
    if _same_person_id(killer_id, focus_person_id):
        return f"{killer} killed {victim}." + details
    return f"{killer} killed {victim}." + details


def _event_label_text(value: object, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text.replace("_", " ") if text else default


def _event_place_text(con: sqlite3.Connection, world: str, payload: dict[str, object]) -> str:
    settlement_id = (
        payload.get("settlement_id")
        or payload.get("near_settlement_id")
        or payload.get("custody_site_settlement_id")
        or payload.get("from_settlement_id")
    )
    settlement_table = _place_read_relation(con, "simulation_settlements")
    if settlement_id and _has_relation(con, settlement_table):
        settlement = _safe_settlement_name(con, world, settlement_id)
        if settlement:
            return settlement
    if settlement_id:
        return str(settlement_id)
    region = str(payload.get("region_id") or "").strip()
    if region:
        return _safe_region_label(con, world, region)
    return "an unrecorded place"


def _event_person_list(
    con: sqlite3.Connection,
    world: str,
    ids: object,
    focus_person_id: object,
    *,
    html_mode: bool,
) -> str:
    if not isinstance(ids, list) or not ids:
        return ""
    shown = (
        _short_person_html_for_event(con, world, pid, focus_person_id)
        if html_mode
        else _short_person_for_event(con, world, pid, focus_person_id)
        for pid in ids[:6]
    )
    text = ", ".join(shown)
    if len(ids) > 6:
        text += f", and {len(ids) - 6} more"
    return text


def _event_details_html(*items: tuple[str, object]) -> str:
    bits = [
        f"{label}: {value}"
        for label, value in items
        if value not in (None, "")
    ]
    if not bits:
        return ""
    return (
        '<details class="event-card-details">'
        '<summary>Details</summary>'
        f'<span>{html.escape("; ".join(bits))}</span>'
        '</details>'
    )


def _knowledge_focus_text(payload: dict[str, object]) -> tuple[str, bool]:
    specific = next(
        (
            str(payload.get(key) or "").strip()
            for key in (
                "innovation_analogue_name",
                "source_innovation_title",
                "innovation_title",
                "innovation_name",
                "discovery",
                "specific_effect",
            )
            if str(payload.get(key) or "").strip()
        ),
        "",
    )
    domain = _event_label_text(payload.get("knowledge_domain"), "knowledge")
    kind = _event_label_text(payload.get("incident_kind"), "knowledge work")
    if specific:
        return f"{specific}, {kind} in {domain}", True
    if domain != "knowledge":
        return f"a new {domain} practice", False
    return kind, False


def _property_crime_sentence(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    focus_person_id: object,
) -> str:
    perpetrator_id = payload.get("perpetrator_person_id") or payload.get("person_id")
    target_id = payload.get("target_person_id") or payload.get("victim_person_id")
    perpetrator = _short_person_for_event(con, world, perpetrator_id, focus_person_id)
    target = _short_person_for_event(con, world, target_id, focus_person_id)
    kind = _event_label_text(payload.get("incident_kind"), "property crime")
    place = _event_place_text(con, world, payload)
    bits = [f"{perpetrator} committed {kind} against {target} at {place}"]
    if payload.get("loss_value") not in (None, ""):
        bits.append(f"loss {_fmt_number(payload.get('loss_value'), 3)}")
    motive = _event_label_text(payload.get("motive"), "")
    if motive:
        bits.append(f"motive {motive}")
    witnesses = _event_person_list(
        con, world, payload.get("witness_person_ids") or payload.get("witnesses"), focus_person_id, html_mode=False
    )
    if witnesses:
        bits.append(f"witnesses {witnesses}")
    return "; ".join(bits) + "."


def _property_crime_sentence_html(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    focus_person_id: object,
) -> str:
    perpetrator_id = payload.get("perpetrator_person_id") or payload.get("person_id")
    target_id = payload.get("target_person_id") or payload.get("victim_person_id")
    perpetrator = _short_person_html_for_event(con, world, perpetrator_id, focus_person_id)
    target = _short_person_html_for_event(con, world, target_id, focus_person_id)
    kind = html.escape(_event_label_text(payload.get("incident_kind"), "property crime"))
    place = html.escape(_event_place_text(con, world, payload))
    motive = _event_label_text(payload.get("motive"), "")
    witnesses = _event_person_list(
        con, world, payload.get("witness_person_ids") or payload.get("witnesses"), focus_person_id, html_mode=False
    )
    details = _event_details_html(
        ("loss", _fmt_number(payload.get("loss_value"), 3) if payload.get("loss_value") not in (None, "") else ""),
        ("motive", motive),
        ("witnesses", witnesses),
        ("region", _event_label_text(payload.get("region_id"), "")),
        ("settlement", payload.get("settlement_id")),
    )
    return f"{perpetrator} committed {kind} against {target} at {place}." + details


def _knowledge_culture_sentence(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    focus_person_id: object,
) -> str:
    creator = _short_person_for_event(con, world, payload.get("creator_person_id") or payload.get("person_id"), focus_person_id)
    patron_id = payload.get("patron_person_id")
    patron = _short_person_for_event(con, world, patron_id, focus_person_id) if patron_id not in (None, "") else ""
    focus, has_specific = _knowledge_focus_text(payload)
    place = _event_place_text(con, world, payload)
    if has_specific:
        sentence = f"{creator} produced {focus} at {place}"
    else:
        sentence = f"{creator} left a documented mark on {focus} at {place}"
    if patron:
        sentence += f"; patron {patron}"
    if not has_specific:
        sentence += "; the record names the field but not a specific invention"
    return sentence + "."


def _knowledge_culture_sentence_html(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    focus_person_id: object,
) -> str:
    creator = _short_person_html_for_event(con, world, payload.get("creator_person_id") or payload.get("person_id"), focus_person_id)
    patron_id = payload.get("patron_person_id")
    patron = _short_person_html_for_event(con, world, patron_id, focus_person_id) if patron_id not in (None, "") else ""
    focus, has_specific = _knowledge_focus_text(payload)
    place = html.escape(_event_place_text(con, world, payload))
    if has_specific:
        sentence = f"{creator} produced {html.escape(focus)} at {place}"
    else:
        sentence = f"{creator} left a documented mark on {html.escape(focus)} at {place}"
    if patron:
        sentence += f"; patron {patron}"
    if not has_specific:
        sentence += "; the record names the field but not a specific invention"
    consequences = payload.get("consequences")
    knowledge_state = consequences.get("knowledge_state") if isinstance(consequences, dict) else {}
    if not isinstance(knowledge_state, dict):
        knowledge_state = {}
    details = _event_details_html(
        ("kind", _event_label_text(payload.get("incident_kind"), "")),
        ("state delta", _fmt_number(knowledge_state.get("state_delta"), 3)),
        ("novelty", _fmt_number(payload.get("novelty_value"), 3)),
        ("domain", _event_label_text(payload.get("knowledge_domain"), "")),
    )
    return sentence + "." + details


def _outlaw_refuge_label(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
) -> str:
    explicit = str(
        payload.get("outlaw_refuge_display_name")
        or payload.get("refuge_display_name")
        or ""
    ).strip()
    if explicit:
        return explicit
    refuge = str(payload.get("outlaw_refuge_id") or payload.get("refuge_id") or "").strip()
    if refuge:
        return _lookup_outlaw_refuge_display_name(
            con,
            world,
            refuge,
            region_id=payload.get("region_id"),
            near_settlement_id=payload.get("near_settlement_id") or payload.get("settlement_id"),
        )
    return "unknown refuge"


def _outlaw_event_sentence(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    event_type: str,
    focus_person_id: object,
) -> str:
    accused = _short_person_for_event(
        con,
        world,
        payload.get("accused_person_id") or payload.get("person_id"),
        focus_person_id,
    )
    offense = str(
        payload.get("incident_kind") or payload.get("offense_type") or "offense"
    ).replace("_", " ")
    place = _event_place_text(con, world, payload)
    refuge = _outlaw_refuge_label(con, world, payload)
    pressure = _event_float(payload, "pursuit_pressure_01")
    pressure_tail = f"; pursuit pressure {pressure:.2f}" if pressure is not None else ""
    if event_type == "outlaw_case_opened":
        return f"{accused} became wanted for {offense} at {place}{pressure_tail}."
    if event_type == "outlaw_flight":
        return f"{accused} fled ordinary settlement life for {refuge}{pressure_tail}."
    if event_type == "outlaw_refuge_joined":
        band = payload.get("band_size")
        band_tail = f"; band size {band}" if band not in (None, "") else ""
        return f"{accused} joined {refuge} near {place}{band_tail}."
    if event_type == "outlaw_raid":
        band = payload.get("band_size")
        band_tail = f"; band size {band}" if band not in (None, "") else ""
        return f"Outlaws from {refuge} raided near {place}{band_tail}."
    if event_type == "outlaw_pursuit":
        band = payload.get("band_size")
        band_tail = f"; band size {band}" if band not in (None, "") else ""
        return f"Forces pursued {accused} at {refuge}{band_tail}."
    if event_type == "outlaw_captured":
        expected = payload.get("custody_expected_release_year")
        expected_tail = f"; expected release {_format_year(expected)}" if expected not in (None, "") else ""
        return f"{accused} was captured into custody for {offense}{expected_tail}."
    if event_type == "outlaw_escape":
        return f"{accused} escaped custody and fled."
    if event_type == "outlaw_died_in_custody":
        return f"{accused} died in custody."
    if event_type == "outlaw_killed":
        return f"{accused} was killed during outlaw pursuit."
    if event_type == "outlaw_bought_off":
        buyoff = _event_float(payload, "buyoff_power_01")
        buyoff_tail = f"; buy-off power {buyoff:.2f}" if buyoff is not None else ""
        return f"{accused}'s wanted case was bought off or softened{buyoff_tail}."
    if event_type == "outlaw_returned":
        return f"{accused} returned from outlawry with the case resolved."
    if event_type == "outlaw_forgotten":
        return f"{accused}'s wanted case faded enough for return."
    return f"{event_type.replace('_', ' ')}: {accused}."


def _outlaw_event_sentence_html(
    con: sqlite3.Connection,
    world: str,
    payload: dict[str, object],
    event_type: str,
    focus_person_id: object,
) -> str:
    accused = _short_person_html_for_event(
        con,
        world,
        payload.get("accused_person_id") or payload.get("person_id"),
        focus_person_id,
    )
    offense = html.escape(
        str(payload.get("incident_kind") or payload.get("offense_type") or "offense").replace("_", " ")
    )
    place = html.escape(_event_place_text(con, world, payload))
    refuge = html.escape(_outlaw_refuge_label(con, world, payload))
    pressure = _event_float(payload, "pursuit_pressure_01")
    if event_type == "outlaw_case_opened":
        return (
            f"{accused} became wanted for {offense} at {place}."
            + _event_details_html(("pursuit pressure", f"{pressure:.2f}" if pressure is not None else ""))
        )
    if event_type == "outlaw_flight":
        return (
            f"{accused} fled ordinary settlement life for {refuge}."
            + _event_details_html(("pursuit pressure", f"{pressure:.2f}" if pressure is not None else ""))
        )
    if event_type == "outlaw_refuge_joined":
        band = payload.get("band_size")
        return (
            f"{accused} joined {refuge} near {place}."
            + _event_details_html(("band size", band))
        )
    if event_type == "outlaw_raid":
        band = payload.get("band_size")
        return (
            f"Outlaws from {refuge} raided near {place}."
            + _event_details_html(("band size", band))
        )
    if event_type == "outlaw_pursuit":
        band = payload.get("band_size")
        return (
            f"Forces pursued {accused} at {refuge}."
            + _event_details_html(("band size", band))
        )
    if event_type == "outlaw_captured":
        expected = payload.get("custody_expected_release_year")
        return (
            f"{accused} was captured into custody for {offense}."
            + _event_details_html(
                (
                    "expected release",
                    _format_year(expected) if expected not in (None, "") else "",
                )
            )
        )
    if event_type == "outlaw_escape":
        return f"{accused} escaped custody and fled."
    if event_type == "outlaw_died_in_custody":
        return f"{accused} died in custody."
    if event_type == "outlaw_killed":
        return f"{accused} was killed during outlaw pursuit."
    if event_type == "outlaw_bought_off":
        buyoff = _event_float(payload, "buyoff_power_01")
        return (
            f"{accused}'s wanted case was bought off or softened."
            + _event_details_html(("buy-off power", f"{buyoff:.2f}" if buyoff is not None else ""))
        )
    if event_type == "outlaw_returned":
        return f"{accused} returned from outlawry with the case resolved."
    if event_type == "outlaw_forgotten":
        return f"{accused}'s wanted case faded enough for return."
    return f"{html.escape(event_type.replace('_', ' '))}: {accused}."


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
        tail = ""
        if _truthy_marker(payload.get("born_out_of_wedlock")) or str(payload.get("legitimacy_status") or "").strip().lower() == "bastard":
            tail = " out of wedlock"
        return f"{child} was born{tail} to {parent_a} and {parent_b}."

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
        household_childcare = _is_household_childcare_payload(payload, "job")
        bits = (
            [f"{person} took on household childcare as {job}"]
            if household_childcare
            else [f"{person} became {job}"]
        )
        if descriptor and not household_childcare:
            bits.append(f"matched through {descriptor}")
        if fitness is not None:
            bits.append(f"fitness {fitness:.2f}")
        previous = payload.get("previous_job")
        if previous:
            bits.append(f"previously {previous}")
        return "; ".join(bits) + "."

    if event_type in {"elite_job_promoted", "guild_admission", "status_rise"}:
        new_job = payload.get("new_job") or payload.get("target_job")
        previous = payload.get("previous_job")
        old_standing = _event_float(payload, "previous_social_standing_01")
        new_standing = _event_float(payload, "new_social_standing_01")
        bits = [f"{person} rose in status"]
        if new_job:
            bits.append(f"new role {new_job}")
        if previous:
            bits.append(f"previously {previous}")
        if old_standing is not None and new_standing is not None:
            bits.append(f"standing {old_standing:.2f} -> {new_standing:.2f}")
        return "; ".join(bits) + "."

    if event_type == "patronage_granted":
        patron = _short_person_for_event(con, world, payload.get("patron_person_id"), focus_person_id)
        client = _short_person_for_event(con, world, payload.get("client_person_id") or payload.get("person_id"), focus_person_id)
        strength = _event_float(payload, "strength_01")
        tail = f" with strength {strength:.2f}" if strength is not None else ""
        return f"{patron} extended patronage to {client}{tail}."

    if event_type in {"status_fall", "bankruptcy", "elite_scandal"}:
        old_standing = _event_float(payload, "previous_social_standing_01")
        new_standing = _event_float(payload, "new_social_standing_01")
        reason = payload.get("fall_reason") or event_type
        bits = [f"{person}'s standing fell", f"reason {str(reason).replace('_', ' ')}"]
        if old_standing is not None and new_standing is not None:
            bits.append(f"standing {old_standing:.2f} -> {new_standing:.2f}")
        return "; ".join(bits) + "."

    if event_type == "elite_household_investment":
        kind = str(payload.get("investment_kind") or "investment").replace("_", " ")
        value = _event_float(payload, "investment_value")
        pool_delta = _event_float(payload, "prosperity_pool_delta")
        bits = [f"{person}'s household made a {kind}"]
        if value is not None:
            bits.append(f"value {value:.2f}")
        if pool_delta is not None:
            bits.append(f"settlement prosperity +{pool_delta:.2f}")
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
        if _is_household_childcare_payload(payload, "new_job", "job"):
            return f"{person} resumed household childcare as {new_job}{span}."
        return f"{person} found work as {new_job}{span}."

    if event_type == "murder":
        return _event_murder_sentence(con, world, payload, focus_person_id)

    if event_type == "property_crime":
        return _property_crime_sentence(con, world, payload, focus_person_id)

    if event_type == "knowledge_culture":
        return _knowledge_culture_sentence(con, world, payload, focus_person_id)

    if event_type.startswith("outlaw_"):
        return _outlaw_event_sentence(con, world, payload, event_type, focus_person_id)

    if event_type in {"settlement_moved", "job_seeker_migration"}:
        if event_type == "settlement_moved":
            payload = _event_move_payload(con, event, payload, focus_person_id)
        else:
            payload = _event_job_seeker_move_payload(con, event, payload, focus_person_id)
        from_place = _settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or "")
        to_place = _settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or "")
        reason = str(payload.get("move_reason") or event_type).replace("_", " ")
        action = "moved" if event_type == "settlement_moved" else "planned a job seeker move"
        movement = _movement_phrase(action, from_place, to_place)
        moved_ids = payload.get("moved_person_ids")
        if isinstance(moved_ids, list) and len(moved_ids) > 1:
            moved = ", ".join(
                _short_person_for_event(con, world, pid, focus_person_id)
                for pid in moved_ids[:6]
            )
            if len(moved_ids) > 6:
                moved += f", and {len(moved_ids) - 6} more"
            return f"{moved} {movement}; reason: {reason}."
        return f"{person} {movement}; reason: {reason}."

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
        cause = str(payload.get("death_cause") or payload.get("cause") or "").strip()
        if cause:
            return f"{person} died from {cause.replace('_', ' ')}."
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
        tail = ""
        if _truthy_marker(payload.get("born_out_of_wedlock")) or str(payload.get("legitimacy_status") or "").strip().lower() == "bastard":
            tail = " out of wedlock"
        return f"{child} was born{tail} to {parent_a} and {parent_b}."

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
        household_childcare = _is_household_childcare_payload(payload, "job")
        bits = (
            [f"{person} took on household childcare as {job}"]
            if household_childcare
            else [f"{person} became {job}"]
        )
        previous = payload.get("previous_job")
        details = _event_details_html(
            ("matched through", descriptor if descriptor and not household_childcare else ""),
            ("fitness", f"{fitness:.2f}" if fitness is not None else ""),
            ("previously", previous),
        )
        return "; ".join(bits) + "." + details

    if event_type in {"elite_job_promoted", "guild_admission", "status_rise"}:
        new_job = payload.get("new_job") or payload.get("target_job")
        previous = payload.get("previous_job")
        old_standing = _event_float(payload, "previous_social_standing_01")
        new_standing = _event_float(payload, "new_social_standing_01")
        bits = [f"{person} rose in status"]
        if new_job:
            bits.append(f"new role {html.escape(str(new_job))}")
        details = _event_details_html(
            ("previously", previous),
            (
                "standing",
                f"{old_standing:.2f} -> {new_standing:.2f}"
                if old_standing is not None and new_standing is not None
                else "",
            ),
        )
        return "; ".join(bits) + "." + details

    if event_type == "patronage_granted":
        patron = _short_person_html_for_event(con, world, payload.get("patron_person_id"), focus_person_id)
        client = _short_person_html_for_event(con, world, payload.get("client_person_id") or payload.get("person_id"), focus_person_id)
        strength = _event_float(payload, "strength_01")
        return (
            f"{patron} extended patronage to {client}."
            + _event_details_html(("strength", f"{strength:.2f}" if strength is not None else ""))
        )

    if event_type in {"status_fall", "bankruptcy", "elite_scandal"}:
        old_standing = _event_float(payload, "previous_social_standing_01")
        new_standing = _event_float(payload, "new_social_standing_01")
        reason = payload.get("fall_reason") or event_type
        bits = [
            f"{person}'s standing fell",
            f"reason {html.escape(str(reason).replace('_', ' '))}",
        ]
        details = _event_details_html(
            (
                "standing",
                f"{old_standing:.2f} -> {new_standing:.2f}"
                if old_standing is not None and new_standing is not None
                else "",
            )
        )
        return "; ".join(bits) + "." + details

    if event_type == "elite_household_investment":
        kind = html.escape(str(payload.get("investment_kind") or "investment").replace("_", " "))
        value = _event_float(payload, "investment_value")
        pool_delta = _event_float(payload, "prosperity_pool_delta")
        return (
            f"{person}'s household made a {kind}."
            + _event_details_html(
                ("value", f"{value:.2f}" if value is not None else ""),
                (
                    "settlement prosperity",
                    f"+{pool_delta:.2f}" if pool_delta is not None else "",
                ),
            )
        )

    if event_type == "job_lost":
        old_job = html.escape(str(payload.get("old_job") or "their job"))
        reason = str(payload.get("reason") or "unknown reason").replace("_", " ")
        nuance = _job_loss_nuance_parts(payload)
        details = _event_details_html(
            ("reason", reason),
            ("details", "; ".join(nuance)),
        )
        return f"{person} lost {old_job}." + details

    if event_type == "unemployment_started":
        reason = html.escape(str(payload.get("reason") or "unknown reason").replace("_", " "))
        last_job = payload.get("last_job")
        last = f" after {html.escape(str(last_job))}" if last_job else ""
        return f"{person} became unemployed{last}; reason: {reason}."

    if event_type == "unemployment_ended":
        new_job = html.escape(str(payload.get("new_job") or payload.get("job") or "work"))
        years = payload.get("unemployment_years")
        span = f" after {years} unemployed year{'s' if years != 1 else ''}" if years is not None else ""
        if _is_household_childcare_payload(payload, "new_job", "job"):
            return f"{person} resumed household childcare as {new_job}{span}."
        return f"{person} found work as {new_job}{span}."

    if event_type == "murder":
        return _event_murder_sentence_html(con, world, payload, focus_person_id)

    if event_type == "property_crime":
        return _property_crime_sentence_html(con, world, payload, focus_person_id)

    if event_type == "knowledge_culture":
        return _knowledge_culture_sentence_html(con, world, payload, focus_person_id)

    if event_type.startswith("outlaw_"):
        return _outlaw_event_sentence_html(con, world, payload, event_type, focus_person_id)

    if event_type in {"settlement_moved", "job_seeker_migration"}:
        if event_type == "settlement_moved":
            payload = _event_move_payload(con, event, payload, focus_person_id)
        else:
            payload = _event_job_seeker_move_payload(con, event, payload, focus_person_id)
        from_place = html.escape(_settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or ""))
        to_place = html.escape(_settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or ""))
        reason = html.escape(str(payload.get("move_reason") or event_type).replace("_", " "))
        action = "moved" if event_type == "settlement_moved" else "planned a job seeker move"
        movement = _movement_phrase(action, from_place, to_place)
        moved_ids = payload.get("moved_person_ids")
        if isinstance(moved_ids, list) and len(moved_ids) > 1:
            moved = ", ".join(
                _short_person_html_for_event(con, world, pid, focus_person_id)
                for pid in moved_ids[:6]
            )
            if len(moved_ids) > 6:
                moved += f", and {len(moved_ids) - 6} more"
            return f"{moved} {movement}; reason: {reason}."
        return f"{person} {movement}; reason: {reason}."

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
        outcome = str(payload.get("outcome") or "unknown").replace("_", " ")
        victim = _short_person_html_for_event(con, world, payload.get("victim_person_id") or focus_person_id, focus_person_id)
        if outcome == "run away":
            visible = f"{victim} ran away."
        elif outcome and outcome != "unknown":
            visible = f"{victim} faced a childcare crisis."
        else:
            visible = f"{victim}'s household had a childcare shortfall."
        details = _event_details_html(
            (
                "dependent minors",
                f"{minor_count} dependent minor{'s' if minor_count != 1 else ''}",
            ),
            ("care supply", f"{supply:.2f}" if supply is not None else ""),
            ("shortfall", f"{shortfall:.2f}" if shortfall is not None else ""),
            ("outcome", outcome),
            ("victim", _short_person_for_event(con, world, payload.get("victim_person_id") or focus_person_id, focus_person_id)),
        )
        return visible + details

    if event_type == "household_prosperity_crisis":
        before = _event_float(payload, "prosperity_before")
        after = _event_float(payload, "prosperity_after")
        purseholder = _short_person_html_for_event(con, world, payload.get("purseholder_person_id") or focus_person_id, focus_person_id)
        details = _event_details_html(
            (
                "savings",
                f"{before:.2f} -> {after:.2f}"
                if before is not None and after is not None
                else "",
            ),
            ("members", _person_list_full_text(con, world, payload.get("household_member_ids"))),
        )
        return f"{purseholder}'s household entered prosperity crisis." + details

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
        details = _event_details_html(
            ("score", f"{fitness:.2f}" if fitness is not None else ""),
            ("strengths near ideal", ", ".join(str(x) for x in near) if near else ""),
            ("high-deviation traits", ", ".join(str(x) for x in high) if high else ""),
        )
        return f"{person}'s career fitness was updated." + details

    if event_type == "death":
        cause = str(payload.get("death_cause") or payload.get("cause") or "").strip()
        if cause:
            return f"{person} died from {html.escape(cause.replace('_', ' '))}."
        return f"{person} died."

    details = payload.get("details")
    if details:
        return html.escape(str(details))
    other_id = _other_person_id(payload, focus_person_id)
    if other_id is not None:
        return f"{event_label}: {person} and {_short_person_html_for_event(con, world, other_id, focus_person_id)}."
    return f"{event_label}: {person}."


def _event_year(event: sqlite3.Row) -> int | None:
    try:
        return int(event["sim_year"])
    except (TypeError, ValueError):
        return None


def _history_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _history_open_end_year(person: dict[str, object], current_year: int | None) -> int | None:
    try:
        deathyear = person.get("deathyear")
        return int(deathyear) if deathyear is not None else current_year
    except (TypeError, ValueError):
        return current_year


def _history_year_range(start_year: object, end_year: object) -> str:
    start = _format_year(start_year)
    end = _format_year(end_year, unknown_text="present")
    return f"{start}-{end}"


def _history_year_span(entry: dict[str, object]) -> int | None:
    start = _history_int(entry.get("start_year"))
    end = _history_int(entry.get("end_year"))
    if start is None or end is None:
        return None
    return end - start


def _job_history_entries(
    events: list[sqlite3.Row],
    person: dict[str, object],
    current_year: int | None,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    open_entry: dict[str, object] | None = None
    open_end_year = _history_open_end_year(person, current_year)

    def close_open(year: int | None) -> None:
        nonlocal open_entry
        if open_entry is None:
            return
        open_entry["end_year"] = year if year is not None else open_end_year
        entries.append(open_entry)
        open_entry = None

    for event in events:
        event_type = str(event["event_type"] or "").strip()
        payload = _load_json_object(event["payload_json"])
        year = _event_year(event)
        if event_type == "job_assigned":
            close_open(year)
            job = str(payload.get("job") or "work").strip() or "work"
            open_entry = {"start_year": year, "end_year": None, "label": job}
        elif event_type == "job_lost":
            if open_entry is not None and str(open_entry.get("label") or "") != "Unemployed":
                close_open(year)
        elif event_type == "unemployment_started":
            if open_entry is not None and str(open_entry.get("label") or "") != "Unemployed":
                close_open(year)
            if open_entry is None:
                open_entry = {"start_year": year, "end_year": None, "label": "Unemployed"}
        elif event_type == "unemployment_ended":
            if open_entry is not None and str(open_entry.get("label") or "") == "Unemployed":
                close_open(year)

    close_open(open_end_year)
    if entries:
        return entries

    job = str(person.get("job") or "").strip()
    if job:
        return [
            {
                "start_year": person.get("job_assigned_year"),
                "end_year": open_end_year,
                "label": job,
            }
        ]
    if str(person.get("employment_status") or "").strip() == "unemployed":
        return [
            {
                "start_year": person.get("unemployment_started_year"),
                "end_year": open_end_year,
                "label": "Unemployed",
            }
        ]
    return []


def _relationship_other_person_id(payload: dict[str, object], focus_person_id: object) -> object:
    person_a = payload.get("person_a_id")
    person_b = payload.get("person_b_id")
    if _same_person_id(person_a, focus_person_id) and person_b not in (None, ""):
        return person_b
    if _same_person_id(person_b, focus_person_id) and person_a not in (None, ""):
        return person_a
    return None


def _merge_adjacent_relationship_entries(entries: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for entry in entries:
        if not merged:
            merged.append(dict(entry))
            continue
        prev = merged[-1]
        if str(prev.get("person_id")) != str(entry.get("person_id")):
            merged.append(dict(entry))
            continue
        try:
            prev_end = int(prev["end_year"]) if prev.get("end_year") is not None else None
            next_start = int(entry["start_year"]) if entry.get("start_year") is not None else None
        except (TypeError, ValueError):
            merged.append(dict(entry))
            continue
        if prev_end is None or next_start is None or next_start > prev_end + 1:
            merged.append(dict(entry))
            continue
        next_end = entry.get("end_year")
        try:
            if next_end is not None and (prev_end is None or int(next_end) > prev_end):
                prev["end_year"] = next_end
        except (TypeError, ValueError):
            prev["end_year"] = next_end
    return merged


def _relationship_history_entries(
    events: list[sqlite3.Row],
    focus_person_id: object,
    person: dict[str, object],
    current_year: int | None,
    *,
    formed_types: set[str],
    ended_types: set[str],
    current_person_key: str,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    open_entries: dict[str, dict[str, object]] = {}
    open_end_year = _history_open_end_year(person, current_year)

    def close_entry(other_id: object, year: int | None) -> None:
        key = str(other_id)
        entry = open_entries.pop(key, None)
        if entry is None:
            entries.append({"start_year": None, "end_year": year, "person_id": other_id})
            return
        entry["end_year"] = year if year is not None else open_end_year
        entries.append(entry)

    for event in events:
        event_type = str(event["event_type"] or "").strip()
        if event_type not in formed_types and event_type not in ended_types:
            continue
        payload = _load_json_object(event["payload_json"])
        other_id = _relationship_other_person_id(payload, focus_person_id)
        if other_id is None:
            continue
        year = _event_year(event)
        if event_type in formed_types:
            for existing_id in list(open_entries):
                close_entry(existing_id, year)
            open_entries[str(other_id)] = {
                "start_year": year,
                "end_year": None,
                "person_id": other_id,
            }
        else:
            close_entry(other_id, year)

    for other_id in list(open_entries):
        close_entry(other_id, open_end_year)

    if entries:
        return _merge_adjacent_relationship_entries(entries)

    current_other_id = person.get(current_person_key)
    if current_other_id not in (None, ""):
        return [{"start_year": None, "end_year": open_end_year, "person_id": current_other_id}]
    return []


def _history_entries_for_person(
    events: list[sqlite3.Row],
    person_id: object,
    person: dict[str, object],
    current_year: int | None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    jobs = _job_history_entries(events, person, current_year)
    partners = _relationship_history_entries(
        events,
        person_id,
        person,
        current_year,
        formed_types={"couple_formed", "same_sex_couple_formed"},
        ended_types={"couple_dissolved"},
        current_person_key="partner_person_id",
    )
    paramours = _relationship_history_entries(
        events,
        person_id,
        person,
        current_year,
        formed_types={"paramour_formed"},
        ended_types={"paramour_ended"},
        current_person_key="paramour_person_id",
    )
    return jobs, partners, paramours


def _history_duration(entry: dict[str, object]) -> int:
    span = _history_year_span(entry)
    if span is None:
        return 1
    return max(1, span)


def _history_duration_text(entry: dict[str, object]) -> str:
    duration = _history_duration(entry)
    return f"{duration} year{'s' if duration != 1 else ''}"


def _history_lifespan_bounds(
    entries: list[dict[str, object]],
    person: dict[str, object] | None,
    current_year: int | None,
) -> tuple[int, int]:
    person = person or {}
    start = _history_int(person.get("birthyear"))
    end = _history_open_end_year(person, current_year)
    known_years: list[int] = []
    for entry in entries:
        for key in ("start_year", "end_year"):
            year = _history_int(entry.get(key))
            if year is not None:
                known_years.append(year)
    if start is None:
        start = min(known_years) if known_years else 0
    if end is None:
        end = max(known_years) if known_years else start
    if end < start:
        end = start
    return start, end


def _history_tick_step(span: int) -> int:
    if span <= 10:
        return 1
    if span <= 30:
        return 5
    if span <= 120:
        return 10
    if span <= 250:
        return 25
    if span <= 600:
        return 50
    return 100


def _history_axis_ticks(start_year: int, end_year: int) -> list[int]:
    if end_year <= start_year:
        return [start_year]
    step = _history_tick_step(end_year - start_year)
    ticks = [start_year]
    while True:
        first = -(-start_year // step) * step
        generated: list[int] = []
        year = first
        while year < end_year:
            if year > start_year:
                generated.append(year)
            year += step
        candidate = [start_year, *generated, end_year]
        if len(candidate) <= 9 or step >= 1000:
            ticks = candidate
            break
        step *= 2
    deduped: list[int] = []
    for tick in ticks:
        if tick not in deduped:
            deduped.append(tick)
    return deduped


def _history_position_pct(year: int, start_year: int, end_year: int) -> float:
    span = max(1, end_year - start_year)
    return ((year - start_year) / float(span)) * 100.0


def _history_bar_position(
    entry: dict[str, object],
    start_year: int,
    end_year: int,
) -> tuple[float, float]:
    entry_start = _history_int(entry.get("start_year"))
    entry_end = _history_int(entry.get("end_year"))
    visible_start = max(start_year, entry_start if entry_start is not None else start_year)
    visible_end = min(end_year, entry_end if entry_end is not None else end_year)
    if visible_end < visible_start:
        visible_end = visible_start
    left = _history_position_pct(visible_start, start_year, end_year)
    right = _history_position_pct(visible_end, start_year, end_year)
    min_width = 0.8 if end_year > start_year else 100.0
    width = max(min_width, right - left)
    if left + width > 100.0:
        left = max(0.0, 100.0 - width)
    return left, min(width, 100.0)


def _history_bar_title(entry: dict[str, object], label: str) -> str:
    parts = [
        _history_year_range(entry.get("start_year"), entry.get("end_year")),
        _history_duration_text(entry),
    ]
    clean_label = str(label or "").strip()
    if clean_label:
        parts.append(clean_label)
    return " | ".join(parts)


def _history_lifespan_grid_html(
    entries: list[dict[str, object]],
    *,
    person: dict[str, object] | None,
    current_year: int | None,
    empty_text: str,
    row_key: Callable[[dict[str, object]], str],
    row_label_html: Callable[[dict[str, object]], str],
    bar_label_text: Callable[[dict[str, object]], str],
    skip_entry: Callable[[dict[str, object]], bool] | None = None,
    bar_class: Callable[[dict[str, object]], str] | None = None,
) -> list[str]:
    skip_entry = skip_entry or (lambda _entry: False)
    bar_class = bar_class or (lambda _entry: "")
    visible_entries = [entry for entry in entries if not skip_entry(entry)]
    if not visible_entries:
        return [f'<div class="relation muted">{html.escape(empty_text)}</div>']

    start_year, end_year = _history_lifespan_bounds(visible_entries, person, current_year)
    ticks = _history_axis_ticks(start_year, end_year)
    tick_marks = "".join(
        '<span class="history-axis-tick'
        f'{" history-axis-tick-up" if index % 2 else ""}'
        f'{" history-axis-tick-edge-start" if index == 0 else ""}'
        f'{" history-axis-tick-edge-end" if index == len(ticks) - 1 else ""}" '
        f'style="left: {_history_position_pct(tick, start_year, end_year):.3f}%">'
        f'{html.escape(_format_year(tick))}</span>'
        for index, tick in enumerate(ticks)
    )
    grid_lines = "".join(
        '<span class="history-gridline" '
        f'style="left: {_history_position_pct(tick, start_year, end_year):.3f}%"></span>'
        for tick in ticks
    )

    grouped: dict[str, list[dict[str, object]]] = {}
    for entry in visible_entries:
        grouped.setdefault(row_key(entry), []).append(entry)

    row_items: list[str] = [
        '<div class="history-axis-spacer" aria-hidden="true"></div>',
        f'<div class="history-axis">{tick_marks}</div>',
    ]
    for group_entries in grouped.values():
        first_entry = group_entries[0]
        bars: list[str] = []
        for entry in group_entries:
            left, width = _history_bar_position(entry, start_year, end_year)
            label = bar_label_text(entry)
            title = _history_bar_title(entry, label)
            extra_class = str(bar_class(entry) or "").strip()
            class_attr = (
                f'history-bar {html.escape(extra_class, quote=True)}'
                if extra_class
                else "history-bar"
            )
            bars.append(
                f'<span class="{class_attr}" tabindex="0" '
                f'style="--history-bar-left: {left:.3f}%; --history-bar-width: {width:.3f}%" '
                f'title="{html.escape(title, quote=True)}" '
                f'aria-label="{html.escape(title, quote=True)}">'
                f'<span class="sr-only">{html.escape(title)}</span>'
                '</span>'
            )
        row_items.append(
            f'<div class="history-row-label">{row_label_html(first_entry)}</div>'
            f'<div class="history-row-track">{grid_lines}{"".join(bars)}</div>'
        )

    axis_label = (
        f"{_format_year(start_year)} to {_format_year(end_year)}"
        if start_year != end_year
        else _format_year(start_year)
    )
    return [
        '<div class="history-lifespan-grid" '
        f'aria-label="Timeline from {html.escape(axis_label, quote=True)}">'
        + "".join(row_items)
        + "</div>"
    ]


def _job_history_items_html(
    entries: list[dict[str, object]],
    person: dict[str, object] | None = None,
    current_year: int | None = None,
) -> list[str]:
    def label(entry: dict[str, object]) -> str:
        return str(entry.get("label") or "Unknown").strip() or "Unknown"

    return _history_lifespan_grid_html(
        entries,
        person=person,
        current_year=current_year,
        empty_text="No recorded job history",
        row_key=lambda entry: label(entry).casefold(),
        row_label_html=lambda entry: html.escape(label(entry)),
        bar_label_text=label,
        skip_entry=lambda entry: label(entry) == "Unemployed",
    )


def _relationship_history_items_html(
    con: sqlite3.Connection,
    world: str,
    entries: list[dict[str, object]],
    empty_text: str,
    person: dict[str, object] | None = None,
    current_year: int | None = None,
) -> list[str]:
    return _history_lifespan_grid_html(
        entries,
        person=person,
        current_year=current_year,
        empty_text=empty_text,
        row_key=lambda entry: str(entry.get("person_id")),
        row_label_html=lambda entry: _person_link_html_compact(con, world, entry.get("person_id")),
        bar_label_text=lambda entry: _person_link_text(con, world, entry.get("person_id")),
    )


def _combined_relationship_history_entries(
    partner_entries: list[dict[str, object]],
    paramour_entries: list[dict[str, object]],
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for kind, entries in (("Partner", partner_entries), ("Paramour", paramour_entries)):
        for entry in entries:
            out.append({**entry, "relationship_kind": kind})
    return sorted(
        out,
        key=lambda entry: (
            _history_int(entry.get("start_year"))
            if _history_int(entry.get("start_year")) is not None
            else -10**12,
            _history_int(entry.get("end_year"))
            if _history_int(entry.get("end_year")) is not None
            else 10**12,
            str(entry.get("relationship_kind") or ""),
            str(entry.get("person_id") or ""),
        ),
    )


def _relationship_kind_label(entry: dict[str, object]) -> str:
    kind = str(entry.get("relationship_kind") or "Partner").strip()
    return "Paramour" if kind.lower() == "paramour" else "Partner"


def _combined_relationship_history_items_html(
    con: sqlite3.Connection,
    world: str,
    entries: list[dict[str, object]],
    person: dict[str, object] | None = None,
    current_year: int | None = None,
) -> list[str]:
    if not entries:
        return ['<div class="relation muted">No recorded relationship history</div>']
    legend = (
        '<div class="relationship-history-legend" aria-label="Relationship legend">'
        '<span class="relationship-history-key">'
        '<span class="relationship-history-swatch relationship-history-swatch-partner" aria-hidden="true"></span>'
        "Partner</span>"
        '<span class="relationship-history-key">'
        '<span class="relationship-history-swatch relationship-history-swatch-paramour" aria-hidden="true"></span>'
        "Paramour</span>"
        "</div>"
    )
    grid = _history_lifespan_grid_html(
        entries,
        person=person,
        current_year=current_year,
        empty_text="No recorded relationship history",
        row_key=lambda entry: str(entry.get("person_id")),
        row_label_html=lambda entry: _person_link_html_compact(con, world, entry.get("person_id")),
        bar_label_text=lambda entry: (
            f"{_relationship_kind_label(entry)} with "
            f"{_person_link_text(con, world, entry.get('person_id'))}"
        ),
        bar_class=lambda entry: (
            "history-bar-paramour"
            if _relationship_kind_label(entry) == "Paramour"
            else "history-bar-partner"
        ),
    )
    return [legend, *grid]


def _job_history_lines(entries: list[dict[str, object]]) -> list[str]:
    if not entries:
        return ["- No recorded job history."]
    return [
        f"- {_history_year_range(entry.get('start_year'), entry.get('end_year'))}: {entry.get('label') or 'Unknown'}"
        for entry in entries
    ]


def _relationship_history_lines(
    con: sqlite3.Connection,
    world: str,
    entries: list[dict[str, object]],
    empty_text: str,
) -> list[str]:
    if not entries:
        return [f"- {empty_text}."]
    return [
        f"- {_history_year_range(entry.get('start_year'), entry.get('end_year'))}: "
        f"{_person_link_text(con, world, entry.get('person_id'))}"
        for entry in entries
    ]


def _combined_relationship_history_lines(
    con: sqlite3.Connection,
    world: str,
    entries: list[dict[str, object]],
) -> list[str]:
    if not entries:
        return ["- No recorded relationship history."]
    return [
        f"- {_history_year_range(entry.get('start_year'), entry.get('end_year'))}: "
        f"{_relationship_kind_label(entry)} - "
        f"{_person_link_text(con, world, entry.get('person_id'))}"
        for entry in entries
    ]


def _row_value(row: sqlite3.Row, key: str, default: object = None) -> object:
    return row[key] if key in row.keys() else default


def _person_obligation_rows(
    con: sqlite3.Connection, world: str, person_id: object
) -> list[sqlite3.Row]:
    if not _has_relation(con, "simulation_obligations_readable"):
        return []
    try:
        return con.execute(
            """
            SELECT *
            FROM simulation_obligations_readable
            WHERE owed_by_person_id = ? OR owed_to_person_id = ?
            ORDER BY
              COALESCE(start_year, source_event_year, 0),
              source_event_id,
              obligation_id
            LIMIT 30
            """,
            (person_id, person_id),
        ).fetchall()
    except sqlite3.Error:
        return []


def _person_reputation_mark_rows(
    con: sqlite3.Connection, world: str, person_id: object
) -> list[sqlite3.Row]:
    if not _has_relation(con, "simulation_reputation_marks_readable"):
        return []
    try:
        return con.execute(
            """
            SELECT *
            FROM simulation_reputation_marks_readable
            WHERE person_id = ?
            ORDER BY
              COALESCE(mark_year, source_event_year, 0),
              source_event_id,
              reputation_mark_id
            LIMIT 30
            """,
            (person_id,),
        ).fetchall()
    except sqlite3.Error:
        return []


def _person_legal_fallout_rows(
    con: sqlite3.Connection, world: str, person_id: object
) -> list[sqlite3.Row]:
    if not _has_relation(con, "simulation_legal_fallout_readable"):
        return []
    try:
        return con.execute(
            """
            SELECT *
            FROM simulation_legal_fallout_readable
            WHERE principal_person_id = ?
               OR opposing_person_id = ?
               OR related_person_id = ?
            ORDER BY
              COALESCE(start_year, source_event_year, 0),
              source_event_id,
              fallout_id
            LIMIT 30
            """,
            (person_id, person_id, person_id),
        ).fetchall()
    except sqlite3.Error:
        return []


def _person_outlaw_case_rows(
    con: sqlite3.Connection, world: str, person_id: object
) -> list[sqlite3.Row]:
    if not _has_relation(con, "simulation_outlaw_cases_readable"):
        return []
    try:
        return con.execute(
            """
            SELECT *
            FROM simulation_outlaw_cases_readable
            WHERE accused_person_id = ?
               OR victim_person_id = ?
               OR target_person_id = ?
            ORDER BY
              COALESCE(start_year, source_event_year, 0),
              case_key
            LIMIT 30
            """,
            (person_id, person_id, person_id),
        ).fetchall()
    except sqlite3.Error:
        return []


def _person_knowledge_effect_rows(
    events: list[sqlite3.Row], person_id: object
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in events:
        event_type = str(_row_value(event, "event_type") or "").strip()
        if event_type != "knowledge_culture":
            continue
        payload = _load_json_object(_row_value(event, "payload_json"))
        creator_id = payload.get("creator_person_id")
        patron_id = payload.get("patron_person_id")
        roles: list[str] = []
        if _same_person_id(creator_id, person_id):
            roles.append("creator")
        if _same_person_id(patron_id, person_id):
            roles.append("patron")
        if not roles:
            continue
        consequences = payload.get("consequences")
        if not isinstance(consequences, dict):
            consequences = {}
        knowledge_state = consequences.get("knowledge_state")
        if not isinstance(knowledge_state, dict):
            knowledge_state = {}
        focus, has_specific_focus = _knowledge_focus_text(payload)
        rows.append(
            {
                "event_id": _event_id(event),
                "year": _row_value(event, "sim_year"),
                "role": ", ".join(roles),
                "incident_kind": payload.get("incident_kind"),
                "focus": focus,
                "has_specific_focus": has_specific_focus,
                "domain": knowledge_state.get("domain")
                or payload.get("knowledge_domain"),
                "state_delta": knowledge_state.get("state_delta"),
                "novelty_value": payload.get("novelty_value"),
                "creator_person_id": creator_id,
                "patron_person_id": patron_id,
                "settlement_id": payload.get("settlement_id"),
                "region_id": payload.get("region_id"),
            }
        )
    return rows[:30]


def _year_span_text(start_year: object, end_year: object = None) -> str:
    return _format_year_span(start_year, end_year)


def _place_tail(
    row: sqlite3.Row | dict[str, object],
    con: sqlite3.Connection | None = None,
    world: str = "",
) -> str:
    getter = row.get if isinstance(row, dict) else lambda key, default=None: _row_value(row, key, default)
    settlement_id = str(getter("settlement_id") or "").strip()
    region_id = str(getter("region_id") or "").strip()
    shown: list[str] = []
    if settlement_id:
        shown.append(
            _safe_settlement_name(con, world, settlement_id)
            if con is not None
            else settlement_id
        )
    if region_id:
        shown.append(
            _safe_region_label(con, world, region_id)
            if con is not None
            else region_id
        )
    return f"; place: {', '.join(shown)}" if shown else ""


def _detail_bits(*items: tuple[str, object]) -> str:
    bits: list[str] = []
    for label, value in items:
        if value in (None, ""):
            continue
        bits.append(f"{label}: {value}")
    return "; ".join(bits)


def _person_consequence_summary_cards(
    obligations: list[sqlite3.Row],
    reputation_marks: list[sqlite3.Row],
    legal_fallout: list[sqlite3.Row],
    outlaw_cases: list[sqlite3.Row],
    knowledge_effects: list[dict[str, object]],
) -> list[str]:
    return [
        _render_detail_card("Obligations", len(obligations)),
        _render_detail_card("Reputation Marks", len(reputation_marks)),
        _render_detail_card("Legal Fallout", len(legal_fallout)),
        _render_detail_card("Outlawry", len(outlaw_cases)),
        _render_detail_card("Knowledge Effects", len(knowledge_effects)),
    ]


def _person_obligation_items_html(
    con: sqlite3.Connection,
    world: str,
    rows: list[sqlite3.Row],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ['<div class="relation muted">No recorded obligations</div>']
    items: list[str] = []
    for row in rows:
        owed_by = _row_value(row, "owed_by_person_id")
        owed_to = _row_value(row, "owed_to_person_id")
        if _same_person_id(owed_by, focus_person_id):
            role = "owes"
            other = owed_to
        else:
            role = "is owed by"
            other = owed_by
        detail = _detail_bits(
            ("status", _row_value(row, "status")),
            ("strength", _fmt_number(_row_value(row, "strength"))),
            ("source", str(_row_value(row, "source_event_type") or "").replace("_", " ")),
        )
        if detail:
            detail += _place_tail(row, con, world)
        items.append(
            '<div class="relation consequence-row consequence-obligation">'
            f'<strong>{html.escape(_year_span_text(_row_value(row, "start_year"), _row_value(row, "expected_end_year")))} · '
            f'Obligation: {html.escape(str(_row_value(row, "obligation_type") or "unknown").replace("_", " "))}</strong><br>'
            f'{_short_person_html_for_event(con, world, focus_person_id, focus_person_id)} '
            f'{html.escape(role)} {_person_link_html(con, world, other)}'
            f'<br><span class="muted">{html.escape(detail)}</span>'
            '</div>'
        )
    return items


def _person_reputation_mark_items_html(
    con: sqlite3.Connection,
    world: str,
    rows: list[sqlite3.Row],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ['<div class="relation muted">No recorded reputation marks</div>']
    items: list[str] = []
    for row in rows:
        before = _row_value(row, "reputation_before") or "unknown"
        after = _row_value(row, "reputation_after") or "unknown"
        axis = str(_row_value(row, "reputation_axis") or "reputation").replace("_", " ")
        detail = _detail_bits(
            ("direction", _row_value(row, "direction")),
            ("strength", _fmt_number(_row_value(row, "mark_strength"))),
            ("source", str(_row_value(row, "source_event_type") or "").replace("_", " ")),
        )
        if detail:
            detail += _place_tail(row, con, world)
        items.append(
            '<div class="relation consequence-row consequence-reputation">'
            f'<strong>{html.escape(_year_span_text(_row_value(row, "mark_year")))} · '
            f'Reputation: {html.escape(axis)}</strong><br>'
            f'Changed from {html.escape(str(before))} to {html.escape(str(after))}'
            f'<br><span class="muted">{html.escape(detail)}</span>'
            '</div>'
        )
    return items


def _person_legal_fallout_role(row: sqlite3.Row, focus_person_id: object) -> str:
    if _same_person_id(_row_value(row, "principal_person_id"), focus_person_id):
        return "principal"
    if _same_person_id(_row_value(row, "opposing_person_id"), focus_person_id):
        return "opposing party"
    if _same_person_id(_row_value(row, "related_person_id"), focus_person_id):
        return "related person"
    return "related"


def _person_legal_fallout_items_html(
    con: sqlite3.Connection,
    world: str,
    rows: list[sqlite3.Row],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ['<div class="relation muted">No recorded legal fallout</div>']
    items: list[str] = []
    for row in rows:
        people = [
            ("principal", _row_value(row, "principal_person_id")),
            ("opposing", _row_value(row, "opposing_person_id")),
            ("related", _row_value(row, "related_person_id")),
        ]
        people_bits = [
            f"{label}: {_short_person_html_for_event(con, world, pid, focus_person_id)}"
            for label, pid in people
            if pid not in (None, "")
        ]
        detail = _detail_bits(
            ("role", _person_legal_fallout_role(row, focus_person_id)),
            ("status", _row_value(row, "status")),
            ("severity", _fmt_number(_row_value(row, "severity"))),
            ("source", str(_row_value(row, "source_event_type") or "").replace("_", " ")),
        )
        if detail:
            detail += _place_tail(row, con, world)
        items.append(
            '<div class="relation consequence-row consequence-legal">'
            f'<strong>{html.escape(_year_span_text(_row_value(row, "start_year"), _row_value(row, "expected_resolution_year")))} · '
            f'Legal Fallout: {html.escape(str(_row_value(row, "fallout_type") or "unknown").replace("_", " "))}</strong><br>'
            f'{"; ".join(people_bits)}'
            f'<br><span class="muted">{html.escape(detail)}</span>'
            '</div>'
        )
    return items


def _person_outlaw_case_role(row: sqlite3.Row, focus_person_id: object) -> str:
    if _same_person_id(_row_value(row, "accused_person_id"), focus_person_id):
        return "accused"
    if _same_person_id(_row_value(row, "victim_person_id"), focus_person_id):
        return "victim"
    if _same_person_id(_row_value(row, "target_person_id"), focus_person_id):
        return "target"
    return "related"


def _person_outlaw_case_items_html(
    con: sqlite3.Connection,
    world: str,
    rows: list[sqlite3.Row],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ['<div class="relation muted">No recorded outlawry</div>']
    items: list[str] = []
    for row in rows:
        accused = _short_person_html_for_event(
            con, world, _row_value(row, "accused_person_id"), focus_person_id
        )
        victim_id = _row_value(row, "victim_person_id")
        target_id = _row_value(row, "target_person_id")
        affected = victim_id if victim_id not in (None, "") else target_id
        affected_html = (
            _short_person_html_for_event(con, world, affected, focus_person_id)
            if affected not in (None, "")
            else '<span class="muted">none</span>'
        )
        detail = _detail_bits(
            ("role", _person_outlaw_case_role(row, focus_person_id)),
            ("status", _row_value(row, "status")),
            ("resolution", _row_value(row, "resolution")),
            ("custody", _row_value(row, "custody_status")),
            ("custody type", str(_row_value(row, "custody_type") or "").replace("_", " ")),
            (
                "custody site",
                _safe_settlement_name(con, world, _row_value(row, "custody_site_settlement_id"))
                if _row_value(row, "custody_site_settlement_id") not in (None, "")
                else "",
            ),
            ("expected release", _format_year(_row_value(row, "custody_expected_release_year")) if _row_value(row, "custody_expected_release_year") not in (None, "") else ""),
            ("severity", _fmt_number(_row_value(row, "severity_01"))),
            ("knownness", _fmt_number(_row_value(row, "knownness_01"))),
            ("pursuit", _fmt_number(_row_value(row, "pursuit_pressure_01"))),
            ("buy-off", _fmt_number(_row_value(row, "buyoff_power_01"))),
            ("refuge", _outlaw_case_refuge_label(con, world, row)),
        )
        if detail:
            detail += _place_tail(row, con, world)
        offense = str(
            _row_value(row, "offense_kind") or _row_value(row, "offense_type") or "outlaw case"
        ).replace("_", " ")
        offense_type = str(_row_value(row, "offense_type") or "").strip().lower()
        if offense_type == "murder" and affected not in (None, ""):
            visible_line = f"{accused} murdered {affected_html}"
        elif affected not in (None, ""):
            visible_line = f"{accused} committed {html.escape(offense)} against {affected_html}"
        else:
            visible_line = f"{accused} became wanted for {html.escape(offense)}"
        detail_html = (
            '<details class="event-card-details">'
            '<summary>Details</summary>'
            f'<span>{html.escape(detail)}</span>'
            '</details>'
            if detail
            else ""
        )
        items.append(
            '<div class="relation consequence-row consequence-outlaw">'
            f'<strong>{html.escape(_year_span_text(_row_value(row, "start_year"), _row_value(row, "resolved_year")))} · '
            f'Outlawry: {html.escape(offense)}</strong><br>'
            f'{visible_line}'
            f'{detail_html}'
            '</div>'
        )
    return items


def _person_knowledge_effect_items_html(
    con: sqlite3.Connection,
    world: str,
    rows: list[dict[str, object]],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ['<div class="relation muted">No recorded knowledge/domain effects</div>']
    items: list[str] = []
    for row in rows:
        domain = str(row.get("domain") or "unknown domain").replace("_", " ")
        kind = str(row.get("incident_kind") or "knowledge culture").replace("_", " ")
        focus = str(row.get("focus") or "").strip() or domain
        has_specific_focus = bool(row.get("has_specific_focus"))
        creator = _short_person_html_for_event(
            con, world, row.get("creator_person_id"), focus_person_id
        )
        patron_id = row.get("patron_person_id")
        patron = (
            f"; patron: {_short_person_html_for_event(con, world, patron_id, focus_person_id)}"
            if patron_id not in (None, "")
            else ""
        )
        visible_line = (
            f"{creator} produced {html.escape(focus)}{patron}"
            if has_specific_focus
            else (
                f"{creator} left a documented mark on {html.escape(focus)}{patron}; "
                "the record names the field but not a specific invention"
            )
        )
        detail = _detail_bits(
            ("role", row.get("role")),
            ("kind", kind),
            ("state delta", _fmt_number(row.get("state_delta"), digits=3)),
            ("novelty", _fmt_number(row.get("novelty_value"), digits=3)),
            ("domain", domain),
        )
        if detail:
            detail += _place_tail(row, con, world)
        detail_html = (
            '<details class="event-card-details">'
            '<summary>Details</summary>'
            f'<span>{html.escape(detail)}</span>'
            '</details>'
            if detail
            else ""
        )
        items.append(
            '<div class="relation consequence-row consequence-knowledge">'
            f'<strong>{html.escape(_year_span_text(row.get("year")))} · '
            f'Knowledge Effect: {html.escape(focus)}</strong><br>'
            f'{visible_line}'
            f'{detail_html}'
            '</div>'
        )
    return items


def _person_obligation_lines(
    con: sqlite3.Connection,
    world: str,
    rows: list[sqlite3.Row],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ["- No recorded obligations."]
    lines: list[str] = []
    for row in rows:
        owed_by = _row_value(row, "owed_by_person_id")
        owed_to = _row_value(row, "owed_to_person_id")
        if _same_person_id(owed_by, focus_person_id):
            role = f"owes {_person_link_text(con, world, owed_to)}"
        else:
            role = f"is owed by {_person_link_text(con, world, owed_by)}"
        details = _detail_bits(
            ("status", _row_value(row, "status")),
            ("strength", _fmt_number(_row_value(row, "strength"))),
        )
        if details:
            details = f"; {details}"
        lines.append(
            f"- {_year_span_text(_row_value(row, 'start_year'), _row_value(row, 'expected_end_year'))}: "
            f"{str(_row_value(row, 'obligation_type') or 'unknown').replace('_', ' ')}; {role}{details}."
        )
    return lines


def _person_reputation_mark_lines(rows: list[sqlite3.Row]) -> list[str]:
    if not rows:
        return ["- No recorded reputation marks."]
    return [
        (
            f"- {_year_span_text(_row_value(row, 'mark_year'))}: "
            f"{str(_row_value(row, 'reputation_axis') or 'reputation').replace('_', ' ')} "
            f"{_row_value(row, 'reputation_before') or 'unknown'} -> "
            f"{_row_value(row, 'reputation_after') or 'unknown'}; "
            f"direction {str(_row_value(row, 'direction') or 'stable')}, "
            f"strength {_fmt_number(_row_value(row, 'mark_strength'))}."
        )
        for row in rows
    ]


def _person_legal_fallout_lines(
    con: sqlite3.Connection,
    world: str,
    rows: list[sqlite3.Row],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ["- No recorded legal fallout."]
    lines: list[str] = []
    for row in rows:
        principal = _person_link_text(con, world, _row_value(row, "principal_person_id"))
        opposing = _person_link_text(con, world, _row_value(row, "opposing_person_id"))
        related = _person_link_text(con, world, _row_value(row, "related_person_id"))
        lines.append(
            f"- {_year_span_text(_row_value(row, 'start_year'), _row_value(row, 'expected_resolution_year'))}: "
            f"{str(_row_value(row, 'fallout_type') or 'unknown').replace('_', ' ')}; "
            f"role {_person_legal_fallout_role(row, focus_person_id)}; "
            f"principal {principal}; opposing {opposing}; related {related}; "
            f"status {str(_row_value(row, 'status') or 'active')}; "
            f"severity {_fmt_number(_row_value(row, 'severity'))}."
        )
    return lines


def _person_outlaw_case_lines(
    con: sqlite3.Connection,
    world: str,
    rows: list[sqlite3.Row],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ["- No recorded outlawry."]
    lines: list[str] = []
    for row in rows:
        accused = _person_link_text(con, world, _row_value(row, "accused_person_id"))
        affected_id = _row_value(row, "victim_person_id") or _row_value(row, "target_person_id")
        affected = (
            _person_link_text(con, world, affected_id)
            if affected_id not in (None, "")
            else "none"
        )
        offense = str(
            _row_value(row, "offense_kind") or _row_value(row, "offense_type") or "outlaw case"
        ).replace("_", " ")
        resolution = _row_value(row, "resolution") or "unresolved"
        refuge = _outlaw_case_refuge_label(con, world, row) or "none"
        custody_bits = _detail_bits(
            ("custody", _row_value(row, "custody_status")),
            ("custody type", str(_row_value(row, "custody_type") or "").replace("_", " ")),
            (
                "custody site",
                _safe_settlement_name(con, world, _row_value(row, "custody_site_settlement_id"))
                if _row_value(row, "custody_site_settlement_id") not in (None, "")
                else "",
            ),
            (
                "expected release",
                _format_year(_row_value(row, "custody_expected_release_year"))
                if _row_value(row, "custody_expected_release_year") not in (None, "")
                else "",
            ),
        )
        custody_tail = f"; {custody_bits}" if custody_bits else ""
        place_tail = _place_tail(row, con, world)
        lines.append(
            f"- {_year_span_text(_row_value(row, 'start_year'), _row_value(row, 'resolved_year'))}: "
            f"{offense}; role {_person_outlaw_case_role(row, focus_person_id)}; "
            f"accused {accused}; affected {affected}; "
            f"status {str(_row_value(row, 'status') or 'active')}; resolution {resolution}; "
            f"severity {_fmt_number(_row_value(row, 'severity_01'))}; "
            f"knownness {_fmt_number(_row_value(row, 'knownness_01'))}; "
            f"pursuit {_fmt_number(_row_value(row, 'pursuit_pressure_01'))}; "
            f"buy-off {_fmt_number(_row_value(row, 'buyoff_power_01'))}; refuge {refuge}{custody_tail}{place_tail}."
        )
    return lines


def _person_knowledge_effect_lines(
    con: sqlite3.Connection,
    world: str,
    rows: list[dict[str, object]],
    focus_person_id: object,
) -> list[str]:
    if not rows:
        return ["- No recorded knowledge/domain effects."]
    lines: list[str] = []
    for row in rows:
        creator = _person_link_text(con, world, row.get("creator_person_id"))
        patron_id = row.get("patron_person_id")
        patron = (
            f"; patron {_person_link_text(con, world, patron_id)}"
            if patron_id not in (None, "")
            else ""
        )
        focus = str(row.get("focus") or row.get("domain") or "unknown domain").replace("_", " ")
        sparse_note = "" if row.get("has_specific_focus") else "; field-level record"
        lines.append(
            f"- {_year_span_text(row.get('year'))}: "
            f"{focus}{sparse_note}; "
            f"role {row.get('role') or 'related'}; creator {creator}{patron}; "
            f"state delta {_fmt_number(row.get('state_delta'), digits=3)}, "
            f"novelty {_fmt_number(row.get('novelty_value'), digits=3)}."
        )
    return lines


def _genome_labels(con: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {
        str(row["trait"]): row
        for row in con.execute("select * from cfg.genome order by rowid")
        if row["trait"]
    }


def _genome_trait_order(con: sqlite3.Connection) -> dict[str, int]:
    ordered: list[str] = []
    seen: set[str] = set()
    queries = (
        """
        select trait
        from cfg.genome_save_columns
        where trait is not null and trim(trait) <> ''
        order by cast(sort_order as integer), slot
        """,
        """
        select trait
        from cfg.genome
        where trait is not null and trim(trait) <> ''
        order by rowid
        """,
    )
    for query in queries:
        try:
            rows = con.execute(query).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            trait = str(row["trait"] or "").strip()
            if trait and trait not in seen:
                seen.add(trait)
                ordered.append(trait)
    return {trait: index for index, trait in enumerate(ordered)}


def _ordered_genome_trait_names(
    traits: Iterable[object],
    trait_order: dict[str, int],
) -> list[str]:
    ordered_tail = len(trait_order)
    return sorted(
        {str(trait) for trait in traits if trait not in (None, "")},
        key=lambda trait: (trait_order.get(trait, ordered_tail), trait.casefold(), trait),
    )


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


def _render_detail_card(label: str, value: object, tooltip: str | None = None) -> str:
    shown = html.escape(str(value if value not in (None, "") else "Unknown"))
    title = f' title="{html.escape(str(tooltip), quote=True)}"' if tooltip else ""
    return f'<div class="detail-card"><div class="detail-label"{title}>{html.escape(label)}</div><div class="detail-value">{shown}</div></div>'


def _render_detail_card_html(label: str, value: str, tooltip: str | None = None) -> str:
    shown = value if value not in (None, "") else "Unknown"
    title = f' title="{html.escape(str(tooltip), quote=True)}"' if tooltip else ""
    return f'<div class="detail-card"><div class="detail-label"{title}>{html.escape(label)}</div><div class="detail-value">{shown}</div></div>'


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
        rows = _legacy_indices_module().top_legacy_index_scores(traits, limit=10)
    except Exception as exc:
        return f'<div class="muted">Could not calculate legacy indexes: {html.escape(str(exc))}</div>'
    cards: list[str] = []
    for row in rows:
        score = max(0.0, min(1.0, float(row.score)))
        if score < LEGACY_SCORE_MIN_DISPLAY:
            continue
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
    if not cards:
        return f'<div class="muted">No legacy indexes at or above {LEGACY_SCORE_MIN_DISPLAY:.2f}.</div>'
    return f'<div class="legacy-grid">{"".join(cards)}</div>'


def _format_01_score(value: object) -> str:
    if value in (None, ""):
        return "Unknown"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "Unknown"


def _format_archive_score(value: object) -> str:
    if value in (None, ""):
        return "Unknown"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "Unknown"


def _person_archive_score_row(
    con: sqlite3.Connection, person_id: object
) -> sqlite3.Row | None:
    if not _has_table(con, "simulation_person_archive_scores"):
        return None
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return None
    return con.execute(
        """
        select *
        from simulation_person_archive_scores
        where person_id = ?
        """,
        (pid,),
    ).fetchone()


def _person_archive_reason_rows(
    con: sqlite3.Connection, person_id: object, limit: int = 12
) -> list[dict[str, object]]:
    if not _has_table(con, "simulation_person_archive_score_reasons"):
        return []
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return []
    rows = con.execute(
        """
        select
            person_id,
            component_key,
            axis,
            contribution,
            source_kind,
            source_id,
            source_year,
            role,
            label,
            explanation,
            sort_rank,
            score_version
        from simulation_person_archive_score_reasons
        where person_id = ?
        order by abs(contribution) desc, component_key asc, sort_rank asc
        limit ?
        """,
        (pid, max(1, min(100, int(limit)))),
    ).fetchall()
    return [dict(row) for row in rows]


def _archive_score_payload(score: sqlite3.Row | None) -> dict[str, object]:
    if score is None or "component_json" not in score.keys():
        return {}
    try:
        payload = json.loads(str(score["component_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _strip_archive_summary_bucket(summary: object, bucket: object) -> str:
    text = str(summary or "").strip()
    bucket_text = str(bucket or "").strip()
    if not text or not bucket_text:
        return text
    prefix = f"{bucket_text[:1].upper()}{bucket_text[1:]}:"
    if text.lower().startswith(prefix.lower()):
        return text[len(prefix):].lstrip()
    return text


def _archive_score_summary(score: sqlite3.Row, payload: dict[str, object]) -> str:
    summary = str(payload.get("summary") or "").strip()
    if summary:
        return _strip_archive_summary_bucket(summary, score["recognition_bucket"])
    return (
        f"Narrative Heat {_format_archive_score(score['narrative_heat_total'])}, "
        f"ARI {_format_archive_score(score['archive_recognition_index'])}."
    )


def _archive_reason_from_payload(payload: dict[str, object], limit: int = 12) -> list[dict[str, object]]:
    raw = payload.get("top_reason_summaries") or payload.get("top_reasons") or []
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)][:limit]


def _archive_reason_contribution(value: object) -> str:
    try:
        return f"{float(value):+.1f}"
    except (TypeError, ValueError):
        return "+0.0"


def _archive_reason_line(reason: dict[str, object], *, include_explanation: bool = True) -> str:
    label = str(reason.get("label") or reason.get("component_key") or "Archive reason").strip()
    contribution = _archive_reason_contribution(reason.get("contribution"))
    bits = [f"{label} ({contribution})"]
    source_bits: list[str] = []
    if reason.get("source_year") not in (None, ""):
        source_bits.append(str(reason.get("source_year")))
    if reason.get("source_kind"):
        source = str(reason.get("source_kind"))
        if reason.get("source_id") not in (None, ""):
            source += f" #{reason.get('source_id')}"
        source_bits.append(source)
    if reason.get("role"):
        source_bits.append(str(reason.get("role")).replace("_", " "))
    if source_bits:
        bits.append("[" + ", ".join(source_bits) + "]")
    explanation = str(reason.get("explanation") or "").strip()
    if include_explanation and explanation:
        bits.append(explanation)
    return " - ".join(bits)


def _archive_reason_items_html(reasons: list[dict[str, object]]) -> str:
    if not reasons:
        return '<div class="relation muted">No cached reasons in this group yet.</div>'
    items = []
    for reason in reasons[:5]:
        line = _archive_reason_line(reason)
        items.append(f'<div class="relation">{html.escape(line)}</div>')
    return "".join(items)


def _archive_reason_groups_html(reasons: list[dict[str, object]]) -> str:
    narrative = [
        r for r in reasons
        if str(r.get("axis") or "") == "narrative" and float(r.get("contribution") or 0.0) > 0.0
    ]
    ari = [
        r for r in reasons
        if str(r.get("axis") or "") == "ari" and float(r.get("contribution") or 0.0) > 0.0
    ]
    obscurity = [
        r for r in reasons
        if str(r.get("axis") or "") == "obscurity" or float(r.get("contribution") or 0.0) < 0.0
    ]
    groups = [
        ("Narrative Heat Drivers", narrative),
        ("ARI Drivers", ari),
        ("Obscurity / Suppression Drivers", obscurity),
    ]
    html_parts = []
    for title, rows in groups:
        html_parts.append(
            '<div>'
            f'<h4 class="subsection-title">{html.escape(title)}</h4>'
            f'<div class="relation-list">{_archive_reason_items_html(rows)}</div>'
            '</div>'
        )
    return '<div class="consequence-groups archive-reason-groups">' + "".join(html_parts) + "</div>"


def _archive_score_component_cards(score: sqlite3.Row) -> str:
    component_labels = [
        ("Events", "narrative_heat_events"),
        ("Contradictions", "narrative_heat_contradictions"),
        ("Consequences", "narrative_heat_consequences"),
        ("Social", "narrative_heat_social"),
        ("Rarity", "narrative_heat_rarity"),
        ("Volatility", "narrative_heat_volatility"),
        ("Legacy", "narrative_heat_legacy"),
    ]
    return "".join(
        _render_detail_card(
            label,
            _format_archive_score(score[key]),
            ARCHIVE_SCORE_DEFINITIONS.get(label),
        )
        for label, key in component_labels
        if key in score.keys()
    )


def _render_archive_score_section(
    person_id: object,
    score: sqlite3.Row | None,
    reasons: list[dict[str, object]] | None = None,
) -> str:
    if score is None:
        return ""
    payload = _archive_score_payload(score)
    reason_rows = list(reasons or _archive_reason_from_payload(payload))
    summary = _archive_score_summary(score, payload)
    violet = "Yes" if int(score["violet_marginalia"] or 0) else "No"
    cards = [
        _render_detail_card(
            "Narrative Heat",
            _format_archive_score(score["narrative_heat_total"]),
            ARCHIVE_SCORE_DEFINITIONS["Narrative Heat"],
        ),
        _render_detail_card(
            "ARI",
            _format_archive_score(score["archive_recognition_index"]),
            ARCHIVE_SCORE_DEFINITIONS["ARI"],
        ),
        _render_detail_card(
            "Hidden Heat",
            _format_archive_score(score["hidden_heat"]),
            ARCHIVE_SCORE_DEFINITIONS["Hidden Heat"],
        ),
        _render_detail_card(
            "Violet Marginalia",
            f"{violet} ({_format_archive_score(score['violet_marginalia_score'])})",
            ARCHIVE_SCORE_DEFINITIONS["Violet Marginalia"],
        ),
        _render_detail_card("Archive Quadrant", score["recognition_bucket"], ARCHIVE_SCORE_DEFINITIONS["Archive Quadrant"]),
        _render_detail_card("Narrative", score["narrative_bucket"], ARCHIVE_SCORE_DEFINITIONS["Narrative"]),
    ]
    components = _archive_score_component_cards(score)
    reason_groups = _archive_reason_groups_html(reason_rows)
    pid = html.escape(str(person_id))
    return f"""
      <section aria-labelledby="person-{pid}-archive-scores">
        <h3 id="person-{pid}-archive-scores" class="section-title">Archive Scores</h3>
        <p><strong>Why this person was noticed:</strong> {html.escape(summary)}</p>
        <div class="detail-grid">{''.join(cards)}</div>
        {reason_groups}
        <div class="detail-grid">{components}</div>
      </section>
    """


def _archive_score_share_lines(
    score: sqlite3.Row | None, reasons: list[dict[str, object]] | None = None
) -> list[str]:
    if score is None:
        return []
    payload = _archive_score_payload(score)
    reason_rows = list(reasons or _archive_reason_from_payload(payload))
    summary = _archive_score_summary(score, payload)
    caveats = payload.get("data_caveats") if isinstance(payload.get("data_caveats"), list) else []
    violet = "yes" if int(score["violet_marginalia"] or 0) else "no"
    lines = [
        "Archive Scores:",
        f"- Narrative heat: {_format_archive_score(score['narrative_heat_total'])}",
        f"- ARI: {_format_archive_score(score['archive_recognition_index'])}",
        f"- Hidden heat: {_format_archive_score(score['hidden_heat'])}",
        f"- Violet marginalia: {violet} ({_format_archive_score(score['violet_marginalia_score'])})",
        f"- Archive quadrant: {score['recognition_bucket']}",
        f"- Why noticed: {summary}",
    ]
    top_reasons = reason_rows[:3]
    if top_reasons:
        lines.append("- Top reasons:")
        lines.extend(f"  - {_archive_reason_line(reason)}" for reason in top_reasons)
    if caveats:
        lines.append(f"- Caveat: {str(caveats[0])}")
    lines.append("")
    return lines


def _status_echelon_label(world: str, person: dict[str, object]) -> str:
    try:
        from library.status_echelons import StatusEchelonCatalog

        catalog = StatusEchelonCatalog.load(_db_path(world, "Config DB"))
        echelon = catalog.echelon_for_values(
            social_standing_01=(
                float(person.get("social_standing_01"))
                if person.get("social_standing_01") is not None
                else None
            ),
            household_prosperity=(
                float(person.get("household_prosperity"))
                if person.get("household_prosperity") is not None
                else None
            ),
            social_class_band=str(person.get("social_class_band") or ""),
            job_market_type=str(person.get("job_market_type") or ""),
        )
        return echelon.display_name
    except Exception:
        standing = float(person.get("social_standing_01") or 0.0)
        if standing >= 0.74:
            return "Elite"
        if standing >= 0.60:
            return "Notable"
        if standing >= 0.48:
            return "Professional"
        if standing >= 0.32:
            return "Comfortable"
        if standing >= 0.16:
            return "Laboring"
        return "Marginal"


def _person_patronage_rows(
    con: sqlite3.Connection, person_id: int
) -> list[sqlite3.Row]:
    if not _has_table(con, "simulation_patronage_ties"):
        return []
    return con.execute(
        """
        select patron_person_id, client_person_id, tie_kind, strength_01,
               status, start_year, end_year, settlement_id
        from simulation_patronage_ties
        where patron_person_id = ? or client_person_id = ?
        order by
            case status when 'active' then 0 else 1 end,
            coalesce(start_year, 0) desc,
            patron_person_id,
            client_person_id
        limit 12
        """,
        (int(person_id), int(person_id)),
    ).fetchall()


def _person_patronage_items_html(
    con: sqlite3.Connection, world: str, rows: list[sqlite3.Row], person_id: int
) -> str:
    if not rows:
        return '<div class="relation muted">No patronage ties recorded</div>'
    items: list[str] = []
    for row in rows:
        patron_id = int(row["patron_person_id"])
        client_id = int(row["client_person_id"])
        if patron_id == int(person_id):
            other = _person_link_html(con, world, client_id)
            role = "Patron of"
        else:
            other = _person_link_html(con, world, patron_id)
            role = "Client of"
        years = ""
        if row["start_year"] is not None:
            years = f" since {html.escape(_format_year(row['start_year']))}"
        strength = _format_01_score(row["strength_01"])
        kind = str(row["tie_kind"] or "patronage").replace("_", " ")
        status = str(row["status"] or "active").replace("_", " ")
        items.append(
            '<div class="relation">'
            f'<strong>{html.escape(role)}</strong> {other}<br>'
            f'<span class="muted">{html.escape(kind)} · {html.escape(status)} · strength {strength}{years}</span>'
            '</div>'
        )
    return "".join(items)


_STATUS_EVENT_TYPES = {
    "status_rise",
    "elite_job_promoted",
    "guild_admission",
    "patronage_granted",
    "status_fall",
    "bankruptcy",
    "elite_household_investment",
    "elite_scandal",
}


def _person_status_mobility_items_html(
    con: sqlite3.Connection,
    world: str,
    events: list[sqlite3.Row],
    person_id: int,
) -> str:
    items: list[str] = []
    for event in events:
        if str(event["event_type"] or "") not in _STATUS_EVENT_TYPES:
            continue
        sentence = _event_sentence_html(con, world, event, person_id)
        items.append(
            '<div class="relation event-card">'
            f'<strong class="event-card-title">{html.escape(_format_year(event["sim_year"]))} · '
            f'{html.escape(str(event["event_type"]).replace("_", " ").title())}</strong><br>'
            f'<span class="event-card-body">{sentence}</span>'
            '</div>'
        )
    if not items:
        return '<div class="relation muted">No status movement recorded</div>'
    return "".join(items[:12])


def _render_person_sheet(con: sqlite3.Connection, world: str, row: sqlite3.Row, person: dict[str, object]) -> str:
    current_year = _current_year(con, world)
    name = html.escape(_person_name(person))
    birthyear = person.get("birthyear")
    deathyear = person.get("deathyear")
    end_year = deathyear if deathyear is not None else current_year
    age = int(end_year) - int(birthyear) if birthyear is not None and end_year is not None else "Unknown"
    life = "Alive" if row["is_alive"] else "Dead"
    years = (
        f"{_format_year(birthyear, unknown_text='?')} - "
        f"{_format_year(deathyear if deathyear is not None else current_year, unknown_text='?')}"
    )
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
    trait_slots = _trait_slots_for_world(world)
    children = _person_children_rows(con, world, row["person_id"])
    child_summary = _children_summary_text(children)
    child_items = _person_child_items_html(con, world, children, trait_slots)

    events = _person_event_rows(con, world, row["person_id"])
    job_history, partner_history, paramour_history = _history_entries_for_person(
        events,
        row["person_id"],
        person,
        current_year,
    )
    relationship_history = _combined_relationship_history_entries(
        partner_history,
        paramour_history,
    )
    obligation_rows = _person_obligation_rows(con, world, row["person_id"])
    reputation_mark_rows = _person_reputation_mark_rows(con, world, row["person_id"])
    legal_fallout_rows = _person_legal_fallout_rows(con, world, row["person_id"])
    outlaw_case_rows = _person_outlaw_case_rows(con, world, row["person_id"])
    knowledge_effect_rows = _person_knowledge_effect_rows(events, row["person_id"])
    archive_score = _person_archive_score_row(con, row["person_id"])
    archive_reason_rows = _person_archive_reason_rows(con, row["person_id"])
    archive_score_section = _render_archive_score_section(
        row["person_id"], archive_score, archive_reason_rows
    )
    consequence_summary_cards = _person_consequence_summary_cards(
        obligation_rows,
        reputation_mark_rows,
        legal_fallout_rows,
        outlaw_case_rows,
        knowledge_effect_rows,
    )
    obligation_items = _person_obligation_items_html(
        con, world, obligation_rows, row["person_id"]
    )
    reputation_items = _person_reputation_mark_items_html(
        con, world, reputation_mark_rows, row["person_id"]
    )
    legal_fallout_items = _person_legal_fallout_items_html(
        con, world, legal_fallout_rows, row["person_id"]
    )
    outlaw_case_items = _person_outlaw_case_items_html(
        con, world, outlaw_case_rows, row["person_id"]
    )
    knowledge_effect_items = _person_knowledge_effect_items_html(
        con, world, knowledge_effect_rows, row["person_id"]
    )
    job_items = _job_history_items_html(job_history, person, current_year)
    relationship_items = _combined_relationship_history_items_html(
        con,
        world,
        relationship_history,
        person,
        current_year,
    )
    event_items: list[str] = []
    for event in events:
        sentence = _event_sentence_html(con, world, event, row["person_id"])
        event_items.append(
            '<div class="relation event-card">'
            f'<strong class="event-card-title">{html.escape(_format_year(event["sim_year"]))} · '
            f'{html.escape(str(event["event_type"]).replace("_", " ").title())}</strong><br>'
            f'<span class="event-card-body">{sentence}</span>'
            '</div>'
        )
    if not event_items:
        event_items = ['<div class="relation muted">No matching events found</div>']
    patronage_rows = _person_patronage_rows(con, int(row["person_id"]))
    patronage_items = _person_patronage_items_html(
        con, world, patronage_rows, int(row["person_id"])
    )
    status_mobility_items = _person_status_mobility_items_html(
        con, world, events, int(row["person_id"])
    )

    labels = _genome_labels(con)
    trait_order = _genome_trait_order(con)
    base_genome = person.get("genome") or {}
    current_genome = person.get("mind_body") or base_genome
    legacy_scores_html = _render_legacy_scores(current_genome)
    trait_rows: list[str] = []
    if isinstance(current_genome, dict) or isinstance(base_genome, dict):
        trait_names = _ordered_genome_trait_names(
            (
                set(current_genome if isinstance(current_genome, dict) else {})
                | set(base_genome if isinstance(base_genome, dict) else {})
            ),
            trait_order,
        )
        display_rows = [
            (trait, *_trait_display_values(str(trait), current_genome, base_genome))
            for trait in trait_names
        ]
        for trait, value, base_value, shown_value in display_rows:
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
    host_html = (
        _person_link_html(con, world, person.get("host_person_id"))
        if person.get("host_person_id")
        else '<span class="muted">None</span>'
    )
    employer_html = (
        _person_link_html(con, world, person.get("employer_person_id"))
        if person.get("employer_person_id")
        else '<span class="muted">None</span>'
    )
    work_cards = [
        _render_detail_card(
            "Work",
            _display_job_label(person.get("job"))
            or person.get("employment_status")
            or "None",
        ),
        _render_detail_card(
            "Work Market",
            str(person.get("job_market_type") or "none").replace("_", " "),
        ),
        _render_detail_card(
            "Housing",
            str(person.get("housing_status") or "unknown").replace("_", " "),
        ),
        _render_detail_card(
            "Household Care",
            str(person.get("household_role") or "none").replace("_", " "),
        ),
        _render_detail_card_html("Host", host_html),
        _render_detail_card_html("Service Attachment", employer_html),
        _render_detail_card(
            "Class",
            str(person.get("social_class_band") or "unknown").replace("_", " "),
        ),
        _render_detail_card("Status Echelon", _status_echelon_label(world, person)),
        _render_detail_card("Standing", _format_01_score(person.get("social_standing_01"))),
        _render_detail_card("Societal Impact", _format_01_score(person.get("societal_impact_01"))),
        _render_detail_card("Perceived Worth", _format_01_score(person.get("perceived_worth_01"))),
        _render_detail_card(
            "Outlaw Status",
            str(person.get("outlaw_status") or "none").replace("_", " "),
        ),
        _render_detail_card(
            "Outlaw Refuge",
            _person_current_outlaw_refuge_label(con, world, person),
        ),
        _render_detail_card(
            "Outlaw Custody",
            _person_current_outlaw_custody_label(con, world, person),
        ),
        _render_detail_card("Patronage Ties", len(patronage_rows)),
    ]
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
      <section aria-labelledby="person-{row['person_id']}-work-standing">
        <h3 id="person-{row['person_id']}-work-standing" class="section-title">Work And Standing</h3>
        <div class="detail-grid">{''.join(work_cards)}</div>
        <div class="consequence-groups">
          <div>
            <h4 class="subsection-title">Patronage</h4>
            <div class="relation-list">{patronage_items}</div>
          </div>
          <div>
            <h4 class="subsection-title">Status Movement</h4>
            <div class="relation-list">{status_mobility_items}</div>
          </div>
        </div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-consequences" class="consequence-section">
        <h3 id="person-{row['person_id']}-consequences" class="section-title">Consequences</h3>
        <div class="detail-grid consequence-summary">{''.join(consequence_summary_cards)}</div>
        <div class="consequence-groups">
          <div>
            <h4 class="subsection-title">Active Obligations</h4>
            <div class="relation-list">{''.join(obligation_items)}</div>
          </div>
          <div>
            <h4 class="subsection-title">Reputation Marks</h4>
            <div class="relation-list">{''.join(reputation_items)}</div>
          </div>
          <div>
            <h4 class="subsection-title">Legal Fallout</h4>
            <div class="relation-list">{''.join(legal_fallout_items)}</div>
          </div>
          <div>
            <h4 class="subsection-title">Outlawry</h4>
            <div class="relation-list">{''.join(outlaw_case_items)}</div>
          </div>
          <div>
            <h4 class="subsection-title">Knowledge Effects</h4>
            <div class="relation-list">{''.join(knowledge_effect_items)}</div>
          </div>
        </div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-tags">
        <h3 id="person-{row['person_id']}-tags" class="section-title">Character Tags</h3>
        <div class="pill-list" aria-label="Character tags">{pill_html}</div>
      </section>
      {archive_score_section}
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
        <p class="history-summary">{html.escape(child_summary)}</p>
        <div class="relation-list">{''.join(child_items)}</div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-job-history">
        <h3 id="person-{row['person_id']}-job-history" class="section-title">Job History</h3>
        <div class="history-list">{''.join(job_items)}</div>
      </section>
      <section aria-labelledby="person-{row['person_id']}-relationship-history">
        <h3 id="person-{row['person_id']}-relationship-history" class="section-title">Relationship History</h3>
        <div class="history-list">{''.join(relationship_items)}</div>
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

    trait_slots = _trait_slots_for_world(world)
    children = _person_children_rows(con, world, row["person_id"])
    child_summary = _children_summary_text(children)
    child_lines: list[str] = []
    for child in children:
        child_person = _person_from_row(child, trait_slots)
        child_lines.append(
            f"- {_person_name(child_person)} "
            f"({_child_years_label(child, child_person)})"
        )
    if not child_lines:
        child_lines.append("- No recorded children.")

    events = _person_event_rows(con, world, row["person_id"])
    job_history, partner_history, paramour_history = _history_entries_for_person(
        events,
        row["person_id"],
        person,
        current_year,
    )
    relationship_history = _combined_relationship_history_entries(
        partner_history,
        paramour_history,
    )
    obligation_rows = _person_obligation_rows(con, world, row["person_id"])
    reputation_mark_rows = _person_reputation_mark_rows(con, world, row["person_id"])
    legal_fallout_rows = _person_legal_fallout_rows(con, world, row["person_id"])
    outlaw_case_rows = _person_outlaw_case_rows(con, world, row["person_id"])
    knowledge_effect_rows = _person_knowledge_effect_rows(events, row["person_id"])
    archive_score = _person_archive_score_row(con, row["person_id"])
    archive_reason_rows = _person_archive_reason_rows(con, row["person_id"])
    archive_score_lines = _archive_score_share_lines(archive_score, archive_reason_rows)
    obligation_lines = _person_obligation_lines(
        con, world, obligation_rows, row["person_id"]
    )
    reputation_mark_lines = _person_reputation_mark_lines(reputation_mark_rows)
    legal_fallout_lines = _person_legal_fallout_lines(
        con, world, legal_fallout_rows, row["person_id"]
    )
    outlaw_case_lines = _person_outlaw_case_lines(
        con, world, outlaw_case_rows, row["person_id"]
    )
    knowledge_effect_lines = _person_knowledge_effect_lines(
        con, world, knowledge_effect_rows, row["person_id"]
    )
    job_history_lines = _job_history_lines(job_history)
    relationship_history_lines = _combined_relationship_history_lines(
        con,
        world,
        relationship_history,
    )
    event_lines: list[str] = []
    for event in events:
        event_lines.append(
            f"- {_format_year(event['sim_year'])}: {_event_sentence(con, world, event, row['person_id'])}"
        )
    if not event_lines:
        event_lines.append("- No matching events found.")

    tags = [*list(person.get("genome_trait_phrases") or []), *list(person.get("genome_composite_names") or [])]
    tags_text = ", ".join(str(tag) for tag in tags) if tags else "No standout tags recorded."
    years = f"born {_format_year(birthyear)}"
    years += (
        f", died {_format_year(deathyear)}"
        if deathyear is not None
        else f", current year {_format_year(current_year)}"
    )

    outlaw_refuge_label = _person_current_outlaw_refuge_label(con, world, person)
    outlaw_custody_label = _person_current_outlaw_custody_label(con, world, person)
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
            f"Work: {_display_job_label(person.get('job')) or person.get('employment_status') or 'none'}.",
            f"Work market: {str(person.get('job_market_type') or 'none').replace('_', ' ')}.",
            f"Housing: {str(person.get('housing_status') or 'unknown').replace('_', ' ')}; household role: {str(person.get('household_role') or 'none').replace('_', ' ')}.",
            f"Service attachment: employer {_person_link_text(con, world, person.get('employer_person_id')) if person.get('employer_person_id') else 'none'}; host {_person_link_text(con, world, person.get('host_person_id')) if person.get('host_person_id') else 'none'}.",
            f"Standing: class {str(person.get('social_class_band') or 'unknown').replace('_', ' ')}, social standing {_format_01_score(person.get('social_standing_01'))}, societal impact {_format_01_score(person.get('societal_impact_01'))}, perceived worth {_format_01_score(person.get('perceived_worth_01'))}.",
            f"Outlawry: status {str(person.get('outlaw_status') or 'none').replace('_', ' ')}, refuge {outlaw_refuge_label}.",
            f"Custody: {outlaw_custody_label}.",
            f"Character tags: {tags_text}",
            "",
            *archive_score_lines,
            "Family:",
            f"- Father: {father}",
            f"- Mother: {mother}",
            f"- Partner: {partner}",
            f"- Paramour: {paramour}",
            "",
            "Children:",
            f"- {child_summary}",
            *child_lines,
            "",
            "Consequences:",
            f"- Obligations: {len(obligation_rows)}",
            f"- Reputation marks: {len(reputation_mark_rows)}",
            f"- Legal fallout: {len(legal_fallout_rows)}",
            f"- Outlawry: {len(outlaw_case_rows)}",
            f"- Knowledge effects: {len(knowledge_effect_rows)}",
            "",
            "Active Obligations:",
            *obligation_lines,
            "",
            "Reputation Marks:",
            *reputation_mark_lines,
            "",
            "Legal Fallout:",
            *legal_fallout_lines,
            "",
            "Outlawry:",
            *outlaw_case_lines,
            "",
            "Knowledge Effects:",
            *knowledge_effect_lines,
            "",
            "Job History:",
            *job_history_lines,
            "",
            "Relationship History:",
            *relationship_history_lines,
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


def _decode_almanack_key(value: object) -> dict[str, object] | None:
    try:
        data = json.loads(str(value or ""))
        source_kind = str(data.get("source_kind") or "detailed").strip()
        person_id = int(data.get("person_id") or 0)
        metric_key = str(data.get("metric_key") or "").strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if person_id <= 0:
        return None
    return {"source_kind": source_kind, "person_id": person_id, "metric_key": metric_key}


def _passive_almanack_row(con: sqlite3.Connection, person_id: int) -> sqlite3.Row | None:
    relation = (
        "simulation_people_light_readable"
        if _has_relation(con, "simulation_people_light_readable")
        else "simulation_people_light"
    )
    if not _has_table(con, "simulation_people_light"):
        return None
    return con.execute(
        f"""
        SELECT *
        FROM {_quote_identifier(relation)}
        WHERE person_id = ?
        """,
        (int(person_id),),
    ).fetchone()


def _passive_almanack_home(row: sqlite3.Row) -> str:
    for key in ("current_settlement_id", "birthplace_settlement_id"):
        if key in row.keys() and row[key]:
            return str(row[key])
    return ""


def render_passive_almanack_outputs(world: str, person_id: object) -> tuple[str, str]:
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return (
            '<div class="person-sheet muted">Click an Almanack row to inspect it.</div>',
            "Click an Almanack row to generate share text.",
        )
    path = _db_path(world, "Save DB")
    if not path.exists():
        return (
            f'<div class="person-sheet muted">{html.escape(str(path))} is missing.</div>',
            f"{path} is missing.",
        )
    with _connect_readonly(path) as con:
        row = _passive_almanack_row(con, pid)
        if row is None:
            return (
                f'<div class="person-sheet muted">No passive person #{pid} in {html.escape(world)}.</div>',
                f"No passive person #{pid} in {world}.",
            )
        name = str(row["name"] or "").strip() if "name" in row.keys() else f"Passive #{pid}"
        life = "Alive" if int(row["is_alive"] or 0) else "Dead"
        birthyear = row["birthyear"] if "birthyear" in row.keys() else ""
        deathyear = row["deathyear"] if "deathyear" in row.keys() else ""
        years = _format_year(birthyear, unknown_text="?")
        years += f" - {_format_year(deathyear)}" if deathyear not in (None, "") else ""
        home = _passive_almanack_home(row)
        job_family = row["job_family"] if "job_family" in row.keys() else ""
        child_count = row["child_count"] if "child_count" in row.keys() else 0
        partner = row["partner_name"] if "partner_name" in row.keys() else ""
        child_years = ""
        if "child_birthyears_json" in row.keys() and row["child_birthyears_json"]:
            try:
                child_years = ", ".join(
                    _format_year(y) for y in json.loads(str(row["child_birthyears_json"]))[:8]
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                child_years = ""
    cards = "".join(
        [
            _detail_card("Source", "Passive explicit"),
            _detail_card("Life", life),
            _detail_card("Years", years),
            _detail_card("Home", home or "Unknown"),
            _detail_card("Job Family", job_family or "Unknown"),
            _detail_card("Children", child_count),
            _detail_card("Partner", partner or "None"),
        ]
    )
    child_note = (
        f'<p class="place-muted">Recorded child birth years: {html.escape(child_years)}</p>'
        if child_years
        else ""
    )
    html_out = (
        '<article class="person-sheet">'
        f'<h2>{html.escape(name)}</h2>'
        f'<div class="person-subtitle">Passive person #{pid}</div>'
        f'<div class="place-grid">{cards}</div>'
        f"{child_note}"
        "</article>"
    )
    share = "\n".join(
        [
            name,
            f"Passive person ID: {pid}",
            f"Status: {life}; years {years}.",
            f"Home: {home or 'unknown'}.",
            f"Job family: {job_family or 'unknown'}.",
            f"Recorded children: {child_count}.",
        ]
    )
    return html_out, share


def _almanack_evidence_for_key(world: str, decoded: dict[str, object]) -> gr.Dataframe:
    metric_key = str(decoded.get("metric_key") or "").strip()
    if not metric_key:
        return _almanack_empty_evidence_frame()
    path = _db_path(world, "Save DB")
    if not path.exists():
        return _almanack_empty_evidence_frame()
    with _connect_readonly(path) as con:
        rows = query_person_almanack_evidence(
            con,
            str(decoded.get("source_kind") or "detailed"),
            int(decoded.get("person_id") or 0),
            metric_key,
            limit=50,
        )
    return _almanack_evidence_table(rows)


def render_almanack_outputs(world: str, key: object) -> tuple[str, str, gr.Dataframe]:
    decoded = _decode_almanack_key(key)
    if decoded is None:
        return (
            '<div class="person-sheet muted">Click an Almanack row to inspect it.</div>',
            "Click an Almanack row to generate share text.",
            _almanack_empty_evidence_frame(),
        )
    source_kind = str(decoded.get("source_kind") or "detailed")
    person_id = int(decoded.get("person_id") or 0)
    if source_kind == "passive":
        sheet, share = render_passive_almanack_outputs(world, person_id)
    else:
        sheet, share = render_person_outputs(world, person_id)
    return sheet, share, _almanack_evidence_for_key(world, decoded)


def select_almanack_from_table(
    almanack_keys: list[str], world: str, evt: gr.SelectData
) -> tuple[str, str, gr.Dataframe]:
    try:
        row_index = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index
        key = almanack_keys[int(row_index)]
    except Exception:
        return (
            '<div class="person-sheet muted">Click an Almanack row to inspect it.</div>',
            "Click an Almanack row to generate share text.",
            _almanack_empty_evidence_frame(),
        )
    return render_almanack_outputs(world, key)


def _almanack_duel_person_id(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _almanack_duel_html(result: dict[str, object]) -> str:
    person_a = result.get("person_a") if isinstance(result.get("person_a"), dict) else {}
    person_b = result.get("person_b") if isinstance(result.get("person_b"), dict) else {}
    name_a = html.escape(str(person_a.get("name") or "Person A"))
    name_b = html.escape(str(person_b.get("name") or "Person B"))
    sections = []
    for category in result.get("categories", []):
        if not isinstance(category, dict):
            continue
        lines = category.get("lines") if isinstance(category.get("lines"), list) else []
        if not lines:
            continue
        leader = html.escape(str(category.get("leader") or "Tie"))
        items = []
        for line in lines[:6]:
            if not isinstance(line, dict):
                continue
            items.append(
                '<div class="relation">'
                f'<strong>{html.escape(str(line.get("metric") or ""))}</strong><br>'
                f'A {html.escape(str(line.get("a") or 0))} / '
                f'B {html.escape(str(line.get("b") or 0))}. '
                f'{html.escape(str(line.get("why") or ""))}'
                '</div>'
            )
        sections.append(
            '<section>'
            f'<h3 class="section-title">{html.escape(str(category.get("category") or ""))}</h3>'
            f'<p class="place-muted">Leader: {leader}</p>'
            f'<div class="relation-list">{"".join(items)}</div>'
            '</section>'
        )
    if not sections:
        sections.append('<p class="place-muted">No cached Almanack overlap for this pair yet.</p>')
    return (
        '<article class="person-sheet">'
        f'<h2>Almanack Duel</h2>'
        f'<div class="person-subtitle">{name_a} vs {name_b}</div>'
        + "".join(sections)
        + "</article>"
    )


def _almanack_duel_text(result: dict[str, object]) -> str:
    person_a = result.get("person_a") if isinstance(result.get("person_a"), dict) else {}
    person_b = result.get("person_b") if isinstance(result.get("person_b"), dict) else {}
    lines = [
        "Almanack Duel",
        f"A: {person_a.get('name') or 'Person A'}",
        f"B: {person_b.get('name') or 'Person B'}",
        "",
    ]
    for category in result.get("categories", []):
        if not isinstance(category, dict):
            continue
        metric_lines = category.get("lines") if isinstance(category.get("lines"), list) else []
        if not metric_lines:
            continue
        lines.append(f"{category.get('category')}: leader {category.get('leader')}")
        for line in metric_lines[:5]:
            if isinstance(line, dict):
                lines.append(f"- {line.get('why')}")
        lines.append("")
    return "\n".join(lines).strip()


def load_almanack_duel(world: str, person_a: object, person_b: object) -> tuple[str, str]:
    a_id = _almanack_duel_person_id(person_a)
    b_id = _almanack_duel_person_id(person_b)
    if not world:
        return '<div class="person-sheet muted">Choose a world.</div>', "Choose a world."
    if a_id is None or b_id is None:
        return (
            '<div class="person-sheet muted">Enter two person ids to compare.</div>',
            "Enter two person ids to compare.",
        )
    path = _db_path(world, "Save DB")
    if not path.exists():
        return (
            f'<div class="person-sheet muted">{html.escape(str(path))} is missing.</div>',
            f"{path} is missing.",
        )
    with _connect_readonly(path) as con:
        result = query_person_almanack_duel(con, a_id, b_id)
    return _almanack_duel_html(result), _almanack_duel_text(result)


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
    if "current_settlement_key" in columns or "birthplace_settlement_key" in columns:
        current_sql = (
            "(select settlement_id from simulation_settlement_lookup "
            "where settlement_key = simulation_people.current_settlement_key)"
            if "current_settlement_key" in columns and _has_table(con, "simulation_settlement_lookup")
            else "null"
        )
        birthplace_sql = (
            "(select settlement_id from simulation_settlement_lookup "
            "where settlement_key = simulation_people.birthplace_settlement_key)"
            if "birthplace_settlement_key" in columns and _has_table(con, "simulation_settlement_lookup")
            else "null"
        )
        return f"coalesce(nullif({current_sql}, ''), nullif({birthplace_sql}, ''))"
    return (
        "coalesce("
        "nullif(json_extract(person_json, '$.current_settlement_id'), ''), "
        "nullif(json_extract(person_json, '$.birthplace_settlement_id'), '')"
        ")"
    )


def _person_birth_region_sql(con: sqlite3.Connection) -> str:
    columns = _table_columns(con, "simulation_people")
    if "birthplace_region_id" not in columns and "birthplace_region_key" in columns and _has_table(con, "simulation_region_lookup"):
        return (
            "(select region_id from simulation_region_lookup "
            "where region_key = simulation_people.birthplace_region_key)"
        )
    return _person_expr(con, "birthplace_region_id")


def _person_job_sql(con: sqlite3.Connection) -> str:
    return _person_expr(con, "job")


def _person_career_fitness_sql(con: sqlite3.Connection) -> str:
    return _person_expr(con, "career_fitness_score")


def _count_one(con: sqlite3.Connection, sql: str, params: Iterable[object] = ()) -> int:
    row = con.execute(sql, tuple(params)).fetchone()
    return int(row[0] or 0) if row else 0


NON_DISPLAY_JOB_LABELS = {"", "unassigned", "dependent", "dependent minor", "dependent_minor"}

JOB_DISPLAY_EXACT_RENAMES = {
    "bad cfo": "financial officer",
    "dark-pattern marketer": "marketer",
    "dependent helper": "household helper",
    "elite crisis negotiator": "crisis negotiator",
    "famous duel referee": "duel referee",
    "fraud analyst gone bad": "fraud analyst",
    "guild peak master": "guild master",
    "helicopter parent archetype": "parent",
    "heretic": "dissenter",
    "mad-scientist archetype": "scientist",
    "poorhouse abuser": "poorhouse worker",
    "prize-winning specialist": "specialist",
    "rare-tool specialist": "tool specialist",
    "warlord archetype": "military leader",
    "witch-hunter archetype": "inquisitor",
}

JOB_DISPLAY_QUALITY_PREFIXES = (
    "authoritarian",
    "bad",
    "basic",
    "corrupt",
    "cruel",
    "debt-ridden",
    "elite",
    "famous",
    "harsh",
    "junior",
    "negligent",
    "predatory",
    "prize-winning",
    "rare-tool",
    "reckless",
    "ruthless",
    "simple",
    "suspicious",
    "unstable",
    "unreliable",
    "volatile",
)


def _clean_display_job_label(job: object) -> str:
    label = str(job or "").strip()
    lower = label.lower()
    if lower in JOB_DISPLAY_EXACT_RENAMES:
        return JOB_DISPLAY_EXACT_RENAMES[lower]
    for prefix in JOB_DISPLAY_QUALITY_PREFIXES:
        prefix_with_space = f"{prefix} "
        if lower.startswith(prefix_with_space):
            return label[len(prefix_with_space) :].strip()
    return label


def _is_display_job(job: object) -> bool:
    label = str(job or "").strip()
    return label.lower() not in NON_DISPLAY_JOB_LABELS


def _display_job_label(job: object) -> str:
    label = str(job or "").strip()
    return _clean_display_job_label(label) if _is_display_job(label) else ""


def _notable_person_label(person: dict[str, object]) -> str:
    job = _display_job_label(person.get("job"))
    name = _person_name(person)
    return f"{name} — {job}" if job else name


def _merge_job_counts(*job_lists: Iterable[tuple[str, int]], limit: int = 5) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for jobs in job_lists:
        for job, count in jobs:
            label = _display_job_label(job)
            if not label:
                continue
            counts[label] = counts.get(label, 0) + int(count or 0)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]


def _latest_cohort_year(con: sqlite3.Connection) -> int | None:
    if not _has_table(con, "simulation_cohorts"):
        return None
    row = con.execute("select max(sim_year) as y from simulation_cohorts").fetchone()
    return int(row["y"]) if row and row["y"] is not None else None


def _open_territory_clause() -> str:
    return "until_sim_year is null"


def _polity_names_for_region(con: sqlite3.Connection, region_id: str) -> str:
    if not (_has_table(con, "simulation_polities") and _has_table(con, "simulation_polity_territory")):
        return ""
    settlement_table = _place_read_relation(con, "simulation_settlements")
    rows = con.execute(
        f"""
        select distinct p.name
        from simulation_polities p
        join simulation_polity_territory t on t.polity_id = p.polity_id
        left join {_quote_identifier(settlement_table)} s
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
        """,
        (*params, *tuple(extra_params)),
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        label = _display_job_label(row["job_name"])
        if label:
            counts[label] = counts.get(label, 0) + int(row["n"] or 0)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]


def _passive_people_place_stats(
    con: sqlite3.Connection,
    place_kind: str,
    place_ids: Iterable[str],
    *,
    limit: int = 3,
) -> tuple[dict[str, int], dict[str, list[tuple[str, int]]]]:
    ids = [str(place_id) for place_id in place_ids if str(place_id).strip()]
    if not ids or not _has_relation(con, "simulation_people_light_readable"):
        return {}, {}
    column = "birthplace_region_id" if place_kind == "region" else "coalesce(current_settlement_id, birthplace_settlement_id)"
    placeholders = ", ".join("?" for _ in ids)
    rows = con.execute(
        f"""
        select
          {column} as place_id,
          coalesce(nullif(job_family, ''), 'Unassigned') as job_name,
          count(*) as n
        from simulation_people_light_readable
        where is_alive = 1
          and {column} in ({placeholders})
        group by place_id, job_name
        order by place_id, n desc, job_name collate nocase
        """,
        tuple(ids),
    ).fetchall()
    alive_counts: dict[str, int] = {place_id: 0 for place_id in ids}
    job_counts: dict[str, dict[str, int]] = {place_id: {} for place_id in ids}
    for row in rows:
        place_id = str(row["place_id"] or "")
        count = int(row["n"] or 0)
        if not place_id:
            continue
        alive_counts[place_id] = alive_counts.get(place_id, 0) + count
        label = _display_job_label(row["job_name"])
        if label:
            place_jobs = job_counts.setdefault(place_id, {})
            place_jobs[label] = place_jobs.get(label, 0) + count
    top_jobs = {
        place_id: sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]
        for place_id, counts in job_counts.items()
    }
    return alive_counts, top_jobs


def _cohort_place_stats(
    con: sqlite3.Connection,
    place_kind: str,
    place_ids: Iterable[str],
    *,
    limit: int = 3,
) -> tuple[dict[str, int], dict[str, list[tuple[str, int]]]]:
    ids = [str(place_id) for place_id in place_ids if str(place_id).strip()]
    if not ids or not _has_relation(con, "simulation_cohorts_readable"):
        return {}, {}
    cohort_year = _latest_cohort_year(con)
    if cohort_year is None:
        return {}, {}
    column = "region_id" if place_kind == "region" else "settlement_id"
    placeholders = ", ".join("?" for _ in ids)
    rows = con.execute(
        f"""
        select
          {column} as place_id,
          coalesce(nullif(job_family, ''), 'Unassigned') as job_name,
          sum(population_count) as n
        from simulation_cohorts_readable
        where sim_year = ?
          and {column} in ({placeholders})
        group by place_id, job_name
        order by place_id, n desc, job_name collate nocase
        """,
        (cohort_year, *ids),
    ).fetchall()
    alive_counts: dict[str, int] = {place_id: 0 for place_id in ids}
    job_counts: dict[str, dict[str, int]] = {place_id: {} for place_id in ids}
    for row in rows:
        place_id = str(row["place_id"] or "")
        count = int(row["n"] or 0)
        if not place_id:
            continue
        alive_counts[place_id] = alive_counts.get(place_id, 0) + count
        label = _display_job_label(row["job_name"])
        if label:
            place_jobs = job_counts.setdefault(place_id, {})
            place_jobs[label] = place_jobs.get(label, 0) + count
    top_jobs = {
        place_id: sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]
        for place_id, counts in job_counts.items()
    }
    return alive_counts, top_jobs


def _nondetailed_place_stats(
    con: sqlite3.Connection,
    place_kind: str,
    place_ids: Iterable[str],
    *,
    limit: int = 3,
) -> tuple[dict[str, int], dict[str, list[tuple[str, int]]]]:
    ids = [str(place_id) for place_id in place_ids if str(place_id).strip()]
    if not ids or not _has_relation(con, "simulation_people_nondetailed_readable"):
        return {}, {}
    column = "birthplace_region_id" if place_kind == "region" else "coalesce(current_settlement_id, birthplace_settlement_id)"
    placeholders = ", ".join("?" for _ in ids)
    rows = con.execute(
        f"""
        select
          {column} as place_id,
          coalesce(nullif(job_family, ''), 'other') as job_name,
          count(*) as n
        from simulation_people_nondetailed_readable
        where is_alive = 1
          and {column} in ({placeholders})
        group by place_id, job_name
        order by place_id, n desc, job_name collate nocase
        """,
        tuple(ids),
    ).fetchall()
    alive_counts: dict[str, int] = {place_id: 0 for place_id in ids}
    job_counts: dict[str, dict[str, int]] = {place_id: {} for place_id in ids}
    for row in rows:
        place_id = str(row["place_id"] or "")
        count = int(row["n"] or 0)
        if not place_id:
            continue
        alive_counts[place_id] = alive_counts.get(place_id, 0) + count
        label = _display_job_label(row["job_name"])
        if label:
            place_jobs = job_counts.setdefault(place_id, {})
            place_jobs[label] = place_jobs.get(label, 0) + count
    top_jobs = {
        place_id: sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]
        for place_id, counts in job_counts.items()
    }
    return alive_counts, top_jobs


def _alive_counts_and_top_jobs_by_place(
    con: sqlite3.Connection,
    world: str,
    place_sql: str,
    place_ids: Iterable[str],
    *,
    limit: int = 3,
) -> tuple[dict[str, int], dict[str, list[tuple[str, int]]]]:
    ids = [str(place_id) for place_id in place_ids if str(place_id).strip()]
    if not ids:
        return {}, {}
    alive_counts: dict[str, int] = {place_id: 0 for place_id in ids}
    top_jobs: dict[str, list[tuple[str, int]]] = {place_id: [] for place_id in ids}
    detail_job_counts: dict[str, dict[str, int]] = {place_id: {} for place_id in ids}
    if _has_table(con, "simulation_people"):
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
        for row in rows:
            place_id = str(row["place_id"] or "")
            count = int(row["n"] or 0)
            if not place_id:
                continue
            alive_counts[place_id] = alive_counts.get(place_id, 0) + count
            label = _display_job_label(row["job_name"])
            if label:
                place_jobs = detail_job_counts.setdefault(place_id, {})
                place_jobs[label] = place_jobs.get(label, 0) + count
    for place_id, counts in detail_job_counts.items():
        top_jobs[place_id] = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]
    place_kind = "region" if "region" in place_sql else "settlement"
    passive_counts, passive_jobs = _passive_people_place_stats(con, place_kind, ids, limit=limit)
    cohort_counts, cohort_jobs = _cohort_place_stats(con, place_kind, ids, limit=limit)
    nondetailed_counts, nondetailed_jobs = _nondetailed_place_stats(con, place_kind, ids, limit=limit)
    for place_id in ids:
        alive_counts[place_id] = (
            alive_counts.get(place_id, 0)
            + passive_counts.get(place_id, 0)
            + cohort_counts.get(place_id, 0)
            + nondetailed_counts.get(place_id, 0)
        )
        top_jobs[place_id] = _merge_job_counts(
            top_jobs.get(place_id, []),
            passive_jobs.get(place_id, []),
            cohort_jobs.get(place_id, []),
            nondetailed_jobs.get(place_id, []),
            limit=limit,
        )
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
    settlement_table = _place_read_relation(con, "simulation_settlements")
    world_where, world_params = _world_where(con, settlement_table, world)
    rows = con.execute(
        f"""
        select region_id, count(*) as n
        from {_quote_identifier(settlement_table)}
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
    if not _has_relation(con, table):
        return []
    where, params = _world_where(con, table, world)
    rows = con.execute(
        f"select * from {_quote_identifier(table)} where {where}",
        tuple(params),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _snapshot_people_rows(con: sqlite3.Connection, world: str) -> list[dict[str, object]]:
    if not _has_table(con, "simulation_people"):
        return []
    where, params = _alive_where(con, world)
    columns = _table_columns(con, "simulation_people")
    if (
        "birthplace_region_id" not in columns
        and "birthplace_region_key" in columns
        and _has_table(con, "simulation_region_lookup")
    ) or (
        "current_settlement_id" not in columns
        and "current_settlement_key" in columns
        and _has_table(con, "simulation_settlement_lookup")
    ):
        rows = con.execute(
            f"""
            select p.*,
                   br.region_id as birthplace_region_id,
                   bs.settlement_id as birthplace_settlement_id,
                   cs.settlement_id as current_settlement_id
            from simulation_people p
            left join simulation_region_lookup br on br.region_key = p.birthplace_region_key
            left join simulation_settlement_lookup bs on bs.settlement_key = p.birthplace_settlement_key
            left join simulation_settlement_lookup cs on cs.settlement_key = p.current_settlement_key
            where {where.replace('simulation_people.', 'p.')}
            """,
            tuple(params),
        ).fetchall()
    else:
        rows = con.execute(
            f"select * from simulation_people where {where}",
            tuple(params),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _load_place_snapshot(con: sqlite3.Connection, world: str) -> dict[str, object]:
    started = time.perf_counter()
    people = _snapshot_people_rows(con, world)
    regions = _snapshot_table_rows(con, _place_read_relation(con, "simulation_regions"), world)
    settlements = _snapshot_table_rows(con, _place_read_relation(con, "simulation_settlements"), world)
    polities = _snapshot_table_rows(con, "simulation_polities", world)
    office_history = _snapshot_table_rows(
        con, "simulation_office_history_readable", world
    )
    if not office_history:
        office_history = [_row_to_dict(row) for row in _office_history_rows(con)]
    snapshot = {
        "world": world,
        "people": people,
        "regions": regions,
        "settlements": settlements,
        "polities": polities,
        "passive_people": _snapshot_table_rows(con, "simulation_people_light_readable", world),
        "cohorts": _snapshot_table_rows(con, "simulation_cohorts_readable", world),
        "nondetailed_people": _snapshot_table_rows(
            con, "simulation_people_nondetailed_readable", world
        ),
        "territory": _snapshot_table_rows(con, "simulation_polity_territory", world),
        "seats": _snapshot_table_rows(con, "simulation_office_seats", world),
        "office_history": office_history,
    }
    _log_info(
        "place_snapshot_loaded world=%s people=%s regions=%s settlements=%s polities=%s territory=%s seats=%s office_history=%s elapsed=%.4fs",
        world,
        len(people),
        len(regions),
        len(settlements),
        len(polities),
        len(_snapshot_rows(snapshot, "territory")),
        len(_snapshot_rows(snapshot, "seats")),
        len(_snapshot_rows(snapshot, "office_history")),
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
    return f"{_person_name(person)} ({_person_years_label(person)})"


def _head_title_id_for_polity_type(world: str, polity_type_id: object) -> str:
    ptype = str(polity_type_id or "").strip()
    if not ptype:
        return ""
    path = _db_path(world, "Config DB")
    if not path.exists():
        return ""
    try:
        with _connect_readonly(path) as con:
            if not _has_table(con, "government_polity_types"):
                return ""
            row = con.execute(
                """
                SELECT head_title_id
                FROM government_polity_types
                WHERE polity_type_id = ?
                LIMIT 1
                """,
                (ptype,),
            ).fetchone()
            return str(row["head_title_id"] or "").strip() if row else ""
    except sqlite3.Error:
        return ""


def _office_history_rows(
    con: sqlite3.Connection,
    polity_id: int | None = None,
) -> list[sqlite3.Row]:
    if not _has_table(con, "simulation_office_holdings") or not _has_table(
        con, "simulation_office_seats"
    ):
        return []
    params: list[object] = []
    where = ""
    if polity_id is not None:
        where = "WHERE s.polity_id = ?"
        params.append(int(polity_id))
    if _has_relation(con, "simulation_office_history_readable"):
        try:
            view_where = "WHERE polity_id = ?" if polity_id is not None else ""
            rows = con.execute(
                f"""
                SELECT *
                FROM simulation_office_history_readable
                {view_where}
                ORDER BY start_sim_year, holding_id
                """,
                tuple(params),
            ).fetchall()
            return list(rows)
        except sqlite3.Error:
            pass
    people_name_sql = "'Unknown'"
    if _has_table(con, "simulation_people"):
        cols = set(_table_columns(con, "simulation_people"))
        if {"first_name", "last_name"} <= cols:
            people_name_sql = (
                "TRIM(COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, ''))"
            )
        elif "person_json" in cols:
            people_name_sql = (
                "TRIM(COALESCE(json_extract(p.person_json, '$.first_name'), '') "
                "|| ' ' || COALESCE(json_extract(p.person_json, '$.last_name'), ''))"
            )
    return list(
        con.execute(
            f"""
            SELECT
                h.holding_id,
                s.polity_id,
                COALESCE(pol.name, '') AS polity_name,
                s.seat_id,
                s.title_id AS office_id,
                s.title_id,
                s.slot_index,
                s.scope_settlement_id,
                h.holder_person_id,
                {people_name_sql} AS holder_name,
                h.start_sim_year,
                h.end_sim_year,
                h.end_reason,
                CASE
                    WHEN h.end_sim_year IS NULL THEN 'current'
                    ELSE 'ended'
                END AS holding_status
            FROM simulation_office_holdings h
            JOIN simulation_office_seats s
              ON s.seat_id = h.seat_id
            LEFT JOIN simulation_polities pol
              ON pol.polity_id = s.polity_id
            LEFT JOIN simulation_people p
              ON p.person_id = h.holder_person_id
            {where}
            ORDER BY h.start_sim_year, h.holding_id
            """,
            tuple(params),
        ).fetchall()
    )


def _office_span_label(start_year: object, end_year: object) -> str:
    start = _format_year(start_year, unknown_text="?")
    if end_year in (None, ""):
        return f"{start}-present"
    return f"{start}-{_format_year(end_year, unknown_text='?')}"


def _office_holder_label_from_history(
    con: sqlite3.Connection | None,
    world: str,
    row: sqlite3.Row | dict[str, object],
    snapshot: dict[str, object] | None = None,
) -> str:
    holder_id = row["holder_person_id"] if isinstance(row, sqlite3.Row) else row.get("holder_person_id")
    name = row["holder_name"] if isinstance(row, sqlite3.Row) else row.get("holder_name")
    if snapshot is not None:
        person = _snapshot_person(snapshot, holder_id)
        if person:
            return f"{_person_name(person)} ({_person_years_label(person)})"
        return str(name or "Unknown")
    if con is not None:
        shown = _person_link_text(con, world, holder_id)
        return str(name or "Unknown") if shown == "Unknown" else shown
    return str(name or "Unknown")


def _office_history_item(
    row: sqlite3.Row | dict[str, object],
    *,
    holder_label: str,
    include_office: bool,
) -> str:
    getter = row.__getitem__ if isinstance(row, sqlite3.Row) else row.get
    office = str(getter("title_id") or getter("office_id") or "office")
    scope = str(getter("scope_settlement_id") or "").strip()
    span = _office_span_label(getter("start_sim_year"), getter("end_sim_year"))
    reason = str(getter("end_reason") or "").strip()
    status = "current" if getter("end_sim_year") in (None, "") else f"ended: {reason or 'unknown'}"
    office_part = f"{office}: " if include_office else ""
    scope_part = f" at {scope}" if scope else ""
    return f"{span}: {office_part}{holder_label}{scope_part} ({status})"


def _head_office_history(
    world: str,
    polity: sqlite3.Row | dict[str, object],
    seats: list[sqlite3.Row] | list[dict[str, object]],
    history: list[sqlite3.Row] | list[dict[str, object]],
) -> list[sqlite3.Row] | list[dict[str, object]]:
    getter = polity.__getitem__ if isinstance(polity, sqlite3.Row) else polity.get
    head_title = _head_title_id_for_polity_type(world, getter("polity_type_id"))
    if head_title:
        selected = [
            row
            for row in history
            if str((row["title_id"] if isinstance(row, sqlite3.Row) else row.get("title_id")) or "") == head_title
        ]
        if selected:
            return selected
    head_seat_ids = {
        int(seat["seat_id"] if isinstance(seat, sqlite3.Row) else seat.get("seat_id"))
        for seat in seats
        if not str(
            (seat["scope_settlement_id"] if isinstance(seat, sqlite3.Row) else seat.get("scope_settlement_id"))
            or ""
        ).strip()
    }
    if not head_seat_ids and seats:
        first = sorted(
            seats,
            key=lambda seat: (
                str(seat["title_id"] if isinstance(seat, sqlite3.Row) else seat.get("title_id") or ""),
                _safe_int(seat["slot_index"] if isinstance(seat, sqlite3.Row) else seat.get("slot_index"), 0),
            ),
        )[0]
        try:
            head_seat_ids.add(int(first["seat_id"] if isinstance(first, sqlite3.Row) else first.get("seat_id")))
        except (TypeError, ValueError):
            pass
    return [
        row
        for row in history
        if _safe_int(row["seat_id"] if isinstance(row, sqlite3.Row) else row.get("seat_id"), -1)
        in head_seat_ids
    ]


def _snapshot_settlement_name(snapshot: dict[str, object], settlement_id: object) -> str:
    if not settlement_id:
        return ""
    row = _snapshot_map(snapshot, "settlements", "settlement_id").get(str(settlement_id))
    if not row:
        return str(settlement_id)
    return str(row.get("display_name") or settlement_id)


def _snapshot_region_display_name(snapshot: dict[str, object], region_id: object) -> str:
    rid = str(region_id or "").strip()
    if not rid:
        return ""
    row = _snapshot_map(snapshot, "regions", "region_id").get(rid)
    if row:
        label = str(row.get("region_display_name") or "").strip()
        if label:
            return label
    return _display_title(rid)


def _snapshot_person_job(person: dict[str, object]) -> str:
    return _display_job_label(person.get("job"))


def _top_jobs_from_people(people: Iterable[dict[str, object]], limit: int) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for person in people:
        job = _snapshot_person_job(person)
        if not job:
            continue
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


def _snapshot_latest_cohort_year(snapshot: dict[str, object]) -> int | None:
    years = [_safe_int(row.get("sim_year"), -1) for row in _snapshot_rows(snapshot, "cohorts")]
    years = [year for year in years if year >= 0]
    return max(years) if years else None


def _snapshot_population_stats(
    snapshot: dict[str, object],
    place_kind: str,
    place_id: str,
    *,
    limit: int = 3,
) -> tuple[int, list[tuple[str, int]]]:
    detailed = (
        _snapshot_people_by_region(snapshot, place_id)
        if place_kind == "region"
        else _snapshot_people_by_settlement(snapshot, place_id)
    )
    job_counts: dict[str, int] = {}
    for person in detailed:
        job = _snapshot_person_job(person)
        if not job:
            continue
        job_counts[job] = job_counts.get(job, 0) + 1
    alive = len(detailed)
    for row in _snapshot_rows(snapshot, "passive_people"):
        if not row.get("is_alive"):
            continue
        row_place = (
            row.get("birthplace_region_id")
            if place_kind == "region"
            else row.get("current_settlement_id") or row.get("birthplace_settlement_id")
        )
        if str(row_place or "") != place_id:
            continue
        alive += 1
        job = _display_job_label(row.get("job_family"))
        if job:
            job_counts[job] = job_counts.get(job, 0) + 1
    for row in _snapshot_rows(snapshot, "nondetailed_people"):
        if not row.get("is_alive"):
            continue
        row_place = (
            row.get("birthplace_region_id")
            if place_kind == "region"
            else row.get("current_settlement_id") or row.get("birthplace_settlement_id")
        )
        if str(row_place or "") != place_id:
            continue
        alive += 1
        job = _display_job_label(row.get("job_family"))
        if job:
            job_counts[job] = job_counts.get(job, 0) + 1
    cohort_year = _snapshot_latest_cohort_year(snapshot)
    if cohort_year is not None:
        for row in _snapshot_rows(snapshot, "cohorts"):
            if _safe_int(row.get("sim_year"), -1) != cohort_year:
                continue
            row_place = row.get("region_id") if place_kind == "region" else row.get("settlement_id")
            if str(row_place or "") != place_id:
                continue
            count = _safe_int(row.get("population_count"), 0)
            alive += count
            job = _display_job_label(row.get("job_family"))
            if job:
                job_counts[job] = job_counts.get(job, 0) + count
    jobs = sorted(job_counts.items(), key=lambda item: (-item[1], item[0].lower()))[:limit]
    return alive, jobs


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


def _city_state_note_from_value(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        raw_notes = value
    else:
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        raw_notes = parsed if isinstance(parsed, dict) else {}
    city_state = raw_notes.get("city_state")
    return dict(city_state) if isinstance(city_state, dict) else {}


def _city_state_note_items(note: dict[str, object]) -> list[str]:
    if not note:
        return []
    items: list[str] = []
    autonomy = str(note.get("autonomy_state") or "").strip()
    if autonomy:
        items.append(f"Autonomy: {autonomy.replace('_', ' ')}")
    league = str(note.get("league_id") or note.get("hegemony_league_id") or "").strip()
    if league:
        items.append(f"League: {league}")
    hegemon = note.get("hegemon_polity_id")
    if hegemon not in (None, ""):
        items.append(f"Hegemon polity: {hegemon}")
    colony = str(note.get("colony_autonomy_level") or "").strip()
    if colony:
        items.append(f"Colony status: {colony.replace('_', ' ')}")
    project = str(note.get("last_public_works_project") or "").strip()
    if project:
        items.append(f"Latest civic work: {project.replace('_', ' ')}")
    crisis = str(note.get("unresolved_civic_crisis_reason") or "").strip()
    if crisis:
        items.append(f"Unresolved crisis: {crisis.replace('_', ' ')}")
    return items


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
    return [_notable_person_label(person) for person in ranked[:limit]]


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


def _config_region_display_name(world: str, region_id: str) -> str:
    cfg = _db_path(world, "Config DB")
    if not cfg.exists():
        return ""
    try:
        with _connect_readonly(cfg) as con:
            if not _has_table(con, "world_geography_regions"):
                return ""
            row = con.execute(
                """
                select region_name
                from world_geography_regions
                where world = ? and region_id = ?
                """,
                (world, region_id),
            ).fetchone()
    except (FileNotFoundError, sqlite3.Error):
        return ""
    return str(row["region_name"] or "").strip() if row else ""


def _render_empty_region_sheet(con: sqlite3.Connection, world: str, region_id: str) -> str:
    name = _history_region_label(con, world, region_id)
    cards = "".join(
        [
            _detail_card("Alive", 0),
            _detail_card("Settlements", 0),
            _detail_card("Food Pressure", "Unknown"),
            _detail_card("Stability", "Unknown"),
            _detail_card("Market Pull", "Unknown"),
            _detail_card("Prosperity", "Unknown"),
            _detail_card("Treasury", "Unknown"),
            _detail_card("Polities", _polity_names_for_region(con, region_id) or "None"),
        ]
    )
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(name)}</h2>'
        '<div class="place-subtitle">Region</div>'
        f'<div class="place-muted">No settlements are recorded for {html.escape(name)} in the current save yet.</div>'
        f'<div class="place-grid">{cards}</div>'
        f'{_region_map_html(con, world, region_id)}'
        '<div class="place-columns">'
        '<section><h3>Settlements</h3><p class="place-muted">None yet.</p></section>'
        '<section><h3>Top Jobs</h3><p class="place-muted">None yet.</p></section>'
        '<section><h3>Notable Residents</h3><p class="place-muted">None yet.</p></section>'
        '</div>'
        '</div>'
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


def _feature_fontawesome_name(kind: str) -> str:
    k = (kind or "").strip().lower()
    if k in {"harbor", "bay", "coast", "coastline"}:
        return "anchor"
    if k in {"lake", "spring", "marsh", "bog", "wadi"}:
        return "droplet"
    if k in {"river", "stream", "ford", "fishery"}:
        return "water"
    if k in {"mountain", "ridge", "pass", "hill", "cliff", "mesa"}:
        return "mountain"
    if k in {"forest", "grove", "clearing", "meadow", "pasture", "orchard"}:
        return "tree"
    if k in {"bridge", "road", "route"}:
        return "bridge"
    if k in {"engineering", "workshop", "mine", "quarry"}:
        return "gears"
    if k in {"sacred", "shrine", "temple"}:
        return "landmark"
    if k in {"star", "wonder"}:
        return "star"
    return "location-dot"


def _fontawesome_local_paths(kind: str, x: float, y: float, *, size: float = 4.1) -> tuple[str, str]:
    icon = FONT_AWESOME_FREE_SOLID[_feature_fontawesome_name(kind)]
    scale = size / max(icon.width, icon.height)
    tx = x - icon.width * scale / 2.0
    ty = y - icon.height * scale / 2.0
    return (icon.path, f"translate({tx:.2f} {ty:.2f}) scale({scale:.5f})")


def _region_micro_fill(cell: object) -> str:
    elev = float(getattr(cell, "elevation", 0.0))
    moist = float(getattr(cell, "moisture", 0.0))
    if getattr(cell, "is_coastal", False):
        return "#918a74"
    if elev >= 0.72:
        return "#d9dad2" if moist >= 0.52 else "#b9b39c"
    if moist >= 0.78:
        return "#246f5e"
    if moist >= 0.58:
        return "#3f875e"
    if moist <= 0.28:
        return "#9a9079"
    return "#7fa45f"


def _render_generated_region_map(
    world: str,
    region_id: str,
    settlements: Iterable[sqlite3.Row],
    *,
    focus_settlement_id: str | None = None,
) -> str:
    cfg = _db_path(world, "Config DB")
    if not cfg.exists():
        return ""
    save = _db_path(world, "Save DB")
    try:
        geometry = _cached_world_map_geometry(
            world,
            str(cfg),
            _sqlite_file_fingerprint(cfg),
            str(save),
            _sqlite_file_fingerprint(save),
        )
    except Exception:
        return ""
    cells = [c for c in geometry.micro_cells if c.region_id == region_id]
    if not cells:
        return ""
    xs = [x for c in cells for x, _ in c.polygon]
    ys = [y for c in cells for _, y in c.polygon]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    w = max(1e-6, x1 - x0)
    h = max(1e-6, y1 - y0)
    pad = 5.0
    inner_long = 100.0 - pad * 2.0
    scale = inner_long / max(w, h)
    view_w = w * scale + pad * 2.0
    view_h = h * scale + pad * 2.0

    def sx(x: float) -> float:
        return pad + (x - x0) * scale

    def sy(y: float) -> float:
        return pad + (y - y0) * scale

    def poly_points(poly: list[tuple[float, float]]) -> str:
        return " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in poly)

    parts = [
        (
            '<svg class="place-map generated-region-map" '
            f'viewBox="0 0 {view_w:.2f} {view_h:.2f}" '
            'role="img" aria-label="Generated region map">'
        ),
        f'<rect x="0" y="0" width="{view_w:.2f}" height="{view_h:.2f}" fill="#454a78" />',
    ]
    for cell in cells:
        parts.append(
            f'<polygon points="{poly_points(cell.polygon)}" fill="{_region_micro_fill(cell)}" '
            'stroke="#26304f" stroke-width=".09" stroke-opacity=".16" />'
        )
    for river in geometry.rivers:
        for segment in river.segments:
            if region_id not in segment.region_ids:
                continue
            pts = [(sx(x), sy(y)) for x, y in segment.points]
            if len(pts) < 2:
                continue
            parts.append(
                f'<polyline points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in pts)}" '
                'fill="none" stroke="#2a8bc8" stroke-width=".85" opacity=".86" stroke-linecap="round" />'
            )
    geo: dict[str, object] = {}
    settlement_rows = list(settlements)
    for row in settlement_rows:
        geo = _load_local_geography(row["local_geography_json"] if "local_geography_json" in row.keys() else None)
        if geo:
            break
    sites = geo.get("settlements") if isinstance(geo, dict) else None
    site_by_slot: dict[int, dict[str, object]] = {}
    if isinstance(sites, list):
        for site in sites:
            if isinstance(site, dict):
                try:
                    site_by_slot[int(site.get("settlement_slot", 0)) + 1] = site
                except (TypeError, ValueError):
                    continue
    features = geo.get("features") if isinstance(geo, dict) else None
    if isinstance(features, list):
        for feat in features:
            if not isinstance(feat, dict):
                continue
            display = str(feat.get("display_name") or "").strip()
            if not display:
                continue
            try:
                if feat.get("source_world_x") is not None and feat.get("source_world_y") is not None:
                    world_xy = (float(feat["source_world_x"]), float(feat["source_world_y"]))
                    wx, wy = project_world_point_to_region_footprint(geometry, region_id, world_xy)
                else:
                    lx = max(0.04, min(0.96, float(feat.get("x", 0.5))))
                    ly = max(0.04, min(0.96, float(feat.get("y", 0.5))))
                    wx, wy = project_local_point_to_region_footprint(geometry, region_id, (lx, ly))
            except (TypeError, ValueError):
                continue
            x = sx(wx)
            y = sy(wy)
            kind = str(feat.get("kind") or "feature")
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r=".72" fill="{_feature_color(kind)}" opacity=".92" '
                'stroke="#ffffff" stroke-width=".18" />'
            )
            parts.append(
                f'<text x="{x + 1.05:.1f}" y="{y - .65:.1f}" font-size="1.45" '
                'font-family="Arial,Helvetica,sans-serif" paint-order="stroke" '
                'stroke="#ffffff" stroke-width=".35" stroke-linejoin="round" fill="#222222">'
                f'{html.escape(display[:20])}</text>'
            )
    for row in settlement_rows:
        slot = int(row["site_slot"] or 1) if "site_slot" in row.keys() else 1
        site = site_by_slot.get(slot)
        world_xy: tuple[float, float] | None = None
        if site:
            try:
                lx = float(site.get("x", 0.5))
                ly = float(site.get("y", 0.5))
            except (TypeError, ValueError):
                lx = ly = 0.5
            try:
                if site.get("world_x") is not None and site.get("world_y") is not None:
                    world_xy = (float(site["world_x"]), float(site["world_y"]))
            except (TypeError, ValueError):
                world_xy = None
        else:
            lx = ly = 0.5
        if world_xy is not None:
            wx, wy = project_world_point_to_region_footprint(
                geometry,
                region_id,
                world_xy,
            )
        else:
            wx, wy = project_local_point_to_region_footprint(
                geometry,
                region_id,
                (max(0.04, min(0.96, lx)), max(0.04, min(0.96, ly))),
            )
        x = sx(wx)
        y = sy(wy)
        sid = str(row["settlement_id"] or "")
        label = str(row["display_name"] or sid)
        focused = sid == (focus_settlement_id or "")
        radius = 0.95 if focused else 0.68
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="#111111" '
            f'stroke="#ffffff" stroke-width="{0.45 if focused else .25}" />'
        )
        if focused or str(row["status"] or "").strip().lower() == "active":
            parts.append(
                f'<text x="{x + radius + .7:.1f}" y="{y + .55:.1f}" font-size="1.55" '
                'font-family="Arial,Helvetica,sans-serif" font-weight="600" paint-order="stroke" '
                f'stroke="#ffffff" stroke-width=".42" stroke-linejoin="round" fill="#111111">'
                f'{html.escape(label[:18])}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


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
        path, transform = _fontawesome_local_paths(kind, x, y)
        parts.append(
            f'<path d="{html.escape(path)}" transform="{transform}" fill="none" '
            'stroke="#fff8e6" stroke-width="1.1" stroke-linejoin="round" '
            'vector-effect="non-scaling-stroke" opacity=".92" />'
        )
        parts.append(
            f'<path d="{html.escape(path)}" transform="{transform}" fill="#101827" '
            'stroke="#101827" stroke-width=".08" stroke-linejoin="round" '
            'vector-effect="non-scaling-stroke" />'
        )
        label = str(feat.get("display_name") or kind)
        parts.append(
            f'<text x="{x + 2.0:.1f}" y="{y - 1.1:.1f}" font-size="1.9" '
            'font-family="Arial,Helvetica,sans-serif" paint-order="stroke" stroke="#fff8e6" '
            f'stroke-width=".55" stroke-linejoin="round" fill="#1f2833" font-weight="700">{html.escape(label[:20])}</text>'
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
        radius = 2.0 if focused else 1.35
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" '
            f'fill="#111111" stroke="#ffffff" stroke-width="{.85 if focused else .65}" />'
        )
        parts.append(
            f'<text x="{x + radius + 1.0:.1f}" y="{y + .7:.1f}" font-size="2.0" '
            'font-family="Arial,Helvetica,sans-serif" font-weight="600" paint-order="stroke" '
            f'stroke="#ffffff" stroke-width=".55" stroke-linejoin="round" fill="#111111">'
            f'{html.escape(label[:18])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _settlement_is_active(row: sqlite3.Row) -> bool:
    return str(row["status"] or "").strip().lower() == "active"


def _region_settlements(
    con: sqlite3.Connection,
    region_id: str,
    *,
    include_inactive_settlements: bool = False,
    focus_settlement_id: str | None = None,
) -> list[sqlite3.Row]:
    if not _has_table(con, "simulation_settlements"):
        return []
    settlement_table = _place_read_relation(con, "simulation_settlements")
    rows = con.execute(
        f"""
        select *
        from {_quote_identifier(settlement_table)}
        where region_id = ?
        order by status = 'active' desc, population_cap desc, display_name collate nocase
        """,
        (region_id,),
    ).fetchall()
    if include_inactive_settlements:
        return rows
    focus = (focus_settlement_id or "").strip()
    return [row for row in rows if _settlement_is_active(row) or str(row["settlement_id"] or "").strip() == focus]


def _region_map_html(
    con: sqlite3.Connection,
    world: str,
    region_id: str,
    *,
    focus_settlement_id: str | None = None,
    include_inactive_settlements: bool = False,
) -> str:
    settlements = _region_settlements(
        con,
        region_id,
        include_inactive_settlements=include_inactive_settlements,
        focus_settlement_id=focus_settlement_id,
    )
    generated = _render_generated_region_map(
        world,
        region_id,
        settlements,
        focus_settlement_id=focus_settlement_id,
    )
    if generated:
        return generated
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
            settlement_table = _place_read_relation(con, "simulation_settlements")
            world_where, params = _world_where(con, settlement_table, saved_world)
            clauses = [world_where]
            if needle:
                clauses.append(
                    "(settlement_id like ? or region_id like ? or display_name like ? or level like ? or status like ?)"
                )
                params.extend([needle] * 5)
            rows = con.execute(
                f"""
                select *
                from {_quote_identifier(settlement_table)}
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
                        "Region": _history_region_label(con, saved_world, rid),
                        "Status": row["status"] or "",
                        "Food": _fmt_number(row["food_pressure"]),
                        "Stability": _fmt_number(row["stability"]),
                        "Prosperity": _fmt_number(row["prosperity_pool"]),
                        "Polity": _polity_names_for_settlement(con, sid, rid),
                        "Top Jobs": jobs,
                    }
                )
                keys.append(_encode_place_key(world, saved_world, sid))
            refuge_rows, refuge_total = _query_outlaw_refuges(
                con,
                search=search or "",
                status_filter="All",
                limit=row_limit,
            )
            for refuge_row in refuge_rows:
                summary = _outlaw_refuge_summary_row(con, saved_world, refuge_row)
                values.append({header: summary.get(header, "") for header in PLACE_TOWN_HEADERS})
                keys.append(_encode_place_key(world, saved_world, refuge_row["refuge_id"]))
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
            region_table = _place_read_relation(con, "simulation_regions")
            world_where, params = _world_where(con, region_table, saved_world)
            clauses = [world_where]
            if needle:
                clauses.append("(region_id like ? or region_display_name like ?)")
                params.extend([needle] * 2)
            rows = con.execute(
                f"""
                select *
                from {_quote_identifier(region_table)}
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
        if not _snapshot_region_settlements(snapshot, region_id):
            return (
                '<div class="place-sheet muted">'
                f'No settlements are recorded for region {html.escape(region_id)} in the current save yet.'
                '</div>'
            )
        return f'<div class="place-sheet muted">No region named {html.escape(region_id)}.</div>'
    people = _snapshot_people_by_region(snapshot, region_id)
    settlements = _snapshot_region_settlements(snapshot, region_id)
    alive, job_counts = _snapshot_population_stats(snapshot, "region", region_id, limit=8)
    region_name = _snapshot_region_display_name(snapshot, region_id)
    jobs = [f"{job}: {n}" for job, n in job_counts]
    residents = _snapshot_notable_people(people, 8)
    settlement_items = []
    for s in settlements[:12]:
        sid = str(s.get("settlement_id") or "")
        settlement_alive, _ = _snapshot_population_stats(snapshot, "settlement", sid, limit=1)
        settlement_items.append(
            (
                f"{s.get('display_name') or sid} "
                f"({s.get('level')}, {s.get('status')}, alive {settlement_alive})"
            )
        )
    cards = "".join(
        [
            _detail_card("Alive", alive),
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
        f'<h2>{html.escape(region_name)}</h2>'
        '<div class="place-subtitle">Region</div>'
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
    region_name = _snapshot_region_display_name(snapshot, rid)
    people = _snapshot_people_by_settlement(snapshot, sid)
    alive, job_counts = _snapshot_population_stats(snapshot, "settlement", sid, limit=8)
    jobs = [f"{job}: {n}" for job, n in job_counts]
    residents = _snapshot_notable_people(people, 8)
    cards = "".join(
        [
            _detail_card("Alive", alive),
            _detail_card("Level", row.get("level") or ""),
            _detail_card("Status", row.get("status") or ""),
            _detail_card("Region", region_name),
            _detail_card("Food Pressure", _fmt_number(row.get("food_pressure"))),
            _detail_card("Stability", _fmt_number(row.get("stability"))),
            _detail_card("Market Pull", _fmt_number(row.get("market_pull"))),
            _detail_card("Prosperity", _fmt_number(row.get("prosperity_pool"))),
            _detail_card("Polity", _snapshot_polity_names_for_settlement(snapshot, sid, rid) or "None"),
            _detail_card("Founded", _format_year(row.get("founded_sim_year"), unknown_text="Unknown")),
        ]
    )
    name_bits = [row.get("etymology"), row.get("name_category_primary"), row.get("name_culture_primary")]
    name_line = " | ".join(str(x) for x in name_bits if x)
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row.get("display_name") or sid))}</h2>'
        f'<div class="place-subtitle">{html.escape(str(row.get("level") or "settlement"))} in {html.escape(region_name)}</div>'
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
        elif terr.get("target_kind") == "region":
            target = _snapshot_region_display_name(snapshot, target)
        territory_items.append(
            f"{terr.get('target_kind')}: {target} since {_format_year(terr.get('since_sim_year'))}"
        )
    seat_items = []
    for seat in seats[:16]:
        holder = _snapshot_person_link_text(snapshot, seat.get("holder_person_id")) if seat.get("holder_person_id") else "vacant"
        scope = f" at {_snapshot_settlement_name(snapshot, seat.get('scope_settlement_id'))}" if seat.get("scope_settlement_id") else ""
        seat_items.append(f"{seat.get('title_id')}{scope}: {holder}")
    office_history = sorted(
        [
            row
            for row in _snapshot_rows(snapshot, "office_history")
            if str(row.get("polity_id")) == str(pid)
        ],
        key=lambda hist: (
            _safe_int(hist.get("start_sim_year"), 0),
            _safe_int(hist.get("holding_id"), 0),
        ),
    )
    ruler_history = _head_office_history(
        str(snapshot.get("world") or ""),
        row,
        seats,
        office_history,
    )
    ruler_items = [
        _office_history_item(
            hist,
            holder_label=_office_holder_label_from_history(
                None,
                str(snapshot.get("world") or ""),
                hist,
                snapshot=snapshot,
            ),
            include_office=False,
        )
        for hist in ruler_history
    ]
    recent_office_items = [
        _office_history_item(
            hist,
            holder_label=_office_holder_label_from_history(
                None,
                str(snapshot.get("world") or ""),
                hist,
                snapshot=snapshot,
            ),
            include_office=True,
        )
        for hist in sorted(
            office_history,
            key=lambda hist: (
                _safe_int(hist.get("end_sim_year"), 10**9),
                _safe_int(hist.get("start_sim_year"), 0),
                _safe_int(hist.get("holding_id"), 0),
            ),
            reverse=True,
        )[:12]
    ]
    vassal_items = [f"{v.get('name')} ({v.get('polity_type_id')})" for v in vassals]
    city_state_items = _city_state_note_items(_city_state_note_from_value(row.get("notes_json") or row.get("notes")))
    cards = "".join(
        [
            _detail_card("Type", row.get("polity_type_id") or ""),
            _detail_card("Status", row.get("status") or ""),
            _detail_card("Territories", len(territories)),
            _detail_card("Seats", len(seats)),
            _detail_card("Held Seats", sum(1 for seat in seats if seat.get("holder_person_id") is not None)),
            _detail_card("Vassals", len(vassals)),
            _detail_card("Capital", _snapshot_settlement_name(snapshot, row.get("capital_settlement_id")) or "None"),
            _detail_card("Founded", _format_year(row.get("founded_sim_year"), unknown_text="Unknown")),
        ]
    )
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row.get("name") or f"Polity {pid}"))}</h2>'
        f'<div class="place-subtitle">Polity #{pid}</div>'
        f'<div class="place-grid">{cards}</div>'
        '<div class="place-columns">'
        f'<section><h3>Territory</h3>{_ul(territory_items)}</section>'
        f'<section><h3>City-State</h3>{_ul(city_state_items)}</section>'
        f'<section><h3>Offices</h3>{_ul(seat_items)}</section>'
        f'<section><h3>Vassals</h3>{_ul(vassal_items)}</section>'
        f'<section><h3>Ruler Timeline</h3>{_ul(ruler_items)}</section>'
        f'<section><h3>Office History</h3>{_ul(recent_office_items)}</section>'
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
            alive, job_counts = _snapshot_population_stats(snapshot, "settlement", sid, limit=3)
            jobs = ", ".join(f"{job} ({n})" for job, n in job_counts)
            values.append(
                {
                    "Name": row.get("display_name") or sid,
                    "Level": row.get("level") or "",
                    "Alive": alive,
                    "Region": _snapshot_region_display_name(snapshot, rid),
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
            alive, job_counts = _snapshot_population_stats(snapshot, "region", rid, limit=3)
            jobs = ", ".join(f"{job} ({n})" for job, n in job_counts)
            active_settlements = sum(
                1 for settlement in _snapshot_region_settlements(snapshot, rid) if settlement.get("status") == "active"
            )
            values.append(
                {
                    "Name": row.get("region_display_name") or rid,
                    "Alive": alive,
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
    region_table = _place_read_relation(con, "simulation_regions")
    if _saved_table_has_world(con, region_table):
        row = con.execute(
            f"select * from {_quote_identifier(region_table)} where world = ? and region_id = ?",
            (world, region_id),
        ).fetchone()
    else:
        row = con.execute(
            f"select * from {_quote_identifier(region_table)} where region_id = ?",
            (region_id,),
        ).fetchone()
    if not row:
        if not _has_table(con, "simulation_settlements"):
            if _config_region_display_name(world, region_id):
                return _render_empty_region_sheet(con, world, region_id)
            return f'<div class="place-sheet muted">No region named {html.escape(region_id)}.</div>'
        if _has_table(con, "simulation_settlements"):
            settlement_table = _place_read_relation(con, "simulation_settlements")
            where, params = _world_where(con, settlement_table, world)
            settlement_count = _count_one(
                con,
                f"""
                select count(*)
                from {_quote_identifier(settlement_table)}
                where {where} and region_id = ?
                """,
                (*params, region_id),
            )
            if settlement_count == 0:
                return _render_empty_region_sheet(con, world, region_id)
        return f'<div class="place-sheet muted">No region named {html.escape(region_id)}.</div>'
    birth_region_sql = _person_birth_region_sql(con)
    alive_counts, top_jobs = _alive_counts_and_top_jobs_by_place(
        con,
        world,
        birth_region_sql,
        [region_id],
        limit=8,
    )
    alive = alive_counts.get(region_id, 0)
    settlements = _region_settlements(con, region_id)
    jobs = [f"{job}: {n}" for job, n in top_jobs.get(region_id, [])]
    region_name = _history_region_label(con, world, region_id)
    people = []
    for p in _top_people_for_where(con, world, f"{birth_region_sql} = ?", (region_id,), limit=8):
        person = _person_from_row(p, _trait_slots_for_world(world))
        people.append(_notable_person_label(person))
    settlement_ids = [str(s["settlement_id"]) for s in settlements[:12]]
    settlement_alive_counts, _ = _alive_counts_and_top_jobs_by_place(
        con,
        world,
        _person_residence_sql(con),
        settlement_ids,
        limit=1,
    )
    settlement_items = [
        (
            f"{s['display_name'] or s['settlement_id']} "
            f"({s['level']}, {s['status']}, alive {settlement_alive_counts.get(str(s['settlement_id']), 0)})"
        )
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
        f'<h2>{html.escape(region_name)}</h2>'
        '<div class="place-subtitle">Region</div>'
        f'<div class="place-grid">{cards}</div>'
        f'{_region_map_html(con, world, region_id)}'
        '<div class="place-columns">'
        f'<section><h3>Settlements</h3>{_ul(settlement_items)}</section>'
        f'<section><h3>Top Jobs</h3>{_ul(jobs)}</section>'
        f'<section><h3>Notable Residents</h3>{_ul(people)}</section>'
        '</div>'
        '</div>'
    )


def _render_town_sheet(con: sqlite3.Connection, world: str, settlement_id: str) -> str:
    settlement_table = _place_read_relation(con, "simulation_settlements")
    if _saved_table_has_world(con, settlement_table):
        row = con.execute(
            f"select * from {_quote_identifier(settlement_table)} where world = ? and settlement_id = ?",
            (world, settlement_id),
        ).fetchone()
    else:
        row = con.execute(
            f"select * from {_quote_identifier(settlement_table)} where settlement_id = ?",
            (settlement_id,),
        ).fetchone()
    if not row:
        if _has_table(con, "simulation_settlements"):
            where, params = _world_where(con, settlement_table, world)
            total = _count_one(con, f"select count(*) from {_quote_identifier(settlement_table)} where {where}", params)
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
    region_name = _history_region_label(con, world, rid)
    residence_sql = _person_residence_sql(con)
    alive_counts, top_jobs = _alive_counts_and_top_jobs_by_place(
        con,
        world,
        residence_sql,
        [sid],
        limit=8,
    )
    alive = alive_counts.get(sid, 0)
    jobs = [f"{job}: {n}" for job, n in top_jobs.get(sid, [])]
    residents = []
    for p in _top_people_for_where(con, world, f"{residence_sql} = ?", (sid,), limit=8):
        person = _person_from_row(p, _trait_slots_for_world(world))
        residents.append(_notable_person_label(person))
    prestige_metrics = _settlement_prestige_metrics(con, world, sid)
    cards = "".join(
        [
            _detail_card("Alive", alive),
            _detail_card("Level", row["level"] or ""),
            _detail_card("Status", row["status"] or ""),
            _detail_card("Region", region_name),
            _detail_card("Food Pressure", _fmt_number(row["food_pressure"])),
            _detail_card("Stability", _fmt_number(row["stability"])),
            _detail_card("Market Pull", _fmt_number(row["market_pull"])),
            _detail_card("Prosperity", _fmt_number(row["prosperity_pool"])),
            _detail_card("Elite Residents", prestige_metrics["elite_residents"]),
            _detail_card("Prestige Jobs", prestige_metrics["prestige_jobs"]),
            _detail_card("Patronage Ties", prestige_metrics["patronage_ties"]),
            _detail_card("Domestic Service", prestige_metrics["domestic_service"]),
            _detail_card("Elite Investments", prestige_metrics["elite_investments"]),
            _detail_card("Polity", _polity_names_for_settlement(con, sid, rid) or "None"),
            _detail_card("Founded", _format_year(row["founded_sim_year"], unknown_text="Unknown")),
        ]
    )
    name_bits = [row["etymology"], row["name_category_primary"], row["name_culture_primary"]]
    name_line = " | ".join(str(x) for x in name_bits if x)
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row["display_name"] or sid))}</h2>'
        f'<div class="place-subtitle">{html.escape(str(row["level"] or "settlement"))} in {html.escape(region_name)}</div>'
        f'<div class="place-muted">{html.escape(name_line)}</div>'
        f'<div class="place-grid">{cards}</div>'
        f'{_region_map_html(con, world, rid, focus_settlement_id=sid)}'
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
        select seat_id, title_id, slot_index, scope_settlement_id, holder_person_id, term_expires_sim_year
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
        elif terr["target_kind"] == "region":
            target = _history_region_label(con, world, target)
        territory_items.append(f"{terr['target_kind']}: {target} since {_format_year(terr['since_sim_year'])}")
    seat_items = []
    for seat in seats[:16]:
        holder = _person_link_text(con, world, seat["holder_person_id"]) if seat["holder_person_id"] else "vacant"
        scope = f" at {_settlement_name(con, world, seat['scope_settlement_id'])}" if seat["scope_settlement_id"] else ""
        seat_items.append(f"{seat['title_id']}{scope}: {holder}")
    office_history = _office_history_rows(con, pid)
    ruler_history = _head_office_history(world, row, seats, office_history)
    ruler_items = [
        _office_history_item(
            hist,
            holder_label=_office_holder_label_from_history(con, world, hist),
            include_office=False,
        )
        for hist in ruler_history
    ]
    recent_office_items = [
        _office_history_item(
            hist,
            holder_label=_office_holder_label_from_history(con, world, hist),
            include_office=True,
        )
        for hist in sorted(
            office_history,
            key=lambda hist: (
                _safe_int(hist["end_sim_year"], 10**9),
                _safe_int(hist["start_sim_year"], 0),
                _safe_int(hist["holding_id"], 0),
            ),
            reverse=True,
        )[:12]
    ]
    vassal_items = [f"{v['name']} ({v['polity_type_id']})" for v in vassals]
    row_keys = set(row.keys()) if hasattr(row, "keys") else set()
    city_state_items = _city_state_note_items(
        _city_state_note_from_value(row["notes_json"] if "notes_json" in row_keys else "")
    )
    cards = "".join(
        [
            _detail_card("Type", row["polity_type_id"] or ""),
            _detail_card("Status", row["status"] or ""),
            _detail_card("Territories", len(territories)),
            _detail_card("Seats", len(seats)),
            _detail_card("Held Seats", sum(1 for s in seats if s["holder_person_id"] is not None)),
            _detail_card("Vassals", len(vassals)),
            _detail_card("Capital", _settlement_name(con, world, row["capital_settlement_id"]) or "None"),
            _detail_card("Founded", _format_year(row["founded_sim_year"], unknown_text="Unknown")),
        ]
    )
    return (
        '<div class="place-sheet">'
        f'<h2>{html.escape(str(row["name"] or f"Polity {pid}"))}</h2>'
        f'<div class="place-subtitle">Polity #{pid}</div>'
        f'<div class="place-grid">{cards}</div>'
        '<div class="place-columns">'
        f'<section><h3>Territory</h3>{_ul(territory_items)}</section>'
        f'<section><h3>City-State</h3>{_ul(city_state_items)}</section>'
        f'<section><h3>Offices</h3>{_ul(seat_items)}</section>'
        f'<section><h3>Vassals</h3>{_ul(vassal_items)}</section>'
        f'<section><h3>Ruler Timeline</h3>{_ul(ruler_items)}</section>'
        f'<section><h3>Office History</h3>{_ul(recent_office_items)}</section>'
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
            if str(item_id or "").startswith("outlaw_refuge:"):
                html_out = _render_outlaw_refuge_sheet(con, saved_world, item_id)
            else:
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


def _map_selection_region_label(world: str, region_id: object) -> str:
    rid = str(region_id or "").strip()
    if not rid:
        return ""
    return _config_region_display_name(world, rid) or _display_title(rid)


def _map_selection_settlement_label(world: str, settlement_id: object) -> str:
    sid = str(settlement_id or "").strip()
    if not sid:
        return ""
    path = _db_path(world, "Save DB")
    if not path.exists():
        return sid
    try:
        with _connect_readonly(path) as con:
            saved_world = _resolve_saved_world(con, world)
            return _settlement_name(con, saved_world, sid) or sid
    except (FileNotFoundError, sqlite3.Error):
        return sid


def render_world_map_selection_detail(world: str, selection_json: str) -> str:
    if not selection_json:
        return '<div class="place-sheet muted">Click a region, settlement, outlaw refuge, or named feature on the map to inspect it.</div>'
    try:
        selection = json.loads(selection_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return '<div class="place-sheet muted">Click a region, settlement, outlaw refuge, or named feature on the map to inspect it.</div>'
    view = str(selection.get("view") or "Regions")
    item_id = str(selection.get("id") or "").strip()
    if view == "Outlaw Refuges" and item_id:
        return render_outlaw_refuge_detail(world, item_id)
    if view == "Map Routes" and item_id:
        layer = str(selection.get("layer") or "").strip()
        layer_title = {
            "road": "Road Route",
            "sea-route": "Sea Route",
            "river": "River",
        }.get(layer, "Map Route")
        from_settlement_id = str(selection.get("from_settlement_id") or "").strip()
        to_settlement_id = str(selection.get("to_settlement_id") or "").strip()
        river_id = str(selection.get("river_id") or "").strip()
        regions = str(selection.get("regions") or "").strip()
        region_labels = [
            _map_selection_region_label(world, rid)
            for rid in regions.split(",")
            if str(rid or "").strip()
        ]
        regions_label = ", ".join(label for label in region_labels if label)
        usage = str(selection.get("usage") or "").strip()
        actual_usage = str(selection.get("actual_usage") or "").strip()
        implied_usage = str(selection.get("implied_usage") or "").strip()
        from_label = _map_selection_settlement_label(world, from_settlement_id)
        to_label = _map_selection_settlement_label(world, to_settlement_id)
        route_label = (
            f"{from_label} -> {to_label}"
            if from_label or to_label
            else river_id or item_id
        )
        cards = [
            _detail_card("Layer", layer_title),
            _detail_card("Route", route_label),
        ]
        if regions_label:
            cards.append(_detail_card("Regions", regions_label))
        if usage:
            cards.append(_detail_card("Usage", usage))
        if actual_usage:
            cards.append(_detail_card("Actual", actual_usage))
        if implied_usage:
            cards.append(_detail_card("Implied", implied_usage))
        if river_id:
            cards.append(_detail_card("River ID", river_id))
        return (
            '<div class="place-sheet">'
            f"<h2>{html.escape(layer_title)}</h2>"
            f'<div class="place-subtitle">{html.escape(route_label)}</div>'
            f'<div class="place-grid">{"".join(cards)}</div>'
            "</div>"
        )
    if view == "Features" and item_id:
        name = str(selection.get("name") or item_id).strip()
        kind = str(selection.get("kind") or "feature").strip()
        region_id = str(selection.get("region_id") or "").strip()
        region_name = _map_selection_region_label(world, region_id)
        etymology = str(selection.get("etymology") or "").strip()
        is_named = str(selection.get("named") or "").strip().lower() in {"1", "true", "yes"}
        kind_title = _display_title(kind or "feature")
        title = name if is_named and name else kind_title
        subtitle = (
            f'Named {html.escape(kind_title)}'
            if is_named
            else f'Regional {html.escape(kind_title)} landmark'
        )
        cards = "".join(
            [
                _detail_card("Name", name if is_named and name else "Unnamed"),
                _detail_card("Kind", kind_title),
                _detail_card("Region", region_name or "Unknown"),
            ]
        )
        return (
            '<div class="place-sheet">'
            f'<h2>{html.escape(title)}</h2>'
            f'<div class="place-subtitle">{subtitle}'
            f'{(" in " + html.escape(region_name)) if region_name else ""}</div>'
            f'<div class="place-grid">{cards}</div>'
            f'{f"<p class=\"place-muted\">{html.escape(etymology)}</p>" if etymology else ""}'
            '</div>'
        )
    if view not in {"Regions", "Towns"} or not item_id:
        return '<div class="place-sheet muted">Click a region, settlement, outlaw refuge, or named feature on the map to inspect it.</div>'
    return render_place_detail(world, view, _encode_place_key(world, "", item_id))


def render_world_map_with_detail_reset(
    world: str,
    include_overlays: bool = True,
    include_inactive_settlements: bool = False,
    noisy_edges: bool = True,
    labels: bool = True,
    include_roads: bool = True,
) -> tuple[str, str]:
    return (
        render_world_map_html(
            world,
            include_overlays=include_overlays,
            noisy_edges=noisy_edges,
            labels=labels,
            include_inactive_settlements=include_inactive_settlements,
            include_roads=include_roads,
        ),
        '<div class="place-sheet muted">Click a region, settlement, outlaw refuge, or named feature on the map to inspect it.</div>',
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
        f'<span>Year {html.escape(_format_year(current))} / {html.escape(_format_year(end))}</span>'
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
        f"Simulation starting. Elapsed 00:00:00. Year {_format_year(start_int)} / {_format_year(end_year)}.",
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
                f"Running. Elapsed {elapsed}. Year {_format_year(current_year)} / {_format_year(expected_end)}.",
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
        f"Simulation finished. Elapsed {elapsed}. Year {_format_year(end_year)} / {_format_year(end_year)}."
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

        with gr.Tab("The Almanack"):
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=6):
                    with gr.Row():
                        almanack_world = gr.Dropdown(worlds, value=initial_world, label="World")
                        almanack_category = gr.Dropdown(
                            ALMANACK_CATEGORY_CHOICES,
                            value="All",
                            label="Category",
                        )
                        almanack_metric = gr.Dropdown(
                            ALMANACK_METRIC_CHOICES,
                            value="Murders Committed",
                            label="Metric",
                        )
                    with gr.Row():
                        almanack_life = gr.Radio(["All", "Alive", "Dead"], value="All", label="People")
                        almanack_source = gr.Radio(ALMANACK_SOURCE_CHOICES, value="Both", label="Source")
                        almanack_rank_mode = gr.Radio(
                            ALMANACK_RANK_MODE_CHOICES,
                            value="Raw Value",
                            label="Rank",
                        )
                    with gr.Row():
                        almanack_limit = gr.Number(value=50, label="Limit", precision=0)
                        almanack_min_value = gr.Textbox(value="", label="Minimum", placeholder="Any")
                    almanack_search = gr.Textbox(
                        label="Search The Almanack",
                        placeholder="Name, id, home, metric, evidence...",
                    )
                    with gr.Row():
                        almanack_load = gr.Button("Load Rankings", variant="primary")
                        almanack_refresh = gr.Button("Refresh Almanack")
                    almanack_status = gr.Textbox(label="Status", interactive=False)
                    almanack_table = gr.Dataframe(
                        value=[],
                        headers=ALMANACK_SELECTED_HEADERS,
                        label="The Almanack",
                        interactive=False,
                        wrap=True,
                        elem_id="almanack-table",
                    )
                    almanack_keys_state = gr.State([])
                with gr.Column(scale=5):
                    almanack_sheet = gr.HTML(
                        value='<div class="person-sheet muted">Load The Almanack, then click a row.</div>',
                        label="Almanack Detail",
                    )
                    almanack_share_text = gr.Textbox(
                        value="Load The Almanack, then click a row.",
                        label="Copyable Gmail Text",
                        lines=14,
                        max_lines=24,
                        interactive=False,
                        buttons=["copy"],
                    )
                    almanack_evidence_table = gr.Dataframe(
                        value=[],
                        headers=ALMANACK_EVIDENCE_HEADERS,
                        label="Why This Row?",
                        interactive=False,
                        wrap=True,
                    )
                    with gr.Row():
                        almanack_duel_a = gr.Textbox(label="Duel A", placeholder="Person id")
                        almanack_duel_b = gr.Textbox(label="Duel B", placeholder="Person id")
                    almanack_duel_button = gr.Button("Compare People")
                    almanack_duel_sheet = gr.HTML(
                        value='<div class="person-sheet muted">Enter two person ids to compare their Almanack traces.</div>',
                        label="Almanack Duel",
                    )
                    almanack_duel_text = gr.Textbox(
                        value="Enter two person ids to compare.",
                        label="Duel Copyable Text",
                        lines=10,
                        max_lines=20,
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

        with gr.Tab("Outlaws"):
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=6):
                    with gr.Row():
                        outlaw_world = gr.Dropdown(worlds, value=initial_world, label="World")
                        outlaw_case_status_filter = gr.Radio(["Active", "All", "Resolved"], value="Active", label="Cases")
                        outlaw_case_limit = gr.Number(value=75, label="Limit", precision=0)
                    outlaw_case_search = gr.Textbox(
                        label="Search Outlaw Cases",
                        placeholder="Name, id, offense, refuge, region, status...",
                    )
                    outlaw_case_load = gr.Button("Browse Outlaws", variant="primary")
                    outlaw_case_status = gr.Textbox(label="Status", interactive=False)
                    outlaw_case_table = gr.Dataframe(
                        label="Outlaw Cases",
                        interactive=False,
                        wrap=False,
                        elem_id="outlaw-case-table",
                    )
                    outlaw_case_keys_state = gr.State([])
                with gr.Column(scale=5):
                    outlaw_person_sheet = gr.HTML(
                        value='<div class="person-sheet muted">Browse outlaw cases, then click a row to open the accused person.</div>',
                        label="Outlaw Person Sheet",
                    )
                    outlaw_share_text = gr.Textbox(
                        value="Click an outlaw case row to generate share text.",
                        label="Copyable Gmail Text",
                        lines=14,
                        max_lines=24,
                        interactive=False,
                        buttons=["copy"],
                    )
            with gr.Row(elem_classes=["world-browser"]):
                with gr.Column(scale=6):
                    with gr.Row():
                        outlaw_refuge_status_filter = gr.Radio(["Active", "All", "Abandoned"], value="Active", label="Refuges")
                        outlaw_refuge_limit = gr.Number(value=50, label="Limit", precision=0)
                    outlaw_refuge_search = gr.Textbox(
                        label="Search Refuges",
                        placeholder="Refuge id, region, nearby settlement, status...",
                    )
                    outlaw_refuge_load = gr.Button("Browse Refuges")
                    outlaw_refuge_status = gr.Textbox(label="Refuge Status", interactive=False)
                    outlaw_refuge_table = gr.Dataframe(
                        label="Outlaw Refuges",
                        interactive=False,
                        wrap=False,
                        elem_id="outlaw-refuge-table",
                    )
                    outlaw_refuge_keys_state = gr.State([])
                with gr.Column(scale=5):
                    outlaw_refuge_sheet = gr.HTML(
                        value='<div class="place-sheet muted">Browse refuges, then click a row to inspect it.</div>',
                        label="Refuge Sheet",
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
                map_include_inactive_settlements = gr.Checkbox(value=False, label="Inactive Settlements")
                map_include_roads = gr.Checkbox(value=True, label="Routes")
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

        with gr.Tab("History"):
            with gr.Row(elem_classes=["world-browser"]):
                history_world = gr.Dropdown(worlds, value=initial_world, label="World")
                history_view = gr.Radio(
                    HISTORY_VIEW_CHOICES,
                    value="Public Chronicle",
                    label="View",
                )
                history_event_type = gr.Textbox(
                    value="",
                    label="Event Type",
                    placeholder="All, murder, property_crime...",
                )
                history_limit = gr.Number(value=100, label="Limit", precision=0)
                history_offset = gr.Number(value=0, label="Offset", precision=0)
            history_search = gr.Textbox(
                label="Search History",
                placeholder="Event type, place, payload value, visibility, source...",
            )
            history_load = gr.Button("Load History", variant="primary")
            history_status = gr.Textbox(label="Status", interactive=False)
            history_table = gr.Dataframe(
                label="History Rows",
                interactive=False,
                wrap=True,
            )
            history_summary_load = gr.Button("Load Summary")
            history_summary_status = gr.Textbox(label="Summary Status", interactive=False)
            history_summary_table = gr.Dataframe(
                label="History Summary",
                interactive=False,
                wrap=True,
            )
            with gr.Row(elem_classes=["world-browser"]):
                history_lens_kind = gr.Radio(
                    HISTORY_LENS_CHOICES,
                    value="Person",
                    label="Lens",
                )
                history_lens_focus = gr.Textbox(
                    value="",
                    label="Focus ID",
                    placeholder="Person id or settlement id",
                )
                history_lens_event_type = gr.Textbox(
                    value="",
                    label="Lens Event Type",
                    placeholder="All, murder, property_crime...",
                )
                history_lens_limit = gr.Number(value=100, label="Lens Limit", precision=0)
                history_lens_offset = gr.Number(value=0, label="Lens Offset", precision=0)
            history_lens_search = gr.Textbox(
                label="Search Lens",
                placeholder="Event type, place, payload value, visibility, source...",
            )
            history_lens_load = gr.Button("Load Lens")
            history_lens_status = gr.Textbox(label="Lens Status", interactive=False)
            history_lens_table = gr.Dataframe(
                label="History Lens Rows",
                interactive=False,
                wrap=True,
            )
            with gr.Row(elem_classes=["world-browser"]):
                rediscovery_event_type = gr.Textbox(
                    value="",
                    label="Rediscovery Event Type",
                    placeholder="All, birth, murder...",
                )
                rediscovery_limit = gr.Number(value=100, label="Rediscovery Limit", precision=0)
                rediscovery_offset = gr.Number(value=0, label="Rediscovery Offset", precision=0)
            rediscovery_search = gr.Textbox(
                label="Search Rediscoveries",
                placeholder="Event type, source, distortion, payload value...",
            )
            rediscovery_detail_load = gr.Button("Load Rediscovery Details")
            rediscovery_detail_status = gr.Textbox(label="Rediscovery Detail Status", interactive=False)
            rediscovery_detail_table = gr.Dataframe(
                label="Rediscovery Details",
                interactive=False,
                wrap=True,
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
                sim_reset = gr.Checkbox(value=True, label="Reset", scale=0, min_width=75)
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
        almanack_inputs = [
            almanack_world,
            almanack_category,
            almanack_metric,
            almanack_life,
            almanack_source,
            almanack_search,
            almanack_min_value,
            almanack_limit,
            almanack_rank_mode,
        ]
        almanack_outputs = [
            almanack_table,
            almanack_status,
            almanack_keys_state,
            almanack_sheet,
            almanack_share_text,
        ]
        almanack_load.click(load_almanack_browser, almanack_inputs, almanack_outputs)
        almanack_refresh.click(refresh_almanack_browser, almanack_inputs, almanack_outputs)
        almanack_search.submit(load_almanack_browser, almanack_inputs, almanack_outputs)
        almanack_table.select(
            select_almanack_from_table,
            [almanack_keys_state, almanack_world],
            [almanack_sheet, almanack_share_text, almanack_evidence_table],
        )
        almanack_duel_button.click(
            load_almanack_duel,
            [almanack_world, almanack_duel_a, almanack_duel_b],
            [almanack_duel_sheet, almanack_duel_text],
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
        outlaw_case_inputs = [
            outlaw_world,
            outlaw_case_status_filter,
            outlaw_case_search,
            outlaw_case_limit,
        ]
        outlaw_case_outputs = [outlaw_case_table, outlaw_case_status, outlaw_case_keys_state]
        outlaw_case_load.click(load_outlaw_cases_browser, outlaw_case_inputs, outlaw_case_outputs)
        for outlaw_case_input in outlaw_case_inputs:
            outlaw_case_input.change(load_outlaw_cases_browser, outlaw_case_inputs, outlaw_case_outputs)
        outlaw_case_search.submit(load_outlaw_cases_browser, outlaw_case_inputs, outlaw_case_outputs)
        outlaw_case_table.select(
            select_outlaw_case_from_table,
            [outlaw_case_keys_state, outlaw_world],
            [outlaw_person_sheet, outlaw_share_text],
        )
        outlaw_refuge_inputs = [
            outlaw_world,
            outlaw_refuge_status_filter,
            outlaw_refuge_search,
            outlaw_refuge_limit,
        ]
        outlaw_refuge_outputs = [outlaw_refuge_table, outlaw_refuge_status, outlaw_refuge_keys_state]
        outlaw_refuge_load.click(load_outlaw_refuges_browser, outlaw_refuge_inputs, outlaw_refuge_outputs)
        for outlaw_refuge_input in outlaw_refuge_inputs:
            outlaw_refuge_input.change(load_outlaw_refuges_browser, outlaw_refuge_inputs, outlaw_refuge_outputs)
        outlaw_refuge_search.submit(load_outlaw_refuges_browser, outlaw_refuge_inputs, outlaw_refuge_outputs)
        outlaw_refuge_table.select(
            select_outlaw_refuge_from_table,
            [outlaw_refuge_keys_state, outlaw_world],
            outlaw_refuge_sheet,
        )
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
        map_inputs = [
            map_world,
            map_include_overlays,
            map_include_inactive_settlements,
            map_noisy_edges,
            map_labels,
            map_include_roads,
        ]
        map_outputs = [world_map_html, map_sheet]
        map_refresh.click(render_world_map_with_detail_reset, map_inputs, map_outputs)
        for map_input in map_inputs:
            map_input.change(render_world_map_with_detail_reset, map_inputs, map_outputs)
        map_open_button.click(render_world_map_selection_detail, [map_world, map_open_selection], map_sheet)
        history_inputs = [
            history_world,
            history_view,
            history_event_type,
            history_search,
            history_limit,
            history_offset,
        ]
        history_outputs = [history_table, history_status]
        history_load.click(load_history_browser, history_inputs, history_outputs)
        history_search.submit(load_history_browser, history_inputs, history_outputs)
        history_summary_load.click(
            load_history_summary,
            [history_world],
            [history_summary_table, history_summary_status],
        )
        history_lens_inputs = [
            history_world,
            history_lens_kind,
            history_lens_focus,
            history_lens_event_type,
            history_lens_search,
            history_lens_limit,
            history_lens_offset,
        ]
        history_lens_outputs = [history_lens_table, history_lens_status]
        history_lens_load.click(load_history_lens, history_lens_inputs, history_lens_outputs)
        history_lens_search.submit(load_history_lens, history_lens_inputs, history_lens_outputs)
        history_lens_focus.submit(load_history_lens, history_lens_inputs, history_lens_outputs)
        rediscovery_detail_inputs = [
            history_world,
            rediscovery_event_type,
            rediscovery_search,
            rediscovery_limit,
            rediscovery_offset,
        ]
        rediscovery_detail_outputs = [rediscovery_detail_table, rediscovery_detail_status]
        rediscovery_detail_load.click(
            load_rediscovery_details,
            rediscovery_detail_inputs,
            rediscovery_detail_outputs,
        )
        rediscovery_search.submit(
            load_rediscovery_details,
            rediscovery_detail_inputs,
            rediscovery_detail_outputs,
        )
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
