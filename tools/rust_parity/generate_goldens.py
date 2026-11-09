"""Gera as respostas de referência (goldens) que o port em Rust precisa reproduzir.

Roda os módulos Python REAIS do HashPilot sobre entradas fixas e grava JSON em
rust/hashpilot-core/tests/golden/. Os testes de `cargo test` comparam o Rust com
esses arquivos: se o comportamento do Python mudar, regere e revise o diff.

Executa com TZ=UTC (o carimbo do log do SRBMiner é hora local; fixar o fuso torna
os goldens independentes da máquina) e com estado temporário isolado.

Uso (na raiz do repositório):
    python tools/rust_parity/generate_goldens.py
    python tools/rust_parity/generate_goldens.py history   # só um golden

Sem `time.tzset` (Windows) só o golden `history` pode ser gerado: ele não depende
do fuso nem de caminhos do sistema, e sai idêntico no Linux.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import itertools
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import types
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
# HASHPILOT_GOLDEN_OUT permite regerar num diretório temporário (test_rust_parity_goldens.py).
OUT = Path(os.environ.get("HASHPILOT_GOLDEN_OUT") or ROOT / "rust/hashpilot-core/tests/golden")
LOGS = ROOT / "portable-data/miner-logs"

os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()
if sys.platform != "win32":
    os.environ.setdefault("HASHPILOT_TEST_STATE_DIR", tempfile.mkdtemp(prefix="hashpilot-golden-"))
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import contribution  # noqa: E402
import gpu_agent  # noqa: E402
import gpu_telemetry  # noqa: E402
import miner_log  # noqa: E402
import payout  # noqa: E402
import rig_config  # noqa: E402
import thermal  # noqa: E402
import valuation  # noqa: E402

SCALARS = [
    None,
    True,
    False,
    0,
    1,
    -2,
    1.5,
    -0.25,
    1e-7,
    1e16,
    "1.5",
    " 2 ",
    "1e3",
    "-4",
    "+3",
    ".5",
    "5.",
    "inf",
    "-inf",
    "nan",
    "Infinity",
    "abc",
    "",
    "0x10",
    "1_000",
    "1__0",
    "_1",
    [],
    {},
]


def write(name, payload):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{name}: {len(payload['cases'])} casos")


def capture(fn):
    try:
        return {"ok": fn()}
    except (ValueError, RuntimeError) as error:
        return {"error": str(error)}


def numbers():
    cases = []
    for value in SCALARS:
        cases.append(
            {
                "input": value,
                "numeric": gpu_agent.numeric(value),
                "payout_number": payout.number(value),
                "valuation_numeric": valuation.numeric(value),
                "valuation_positive": valuation.numeric(value, positive=True),
            }
        )
    write("numbers", {"cases": cases})


def stamp(text):
    return miner_log.parse_stamp(text)


SYNTHETIC = [
    (
        "bracket_label_and_gh_units",
        0,
        True,
        "[2026-11-03 10:00:00] Miner version: 3.8.4\n[2026-11-03 10:00:01] Connected to prl.kryptex.network:7048 [0]\n"
        "[2026-11-03 10:00:30] GPU[0] RTX: 80.5 GH/s [A:3 R:1 HW:0]\n"
        "[2026-11-03 10:00:30] Average hashrate: 1m 81 GH/s | 15m 0.08 TH/s\n",
        "2026-11-03 10:01:00",
    ),
    (
        "shared_log_selects_index_1",
        1,
        False,
        "[2026-11-03 10:00:00] Miner version: 3.8.0\n[2026-11-03 10:00:10] GPU0 RTX 3070: 70.00 TH/s [T:60C A:5 R:0 HW:0]\n"
        "[2026-11-03 10:00:10] GPU1 RTX 3080: 90.00 TH/s [T:61C A:7 R:1 HW:2]\n[2026-11-03 10:00:11] GPU1[t0] share accepted\n"
        "[2026-11-03 10:00:11] GPU0[t0] share accepted\n[2026-11-03 10:00:12] Average hashrate: 1m 160 TH/s | 15m 150 TH/s\n"
        "[2026-11-03 10:00:12] Total: 160.00 TH/s [A:12 R:1 HW:2]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "index_absent_from_shared_log",
        2,
        True,
        "[2026-11-03 10:00:10] GPU0 X: 70.00 TH/s [A:5 R:0 HW:0]\n[2026-11-03 10:00:10] GPU1 Y: 90.00 TH/s [A:7 R:1 HW:2]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "isolated_gpu0_log_for_other_index",
        3,
        True,
        "[2026-11-03 10:00:10] GPU0 X: 70.00 TH/s [A:5 R:0 HW:0]\n[2026-11-03 10:00:12] GPU0[t0] share accepted\n",
        "2026-11-03 10:01:00",
    ),
    (
        "future_line_is_skipped_and_reported",
        0,
        True,
        "[2026-11-03 10:00:10] GPU0 X: 70.00 TH/s [A:5 R:0 HW:0]\n[2026-11-03 10:05:00] GPU0 X: 99.00 TH/s [A:9 R:0 HW:0]\n"
        "[2026-11-03 10:04:00] GPU0 X: 98.00 TH/s [A:8 R:0 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "restart_resets_state",
        0,
        True,
        "[2026-11-03 09:00:00] Miner version: 3.8.3\n[2026-11-03 09:00:10] GPU0 X: 60.00 TH/s [A:50 R:2 HW:1]\n"
        "[2026-11-03 09:00:20] GPU0 turned off\n[2026-11-03 10:00:00] Miner version: 3.8.4\n"
        "[2026-11-03 10:00:30] Job received [job]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "pause_toggle_and_reconnect",
        0,
        True,
        "[2026-11-03 10:00:00] Connected to pool\n[2026-11-03 10:00:05] Reconnecting to pool\n"
        "[2026-11-03 10:00:06] GPU0 turned off\n[2026-11-03 10:00:20] GPU0 turned on\n"
        "[2026-11-03 10:00:30] GPU0 turned off\n[2026-11-03 10:00:40] Connection lost\n",
        "2026-11-03 10:01:00",
    ),
    (
        "out_of_order_rate_keeps_newest",
        0,
        True,
        "[2026-11-03 10:00:30] GPU0 X: 75.00 TH/s [A:9 R:0 HW:0]\n[2026-11-03 10:00:20] GPU0 X: 74.00 TH/s [A:8 R:0 HW:0]\n"
        "[2026-11-03 10:00:30] GPU0 X: 76.00 TH/s [A:10 R:0 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "non_integer_and_negative_counters",
        0,
        True,
        "[2026-11-03 10:00:30] GPU0 X: 75.00 TH/s [A:1.5 R:2 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "invalid_and_missing_stamps",
        0,
        True,
        "[2026-99-03 10:00:30] GPU0 X: 75.00 TH/s [A:1 R:0 HW:0]\nGPU0 X: 74.00 TH/s [A:2 R:0 HW:0]\n"
        "[2026-11-03 10:00:31] GPU0 X: 73.00 TH/s [A:3 R:0 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "crlf_and_form_feed_line_breaks",
        0,
        True,
        "[2026-11-03 10:00:30] GPU0 X: 75.00 TH/s [A:1 R:0 HW:0]\r\n[2026-11-03 10:00:31] GPU0[t0] share accepted\x0c"
        "[2026-11-03 10:00:32] Average hashrate: 1m 75 TH/s | 15m 70 TH/s\r",
        "2026-11-03 10:01:00",
    ),
    (
        "unknown_unit_is_missing_not_zero",
        0,
        True,
        "[2026-11-03 10:00:30] GPU0 X: 75.00 ZH/s [A:1 R:0 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
    (
        "old_points_leave_the_900s_window",
        0,
        True,
        "[2026-11-03 09:30:00] GPU0 X: 70.00 TH/s [A:1 R:0 HW:0]\n[2026-11-03 09:50:00] GPU0 X: 71.00 TH/s [A:2 R:0 HW:0]\n"
        "[2026-11-03 10:00:00] GPU0 X: 72.00 TH/s [A:3 R:0 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
    ("empty_log", 0, True, "", "2026-11-03 10:01:00"),
    # Bordas da janela de linha futura (now + 5 s): exatamente no limite entra; 1 s e 20 s além, não.
    (
        "future_window_boundaries",
        0,
        True,
        "[2026-11-03 10:01:05] GPU0 X: 70.00 TH/s [A:1 R:0 HW:0]\n[2026-11-03 10:01:06] GPU0 X: 71.00 TH/s [A:2 R:0 HW:0]\n"
        "[2026-11-03 10:01:20] GPU0 X: 72.00 TH/s [A:3 R:0 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
    # Borda da janela de 900 s dos pontos do gráfico.
    (
        "chart_window_boundary",
        0,
        True,
        "[2026-11-03 09:46:00] GPU0 X: 70.00 TH/s [A:1 R:0 HW:0]\n[2026-11-03 09:45:59] GPU0 X: 69.00 TH/s [A:1 R:0 HW:0]\n",
        "2026-11-03 10:01:00",
    ),
]


def parsed(text, index, allow, now):
    result, future = gpu_telemetry._parse_gpu_log(text, "GPU-test", index, now, allow)
    result = dict(result)
    result["rate_points"] = [list(point) for point in result["rate_points"]]
    return {"result": result, "future": future}


def miner_logs():
    cases = []
    for path in sorted(LOGS.glob("*.log")):
        text = path.read_text(encoding="utf-8-sig")
        stamps = [s for s in (miner_log.log_timestamp(line) for line in text.splitlines()) if s is not None]
        now = (max(stamps) + 1) if stamps else 0.0
        cases.append(
            {
                "name": f"real:{path.name}",
                "fixture": f"portable-data/miner-logs/{path.name}",
                "index": 0,
                "allow_local_gpu0": True,
                "now": now,
                **parsed(text, 0, True, now),
            }
        )
    for name, index, allow, text, now_text in SYNTHETIC:
        now = stamp(now_text)
        cases.append(
            {
                "name": name,
                "text": text,
                "index": index,
                "allow_local_gpu0": allow,
                "now": now,
                **parsed(text, index, allow, now),
            }
        )
    stamps = []
    for text in [
        "2026-11-03 10:00:00",
        "2026-99-25 00:00:00",
        "2026-02-29 00:00:00",
        "2028-02-29 23:59:59",
        "1970-01-01 00:00:00",
        "2026-11-03 24:00:00",
    ]:
        stamps.append({"text": text, "epoch_utc": miner_log.parse_stamp(text)})
    write(
        "miner_log",
        {
            "cases": cases,
            "stamps": stamps,
            "device_map": [
                capture(lambda: miner_log.parse_miner_device_map(t, d)) | {"text": t, "devices": d}
                for t, d in DEVICE_MAPS
            ],
        },
    )


DEVICE_MAPS = [
    ("[2026] GPU0: nvidia_geforce_rtx_3070 [BUS: 06]", [{"uuid": "GPU-a", "pci_bus_id": "00000000:06:00.0"}]),
    (
        "GPU3: nvidia_geforce_rtx_3070 [BUS: 06]\nGPU1: nvidia_geforce_rtx_3070 [BUS: 0A]",
        [{"uuid": "GPU-a", "pci_bus_id": "00000000:0A:00.0"}, {"uuid": "GPU-b", "pci_bus_id": "00000000:06:00.0"}],
    ),
    ("GPU0: amd_radeon [BUS: 06]", [{"uuid": "GPU-a", "pci_bus_id": "00000000:06:00.0"}]),
    ("GPU0: nvidia_x [BUS: 06]\nGPU1: nvidia_y [BUS: 06]", [{"uuid": "GPU-a", "pci_bus_id": "00000000:06:00.0"}]),
    ("GPU0: nvidia_x [BUS: 06]", [{"uuid": "GPU-a", "pci_bus_id": "sem-pci"}]),
]


def thermal_sequences():
    def run(name, policy_args, steps):
        try:
            policy = thermal.ThermalPolicy(**policy_args)
        except ValueError as error:
            return {"name": name, "policy": policy_args, "constructor_error": str(error), "steps": []}
        out = []
        for temp, now in steps:
            try:
                decision = policy.step(temp, now)
                out.append(
                    {
                        "temp": temp,
                        "now": now,
                        "clock_mhz": decision.clock_mhz,
                        "reason": decision.reason,
                        "mode": decision.mode,
                    }
                )
            except ValueError as error:
                out.append({"temp": temp, "now": now, "error": str(error)})
        return {"name": name, "policy": policy_args, "steps": out}

    cases = [
        run("recovery_after_60s_cold", {}, [(74, t) for t in range(0, 101, 2)]),
        run(
            "cooling_bands_with_cooldown",
            {},
            [(78, 0), (78, 2), (78, 6), (80, 8), (80, 10), (80, 12), (79, 14), (81, 20)],
        ),
        run("fast_heating_anticipates", {}, [(70, 0), (72, 2), (74.5, 4), (77.5, 6), (77.9, 8), (76, 10)]),
        run("emergency_overrides_cooldown", {}, [(80, 0), (83, 1), (85, 2), (83, 2.5), (60, 3)]),
        run(
            "limits_reached",
            {"min_clock": 1500, "max_clock": 1500, "initial_clock": 1500},
            [(81, 0), (81, 10), (70, 20), (70, 100), (70, 130)],
        ),
        run("initial_clock_is_clamped", {"min_clock": 1600, "max_clock": 1700, "initial_clock": 1800}, [(70, 0)]),
        run("invalid_inputs", {}, [(151, 0), (-1, 0), (70, -1), (70, 10), (70, 5), (70, 10)]),
        run("constructor_min_gt_max", {"min_clock": 1800, "max_clock": 1700}, []),
        run("constructor_non_positive", {"min_clock": 0}, []),
        run("fractional_times_round_half_even", {}, [(78, 0), (78, 1.5), (78, 3.5), (74, 10), (74, 40.5), (74, 70.5)]),
        # Inclinação exatamente 0,4 °C/s a 77 °C: o limiar é inclusivo (>= .4).
        run("anticipation_slope_boundary", {}, [(75, 0), (77, 5), (60, 10), (76.6, 11), (77.0, 12)]),
        # Exatamente nos limites de faixa: 75 recupera, 78/80/83 mudam de modo.
        run("band_edges", {}, [(75, 0), (75, 60), (78, 61), (80, 70), (83, 80)]),
    ]
    write(
        "thermal",
        {
            "cases": cases,
            "constants": {
                k: getattr(thermal, k)
                for k in (
                    "EMERGENCY_C",
                    "FAST_COOLING_C",
                    "COOLING_C",
                    "ANTICIPATE_C",
                    "RECOVERY_C",
                    "MINER_OFF_TEMPERATURE_C",
                )
            },
        },
    )


def valuations():
    cases = []
    for prl in [None, 0, 2.5, "3", -1, True, "inf"]:
        for prices in [
            (None, None, None, None),
            (0.5, 0.1, None, None),
            (0.5, 0.1, 60000, None),
            (0.5, None, None, 300000),
            ("0.5", "x", 60000, 300000),
            (0, 0, 0, 0),
            (-1, 0.1, 60000, 300000),
        ]:
            cases.append({"prl": prl, "prices": list(prices), "result": valuation.value_in_currencies(prl, *prices)})
    write("valuation", {"cases": cases})


NOW = 1_800_000_000.0


def payout_states():
    def balance(**data):
        return {"fetched_at": NOW - 10, "data": data}

    network = {"data": {"minpay": 2, "blocks_for_maturation": 100, "block_time": 60}}
    snapshots = [
        {"timestamp": NOW - 2400, "total_prl": 1.0},
        {"timestamp": NOW - 300, "total_prl": 1.35},
        {"timestamp": NOW + 60, "total_prl": 9.0},
        {"timestamp": NOW - 7200, "total_prl": 0.1},
        "lixo",
    ]
    payments = {
        "data": {
            "results": [
                {"date": NOW - 9000, "received": 1.2, "txid": "abc", "status": "paid"},
                {"date": NOW - 5000, "received": "0.8", "txid": 12345, "status": None},
                {"date": NOW - 5000, "received": 9, "txid": "tie-later", "status": "paid"},
                {"date": NOW + 999, "received": 5, "txid": "future"},
                "lixo",
                {"date": "x"},
            ]
        }
    }
    states = [
        ("empty_state", {}, None, None),
        (
            "stale_without_fetched_at",
            {"sources": {"balance": {"data": {"threshold": 1, "total": 0.2, "confirmed": 0.1}}}},
            None,
            0.5,
        ),
        (
            "observed_rate_with_maturation",
            {
                "snapshots": snapshots,
                "sources": {
                    "balance": balance(threshold=2, total=1.35, confirmed=1.0, unconfirmed=0.35, maturation_time=5400),
                    "network": network,
                    "payout_history": payments,
                },
            },
            None,
            0.5,
        ),
        (
            "observed_rate_maturation_from_network",
            {
                "snapshots": snapshots,
                "sources": {"balance": balance(threshold=2, total=1.35, confirmed=1.0), "network": network},
            },
            None,
            "0.5",
        ),
        (
            "forecast_rate_without_any_maturation",
            {"sources": {"balance": balance(threshold=2, total=1.0, confirmed=1.0)}},
            {"prl_day": 0.5},
            None,
        ),
        (
            "forecast_zero_is_no_rate",
            {"sources": {"balance": balance(threshold=2, total=1.0, confirmed=1.0)}},
            {"prl_day": 0},
            None,
        ),
        (
            "confirmed_reached",
            {"sources": {"balance": balance(threshold=2, total=2.5, confirmed=2.0), "network": network}},
            None,
            0.5,
        ),
        (
            "generated_waiting_confirmation",
            {"sources": {"balance": balance(threshold=2, total=2.1, confirmed=1.5, maturation_time=3000)}},
            None,
            0.5,
        ),
        (
            "generated_without_maturation",
            {"sources": {"balance": balance(threshold=2, total=2.1, confirmed=1.5)}},
            None,
            None,
        ),
        (
            "threshold_zero_uses_minpay",
            {"sources": {"balance": balance(threshold=0, total=0.5, confirmed=0.5), "network": network}},
            None,
            None,
        ),
        (
            "balance_error_marks_stale",
            {"errors": {"balance": "timeout"}, "sources": {"balance": balance(threshold=2, total=1, confirmed=1)}},
            None,
            None,
        ),
        ("no_rate_no_forecast", {"sources": {"balance": balance(threshold=2, total=1.0, confirmed=0.9)}}, None, None),
        (
            "slow_rate_beyond_one_year",
            {"sources": {"balance": balance(threshold=2000, total=1.0, confirmed=1.0, maturation_time=60)}},
            {"prl_day": 0.001},
            None,
        ),
        (
            "future_fetch_marks_stale",
            {"sources": {"balance": {"fetched_at": NOW + 60, "data": {"threshold": 2, "total": 1, "confirmed": 1}}}},
            None,
            None,
        ),
        (
            "string_and_bool_values",
            {"sources": {"balance": balance(threshold="2", total="1.5", confirmed=True, unconfirmed="x")}},
            {"prl_day": "0.25"},
            "abc",
        ),
    ]
    cases = [
        {
            "name": name,
            "state": state,
            "forecast": forecast,
            "price_brl": price,
            "result": payout.payout_model(state, forecast, price, now=NOW),
        }
        for name, state, forecast, price in states
    ]
    # Estado financeiro público e autorizado do autor (portable-data/), avaliado logo após a coleta.
    cache = json.loads((ROOT / "portable-data/finance-cache.json").read_text(encoding="utf-8-sig"))
    latest = json.loads((ROOT / "portable-data/finance-latest.json").read_text(encoding="utf-8-sig"))
    fetched = payout.number(cache.get("sources", {}).get("balance", {}).get("fetched_at"))
    for label, moment in (("fresh", (fetched or NOW) + 30), ("stale", (fetched or NOW) + 3600)):
        cases.append(
            {
                "name": f"real:{label}",
                "fixture": "portable-data/finance-cache.json",
                "now": moment,
                "forecast": latest.get("forecast"),
                "price_brl": latest.get("price_brl"),
                "result": payout.payout_model(cache, latest.get("forecast"), latest.get("price_brl"), now=moment),
            }
        )
    durations = [
        {"input": v, "result": payout.duration(v)}
        for v in [
            None,
            -5,
            0,
            1,
            59,
            60,
            61,
            3599,
            3600,
            3661,
            86399,
            86400,
            86400 * 365 + 59,
            86400 * 366,
            "120",
            True,
            1e9,
        ]
    ]
    write("payout", {"cases": cases, "durations": durations, "now": NOW})


W = "prl1" + "q" * 40
W2 = "prl1" + "p" * 40


def gpu(index, uuid, **extra):
    return {"vendor": "NVIDIA", "index": index, "uuid": uuid, "name": f"NVIDIA GeForce RTX 30{70 + index}", **extra}


def rig():
    root = Path(tempfile.mkdtemp(prefix="hashpilot-rig-")).resolve()

    def scrub(value):
        text = json.dumps(value, ensure_ascii=False)
        return json.loads(text.replace(str(root), "<ROOT>"))

    six = [gpu(i, f"GPU-{i:08d}-aaaa") for i in range(7)]
    cases = [
        ("single_default", [gpu(0, "GPU-abc", pci_bus_id="00000000:06:00.0", memory_total_mb=8192)], {}),
        ("seven_detected_keeps_six_sorted", list(reversed(six)), {"wallet": W}),
        (
            "duplicates_and_non_nvidia",
            [gpu(1, "GPU-x"), gpu(0, "GPU-x"), {"vendor": "AMD", "index": 0, "uuid": "GPU-amd"}, "lixo"],
            {},
        ),
        (
            "overrides",
            [gpu(i, f"GPU-o{i}") for i in range(6)],
            {
                "wallet": W,
                "gpu_settings": {
                    "GPU-o0": {"enabled": False},
                    "GPU-o1": {"enabled": "sim"},
                    "GPU-o2": {"wallet": W2},
                    "GPU-o3": {"wallet": ""},
                    "GPU-o4": {"wallet": "bc1xyz", "profile": "cool"},
                    "GPU-o5": "lixo",
                },
            },
        ),
        (
            "profiles",
            [gpu(0, "GPU-p0"), gpu(1, "GPU-p1"), gpu(2, "GPU-p2")],
            {
                "gpu_settings": {
                    "GPU-p0": {"profile": "balanced"},
                    "GPU-p1": {"profile": "turbo"},
                    "GPU-p2": {"profile": "cool", "enabled": True},
                }
            },
        ),
        (
            "author_wallet_flags",
            [gpu(0, "GPU-w0"), gpu(1, "GPU-w1")],
            {"wallet": contribution.AUTHOR_WALLET, "gpu_settings": {"GPU-w1": {"wallet": W}}},
        ),
        ("settings_not_dict", [gpu(0, "GPU-a")], ["x"]),
        ("invalid_global_wallet", [gpu(0, "GPU-a")], {"wallet": "bc1xyz"}),
        ("gpu_settings_not_dict", [gpu(0, "GPU-a")], {"gpu_settings": []}),
        ("bool_index", [{"vendor": "NVIDIA", "index": True, "uuid": "GPU-a"}], {}),
        ("negative_index", [gpu(-1, "GPU-a")], {}),
        ("invalid_uuid", [gpu(0, "gpu-lower")], {}),
        ("uuid_too_long", [gpu(0, "GPU-" + "a" * 101)], {}),
    ]
    resolve = [
        {
            "name": n,
            "detected": d,
            "settings": s,
            **scrub(capture(lambda d=d, s=s: rig_config.resolve_devices(d, s, root))),
        }
        for n, d, s in cases
    ]
    updates_in = [
        ("enable_new", {"wallet": W}, "GPU-a", {"enabled": True}),
        ("disable_existing", {"gpu_settings": {"GPU-a": {"enabled": True, "wallet": W2}}}, "GPU-a", {"enabled": False}),
        ("set_wallet", {}, "GPU-a", {"wallet_override": W2, "change_wallet": True}),
        (
            "clear_wallet",
            {"gpu_settings": {"GPU-a": {"wallet": W2}}},
            "GPU-a",
            {"wallet_override": "", "change_wallet": True},
        ),
        ("wallet_ignored_without_change_flag", {}, "GPU-a", {"wallet_override": W2}),
        ("invalid_wallet", {}, "GPU-a", {"wallet_override": "bc1", "change_wallet": True}),
        ("enabled_not_bool", {}, "GPU-a", {"enabled": "sim"}),
        ("entry_not_dict_is_replaced", {"gpu_settings": {"GPU-a": "lixo"}}, "GPU-a", {}),
        ("gpu_settings_not_dict", {"gpu_settings": 3}, "GPU-a", {}),
        ("settings_not_dict", [1], "GPU-a", {}),
        ("invalid_uuid", {}, "x", {}),
    ]
    updates = [
        {
            "name": n,
            "settings": s,
            "gpu_uuid": u,
            "kwargs": k,
            **capture(lambda s=s, u=u, k=k: rig_config.update_gpu_settings(s, u, **k)),
        }
        for n, s, u, k in updates_in
    ]
    wallets = [
        {"input": v, "valid": contribution.valid_wallet(v)}
        for v in [
            W,
            W2,
            contribution.AUTHOR_WALLET,
            "prl1" + "q" * 29,
            "prl1" + "q" * 30,
            "prl1" + "q" * 100,
            "prl1" + "q" * 101,
            "prl1" + "b" * 40,
            "prl1" + "i" * 40,
            "prl1" + "o" * 40,
            "prl1" + "1" * 40,
            "PRL1" + "q" * 40,
            " " + W,
            5,
            None,
        ]
    ]
    support = [
        {"settings": s, "wallet": w, "result": contribution.support_configuration(s, w)}
        for s, w in [
            ({"support_enabled": True, "wallet": W}, W),
            ({"support_enabled": True}, W),
            ({"support_enabled": "true", "wallet": W}, W),
            ({"support_enabled": True, "wallet": W2}, W),
            ({"support_enabled": False, "wallet": W}, W),
            ({"support_enabled": True, "wallet": contribution.AUTHOR_WALLET}, contribution.AUTHOR_WALLET),
            ({"support_enabled": True, "wallet": W}, "bc1"),
            (None, W),
            (["x"], W),
        ]
    ]
    write(
        "rig_config",
        {"cases": resolve, "updates": updates, "wallets": wallets, "support": support, "max_gpus": rig_config.MAX_GPUS},
    )


def configs():
    env = {"HP_GOLDEN": "/x/y", "HP_OTHER": "z"}
    cases = []
    for platform, environ in [
        ("win32", {"LOCALAPPDATA": "C:/Users/u/AppData/Local", config.TEST_STATE_ENV: "/tmp/x"}),
        ("linux", {config.TEST_STATE_ENV: "/tmp/x"}),
        ("linux", {}),
        ("darwin", {"LOCALAPPDATA": "/a"}),
    ]:
        cases.append(
            {
                "platform": platform,
                "environ": environ,
                **capture(lambda p=platform, e=environ: str(config.resolve_state_dir(p, e))),
            }
        )
    expansions = []
    old = dict(os.environ)
    os.environ.update(env)
    try:
        for log in [
            "",
            None,
            "/plain/logs",
            "$HP_GOLDEN/logs",
            "${HP_GOLDEN}/a",
            "$HP_MISSING/b",
            "${HP_MISSING}/c",
            "$HP_GOLDEN$HP_OTHER",
            "pre-$/x",
            "${unterminated",
            "$$HP_GOLDEN",
            "a$",
        ]:
            settings = {} if log is None else {"log_dir": log}
            expansions.append(
                {"settings": settings, "result": str(config.log_dir(Path("/s/Local/PearlMonitor"), settings))}
            )
        import ntpath
        import posixpath

        inputs = [
            "plain",
            "%HP_GOLDEN%\\logs",
            "%HP_MISSING%\\x",
            "%%literal",
            "'%HP_GOLDEN%' fica",
            "'aberta %HP_GOLDEN%",
            "$HP_GOLDEN\\a",
            "${HP_GOLDEN}\\b",
            "${HP_MISSING}\\c",
            "$$x",
            "%aberto",
            "${aberto",
            "$",
            "a$b-c$",
            "$HP_GOLDEN-sufixo",
            "x%HP_GOLDEN%y%HP_OTHER%z",
        ]
        nt = [{"input": text, "result": ntpath.expandvars(text)} for text in inputs]
        posix = [
            {"input": text, "result": posixpath.expandvars(text)}
            for text in inputs + ["$HP_GOLDEN$HP_OTHER", "${}", "$1x"]
        ]
    finally:
        os.environ.clear()
        os.environ.update(old)
    write("config", {"cases": cases, "log_dir": expansions, "env": env, "expandvars_nt": nt, "expandvars_posix": posix})


# --- Histórico SQLite (history.HistoryStore) ---------------------------------------
#
# Cada cenário roda o HistoryStore REAL numa pasta temporária (cwd, com nomes de
# arquivo sem pasta, para as chaves SHA-256 não dependerem do sistema) e despeja o
# banco inteiro: SQL do esquema, linhas, user_version, offsets e rejeitadas. O
# uuid4, o relógio e o fuso do carimbo do log são fixos; nada mais é simulado.

HISTORY_NOW = 1794100000.25
HISTORY_WALLET = "prl1fixture"


def history_utc_stamp(text):
    """`miner_log.parse_stamp` sob TZ=UTC, sem `time.tzset` (o golden sai igual no Windows)."""
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


def history_apply(history, store, op):
    kind = op["op"]
    try:
        if kind == "telemetry":
            store.telemetry(op["data"], op.get("source", "live"))
        elif kind == "rig_telemetry":
            store.rig_telemetry(op["rig"])
        elif kind == "event":
            store.event(op["timestamp"], op["kind"], op["data"], op.get("key"), wallet=op.get("wallet"))
        elif kind == "finance":
            store.finance(op["state"])
        elif kind == "preferences":
            store.preferences(op["data"])
        elif kind == "write_file":
            data = bytes.fromhex(op["hex"]) if "hex" in op else op["text"].encode("utf-8")
            with open(op["path"], "ab" if op.get("append") else "wb") as handle:
                handle.write(data)
        elif kind == "archive_log":
            store.archive_log(Path(op["path"]), gpu_uuid=op.get("gpu_uuid"), wallet=op.get("wallet"))
        elif kind == "archive_thermal":
            store.archive_thermal(Path(op["path"]))
        elif kind == "reopen":
            store.flush()
            store.close()
            return history.HistoryStore(Path("history.sqlite3"), op.get("wallet", HISTORY_WALLET))
        elif kind == "last_error":
            store.flush()
            return {"last_error": store.last_error}
        elif kind == "read":
            store.flush()
            return {"read": op["call"], "result": getattr(store, op["call"])(*op.get("args", []))}
        else:
            raise AssertionError(f"operação desconhecida: {kind}")
    # `stats()` deixa o sqlite3.Error subir (ex.: banco recusado pela versão do esquema).
    except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
        return {"raised": str(error)}
    return None


def history_dump(database):
    with contextlib.closing(sqlite3.connect(database)) as connection:
        schema = [
            list(row)
            for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")
        ]
        tables = {}
        for (table,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall():
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            rows = connection.execute(f"SELECT * FROM {table} ORDER BY {','.join(columns)}").fetchall()
            tables[table] = {"columns": columns, "rows": [list(row) for row in rows]}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
    offsets, rejected = Path("history-log-offsets.json"), Path("history-rejected.jsonl")
    return {
        "schema": schema,
        "tables": tables,
        "user_version": version,
        "journal_mode": journal,
        "offsets": offsets.read_text(encoding="utf-8") if offsets.exists() else None,
        "rejected": rejected.read_text(encoding="utf-8").splitlines() if rejected.exists() else [],
    }


def history_case(name, ops, prepare=()):
    import history

    counter = itertools.count(1)
    fake_uuid = types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex=f"{next(counter):032x}"))
    fake_time = types.SimpleNamespace(time=lambda: HISTORY_NOW, monotonic=time.monotonic, sleep=time.sleep)
    outputs = []
    previous = os.getcwd()
    with (
        tempfile.TemporaryDirectory(prefix="hashpilot-history-") as folder,
        mock.patch.object(history, "uuid", fake_uuid),
        mock.patch.object(history, "time", fake_time),
        mock.patch.object(history, "parse_stamp", history_utc_stamp),
    ):
        os.chdir(folder)
        try:
            database = Path("history.sqlite3")
            if prepare:
                with contextlib.closing(sqlite3.connect(database)) as connection:
                    for statement in prepare:
                        connection.execute(statement)
                    connection.commit()
            store = history.HistoryStore(database, HISTORY_WALLET)
            for op in ops:
                result = history_apply(history, store, op)
                if isinstance(result, history.HistoryStore):
                    store = result
                elif result is not None:
                    outputs.append(result)
            store.flush()
            store.close()
            dump = history_dump(database)
            dump["last_error"] = store.last_error
        finally:
            os.chdir(previous)
    return {"name": name, "prepare": list(prepare), "ops": ops, "outputs": outputs, **dump}


def history_reads():
    return [
        {"op": "read", "call": "restore", "args": [1794100011.0]},
        {"op": "read", "call": "restore", "args": [0]},
        {"op": "read", "call": "restore_gpu", "args": ["GPU-1111", 0, "prl1gpu"]},
        {"op": "read", "call": "restore_gpu", "args": ["GPU-2222", 0, HISTORY_WALLET]},
        {"op": "read", "call": "restore_gpu", "args": ["GPU-1111", 0, ""]},
        {"op": "read", "call": "gpu_stats", "args": []},
        {"op": "read", "call": "gpu_stats", "args": ["GPU-2222"]},
        {"op": "read", "call": "finance_state", "args": [0]},
        {"op": "read", "call": "finance_state", "args": [1794100030.0, "prl1other"]},
        {"op": "read", "call": "stats", "args": []},
    ]


def history_basic_ops():
    device = {
        "uuid": "GPU-1111",
        "index": 0,
        "name": "RTX 3070",
        "identity": {"vendor": "NVIDIA", "extra": 1},
        "collected_at": 1794100019.5,
        "gpu": {"temperature.gpu": 76, "power.draw": 167.5, "fan.speed": 94},
        "miner": {"hashrate": 78.37, "hashrate_at": 1794100019.0, "recent_logs": "x", "rate_points": []},
        "miner_running": True,
        "wallet": "prl1gpu",
        "log_path": "gpus/GPU-1111/mining.log",
        "driver_version": "621.14",
        "memory_mib": 8192,
        "serial": None,
    }
    disabled = {
        "uuid": "GPU-2222",
        "index": 1,
        "enabled": False,
        "gpu": None,
        "miner": None,
        "configuration_error": "sem carteira",
        "gpu_error": "",
    }
    return [
        {
            "op": "telemetry",
            "data": {
                "collected_at": 1794100010.5,
                "gpu": {"temperature.gpu": 70, "power.draw": 150.5, "fan.speed": 80, "name": "RTX ação"},
                "miner": {
                    "hashrate": 78.3,
                    "hashrate_at": 1794100005,
                    "paused": False,
                    "connection": "ok",
                    "recent_logs": "[2026-11-08 20:00:00] linha um\nlinha dois\r\n",
                    "rate_points": [[1, 2]],
                },
                "miner_running": True,
                "log_path": "logs/mining-1.log",
            },
        },
        {
            "op": "telemetry",
            "data": {
                "collected_at": 1794100012,
                "gpu": {"temperature.gpu": 71},
                "miner": {"hashrate": 78.4, "hashrate_at": 1794100011.5, "paused": False, "connection": "ok"},
                "miner_running": True,
                "log_path": "logs/mining-1.log",
            },
        },
        {
            "op": "telemetry",
            "data": {
                "collected_at": 1794100014.25,
                "gpu": None,
                "miner": {"hashrate": None, "paused": True},
                "miner_running": False,
                "gpu_error": "nvidia-smi falhou",
                "process_error": "",
                "log_path": "logs/mining-1.log",
            },
        },
        {
            "op": "telemetry",
            "source": "import",
            "data": {
                "collected_at": 1794099000.0,
                "gpu": {"temperature.gpu": 60},
                "miner": {"hashrate": 70.0, "hashrate_at": 1794098990.0},
                "miner_running": True,
            },
        },
        {
            "op": "telemetry",
            "data": {
                "wallet": "prl1other",
                "collected_at": 1794100016.0,
                "miner": {"connection": "ok", "paused": False},
                "miner_running": True,
                "log_path": "logs/mining-1.log",
            },
        },
        {
            "op": "telemetry",
            "data": {
                "collected_at": 1794100010.5,
                "miner": {"paused": False, "connection": "ok"},
                "miner_running": True,
                "log_path": "logs/mining-1.log",
            },
        },
        {"op": "rig_telemetry", "rig": {"collected_at": 1794100020.0, "devices": [device, disabled]}},
        {
            "op": "rig_telemetry",
            "rig": {
                "collected_at": 1794100030.0,
                "devices": [
                    {
                        "uuid": "GPU-1111",
                        "index": 0,
                        "name": "RTX 3070 (2)",
                        "gpu": {"temperature.gpu": 77},
                        "miner": {"hashrate": 79.0, "hashrate_at": 1794099800.0},
                        "wallet": "prl1gpu",
                    }
                ],
            },
        },
        # Bordas da janela de 180 s do restore_gpu: exatamente 180 entra; 180,25 e idade negativa não.
        {
            "op": "rig_telemetry",
            "rig": {
                "devices": [
                    {
                        "uuid": "GPU-1111",
                        "collected_at": 1794100040.0,
                        "wallet": "prl1gpu",
                        "miner": {"hashrate": 77.5, "hashrate_at": 1794099860.0},
                    }
                ]
            },
        },
        {
            "op": "rig_telemetry",
            "rig": {
                "devices": [
                    {
                        "uuid": "GPU-1111",
                        "collected_at": 1794100041.0,
                        "wallet": "prl1gpu",
                        "miner": {"hashrate": 77.6, "hashrate_at": 1794099860.75},
                    }
                ]
            },
        },
        {
            "op": "rig_telemetry",
            "rig": {
                "devices": [
                    {
                        "uuid": "GPU-1111",
                        "collected_at": 1794100042.0,
                        "wallet": "prl1gpu",
                        "miner": {"hashrate": 77.7, "hashrate_at": 1794100043.0},
                    }
                ]
            },
        },
        # Carteira vazia ("") é falsa no Python: cai na carteira do histórico.
        {
            "op": "rig_telemetry",
            "rig": {"devices": [{"uuid": "GPU-2222", "collected_at": 1794100043.0, "wallet": "", "enabled": True}]},
        },
        {
            "op": "telemetry",
            "data": {
                "wallet": "",
                "collected_at": 1794100018.0,
                "miner": {"paused": False, "connection": "ok"},
                "miner_running": True,
                "log_path": "logs/mining-1.log",
            },
        },
        {"op": "rig_telemetry", "rig": {"devices": [{"uuid": f"GPU-{n}"} for n in range(7)]}},
        {"op": "rig_telemetry", "rig": {"devices": [{"uuid": "GPU-1111"}, {"uuid": "GPU-1111"}]}},
        {"op": "event", "timestamp": 1794100040.0, "kind": "manual", "data": {"a": 1}, "key": "k-1"},
        {
            "op": "event",
            "timestamp": 1794100041.5,
            "kind": "sem_chave",
            "data": {
                "numeros": [1e16, 1e-7, -0.0, 0.1, 12345678901234567890, 2.5e-5, 1e22, 100.0],
                "texto": "ação   \u0001 \"aspas\" \\ / \t fim",
                "vazio": {},
                "lista": [],
            },
        },
        {"op": "event", "timestamp": 1794100042, "kind": "outra", "data": None, "key": "", "wallet": "prl1other"},
        {
            "op": "finance",
            "state": {
                "snapshots": [{"timestamp": 1794100000.0, "total_prl": 1.5}, {"timestamp": 1794100060, "total_prl": 2}],
                "sources": {
                    "kryptex": {"fetched_at": 1794100050.0, "data": {"balance": 2.0}},
                    "coingecko": {"fetched_at": 1794100055.5, "data": {"brl": 0.42}},
                },
                "errors": {"coingecko": "timeout"},
            },
        },
        {
            "op": "finance",
            "state": {
                "snapshots": [
                    {"timestamp": 1794100060, "total_prl": 9},
                    {"timestamp": 1794100065.0, "total_prl": 2.25},
                ],
                "sources": {"kryptex": {"fetched_at": 1794100070.0, "data": {"balance": 2.5}}},
                "errors": {},
                "attempted_at": 1794100071,
            },
        },
        {
            "op": "finance",
            "state": {
                "wallet": "prl1other",
                "snapshots": [{"timestamp": 1794100066.0, "total_prl": 0.5}],
                "sources": {},
                "errors": {"kryptex": "HTTP 503"},
                "attempted_at": 1794100072,
            },
        },
        {"op": "preferences", "data": {"range": "1h", "zoom": [1, 2]}},
        {"op": "preferences", "data": {"range": "24h"}},
        *history_reads(),
    ]


def history_archive_ops():
    thermal_lines = (
        '{"sampled_at": 1794100100.5, "temp": 76}\n'
        '{"sampled_at": "1794100101", "temp": 77}\r\n'
        '{"temp": 78}\n'
        '{"sampled_at": -1}\n'
        '{"sampled_at": "nan"}\n'
        '{"sampled_at": true, "ação": "sim"}\n'
        '{"sampled_at": 1794'
    )
    return [
        {
            "op": "write_file",
            "path": "mining-a.log",
            "text": "﻿[2026-11-08 20:00:00] GPU0 RTX 3070: 78.00 TH/s   \r\n"
            "linha sem carimbo\n"
            "[2026-13-40 99:99:99] carimbo inválido\n"
            "incompleta",
        },
        {"op": "archive_log", "path": "mining-a.log", "gpu_uuid": "GPU-1111", "wallet": "prl1gpu"},
        {"op": "archive_log", "path": "mining-a.log", "gpu_uuid": "GPU-1111", "wallet": "prl1gpu"},
        {
            "op": "write_file",
            "path": "mining-a.log",
            "append": True,
            "text": " agora completa\n[2026-11-08 20:00:05] segunda\nlinha sem carimbo\n",
        },
        {"op": "archive_log", "path": "mining-a.log", "gpu_uuid": "GPU-1111", "wallet": "prl1gpu"},
        {"op": "write_file", "path": "mining-a.log", "append": True, "hex": "fffe410a"},
        {"op": "write_file", "path": "mining-a.log", "append": True, "text": "fim com separador\x1c\x1f \n"},
        {"op": "archive_log", "path": "mining-a.log"},
        {"op": "reopen"},
        {
            "op": "write_file",
            "path": "mining-a.log",
            "append": True,
            "text": "[2026-11-08 20:01:00] depois de reabrir\n",
        },
        {"op": "archive_log", "path": "mining-a.log", "gpu_uuid": "GPU-1111"},
        {"op": "write_file", "path": "mining-a.log", "text": "[2026-11-08 20:02:00] rotacionado\n"},
        {"op": "archive_log", "path": "mining-a.log", "gpu_uuid": "GPU-1111"},
        {"op": "write_file", "path": "thermal.jsonl", "text": thermal_lines},
        {"op": "archive_thermal", "path": "thermal.jsonl"},
        {"op": "archive_thermal", "path": "thermal.jsonl"},
        {"op": "archive_thermal", "path": "ausente.jsonl"},
        {
            "op": "telemetry",
            "data": {
                "collected_at": 1794100200.0,
                "miner": {"recent_logs": "sem caminho 1\n\nsem caminho 2"},
                "log_path": None,
            },
        },
        {"op": "reopen", "wallet": "prl1second"},
        {"op": "archive_thermal", "path": "thermal.jsonl"},
        {"op": "read", "call": "stats", "args": []},
    ]


def history_rejected_ops():
    return [
        {"op": "telemetry", "data": {"miner": {}}},
        {"op": "last_error"},
        {"op": "rig_telemetry", "rig": {"devices": [{"uuid": "GPU-3333", "collected_at": None}]}},
        {"op": "last_error"},
        {"op": "finance", "state": {"snapshots": [{"timestamp": 1.0}]}},
        {"op": "last_error"},
        {"op": "telemetry", "data": {"miner": {"recent_logs": "linha"}}},
        {"op": "preferences", "data": ["não", "é", "objeto"]},
        {"op": "last_error"},
        {"op": "event", "timestamp": 1794100300.0, "kind": "depois", "data": {}, "key": "ok"},
        {"op": "last_error"},
    ]


LEGACY_GPU_TABLE = (
    "CREATE TABLE gpu_telemetry (gpu_uuid TEXT NOT NULL,timestamp REAL NOT NULL,wallet TEXT NOT NULL,"
    "session TEXT NOT NULL,source TEXT NOT NULL,gpu_json TEXT,miner_json TEXT NOT NULL,miner_running INTEGER,"
    "enabled INTEGER,errors_json TEXT,log_path TEXT,PRIMARY KEY(gpu_uuid,timestamp))"
)


def history_store():
    cases = [
        history_case("basico", history_basic_ops()),
        history_case("arquivo_de_log", history_archive_ops()),
        history_case("rejeitadas", history_rejected_ops()),
        history_case(
            "migracao_gpu_v2",
            [
                {
                    "op": "rig_telemetry",
                    "rig": {
                        "devices": [
                            {
                                "uuid": "GPU-1111",
                                "collected_at": 1794000000.0,
                                "wallet": "prl1new",
                                "gpu": {"temperature.gpu": 71},
                            }
                        ]
                    },
                },
                {"op": "read", "call": "gpu_stats", "args": []},
                {"op": "read", "call": "restore_gpu", "args": ["GPU-1111", 0, "prl1old"]},
            ],
            prepare=[
                LEGACY_GPU_TABLE,
                "INSERT INTO gpu_telemetry VALUES('GPU-1111',1794000000.0,'prl1old','s-old','live','{\"temperature.gpu\":70}','{}',1,1,'{}',NULL)",
                "INSERT INTO gpu_telemetry VALUES('GPU-1111',1794000002.0,'prl1old','s-old','live',NULL,'{}',0,1,'{}','a.log')",
                "PRAGMA user_version=2",
            ],
        ),
        history_case(
            "versao_futura",
            [
                {"op": "event", "timestamp": 1.0, "kind": "x", "data": {}, "key": "k"},
                {"op": "last_error"},
                {"op": "read", "call": "stats", "args": []},
            ],
            prepare=["PRAGMA user_version=4"],
        ),
        history_case(
            "esquema_gpu_desconhecido",
            [
                {"op": "last_error"},
            ],
            prepare=["CREATE TABLE gpu_telemetry (gpu_uuid TEXT PRIMARY KEY, timestamp REAL)"],
        ),
    ]
    write("history", {"cases": cases, "now": HISTORY_NOW, "wallet": HISTORY_WALLET})


FIN_NOW = 1794189600.5
FIN_W1 = "prl1" + "q" * 38
FIN_W2 = "prl1" + "z" * 40
FIN_W3 = "prl1" + "x" * 36


def fin_compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def fin_market(at, brl=0.0123, usd=0.0021, **network):
    data = {
        "net_hashrate": 4.5e15,
        "block_time": 60,
        "block_reward": 50.0,
        "fee": 0.01,
        "minpay": 2,
        "blocks_for_maturation": 100,
    }
    data.update(network)
    return {
        "price": {
            "fetched_at": at,
            "data": {
                "pearl-2": {"brl": brl, "usd": usd, "last_updated_at": at - 20},
                "bitcoin": {"brl": 600000.0, "usd": 110000.0, "last_updated_at": at - 30},
            },
        },
        "network": {"fetched_at": at, "data": data},
    }


def fin_wallet(wallet, at, total, paid, snapshots, **sources):
    result = {
        "wallet": wallet,
        "sources": {
            **fin_market(at),
            "balance": {
                "fetched_at": at,
                "data": {"total": total, "confirmed": total * 0.75, "unconfirmed": total * 0.25, "threshold": 2.0},
            },
            "payouts": {"fetched_at": at, "data": {"paid": paid}},
            "rewards": {"fetched_at": at, "data": [{"date": at - 3600, "amount": 0.1}]},
        },
        "snapshots": snapshots,
        "errors": {},
    }
    result["sources"].update(sources)
    return result


def fin_midnight(now):
    import finance

    local = dt.datetime.fromtimestamp(now, finance.LOCAL_TZ)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def finance_model_cases():
    import finance

    now = FIN_NOW
    midnight = fin_midnight(now)
    series = [
        {"timestamp": now - 40 * 86400, "total_prl": 0.5},
        {"timestamp": now - 8 * 86400, "total_prl": 1.0},
        {"timestamp": midnight - 60, "total_prl": 2.0},
        {"timestamp": midnight + 600, "total_prl": 2.1},
        {"timestamp": now - 120, "total_prl": 2.6},
        {"timestamp": now - 60, "total_prl": 2.65},
    ]
    uncovered = [
        {"timestamp": midnight - 200, "total_prl": 2.0},
        {"timestamp": midnight + 30, "total_prl": 2.05},
        {"timestamp": now - 30, "total_prl": 2.5},
    ]
    malformed = {
        "wallet": FIN_W1,
        "sources": {
            "price": {
                "fetched_at": "1794189590",
                "data": {"pearl-2": {"brl": "0.02", "usd": -1, "last_updated_at": "x"}, "bitcoin": "lixo"},
            },
            "balance": {
                "fetched_at": None,
                "data": {"total": "3.5", "confirmed": -2, "unconfirmed": "abc", "extra": 7},
            },
            "payouts": {"fetched_at": now - 10, "data": {"paid": "1e1"}},
            "rewards": {"fetched_at": now, "data": {"not": "list"}},
            "network": {
                "fetched_at": now - 5,
                "data": {"net_hashrate": "4e15", "block_time": "60", "block_reward": 50, "fee": "0.02"},
            },
            "payout_history": {"fetched_at": True, "data": {"results": []}},
            "unknown": {"fetched_at": 0.0, "data": {"x": 1}},
            "not_dict": [1, 2],
        },
        "snapshots": [
            {"timestamp": now + 100, "total_prl": 9},
            {"timestamp": now + 3, "total_prl": 1.3},
            {"timestamp": -1, "total_prl": 1},
            {"timestamp": now - 50, "total_prl": -1},
            {"timestamp": now - 70, "total_prl": 1.0},
            {"timestamp": now - 70, "total_prl": 1.25},
            {"timestamp": "1794189500", "total_prl": "1.1"},
            {"timestamp": -0.0, "total_prl": 0},
            {"timestamp": 0.0, "total_prl": 0.5},
            "lixo",
            {"timestamp": True, "total_prl": 1},
        ],
        "errors": [],
    }
    full = fin_wallet(FIN_W1, now - 10, 1.25, 1.4, series)
    support = {"wallet": FIN_W1, "support_enabled": True}
    cases = []

    def financial(name, state, hashrate, at=now, wallet=FIN_W1, settings=None):
        settings = {"wallet": wallet} if settings is None else settings
        expected = finance.financial_model(copy_json(state), hashrate, at, wallet=wallet, settings=settings)
        cases.append(
            {
                "kind": "financial",
                "name": name,
                "state": state,
                "hashrate": hashrate,
                "now": at,
                "wallet": wallet,
                "settings": settings,
                "expected": expected,
            }
        )

    for name, state in (("vazio", {}), ("nao_objeto", ["x"]), ("malformado", malformed), ("completo", full)):
        cases.append(
            {
                "kind": "normalize",
                "name": name,
                "state": state,
                "now": now,
                "expected": finance.normalize_finance_state(copy_json(state), now),
            }
        )
    financial("vazio", {}, None)
    financial("completo_sem_hashrate", full, None)
    financial("completo", full, 76.5)
    financial("hashrate_texto", full, "76.5")
    financial("hashrate_zero", full, 0)
    financial("hashrate_negativo", full, -3.0)
    financial("contribuicao", full, 76.5, settings=support)
    financial(
        "contribuicao_autor",
        full,
        76.5,
        wallet=contribution.AUTHOR_WALLET,
        settings={"wallet": contribution.AUTHOR_WALLET, "support_enabled": True},
    )
    financial("malformado", malformed, 50.0)
    financial("rede_antiga", fin_wallet(FIN_W1, now - 400, 1.25, 1.4, series), 76.5)
    financial("rede_no_futuro", fin_wallet(FIN_W1, now + 6, 1.25, 1.4, series), 76.5)
    stale_network = fin_wallet(FIN_W1, now - 10, 1.25, 1.4, series)
    stale_network["sources"]["network"]["fetched_at"] = now - 181
    financial("so_rede_antiga", stale_network, 76.5)
    financial("erro_cotacao", {**full, "errors": {"price": "HTTP Error 429: Too Many Requests"}}, 76.5)
    financial("erro_vazio", {**full, "errors": {"network": ""}}, 76.5)
    for label, fee in (("taxa_nula", None), ("taxa_alta", 1.5), ("taxa_um", 1), ("taxa_texto", "0.03")):
        financial(
            label,
            fin_wallet(
                FIN_W1,
                now - 10,
                1.25,
                1.4,
                series,
                network={"fetched_at": now - 10, "data": {**fin_market(0)["network"]["data"], "fee": fee}},
            ),
            76.5,
        )
    no_fee = fin_wallet(FIN_W1, now - 10, 1.25, 1.4, series)
    del no_fee["sources"]["network"]["data"]["fee"]
    financial("taxa_ausente", no_fee, 76.5)
    financial(
        "estouro",
        fin_wallet(
            FIN_W1,
            now - 10,
            1.25,
            1.4,
            series,
            network={"fetched_at": now - 10, "data": {**fin_market(0)["network"]["data"], "net_hashrate": 1e-300}},
        ),
        1e300,
    )
    financial("sem_cobertura", fin_wallet(FIN_W1, now - 10, 1.25, 1.4, uncovered), 76.5)
    border = [
        {"timestamp": midnight - 150, "total_prl": 2.0},
        {"timestamp": midnight + 90, "total_prl": 2.04},
        {"timestamp": now - 30, "total_prl": 2.5},
    ]
    financial("cobertura_150_s", fin_wallet(FIN_W1, now - 10, 1.25, 1.4, border), 76.5)
    border_ok = [{"timestamp": midnight - 120, "total_prl": 2.0}, {"timestamp": now - 30, "total_prl": 2.5}]
    financial("cobertura_120_s", fin_wallet(FIN_W1, now - 10, 1.25, 1.4, border_ok), 76.5)
    financial("uma_amostra", fin_wallet(FIN_W1, now - 10, 1.25, 1.4, [{"timestamp": now - 30, "total_prl": 2.5}]), 76.5)
    no_btc = fin_wallet(FIN_W1, now - 10, 1.25, 1.4, series)
    del no_btc["sources"]["price"]["data"]["bitcoin"]
    del no_btc["sources"]["price"]["data"]["pearl-2"]["last_updated_at"]
    financial("sem_bitcoin", no_btc, 76.5)
    btc_old = fin_wallet(FIN_W1, now - 10, 1.25, 1.4, series)
    btc_old["sources"]["price"]["data"]["bitcoin"]["last_updated_at"] = now - 500
    financial("bitcoin_antigo", btc_old, 76.5)
    free = fin_wallet(FIN_W1, now - 10, 1.25, 1.4, series)
    free["sources"]["price"]["data"]["pearl-2"]["brl"] = 0
    financial("preco_zero", free, 76.5)
    for label, offset in (("segunda", 30), ("segunda_fim", 86400 - 0.25), ("domingo", -30)):
        monday = midnight - ((dt.datetime.fromtimestamp(now, finance.LOCAL_TZ).weekday()) * 86400) + 7 * 86400
        at = monday + offset
        financial(
            label,
            fin_wallet(
                FIN_W1,
                at - 10,
                1.25,
                1.4,
                [{"timestamp": monday - 7 * 86400 - 60, "total_prl": 1.0}, {"timestamp": at - 60, "total_prl": 2.0}],
            ),
            76.5,
            at=at,
        )
    financial("microssegundo", full, 76.5, at=midnight - 0.0000004)
    return cases


def copy_json(value):
    return json.loads(json.dumps(value))


def finance_portfolio_cases():
    import finance

    now = FIN_NOW
    midnight = fin_midnight(now)

    def gpu(uuid, wallet=None, rate=40.0, age=5, **extra):
        device = {
            "uuid": uuid,
            "enabled": True,
            "miner_running": True,
            "miner": {"hashrate": rate, "hashrate_at": now - age, "paused": False},
        }
        if wallet is not None:
            device["wallet"] = wallet
        device.update(extra)
        return device

    def snapshots(base):
        return [{"timestamp": midnight - 30, "total_prl": base}, {"timestamp": now - 60, "total_prl": base + 0.5}]

    two = {
        "schema_version": 2,
        "primary_wallet": FIN_W1,
        "wallets": {
            FIN_W1: fin_wallet(FIN_W1, now - 10, 1.25, 1.4, snapshots(1.0)),
            FIN_W2: fin_wallet(FIN_W2, now - 100, 0.75, 0.2, snapshots(0.5)),
        },
    }
    three = {
        "schema_version": 2,
        "primary_wallet": FIN_W1,
        "wallets": {
            FIN_W1: fin_wallet(FIN_W1, now - 10, 0.1, 0.1, snapshots(0.1)),
            FIN_W2: fin_wallet(FIN_W2, now - 10, 0.2, 0.2, snapshots(0.2)),
            FIN_W3: fin_wallet(FIN_W3, now - 10, 0.3, 0.3, snapshots(0.3)),
        },
    }
    no_pool = copy_json(two)
    del no_pool["wallets"][FIN_W2]["sources"]["balance"]
    devices_two = [
        gpu("GPU-a", FIN_W1, 40.25),
        gpu("GPU-b", FIN_W1, 36.5),
        gpu("GPU-c", FIN_W2, 30.0),
        gpu("GPU-d", FIN_W2, 20.0, age=400),
        gpu("GPU-e", FIN_W2, 10.0, enabled=False),
        gpu("GPU-f", FIN_W2, 10.0, miner={"hashrate": 10.0, "hashrate_at": now, "paused": True}),
    ]
    mixed = [
        gpu("GPU-1", "", 10.0),
        {"uuid": "GPU-2", "miner_running": True, "miner": {"hashrate": "12.5", "hashrate_at": now - 1}},
        {"uuid": "GPU-3", "enabled": None, "miner_running": True, "miner": {"hashrate": 3.0, "hashrate_at": now}},
        {"uuid": "GPU-4", "miner_running": False, "miner": None},
        gpu("GPU-5", None, -1.0),
        gpu("GPU-6", None, 5.0, age=-6),
    ]
    cases = []

    def portfolio(name, state, devices, selected=None, settings=None, default=FIN_W1):
        settings = {"wallet": FIN_W1} if settings is None else settings
        with mock.patch.object(finance, "WALLET", default):
            expected = finance.portfolio_model(copy_json(state), copy_json(devices), now, selected, settings=settings)
        # `hashrate_by_wallet` nasce de um `set` (ordem aleatória por processo): ordena para o
        # golden ser estável. O teste Rust compara esse objeto sem depender da ordem.
        expected["hashrate_by_wallet"] = dict(sorted(expected["hashrate_by_wallet"].items()))
        cases.append(
            {
                "kind": "portfolio",
                "name": name,
                "state": state,
                "devices": devices,
                "now": now,
                "selected": selected,
                "settings": settings,
                "default_wallet": default,
                "expected": expected,
            }
        )

    portfolio("legado_sem_gpu", fin_wallet(FIN_W1, now - 10, 1.25, 1.4, snapshots(1.0)), [])
    portfolio("legado_sem_carteira", {"sources": {}, "snapshots": []}, [], settings={})
    portfolio(
        "uma_carteira",
        {"schema_version": 2, "primary_wallet": FIN_W1, "wallets": {FIN_W1: two["wallets"][FIN_W1]}},
        [gpu("GPU-a"), gpu("GPU-b", rate=36.5)],
    )
    portfolio("duas_consolidado", two, devices_two)
    portfolio("duas_selecionada", two, devices_two, selected=FIN_W2)
    portfolio("duas_selecao_desconhecida", two, devices_two, selected=FIN_W3)
    portfolio("sem_saldo_numa", no_pool, devices_two)
    portfolio(
        "soma_compensada", three, [gpu("GPU-a", FIN_W1, 0.1), gpu("GPU-b", FIN_W2, 0.2), gpu("GPU-c", FIN_W3, 0.3)]
    )
    portfolio("gpus_variadas", two, mixed)
    portfolio(
        "carteira_sem_estado", two, [gpu("GPU-z", FIN_W3, 5.0)], settings={"wallet": FIN_W1, "support_enabled": True}
    )
    portfolio("versao_texto", {**two, "schema_version": 2.0}, devices_two)
    portfolio("versao_um", {**two, "schema_version": 1}, devices_two)

    def snapshots_case(name, state, wallets=None):
        cases.append(
            {
                "kind": "snapshots",
                "name": name,
                "state": state,
                "wallets": wallets,
                "expected": finance.portfolio_snapshots(copy_json(state), wallets),
            }
        )

    snapshots_case("legado", {"snapshots": [{"timestamp": 1, "total_prl": 2}]})
    snapshots_case("legado_vazio", {})
    snapshots_case("uma", two, [FIN_W2])
    snapshots_case("duas", two)
    snapshots_case("repetidas", two, [FIN_W2, FIN_W1, FIN_W2])
    snapshots_case("lista_vazia", two, [])
    snapshots_case("carteira_sem_amostra", two, [FIN_W1, FIN_W3])
    interleaved = {
        "schema_version": 2,
        "wallets": {
            FIN_W1: {
                "snapshots": [
                    {"timestamp": 10.0, "total_prl": 0.1},
                    {"timestamp": 30.0, "total_prl": 0.7},
                    {"timestamp": 40, "total_prl": 0.9},
                ]
            },
            FIN_W2: {
                "snapshots": [
                    {"timestamp": 20.0, "total_prl": 0.2},
                    {"timestamp": 30.0, "total_prl": 0.25},
                    {"timestamp": 40.0, "total_prl": 0.3},
                ]
            },
            FIN_W3: {"snapshots": [{"timestamp": 5.0, "total_prl": 0.3}, {"timestamp": 30, "total_prl": 0.1}]},
        },
    }
    snapshots_case("intercaladas", interleaved)
    snapshots_case("intercaladas_ordem", interleaved, [FIN_W3, FIN_W2, FIN_W1])
    snapshots_case("sem_carteiras", {"schema_version": 2, "wallets": {}})
    for values in ([0.1, 0.2, 0.3], [1, 2], [1, 0.5, 2], [1e100, 1.0, -1e100], [], [True, 1.5], [0.1] * 10, [-0.0]):
        cases.append({"kind": "sum", "values": values, "expected": sum(values)})
    return cases


class FinFakeHistory:
    def __init__(self, archived):
        import threading

        self.ready = threading.Event()
        self.ready.set()
        self.archived = archived
        self.calls = []

    def finance(self, state):
        self.calls.append(fin_compact(["finance", state]))

    def finance_state(self, since, wallet=None):
        self.calls.append(fin_compact(["finance_state", since, wallet]))
        return copy_json(self.archived.get(wallet, {"wallet": wallet, "snapshots": [], "sources": {}}))


class FinFetcher:
    def __init__(self):
        self.responses = {}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        response = self.responses.get(url, {"__error__": f"sem resposta para {url}"})
        if isinstance(response, dict) and "__error__" in response:
            raise RuntimeError(response["__error__"])
        return copy_json(response)


def fin_tree(directory):
    files = {}
    for path in sorted(Path(directory).rglob("*")):
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = path.read_bytes().decode("utf-8")
    return files


def fin_responses(wallet, at, total=1.25, paid=1.4):
    market = fin_market(at)
    responses = {
        "price": {
            "pearl-2": {"brl": "0.0123", "usd": 0.0021, "last_updated_at": at - 20},
            "bitcoin": {"brl": 600000, "usd": 110000.5},
        },
        "balance": {
            "total": str(total),
            "confirmed": total,
            "unconfirmed": 0,
            "threshold": 2,
            "maturation_time": 6000,
            "extra": "x",
        },
        "payouts": {"paid": paid, "count": 3},
        "rewards": [{"date": at - 3600, "amount": 0.1}],
        "payout_history": {
            "count": 1,
            "results": [{"date": at - 9000, "received": 1.4, "txid": "abc", "status": "paid"}],
        },
        "network": market["network"]["data"],
    }
    import finance

    return {url: responses[key] for key, url in finance.urls_for_wallet(wallet).items()}


def finance_collector_case(name, wallet, rounds, cache=None, archived=None, prepare=False):
    import finance

    fetcher = FinFetcher()
    history = FinFakeHistory(archived) if archived is not None else None
    clock = {"now": rounds[0]["now"] if rounds else FIN_NOW}
    case = {
        "kind": "collector",
        "name": name,
        "wallet": wallet,
        "cache": cache,
        "archived": archived,
        "prepare": prepare,
        "rounds": rounds,
    }
    with tempfile.TemporaryDirectory() as directory, mock.patch.object(finance.time, "time", lambda: clock["now"]):
        if cache is not None:
            (Path(directory) / "finance-cache.json").write_bytes(cache.encode("utf-8"))
        collector = finance.FinanceCollector(Path(directory), history, wallet=wallet, fetcher=fetcher)
        case["initial"] = fin_compact(collector.state)
        if prepare:
            collector.stop.set()
            collector.work()
            published = []
            while not collector.samples.empty():
                published.append(fin_compact(collector.samples.get_nowait()))
            case["prepared"] = published
            case["prepared_state"] = fin_compact(collector.state)
        outputs = []
        for round_ in rounds:
            clock["now"] = round_["now"]
            fetcher.responses = round_["responses"]
            try:
                outputs.append({"sample": fin_compact(collector.sample())})
            except (TypeError, ValueError, OSError) as error:
                outputs.append({"raised": str(error), "state": fin_compact(collector.state)})
            outputs[-1]["files"] = fin_tree(directory)
        case["outputs"] = outputs
        case["fetched"] = sorted(fetcher.calls)
        case["history_calls"] = history.calls if history else []
    return case


def finance_collector_cases():
    import finance

    now = FIN_NOW
    base = fin_responses(FIN_W1, now)
    cases = []
    cases.append(
        finance_collector_case(
            "completo",
            FIN_W1,
            [
                {"now": now, "responses": base},
                {"now": now + 30, "responses": fin_responses(FIN_W1, now + 30, total=1.3)},
                {"now": now + 75, "responses": fin_responses(FIN_W1, now + 75, total=1.35)},
            ],
        )
    )
    urls = finance.urls_for_wallet(FIN_W1)
    broken = dict(base)
    broken[urls["price"]] = {"pearl-2": {"usd": 1}}
    broken[urls["balance"]] = {"confirmed": 1}
    broken[urls["payouts"]] = {"paid": "abc"}
    broken[urls["rewards"]] = {"not": "list"}
    broken[urls["payout_history"]] = {"results": {}}
    broken[urls["network"]] = ["lista"]
    broken2 = dict(base)
    broken2[urls["price"]] = {"bitcoin": {}}
    broken2[urls["balance"]] = "texto"
    broken2[urls["payouts"]] = {"__error__": "HTTP Error 503: Service Unavailable"}
    broken2[urls["rewards"]] = {"__error__": "timed out"}
    broken2[urls["payout_history"]] = []
    broken3 = dict(base)
    broken3[urls["price"]] = {"pearl-2": "lixo"}
    broken3[urls["balance"]] = {"total": -5}
    previous = fin_compact(fin_wallet(FIN_W1, now - 3600, 1.0, 1.4, [{"timestamp": now - 3600, "total_prl": 2.4}]))
    cases.append(
        finance_collector_case(
            "erros",
            FIN_W1,
            [
                {"now": now, "responses": broken},
                {"now": now + 60, "responses": broken2},
                {"now": now + 120, "responses": broken3},
            ],
            cache="﻿" + previous,
        )
    )
    cases.append(
        finance_collector_case(
            "cache_outra_carteira",
            FIN_W1,
            [{"now": now, "responses": base}],
            cache=fin_compact({"wallet": FIN_W2, "sources": {}, "snapshots": []}),
        )
    )
    cases.append(finance_collector_case("cache_invalido", FIN_W1, [{"now": now, "responses": base}], cache="{nao json"))
    cases.append(finance_collector_case("cache_lista", FIN_W1, [{"now": now, "responses": base}], cache="[1, 2]"))
    old = fin_wallet(
        FIN_W1,
        now - 60,
        1.0,
        1.4,
        [
            {"timestamp": now - 40 * 86400, "total_prl": 1.0},
            {"timestamp": now - 35 * 86400 + 1, "total_prl": 1.5},
            {"timestamp": now - 30, "total_prl": 2.0},
        ],
    )
    cases.append(
        finance_collector_case("poda_35_dias", FIN_W1, [{"now": now, "responses": base}], cache=json.dumps(old))
    )
    empty_cache = fin_compact(
        {
            "wallet": "",
            "sources": {
                "balance": {"fetched_at": now - 10, "data": {"total": 1.0}},
                "payouts": {"fetched_at": now - 10, "data": {"paid": None}},
            },
            "snapshots": [],
        }
    )
    cases.append(finance_collector_case("carteira_vazia", "", [{"now": now, "responses": fin_responses("", now)}]))
    cases.append(
        finance_collector_case(
            "carteira_vazia_pago_nulo", "", [{"now": now, "responses": fin_responses("", now)}], cache=empty_cache
        )
    )
    archived = {
        FIN_W1: {
            "wallet": FIN_W1,
            "snapshots": [{"timestamp": now - 7200, "total_prl": 1.9}, {"timestamp": now - 3600, "total_prl": 2.0}],
            "sources": {
                "balance": {"fetched_at": now - 30, "data": {"total": 1.1}},
                "price": {"fetched_at": now - 7200, "data": {"pearl-2": {"brl": 0.01}}},
            },
        }
    }
    cases.append(
        finance_collector_case(
            "historico", FIN_W1, [{"now": now, "responses": base}], cache="﻿" + previous, archived=archived, prepare=True
        )
    )
    cases.append(finance_collector_case("historico_sem_cache", FIN_W1, [], archived={}, prepare=True))
    return cases


def finance_portfolio_collector_case():
    import finance

    now = FIN_NOW
    fetcher = FinFetcher()
    archived = {FIN_W2: {"wallet": FIN_W2, "snapshots": [{"timestamp": now - 600, "total_prl": 0.9}], "sources": {}}}
    history = FinFakeHistory(archived)
    clock = {"now": now}
    rounds = []
    responses = {**fin_responses(FIN_W1, now), **fin_responses(FIN_W2, now, total=0.75, paid=0.2)}
    second = {**fin_responses(FIN_W1, now + 60, total=1.3), **fin_responses(FIN_W2, now + 60, total=0.8, paid=0.2)}
    second[finance.urls_for_wallet(FIN_W2)["balance"]] = {"__error__": "HTTP Error 500: Internal Server Error"}
    case = {
        "kind": "portfolio_collector",
        "wallets": [FIN_W2, FIN_W1, "invalida"],
        "default_wallet": FIN_W1,
        "archived": archived,
    }
    with (
        tempfile.TemporaryDirectory() as directory,
        mock.patch.object(finance.time, "time", lambda: clock["now"]),
        mock.patch.object(finance, "WALLET", FIN_W1),
    ):
        collector = finance.PortfolioFinanceCollector(
            Path(directory), history, wallets=[FIN_W2, FIN_W1], fetcher=fetcher
        )
        collector.configure_wallets([FIN_W2, FIN_W1, "invalida"])
        for at, payload in ((now, responses), (now + 60, second)):
            clock["now"] = at
            fetcher.responses = payload
            rounds.append(
                {
                    "now": at,
                    "responses": payload,
                    "sample": fin_compact(collector.sample()),
                    "files": fin_tree(directory),
                }
            )
        collector.configure_wallets(["invalida"])
        case["after_invalid"] = sorted(collector.wallets)
    case["rounds"] = rounds
    case["fetched"] = sorted(fetcher.calls)
    case["history_calls"] = sorted(history.calls)
    return case


PROCESS_ARGV = [
    "p",
    "p a b",
    "p\ta\t\tb",
    "  p a",
    '"C:\\Program Files\\SRBMiner\\SRBMiner-MULTI.exe" --algorithm pearlhash --gpu-id 0,1',
    'C:\\m\\SRBMiner-MULTI.exe --gpu-id 0 --pool "stratum+tcp://x:1" --wallet "prl1 abc"',
    'p "a b c" d e',
    'p "ab\\"c" "\\\\" d',
    'p a\\\\\\b d"e f"g h',
    'p a\\\\\\"b c d',
    'p a\\\\\\\\"b c" d e',
    'p "a""b"',
    'p """"',
    'p """"""',
    'p a"b"""c d',
    'p "a"""b c',
    'p ""',
    'p "" x',
    'p a\\',
    'p a\\\\',
    'p "a\\\\"',
    'p "unterminated',
    'p "x"y"z"',
    'p \\"a',
    'p ação "é ç" 日本',
    '"" a',
    '"a"b c',
    '"a b',
    'p --gpu-id',
    'p --gpu-id ""',
    "p a   ",
]
PROCESS_PATHS = [
    "C:\\Program Files\\SRBMiner\\..\\SRBMiner\\SRBMiner-MULTI.exe",
    "C:/Mining//Pearl/./SRBMiner-MULTI.EXE",
    "c:\\MINING\\Pearl\\",
    "D:\\a\\b\\..\\..\\c",
    "C:\\Users\\Ação\\Miner.exe",
]


SUCCESSOR_MINER = "C:\\Mining\\SRBMiner-Multi-3-8-4\\SRBMiner-MULTI.exe"


def successor_cases():
    import miner_identity

    log = "C:\\Logs\\GPU-a\\mining-1.log"
    argv = [SUCCESSOR_MINER, "--gpu-id", "1", "--pool", "127.0.0.1:7048", "--wallet", "prl1w", "--worker", "rig-1"]
    argv += ["--log-file", log]
    base = {"pid": 20, "created_at": "200", "executable": SUCCESSOR_MINER, "argv": argv}
    previous = {
        "miner": {"pid": 10, "created_at": "100"},
        "miner_log": log,
        "wallet": "prl1w",
        "pool": "127.0.0.1:7048",
    }
    device = {"index": 0, "miner_index": 1, "worker_name": "rig-1"}

    def changed(argv_from=None, **fields):
        candidate = {**base, **fields}
        if argv_from is not None:
            candidate["argv"] = argv_from
        return candidate

    def swap(name, value):
        items = list(argv)
        items[items.index(name) + 1] = value
        return items

    variants = [
        ("igual", changed(), previous, device),
        ("mesmo_pid", changed(pid=10), previous, device),
        ("mais_antigo", changed(created_at="100"), previous, device),
        ("created_texto_invalido", changed(created_at="x"), previous, device),
        ("created_inteiro", changed(created_at=201), previous, device),
        ("created_espacos", changed(created_at=" 300 "), previous, device),
        ("created_float", changed(created_at=200.9), previous, device),
        ("sem_created", {key: value for key, value in base.items() if key != "created_at"}, previous, device),
        ("exe_maiusculo", changed(executable=SUCCESSOR_MINER.upper()), previous, device),
        ("exe_barra", changed(executable=SUCCESSOR_MINER.replace("\\", "/")), previous, device),
        ("exe_outro", changed(executable="C:\\Outro\\SRBMiner-MULTI.exe"), previous, device),
        ("argv0_outro", changed(argv_from=["C:\\x.exe", *argv[1:]]), previous, device),
        ("log_outro", changed(argv_from=swap("--log-file", "C:\\Logs\\outro.log")), previous, device),
        ("log_caixa", changed(argv_from=swap("--log-file", log.upper())), previous, device),
        ("sem_log_anterior", changed(), {**previous, "miner_log": None}, device),
        ("gpu_outra", changed(argv_from=swap("--gpu-id", "0")), previous, device),
        ("sem_miner_index", changed(argv_from=swap("--gpu-id", "0")), previous, {"index": 0, "worker_name": "rig-1"}),
        ("carteira_outra", changed(argv_from=swap("--wallet", "prl1x")), previous, device),
        ("pool_outra", changed(argv_from=swap("--pool", "prl.kryptex.network:7048")), previous, device),
        ("worker_outro", changed(), previous, {**device, "worker_name": "rig-2"}),
        ("gpu_repetida", changed(argv_from=[*argv, "--gpu-id", "1"]), previous, device),
        ("argv_vazio", changed(argv_from=[]), previous, device),
        ("sem_miner_anterior", changed(), {**previous, "miner": None}, device),
        ("log_sem_valor", changed(argv_from=[*argv[:-2], "--log-file"]), previous, device),
    ]
    return [
        {
            "kind": "successor",
            "name": name,
            "candidate": candidate,
            "previous": old,
            "device": gpu,
            "miner_path": SUCCESSOR_MINER,
            "expected": miner_identity.match_successor(candidate, old, gpu, SUCCESSOR_MINER),
        }
        for name, candidate, old, gpu in variants
    ]


def process_golden():
    import process_backend

    cases = [{"kind": "argv", "line": line, "expected": process_backend.windows_argv(line)} for line in PROCESS_ARGV]
    cases += [
        {"kind": "path", "path": path, "expected": process_backend.normalized_path(path)} for path in PROCESS_PATHS
    ]
    cases += successor_cases()
    write("process", {"cases": cases})


def health_cases():
    import mining_health

    now = 100000.5
    miners = {
        "vazio": {},
        "minerando": {
            "hashrate": 76.5,
            "hashrate_at": now - 10,
            "connection": "Conectado à pool",
            "last_share_at": now - 60,
        },
        "sem_share": {"hashrate": 76.5, "hashrate_at": now - 10, "connection": "Conectado à pool"},
        "share_antiga": {
            "hashrate": 76.5,
            "hashrate_at": now - 10,
            "last_share_at": now - 2000,
            "started_at": now - 5000,
        },
        "share_futura": {"hashrate": 76.5, "hashrate_at": now - 10, "last_share_at": now + 5},
        "share_igual": {"hashrate": 1, "hashrate_at": now, "last_share_at": now, "connection": "Conectado à pool"},
        "reconectando": {
            "hashrate": 76.5,
            "hashrate_at": now - 10,
            "last_share_at": now - 30,
            "connection": "Reconectando à pool",
        },
        "job_recente": {"hashrate": 76.5, "hashrate_at": now - 10, "last_job_at": now - 20},
        "job_antes_do_inicio": {
            "hashrate": 76.5,
            "hashrate_at": now - 10,
            "last_job_at": now - 400,
            "started_at": now - 300,
        },
        "hashrate_velho": {"hashrate": 76.5, "hashrate_at": now - 181, "connection": "Conectado à pool"},
        "hashrate_limite": {"hashrate": 76.5, "hashrate_at": now - 180, "connection": "Conectado à pool"},
        "hashrate_zero": {"hashrate": 0, "hashrate_at": now - 1, "connection": "Conectado à pool"},
        "hashrate_bool": {"hashrate": True, "hashrate_at": now - 1, "connection": "Conectado à pool"},
        "pausado": {"hashrate": 76.5, "hashrate_at": now - 1, "paused": True},
        "inicio_inteiro": {
            "hashrate": 76.5,
            "hashrate_at": now - 1,
            "started_at": 99000,
            "connection": "Conectado à pool",
        },
        "inicio_texto": {"hashrate": 76.5, "hashrate_at": now - 1, "started_at": "x", "connection": "Conectado à pool"},
        "alerta": {"hashrate": 76.5, "hashrate_at": now - 5, "last_share_at": now - 1000, "started_at": now - 4000},
    }
    starts = [None, now - 600, now - 100, 1, now + 10]
    intervals = [
        (),
        (100.0, 200.0),
        (100.0, 200.0, 300.0),
        (2000.0, 2000.0, 3000.0, 2500.0),
        (0.0, -5.0, 90000.0, 50.0, 60.0, 70.0),
        (150.0, 150.0, 150.0),
        (1200.0, 1200.0, 1200.0),
    ]
    cases = []
    for (name, miner), running, started, gaps in itertools.product(
        miners.items(), [True, False, None, 1], starts, intervals
    ):
        cases.append(
            {
                "kind": "health",
                "name": name,
                "miner": miner,
                "running": running,
                "now": now,
                "started_at": started,
                "intervals": list(gaps),
                "expected": mining_health.health(miner, running, now, started_at=started, intervals=gaps),
            }
        )
    return cases


def operations_case():
    import mining_health

    with tempfile.TemporaryDirectory() as folder:
        store = mining_health.OperationalStore(Path(folder) / "operations.sqlite3")
        ops = []

        def run(op, *args, **kwargs):
            method = getattr(store, op)
            ops.append({"op": op, "args": list(args), "kwargs": kwargs, "result": method(*args, **kwargs)})

        gpu = "GPU-a"
        for at in (1000.0, 1100.0, 1300.0, 1700.0, 2500.0):
            run("assess", gpu, {"last_share_at": at, "hashrate": 70.0, "hashrate_at": at}, True, at + 5.0, 900.0)
        run("assess", gpu, {"hashrate": 70.0, "hashrate_at": 2600.0}, True, 2610.0, 900.0)
        run("assess", gpu, {"hashrate": 70.0, "hashrate_at": 2600.0}, True, 2610.0, 2550.0)
        run("assess", gpu, {"last_share_at": 1000.0}, True, 90000.0, None)
        run("assess", "GPU-b", {"last_share_at": 50}, False, 60.0, None)
        for at in (5000.0, 5001.0, 5070.0, 5200.0, 5400.0, 9000.0, 9500.0):
            run("budget", gpu, at)
            run("reserve", gpu, "Processo encerrado " + "x" * 130, at)
        run("budget", gpu, 9100.0)
        run("budget", gpu, 20000.0)
        for at in (100.0, 120.0, 160.0, 300.0, 400.0, 3800.0):
            run("reserve_component", gpu, "controller", at)
        for at in (100.0, 125.0, 140.0, 200.0):
            run("reserve_component", gpu, "manual_start", at, delay=30, limit=2)
        for at, kind, message in (
            (10.0, "started", "Minerador iniciado"),
            (20.0, "started", "Minerador iniciado"),
            (75.0, "started", "Minerador iniciado"),
            (135.0, "started", "Minerador iniciado"),
            (76.0, "stopped", "Parada confirmada"),
            (77.0, "stopped", "Parada confirmada" + "é" * 400),
            (78.0, "stopped", "Parada confirmada" + "é" * 500),
            (5.0, "stopped", "Parada confirmada"),
        ):
            run("event", gpu, kind, message, at)
        run("recent_events", gpu)
        run("recent_events", gpu, 2)
        run("recent_events", gpu, 0)
        run("recent_events", gpu, 50)
        run("recent_events", "GPU-z")
        with contextlib.closing(sqlite3.connect(Path(folder) / "operations.sqlite3")) as db:
            dump = {
                table: [list(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in ("acks", "recoveries", "incidents", "component_attempts")
            }
    return [{"kind": "operations", "ops": ops, "dump": dump}]


def continuity_cases():
    import mining_continuity

    window = mining_continuity.ContinuityWindow()
    a, b, c = ({"uuid": f"GPU-{name}", "index": index} for index, name in enumerate("abc"))
    steps = []
    for now, devices in (
        (0.0, [a, b]),
        (10.0, [b]),
        (50.0, []),
        (95.0, [c]),
        (100.0, [c]),
        (140.0, [c]),
        (141.0, [a, c, {**a, "index": 9}]),
    ):
        steps.append({"op": "inventory", "now": now, "devices": devices, "result": window.inventory(devices, now)})
    for now in (100.0, 110.0, 141.0, 150.0, 199.0, 202.0, 190.0, 260.0, 261.0):
        steps.append({"op": "tick", "now": now, "result": window.tick(now), "grace_until": window.grace_until})
    sensors = [
        {"temperature.gpu": 60, "sampled_at": 990.0},
        {"temperature.gpu": 70, "sampled_at": 990.0},
        {"temperature.gpu": 69.9, "sampled_at": 984.5},
        {"temperature.gpu": 60, "sampled_at": 984.0},
        {"temperature.gpu": -1, "sampled_at": 990.0},
        {"temperature.gpu": 60, "sampled_at": 1001.0},
        {"temperature.gpu": 60, "sampled_at": 990.0, "error": "x"},
        {"temperature.gpu": True, "sampled_at": 990.0},
        {"temperature.gpu": None, "sampled_at": 990.0},
        {"temperature.gpu": 60},
        {},
    ]
    safe = [
        {"sensor": sensor, "now": 999.5, "expected": mining_continuity.safe_sensor(sensor, 999.5)} for sensor in sensors
    ]
    return [{"kind": "continuity", "steps": steps}, {"kind": "safe_sensor", "cases": safe}]


def reference_cases():
    import gpu_profiles
    import reference_profile

    uuid = reference_profile.REFERENCE_RTX3070_UUID
    good = {
        "uuid": uuid,
        "name": "NVIDIA GeForce RTX 3070",
        "power_default_w": 240,
        "supported_graphics_clocks_mhz": [1500, 1560, 1710],
    }
    gpus = [
        good,
        {**good, "power_default_w": 240.0},
        {**good, "power_default_w": 220},
        {**good, "name": "RTX 3070 Ti"},
        {**good, "name": None},
        {**good, "uuid": "GPU-outra"},
        {**good, "supported_graphics_clocks_mhz": [1560.0]},
        {**good, "supported_graphics_clocks_mhz": []},
        {key: value for key, value in good.items() if key != "name"},
    ]
    confirmed = [{"gpu": gpu, "expected": reference_profile.reference_device_confirmed(gpu)} for gpu in gpus]
    paths = [
        "C:/Mining/SRBMiner-Multi-3-8-4/SRBMiner-MULTI.exe",
        "C:/Mining/SRBMiner-Multi-3-8-3/SRBMiner-MULTI.exe",
        "C:/Mining/SRBMiner-Multi-3-10-0/SRBMiner-MULTI.exe",
        "C:/Mining/SRBMiner-Multi-4-0-0/SRBMiner-MULTI.exe",
        "C:/Mining/SRBMiner-Multi-3-8/SRBMiner-MULTI.exe",
        "C:/Mining/srbminer-multi-3-8-4/SRBMiner-MULTI.exe",
        "C:/Mining/SRBMiner-Multi-3-8-4x/SRBMiner-MULTI.exe",
        "SRBMiner-MULTI.exe",
    ]
    memory_settings = [
        {},
        {"reference_memory_mhz": 405},
        {"reference_memory_mhz": 405.0},
        {"reference_memory_mhz": "405"},
        {"reference_memory_mhz": 5001},
    ]
    memory = [
        {"miner_path": path, "settings": settings, "expected": reference_profile.reference_memory_clock(path, settings)}
        for path in paths
        for settings in memory_settings
    ]
    start_settings = [
        {},
        {"reference_start_core_mhz": 1710},
        {"reference_start_core_mhz": 1725},
        {"reference_start_core_mhz": 1710.0},
        {"reference_start_core_mhz": 1800},
        {"reference_start_core_mhz": "1710"},
        {"reference_start_core_mhz": 1710, "target_temperature_c": 78},
        {"reference_start_core_mhz": 1710, "target_temperature_c": 77.5},
        {"reference_start_core_mhz": 1710, "target_temperature_c": True},
        {"reference_start_core_mhz": 1710, "target_temperature_c": None},
    ]
    start = [
        {"gpu": gpu, "settings": settings, "expected": reference_profile.reference_start_clock(gpu, settings)}
        for gpu in (good, {**good, "supported_graphics_clocks_mhz": [1560, 1725]})
        for settings in start_settings
    ]
    profiles = [
        {"value": value, **capture(lambda value=value: gpu_profiles.profile(value))}
        for value in ("performance", "balanced", "cool", "turbo", None, 1)
    ]
    return [
        {"kind": "reference_confirmed", "cases": confirmed},
        {"kind": "reference_memory", "cases": memory},
        {"kind": "reference_start", "cases": start},
        {"kind": "profile", "cases": profiles},
    ]


def indent_cases():
    values = [
        {},
        [],
        {"a": [], "b": {}, "c": [1, 2.5, 1e16, 1e-07, -0.0, None, True, 'ã\n"\\\u001f']},
        {"devices": {"GPU-a": {"miner": {"pid": 1, "argv": ["C:\\x.exe", "--gpu-id", "0"]}, "ok": False}}},
        [[[]], [{}], {"x": [{"y": []}]}],
        "texto",
        3,
    ]
    return [
        {"kind": "indent", "value": value, "expected": json.dumps(value, ensure_ascii=False, indent=2)}
        for value in values
    ]


def rig_support_golden():
    cases = health_cases() + operations_case() + continuity_cases() + reference_cases() + indent_cases()
    write("rig_support", {"cases": cases})


def finance_golden():
    cases = (
        finance_model_cases()
        + finance_portfolio_cases()
        + finance_collector_cases()
        + [finance_portfolio_collector_case()]
    )
    write("finance", {"cases": cases, "author_wallet": contribution.AUTHOR_WALLET})


GENERATORS = {
    "numbers": numbers,
    "miner_log": miner_logs,
    "thermal": thermal_sequences,
    "valuation": valuations,
    "payout": payout_states,
    "rig_config": rig,
    "config": configs,
    "history": history_store,
    "finance": finance_golden,
    "process": process_golden,
    "rig_support": rig_support_golden,
}
# Goldens que não dependem do fuso nem de caminhos e podem ser gerados no Windows.
PORTABLE = {"history", "finance", "rig_support"}
# Goldens que usam a API real do Windows (shell32 `CommandLineToArgvW`, `GetFullPathNameW`).
WINDOWS_ONLY = {"process"}


if __name__ == "__main__":
    windows = sys.platform == "win32"
    if sys.argv[1:] == ["--list"]:
        here = sorted(PORTABLE | WINDOWS_ONLY) if windows else sorted(set(GENERATORS) - WINDOWS_ONLY)
        print(json.dumps({"here": here, "windows_only": sorted(WINDOWS_ONLY)}))
        sys.exit(0)
    default = (PORTABLE | WINDOWS_ONLY) if windows else set(GENERATORS) - WINDOWS_ONLY
    selected = sys.argv[1:] or [name for name in GENERATORS if name in default]
    unknown = [name for name in selected if name not in GENERATORS]
    if unknown:
        sys.exit(f"Golden desconhecido: {', '.join(unknown)}. Opções: {', '.join(GENERATORS)}")
    if not hasattr(time, "tzset") and set(selected) - PORTABLE - WINDOWS_ONLY:
        allowed = ", ".join(sorted(PORTABLE | WINDOWS_ONLY))
        sys.exit(f"Sem time.tzset (Windows): só os goldens {allowed} podem ser gerados aqui.")
    if not windows and set(selected) & WINDOWS_ONLY:
        sys.exit(f"Os goldens {', '.join(sorted(WINDOWS_ONLY))} usam a API do Windows e só são gerados lá.")
    for name in selected:
        GENERATORS[name]()
