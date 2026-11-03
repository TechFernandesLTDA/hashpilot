"""Caches pequenos para leituras repetidas a cada ciclo de 2 s, invalidados pelo próprio arquivo."""
from __future__ import annotations
import copy
import os
from pathlib import Path
import threading
import time

# Windows mtime can tick at ~16 ms: a file changed this recently is always re-read,
# so two same-size writes in one tick can never leave a stale wallet or settings value.
SETTLE_SECONDS=2
_LOCK=threading.Lock()
_FILES={}
_GLOBS={}


def signature(path):
    try:
        stat=os.stat(path)
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns,stat.st_size


def _settled(current):
    return current is not None and time.time()-current[0]/1e9>=SETTLE_SECONDS


def cached_load(path,loader):
    """Chama `loader(path)` só quando mtime/tamanho mudam; devolve uma cópia que pode ser alterada.

    Exceções do loader não são guardadas: a próxima chamada tenta de novo.
    """
    key=str(Path(path))
    current=signature(path)
    with _LOCK:
        entry=_FILES.get(key)
        if entry and entry[0]==current and current is not None:
            return copy.deepcopy(entry[1])
    value=loader(path)
    if _settled(current) and signature(path)==current:
        with _LOCK:_FILES[key]=(current,copy.deepcopy(value))
    return value


def cached_glob(directory,pattern):
    """Glob refeito só quando o diretório muda (criar, renomear ou apagar arquivo altera seu mtime)."""
    key=(str(Path(directory)),pattern)
    current=signature(directory)
    with _LOCK:
        entry=_GLOBS.get(key)
    if entry and current is not None and entry[0]==current:
        return list(entry[1])
    found=list(Path(directory).glob(pattern))
    if _settled(current) and signature(directory)==current:
        with _LOCK:_GLOBS[key]=(current,found)
    return found


def newest(paths):
    """Arquivo mais recente; um arquivo removido entre o glob e o stat é ignorado."""
    best=None
    for path in paths:
        try:stamp=path.stat().st_mtime
        except OSError:continue
        if best is None or stamp>best[0]:best=(stamp,path)
    return best[1] if best else None


def clear():
    with _LOCK:
        _FILES.clear();_GLOBS.clear()
