"""Lightweight mixed-population cache and census consistency checks."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext


@dataclass(frozen=True)
class MixedPopulationAuditIssue:
    year: int
    settlement_id: str
    check: str
    detail: str


def _audit_enabled() -> bool:
    raw = os.environ.get("HISTORY_SIM_VALIDATE_MIXED_COUNTS", "").strip().lower()
    return raw in {"1", "true", "yes"}


def audit_enabled() -> bool:
    """Return whether per-year mixed census validation is enabled."""
    return _audit_enabled()


def _direct_detailed_alive_by_settlement(ctx: SimulationContext) -> dict[str, int]:
    out: dict[str, int] = {}
    for sid, count in ctx.alive_census_cache().count_by_settlement.items():
        out[str(sid)] = int(count)
    return out


def _direct_nondetailed_alive_by_settlement(
    ctx: SimulationContext, *, conn: sqlite3.Connection | None = None
) -> dict[str, int]:
    from library.nondetailed_population import nondetailed_counts_by_settlement
    from library.world_save import ensure_checkpoint_schema

    if conn is not None:
        return {
            str(sid): int(count)
            for sid, count in nondetailed_counts_by_settlement(conn).items()
        }
    with closing(sqlite3.connect(ctx.save_db_path)) as own_conn:
        own_conn.row_factory = sqlite3.Row
        ensure_checkpoint_schema(own_conn)
        return {
            str(sid): int(count)
            for sid, count in nondetailed_counts_by_settlement(own_conn).items()
        }


def validate_mixed_population_caches(
    ctx: SimulationContext,
    *,
    year: int,
    conn: sqlite3.Connection | None = None,
) -> list[MixedPopulationAuditIssue]:
    """Compare cached mixed/nondetailed counts against direct recomputation."""
    issues: list[MixedPopulationAuditIssue] = []
    y = int(year)
    detailed_direct = _direct_detailed_alive_by_settlement(ctx)
    nondetailed_direct = _direct_nondetailed_alive_by_settlement(ctx, conn=conn)

    try:
        nondetailed_cached = ctx.nondetailed_population_counts_by_settlement(conn=conn)
    except Exception as exc:
        issues.append(
            MixedPopulationAuditIssue(
                year=y,
                settlement_id="",
                check="nondetailed_cache_read",
                detail=str(exc),
            )
        )
        nondetailed_cached = {}

    try:
        mixed_cached = ctx.mixed_population_counts_by_settlement()
    except Exception as exc:
        issues.append(
            MixedPopulationAuditIssue(
                year=y,
                settlement_id="",
                check="mixed_cache_read",
                detail=str(exc),
            )
        )
        mixed_cached = {}

    settlement_ids = sorted(
        set(detailed_direct)
        | set(nondetailed_direct)
        | set(nondetailed_cached)
        | set(mixed_cached)
        | set(ctx.settlements_by_id)
    )
    for sid in settlement_ids:
        detailed = int(detailed_direct.get(sid, 0))
        nondetailed = int(nondetailed_direct.get(sid, 0))
        mixed_direct = detailed + nondetailed
        cached_nd = int(nondetailed_cached.get(sid, 0))
        cached_mixed = int(mixed_cached.get(sid, 0))
        if cached_nd != nondetailed:
            issues.append(
                MixedPopulationAuditIssue(
                    year=y,
                    settlement_id=sid,
                    check="nondetailed_alive",
                    detail=f"cached={cached_nd} direct={nondetailed}",
                )
            )
        if cached_mixed != mixed_direct:
            issues.append(
                MixedPopulationAuditIssue(
                    year=y,
                    settlement_id=sid,
                    check="mixed_alive",
                    detail=f"cached={cached_mixed} direct={mixed_direct}",
                )
            )

    for sid, st in ctx.settlements_by_id.items():
        status = (st.status or "").strip().lower()
        detailed = int(detailed_direct.get(sid, 0))
        nondetailed = int(nondetailed_direct.get(sid, 0))
        mixed = detailed + nondetailed
        if status == "abandoned" and mixed > 0:
            issues.append(
                MixedPopulationAuditIssue(
                    year=y,
                    settlement_id=sid,
                    check="abandoned_populated",
                    detail=f"mixed_alive={mixed} detailed={detailed} nondetailed={nondetailed}",
                )
            )
        if status == "active" and mixed <= 0 and (st.founding_reason or "").strip():
            issues.append(
                MixedPopulationAuditIssue(
                    year=y,
                    settlement_id=sid,
                    check="active_empty",
                    detail="active settlement with zero mixed_alive",
                )
            )

    return issues


def validate_low_resolution_sample_events(
    ctx: SimulationContext,
    *,
    year: int,
) -> list[MixedPopulationAuditIssue]:
    """Ensure low-resolution promotions only fire when detailed_alive was zero."""
    issues: list[MixedPopulationAuditIssue] = []
    y = int(year)
    for event_year, event_type, payload in getattr(ctx, "_pending_simulation_events", ()):
        if str(event_type) != "settlement_low_resolution_sample":
            continue
        if event_year is not None and int(event_year) != y:
            continue
        detailed_alive = payload.get("detailed_alive")
        if detailed_alive is None:
            continue
        if int(detailed_alive) != 0:
            sid = str(payload.get("settlement_id") or "")
            issues.append(
                MixedPopulationAuditIssue(
                    year=y,
                    settlement_id=sid,
                    check="low_resolution_sample_detailed_alive",
                    detail=f"detailed_alive={detailed_alive}",
                )
            )
    return issues


def run_mixed_population_audit_if_enabled(
    ctx: SimulationContext,
    *,
    year: int,
    conn: sqlite3.Connection | None = None,
) -> list[MixedPopulationAuditIssue]:
    """Run cache and event audits when HISTORY_SIM_VALIDATE_MIXED_COUNTS is set."""
    if not _audit_enabled():
        return []
    issues = validate_mixed_population_caches(ctx, year=int(year), conn=conn)
    issues.extend(validate_low_resolution_sample_events(ctx, year=int(year)))
    if issues:
        sample = issues[:8]
        lines = [
            f"mixed_population_audit year={year} issue_count={len(issues)}",
            *(f"  {item.check} {item.settlement_id}: {item.detail}" for item in sample),
        ]
        if len(issues) > len(sample):
            lines.append(f"  ... and {len(issues) - len(sample)} more")
        print("\n".join(lines))
    return issues


def audit_save_sqlite_counts(
    save_db_path: str,
) -> list[dict[str, Any]]:
    """Post-run audit rows from save.sqlite without SimulationContext caches."""
    from library.world_save import ensure_checkpoint_schema

    rows: list[dict[str, Any]] = []
    with closing(sqlite3.connect(save_db_path)) as conn:
        conn.row_factory = sqlite3.Row
        ensure_checkpoint_schema(conn)
        world_year_row = conn.execute(
            "SELECT current_year FROM world_state LIMIT 1"
        ).fetchone()
        world_year = (
            int(world_year_row["current_year"]) if world_year_row is not None else None
        )
        year_clause = ""
        params: tuple[object, ...] = ()
        if world_year is not None:
            year_clause = "AND (p.deathyear IS NULL OR p.deathyear > ?)"
            params = (int(world_year),)
        detailed_rows = conn.execute(
            f"""
            SELECT
              COALESCE(cs.settlement_id, bs.settlement_id) AS settlement_id,
              COUNT(*) AS c
            FROM simulation_people p
            LEFT JOIN simulation_settlement_lookup cs
              ON cs.settlement_key = p.current_settlement_key
            LEFT JOIN simulation_settlement_lookup bs
              ON bs.settlement_key = p.birthplace_settlement_key
            WHERE p.is_alive = 1
              {year_clause}
              AND COALESCE(cs.settlement_id, bs.settlement_id) IS NOT NULL
            GROUP BY COALESCE(cs.settlement_id, bs.settlement_id)
            """,
            params,
        ).fetchall()
        detailed = {str(r["settlement_id"]): int(r["c"]) for r in detailed_rows}
        nondetailed_rows = conn.execute(
            """
            SELECT
              COALESCE(current_settlement_id, birthplace_settlement_id) AS settlement_id,
              COUNT(*) AS c
            FROM simulation_people_nondetailed_readable
            WHERE is_alive = 1
              AND COALESCE(current_settlement_id, birthplace_settlement_id) IS NOT NULL
            GROUP BY COALESCE(current_settlement_id, birthplace_settlement_id)
            """
        ).fetchall()
        nondetailed = {str(r["settlement_id"]): int(r["c"]) for r in nondetailed_rows}
        settlement_rows = conn.execute(
            """
            SELECT settlement_id, region_id, status, abandoned_sim_year
            FROM simulation_settlements_readable
            ORDER BY settlement_id
            """
        ).fetchall()
        for row in settlement_rows:
            sid = str(row["settlement_id"])
            status = str(row["status"] or "").strip().lower()
            d_alive = int(detailed.get(sid, 0))
            nd_alive = int(nondetailed.get(sid, 0))
            mixed = d_alive + nd_alive
            rows.append(
                {
                    "settlement_id": sid,
                    "region_id": str(row["region_id"] or ""),
                    "status": status,
                    "abandoned_sim_year": row["abandoned_sim_year"],
                    "detailed_alive": d_alive,
                    "nondetailed_alive": nd_alive,
                    "mixed_alive": mixed,
                }
            )
    return rows
