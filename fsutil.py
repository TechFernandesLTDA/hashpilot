"""Escrita atômica de JSON compartilhada por painel, serviço e scripts.

Antes vivia em `contribution.py`, o que obrigava quinze módulos a importar o
proxy de contribuição só para salvar arquivos; `contribution.atomic_json`
continua disponível como reexportação.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time


def atomic_json(path: str | os.PathLike[str], payload: object, *, durable: bool = False, compact: bool = False) -> None:
    """`compact` serve para snapshots reescritos a cada ciclo; arquivos lidos por pessoas mantêm a indentação."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open('w', encoding='utf-8') as handle:
        if compact:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        if durable:
            handle.flush()
            os.fsync(handle.fileno())
    # Windows readers/antivirus may briefly deny replacement of an open file.
    # Retry only the atomic replacement: readers keep the previous complete JSON.
    for attempt in range(21):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 20:
                raise
            time.sleep(0.05)
