from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import bootstrap

class PortableBootstrapTests(unittest.TestCase):
    def prepare(self, directory, value):
        directory.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(directory / "history.sqlite3")) as conn:
            conn.execute("CREATE TABLE samples(value TEXT)")
            conn.execute("INSERT INTO samples VALUES (?)", (value,))
            conn.commit()
        (directory / "finance-cache.json").write_text(json.dumps({"marker": value}), encoding="utf-8")

    def test_first_run_restores_consistent_database_and_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, local = root / "bundle", root / "local"
            self.prepare(bundle, "snapshot")
            with patch.object(bootstrap, "BUNDLE", bundle):
                self.assertEqual(set(bootstrap.restore_bundled_data(local)), {"history.sqlite3", "finance-cache.json"})
                self.assertEqual(bootstrap.restore_bundled_data(local), [])
            with closing(sqlite3.connect(local / "history.sqlite3")) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute("SELECT value FROM samples").fetchone()[0], "snapshot")
            self.assertEqual(json.loads((local / "finance-cache.json").read_text()), {"marker": "snapshot"})

    def test_existing_history_and_financial_cache_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, local = root / "bundle", root / "local"
            self.prepare(bundle, "older snapshot")
            self.prepare(local, "new local history")
            with patch.object(bootstrap, "BUNDLE", bundle):
                self.assertEqual(bootstrap.restore_bundled_data(local), [])
            with closing(sqlite3.connect(local / "history.sqlite3")) as conn:
                self.assertEqual(conn.execute("SELECT value FROM samples").fetchone()[0], "new local history")
            self.assertEqual(json.loads((local / "finance-cache.json").read_text()), {"marker": "new local history"})

if __name__ == "__main__":
    unittest.main()
