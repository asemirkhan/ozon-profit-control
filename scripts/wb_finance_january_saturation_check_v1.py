from __future__ import annotations

import csv
import hashlib
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.finance import _extract_finance_rows_from_payload, _extract_rrd_cursor
from src.connectors.wb.http import request_json_with_status
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result

MOSCOW = ZoneInfo("Europe/Moscow")
SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"

DAYS = [
    ("2026-01-26", "january_problem"),
    ("2026-01-27", "january_problem"),
    ("2026-02-20", "optional_control"),
]

OUTPUT_DIR = ROOT / "exports" / "wb_finance_january_saturation_check_v1"
DOC_PATH = ROOT / "artifacts" / "wb_finance_january_saturation_check_v1.md"
DAY_CSV = OUTPUT_DIR / "day_summary.csv"
PASS_CSV = OUTPUT_DIR / "pass_comparison.csv"
PAGE_CSV = OUTPUT_DIR / "page_metrics.csv"

LIMIT = int(os.getenv("WB_FINANCE_PAGE_LIMIT", "1000"))
INTER_REQUEST_PAUSE_SEC = 50
INTER_PASS_PAUSE_SEC = 120
POST_429_COOLING_SEC = 150
MAX_PASSES = 3
DAY_BUDGET_SEC = 20 * 60
BETWEEN_DAY_COOLDOWN_SEC = 180


@dataclass
class PageMetric:
    day: str
    label: str
    pass_number: int
    page_number: int
    request_no: int
    cursor_start: int
    rows_returned: int
    page_new_unique_rrd_id_count: int
    page_duplicate_rows_count: int
    min_rrd_id: int | None
    max_rrd_id: int | None
    http_status: int | None
    had_429: bool
    request_error_type: str | None
    elapsed_minutes: float


@dataclass
class PassMetric:
    day: str
    label: str
    pass_number: int
    rows_total: int
    new_unique_rrd_id_count: int
    duplicate_rows_count: int
    total_amount: str
    amount_delta_this_pass: str
    min_rrd_id: int | None
    max_rrd_id: int | None
    unique_rrd_id_hash: str
    growth_ratio_rows: float
    growth_ratio_amount: float
    duplicate_ratio: float
    requests_in_pass: int
    pass_time_minutes: float
    had_429: bool
    stop_reason: str


@dataclass
class DaySummary:
    day: str
    label: str
    pass1_rows: int
    pass2_rows: int
    pass3_rows: int
    pass1_amount: str
    pass2_amount: str
    pass3_amount: str
    new_rrd_id_after_pass1: int
    duplicate_rows_after_pass1: int
    amount_changed_after_pass1: bool
    range_changed_after_pass1: bool
    saturated_after_pass1: bool
    final_conclusion: str


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def configure_env() -> None:
    os.environ["WB_FINANCE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_HTTP_RETRY_DELAYS_SEC"] = "0"
    os.environ["WB_MAX_REQUESTS_PER_SECOND"] = "1"
    os.environ["WB_MAX_PARALLEL_REQUESTS_PER_SOURCE"] = "1"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_no data_"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(fmt(row.get(column)).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([head, sep, *body])


def decimal_to_str(value: Decimal) -> str:
    return f"{value:.2f}"


def growth_ratio_int(delta: int, previous_total: int) -> float:
    if previous_total <= 0:
        return 1.0 if delta > 0 else 0.0
    return delta / previous_total


def growth_ratio_amount(delta: Decimal, previous_total: Decimal) -> float:
    if previous_total == 0:
        return 1.0 if delta != 0 else 0.0
    return float(delta / abs(previous_total))


def duplicate_ratio(new_unique_rrd_id_count: int, duplicate_rows_count: int) -> float:
    total = new_unique_rrd_id_count + duplicate_rows_count
    if total <= 0:
        return 0.0
    return duplicate_rows_count / total


def unique_rrd_id_hash(rrd_ids: set[int]) -> str:
    encoded = ",".join(str(item) for item in sorted(rrd_ids)).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def fetch_page(day: str, cursor: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    diagnostics: list[dict[str, Any]] = []
    try:
        payload, status = request_json_with_status(
            method="GET",
            url=os.getenv("WB_FINANCE_URL", "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"),
            headers=get_wb_headers(),
            params={"dateFrom": day, "dateTo": day, "limit": LIMIT, "rrdid": cursor},
            timeout=30,
            diagnostics_collector=diagnostics.append,
            max_attempts_override=1,
            retry_delays_override=[],
            retry_after_buffer_sec=0,
            rate_api_category="finance",
            rate_endpoint="/api/v5/supplier/reportDetailByPeriod",
        )
    except Exception:
        status = int(diagnostics[-1].get("http_status") or 0) if diagnostics else None
        payload = []

    if isinstance(payload, dict):
        rows = _extract_finance_rows_from_payload(payload)
    elif isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
    else:
        rows = []
    return diagnostics, rows, status


def make_success_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"request_success": True, "response": {"finance": rows}}


def normalized_snapshot(day: str, successful_pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Decimal, set[int], int | None, int | None]:
    result = build_wb_finance_normalization_result(
        successful_pages,
        target_day=date.fromisoformat(day),
    )
    normalized_rows = result["rows"]
    total_amount = sum(((row.get("amount") or Decimal("0")) for row in normalized_rows), Decimal("0"))
    rrd_ids = {
        int(row["rrd_id"])
        for row in normalized_rows
        if row.get("rrd_id") is not None
    }
    min_rrd_id = min(rrd_ids) if rrd_ids else None
    max_rrd_id = max(rrd_ids) if rrd_ids else None
    return normalized_rows, total_amount, rrd_ids, min_rrd_id, max_rrd_id


def run_day(day: str, label: str) -> tuple[DaySummary, list[PassMetric], list[PageMetric]]:
    started = time.monotonic()
    request_no = 0
    known_rrd_ids: set[int] = set()
    successful_pages: list[dict[str, Any]] = []
    pass_metrics: list[PassMetric] = []
    page_metrics: list[PageMetric] = []

    previous_rows_total = 0
    previous_amount_total = Decimal("0")

    for pass_number in range(1, MAX_PASSES + 1):
        cursor = 0
        page_number = 0
        pass_request_count = 0
        pass_duplicate_rows = 0
        pass_had_429 = False
        pass_started = time.monotonic()
        stop_reason = "max_passes_reached"

        while True:
            if time.monotonic() - started >= DAY_BUDGET_SEC:
                stop_reason = "day_budget_exhausted"
                break

            page_number += 1
            request_no += 1
            pass_request_count += 1
            diagnostics, rows, status = fetch_page(day, cursor)
            if not diagnostics:
                diagnostics = [
                    {
                        "attempt_no": 1,
                        "http_status": status,
                        "error_type": None,
                    }
                ]

            raw_rrd_ids = [rrdid for row in rows if (rrdid := _extract_rrd_cursor(row)) is not None]
            unique_rrd_ids = set(raw_rrd_ids)
            known_before_page = set(known_rrd_ids)
            new_unique_ids = unique_rrd_ids - known_before_page
            page_duplicate_rows = sum(1 for rrdid in raw_rrd_ids if rrdid in known_before_page)
            min_rrd_id = min(unique_rrd_ids) if unique_rrd_ids else None
            max_rrd_id = max(unique_rrd_ids) if unique_rrd_ids else None

            had_429 = False
            request_error_type: str | None = None
            for attempt in diagnostics:
                http_status = int(attempt.get("http_status") or 0) if attempt.get("http_status") is not None else status
                request_error_type = str(attempt.get("error_type") or "") or None
                if http_status == 429 or request_error_type == "rate_limit":
                    had_429 = True
                    break

            if had_429:
                pass_had_429 = True
            else:
                successful_pages.append(make_success_page(rows))
                known_rrd_ids.update(new_unique_ids)

            pass_duplicate_rows += page_duplicate_rows
            page_metrics.append(
                PageMetric(
                    day=day,
                    label=label,
                    pass_number=pass_number,
                    page_number=page_number,
                    request_no=request_no,
                    cursor_start=cursor,
                    rows_returned=len(rows),
                    page_new_unique_rrd_id_count=len(new_unique_ids) if not had_429 else 0,
                    page_duplicate_rows_count=page_duplicate_rows,
                    min_rrd_id=min_rrd_id,
                    max_rrd_id=max_rrd_id,
                    http_status=int(diagnostics[-1].get("http_status") or 0) if diagnostics[-1].get("http_status") is not None else status,
                    had_429=had_429,
                    request_error_type=request_error_type,
                    elapsed_minutes=round((time.monotonic() - started) / 60.0, 2),
                )
            )

            if had_429:
                stop_reason = "rate_limit"
                break
            if len(rows) == 0:
                error_type = str(diagnostics[-1].get("error_type") or "") or ""
                if int(diagnostics[-1].get("http_status") or 0) == 204 or error_type.startswith("http_no_content"):
                    stop_reason = "http_no_content"
                else:
                    stop_reason = "empty_response"
                break
            if len(rows) < LIMIT:
                stop_reason = "short_terminal_page"
                break
            if max_rrd_id is None or max_rrd_id <= cursor:
                stop_reason = "cursor_stall"
                break

            cursor = max_rrd_id
            time.sleep(INTER_REQUEST_PAUSE_SEC)

        normalized_rows, amount_total, normalized_rrd_ids, min_rrd_id, max_rrd_id = normalized_snapshot(day, successful_pages)
        rows_total = len(normalized_rows)
        new_rows_total = rows_total - previous_rows_total
        amount_delta = amount_total - previous_amount_total

        pass_metrics.append(
            PassMetric(
                day=day,
                label=label,
                pass_number=pass_number,
                rows_total=rows_total,
                new_unique_rrd_id_count=new_rows_total,
                duplicate_rows_count=pass_duplicate_rows,
                total_amount=decimal_to_str(amount_total),
                amount_delta_this_pass=decimal_to_str(amount_delta),
                min_rrd_id=min_rrd_id,
                max_rrd_id=max_rrd_id,
                unique_rrd_id_hash=unique_rrd_id_hash(normalized_rrd_ids),
                growth_ratio_rows=growth_ratio_int(new_rows_total, previous_rows_total),
                growth_ratio_amount=growth_ratio_amount(amount_delta, previous_amount_total),
                duplicate_ratio=duplicate_ratio(new_rows_total, pass_duplicate_rows),
                requests_in_pass=pass_request_count,
                pass_time_minutes=round((time.monotonic() - pass_started) / 60.0, 2),
                had_429=pass_had_429,
                stop_reason=stop_reason,
            )
        )

        previous_rows_total = rows_total
        previous_amount_total = amount_total

        if pass_number < MAX_PASSES:
            if pass_had_429:
                time.sleep(POST_429_COOLING_SEC)
            else:
                time.sleep(INTER_PASS_PAUSE_SEC)

    pass1 = pass_metrics[0]
    pass2 = pass_metrics[1]
    pass3 = pass_metrics[2]
    new_rrd_id_after_pass1 = pass2.new_unique_rrd_id_count + pass3.new_unique_rrd_id_count
    duplicate_rows_after_pass1 = pass2.duplicate_rows_count + pass3.duplicate_rows_count
    amount_changed_after_pass1 = any(item.total_amount != pass1.total_amount for item in pass_metrics[1:])
    range_changed_after_pass1 = any(
        item.min_rrd_id != pass1.min_rrd_id or item.max_rrd_id != pass1.max_rrd_id
        for item in pass_metrics[1:]
    )
    saturated_after_pass1 = (
        new_rrd_id_after_pass1 == 0
        and not amount_changed_after_pass1
        and not range_changed_after_pass1
    )
    final_conclusion = (
        "saturated_after_pass1_replay_tail_only"
        if saturated_after_pass1
        else "not_saturated_after_pass1_real_undercollection"
    )
    summary = DaySummary(
        day=day,
        label=label,
        pass1_rows=pass1.rows_total,
        pass2_rows=pass2.rows_total,
        pass3_rows=pass3.rows_total,
        pass1_amount=pass1.total_amount,
        pass2_amount=pass2.total_amount,
        pass3_amount=pass3.total_amount,
        new_rrd_id_after_pass1=new_rrd_id_after_pass1,
        duplicate_rows_after_pass1=duplicate_rows_after_pass1,
        amount_changed_after_pass1=amount_changed_after_pass1,
        range_changed_after_pass1=range_changed_after_pass1,
        saturated_after_pass1=saturated_after_pass1,
        final_conclusion=final_conclusion,
    )
    return summary, pass_metrics, page_metrics


def build_report(day_rows: list[DaySummary], pass_rows: list[PassMetric]) -> str:
    january_rows = [row for row in day_rows if row.label == "january_problem"]
    saturated_january_days = sum(1 for row in january_rows if row.saturated_after_pass1)
    january_conclusion = (
        "january_bottleneck_false_replay_tail_after_effective_saturation"
        if january_rows and saturated_january_days == len(january_rows)
        else "january_bottleneck_real_undercollection"
    )

    lines = [
        "# WB Finance January Saturation Check v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Test Setup",
        "",
        "- current mode: restart each pass from day start",
        f"- inter-request pause: `{INTER_REQUEST_PAUSE_SEC}s`",
        f"- inter-pass pause: `{INTER_PASS_PAUSE_SEC}s`",
        f"- post-429 cooling: `{POST_429_COOLING_SEC}s`",
        f"- passes checked: `{MAX_PASSES}`",
        f"- day budget: `{DAY_BUDGET_SEC // 60}m`",
        "- cumulative totals use normalized WB finance rows for the target day and summed normalized `amount`.",
        "",
        "## January Saturation Report",
        "",
        md_table(
            [asdict(row) for row in day_rows],
            [
                "day",
                "label",
                "pass1_rows",
                "pass2_rows",
                "pass3_rows",
                "pass1_amount",
                "pass2_amount",
                "pass3_amount",
                "new_rrd_id_after_pass1",
                "duplicate_rows_after_pass1",
                "amount_changed_after_pass1",
                "range_changed_after_pass1",
                "saturated_after_pass1",
                "final_conclusion",
            ],
        ),
        "",
        "## Comparison Table",
        "",
        md_table(
            [asdict(row) for row in pass_rows],
            [
                "day",
                "pass_number",
                "rows_total",
                "total_amount",
                "new_unique_rrd_id_count",
                "duplicate_rows_count",
                "min_rrd_id",
                "max_rrd_id",
                "growth_ratio_rows",
                "growth_ratio_amount",
                "duplicate_ratio",
                "requests_in_pass",
                "pass_time_minutes",
                "had_429",
                "stop_reason",
            ],
        ),
        "",
        "## Final Conclusion",
        "",
        f"- overall january verdict: `{january_conclusion}`",
        f"- saturated january days: `{saturated_january_days}` / `{len(january_rows)}`",
        "- interpretation rule: if pass 2 and pass 3 add no new `rrd_id`, do not change normalized amount, and do not widen the `rrd_id` range, the day is treated as effectively saturated rather than undercollected.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    load_dotenv(ROOT / ".env")
    configure_env()

    day_rows: list[DaySummary] = []
    pass_rows: list[PassMetric] = []
    page_rows: list[PageMetric] = []

    for index, (day, label) in enumerate(DAYS, start=1):
        summary, pass_metrics, page_metrics = run_day(day, label)
        day_rows.append(summary)
        pass_rows.extend(pass_metrics)
        page_rows.extend(page_metrics)
        print(
            f"[{day}] saturated_after_pass1={summary.saturated_after_pass1} "
            f"new_rrd_after_pass1={summary.new_rrd_id_after_pass1} "
            f"amounts={summary.pass1_amount}->{summary.pass2_amount}->{summary.pass3_amount}",
            flush=True,
        )
        if index < len(DAYS):
            time.sleep(BETWEEN_DAY_COOLDOWN_SEC)

    write_csv(DAY_CSV, [asdict(row) for row in day_rows])
    write_csv(PASS_CSV, [asdict(row) for row in pass_rows])
    write_csv(PAGE_CSV, [asdict(row) for row in page_rows])
    DOC_PATH.write_text(build_report(day_rows, pass_rows), encoding="utf-8")

    print(f"Wrote {DAY_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {PASS_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {PAGE_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {DOC_PATH.relative_to(ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
