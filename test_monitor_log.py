import time
import unittest
from pathlib import Path

from monitor import parse_log

LOG = Path(__file__).resolve().parent / "portable-data/miner-logs/maximum-20261024-200354.log"


class LegacyPanelParserTests(unittest.TestCase):
    """O painel legado usa o mesmo parser do serviço (antes tinha regex só para GPU0/TH/s)."""

    def test_real_log_matches_service_parser_fields(self):
        text = LOG.read_text(encoding="utf-8-sig")
        result = parse_log(text, now=time.mktime((2026, 10, 24, 20, 15, 0, 0, 0, -1)))
        self.assertAlmostEqual(result["hashrate"], 76.08)
        self.assertEqual(result["accepted"], 8)
        self.assertAlmostEqual(result["average_15m"], 74.10)
        self.assertEqual(result["connection"], "Conectado à pool")
        self.assertIn("recent_logs", result)
        self.assertNotIn("gpu_uuid", result)

    def test_bracketed_label_and_gh_units_are_no_longer_ignored(self):
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 5))
        result = parse_log(f"[{stamp}] GPU[0] X: 500 GH/s [A:1 R:0 HW:0]\n[{stamp}] Average hashrate: 1m 0.5 TH/s | 15m 500 GH/s", now=now)
        self.assertAlmostEqual(result["hashrate"], 0.5)
        self.assertAlmostEqual(result["average_15m"], 0.5)

    def test_empty_log_is_missing_not_zero(self):
        result = parse_log("")
        self.assertIsNone(result["hashrate"])
        self.assertIsNone(result["average_1m"])


if __name__ == "__main__":
    unittest.main()
