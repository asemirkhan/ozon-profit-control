from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from psycopg import sql
from psycopg.rows import dict_row

from src.connectors.wb.ads import WB_ADS_STATS_URL
from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status
from src.utils.db import get_conn


SCHEMA = os.getenv("WB_SCHEMA", "client_demo")


@dataclass(frozen=True)
class StageResult:
    stage_name: str
    day: str
    ids_count: int
    campaign_ids: list[int]
    http_status: int | None
    raw_rows_count: int
    loaded_rows_count: int
    response_headers_saved: bool
    rate_limit_remaining: str | None
    rate_limit_retry: str | None
    rate_limit_reset: str | None
    verdict: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _select_day_and_ids() -> tuple[date, list[int]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                sql.SQL(
                    """
                    SELECT date, COUNT(*) AS rows
                    FROM {}.stg_wb_ads
                    GROUP BY 1
                    ORDER BY rows DESC, date ASC
                    LIMIT 1
                    """
                ).format(sql.Identifier(SCHEMA))
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("stg_wb_ads is empty; cannot choose a proven day")
            target_day = row["date"]

            cur.execute(
                sql.SQL(
                    """
                    SELECT DISTINCT campaign_id
                    FROM {}.stg_wb_ads
                    WHERE date = %s
                    ORDER BY campaign_id
                    """
                ).format(sql.Identifier(SCHEMA)),
                (target_day,),
            )
            ids = [int(r["campaign_id"]) for r in cur.fetchall() if r.get("campaign_id") is not None]
            if not ids:
                raise RuntimeError("No campaign_ids found for selected day in stg_wb_ads")
            return target_day, ids


def _run_fullstats(
    *,
    stage_name: str,
    day_value: date,
    campaign_ids: list[int],
    range_days: int = 1,
) -> tuple[StageResult, dict[str, Any]]:
    headers = get_wb_headers()
    params = {
        "ids": ",".join(str(cid) for cid in campaign_ids),
        "beginDate": day_value.isoformat(),
        "endDate": (day_value + timedelta(days=range_days - 1)).isoformat(),
    }
    diagnostics: list[dict[str, Any]] = []
    parsed: dict[str, Any] | list[Any] | None = None
    status: int | None = None
    error: str | None = None

    try:
        parsed, status = request_json_with_status(
            method="GET",
            url=WB_ADS_STATS_URL,
            headers=headers,
            params=params,
            diagnostics_collector=diagnostics.append,
            max_attempts_override=1,
            retry_delays_override=[],
            retry_after_buffer_sec=0,
            rate_api_category="ads",
            rate_endpoint="/adv/v3/fullstats",
        )
    except Exception as exc:  # noqa: BLE001 - want diagnostics from 429 too
        error = str(exc)

    diag = diagnostics[-1] if diagnostics else {}
    status = status or diag.get("http_status")
    response_headers = diag.get("response_headers") or {}
    rate_remaining = response_headers.get("X-Ratelimit-Remaining")
    rate_retry = diag.get("rate_limit_retry")
    rate_reset = diag.get("rate_limit_reset")

    raw_rows = 0
    if status == 200 and parsed is not None:
        if isinstance(parsed, list):
            raw_rows = len(parsed)
        elif isinstance(parsed, dict):
            raw_rows = 1

    verdict = "ok" if status == 200 else ("rate_limited" if status == 429 else "error")
    stage_result = StageResult(
        stage_name=stage_name,
        day=day_value.isoformat(),
        ids_count=len(campaign_ids),
        campaign_ids=campaign_ids,
        http_status=status,
        raw_rows_count=raw_rows,
        loaded_rows_count=0,
        response_headers_saved=bool(response_headers),
        rate_limit_remaining=str(rate_remaining) if rate_remaining is not None else None,
        rate_limit_retry=str(rate_retry) if rate_retry is not None else None,
        rate_limit_reset=str(rate_reset) if rate_reset is not None else None,
        verdict=verdict,
    )

    artifact = {
        "stage_name": stage_name,
        "day": day_value.isoformat(),
        "range_days": range_days,
        "campaign_ids": campaign_ids,
        "request_url": diag.get("request_url"),
        "request_params": params,
        "http_status": status,
        "error": error,
        "response_headers": response_headers,
        "response_body": diag.get("response_body"),
        "parsed_rows_count": raw_rows,
        "diagnostics": diagnostics,
    }
    return stage_result, artifact


def _write_csv(path: Path, results: list[StageResult]) -> None:
    fieldnames = [
        "stage_name",
        "day",
        "ids_count",
        "campaign_ids",
        "http_status",
        "raw_rows_count",
        "loaded_rows_count",
        "response_headers_saved_yes_no",
        "x_ratelimit_remaining",
        "x_ratelimit_retry",
        "x_ratelimit_reset",
        "verdict",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "stage_name": result.stage_name,
                    "day": result.day,
                    "ids_count": result.ids_count,
                    "campaign_ids": ",".join(str(cid) for cid in result.campaign_ids),
                    "http_status": result.http_status,
                    "raw_rows_count": result.raw_rows_count,
                    "loaded_rows_count": result.loaded_rows_count,
                    "response_headers_saved_yes_no": "yes" if result.response_headers_saved else "no",
                    "x_ratelimit_remaining": result.rate_limit_remaining,
                    "x_ratelimit_retry": result.rate_limit_retry,
                    "x_ratelimit_reset": result.rate_limit_reset,
                    "verdict": result.verdict,
                }
            )


def _write_report(
    path: Path,
    *,
    day_value: date,
    campaign_ids: list[int],
    results: list[StageResult],
    safe_batch: int | None,
) -> None:
    def _status(name: str) -> int | None:
        for item in results:
            if item.stage_name == name:
                return item.http_status
        return None

    lines: list[str] = []
    lines.append("# WB ads fullstats ladder report")
    lines.append("")
    lines.append(f"- day: `{day_value.isoformat()}`")
    lines.append(f"- campaign_ids: `{', '.join(str(cid) for cid in campaign_ids)}`")
    lines.append("")
    lines.append("## Ladder summary")
    for result in results:
        lines.append(
            f"- {result.stage_name}: ids={result.ids_count}, status={result.http_status}, verdict={result.verdict}"
        )
    lines.append("")
    if safe_batch is None:
        lines.append("- safe_batch_size: not found")
    else:
        lines.append(f"- safe_batch_size: {safe_batch}")
    lines.append("")
    lines.append("## Final 3-line вывод")
    if safe_batch is None:
        lines.append("- fullstats: not confirmed (429 at minimal ladder)")
        lines.append("- safe batch size: not found")
        lines.append("- proceed to 3/7 days: no")
    else:
        range_3d_ok = _status("range_3d") == 200
        range_7d_ok = _status("range_7d") == 200
        lines.append("- fullstats: alive")
        lines.append(f"- safe batch size: {safe_batch}")
        lines.append(
            f\"- proceed: 3d={'yes' if range_3d_ok else 'no'}, 7d={'yes' if range_7d_ok else 'no'}\"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    _load_dotenv()
    day_value, ids = _select_day_and_ids()
    stages = [("A", 1), ("B", 3), ("C", 5), ("D", 10)]
    results: list[StageResult] = []
    artifacts: list[tuple[str, dict[str, Any]]] = []

    out_dir = PROJECT_ROOT / "exports" / "wb_ads_fullstats_ladder_v1"
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_batch: int | None = None
    safe_ids: list[int] = []
    for stage_name, count in stages:
        stage_ids = ids[:count]
        result, artifact = _run_fullstats(
            stage_name=f"stage_{stage_name}",
            day_value=day_value,
            campaign_ids=stage_ids,
            range_days=1,
        )
        results.append(result)
        artifacts.append((f"stage_{stage_name}", artifact))
        if safe_batch is None and result.http_status == 200:
            safe_batch = count
            safe_ids = stage_ids
            break

    if safe_batch is not None:
        for label, range_days in [("range_1d", 1), ("range_3d", 3), ("range_7d", 7)]:
            result, artifact = _run_fullstats(
                stage_name=label,
                day_value=day_value,
                campaign_ids=safe_ids,
                range_days=range_days,
            )
            results.append(result)
            artifacts.append((label, artifact))

    for name, artifact in artifacts:
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_path = out_dir / "ads_fullstats_ladder_steps.csv"
    _write_csv(csv_path, results)

    report_path = out_dir / "ads_fullstats_ladder_report.md"
    _write_report(
        report_path,
        day_value=day_value,
        campaign_ids=safe_ids if safe_ids else ids[:10],
        results=results,
        safe_batch=safe_batch,
    )
    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
