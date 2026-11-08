import logging
import tempfile
import unittest
from pathlib import Path

import app_logging


class AppLoggingTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger(app_logging.ROOT)
        for handler in list(root.handlers):
            if not isinstance(handler, logging.NullHandler):
                root.removeHandler(handler)
                handler.close()

    def test_configure_writes_traceback_once_per_path(self):
        with tempfile.TemporaryDirectory() as folder:
            first = app_logging.configure(folder)
            second = app_logging.configure(folder)
            self.assertEqual(first, second)
            log = app_logging.get_logger("teste")
            try:
                raise ValueError("falha simulada")
            except ValueError:
                log.exception("Contexto do erro")
            for handler in logging.getLogger(app_logging.ROOT).handlers:
                handler.flush()
            text = (Path(folder) / "hashpilot.log").read_text(encoding="utf-8")
            self.assertIn("Contexto do erro", text)
            self.assertIn("Traceback", text)
            self.assertEqual(text.count("Contexto do erro"), 1)
            self.tearDown()

    def test_unconfigured_logger_is_silent(self):
        self.assertTrue(any(isinstance(h, logging.NullHandler) for h in logging.getLogger(app_logging.ROOT).handlers))

    def test_unwritable_directory_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            blocker = Path(folder) / "arquivo"
            blocker.write_text("x", encoding="utf-8")
            self.assertIsNone(app_logging.configure(blocker / "sub"))


if __name__ == "__main__":
    unittest.main()
