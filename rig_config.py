"""Configuração de até seis GPUs NVIDIA por UUID, sem acesso ao hardware."""
from __future__ import annotations
import copy
from pathlib import Path
import re
from contribution import AUTHOR_WALLET,valid_wallet

MAX_GPUS=6


def device_key(gpu_uuid):
    if not isinstance(gpu_uuid,str) or not re.fullmatch(r"GPU-[A-Za-z0-9-]{1,100}",gpu_uuid):
        raise ValueError("UUID NVIDIA inválido")
    return gpu_uuid


def gpu_paths(log_root,gpu_uuid):
    root=Path(log_root).resolve()/"gpus"/device_key(gpu_uuid)
    return {"log_dir":str(root),"state_dir":str(root),"latest_path":str(root/"latest.json"),
        "controller_status_path":str(root/"gpu-agent-control.json"),
        "proxy_settings_path":str(root/"proxy-settings.json"),
        "benchmark_request_path":str(root/"gpu-agent-request.json")}


def resolve_devices(detected,settings,log_root):
    """New GPUs default enabled; only a running manager starts desired workers."""
    if not isinstance(settings,dict):
        raise ValueError("Configuração do rig inválida")
    global_wallet=settings.get("wallet",AUTHOR_WALLET)
    if not valid_wallet(global_wallet):
        raise ValueError("Carteira global Pearl inválida")
    overrides=settings.get("gpu_settings",{})
    if not isinstance(overrides,dict):
        raise ValueError("gpu_settings precisa ser um objeto por UUID")
    result=[]
    seen=set()
    candidates=[gpu for gpu in detected if isinstance(gpu,dict) and gpu.get('vendor')=='NVIDIA']
    for gpu in candidates:
        if isinstance(gpu.get('index'),bool) or not isinstance(gpu.get('index'),int) or gpu['index']<0:
            raise ValueError('Índice NVIDIA inválido')
    for detected_gpu in sorted(candidates,key=lambda item:item['index']):
        identifier=device_key(detected_gpu.get("uuid"))
        if identifier in seen:
            continue
        seen.add(identifier)
        if len(result)>=MAX_GPUS:
            break
        entry=overrides.get(identifier,{})
        error=None
        if not isinstance(entry,dict):
            entry={"enabled":False}
            error="Configuração desta GPU inválida; dispositivo desabilitado"
        enabled=entry.get("enabled",True)
        if not isinstance(enabled,bool):
            enabled=False
            error="enabled precisa ser booleano; dispositivo desabilitado"
        override=entry.get("wallet")
        if override=='':override=None
        if override is not None and not valid_wallet(override):
            enabled=False
            override=None
            error="Carteira desta GPU inválida; dispositivo desabilitado"
        wallet=override or global_wallet
        gpu={**detected_gpu,**gpu_paths(log_root,identifier),"enabled":enabled,
            "wallet":wallet,"effective_wallet":wallet,"wallet_override":override,
            "uses_default_wallet":override is None,"uses_author_wallet":wallet==AUTHOR_WALLET,
            "device_key":identifier,"worker_name":"HashPilot_"+identifier[4:],
            "configuration_error":error}
        result.append(gpu)
    return result


def update_gpu_settings(settings,gpu_uuid,*,enabled=None,wallet_override=None,change_wallet=False):
    """Return a new settings document; callers retain ownership of atomic saving."""
    identifier=device_key(gpu_uuid)
    if not isinstance(settings,dict):
        raise ValueError("Configuração inválida")
    result=copy.deepcopy(settings)
    overrides=result.setdefault("gpu_settings",{})
    if not isinstance(overrides,dict):
        raise ValueError("gpu_settings inválido")
    entry=overrides.setdefault(identifier,{})
    if not isinstance(entry,dict):
        entry={'enabled':False}
        overrides[identifier]=entry
    if enabled is not None:
        if not isinstance(enabled,bool):
            raise ValueError("enabled precisa ser booleano")
        entry["enabled"]=enabled
    if change_wallet:
        if wallet_override not in (None,"") and not valid_wallet(wallet_override):
            raise ValueError("Carteira Pearl inválida")
        entry["wallet"]=wallet_override or None
    return result
