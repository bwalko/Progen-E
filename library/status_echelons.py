"""Status echelons for prestige mobility and elite household economics."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StatusEchelonParams:
    echelon_key: str
    display_name: str
    min_social_standing_01: float
    min_household_prosperity: float
    class_bands: tuple[str, ...]
    job_market_types: tuple[str, ...]
    prestige_access_multiplier: float
    patronage_power_01: float
    service_hiring_multiplier: float
    scandal_fall_severity_01: float
    investment_share_01: float


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


def _str_cell(row: dict[str, object], key: str, default: str) -> str:
    raw = row.get(key)
    s = "" if raw is None else str(raw).strip()
    return s or default


def _tuple_cell(row: dict[str, object], key: str) -> tuple[str, ...]:
    raw = str(row.get(key) or "").strip().lower()
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.replace("|", ";").split(";") if part.strip())


def _default_rows() -> tuple[StatusEchelonParams, ...]:
    return (
        StatusEchelonParams("marginal", "Marginal", 0.0, 0.0, ("marginal",), ("vice", "criminal", "none"), 0.35, 0.02, 0.0, 0.18, 0.0),
        StatusEchelonParams("laboring", "Laboring", 0.16, 0.0, ("servant", "commoner", "household"), ("settlement_market", "domestic_service", "household_care"), 0.65, 0.04, 0.05, 0.20, 0.0),
        StatusEchelonParams("comfortable", "Comfortable", 0.32, 0.35, ("commoner", "professional"), ("settlement_market",), 0.90, 0.08, 0.20, 0.24, 0.01),
        StatusEchelonParams("professional", "Professional", 0.48, 0.80, ("professional", "upper"), ("settlement_market", "office"), 1.10, 0.18, 0.45, 0.30, 0.02),
        StatusEchelonParams("notable", "Notable", 0.60, 1.50, ("notable", "upper"), ("settlement_market", "office"), 1.35, 0.34, 0.75, 0.42, 0.035),
        StatusEchelonParams("elite", "Elite", 0.74, 3.00, ("elite", "upper"), ("settlement_market", "office"), 1.70, 0.58, 1.15, 0.58, 0.055),
        StatusEchelonParams("ruling", "Ruling", 0.86, 4.50, ("ruling", "elite", "upper"), ("office",), 2.05, 0.78, 1.45, 0.72, 0.070),
    )


def _row_from_dict(row: dict[str, object]) -> StatusEchelonParams:
    key = _str_cell(row, "echelon_key", "laboring").lower()
    return StatusEchelonParams(
        echelon_key=key,
        display_name=_str_cell(row, "display_name", key.replace("_", " ").title()),
        min_social_standing_01=_clamp(_float_cell(row, "min_social_standing_01", 0.0), 0.0, 1.0),
        min_household_prosperity=max(0.0, _float_cell(row, "min_household_prosperity", 0.0)),
        class_bands=_tuple_cell(row, "class_bands"),
        job_market_types=_tuple_cell(row, "job_market_types"),
        prestige_access_multiplier=max(0.0, _float_cell(row, "prestige_access_multiplier", 1.0)),
        patronage_power_01=_clamp(_float_cell(row, "patronage_power_01", 0.0), 0.0, 1.0),
        service_hiring_multiplier=max(0.0, _float_cell(row, "service_hiring_multiplier", 0.0)),
        scandal_fall_severity_01=_clamp(_float_cell(row, "scandal_fall_severity_01", 0.0), 0.0, 1.0),
        investment_share_01=_clamp(_float_cell(row, "investment_share_01", 0.0), 0.0, 1.0),
    )


@dataclass(frozen=True)
class StatusEchelonCatalog:
    rows: tuple[StatusEchelonParams, ...]

    @classmethod
    def load(cls, db_path: Path | str) -> "StatusEchelonCatalog":
        return _load_catalog(str(Path(db_path).resolve()))

    def echelon_for_values(
        self,
        *,
        social_standing_01: float | None,
        household_prosperity: float | None,
        social_class_band: str | None = None,
        job_market_type: str | None = None,
    ) -> StatusEchelonParams:
        standing = _clamp(float(social_standing_01 or 0.0), 0.0, 1.0)
        prosperity = max(0.0, float(household_prosperity or 0.0))
        class_band = (social_class_band or "").strip().lower()
        market_type = (job_market_type or "").strip().lower()
        best = self.rows[0]
        for row in self.rows:
            effective_standing = standing
            if class_band and class_band in row.class_bands:
                effective_standing += 0.025
            if market_type and market_type in row.job_market_types:
                effective_standing += 0.015
            if (
                effective_standing >= float(row.min_social_standing_01)
                and prosperity >= float(row.min_household_prosperity)
            ):
                best = row
        return best

    def echelon_for_person(self, person: Any) -> StatusEchelonParams:
        return self.echelon_for_values(
            social_standing_01=getattr(person, "social_standing_01", None),
            household_prosperity=getattr(person, "household_prosperity", None),
            social_class_band=getattr(person, "social_class_band", None),
            job_market_type=getattr(person, "job_market_type", None),
        )


@lru_cache(maxsize=8)
def _load_catalog(db_path_s: str) -> StatusEchelonCatalog:
    rows: list[StatusEchelonParams] = []
    path = Path(db_path_s)
    if path.is_file():
        with closing(sqlite3.connect(str(path))) as conn:
            conn.row_factory = sqlite3.Row
            try:
                raw_rows = conn.execute(
                    "SELECT rowid, * FROM status_echelons ORDER BY min_social_standing_01, min_household_prosperity, rowid"
                ).fetchall()
            except sqlite3.OperationalError:
                raw_rows = []
        for raw in raw_rows:
            row = {k: raw[k] for k in raw.keys()}
            key = _str_cell(row, "echelon_key", "").lower()
            if key:
                rows.append(_row_from_dict(row))
    if not rows:
        rows = list(_default_rows())
    rows.sort(key=lambda r: (float(r.min_social_standing_01), float(r.min_household_prosperity)))
    return StatusEchelonCatalog(tuple(rows))


def clear_status_echelon_cache() -> None:
    _load_catalog.cache_clear()
