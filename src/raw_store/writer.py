from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SOURCE_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def write_raw_json(
    schema: str,
    source: str,
    run_id: str,
    payload: Mapping[str, Any],
    page: int,
    request_params: Mapping[str, Any] | None = None,
    http_status: int | None = None,
) -> Path:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not SOURCE_PATTERN.fullmatch(source):
        raise ValueError(f"Invalid source name: {source}")
    if page < 1:
        raise ValueError("page must be >= 1")

    day = datetime.now(timezone.utc).date().isoformat()
    base_dir = _project_root() / "data" / "raw" / schema / source / day
    base_dir.mkdir(parents=True, exist_ok=True)

    file_path = base_dir / f"{run_id}_{page}.json"
    file_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    meta_path = file_path.with_name(file_path.name + ".meta.json")
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "page": page,
        "schema": schema,
    }
    if request_params is not None:
        meta["request_params"] = request_params
    if http_status is not None:
        meta["http_status"] = http_status

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path
