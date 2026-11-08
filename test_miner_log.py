"""Contrato do formato de log do SRBMiner com linhas reais.

A fixture é um log público e autorizado do autor (`portable-data/`, SRBMiner
3.8.0, RTX 3070 física). Não há log público da 3.8.4 no repositório: ao
publicar um, acrescente-o a LOGS com os valores esperados da última amostra.
"""

import time
import unittest
from pathlib import Path

from gpu_telemetry import parse_gpu_log
from miner_log import AVERAGE, DEVICE_BUS, DEVICE_INDEX, RATE, STAMP, log_timestamp, parse_stamp
from rig import parse_miner_device_map

ROOT = Path(__file__).resolve().parent
LOGS = {
    "3.8.0": (
        ROOT / "portable-data/miner-logs/maximum-20261024-200354.log",
        {"hashrate": 76.08, "accepted": 8, "rejected": 0, "hw_errors": 0, "average_1m": 76.22, "average_15m": 74.10},
    ),
}


class RealLogContractTests(unittest.TestCase):
    def test_each_supported_version_parses_last_sample_and_version(self):
        for version, (path, expected) in LOGS.items():
            with self.subTest(version=version):
                text = path.read_text(encoding="utf-8-sig")
                now = log_timestamp(text.strip().splitlines()[-1]) + 1
                result = parse_gpu_log(text, "GPU-fixture", 0, now=now)
                self.assertEqual(result["version"], version)
                self.assertEqual(result["connection"], "Conectado à pool")
                for key, value in expected.items():
                    self.assertAlmostEqual(result[key], value, places=6, msg=key)
                self.assertGreater(len(result["rate_points"]), 5)

    def test_every_rate_line_of_real_log_matches_except_total(self):
        path = LOGS["3.8.0"][0]
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        rates = [line for line in lines if " TH/s [" in line and "Total:" not in line]
        self.assertTrue(rates)
        for line in rates:
            self.assertIsNotNone(STAMP.match(line), line)
            self.assertIsNotNone(RATE.search(line), line)
        self.assertTrue(
            all(RATE.search(line) is None for line in lines if line.split("] ", 1)[-1].startswith("Total:"))
        )
        self.assertTrue(any(AVERAGE.search(line) for line in lines))

    def test_real_device_listing_maps_bus(self):
        line = next(line for line in LOGS["3.8.0"][0].read_text(encoding="utf-8-sig").splitlines() if "BUS:" in line)
        self.assertEqual(DEVICE_INDEX.search(line)[1], "0")
        self.assertEqual(int(DEVICE_BUS.search(line)[1], 16), 6)
        mapping = parse_miner_device_map(line, [{"uuid": "GPU-a", "index": 0, "pci_bus_id": "00000000:06:00.0"}])
        self.assertEqual(list(mapping.values()), [0])


class FormatVariantTests(unittest.TestCase):
    def test_bracketed_labels_and_other_units(self):
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 10))
        text = f"[{stamp}] GPU[0] RTX: 80.5 GH/s [A:3 R:1 HW:0]\n[{stamp}] Average hashrate: 1m 81 GH/s | 15m 0.08 TH/s"
        result = parse_gpu_log(text, "GPU-x", 0, now=now)
        self.assertAlmostEqual(result["hashrate"], 0.0805)
        self.assertAlmostEqual(result["average_1m"], 0.081)
        self.assertAlmostEqual(result["average_15m"], 0.08)
        self.assertEqual((result["accepted"], result["rejected"]), (3, 1))

    def test_invalid_stamp_is_missing_not_zero(self):
        self.assertIsNone(parse_stamp("2026-99-25 00:00:00"))
        self.assertIsNone(log_timestamp("[2026-99-25 00:00:00] fixture"))
        self.assertIsNone(log_timestamp("sem carimbo"))


if __name__ == "__main__":
    unittest.main()
