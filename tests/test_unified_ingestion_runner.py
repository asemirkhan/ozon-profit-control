from __future__ import annotations

from datetime import date
from unittest import TestCase

from src.ingestion.runner import _build_chunks, _filter_datasets_by_period
from src.ingestion.source_registry import get_source_config


class TestUnifiedIngestionRunner(TestCase):
    def test_build_chunks_splits_period(self) -> None:
        chunks = _build_chunks(
            date_from=date(2026, 2, 1),
            date_to=date(2026, 3, 11),
            chunk_days=31,
            chunk_by_calendar_month=False,
            force_single_chunk=False,
        )
        self.assertEqual(
            chunks,
            [
                (date(2026, 2, 1), date(2026, 3, 3)),
                (date(2026, 3, 4), date(2026, 3, 11)),
            ],
        )

    def test_build_chunks_calendar_month_mode(self) -> None:
        chunks = _build_chunks(
            date_from=date(2026, 2, 10),
            date_to=date(2026, 3, 11),
            chunk_days=31,
            chunk_by_calendar_month=True,
            force_single_chunk=False,
        )
        self.assertEqual(
            chunks,
            [
                (date(2026, 2, 10), date(2026, 2, 28)),
                (date(2026, 3, 1), date(2026, 3, 11)),
            ],
        )

    def test_filter_datasets_by_period(self) -> None:
        datasets = {
            "stg_wb_sale": [
                {"sale_id": "a", "sale_date": "2026-01-31T10:00:00Z"},
                {"sale_id": "b", "sale_date": "2026-02-05T10:00:00Z"},
                {"sale_id": "c", "sale_date": "2026-03-12T10:00:00Z"},
            ]
        }
        filtered = _filter_datasets_by_period(
            datasets=datasets,
            period_from=date(2026, 2, 1),
            period_to=date(2026, 3, 11),
            period_filter_fields={"stg_wb_sale": "sale_date"},
        )
        self.assertEqual([row["sale_id"] for row in filtered["stg_wb_sale"]], ["b"])

    def test_source_registry_has_required_sources(self) -> None:
        required = [
            ("wb", "products"),
            ("wb", "orders"),
            ("wb", "sales"),
            ("wb", "finance"),
            ("wb", "ads"),
            ("wb", "stock"),
            ("ozon", "products"),
            ("ozon", "postings"),
            ("ozon", "transactions"),
            ("ozon", "inventory"),
            ("ozon", "ads"),
        ]
        for marketplace, source in required:
            config = get_source_config(marketplace, source)
            self.assertEqual(config.marketplace, marketplace)
            self.assertEqual(config.source, source)
