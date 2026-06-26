"""Compare detailed-floor promotion traces: legacy per-promotion conn vs batch conn.

Writes:
- temp/detailed_floor_promotion_trace_before.tsv
- temp/detailed_floor_promotion_trace_after.tsv
- temp/detailed_floor_promotion_diff.md

Example::

    python utils/util_trace_detailed_floor_promotion_diff.py \\
      --years 10 --starting-couples 100 --seed 639789854
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TEMP = _ROOT / "temp"
_TRACE_BEFORE = _TEMP / "detailed_floor_promotion_trace_before.tsv"
_TRACE_AFTER = _TEMP / "detailed_floor_promotion_trace_after.tsv"
_DIFF_MD = _TEMP / "detailed_floor_promotion_diff.md"
_YEARLY_BEFORE = _TEMP / "detailed_floor_yearly_before.tsv"
_YEARLY_AFTER = _TEMP / "detailed_floor_yearly_after.tsv"


def _run_sim(
    *,
    world_id: str,
    years: int,
    starting_couples: int,
    seed: int,
    mode: str,
    trace_path: Path,
    yearly_path: Path,
) -> None:
    env = os.environ.copy()
    env["POPULATION_GROWTH_SIM_SEED"] = str(seed)
    env["HISTORY_SIM_RESET_WORLD"] = "1"
    env["POPULATION_SIM_SKIP_TIMING_LOG"] = "1"
    env["DETAILED_FLOOR_PROMOTION_TRACE"] = str(trace_path)
    env.pop("DETAILED_FLOOR_BATCH_CONN", None)
    env.pop("DETAILED_FLOOR_LEGACY_CODE", None)
    env.pop("DETAILED_FLOOR_BATCH_UNFIXED", None)
    if mode == "legacy":
        env["DETAILED_FLOOR_LEGACY_CODE"] = "1"
    elif mode == "batch_unfixed":
        env["DETAILED_FLOOR_BATCH_UNFIXED"] = "1"
    else:
        env["DETAILED_FLOOR_BATCH_CONN"] = "1"
    env["HISTORY_SIM_PROFILE_LAST_N_YEARS"] = str(years)
    cmd = [
        sys.executable,
        str(_ROOT / "utils" / "run_population_simulation.py"),
        "--world-id",
        world_id,
        "--reset-world",
        "--years",
        str(years),
        "--starting-couples",
        str(starting_couples),
        "--seed",
        str(seed),
        "--use-nondetailed-directory",
        "--profile-last-years",
        str(years),
        "--skip-report-files",
        "--skip-timing-log",
    ]
    subprocess.run(cmd, cwd=_ROOT, env=env, check=True)
    scale_rows = _ROOT / "unit_test" / "population_sim_scale.tsv"
    if not scale_rows.exists():
        raise SystemExit(f"missing scale log: {scale_rows}")
    yearly: dict[int, int] = {}
    with scale_rows.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("sim_seed") != str(seed):
                continue
            if row.get("label") != "detailed_alive":
                continue
            if row.get("phase") != "before_summary":
                continue
            year = int(row["gauge_year"])
            yearly[year] = int(float(row["value"]))
    with yearly_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["year", "detailed_alive_before_summary"])
        for year in sorted(yearly):
            writer.writerow([year, yearly[year]])


def _load_trace(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _promotion_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [r for r in rows if r.get("promotion_reason") not in {"", "none", "pass_complete"}]


def _summarize(rows: list[dict[str, str]]) -> dict[str, object]:
    promos = _promotion_rows(rows)
    by_year = Counter(int(r["year"]) for r in promos)
    by_reason = Counter(r.get("promotion_reason", "") for r in promos)
    stale_cached = [
        r
        for r in promos
        if r.get("cached_counts_used") == "0" and r.get("promotion_needed") == "1"
    ]
    return {
        "promotion_events": len(promos),
        "by_year": dict(sorted(by_year.items())),
        "by_reason": dict(by_reason),
        "stale_cached_decisions": len(stale_cached),
    }


def _first_divergence(before: dict[int, int], after: dict[int, int]) -> tuple[int | None, int, int]:
    years = sorted(set(before) | set(after))
    for year in years:
        b = int(before.get(year, 0))
        a = int(after.get(year, 0))
        if b != a:
            return year, b, a
    last = years[-1] if years else None
    if last is None:
        return None, 0, 0
    return None, int(before.get(last, 0)), int(after.get(last, 0))


def _decision_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        str(row.get("year", "")),
        str(row.get("settlement_id", "")),
        str(row.get("promotion_ordinal", "")),
    )


def _write_diff_md(
    *,
    before_rows: list[dict[str, str]],
    after_rows: list[dict[str, str]],
    yearly_before: dict[int, int],
    yearly_after: dict[int, int],
    classification: str,
) -> None:
    before_promos = _promotion_rows(before_rows)
    after_promos = _promotion_rows(after_rows)
    before_by_decision = {_decision_key(r): r for r in before_promos}
    after_by_decision = {_decision_key(r): r for r in after_promos}
    divergent_decisions: list[tuple[tuple[str, str, str], dict[str, str] | None, dict[str, str] | None]] = []
    all_keys = sorted(set(before_by_decision) | set(after_by_decision))
    for key in all_keys:
        b = before_by_decision.get(key)
        a = after_by_decision.get(key)
        if b != a:
            divergent_decisions.append((key, b, a))
    first_year, before_alive, after_alive = _first_divergence(yearly_before, yearly_after)
    lines = [
        "# Detailed floor promotion divergence",
        "",
        "## Classification",
        "",
        classification,
        "",
        "## Yearly detailed_alive (before_summary)",
        "",
        "| year | legacy_conn | batch_unfixed | delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for year in sorted(set(yearly_before) | set(yearly_after)):
        b = int(yearly_before.get(year, 0))
        a = int(yearly_after.get(year, 0))
        mark = " **" if b != a else ""
        lines.append(f"| {year}{mark} | {b} | {a} | {a - b} |")
    if first_year is not None:
        lines.extend(
            [
                "",
                f"First yearly `detailed_alive` divergence at year **{first_year}** "
                f"(legacy {before_alive}, batch-unfixed {after_alive}, delta {after_alive - before_alive}).",
            ]
        )
    else:
        lines.extend(
            [
                "",
                f"No yearly divergence detected; final detailed_alive legacy={before_alive}, batch={after_alive}.",
            ]
        )
    lines.extend(
        [
            "",
            "## Promotion volume",
            "",
            f"- Legacy per-promotion conn: {_summarize(before_rows)['promotion_events']} promotion rows",
            f"- Batch conn unfixed (pre-map fix): {_summarize(after_rows)['promotion_events']} promotion rows",
            "",
            "## First divergent promotion decisions (up to 25)",
            "",
        ]
    )
    if not divergent_decisions:
        lines.append("No per-promotion trace row differences.")
    else:
        lines.append("| year | settlement_id | ord | legacy reason | batch reason | legacy person | batch person |")
        lines.append("| --- | --- | ---: | --- | --- | --- | --- |")
        for (year, sid, ordinal), b, a in divergent_decisions[:25]:
            lines.append(
                "| {year} | {sid} | {ord} | {lreason} | {areason} | {lpid} | {apid} |".format(
                    year=year,
                    sid=sid,
                    ord=ordinal,
                    lreason=(b or {}).get("promotion_reason", ""),
                    areason=(a or {}).get("promotion_reason", ""),
                    lpid=(b or {}).get("person_ids_selected", ""),
                    apid=(a or {}).get("person_ids_selected", ""),
                )
            )
    stale_legacy = _summarize(before_rows)["stale_cached_decisions"]
    stale_batch = _summarize(after_rows)["stale_cached_decisions"]
    lines.extend(
        [
            "",
            "## Cached-count usage during promotions",
            "",
            f"- Legacy conn decisions where cached mixed != decision mixed: {stale_legacy}",
            f"- Batch conn unfixed decisions where cached mixed != decision mixed: {stale_batch}",
            "",
            "## Trace files",
            "",
            f"- Legacy: `{_TRACE_BEFORE.relative_to(_ROOT).as_posix()}`",
            f"- Batch: `{_TRACE_AFTER.relative_to(_ROOT).as_posix()}`",
        ]
    )
    _DIFF_MD.parent.mkdir(parents=True, exist_ok=True)
    _DIFF_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _classify(
    *,
    yearly_before: dict[int, int],
    yearly_after: dict[int, int],
    before_rows: list[dict[str, str]],
    after_rows: list[dict[str, str]],
) -> str:
    first_year, _, _ = _first_divergence(yearly_before, yearly_after)
    if first_year is None and _promotion_rows(before_rows) == _promotion_rows(after_rows):
        return (
            "**Match** — legacy per-promotion conn and batch conn with in-memory floor maps "
            "produce identical yearly `detailed_alive` and promotion traces."
        )
    before_promos = len(_promotion_rows(before_rows))
    after_promos = len(_promotion_rows(after_rows))
    stale_batch = [
        r
        for r in _promotion_rows(after_rows)
        if r.get("promotion_needed") == "1"
        and int(r.get("detailed_alive_before") or 0)
        < int(r.get("direct_detailed_alive") or 0)
    ]
    if after_promos > before_promos and stale_batch:
        return (
            "**new code over-promotes because cached counts are stale during the loop** — "
            "batch-conn floor decisions used `len(by_settlement)` while direct census "
            f"already showed more detailed residents ({len(stale_batch)} promotion rows); "
            "in-memory detailed maps fix this."
        )
    if after_promos < before_promos:
        return (
            "**old code under-promoted because commits/connection handling prevented intended promotions** — "
            "legacy per-promotion SQLite connections committed/deferred differently than the batched floor pass."
        )
    reason_mismatch = 0
    for key in set(_decision_key(r) for r in _promotion_rows(before_rows)) & set(
        _decision_key(r) for r in _promotion_rows(after_rows)
    ):
        b = next(r for r in before_rows if _decision_key(r) == key)
        a = next(r for r in after_rows if _decision_key(r) == key)
        if b.get("promotion_reason") != a.get("promotion_reason") or b.get(
            "person_ids_selected"
        ) != a.get("person_ids_selected"):
            reason_mismatch += 1
    if reason_mismatch:
        return (
            "**new code changes selection order or RNG consumption** — "
            f"{reason_mismatch} shared decision keys pick different people or promotion sources."
        )
    if first_year is not None:
        return (
            "**other** — yearly totals diverge from year "
            f"{first_year} without a clear stale-cache or selection-order signature in the first 25 trace diffs."
        )
    return "**other** — traces differ but yearly totals match."


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-id", default="default")
    parser.add_argument("--years", type=int, default=10)
    parser.add_argument("--starting-couples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=639789854)
    args = parser.parse_args()
    _TEMP.mkdir(parents=True, exist_ok=True)
    for path in (_TRACE_BEFORE, _TRACE_AFTER):
        if path.exists():
            path.unlink()
    print("Running legacy detailed-floor trace (DETAILED_FLOOR_LEGACY_CODE=1)...")
    _run_sim(
        world_id=args.world_id,
        years=args.years,
        starting_couples=args.starting_couples,
        seed=args.seed,
        mode="legacy",
        trace_path=_TRACE_BEFORE,
        yearly_path=_YEARLY_BEFORE,
    )
    print("Running batch-unfixed detailed-floor trace (DETAILED_FLOOR_BATCH_UNFIXED=1)...")
    _run_sim(
        world_id=args.world_id,
        years=args.years,
        starting_couples=args.starting_couples,
        seed=args.seed,
        mode="batch_unfixed",
        trace_path=_TRACE_AFTER,
        yearly_path=_YEARLY_AFTER,
    )
    before_rows = _load_trace(_TRACE_BEFORE)
    after_rows = _load_trace(_TRACE_AFTER)
    yearly_before = {
        int(r["year"]): int(r["detailed_alive_before_summary"])
        for r in csv.DictReader(_YEARLY_BEFORE.open(encoding="utf-8"), delimiter="\t")
    }
    yearly_after = {
        int(r["year"]): int(r["detailed_alive_before_summary"])
        for r in csv.DictReader(_YEARLY_AFTER.open(encoding="utf-8"), delimiter="\t")
    }
    classification = _classify(
        yearly_before=yearly_before,
        yearly_after=yearly_after,
        before_rows=before_rows,
        after_rows=after_rows,
    )
    _write_diff_md(
        before_rows=before_rows,
        after_rows=after_rows,
        yearly_before=yearly_before,
        yearly_after=yearly_after,
        classification=classification,
    )
    print(f"Wrote {_DIFF_MD}")


if __name__ == "__main__":
    main()
