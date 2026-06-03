"""Tests for event-history tuning reports."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.event_history_report import (
    build_event_history_report,
    format_event_history_summary,
    write_event_history_report,
)
from library.world_save import (
    append_simulation_event_rows,
    ensure_checkpoint_schema,
    mark_event_record_lost,
)


def _insert_person(
    conn: sqlite3.Connection, person_id: int, first_name: str, last_name: str
) -> None:
    conn.execute(
        """
        INSERT INTO simulation_people (
            person_id, is_founder, is_alive, first_name, last_name,
            gender, ethnic, species, birthyear, person_json
        )
        VALUES (?, 1, 1, ?, ?, 'female', 'human', 'human', 970, '{}')
        """,
        (int(person_id), first_name, last_name),
    )


class TestEventHistoryReport(unittest.TestCase):
    def test_build_report_counts_visibility_metrics_and_samples(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            save = root / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 1, "Tara", "Stone")
                _insert_person(conn, 2, "Pell", "Ash")
                _insert_person(conn, 3, "Ira", "Marsh")
                _insert_person(conn, 4, "Lio", "Dawn")
                birth_id, _crime_id, _virtue_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1001,
                            "birth",
                            {
                                "person_id": 4,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1002,
                            "property_crime",
                            {
                                "perpetrator_person_id": 1,
                                "target_person_id": 2,
                                "incident_kind": "storehouse_robbery",
                                "motive": "scarcity",
                                "loss_value": 0.18,
                                "resource_pressure": 1.2,
                                "historical_importance": 0.31,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1003,
                            "public_virtue",
                            {
                                "benefactor_person_id": 3,
                                "beneficiary_person_id": 4,
                                "incident_kind": "heroic_rescue",
                                "motive": "mercy",
                                "relief_value": 0.12,
                                "resource_pressure": 0.7,
                                "historical_importance": 0.42,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                mark_event_record_lost(conn, birth_id, lost_year=1040)
                conn.commit()

                report = build_event_history_report(
                    conn,
                    save_path=save,
                    sample_limit=10,
                    sample_event_types={"property_crime", "public_virtue"},
                )

            self.assertEqual(report.total_events, 3)
            self.assertEqual(report.total_records, 3)
            self.assertGreater(report.save_size_bytes or 0, 0)
            type_counts = {row.keys[0]: row.count for row in report.event_counts_by_type}
            self.assertEqual(type_counts["birth"], 1)
            self.assertEqual(type_counts["property_crime"], 1)
            visibility = {row.keys: row.count for row in report.visibility_counts}
            self.assertEqual(
                visibility[("birth", "lineage_memory", "lost")],
                1,
            )
            self.assertEqual(
                visibility[("property_crime", "property_crime_record", "rumored")],
                1,
            )
            metrics = {
                (row.event_type, row.metric): row
                for row in report.metric_summaries
            }
            self.assertAlmostEqual(
                metrics[("property_crime", "resource_pressure")].average,
                1.2,
            )
            self.assertAlmostEqual(
                metrics[("public_virtue", "historical_importance")].average,
                0.42,
            )
            sample_text = "\n".join(row.public_prose for row in report.public_samples)
            self.assertIn("Market talk", sample_text)
            self.assertIn("chronicle", sample_text)
            summary = format_event_history_summary(report)
            self.assertIn("total_events: 3", summary)
            self.assertIn("property_crime", summary)

    def test_write_report_outputs_tsv_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            save = root / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 1, "Mira", "Vale")
                _insert_person(conn, 2, "Eno", "Reed")
                append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1010,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "incident_kind": "feud_killing",
                                "motive": "old_grudge",
                                "historical_importance": 0.72,
                                "resource_pressure": 1.1,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                conn.commit()
                report = build_event_history_report(conn, save_path=save, sample_limit=5)

            out = root / "report"
            write_event_history_report(report, out)

            self.assertTrue((out / "summary.txt").exists())
            self.assertTrue((out / "event_counts_by_type.tsv").exists())
            self.assertTrue((out / "event_visibility_counts.tsv").exists())
            self.assertTrue((out / "tracked_incident_counts.tsv").exists())
            self.assertTrue((out / "event_metric_summaries.tsv").exists())
            self.assertTrue((out / "public_chronicle_samples.tsv").exists())
            self.assertIn(
                "murder",
                (out / "event_counts_by_type.tsv").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "feud killing",
                (out / "public_chronicle_samples.tsv").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "murder\t1",
                (out / "tracked_incident_counts.tsv").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
