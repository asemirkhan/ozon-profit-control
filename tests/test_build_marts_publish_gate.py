from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from src.marts.build_marts import build_marts


class TestBuildMartsPublishGate(TestCase):
    def test_published_build_uses_gate_and_marks_snapshot(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sql_path = Path(tmp_dir) / "one.sql"
            sql_path.write_text("SELECT 1;", encoding="utf-8")

            with (
                patch("src.marts.build_marts.MART_SQL_FILES", ("one.sql",)),
                patch("src.marts.build_marts.get_conn", return_value=nullcontext(object())),
                patch("src.marts.build_marts.run_in_transaction", side_effect=lambda conn, fn: fn(conn)),
                patch("src.marts.build_marts.execute_sql_file"),
                patch("src.marts.build_marts.ensure_snapshot_publishable") as ensure_mock,
                patch("src.marts.build_marts.mark_snapshot_published", return_value=1) as mark_mock,
            ):
                ensure_mock.return_value = []
                build_marts(
                    schema="client_demo",
                    period_start="2026-02-10",
                    period_end="2026-03-11",
                    sql_dir=tmp_dir,
                    build_mode="published_build",
                    marketplace="wb",
                )

            ensure_mock.assert_called_once()
            mark_mock.assert_called_once()

    def test_diagnostic_build_skips_gate_and_publish_mark(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            sql_path = Path(tmp_dir) / "one.sql"
            sql_path.write_text("SELECT 1;", encoding="utf-8")

            with (
                patch("src.marts.build_marts.MART_SQL_FILES", ("one.sql",)),
                patch("src.marts.build_marts.get_conn", return_value=nullcontext(object())),
                patch("src.marts.build_marts.run_in_transaction", side_effect=lambda conn, fn: fn(conn)),
                patch("src.marts.build_marts.execute_sql_file"),
                patch("src.marts.build_marts.ensure_snapshot_publishable") as ensure_mock,
                patch("src.marts.build_marts.mark_snapshot_published") as mark_mock,
            ):
                build_marts(
                    schema="client_demo",
                    period_start="2026-02-10",
                    period_end="2026-03-11",
                    sql_dir=tmp_dir,
                    build_mode="diagnostic_build",
                )

            ensure_mock.assert_not_called()
            mark_mock.assert_not_called()
