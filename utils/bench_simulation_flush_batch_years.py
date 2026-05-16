"""Compare population run wall time for different SIM_STORE_FLUSH_BATCH_YEARS values.

Spawns separate processes (avoids Windows file locks on the run store). Uses
``utils/run_population_simulation.py`` by default; intended for long runs (e.g.
``--years 400``). For a quick smoke, pass ``--years 2`` (timing summary may be
noisy for very short runs).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_RUNNER = _ROOT / "utils" / "run_population_simulation.py"


def main() -> None:
    p = argparse.ArgumentParser(
        description="Benchmark run_population_simulation for two flush batch sizes."
    )
    p.add_argument(
        "--years",
        type=int,
        default=400,
        help="Simulation years passed to the runner (default: 400)",
    )
    p.add_argument(
        "--runner",
        type=Path,
        default=_DEFAULT_RUNNER,
        help=f"Path to run_population_simulation.py (default: {_DEFAULT_RUNNER})",
    )
    args = p.parse_args()
    years = int(args.years)
    runner = Path(args.runner).resolve()
    if not runner.is_file():
        raise SystemExit(f"Runner not found: {runner}")

    pairs = [
        ("50 (batched, default)", "50"),
        ("1 (yearly disk flush)", "1"),
    ]
    times: list[float] = []
    env_base = os.environ.copy()
    for label, batch in pairs:
        env = {**env_base, "SIM_STORE_FLUSH_BATCH_YEARS": batch}
        print(f"--- flush every {label} ---")
        r = subprocess.run(
            [sys.executable, str(runner), "--years", str(years)],
            cwd=_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        print(r.stdout.strip())
        if r.stderr:
            print(r.stderr, end="")
        sec = 0.0
        for line in r.stdout.splitlines():
            mo = re.search(r"\bwrote\s+.+\s+in\s+([0-9]+(?:\.[0-9]+)?)s\s*$", line.strip())
            if mo:
                sec = float(mo.group(1))
        times.append(sec)
    if len(times) == 2 and times[0] > 0 and times[1] > 0:
        print(
            f"Summary: batched={times[0]:.2f}s, yearly={times[1]:.2f}s, "
            f"ratio batched/yearly={times[0]/times[1]:.3f}"
        )


if __name__ == "__main__":
    main()
