import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import monitor
from monitor import Monitor


class ShutdownOrderTests(unittest.TestCase):
    def test_collector_finishes_before_history_closes(self):
        calls = []
        stub = SimpleNamespace(tray=SimpleNamespace(stop=lambda: calls.append("tray")),
                               collector_thread=SimpleNamespace(join=lambda timeout: calls.append(("collector", timeout))),
                               history=SimpleNamespace(close=lambda: calls.append("history")))
        Monitor.shutdown_background(stub)
        self.assertEqual(calls, ["tray", ("collector", 5), "history"])

    def test_finish_close_waits_then_gives_up_after_deadline(self):
        alive = SimpleNamespace(is_alive=lambda: True)
        stub = SimpleNamespace(history=SimpleNamespace(worker=alive), after=MagicMock(), destroy=MagicMock(),
                               close_deadline=time.monotonic() + 60)
        stub.finish_close = lambda: None
        Monitor.finish_close(stub)
        stub.after.assert_called_once()
        stub.destroy.assert_not_called()
        stub.close_deadline = time.monotonic() - 1
        with self.assertLogs("hashpilot.monitor", "WARNING"):
            Monitor.finish_close(stub)
        stub.destroy.assert_called_once()
        self.assertGreater(monitor.CLOSE_TIMEOUT_SECONDS, 5)


if __name__ == "__main__":
    unittest.main()
