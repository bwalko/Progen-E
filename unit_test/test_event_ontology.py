"""Tests for event ontology authoring rows."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.event_ontology import (
    clear_event_ontology_cache,
    event_ontology_entries,
    event_public_view_columns,
)


class TestEventOntology(unittest.TestCase):
    def tearDown(self) -> None:
        clear_event_ontology_cache()

    def test_authored_ontology_covers_initial_workstream_families(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            clear_event_ontology_cache()

            entries = event_ontology_entries(db_path=cfg)

        families = {entry.event_family for entry in entries}
        self.assertEqual(
            {
                "violent_crime",
                "property_survival_crime",
                "household_scandal",
                "political_crime",
                "religious_cultural_conflict",
                "status_mobility",
                "economy",
                "public_virtue",
                "knowledge_culture",
                "private_life",
            },
            families,
        )
        by_key = {entry.event_key: entry for entry in entries}
        murder_views = event_public_view_columns(by_key["murder"])
        self.assertIn("victim", murder_views["unknown"])
        self.assertIn("monster", murder_views["rumored"])
        self.assertIn("killer", murder_views["known"])
        self.assertIn("death", by_key["murder"].consequence_hooks)
        self.assertLess(by_key["murder"].importance_min, by_key["murder"].importance_max)
        self.assertIn("legal_fallout", by_key["inheritance_fraud"].consequence_hooks)
        self.assertIn("temple_chronicle", by_key["heresy_accusation"].default_record_type)

    def test_missing_ontology_table_uses_vertical_slice_fallback(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            sqlite3.connect(cfg).close()
            clear_event_ontology_cache()

            entries = event_ontology_entries(db_path=cfg)

        keys = {entry.event_key for entry in entries}
        self.assertEqual(
            {"murder", "theft", "affair_exposed", "rescue", "invention"},
            keys,
        )


if __name__ == "__main__":
    unittest.main()
