"""Leituras do painel legado: processos, nvidia-smi da GPU 0 e log do minerador. Sem Tk."""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path
import subprocess
import time

from gpu_telemetry import _parse_gpu_log, read_log_tail
from nvidia_smi import nvidia_smi_path

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def selected_gpu_context(data, identifier, fallback_wallet):
    devices=((data or {}).get('rig') or {}).get('devices') or []
    device=next((item for item in devices if item.get('uuid')==identifier),None)
    return (identifier,device.get('wallet') or fallback_wallet) if device else None


def run(command: list[str], timeout: float = 3) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:220])
    return result.stdout


def miner_process_ids() -> set[int]:
    """PIDs do SRBMiner pelo Toolhelp32; `tasklist` só se a API do Windows falhar."""
    try:
        from process_list import process_ids
        return process_ids("SRBMiner-MULTI.exe")
    except OSError:
        output = run(["tasklist", "/FI", "IMAGENAME eq SRBMiner-MULTI.exe", "/FO", "CSV", "/NH"])
        return {int(row[1]) for row in csv.reader(output.splitlines())
                if len(row) > 1 and row[0].lower() == "srbminer-multi.exe" and row[1].isdigit()}


def number(value: str) -> float | None:

    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def read_gpu() -> dict:
    fields = ["name", "driver_version", "temperature.gpu", "power.draw", "power.limit",
              "clocks.gr", "clocks.mem", "utilization.gpu", "utilization.memory",
              "memory.used", "memory.total", "fan.speed", "pstate"]
    smi = nvidia_smi_path()
    if not smi:
        raise RuntimeError("Sensores NVIDIA indisponíveis: nvidia-smi não encontrado.")
    output = run([smi, "-i", "0", "--query-gpu=" + ",".join(fields), "--format=csv,noheader,nounits"])
    row = next(csv.reader(io.StringIO(output)))
    result = dict(zip(fields, [x.strip() for x in row]))
    for field in fields[2:-1]:
        result[field] = number(result.get(field, ""))
    result["sampled_at"] = time.time()
    return result


def tail(path: Path, limit: int = 160_000) -> str:
    """Fim do log legado, lido de forma incremental (mesma leitura do serviço, com UTF-16)."""
    return read_log_tail(path, limit)


def parse_log(text: str, now: float | None = None) -> dict:
    """Log legado de uma GPU: mesmo parser do rig (`gpu_telemetry`), sem cache.

    Antes havia aqui regex próprias que só reconheciam `GPU0` e médias em TH/s;
    agora qualquer unidade e o rótulo `GPU[0]` são tratados como no serviço.
    """
    now = time.time() if now is None else now
    result, _future = _parse_gpu_log(text, None, 0, now, True)
    for key in ("gpu_uuid", "gpu_index", "log_gpu_index", "isolated_log", "connection_at", "last_job_at", "last_log_at", "pause_at", "version"):
        result.pop(key, None)
    return result
