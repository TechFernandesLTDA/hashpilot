"""Log de diagnóstico local com rotação (`hashpilot.log`), só biblioteca padrão.

Erros continuam aparecendo como estado explícito na interface; este arquivo
guarda o traceback que a interface resume em uma linha. Sem `configure`, nada
é escrito (NullHandler), o que mantém testes e imports silenciosos.
Não registra carteira completa, chaves ou conteúdo de settings.json.

Os arquivos ficam em `<pasta>/diagnostics/`, nunca direto na pasta de logs: o
painel legado trata todo `*.log` de LOG_DIR como log do minerador e o importa
para o histórico permanente (sem expurgo). Um diagnóstico ali seria arquivado
como mineração.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
import time

ROOT = "hashpilot"
SUBDIRECTORY = "diagnostics"
MAX_BYTES = 1_000_000
# Erro idêntico no mesmo ponto só volta ao arquivo depois deste intervalo.
REPEAT_SECONDS = 600.0
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)s %(process)d %(threadName)s %(name)s: %(message)s"

logging.getLogger(ROOT).addHandler(logging.NullHandler())
_LAST: dict[str, tuple[str, float]] = {}
_LAST_LOCK = threading.Lock()


def _first_time(key: str, signature: str, now: float | None) -> bool:
    """True na 1ª ocorrência, quando o erro muda ou após REPEAT_SECONDS; repetições idênticas são omitidas."""
    moment = time.monotonic() if now is None else now
    with _LAST_LOCK:
        previous = _LAST.get(key)
        if previous and previous[0] == signature and moment - previous[1] < REPEAT_SECONDS:
            return False
        _LAST[key] = (signature, moment)
        return True


def exception_once(logger: logging.Logger, key: str, message: str, *args: object, now: float | None = None) -> bool:
    """`logger.exception` sem repetir o mesmo traceback a cada ciclo de 2 s (preserva a 1ª ocorrência no anel)."""
    error = sys.exc_info()[1]
    if not _first_time(key, f"{type(error).__name__}: {error}", now):
        return False
    logger.exception(message, *args)
    return True


def warning_once(
    logger: logging.Logger, key: str, signature: str, message: str, *args: object, now: float | None = None
) -> bool:
    """Aviso sem traceback, com a mesma regra de repetição de `exception_once`."""
    if not _first_time(key, signature, now):
        return False
    logger.warning(message, *args)
    return True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")


def diagnostics_dir(directory: str | Path) -> Path:
    """Subpasta própria dos diagnósticos, fora do alcance de `LOG_DIR.glob("*.log")`."""
    return Path(directory) / SUBDIRECTORY


def configure(directory: str | Path, filename: str = "hashpilot.log", level: int = logging.INFO) -> Path | None:
    """Ativa o arquivo rotativo uma única vez por processo; falha de disco não derruba o app."""
    root = logging.getLogger(ROOT)
    path = diagnostics_dir(directory) / filename
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path.resolve():
            return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True)
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    return path
