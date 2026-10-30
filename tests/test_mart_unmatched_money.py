from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestMartUnmatchedMoney(TestCase):
    schema = "test_mart_unmatched_money"
    period_start = "2026-03-01"
    period_end = "2026-03-01"

    def test_store_level_expenses_are_excluded_from_unmatched_layer(self) -> None:
        sql = (Path(__file__).resolve().parents[1] / "src/marts/sql/mart_unmatched_money.sql").read_text(encoding="utf-8")
        self.assertIn("COALESCE(m.attribution_method, '') <> 'store_level'", sql)

    def test_promotion_duplicate_is_excluded_from_service_reconciliation(self) -> None:
        sql = (Path(__file__).resolve().parents[1] / "src/marts/sql/mart_money_layer_reconciliation.sql").read_text(encoding="utf-8")
        self.assertIn("NOT LIKE '%продвижени%'", sql)

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
                    cur.execute(f"CREATE SCHEMA {cls.schema}")

                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.fact_product_money_attribution (
                          marketplace TEXT,
                          canonical_product_key TEXT,
                          event_date DATE,
                          money_component_type TEXT,
                          money_bucket TEXT,
                          amount NUMERIC,
                          attribution_method TEXT,
                          attribution_confidence TEXT,
                          source_table TEXT,
                          source_record_id TEXT,
                          loaded_at TIMESTAMPTZ
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.ingestion_run (
                          run_id TEXT,
                          marketplace TEXT,
                          source TEXT,
                          schema_name TEXT,
                          mode TEXT,
                          date_from DATE,
                          date_to DATE,
                          started_at TIMESTAMPTZ,
                          finished_at TIMESTAMPTZ,
                          status TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.ingestion_completeness (
                          run_id TEXT,
                          marketplace TEXT,
                          source TEXT,
                          schema_name TEXT,
                          expected_days INTEGER,
                          actual_days INTEGER,
                          missing_days_count INTEGER,
                          coverage_ratio NUMERIC,
                          expected_entities INTEGER,
                          actual_entities INTEGER,
                          row_loss_ratio NUMERIC,
                          source_criticality TEXT,
                          usable_for_publish BOOLEAN,
                          error_type TEXT,
                          retry_exhausted BOOLEAN,
                          partial_reason TEXT,
                          status TEXT,
                          created_at TIMESTAMPTZ
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_finance (
                          finance_id TEXT,
                          operation_type TEXT,
                          order_id TEXT,
                          sale_id TEXT,
                          nm_id TEXT,
                          offer_id TEXT,
                          barcode TEXT,
                          raw_payload JSONB
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_ads (
                          date DATE,
                          campaign_id TEXT,
                          offer_id TEXT,
                          nm_id TEXT,
                          campaign_name TEXT,
                          spend NUMERIC,
                          raw_payload JSONB
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_transaction (
                          operation_id TEXT,
                          operation_type TEXT,
                          posting_number TEXT,
                          marketplace TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_transaction_item (
                          operation_id TEXT,
                          sku TEXT,
                          item_name TEXT,
                          marketplace TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_posting_item_financial (
                          posting_number TEXT,
                          product_id TEXT,
                          sku TEXT,
                          marketplace TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_posting_item (
                          posting_number TEXT,
                          product_id TEXT,
                          sku TEXT,
                          offer_id TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.dim_product_bridge (
                          bridge_id BIGSERIAL,
                          marketplace TEXT,
                          canonical_product_key TEXT,
                          offer_id TEXT,
                          sku TEXT,
                          product_id TEXT,
                          nm_id TEXT,
                          vendor_code TEXT,
                          barcode TEXT,
                          item_code TEXT,
                          product_name TEXT,
                          category_name TEXT,
                          brand TEXT,
                          match_confidence TEXT,
                          updated_at TIMESTAMPTZ
                        )
                        """
                    )

                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.dim_product_bridge (
                          marketplace, canonical_product_key, nm_id, offer_id, match_confidence, updated_at
                        ) VALUES ('wb', 'wb_cp_1', '1001', 'SKU-1', 'high', now())
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.stg_wb_finance (
                          finance_id, operation_type, order_id, sale_id, nm_id, offer_id, barcode, raw_payload
                        ) VALUES (
                          'fin-1', 'Комиссия', 'order-1', 'sale-1', '1001', 'SKU-1', 'BAR-1', '{{"k":"v"}}'::jsonb
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.fact_product_money_attribution (
                          marketplace, canonical_product_key, event_date, money_component_type, money_bucket,
                          amount, attribution_method, attribution_confidence, source_table, source_record_id, loaded_at
                        ) VALUES (
                          'wb', '~', '2026-03-01', 'expense', 'commission',
                          50, 'unmatched', 'low', 'stg_wb_finance', 'fin-1:commission:unmatched', now()
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.ingestion_run (
                          run_id, marketplace, source, schema_name, mode, date_from, date_to, started_at, finished_at, status
                        ) VALUES (
                          'run-1', 'wb', 'finance', '{cls.schema}', 'backfill', '2026-03-01', '2026-03-01', now(), now(), 'error'
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.ingestion_completeness (
                          run_id, marketplace, source, schema_name, expected_days, actual_days, missing_days_count,
                          coverage_ratio, expected_entities, actual_entities, row_loss_ratio, source_criticality,
                          usable_for_publish, error_type, retry_exhausted, partial_reason, status, created_at
                        ) VALUES (
                          'run-1', 'wb', 'finance', '{cls.schema}', 1, 0, 1,
                          0, 0, 0, 0, 'critical',
                          FALSE, 'rate_limit', TRUE, 'source_incomplete', 'error', now()
                        )
                        """
                    )

        params = {
            "schema": cls.schema,
            "period_start": cls.period_start,
            "period_end": cls.period_end,
        }
        with get_conn() as conn:
            with conn.transaction():
                execute_sql_file(conn, "src/marts/sql/mart_unmatched_money.sql", params)

    @classmethod
    def tearDownClass(cls) -> None:
        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
        super().tearDownClass()

    def test_candidate_mapping_and_stage(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      money_type,
                      match_status,
                      candidate_product_count,
                      most_likely_product_id,
                      most_likely_key_type,
                      incompleteness_stage,
                      unmatched_reason
                    FROM {self.schema}.fact_unmatched_money
                    """
                )
                row = cur.fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "commission")
        self.assertEqual(row[1], "unmatched_explainable")
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], "wb_cp_1")
        self.assertEqual(row[4], "nm_id")
        self.assertEqual(row[5], "source_gap")
        self.assertEqual(row[6], "source_gap")
