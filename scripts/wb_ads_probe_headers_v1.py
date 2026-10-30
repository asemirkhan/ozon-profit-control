from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.connectors.wb.ads import WB_ADS_CAMPAIGNS_URL
from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_rate_headers(entry: dict[str, Any]) -> dict[str, str]:
    keys = {"X-Ratelimit-Retry", "X-Ratelimit-Reset", "X-Ratelimit-Limit"}
    headers = entry.get("response_headers") or {}
    return {key: str(headers.get(key)) for key in keys if key in headers}


def main() -> int:
    diagnostics: list[dict[str, Any]] = []
    headers = get_wb_headers()
    host = WB_ADS_CAMPAIGNS_URL.split("/")[2]
    started_at = _now_iso()
    status = None
    error: str | None = None

    try:
        _body, status = request_json_with_status(
            method="GET",
            url=WB_ADS_CAMPAIGNS_URL,
            headers=headers,
            diagnostics_collector=diagnostics.append,
            max_attempts_override=1,
            retry_delays_override=[],
            retry_after_buffer_sec=0,
            rate_api_category="ads",
            rate_endpoint="/adv/v1/promotion/count",
        )
    except Exception as exc:  # noqa: BLE001 - keep diagnostics even on failure
        error = str(exc)

    payload = {
        "started_at": started_at,
        "finished_at": _now_iso(),
        "endpoint": "/adv/v1/promotion/count",
        "url": WB_ADS_CAMPAIGNS_URL,
        "host": host,
        "http_status": status,
        "error": error,
        "rate_headers": _extract_rate_headers(diagnostics[-1]) if diagnostics else {},
        "diagnostics": diagnostics,
    }

    out_dir = Path("exports/wb_ads_probe_headers_v1")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "probe_result.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
