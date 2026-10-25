"""Agente NVIDIA local: benchmark persistente, propostas limitadas e IA Ollama opcional.

Importar, detectar, observar e recomendar nunca altera o hardware. Escritas ficam
isoladas em NvidiaActuator, desativado por padrão e protegido por posse exclusiva.
"""
from __future__ import annotations
from ai_advisor import RoutedAdvisor

import argparse
from contextlib import contextmanager
import csv
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import statistics
import subprocess
import threading
import time
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ACTIONS = ("hold", "clock_up", "clock_down", "power_up", "power_down", "restore_best")
SCHEMA = {"type":"object", "properties":{"action":{"type":"string","enum":list(ACTIONS)},
    "reason":{"type":"string"}}, "required":["action","reason"], "additionalProperties":False}


def numeric(value, default=None):
    if isinstance(value, bool):
        return default
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, OverflowError):
        return default


def smi_command(arguments, timeout=4):
    executable = shutil.which("nvidia-smi")
    if not executable and os.name == "nt":
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe"
        executable = str(candidate) if candidate.is_file() else None
    if not executable:
        raise RuntimeError("Driver NVIDIA/nvidia-smi indisponível")
    result = subprocess.run([executable, *map(str, arguments)], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:240])
    return result.stdout.strip()


def validate_device(gpu):
    """Identity comes from the NVIDIA driver, not a name supplied by the LLM."""
    if gpu.get("vendor") != "NVIDIA" or not re.fullmatch(r"GPU-[A-Za-z0-9-]+", str(gpu.get("uuid", ""))):
        raise ValueError("O agente aceita apenas GPUs NVIDIA identificadas pelo driver")
    return gpu


def catalog_identity(name, pci_device_id=None):
    """Enriquece identidade; fatos do catálogo nunca viram limites de controle."""
    try:
        catalog=json.loads((Path(__file__).resolve().parent/"data/nvidia-gpus.json").read_text(encoding="utf-8"))
        normalized=re.sub(r"\s+"," ",str(name)).strip().casefold()
        matches=[item for item in catalog["gpus"] if normalized in
                 {str(value).casefold() for value in [item["name"],*item.get("aliases",[])]}]
        if len(matches)!=1:
            return {}
        item=matches[0]
        # A contradictory PCI id cannot establish an architecture match.
        code=re.sub(r"^0x","",str(pci_device_id or ""),flags=re.I).upper()
        code=code[:4] if len(code)==8 else code
        if code and code not in item.get("pci_device_ids",[]):
            return {"catalog_match":"name_only_pci_mismatch"}
        return {"catalog_id":item["id"],"architecture":item.get("architecture"),
                "form_factor":item.get("form_factor"),"family":item.get("family"),
                "catalog_match":"name_and_pci" if code else "name_only",
                "catalog_sources":item.get("sources",[])}
    except (OSError,ValueError,KeyError,TypeError):
        return {}


def detect_nvidia_gpus(command=smi_command, max_devices=None):
    if max_devices is not None and int(max_devices)<=0:
        return []
    output = command(["--query-gpu=index,name,uuid,pci.bus_id", "--format=csv,noheader,nounits"])
    devices = []
    for row in csv.reader(output.splitlines()):
        if len(row) != 4:
            continue
        index, name, identifier, pci = (value.strip() for value in row)
        if not index.isdigit() or not identifier.startswith("GPU-"):
            continue
        gpu = {"vendor":"NVIDIA", "index":int(index), "name":name, "uuid":identifier,
               "pci_bus_id":pci, "capabilities_queried_at":time.time(), "supported_graphics_clocks_mhz":[]}
        for field,key in (("driver_version","driver_version"),("serial","serial"),
                          ("pci.device_id","pci_device_id"),("pci.sub_device_id","pci_subdevice_id"),
                          ("vbios_version","vbios_version"),("compute_cap","compute_capability")):
            try:
                value=command(["-i",identifier,"--query-gpu="+field,"--format=csv,noheader,nounits"]).strip()
                gpu[key]=value if value and value.upper() not in ("N/A","[N/A]","NOT SUPPORTED","[NOT SUPPORTED]") else None
            except (OSError,RuntimeError,subprocess.SubprocessError):
                gpu[key]=None
        for field, key in (("power.min_limit","power_min_w"),("power.max_limit","power_max_w"),
            ("power.default_limit","power_default_w"),("power.limit","power_limit_w"),
            ("clocks.max.graphics","graphics_max_mhz"),("memory.total","memory_mib"),
            ("temperature.gpu.tlimit","temperature_limit_c")):
            try:
                value = command(["-i",identifier,"--query-gpu="+field,"--format=csv,noheader,nounits"])
                gpu[key] = numeric(value)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                gpu[key] = None
        try:
            clocks = command(["-i",identifier,"--query-supported-clocks=graphics","--format=csv,noheader,nounits"])
            gpu["supported_graphics_clocks_mhz"] = sorted({int(value) for line in clocks.splitlines()
                if (value:=numeric(line)) is not None and value > 0 and value.is_integer()})
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass
        gpu.update(catalog_identity(name,gpu.get("pci_device_id")))
        gpu.setdefault("architecture",None)
        devices.append(validate_device(gpu))
        if max_devices is not None and len(devices)>=max(0,int(max_devices)):
            break
    return devices


def read_sample(gpu, command=smi_command):
    validate_device(gpu)
    fields = "temperature.gpu,power.draw,power.limit,clocks.gr,utilization.gpu"
    values = next(csv.reader(command(["-i",gpu["uuid"],"--query-gpu="+fields,
                                      "--format=csv,noheader,nounits"]).splitlines()),[])
    if len(values) != 5:
        raise RuntimeError("Resposta NVIDIA incompleta")
    result=dict(zip(("temperature_c","power_w","power_limit_w","clock_mhz","utilization_pct"),
                    map(numeric,values)), sampled_at=time.time())
    if result["temperature_c"] is None or not 0<=result["temperature_c"]<=120:
        raise RuntimeError("Temperatura NVIDIA inválida")
    return result


def normalize_sample(sample, now=None):
    now = time.time() if now is None else float(now)
    pairs = {"temperature_c":("temperature_c","temperature.gpu"), "power_w":("power_w","power.draw"),
             "power_limit_w":("power_limit_w","power.limit"), "clock_mhz":("clock_mhz","clocks.gr"),
             "utilization_pct":("utilization_pct","utilization.gpu")}
    result = {key:numeric(sample.get(keys[0], sample.get(keys[1]))) for key, keys in pairs.items()}
    result["sampled_at"] = numeric(sample['sampled_at']) if 'sampled_at' in sample else now
    return result


def thermal_limits(gpu):
    # 78/83 are conservative product ceilings; a lower driver limit always wins.
    driver = numeric(gpu.get("temperature_limit_c"))
    hard = min(83.0,driver-2) if driver and 50 <= driver <= 110 else 83.0
    return min(76.0, hard-7), hard


class OllamaAdvisor:
    """Loopback only; JSON actions only; CPU inference avoids GPU mining contention."""
    def __init__(self, model="qwen2.5:0.5b", endpoint="http://127.0.0.1:11434", timeout=12):
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost","127.0.0.1","::1") or parsed.username or parsed.password:
            raise ValueError("A IA deve usar o Ollama local, sem API externa")
        if not isinstance(model,str) or not re.fullmatch(r"[a-zA-Z0-9._:/-]{1,120}", model):
            raise ValueError("Nome de modelo inválido")
        if model.endswith((':cloud','-cloud')):raise ValueError('Ollama local não aceita modelos cloud')
        self.endpoint, self.model = endpoint.rstrip("/"), model
        self.timeout = max(1,min(30,float(timeout)))

    @staticmethod
    def parse(text):
        if not isinstance(text,str):raise ValueError('Resposta da IA precisa ser texto JSON')
        value = json.loads(text)
        if not isinstance(value,dict) or set(value) != {"action","reason"} or value["action"] not in ACTIONS:
            raise ValueError("Resposta da IA fora do conjunto permitido")
        if not isinstance(value["reason"],str) or not 1 <= len(value["reason"].strip()) <= 400:
            raise ValueError("Justificativa da IA inválida")
        return value

    def advise(self, context):
        payload = {"model":self.model,"stream":False,"format":SCHEMA,"keep_alive":0,
            "options":{"num_gpu":0,"num_ctx":2048,"num_predict":160,"temperature":0},
            "system":"You are a local NVIDIA mining advisor. Return JSON matching the schema. "
                     "Choose one small action from the enum. Never emit commands, clocks, power values or code. "
                     "Prioritize thermal safety and accepted shares. Explain in Portuguese in one sentence.",
            "prompt":json.dumps(context,ensure_ascii=False)}
        request = Request(self.endpoint+"/api/generate",json.dumps(payload).encode(),{"Content-Type":"application/json"})
        # Disable system HTTP proxies: hardware telemetry must stay on loopback.
        from urllib.request import ProxyHandler, build_opener
        with build_opener(ProxyHandler({})).open(request,timeout=self.timeout) as response:
            body = response.read(65537)
        if len(body)>65536:
            raise ValueError("Resposta da IA excede o limite")
        return self.parse(json.loads(body)["response"])


class BackgroundAdvisor:
    """At most one local inference per minute; sensor/control loop never waits."""
    def __init__(self,advisor,interval_seconds=60):
        self.advisor=advisor
        self.interval=max(60,float(interval_seconds))
        self.lock=threading.Lock()
        self.thread=None
        self.last_started=None
        self.completed_at=None
        self.advice=None
        self.error=None
        self.config_signature=None
        self.revision=0

    def _work(self,context,revision=None):
        try:
            advice=self.advisor.advise(context)
            error=None
        except (OSError,ValueError,KeyError,TypeError,TimeoutError,URLError,sqlite3.Error) as failure:
            advice=None
            error=str(failure)[:160]
        with self.lock:
            if revision is not None and revision!=self.revision:
                return
            self.advice,self.error=advice,error
            self.completed_at=time.monotonic()

    def poll(self,context,now=None):
        stamp=time.monotonic() if now is None else float(now)
        with self.lock:
            alive=self.thread is not None and self.thread.is_alive()
            cloud=False
            if isinstance(self.advisor,RoutedAdvisor):
                try:config=self.advisor.settings()
                except (OSError,ValueError,TypeError):
                    return {'advice':None,'status':'unavailable: configuração de IA inválida','in_flight':alive}
                signature=json.dumps(config.get('ai',{}),sort_keys=True)+str(config.get('ai_enabled'))
                if signature!=self.config_signature:
                    self.config_signature=signature
                    self.revision+=1
                    self.advice=self.error=self.completed_at=self.last_started=None
                cloud=isinstance(config.get('ai'),dict) and config['ai'].get('provider') in ('openrouter','opencode')
            interval=max(self.interval,3600 if cloud and self.advice is not None else 60)
            if not alive and (self.last_started is None or stamp-self.last_started>=interval):
                self.last_started=stamp
                # Only immutable telemetry leaves this loop; the result remains a suggestion.
                copied=json.loads(json.dumps(context))
                self.thread=threading.Thread(target=self._work,args=(copied,self.revision),daemon=True,name="hashpilot-advisor")
                self.thread.start()
                alive=True
            fresh=self.completed_at is not None and 0<=stamp-self.completed_at<=90
            waiting=max(0,int(interval-(stamp-self.last_started))) if self.last_started is not None else 0
            label=getattr(self.advisor,'label','local_ollama')
            if fresh and self.advice is not None:status=label
            elif alive:status='pending: IA em segundo plano'
            elif self.error:status='unavailable: '+self.error
            else:status=f'{label} · em espera; próxima consulta em {waiting} s'
            return {"advice":dict(self.advice) if self.advice is not None and fresh else None,
                "status":status,'in_flight':alive,'next_request_seconds':waiting,
                'response_age_seconds':max(0,stamp-self.completed_at) if self.completed_at is not None else None}


class GPUAgent:
    """One SQLite database, separate GPU UUIDs/sessions; history is append-only."""
    def __init__(self, state_dir, *, advisor=None):
        self.path = Path(state_dir)/"gpu-agent.sqlite3"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path,timeout=5,check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS benchmarks(id TEXT PRIMARY KEY,gpu_uuid TEXT NOT NULL,started REAL NOT NULL,
            duration REAL NOT NULL,objective TEXT NOT NULL,state TEXT NOT NULL,device_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,session TEXT NOT NULL,stamp REAL NOT NULL,
            hash_stamp REAL,hashrate REAL,accepted INTEGER,rejected INTEGER,temperature REAL,power REAL,
            power_limit REAL,clock REAL,valid INTEGER NOT NULL,reason TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS observations_session ON observations(session,stamp);
          CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,gpu_uuid TEXT NOT NULL,stamp REAL NOT NULL,
            session TEXT,payload_json TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS trials(id INTEGER PRIMARY KEY,gpu_uuid TEXT NOT NULL,stamp REAL NOT NULL,
            state TEXT NOT NULL,payload_json TEXT NOT NULL);
        """)
        self.db.commit()
        self.advisor = advisor

    def close(self):
        with self.lock:
            self.db.close()

    def start_benchmark(self, gpu, duration_seconds=300, objective="performance", now=None):
        validate_device(gpu)
        duration = numeric(duration_seconds)
        if duration is None or not 60 <= duration <= 3600:
            raise ValueError("Benchmark deve durar entre 60 e 3600 segundos")
        if objective not in ("performance","efficiency"):
            raise ValueError("Objetivo precisa ser performance ou efficiency")
        stamp = time.time() if now is None else float(now)
        identifier = uuid.uuid4().hex
        with self.lock,self.db:
            self.db.execute("UPDATE benchmarks SET state='superseded' WHERE gpu_uuid=? AND state='running'",(gpu["uuid"],))
            self.db.execute("INSERT INTO benchmarks VALUES (?,?,?,?,?,?,?)",(identifier,gpu["uuid"],stamp,duration,
                objective,"running",json.dumps(gpu)))
        return self.status(gpu["uuid"],now=stamp)

    def _latest(self, identifier):
        return self.db.execute("SELECT * FROM benchmarks WHERE gpu_uuid=? ORDER BY started DESC,rowid DESC LIMIT 1",(identifier,)).fetchone()

    def observe(self, gpu, sample, hashrate_ths=None, accepted=0, rejected=0, now=None, hash_sampled_at=None,hw_errors=0):
        validate_device(gpu)
        stamp = time.time() if now is None else float(now)
        sample = normalize_sample(sample,stamp)
        rate, hash_stamp = numeric(hashrate_ths), numeric(hash_sampled_at)
        counters=[numeric(value) for value in (accepted,rejected,hw_errors)]
        counters_valid=all(value is not None and 0<=value<=2**63-1 and value.is_integer() for value in counters)
        accepted,rejected,hw_errors=[int(value) for value in counters] if counters_valid else [0,0,0]
        valid,reason = True,"ok"
        soft,hard = thermal_limits(gpu)
        if sample["temperature_c"] is None or not 0 <= sample["temperature_c"] < hard:
            valid,reason = False,"thermal_or_sensor"
        elif sample['sampled_at'] is None or stamp-sample["sampled_at"]>10 or sample["sampled_at"]>stamp+2:
            valid,reason = False,"stale_sensor"
        elif rate is None or rate <= 0 or hash_stamp is None or not 0 <= stamp-hash_stamp <= 180:
            valid,reason = False,"missing_or_stale_hashrate"
        elif sample["power_w"] is None or sample["power_w"]<=0 or sample["clock_mhz"] is None:
            valid,reason = False,"incomplete_sensor"
        elif not counters_valid:valid,reason=False,'invalid_counters'
        elif hw_errors:valid,reason=False,'hardware_error'
        with self.lock,self.db:
            session = self._latest(gpu["uuid"])
            if session and session["state"]=="running":
                if hash_stamp is not None and hash_stamp<session["started"]:
                    valid,reason = False,"hashrate_before_benchmark"
                previous = self.db.execute("SELECT accepted,rejected FROM observations WHERE session=? ORDER BY id DESC LIMIT 1",(session["id"],)).fetchone()
                if counters_valid and previous and (accepted < previous["accepted"] or rejected < previous["rejected"]):
                    valid,reason = False,"miner_restarted"
                elif counters_valid and previous and rejected>previous["rejected"]:
                    valid,reason = False,"new_rejected_share"
                self.db.execute("""INSERT INTO observations(session,stamp,hash_stamp,hashrate,accepted,rejected,
                    temperature,power,power_limit,clock,valid,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (session["id"],stamp,hash_stamp,rate,accepted,rejected,sample["temperature_c"],sample["power_w"],
                     sample["power_limit_w"],sample["clock_mhz"],int(valid),reason))
                if stamp >= session["started"]+session["duration"]:
                    self.db.execute("UPDATE benchmarks SET state=? WHERE id=?",("complete",session["id"]))
        return self.status(gpu["uuid"],now=stamp)

    def status(self, gpu_uuid, now=None):
        stamp = time.time() if now is None else float(now)
        with self.lock:
            session = self._latest(gpu_uuid)
            if not session:
                return {"state":"idle","gpu_uuid":gpu_uuid,"reason":"Nenhum benchmark executado nesta GPU"}
            result = dict(session)
            result["device"] = json.loads(result.pop("device_json"))
            rows = [dict(row) for row in self.db.execute("SELECT * FROM observations WHERE session=? ORDER BY stamp,id",(session["id"],))]
            decision = self.db.execute("SELECT payload_json FROM decisions WHERE gpu_uuid=? ORDER BY id DESC LIMIT 1",(gpu_uuid,)).fetchone()
        result["elapsed_seconds"] = max(0,min(session["duration"],stamp-session["started"]))
        result["progress"] = min(1,result["elapsed_seconds"]/session["duration"])
        result["samples"] = len(rows)
        coverage=0.0
        for previous,current in zip(rows,rows[1:]):
            gap=current["stamp"]-previous["stamp"]
            if 0<gap<=15 and previous["temperature"] is not None and current["temperature"] is not None:
                coverage+=gap
        result["observed_seconds"]=coverage
        result["invalid_samples"] = sum(not row["valid"] for row in rows)
        result["unstable_samples"] = sum(row["reason"] in ("thermal_or_sensor","stale_sensor","incomplete_sensor","miner_restarted","new_rejected_share","invalid_counters","hardware_error")
            for row in rows if row["stamp"]-session["started"]>=30)
        # A repeated miner log line is one measurement, not 45 new hashes.
        independent = {}
        for row in rows:
            if row["valid"] and row["hash_stamp"]-session["started"]>=30:
                independent.setdefault(row["hash_stamp"],row)
        measurements = list(independent.values())
        result["independent_hash_samples"] = len(measurements)
        result["mean_hashrate_ths"] = statistics.mean(row["hashrate"] for row in measurements) if measurements else None
        result["mean_power_w"] = statistics.mean(row["power"] for row in measurements) if measurements else None
        result["efficiency_ths_per_w"] = result["mean_hashrate_ths"]/result["mean_power_w"] if result["mean_power_w"] else None
        result["validated"] = session["state"]=="complete" and len(measurements)>=3 and not result["unstable_samples"] and coverage>=session["duration"]*.7
        result["reason"] = ("Benchmark concluído com medições independentes" if result["validated"] else
            "Coletando hashrate real e estabilidade" if session["state"]=="running" else "Amostras insuficientes ou instáveis; repetir benchmark")
        profiles = {}
        for row in measurements:
            key=(row["clock"],row["power_limit"])
            profiles.setdefault(key,[]).append(row)
        candidates=[]
        for (clock,power_limit), group in profiles.items():
            if len(group)<3:
                continue
            rate=statistics.mean(row["hashrate"] for row in group)
            power=statistics.mean(row["power"] for row in group)
            candidates.append({"clock_mhz":clock,"power_limit_w":power_limit,"hashrate_ths":rate,
                "power_w":power,"efficiency_ths_per_w":rate/power,"samples":len(group)})
        metric="hashrate_ths" if session["objective"]=="performance" else "efficiency_ths_per_w"
        result["best_profile"] = max(candidates,key=lambda item:item[metric]) if candidates else None
        result["validated"] = result["validated"] and bool(result["best_profile"])
        if session["state"]=="complete" and not result["validated"]:
            result["reason"]="Amostras insuficientes, perfil ainda sem estabilidade ou cobertura interrompida; repetir benchmark"
        result["last_decision"] = json.loads(decision[0]) if decision else None
        return result

    def recommend(self, gpu, sample, *, ollama=False, now=None):
        validate_device(gpu)
        stamp=time.time() if now is None else float(now)
        sample=normalize_sample(sample,stamp)
        benchmark=self.status(gpu["uuid"],now=stamp)
        soft,hard=thermal_limits(gpu)
        temp=sample["temperature_c"]
        action,reason="hold","Aguardando benchmark e sensores estáveis"
        mode="adaptive"
        if temp is None or stamp-sample["sampled_at"]>10:
            reason="Sensor ausente ou antigo: ajuste bloqueado"
        elif temp>=hard:
            action,reason="clock_down","Emergência térmica: reduzir e solicitar pausa ao supervisor"
            mode="emergency"
        elif temp>=soft:
            action,reason="clock_down","Temperatura próxima do limite; reduzir gradualmente"
        elif benchmark["state"]=="running" and benchmark["elapsed_seconds"]>=60 and benchmark.get("independent_hash_samples",0)>=2:
            if benchmark.get("unstable_samples"):
                action,reason="clock_down","Benchmark instável ou shares rejeitadas: reduzir e validar novamente"
            elif sample["utilization_pct"] is not None and sample["utilization_pct"]>=90 and temp<=soft-3:
                if benchmark.get("objective")=="efficiency":
                    action,reason="power_down","Testar redução pequena de potência e medir hashrate por watt"
                else:
                    action,reason="clock_up","Testar próximo clock suportado e validar rendimento e temperatura"
        elif benchmark.get("validated") and benchmark.get("best_profile"):
            action,reason="restore_best","Reutilizar melhor perfil medido nesta GPU e neste benchmark"
        advice_status="disabled"
        if ollama and mode!="emergency":
            if not self.advisor:
                advice_status="unavailable: Ollama não configurado; política adaptativa ativa"
            else:
                try:
                    advice=self.advisor.advise({"gpu":gpu["name"],"sample":sample,"benchmark":{
                        key:benchmark.get(key) for key in ("state","objective","independent_hash_samples","mean_hashrate_ths","efficiency_ths_per_w","invalid_samples")},
                        "safe_default":{"action":action,"reason":reason},"soft_temperature_c":soft})
                    # The model cannot unlock warm-up, poor samples, thermal or missing sensors.
                    if action!="hold" and temp is not None and temp<soft and not benchmark.get("unstable_samples"):
                        action,reason=advice["action"],advice["reason"]
                    advice_status="local_ollama"
                except (OSError,ValueError,KeyError,TypeError,TimeoutError,URLError) as error:
                    advice_status="unavailable: "+str(error)[:160]
        plan=validated_plan(gpu,sample,action,benchmark.get("best_profile"),mode=mode)
        plan.update(reason=reason,source=mode,ai_status=advice_status,gpu_uuid=gpu["uuid"],at=stamp,
                    session=benchmark.get("id"),pause_required=mode=="emergency",writes_hardware=False)
        with self.lock,self.db:
            previous=self.db.execute("SELECT stamp,payload_json FROM decisions WHERE gpu_uuid=? AND json_extract(payload_json,'$.action')!='hold' ORDER BY id DESC LIMIT 1",(gpu["uuid"],)).fetchone()
            if previous and stamp-previous["stamp"]<30 and mode!="emergency":
                plan.update(action="hold",clock_mhz=None,power_limit_w=None,reason="Validando o ajuste anterior por pelo menos 30 segundos")
            self.db.execute("INSERT INTO decisions(gpu_uuid,stamp,session,payload_json) VALUES(?,?,?,?)",
                (gpu["uuid"],stamp,benchmark.get("id"),json.dumps(plan,ensure_ascii=False)))
        return plan

    def record_application(self,gpu,plan,before,baseline_hashrate,now=None):
        """Call only AFTER a successful write by the single hardware supervisor."""
        validate_device(gpu)
        sample=normalize_sample(before)
        baseline=numeric(baseline_hashrate)
        if baseline is None or baseline<=0 or plan.get("gpu_uuid")!=gpu["uuid"]:
            raise ValueError("Aplicação precisa da GPU e hashrate de referência reais")
        stamp=time.time() if now is None else float(now)
        payload={"before":sample,"plan":plan,"baseline_hashrate_ths":baseline,"samples":[]}
        with self.lock,self.db:
            pending=self.db.execute("SELECT 1 FROM trials WHERE gpu_uuid=? AND state='validating'",(gpu["uuid"],)).fetchone()
            if pending:
                raise RuntimeError("O ajuste anterior ainda está em validação")
            self.db.execute("INSERT INTO trials(gpu_uuid,stamp,state,payload_json) VALUES(?,?,'validating',?)",
                (gpu["uuid"],stamp,json.dumps(payload)))

    def trial_feedback(self,gpu,sample,hashrate_ths,hash_sampled_at,*,new_rejected_shares=0,now=None):
        """Measured trial validation; a rollback is also only a bounded proposal."""
        validate_device(gpu)
        stamp=time.time() if now is None else float(now)
        sample=normalize_sample(sample,stamp)
        with self.lock,self.db:
            row=self.db.execute("SELECT * FROM trials WHERE gpu_uuid=? AND state='validating' ORDER BY id DESC LIMIT 1",(gpu["uuid"],)).fetchone()
            if not row:
                return {"state":"idle"}
            payload=json.loads(row["payload_json"])
            elapsed=stamp-row["stamp"]
            rate,hash_stamp=numeric(hashrate_ths),numeric(hash_sampled_at)
            if rate and hash_stamp and row["stamp"]+30<=hash_stamp<=stamp and stamp-hash_stamp<=180:
                if not any(item[0]==hash_stamp for item in payload["samples"]):
                    payload["samples"].append([hash_stamp,rate])
            failure=bool(new_rejected_shares) or sample["temperature_c"] is None or sample["temperature_c"]>=thermal_limits(gpu)[0]
            measured=statistics.mean(item[1] for item in payload["samples"]) if payload["samples"] else None
            if elapsed>=90 and len(payload["samples"])>=2 and measured<payload["baseline_hashrate_ths"]*.97:
                failure=True
            state="rejected" if failure else "accepted" if elapsed>=90 and len(payload["samples"])>=2 else "validating"
            if elapsed>240 and state=="validating":
                state="rejected"
            self.db.execute("UPDATE trials SET state=?,payload_json=? WHERE id=?",(state,json.dumps(payload),row["id"]))
        result={"state":state,"elapsed_seconds":elapsed,"measured_hashrate_ths":measured,
                "baseline_hashrate_ths":payload["baseline_hashrate_ths"],"independent_hash_samples":len(payload["samples"])}
        if state=="rejected":
            original=payload["plan"]["action"]
            inverse={"clock_up":"clock_down","clock_down":"clock_up","power_up":"power_down","power_down":"power_up"}.get(original,"hold")
            if sample["temperature_c"] is None or sample["temperature_c"]>=thermal_limits(gpu)[0]:
                inverse="clock_down"
            result["rollback"]=validated_plan(gpu,sample,inverse)
            result["rollback"].update(gpu_uuid=gpu["uuid"],at=stamp,reason="Teste instável ou perda de hashrate; reverter o último degrau")
        return result

    def export_policy(self,gpu,directory,now=None):
        """A short-lived hint for the existing supervisor, never an authorization."""
        validate_device(gpu)
        stamp=time.time() if now is None else float(now)
        status=self.status(gpu["uuid"],now=stamp)
        if not status.get("validated") or not status.get("best_profile"):
            return None
        best=status["best_profile"]
        payload={"schema_version":1,"gpu_uuid":gpu["uuid"],"gpu_name":gpu["name"],"benchmark_id":status["id"],
            "validated":True,"generated_at":stamp,"valid_until":stamp+3600,
            "target_clock_mhz":best["clock_mhz"],"target_power_w":best["power_limit_w"],
            "hashrate_ths":best["hashrate_ths"],"objective":status["objective"],
            "authorization":False,"reason":"Melhor perfil medido; supervisor mantém seus próprios limites térmicos e de hardware"}
        directory=Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        destination=directory/"gpu-agent-policy.json"
        temporary=destination.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        temporary.replace(destination)
        return payload

    def cancel_trial(self,gpu,sample,now=None):
        """Manual rebenchmark cancels the pending experiment before restarting."""
        validate_device(gpu)
        with self.lock,self.db:
            row=self.db.execute("SELECT * FROM trials WHERE gpu_uuid=? AND state='validating' ORDER BY id DESC LIMIT 1",(gpu["uuid"],)).fetchone()
            if not row:
                return None
            payload=json.loads(row["payload_json"])
            self.db.execute("UPDATE trials SET state='cancelled_by_user' WHERE id=?",(row["id"],))
        inverse={"clock_up":"clock_down","clock_down":"clock_up","power_up":"power_down","power_down":"power_up"}.get(payload["plan"]["action"],"hold")
        current=normalize_sample(sample)
        if current["temperature_c"] is None or current["temperature_c"]>=thermal_limits(gpu)[0]:
            inverse="clock_down"
        plan=validated_plan(gpu,sample,inverse)
        plan.update(gpu_uuid=gpu["uuid"],at=time.time() if now is None else float(now),reason="Benchmark solicitado: desfazer experimento anterior")
        return plan


def validated_plan(gpu, sample, action, best_profile=None, *, mode="adaptive"):
    validate_device(gpu)
    if action not in ACTIONS:
        raise ValueError("Ação fora do conjunto permitido")
    sample=normalize_sample(sample)
    plan={"action":"hold","clock_mhz":None,"power_limit_w":None,"blocked":None}
    clocks=sorted({int(value) for value in gpu.get("supported_graphics_clocks_mhz",[]) if numeric(value) and value>0})
    current=sample["clock_mhz"]
    power=sample["power_limit_w"]
    minimum,maximum,default=(numeric(gpu.get(key)) for key in ("power_min_w","power_max_w","power_default_w"))
    if action in ("clock_up","clock_down","restore_best"):
        if not clocks or current is None:
            plan["blocked"]="Clock suportado não informado pelo driver; nenhuma escrita permitida"
            return plan
        if action=="restore_best":
            desired=numeric((best_profile or {}).get("clock_mhz"))
            if desired not in clocks:
                plan["blocked"]="Melhor perfil sem clock suportado"
                return plan
            action="clock_up" if desired>current else "clock_down" if desired<current else "hold"
        eligible=[clock for clock in clocks if (current<clock<=current+15 if action=="clock_up" else current-15<=clock<current)]
        if eligible:
            plan.update(action=action,clock_mhz=min(eligible) if action=="clock_up" else max(eligible))
        else:
            plan["blocked"]="Nenhum degrau suportado de até 15 MHz nesta direção"
    elif action in ("power_up","power_down"):
        if None in (minimum,maximum,default,power) or not 0<minimum<=default<=maximum or not minimum<=power<=maximum:
            plan["blocked"]="Limites reais de potência indisponíveis ou inconsistentes"
            return plan
        target=max(minimum,min(default,power+(5 if action=="power_up" else -5)))
        if action=="power_up" and target<=power or action=="power_down" and target>=power:
            plan["blocked"]="Limite de potência atingido"
        else:
            plan.update(action=action,power_limit_w=target)
    return plan


class NvidiaActuator:
    """Explicit control owner. Existing thermal supervisor must remain the sole writer."""
    def __init__(self, state_dir, *, enabled=False, command=smi_command):
        self.path=Path(state_dir)/"gpu-control-owner.json"
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.enabled=enabled
        self.command=command
        self.token=uuid.uuid4().hex
        self.owns=False

    def acquire(self, gpu_uuid, *, external_controller_active=False):
        if not self.enabled:
            raise RuntimeError("Escritas de hardware não foram habilitadas")
        if external_controller_active:
            raise RuntimeError("Já existe um supervisor térmico ativo; controle concorrente bloqueado")
        try:
            with self.path.open("x",encoding="utf-8") as handle:
                json.dump({"pid":os.getpid(),"token":self.token,"gpu_uuid":gpu_uuid,"at":time.time()},handle)
        except FileExistsError as error:
            raise RuntimeError("Outra sessão detém o controle da GPU; não remover a trava enquanto estiver ativa") from error
        self.owns=True

    def apply(self, gpu, sample, plan, *, external_controller_active=False):
        validate_device(gpu)
        if not self.enabled or not self.owns or external_controller_active:
            raise RuntimeError("Hardware somente leitura ou controle concorrente")
        owner=json.loads(self.path.read_text(encoding="utf-8"))
        if owner.get("token")!=self.token or owner.get("gpu_uuid")!=gpu["uuid"]:
            raise RuntimeError("Posse da GPU inválida")
        stamp=time.time()
        normalized=normalize_sample(sample)
        if normalized["temperature_c"] is None or not 0<=normalized["temperature_c"]<=120 or not -2<=stamp-normalized["sampled_at"]<=10:
            raise RuntimeError("Sensores atuais obrigatórios antes de aplicar")
        if plan.get("gpu_uuid")!=gpu["uuid"] or not 0<=stamp-numeric(plan.get("at"),0)<=30:
            raise RuntimeError("Plano antigo ou de outra GPU")
        checked=validated_plan(gpu,sample,plan["action"])
        for key in ("clock_mhz","power_limit_w"):
            if plan.get(key)!=checked.get(key):
                raise ValueError("Plano adulterado ou fora dos limites do hardware")
        if normalized["temperature_c"]>=thermal_limits(gpu)[0] and plan["action"] in ("clock_up","power_up"):
            raise RuntimeError("Aumento bloqueado pela temperatura")
        if checked["clock_mhz"] is not None:
            value=int(checked["clock_mhz"])
            self.command(["-i",gpu["uuid"],"-lgc",f"{value},{value}"])
        elif checked["power_limit_w"] is not None:
            self.command(["-i",gpu["uuid"],"-pl",str(checked["power_limit_w"])])
        return checked

    def close(self):
        if self.owns:
            owner=json.loads(self.path.read_text(encoding="utf-8"))
            if owner.get("token")==self.token:
                self.path.unlink()
            self.owns=False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot",action="store_true",help="Detecta GPUs NVIDIA sem modificar hardware")
    parser.add_argument("--benchmark",action="store_true",help="Mede o minerador ativo; não altera clocks")
    parser.add_argument("--duration",type=int,default=300)
    parser.add_argument("--objective",choices=("performance","efficiency"),default="performance")
    parser.add_argument("--gpu",type=int,default=None)
    parser.add_argument("--index",type=int,dest="gpu",help="Alias de --gpu")
    parser.add_argument("--uuid",help="Identidade NVIDIA estável; índice informado precisa corresponder")
    parser.add_argument("--log-dir",type=Path,help="Diretório exclusivo de logs desta GPU")
    parser.add_argument("--miner-log",type=Path,help="Log da instância exclusiva do minerador desta GPU")
    parser.add_argument("--miner-index",type=int,help="Índice SRBMiner confirmado por PCI; pode diferir do índice NVIDIA")
    parser.add_argument("--state-dir",type=Path)
    parser.add_argument("--monitor-state-dir",type=Path)
    parser.add_argument("--control",action="store_true",help="Supervisor NVIDIA explícito; exige --miner-pid e elevação")
    parser.add_argument("--miner-pid",type=int)
    parser.add_argument("--samples",type=int,default=0,help=argparse.SUPPRESS)
    parser.add_argument("--ollama",action="store_true")
    args=parser.parse_args()
    from config import STATE_DIR
    state_dir=args.state_dir or STATE_DIR
    if args.control:
        if not args.miner_pid:
            parser.error("--control exige --miner-pid")
        raise SystemExit(control(args,state_dir,args.monitor_state_dir or STATE_DIR))
    devices=detect_nvidia_gpus()
    if args.snapshot or not args.benchmark:
        print(json.dumps({"gpus":devices,"hardware_writes":False},ensure_ascii=False,indent=2))
        return
    gpu=select_device(devices,args)
    agent=GPUAgent(state_dir,advisor=RoutedAdvisor(__import__("config").STATE_DIR))
    agent.start_benchmark(gpu,args.duration,args.objective)
    try:
        while True:
            miner,fresh=read_miner_data(args,gpu,args.monitor_state_dir or state_dir,len(devices))
            sample=read_sample(gpu)
            status=agent.observe(gpu,sample,miner.get("hashrate") if fresh else None,miner.get("accepted",0),miner.get("rejected",0),hash_sampled_at=miner.get("hashrate_at"),hw_errors=miner.get("hw_errors",0))
            plan=agent.recommend(gpu,sample,ollama=args.ollama)
            print(json.dumps({"benchmark":status,"proposal":plan,"hardware_writes":False},ensure_ascii=False),flush=True)
            if status["state"]!="running":
                break
            time.sleep(10)
    finally:
        agent.close()


def process_alive(pid):
    if not isinstance(pid,int) or pid<=0:
        return False
    if os.name=="nt":
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            return False
        try:
            code=wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid,0)
        return True
    except (OSError,PermissionError):
        return False


def external_supervisor_active(*directories,gpu_uuid=None,gpu_index=None):
    for directory in directories:
        for name in ("thermal-control.json","gpu-agent-control.json"):
            try:
                status=json.loads((Path(directory)/name).read_text(encoding="utf-8-sig"))
                pid=status.get("controller_pid")
                reported=status.get("gpu_uuid") or (status.get("gpu") or {}).get("uuid")
                if gpu_uuid is not None:
                    if reported and reported!=gpu_uuid:
                        continue
                    if not reported and gpu_index is not None and int(status.get("gpu_index",0))!=int(gpu_index):
                        continue
                if pid!=os.getpid() and status.get("enabled") and process_alive(pid):
                    return True
            except (OSError,ValueError,TypeError):
                pass
    return False


def process_identity(pid):
    """PID mais horário de criação e executável; um PID reutilizado é outro processo."""
    if not isinstance(pid,int) or pid<=0:
        return None
    if os.name=="nt":
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            return None
        try:
            created,exited,system,user=(wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user)):
                return None
            buffer=ctypes.create_unicode_buffer(32768)
            length=wintypes.DWORD(len(buffer))
            if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(length)):
                return None
            return {"created_filetime":(created.dwHighDateTime<<32)|created.dwLowDateTime,"executable":buffer.value.casefold()}
        finally:
            kernel.CloseHandle(handle)
    try:
        proc=Path("/proc")/str(pid)
        fields=(proc/"stat").read_text().rsplit(")",1)[1].split()
        return {"start_ticks":fields[19],"executable":str((proc/"exe").resolve(strict=True))}
    except (OSError,ValueError,IndexError):
        return None


def same_process(pid,identity):
    return identity is not None and process_alive(pid) and process_identity(pid)==identity


def requested_control_stop(state_dir,gpu_uuid,started_at,now=None):
    stamp=time.time() if now is None else float(now)
    try:
        request=json.loads((Path(state_dir)/"gpu-agent-stop.json").read_text(encoding="utf-8-sig"))
        requested=numeric(request.get("at"),0)
        return (request.get("gpu_uuid")==gpu_uuid and request.get("pid")==os.getpid()
                and requested>=started_at and -2<=stamp-requested<=60)
    except (OSError,ValueError,AttributeError):
        return False


def select_device(devices,args):
    identifier=getattr(args,"uuid",None)
    index=getattr(args,"gpu",None)
    if identifier:
        gpu=next((item for item in devices if item["uuid"]==identifier),None)
        if gpu is not None and index is not None and gpu["index"]!=index:
            raise RuntimeError("UUID e índice NVIDIA não correspondem; atualização de identidade necessária")
    else:
        gpu=next((item for item in devices if item["index"]==(0 if index is None else index)),None)
    if gpu is None:
        raise RuntimeError("GPU NVIDIA selecionada não encontrada")
    miner_index=getattr(args,"miner_index",None)
    if miner_index is not None:
        if not isinstance(miner_index,int) or not 0<=miner_index<=255:
            raise RuntimeError("Índice SRBMiner inválido")
        gpu={**gpu,"miner_index":miner_index}
    return validate_device(gpu)


def snapshot_device_payload(snapshot,gpu,device_count):
    """Nunca utiliza hashrate agregado de um rig como se pertencesse a uma GPU."""
    devices=(snapshot.get("rig") or {}).get("devices",snapshot.get("devices"))
    if isinstance(devices,dict):
        entry=devices.get(gpu["uuid"])
        if isinstance(entry,dict):
            return entry
        return None
    elif isinstance(devices,list):
        for entry in devices:
            if not isinstance(entry,dict):
                continue
            identity=entry.get("identity") or entry.get("gpu") or entry
            if identity.get("uuid")==gpu["uuid"] or entry.get("gpu_uuid")==gpu["uuid"]:
                return entry
        return None
    reported=(snapshot.get("gpu") or {}).get("uuid") or ((snapshot.get("gpu_agent") or {}).get("gpu") or {}).get("uuid")
    if reported==gpu["uuid"]:
        return snapshot
    if not reported and device_count==1 and gpu["index"]==0:
        return snapshot
    return None


def read_miner_data(args,gpu,monitor_state_dir,device_count,now=None):
    stamp=time.time() if now is None else float(now)
    path=getattr(args,"miner_log",None)
    log_dir=getattr(args,"log_dir",None)
    if path is None and log_dir is not None:
        paths=list(Path(log_dir).glob("mining-*.log"))
        path=max(paths,key=lambda item:item.stat().st_mtime) if paths else None
        if path is None:
            return {},False
    if path is not None:
        from gpu_telemetry import read_device_payload
        payload=read_device_payload(gpu,path,now=stamp,allow_local_gpu0=True,
                                    miner_index=getattr(args,"miner_index",None))
        miner=payload["miner"]
        hash_stamp=numeric(miner.get("hashrate_at"))
        return miner,not payload["log_error"] and hash_stamp is not None and 0<=stamp-hash_stamp<=180
    try:
        snapshot=json.loads((Path(monitor_state_dir)/"latest.json").read_text(encoding="utf-8-sig"))
    except (OSError,ValueError):
        return {},False
    entry=snapshot_device_payload(snapshot,gpu,device_count)
    if entry is None:
        return {},False
    collected=numeric(entry.get("collected_at"),numeric(snapshot.get("collected_at"),0))
    miner=entry.get("miner") or {}
    return miner,0<=stamp-collected<=10


def write_status(path,payload):
    from contribution import atomic_json
    atomic_json(path,payload)


def control(args,state_dir,monitor_state_dir):
    """Explicit owner process. Never called by read-only monitor/imports."""
    if os.name=="nt":
        import ctypes
        if not ctypes.WinDLL("shell32").IsUserAnAdmin():
            raise RuntimeError("O supervisor de GPU precisa ser iniciado como administrador")
    if args.duration<300 or args.duration>3600:
        raise ValueError("Controle exige baseline de 300 a 3600 segundos")
    devices=detect_nvidia_gpus()
    gpu=select_device(devices,args)
    state_dir,monitor_state_dir=Path(state_dir),Path(monitor_state_dir)
    def other_supervisor():
        return external_supervisor_active(state_dir,monitor_state_dir,gpu_uuid=gpu["uuid"],gpu_index=gpu["index"])
    if other_supervisor():
        raise RuntimeError("Já existe supervisor ativo: o agente fica somente leitura")
    if not process_alive(args.miner_pid):
        raise RuntimeError("Processo do minerador já não está ativo")
    miner_identity=process_identity(args.miner_pid)
    if miner_identity is None or Path(miner_identity["executable"]).name.casefold()!="srbminer-multi.exe":
        raise RuntimeError("Não foi possível confirmar identidade e criação do processo SRBMiner")
    baseline=read_sample(gpu)
    original_power=baseline["power_limit_w"]
    actuator=NvidiaActuator(state_dir,enabled=True,command=smi_command)
    agent=GPUAgent(state_dir,advisor=RoutedAdvisor(__import__("config").STATE_DIR))
    background_advisor=BackgroundAdvisor(agent.advisor) if agent.advisor else None
    status_path=state_dir/"gpu-agent-control.json"
    status={"enabled":False,"controller_pid":os.getpid(),"miner_pid":args.miner_pid,"gpu_uuid":gpu["uuid"],
            "gpu_name":gpu["name"],"mode":"baseline","sampled_at":time.time(),"error":None,"writes":0}
    owns=False
    controller_started_at=time.time()
    try:
        actuator.acquire(gpu["uuid"],external_controller_active=other_supervisor())
        owns=True
        status["enabled"]=True
        agent.start_benchmark(gpu,args.duration,args.objective)
        trial_active=False
        cycles=0
        last_request=0
        last_thermal_adjustment=0
        objective=args.objective
        while same_process(args.miner_pid,miner_identity):
            if requested_control_stop(state_dir,gpu["uuid"],controller_started_at):
                status.update(mode="stopped",reason="Parada solicitada pelo gerenciador desta GPU")
                break
            if other_supervisor():
                raise RuntimeError("Outro supervisor detectado; interrompendo controle concorrente")
            miner,fresh=read_miner_data(args,gpu,monitor_state_dir,len(devices))
            sample=read_sample(gpu)
            if sample["temperature_c"] is None or sample["temperature_c"]>=thermal_limits(gpu)[1]:
                raise RuntimeError("Limite térmico ou sensor inválido: supervisor deve encerrar o minerador")
            now=time.time()
            if sample["temperature_c"]>=thermal_limits(gpu)[0] and now-last_thermal_adjustment>=6:
                cooling=validated_plan(gpu,sample,"clock_down")
                cooling.update(gpu_uuid=gpu["uuid"],at=now,reason="Alívio térmico gradual durante a medição")
                if cooling["action"]!="hold":
                    actuator.apply(gpu,sample,cooling,external_controller_active=False)
                    status.update(mode="cooling",thermal_proposal=cooling,writes=status["writes"]+1)
                    last_thermal_adjustment=now
                    sample=read_sample(gpu)
            try:
                request=json.loads((state_dir/"gpu-agent-request.json").read_text(encoding="utf-8-sig"))
                request_stamp=numeric(request.get("at"),0)
                if request.get("action")=="benchmark" and request.get("gpu_uuid")==gpu["uuid"] and request_stamp>last_request and 0<=now-request_stamp<=60:
                    objective=request.get("objective","performance")
                    duration=numeric(request.get("duration_seconds"),300)
                    if objective not in ("performance","efficiency") or not 300<=duration<=3600:
                        raise ValueError("Pedido de benchmark inválido")
                    if trial_active:
                        rollback=agent.cancel_trial(gpu,sample)
                        if rollback and rollback["action"]!="hold":
                            actuator.apply(gpu,sample,rollback,external_controller_active=False)
                            status["writes"]+=1
                        trial_active=False
                    agent.start_benchmark(gpu,duration,objective)
                    last_request=request_stamp
                    status.update(mode="manual_baseline",last_manual_request=request_stamp)
            except FileNotFoundError:
                pass
            except (ValueError,TypeError):
                status["request_error"]="Pedido de benchmark inválido; ignorado"
            if trial_active and not fresh:
                raise RuntimeError("Monitor sem telemetria atual durante ajuste; encerrando controle")
            result=agent.observe(gpu,sample,miner.get("hashrate") if fresh else None,miner.get("accepted",0),
                                 miner.get("rejected",0),hash_sampled_at=miner.get("hashrate_at"),hw_errors=miner.get("hw_errors",0))
            status.update(sampled_at=now,sample=sample,benchmark=result)
            answer=None
            if background_advisor and agent.advisor.enabled() and result.get("elapsed_seconds",0)>=60:
                answer=background_advisor.poll({"sample":sample,"benchmark":{key:result.get(key) for key in
                    ("validated","mean_hashrate_ths","mean_power_w","efficiency_ths_per_w")},"allowed_actions":list(ACTIONS)})
                status["ai_status"]=answer["status"]
            if trial_active:
                trial=agent.trial_feedback(gpu,sample,miner.get("hashrate"),miner.get("hashrate_at"),
                    new_rejected_shares=max(0,int(miner.get("rejected",0))-trial_rejected))
                status.update(mode="validating",trial=trial)
                if trial["state"]=="rejected":
                    rollback=trial.get("rollback")
                    if rollback:
                        actuator.apply(gpu,sample,rollback,external_controller_active=False)
                        status["writes"]+=1
                    trial_active=False
                    status["mode"]="rollback"
                    agent.start_benchmark(gpu,args.duration,objective)
                elif trial["state"]=="accepted":
                    trial_active=False
                    status["mode"]="accepted"
                    agent.start_benchmark(gpu,args.duration,objective)
            elif result.get("validated") and fresh:
                # Explore one device-supported step after a measured baseline.
                desired="power_down" if objective=="efficiency" else "clock_up"
                soft,_=thermal_limits(gpu)
                if sample["temperature_c"]>=soft:
                    desired="clock_down"
                if answer:
                    advice=answer["advice"]
                    if advice and sample["temperature_c"]<soft:
                        desired=advice["action"]
                    status["ai_status"]=answer["status"]
                plan=validated_plan(gpu,sample,desired,result.get("best_profile"))
                plan.update(gpu_uuid=gpu["uuid"],at=time.time(),reason="Experimento após baseline validado")
                if plan["action"]!="hold" and (plan["action"] not in ("clock_up","power_up") or sample["temperature_c"]<soft-3):
                    actuator.apply(gpu,sample,plan,external_controller_active=False)
                    agent.record_application(gpu,plan,sample,result["mean_hashrate_ths"])
                    trial_rejected=int(miner.get("rejected",0))
                    trial_active=True
                    status.update(mode="validating",proposal=plan,writes=status["writes"]+1)
                else:
                    status.update(mode="hold",proposal=plan)
                    agent.start_benchmark(gpu,args.duration,objective)
            elif result["state"]=="complete":
                status["mode"]="insufficient_data"
                agent.start_benchmark(gpu,args.duration,objective)
            write_status(status_path,status)
            cycles+=1
            if args.samples and cycles>=args.samples:
                break
            time.sleep(2)
        return 0
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        status.update(mode="failed",error=str(error),reason="Controle encerrado; iniciador deve parar seu minerador")
        return 1
    finally:
        # The caller owns miner lifecycle. Returning nonzero is the stop signal.
        # The CLI never kills an arbitrary PID supplied on its command line.
        if owns:
            try:
                smi_command(["-i",gpu["uuid"],"-rgc"])
                minimum,maximum=(numeric(gpu.get(key)) for key in ("power_min_w","power_max_w"))
                if minimum is not None and maximum is not None and original_power is not None and minimum<=original_power<=maximum:
                    smi_command(["-i",gpu["uuid"],"-pl",str(original_power)])
            except (OSError,RuntimeError,subprocess.SubprocessError) as error:
                status["restore_error"]=str(error)
            actuator.close()
        status.update(enabled=False,sampled_at=time.time())
        write_status(status_path,status)
        agent.close()


if __name__=="__main__":
    main()
