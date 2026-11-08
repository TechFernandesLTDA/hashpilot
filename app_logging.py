"""Log de diagnóstico local com rotação (`hashpilot.log`), só biblioteca padrão.

Erros continuam aparecendo como estado explícito na interface; este arquivo
guarda o traceback que a interface resume em uma linha. Sem `configure`, nada
é escrito (NullHandler), o que mantém testes e imports silenciosos.
Não registra carteira completa, chaves ou conteúdo de settings.json.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = "hashpilot"
MAX_BYTES = 1_000_000
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)s %(process)d %(threadName)s %(name)s: %(message)s"

logging.getLogger(ROOT).addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{name}")


def configure(directory, filename: str = "hashpilot.log", level: int = logging.INFO) -> Path | None:
    """Ativa o arquivo rotativo uma única vez por processo; falha de disco não derruba o app."""
    root = logging.getLogger(ROOT)
    path = Path(directory) / filename
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
