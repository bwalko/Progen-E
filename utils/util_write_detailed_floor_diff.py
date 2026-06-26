"""Write temp/detailed_floor_promotion_diff.md from existing trace TSVs."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.util_trace_detailed_floor_promotion_diff import (  # noqa: E402
    _TRACE_AFTER,
    _TRACE_BEFORE,
    _DIFF_MD,
    _classify,
    _first_divergence,
    _load_trace,
    _promotion_rows,
    _summarize,
    _decision_key,
    _write_diff_md,
)


def _yearly_from_traces(before_rows, after_rows) -> tuple[dict[int, int], dict[int, int]]:
    """Approximate yearly promotion pressure; use scale log when available."""
    scale = _ROOT / "unit_test" / "population_sim_scale.tsv"
    yearly_before: dict[int, int] = {}
    yearly_after: dict[int, int] = {}
    if scale.exists():
        with scale.open(encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                if row.get("sim_seed") != "639789854":
                    continue
                if row.get("label") != "detailed_alive" or row.get("phase") != "before_summary":
                    continue
                year = int(row["gauge_year"])
                val = int(float(row["value"]))
                ts = row.get("iso_timestamp", "")
                if "LEGACY" in ts:
                    continue
                # keep latest rows per run by overwriting; caller should filter timestamps manually
                yearly_before[year] = val
                yearly_after[year] = val
    if not yearly_before:
        cumulative_b = 0
        cumulative_a = 0
        for year in range(1000, 1010):
            cumulative_b += sum(
                1 for r in _promotion_rows(before_rows) if int(r["year"]) == year
            )
            cumulative_a += sum(
                1 for r in _promotion_rows(after_rows) if int(r["year"]) == year
            )
            yearly_before[year] = cumulative_b
            yearly_after[year] = cumulative_a
    return yearly_before, yearly_after


def main() -> None:
    before_rows = _load_trace(_TRACE_BEFORE)
    after_rows = _load_trace(_TRACE_AFTER)
    yearly_before_path = _ROOT / "temp" / "detailed_floor_yearly_before.tsv"
    yearly_after_path = _ROOT / "temp" / "detailed_floor_yearly_after.tsv"
    if yearly_before_path.exists() and yearly_after_path.exists():
        yearly_before = {
            int(r["year"]): int(r["detailed_alive_before_summary"])
            for r in csv.DictReader(yearly_before_path.open(encoding="utf-8"), delimiter="\t")
        }
        yearly_after = {
            int(r["year"]): int(r["detailed_alive_before_summary"])
            for r in csv.DictReader(yearly_after_path.open(encoding="utf-8"), delimiter="\t")
        }
    else:
        yearly_before, yearly_after = _yearly_from_traces(before_rows, after_rows)
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
