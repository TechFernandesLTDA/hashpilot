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
import math


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class HistorySchemaError(RuntimeError):
    """The database layout cannot be safely written by this version."""


class HistoryStore:
    def __init__(self, path: Path, wallet: str):
        self.path, self.wallet = Path(path), wallet
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session = uuid.uuid4().hex
        self.jobs = queue.Queue()
        self.last_error = None
        self.last_commit = None
        self.closed = False
        self.enqueue_lock=threading.RLock()
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
        connection.execute("BEGIN IMMEDIATE")
        try:
            version=connection.execute('PRAGMA user_version').fetchone()[0]
            if version>3:raise HistorySchemaError('Versão futura do histórico não suportada')
            statements=["""CREATE TABLE IF NOT EXISTS telemetry (
            wallet TEXT NOT NULL, timestamp REAL NOT NULL, session TEXT NOT NULL,
            source TEXT NOT NULL, gpu_json TEXT, miner_json TEXT NOT NULL,
            miner_running INTEGER, errors_json TEXT, log_path TEXT,
            PRIMARY KEY(wallet,timestamp))""", """CREATE TABLE IF NOT EXISTS events (
            wallet TEXT NOT NULL, event_key TEXT NOT NULL, timestamp REAL NOT NULL,
            session TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(wallet,event_key))""", "CREATE INDEX IF NOT EXISTS events_by_time ON events(wallet,timestamp)", """CREATE TABLE IF NOT EXISTS finance_snapshots (
            wallet TEXT NOT NULL, timestamp REAL NOT NULL, total_prl REAL NOT NULL,
            PRIMARY KEY(wallet,timestamp))""", """CREATE TABLE IF NOT EXISTS finance_sources (
            wallet TEXT NOT NULL, source TEXT NOT NULL, fetched_at REAL NOT NULL,
            data_json TEXT NOT NULL, PRIMARY KEY(wallet,source,fetched_at))""", """CREATE TABLE IF NOT EXISTS preferences (
            wallet TEXT NOT NULL, name TEXT NOT NULL, data_json TEXT NOT NULL,
            PRIMARY KEY(wallet,name))""", """CREATE TABLE IF NOT EXISTS gpu_devices (
            gpu_uuid TEXT PRIMARY KEY,first_seen REAL NOT NULL,last_seen REAL NOT NULL,identity_json TEXT NOT NULL)""", """CREATE TABLE IF NOT EXISTS gpu_telemetry (
            gpu_uuid TEXT NOT NULL,timestamp REAL NOT NULL,wallet TEXT NOT NULL,session TEXT NOT NULL,
            source TEXT NOT NULL,gpu_json TEXT,miner_json TEXT NOT NULL,miner_running INTEGER,
            enabled INTEGER,errors_json TEXT,log_path TEXT,PRIMARY KEY(gpu_uuid,wallet,timestamp))"""]
            for statement in statements:connection.execute(statement)
            columns=connection.execute("PRAGMA table_info(gpu_telemetry)").fetchall()
            primary=[row[1] for row in sorted(columns,key=lambda row:row[5]) if row[5]]
            if primary==['gpu_uuid','timestamp']:
                connection.execute("""CREATE TABLE gpu_telemetry_new (
                    gpu_uuid TEXT NOT NULL,timestamp REAL NOT NULL,wallet TEXT NOT NULL,session TEXT NOT NULL,
                    source TEXT NOT NULL,gpu_json TEXT,miner_json TEXT NOT NULL,miner_running INTEGER,
                    enabled INTEGER,errors_json TEXT,log_path TEXT,PRIMARY KEY(gpu_uuid,wallet,timestamp))""")
                fields='gpu_uuid,timestamp,wallet,session,source,gpu_json,miner_json,miner_running,enabled,errors_json,log_path'
                connection.execute(f'INSERT INTO gpu_telemetry_new ({fields}) SELECT {fields} FROM gpu_telemetry')
                old_count=connection.execute('SELECT COUNT(*) FROM gpu_telemetry').fetchone()[0]
                new_count=connection.execute('SELECT COUNT(*) FROM gpu_telemetry_new').fetchone()[0]
                if old_count!=new_count:raise HistorySchemaError('Migração de GPU perdeu linhas')
                connection.execute('DROP TABLE gpu_telemetry')
                connection.execute('ALTER TABLE gpu_telemetry_new RENAME TO gpu_telemetry')
            elif primary!=['gpu_uuid','wallet','timestamp']:
                raise HistorySchemaError('Esquema de GPU desconhecido')
            connection.execute('CREATE INDEX IF NOT EXISTS gpu_telemetry_wallet_time ON gpu_telemetry(wallet,timestamp)')
            if version<3:connection.execute('PRAGMA user_version=3')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def submit(self, kind, payload):
        snapshot=json.loads(encode(payload))
        with self.enqueue_lock:
            if not self.closed:
                self.jobs.put((kind, snapshot))
                return True
        return False

    def telemetry(self, data, source="live"):
        # Raw log lines are archived separately once, not copied on every 2 s sample.
        clean = dict(data)
        miner = {k: v for k, v in data.get("miner", {}).items() if k not in ("recent_logs", "rate_points")}
        clean["miner"] = miner
        self.submit("telemetry", {"data": clean, "source": source})
        for line in data.get("miner", {}).get("recent_logs", "").splitlines():
            key = hashlib.sha256(((data.get("log_path") or "")+"\n"+line).encode()).hexdigest()
            self.submit("event", {"timestamp": data["collected_at"], "key": "log:"+key,
                                  "kind": "miner_log", "wallet": data.get("wallet"), "data": {"line": line, "path": data.get("log_path")}})

    def finance(self, state):
        self.submit("finance", json.loads(encode(state)))

    def rig_telemetry(self, rig):
        devices=rig.get('devices',[])
        if len(devices)>6 or len({item['uuid'] for item in devices})!=len(devices):
            raise ValueError('Amostra do rig precisa conter até seis UUIDs únicos')
        self.submit('rig_telemetry',json.loads(encode(rig)))

    def preferences(self, data):
        self.submit("preferences", data)

    def event(self, timestamp, kind, data, key=None, *, wallet=None):
        self.submit("event", {"timestamp": timestamp, "key": key or uuid.uuid4().hex, "kind": kind, "data": data,'wallet':wallet})

    def archive_log(self, path: Path, *, gpu_uuid=None, wallet=None):
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
                try:stamp = dt.datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").timestamp() if match else time.time()
                except ValueError:stamp=time.time()
                key = "log:"+hashlib.sha256((str(path)+"\n"+line).encode()).hexdigest()
                self.event(stamp, "miner_log", {"line": line, "path": str(path),'gpu_uuid':gpu_uuid}, key,wallet=wallet)
            self.log_offsets[str(path)] = handle.tell()

    def archive_thermal(self, path: Path):
        """Arquiva ajustes do supervisor, inclusive períodos com o painel fechado."""
        path = Path(path)
        if not path.is_file():
            return
        offset = self.log_offsets.get(str(path), 0)
        if offset > path.stat().st_size:
            offset = 0
        with path.open("rb") as handle:
            handle.seek(offset)
            while raw := handle.readline():
                if not raw.endswith(b"\n"):
                    handle.seek(-len(raw), 1)
                    break
                key = "thermal:" + hashlib.sha256((str(path)+"\n").encode()+raw).hexdigest()
                try:
                    payload = json.loads(raw.decode("utf-8-sig"))
                    stamp = float(payload["sampled_at"])
                    if not math.isfinite(stamp) or stamp<0:raise ValueError('Timestamp térmico inválido')
                    self.event(stamp, "thermal_control", payload, key)
                except (ValueError, KeyError, TypeError) as error:
                    self.event(time.time(), "thermal_import_error", {"path": str(path), "error": str(error)}, key)
            self.log_offsets[str(path)] = handle.tell()

    def write(self, connection, kind, payload):
        if kind == "telemetry":
            data, source = payload["data"], payload["source"]
            errors = {k: data.get(k) for k in ("gpu_error", "process_error", "log_error") if data.get(k)}
            connection.execute("INSERT OR IGNORE INTO telemetry VALUES(?,?,?,?,?,?,?,?,?)", (
                data.get('wallet') or self.wallet, data["collected_at"], self.session, source,
                encode(data.get("gpu")), encode(data.get("miner", {})),
                data.get("miner_running"), encode(errors), data.get("log_path")))
            miner = data.get("miner", {})
            status = (data.get("miner_running"), miner.get("paused"), miner.get("connection"), bool(errors), data.get("log_path"),data.get('wallet') or self.wallet)
            if source == "live" and status != self.last_status:
                self.write(connection, "event", {"timestamp": data["collected_at"], "key": "status:"+str(data["collected_at"]),
                    "kind": "status_changed", "wallet":status[5], "data": {"miner_running": status[0], "paused": status[1], "connection": status[2], "errors": errors, "log_path": status[4]}})
                self.last_status = status
        elif kind == 'rig_telemetry':
            for device in payload.get('devices',[]):
                identifier=device['uuid']
                stamp=device.get('collected_at',payload.get('collected_at',time.time()))
                identity=dict(device.get('identity') or {})
                identity.update({key:device.get(key) for key in ('uuid','index','name','vendor','pci_bus_id','pci_device_id','pci_sub_device_id','pci_subdevice_id','vbios_version','driver_version','serial','architecture','memory_mib') if device.get(key) is not None})
                connection.execute('INSERT INTO gpu_devices VALUES(?,?,?,?) ON CONFLICT(gpu_uuid) DO UPDATE SET last_seen=excluded.last_seen,identity_json=excluded.identity_json',
                    (identifier,stamp,stamp,encode(identity)))
                errors={key:device.get(key) for key in ('gpu_error','log_error','process_error','configuration_error') if device.get(key)}
                connection.execute('INSERT OR IGNORE INTO gpu_telemetry (gpu_uuid,timestamp,wallet,session,source,gpu_json,miner_json,miner_running,enabled,errors_json,log_path) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (identifier,stamp,device.get('wallet') or self.wallet,self.session,'live',encode(device.get('gpu')),
                     encode(device.get('miner') or {}),device.get('miner_running'),device.get('enabled',True),encode(errors),device.get('log_path')))
        elif kind == "event":
            connection.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)", (
                payload.get('wallet') or self.wallet, payload["key"], payload["timestamp"], self.session, payload["kind"], encode(payload["data"])))
        elif kind == "finance":
            wallet=payload.get('wallet') or self.wallet
            connection.executemany("INSERT OR IGNORE INTO finance_snapshots VALUES(?,?,?)", [
                (wallet, point["timestamp"], point["total_prl"]) for point in payload.get("snapshots", [])])
            for key, source in payload.get("sources", {}).items():
                connection.execute("INSERT OR IGNORE INTO finance_sources VALUES(?,?,?,?)", (
                    wallet, key, source["fetched_at"], encode(source["data"])))
            if payload.get("errors"):
                stamp = payload.get("attempted_at", time.time())
                self.write(connection, "event", {"timestamp": stamp, "key": "finance-error:"+str(stamp),
                    "kind": "finance_error", "wallet":wallet, "data": payload["errors"]})
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
            stop=False
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
                except HistorySchemaError as error:
                    self.last_error='Histórico indisponível: '+str(error)
                    self.ready.set()
                    with self.enqueue_lock:
                        self.closed=True
                        while True:
                            try:self.jobs.get_nowait()
                            except queue.Empty:break
                            self.jobs.task_done()
                    stop=True
                    break
                except (sqlite3.IntegrityError,ValueError,TypeError,KeyError,AttributeError) as error:
                    # A malformed sample must not kill or permanently block the writer.
                    self.last_error='Amostra rejeitada: '+str(error)
                    if connection is not None:connection.rollback()
                    try:
                        with self.path.with_name('history-rejected.jsonl').open('a',encoding='utf-8') as rejected:
                            rejected.write(encode({'at':time.time(),'kind':job[0],'error':str(error)})+'\n')
                    except OSError:self.last_error+='; não foi possível gravar o diagnóstico'
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
                    if self.closed:
                        self.ready.set()
                        with self.enqueue_lock:
                            while True:
                                try:self.jobs.get_nowait()
                                except queue.Empty:break
                                self.jobs.task_done()
                        stop=True
                        break
            self.jobs.task_done()
            if stop:break
        if connection:
            connection.close()

    def flush(self, timeout=4):
        deadline = time.monotonic()+timeout
        while self.jobs.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(.01)
        return not self.jobs.unfinished_tasks

    def close(self):
        with self.enqueue_lock:
            if not self.closed:
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

    def restore_gpu(self,gpu_uuid,since,wallet):
        try:
            if not wallet:raise ValueError('Carteira da GPU obrigatória')
            rows=self.read('SELECT timestamp,gpu_json,miner_json FROM gpu_telemetry WHERE gpu_uuid=? AND wallet=? AND timestamp>=? ORDER BY timestamp',(gpu_uuid,wallet,since))
            points={key:{} for key in ('hash','temp','power','fan')}
            for stamp,gpu_json,miner_json in rows:
                gpu,miner=json.loads(gpu_json) or {},json.loads(miner_json)
                measured=miner.get('hashrate_at')
                if measured and measured>=since and 0<=stamp-measured<=180 and miner.get('hashrate') is not None:
                    points['hash'][measured]=miner['hashrate']
                for key,field in (('temp','temperature.gpu'),('power','power.draw'),('fan','fan.speed')):
                    if gpu.get(field) is not None: points[key][stamp]=gpu[field]
            identity=self.read('SELECT identity_json FROM gpu_devices WHERE gpu_uuid=?',(gpu_uuid,))
            return {'points':{key:sorted(values.items()) for key,values in points.items()},'count':len(rows),
                'identity':json.loads(identity[0][0]) if identity else {}}
        except (sqlite3.Error,ValueError) as error:
            return {'points':{},'count':0,'identity':{},'error':str(error)}

    def gpu_stats(self,gpu_uuid=None):
        query='SELECT gpu_uuid,COUNT(*),MIN(timestamp),MAX(timestamp) FROM gpu_telemetry'
        params=()
        if gpu_uuid is not None:
            query+=' WHERE gpu_uuid=?'
            params=(gpu_uuid,)
        try: return {row[0]:list(row[1:]) for row in self.read(query+' GROUP BY gpu_uuid',params)}
        except sqlite3.Error: return {}

    def finance_state(self, since, wallet=None):
        try:
            wallet=self.wallet if wallet is None else wallet
            snapshots = [{"timestamp": stamp, "total_prl": total} for stamp, total in self.read(
                "SELECT timestamp,total_prl FROM finance_snapshots WHERE wallet=? AND timestamp>=? ORDER BY timestamp", (wallet, since))]
            sources = {}
            for key, stamp, payload in self.read("SELECT source,MAX(fetched_at),data_json FROM finance_sources WHERE wallet=? GROUP BY source", (wallet,)):
                sources[key] = {"fetched_at": stamp, "data": json.loads(payload)}
            return {"wallet": wallet, "snapshots": snapshots, "sources": sources}
        except (sqlite3.Error, ValueError):
            return {"wallet": wallet, "snapshots": [], "sources": {}}

    def stats(self):
        return {table: self.read(f"SELECT COUNT(*),MIN({column}),MAX({column}) FROM {table} WHERE wallet=?", (self.wallet,))[0]
                for table, column in (("telemetry", "timestamp"), ("events", "timestamp"), ("finance_snapshots", "timestamp"), ("finance_sources", "fetched_at"))}
