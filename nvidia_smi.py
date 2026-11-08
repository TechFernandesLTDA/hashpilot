"""Localização única do `nvidia-smi` (PATH e, no Windows, System32)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


def nvidia_smi_path() -> str | None:
    """Caminho do executável do driver, ou None quando ausente (nunca um palpite)."""
    executable = shutil.which("nvidia-smi")
    if not executable and os.name == "nt":
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe"
        executable = str(candidate) if candidate.is_file() else None
    return executable
