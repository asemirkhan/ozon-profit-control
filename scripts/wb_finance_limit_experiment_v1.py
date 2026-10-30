from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.finance import WbFinanceFetchError, fetch_wb_finance_pages
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.services.wb_finance_canonical_method_v1 import CLOSED_PERIOD
from src.utils.rate_limit_coordinator import get_rate_limit_coordinator

OUTPUT_BASE = ROOT / "exports" / "wb_finance_limit_experiment_v1"


@dataclass(frozen=True)
class Baseline:
    rows: int
    distinct_rrd: int
    min_rrd: int | None
    max_rrd: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Controlled finance page-size experiment.")
    parser.add_argument("--schema", default=os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo")
    parser.add_argument("--day", required=True)
    parser.add_argument("--limits", nargs="+", type=int, required=True)
    parser.add_argument("--between-runs-sec", type=int, default=75)
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


def _baseline(schema: str, day_value: date) -> Baseline:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                COUNT(*)::int,
                COUNT(DISTINCT rrd_id)::int,
                MIN(rrd_id)::bigint,
                MAX(rrd_id)::bigint
            FROM {schema}.stg_wb_finance
            WHERE operation_date::date = %s
            """,
            (day_value,),
        )
        row = cur.fetchone()
    return Baseline(
        rows=int(row[0] or 0),
        distinct_rrd=int(row[1] or 0),
        min_rrd=int(row[2]) if row[2] is not None else None,
        max_rrd=int(row[3]) if row[3] is not None else None,
    )


def _apply_closed_env() -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    for key, value in CLOSED_PERIOD.env.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    os.environ["WB_FINANCE_STABLE_MODE"] = "0"
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _run_probe(day_text: str, limit: int) -> dict[str, object]:
    previous = _apply_closed_env()
    get_rate_limit_coordinator()._states.clear()  # type: ignore[attr-defined]
    started = time.time()
    try:
        try:
            pages = fetch_wb_finance_pages(day_text, day_text, limit=limit, max_pages=100, offset=0)
            error_type = ""
        except WbFinanceFetchError as exc:
            pages = exc.diagnostic_pages
            error_type = exc.error_type or "fetch_error"
        elapsed = round(time.time() - started, 2)
    finally:
        _restore_env(previous)

    norm = build_wb_finance_normalization_result(pages, target_day=date.fromisoformat(day_text))
    rows = norm["rows"]
    rrd_ids = sorted({int(row["rrd_id"]) for row in rows if row.get("rrd_id") is not None})
    statuses = Counter(int(page.get("http_status") or 0) for page in pages)
    retries = [
        int(page.get("rate_limit_retry") or 0)
        for page in pages
        if page.get("rate_limit_retry") not in (None, "")
    ]
    resets = [
        int(page.get("rate_limit_reset") or 0)
        for page in pages
        if page.get("rate_limit_reset") not in (None, "")
    ]
    limits = [
        int(page.get("rate_limit_limit") or 0)
        for page in pages
        if page.get("rate_limit_limit") not in (None, "")
    ]
    return {
        "limit": limit,
        "elapsed_sec": elapsed,
        "request_count": len(pages),
        "status_200": statuses.get(200, 0),
        "status_204": statuses.get(204, 0),
        "status_429": statuses.get(429, 0),
        "error_type": error_type,
        "page_rows": "|".join(str(int(page.get("finance_rows_in_response") or 0)) for page in pages),
        "normalized_rows": len(rows),
        "distinct_rrd": len(rrd_ids),
        "min_rrd": rrd_ids[0] if rrd_ids else None,
        "max_rrd": rrd_ids[-1] if rrd_ids else None,
        "terminal_reason": next(
            (str(page.get("finance_terminal_reason")) for page in reversed(pages) if page.get("finance_terminal_reason")),
            "",
        ),
        "retry_headers": "|".join(str(value) for value in retries),
        "reset_headers": "|".join(str(value) for value in resets),
        "limit_headers": "|".join(str(value) for value in limits),
    }


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    day_value = date.fromisoformat(args.day)
    label = f"_{args.label.strip()}" if args.label.strip() else ""
    output_dir = OUTPUT_BASE.parent / f"{OUTPUT_BASE.name}{label}_{day_value.isoformat()}"
    output_dir.mkdir(parents=True, exist_ok=True)

    baseline = _baseline(args.schema, day_value)
    rows: list[dict[str, object]] = []
    for idx, limit in enumerate(args.limits, start=1):
        result = _run_probe(day_value.isoformat(), limit)
        result["baseline_rows"] = baseline.rows
        result["baseline_distinct_rrd"] = baseline.distinct_rrd
        result["baseline_min_rrd"] = baseline.min_rrd
        result["baseline_max_rrd"] = baseline.max_rrd
        result["exact_match"] = (
            int(result["normalized_rows"] or 0) == baseline.rows
            and int(result["distinct_rrd"] or 0) == baseline.distinct_rrd
            and result["min_rrd"] == baseline.min_rrd
            and result["max_rrd"] == baseline.max_rrd
        )
        rows.append(result)
        if idx < len(args.limits) and args.between_runs_sec > 0:
            time.sleep(args.between_runs_sec)

    csv_path = output_dir / "finance_limit_results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "limit",
                "elapsed_sec",
                "request_count",
                "status_200",
                "status_204",
                "status_429",
                "error_type",
                "page_rows",
                "normalized_rows",
                "distinct_rrd",
                "min_rrd",
                "max_rrd",
                "baseline_rows",
                "baseline_distinct_rrd",
                "baseline_min_rrd",
                "baseline_max_rrd",
                "exact_match",
                "terminal_reason",
                "retry_headers",
                "reset_headers",
                "limit_headers",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    report_lines = [
        "# Finance Limit Experiment",
        "",
        f"- schema: `{args.schema}`",
        f"- day: `{day_value.isoformat()}`",
        f"- baseline_rows: `{baseline.rows}`",
        f"- baseline_distinct_rrd: `{baseline.distinct_rrd}`",
        f"- baseline_rrd_range: `{baseline.min_rrd} .. {baseline.max_rrd}`",
        "",
        "## Results",
    ]
    for row in rows:
        report_lines.extend(
            [
                f"- limit `{row['limit']}`: elapsed `{row['elapsed_sec']}` sec, requests `{row['request_count']}`, "
                f"200/204/429 = `{row['status_200']}/{row['status_204']}/{row['status_429']}`, "
                f"rows `{row['normalized_rows']}`, exact_match `{row['exact_match']}`",
                f"  page_rows: `{row['page_rows']}`",
                f"  terminal_reason: `{row['terminal_reason'] or '-'}; error_type: {row['error_type'] or '-'}'",
            ]
        )
    (output_dir / "finance_limit_experiment_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(csv_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
