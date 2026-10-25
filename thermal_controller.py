"""Controle térmico gradual da RTX 3070. Executado pelo supervisor Windows elevado."""
from __future__ import annotations
import argparse
import csv
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from thermal import ThermalPolicy

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

class NvidiaBackend:
    def __init__(self, miner_pid: int, dry_run=False):
        if sys.platform != "win32":
            raise RuntimeError("O controlador de clocks desta RTX 3070 é específico do Windows")
        self.dry_run = dry_run
        self.smi = shutil.which("nvidia-smi")
        if not self.smi:
            raise RuntimeError("nvidia-smi não encontrado")
        if not dry_run and not ctypes.WinDLL("shell32").IsUserAnAdmin():
            raise RuntimeError("É necessário executar o supervisor como administrador")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetExitCodeProcess.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.OpenProcess(0x1000, False, miner_pid)
        if not self.handle:
            raise RuntimeError("Não foi possível acompanhar o processo do minerador")
        sample = self.read()
        if not sample["name"].endswith("RTX 3070"):
            self.close()
            raise RuntimeError("Este controlador foi preparado somente para a RTX 3070")

    def command(self, *arguments):
        completed = subprocess.run([self.smi, "-i", "0", *map(str, arguments)], capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=4, creationflags=CREATE_NO_WINDOW)
        if completed.returncode:
            raise RuntimeError((completed.stderr or completed.stdout).strip()[:240])
        return completed.stdout.strip()

    def read(self):
        row = next(csv.reader(self.command("--query-gpu=name,temperature.gpu,power.draw,clocks.gr,clocks.mem,power.limit",
                                          "--format=csv,noheader,nounits").splitlines()))
        values = [float(v.strip()) for v in row[1:]]
        if not all(math.isfinite(v) for v in values) or not 0 <= values[0] <= 120:
            raise ValueError("Leitura de sensor inválida")
        return dict(zip(("name", "temperature_c", "power_w", "actual_clock_mhz", "memory_clock_mhz", "power_limit_w"),
                        [row[0].strip(), *values]))

    def alive(self):
        code = wintypes.DWORD()
        return bool(self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code))) and code.value == 259

    def set_clock(self, clock):
        clock = int(clock)
        if clock < 1500 or clock > 1800 or clock % 15:
            raise ValueError("Clock fora do intervalo validado do controlador")
        if not self.dry_run:
            self.command("-lgc", f"{clock},{clock}")

    def set_power(self, watts):
        if watts not in (150, 240):
            raise ValueError("Limite de potência inválido")
        if not self.dry_run:
            self.command("-pl", watts)

    def close(self):
        if getattr(self, "handle", None):
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def atomic_json(path, payload):
    from contribution import atomic_json as persist_json
    persist_json(path,payload)


def control(args):
    args.status_dir.mkdir(parents=True, exist_ok=True)
    prefix = "thermal-dry-run" if args.dry_run else "thermal-control"
    status_file = args.status_dir / f"{prefix}.json"
    events_file = args.status_dir / f"{prefix}-events.jsonl"
    backend = None
    policy = ThermalPolicy(min_clock=1500, max_clock=1800, initial_clock=args.initial_clock)
    current = args.initial_clock
    failures = valid_reads = iterations = 0
    degraded = False
    previous = None
    status = {"enabled": False, "mode": "starting", "reason": "Iniciando controle térmico",
              "current_clock_mhz": current, "desired_clock_mhz": current,
              "min_clock_mhz": 1500, "max_clock_mhz": 1800, "target_band_c": [75, 78],
              "emergency_pause_c": 84, "sampled_at": time.time(), "error": None,
              "controller_pid": os.getpid(), "miner_pid": args.miner_pid, "dry_run": args.dry_run}
    try:
        backend = NvidiaBackend(args.miner_pid, args.dry_run)
        while backend.alive():
            started = time.monotonic()
            try:
                sample = backend.read()
                failures = 0
                valid_reads += 1
                if degraded and valid_reads >= 3:
                    backend.set_power(240)
                    degraded = False
                decision = policy.step(sample["temperature_c"], started)
                if decision.clock_mhz != current:
                    backend.set_clock(decision.clock_mhz)
                    current = decision.clock_mhz
                status.update(sample)
                status.update(enabled=not args.dry_run, mode=decision.mode, reason=decision.reason,
                              current_clock_mhz=int(sample["actual_clock_mhz"]), commanded_clock_mhz=current, desired_clock_mhz=decision.clock_mhz,
                              sampled_at=time.time(), error=None, consecutive_errors=0)
                if degraded:
                    status.update(mode="sensor_recovery", reason="Confirmando sensores antes de recuperar a potência")
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                failures += 1
                valid_reads = 0
                status.update(enabled=not args.dry_run, mode="sensor_error", reason="Falha de leitura/controle; aguardando nova amostra",
                              sampled_at=time.time(), error=str(error), consecutive_errors=failures)
                if failures >= 3:
                    backend.set_clock(1500)
                    backend.set_power(150)
                    current = 1500
                    degraded = True
                    policy = ThermalPolicy(min_clock=1500, max_clock=1800, initial_clock=1500)
                    status.update(current_clock_mhz=1500, desired_clock_mhz=1500, mode="sensor_failsafe",
                                  reason="Sensores indisponíveis: reduzido para 1500 MHz / 150 W")
            marker = (status["mode"], current, status.get("error"))
            if marker != previous:
                with events_file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(status, ensure_ascii=False) + "\n")
                previous = marker
            atomic_json(status_file, status)
            iterations += 1
            if args.samples and iterations >= args.samples:
                break
            time.sleep(max(.05, 2 - (time.monotonic() - started)))
        return 0
    except KeyboardInterrupt:
        status.update(reason="Controle encerrado pelo supervisor")
        return 0
    except Exception as error:
        status.update(mode="failed", error=str(error), reason="Falha no controlador; supervisor deve encerrar o minerador")
        return 1
    finally:
        status.update(enabled=False, sampled_at=time.time())
        if status["mode"] != "failed":
            status["mode"] = "stopped"
        try:
            atomic_json(status_file, status)
        finally:
            if backend:
                backend.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--miner-pid", type=int, required=True)
    parser.add_argument("--initial-clock", type=int, default=1740)
    parser.add_argument("--status-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--samples", type=int, default=0)
    args = parser.parse_args()
    if args.miner_pid <= 0 or args.initial_clock not in range(1500, 1801, 15) or args.samples < 0:
        parser.error("PID, clock ou número de amostras inválido")
    return control(args)

if __name__ == "__main__":
    raise SystemExit(main())
