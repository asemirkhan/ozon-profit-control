from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.sales import fetch_wb_sales_pages
from src.loaders.upsert_wb import upsert_stg_wb_sale
from src.normalizers.wb.sales_normalizer import normalize_wb_sales
from src.raw_store.writer import write_raw_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canonical WB sales bulk cumulative runner.")
    parser.add_argument("--schema", default=os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    return parser.parse_args()


def _sale_day(row: dict[str, object]) -> date | None:
    sale_dt = row.get("sale_date")
    if sale_dt is None:
        return None
    if hasattr(sale_dt, "date"):
        return sale_dt.date()  # type: ignore[return-value]
    return sale_dt if isinstance(sale_dt, date) else None


def _write_raw_pages(schema: str, run_id: str, pages: list[dict[str, object]]) -> None:
    for idx, page in enumerate(pages, start=1):
        payload = page.get("response")
        if not isinstance(payload, dict):
            payload = {"sales": []}
        write_raw_json(
            schema=schema,
            source="wb_sales",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=page.get("request_params") if isinstance(page.get("request_params"), dict) else None,
            http_status=page.get("http_status") if isinstance(page.get("http_status"), int) else None,
            meta_extra={
                "request_page_no": page.get("request_page_no"),
                "request_attempt_no": page.get("request_attempt_no"),
                "request_started_at": page.get("request_started_at"),
                "request_finished_at": page.get("request_finished_at"),
                "request_url": page.get("request_url"),
                "request_endpoint": page.get("request_endpoint"),
                "rate_limit_retry": page.get("rate_limit_retry"),
                "rate_limit_limit": page.get("rate_limit_limit"),
                "rate_limit_reset": page.get("rate_limit_reset"),
                "cooldown_wait_sec": page.get("cooldown_wait_sec"),
                "response_body_size": page.get("response_body_size"),
            },
            file_stem=f"{run_id}_p{idx:03d}",
        )


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to)
    if start > end:
        raise ValueError("invalid range")

    pages = fetch_wb_sales_pages(
        date_from=start.isoformat(),
        limit=100,
        max_pages=1,
        offset=0,
        pagination_mode="single",
    )

    run_id = f"wb_sales_bulk_{start.isoformat()}_{end.isoformat()}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    _write_raw_pages(args.schema, run_id, pages)

    normalized = normalize_wb_sales(pages)
    filtered = [row for row in normalized if (sale_day := _sale_day(row)) is not None and start <= sale_day <= end]
    loaded = upsert_stg_wb_sale(args.schema, filtered)

    day_counts = Counter()
    for row in filtered:
        sale_day = _sale_day(row)
        if sale_day is not None:
            day_counts[sale_day.isoformat()] += 1

    print(
        json.dumps(
            {
                "schema": args.schema,
                "mode": "bulk_cumulative",
                "date_from": start.isoformat(),
                "date_to": end.isoformat(),
                "raw_pages": len(pages),
                "normalized_rows": len(normalized),
                "filtered_rows": len(filtered),
                "loaded_rows": loaded,
                "days_loaded": dict(sorted(day_counts.items())),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
