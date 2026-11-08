"""Formato do log de texto do SRBMiner: fonte única dos padrões usados no projeto.

O SRBMiner não expõe API ao HashPilot; telemetria do minerador, histórico e
importação de logs dependem destas expressões. Se o formato mudar numa versão
nova do minerador, o ajuste é feito aqui e conferido por `test_miner_log.py`,
que usa linhas reais anonimizadas de cada versão suportada.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]")
GPU_LABEL = re.compile(r"\bGPU\s*(?:\[\s*)?(\d+)(?:\s*\])?(?!\d)")
RATE = re.compile(r"\bGPU\s*(?:\[\s*)?(\d+)(?:\s*\])?\s+.*?:\s*([\d.]+)\s+([kMGTPE]?H)/s\s+\[(.*?)\]")
AVERAGE = re.compile(r"Average hashrate:\s*1m\s+([\d.]+)\s+([kMGTPE]?H)/s\s*\|\s*15m\s+([\d.]+)\s+([kMGTPE]?H)/s", re.I)
COUNTERS = re.compile(r"([A-Z]+):([\d.]+)")
DEVICE_BUS = re.compile(r"\bBUS\s*:\s*([0-9a-f]{1,2})\b", re.I)
DEVICE_INDEX = re.compile(r"\bGPU\s*(\d+)\s*[:\[]", re.I)
UNITS = {"H": 1e-12, "kH": 1e-9, "MH": 1e-6, "GH": 1e-3, "TH": 1, "PH": 1e3, "EH": 1e6}
CONNECTION_LOST = ("disconnected", "connection failed", "reconnecting", "connection lost")

_STAMPS: dict[str, float | None] = {}


def parse_stamp(text: str) -> float | None:
    """Converte o texto entre colchetes (hora local) em epoch; inválido vira None."""
    try:
        return dt.datetime.strptime(text, STAMP_FORMAT).timestamp()
    except ValueError:
        return None


def log_timestamp(line: str) -> float | None:
    match = STAMP.match(line)
    if not match:
        return None
    text = match[1]
    if text in _STAMPS:
        return _STAMPS[text]
    value = parse_stamp(text)
    if len(_STAMPS) >= 4096:
        _STAMPS.clear()
    _STAMPS[text] = value
    return value


def parse_miner_device_map(text: str, devices: list[dict[str, Any]]) -> dict[str, int]:
    """Match SRBMiner IDs to NVIDIA UUIDs by unique PCI bus, never model name."""
    buses: dict[int, set[int]] = {}
    for line in text.splitlines():
        index = DEVICE_INDEX.search(line)
        bus = DEVICE_BUS.search(line)
        if index and bus and "nvidia" in line.lower():
            buses.setdefault(int(bus[1], 16), set()).add(int(index[1]))
    result: dict[str, int] = {}
    seen_buses: set[int] = set()
    for gpu in devices:
        pci = re.fullmatch(r"[0-9a-fA-F]{4,8}:([0-9a-fA-F]{2}):[0-9a-fA-F]{2}\.[0-7]", str(gpu.get("pci_bus_id", "")))
        if not pci:
            raise RuntimeError("Endereço PCI da NVIDIA ausente; seleção do minerador não confirmada")
        bus_number = int(pci[1], 16)
        candidates = buses.get(bus_number, set())
        if bus_number in seen_buses or len(candidates) != 1:
            raise RuntimeError("Mapeamento PCI/ID do SRBMiner ambíguo ou ausente; mineração não iniciada")
        seen_buses.add(bus_number)
        result[gpu["uuid"]] = next(iter(candidates))
    if len(set(result.values())) != len(result):
        raise RuntimeError("Um ID do minerador correspondeu a mais de uma GPU")
    return result
