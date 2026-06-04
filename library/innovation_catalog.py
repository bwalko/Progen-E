"""Config-backed innovation catalog and progression helpers."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

ACTIVE_CURATION_STATUSES = frozenset({"active", "reviewed", "seed"})


@dataclass(frozen=True)
class InnovationEra:
    era_id: str
    sort_order: int
    history_year_from: int
    history_year_to: int
    advancement_threshold: int
    notes: str = ""

    def contains(self, historical_year: int) -> bool:
        return self.history_year_from <= int(historical_year) <= self.history_year_to


@dataclass(frozen=True)
class InnovationCategoryRule:
    category: str
    max_rank_jump: int
    max_log_gap: float
    base_discovery_chance: float
    spread_multiplier: float
    polity_spread_multiplier: float
    wealth_weight: float
    notes: str = ""


@dataclass(frozen=True)
class Innovation:
    innovation_id: str
    source_id: str
    source_link: str
    source_title: str
    analogue_name: str
    category: str
    domain: str
    era_id: str
    history_year: int
    history_year_from: int | None
    history_year_to: int | None
    rank: int
    spreadability: float
    complexity: float
    starter_prevalence: float
    prerequisite_ids: tuple[str, ...]
    curation_status: str
    notes: str = ""


@dataclass(frozen=True)
class InnovationCatalog:
    innovations: tuple[Innovation, ...]
    eras: tuple[InnovationEra, ...]
    category_rules: dict[str, InnovationCategoryRule]

    @classmethod
    def load(cls, db_path: Path | str) -> "InnovationCatalog":
        path = Path(db_path)
        if not path.exists():
            return cls((), _default_eras(), _default_rules())
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            eras = _load_eras(conn)
            rules = _load_rules(conn)
            innovations = _load_innovations(conn)
        finally:
            conn.close()
        return cls(tuple(innovations), tuple(eras), rules)

    def active_innovations(self) -> tuple[Innovation, ...]:
        return self.innovations

    def era_for_year(self, historical_year: int | None) -> InnovationEra | None:
        if historical_year is None:
            return None
        for era in self.eras:
            if era.contains(int(historical_year)):
                return era
        return None

    def era_rank(self, era_id: str | None) -> int:
        eid = str(era_id or "").strip()
        for era in self.eras:
            if era.era_id == eid:
                return int(era.sort_order)
        return 0

    def era_by_rank(self, rank: int) -> InnovationEra | None:
        for era in self.eras:
            if int(era.sort_order) == int(rank):
                return era
        return None

    def next_era_id(self, era_id: str | None) -> str | None:
        current = self.era_rank(era_id)
        next_era = self.era_by_rank(current + 1)
        return next_era.era_id if next_era is not None else None

    def category_rule(self, category: str | None) -> InnovationCategoryRule:
        key = str(category or "").strip()
        return self.category_rules.get(key) or _fallback_rule(key or "craft")

    def innovation_by_id(self, innovation_id: str) -> Innovation | None:
        wanted = str(innovation_id or "").strip()
        for item in self.innovations:
            if item.innovation_id == wanted:
                return item
        return None

    def candidate_innovations(
        self, historical_year: int, *, max_ahead_years: int = 0
    ) -> tuple[Innovation, ...]:
        ceiling = int(historical_year) + max(0, int(max_ahead_years))
        return tuple(item for item in self.innovations if item.history_year <= ceiling)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _as_int(value: object, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _split_ids(value: object) -> tuple[str, ...]:
    text = _as_text(value)
    if not text:
        return ()
    return tuple(part.strip() for part in text.split(";") if part.strip())


def _load_eras(conn: sqlite3.Connection) -> list[InnovationEra]:
    if not _table_exists(conn, "innovation_eras"):
        return list(_default_eras())
    rows = conn.execute(
        """
        SELECT era_id, sort_order, history_year_from, history_year_to,
               advancement_threshold, notes
        FROM innovation_eras
        ORDER BY CAST(sort_order AS INTEGER), era_id
        """
    ).fetchall()
    out: list[InnovationEra] = []
    for row in rows:
        era_id = _as_text(row["era_id"])
        if not era_id:
            continue
        out.append(
            InnovationEra(
                era_id=era_id,
                sort_order=_as_int(row["sort_order"]),
                history_year_from=_as_int(row["history_year_from"]),
                history_year_to=_as_int(row["history_year_to"]),
                advancement_threshold=max(1, _as_int(row["advancement_threshold"], 5)),
                notes=_as_text(row["notes"]),
            )
        )
    return out or list(_default_eras())


def _load_rules(conn: sqlite3.Connection) -> dict[str, InnovationCategoryRule]:
    if not _table_exists(conn, "innovation_category_rules"):
        return _default_rules()
    rows = conn.execute(
        """
        SELECT category, max_rank_jump, max_log_gap, base_discovery_chance,
               spread_multiplier, polity_spread_multiplier, wealth_weight, notes
        FROM innovation_category_rules
        """
    ).fetchall()
    out: dict[str, InnovationCategoryRule] = {}
    for row in rows:
        category = _as_text(row["category"])
        if not category:
            continue
        out[category] = InnovationCategoryRule(
            category=category,
            max_rank_jump=max(1, _as_int(row["max_rank_jump"], 2)),
            max_log_gap=max(0.1, _as_float(row["max_log_gap"], 7.5)),
            base_discovery_chance=max(0.0, _as_float(row["base_discovery_chance"], 0.5)),
            spread_multiplier=max(0.0, _as_float(row["spread_multiplier"], 1.0)),
            polity_spread_multiplier=max(
                0.0, _as_float(row["polity_spread_multiplier"], 1.25)
            ),
            wealth_weight=max(0.0, _as_float(row["wealth_weight"], 0.02)),
            notes=_as_text(row["notes"]),
        )
    return out or _default_rules()


def _load_innovations(conn: sqlite3.Connection) -> list[Innovation]:
    if not _table_exists(conn, "innovations"):
        return []
    rows = conn.execute(
        """
        SELECT innovation_id, source_id, source_link, source_title, analogue_name,
               category, domain, era_id, history_year, history_year_from,
               history_year_to, rank, spreadability, complexity, starter_prevalence,
               prerequisite_ids, curation_status, notes
        FROM innovations
        ORDER BY category, CAST(rank AS INTEGER), CAST(history_year AS INTEGER),
                 innovation_id
        """
    ).fetchall()
    out: list[Innovation] = []
    for row in rows:
        status = _as_text(row["curation_status"]).lower()
        if status not in ACTIVE_CURATION_STATUSES:
            continue
        innovation_id = _as_text(row["innovation_id"])
        if not innovation_id:
            continue
        history_year = _as_int(row["history_year"], 9_999_999)
        out.append(
            Innovation(
                innovation_id=innovation_id,
                source_id=_as_text(row["source_id"]),
                source_link=_as_text(row["source_link"]),
                source_title=_as_text(row["source_title"]),
                analogue_name=_as_text(row["analogue_name"], innovation_id),
                category=_as_text(row["category"], "craft"),
                domain=_as_text(row["domain"], "toolmaking"),
                era_id=_as_text(row["era_id"], "unknown"),
                history_year=history_year,
                history_year_from=(
                    _as_int(row["history_year_from"])
                    if _as_text(row["history_year_from"])
                    else None
                ),
                history_year_to=(
                    _as_int(row["history_year_to"])
                    if _as_text(row["history_year_to"])
                    else None
                ),
                rank=max(1, _as_int(row["rank"], 1)),
                spreadability=max(0.0, min(1.0, _as_float(row["spreadability"], 0.5))),
                complexity=max(0.0, min(1.0, _as_float(row["complexity"], 0.5))),
                starter_prevalence=max(
                    0.0, min(1.0, _as_float(row["starter_prevalence"], 0.0))
                ),
                prerequisite_ids=_split_ids(row["prerequisite_ids"]),
                curation_status=status,
                notes=_as_text(row["notes"]),
            )
        )
    return sorted(out, key=lambda item: (item.category, item.rank, item.history_year, item.innovation_id))


def _default_eras() -> tuple[InnovationEra, ...]:
    return (
        InnovationEra("paleolithic", 0, -9_999_999, -10_001, 8),
        InnovationEra("neolithic", 1, -10_000, -3301, 6),
        InnovationEra("bronze_age", 2, -3300, -1201, 5),
        InnovationEra("iron_age", 3, -1200, -501, 5),
        InnovationEra("classical", 4, -500, 499, 5),
        InnovationEra("medieval", 5, 500, 1499, 5),
        InnovationEra("early_modern", 6, 1500, 1749, 5),
        InnovationEra("industrial", 7, 1750, 1899, 5),
        InnovationEra("modern", 8, 1900, 1979, 5),
        InnovationEra("digital", 9, 1980, 999_999, 5),
    )


def _fallback_rule(category: str) -> InnovationCategoryRule:
    return InnovationCategoryRule(
        category=category,
        max_rank_jump=2,
        max_log_gap=7.5,
        base_discovery_chance=0.50,
        spread_multiplier=1.0,
        polity_spread_multiplier=1.25,
        wealth_weight=0.02,
    )


def _default_rules() -> dict[str, InnovationCategoryRule]:
    rules = {
        "military": InnovationCategoryRule("military", 1, 6.7, 0.42, 0.70, 1.45, 0.015),
        "craft": _fallback_rule("craft"),
        "agriculture": InnovationCategoryRule("agriculture", 3, 8.2, 0.62, 1.10, 1.35, 0.030),
        "communication": InnovationCategoryRule("communication", 3, 8.0, 0.58, 1.05, 1.40, 0.030),
    }
    return rules
