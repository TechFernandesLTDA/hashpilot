"""Telemetria e logs isolados por UUID NVIDIA, sem Tk e sem escritas de hardware."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
import re
import subprocess
import time

from gpu_agent import numeric, smi_command, validate_device

FIELDS=("uuid","index","name","driver_version","temperature.gpu","power.draw","power.limit",
        "clocks.gr","clocks.mem","utilization.gpu","utilization.memory","memory.used","memory.total","fan.speed","pstate")
NUMERIC_FIELDS=FIELDS[4:-1]
STAMP=re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]")
GPU_LABEL=re.compile(r"\bGPU\s*(?:\[\s*)?(\d+)(?:\s*\])?(?!\d)")
RATE=re.compile(r"\bGPU\s*(?:\[\s*)?(\d+)(?:\s*\])?\s+.*?:\s*([\d.]+)\s+([kMGTPE]?H)/s\s+\[(.*?)\]")
AVERAGE=re.compile(r"Average hashrate:\s*1m\s+([\d.]+)\s+([kMGTPE]?H)/s\s*\|\s*15m\s+([\d.]+)\s+([kMGTPE]?H)/s",re.I)
UNITS={"H":1e-12,"kH":1e-9,"MH":1e-6,"GH":1e-3,"TH":1,"PH":1e3,"EH":1e6}
COUNTERS=re.compile(r"([A-Z]+):([\d.]+)")
CACHE_LIMIT=32
_STAMPS={}
_PARSED={}
_TAILS={}


def _remember(cache,key,value):
    if key not in cache and len(cache)>=CACHE_LIMIT:cache.pop(next(iter(cache)))
    cache[key]=value


def read_gpu_samples(devices, command=smi_command, now=None):
    """Uma consulta CSV para todas as placas; erros e ausência não viram zeros."""
    stamp=time.time() if now is None else float(now)
    devices=list(devices)
    expected={validate_device(gpu)["uuid"]:gpu for gpu in devices}
    result={identifier:{"uuid":identifier,"index":gpu["index"],"name":gpu["name"],
                        "sampled_at":stamp,"error":"GPU não apareceu na consulta NVIDIA"}
            for identifier,gpu in expected.items()}
    if not devices:
        return result
    try:
        output=command(["--query-gpu="+",".join(FIELDS),"--format=csv,noheader,nounits"])
        for row in csv.reader(output.splitlines()):
            values=[value.strip() for value in row]
            if len(values)!=len(FIELDS) or values[0] not in expected:
                continue
            sample=dict(zip(FIELDS,values))
            identifier=sample["uuid"]
            try:
                reported_index=int(sample["index"])
            except ValueError:
                continue
            sample["index"]=reported_index
            # UUID establishes identity even if driver enumeration changed.
            for field in NUMERIC_FIELDS:
                sample[field]=numeric(sample.get(field))
            sample.update(sampled_at=stamp,error=None)
            temperature=sample.get("temperature.gpu")
            if temperature is None or not 0<=temperature<=120:
                sample["error"]="Temperatura NVIDIA indisponível ou inválida"
            result[identifier]=sample
    except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
        for sample in result.values():
            sample["error"]=str(error)[:240]
    return result


def log_timestamp(line):
    match=STAMP.match(line)
    if match:
        text=match[1]
        if text in _STAMPS:return _STAMPS[text]
        try:
            value=dt.datetime.strptime(text,"%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            value=None
        if len(_STAMPS)>=4096:_STAMPS.clear()
        _STAMPS[text]=value
        return value
    return None


def parse_gpu_log(text,gpu_uuid,index,now=None,*,allow_local_gpu0=True):
    """Seleciona índice físico ou GPU0 de uma instância comprovadamente isolada.

    Em log compartilhado, médias globais e shares sem identificador não podem ser
    atribuídos a uma placa. `Total:` jamais participa de hashrate por GPU.
    O mesmo texto não é reprocessado: só a janela de 900 s dos pontos é reaplicada.
    """
    stamp_now=time.time() if now is None else float(now)
    key=(gpu_uuid,int(index),bool(allow_local_gpu0))
    cached=_PARSED.get(key)
    # Reuse only while time moves forward and no skipped future-dated line has become current.
    if cached and cached[0]==text and cached[2]<=stamp_now<cached[3]-5:
        result=dict(cached[1])
        result["rate_points"]=[point for point in result["rate_points"] if stamp_now-900<=point[0]<=stamp_now+5]
        return result
    result,future=_parse_gpu_log(text,gpu_uuid,index,stamp_now,allow_local_gpu0)
    _remember(_PARSED,key,(text,{**result,"rate_points":list(result["rate_points"])},stamp_now,float("inf") if future is None else future))
    return result


def _parse_gpu_log(text,gpu_uuid,index,stamp_now,allow_local_gpu0):
    future=None
    lines=text.splitlines()
    labels={int(match[1]) for line in lines if (match:=GPU_LABEL.search(line))}
    target=int(index) if int(index) in labels else 0 if allow_local_gpu0 and labels=={0} else None
    result={"gpu_uuid":gpu_uuid,"gpu_index":int(index),"log_gpu_index":target,
            "hashrate":None,"hashrate_at":None,"accepted":0,"rejected":0,"hw_errors":0,
            "average_1m":None,"average_15m":None,"started_at":None,"last_share_at":None,
            "connection":"Aguardando conexão","connection_at":None,"last_job_at":None,"last_log_at":None,"paused":False,"rate_points":[],
            "isolated_log":len(labels)<=1,"recent_logs":"\n".join(lines[-35:])}
    for line in lines:
        stamp=log_timestamp(line)
        if stamp is not None and stamp>stamp_now+5:
            future=stamp if future is None else min(future,stamp)
            continue
        if stamp is None:continue
        if result['last_log_at'] is None or stamp>=result['last_log_at']:result['last_log_at']=stamp
        label=GPU_LABEL.search(line)
        belongs=label is not None and target is not None and int(label[1])==target
        if "Miner version:" in line:
            result["started_at"]=stamp
            result['version']=line.split('Miner version:',1)[1].strip()
        if "Job received" in line and (result['last_job_at'] is None or stamp>=result['last_job_at']):
            result['last_job_at']=stamp
        if result['connection_at'] is None or stamp>=result['connection_at']:
            if "Connected to " in line or "Job received" in line:
                result["connection"],result['connection_at']="Conectado à pool",stamp
            if any(word in line.lower() for word in ("disconnected","connection failed","reconnecting","connection lost")):
                result["connection"],result['connection_at']="Reconectando à pool",stamp
        if "share accepted" in line and (belongs or (label is None and len(labels)==1 and target is not None)):
            if result['last_share_at'] is None or stamp>=result['last_share_at']:result["last_share_at"]=stamp
        if belongs and "turned off" in line:
            result["paused"]=True
        elif belongs and "turned on" in line:
            result["paused"]=False
        match=RATE.search(line)
        if match and target is not None and int(match[1])==target:
            value=numeric(match[2])
            rate=numeric(value*UNITS[match[3]]) if value is not None and match[3] in UNITS else None
            # Out-of-order log delivery may add chart points but cannot replace a newer live value.
            if stamp is not None and rate is not None and stamp_now-900<=stamp<=stamp_now+5:
                result["rate_points"].append((stamp,rate))
            if stamp is not None and stamp<=stamp_now+5 and rate is not None and rate>=0 and (result["hashrate_at"] is None or stamp>=result["hashrate_at"]):
                result["hashrate"],result["hashrate_at"]=rate,stamp
                counters=dict(COUNTERS.findall(match[4]))
                for key,code in (("accepted","A"),("rejected","R"),("hw_errors","HW")):
                    value=numeric(counters.get(code))
                    if value is not None and value>=0 and value.is_integer():result[key]=int(value)
        average=AVERAGE.search(line)
        if average and target is not None and (belongs or (label is None and len(labels)==1)):
            first,first_unit,last,last_unit=average.groups()
            if first_unit in UNITS and last_unit in UNITS:
                first,last=numeric(first),numeric(last)
                if first is not None:result["average_1m"]=numeric(first*UNITS[first_unit])
                if last is not None:result["average_15m"]=numeric(last*UNITS[last_unit])
    result["rate_points"]=sorted(dict(result["rate_points"]).items())
    return result,future



def read_log_tail(path,limit=200_000):
    """Lê só os bytes acrescentados desde a última chamada, conferindo a emenda com o que já foi lido."""
    path=Path(path)
    with path.open("rb") as handle:
        handle.seek(0,2)
        size=handle.tell()
        cached=_TAILS.get((str(path),limit))
        if cached and cached["size"]<=size and size-cached["size"]<limit:
            check=cached["body"][-256:]
            handle.seek(cached["size"]-len(check))
            if handle.read(len(check))==check:
                body=cached["body"]+handle.read(size-cached["size"])
                start=cached["start"]
                if len(body)>limit:
                    cut=len(body)-limit
                    if cached["signature"] in (b'\xff\xfe',b'\xfe\xff'):cut+=cut%2
                    body,start=body[cut:],start+cut
                return _remember_tail(path,limit,cached["signature"],size,start,body)
        handle.seek(0)
        signature=handle.read(2)
        start=max(0,size-limit)
        if signature in (b'\xff\xfe',b'\xfe\xff'):start-=start%2
        handle.seek(start)
        body=handle.read(size-start)
    return _remember_tail(path,limit,signature,size,start,body)


def _remember_tail(path,limit,signature,size,start,body):
    _remember(_TAILS,(str(path),limit),{"signature":signature,"size":size,"start":start,"body":body})
    encoding=('utf-16-le' if signature==b'\xff\xfe' else 'utf-16-be') if signature in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig'
    text=body.decode(encoding,errors="replace").lstrip('﻿')
    return text.split("\n",1)[-1] if start>0 or size>limit else text


def read_device_payload(gpu,log_path,*,miner_running=None,now=None,allow_local_gpu0=True,miner_index=None):
    validate_device(gpu)
    log_index=gpu.get("miner_index",gpu["index"]) if miner_index is None else miner_index
    result={"identity":dict(gpu),"miner_running":miner_running,"log_path":str(log_path) if log_path else None,
            "log_error":None,"miner":parse_gpu_log("",gpu["uuid"],log_index,now,allow_local_gpu0=allow_local_gpu0)}
    result["miner"].update(gpu_index=gpu["index"],miner_index=log_index)
    if log_path is None:
        result["log_error"]="Nenhum log próprio desta GPU encontrado"
        return result
    try:
        text=read_log_tail(log_path)
        result["miner"]=parse_gpu_log(text,gpu["uuid"],log_index,now,allow_local_gpu0=allow_local_gpu0)
        result["miner"].update(gpu_index=gpu["index"],miner_index=log_index)
    except (OSError,ValueError) as error:
        result["log_error"]=str(error)[:240]
    return result
