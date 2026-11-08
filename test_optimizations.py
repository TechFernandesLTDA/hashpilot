"""Regressões das otimizações da v0.5.6: caches não podem mudar o que é medido ou gravado."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import file_cache
import gpu_telemetry
from gpu_agent import detect_nvidia_gpus
from gpu_telemetry import parse_gpu_log, read_log_tail
from history import HistoryStore
from testing_support import windows_only


def rate_line(stamp, rate, accepted=1):
    return f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(stamp))}] GPU0 RTX: {rate} TH/s [A:{accepted} R:0 HW:0]"


class DetectionTests(unittest.TestCase):
    CAPABILITIES = "580.95, [N/A], 0x248410DE, 0x12341043, 94.04, 8.6, 100, 270, 220, 240, 2100, 8192, 93"

    def command_factory(self, calls, combined=True):
        def command(arguments):
            calls.append(arguments)
            query = next((part for part in arguments if part.startswith("--query-gpu=")), "")
            if query.startswith("--query-gpu=index,name"):
                return "0, NVIDIA GeForce RTX 3070, GPU-a, 00000000:01:00.0"
            if "supported-clocks" in " ".join(arguments):
                return "1500\n1800"
            if "," in query:
                if combined:
                    return self.CAPABILITIES
                raise RuntimeError("Field is not a valid field to query")
            raise RuntimeError("Not supported")
        return command

    def test_capabilities_use_one_query_per_card(self):
        calls = []
        gpu = detect_nvidia_gpus(self.command_factory(calls))[0]
        self.assertEqual(len(calls), 3)
        self.assertEqual(gpu["power_limit_w"], 240)
        self.assertEqual(gpu["temperature_limit_c"], 93)
        self.assertIsNone(gpu["serial"])
        self.assertEqual(gpu["supported_graphics_clocks_mhz"], [1500, 1800])
        self.assertEqual(gpu["architecture"], "Ampere")

    def test_unsupported_field_falls_back_to_individual_queries(self):
        calls = []
        gpu = detect_nvidia_gpus(self.command_factory(calls, combined=False))[0]
        self.assertGreater(len(calls), 10)
        self.assertIsNone(gpu["power_limit_w"])

    def test_cache_skips_static_queries_until_ttl_and_returns_independent_copies(self):
        calls, cache = [], {}
        command = self.command_factory(calls)
        first = detect_nvidia_gpus(command, cache=cache, now=1000)[0]
        first["supported_graphics_clocks_mhz"].append(9999)
        second = detect_nvidia_gpus(command, cache=cache, now=1030)[0]
        self.assertEqual(len(calls), 4)
        self.assertEqual(second["supported_graphics_clocks_mhz"], [1500, 1800])
        detect_nvidia_gpus(command, cache=cache, now=1000+601)
        self.assertEqual(len(calls), 7)


class LogTests(unittest.TestCase):
    def test_same_text_is_reused_but_window_is_reapplied(self):
        now = 1_800_000_000
        text = "\n".join([rate_line(now-1000, 70), rate_line(now-10, 80)])
        first = parse_gpu_log(text, "GPU-cache-a", 0, now)
        self.assertEqual([rate for _, rate in first["rate_points"]], [80])
        later = parse_gpu_log(text, "GPU-cache-a", 0, now+900)
        self.assertEqual(later["rate_points"], [])
        self.assertEqual(later["hashrate"], 80)

    def test_future_line_becomes_current_after_its_time(self):
        now = 1_800_000_000
        text = "\n".join([rate_line(now-10, 80), rate_line(now+60, 90)])
        self.assertEqual(parse_gpu_log(text, "GPU-cache-b", 0, now)["hashrate"], 80)
        self.assertEqual(parse_gpu_log(text, "GPU-cache-b", 0, now+60)["hashrate"], 90)

    def test_incremental_tail_matches_full_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"mining.log"
            path.write_bytes(b"".join(f"line {i:05d}\n".encode() for i in range(3000)))
            first = read_log_tail(path, 4096)
            self.assertEqual(read_log_tail(path, 4096), first)
            with path.open("ab") as handle:
                handle.write(b"".join(f"more {i:05d}\n".encode() for i in range(50)))
            incremental = read_log_tail(path, 4096)
            gpu_telemetry._TAILS.clear()
            self.assertEqual(incremental, read_log_tail(path, 4096))
            self.assertTrue(incremental.endswith("more 00049\n"))

    def test_rewritten_log_is_read_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"mining.log"
            path.write_text("old content A\n"*100)
            read_log_tail(path, 4096)
            path.write_text("new content B\n"*120)
            self.assertNotIn("old", read_log_tail(path, 4096))


class FileCacheTests(unittest.TestCase):
    def test_loader_runs_again_only_after_change_and_copy_is_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"settings.json"
            path.write_text('{"wallet":"a"}')
            os.utime(path, (time.time()-10, time.time()-10))
            loads = []
            loader = lambda p: (loads.append(1) or json.loads(Path(p).read_text()))
            file_cache.cached_load(path, loader)["wallet"] = "changed"
            self.assertEqual(file_cache.cached_load(path, loader), {"wallet": "a"})
            self.assertEqual(len(loads), 1)
            path.write_text('{"wallet":"bb"}')
            self.assertEqual(file_cache.cached_load(path, loader), {"wallet": "bb"})

    def test_recent_same_size_write_is_never_served_from_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"settings.json"
            path.write_text('{"wallet":"a"}')
            self.assertEqual(file_cache.cached_load(path, lambda p: json.loads(Path(p).read_text())), {"wallet": "a"})
            stat = path.stat()
            path.write_text('{"wallet":"b"}')
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertEqual(file_cache.cached_load(path, lambda p: json.loads(Path(p).read_text())), {"wallet": "b"})

    def test_glob_sees_new_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory/"mining-1.log").write_text("a")
            os.utime(directory, (time.time()-10, time.time()-10))
            self.assertEqual(len(file_cache.cached_glob(directory, "mining-*.log")), 1)
            (directory/"mining-2.log").write_text("b")

            found = file_cache.cached_glob(directory, "mining-*.log")
            self.assertEqual(sorted(p.name for p in found), ["mining-1.log", "mining-2.log"])


@unittest.skipUnless(os.name == "nt", "Toolhelp32 é do Windows")
class ProcessListTests(unittest.TestCase):
    def test_current_process_is_listed(self):
        from process_list import pid_running, processes
        self.assertTrue(pid_running(os.getpid()))
        self.assertTrue(any(pid == os.getpid() for pid, _ in processes()))


class HistoryTests(unittest.TestCase):
    def test_archive_is_one_batch_and_restart_resumes_from_committed_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            log = root/"mining.log"
            log.write_text("".join(f"[2026-11-02 10:00:{i:02d}] linha {i}\n" for i in range(40)))
            store = HistoryStore(root/"history.sqlite3", "prl1wallet")
            store.archive_log(log)
            self.assertTrue(store.flush())
            store.close()
            self.assertEqual(json.loads((root/"history-log-offsets.json").read_text())[str(log)], log.stat().st_size)
            reopened = HistoryStore(root/"history.sqlite3", "prl1wallet")
            with patch.object(reopened, "submit", wraps=reopened.submit) as submit:
                reopened.archive_log(log)
                self.assertFalse([call for call in submit.call_args_list if call.args[0] == "events"])
            with log.open("a") as handle:
                handle.write("[2026-11-02 10:01:00] nova\n")
            reopened.archive_log(log)
            self.assertTrue(reopened.flush())
            reopened.close()
            with closing(sqlite3.connect(root/"history.sqlite3")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='miner_log'").fetchone()[0], 41)

    def test_rig_rows_do_not_copy_raw_log_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = HistoryStore(Path(temporary)/"history.sqlite3", "prl1wallet")
            device = {"uuid": "GPU-x", "collected_at": 100, "gpu": {"temperature.gpu": 60},
                      "miner": {"hashrate": 80, "hashrate_at": 99, "recent_logs": "x"*3000, "rate_points": [[99, 80]]}}
            store.rig_telemetry({"devices": [device]})
            self.assertTrue(store.flush())
            store.close()
            with closing(sqlite3.connect(Path(temporary)/"history.sqlite3")) as db:
                miner = json.loads(db.execute("SELECT miner_json FROM gpu_telemetry").fetchone()[0])
                journal = db.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(miner, {"hashrate": 80, "hashrate_at": 99})
            self.assertEqual(journal, "wal")


class ServiceSnapshotTests(unittest.TestCase):
    def test_fresh_service_snapshot_replaces_driver_query_and_log_parse(self):
        import gpu_runtime
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            device = {"vendor": "NVIDIA", "index": 0, "uuid": "GPU-snap", "name": "RTX 3070", "enabled": True,
                      "wallet": "prl1w", "log_dir": str(root/"gpu"), "state_dir": str(root/"gpu")}
            (root/"gpu").mkdir()
            now = time.time()
            sample = {"uuid": "GPU-snap", "temperature.gpu": 61, "sampled_at": now-1, "error": None}
            (root/"gpu"/"latest.json").write_text(json.dumps({"gpu_uuid": "GPU-snap", "collected_at": now-1, "gpu": sample,
                "miner": {"hashrate": 80, "hashrate_at": now-3, "rate_points": []}, "log_error": None}))
            (root/"rig-manager.json").write_text(json.dumps({"sampled_at": now, "devices": {"GPU-snap": {
                "miner_log": str(root/"gpu"/"mining.log"), "miner_pid": 7, "state": "running"}}}))
            reader = gpu_runtime.RigTelemetry()
            reader.detected, reader.detected_at = [device], now
            with patch("gpu_runtime.STATE_DIR", root), patch("gpu_runtime.cached_settings", return_value={}), \
                 patch("gpu_runtime.resolve_devices", return_value=[device]), \
                 patch("gpu_runtime.read_gpu_samples") as driver, patch("gpu_runtime.read_device_payload") as parse:
                result = reader.sample({7})
            driver.assert_not_called()
            parse.assert_not_called()
            entry = result["devices"][0]
            self.assertEqual(entry["gpu"]["temperature.gpu"], 61)
            self.assertEqual(entry["miner"]["hashrate"], 80)
            self.assertTrue(entry["miner_running"])

    def test_stale_service_snapshot_is_not_used(self):
        import gpu_runtime
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/"latest.json").write_text(json.dumps({"gpu_uuid": "GPU-snap", "collected_at": time.time()-30,
                                                        "gpu": {"uuid": "GPU-snap"}, "miner": {}}))
            device = {"uuid": "GPU-snap", "log_dir": str(root)}
            self.assertIsNone(gpu_runtime.service_snapshot(device, {"miner_log": "x"}, True, time.time()))


@unittest.skipUnless(os.name == "nt", "PowerShell do Windows")
class ServiceScriptInterpreterTests(unittest.TestCase):
    def test_pythonw_from_the_panel_is_replaced_by_console_python(self):
        """pythonw.exe returns no stdout; the update used to stop the service and then abort the start."""
        import subprocess
        root = Path(__file__).resolve().parent/"mining/windows"
        with tempfile.TemporaryDirectory() as temporary:
            for name in ("python.exe", "pythonw.exe"):
                (Path(temporary)/name).write_bytes(b"")
            for script in ("Iniciar-Rig.ps1", "Atualizar-Rig.ps1"):
                text = (root/script).read_text(encoding="utf-8")
                line = next(item for item in text.splitlines() if "-ieq 'pythonw.exe'" in item)
                self.assertLess(text.index(line), text.index("IsInRole"), script)
                command = f"$Python='{Path(temporary)/'pythonw.exe'}'; {line}; Write-Output $Python"
                result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(Path(result.stdout.strip()).name, "python.exe", script)


class CommandLineCacheTests(unittest.TestCase):

    @windows_only("ctypes.WinDLL")
    def test_command_line_is_queried_once_per_process_identity(self):
        from rig import ProcessBackend
        backend = ProcessBackend()
        identity = {"pid": 42, "created_at": "1", "executable": "C:/m/SRBMiner-MULTI.exe"}
        with patch.object(ProcessBackend, "_kernel"), \
             patch.object(ProcessBackend, "_metadata", side_effect=lambda *_: dict(identity)), \
             patch("rig.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '"C:/m/SRBMiner-MULTI.exe" --gpu-id 0'
            first = backend.inspect(42, command_line=True)
            second = backend.inspect(42, command_line=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(first["argv"], second["argv"])
        self.assertEqual(first["argv"][1:], ["--gpu-id", "0"])


if __name__ == "__main__":
    unittest.main()
