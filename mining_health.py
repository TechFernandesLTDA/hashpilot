"""Saúde por ACK da pool e orçamento persistente de recuperação por GPU."""
from __future__ import annotations
import math
from pathlib import Path
import sqlite3
import statistics
import time
from contextlib import contextmanager


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def health(miner, running, now=None, *, started_at=None, intervals=()):
    now = time.time() if now is None else now
    stamp = miner.get('hashrate_at')
    hashing = running is True and not miner.get('paused') and finite(stamp) and 0 <= now-stamp <= 180 and (miner.get('hashrate') or 0)>0
    last = miner.get('last_share_at')
    if not finite(last) or last>now: last=None
    gaps=[x for x in intervals if finite(x) and 0<x<=86400]
    threshold=max(900, min(7200, statistics.median(gaps)*6)) if len(gaps)>=3 else 900
    beginning=started_at if finite(started_at) else miner.get('started_at')
    age=now-(last if last is not None else beginning) if finite(last if last is not None else beginning) else None
    connected=miner.get('connection')=='Conectado à pool' or (last is not None and miner.get('connection')!='Reconectando à pool' and now-last<=threshold)
    warning=bool(hashing and age is not None and age>threshold and finite(beginning) and now-beginning>=300)
    if running is not True: state='stopped' if running is False else 'unknown'
    elif miner.get('paused'): state='cooling'
    elif not connected: state='connecting'
    elif not hashing: state='no_hashrate'
    elif warning: state='no_shares'
    elif last is not None and now-last<=threshold: state='confirmed'
    else: state='awaiting_share'
    labels={'stopped':'Minerador parado','unknown':'Processo não confirmado','cooling':'GPU em resfriamento',
        'connecting':'Aguardando conexão com a pool','no_hashrate':'Sem hashrate recente',
        'no_shares':'Alerta: sem shares aceitas no prazo esperado','confirmed':'Pool confirmou shares',
        'awaiting_share':'Calculando · aguardando share aceita'}
    return {'state':state,'label':labels[state],'hashing':hashing,'connected':connected,
        'last_accepted_at':last,'seconds_without_share':age,'warning':warning,
        'warning_after_seconds':threshold,'interval_samples':len(gaps),
        'basis':'histórico de ACKs (margem 6×)' if len(gaps)>=3 else 'janela inicial conservadora de 15 min; dificuldade desconhecida',
        'ack_source':'resposta da pool registrada pelo minerador; não comprova pagamento'}


class OperationalStore:
    """SQLite commits precedem restart; restart budget survives service restarts."""
    def __init__(self, path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS acks(gpu TEXT, stamp REAL, PRIMARY KEY(gpu,stamp));
                CREATE TABLE IF NOT EXISTS recoveries(id INTEGER PRIMARY KEY,gpu TEXT,stamp REAL,reason TEXT);
                CREATE INDEX IF NOT EXISTS recovery_gpu_stamp ON recoveries(gpu,stamp);''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=3)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db: yield db
        finally: db.close()

    def assess(self, identifier, miner, running, now, started_at=None):
        last=miner.get('last_share_at')
        with self.connect() as db:
            if finite(last) and 0<=now-last<=86400:
                db.execute('INSERT OR IGNORE INTO acks VALUES(?,?)',(identifier,last))
            stamps=[r[0] for r in db.execute('SELECT stamp FROM acks WHERE gpu=? ORDER BY stamp DESC LIMIT 33',(identifier,))]
        if last is None and stamps and finite(started_at) and stamps[0]>=started_at:
            miner={**miner,'last_share_at':stamps[0]}
        gaps=[a-b for a,b in zip(stamps,stamps[1:])]
        return health(miner,running,now,started_at=started_at,intervals=gaps)

    def budget(self, identifier, now):
        with self.connect() as db:
            rows=db.execute('SELECT stamp,reason FROM recoveries WHERE gpu=? AND stamp>? ORDER BY stamp DESC',(identifier,now-3600)).fetchall()
        delay=min(900,60*2**max(0,len(rows)-1)) if rows else 0
        next_at=rows[0][0]+delay if rows else now
        blocked=len(rows)>=3
        if blocked: next_at=max(next_at,min(r[0] for r in rows)+3600)
        return {'attempts_last_hour':len(rows),'limit_per_hour':3,'blocked':blocked,
            'next_allowed_at':next_at,'last_reason':rows[0][1] if rows else None}

    def reserve(self, identifier, reason, now):
        # Single service owner, plus transaction protects competing reservations.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            stamps=[r[0] for r in db.execute('SELECT stamp FROM recoveries WHERE gpu=? AND stamp>? ORDER BY stamp DESC',(identifier,now-3600))]
            if len(stamps)>=3 or stamps and now<stamps[0]+min(900,60*2**max(0,len(stamps)-1)):
                return False
            db.execute('INSERT INTO recoveries(gpu,stamp,reason) VALUES(?,?,?)',(identifier,now,str(reason)[:120]))
        return True
