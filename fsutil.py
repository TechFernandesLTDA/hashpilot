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


def torn_file(data: bytes) -> bool:
    """Arquivo vazio ou só com NUL/espaços: o rastro de uma queda de energia.

    No NTFS o rename do `atomic_json` sobrevive à queda, mas os dados do temporário
    não gravados com fsync voltam como bytes zerados. Nenhum JSON válido tem essa forma.
    """
    return not data.strip(b"\x00 \t\r\n")


def quarantine_torn(path: str | os.PathLike[str]) -> Path | None:
    """Move um arquivo danificado por queda para `corrompidos/` ao lado e devolve o destino.

    Devolve None (sem mexer em nada) quando o arquivo não existe ou tem conteúdo de verdade.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    if not torn_file(data):
        return None
    folder = path.parent / "corrompidos"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}.{os.getpid()}"
    path.replace(target)
    return target


def durable_text(path: str | os.PathLike[str], text: str) -> None:
    """Grava texto UTF-8 com fsync: o temporário de um rename que precisa sobreviver a uma queda."""
    with Path(path).open('w', encoding='utf-8') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


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
