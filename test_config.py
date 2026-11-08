import tempfile
import unittest
from pathlib import Path

from config import TEST_STATE_ENV, UNSUPPORTED_PLATFORM, load_settings, log_dir, resolve_state_dir


class ConfigResolutionTests(unittest.TestCase):
    def test_windows_uses_localappdata_and_ignores_test_override(self):
        path = resolve_state_dir("win32", {"LOCALAPPDATA": "C:/Users/u/AppData/Local", TEST_STATE_ENV: "/tmp/x"})
        self.assertEqual(path, Path("C:/Users/u/AppData/Local") / "PearlMonitor")

    def test_other_platforms_refuse_without_explicit_test_directory(self):
        with self.assertRaisesRegex(RuntimeError, UNSUPPORTED_PLATFORM):
            resolve_state_dir("linux", {})
        self.assertEqual(resolve_state_dir("linux", {TEST_STATE_ENV: "/tmp/x"}), Path("/tmp/x/PearlMonitor"))

    def test_settings_missing_is_empty_but_invalid_is_explicit_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            self.assertEqual(load_settings(path), {})
            path.write_text("[]", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "objeto JSON"):
                load_settings(path)
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "Não foi possível ler"):
                load_settings(path)

    def test_log_dir_defaults_next_to_state_and_honors_setting(self):
        state = Path("/s/Local/PearlMonitor")
        self.assertEqual(log_dir(state, {}), Path("/s/Local/Mining/Pearl/logs"))
        self.assertEqual(log_dir(state, {"log_dir": "/elsewhere"}), Path("/elsewhere"))


if __name__ == "__main__":
    unittest.main()
