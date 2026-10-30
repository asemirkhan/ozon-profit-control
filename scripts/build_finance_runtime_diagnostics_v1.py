from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"
OUTPUT_DIR = ROOT / "exports" / "finance_runtime_diagnostics_v1"
FINANCE_STATE_CSV = OUTPUT_DIR / "finance_day_state_snapshot.csv"
SUMMARY_CSV = OUTPUT_DIR / "finance_runtime_summary.csv"

CLOSED_PERIOD_DAY_CSV = ROOT / "exports" / "wb_finance_canonical_closed_period_runner_v1" / "closed_period_day_results.csv"
LIVE_TAIL_DAY_CSV = ROOT / "exports" / "wb_finance_canonical_live_tail_runner_v1" / "live_tail_day_results.csv"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run_psql(query: str) -> list[list[str]]:
    cmd = [
        "docker",
        "exec",
        "-i",
        "ozon_postgres",
        "psql",
        "-U",
        "ozon",
        "-d",
        "ozon_db",
        "-At",
        "-F",
        ",",
        "-c",
        query,
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql failed")
    rows: list[list[str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        rows.append(line.split(","))
    return rows


def fetch_finance_day_state() -> list[dict[str, Any]]:
    query = f"""
        SELECT
            day::text,
            status,
            final_analytic_usable_flag::text,
            raw_presence_flag::text,
            stg_presence_flag::text,
            finance_day_complete_flag::text,
            attempts_total::text,
            pages_done_total::text,
            rows_done_total::text,
            http_429_count_total::text,
            cumulative_wait_sec::text,
            COALESCE(last_progress_at::text, ''),
            COALESCE(last_success_at::text, ''),
            updated_at::text
        FROM {SCHEMA}.finance_day_state
        ORDER BY day;
    """.strip()
    raw_rows = run_psql(query)
    columns = [
        "day",
        "status",
        "final_analytic_usable_flag",
        "raw_presence_flag",
        "stg_presence_flag",
        "finance_day_complete_flag",
        "attempts_total",
        "pages_done_total",
        "rows_done_total",
        "http_429_count_total",
        "cumulative_wait_sec",
        "last_progress_at",
        "last_success_at",
        "updated_at",
    ]
    return [dict(zip(columns, row, strict=False)) for row in raw_rows]


def as_float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def summarize(values: Iterable[float]) -> dict[str, float]:
    items = list(values)
    if not items:
        return {"count": 0.0, "avg": 0.0, "max": 0.0, "min": 0.0, "sum": 0.0}
    return {
        "count": float(len(items)),
        "avg": sum(items) / len(items),
        "max": max(items),
        "min": min(items),
        "sum": sum(items),
    }


def build_summary(day_state: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strict_days = [row for row in day_state if row.get("final_analytic_usable_flag") == "t"]
    total_days = len(day_state)
    strict_count = len(strict_days)
    http_429_total = sum(int(row.get("http_429_count_total") or 0) for row in day_state)
    attempts_avg = summarize(as_float(row.get("attempts_total")) for row in day_state)

    closed_days = load_csv(CLOSED_PERIOD_DAY_CSV)
    closed_durations = summarize(as_float(row.get("day_duration_sec")) for row in closed_days)
    closed_429 = sum(int(row.get("count_429") or 0) for row in closed_days)

    live_days = load_csv(LIVE_TAIL_DAY_CSV)
    live_strict = sum(1 for row in live_days if row.get("strict_close") in {"true", "True", "1"})

    return [
        {
            "schema": SCHEMA,
            "total_days": total_days,
            "strict_days": strict_count,
            "http_429_total": http_429_total,
            "attempts_avg": round(attempts_avg["avg"], 2),
            "attempts_max": round(attempts_avg["max"], 2),
            "closed_period_days": len(closed_days),
            "closed_period_429_total": closed_429,
            "closed_period_duration_avg_sec": round(closed_durations["avg"], 2),
            "closed_period_duration_max_sec": round(closed_durations["max"], 2),
            "live_tail_days": len(live_days),
            "live_tail_strict": live_strict,
        }
    ]


def main() -> None:
    try:
        day_state = fetch_finance_day_state()
    except Exception as exc:
        print(f"finance_state_error={exc}")
        day_state = []

    write_csv(FINANCE_STATE_CSV, day_state)
    summary = build_summary(day_state)
    write_csv(SUMMARY_CSV, summary)
    print(f"wrote={FINANCE_STATE_CSV}")
    print(f"wrote={SUMMARY_CSV}")


if __name__ == "__main__":
    main()
