from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.connectors.wb.http import _build_ssl_config


class TestWbHttpTls(unittest.TestCase):
    def test_tls_verification_is_enabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = _build_ssl_config()

        self.assertEqual(config.mode, "default_verify")
        self.assertTrue(config.context.check_hostname)
        self.assertTrue(config.context.verify_mode)

    def test_insecure_tls_override_is_rejected(self) -> None:
        with patch.dict(os.environ, {"WB_SSL_NO_VERIFY": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "TLS certificate verification must remain enabled"):
                _build_ssl_config()


if __name__ == "__main__":
    unittest.main()
