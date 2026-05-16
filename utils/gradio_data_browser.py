"""Read-only Gradio browser for History Project config CSVs and SQLite worlds."""

from __future__ import annotations

import argparse
import csv
import html
import importlib.util
import json
import shlex
import re
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Iterable

import gradio as gr


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
WORLDS_DIR = PROJECT_ROOT / "worlds"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

APP_CSS = """
.world-browser {
    font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
    border: 1px solid #d6c7a1 !important;
    background: linear-gradient(180deg, #fbf8ef 0%, #f4ecd8 100%) !important;
    color: #2f2a21 !important;
    padding: 18px;
    border-radius: 8px;
}
.person-sheet,
.person-sheet * {
    box-sizing: border-box;
}
.person-sheet :where(h1, h2, h3, h4, h5, h6, p, div, span, strong, em, li, section, article),
.person-sheet :where(.prose, .markdown, .md, .output-html, .gr-html) {
    color: #2f2a21 !important;
}
.person-title {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    border-bottom: 1px solid #dccda9;
    padding-bottom: 12px;
    margin-bottom: 14px;
}
.person-title h2 {
    color: #2f2a21 !important;
    font-size: 28px;
    line-height: 1.1;
    margin: 0;
}
.person-title .badge {
    border: 1px solid #9d8352;
    color: #4f3f25 !important;
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
    background: rgba(255, 255, 255, .55) !important;
    border: 1px solid #e4d7b8 !important;
    border-radius: 6px;
    padding: 10px;
}
.detail-label {
    color: #6f6046 !important;
    font-size: 12px;
    text-transform: uppercase;
}
.detail-value {
    color: #2f2a21 !important;
    font-size: 16px;
    font-weight: 650;
}
.section-title {
    margin: 18px 0 8px;
    color: #58452a !important;
    font-size: 18px;
}
.trait-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 8px 12px;
}
.trait-row {
    display: grid;
    grid-template-columns: 96px 48px 1fr;
    align-items: center;
    gap: 8px;
    font-size: 13px;
}
.trait-name {
    color: #2f2a21 !important;
    text-transform: capitalize;
}
.trait-value {
    color: #2f2a21 !important;
    font-variant-numeric: tabular-nums;
    text-align: right;
}
.trait-track {
    position: relative;
    height: 12px;
    border-radius: 999px;
    background: linear-gradient(90deg, #9c4f48 0%, #d8c27a 28%, #4f7f74 50%, #d8c27a 72%, #9c4f48 100%);
    overflow: hidden;
}
.trait-marker {
    position: absolute;
    top: -3px;
    width: 4px;
    height: 18px;
    border-radius: 3px;
    background: #211b14;
    box-shadow: 0 0 0 1px rgba(255,255,255,.8);
}
.legacy-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 8px;
}
.legacy-score {
    border: 1px solid #d9c79f !important;
    background: rgba(255,255,255,.42) !important;
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
    color: #6f6046 !important;
    font-size: 12px;
    line-height: 1.25;
    margin-top: 4px;
}
.legacy-track {
    height: 7px;
    border-radius: 999px;
    background: rgba(111, 96, 70, .20) !important;
    margin-top: 7px;
    overflow: hidden;
}
.legacy-fill {
    height: 100%;
    border-radius: 999px;
    background: #6a7d3e !important;
}
.pill-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}
.pill {
    border: 1px solid #b99f69;
    background: rgba(255,255,255,.48);
    color: #2f2a21 !important;
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
    border-left: 3px solid #9d8352;
    background: rgba(255,255,255,.45);
    color: #2f2a21 !important;
    padding: 7px 9px;
}
.muted {
    color: #6f6046 !important;
}
.person-link {
    color: #4d5f35 !important;
    font-weight: 700;
    text-decoration: underline;
    text-underline-offset: 2px;
    cursor: pointer;
}
.person-link:hover {
    color: #263515 !important;
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
    return WORLDS_DIR / world / filename


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


def _dataframe(rows: Iterable[sqlite3.Row | dict[str, object]], headers: list[str]) -> gr.Dataframe:
    values = [[row.get(header, "") if isinstance(row, dict) else row[header] for header in headers] for row in rows]
    return gr.Dataframe(value=values, headers=headers, datatype=["str"] * len(headers), interactive=False)


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


def _person_name(person: dict[str, object]) -> str:
    first = str(person.get("first_name") or "").strip()
    last = str(person.get("last_name") or "").strip()
    return " ".join(part for part in (first, last) if part) or "Unnamed"


def _person_summary_row(row: sqlite3.Row, current_year: int | None) -> dict[str, object]:
    person = _load_json_object(row["person_json"])
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
    headers = {
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
    out = {label: "" for label in headers.values()}
    if not isinstance(traits, dict) or not traits:
        return out
    try:
        rows = _legacy_indices_module().legacy_index_scores(traits)
    except Exception:
        return out
    for row in rows:
        label = headers.get(str(row.key))
        if label:
            out[label] = f"{float(row.score):.2f}"
    return out


def _current_year(con: sqlite3.Connection, world: str) -> int | None:
    row = con.execute("select current_year from world_state where world = ?", (world,)).fetchone()
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
        current_year = _current_year(con, world)

    age_sql = "coalesce(json_extract(person_json, '$.deathyear'), ?) - json_extract(person_json, '$.birthyear')"
    clauses = ["world = ?"]
    params: list[object] = [world]
    if life_filter == "Alive":
        clauses.append("is_alive = 1")
    elif life_filter == "Dead":
        clauses.append("is_alive = 0")
    if search:
        clauses.append("person_json like ?")
        params.append(f"%{search}%")
    if min_age not in (None, ""):
        clauses.append(f"{age_sql} >= ?")
        params.extend([current_year, _safe_int(min_age, 0, 0, 10_000)])
    if max_age not in (None, ""):
        clauses.append(f"{age_sql} <= ?")
        params.extend([current_year, _safe_int(max_age, 10_000, 0, 10_000)])

    where_sql = " and ".join(clauses)
    sort_map = {
        "ID": "person_id",
        "Name": "json_extract(person_json, '$.last_name') collate nocase, json_extract(person_json, '$.first_name') collate nocase",
        "Age": age_sql,
        "Born": "json_extract(person_json, '$.birthyear')",
        "Died": "json_extract(person_json, '$.deathyear')",
        "Gender": "json_extract(person_json, '$.gender') collate nocase",
        "Species": "json_extract(person_json, '$.species') collate nocase",
        "Ethnic": "json_extract(person_json, '$.ethnic') collate nocase",
        "Home": "coalesce(json_extract(person_json, '$.current_settlement_id'), json_extract(person_json, '$.birthplace')) collate nocase",
    }
    order_sql = sort_map.get(sort_by or "", "is_alive desc, person_id desc")
    order_sql_default = sort_map.get("Age" or "", "is_alive desc, person_id desc")
    direction = "asc" if sort_dir == "Ascending" else "desc"
    if sort_by in (None, "", "Default"):
        order_clause = f"{order_sql_default} desc, person_id desc"
        order_params = [current_year]
    elif sort_by == "Age":
        order_clause = f"{order_sql} {direction}, person_id desc"
        order_params = [current_year]
    else:
        order_clause = f"{order_sql} {direction}, person_id desc"
        order_params = []

    with _connect_readonly(path) as con:
        rows = con.execute(
            f"""
            select person_id, is_alive, person_json
            from simulation_people
            where {where_sql}
            order by {order_clause}
            limit ?
            """,
            [*params, *order_params, row_limit],
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
    values = [_person_summary_row(row, current_year) for row in rows]
    person_ids = [int(row["person_id"]) for row in rows]
    filter_bits = []
    if min_age not in (None, ""):
        filter_bits.append(f"age >= {_safe_int(min_age, 0, 0, 10_000)}")
    if max_age not in (None, ""):
        filter_bits.append(f"age <= {_safe_int(max_age, 10_000, 0, 10_000)}")
    filter_text = f" | filters: {', '.join(filter_bits)}" if filter_bits else ""
    status = f"{path.name}: showing {len(values)} of {total} people{filter_text}. Click any person row to open their sheet."
    return _dataframe(values, headers), status, person_ids


def _lookup_person(con: sqlite3.Connection, world: str, person_id: object) -> tuple[sqlite3.Row | None, dict[str, object]]:
    try:
        pid = int(person_id)
    except (TypeError, ValueError):
        return None, {}
    row = con.execute(
        "select * from simulation_people where world = ? and person_id = ?",
        (world, pid),
    ).fetchone()
    return row, _load_json_object(row["person_json"]) if row else {}


def _settlement_name(con: sqlite3.Connection, world: str, settlement_id: object) -> str:
    if not settlement_id:
        return ""
    row = con.execute(
        "select display_name from simulation_settlements where world = ? and settlement_id = ?",
        (world, str(settlement_id)),
    ).fetchone()
    return str(row["display_name"] or settlement_id) if row else str(settlement_id)


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
    return con.execute(
        """
        select sim_year, event_type, payload_json
        from simulation_events
        where world = ?
          and (
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
            world,
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
    con: sqlite3.Connection, world: str, person_ids: object, *, limit: int = 5
) -> str:
    if not isinstance(person_ids, list) or not person_ids:
        return "none"
    shown = ", ".join(_short_person(con, world, pid) for pid in person_ids[:limit])
    if len(person_ids) > limit:
        shown += f", and {len(person_ids) - limit} more"
    return shown


def _person_list_html(
    con: sqlite3.Connection, world: str, person_ids: object, *, limit: int = 5
) -> str:
    if not isinstance(person_ids, list) or not person_ids:
        return "none"
    shown = ", ".join(_short_person_html(con, world, pid) for pid in person_ids[:limit])
    if len(person_ids) > limit:
        shown += f", and {len(person_ids) - limit} more"
    return shown


def _event_sentence(con: sqlite3.Connection, world: str, event: sqlite3.Row, focus_person_id: object) -> str:
    payload = _load_json_object(event["payload_json"])
    event_type = str(event["event_type"] or payload.get("event_type") or "").strip()
    person = _short_person(con, world, payload.get("person_id") or focus_person_id)
    event_label = event_type.replace("_", " ")

    if event_type in {"birth", "founder_created"}:
        child_id = payload.get("child_id") or payload.get("person_id")
        child = _short_person(con, world, child_id)
        if event_type == "founder_created":
            return f"{child} entered the world as a founder."
        parent_a = _short_person(con, world, payload.get("person_a_id"))
        parent_b = _short_person(con, world, payload.get("person_b_id"))
        return f"{child} was born to {parent_a} and {parent_b}."

    if event_type in {"couple_formed", "couple_dissolved", "paramour_formed", "paramour_ended", "same_sex_couple_formed"}:
        a = _short_person(con, world, payload.get("person_a_id"))
        b = _short_person(con, world, payload.get("person_b_id"))
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
            moved = ", ".join(_short_person(con, world, pid) for pid in moved_ids[:6])
            if len(moved_ids) > 6:
                moved += f", and {len(moved_ids) - 6} more"
            return f"{moved} moved from {from_place} to {to_place}; reason: {reason}."
        return f"{person} moved from {from_place} to {to_place}; reason: {reason}."

    if event_type == "partner_residence_reconciled":
        moved = _short_person(con, world, payload.get("moved_person_id") or focus_person_id)
        to_place = _settlement_name(con, world, payload.get("target_settlement_id")) or str(payload.get("target_settlement_id") or "unknown")
        return f"{moved} moved to {to_place} to reconcile partner residence."

    if event_type == "orphan_routed_to_largest_settlement":
        child = _short_person(con, world, payload.get("person_id") or focus_person_id)
        from_place = _settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or "unknown")
        to_place = _settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or "unknown")
        return f"{child} was routed from {from_place} to {to_place} for local care."

    if event_type == "household_childcare_shortfall":
        minors = payload.get("dependent_minor_ids")
        minor_count = len(minors) if isinstance(minors, list) else int(payload.get("need") or 0)
        supply = _event_float(payload, "supply")
        shortfall = _event_float(payload, "shortfall")
        outcome = str(payload.get("outcome") or "unknown").replace("_", " ")
        victim = _short_person(con, world, payload.get("victim_person_id") or focus_person_id)
        bits = [f"Household childcare shortfall affected {minor_count} dependent minor{'s' if minor_count != 1 else ''}"]
        if supply is not None:
            bits.append(f"care supply {supply:.2f}")
        if shortfall is not None:
            bits.append(f"shortfall {shortfall:.2f}")
        bits.append(f"outcome {outcome}")
        bits.append(f"victim {victim}")
        return "; ".join(bits) + "."

    if event_type == "household_prosperity_crisis":
        members = _person_list_text(con, world, payload.get("household_member_ids"))
        before = _event_float(payload, "prosperity_before")
        after = _event_float(payload, "prosperity_after")
        purseholder = _short_person(con, world, payload.get("purseholder_person_id") or focus_person_id)
        bits = [f"{purseholder}'s household entered prosperity crisis"]
        if before is not None and after is not None:
            bits.append(f"savings {before:.2f} -> {after:.2f}")
        bits.append(f"members: {members}")
        return "; ".join(bits) + "."

    if event_type == "office_succession":
        holder = _short_person(con, world, payload.get("holder_person_id") or focus_person_id)
        previous = _short_person(con, world, payload.get("previous_holder_id"))
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
        return f"{event_label}: {person} and {_short_person(con, world, other_id)}."
    return f"{event_label}: {person}."


def _event_sentence_html(con: sqlite3.Connection, world: str, event: sqlite3.Row, focus_person_id: object) -> str:
    payload = _load_json_object(event["payload_json"])
    event_type = str(event["event_type"] or payload.get("event_type") or "").strip()
    person = _short_person_html(con, world, payload.get("person_id") or focus_person_id)
    event_label = html.escape(event_type.replace("_", " "))

    if event_type in {"birth", "founder_created"}:
        child_id = payload.get("child_id") or payload.get("person_id")
        child = _short_person_html(con, world, child_id)
        if event_type == "founder_created":
            return f"{child} entered the world as a founder."
        parent_a = _short_person_html(con, world, payload.get("person_a_id"))
        parent_b = _short_person_html(con, world, payload.get("person_b_id"))
        return f"{child} was born to {parent_a} and {parent_b}."

    if event_type in {"couple_formed", "couple_dissolved", "paramour_formed", "paramour_ended", "same_sex_couple_formed"}:
        a = _short_person_html(con, world, payload.get("person_a_id"))
        b = _short_person_html(con, world, payload.get("person_b_id"))
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
            moved = ", ".join(_short_person_html(con, world, pid) for pid in moved_ids[:6])
            if len(moved_ids) > 6:
                moved += f", and {len(moved_ids) - 6} more"
            return f"{moved} moved from {from_place} to {to_place}; reason: {reason}."
        return f"{person} moved from {from_place} to {to_place}; reason: {reason}."

    if event_type == "partner_residence_reconciled":
        moved = _short_person_html(con, world, payload.get("moved_person_id") or focus_person_id)
        to_place = html.escape(_settlement_name(con, world, payload.get("target_settlement_id")) or str(payload.get("target_settlement_id") or "unknown"))
        return f"{moved} moved to {to_place} to reconcile partner residence."

    if event_type == "orphan_routed_to_largest_settlement":
        child = _short_person_html(con, world, payload.get("person_id") or focus_person_id)
        from_place = html.escape(_settlement_name(con, world, payload.get("from_settlement_id")) or str(payload.get("from_settlement_id") or "unknown"))
        to_place = html.escape(_settlement_name(con, world, payload.get("to_settlement_id")) or str(payload.get("to_settlement_id") or "unknown"))
        return f"{child} was routed from {from_place} to {to_place} for local care."

    if event_type == "household_childcare_shortfall":
        minors = payload.get("dependent_minor_ids")
        minor_count = len(minors) if isinstance(minors, list) else int(payload.get("need") or 0)
        supply = _event_float(payload, "supply")
        shortfall = _event_float(payload, "shortfall")
        outcome = html.escape(str(payload.get("outcome") or "unknown").replace("_", " "))
        victim = _short_person_html(con, world, payload.get("victim_person_id") or focus_person_id)
        bits = [f"Household childcare shortfall affected {minor_count} dependent minor{'s' if minor_count != 1 else ''}"]
        if supply is not None:
            bits.append(f"care supply {supply:.2f}")
        if shortfall is not None:
            bits.append(f"shortfall {shortfall:.2f}")
        bits.append(f"outcome {outcome}")
        bits.append(f"victim {victim}")
        return "; ".join(bits) + "."

    if event_type == "household_prosperity_crisis":
        members = _person_list_html(con, world, payload.get("household_member_ids"))
        before = _event_float(payload, "prosperity_before")
        after = _event_float(payload, "prosperity_after")
        purseholder = _short_person_html(con, world, payload.get("purseholder_person_id") or focus_person_id)
        bits = [f"{purseholder}'s household entered prosperity crisis"]
        if before is not None and after is not None:
            bits.append(f"savings {before:.2f} -&gt; {after:.2f}")
        bits.append(f"members: {members}")
        return "; ".join(bits) + "."

    if event_type == "office_succession":
        holder = _short_person_html(con, world, payload.get("holder_person_id") or focus_person_id)
        previous = _short_person_html(con, world, payload.get("previous_holder_id"))
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
        return f"{event_label}: {person} and {_short_person_html(con, world, other_id)}."
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
    children = con.execute(
        """
        select person_id, person_json
        from simulation_people
        where world = ? and (father_id = ? or mother_id = ?)
        order by person_id
        limit 12
        """,
        (world, row["person_id"], row["person_id"]),
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
    genome = person.get("mind_body") or person.get("genome") or {}
    legacy_scores_html = _render_legacy_scores(genome)
    trait_rows: list[str] = []
    if isinstance(genome, dict):
        for trait, raw_value in sorted(genome.items(), key=lambda item: -abs(float(item[1]))):
            value = float(raw_value)
            pos = max(0, min(100, (value + 100) / 2))
            phrase = _trait_phrase(str(trait), value, labels, soften_typical=True)
            aria_label = _trait_accessibility_label(str(trait), value, phrase)
            trait_rows.append(
                '<div class="trait-row" role="group" '
                f'aria-label="{html.escape(aria_label)}">'
                f'<div class="trait-name" title="{html.escape(phrase)}">{html.escape(str(trait))}</div>'
                f'<div class="trait-value">{value:+.1f}</div>'
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

    children = con.execute(
        """
        select person_id, person_json
        from simulation_people
        where world = ? and (father_id = ? or mother_id = ?)
        order by person_id
        limit 12
        """,
        (world, row["person_id"], row["person_id"]),
    ).fetchall()
    child_lines = [
        f"- {_person_name(_load_json_object(child['person_json']))}"
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
        row, person = _lookup_person(save_con, world, pid)
        if not row:
            return f'<div class="person-sheet muted" role="status">No person #{html.escape(str(pid))} in {html.escape(world)}.</div>'
        return _render_person_sheet(save_con, world, row, person)


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
        row, person = _lookup_person(save_con, world, pid)
        if not row:
            return f"No person #{pid} in {world}."
        return _render_person_share_text(save_con, world, row, person)


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
) -> tuple[str, str, str, str]:
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
        return "Invalid inputs", str(exc), "", ""

    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    status = "Simulation finished." if completed.returncode == 0 else f"Simulation failed with exit code {completed.returncode}."
    command_text = subprocess.list2cmdline(command)
    return status, command_text, completed.stdout, completed.stderr


def build_app(default_world: str = "default") -> gr.Blocks:
    worlds = _world_names()
    csvs = _csv_names()
    initial_world = default_world if default_world in worlds else (worlds[0] if worlds else "")
    initial_tables = _table_names(initial_world, "Config DB") if initial_world else []

    with gr.Blocks(title="History Project Data Browser") as app:
        gr.HTML(f"<style>{APP_CSS}</style>")
        gr.Markdown("# History Project World Browser")

        with gr.Tab("World Browser"):
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
                            ["Default", "ID", "Name", "Age", "Born", "Died", "Gender", "Species", "Ethnic", "Home"],
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
                sim_world = gr.Textbox(value=initial_world or "default", label="World ID")
                sim_years = gr.Number(value=100, label="Years", precision=0)
                sim_starting_couples = gr.Number(value=10, label="Starting Couples", precision=0)
                sim_start_year = gr.Number(value=1000, label="Start Year", precision=0)
            with gr.Row():
                sim_seed = gr.Textbox(value="", label="Seed", placeholder="Blank = random")
                sim_flush = gr.Number(value=50, label="Flush Batch Years", precision=0)
                sim_reset = gr.Checkbox(value=False, label="Reset world before run")
                sim_skip_timing = gr.Checkbox(value=False, label="Skip timing log")
            sim_extra_args = gr.Textbox(
                value="",
                label="Extra CLI Args",
                placeholder="Optional, for newly added flags",
            )
            sim_preview = gr.Textbox(label="Command Preview", lines=3, interactive=False)
            with gr.Row():
                sim_preview_button = gr.Button("Preview Command")
                sim_run_button = gr.Button("Run Simulation", variant="primary")
            sim_status = gr.Textbox(label="Status", interactive=False)
            sim_stdout = gr.Textbox(label="Output", lines=14, interactive=False)
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
        app.load(
            load_people_browser,
            person_browser_inputs,
            [person_table, person_status, person_ids_state],
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
        sim_preview_button.click(preview_sim_command, sim_inputs, sim_preview)
        for sim_input in sim_inputs:
            sim_input.change(preview_sim_command, sim_inputs, sim_preview)
        sim_run_button.click(
            run_simulation_from_ui,
            sim_inputs,
            [sim_status, sim_preview, sim_stdout, sim_stderr],
        )

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default="default", help="Initial world selection.")
    parser.add_argument("--host", default="127.0.0.1", help="Gradio host.")
    parser.add_argument("--port", type=int, default=7860, help="Gradio port.")
    args = parser.parse_args()
    build_app(args.world).launch(server_name=args.host, server_port=args.port)


if __name__ == "__main__":
    main()
