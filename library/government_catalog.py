"""Load government config tables from ``config.sqlite``."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GovernmentEraRow:
    world: str
    era_id: str
    history_year_from: int
    history_year_to: int
    allowed_polity_type_ids: tuple[str, ...]
    default_succession_style: str
    notes: str


@dataclass(frozen=True)
class PolityTypeRow:
    polity_type_id: str
    era_id: str
    parent_polity_type_id: str | None
    jurisdiction_grain: str
    head_title_id: str
    min_population_to_form: int
    max_population_before_split: int
    default_male_weight: float
    can_be_vassalized: bool
    can_have_vassals: bool


@dataclass(frozen=True)
class TitleRow:
    title_id: str
    title_names_by_era: dict[str, str]
    polity_type_id: str
    role: str
    selection_rule: str
    max_holders: int
    term_years: int | None
    min_age: int
    min_leadership_index: float
    min_military_quality_index: float
    min_career_fitness: float
    male_weight: float
    can_be_usurped: bool
    usurp_base_chance: float
    eligibility_kinship: str
    # Population-scaled holder sizing (for per-settlement merit titles like
    # ``settlement_leader`` / ``settlement_alderman``). Values are real-world counts
    # interpreted under ``world_start.population_scale``; ``0``/blank disables scaling.
    min_population_for_first_holder: int = 0
    pop_per_holder: int = 0
    # Probability per death-succession that an "ambitious leader" takes over via merit
    # selection instead of the hereditary chain. Only consulted for primogeniture-style
    # titles in :func:`library.simulation_government._succession_tick`. Default ``0.0``
    # preserves strict hereditary succession.
    merit_takeover_chance: float = 0.0


@dataclass(frozen=True)
class StartingPolitySpec:
    world: str
    region_id: str
    polity_type_id: str
    founding_year_offset: int
    founder_selection: str


@dataclass(frozen=True)
class GovernmentCatalog:
    eras: tuple[GovernmentEraRow, ...]
    polity_types: tuple[PolityTypeRow, ...]
    titles: tuple[TitleRow, ...]
    starting_polities: tuple[StartingPolitySpec, ...]

    def polity_type_by_id(self, pid: str) -> PolityTypeRow | None:
        s = (pid or "").strip()
        for p in self.polity_types:
            if p.polity_type_id == s:
                return p
        return None

    def titles_for_polity_type(self, polity_type_id: str) -> tuple[TitleRow, ...]:
        """Titles bound to the given polity type plus titles applied to all (``*``)."""
        pt = (polity_type_id or "").strip()
        return tuple(
            t for t in self.titles if t.polity_type_id == pt or t.polity_type_id == "*"
        )

    def universal_titles(self) -> tuple[TitleRow, ...]:
        """Titles whose ``polity_type_id`` is ``*`` (apply to every polity)."""
        return tuple(t for t in self.titles if t.polity_type_id == "*")

    def title_by_id(self, title_id: str) -> TitleRow | None:
        s = (title_id or "").strip()
        for t in self.titles:
            if t.title_id == s:
                return t
        return None


def _parse_int(val: object, default: int = 0) -> int:
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return default


def _parse_float(val: object, default: float = 0.0) -> float:
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _parse_bool(val: object) -> bool:
    s = str(val or "").strip().lower()
    return s in ("1", "true", "yes", "y")


def _split_allowed(raw: object) -> tuple[str, ...]:
    s = str(raw or "").strip()
    if not s:
        return ()
    parts = [p.strip() for p in s.replace(";", ",").split(",")]
    return tuple(p for p in parts if p)


def _parse_title_names_json(raw: object) -> dict[str, str]:
    s = str(raw or "").strip()
    if not s:
        return {}
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        return {}
    if not isinstance(d, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in d.items() if str(v).strip()}


def load_government_catalog(db_path: Path | str) -> GovernmentCatalog:
    path = Path(db_path).resolve()
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        era_rows = conn.execute(
            "SELECT * FROM government_eras ORDER BY history_year_from"
        ).fetchall()
        pt_rows = conn.execute(
            "SELECT * FROM government_polity_types ORDER BY polity_type_id"
        ).fetchall()
        title_rows = conn.execute(
            "SELECT * FROM government_titles ORDER BY title_id"
        ).fetchall()
        try:
            sp_rows = conn.execute(
                "SELECT * FROM government_starting_polities WHERE region_id IS NOT NULL AND trim(region_id) != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            sp_rows = []

    eras: list[GovernmentEraRow] = []
    for r in era_rows:
        rd = dict(r)
        eras.append(
            GovernmentEraRow(
                world=str(rd.get("world") or "default").strip() or "default",
                era_id=str(rd.get("era_id") or "").strip(),
                history_year_from=_parse_int(rd.get("history_year_from"), -10**9),
                history_year_to=_parse_int(rd.get("history_year_to"), 10**9),
                allowed_polity_type_ids=_split_allowed(rd.get("allowed_polity_type_ids")),
                default_succession_style=str(
                    rd.get("default_succession_style") or ""
                ).strip(),
                notes=str(rd.get("notes") or "").strip(),
            )
        )

    polity_types: list[PolityTypeRow] = []
    for r in pt_rows:
        rd = dict(r)
        parent = str(rd.get("parent_polity_type_id") or "").strip() or None
        polity_types.append(
            PolityTypeRow(
                polity_type_id=str(rd.get("polity_type_id") or "").strip(),
                era_id=str(rd.get("era_id") or "").strip(),
                parent_polity_type_id=parent,
                jurisdiction_grain=str(rd.get("jurisdiction_grain") or "region").strip(),
                head_title_id=str(rd.get("head_title_id") or "").strip(),
                min_population_to_form=max(1, _parse_int(rd.get("min_population_to_form"), 1)),
                max_population_before_split=max(
                    1, _parse_int(rd.get("max_population_before_split"), 999999)
                ),
                default_male_weight=_parse_float(rd.get("default_male_weight"), 0.5),
                can_be_vassalized=_parse_bool(rd.get("can_be_vassalized")),
                can_have_vassals=_parse_bool(rd.get("can_have_vassals")),
            )
        )

    titles: list[TitleRow] = []
    for r in title_rows:
        rd = dict(r)
        term_raw = rd.get("term_years")
        term_years: int | None
        if term_raw is None or str(term_raw).strip() == "":
            term_years = None
        else:
            term_years = max(1, _parse_int(term_raw, 1))
        titles.append(
            TitleRow(
                title_id=str(rd.get("title_id") or "").strip(),
                title_names_by_era=_parse_title_names_json(rd.get("title_name_by_era_json")),
                polity_type_id=str(rd.get("polity_type_id") or "").strip(),
                role=str(rd.get("role") or "").strip().lower(),
                selection_rule=str(rd.get("selection_rule") or "").strip().lower(),
                max_holders=max(1, _parse_int(rd.get("max_holders"), 1)),
                term_years=term_years,
                min_age=max(0, _parse_int(rd.get("min_age"), 16)),
                min_leadership_index=_parse_float(rd.get("min_leadership_index"), 0.0),
                min_military_quality_index=_parse_float(
                    rd.get("min_military_quality_index"), 0.0
                ),
                min_career_fitness=_parse_float(rd.get("min_career_fitness"), 0.0),
                male_weight=_clamp01(_parse_float(rd.get("male_weight"), 0.5)),
                can_be_usurped=_parse_bool(rd.get("can_be_usurped")),
                usurp_base_chance=_parse_float(rd.get("usurp_base_chance"), 0.0),
                eligibility_kinship=str(rd.get("eligibility_kinship") or "realm_resident")
                .strip()
                .lower(),
                min_population_for_first_holder=max(
                    0, _parse_int(rd.get("min_population_for_first_holder"), 0)
                ),
                pop_per_holder=max(0, _parse_int(rd.get("pop_per_holder"), 0)),
                merit_takeover_chance=_clamp01(
                    _parse_float(rd.get("merit_takeover_chance"), 0.0)
                ),
            )
        )

    starting: list[StartingPolitySpec] = []
    for r in sp_rows:
        rd = dict(r)
        starting.append(
            StartingPolitySpec(
                world=str(rd.get("world") or "default").strip(),
                region_id=str(rd.get("region_id") or "").strip(),
                polity_type_id=str(rd.get("polity_type_id") or "").strip(),
                founding_year_offset=_parse_int(rd.get("founding_year_offset"), 0),
                founder_selection=str(rd.get("founder_selection") or "first_resident_couple")
                .strip()
                .lower(),
            )
        )

    return GovernmentCatalog(
        eras=tuple(eras),
        polity_types=tuple(polity_types),
        titles=tuple(titles),
        starting_polities=tuple(starting),
    )


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def resolve_government_era(
    catalog: GovernmentCatalog, *, world: str, historical_year: int
) -> GovernmentEraRow | None:
    w = (world or "default").strip() or "default"
    hy = int(historical_year)
    best: GovernmentEraRow | None = None
    for e in catalog.eras:
        ew = (e.world or "default").strip() or "default"
        if ew != w and ew != "*":
            continue
        if e.history_year_from <= hy <= e.history_year_to:
            if best is None or e.history_year_from > best.history_year_from:
                best = e
    return best


def pick_primary_polity_type(
    catalog: GovernmentCatalog, era: GovernmentEraRow
) -> PolityTypeRow | None:
    allowed = era.allowed_polity_type_ids
    if not allowed:
        return None
    by_id = {p.polity_type_id: p for p in catalog.polity_types}
    for token in allowed:
        p = by_id.get(token.strip())
        if p is not None and not (p.parent_polity_type_id or "").strip():
            return p
    for token in allowed:
        p = by_id.get(token.strip())
        if p is not None:
            return p
    return None


def effective_min_population_to_form(ptype: PolityTypeRow, scale: float) -> int:
    """Real-world ``min_population_to_form`` translated into simulation alive count.

    Uses :func:`library.geography.scale_population_threshold` semantics: scales by the
    world's ``population_scale`` and floors at 1 so a non-zero CSV value never collapses
    to zero alive.
    """
    raw = max(0, int(ptype.min_population_to_form or 0))
    if raw <= 0:
        return 0
    return max(1, int(round(raw * float(scale))))


def effective_max_population_before_split(ptype: PolityTypeRow, scale: float) -> int:
    """Real-world ``max_population_before_split`` translated into simulation alive count."""
    raw = max(0, int(ptype.max_population_before_split or 0))
    if raw <= 0:
        return 0
    return max(1, int(round(raw * float(scale))))


def pick_polity_type_for_region_population(
    catalog: GovernmentCatalog,
    era: GovernmentEraRow,
    *,
    alive_in_region: int,
    population_scale: float = 1.0,
) -> PolityTypeRow | None:
    """Choose polity tier among era-allowed types using each type's ``min_population_to_form``.

    Picks the **highest** allowed tier whose minimum alive count is satisfied; otherwise
    the **lowest** minimum among allowed types (so tiny regions become counties, not
    kingdoms, when the feudal ladder lists ``kingdom;duchy;county``).

    ``population_scale`` (default ``1.0``) is the same global scale used for
    ``carrying_capacity`` in :mod:`library.geography`; CSV thresholds are interpreted as
    real-world counts, multiplied by this scale before comparison against
    ``alive_in_region``.
    """
    by_id = {p.polity_type_id: p for p in catalog.polity_types}
    candidates: list[PolityTypeRow] = []
    for raw in era.allowed_polity_type_ids:
        token = (raw or "").strip()
        if not token:
            continue
        p = by_id.get(token)
        if p is not None:
            candidates.append(p)
    if not candidates:
        return pick_primary_polity_type(catalog, era)
    n = max(0, int(alive_in_region))
    tiers = sorted(
        candidates,
        key=lambda p: int(p.min_population_to_form or 1),
        reverse=True,
    )
    for p in tiers:
        if n >= effective_min_population_to_form(p, population_scale):
            return p
    return min(candidates, key=lambda p: int(p.min_population_to_form or 1))


def display_title_name(title: TitleRow, job_era_key: str) -> str:
    """Pick display string from JSON map using era key or wildcard."""
    m = title.title_names_by_era
    if job_era_key in m:
        return m[job_era_key]
    if "*" in m:
        return m["*"]
    if m:
        return next(iter(m.values()))
    return title.title_id.replace("_", " ").title()


def load_genome_composite_rows(db_path: Path | str) -> tuple[dict[str, object], ...]:
    path = Path(db_path).resolve()
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM genome_composites").fetchall()
    return tuple(dict(r) for r in rows)
