"""Exporta um snapshot consistente dos dados do HashPilot para este repositório privado."""
from __future__ import annotations
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import shutil
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import STATE_DIR, LOG_DIR, WALLET, POOL

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "portable-data"

def export_snapshot():
    DEST.mkdir(parents=True, exist_ok=True)
    database = STATE_DIR / "history.sqlite3"
    if not database.is_file():
        raise RuntimeError(f"Histórico ainda não existe: {database}")
    temporary = DEST / ".history.sqlite3.tmp"
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(temporary)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Snapshot não passou na verificação de integridade")
            counts = {name: target.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                      for (name,) in target.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    temporary.replace(DEST / "history.sqlite3")
    for name in ("finance-cache.json", "finance-latest.json", "latest.json", "restart-evidence.json"):
        source_file = STATE_DIR / name
        if source_file.is_file():
            # Arquivos são substituídos atomicamente pelo coletor: cada leitura é um documento completo.
            payload = json.loads(source_file.read_text(encoding="utf-8-sig"))
            (DEST / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    settings = {"wallet": WALLET, "pool": POOL, "log_dir": str(LOG_DIR)}
    (DEST / "settings-windows.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    logs = DEST / "miner-logs"
    logs.mkdir(exist_ok=True)
    if LOG_DIR.is_dir():
        for path in LOG_DIR.iterdir():
            if path.is_file() and path.suffix.lower() in (".log", ".json", ".txt", ".csv"):
                shutil.copy2(path, logs / path.name)
    manifest = {"exported_at": dt.datetime.now(dt.timezone.utc).isoformat(), "wallet": WALLET,
                "tables": counts, "integrity_check": "ok", "source": "SQLite online backup; monitor may remain active"}
    (DEST / "backup-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))

if __name__ == "__main__":
    export_snapshot()
