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
        try:
            return dt.datetime.strptime(match[1],"%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            pass
    return None


def parse_gpu_log(text,gpu_uuid,index,now=None,*,allow_local_gpu0=True):
    """Seleciona índice físico ou GPU0 de uma instância comprovadamente isolada.

    Em log compartilhado, médias globais e shares sem identificador não podem ser
    atribuídos a uma placa. `Total:` jamais participa de hashrate por GPU.
    """
    stamp_now=time.time() if now is None else float(now)
    lines=text.splitlines()
    labels={int(match[1]) for line in lines if (match:=GPU_LABEL.search(line))}
    target=int(index) if int(index) in labels else 0 if allow_local_gpu0 and labels=={0} else None
    result={"gpu_uuid":gpu_uuid,"gpu_index":int(index),"log_gpu_index":target,
            "hashrate":None,"hashrate_at":None,"accepted":0,"rejected":0,"hw_errors":0,
            "average_1m":None,"average_15m":None,"started_at":None,"last_share_at":None,
            "connection":"Aguardando conexão","paused":False,"rate_points":[],
            "isolated_log":len(labels)<=1,"recent_logs":"\n".join(lines[-35:])}
    for line in lines:
        stamp=log_timestamp(line)
        label=GPU_LABEL.search(line)
        belongs=label is not None and target is not None and int(label[1])==target
        if "Miner version:" in line:
            result["started_at"]=stamp
        if "Connected to " in line:
            result["connection"]="Conectado à pool"
        if any(word in line.lower() for word in ("disconnected","connection failed","reconnecting","connection lost")):
            result["connection"]="Reconectando à pool"
        if "share accepted" in line and (belongs or (label is None and len(labels)==1 and target is not None)):
            result["last_share_at"]=stamp
        if belongs and "turned off" in line:
            result["paused"]=True
        elif belongs and "turned on" in line:
            result["paused"]=False
        match=RATE.search(line)
        if match and target is not None and int(match[1])==target:
            value=numeric(match[2])
            rate=value*UNITS[match[3]] if value is not None and match[3] in UNITS else None
            # Out-of-order log delivery may add chart points but cannot replace a newer live value.
            if stamp is not None and rate is not None and stamp_now-900<=stamp<=stamp_now+5:
                result["rate_points"].append((stamp,rate))
            if result["hashrate_at"] is None or stamp is not None and stamp>=result["hashrate_at"]:
                result["hashrate"],result["hashrate_at"]=rate,stamp
                counters=dict(re.findall(r"([A-Z]+):([\d.]+)",match[4]))
                for key,code in (("accepted","A"),("rejected","R"),("hw_errors","HW")):
                    result[key]=max(0,int(float(counters.get(code,0))))
        average=AVERAGE.search(line)
        if average and target is not None and (belongs or (label is None and len(labels)==1)):
            first,first_unit,last,last_unit=average.groups()
            if first_unit in UNITS and last_unit in UNITS:
                result["average_1m"]=float(first)*UNITS[first_unit]
                result["average_15m"]=float(last)*UNITS[last_unit]
    result["rate_points"]=sorted(set(result["rate_points"]))
    return result


def read_log_tail(path,limit=200_000):
    path=Path(path)
    with path.open("rb") as handle:
        handle.seek(0,2)
        size=handle.tell()
        handle.seek(max(0,size-limit))
        body=handle.read()
    encoding="utf-16" if body.startswith((b"\xff\xfe",b"\xfe\xff")) else "utf-8-sig"
    text=body.decode(encoding,errors="replace")
    return text.split("\n",1)[-1] if size>limit else text


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
