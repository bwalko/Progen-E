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
    load_public_known_prose,
    load_public_chronicle_prose,
    load_public_rumor_prose,
    load_public_unknown_prose,
)
from library.world_save import (
    append_simulation_event_rows,
    ensure_checkpoint_schema,
    mark_event_record_lost,
    rediscover_event_record,
    upsert_event_record,
    upsert_public_event_record,
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
            self.assertIn("Tara Stone", by_type["property_crime"].public_prose)
            self.assertIn("Pell Ash", by_type["property_crime"].public_prose)
            self.assertIn("storehouse robbery", by_type["property_crime"].public_prose)
            self.assertEqual(by_type["public_virtue"].visibility_state, "public_known")
            self.assertIn("Ira Marsh", by_type["public_virtue"].public_prose)
            self.assertIn("Lio Dawn", by_type["public_virtue"].public_prose)
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
            self.assertIn("Lio Reed", lost_rows[0].public_prose)
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

    def test_one_factual_event_can_have_unknown_rumored_and_known_public_views(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 1, "Fred", "Vale")
                _insert_person(conn, 2, "Lio", "Reed")
                murder_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1010,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "incident_kind": "murder",
                                "motive": "inheritance_plot",
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )[0]
                upsert_public_event_record(
                    conn,
                    murder_id,
                    public_stage="unknown",
                    record_key="default",
                    record_type="missing_person_notice",
                    confidence=0.2,
                    public_victim_person_id=2,
                    distortion={
                        "public_unknown_summary": "{place}: Lio Reed went missing in {year}."
                    },
                )
                upsert_public_event_record(
                    conn,
                    murder_id,
                    public_stage="rumored",
                    record_key="monster_rumor",
                    confidence=0.35,
                    public_victim_person_id=2,
                    distortion={"rumored_cause": "taken by a monster"},
                )
                upsert_public_event_record(
                    conn,
                    murder_id,
                    public_stage="known",
                    record_key="court_truth",
                    record_type="violent_crime_record",
                    confidence=0.95,
                    public_actor_person_id=1,
                    public_victim_person_id=2,
                )

                unknown_rows = load_public_unknown_prose(conn)
                rumor_rows = load_public_rumor_prose(conn)
                known_rows = load_public_known_prose(conn)
                chronicle_rows = load_public_chronicle_prose(conn)
                admin_rows = load_admin_event_summaries(conn)

            self.assertEqual([row.public_knowledge_stage for row in unknown_rows], ["unknown"])
            self.assertIn("went missing", unknown_rows[0].public_prose)
            self.assertEqual([row.public_knowledge_stage for row in rumor_rows], ["rumored"])
            self.assertIn("taken by a monster", rumor_rows[0].public_prose)
            self.assertEqual([row.public_knowledge_stage for row in known_rows], ["known"])
            self.assertIn("Fred Vale", known_rows[0].public_prose)
            self.assertIn("Lio Reed", known_rows[0].public_prose)
            self.assertEqual(
                [row.public_knowledge_stage for row in chronicle_rows],
                ["unknown", "rumored", "known"],
            )
            self.assertIn("Fred Vale killed Lio Reed", admin_rows[0].prose)

    def test_misattributed_public_record_renders_false_public_account(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 1, "Fred", "Vale")
                _insert_person(conn, 2, "Lio", "Reed")
                _insert_person(conn, 3, "Nora", "Mist")
                murder_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1010,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "incident_kind": "murder",
                                "motive": "inheritance_plot",
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )[0]
                upsert_public_event_record(
                    conn,
                    murder_id,
                    public_stage="misattributed",
                    record_key="false_accusation",
                    confidence=0.32,
                    public_actor_person_id=3,
                    public_victim_person_id=2,
                )

                rumor_rows = load_public_rumor_prose(conn)
                admin_rows = load_admin_event_summaries(conn)

            false_row = next(
                row for row in rumor_rows if row.visibility_state == "misattributed"
            )
            self.assertEqual(false_row.public_knowledge_stage, "rumored")
            self.assertIn("Nora Mist", false_row.public_prose)
            self.assertIn("Lio Reed", false_row.public_prose)
            self.assertIn("Fred Vale killed Lio Reed", admin_rows[0].prose)

    def test_active_incident_families_have_state_specific_payload_prose(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                for pid, first, last in (
                    (1, "Mira", "Vale"),
                    (2, "Lio", "Reed"),
                    (3, "Willa", "Hawk"),
                    (4, "Pell", "Ash"),
                    (5, "Ira", "Marsh"),
                    (6, "Tara", "Stone"),
                    (7, "Galen", "Sage"),
                    (8, "Nora", "Mist"),
                    (9, "Sera", "Archivist"),
                ):
                    _insert_person(conn, pid, first, last)
                event_ids = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1010,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "witness_person_ids": [3, 4],
                                "incident_kind": "feud_killing",
                                "motive": "old_grudge",
                                "historical_importance": 0.71,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1011,
                            "property_crime",
                            {
                                "perpetrator_person_id": 1,
                                "target_person_id": 2,
                                "witness_person_ids": [3],
                                "incident_kind": "storehouse_robbery",
                                "motive": "scarcity",
                                "loss_value": 0.19,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1012,
                            "affair_scandal",
                            {
                                "accused_person_id": 1,
                                "paramour_person_id": 5,
                                "betrayed_partner_person_ids": [2],
                                "witness_person_ids": [3],
                                "incident_kind": "inheritance_scandal",
                                "motive": "desire",
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1013,
                            "public_virtue",
                            {
                                "benefactor_person_id": 6,
                                "beneficiary_person_id": 2,
                                "witness_person_ids": [3],
                                "incident_kind": "famine_mercy",
                                "motive": "household_support",
                                "relief_value": 0.22,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1014,
                            "knowledge_culture",
                            {
                                "creator_person_id": 6,
                                "patron_person_id": 7,
                                "witness_person_ids": [3, 4],
                                "incident_kind": "innovation",
                                "innovation_analogue_name": "kiln_glazes",
                                "knowledge_domain": "materials",
                                "motive": "experimentation",
                                "novelty_value": 0.14,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                event_info = {
                    event_ids[0]: ("murder", 1, 2, "Lio Reed"),
                    event_ids[1]: ("property_crime", 1, 2, "Lio Reed"),
                    event_ids[2]: ("affair_scandal", 1, 5, "inheritance scandal"),
                    event_ids[3]: ("public_virtue", 6, 2, "Lio Reed"),
                    event_ids[4]: ("knowledge_culture", 6, None, "kiln glazes"),
                }
                for event_id, (event_type, actor_id, victim_id, _anchor) in event_info.items():
                    record_type = f"{event_type}_record"
                    upsert_public_event_record(
                        conn,
                        event_id,
                        public_stage="unknown",
                        record_key="w5_unknown",
                        record_type=record_type,
                        confidence=0.25,
                        public_victim_person_id=victim_id,
                    )
                    upsert_public_event_record(
                        conn,
                        event_id,
                        public_stage="rumored",
                        record_key="w5_rumor",
                        record_type=record_type,
                        confidence=0.45,
                        public_actor_person_id=actor_id,
                        public_victim_person_id=victim_id,
                    )
                    upsert_public_event_record(
                        conn,
                        event_id,
                        public_stage="known",
                        record_key="w5_known",
                        record_type=record_type,
                        confidence=0.9,
                        public_actor_person_id=actor_id,
                        public_victim_person_id=victim_id,
                    )
                    upsert_public_event_record(
                        conn,
                        event_id,
                        public_stage="misattributed",
                        record_key="w5_false",
                        record_type=record_type,
                        confidence=0.3,
                        public_actor_person_id=8,
                        public_victim_person_id=victim_id,
                    )
                    upsert_event_record(
                        conn,
                        event_id,
                        record_key="w5_lost",
                        record_type=record_type,
                        visibility_state="lost",
                        lost_year=1060,
                    )
                    upsert_event_record(
                        conn,
                        event_id,
                        record_key="w5_sealed",
                        record_type=record_type,
                        visibility_state="sealed",
                        source_institution_id="temple_ledger",
                    )
                    upsert_event_record(
                        conn,
                        event_id,
                        record_key="w5_rediscovered",
                        record_type=record_type,
                        visibility_state="rediscovered",
                        rediscovered_year=1110,
                        source_person_id=9,
                        preserving_settlement_id="aeria_north:settlement:1",
                    )

                rows = load_event_record_prose_rows(
                    conn,
                    event_types={info[0] for info in event_info.values()},
                    limit=500,
                )
                admin_text = "\n".join(
                    row.prose for row in load_admin_event_summaries(conn)
                )

            self.assertIn("Willa Hawk", admin_text)
            self.assertIn("Galen Sage", admin_text)
            required_states = {
                "public_unknown",
                "rumored",
                "misattributed",
                "public_known",
                "lost",
                "sealed",
                "rediscovered",
            }

            def text_for(event_id: int, state: str) -> str:
                return "\n".join(
                    row.public_prose
                    for row in rows
                    if row.event_id == event_id and row.visibility_state == state
                )

            for event_id, (_event_type, actor_id, _victim_id, anchor) in event_info.items():
                states = {
                    row.visibility_state for row in rows if row.event_id == event_id
                }
                self.assertTrue(required_states.issubset(states))
                self.assertIn(anchor, text_for(event_id, "public_unknown"))
                self.assertIn(anchor, text_for(event_id, "public_known"))
                self.assertIn(anchor, text_for(event_id, "lost"))
                self.assertIn(anchor, text_for(event_id, "sealed"))
                self.assertIn(anchor, text_for(event_id, "rediscovered"))
                self.assertIn("temple ledger", text_for(event_id, "sealed"))
                self.assertIn("Sera Archivist", text_for(event_id, "rediscovered"))
                self.assertIn(
                    "Nora Mist",
                    text_for(event_id, "misattributed"),
                )
                self.assertIn(
                    "Mira Vale" if actor_id == 1 else "Tara Stone",
                    text_for(event_id, "rumored"),
                )

    def test_default_public_uncertainty_prose_for_active_event_families(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            sav = Path(td) / "save.sqlite"
            with closing(sqlite3.connect(sav)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 11, "Lio", "Reed")
                _insert_person(conn, 12, "Mira", "Vale")
                _insert_person(conn, 13, "Nora", "Crown")
                append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1000,
                            "death",
                            {
                                "person_id": 11,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1001,
                            "settlement_moved",
                            {
                                "person_id": 12,
                                "from_settlement_id": "aeria_north:settlement:1",
                                "to_settlement_id": "boreas_port:settlement:1",
                                "from_region_id": "aeria_north",
                                "to_region_id": "boreas_port",
                                "move_reason": "resource_pressure_migration",
                            },
                        ),
                        (
                            1002,
                            "office_succession",
                            {
                                "holder_person_id": 13,
                                "previous_holder_id": 11,
                                "title_id": "thane",
                                "via": "hereditary",
                            },
                        ),
                        (
                            1003,
                            "campaign_started",
                            {"campaign_id": 9, "kind": "civil_war"},
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )

                unknown_rows = load_public_unknown_prose(conn)
                rumor_rows = load_public_rumor_prose(conn)
                known_rows = load_public_known_prose(conn)

            unknown_text = "\n".join(row.public_prose for row in unknown_rows)
            rumor_text = "\n".join(row.public_prose for row in rumor_rows)
            known_text = "\n".join(row.public_prose for row in known_rows)
            self.assertIn("Lio Reed had died", unknown_text)
            self.assertIn("but not the cause", unknown_text)
            self.assertIn("Mira Vale moved", unknown_text)
            self.assertIn("route or cause", unknown_text)
            self.assertIn("thane changed hands", unknown_text)
            self.assertIn("war news", unknown_text)
            self.assertIn("resource pressure migration", rumor_text)
            self.assertIn("Nora Crown's office", rumor_text)
            self.assertIn("civil war", rumor_text)
            self.assertIn("named Nora Crown to thane", known_text)


if __name__ == "__main__":
    unittest.main()
