from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection

PLACEHOLDER_PATTERN = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    path_obj = Path(path)
    if path_obj.is_absolute():
        return path_obj
    return project_root() / path_obj


def get_conn() -> Connection[Any]:
    host = os.getenv("PGHOST")
    port = os.getenv("PGPORT", "5432")
    database = os.getenv("PGDATABASE")
    user = os.getenv("PGUSER")
    password = os.getenv("PGPASSWORD")

    missing = [
        name
        for name, value in {
            "PGHOST": host,
            "PGDATABASE": database,
            "PGUSER": user,
            "PGPASSWORD": password,
        }.items()
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(f"Missing Postgres env vars: {joined}")

    return psycopg.connect(
        host=host,
        port=int(port),
        dbname=database,
        user=user,
        password=password,
    )


def execute_sql(conn: Connection[Any], sql: str) -> None:
    if not sql.strip():
        return
    with conn.cursor() as cur:
        cur.execute(sql)


def render_sql_template(sql: str, params: Mapping[str, Any]) -> str:
    str_params = {key: str(value) for key, value in params.items()}

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in str_params:
            raise KeyError(f"Missing SQL template param: {key}")
        return str_params[key]

    rendered = PLACEHOLDER_PATTERN.sub(_replace, sql)
    unresolved = PLACEHOLDER_PATTERN.findall(rendered)
    if unresolved:
        unique = ", ".join(sorted(set(unresolved)))
        raise ValueError(f"Unresolved SQL placeholders: {unique}")

    return rendered


def execute_sql_file(conn: Connection[Any], path: str | Path, params: Mapping[str, Any]) -> None:
    file_path = resolve_project_path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"SQL file not found: {file_path}")

    sql_template = file_path.read_text(encoding="utf-8")
    sql = render_sql_template(sql_template, params)
    execute_sql(conn, sql)


def run_in_transaction(conn: Connection[Any], fn: Callable[[Connection[Any]], None]) -> None:
    with conn.transaction():
        fn(conn)


@contextmanager
def transaction(conn: Connection[Any]) -> Iterator[Connection[Any]]:
    with conn.transaction():
        yield conn
