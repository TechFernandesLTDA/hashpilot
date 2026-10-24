"""O painel lê status sem transformar ausência ou falha em valor atualizado."""
import json
from pathlib import Path
import tempfile
import unittest
from monitor import read_thermal_status, thermal_description


class ThermalStatusRules(unittest.TestCase):
    def test_optional_missing_file_and_stale_timestamp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"thermal-control.json"
            self.assertIsNone(read_thermal_status(path, now=100))
            path.write_text(json.dumps({"enabled": True, "mode": "cooling", "current_clock_mhz": 1740,
                "desired_clock_mhz": 1725, "reason": "Teste de leitura", "sampled_at": 98}), encoding="utf-8")
            current = read_thermal_status(path, now=100)
            self.assertFalse(current["stale"])
            self.assertEqual(current["age_seconds"], 2)
            self.assertIn("1725", thermal_description(current))
            old = read_thermal_status(path, now=120)
            self.assertTrue(old["stale"])
            self.assertIn("desatualizado", thermal_description(old))

    def test_invalid_json_and_reported_controller_error_remain_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"thermal-control.json"
            path.write_text("{incompleto", encoding="utf-8")
            status = read_thermal_status(path, now=100)
            self.assertFalse(status["available"])
            self.assertTrue(status["error"])
            self.assertIn("Erro", thermal_description(status))
            self.assertIn("permissão", thermal_description({"enabled": True, "error": "sem permissão"}))


if __name__ == "__main__":
    unittest.main()
