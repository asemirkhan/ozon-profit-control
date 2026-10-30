from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "wb_ads_canonical_runner_v1.py"


class WbAdsRuntimeRateLimitExitTests(unittest.TestCase):
    def test_runner_checks_stop_before_enqueuing_fallback(self):
        source = RUNNER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIsNotNone(tree)

        stop_assignment = source.index("stop_on_rate_limit = status == 429 and STOP_AFTER_FIRST_429")
        fallback_condition = source.index('QUEUE_STRATEGY != "legacy" and not stop_on_rate_limit')
        fallback_insert = source.index("request_units[index:index] = fallback_units")
        stop_assignment_use = source.index("if stop_on_rate_limit:", fallback_insert)

        self.assertLess(stop_assignment, fallback_condition)
        self.assertLess(fallback_condition, fallback_insert)
        self.assertLess(fallback_insert, stop_assignment_use)


if __name__ == "__main__":
    unittest.main()
