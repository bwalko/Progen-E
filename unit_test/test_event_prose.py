"""Tests for deterministic event prose rendering."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.event_prose import (
    load_admin_event_summaries,
    load_event_record_prose_rows,
    load_public_chronicle_prose,
)
from library.world_save import (
    append_simulation_event_rows,
    ensure_checkpoint_schema,
    mark_event_record_lost,
    rediscover_event_record,
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


class TestEventProse(unittest.TestCase):
    def test_admin_summary_renders_factual_murder_details(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
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
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )

                summaries = load_admin_event_summaries(conn)

            self.assertEqual(len(summaries), 1)
            summary = summaries[0]
            self.assertEqual(summary.template_key, "admin.murder.default")
            self.assertIn("1010", summary.prose)
            self.assertIn("Mira Vale killed Eno Reed", summary.prose)
            self.assertIn("feud killing", summary.prose)
            self.assertIn("old grudge", summary.prose)
            self.assertIn("settlement 1 of Aeria North", summary.prose)

    def test_public_chronicle_filters_and_renders_visible_records(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                for pid, first, last in (
                    (1, "Tara", "Stone"),
                    (2, "Pell", "Ash"),
                    (3, "Ira", "Marsh"),
                    (4, "Lio", "Dawn"),
                ):
                    _insert_person(conn, pid, first, last)
                append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1001,
                            "paramour_formed",
                            {
                                "person_a_id": 1,
                                "person_b_id": 2,
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
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )

                rows = load_public_chronicle_prose(conn)

            by_type = {row.event_type: row for row in rows}
            self.assertNotIn("paramour_formed", by_type)
            self.assertEqual(by_type["property_crime"].visibility_state, "rumored")
            self.assertIn("Market talk", by_type["property_crime"].public_prose)
            self.assertIn("Tara Stone", by_type["property_crime"].public_prose)
            self.assertIn("storehouse robbery", by_type["property_crime"].public_prose)
            self.assertEqual(by_type["public_virtue"].visibility_state, "public_known")
            self.assertIn("chronicle", by_type["public_virtue"].public_prose)
            self.assertIn("Ira Marsh", by_type["public_virtue"].public_prose)
            self.assertIn("heroic rescue", by_type["public_virtue"].public_prose)

    def test_lost_and_rediscovered_records_get_state_specific_prose(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 10, "Lio", "Reed")
                _insert_person(conn, 41, "Sera", "Archivist")
                original_event_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            990,
                            "birth",
                            {
                                "person_id": 10,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )[0]

                mark_event_record_lost(conn, original_event_id, lost_year=1040)
                lost_rows = load_event_record_prose_rows(
                    conn, visibility_states={"lost"}
                )
                public_before = load_public_chronicle_prose(conn)

                rediscover_event_record(
                    conn,
                    original_event_id,
                    rediscovered_year=1100,
                    source_person_id=41,
                    source_institution_id="temple_ledger",
                    preserving_settlement_id="aeria_north:settlement:1",
                    confidence=0.82,
                )
                public_after = load_public_chronicle_prose(conn)

            self.assertEqual(len(lost_rows), 1)
            self.assertIn("No living chronicle preserved", lost_rows[0].public_prose)
            self.assertFalse(public_before)
            rediscovered_birth = next(
                row
                for row in public_after
                if row.event_id == original_event_id
                and row.visibility_state == "rediscovered"
            )
            self.assertIn("later hand recovered", rediscovered_birth.public_prose)
            self.assertIn("Lio Reed", rediscovered_birth.public_prose)
            rediscovery = next(
                row for row in public_after if row.event_type == "event_rediscovered"
            )
            self.assertIn(str(original_event_id), rediscovery.public_prose)


if __name__ == "__main__":
    unittest.main()
