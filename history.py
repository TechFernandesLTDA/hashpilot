"""Arquivo permanente local. Cada commit sobrevive ao encerramento do processo."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time
import uuid


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class HistoryStore:
    def __init__(self, path: Path, wallet: str):
        self.path, self.wallet = Path(path), wallet
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session = uuid.uuid4().hex
        self.jobs = queue.Queue()
        self.last_error = None
        self.last_commit = None
        self.closed = False
        self.last_status = None
        self.ready = threading.Event()
        self.info = {}
        self.info_at = 0
        self.log_offsets = {}
        self.worker = threading.Thread(target=self.work, daemon=True, name="history-writer")
        self.worker.start()
        self.submit("event", {"timestamp": time.time(), "key": "session:"+self.session,
                              "kind": "session_started", "data": {"session": self.session}})

    def connection(self):
        connection = sqlite3.connect(self.path, timeout=.2)
        connection.execute("PRAGMA busy_timeout=200")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def schema(self, connection):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
          CREATE TABLE IF NOT EXISTS telemetry (
            wallet TEXT NOT NULL, timestamp REAL NOT NULL, session TEXT NOT NULL,
            source TEXT NOT NULL, gpu_json TEXT, miner_json TEXT NOT NULL,
            miner_running INTEGER, errors_json TEXT, log_path TEXT,
            PRIMARY KEY(wallet,timestamp));
          CREATE TABLE IF NOT EXISTS events (
            wallet TEXT NOT NULL, event_key TEXT NOT NULL, timestamp REAL NOT NULL,
            session TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(wallet,event_key));
          CREATE INDEX IF NOT EXISTS events_by_time ON events(wallet,timestamp);
          CREATE TABLE IF NOT EXISTS finance_snapshots (
            wallet TEXT NOT NULL, timestamp REAL NOT NULL, total_prl REAL NOT NULL,
            PRIMARY KEY(wallet,timestamp));
          CREATE TABLE IF NOT EXISTS finance_sources (
            wallet TEXT NOT NULL, source TEXT NOT NULL, fetched_at REAL NOT NULL,
            data_json TEXT NOT NULL, PRIMARY KEY(wallet,source,fetched_at));
          CREATE TABLE IF NOT EXISTS preferences (
            wallet TEXT NOT NULL, name TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(wallet,name));
          PRAGMA user_version=1;
        """)
        connection.commit()

    def submit(self, kind, payload):
        if not self.closed:
            self.jobs.put((kind, payload))

    def telemetry(self, data, source="live"):
        # Raw log lines are archived separately once, not copied on every 2 s sample.
        clean = dict(data)
        miner = {k: v for k, v in data.get("miner", {}).items() if k not in ("recent_logs", "rate_points")}
        clean["miner"] = miner
        self.submit("telemetry", {"data": clean, "source": source})
        for line in data.get("miner", {}).get("recent_logs", "").splitlines():
            key = hashlib.sha256(((data.get("log_path") or "")+"\n"+line).encode()).hexdigest()
            self.submit("event", {"timestamp": data["collected_at"], "key": "log:"+key,
                                  "kind": "miner_log", "data": {"line": line, "path": data.get("log_path")}})

    def finance(self, state):
        self.submit("finance", json.loads(encode(state)))

    def preferences(self, data):
        self.submit("preferences", data)

    def event(self, timestamp, kind, data, key=None):
        self.submit("event", {"timestamp": timestamp, "key": key or uuid.uuid4().hex, "kind": kind, "data": data})

    def archive_log(self, path: Path):
        """Segue o arquivo completo, inclusive rajadas maiores que o tail da UI."""
        import datetime as dt
        import re
        stamp_pattern = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]")
        offset = self.log_offsets.get(str(path), 0)
        if offset > path.stat().st_size:
            offset = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            while raw := handle.readline():
                # Uma linha incompleta fica para a próxima leitura.
                if not raw.endswith(b"\n"):
                    handle.seek(-len(raw), 1)
                    break
                line = raw.decode("utf-8-sig", errors="replace").rstrip()
                match = stamp_pattern.match(line)
                stamp = dt.datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").timestamp() if match else time.time()
                key = "log:"+hashlib.sha256((str(path)+"\n"+line).encode()).hexdigest()
                self.event(stamp, "miner_log", {"line": line, "path": str(path)}, key)
            self.log_offsets[str(path)] = handle.tell()

    def write(self, connection, kind, payload):
        if kind == "telemetry":
            data, source = payload["data"], payload["source"]
            errors = {k: data.get(k) for k in ("gpu_error", "process_error", "log_error") if data.get(k)}
            connection.execute("INSERT OR IGNORE INTO telemetry VALUES(?,?,?,?,?,?,?,?,?)", (
                self.wallet, data["collected_at"], self.session, source,
                encode(data.get("gpu")), encode(data.get("miner", {})),
                data.get("miner_running"), encode(errors), data.get("log_path")))
            miner = data.get("miner", {})
            status = (data.get("miner_running"), miner.get("paused"), miner.get("connection"), bool(errors), data.get("log_path"))
            if source == "live" and status != self.last_status:
                self.write(connection, "event", {"timestamp": data["collected_at"], "key": "status:"+str(data["collected_at"]),
                    "kind": "status_changed", "data": {"miner_running": status[0], "paused": status[1], "connection": status[2], "errors": errors, "log_path": status[4]}})
                self.last_status = status
        elif kind == "event":
            connection.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)", (
                self.wallet, payload["key"], payload["timestamp"], self.session, payload["kind"], encode(payload["data"])))
        elif kind == "finance":
            connection.executemany("INSERT OR IGNORE INTO finance_snapshots VALUES(?,?,?)", [
                (self.wallet, point["timestamp"], point["total_prl"]) for point in payload.get("snapshots", [])])
            for key, source in payload.get("sources", {}).items():
                connection.execute("INSERT OR IGNORE INTO finance_sources VALUES(?,?,?,?)", (
                    self.wallet, key, source["fetched_at"], encode(source["data"])))
            if payload.get("errors"):
                stamp = payload.get("attempted_at", time.time())
                self.write(connection, "event", {"timestamp": stamp, "key": "finance-error:"+str(stamp),
                    "kind": "finance_error", "data": payload["errors"]})
        elif kind == "preferences":
            connection.executemany("INSERT OR REPLACE INTO preferences VALUES(?,?,?)", [
                (self.wallet, key, encode(value)) for key, value in payload.items()])

    def work(self):
        connection = None
        while True:
            job = self.jobs.get()
            if job is None:
                self.jobs.task_done()
                break
            while True:
                try:
                    if connection is None:
                        connection = self.connection()
                        self.schema(connection)
                        self.ready.set()
                    # Short transactions. On busy, retry the SAME job; no queue drop.
                    with connection:
                        self.write(connection, *job)
                    self.last_commit, self.last_error = time.time(), None
                    if time.monotonic()-self.info_at > 15:
                        self.info = self.stats()
                        self.info_at = time.monotonic()
                    break
                except (sqlite3.Error, OSError) as error:
                    self.last_error = str(error)
                    if connection is not None:
                        try:
                            connection.rollback()
                            connection.close()
                        except sqlite3.Error:
                            pass
                        connection = None
                    time.sleep(.25)
            self.jobs.task_done()
        if connection:
            connection.close()

    def flush(self, timeout=4):
        deadline = time.monotonic()+timeout
        while self.jobs.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(.01)
        return not self.jobs.unfinished_tasks

    def close(self):
        if self.closed:
            return
        self.event(time.time(), "session_closed", {"session": self.session})
        self.closed = True
        self.jobs.put(None)
        self.worker.join(timeout=4)

    def read(self, query, parameters=()):
        if not self.path.exists():
            return []
        connection = self.connection()
        try:
            return connection.execute(query, parameters).fetchall()
        finally:
            connection.close()

    def restore(self, since):
        try:
            rows = self.read("SELECT timestamp,gpu_json,miner_json,source FROM telemetry WHERE wallet=? AND timestamp>=? ORDER BY timestamp", (self.wallet, since))
            points = {"hash": {}, "temp": {}, "power": {}, "fan": {}}
            for timestamp, gpu_json, miner_json, source in rows:
                gpu, miner = json.loads(gpu_json) or {}, json.loads(miner_json)
                rate_time = miner.get("hashrate_at")
                if rate_time and rate_time >= since and miner.get("hashrate") is not None:
                    points["hash"][rate_time] = miner["hashrate"]
                for key, field in (("temp", "temperature.gpu"), ("power", "power.draw"), ("fan", "fan.speed")):
                    if gpu.get(field) is not None:
                        points[key][timestamp] = gpu[field]
            preferences = {name: json.loads(value) for name, value in self.read("SELECT name,data_json FROM preferences WHERE wallet=?", (self.wallet,))}
            return {"points": {key: sorted(values.items()) for key, values in points.items()}, "preferences": preferences, "count": len(rows)}
        except (sqlite3.Error, ValueError) as error:
            return {"points": {}, "preferences": {}, "count": 0, "error": str(error)}

    def finance_state(self, since):
        try:
            snapshots = [{"timestamp": stamp, "total_prl": total} for stamp, total in self.read(
                "SELECT timestamp,total_prl FROM finance_snapshots WHERE wallet=? AND timestamp>=? ORDER BY timestamp", (self.wallet, since))]
            sources = {}
            for key, stamp, payload in self.read("SELECT source,MAX(fetched_at),data_json FROM finance_sources WHERE wallet=? GROUP BY source", (self.wallet,)):
                sources[key] = {"fetched_at": stamp, "data": json.loads(payload)}
            return {"wallet": self.wallet, "snapshots": snapshots, "sources": sources}
        except (sqlite3.Error, ValueError):
            return {"wallet": self.wallet, "snapshots": [], "sources": {}}

    def stats(self):
        return {table: self.read(f"SELECT COUNT(*),MIN({column}),MAX({column}) FROM {table} WHERE wallet=?", (self.wallet,))[0]
                for table, column in (("telemetry", "timestamp"), ("events", "timestamp"), ("finance_snapshots", "timestamp"), ("finance_sources", "fetched_at"))}
