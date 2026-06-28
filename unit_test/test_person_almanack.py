from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing

from library.person_almanack import (
    ALMANACK_SCHEMA_VERSION,
    metric_definition_choices,
    person_almanack_cache_status,
    query_person_almanack,
    query_person_almanack_duel,
    query_person_almanack_evidence,
    refresh_person_almanack,
)
from library.world_save import append_simulation_event_rows, ensure_checkpoint_schema


class TestPersonAlmanack(unittest.TestCase):
    def test_refresh_builds_initial_people_pattern_metrics(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            self._insert_people(conn)
            self._insert_passive_people(conn)
            event_ids = append_simulation_event_rows(
                conn,
                "default",
                [
                    (
                        1001,
                        "murder",
                        {
                            "killer_person_id": 1,
                            "victim_person_id": 2,
                            "incident_kind": "feud_killing",
                        },
                    ),
                    (
                        1002,
                        "murder",
                        {
                            "killer_person_id": 1,
                            "victim_person_id": 3,
                            "incident_kind": "predatory_murder",
                        },
                    ),
                    (
                        1003,
                        "property_crime",
                        {
                            "perpetrator_person_id": 2,
                            "target_person_id": 3,
                            "incident_kind": "theft",
                            "loss_value": 0.25,
                        },
                    ),
                    (
                        1004,
                        "property_crime",
                        {
                            "perpetrator_person_id": 2,
                            "target_person_id": 4,
                            "incident_kind": "fraud",
                            "loss_value": 0.75,
                        },
                    ),
                    (1005, "couple_formed", {"person_a_id": 1, "person_b_id": 2}),
                    (
                        1006,
                        "same_sex_couple_formed",
                        {"person_a_id": 1, "person_b_id": 3},
                    ),
                    (1007, "paramour_formed", {"person_a_id": 1, "person_b_id": 2}),
                    (1008, "paramour_formed", {"person_a_id": 1, "person_b_id": 3}),
                    (1009, "job_assigned", {"person_id": 1, "job": "farmer"}),
                    (1010, "job_assigned", {"person_id": 1, "job": "scribe"}),
                    (1011, "job_lost", {"person_id": 2, "old_job": "guard"}),
                    (
                        1012,
                        "settlement_moved",
                        {
                            "moved_person_id": 1,
                            "move_reason": "war_displacement",
                        },
                    ),
                ],
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO simulation_event_moves (
                    event_id, moved_person_id, move_reason
                )
                VALUES (?, 1, 'war_displacement')
                """,
                (event_ids[-1],),
            )
            conn.execute(
                """
                INSERT INTO simulation_office_holdings (
                    seat_id, holder_person_id, start_sim_year, end_sim_year, end_reason
                )
                VALUES (1, 1, 1010, NULL, NULL)
                """
            )
            conn.execute(
                """
                INSERT INTO simulation_legal_fallout (
                    source_event_id, fallout_key, fallout_type, status,
                    principal_person_id, opposing_person_id, severity,
                    start_year, details_json, created_at, updated_at
                )
                VALUES (?, 'inheritance:1', 'inheritance_dispute', 'active',
                        1, 2, 0.7, 1011, '{}',
                        '2026-01-01T00:00:00+00:00',
                        '2026-01-01T00:00:00+00:00')
                """,
                (event_ids[-1],),
            )

            count = refresh_person_almanack(conn, simulation_year=1020)

            self.assertGreaterEqual(count, 8)
            metrics = {
                (str(row["source_kind"]), int(row["person_id"]), str(row["metric_key"])): row
                for row in conn.execute(
                    """
                    SELECT *
                    FROM simulation_person_almanack_metrics
                    """
                )
            }
            self.assertEqual(
                metrics[("detailed", 1, "murders_committed")]["metric_count"],
                2,
            )
            self.assertEqual(
                metrics[("detailed", 2, "property_crimes_committed")]["metric_count"],
                2,
            )
            self.assertAlmostEqual(
                float(metrics[("detailed", 2, "property_loss_caused")]["metric_value"]),
                1.0,
            )
            self.assertEqual(
                metrics[("detailed", 1, "children_recorded")]["metric_count"],
                5,
            )
            self.assertEqual(
                metrics[("detailed", 1, "children_lost_young")]["metric_count"],
                1,
            )
            self.assertEqual(
                metrics[("passive", 100, "children_recorded")]["metric_count"],
                3,
            )
            self.assertEqual(
                metrics[("detailed", 1, "distinct_partners")]["metric_count"],
                2,
            )
            self.assertEqual(
                metrics[("detailed", 1, "distinct_paramours")]["metric_count"],
                2,
            )
            self.assertEqual(
                metrics[("detailed", 1, "distinct_jobs")]["metric_count"],
                2,
            )
            self.assertEqual(
                metrics[("detailed", 2, "job_losses")]["metric_count"],
                1,
            )
            self.assertEqual(
                metrics[("detailed", 1, "offices_held")]["metric_count"],
                1,
            )
            self.assertEqual(
                float(metrics[("detailed", 3, "age_at_death")]["metric_value"]),
                33.0,
            )
            self.assertIn(("detailed", 1, "crossroads_index"), metrics)
            self.assertIn(("detailed", 3, "property_crimes_suffered"), metrics)
            self.assertIn(("detailed", 3, "property_loss_suffered"), metrics)
            self.assertIn(("detailed", 4, "family_members_murdered"), metrics)
            self.assertIn(("detailed", 1, "children_lost_young"), metrics)
            self.assertIn(("detailed", 1, "children_across_distinct_partners"), metrics)
            self.assertIn(("detailed", 1, "largest_relationship_age_gap"), metrics)
            self.assertIn(("detailed", 1, "descendants_2g"), metrics)
            self.assertIn(("detailed", 1, "legal_entanglements"), metrics)
            self.assertIn(("detailed", 1, "displacements"), metrics)
            self.assertIsNotNone(metrics[("detailed", 1, "murders_committed")]["world_rank"])
            self.assertIsNotNone(metrics[("detailed", 1, "murders_committed")]["percentile"])

            definition_labels = [label for label, _key in metric_definition_choices(conn)]
            self.assertIn("Crossroads Index", definition_labels)
            self.assertIn("Job Losses", definition_labels)
            self.assertIn("Offices Held", definition_labels)
            self.assertIn("Age at Death", definition_labels)
            self.assertNotIn("Disasters Survived", definition_labels)
            self.assertNotIn("Single Strange Event Score", definition_labels)
            all_definition_labels = [
                label for label, _key in metric_definition_choices(conn, enabled_only=False)
            ]
            self.assertIn("Disasters Survived", all_definition_labels)

            top_murder = query_person_almanack(
                conn,
                metric_key="murders_committed",
                limit=1,
            )
            self.assertEqual(top_murder[0]["person_id"], 1)
            self.assertEqual(top_murder[0]["name"], "Ari Vale")
            self.assertIn("event", str(top_murder[0]["evidence_summary"]))
            self.assertEqual(top_murder[0]["world_rank"], 1)

            abnormal_murders = query_person_almanack(
                conn,
                metric_key="murders_committed",
                rank_mode="Era Abnormality",
                limit=5,
            )
            self.assertEqual(abnormal_murders[0]["person_id"], 1)

            passive_children = query_person_almanack(
                conn,
                metric_key="children_recorded",
                source_filter="Passive explicit",
                search="Mira",
                limit=5,
            )
            self.assertEqual(len(passive_children), 1)
            self.assertEqual(passive_children[0]["source_kind"], "passive")
            self.assertEqual(passive_children[0]["name"], "Mira Lowdetail")

            evidence = query_person_almanack_evidence(
                conn,
                "detailed",
                1,
                "murders_committed",
            )
            self.assertGreaterEqual(len(evidence), 2)
            self.assertEqual(evidence[0]["source_table"], "simulation_events")
            self.assertIn("killer", {str(row["role"]) for row in evidence})

            young_loss_evidence = query_person_almanack_evidence(
                conn,
                "detailed",
                1,
                "children_lost_young",
            )
            self.assertEqual(len(young_loss_evidence), 1)
            self.assertIn("Eli Vale died in 1010 at age 3", young_loss_evidence[0]["summary"])
            self.assertEqual(young_loss_evidence[0]["payload_path"], "deathyear")

            duel = query_person_almanack_duel(conn, 1, 2)
            self.assertEqual(duel["person_a"]["name"], "Ari Vale")
            self.assertEqual(duel["person_b"]["name"], "Bela Reed")
            self.assertTrue(duel["categories"])

            status = person_almanack_cache_status(conn)
            self.assertFalse(status["stale"])
            self.assertEqual(status["cache_schema_version"], ALMANACK_SCHEMA_VERSION)
            conn.execute(
                """
                UPDATE simulation_person_almanack_cache
                SET cache_schema_version = ?
                WHERE cache_key = 'default'
                """,
                (ALMANACK_SCHEMA_VERSION - 1,),
            )
            self.assertTrue(person_almanack_cache_status(conn)["stale"])
            self.assertEqual(refresh_person_almanack(conn, simulation_year=1020), count)
            append_simulation_event_rows(
                conn,
                "default",
                [(1012, "murder", {"killer_person_id": 2, "victim_person_id": 5})],
            )
            self.assertTrue(person_almanack_cache_status(conn)["stale"])

    def test_refresh_marks_empty_cache_fresh(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            append_simulation_event_rows(
                conn,
                "default",
                [
                    (
                        1000,
                        "festival",
                        {"detail": "not an Almanack metric"},
                    )
                ],
                created_at="2026-01-01T00:00:00+00:00",
            )
            conn.commit()

            self.assertEqual(refresh_person_almanack(conn, simulation_year=1000), 0)
            status = person_almanack_cache_status(conn)

            self.assertTrue(status["exists"])
            self.assertEqual(status["row_count"], 0)
            self.assertFalse(status["stale"])
            self.assertEqual(status["source_event_max_id"], status["current_event_max_id"])

    def _insert_people(self, conn: sqlite3.Connection) -> None:
        people = [
            (1, None, None, 1, "Ari", "Vale", 980, None, "farmer"),
            (2, None, None, 1, "Bela", "Reed", 981, None, "guard"),
            (3, None, None, 0, "Caro", "Ash", 982, 1015, "scribe"),
            (4, 1, 3, 1, "Dara", "Vale", 1005, None, "child"),
            (5, 1, 3, 0, "Eli", "Vale", 1007, 1010, "child"),
            (6, 1, 3, 1, "Fia", "Vale", 1008, None, "child"),
            (7, 1, 3, 0, "Gio", "Vale", 1009, 1049, "child"),
            (8, 1, 3, 0, "Hal", "Vale", 1010, None, "child"),
        ]
        conn.executemany(
            """
            INSERT INTO simulation_people (
                person_id, is_founder, father_id, mother_id, is_alive,
                first_name, last_name, gender, ethnic, species, birthyear,
                deathyear, job, person_json
            )
            VALUES (?, 0, ?, ?, ?, ?, ?, 'female', 'test', 'human', ?, ?, ?, ?)
            """,
            [
                (
                    person_id,
                    father_id,
                    mother_id,
                    is_alive,
                    first_name,
                    last_name,
                    birthyear,
                    deathyear,
                    job,
                    json.dumps({}, separators=(",", ":")),
                )
                for (
                    person_id,
                    father_id,
                    mother_id,
                    is_alive,
                    first_name,
                    last_name,
                    birthyear,
                    deathyear,
                    job,
                ) in people
            ],
        )

    def _insert_passive_people(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO simulation_people_light (
                person_id, name, birthyear, deathyear, is_alive, gender,
                species, ethnic, job_family, child_count,
                child_person_ids_json, child_birthyears_json
            )
            VALUES (
                100, 'Mira Lowdetail', 970, NULL, 1, 'female',
                'human', 'test', 'farm', 3, '[901,902,903]', '[990,992,995]'
            )
            """
        )


if __name__ == "__main__":
    unittest.main()
