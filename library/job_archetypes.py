"""Semantic job archetypes layered over concrete job titles.

The genome job catalog chooses a literal title.  This module adds social and
market semantics that are too broad for individual title strings: care work,
domestic service, vice/criminal pools, prestige, class, and perceived worth.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from library.job_economics import normalize_job_catalog_key

JOB_MARKET_TYPES: frozenset[str] = frozenset(
    {
        "settlement_market",
        "household_care",
        "domestic_service",
        "criminal",
        "vice",
        "office",
        "none",
    }
)

HOUSING_STATUSES: frozenset[str] = frozenset(
    {
        "family_home",
        "own_household",
        "employer_household",
        "kin_board",
        "charity_board",
        "street",
    }
)


@dataclass(frozen=True)
class JobArchetypeParams:
    job_market_type: str
    role_family: str
    workplace: str
    skill_level: str
    manuality: str
    supervision_level: str
    class_band: str
    personal_prosperity_01: float
    societal_impact_01: float
    public_prestige_01: float
    perceived_worth_01: float
    care_intensity_01: float
    home_compatible: bool
    domestic_service_kind: str | None
    female_mindset_affinity_01: float
    adult_only: bool
    board_compensation_01: float
    cash_wage_multiplier: float


DEFAULT_JOB_ARCHETYPE = JobArchetypeParams(
    job_market_type="settlement_market",
    role_family="labor",
    workplace="settlement",
    skill_level="ordinary",
    manuality="mixed",
    supervision_level="peer",
    class_band="commoner",
    personal_prosperity_01=0.32,
    societal_impact_01=0.42,
    public_prestige_01=0.32,
    perceived_worth_01=0.38,
    care_intensity_01=0.0,
    home_compatible=False,
    domestic_service_kind=None,
    female_mindset_affinity_01=0.5,
    adult_only=False,
    board_compensation_01=0.0,
    cash_wage_multiplier=1.0,
)


@dataclass(frozen=True)
class _ArchetypeRow:
    pattern: str
    match_type: str
    params: JobArchetypeParams


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _float_cell(row: dict[str, object], key: str, default: float) -> float:
    raw = row.get(key)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _bool_cell(row: dict[str, object], key: str, default: bool = False) -> bool:
    raw = row.get(key)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _str_cell(row: dict[str, object], key: str, default: str) -> str:
    raw = row.get(key)
    s = "" if raw is None else str(raw).strip()
    return s or default


def _params_from_row(row: dict[str, object], base: JobArchetypeParams) -> JobArchetypeParams:
    market_type = _str_cell(row, "job_market_type", base.job_market_type).lower()
    if market_type not in JOB_MARKET_TYPES:
        market_type = base.job_market_type
    service_kind = _str_cell(row, "domestic_service_kind", base.domestic_service_kind or "")
    return JobArchetypeParams(
        job_market_type=market_type,
        role_family=_str_cell(row, "role_family", base.role_family).lower(),
        workplace=_str_cell(row, "workplace", base.workplace).lower(),
        skill_level=_str_cell(row, "skill_level", base.skill_level).lower(),
        manuality=_str_cell(row, "manuality", base.manuality).lower(),
        supervision_level=_str_cell(row, "supervision_level", base.supervision_level).lower(),
        class_band=_str_cell(row, "class_band", base.class_band).lower(),
        personal_prosperity_01=_clamp(
            _float_cell(row, "personal_prosperity_01", base.personal_prosperity_01),
            0.0,
            1.0,
        ),
        societal_impact_01=_clamp(
            _float_cell(row, "societal_impact_01", base.societal_impact_01),
            0.0,
            1.0,
        ),
        public_prestige_01=_clamp(
            _float_cell(row, "public_prestige_01", base.public_prestige_01),
            0.0,
            1.0,
        ),
        perceived_worth_01=_clamp(
            _float_cell(row, "perceived_worth_01", base.perceived_worth_01),
            0.0,
            1.0,
        ),
        care_intensity_01=_clamp(
            _float_cell(row, "care_intensity_01", base.care_intensity_01),
            0.0,
            1.0,
        ),
        home_compatible=_bool_cell(row, "home_compatible", base.home_compatible),
        domestic_service_kind=service_kind or None,
        female_mindset_affinity_01=_clamp(
            _float_cell(
                row,
                "female_mindset_affinity_01",
                base.female_mindset_affinity_01,
            ),
            0.0,
            1.0,
        ),
        adult_only=_bool_cell(row, "adult_only", base.adult_only),
        board_compensation_01=_clamp(
            _float_cell(row, "board_compensation_01", base.board_compensation_01),
            0.0,
            1.0,
        ),
        cash_wage_multiplier=max(
            0.0,
            _float_cell(row, "cash_wage_multiplier", base.cash_wage_multiplier),
        ),
    )


def infer_job_archetype_params(job_title: str | None) -> JobArchetypeParams:
    jk = normalize_job_catalog_key(job_title or "")
    if not jk:
        return DEFAULT_JOB_ARCHETYPE

    def has(*parts: str) -> bool:
        return any(p in jk for p in parts)

    if has("child rearer", "parent"):
        return JobArchetypeParams(
            "household_care",
            "care",
            "home",
            "skilled",
            "mixed",
            "self_directed",
            "household",
            0.04,
            0.92,
            0.24,
            0.34,
            1.0,
            True,
            None,
            0.82,
            False,
            0.0,
            0.0,
        )
    if has("nanny", "child watcher"):
        return JobArchetypeParams(
            "domestic_service",
            "care",
            "employer_household",
            "skilled",
            "mixed",
            "supervised",
            "servant",
            0.16,
            0.82,
            0.28,
            0.42,
            0.90,
            True,
            "nanny",
            0.88,
            True,
            0.72,
            0.35,
        )
    if has("maid", "servant", "valet", "household manager", "aide", "retainer"):
        kind = "household_manager" if "manager" in jk else "servant"
        return JobArchetypeParams(
            "domestic_service",
            "domestic",
            "employer_household",
            "ordinary",
            "manual",
            "supervised",
            "servant",
            0.14,
            0.52,
            0.24,
            0.35,
            0.32,
            True,
            kind,
            0.68,
            True,
            0.68,
            0.28,
        )
    if has("prostitute", "courtesan", "brothel worker"):
        return JobArchetypeParams(
            "vice",
            "vice",
            "street_or_house",
            "ordinary",
            "social",
            "informal",
            "marginal",
            0.18,
            0.14,
            0.10,
            0.16,
            0.0,
            False,
            None,
            0.72,
            True,
            0.05,
            0.55,
        )
    if has("thief", "fraud", "extortion", "raider", "criminal", "bandit"):
        return JobArchetypeParams(
            "criminal",
            "criminal",
            "informal",
            "ordinary",
            "mixed",
            "informal",
            "marginal",
            0.20,
            0.04,
            0.04,
            0.06,
            0.0,
            False,
            None,
            0.45,
            True,
            0.0,
            0.50,
        )
    if has("judge", "magistrate", "officer", "mayor", "ruler", "guild master"):
        return JobArchetypeParams(
            "office",
            "authority",
            "office",
            "elite",
            "cognitive",
            "supervisory",
            "upper",
            0.72,
            0.70,
            0.78,
            0.76,
            0.0,
            False,
            None,
            0.42,
            True,
            0.0,
            1.5,
        )
    return DEFAULT_JOB_ARCHETYPE


@dataclass(frozen=True)
class JobArchetypeCatalog:
    exact_rows: dict[str, JobArchetypeParams]
    pattern_rows: tuple[_ArchetypeRow, ...]
    fallback: JobArchetypeParams

    @classmethod
    def load(cls, db_path: Path | str) -> "JobArchetypeCatalog":
        return _load_catalog(str(Path(db_path).resolve()))

    def lookup(self, job_title: str | None) -> JobArchetypeParams:
        jk = normalize_job_catalog_key(job_title or "")
        if not jk:
            return self.fallback
        if jk in self.exact_rows:
            return self.exact_rows[jk]
        for row in self.pattern_rows:
            if row.match_type == "contains" and row.pattern in jk:
                return row.params
            if row.match_type == "prefix" and jk.startswith(row.pattern):
                return row.params
            if row.match_type == "suffix" and jk.endswith(row.pattern):
                return row.params
        inferred = infer_job_archetype_params(jk)
        if inferred != DEFAULT_JOB_ARCHETYPE:
            return inferred
        return self.fallback


@lru_cache(maxsize=8)
def _load_catalog(db_path_s: str) -> JobArchetypeCatalog:
    path = Path(db_path_s)
    exact_rows: dict[str, JobArchetypeParams] = {}
    pattern_rows: list[_ArchetypeRow] = []
    fallback = DEFAULT_JOB_ARCHETYPE
    if not path.is_file():
        return JobArchetypeCatalog(exact_rows, tuple(pattern_rows), fallback)
    with closing(sqlite3.connect(str(path))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            raw_rows = conn.execute("SELECT rowid, * FROM job_archetypes ORDER BY rowid").fetchall()
        except sqlite3.OperationalError:
            return JobArchetypeCatalog(exact_rows, tuple(pattern_rows), fallback)

    for raw in raw_rows:
        row = {k: raw[k] for k in raw.keys()}
        pattern = normalize_job_catalog_key(str(row.get("job_key_pattern") or ""))
        if not pattern:
            continue
        match_type = _str_cell(row, "match_type", "exact").lower()
        base = fallback if pattern == "*" else infer_job_archetype_params(pattern)
        params = _params_from_row(row, base)
        if pattern == "*" or match_type == "default":
            fallback = params
        elif match_type == "exact":
            exact_rows[pattern] = params
        elif match_type in {"contains", "prefix", "suffix"}:
            pattern_rows.append(_ArchetypeRow(pattern, match_type, params))
    return JobArchetypeCatalog(exact_rows, tuple(pattern_rows), fallback)


def clear_job_archetype_cache() -> None:
    _load_catalog.cache_clear()
