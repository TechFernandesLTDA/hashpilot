"""Restaura o snapshot versionado somente em um perfil local ainda vazio."""
from __future__ import annotations
from contextlib import closing
import json
import shutil
import sqlite3
from pathlib import Path
from config import STATE_DIR

BUNDLE = Path(__file__).resolve().parent / "portable-data"

def restore_bundled_data(state_dir: Path = STATE_DIR) -> list[str]:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    restored = []
    database = state_dir / "history.sqlite3"
    source_db = BUNDLE / "history.sqlite3"
    if not database.exists() and source_db.is_file():
        with closing(sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot de histórico inválido")
            with closing(sqlite3.connect(database)) as destination:
                source.backup(destination)
        restored.append(database.name)
    cache = state_dir / "finance-cache.json"
    source_cache = BUNDLE / "finance-cache.json"
    if not cache.exists() and source_cache.is_file():
        json.loads(source_cache.read_text(encoding="utf-8-sig"))
        shutil.copy2(source_cache, cache)
        restored.append(cache.name)
    return restored

if __name__ == "__main__":
    print("Restaurados: " + (", ".join(restore_bundled_data()) or "nenhum; dados locais preservados"))
