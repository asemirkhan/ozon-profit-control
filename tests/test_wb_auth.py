from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from src.connectors.wb.auth import MissingWbCredentialsError, get_wb_headers


class TestWbAuth(TestCase):
    def test_prefers_wb_api_token_over_wb_api_key(self) -> None:
        env = {
            "WB_API_TOKEN": "token_value",
            "WB_API_KEY": "key_value",
        }
        with patch.dict(os.environ, env, clear=False):
            headers = get_wb_headers()

        self.assertEqual(headers["Authorization"], "token_value")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_falls_back_to_wb_api_key(self) -> None:
        env = {
            "WB_API_TOKEN": "",
            "WB_API_KEY": "key_value",
        }
        with patch.dict(os.environ, env, clear=False):
            headers = get_wb_headers()

        self.assertEqual(headers["Authorization"], "key_value")

    def test_raises_when_no_token_or_api_key(self) -> None:
        env = {
            "WB_API_TOKEN": "",
            "WB_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(MissingWbCredentialsError):
                get_wb_headers()
