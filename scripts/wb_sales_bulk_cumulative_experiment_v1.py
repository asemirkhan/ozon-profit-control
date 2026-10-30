from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status
from src.connectors.wb.sales import fetch_wb_sales_pages
from src.normalizers.wb.sales_normalizer import normalize_wb_sales
from src.utils.db import get_conn

load_dotenv(ROOT / ".env")

OUTPUT_BASE_DIR = ROOT / "exports" / "wb_sales_bulk_cumulative_experiment_v1"


@dataclass
class ModeResult:
    mode: str
    elapsed_sec: float
    request_count: int
    raw_rows: int
    filtered_rows: int
    distinct_sale_ids: int
    duplicate_rows_inside_result: int
    day_counts: dict[str, int]
    rate_limit_hits: int
    notes: str


def _daterange(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current = current.fromordinal(current.toordinal() + 1)
    return days


def _sale_day(row: dict[str, Any]) -> date | None:
    value = row.get("sale_date") or row.get("date") or row.get("saleDate")
    if value is None:
        return None
    if hasattr(value, "date"):
        return value.date()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _rows_metrics(rows: list[dict[str, Any]], *, start: date, end: date) -> tuple[int, int, dict[str, int]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        day = _sale_day(row)
        if day is not None and start <= day <= end:
            filtered.append(row)

    sale_ids = [
        str(row.get("sale_id") or row.get("saleID") or row.get("saleId") or "")
        for row in filtered
        if row.get("sale_id") is not None or row.get("saleID") is not None or row.get("saleId") is not None
    ]
    distinct_ids = len(set(sale_ids))
    duplicate_rows = max(len(sale_ids) - distinct_ids, 0)
    day_counts = Counter((_sale_day(row) or start).isoformat() for row in filtered)
    return len(filtered), distinct_ids, dict(sorted(day_counts.items()))


def _safe_mode(start: date, end: date) -> ModeResult:
    t0 = time.monotonic()
    raw_rows = 0
    filtered_rows = 0
    distinct_ids: set[str] = set()
    duplicate_rows = 0
    day_counts: Counter[str] = Counter()
    rate_limit_hits = 0
    request_count = 0

    for day in _daterange(start, end):
        pages = fetch_wb_sales_pages(
            date_from=day.isoformat(),
            limit=100,
            max_pages=100,
            offset=0,
            pagination_mode="offset",
        )
        request_count += len(pages)
        for page in pages:
            response = page.get("response") or {}
            sales_rows = response.get("sales") if isinstance(response, dict) else []
            if isinstance(sales_rows, list):
                raw_rows += len(sales_rows)
            if str(page.get("error_type") or "") == "rate_limit" or int(page.get("http_status") or 0) == 429:
                rate_limit_hits += 1

        rows = normalize_wb_sales(pages)
        for row in rows:
            sale_day = _sale_day(row)
            if sale_day != day:
                continue
            filtered_rows += 1
            sale_id = str(row.get("sale_id") or "")
            if sale_id in distinct_ids:
                duplicate_rows += 1
            elif sale_id:
                distinct_ids.add(sale_id)
            day_counts[day.isoformat()] += 1

    return ModeResult(
        mode="safe_day_by_day",
        elapsed_sec=round(time.monotonic() - t0, 2),
        request_count=request_count,
        raw_rows=raw_rows,
        filtered_rows=filtered_rows,
        distinct_sale_ids=len(distinct_ids),
        duplicate_rows_inside_result=duplicate_rows,
        day_counts=dict(sorted(day_counts.items())),
        rate_limit_hits=rate_limit_hits,
        notes="One request per day, canonical safe mode with local exact-day filter.",
    )


def _bulk_mode(start: date, end: date) -> tuple[ModeResult, dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    t0 = time.monotonic()
    parsed, status = request_json_with_status(
        method="GET",
        url="https://statistics-api.wildberries.ru/api/v1/supplier/sales",
        headers=get_wb_headers(),
        params={"dateFrom": start.isoformat(), "limit": 100, "offset": 0},
        diagnostics_collector=diagnostics.append,
        max_attempts_override=1,
        retry_delays_override=[],
        retry_after_buffer_sec=0,
        rate_api_category="sales",
        rate_endpoint="/api/v1/supplier/sales",
    )
    rows = parsed if isinstance(parsed, list) else []
    filtered_rows, distinct_sale_ids, day_counts = _rows_metrics(rows, start=start, end=end)
    sale_ids = [
        str(row.get("sale_id") or row.get("saleID") or row.get("saleId") or "")
        for row in rows
        if (
            _sale_day(row) is not None
            and start <= _sale_day(row) <= end
            and (
                row.get("sale_id") is not None
                or row.get("saleID") is not None
                or row.get("saleId") is not None
            )
        )
    ]
    duplicate_rows = max(len(sale_ids) - len(set(sale_ids)), 0)
    artifact = {
        "request_params": {"dateFrom": start.isoformat(), "limit": 100, "offset": 0},
        "http_status": status,
        "rows_total": len(rows),
        "rows_in_target_period": filtered_rows,
        "diagnostics": diagnostics,
        "response": rows,
    }
    result = ModeResult(
        mode="bulk_cumulative",
        elapsed_sec=round(time.monotonic() - t0, 2),
        request_count=1,
        raw_rows=len(rows),
        filtered_rows=filtered_rows,
        distinct_sale_ids=distinct_sale_ids,
        duplicate_rows_inside_result=duplicate_rows,
        day_counts=day_counts,
        rate_limit_hits=sum(
            1
            for item in diagnostics
            if str(item.get("error_type") or "") == "rate_limit" or int(item.get("http_status") or 0) == 429
        ),
        notes="One cumulative request from start date, then local day-range filter.",
    )
    return result, artifact


def _baseline_counts(start: date, end: date) -> dict[str, int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select sale_date::date as day, count(*)::int as rows
                from client_demo.stg_wb_sale
                where sale_date::date between %s and %s
                group by 1
                order by 1
                """,
                (start, end),
            )
            return {row[0].isoformat(): int(row[1]) for row in cur.fetchall()}


def _write_outputs(
    *,
    output_dir: Path,
    start: date,
    end: date,
    baseline: dict[str, int],
    safe: ModeResult,
    bulk: ModeResult,
    bulk_artifact: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = output_dir / "sales_mode_comparison.csv"
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "mode",
                "elapsed_sec",
                "request_count",
                "raw_rows",
                "filtered_rows",
                "distinct_sale_ids",
                "duplicate_rows_inside_result",
                "rate_limit_hits",
                "notes",
            ],
        )
        writer.writeheader()
        for result in (safe, bulk):
            writer.writerow(
                {
                    "mode": result.mode,
                    "elapsed_sec": result.elapsed_sec,
                    "request_count": result.request_count,
                    "raw_rows": result.raw_rows,
                    "filtered_rows": result.filtered_rows,
                    "distinct_sale_ids": result.distinct_sale_ids,
                    "duplicate_rows_inside_result": result.duplicate_rows_inside_result,
                    "rate_limit_hits": result.rate_limit_hits,
                    "notes": result.notes,
                }
            )

    day_path = output_dir / "sales_mode_day_counts.csv"
    all_days = sorted(set(baseline) | set(safe.day_counts) | set(bulk.day_counts))
    with day_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["day", "baseline_stg_rows", "safe_mode_rows", "bulk_mode_rows"],
        )
        writer.writeheader()
        for day in all_days:
            writer.writerow(
                {
                    "day": day,
                    "baseline_stg_rows": baseline.get(day, 0),
                    "safe_mode_rows": safe.day_counts.get(day, 0),
                    "bulk_mode_rows": bulk.day_counts.get(day, 0),
                }
            )

    (output_dir / "bulk_response_artifact.json").write_text(
        json.dumps(bulk_artifact, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    report_lines = [
        "# WB Sales Bulk Cumulative Experiment",
        "",
        f"- period: `{start.isoformat()}` .. `{end.isoformat()}`",
        f"- baseline_total_rows: `{sum(baseline.values())}`",
        "",
        "## Safe Mode",
        f"- elapsed_sec: `{safe.elapsed_sec}`",
        f"- request_count: `{safe.request_count}`",
        f"- filtered_rows: `{safe.filtered_rows}`",
        f"- distinct_sale_ids: `{safe.distinct_sale_ids}`",
        f"- duplicate_rows_inside_result: `{safe.duplicate_rows_inside_result}`",
        f"- rate_limit_hits: `{safe.rate_limit_hits}`",
        "",
        "## Bulk Mode",
        f"- elapsed_sec: `{bulk.elapsed_sec}`",
        f"- request_count: `{bulk.request_count}`",
        f"- filtered_rows: `{bulk.filtered_rows}`",
        f"- distinct_sale_ids: `{bulk.distinct_sale_ids}`",
        f"- duplicate_rows_inside_result: `{bulk.duplicate_rows_inside_result}`",
        f"- rate_limit_hits: `{bulk.rate_limit_hits}`",
        "",
        "## Verdict",
        f"- same_rows_as_baseline_safe: `{safe.filtered_rows == sum(baseline.values())}`",
        f"- same_rows_as_baseline_bulk: `{bulk.filtered_rows == sum(baseline.values())}`",
        f"- bulk_faster_than_safe: `{bulk.elapsed_sec < safe.elapsed_sec}`",
        f"- bulk_duplicate_free: `{bulk.duplicate_rows_inside_result == 0}`",
    ]
    (output_dir / "sales_bulk_cumulative_experiment_report.md").write_text(
        "\n".join(report_lines),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--preflight-sleep-sec", type=int, default=65)
    parser.add_argument("--label", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to)
    if start > end:
        raise ValueError("invalid range")
    label = (args.label or "").strip()
    output_dir = OUTPUT_BASE_DIR if not label else OUTPUT_BASE_DIR.parent / f"{OUTPUT_BASE_DIR.name}_{label}"
    baseline = _baseline_counts(start, end)

    if args.preflight_sleep_sec > 0:
        time.sleep(args.preflight_sleep_sec)
    safe = _safe_mode(start, end)

    if args.preflight_sleep_sec > 0:
        time.sleep(args.preflight_sleep_sec)
    bulk, bulk_artifact = _bulk_mode(start, end)

    _write_outputs(
        output_dir=output_dir,
        start=start,
        end=end,
        baseline=baseline,
        safe=safe,
        bulk=bulk,
        bulk_artifact=bulk_artifact,
    )
    print({"safe_elapsed_sec": safe.elapsed_sec, "bulk_elapsed_sec": bulk.elapsed_sec})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
