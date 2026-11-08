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
            text = (Path(folder) / "diagnostics" / "hashpilot.log").read_text(encoding="utf-8")
            self.assertIn("Contexto do erro", text)
            self.assertIn("Traceback", text)
            self.assertEqual(text.count("Contexto do erro"), 1)
            self.tearDown()

    def test_diagnostics_never_look_like_miner_logs_in_log_dir(self):
        # O painel legado importa todo LOG_DIR/*.log como log do minerador.
        with tempfile.TemporaryDirectory() as folder:
            for name in ("hashpilot.log", "rig-service.log"):
                app_logging.configure(folder, name)
            app_logging.get_logger("teste").error("linha de diagnóstico")
            for handler in logging.getLogger(app_logging.ROOT).handlers:
                handler.flush()
            self.assertEqual(list(Path(folder).glob("*.log")), [])
            self.assertEqual(len(list((Path(folder) / "diagnostics").glob("*.log"))), 2)
            self.tearDown()

    def test_repeated_identical_error_is_logged_once_per_window(self):
        log = app_logging.get_logger("ciclo")

        def fail(text, now):
            try:
                raise RuntimeError(text)
            except RuntimeError:
                return app_logging.exception_once(log, "teste-ciclo", "falha", now=now)

        with self.assertLogs("hashpilot.ciclo", "ERROR") as captured:
            self.assertTrue(fail("sem sensor", 1000.0))
            self.assertFalse(fail("sem sensor", 1002.0))  # ciclo seguinte, mesmo erro
            self.assertTrue(fail("outro erro", 1004.0))  # erro mudou
            self.assertTrue(fail("outro erro", 1004.0 + app_logging.REPEAT_SECONDS))  # janela venceu
        self.assertEqual(len(captured.records), 3)
        self.assertIsNotNone(captured.records[0].exc_info)
        with self.assertLogs("hashpilot.ciclo", "WARNING") as warned:
            self.assertTrue(app_logging.warning_once(log, "w", "x", "aviso %s", 1, now=5.0))
            self.assertFalse(app_logging.warning_once(log, "w", "x", "aviso %s", 1, now=6.0))
        self.assertEqual(len(warned.records), 1)

    def test_unconfigured_logger_is_silent(self):
        self.assertTrue(any(isinstance(h, logging.NullHandler) for h in logging.getLogger(app_logging.ROOT).handlers))

    def test_unwritable_directory_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            blocker = Path(folder) / "arquivo"
            blocker.write_text("x", encoding="utf-8")
            self.assertIsNone(app_logging.configure(blocker / "sub"))


if __name__ == "__main__":
    unittest.main()
