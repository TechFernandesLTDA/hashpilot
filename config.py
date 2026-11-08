"""Configuração portátil do HashPilot. Nenhuma chave privada é necessária.

As constantes do módulo continuam disponíveis para os imports existentes
(`from config import STATE_DIR`), mas são calculadas por funções puras que
recebem plataforma e ambiente: assim a regra "somente Windows" fica num ponto
único e testável. Fora do Windows, apenas a variável `HASHPILOT_TEST_STATE_DIR`
(usada por `tools/run_tests.py`) permite importar o módulo, sempre com um
diretório temporário — nunca para executar o produto.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping
from contribution import AUTHOR_WALLET

DEFAULT_WALLET = AUTHOR_WALLET
DEFAULT_POOL = "prl.kryptex.network:7048"
TEST_STATE_ENV = "HASHPILOT_TEST_STATE_DIR"
UNSUPPORTED_PLATFORM = "O HashPilot está disponível somente para Windows 10/11 de 64 bits."


def resolve_state_dir(platform: str | None = None, environ: Mapping[str, str] | None = None) -> Path:
    """Diretório de estado; fora do Windows só existe para testes isolados."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "PearlMonitor"
    override = environ.get(TEST_STATE_ENV)
    if override:
        return Path(override) / "PearlMonitor"
    raise RuntimeError(UNSUPPORTED_PLATFORM)


def load_settings(path: Path) -> dict[str, Any]:
    """Lê settings.json; ausente vira {}, ilegível é erro explícito (nunca padrão silencioso)."""
    try:
        settings = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Não foi possível ler a configuração local em {path}: {error}") from error
    if not isinstance(settings, dict):
        raise RuntimeError("settings.json precisa conter um objeto JSON")
    return settings


def log_dir(state_dir: Path, settings: Mapping[str, Any]) -> Path:
    default_logs = state_dir.parent / "Mining/Pearl/logs"
    return Path(os.path.expandvars(str(settings.get("log_dir") or default_logs)))


STATE_DIR = resolve_state_dir()
SETTINGS_PATH = STATE_DIR / "settings.json"
SETTINGS = load_settings(SETTINGS_PATH)
WALLET = str(SETTINGS.get("wallet", DEFAULT_WALLET)).strip()
POOL = str(SETTINGS.get("pool", DEFAULT_POOL)).strip()
default_logs = STATE_DIR.parent / "Mining/Pearl/logs"
LOG_DIR = log_dir(STATE_DIR, SETTINGS)
