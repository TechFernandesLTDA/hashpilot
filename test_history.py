"""Durabilidade, isolamento e recuperação sem tocar na mineração real."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from history import HistoryStore


def sample(stamp, rate=75):
    return {"collected_at": stamp, "gpu": {"temperature.gpu": 74, "power.draw": 158, "fan.speed": 100},
            "miner": {"hashrate": rate, "hashrate_at": stamp, "accepted": 8, "rejected": 0, "hw_errors": 0,
                      "paused": False, "connection": "Conectado"}, "miner_running": True, "log_path": "example.log"}


class HistoryRules(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)/"history.sqlite3"
        self.store = HistoryStore(self.path, "test-wallet")
        self.assertTrue(self.store.flush())

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def test_series_restore_after_reopening_and_wallet_isolation(self):
        stamp = time.time()-30
        self.store.telemetry(sample(stamp))
        self.assertTrue(self.store.flush())
        before = self.store.stats()["telemetry"]
        self.store.close()
        self.store = HistoryStore(self.path, "test-wallet")
        self.assertTrue(self.store.flush())
        self.assertEqual(self.store.stats()["telemetry"], before)
        self.assertEqual(self.store.restore(stamp-1)["points"]["hash"], [(stamp, 75)])
        other = HistoryStore(self.path, "other-wallet")
        try:
            self.assertTrue(other.flush())
            self.assertEqual(other.restore(0)["count"], 0)
        finally:
            other.close()

    def test_financial_migration_idempotent_and_no_expiration(self):
        stamp = time.time()-60*86400
        state = {"snapshots": [{"timestamp": stamp, "total_prl": 1.5}],
                 "sources": {"balance": {"fetched_at": stamp, "data": {"total": 1.5}}}}
        self.store.finance(state)
        self.store.finance(state)
        self.assertTrue(self.store.flush())
        self.assertEqual(self.store.stats()["finance_snapshots"], (1, stamp, stamp))
        self.assertEqual(len(self.store.finance_state(0)["snapshots"]), 1)
        self.assertEqual(self.store.stats()["finance_sources"][0], 1)

    def test_thermal_events_survive_restart_and_incomplete_line_is_retried(self):
        events = self.path.parent / "thermal-control-events.jsonl"
        first = {"sampled_at": time.time()-5, "mode": "anticipating", "desired_clock_mhz": 1725}
        second = {"sampled_at": time.time(), "mode": "hold", "desired_clock_mhz": 1725}
        encoded = json.dumps(second)
        events.write_text(json.dumps(first)+"\n"+encoded[:10], encoding="utf-8")
        self.store.archive_thermal(events)
        self.assertTrue(self.store.flush())
        self.assertEqual(self.store.read("SELECT COUNT(*) FROM events WHERE kind='thermal_control'", ())[0][0], 1)
        with events.open("a", encoding="utf-8") as handle:
            handle.write(encoded[10:]+"\n")
        self.store.archive_thermal(events)
        self.assertTrue(self.store.flush())
        self.store.close()
        self.store = HistoryStore(self.path, "test-wallet")
        self.store.archive_thermal(events)
        self.assertTrue(self.store.flush())
        self.assertEqual(self.store.read("SELECT COUNT(*) FROM events WHERE kind='thermal_control'", ())[0][0], 2)

    def test_busy_writer_keeps_queue_and_does_not_block_submission(self):
        blocker = sqlite3.connect(self.path)
        blocker.execute("BEGIN IMMEDIATE")
        stamp = time.time()
        started = time.monotonic()
        self.store.telemetry(sample(stamp))
        self.assertLess(time.monotonic()-started, .1)
        self.assertFalse(self.store.flush(.3))
        blocker.rollback()
        blocker.close()
        self.assertTrue(self.store.flush(4))
        self.assertEqual(self.store.stats()["telemetry"][0], 1)

    def test_committed_sample_survives_forced_process_exit(self):
        self.store.close()
        script = "from pathlib import Path; from history import HistoryStore; import os,json; h=HistoryStore(Path(os.environ['TEST_DB']),'test-wallet'); h.telemetry(json.loads(os.environ['TEST_SAMPLE'])); assert h.flush(); os._exit(0)"
        import os
        environment = os.environ.copy()
        environment["TEST_DB"] = str(self.path)
        environment["TEST_SAMPLE"] = json.dumps(sample(time.time()))
        subprocess.run([sys.executable, "-c", script], env=environment, check=True, timeout=10)
        self.store = HistoryStore(self.path, "test-wallet")
        self.assertTrue(self.store.flush())
        self.assertEqual(self.store.stats()["telemetry"][0], 1)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_gpu0_already_in_gpu_telemetry_is_not_copied_to_legacy_table(self):
        stamp = time.time()-20
        device = {"uuid": "GPU-0000", "index": 0, "wallet": "test-wallet", "collected_at": stamp,
                  "gpu": sample(stamp)["gpu"], "miner": sample(stamp)["miner"], "miner_running": True}
        self.store.rig_telemetry({"collected_at": stamp, "devices": [device]})
        self.store.telemetry(sample(stamp), archive=False)
        changed = sample(stamp+2)
        changed["miner"]["connection"] = "Reconectando"
        self.store.telemetry(changed, archive=False)
        self.store.telemetry(sample(stamp+4), archive=False)
        self.assertTrue(self.store.flush())
        counts = self.store.stats()
        self.assertEqual(counts["telemetry"][0], 0)
        self.assertEqual(counts["gpu_telemetry"][0], 1)
        # Os eventos de status continuam: início, mudança de conexão e volta.
        kinds = [row[0] for row in self.store.read("SELECT kind FROM events WHERE kind='status_changed' ORDER BY timestamp")]
        self.assertEqual(len(kinds), 3)
        # O painel conta as amostras pela maior tabela.
        from history import history_samples
        self.assertEqual(history_samples(counts), 1)
        self.assertEqual(history_samples({"telemetry": [5, 1, 2], "gpu_telemetry": [3, 1, 2]}), 5)
        self.assertEqual(history_samples({}), 0)
        # Sem carimbo a amostra é rejeitada também sem arquivar.
        self.store.telemetry({"miner": {}}, archive=False)
        self.assertTrue(self.store.flush())
        self.assertIn("collected_at", self.store.last_error or "")


class PanelWritesGpu0Once(unittest.TestCase):
    def test_collector_sends_gpu0_to_legacy_table_only_as_status(self):
        import monitor
        calls = []

        class FakeHistory:
            def rig_telemetry(self, rig):
                calls.append(("rig", len(rig["devices"])))

            def telemetry(self, data, source="live", *, archive=True):
                calls.append(("telemetry", source, archive, data["wallet"]))

            def archive_thermal(self, path):
                collector.stop.set()

            def event(self, *args, **kwargs):
                pass

        collector = monitor.Collector(history=FakeHistory())
        device = {"uuid": "GPU-0", "index": 0, "wallet": "w", "gpu": {}, "miner": {}, "miner_running": True, "log_path": None}
        collector.sample = lambda: {"collected_at": 1.0, "rig": {"devices": [device]}}
        collector.work()
        self.assertEqual(calls, [("rig", 1), ("telemetry", "live", False, "w")])


if __name__ == "__main__":
    unittest.main()
