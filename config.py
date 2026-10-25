"""Configuração portátil do HashPilot. Nenhuma chave privada é necessária."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from contribution import AUTHOR_WALLET

DEFAULT_WALLET = AUTHOR_WALLET
if sys.platform == "win32":
    STATE_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "PearlMonitor"
else:
    raise RuntimeError("O HashPilot está disponível somente para Windows 10/11 de 64 bits.")
SETTINGS_PATH = STATE_DIR / "settings.json"
try:
    SETTINGS = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
except FileNotFoundError:
    SETTINGS = {}
except (OSError, ValueError) as error:
    raise RuntimeError(f"Não foi possível ler a configuração local em {SETTINGS_PATH}: {error}") from error
if not isinstance(SETTINGS, dict):
    raise RuntimeError("settings.json precisa conter um objeto JSON")
WALLET = str(SETTINGS.get("wallet", DEFAULT_WALLET)).strip()
POOL = str(SETTINGS.get("pool", "prl.kryptex.network:7048")).strip()
default_logs = STATE_DIR.parent / "Mining/Pearl/logs"
LOG_DIR = Path(os.path.expandvars(str(SETTINGS.get("log_dir") or default_logs)))
