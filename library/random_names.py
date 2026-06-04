"""Random given and family names from SQLite ethnic / first_name / last_name tables."""

from __future__ import annotations

import random
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import Any

from library.geography import get_region
from library.random_traits import DEFAULT_DB_PATH, _as_int, _connect

_SURNAME_MODES: tuple[tuple[str, str], ...] = (
    ("kin", "sur_kin_rate"),
    ("hails", "sur_hails_rate"),
    ("lookup", "sur_lookup_rate"),
    ("none", "sur_none_rate"),
)
SURNAME_MODE_VALUES: frozenset[str] = frozenset(mode for mode, _ in _SURNAME_MODES)

# ``species.ethnic`` values that have no ``ethnic`` / name-table rows yet → nearest pool.
_NAME_ETHNIC_ALIASES: dict[str, str] = {
    "Pannonian": "Dacian",
    "Phoenician/Punic": "Latin/Roman",
    "Phrygian": "Thracian",
    "Romanian/Vlach": "Latin/Roman",
    "Samnite/Oscan": "Latin/Roman",
    "Scythian/Sarmatian": "Old Polish/West Slavic",
    "Sicel": "Messapic",
    "Tocharian": "Middle German",
    "Umbrian": "Latin/Roman",
}


def _name_ethnic_for_tables(ethnic: str) -> str:
    key = (ethnic or "").strip()
    return _NAME_ETHNIC_ALIASES.get(key, key)


@lru_cache(maxsize=8)
def _name_tables_cached(
    db_path_s: str,
) -> tuple[
    dict[str, dict[str, object]],
    dict[tuple[str, str], list[tuple[str, int, int]]],
    dict[str, list[tuple[str, int]]],
]:
    path = Path(db_path_s)
    if not path.is_file():
        raise FileNotFoundError(
            f"Database not found: {path}. Run: python utils/util_load_config.py --world default"
        )
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        ethnic_rows = [
            {str(k): row[k] for k in row.keys()}
            for row in conn.execute("SELECT * FROM ethnic")
        ]
        first_rows = [
            {
                "ethnic": row["ethnic"],
                "gender": row["gender"],
                "name": row["name"],
                "rate": row["rate"],
                "name_part": row["name_part"],
            }
            for row in conn.execute(
                "SELECT ethnic, gender, name, rate, name_part FROM first_name"
            )
        ]
        last_rows = [
            {"ethnic": row["ethnic"], "name": row["name"], "rate": row["rate"]}
            for row in conn.execute("SELECT ethnic, name, rate FROM last_name")
        ]

    ethnic_map: dict[str, dict[str, object]] = {}
    for row in ethnic_rows:
        ethnic = str(row["ethnic"] or "").strip()
        if not ethnic:
            continue
        ethnic_map[ethnic] = dict(row)

    first_map: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
    for row in first_rows:
        ethnic = str(row["ethnic"] or "").strip()
        gender = str(row["gender"] or "").strip()
        name = str(row["name"] or "").strip()
        rate = max(0, _as_int(row["rate"], 0))
        name_part = _as_int(row["name_part"], 0)
        if not ethnic or not gender or not name or rate <= 0:
            continue
        first_map.setdefault((ethnic, gender), []).append((name, rate, name_part))

    last_map: dict[str, list[tuple[str, int]]] = {}
    for row in last_rows:
        ethnic = str(row["ethnic"] or "").strip()
        name = str(row["name"] or "").strip()
        rate = max(0, _as_int(row["rate"], 0))
        if not ethnic or not name or rate <= 0:
            continue
        last_map.setdefault(ethnic, []).append((name, rate))
    return ethnic_map, first_map, last_map


def preload_name_cache(*, db_path: Path | str | None = None) -> None:
    """Warm name-table cache once for simulation startup."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    _name_tables_cached(str(path.resolve()))


def world_start_year(*, db_path: Path | str | None, world: str) -> int:
    """``start_year`` for ``world`` from ``world_start`` (simulation clock default)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            'SELECT "start_year" FROM world_start WHERE "world" = ?',
            (world.strip(),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise LookupError(f"No world_start row for world={world!r}")
    y = _as_int(row["start_year"], 0)
    if y <= 0:
        raise LookupError(f"world_start.start_year invalid for world={world!r}")
    return y


def _parse_min_max(cell: object, *, default: tuple[int, int] = (1, 1)) -> tuple[int, int]:
    if cell is None:
        return default
    text = str(cell).strip()
    if not text:
        return default
    parts = [p.strip() for p in text.split(";") if p.strip()]
    if len(parts) >= 2:
        lo, hi = _as_int(parts[0], default[0]), _as_int(parts[1], default[1])
    else:
        lo = hi = _as_int(parts[0], default[0])
    lo = max(1, lo)
    hi = max(lo, hi)
    return lo, hi


def roll_name_component_count(min_c: int, max_c: int) -> int:
    """Roll 0–8 to stop at current count (starting at ``min_c``); 9 increments until ``max_c``."""
    n = min_c
    hi = max(min_c, max_c)
    while True:
        if n >= hi:
            return hi
        if random.randint(0, 9) < 9:
            return n
        n += 1


def _suppress_name_part(ethnic_row: sqlite3.Row) -> bool:
    for key in ("suppress_name_part", "suppress_constructed"):
        if key in ethnic_row.keys() and ethnic_row[key] is not None:
            s = str(ethnic_row[key]).strip()
            if s != "":
                return _as_int(ethnic_row[key], 0) == 1
    return False


def _ethnic_row(conn: sqlite3.Connection, ethnic: str) -> sqlite3.Row:
    row = conn.execute(
        'SELECT * FROM ethnic WHERE "ethnic" = ?',
        (ethnic.strip(),),
    ).fetchone()
    if row is None:
        raise LookupError(f"No ethnic row for ethnic={ethnic!r}")
    return row


def _choose_weighted_mode(ethnic_row: sqlite3.Row) -> str:
    weighted: list[tuple[str, int]] = []
    for mode, col in _SURNAME_MODES:
        if col not in ethnic_row.keys():
            continue
        w = max(0, _as_int(ethnic_row[col], 0))
        if w > 0:
            weighted.append((mode, w))
    if not weighted:
        raise LookupError(
            f"All surname rates are zero for ethnic={ethnic_row['ethnic']!r}"
        )
    modes, weights = zip(*weighted)
    return random.choices(modes, weights=weights, k=1)[0]


def _father_last_name_is_kin(
    *,
    ethnic_map: dict[str, dict[str, object]],
    father_last_name: str | None,
    father_ethnic: str | None,
) -> bool:
    father_last = str(father_last_name or "").strip()
    if not father_last:
        return False
    father_er = ethnic_map.get(_name_ethnic_for_tables(str(father_ethnic or "")))
    if father_er is None:
        return False
    for key in ("kin_m", "kin_f"):
        tpl = str(father_er.get(key) or "").strip()
        if _matches_kin_template(father_last, tpl):
            return True
    return False


def _normalize_birth_surname_mode(
    *,
    mode: str | None,
    ethnic_row: dict[str, object],
    ethnic_map: dict[str, dict[str, object]],
    father_last_name: str | None,
    father_ethnic: str | None,
) -> str:
    chosen = str(mode or "").strip().lower()
    if not chosen:
        chosen = _choose_weighted_mode(ethnic_row)
    if chosen not in SURNAME_MODE_VALUES:
        expected = sorted(SURNAME_MODE_VALUES)
        raise ValueError(f"Unknown surname convention {mode!r}; expected one of {expected!r}")
    if chosen == "lookup" and _father_last_name_is_kin(
        ethnic_map=ethnic_map,
        father_last_name=father_last_name,
        father_ethnic=father_ethnic,
    ):
        return "kin"
    return chosen


def choose_birth_surname_convention(
    *,
    ethnic: str,
    father_last_name: str | None,
    father_ethnic: str | None,
    db_path: Path | str | None = None,
) -> str:
    """Pick one surname convention for a parent partnership.

    The returned convention is intended to be stored with the relationship and
    reused for every child of the same parents. ``lookup`` is normalized to
    ``kin`` when the father's current surname is itself a kin-form name, avoiding
    literal inheritance of names like ``Oakson`` as a fixed family surname.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    ethnic_map, _, _ = _name_tables_cached(str(path.resolve()))
    eth_key = _name_ethnic_for_tables(ethnic)
    er = ethnic_map.get(eth_key)
    if er is None:
        raise LookupError(f"No ethnic row for ethnic={ethnic!r}")
    return _normalize_birth_surname_mode(
        mode=None,
        ethnic_row=er,
        ethnic_map=ethnic_map,
        father_last_name=father_last_name,
        father_ethnic=father_ethnic,
    )


def _first_name_pool(
    conn: sqlite3.Connection,
    *,
    ethnic: str,
    gender: str,
    suppress_part_1: bool,
) -> list[tuple[str, int]]:
    rows = conn.execute(
        """
        SELECT name, rate, name_part
        FROM first_name
        WHERE ethnic = ? AND gender = ?
        """,
        (ethnic.strip(), gender.strip()),
    ).fetchall()
    pool: list[tuple[str, int]] = []
    for r in rows:
        if suppress_part_1 and _as_int(r["name_part"], 0) == 1:
            continue
        w = max(0, _as_int(r["rate"], 0))
        if w <= 0:
            continue
        name = str(r["name"] or "").strip()
        if name:
            pool.append((name, w))
    return pool


def _last_name_pool(conn: sqlite3.Connection, *, ethnic: str) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT name, rate FROM last_name WHERE ethnic = ?",
        (ethnic.strip(),),
    ).fetchall()
    pool: list[tuple[str, int]] = []
    for r in rows:
        w = max(0, _as_int(r["rate"], 0))
        if w <= 0:
            continue
        name = str(r["name"] or "").strip()
        if name:
            pool.append((name, w))
    return pool


def _pick_weighted_name(pool: list[tuple[str, int]]) -> str:
    if not pool:
        raise LookupError("empty name pool")
    total = sum(max(0, int(weight)) for _name, weight in pool)
    if total <= 0:
        raise LookupError("empty name pool")
    r = random.uniform(0, float(total))
    acc = 0.0
    for name, weight in pool:
        acc += max(0, int(weight))
        if r <= acc:
            return name
    return pool[-1][0]


def _tokenize_geography_example(cell: object) -> list[str]:
    """Split placename example columns into comparable lowercase stems (length >= 4)."""
    raw = str(cell or "").strip()
    if not raw:
        return []
    parts = re.split(r"[\s\-]+", raw)
    out: list[str] = []
    for p in parts:
        t = "".join(ch for ch in p if ch.isalnum()).casefold()
        if len(t) >= 4:
            out.append(t)
    whole = "".join(ch for ch in raw if ch.isalnum()).casefold()
    if len(whole) >= 4:
        out.append(whole)
    return out


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


@lru_cache(maxsize=8)
def _toponym_marker_tokens(db_path_s: str) -> frozenset[str]:
    """Lowercase tokens drawn from placename examples and region display names.

    Lookup-style surnames matching these tokens are only kept when they also
    match the person's birthplace / local settlement labels (see
    :func:`_filter_lookup_last_name_pool`).
    """
    tokens: set[str] = set()
    with closing(sqlite3.connect(db_path_s)) as conn:
        conn.row_factory = sqlite3.Row
        has_placenames = _sqlite_table_exists(conn, "placenames")
        has_regions = _sqlite_table_exists(conn, "world_geography_regions")
        has_last = _sqlite_table_exists(conn, "last_name")

        example_blob = ""
        if has_placenames:
            rows = conn.execute(
                """
                SELECT "Modern Example", "Intermediate Example", "Archaic Example"
                FROM placenames
                """
            ).fetchall()
            for row in rows:
                for key in row.keys():
                    for t in _tokenize_geography_example(row[key]):
                        tokens.add(t)
            example_blob = " ".join(
                str(row[k] or "")
                for row in conn.execute(
                    """
                    SELECT "Modern Example", "Intermediate Example", "Archaic Example"
                    FROM placenames
                    """
                )
                for k in row.keys()
            ).casefold()

        region_names: list[str] = []
        if has_regions:
            for row in conn.execute("SELECT region_name FROM world_geography_regions"):
                rn = str(row["region_name"] or "").strip()
                if not rn:
                    continue
                region_names.append(rn)
                tokens.add(rn.casefold())
                for w in re.findall(r"[\w']+", rn):
                    wl = w.casefold()
                    if len(wl) >= 4:
                        tokens.add(wl)

        if has_last and has_placenames and example_blob:
            for row in conn.execute("SELECT DISTINCT name FROM last_name"):
                ln = str(row["name"] or "").strip()
                lk = ln.casefold()
                if len(lk) < 4:
                    continue
                if lk in example_blob:
                    tokens.add(lk)

        if has_last and region_names:
            for row in conn.execute("SELECT DISTINCT name FROM last_name"):
                ln = str(row["name"] or "").strip()
                lk = ln.casefold()
                if len(lk) < 4:
                    continue
                for rn in region_names:
                    rnc = rn.casefold()
                    if lk in rnc or rnc in lk:
                        tokens.add(lk)
                        break
    return frozenset(tokens)


def _allowed_place_strings_for_person(
    *,
    birthplace: str,
    birthplace_region_id: str | None,
    simulation_context: Any,
    world: str,
    db_path: Path,
) -> list[str]:
    """Labels for birth / residence locality used to permit toponymic surname parts."""
    out: list[str] = []
    bp = (birthplace or "").strip()
    if bp:
        out.append(bp)
    rid = (birthplace_region_id or "").strip()
    if rid and simulation_context is not None:
        for sid in simulation_context.settlement_ids_by_region.get(rid, []):
            st = simulation_context.settlements_by_id.get(sid)
            if st is None or (st.status or "").strip().lower() != "active":
                continue
            for label in (st.display_name, st.region_display_name):
                s = (label or "").strip()
                if s:
                    out.append(s)
    if rid:
        try:
            reg = get_region(rid, world=world.strip(), db_path=db_path)
            rn = (reg.region_name or "").strip()
            if rn:
                out.append(rn)
        except LookupError:
            pass
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        k = s.casefold()
        if k and k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def _toponym_matches_allowed_place(token: str, allowed_place_strings: list[str]) -> bool:
    """Whether placename token ``token`` is anchored to an allowed locality string."""
    t = token.casefold().strip()
    if not t:
        return False
    for a in allowed_place_strings:
        if not a:
            continue
        al = a.casefold().strip()
        if not al:
            continue
        if t == al or t in al or al in t:
            return True
    return False


def _filter_lookup_last_name_pool(
    pool: list[tuple[str, int]],
    *,
    allowed_place_strings: list[str],
    marker_tokens: frozenset[str],
) -> list[tuple[str, int]]:
    """Drop weighted surname candidates that are configured toponyms outside ``allowed_place_strings``."""
    if not pool or not marker_tokens:
        return pool
    filtered: list[tuple[str, int]] = []
    for name, wt in pool:
        key = name.casefold().strip()
        if not key or key not in marker_tokens:
            filtered.append((name, wt))
            continue
        if _toponym_matches_allowed_place(key, allowed_place_strings):
            filtered.append((name, wt))
    return filtered if filtered else pool


def _compound_weighted(
    pool: list[tuple[str, int]],
    n: int,
    sep: str,
) -> str:
    if n <= 0:
        return ""
    parts = [_pick_weighted_name(pool) for _ in range(n)]
    return sep.join(parts)


def _kin_template(ethnic_row: sqlite3.Row, gender: str) -> str:
    key = "kin_m" if gender.strip() == "Male" else "kin_f"
    if key not in ethnic_row.keys():
        return ""
    return str(ethnic_row[key] or "").strip()


def _father_stem_first_name(
    conn: sqlite3.Connection,
    *,
    ethnic: str,
    ethnic_row: sqlite3.Row,
    num_first_spec: str | None,
    sep_first: str | None,
) -> str:
    """Patronymic / kin ``$`` stem: synthetic father uses Male given-name rules."""
    suppress = _suppress_name_part(ethnic_row)
    pool = _first_name_pool(conn, ethnic=ethnic, gender="Male", suppress_part_1=suppress)
    if not pool:
        raise LookupError(
            f"No first_name rows for patronym stem ethnic={ethnic!r} (Male)"
        )
    lo, hi = _parse_min_max(num_first_spec)
    n = roll_name_component_count(lo, hi)
    sep = str(sep_first or "").strip() if sep_first is not None else ""
    return _compound_weighted(pool, n, sep)


def _matches_kin_template(last_name: str, template: str) -> bool:
    last = str(last_name or "").strip()
    tpl = str(template or "").strip()
    if not last or not tpl:
        return False
    if "$" not in tpl:
        return last == tpl
    pattern = "^" + re.escape(tpl).replace("\\$", ".+") + "$"
    return re.fullmatch(pattern, last) is not None


def _is_kin_last_name(
    conn: sqlite3.Connection,
    *,
    ethnic: str | None,
    last_name: str | None,
) -> bool:
    if not ethnic or not last_name:
        return False
    try:
        er = _ethnic_row(conn, ethnic)
    except LookupError:
        return False
    for key in ("kin_m", "kin_f"):
        if key not in er.keys():
            continue
        tpl = str(er[key] or "").strip()
        if _matches_kin_template(str(last_name), tpl):
            return True
    return False


def sample_first_name_for_ethnic(
    *,
    ethnic: str,
    rng: random.Random,
    db_path: Path | str | None = None,
) -> str | None:
    """Weighted random first name for ``ethnic`` across both genders.

    Used by placename generation to seed a culture-shaped first-name stem when
    no prominent resident exists yet. Honors ``suppress_constructed`` /
    ``suppress_name_part`` (skip ``name_part == 1`` rows). Returns ``None`` if
    the ethnic has no usable first-name rows.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    ethnic_map, first_map, _ = _name_tables_cached(str(path.resolve()))
    eth_key = _name_ethnic_for_tables(ethnic)
    er = ethnic_map.get(eth_key)
    suppress = False
    if er is not None:
        for key in ("suppress_name_part", "suppress_constructed"):
            val = er.get(key)
            if val is None:
                continue
            s = str(val).strip()
            if s and _as_int(val, 0) == 1:
                suppress = True
                break

    pool: list[tuple[str, int]] = []
    for (eth, _gender), names in first_map.items():
        if eth != eth_key:
            continue
        for name, rate, part in names:
            if suppress and part == 1:
                continue
            if rate <= 0 or not name:
                continue
            pool.append((name, rate))
    if not pool:
        return None
    names_only, weights = zip(*pool)
    return rng.choices(list(names_only), weights=list(weights), k=1)[0]


def choose_random_first_last(
    *,
    ethnic: str,
    gender: str,
    birthplace: str,
    db_path: Path | str | None = None,
    birthplace_region_id: str | None = None,
    world: str = "default",
    simulation_context: Any | None = None,
) -> tuple[str, str]:
    """Weighted given name(s) and surname per ``ethnic`` row (see project naming rules).

    Lookup-mode surname components that match placename / region toponym tokens from
    config are only chosen when they align with ``birthplace`` or the settlement /
    region labels for ``birthplace_region_id``.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    ethnic_map, first_map, last_map = _name_tables_cached(str(path.resolve()))
    eth_key = _name_ethnic_for_tables(ethnic)
    er = ethnic_map.get(eth_key)
    if er is None:
        raise LookupError(f"No ethnic row for ethnic={ethnic!r}")
    suppress = _suppress_name_part(er)

    base_pool = first_map.get((eth_key, gender.strip()), [])
    pool_given = [(n, w) for n, w, part in base_pool if (not suppress or part != 1)]
    if not pool_given:
        raise LookupError(f"No first_name rows for ethnic={ethnic!r} gender={gender!r}")

    lo_f, hi_f = _parse_min_max(er["num_first_names"] if "num_first_names" in er.keys() else None)
    lo_l, hi_l = _parse_min_max(er["num_last_names"] if "num_last_names" in er.keys() else None)
    sep_f = str(er["sep_first_names"] or "").strip() if "sep_first_names" in er.keys() else ""
    sep_l = str(er["sep_last_names"] or "").strip() if "sep_last_names" in er.keys() else ""

    n_first = roll_name_component_count(lo_f, hi_f)
    first = _compound_weighted(pool_given, n_first, sep_f)

    mode = _choose_weighted_mode(er)
    bp = (birthplace or "").strip()
    if mode == "none":
        last = ""
    elif mode == "hails":
        hf = str(er["hails_from"] or "").strip() if "hails_from" in er.keys() else ""
        parts = [p for p in (hf, bp) if p]
        last = " ".join(parts)
    elif mode == "kin":
        tpl = _kin_template(er, gender)
        if not tpl:
            raise LookupError(f"Missing kin template for ethnic={ethnic!r} gender={gender!r}")
        male_pool = first_map.get((eth_key, "Male"), [])
        male_given = [(n, w) for n, w, part in male_pool if (not suppress or part != 1)]
        if not male_given:
            raise LookupError(f"No first_name rows for patronym stem ethnic={ethnic!r} (Male)")
        stem = _compound_weighted(male_given, roll_name_component_count(lo_f, hi_f), sep_f)
        last = tpl.replace("$", stem)
    else:
        pool_ln = last_map.get(eth_key, [])
        markers = _toponym_marker_tokens(str(path.resolve()))
        allowed = _allowed_place_strings_for_person(
            birthplace=bp,
            birthplace_region_id=birthplace_region_id,
            simulation_context=simulation_context,
            world=world,
            db_path=path,
        )
        pool_ln = _filter_lookup_last_name_pool(
            pool_ln, allowed_place_strings=allowed, marker_tokens=markers
        )
        if not pool_ln:
            last = ""
        else:
            n_last = roll_name_component_count(lo_l, hi_l)
            last = _compound_weighted(pool_ln, n_last, sep_l)

    return first, last


def choose_random_first_last_from_birth(
    *,
    ethnic: str,
    gender: str,
    birthplace: str,
    father_last_name: str | None,
    father_ethnic: str | None,
    father_first_name: str | None,
    surname_convention: str | None = None,
    db_path: Path | str | None = None,
    birthplace_region_id: str | None = None,
    world: str = "default",
    simulation_context: Any | None = None,
) -> tuple[str, str]:
    """Birth-name variant with paternal surname inheritance on lookup rolls.

    Rule:
    - If child rolls ``sur_lookup`` and father has a non-empty last name, use it.
    - If that paternal last name is itself a kin-form name, do not inherit it
      literally; switch to ``kin`` naming for the child instead.

    Inherited lookup surnames may include placename-like segments; those segments are
    kept only when they match the child's birthplace / local settlement labels (same
    filter as :func:`choose_random_first_last`).
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    ethnic_map, first_map, last_map = _name_tables_cached(str(path.resolve()))
    eth_key = _name_ethnic_for_tables(ethnic)
    er = ethnic_map.get(eth_key)
    if er is None:
        raise LookupError(f"No ethnic row for ethnic={ethnic!r}")
    suppress = _suppress_name_part(er)

    base_pool = first_map.get((eth_key, gender.strip()), [])
    pool_given = [(n, w) for n, w, part in base_pool if (not suppress or part != 1)]
    if not pool_given:
        raise LookupError(f"No first_name rows for ethnic={ethnic!r} gender={gender!r}")

    lo_f, hi_f = _parse_min_max(er["num_first_names"] if "num_first_names" in er.keys() else None)
    lo_l, hi_l = _parse_min_max(er["num_last_names"] if "num_last_names" in er.keys() else None)
    sep_f = str(er["sep_first_names"] or "").strip() if "sep_first_names" in er.keys() else ""
    sep_l = str(er["sep_last_names"] or "").strip() if "sep_last_names" in er.keys() else ""

    n_first = roll_name_component_count(lo_f, hi_f)
    first = _compound_weighted(pool_given, n_first, sep_f)

    mode = _normalize_birth_surname_mode(
        mode=surname_convention,
        ethnic_row=er,
        ethnic_map=ethnic_map,
        father_last_name=father_last_name,
        father_ethnic=father_ethnic,
    )
    bp = (birthplace or "").strip()
    father_last = str(father_last_name or "").strip()
    if mode == "lookup" and father_last:
        markers = _toponym_marker_tokens(str(path.resolve()))
        parts = re.split(r"[\s\-]+", father_last.strip()) if father_last else []
        if not markers or not any(part.casefold().strip() in markers for part in parts):
            return first, father_last
        allowed = _allowed_place_strings_for_person(
            birthplace=bp,
            birthplace_region_id=birthplace_region_id,
            simulation_context=simulation_context,
            world=world,
            db_path=path,
        )
        dropped = False
        rebuilt: list[str] = []
        for part in parts:
            pk = part.casefold().strip()
            if pk and pk in markers and not _toponym_matches_allowed_place(pk, allowed):
                dropped = True
                continue
            if part.strip():
                rebuilt.append(part.strip())
        if dropped and rebuilt:
            return first, " ".join(rebuilt)
        if dropped and not rebuilt:
            mode = "lookup"
        else:
            return first, father_last

    if mode == "none":
        last = ""
    elif mode == "hails":
        hf = str(er["hails_from"] or "").strip() if "hails_from" in er.keys() else ""
        parts = [p for p in (hf, bp) if p]
        last = " ".join(parts)
    elif mode == "kin":
        tpl = _kin_template(er, gender)
        if not tpl:
            raise LookupError(f"Missing kin template for ethnic={ethnic!r} gender={gender!r}")
        stem = str(father_first_name or "").strip()
        if not stem:
            male_pool = first_map.get((eth_key, "Male"), [])
            male_given = [(n, w) for n, w, part in male_pool if (not suppress or part != 1)]
            if not male_given:
                raise LookupError(f"No first_name rows for patronym stem ethnic={ethnic!r} (Male)")
            stem = _compound_weighted(male_given, roll_name_component_count(lo_f, hi_f), sep_f)
        last = tpl.replace("$", stem)
    else:
        pool_ln = last_map.get(eth_key, [])
        markers = _toponym_marker_tokens(str(path.resolve()))
        allowed = _allowed_place_strings_for_person(
            birthplace=bp,
            birthplace_region_id=birthplace_region_id,
            simulation_context=simulation_context,
            world=world,
            db_path=path,
        )
        pool_ln = _filter_lookup_last_name_pool(
            pool_ln, allowed_place_strings=allowed, marker_tokens=markers
        )
        if not pool_ln:
            last = ""
        else:
            n_last = roll_name_component_count(lo_l, hi_l)
            last = _compound_weighted(pool_ln, n_last, sep_l)
    return first, last
