"""Perfil opt-in da RTX 3070 de referência do autor (UUID fixo, presets medidos).

Separado de rig.py para que o fluxo histórico dessa unidade não se misture ao
iniciador genérico: nenhuma função aqui vale para outro modelo ou outra placa.
"""
from __future__ import annotations
from pathlib import Path
import re
from typing import Any

REFERENCE_RTX3070_UUID="GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75"


def reference_device_confirmed(gpu: dict[str, Any]) -> bool:
    return (gpu.get("uuid")==REFERENCE_RTX3070_UUID and str(gpu.get("name","")).endswith("RTX 3070")
        and gpu.get("power_default_w")==240 and 1560 in gpu.get("supported_graphics_clocks_mhz",[]))


def reference_memory_clock(miner_path: str | Path, settings: dict[str, Any]) -> int:
    """Explicit author-board opt-in; older miners keep the established memory clock."""
    version=re.fullmatch(r'SRBMiner-Multi-(\d+)-(\d+)-(\d+)',Path(miner_path).parent.name)
    return 405 if settings.get('reference_memory_mhz')==405 and version and tuple(map(int,version.groups()))>=(3,8,4) else 5001


def reference_start_clock(gpu: dict[str, Any], settings: dict[str, Any]) -> int:
    # Only the measured author-board restart preset is eligible; no arbitrary OC.
    chosen=settings.get('reference_start_core_mhz')
    return int(chosen) if chosen in (1710,1725) and chosen in gpu.get('supported_graphics_clocks_mhz',[]) else 1560
