"""Diagnóstico por lista de campos permitidos e observação passiva de estabilidade.

Não importa config, não lê settings/chaves, não executa subprocessos e não envia rede.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import time

GPU_FIELDS = ("temperature.gpu", "power.draw", "power.limit", "clocks.gr", "clocks.mem", "fan.speed", "utilization.gpu", "memory.used", "memory.total")
MINER_FIELDS = ("hashrate", "hashrate_at", "accepted", "rejected", "hw_errors")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def obj(value):
    return value if isinstance(value, dict) else {}


def model_name(value):
    match = re.search(r"\b(?:NVIDIA )?(?:GeForce )?(?:RTX|GTX) \d{3,4}(?: Ti| SUPER)?(?: Laptop GPU)?\b", str(value))
    return match[0] if match else "NVIDIA (modelo não exportado)"


def sanitize_snapshot(payload, now):
    payload = obj(payload)
    rig = obj(payload.get("rig"))
    devices = rig.get("devices")
    if not isinstance(devices, list) or not devices:
        devices = [payload] if obj(payload.get("gpu")) else []
    safe = []
    for slot, device in enumerate(devices[:6]):
        device = obj(device)
        gpu, miner = obj(device.get("gpu")), obj(device.get("miner"))
        driver = gpu.get("driver_version")
        safe.append({"slot": slot, "model": model_name(gpu.get("name", device.get("name", ""))),
                     "driver": driver if isinstance(driver, str) and re.fullmatch(r"\d{2,4}\.\d{1,3}", driver) else None,
                     "enabled": device.get("enabled", True) is True,
                     "miner_running": device.get("miner_running") is True,
                     "paused": miner.get("paused") is True,
                     "sensor_at": number(gpu.get("sampled_at")),
                     "gpu": {key: number(gpu.get(key)) for key in GPU_FIELDS},
                     "miner": {key: number(miner.get(key)) for key in MINER_FIELDS}})
    return {"schema": 1, "observed_at": now, "snapshot_at": number(payload.get("collected_at")), "devices": safe}


def capture(state_dir, now=None):
    now = time.time() if now is None else now
    path = state_dir / "latest.json"
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Limite excedido")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Raiz inválida")
        result = sanitize_snapshot(payload, now)
        result["read_status"] = "ok"
        return result
    except (OSError, ValueError, RecursionError):
        # Never export exception messages, raw data, paths or malformed content.
        return {"schema": 1, "observed_at": now, "snapshot_at": None, "devices": [], "read_status": "unavailable"}


def summarize(rows, hours, interval):
    if not rows:
        return {"status": "incomplete", "samples": 0}
    rows = sorted(rows, key=lambda row: row["observed_at"])
    duration = max(0, rows[-1]["observed_at"] - rows[0]["observed_at"])
    covered = 0.0
    healthy = 0
    max_temp = None
    independent = set()
    gaps = 0
    for previous, row in zip(rows, rows[1:]):
        delta = row["observed_at"] - previous["observed_at"]
        if delta <= interval * 2:
            covered += delta
        else:
            gaps += 1
    for row in rows:
        at = row["observed_at"]
        stamp = row.get("snapshot_at")
        devices = [d for d in row.get("devices", []) if d.get("enabled")]
        valid = row.get("read_status") == "ok" and stamp is not None and -5 <= at-stamp <= 30 and bool(devices)
        for device in devices:
            gpu, miner = device["gpu"], device["miner"]
            sensor, rate_at = device.get("sensor_at"), miner.get("hashrate_at")
            temp = gpu.get("temperature.gpu")
            if temp is not None:
                max_temp = max(temp, max_temp if max_temp is not None else temp)
            fresh_rate = rate_at is not None and -5 <= at-rate_at <= 180
            if fresh_rate and miner.get("hashrate") is not None:
                independent.add((device["slot"], rate_at))
            valid = valid and device["miner_running"] and not device["paused"] and sensor is not None and -5 <= at-sensor <= 30 and fresh_rate and (miner.get("hashrate") or 0) > 0 and temp is not None and 0 <= temp < 84 and miner.get("hw_errors") == 0
        healthy += bool(valid)
    completion = duration >= hours*3600
    coverage = covered/duration if duration else 0
    revisions = sorted({row.get("operator_declared_revision") for row in rows if row.get("operator_declared_revision")})
    # An observation outcome is not certification or an A/B performance result.
    status = "completed_with_observations" if completion and coverage >= .99 and healthy == len(rows) and len(revisions) <= 1 else "needs_review" if completion else "incomplete"
    return {"schema": 1, "status": status, "samples": len(rows), "requested_hours": hours,
            "observed_seconds": duration, "coverage_ratio": coverage, "gaps_over_two_intervals": gaps,
            "healthy_samples": healthy, "independent_hashrate_samples": len(independent), "max_temperature_c": max_temp,
            "operator_declared_revisions": revisions,
            "limitation": "Observação passiva; não certifica hardware, desempenho ou segurança. Shares são contadores cumulativos por amostra, não somas."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("LOCALAPPDATA", ".")) / "PearlMonitor")
    parser.add_argument("--revision", help="Commit de 40 caracteres da aplicação observada; declaração do operador")
    sub = parser.add_subparsers(dest="action", required=True)
    diagnostic = sub.add_parser("diagnostic")
    diagnostic.add_argument("--output", type=Path, required=True)
    soak = sub.add_parser("observe")
    soak.add_argument("--output", type=Path, required=True)
    soak.add_argument("--hours", type=float, default=24)
    soak.add_argument("--interval", type=float, default=30)
    analyze = sub.add_parser("summarize")
    analyze.add_argument("input", type=Path)
    analyze.add_argument("--hours", type=float, default=24)
    analyze.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()
    if args.revision is not None and not re.fullmatch(r"[a-f0-9]{40}", args.revision):
        parser.error("Revisão precisa ser um commit de 40 caracteres")
    if args.action == "diagnostic":
        # Exclusive creation prevents overwriting history or another user file.
        with args.output.open("x", encoding="utf-8") as stream:
            result = capture(args.state_dir)
            result["operator_declared_revision"] = args.revision
            json.dump(result, stream, indent=2, allow_nan=False)
        print("Diagnóstico criado. Revise o arquivo antes de compartilhar.")
    elif args.action == "observe":
        if args.revision is None:
            parser.error("Informe --revision com o commit realmente observado")
        if not math.isfinite(args.hours) or not 0 < args.hours <= 168 or not math.isfinite(args.interval) or not 2 <= args.interval <= 300:
            parser.error("Duração deve estar entre 0 e 168 horas; intervalo entre 2 e 300 segundos")
        deadline = time.monotonic() + args.hours*3600
        with args.output.open("x", encoding="utf-8", buffering=1) as stream:
            while True:
                result = capture(args.state_dir)
                result["operator_declared_revision"] = args.revision
                stream.write(json.dumps(result, allow_nan=False) + "\n")
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(args.interval, remaining))
    else:
        if not math.isfinite(args.hours) or args.hours <= 0 or not math.isfinite(args.interval) or args.interval <= 0:
            parser.error("Duração e intervalo precisam ser positivos e finitos")
        rows = []
        with args.input.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict) or number(row.get("observed_at")) is None:
                        raise ValueError("Amostra inválida")
                    rows.append(row)
                except ValueError:
                    parser.error("Registro inválido; preserve o original para revisão local")
        print(json.dumps(summarize(rows, args.hours, args.interval), indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
