import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import monitor
from monitor import Monitor


class ShutdownOrderTests(unittest.TestCase):
    def test_collector_finishes_before_history_closes(self):
        calls = []
        stub = SimpleNamespace(
            tray=SimpleNamespace(stop=lambda: calls.append("tray")),
            collector_thread=SimpleNamespace(join=lambda timeout: calls.append(("collector", timeout))),
            history=SimpleNamespace(close=lambda: calls.append("history")),
        )
        Monitor.shutdown_background(stub)
        self.assertEqual(calls, ["tray", ("collector", 5), "history"])

    def stub(self, history_alive):
        state = {"alive": history_alive}
        stub = SimpleNamespace(
            history=SimpleNamespace(worker=SimpleNamespace(is_alive=lambda: state["alive"])),
            after=MagicMock(),
            destroy=MagicMock(),
            withdraw=MagicMock(),
            close_deadline=time.monotonic() + 60,
        )
        stub.finish_close = lambda: None
        return stub, state

    def test_finish_close_never_discards_pending_history_writes(self):
        stub, state = self.stub(history_alive=True)
        Monitor.finish_close(stub)
        stub.after.assert_called_once()
        stub.destroy.assert_not_called()
        stub.withdraw.assert_not_called()
        # Prazo esgotado: a janela some, mas o processo continua até o histórico terminar.
        stub.close_deadline = time.monotonic() - 1
        with self.assertLogs("hashpilot.monitor", "WARNING"):
            Monitor.finish_close(stub)
        stub.withdraw.assert_called_once()
        stub.destroy.assert_not_called()
        Monitor.finish_close(stub)
        stub.withdraw.assert_called_once()  # oculta uma vez só
        state["alive"] = False
        Monitor.finish_close(stub)
        stub.destroy.assert_called_once()
        self.assertGreater(monitor.CLOSE_TIMEOUT_SECONDS, 5)

    def test_finish_close_destroys_immediately_when_idle(self):
        stub, _ = self.stub(history_alive=False)
        Monitor.finish_close(stub)
        stub.destroy.assert_called_once()
        stub.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
