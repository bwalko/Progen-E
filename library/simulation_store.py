"""File-backed simulation store (DB-like tables) for run persistence."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from shutil import rmtree


@dataclass
class SimulationFileStore:
    """Persist simulation data to a flushed-per-run directory of CSV tables.

    Rows are staged in memory and written to disk in batches every
    ``flush_batch_years`` (default 50) simulation years. After each flush,
    staging buffers are cleared; full history remains in the CSV files on disk.
    """

    root_dir: Path
    flush_on_init: bool = True
    flush_batch_years: int = 50

    _people_rows: list[dict[str, object]] = field(default_factory=list)
    _events_rows: list[dict[str, object]] = field(default_factory=list)
    _people_updates_rows: list[dict[str, object]] = field(default_factory=list)
    _yearly_summary_rows: list[dict[str, object]] = field(default_factory=list)
    _current_people_rows: list[tuple[int, int]] = field(default_factory=list)
    _headers: dict[str, list[str]] = field(default_factory=dict)

    def initialize(self) -> None:
        if self.flush_on_init and self.root_dir.exists():
            rmtree(self.root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._headers.clear()
        self._clear_staging()
        self._ensure_table(
            "people.csv",
            [
                "person_id",
                "first_name",
                "last_name",
                "gender",
                "ethnic",
                "species",
                "birthplace",
                "birthplace_region_id",
                "birthplace_settlement_id",
                "birthyear",
                "deathyear",
                "is_founder",
                "father_id",
                "mother_id",
                "father_name",
                "mother_name",
            ],
        )
        self._ensure_table(
            "events.csv",
            [
                "year",
                "event_type",
                "person_id",
                "person_a_id",
                "person_b_id",
                "child_id",
                "from_region_id",
                "to_region_id",
                "cross_region",
                "move_reason",
                "details",
            ],
        )
        self._ensure_table(
            "yearly_summary.csv",
            [
                "year",
                "historical_year",
                "milestone_year",
                "alive_count",
                "dead_count",
                "detailed_alive_count",
                "passive_person_alive_count",
                "nondetailed_alive_count",
                "aggregate_cohort_alive_count",
                "aggregate_cohort_partnered_count",
                "mixed_mode_alive_count",
                "births_count",
                "deaths_count",
                "passive_cohort_births_count",
                "passive_cohort_deaths_count",
                "nondetailed_births_count",
                "nondetailed_deaths_count",
                "couples_count",
                "infant_mortality_pct",
                "under5_mortality_pct",
                "percent_reaching_age_100",
            ],
        )
        self._ensure_table("current_people.csv", ["year", "person_id"])
        self._ensure_table("people_updates.csv", ["year", "person_id", "field", "value"])

    def stage_person(self, row: dict[str, object]) -> None:
        self._people_rows.append(dict(row))

    def stage_event(self, row: dict[str, object]) -> None:
        self._events_rows.append(dict(row))

    def stage_yearly_summary(self, row: dict[str, object]) -> None:
        self._yearly_summary_rows.append(dict(row))

    def stage_people_update(self, row: dict[str, object]) -> None:
        self._people_updates_rows.append(dict(row))

    def stage_current_people_snapshot(self, *, year: int, person_ids: set[int]) -> None:
        for pid in sorted(person_ids):
            self._current_people_rows.append((year, pid))

    def maybe_flush_after_year(self, simulation_year: int, simulation_start_year: int) -> None:
        """Flush staged rows to disk every ``flush_batch_years`` completed years."""
        elapsed = int(simulation_year) - int(simulation_start_year) + 1
        if elapsed > 0 and elapsed % int(self.flush_batch_years) == 0:
            self.flush_to_disk()

    def finalize(self) -> None:
        """Write any remaining staged rows (call at end of simulation)."""
        if self._has_staged_data():
            self.flush_to_disk()

    def flush_to_disk(self) -> None:
        self._bulk_append_dict_rows("people.csv", self._people_rows)
        self._bulk_append_dict_rows("events.csv", self._events_rows)
        self._bulk_append_dict_rows("people_updates.csv", self._people_updates_rows)
        self._bulk_append_dict_rows("yearly_summary.csv", self._yearly_summary_rows)

        if self._current_people_rows:
            path = self.root_dir / "current_people.csv"
            with path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for y, pid in self._current_people_rows:
                    writer.writerow([y, pid])

        self._clear_staging()

    def append_person(self, row: dict[str, object]) -> None:
        self.stage_person(row)

    def append_event(self, row: dict[str, object]) -> None:
        self.stage_event(row)

    def append_yearly_summary(self, row: dict[str, object]) -> None:
        self.stage_yearly_summary(row)

    def append_people_update(self, row: dict[str, object]) -> None:
        self.stage_people_update(row)

    def write_current_people_snapshot(self, *, year: int, person_ids: set[int]) -> None:
        self.stage_current_people_snapshot(year=year, person_ids=person_ids)

    def _clear_staging(self) -> None:
        self._people_rows.clear()
        self._events_rows.clear()
        self._people_updates_rows.clear()
        self._yearly_summary_rows.clear()
        self._current_people_rows.clear()

    def _has_staged_data(self) -> bool:
        return bool(
            self._people_rows
            or self._events_rows
            or self._people_updates_rows
            or self._yearly_summary_rows
            or self._current_people_rows
        )

    def _ensure_table(self, filename: str, headers: list[str]) -> None:
        path = self.root_dir / filename
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as f:
                self._headers[filename] = next(csv.reader(f))
            return
        self._headers[filename] = list(headers)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

    def _bulk_append_dict_rows(self, filename: str, rows: list[dict[str, object]]) -> None:
        if not rows:
            return
        headers = self._headers.get(filename)
        if headers is None:
            path = self.root_dir / filename
            with path.open("r", newline="", encoding="utf-8") as f:
                headers = next(csv.reader(f))
            self._headers[filename] = headers
        path = self.root_dir / filename
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            for row in rows:
                writer.writerow({h: row.get(h, "") for h in headers})
