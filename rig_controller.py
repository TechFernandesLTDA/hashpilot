"""Snapshots isolados de um rig NVIDIA; não escreve clocks nem inicia processos."""
from __future__ import annotations
from pathlib import Path
import time
from contribution import atomic_json
from gpu_telemetry import read_device_payload,read_gpu_samples


def collect_snapshots(devices,entries,*,sample_reader=read_gpu_samples,now=None):
    stamp=time.time() if now is None else float(now)
    samples=sample_reader(devices,now=stamp)
    snapshots={}
    for device in devices:
        identifier=device["uuid"]
        entry=entries.get(identifier,{})
        payload=read_device_payload(device,entry.get("miner_log"),miner_running=entry.get("miner_running",False),now=stamp)
        snapshot={"collected_at":stamp,"gpu":samples.get(identifier,{}),"miner":payload["miner"],
            "miner_running":payload["miner_running"],"gpu_agent":{"gpu":dict(device)},
            "wallet":entry.get("wallet",device.get("wallet")),"pool":entry.get("pool","prl.kryptex.network:7048"),
            "log_error":payload["log_error"],"rig_managed":bool(entry.get("managed")),"gpu_uuid":identifier}
        snapshots[identifier]=snapshot
        atomic_json(Path(device["log_dir"])/"latest.json",snapshot)
    return snapshots
