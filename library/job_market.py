"""Human-editable job market semantics layered over genome job titles."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from library.job_economics import normalize_job_catalog_key


@dataclass(frozen=True)
class JobMarketParams:
    job_family: str
    essential_need: float
    luxury_need: float
    urban_scale: float
    scarcity_resilience: float
    saturation_curve: str
    food_delta: float
    stability_delta: float
    care_delta: float
    capacity_delta: float
    taxability: float


DEFAULT_JOB_MARKET = JobMarketParams(
    job_family="labor",
    essential_need=0.55,
    luxury_need=0.20,
    urban_scale=0.25,
    scarcity_resilience=0.55,
    saturation_curve="medium",
    food_delta=0.0,
    stability_delta=0.0,
    care_delta=0.0,
    capacity_delta=0.0,
    taxability=0.60,
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _float_cell(row: dict[str, object], key: str, default: float) -> float:
    try:
        raw = row.get(key)
        if raw is None or str(raw).strip() == "":
            return float(default)
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _str_cell(row: dict[str, object], key: str, default: str) -> str:
    raw = row.get(key)
    s = "" if raw is None else str(raw).strip()
    return s or default


def _params_from_row(row: dict[str, object], base: JobMarketParams) -> JobMarketParams:
    curve = _str_cell(row, "saturation_curve", base.saturation_curve).lower()
    if curve not in {"flat", "medium", "steep"}:
        curve = base.saturation_curve
    return JobMarketParams(
        job_family=_str_cell(row, "job_family", base.job_family).lower(),
        essential_need=_clamp(_float_cell(row, "essential_need", base.essential_need), 0.0, 1.0),
        luxury_need=_clamp(_float_cell(row, "luxury_need", base.luxury_need), 0.0, 1.0),
        urban_scale=_clamp(_float_cell(row, "urban_scale", base.urban_scale), 0.0, 1.0),
        scarcity_resilience=_clamp(
            _float_cell(row, "scarcity_resilience", base.scarcity_resilience), 0.0, 1.0
        ),
        saturation_curve=curve,
        food_delta=_clamp(_float_cell(row, "food_delta", base.food_delta), -1.0, 1.0),
        stability_delta=_clamp(
            _float_cell(row, "stability_delta", base.stability_delta), -1.0, 1.0
        ),
        care_delta=_clamp(_float_cell(row, "care_delta", base.care_delta), -1.0, 1.0),
        capacity_delta=_clamp(
            _float_cell(row, "capacity_delta", base.capacity_delta), -1.0, 1.0
        ),
        taxability=_clamp(_float_cell(row, "taxability", base.taxability), 0.0, 1.0),
    )


def infer_job_market_params(job_title: str | None) -> JobMarketParams:
    """Keyword fallback so every old or flavor-heavy job has market semantics."""
    jk = normalize_job_catalog_key(job_title or "")
    if not jk:
        return DEFAULT_JOB_MARKET

    def has(*parts: str) -> bool:
        return any(p in jk for p in parts)

    if has("farmer", "gatherer", "hunter", "fisher", "herder", "cook", "irrigation"):
        return JobMarketParams("food", 0.95, 0.05, 0.18, 0.95, "steep", 0.65, 0.02, 0.0, 0.08, 0.35)
    if has("midwife", "healer", "physician", "nurse", "caretaker", "child watcher"):
        return JobMarketParams("care", 0.90, 0.10, 0.22, 0.82, "steep", 0.0, 0.03, 0.75, 0.0, 0.45)
    if has("soldier", "guard", "defender", "watch", "constable", "legionary", "knight", "infantry"):
        return JobMarketParams("security", 0.78, 0.18, 0.32, 0.72, "medium", 0.0, 0.62, 0.0, 0.0, 0.55)
    if has("smith", "mason", "carpenter", "artisan", "potter", "weaver", "workshop", "builder"):
        return JobMarketParams("craft", 0.68, 0.28, 0.42, 0.66, "medium", 0.0, 0.05, 0.0, 0.35, 0.65)
    if has("porter", "carrier", "hauler", "laborer", "worker", "hand", "sweeper", "helper", "assistant"):
        return JobMarketParams("labor", 0.72, 0.08, 0.12, 0.88, "flat", 0.05, 0.0, 0.0, 0.10, 0.35)
    if has("merchant", "caravan", "dock", "courier", "broker", "trader", "market"):
        return JobMarketParams("trade", 0.45, 0.45, 0.58, 0.38, "medium", 0.0, 0.02, 0.0, 0.02, 0.82)
    if has("scribe", "clerk", "steward", "accountant", "judge", "magistrate", "administrator"):
        return JobMarketParams("admin", 0.48, 0.38, 0.62, 0.42, "steep", 0.0, 0.12, 0.0, 0.03, 0.90)
    if has("teacher", "scholar", "scientist", "professor", "analyst", "engineer", "architect", "software"):
        return JobMarketParams("knowledge", 0.36, 0.52, 0.72, 0.30, "steep", 0.0, 0.02, 0.0, 0.22, 0.78)
    if has("priest", "ritual", "monk", "nun", "oracle", "cult", "temple", "astrologer"):
        return JobMarketParams("ritual", 0.34, 0.50, 0.45, 0.48, "medium", 0.0, 0.10, 0.05, 0.0, 0.55)
    if has("artist", "performer", "actor", "duelist", "athlete", "entertainer", "stunt"):
        return JobMarketParams("entertainment", 0.18, 0.72, 0.70, 0.18, "steep", 0.0, 0.0, 0.0, 0.0, 0.62)
    if has("courtier", "noble", "mayor", "leader", "officer", "guild master", "founder"):
        return JobMarketParams("prestige", 0.22, 0.70, 0.78, 0.22, "steep", 0.0, 0.08, 0.0, 0.0, 0.86)
    if has("gambler", "troublemaker", "corrupt", "false", "raider", "criminal"):
        return JobMarketParams("criminal", 0.04, 0.35, 0.42, 0.55, "flat", -0.05, -0.38, 0.0, 0.0, 0.05)
    return DEFAULT_JOB_MARKET


@dataclass
class JobMarketCatalog:
    _rows: dict[str, JobMarketParams]
    _fallback: JobMarketParams

    @classmethod
    def load(cls, db_path: Path | str) -> "JobMarketCatalog":
        return _load_catalog(str(Path(db_path).resolve()))

    def lookup(self, job_title: str | None) -> JobMarketParams:
        jk = normalize_job_catalog_key(job_title or "")
        if not jk:
            return self._fallback
        return self._rows.get(jk, infer_job_market_params(jk))


@lru_cache(maxsize=8)
def _load_catalog(db_path_s: str) -> JobMarketCatalog:
    path = Path(db_path_s)
    rows: dict[str, JobMarketParams] = {}
    fallback = DEFAULT_JOB_MARKET
    if not path.is_file():
        return JobMarketCatalog(rows, fallback)
    with closing(sqlite3.connect(str(path))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            raw = conn.execute("SELECT * FROM job_market").fetchall()
        except sqlite3.OperationalError:
            return JobMarketCatalog(rows, fallback)
    for r in raw:
        d = {k: r[k] for k in r.keys()}
        jk = normalize_job_catalog_key(str(d.get("job_key") or ""))
        if not jk:
            continue
        base = fallback if jk == "*" else infer_job_market_params(jk)
        params = _params_from_row(d, base)
        if jk == "*":
            fallback = params
        else:
            rows[jk] = params
    return JobMarketCatalog(rows, fallback)
