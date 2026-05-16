"""Job catalog keys and loading per-job economic weights from ``job_economics`` config."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_JOB_SEX_TAG = re.compile(r"^(.+?)\s*\[([mMfF])\]\s*$")


def normalize_job_catalog_key(raw: str) -> str:
    """Stable key for ``job_economics`` rows: strip sex tag, lower, collapse whitespace."""
    s = (raw or "").strip()
    if not s:
        return ""
    m = _JOB_SEX_TAG.match(s)
    if m:
        title = (m.group(1) or "").strip()
        if title:
            s = title
    return " ".join(s.lower().split())


JobTier = Literal["common", "premium"]


@dataclass(frozen=True)
class JobEconomicsParams:
    pool_draw: float
    wage_yield: float
    value_add: float
    tax_rate: float


DEFAULT_COMMON = JobEconomicsParams(0.25, 0.35, 0.30, 0.08)
DEFAULT_PREMIUM = JobEconomicsParams(0.18, 0.52, 0.42, 0.10)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _parse_abs_field(val: object, default: float = 0.0) -> float:
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _parse_mul_field(val: object) -> float | None:
    """Blank / missing means no deviation (multiplier 1.0)."""
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _merge_base_and_muls(
    base: JobEconomicsParams,
    muls: dict[str, float | None],
) -> JobEconomicsParams:
    def m(axis: str) -> float:
        v = muls.get(axis)
        return 1.0 if v is None else max(0.0, float(v))

    return JobEconomicsParams(
        pool_draw=_clamp(base.pool_draw * m("pool_draw"), 0.04, 1.35),
        wage_yield=_clamp(base.wage_yield * m("wage_yield"), 0.06, 1.45),
        value_add=_clamp(base.value_add * m("value_add"), 0.06, 1.45),
        tax_rate=_clamp(base.tax_rate * m("tax_rate"), 0.01, 0.28),
    )


@dataclass
class JobEconomicsCatalog:
    """Era ``base`` rows (absolute) plus optional ``deviation`` rows (multipliers on base)."""

    _bases: dict[str, JobEconomicsParams]
    _base_star: JobEconomicsParams | None
    _dev: dict[tuple[str, str], dict[str, float | None]]
    _legacy_rows: dict[tuple[str, str], JobEconomicsParams] | None

    @classmethod
    def load(cls, db_path: Path | str) -> JobEconomicsCatalog:
        path = Path(db_path)
        bases: dict[str, JobEconomicsParams] = {}
        base_star: JobEconomicsParams | None = None
        dev: dict[tuple[str, str], dict[str, float | None]] = {}
        legacy: dict[tuple[str, str], JobEconomicsParams] = {}

        if not path.is_file():
            return cls(
                _bases=bases,
                _base_star=None,
                _dev=dev,
                _legacy_rows=None,
            )
        with closing(sqlite3.connect(str(path.resolve()))) as conn:
            conn.row_factory = sqlite3.Row
            try:
                info = conn.execute("PRAGMA table_info(job_economics)").fetchall()
                col_names = {str(r[1]) for r in info}
            except sqlite3.OperationalError:
                return cls(
                    _bases=bases,
                    _base_star=None,
                    _dev=dev,
                    _legacy_rows=None,
                )
            if "job_key" not in col_names:
                return cls(
                    _bases=bases,
                    _base_star=None,
                    _dev=dev,
                    _legacy_rows=None,
                )
            has_row_kind = "row_kind" in col_names
            try:
                db_rows = conn.execute("SELECT * FROM job_economics").fetchall()
            except sqlite3.OperationalError:
                return cls(
                    _bases=bases,
                    _base_star=None,
                    _dev=dev,
                    _legacy_rows=None,
                )

        if not has_row_kind:
            for r in db_rows:
                jk = normalize_job_catalog_key(str(r["job_key"] or ""))
                era = str(r["era"] or "").strip().lower()
                if not jk or not era:
                    continue
                legacy[(jk, era)] = JobEconomicsParams(
                    pool_draw=_parse_abs_field(r["pool_draw"]),
                    wage_yield=_parse_abs_field(r["wage_yield"]),
                    value_add=_parse_abs_field(r["value_add"]),
                    tax_rate=_parse_abs_field(r["tax_rate"]),
                )
            return cls(_bases={}, _base_star=None, _dev={}, _legacy_rows=legacy)

        for r in db_rows:
            jk = normalize_job_catalog_key(str(r["job_key"] or ""))
            era = str(r["era"] or "").strip().lower()
            if not jk or not era:
                continue
            rk_raw = r["row_kind"] if "row_kind" in r.keys() else None
            rk = str(rk_raw or "deviation").strip().lower()
            if rk == "base":
                if jk != "*":
                    continue
                params = JobEconomicsParams(
                    pool_draw=_parse_abs_field(r["pool_draw"], 0.25),
                    wage_yield=_parse_abs_field(r["wage_yield"], 0.35),
                    value_add=_parse_abs_field(r["value_add"], 0.30),
                    tax_rate=_parse_abs_field(r["tax_rate"], 0.08),
                )
                if era == "*":
                    base_star = params
                else:
                    bases[era] = params
                continue
            if rk != "deviation":
                continue
            dev[(jk, era)] = {
                "pool_draw": _parse_mul_field(r["pool_draw"]),
                "wage_yield": _parse_mul_field(r["wage_yield"]),
                "value_add": _parse_mul_field(r["value_add"]),
                "tax_rate": _parse_mul_field(r["tax_rate"]),
            }

        return cls(
            _bases=bases,
            _base_star=base_star,
            _dev=dev,
            _legacy_rows=None,
        )

    def _base_for_era(self, era: str) -> JobEconomicsParams:
        e = (era or "").strip().lower() or "modern"
        if e in self._bases:
            return self._bases[e]
        if self._base_star is not None:
            return self._base_star
        return DEFAULT_COMMON

    def _dev_muls(self, jk: str, era: str) -> dict[str, float | None]:
        if (jk, era) in self._dev:
            return dict(self._dev[(jk, era)])
        if (jk, "*") in self._dev:
            return dict(self._dev[(jk, "*")])
        return {
            "pool_draw": None,
            "wage_yield": None,
            "value_add": None,
            "tax_rate": None,
        }

    def lookup(
        self,
        job_title: str | None,
        era: str | None,
        *,
        tier: JobTier = "common",
    ) -> JobEconomicsParams:
        _ = tier
        if self._legacy_rows is not None:
            return self._lookup_legacy(job_title, era, tier=tier)
        jk = normalize_job_catalog_key(job_title or "")
        if not jk:
            return DEFAULT_PREMIUM if tier == "premium" else DEFAULT_COMMON
        e = (era or "").strip().lower()
        if not e:
            e = "modern"
        base = self._base_for_era(e)
        return _merge_base_and_muls(base, self._dev_muls(jk, e))

    def _lookup_legacy(
        self,
        job_title: str | None,
        era: str | None,
        *,
        tier: JobTier,
    ) -> JobEconomicsParams:
        rows = self._legacy_rows or {}
        jk = normalize_job_catalog_key(job_title or "")
        if not jk:
            return DEFAULT_PREMIUM if tier == "premium" else DEFAULT_COMMON
        e = (era or "").strip().lower()
        if not e:
            e = "modern"
        if (jk, e) in rows:
            return rows[(jk, e)]
        if (jk, "*") in rows:
            return rows[(jk, "*")]
        if ("*", e) in rows:
            return rows[("*", e)]
        if ("*", "*") in rows:
            return rows[("*", "*")]
        return DEFAULT_PREMIUM if tier == "premium" else DEFAULT_COMMON
