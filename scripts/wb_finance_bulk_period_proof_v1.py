from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.finance import WbFinanceFetchError, fetch_wb_finance_pages
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.raw_store.writer import write_raw_json
from src.utils.rate_limit_coordinator import get_rate_limit_coordinator


@dataclass(frozen=True)
class LayerStats:
    row_count: int
    distinct_days: int
    min_day: date | None
    max_day: date | None
    max_rrd_id: int | None
    amount_sum: Decimal
    payout_sum: Decimal
    commission_sum: Decimal
    logistics_sum: Decimal
    service_sum: Decimal
    day_counts: dict[str, int]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk finance proof for reportDetailByPeriod.")
    parser.add_argument("--schema", default=os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--label", default="")
    return parser.parse_args()


def _connect():
    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=os.environ["PGPORT"],
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )


def _sum_decimal(rows: list[dict[str, Any]], field: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        value = row.get(field)
        if isinstance(value, Decimal):
            total += value
        elif value not in (None, ""):
            total += Decimal(str(value))
    return total


def _stats_from_rows(rows: list[dict[str, Any]]) -> LayerStats:
    day_counter: Counter[str] = Counter()
    max_rrd_id: int | None = None
    min_day: date | None = None
    max_day: date | None = None
    for row in rows:
        op_dt = row.get("operation_date")
        day_value = None
        if hasattr(op_dt, "date"):
            day_value = op_dt.date()
        elif isinstance(op_dt, date):
            day_value = op_dt
        if day_value is not None:
            iso = day_value.isoformat()
            day_counter[iso] += 1
            min_day = day_value if min_day is None or day_value < min_day else min_day
            max_day = day_value if max_day is None or day_value > max_day else max_day
        rrd_id = row.get("rrd_id")
        if rrd_id is not None:
            parsed = int(rrd_id)
            max_rrd_id = parsed if max_rrd_id is None or parsed > max_rrd_id else max_rrd_id
    return LayerStats(
        row_count=len(rows),
        distinct_days=len(day_counter),
        min_day=min_day,
        max_day=max_day,
        max_rrd_id=max_rrd_id,
        amount_sum=_sum_decimal(rows, "amount"),
        payout_sum=_sum_decimal(rows, "payout_amount"),
        commission_sum=_sum_decimal(rows, "commission_amount"),
        logistics_sum=_sum_decimal(rows, "logistics_amount"),
        service_sum=_sum_decimal(rows, "service_amount"),
        day_counts=dict(sorted(day_counter.items())),
    )


def _existing_stats(schema: str, start: date, end: date) -> LayerStats:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                COUNT(*)::int AS row_count,
                COUNT(DISTINCT operation_date::date)::int AS distinct_days,
                MIN(operation_date::date) AS min_day,
                MAX(operation_date::date) AS max_day,
                MAX(rrd_id)::bigint AS max_rrd_id,
                COALESCE(SUM(amount), 0)::numeric AS amount_sum,
                COALESCE(SUM(payout_amount), 0)::numeric AS payout_sum,
                COALESCE(SUM(commission_amount), 0)::numeric AS commission_sum,
                COALESCE(SUM(logistics_amount), 0)::numeric AS logistics_sum,
                COALESCE(SUM(service_amount), 0)::numeric AS service_sum
            FROM {schema}.stg_wb_finance
            WHERE operation_date::date BETWEEN %s AND %s
            """,
            (start, end),
        )
        row = cur.fetchone()
        cur.execute(
            f"""
            SELECT operation_date::date AS day, COUNT(*)::int AS row_count
            FROM {schema}.stg_wb_finance
            WHERE operation_date::date BETWEEN %s AND %s
            GROUP BY 1
            ORDER BY 1
            """,
            (start, end),
        )
        day_counts = {day.isoformat(): int(count) for day, count in cur.fetchall()}
    return LayerStats(
        row_count=int(row[0] or 0),
        distinct_days=int(row[1] or 0),
        min_day=row[2],
        max_day=row[3],
        max_rrd_id=int(row[4]) if row[4] is not None else None,
        amount_sum=Decimal(str(row[5] or 0)),
        payout_sum=Decimal(str(row[6] or 0)),
        commission_sum=Decimal(str(row[7] or 0)),
        logistics_sum=Decimal(str(row[8] or 0)),
        service_sum=Decimal(str(row[9] or 0)),
        day_counts=day_counts,
    )


def _write_raw_pages(schema: str, run_id: str, pages: list[dict[str, Any]]) -> None:
    for idx, page in enumerate(pages, start=1):
        payload = page.get("response")
        if not isinstance(payload, dict):
            payload = {"finance": []}
        write_raw_json(
            schema=schema,
            source="wb_finance",
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
                "response_body_size": page.get("response_body_size"),
                "finance_rows_in_response": page.get("finance_rows_in_response"),
                "finance_rrdid_request": page.get("finance_rrdid_request"),
                "finance_max_rrdid_in_response": page.get("finance_max_rrdid_in_response"),
                "finance_is_terminal_page": page.get("finance_is_terminal_page"),
                "finance_terminal_reason": page.get("finance_terminal_reason"),
            },
            file_stem=f"{run_id}_p{idx:03d}",
        )


def _write_day_compare_csv(path: Path, existing: LayerStats, bulk: LayerStats) -> None:
    all_days = sorted(set(existing.day_counts) | set(bulk.day_counts))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["day", "existing_rows", "bulk_rows", "match_yes_no"],
        )
        writer.writeheader()
        for day in all_days:
            existing_rows = int(existing.day_counts.get(day, 0))
            bulk_rows = int(bulk.day_counts.get(day, 0))
            writer.writerow(
                {
                    "day": day,
                    "existing_rows": existing_rows,
                    "bulk_rows": bulk_rows,
                    "match_yes_no": str(existing_rows == bulk_rows),
                }
            )


def _same_stats(existing: LayerStats, bulk: LayerStats) -> bool:
    return (
        existing.row_count == bulk.row_count
        and existing.distinct_days == bulk.distinct_days
        and existing.min_day == bulk.min_day
        and existing.max_day == bulk.max_day
        and existing.max_rrd_id == bulk.max_rrd_id
        and existing.amount_sum == bulk.amount_sum
        and existing.payout_sum == bulk.payout_sum
        and existing.commission_sum == bulk.commission_sum
        and existing.logistics_sum == bulk.logistics_sum
        and existing.service_sum == bulk.service_sum
        and existing.day_counts == bulk.day_counts
    )


def main() -> int:
    load_dotenv(ROOT / ".env")
    os.environ["WB_FINANCE_STABLE_MODE"] = "0"
    os.environ["WB_FINANCE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_HTTP_RETRY_DELAYS_SEC"] = "0"

    args = parse_args()
    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to)
    if start > end:
        raise ValueError("invalid range")

    label = (args.label or "").strip()
    suffix = f"_{label}" if label else ""
    output_dir = ROOT / "exports" / f"wb_finance_bulk_period_proof_v1{suffix}"
    output_dir.mkdir(parents=True, exist_ok=True)

    get_rate_limit_coordinator()._states.clear()  # type: ignore[attr-defined]
    started = time.time()
    try:
        pages = fetch_wb_finance_pages(
            date_from=start.isoformat(),
            date_to=end.isoformat(),
            limit=args.limit,
            max_pages=1000,
            offset=0,
        )
        error_type = ""
    except WbFinanceFetchError as exc:
        pages = exc.diagnostic_pages
        error_type = exc.error_type or "fetch_error"
    elapsed_sec = round(time.time() - started, 2)

    run_id = f"wb_finance_bulk_{start.isoformat()}_{end.isoformat()}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    _write_raw_pages(args.schema, run_id, pages)

    bulk_result = build_wb_finance_normalization_result(pages)
    bulk_rows = bulk_result["rows"]
    bulk_stats = _stats_from_rows(bulk_rows)
    existing_stats = _existing_stats(args.schema, start, end)

    got_204 = any(int(page.get("http_status") or 0) == 204 for page in pages)
    status_counter = Counter(int(page.get("http_status") or 0) for page in pages)
    page_rows = [int(page.get("finance_rows_in_response") or 0) for page in pages]
    last_rrd_requests = [int(page.get("finance_rrdid_request") or 0) for page in pages]
    last_rrd_responses = [
        int(page.get("finance_max_rrdid_in_response"))
        for page in pages
        if page.get("finance_max_rrdid_in_response") not in (None, "")
    ]

    summary = {
        "schema": args.schema,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "limit": args.limit,
        "elapsed_sec": elapsed_sec,
        "total_requests": len(pages),
        "total_rows": bulk_stats.row_count,
        "got_204_yes_no": str(got_204),
        "days_covered": bulk_stats.distinct_days,
        "matches_existing_yes_no": str(_same_stats(existing_stats, bulk_stats)),
        "status_200_count": int(status_counter.get(200, 0)),
        "status_204_count": int(status_counter.get(204, 0)),
        "status_429_count": int(status_counter.get(429, 0)),
        "error_type": error_type,
        "bulk_min_day": bulk_stats.min_day.isoformat() if bulk_stats.min_day else "",
        "bulk_max_day": bulk_stats.max_day.isoformat() if bulk_stats.max_day else "",
        "bulk_max_rrd_id": bulk_stats.max_rrd_id or "",
        "existing_min_day": existing_stats.min_day.isoformat() if existing_stats.min_day else "",
        "existing_max_day": existing_stats.max_day.isoformat() if existing_stats.max_day else "",
        "existing_max_rrd_id": existing_stats.max_rrd_id or "",
        "bulk_amount_sum": f"{bulk_stats.amount_sum:.2f}",
        "existing_amount_sum": f"{existing_stats.amount_sum:.2f}",
        "bulk_payout_sum": f"{bulk_stats.payout_sum:.2f}",
        "existing_payout_sum": f"{existing_stats.payout_sum:.2f}",
        "bulk_commission_sum": f"{bulk_stats.commission_sum:.2f}",
        "existing_commission_sum": f"{existing_stats.commission_sum:.2f}",
        "bulk_logistics_sum": f"{bulk_stats.logistics_sum:.2f}",
        "existing_logistics_sum": f"{existing_stats.logistics_sum:.2f}",
        "bulk_service_sum": f"{bulk_stats.service_sum:.2f}",
        "existing_service_sum": f"{existing_stats.service_sum:.2f}",
        "page_rows": "|".join(str(value) for value in page_rows),
        "rrdid_requests": "|".join(str(value) for value in last_rrd_requests),
        "rrdid_responses": "|".join(str(value) for value in last_rrd_responses),
    }

    with (output_dir / "bulk_finance_period_summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    with (output_dir / "bulk_finance_period_requests.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "request_no",
                "http_status",
                "rows_in_response",
                "rrdid_request",
                "max_rrdid_in_response",
                "is_terminal_page",
                "terminal_reason",
                "rate_limit_retry",
                "rate_limit_reset",
                "rate_limit_limit",
            ],
        )
        writer.writeheader()
        for idx, page in enumerate(pages, start=1):
            writer.writerow(
                {
                    "request_no": idx,
                    "http_status": int(page.get("http_status") or 0),
                    "rows_in_response": int(page.get("finance_rows_in_response") or 0),
                    "rrdid_request": int(page.get("finance_rrdid_request") or 0),
                    "max_rrdid_in_response": page.get("finance_max_rrdid_in_response") or "",
                    "is_terminal_page": bool(page.get("finance_is_terminal_page") or False),
                    "terminal_reason": page.get("finance_terminal_reason") or "",
                    "rate_limit_retry": page.get("rate_limit_retry") or "",
                    "rate_limit_reset": page.get("rate_limit_reset") or "",
                    "rate_limit_limit": page.get("rate_limit_limit") or "",
                }
            )

    _write_day_compare_csv(output_dir / "bulk_finance_day_compare.csv", existing_stats, bulk_stats)

    report_lines = [
        "# Finance Bulk Period Proof",
        "",
        f"- period: `{start.isoformat()} .. {end.isoformat()}`",
        f"- limit: `{args.limit}`",
        f"- elapsed_sec: `{elapsed_sec}`",
        f"- total_requests: `{len(pages)}`",
        f"- total_rows: `{bulk_stats.row_count}`",
        f"- got_204_yes_no: `{got_204}`",
        f"- days_covered: `{bulk_stats.distinct_days}`",
        f"- matches_existing_yes_no: `{_same_stats(existing_stats, bulk_stats)}`",
        "",
        "## Bulk vs Existing",
        f"- existing rows: `{existing_stats.row_count}`",
        f"- bulk rows: `{bulk_stats.row_count}`",
        f"- existing amount sum: `{existing_stats.amount_sum:.2f}`",
        f"- bulk amount sum: `{bulk_stats.amount_sum:.2f}`",
        f"- existing payout sum: `{existing_stats.payout_sum:.2f}`",
        f"- bulk payout sum: `{bulk_stats.payout_sum:.2f}`",
        f"- existing commission sum: `{existing_stats.commission_sum:.2f}`",
        f"- bulk commission sum: `{bulk_stats.commission_sum:.2f}`",
        f"- existing logistics sum: `{existing_stats.logistics_sum:.2f}`",
        f"- bulk logistics sum: `{bulk_stats.logistics_sum:.2f}`",
        f"- existing service sum: `{existing_stats.service_sum:.2f}`",
        f"- bulk service sum: `{bulk_stats.service_sum:.2f}`",
        f"- existing max rrd_id: `{existing_stats.max_rrd_id}`",
        f"- bulk max rrd_id: `{bulk_stats.max_rrd_id}`",
        "",
        "## Request Pattern",
        f"- status 200 count: `{status_counter.get(200, 0)}`",
        f"- status 204 count: `{status_counter.get(204, 0)}`",
        f"- status 429 count: `{status_counter.get(429, 0)}`",
        f"- page rows: `{summary['page_rows']}`",
        f"- rrdid requests: `{summary['rrdid_requests']}`",
        f"- rrdid responses: `{summary['rrdid_responses']}`",
        f"- error_type: `{error_type or '-'}'",
    ]
    (output_dir / "finance_bulk_period_proof_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
