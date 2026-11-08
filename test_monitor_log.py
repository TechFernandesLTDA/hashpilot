import time
import unittest
from pathlib import Path

import tempfile

from monitor import parse_log, tail

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


class LegacyTailTests(unittest.TestCase):
    def test_tail_appends_incrementally_and_drops_partial_first_line(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.log"
            path.write_bytes(b"linha-a\n" * 50)
            self.assertEqual(tail(path, 100).splitlines()[0], "linha-a")
            with path.open("ab") as handle:
                handle.write(b"[2026-10-24 20:00:00] nova\n")
            self.assertTrue(tail(path, 100).endswith("nova\n"))

    def test_utf16_log_is_decoded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "u.log"
            path.write_bytes("[2026-10-24 20:00:00] Miner version: 3.8.0\n".encode("utf-16"))
            self.assertIn("Miner version", tail(path))


if __name__ == "__main__":
    unittest.main()
