from __future__ import annotations

from unittest import TestCase

from psycopg.types.json import Jsonb

from src.loaders.upsert_wb import _prepare_rows_for_db


class TestUpsertWb(TestCase):
    def test_prepare_rows_wraps_raw_payload_dict_and_list_as_jsonb(self) -> None:
        rows = [
            {"raw_payload": {"a": 1}, "nm_id": "1"},
            {"raw_payload": [1, 2, 3], "nm_id": "2"},
            {"raw_payload": "already_serialized", "nm_id": "3"},
        ]

        prepared = _prepare_rows_for_db(rows)

        self.assertIsInstance(prepared[0]["raw_payload"], Jsonb)
        self.assertIsInstance(prepared[1]["raw_payload"], Jsonb)
        self.assertEqual(prepared[2]["raw_payload"], "already_serialized")
        self.assertIsInstance(rows[0]["raw_payload"], dict)

    def test_prepare_rows_does_not_touch_non_jsonb_columns(self) -> None:
        rows = [
            {"raw_payload": {"a": 1}, "other_payload": {"k": "v"}, "nm_id": "1"},
        ]

        prepared = _prepare_rows_for_db(rows)

        self.assertIsInstance(prepared[0]["raw_payload"], Jsonb)
        self.assertEqual(prepared[0]["other_payload"], {"k": "v"})
