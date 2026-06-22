"""Semantic job archetypes layered over concrete job titles.

The genome job catalog chooses a literal title.  This module adds social and
market semantics that are too broad for individual title strings: care work,
domestic service, vice/criminal pools, prestige, class, and perceived worth.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
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
    physical_demand_01: float = 0.30
    force_authority_01: float = 0.0
    leveling_affinity_01: float = 0.50
    informal_role_01: float = 0.0


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
    physical_demand_01=0.30,
    force_authority_01=0.0,
    leveling_affinity_01=0.50,
    informal_role_01=0.0,
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
        physical_demand_01=_clamp(
            _float_cell(row, "physical_demand_01", base.physical_demand_01),
            0.0,
            1.0,
        ),
        force_authority_01=_clamp(
            _float_cell(row, "force_authority_01", base.force_authority_01),
            0.0,
            1.0,
        ),
        leveling_affinity_01=_clamp(
            _float_cell(row, "leveling_affinity_01", base.leveling_affinity_01),
            0.0,
            1.0,
        ),
        informal_role_01=_clamp(
            _float_cell(row, "informal_role_01", base.informal_role_01),
            0.0,
            1.0,
        ),
    )


def infer_job_archetype_params(job_title: str | None) -> JobArchetypeParams:
    jk = normalize_job_catalog_key(job_title or "")
    if not jk:
        return DEFAULT_JOB_ARCHETYPE

    def has(*parts: str) -> bool:
        return any(p in jk for p in parts)

    if has("child rearer", "parent"):
        return replace(
            JobArchetypeParams(
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
            ),
            physical_demand_01=0.18,
            leveling_affinity_01=0.35,
        )
    if has("nanny", "child watcher"):
        return replace(
            JobArchetypeParams(
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
            ),
            physical_demand_01=0.22,
            leveling_affinity_01=0.35,
        )
    if has("maid", "servant", "valet", "household manager", "aide", "retainer"):
        kind = "household_manager" if "manager" in jk else "servant"
        return replace(
            JobArchetypeParams(
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
            ),
            physical_demand_01=0.25 if kind == "household_manager" else 0.40,
            force_authority_01=0.05 if kind == "household_manager" else 0.0,
            leveling_affinity_01=0.45,
        )
    if has("prostitute", "courtesan", "brothel worker"):
        return replace(
            JobArchetypeParams(
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
            ),
            physical_demand_01=0.16,
            leveling_affinity_01=0.30,
            informal_role_01=1.0,
        )
    if has("charlatan", "huckster", "con artist", "scammer", "fraud"):
        return replace(
            JobArchetypeParams(
            "criminal",
            "criminal",
            "informal",
            "skilled",
            "social",
            "informal",
            "marginal",
            0.20,
            0.04,
            0.05,
            0.08,
            0.0,
            False,
            None,
            0.42,
            True,
            0.0,
            0.60,
            ),
            physical_demand_01=0.10,
            leveling_affinity_01=0.65,
            informal_role_01=1.0,
        )
    if has("thief", "extortion", "raider", "criminal", "bandit", "smuggler", "poacher"):
        return replace(
            JobArchetypeParams(
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
            ),
            physical_demand_01=0.80 if has("raider", "bandit") else 0.35,
            force_authority_01=0.68 if has("raider", "bandit") else 0.10,
            leveling_affinity_01=0.30,
            informal_role_01=1.0,
        )
    if has(
        "soldier",
        "guard",
        "warrior",
        "infantry",
        "legionary",
        "longbowman",
        "gladiator",
        "duelist",
        "mercenary",
        "brawler",
        "enforcer",
        "constable",
        "sheriff",
        "knight",
    ):
        return replace(
            DEFAULT_JOB_ARCHETYPE,
            role_family="security",
            skill_level="ordinary",
            manuality="manual",
            societal_impact_01=0.58,
            public_prestige_01=0.42,
            perceived_worth_01=0.46,
            physical_demand_01=0.80 if not has("guard", "constable", "sheriff") else 0.74,
            force_authority_01=0.72,
            leveling_affinity_01=0.35,
        )
    if has(
        "farmer",
        "hunter",
        "fisher",
        "laborer",
        "labourer",
        "porter",
        "rower",
        "mason",
        "carpenter",
        "smith",
        "blacksmith",
        "digger",
        "builder",
        "road worker",
        "sailor",
        "teamster",
        "courier",
        "runner",
        "messenger",
    ):
        demand = 0.70
        if has("hunter", "rower", "porter", "digger", "laborer", "labourer"):
            demand = 0.82
        elif has("courier", "runner", "messenger", "sailor", "teamster"):
            demand = 0.62
        return replace(
            DEFAULT_JOB_ARCHETYPE,
            role_family="labor",
            manuality="manual",
            societal_impact_01=0.52,
            perceived_worth_01=0.42,
            physical_demand_01=demand,
            force_authority_01=0.20 if has("hunter") else 0.05,
            leveling_affinity_01=0.45,
        )
    if has("banker", "shipowner", "landholder"):
        role = "finance" if "banker" in jk else ("trade" if "shipowner" in jk else "estate")
        return replace(
            JobArchetypeParams(
            "settlement_market",
            role,
            "market" if role != "estate" else "estate",
            "elite",
            "cognitive" if role != "estate" else "mixed",
            "supervisory",
            "elite",
            0.82,
            0.56,
            0.72,
            0.68,
            0.0,
            False,
            None,
            0.44,
            True,
            0.0,
            1.7,
            ),
            physical_demand_01=0.05 if role == "finance" else (0.12 if role == "trade" else 0.20),
            force_authority_01=0.30 if role == "estate" else 0.10,
            leveling_affinity_01=0.65 if role == "finance" else 0.45,
        )
    if has("merchant", "caravan master", "moneylender"):
        role = "finance" if "moneylender" in jk else "trade"
        return replace(
            JobArchetypeParams(
            "settlement_market",
            role,
            "market",
            "elite" if "moneylender" not in jk else "skilled",
            "cognitive",
            "self_directed",
            "notable",
            0.72,
            0.56,
            0.62,
            0.64,
            0.0,
            False,
            None,
            0.44,
            True,
            0.0,
            1.5,
            ),
            physical_demand_01=0.55 if "caravan master" in jk else (0.05 if role == "finance" else 0.18),
            force_authority_01=0.25 if "caravan master" in jk else 0.05,
            leveling_affinity_01=0.55,
        )
    if has("scholar", "physician", "priest"):
        role = "care" if "physician" in jk else ("ritual" if "priest" in jk else "knowledge")
        return replace(
            JobArchetypeParams(
            "settlement_market",
            role,
            "settlement",
            "elite" if role != "ritual" else "skilled",
            "cognitive" if role != "ritual" else "social",
            "self_directed",
            "professional",
            0.58 if role != "care" else 0.64,
            0.82 if role == "care" else 0.72,
            0.66,
            0.74,
            0.38 if role == "care" else 0.0,
            False,
            None,
            0.50,
            True,
            0.0,
            1.25,
            ),
            physical_demand_01=0.20 if role == "care" else 0.10,
            leveling_affinity_01=0.60,
        )
    if has("courtier", "steward", "treasurer"):
        role = "finance" if "treasurer" in jk else ("prestige" if "courtier" in jk else "stewardship")
        return replace(
            JobArchetypeParams(
            "office",
            role,
            "office",
            "elite" if role != "stewardship" else "skilled",
            "cognitive" if role != "prestige" else "social",
            "supervisory",
            "upper" if role != "stewardship" else "professional",
            0.70,
            0.56,
            0.68,
            0.66,
            0.0,
            False,
            None,
            0.46,
            True,
            0.0,
            1.45,
            ),
            physical_demand_01=0.05 if role in {"finance", "prestige"} else 0.18,
            force_authority_01=0.10 if role == "stewardship" else 0.05,
            leveling_affinity_01=0.70,
        )
    if has("judge", "magistrate", "officer", "mayor", "ruler", "guild master"):
        return replace(
            JobArchetypeParams(
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
            ),
            physical_demand_01=0.10,
            force_authority_01=0.35 if has("ruler", "officer", "guild master") else 0.22,
            leveling_affinity_01=0.55,
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
