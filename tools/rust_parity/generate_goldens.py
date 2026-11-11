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
    # Fim do log (janela de ~200 kB) sem "Connected to": a pool aceitando shares e o bloco de
    # status do SRBMiner 3.8.4 provam a conexão (bug real de 10/11/2026: "Aguardando conexão").
    (
        "tail_without_connect_line_shares_and_status",
        0,
        True,
        "[2026-11-10 23:53:08] GPU0 RTX 3070: 68.10 TH/s [T:74C FAN:93% P:150.2W EFF:0.453 CC:1695 MC:810 A:440 R:3 HW:0]\n"
        "[2026-11-10 23:53:08] Total: 68.10 TH/s [P:150.2W EFF:0.453 A:440 R:3 HW:0]\n"
        "[2026-11-10 23:54:04] GPU0[t0] share accepted [  214ms] [pearlhash][0]\n"
        "[2026-11-10 23:54:08] Uptime: 0d 15:18:26\n[2026-11-10 23:54:08] Pool: prl.kryptex.network:7048\n"
        "[2026-11-10 23:54:08] Connection: 0d 15:18:22\n[2026-11-10 23:54:08] Latency: ~215 ms\n"
        "[2026-11-10 23:54:08] Last job: 51 sec ago\n",
        "2026-11-10 23:55:00",
    ),
    # Só shares (sem bloco de status), com share de outra GPU num log compartilhado.
    (
        "tail_without_connect_line_shares_only",
        1,
        True,
        "[2026-11-10 23:50:00] GPU0 X: 70.00 TH/s [A:5 R:0 HW:0]\n[2026-11-10 23:50:00] GPU1 Y: 71.00 TH/s [A:6 R:0 HW:0]\n"
        "[2026-11-10 23:51:00] GPU0[t0] share accepted [  214ms] [pearlhash][0]\n",
        "2026-11-10 23:55:00",
    ),
    # Hashrate sozinho não prova conexão; status sem duração também não; idade de job enorme é ignorada.
    (
        "tail_hashrate_without_pool_evidence",
        0,
        True,
        "[2026-11-10 23:53:08] GPU0 X: 68.10 TH/s [A:440 R:3 HW:0]\n[2026-11-10 23:53:10] Connection: -\n"
        "[2026-11-10 23:53:10] Last job: 12345678901 sec ago\n",
        "2026-11-10 23:55:00",
    ),
    # Queda depois do último share vence; share depois da queda reconecta; "Last job" mais velho
    # que um job já visto não volta o relógio.
    (
        "tail_drop_after_share_then_back",
        0,
        True,
        "[2026-11-10 23:50:00] GPU0 X: 70.00 TH/s [A:5 R:0 HW:0]\n[2026-11-10 23:50:10] GPU0[t0] share accepted\n"
        "[2026-11-10 23:50:20] Last job: 5 sec ago\n[2026-11-10 23:50:30] Last job: 40 sec ago\n"
        "[2026-11-10 23:51:00] Reconnecting to prl.kryptex.network:7048 in 5 seconds [0]\n",
        "2026-11-10 23:55:00",
    ),
    (
        "tail_drop_then_share_reconnects",
        0,
        True,
        "[2026-11-10 23:50:00] GPU0 X: 70.00 TH/s [A:5 R:0 HW:0]\n"
        "[2026-11-10 23:51:00] Reconnecting to prl.kryptex.network:7048 in 5 seconds [0]\n"
        "[2026-11-10 23:51:30] GPU0[t0] share accepted\n",
        "2026-11-10 23:55:00",
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
            if "archive" in op:
                store.telemetry(op["data"], op.get("source", "live"), archive=op["archive"])
            else:
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
        # archive=False (GPU0 já gravada em gpu_telemetry): sem linha na tabela legada, mas o
        # status_changed continua; status repetido não gera evento; sem collected_at é rejeitada.
        {
            "op": "telemetry",
            "archive": False,
            "data": {
                "collected_at": 1794100019.5,
                "gpu": {"temperature.gpu": 72},
                "miner": {
                    "paused": False,
                    "connection": "perdida",
                    "recent_logs": "[2026-11-08 20:00:19] sem arquivo\n",
                },
                "miner_running": True,
                "log_path": "logs/mining-1.log",
            },
        },
        {
            "op": "telemetry",
            "archive": False,
            "data": {
                "collected_at": 1794100021.5,
                "miner": {"paused": False, "connection": "perdida"},
                "miner_running": True,
                "log_path": "logs/mining-1.log",
            },
        },
        {"op": "telemetry", "archive": False, "data": {"miner": {"connection": "x"}}},
        {"op": "telemetry", "archive": True, "data": {"collected_at": 1794100022.0, "miner": {"connection": "ok"}}},
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


def catalog_cases():
    import miner_update

    unknown = None
    try:
        miner_update.descriptor("9.9.9")
    except ValueError as error:
        unknown = str(error)
    return [
        {
            "kind": "catalog",
            "catalog": miner_update.CATALOG,
            "default_version": miner_update.DEFAULT_VERSION,
            "descriptors": {version: miner_update.descriptor(version) for version in miner_update.CATALOG},
            "unknown": unknown,
        }
    ]


# --- gpu_defaults: restauração pelo NVML com um driver falso (só o `call` é trocado) --------

NVML_CONFIGS = [
    {"name": "tudo_restaurado"},
    {"name": "falha_ao_zerar_offset", "fail": ["nvmlDeviceSetClockOffsets"]},
    {"name": "falha_na_ventoinha", "fail": ["nvmlDeviceSetDefaultFanSpeed_v2"]},
    {"name": "offset_sem_confirmacao", "ignore": ["nvmlDeviceSetClockOffsets"]},
    {"name": "potencia_sem_confirmacao", "ignore": ["nvmlDeviceSetPowerManagementLimit"]},
    {"name": "ventoinha_presa_em_manual", "stuck_policy": 1},
    {"name": "ventoinha_ignora_pedido", "ignore": ["nvmlDeviceSetDefaultFanSpeed_v2"]},
    {"name": "offsets_ja_zerados", "offsets": {}},
    {"name": "so_memoria_no_p2", "offsets": {"2,2": 500}},
    {"name": "sem_pstates", "states": [32] * 16},
    {"name": "pstates_repetidos_e_invalidos", "states": [8, 2, 2, 0, -1, 20, 32, 15, 16]},
    {"name": "ventoinhas_demais", "fans": 17},
    {"name": "dezesseis_ventoinhas", "fans": 16},
    {"name": "sem_ventoinha", "fans": 0},
    {"name": "limite_padrao_baixo", "default": 999},
    {"name": "limite_padrao_minimo", "default": 1000, "power": 1000},
    {"name": "limite_padrao_alto", "default": 2000001},
    {"name": "limite_padrao_maximo", "default": 2000000},
    {"name": "zero_fora_dos_limites", "limits": [10, 2000]},
    {"name": "zero_no_limite", "limits": [0, 0], "offsets": {"0,0": 5}},
    {"name": "funcao_ausente", "missing": ["nvmlDeviceResetMemoryLockedClocks"]},
    {"name": "leitura_de_ventoinhas_falha", "fail": ["nvmlDeviceGetNumFans"]},
    {"name": "leitura_de_offset_falha", "fail": ["nvmlDeviceGetClockOffsets"]},
    {"name": "leitura_de_pstates_falha", "fail": ["nvmlDeviceGetSupportedPerformanceStates"]},
    {"name": "destravar_clock_falha", "fail": ["nvmlDeviceResetGpuLockedClocks"]},
    {"name": "potencia_ja_de_fabrica", "power": 240000},
    {
        "name": "tudo_falha",
        "fail": [
            "nvmlDeviceGetNumFans",
            "nvmlDeviceGetSupportedPerformanceStates",
            "nvmlDeviceResetGpuLockedClocks",
            "nvmlDeviceResetMemoryLockedClocks",
            "nvmlDeviceGetPowerManagementDefaultLimit",
        ],
    },
]


def nvml_cases():
    import ctypes as c

    import gpu_defaults

    class GoldenNvml(gpu_defaults.NvidiaDefaults):
        def __init__(self, config):
            self.handle = c.c_void_p(123)
            self.config = config
            self.calls = []
            self.fans = config.get("fans", 2)
            self.policies = {index: 1 for index in range(self.fans)}
            default_offsets = {
                f"{domain},{state}": 150 if domain == 0 else 700 for state in (0, 2, 8) for domain in (0, 2)
            }
            self.offsets = {
                tuple(map(int, key.split(","))): value for key, value in config.get("offsets", default_offsets).items()
            }
            self.limits = config.get("limits", [-1000, 2000])
            self.default = config.get("default", 240000)
            self.power = config.get("power", 170000)

        def call(self, name, types, *args):
            self.calls.append(name)
            if name in self.config.get("fail", []):
                raise gpu_defaults.NvmlError(name, 4)
            if name in self.config.get("missing", []):
                raise gpu_defaults.NvmlError(name, "API indisponível")
            ignore = name in self.config.get("ignore", [])
            if name == "nvmlDeviceGetNumFans":
                args[1]._obj.value = self.fans
            elif name == "nvmlDeviceGetFanControlPolicy_v2":
                args[2]._obj.value = self.config.get("stuck_policy", self.policies.get(args[1], 1))
            elif name == "nvmlDeviceSetDefaultFanSpeed_v2":
                if not ignore:
                    self.policies[args[1]] = 0
            elif name == "nvmlDeviceGetSupportedPerformanceStates":
                for position, value in enumerate(self.config.get("states", [0, 2, 8])[:16]):
                    args[1][position] = value
            elif name == "nvmlDeviceGetClockOffsets":
                offset = args[1]._obj
                offset.offset = self.offsets.get((offset.type, offset.pstate), 0)
                offset.minimum, offset.maximum = self.limits
            elif name == "nvmlDeviceSetClockOffsets":
                if not ignore:
                    offset = args[1]._obj
                    self.offsets[offset.type, offset.pstate] = offset.offset
            elif name == "nvmlDeviceGetPowerManagementDefaultLimit":
                args[1]._obj.value = self.default
            elif name == "nvmlDeviceGetPowerManagementLimit":
                args[1]._obj.value = self.power
            elif name == "nvmlDeviceSetPowerManagementLimit":
                if not ignore:
                    self.power = args[1]
            elif name not in ("nvmlDeviceResetGpuLockedClocks", "nvmlDeviceResetMemoryLockedClocks"):
                raise AssertionError(name)

    cases = []
    for setup in NVML_CONFIGS:
        api = GoldenNvml(setup)
        report = api.restore()
        report["at"] = 0.0
        cases.append({"kind": "nvml", "config": setup, "report": report, "calls": api.calls})
    return cases


# --- gpu_control: a trava de controle por GPU (`NvidiaActuator`), com trava real -------------

GPU_CONTROL_SCENARIOS = {
    "vazio": {"owner": None, "steps": ["acquire", "assert", "assert_other", "close"]},
    "dono_v2_antigo": {
        "owner": '{"pid": 1, "token": "x", "gpu_uuid": "GPU-a", "lease_version": 2}',
        "steps": ["acquire", "assert", "close"],
    },
    "versao_float": {"owner": '{"pid": "{self}", "lease_version": 2.0}', "steps": ["acquire", "close"]},
    "legado_pid_morto": {"owner": '{"pid": 4294967290}', "steps": ["acquire", "close"]},
    "legado_pid_vivo": {"owner": '{"pid": "{self}"}', "steps": ["acquire", "close"]},
    "legado_sem_pid": {"owner": '{"active": true}', "steps": ["acquire", "close"]},
    "legado_pid_bool": {"owner": '{"pid": true}', "steps": ["acquire"]},
    "legado_pid_negativo": {"owner": '{"pid": -5}', "steps": ["acquire"]},
    "legado_pid_float": {"owner": '{"pid": 1.5}', "steps": ["acquire"]},
    "legado_pid_grande": {"owner": '{"pid": 4294967296}', "steps": ["acquire"]},
    "corrompido": {"owner": "{", "steps": ["acquire", "close"]},
    "lista": {"owner": "[1]", "steps": ["acquire", "acquire"]},
    "trava_ocupada": {"owner": None, "hold": True, "steps": ["acquire", "assert"]},
    "posse_substituida": {"owner": None, "steps": ["acquire", "replace", "assert", "close"]},
    "posse_apagada": {"owner": None, "steps": ["acquire", "delete", "assert", "close"]},
    "duas_vezes": {"owner": None, "steps": ["acquire", "acquire", "close", "acquire", "close"]},
    "sem_posse": {"owner": None, "steps": ["assert", "close"]},
}


def gpu_control_golden():
    import gpu_agent

    cases = []
    for name, scenario in GPU_CONTROL_SCENARIOS.items():
        folder = Path(tempfile.mkdtemp()).resolve()
        holder = None
        try:
            owner_path = folder / "gpu-control-owner.json"
            if scenario["owner"] is not None:
                owner_path.write_text(scenario["owner"].replace('"{self}"', str(os.getpid())), encoding="utf-8")
            if scenario.get("hold"):
                holder = gpu_agent.NvidiaActuator(folder, enabled=True)
                holder._lock()
            actuator = gpu_agent.NvidiaActuator(folder, enabled=True)
            records = []
            for step in scenario["steps"]:
                record = {"op": step}
                try:
                    if step == "acquire":
                        actuator.acquire("GPU-a")
                    elif step == "assert":
                        actuator.assert_owner("GPU-a")
                    elif step == "assert_other":
                        actuator.assert_owner("GPU-b")
                    elif step == "replace":
                        owner_path.write_text('{"token": "outro", "gpu_uuid": "GPU-a"}', encoding="utf-8")
                    elif step == "delete":
                        owner_path.unlink()
                    elif step == "close":
                        actuator.close()
                except (OSError, RuntimeError, ValueError) as error:
                    record["error"] = str(error)
                try:
                    content = json.loads(owner_path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    content = "ausente"
                except ValueError:
                    content = {"text": owner_path.read_text(encoding="utf-8")}
                if isinstance(content, dict) and content.get("token") == actuator.token:
                    content = {**content, "token": "{token}", "pid": "{pid}", "at": "{at}"}
                elif isinstance(content, dict) and content.get("pid") == os.getpid():
                    content = {**content, "pid": "{self}"}
                record["owner"] = content
                record["files"] = sorted(item.name for item in folder.iterdir())
                records.append(record)
            actuator.close()
            cases.append({"name": name, "scenario": scenario, "records": records})
        finally:
            if holder is not None:
                holder._unlock()
            import shutil

            shutil.rmtree(folder, ignore_errors=True)
    write("gpu_control", {"cases": cases})


# --- nvidia_detect: detect_nvidia_gpus e read_gpu_samples com um nvidia-smi simulado ----------

LIST_QUERY = "--query-gpu=index,name,uuid,pci.bus_id --format=csv,noheader,nounits"
CAPS_FIELDS = "driver_version,serial,pci.device_id,pci.sub_device_id,vbios_version,compute_cap,power.min_limit,power.max_limit,power.default_limit,power.limit,clocks.max.graphics,memory.total,temperature.gpu.tlimit"
SAMPLE_QUERY = "--query-gpu=uuid,index,name,driver_version,temperature.gpu,power.draw,power.limit,clocks.gr,clocks.mem,utilization.gpu,utilization.memory,memory.used,memory.total,fan.speed,pstate --format=csv,noheader,nounits"


def caps_key(uuid):
    return f"-i {uuid} --query-gpu={CAPS_FIELDS} --format=csv,noheader,nounits"


def clocks_key(uuid):
    return f"-i {uuid} --query-supported-clocks=graphics --format=csv,noheader,nounits"


def field_key(uuid, field):
    return f"-i {uuid} --query-gpu={field} --format=csv,noheader,nounits"


A = "GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75"
B = "GPU-11111111-2222-3333-4444-555555555555"
CAPS_A = (
    "566.36, 1324021012345, 0x248410DE, 0x147E10DE, 94.04.25.00.4B, 8.6, 100.00, 240.00, 240.00, 240.00, 2100, 8192, 93"
)
CAPS_B = (
    "566.36, [N/A], 0x1F0810DE, [Not Supported], 90.06.18.00.98, 7.5, 125.00, 200.00, 175.00, 175.00, 2100, 8192, N/A"
)
CLOCKS = "2100\n2085\n1995\n210\n2085\n0\n-15\n1500.5\nabc"

DETECT_SCENARIOS = {
    "duas_gpus": {
        "script": {
            LIST_QUERY: "0, NVIDIA GeForce RTX 3070, "
            + A
            + ", 00000000:01:00.0\n1, NVIDIA GeForce RTX 2060, "
            + B
            + ", 00000000:02:00.0",
            caps_key(A): CAPS_A,
            clocks_key(A): CLOCKS,
            caps_key(B): CAPS_B,
            clocks_key(B): "1800\n1500",
        },
        "steps": [{"now": 1000.0}],
    },
    "consulta_campo_a_campo": {
        "script": {
            LIST_QUERY: "0, GeForce RTX 3070, " + A + ", 00000000:01:00.0",
            caps_key(A): "566.36, 1, 2",
            field_key(A, "driver_version"): "566.36",
            field_key(A, "pci.device_id"): "0x248810DE",
            field_key(A, "power.limit"): " 220.00 ",
            field_key(A, "memory.total"): "8192\n8192",
            field_key(A, "serial"): "[N/A]",
            clocks_key(A): "1800",
        },
        "steps": [{"now": 5.0}],
    },
    "caps_com_erro": {"script": {LIST_QUERY: "0, RTX 3070, " + A + ", 00000000:01:00.0"}, "steps": [{"now": 5.0}]},
    "pci_divergente": {
        "script": {
            LIST_QUERY: "0, NVIDIA   geforce rtx 3070 , " + A + ", 00000000:01:00.0",
            caps_key(A): CAPS_A.replace("0x248410DE", "0x9999"),
            clocks_key(A): "1800",
        },
        "steps": [{"now": 5.0}],
    },
    "nome_desconhecido": {
        "script": {
            LIST_QUERY: "0, Placa Misteriosa X, " + A + ", 00000000:01:00.0",
            caps_key(A): CAPS_A,
            clocks_key(A): "",
        },
        "steps": [{"now": 5.0}],
    },
    "linhas_invalidas": {
        "script": {
            LIST_QUERY: "\n0, RTX 3070, "
            + A
            + "\nx, RTX 3070, "
            + A
            + ", p\n2, RTX 3070, NAO-GPU, p\n"
            + '3, "NVIDIA GeForce RTX 3070, edição", '
            + B
            + ", 00000000:03:00.0\n"
            + '4,"NVIDIA GeForce RTX 3070, edi""cao""",'
            + B
            + ",00000000:04:00.0\n\r\n",
            caps_key(B): CAPS_B,
            clocks_key(B): "1800",
        },
        "steps": [{"now": 5.0}],
    },
    "limite_de_placas": {
        "script": {
            LIST_QUERY: "0, RTX 3070, " + A + ", p0\n1, RTX 2060, " + B + ", p1",
            caps_key(A): CAPS_A,
            clocks_key(A): "1",
            caps_key(B): CAPS_B,
            clocks_key(B): "1",
        },
        "steps": [{"now": 5.0, "max_devices": 1}, {"now": 5.0, "max_devices": 0}, {"now": 5.0, "max_devices": -3}],
    },
    "cache": {
        "cache": True,
        "script": {LIST_QUERY: "0, RTX 3070, " + A + ", p0", caps_key(A): CAPS_A, clocks_key(A): "1800"},
        "steps": [{"now": 1000.0}, {"now": 1100.0}, {"now": 1599.9}, {"now": 1600.0}, {"now": 1590.0}, {"now": 2300.0}],
    },
    "uuid_vazio": {
        "script": {LIST_QUERY: "1, RTX 3070, GPU-, p", caps_key("GPU-"): CAPS_A, clocks_key("GPU-"): ""},
        "steps": [{"now": 5.0}],
    },
    "uuid_invalido": {
        "script": {
            LIST_QUERY: "0, RTX 3070, GPU-abc_def, p0",
            caps_key("GPU-abc_def"): CAPS_A,
            clocks_key("GPU-abc_def"): "",
        },
        "steps": [{"now": 5.0}],
    },
    "lista_falha": {"script": {}, "steps": [{"now": 5.0}]},
}

DEV_A = {"vendor": "NVIDIA", "uuid": A, "index": 0, "name": "NVIDIA GeForce RTX 3070"}
DEV_B = {"vendor": "NVIDIA", "uuid": B, "index": 1, "name": "RTX 2060"}
ROW_A = A + ", 0, NVIDIA GeForce RTX 3070, 566.36, 76, 177.10, 240.00, 1695, 7001, 100, 85, 6400, 8192, 95, P2"
SAMPLE_SCENARIOS = {
    "normal": {
        "devices": [DEV_A, DEV_B],
        "output": ROW_A
        + "\n"
        + B
        + ", 1, RTX 2060, 566.36, 60, 90.5, 175.00, 1500, 7000, 99, 80, 5000, 6144, [N/A], P2",
    },
    "uma_ausente_e_estranha": {
        "devices": [DEV_A, DEV_B],
        "output": ROW_A + "\nGPU-outra, 2, X, 1, 50, 1, 1, 1, 1, 1, 1, 1, 1, 1, P0",
    },
    "indice_trocado_e_invalido": {
        "devices": [DEV_A, DEV_B],
        "output": A
        + ", 3, NVIDIA GeForce RTX 3070, 566.36, 70, 1, 1, 1, 1, 1, 1, 1, 1, 1, P0\n"
        + B
        + ", x, RTX, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, P0",
    },
    "temperatura_invalida": {
        "devices": [DEV_A, DEV_B],
        "output": A
        + ", 0, N, 1, [N/A], 1, 1, 1, 1, 1, 1, 1, 1, 1, P0\n"
        + B
        + ", 1, N, 1, 121, 1, 1, 1, 1, 1, 1, 1, 1, 1, P0",
    },
    "linha_repetida_e_curta": {
        "devices": [DEV_A],
        "output": ROW_A + "\n" + ROW_A.replace(", 76,", ", 80,") + "\n" + A + ", 0, curta",
    },
    "comando_falha": {"devices": [DEV_A, DEV_B], "error": "x" * 300},
    "sem_placas": {"devices": []},
    "placa_invalida": {"devices": [{"vendor": "AMD", "uuid": A, "index": 0, "name": "x"}]},
}


def nvidia_detect_golden():
    import gpu_agent
    import gpu_telemetry

    def fake(script, calls):
        def command(arguments, timeout=4):
            key = " ".join(map(str, arguments))
            calls.append(list(map(str, arguments)))
            if key not in script:
                raise RuntimeError("sem resposta para " + key[:60])
            value = script[key]
            if isinstance(value, dict):
                raise RuntimeError(value["error"])
            return value

        return command

    cases = []
    for name, scenario in DETECT_SCENARIOS.items():
        cache = {} if scenario.get("cache") else None
        records = []
        for step in scenario["steps"]:
            calls = []
            record = {**step}
            try:
                record["result"] = gpu_agent.detect_nvidia_gpus(
                    fake(scenario["script"], calls), max_devices=step.get("max_devices"), cache=cache, now=step["now"]
                )
            except (OSError, RuntimeError, ValueError) as error:
                record["error"] = str(error)
            record["calls"] = calls
            records.append(record)
        cases.append(
            {
                "kind": "detect",
                "name": name,
                "cache": bool(scenario.get("cache")),
                "script": scenario["script"],
                "steps": records,
            }
        )
    for name, scenario in SAMPLE_SCENARIOS.items():
        calls = []
        script = {SAMPLE_QUERY: {"error": scenario["error"]} if "error" in scenario else scenario.get("output", "")}
        record = {"kind": "samples", "name": name, "devices": scenario["devices"], "script": script, "now": 1234.5}
        try:
            record["result"] = gpu_telemetry.read_gpu_samples(
                scenario["devices"], command=fake(script, calls), now=1234.5
            )
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            record["error"] = str(error)
        record["calls"] = calls
        cases.append(record)
    write("nvidia_detect", {"cases": cases})


# --- thermal_target: traços do controlador por alvo e do explorador de memória ---------------


def finite_or_text(value):
    """JSON sem infinito: ±inf viram texto."""
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return "inf" if value > 0 else "-inf"
    return value


CONTROLLER_SCENARIOS = [
    {"name": "alvo_72_placa_quente", "seed": 1, "target": 72.0, "ambient": 4.0, "plateau": 1800, "steps": 3000},
    {"name": "alvo_78_plato_de_hashrate", "seed": 2, "target": 78.0, "ambient": 0.0, "plateau": 1650, "steps": 3000},
    {
        "name": "alvo_65_ventoinha_fraca",
        "seed": 3,
        "target": 65.0,
        "ambient": 2.0,
        "plateau": 2000,
        "steps": 2500,
        "fan_cap": 80.0,
    },
    {
        "name": "sensores_falhando",
        "seed": 4,
        "target": 75.0,
        "ambient": 1.0,
        "plateau": 1750,
        "steps": 2000,
        "missing": 0.08,
    },
    {
        "name": "meio_grau_empates",
        "seed": 5,
        "target": 74.5,
        "ambient": 3.0,
        "plateau": 1720,
        "steps": 2000,
        "half": True,
    },
]


def controller_trace(scenario):
    import random

    import thermal_target as tt

    rng = random.Random(scenario["seed"])
    controller = tt.TargetTemperatureController(target_c=scenario["target"])
    clock, temp, now = 1635.0, 60.0, 1000.0
    ops = []
    reasons = []
    for _ in range(scenario["steps"]):
        now += 2.0
        equilibrium = 40 + (clock - 1200) * 0.075 + scenario["ambient"]
        temp += 0.12 * (equilibrium - temp) + rng.uniform(-0.4, 0.4)
        fan = min(scenario.get("fan_cap", 100.0), max(30.0, round(30 + (temp - 50) * 3.2)))
        rate = round(min(clock, scenario["plateau"]) * 0.046 + rng.uniform(-0.05, 0.05), 3)
        sample_temp = round(temp * 2) / 2 if scenario.get("half") else round(temp, 2)
        sample = [sample_temp, float(fan), clock, now, rate]
        if rng.random() < scenario.get("missing", 0.0):
            sample[rng.choice([0, 1, 2, 4])] = None
        decision = controller.step(*sample)
        if decision.reason not in reasons:
            reasons.append(decision.reason)
        ops.append(
            {"op": "step", "in": sample, "out": [decision.action, reasons.index(decision.reason), decision.ceiling_mhz]}
        )
        if decision.action in ("clock_up", "clock_down") and sample[2] is not None:
            before = clock
            clock = clock + 15.0 if decision.action == "clock_up" else max(1200.0, clock - 15.0)
            rate_before = controller.rates.get(round(before))
            controller.notify_applied(decision.action, before, clock, now, rate_before=rate_before)
            ops.append({"op": "notify", "in": [decision.action, before, clock, now, rate_before]})
    state = {
        "last_down_at": finite_or_text(controller.last_down_at),
        "last_up_at": finite_or_text(controller.last_up_at),
        "headroom_since": controller.headroom_since,
        "ceiling_mhz": controller.ceiling_mhz,
        "ceiling_until": finite_or_text(controller.ceiling_until),
        "pending_up": controller.pending_up,
        "rates": {str(key): value for key, value in sorted(controller.rates.items())},
    }
    return {
        "kind": "controller",
        "name": scenario["name"],
        "target": scenario["target"],
        "reasons": reasons,
        "ops": ops,
        "state": state,
    }


EXPLORER_SCENARIOS = [
    {"name": "rtx3070_p2", "seed": 11, "candidates": [7001, 6801, 5001, 810, 405, 6801, 0, -5], "steps": 3000},
    {
        "name": "lucro",
        "seed": 12,
        "candidates": [7001, 6801, 5001, 810],
        "steps": 2600,
        "objective": "profit",
        "revenue": 1.9,
        "price": 0.95,
    },
    {
        "name": "erros_e_quente",
        "seed": 13,
        "candidates": [6801, 5001, 810, 405],
        "steps": 2600,
        "errors_at": 405,
        "hot_at": 6801,
    },
    {"name": "um_candidato", "seed": 14, "candidates": [810], "steps": 200},
    {"name": "tudo_inalcancavel", "seed": 15, "candidates": [9001, 8001], "steps": 600, "cap": 6801},
]


def explorer_trace(scenario):
    import random

    import thermal_target as tt

    rng = random.Random(scenario["seed"])
    explorer = tt.MemoryExplorer(candidates=scenario["candidates"], target_c=76.0)
    if scenario.get("objective"):
        explorer.objective = scenario["objective"]
        explorer.revenue_per_ths_day = scenario["revenue"]
        explorer.price_kwh = scenario["price"]
    applied, now, errors = None, 5000.0, 0
    rows = []
    for _ in range(scenario["steps"]):
        now += 10.0
        cap = scenario.get("cap", 6801.0)
        actual = None if applied is None else min(applied, cap)
        memory = actual or 6801.0
        rate = round(75.2 + (memory - 810) * 0.00002 + rng.uniform(-0.15, 0.15), 3)
        watts = round(150 + memory * 0.0018 + rng.uniform(-1, 1), 2)
        temp = round(74.5 + rng.uniform(-0.8, 0.8) + (3.0 if applied == scenario.get("hot_at") else 0.0), 2)
        if applied == scenario.get("errors_at") and rng.random() < 0.05:
            errors += 1
        hashrate = None if rng.random() < 0.03 else rate
        sample = [now, temp, hashrate, float(errors), actual, watts]
        out = explorer.step(*sample)
        rows.append(sample + [out])
        if out is not None:
            applied = out
    state = {
        "best_mhz": explorer.best_mhz,
        "scores": [[clock, score] for clock, score in explorer.scores.items()],
        "queue": explorer.queue,
        "current_mhz": explorer.current_mhz,
        "unreachable": sorted(explorer.unreachable),
        "power": {str(clock): watts for clock, watts in explorer.power.items()},
        "explored_at": finite_or_text(explorer.explored_at),
        "candidates": explorer.candidates,
    }
    return {"kind": "explorer", "name": scenario["name"], "scenario": scenario, "rows": rows, "state": state}


# Fronteiras que os traços naturais quase nunca tocam: cada roteiro é executado pelo Python real.
CONTROLLER_SCRIPTS = {
    "arredondamento_par": (
        75.0,
        [
            ("step", 70.0, 60.0, 1702.5, 0.0, 78.0),
            ("step", 70.0, 60.0, 1703.5, 2.0, 79.0),
            ("step", 70.0, 60.0, 1702.5, 4.0, 80.0),
        ],
    ),
    "ventoinha_em_95": (75.0, [("step", 76.0, 95.0, 1700.0, 0.0, 78.0), ("step", 76.0, 94.9, 1700.0, 10.0, 78.0)]),
    "estouro_fora_da_janela": (
        75.0,
        [
            ("notify", "clock_up", 1700.0, 1715.0, 0.0, 78.0),
            ("step", 76.0, 60.0, 1715.0, 91.0, 79.0),
            ("step", 76.0, 60.0, 1715.0, 93.0, 79.0),
        ],
    ),
    "estouro_dentro_da_janela": (
        75.0,
        [("notify", "clock_up", 1700.0, 1715.0, 0.0, 78.0), ("step", 76.0, 60.0, 1715.0, 90.0, 79.0)],
    ),
    "dois_tetos_vale_o_menor": (
        75.0,
        [
            ("notify", "clock_up", 1700.0, 1715.0, 0.0, 78.0),
            ("step", 76.0, 60.0, 1715.0, 2.0, 79.0),
            ("notify", "clock_up", 1650.0, 1665.0, 10.0, 78.0),
            ("step", 76.0, 60.0, 1665.0, 12.0, 79.0),
            ("notify", "clock_up", 1750.0, 1765.0, 20.0, 78.0),
            ("step", 76.0, 60.0, 1765.0, 22.0, 79.0),
            ("step", 70.0, 60.0, 1700.0, 40.0, 79.0),
        ],
    ),
    "folga_reinicia_no_notify": (
        75.0,
        [
            ("step", 70.0, 60.0, 1700.0, 100.0, 78.0),
            ("notify", "power_down", 1700.0, 1700.0, 115.0, None),
            ("step", 70.0, 60.0, 1700.0, 125.0, 78.0),
            ("step", 70.0, 60.0, 1700.0, 140.0, 78.0),
            ("step", 70.0, 60.0, 1700.0, 146.0, 78.0),
        ],
    ),
    "subida_espera_a_descida": (
        75.0,
        [
            ("notify", "clock_down", 1715.0, 1700.0, 100.0, None),
            ("step", 70.0, 60.0, 1700.0, 101.0, 78.0),
            ("step", 70.0, 60.0, 1700.0, 125.0, 78.0),
            ("step", 70.0, 60.0, 1700.0, 131.0, 78.0),
        ],
    ),
    "ganho_no_limite": (
        75.0,
        [
            ("step", 70.0, 60.0, 1700.0, 0.0, 100.0),
            ("notify", "clock_up", 1700.0, 1715.0, 1.0, 100.0),
            ("step", 70.0, 60.0, 1715.0, 2.0, 100.3),
            ("step", 70.0, 60.0, 1715.0, 46.0, 100.3),
        ],
    ),
}

EXPLORER_SCRIPTS = {
    "empate_a_0_4": {
        "candidates": [6801, 810],
        "rate": {6801: [100.0], 810: [99.6]},
        "power": {6801: 152.0, 810: 140.0},
        "steps": 140,
    },
    "sem_empate_a_0_6": {
        "candidates": [6801, 810],
        "rate": {6801: [100.0], 810: [99.4]},
        "power": {6801: 152.0, 810: 140.0},
        "steps": 140,
    },
    "duas_amostras": {
        "candidates": [6801, 810],
        "rate": {6801: [100.0], 810: [None] * 49 + [101.0, 101.0] + [None] * 200},
        "power": {6801: 152.0, 810: 140.0},
        "steps": 140,
    },
    "soma_compensada": {
        "candidates": [6801, 810],
        "rate": {6801: [None] * 49 + [1e16, 1.0, 1.0, 1.0] + [None] * 200, 810: [2500000000000000.5]},
        "power": {6801: 150.0, 810: 150.0},
        "steps": 140,
        "objective": "profit",
        "revenue": 1.0,
        "price": 0.0,
    },
    "melhor_primeiro_na_reexploracao": {
        "candidates": [7001, 6801, 810],
        "rate": {7001: [99.0], 6801: [100.0], 810: [98.0]},
        "power": {7001: 160.0, 6801: 152.0, 810: 140.0},
        "steps": 2400,
    },
}


def controller_script(name, target, script):
    import thermal_target as tt

    controller = tt.TargetTemperatureController(target_c=target)
    ops, reasons = [], []
    for item in script:
        if item[0] == "notify":
            controller.notify_applied(item[1], item[2], item[3], item[4], rate_before=item[5])
            ops.append({"op": "notify", "in": list(item[1:])})
            continue
        decision = controller.step(*item[1:])
        if decision.reason not in reasons:
            reasons.append(decision.reason)
        ops.append(
            {
                "op": "step",
                "in": list(item[1:]),
                "out": [decision.action, reasons.index(decision.reason), decision.ceiling_mhz],
            }
        )
    state = {
        "last_down_at": finite_or_text(controller.last_down_at),
        "last_up_at": finite_or_text(controller.last_up_at),
        "headroom_since": controller.headroom_since,
        "ceiling_mhz": controller.ceiling_mhz,
        "ceiling_until": finite_or_text(controller.ceiling_until),
        "pending_up": controller.pending_up,
        "rates": {str(key): value for key, value in sorted(controller.rates.items())},
    }
    return {"kind": "controller", "name": name, "target": target, "reasons": reasons, "ops": ops, "state": state}


def explorer_script(name, scenario):
    import thermal_target as tt

    explorer = tt.MemoryExplorer(candidates=scenario["candidates"], target_c=76.0)
    if scenario.get("objective"):
        explorer.objective = scenario["objective"]
        explorer.revenue_per_ths_day = scenario["revenue"]
        explorer.price_kwh = scenario["price"]
    rows, now, since = [], 0.0, 0
    for _ in range(scenario["steps"]):
        now += 10.0
        current = explorer.current_mhz
        series = scenario["rate"].get(int(current), [None]) if current is not None else [None]
        rate = series[since % len(series)] if len(series) > 1 else series[0]
        if len(series) > 1 and since >= len(series):
            rate = None
        power = scenario["power"].get(int(current)) if current is not None else None
        sample = [now, 75.0, rate, 0.0, current, power]
        out = explorer.step(*sample)
        rows.append(sample + [out])
        since = 0 if out is not None else since + 1
    state = {
        "best_mhz": explorer.best_mhz,
        "scores": [[clock, score] for clock, score in explorer.scores.items()],
        "queue": explorer.queue,
        "current_mhz": explorer.current_mhz,
        "unreachable": sorted(explorer.unreachable),
        "power": {str(clock): watts for clock, watts in explorer.power.items()},
        "explored_at": finite_or_text(explorer.explored_at),
        "candidates": explorer.candidates,
    }
    return {"kind": "explorer", "name": name, "scenario": scenario, "rows": rows, "state": state}


def thermal_target_golden():
    import thermal_target as tt

    cases = [controller_trace(scenario) for scenario in CONTROLLER_SCENARIOS]
    cases += [explorer_trace(scenario) for scenario in EXPLORER_SCENARIOS]
    cases += [controller_script(name, target, script) for name, (target, script) in CONTROLLER_SCRIPTS.items()]
    cases += [explorer_script(name, scenario) for name, scenario in EXPLORER_SCRIPTS.items()]
    clamps = []
    for value, limit in [
        (80, 93),
        (99, 93),
        (10, 93),
        ("77.5", 90),
        (None, 90),
        ("abc", 90),
        (float("nan"), 90),
        (60, 40),
    ]:
        clamps.append(
            {
                "in": [finite_or_text(value) if not isinstance(value, float) or value == value else "nan", limit],
                "out": tt.clamp_target(value, limit),
            }
        )
    cases.append({"kind": "clamp", "name": "clamp_target", "cases": clamps})
    write("thermal_target", {"cases": cases})


def rig_support_golden():
    cases = (
        health_cases()
        + operations_case()
        + continuity_cases()
        + reference_cases()
        + indent_cases()
        + catalog_cases()
        + nvml_cases()
    )
    write("rig_support", {"cases": cases})


# --- rig_manager: cenários do RigManager com o backend falso do test_rig.py -----------------
# Cada cenário é uma lista de passos em dados (a mesma linguagem que o teste Rust interpreta).
# Depois de cada passo grava o resultado, o registro de GPUs, o estado do gerenciador e o que
# o backend fez; no fim, os arquivos gravados e o operations.sqlite3.

RIG_CLOCK = 1000.5
LOG_EPOCH = 1793000000.0
RIG_OTHER = "prl1" + "q" * 40


def rig_detected(count=6, mixed=True):
    return [
        {
            "vendor": "NVIDIA",
            "uuid": f"GPU-test-{index}",
            "index": index,
            "name": ("NVIDIA GeForce RTX 3070" if not mixed or index % 2 == 0 else "NVIDIA GeForce GTX 1660 SUPER"),
        }
        for index in range(count)
    ]


def rig_reference_gpu():
    import reference_profile

    return {
        **rig_detected(1)[0],
        "uuid": reference_profile.REFERENCE_RTX3070_UUID,
        "power_default_w": 240,
        "supported_graphics_clocks_mhz": [1500, 1560, 1740, 1755],
    }


def rig_scenarios():
    from contribution import AUTHOR_WALLET

    author = {"wallet": AUTHOR_WALLET}
    ref = rig_reference_gpu()
    legacy = [
        {
            "op": "backend_start",
            "args": ["{miner}", "--gpu-id", "0", "--wallet", AUTHOR_WALLET, "--log-file", "{root}\\legacy.log"],
        },
        {
            "op": "backend_start",
            "args": ["{root}\\python.exe", "{log_parent}\\thermal_controller.py", "--miner-pid", "101"],
        },
    ]
    supervisor = {"op": "backend_start", "args": ["{root}\\powershell.exe", "-File", "{log_parent}\\Iniciar-Pearl.ps1"]}
    migration = [
        {"op": "set_detector", "detected": [ref]},
        *legacy,
        supervisor,
        {"op": "set_legacy", "started": [0, 1, 2]},
    ]

    def request(file, **payload):
        return {"op": "write_request", "file": file, "payload": payload}

    def gpu_sample(temperature, sampled_at):
        return {"temperature.gpu": temperature, "sampled_at": sampled_at, "error": None}

    stalled_miner = {"hashrate": 0, "hashrate_at": None, "connection": None}
    install = [
        {"op": "install", "version": "3.8.0", "content": "binario antigo"},
        {"op": "install", "version": "3.8.1", "content": "binario novo"},
        {"op": "set_miner", "version": "3.8.0"},
    ]

    def log_stamp(offset):
        # Carimbo do log no fuso do gerador (TZ=UTC, que o Python respeita também no Windows);
        # o teste Rust lê o mesmo texto com StampZone::UTC.
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(LOG_EPOCH + offset))

    def update_snapshots(version, stamp):
        return {
            f"GPU-test-{index}": {
                "miner_running": True,
                "miner": {"version": version, "hashrate": 80, "hashrate_at": stamp},
            }
            for index in range(2)
        }

    other = rig_detected(2)[1]
    return {
        "seis_gpus": [{"op": "reconcile"}],
        "gpu_ocupada": [
            {"op": "backend", "set": {"blocked": "GPU already occupied"}},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "desligar_uma": [
            {"op": "reconcile"},
            {"op": "reconcile", "settings": {**author, "gpu_settings": {"GPU-test-2": {"enabled": False}}}},
        ],
        "carteira_por_gpu": [
            {"op": "reconcile"},
            {"op": "reconcile", "settings": {**author, "gpu_settings": {"GPU-test-3": {"wallet": RIG_OTHER}}}},
        ],
        "falha_isolada": [{"op": "backend", "set": {"fail_uuid": "GPU-test-3"}}, {"op": "reconcile"}],
        "reinicio_preserva": [{"op": "reconcile"}, {"op": "new_manager"}, {"op": "reconcile"}],
        "pid_reutilizado": [
            {"op": "reconcile"},
            {
                "op": "mutate",
                "target": ["device", "GPU-test-1", "miner"],
                "fields": {"created_at": "9999999", "argv": ["unrelated.exe"], "launch_id": "different"},
            },
            {"op": "new_manager"},
            {"op": "reconcile", "settings": {**author, "gpu_settings": {"GPU-test-1": {"enabled": False}}}},
        ],
        "linha_de_comando_alterada": [
            {"op": "reconcile"},
            {"op": "mutate", "target": ["device", "GPU-test-1", "miner"], "append_argv": "unexpected"},
            {"op": "reconcile"},
        ],
        "registro_corrompido": [
            {"op": "corrupt_registry", "text": "[1, 2]"},
            {"op": "new_manager"},
            {"op": "reconcile"},
        ],
        "registro_metadados": [
            {
                "op": "corrupt_registry",
                "text": json.dumps(
                    {
                        "schema_version": 1,
                        "marker": "HashPilotRig-v1",
                        "devices": {"GPU-test-0": {"gpu_uuid": "GPU-test-0", "miner": {"pid": 1}}},
                    }
                ),
            },
            {"op": "new_manager"},
            {"op": "reconcile"},
        ],
        "pedido_parar": [
            {"op": "reconcile"},
            request("r1.json", id="req-1", action="stop", gpu_uuid="GPU-test-4", at=RIG_CLOCK),
            {"op": "process_requests"},
            {"op": "reconcile"},
        ],
        "parar_e_iniciar_em_ordem": [
            {"op": "reconcile"},
            request("zzzz-stop.json", id="stop-first", action="stop", gpu_uuid="GPU-test-2", at=990.5),
            request("aaaa-start.json", id="start-second", action="start", gpu_uuid="GPU-test-2", at=991.5),
            {"op": "process_requests"},
            {"op": "reconcile"},
        ],
        "pedidos_diversos": [
            {"op": "reconcile", "detected": rig_detected(2)},
            request("a.json", id="b1", action="benchmark", gpu_uuid=None, at=990.0, objective="efficiency"),
            request("b.json", id="p1", action="profile", gpu_uuid="GPU-test-0", at=990.1, profile="cool"),
            request("c.json", id="p2", action="profile", gpu_uuid="GPU-test-1", at=990.2, profile="turbo"),
            request("d.json", id="x1", action="restart", gpu_uuid="GPU-test-1", at=990.3),
            request("e.json", id="x2", action="desconhecida", gpu_uuid=None, at=990.4),
            request("f.json", id="x3", action="reconcile", gpu_uuid=None, at=500.0),
            request("g.json", id="b1", action="benchmark", gpu_uuid=None, at=990.5),
            request("h.json", id="x4", action="stop", gpu_uuid=None, at=990.6),
            request("i.json", id="x5", action="start", gpu_uuid="../fora", at=990.7),
            request("j.json", id="x6", action="restart", gpu_uuid="GPU-nao-existe", at=990.8),
            {"op": "process_requests"},
            {"op": "reconcile", "detected": rig_detected(2)},
            request("k.json", id="s1", action="stop_all", gpu_uuid=None, at=999.0),
            request("l.json", id="z1", action="shutdown", gpu_uuid=None, at=999.5),
            {"op": "process_requests"},
            {"op": "reconcile", "detected": rig_detected(2)},
        ],
        "contribuicao": [
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {
                    "wallet": RIG_OTHER,
                    "support_enabled": True,
                    "gpu_settings": {"GPU-test-1": {"wallet": AUTHOR_WALLET}},
                },
            },
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {
                    "wallet": RIG_OTHER,
                    "support_enabled": False,
                    "gpu_settings": {"GPU-test-1": {"wallet": AUTHOR_WALLET}},
                },
            },
        ],
        "legado_somente_leitura": [
            *legacy,
            {"op": "set_legacy", "started": [0, 1]},
            {"op": "adopt_legacy"},
            {"op": "reconcile"},
            {"op": "stop_gpu", "uuid": "GPU-test-0"},
        ],
        "ordem_do_driver": [
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {**author, "gpu_settings": {"GPU-test-1": {"wallet": RIG_OTHER}}},
            },
            {
                "op": "reconcile",
                "detected": [{**rig_detected(2)[0], "index": 1}, {**rig_detected(2)[1], "index": 0}],
                "settings": {**author, "gpu_settings": {"GPU-test-1": {"wallet": RIG_OTHER}}},
            },
        ],
        "proxy_morto": [
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {"wallet": RIG_OTHER, "support_enabled": True},
            },
            {"op": "kill", "target": ["device", "GPU-test-1", "proxy"]},
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {"wallet": RIG_OTHER, "support_enabled": True},
            },
        ],
        "troca_de_placa": [
            {"op": "reconcile"},
            {"op": "reconcile", "detected": [*rig_detected()[:5], {**rig_detected()[5], "uuid": "GPU-replacement-5"}]},
            {"op": "new_manager"},
        ],
        "sete_gpus": [{"op": "reconcile", "detected": rig_detected(7)}],
        "migracao_espera_supervisor": [
            *migration,
            {"op": "backend", "set": {"complete_pids": [103]}},
            {"op": "migrate_legacy", "uuid": ref["uuid"], "reference": True},
            {"op": "reconcile"},
        ],
        "migracao_recusada": [
            *migration,
            {"op": "migrate_legacy", "uuid": "GPU-another-board", "reference": True},
            {"op": "adopt_legacy"},
            {"op": "mutate", "target": ["started", 1], "fields": {"created_at": "9999999"}},
            {"op": "migrate_legacy", "uuid": ref["uuid"], "reference": True},
        ],
        "migracao_supervisor_vivo": [*migration, {"op": "migrate_legacy", "uuid": ref["uuid"], "reference": True}],
        "referencia_persistente": [
            *migration,
            {"op": "backend", "set": {"wait_mode": "complete"}},
            {"op": "migrate_legacy", "uuid": ref["uuid"], "reference": True},
            {"op": "reconcile"},
            {"op": "stop_gpu", "uuid": ref["uuid"]},
            {"op": "publish"},
            {"op": "new_manager"},
            {"op": "reconcile"},
        ],
        "referencia_limites_desconhecidos": [
            *migration,
            {"op": "backend", "set": {"wait_mode": "complete"}},
            {"op": "migrate_legacy", "uuid": ref["uuid"], "reference": True},
            {"op": "set_detector", "detected": [{**ref, "power_default_w": None}]},
            {"op": "reconcile"},
        ],
        "referencia_memoria_baixa": [
            {"op": "set_attr", "name": "reference_uuid", "value": ref["uuid"]},
            {"op": "set_attr", "name": "miner_path", "value": "{root}\\SRBMiner-Multi-3-8-4\\SRBMiner-MULTI.exe"},
            {
                "op": "reconcile",
                "detected": [{**ref, "supported_graphics_clocks_mhz": [1560, 1740]}, other],
                "settings": {**author, "reference_memory_mhz": 405},
            },
            {
                "op": "reconcile",
                "detected": [{**ref, "supported_graphics_clocks_mhz": [1560, 1740]}, other],
                "settings": {**author, "reference_memory_mhz": 5001},
            },
        ],
        "saude_reinicio_automatico": [
            {"op": "reconcile", "detected": rig_detected(1)},
            *[
                step
                for at in (1400.5, 1402.5, 1404.5, 1411.5, 1412.5)
                for step in (
                    {"op": "set_clock", "value": at},
                    {
                        "op": "observe_health",
                        "snapshots": {
                            "GPU-test-0": {"miner": stalled_miner, "miner_running": True, "gpu": gpu_sample(60, at - 1)}
                        },
                    },
                )
            ],
            {"op": "set_clock", "value": 1415.0},
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "set_attr", "name": "require_recovery_sensors", "value": True},
            {"op": "set_clock", "value": 1800.0},
            *[
                step
                for at in (1800.0, 1811.0, 1812.0)
                for step in (
                    {"op": "set_clock", "value": at},
                    {
                        "op": "observe_health",
                        "snapshots": {
                            "GPU-test-0": {"miner": stalled_miner, "miner_running": True, "gpu": gpu_sample(60, at - 1)}
                        },
                    },
                )
            ],
            {"op": "set_clock", "value": 1815.0},
            {
                "op": "observe_health",
                "snapshots": {
                    "GPU-test-0": {"miner": stalled_miner, "miner_running": True, "gpu": gpu_sample(75, 1813.0)}
                },
            },
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "set_clock", "value": 1830.0},
            {
                "op": "observe_health",
                "snapshots": {
                    "GPU-test-0": {
                        "miner": {
                            "hashrate": 70.0,
                            "hashrate_at": 1829.0,
                            "connection": "Conectado à pool",
                            "last_share_at": 1820.0,
                        },
                        "miner_running": True,
                        "gpu": gpu_sample(60, 1829.0),
                        "log_error": None,
                    }
                },
            },
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "sucessor_confirmado": [
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "kill", "target": ["device", "GPU-test-0", "controller"]},
            {"op": "spawn_successor", "uuid": "GPU-test-0", "parent": True},
            {"op": "kill", "target": ["device", "GPU-test-0", "miner"]},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "sucessor_sem_pai": [
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "spawn_successor", "uuid": "GPU-test-0", "parent": False},
            {"op": "kill", "target": ["device", "GPU-test-0", "miner"]},
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "controlador_recuperado": [
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "kill", "target": ["device", "GPU-test-0", "controller"]},
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "kill", "target": ["device", "GPU-test-0", "controller"]},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "executavel_bloqueado": [
            {
                "op": "reconcile",
                "detected": rig_detected(1),
                "settings": {**author, "gpu_settings": {"GPU-test-0": {"enabled": False}}},
            },
            {"op": "backend", "set": {"verify_error": "Executável ausente"}},
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "set_clock", "value": 1040.0},
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "backend", "set": {"verify_error": None}},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "inicio_manual_com_sensor": [
            {
                "op": "reconcile",
                "detected": rig_detected(1),
                "settings": {**author, "gpu_settings": {"GPU-test-0": {"enabled": False}}},
            },
            {"op": "set_attr", "name": "require_recovery_sensors", "value": True},
            request("m1.json", id="m1", action="start", gpu_uuid="GPU-test-0", at=1000.0),
            {"op": "process_requests"},
            {"op": "set_attr", "name": "require_recovery_sensors", "value": False},
            request("m2.json", id="m2", action="start", gpu_uuid="GPU-test-0", at=1000.0),
            request("m3.json", id="m3", action="start", gpu_uuid="GPU-test-0", at=1000.1),
            {"op": "process_requests"},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "atualizacao_validada": [
            *install,
            {"op": "reconcile", "detected": rig_detected(2)},
            {"op": "activate", "version": "3.8.1", "request_id": "u1"},
            {"op": "reconcile", "detected": rig_detected(2)},
            {"op": "validate_update", "snapshots": update_snapshots("3.8.0", 1000.0)},
            {"op": "validate_update", "snapshots": update_snapshots("3.8.1", 700.0)},
            {"op": "validate_update", "snapshots": update_snapshots("3.8.1", 820.0)},
            {
                "op": "validate_update",
                "snapshots": {
                    **update_snapshots("3.8.1", 1000.0),
                    "GPU-test-0": {
                        "miner_running": True,
                        "miner": {"version": "3.8.1", "hashrate": 80, "hashrate_at": 1000.0, "paused": True},
                    },
                },
            },
            {
                "op": "validate_update",
                "snapshots": {
                    **update_snapshots("3.8.1", 1000.0),
                    "GPU-test-0": {
                        "miner_running": "sim",
                        "miner": {"version": "3.8.1", "hashrate": 80, "hashrate_at": 1000.0},
                    },
                },
            },
            {
                "op": "validate_update",
                "snapshots": {
                    **update_snapshots("3.8.1", 1000.0),
                    "GPU-test-1": {
                        "miner_running": True,
                        "miner": {"version": "3.8.1", "hashrate": 0, "hashrate_at": 1000.0},
                    },
                },
            },
            {"op": "new_manager"},
            {"op": "set_miner", "version": "3.8.1"},
            {"op": "validate_update", "snapshots": update_snapshots("3.8.1", 1000.0)},
            {"op": "reconcile", "detected": rig_detected(2)},
        ],
        "atualizacao_revertida": [
            *install,
            {"op": "reconcile", "detected": rig_detected(2)},
            {"op": "activate", "version": "3.8.1", "request_id": "u2"},
            {"op": "reconcile", "detected": rig_detected(2)},
            {"op": "set_clock", "value": 1180.5},
            {"op": "validate_update", "snapshots": {}},
            {"op": "set_clock", "value": 1181.0},
            {"op": "validate_update", "snapshots": {}},
            {"op": "reconcile", "detected": rig_detected(2)},
        ],
        "atualizacao_desabilitada_ignorada": [
            *install,
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {**author, "gpu_settings": {"GPU-test-1": {"enabled": False}}},
            },
            {"op": "activate", "version": "3.8.1"},
            {"op": "validate_update", "snapshots": {"GPU-test-0": update_snapshots("3.8.1", 1000.0)["GPU-test-0"]}},
        ],
        "atualizacao_bloqueada": [
            *install,
            {"op": "reconcile", "detected": rig_detected(2)},
            {"op": "set_entry", "uuid": "GPU-test-1", "fields": {"external_readonly": True}},
            {"op": "activate", "version": "3.8.1"},
            {
                "op": "set_entry",
                "uuid": "GPU-test-1",
                "fields": {"external_readonly": False, "ownership_unverified": True},
            },
            {"op": "activate", "version": "3.8.1"},
            {"op": "set_entry", "uuid": "GPU-test-1", "fields": {"ownership_unverified": False}},
            {"op": "tamper", "version": "3.8.1"},
            {"op": "activate", "version": "3.8.1"},
            {"op": "activate", "version": "9.9.9"},
            {"op": "activate", "version": None},
            {"op": "tamper", "version": "3.8.0"},
            {"op": "install", "version": "3.8.1", "content": "nova"},
            {"op": "activate", "version": "3.8.1"},
        ],
        "atualizacao_mesma_versao": [*install, {"op": "activate", "version": "3.8.0", "request_id": "u3"}],
        "atualizacao_por_pedido": [
            *install,
            {"op": "reconcile", "detected": rig_detected(2)},
            request("u1.json", id="u1", action="miner_update", gpu_uuid=None, at=1000.0, version="9.9.9"),
            {"op": "process_requests"},
            request("u2.json", id="u2", action="miner_update", gpu_uuid=None, at=1000.1, version="3.8.1"),
            {"op": "process_requests"},
            {"op": "new_manager"},
            {"op": "set_miner", "version": "3.8.1"},
            request("u3.json", id="u3", action="miner_update", gpu_uuid=None, at=1000.2, version="3.8.0"),
            {"op": "process_requests"},
            {"op": "reconcile", "detected": rig_detected(2)},
        ],
        "selecao_adulterada": [
            *install,
            {"op": "select", "version": "3.8.1"},
            {"op": "corrupt_selection", "text": '{"version": "../../fora"}'},
            {"op": "activate", "version": "3.8.1"},
        ],
        "coleta": [
            {"op": "set_clock", "value": LOG_EPOCH + 10},
            {
                "op": "reconcile",
                "detected": rig_detected(3),
                "settings": {**author, "gpu_settings": {"GPU-test-2": {"enabled": False}}},
            },
            {
                "op": "collect",
                "samples": {"GPU-test-0": gpu_sample(61.0, LOG_EPOCH + 9)},
                "logs": {
                    "GPU-test-0": "\n".join(
                        [
                            f"[{log_stamp(0)}] GPU0 RTX: 71.5 TH/s [T:65C P:150W A:10 R:1 HW:0]",
                            f"[{log_stamp(5)}] GPU1 GTX: 9.5 TH/s [T:60C P:90W A:3 R:0 HW:0]",
                        ]
                    ),
                    "GPU-test-1": "\n".join(
                        [
                            f"[{log_stamp(-60)}] Miner version: 3.8.4",
                            f"[{log_stamp(0)}] GPU1 GTX: 9.5 TH/s [T:60C P:90W A:3 R:0 HW:0]",
                            f"[{log_stamp(2)}] GPU1 turned off",
                        ]
                    ),
                },
            },
            {"op": "observe_health", "snapshots_from": "collect"},
        ],
        "desconectada": [
            {"op": "reconcile", "detected": rig_detected(3)},
            {"op": "reconcile", "detected": rig_detected(3)[1:]},
            {"op": "reconcile", "detected": rig_detected(3)},
        ],
        "sucessor_ambiguo": [
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "spawn_successor", "uuid": "GPU-test-0", "parent": True},
            {"op": "backend_start", "args": ["{miner}", "--algorithm-gpu", "pearlhash"]},
            {"op": "add_legacy", "started": [3]},
            {"op": "kill", "target": ["device", "GPU-test-0", "miner"]},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "sucessor_ausente": [
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "kill", "target": ["device", "GPU-test-0", "miner"]},
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "reconcile", "detected": rig_detected(1)},
            {"op": "set_clock", "value": 1016.0},
            {"op": "reconcile", "detected": rig_detected(1)},
        ],
        "registro_cheio": [
            {"op": "reconcile"},
            {"op": "mutate", "target": ["device", "GPU-test-5", "miner"], "append_argv": "unexpected"},
            {"op": "reconcile", "detected": [*rig_detected()[:5], {**rig_detected()[5], "uuid": "GPU-replacement-5"}]},
        ],
        "proxy_e_controlador_mortos": [
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {"wallet": RIG_OTHER, "support_enabled": True},
            },
            {"op": "kill", "target": ["device", "GPU-test-1", "proxy"]},
            {"op": "kill", "target": ["device", "GPU-test-1", "controller"]},
            {
                "op": "reconcile",
                "detected": rig_detected(2),
                "settings": {"wallet": RIG_OTHER, "support_enabled": True},
            },
        ],
    }


class RigGoldenBackend:
    """O `FakeBackend` do test_rig.py, com os ajustes que os cenários pedem."""

    def __init__(self):
        import test_rig

        self.fake = test_rig.FakeBackend()
        self.blocked = None
        self.verify_error = None
        self.wait_mode = "default"
        self.complete_pids = []
        self.waited = []

    def __getattr__(self, name):
        return getattr(self.fake, name)

    def verify_miner(self, path):
        if self.verify_error:
            raise ValueError(self.verify_error)

    def assert_gpu_available(self, miner_path, index):
        if self.blocked:
            raise RuntimeError(self.blocked)

    def wait(self, identity, timeout=12):
        self.waited.append(identity["pid"])
        if self.wait_mode == "complete" or identity["pid"] in self.complete_pids:
            self.fake.processes[identity["pid"]]["alive"] = False
            return True
        return self.fake.wait(identity, timeout)


def rig_run(name, steps):
    import rig

    folder = Path(tempfile.mkdtemp()).resolve()
    try:
        import miner_update

        saved = dict(miner_update.CATALOG)
        try:
            return rig_run_in(folder, name, steps, rig)
        finally:
            miner_update.CATALOG.clear()
            miner_update.CATALOG.update(saved)
    finally:
        import shutil

        shutil.rmtree(folder, ignore_errors=True)


def rig_run_in(root, name, steps, rig):
    import re
    import fsutil

    fsutil.atomic_json(root / "settings.json", {"wallet": __import__("contribution").AUTHOR_WALLET})
    backend = RigGoldenBackend()
    state = {"clock": RIG_CLOCK, "detected": rig_detected()}

    def make():
        return rig.RigManager(
            root / "state",
            root / "logs",
            root / "settings.json",
            root / "SRBMiner-MULTI.exe",
            root / "python.exe",
            backend=backend,
            detector=lambda: copy_json(state["detected"]),
            clock=lambda: state["clock"],
            proxy_ready=lambda entry, identity: 43210,
        )

    manager = make()

    def expand(value):
        if isinstance(value, str):
            return (
                value.replace("{root}", str(root))
                .replace("{miner}", str(manager.miner_path))
                .replace("{log_parent}", str(manager.log_dir.parent))
            )
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            return {key: expand(item) for key, item in value.items()}
        return value

    stamp = re.compile(r"mining-\d{8}-\d{6}-[0-9a-f]{6}\.log")

    def norm(value):
        if isinstance(value, str):
            return stamp.sub("mining-{stamp}.log", value.replace(str(root), "{root}").replace(str(ROOT), "{project}"))
        if isinstance(value, list):
            return [norm(item) for item in value]
        if isinstance(value, dict):
            return {norm(key): norm(item) for key, item in value.items()}
        return value

    def target(ref):
        if ref[0] == "device":
            return manager.devices[ref[1]][ref[2]]["pid"]
        return backend.started[ref[1]]["pid"]

    seen = 0
    records = []
    last_collect = {}
    for step in steps:
        op = step["op"]
        record = {"op": op}
        try:
            if op == "reconcile":
                result = manager.reconcile(
                    detected=copy_json(step["detected"]) if "detected" in step else None,
                    settings=copy_json(step["settings"]) if "settings" in step else None,
                )
                record["result"] = result
                state["desired"] = result
            elif op == "new_manager":
                manager = make()
            elif op == "set_detector":
                state["detected"] = step["detected"]
            elif op == "set_clock":
                state["clock"] = step["value"]
            elif op == "backend":
                for key, value in step["set"].items():
                    if key == "fail_uuid":
                        backend.fake.fail_uuid = value
                    else:
                        setattr(backend, key, value)
            elif op == "kill":
                backend.processes[target(step["target"])]["alive"] = False
            elif op == "mutate":
                identity = backend.processes[target(step["target"])]["identity"]
                identity.update(copy_json(step.get("fields", {})))
                if "append_argv" in step:
                    identity["argv"].append(step["append_argv"])
            elif op == "backend_start":
                backend.start(expand(step["args"]), root)
            elif op == "add_legacy":
                backend.fake.legacy += [copy_json(backend.started[index]) for index in step["started"]]
            elif op == "set_legacy":
                backend.fake.legacy = [copy_json(backend.started[index]) for index in step["started"]]
            elif op == "spawn_successor":
                entry = manager.devices[step["uuid"]]
                old = entry["miner"]
                successor = backend.start(list(old["argv"]), root)
                candidate = {**successor, "parent_pid": old["pid"] if step["parent"] else 999999}
                backend.fake.legacy = [candidate]
            elif op == "write_request":
                fsutil.atomic_json(manager.state_dir / "rig-requests" / step["file"], step["payload"])
            elif op == "process_requests":
                manager.process_requests()
            elif op == "stop_gpu":
                record["result"] = manager.stop_gpu(step["uuid"], restore=step.get("restore", False))
            elif op == "adopt_legacy":
                record["result"] = manager.adopt_legacy()
            elif op == "migrate_legacy":
                record["result"] = manager.migrate_legacy(step["uuid"], reference_rtx3070=step["reference"])
            elif op == "observe_health":
                if "snapshots_from" in step:
                    manager.observe_health(copy_json(last_collect))
                else:
                    manager.observe_health(copy_json(step["snapshots"]))
            elif op == "corrupt_selection":
                (root / "miner" / "miner-install.json").write_text(step["text"], encoding="utf-8")
            elif op == "publish":
                manager.publish()
            elif op == "set_attr":
                setattr(
                    manager,
                    step["name"],
                    Path(expand(step["value"])) if step["name"] == "miner_path" else step["value"],
                )
            elif op == "install":
                import hashlib
                import miner_update

                folder = "SRBMiner-Multi-" + step["version"].replace(".", "-")
                binary = root / "miner" / folder / "SRBMiner-MULTI.exe"
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(step["content"].encode())
                miner_update.CATALOG[step["version"]] = {
                    "archive_sha256": "0" * 64,
                    "exe_sha256": hashlib.sha256(step["content"].encode()).hexdigest(),
                }
            elif op == "tamper":
                folder = "SRBMiner-Multi-" + step["version"].replace(".", "-")
                (root / "miner" / folder / "SRBMiner-MULTI.exe").write_bytes(b"adulterado")
            elif op == "set_miner":
                import miner_update

                manager.miner_base = root / "miner"
                manager.miner_path = miner_update.executable(step["version"], manager.miner_base)
            elif op == "select":
                import miner_update

                miner_update.select(step["version"], root / "miner")
            elif op == "activate":
                manager.activate_miner(step["version"], step.get("request_id"))
            elif op == "validate_update":
                manager.validate_miner_update(copy_json(step["snapshots"]))
            elif op == "set_entry":
                manager.devices[step["uuid"]].update(copy_json(step["fields"]))
            elif op == "collect":
                import rig_controller

                for uuid, text in step.get("logs", {}).items():
                    Path(manager.devices[uuid]["miner_log"]).write_text(text, encoding="utf-8")
                record["result"] = rig_controller.collect_snapshots(
                    state["desired"],
                    manager.devices,
                    sample_reader=lambda devices, now: copy_json(step["samples"]),
                    now=state["clock"],
                )
            elif op == "corrupt_registry":
                manager.registry_path.parent.mkdir(parents=True, exist_ok=True)
                manager.registry_path.write_text(step["text"], encoding="utf-8")
            else:
                raise AssertionError(op)
            if op == "collect":
                last_collect = record["result"]
        except Exception as error:  # noqa: BLE001 — o golden registra qualquer erro do Python
            record["error"] = str(error)
        record["devices"] = copy_json(manager.devices)
        record["manager"] = {
            "error": manager.error,
            "ignored": manager.ignored,
            "processed_ids": manager.processed_ids,
            "reference_uuid": manager.reference_uuid,
            "registry_valid": manager.registry_valid,
            "shutting_down": manager.shutting_down,
            "miner_path": str(manager.miner_path),
            "miner_base": str(manager.miner_base),
            "miner_update_pending": manager.miner_update_pending,
        }
        record["backend"] = {
            "started": [item["argv"] for item in backend.started[seen:]],
            "stopped": list(backend.stopped),
            "restored": list(backend.restored),
            "waited": list(backend.waited),
        }
        seen = len(backend.started)
        records.append(norm(copy_json(record)))
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix in (".sqlite3", ".log") or "-journal" in path.name:
            continue
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix == ".jsonl":
            content = [json.loads(line) for line in text.splitlines() if line]
        else:
            try:
                content = json.loads(text)
            except ValueError:
                content = {"text": text}
        if relative == "state/rig-manager.json" and isinstance(content, dict) and "pid" in content:
            content["pid"] = 0
        if relative.endswith("miner-install.json") and isinstance(content, dict):
            content["selected_at"] = 0
        files[norm(relative)] = norm(content)
    database = root / "state" / "operations.sqlite3"
    with contextlib.closing(sqlite3.connect(database)) as db:
        dump = {
            table: [list(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("acks", "recoveries", "incidents", "component_attempts")
        }
    return {"name": name, "steps": steps, "records": records, "files": files, "operations": norm(dump)}


def rig_manager_golden():
    sys.path.insert(0, str(ROOT))
    cases = [rig_run(name, steps) for name, steps in rig_scenarios().items()]
    write("rig_manager", {"cases": cases, "clock": RIG_CLOCK})


def finance_golden():
    cases = (
        finance_model_cases()
        + finance_portfolio_cases()
        + finance_collector_cases()
        + [finance_portfolio_collector_case()]
    )
    write("finance", {"cases": cases, "author_wallet": contribution.AUTHOR_WALLET})


# Proxy de contribuição (`contribution.py`): ledger, reescrita Stratum e conexões reais por socket.
CONTRIBUTION_WALLET = "prl1" + "q" * 40
CONTRIBUTION_OTHER = "prl1" + "z" * 40


def contribution_ledger_files():
    w = CONTRIBUTION_WALLET
    base = {
        "eligible_connected_seconds": 7000.5,
        "primary_connected_seconds": 6800.0,
        "support_connected_seconds": 200.5,
    }
    seeded = {
        **base,
        "verified_primary_seconds": 3420,
        "reserved_support_seconds": 0,
        "verified_support_seconds": 0,
        "accepted_primary_shares": 3,
        "extra": "mantido",
    }
    return {
        "ausente": None,
        "v2_valido": json.dumps({"schema_version": 2, "wallets": {w: seeded}, "nota": "chave extra"}, indent=2),
        "v1_migra": json.dumps(
            {
                "schema_version": 1,
                "wallets": {w: {**base, "verified_primary_seconds": 9999, "reserved_support_seconds": 5}},
            }
        ),
        "outra_carteira": json.dumps({"schema_version": 2, "wallets": {CONTRIBUTION_OTHER: base}}),
        "booleano": json.dumps({"schema_version": 2, "wallets": {w: {**base, "primary_connected_seconds": True}}}),
        "negativo": json.dumps({"schema_version": 2, "wallets": {w: {**base, "accepted_support_shares": -1}}}),
        "grande_demais": json.dumps(
            {"schema_version": 2, "wallets": {w: {**base, "eligible_connected_seconds": 2e12}}}
        ),
        "sem_elegivel": json.dumps({"schema_version": 2, "wallets": {w: {"primary_connected_seconds": 1}}}),
        "texto": json.dumps({"schema_version": 2, "wallets": {w: {**base, "support_connected_seconds": "1"}}}),
        "reserva_excede": json.dumps(
            {
                "schema_version": 2,
                "wallets": {w: {**base, "verified_primary_seconds": 3420, "reserved_support_seconds": 181}},
            }
        ),
        "suporte_excede": json.dumps(
            {
                "schema_version": 2,
                "wallets": {
                    w: {
                        **base,
                        "verified_primary_seconds": 3420,
                        "reserved_support_seconds": 100,
                        "verified_support_seconds": 101,
                    }
                },
            }
        ),
        "acumulo": json.dumps(
            {
                "schema_version": 2,
                "wallets": {w: {**base, "primary_connected_seconds": 7000, "verified_primary_seconds": 6840}},
            }
        ),
        "verificado_excede": json.dumps(
            {"schema_version": 2, "wallets": {w: {**base, "verified_primary_seconds": 6800.1}}}
        ),
        "schema_3": json.dumps({"schema_version": 3, "wallets": {}}),
        "wallets_lista": json.dumps({"schema_version": 2, "wallets": []}),
        "chave_invalida": json.dumps({"schema_version": 2, "wallets": {"seed palavras": base}}),
        "entrada_lista": json.dumps({"schema_version": 2, "wallets": {w: [1]}}),
        "nan": '{"schema_version": 2, "wallets": {"' + w + '": {"eligible_connected_seconds": NaN}}}',
        "json_quebrado": '{"schema_version": 2,',
        "raiz_lista": "[]",
    }


LEDGER_OPS = [
    ("seconds",),
    ("role", True),
    ("role", False),
    ("until_switch", True),
    ("until_switch", False),
    ("available_support",),
    ("advance", 100.25, True),
    ("role", True),
    ("until_switch", True),
    ("advance", 3400, True),
    ("role", True),
    ("until_switch", True),
    ("advance", 7200.5, False),
    ("advance", -1, True),
    ("advance", True, True),
    ("record_route", 2.5, "primary"),
    ("record_route", 0, "primary"),
    ("record_route", 6, "primary"),
    ("record_route", 1, "x"),
    ("record_route", True, "primary"),
    ("available_support",),
    ("reserve_support",),
    ("available_support",),
    ("record_route", 4.75, "support"),
    ("record_route", 5, "support"),
    ("record_share", "primary", True),
    ("record_share", "support", False),
    ("record_share", "support", True),
    ("reserve_support",),
    ("seconds",),
    ("save",),
]


def contribution_ledger_case(name, content):
    with tempfile.TemporaryDirectory() as temporary:
        ledger_path = Path(temporary) / "support-mining-ledger.json"
        if content is not None:
            ledger_path.write_text(content, encoding="utf-8")
        ledger = contribution.ContributionLedger(ledger_path, CONTRIBUTION_WALLET)
        loaded = {"healthy": ledger.healthy, "error": ledger.error}
        ops = []
        for op in LEDGER_OPS:
            try:
                value = getattr(ledger, op[0])
                result = value if op[0] == "seconds" else value(*op[1:])
                ops.append({"op": list(op), "result": result})
            except (ValueError, RuntimeError) as error:
                ops.append({"op": list(op), "error": f"{type(error).__name__}: {error}"})
        saved = ledger_path.read_text(encoding="utf-8") if ledger_path.exists() else None
        return {
            "kind": "ledger",
            "name": name,
            "content": content,
            "loaded": loaded,
            "ops": ops,
            "saved": saved,
            "data": ledger.data,
        }


def contribution_rewrites():
    w, a = CONTRIBUTION_WALLET, contribution.AUTHOR_WALLET
    messages = [
        {"id": 1, "method": "mining.authorize", "params": [w, "x"]},
        {"id": 1, "method": "mining.authorize", "params": [w + ".rig1", "x"]},
        {"id": 1, "method": "mining.authorize", "params": [w + "x", "x"]},
        {"id": 1, "method": "mining.authorize", "params": []},
        {"id": "a", "method": "eth_submitLogin", "params": {"login": w, "pass": "x"}},
        {"id": 2, "method": "login", "params": {"login": w, "user": w}},
        {"id": 2, "method": "mining.login", "params": {"wallet": w + ".r", "nome": "café ✓ 😀"}},
        {"id": 3, "method": "mining.subscribe", "params": ["del\x7f tab\t nul\x00 \u2028"]},
        {"id": 3, "method": "mining.subscribe", "params": ["SRBMiner/3.8.4"]},
        {"id": 4, "method": "mining.configure", "params": [[], {}]},
        {"id": 5, "method": "mining.submit", "params": [w + ".rig1", "job", 1.5, 1e16, None]},
        {"id": 6, "method": "mining.submit", "params": {"user": w, "nonce": "ab"}},
        {"id": 6, "method": "mining.submit", "params": "texto"},
        {"id": 7, "method": "mining.extranonce.subscribe"},
        {"id": 8, "method": "client.get_version", "params": None},
        {"id": 9, "method": "mining.suggest_difficulty", "params": [1]},
        {"id": 9, "params": [1]},
        {"id": 9, "method": 5},
        [1, 2],
        "texto",
    ]
    cases = []
    for message in messages:
        for active, authenticated in ((w, False), (a, False), (a, True)):
            try:
                result, is_auth = contribution.rewrite_message(message, w, active, authenticated=authenticated)
                out = {"line": json.dumps(result, separators=(",", ":")), "auth": is_auth}
            except ValueError as error:
                out = {"error": str(error)}
            cases.append(
                {"kind": "rewrite", "message": message, "active": active, "authenticated": authenticated, **out}
            )
    return cases


def stratum(payload):
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode()


def contribution_proxy_scenarios():
    w = CONTRIBUTION_WALLET
    ok = lambda identifier: stratum({"id": identifier, "result": True, "error": None})  # noqa: E731
    seeded = {
        "schema_version": 2,
        "wallets": {
            w: {
                "eligible_connected_seconds": 3420.0,
                "primary_connected_seconds": 3420.0,
                "support_connected_seconds": 0.0,
                "verified_primary_seconds": 3420,
            }
        },
    }
    subscribe = stratum({"id": 1, "method": "mining.subscribe", "params": ["SRBMiner"]})
    authorize = stratum({"id": 2, "method": "mining.authorize", "params": [w + ".rig1", "x"]})
    submit = lambda identifier: stratum(
        {"id": identifier, "method": "mining.submit", "params": [w + ".rig1", "job", "nonce"]}
    )  # noqa: E731
    return {
        "principal_completo": {
            "support": False,
            "ledger": None,
            "client": [subscribe, authorize, submit(3), submit(4)],
            "wait": [1, 2, 1, 1],
            "responses": [
                [ok(1)],
                [ok(2), stratum({"id": None, "method": "mining.notify", "params": ["job"]})],
                [ok(3)],
                [stratum({"id": 4, "result": False, "error": [23, "low"]})],
            ],
        },
        "suporte_completo": {
            "support": True,
            "ledger": seeded,
            "client": [subscribe, authorize, submit(3)],
            "wait": [1, 1, 1],
            "responses": [[ok(1)], [ok(2)], [ok(3)]],
        },
        "suporte_auth_recusada": {
            "support": True,
            "ledger": seeded,
            "client": [authorize, submit(3)],
            "wait": [1, 0],
            "responses": [[stratum({"id": 2, "result": False, "error": "conta"})]],
        },
        "principal_auth_recusada": {
            "support": False,
            "ledger": None,
            "client": [authorize],
            "responses": [[stratum({"id": 2, "result": None, "error": None})]],
        },
        "share_antes_da_auth": {"support": False, "ledger": None, "client": [submit(3)], "responses": []},
        "suporte_share_sem_confirmacao": {
            "support": True,
            "ledger": seeded,
            "client": [authorize + submit(3)],
            "responses": [[]],
        },
        "outra_carteira": {
            "support": False,
            "ledger": None,
            "client": [stratum({"id": 2, "method": "mining.authorize", "params": [CONTRIBUTION_OTHER, "x"]})],
            "responses": [],
        },
        "metodo_desconhecido": {
            "support": False,
            "ledger": None,
            "client": [stratum({"id": 1, "method": "eth_getWork"})],
            "responses": [],
        },
        "id_duplicado": {"support": False, "ledger": None, "client": [subscribe + subscribe], "responses": [[]]},
        "json_invalido": {"support": False, "ledger": None, "client": [b"{nao json}\n"], "responses": []},
        "linhas_vazias_crlf": {
            "support": False,
            "ledger": None,
            "client": [b"\r\n  \n" + subscribe[:-1] + b"\r\n", authorize],
            "wait": [1, 1],
            "responses": [[ok(1)], [ok(2)]],
        },
        "resposta_nao_objeto": {"support": False, "ledger": None, "client": [subscribe], "responses": [[b"[1]\n"]]},
        "auth_sem_id": {
            "support": False,
            "ledger": None,
            "client": [stratum({"method": "mining.authorize", "params": [w, "x"]})],
            "responses": [],
        },
        "erro_zero_autoriza": {
            "support": False,
            "ledger": None,
            "client": [authorize, submit(3), submit(4)],
            "wait": [1, 1, 1],
            "responses": [
                [stratum({"id": 2, "result": True, "error": 0})],
                [stratum({"id": 3, "result": True, "error": 0.0})],
                [stratum({"id": 4, "result": True, "error": "x"})],
            ],
        },
        "auth_duplicada": {
            "support": False,
            "ledger": None,
            "client": [authorize, stratum({"id": 9, "method": "mining.authorize", "params": [w, "x"]})],
            "wait": [1, 0],
            "responses": [[ok(2)], []],
        },
    }


def run_proxy_scenario(name, scenario):
    import socket
    import threading

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        settings = root / "proxy-settings.json"
        settings.write_text(
            json.dumps({"wallet": CONTRIBUTION_WALLET, "support_enabled": scenario["support"]}), encoding="utf-8"
        )
        state = root / "state"
        state.mkdir()
        if scenario["ledger"] is not None:
            (state / "support-mining-ledger.json").write_text(json.dumps(scenario["ledger"]), encoding="utf-8")
        proxy = contribution.LocalStratumProxy(CONTRIBUTION_WALLET, settings, state, clock=lambda: 1000.0)
        client_end, client_proxy = socket.socketpair()
        upstream_proxy, upstream_end = socket.socketpair()
        received, client_bytes = [], bytearray()

        def pool():
            upstream_end.settimeout(3)
            buffer = bytearray()
            try:
                for responses in scenario["responses"]:
                    while b"\n" not in buffer:
                        chunk = upstream_end.recv(65536)
                        if not chunk:
                            return
                        buffer.extend(chunk)
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    received.append(line.decode())
                    for response in responses:
                        upstream_end.sendall(response)
                time.sleep(0.4)
                upstream_end.settimeout(0.2)
                while True:
                    chunk = upstream_end.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
            except OSError:
                pass
            finally:
                received.extend(item.decode() for item in bytes(buffer).split(b"\n") if item)
                upstream_end.close()

        def miner():
            client_end.settimeout(1.5)
            seen = 0
            try:
                for chunk, wait in zip(scenario["client"], scenario.get("wait", [0] * len(scenario["client"]))):
                    client_end.sendall(chunk)
                    seen += wait
                    while client_bytes.count(b"\n") < seen:
                        data = client_end.recv(65536)
                        if not data:
                            return
                        client_bytes.extend(data)
                client_end.settimeout(3)
                while True:
                    data = client_end.recv(65536)
                    if not data:
                        return
                    client_bytes.extend(data)
            except OSError:
                pass

        threads = [threading.Thread(target=pool), threading.Thread(target=miner)]
        for thread in threads:
            thread.start()
        error = None
        try:
            proxy.serve_connection(client_proxy, upstream_proxy)
        except (OSError, ValueError, RuntimeError) as failure:
            error = f"{type(failure).__name__}: {failure}"
        client_proxy.close()
        upstream_proxy.close()
        for thread in threads:
            thread.join()
        client_end.close()
        status = json.loads((state / "support-mining.json").read_text(encoding="utf-8"))
        for volatile in ("pid", "sampled_at"):
            status.pop(volatile)
        ledger = state / "support-mining-ledger.json"
        return {
            "kind": "proxy",
            "name": name,
            "scenario": {
                **scenario,
                "client": [chunk.decode() for chunk in scenario["client"]],
                "responses": [[item.decode() for item in group] for group in scenario["responses"]],
            },
            "upstream_received": received,
            "client_received": client_bytes.decode(),
            "error": error,
            "safety_disabled": proxy.safety_disabled,
            "status": status,
            "ledger": ledger.read_text(encoding="utf-8") if ledger.exists() else None,
        }


def contribution_golden():
    cases = [contribution_ledger_case(name, content) for name, content in contribution_ledger_files().items()]
    cases += contribution_rewrites()
    cases += [run_proxy_scenario(name, scenario) for name, scenario in contribution_proxy_scenarios().items()]
    write("contribution", {"wallet": CONTRIBUTION_WALLET, "author": contribution.AUTHOR_WALLET, "cases": cases})


# Consultor de IA (`ai_advisor.py` e os consultores de `gpu_agent.py`). A rede da nuvem e o
# cliente OpenCode são simulados; o Ollama fala HTTP de verdade com um servidor local.
def ai_try(function, *args, **kwargs):
    try:
        return {"ok": function(*args, **kwargs)}
    except (ValueError, KeyError, TypeError, OSError) as error:
        return {"error": f"{type(error).__name__}: {error}"}


AI_SETTINGS = [
    {},
    {"ai_enabled": True},
    {"ai_enabled": False, "ai": {}},
    {"ai": []},
    [],
    "texto",
    {"ai": {"provider": "openrouter"}},
    {"ai": {"provider": "openrouter", "model": "x/y:free", "cloud_consent": True}},
    {"ai": {"provider": "openrouter", "model": "x/y"}},
    {"ai": {"provider": "opencode"}},
    {"ai": {"provider": "opencode", "model": "opencode/glm-5-free"}},
    {"ai": {"provider": "opencode", "model": "opencode/GLM-free"}},
    {"ai": {"provider": "ollama", "model": None}, "ai_enabled": True},
    {"ai": {"provider": "ollama", "model": ""}},
    {"ai": {"provider": "ollama", "model": 5}},
    {"ai": {"provider": "ollama", "model": "a b"}},
    {"ai": {"provider": "x"}},
    {"ai": {"provider": 5}},
    {"ai": {"provider": "ollama", "model": "m" * 161}},
    {"ai": {"provider": "ollama", "model": "m" * 160}},
    {"ai": {"provider": "ollama", "cloud_consent": 1}, "ai_enabled": 1},
]

AI_CONTEXTS = [
    {},
    [],
    {"sample": [], "benchmark": "x"},
    {
        "sample": {
            "temperature_c": 70.5,
            "power_w": 150,
            "clock_mhz": True,
            "utilization_pct": -1,
            "temperature.gpu": "70",
            "outro": 1,
            "power.limit": 0,
        },
        "benchmark": {
            "validated": True,
            "mean_hashrate_ths": 99.5,
            "elapsed_seconds": 0,
            "invalid_samples": 2,
            "nome": "x",
            "independent_hash_samples": None,
        },
    },
    {"benchmark": {"validated": 1, "efficiency_ths_per_w": 1e-300, "mean_power_w": 1e308}},
    {"sample": None, "benchmark": None, "gpu": "RTX"},
]

AI_COMPLETIONS = [
    None,
    [],
    {},
    {"choices": []},
    {"choices": ["x"]},
    {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    {"choices": [{"message": {"content": "  {\"action\":\"hold\"}\n"}}]},
    {"choices": [{"message": "texto"}]},
    {"choices": [{"message": {"content": "   "}}]},
    {"choices": [{"message": {"content": None}}]},
    {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "{\"a\":"},
                        {"type": "image"},
                        {"type": "text", "text": "1}"},
                        "x",
                    ]
                }
            }
        ]
    },
    {"choices": [{"message": {"content": "```json\n{\"action\":\"hold\"}\n```"}}]},
    {"choices": [{"message": {"content": "```JSON  \n {\"x\":1} \n```"}}]},
    {"choices": [{"message": {"content": "```\nlinha1\nlinha2\n```"}}]},
    {"choices": [{"message": {"content": "```json {\"x\":1}```"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "\x1c ok \x1f"}}]},
]

AI_PARSES = [
    None,
    5,
    "",
    "nao json",
    "[]",
    '{"action":"hold"}',
    '{"action":"hold","reason":"ok"}',
    '{"action":"voar","reason":"ok"}',
    '{"action":"clock_up","reason":"   "}',
    '{"action":"clock_up","reason":5}',
    '{"action":"power_down","reason":"' + "r" * 400 + '"}',
    '{"action":"power_down","reason":"' + "r" * 401 + '"}',
    '{"action":"hold","reason":"ok","extra":1}',
    '{"reason":"ação ✓","action":"restore_best"}',
    ' {"action":"hold","reason":"  x  "} ',
]

AI_OLLAMA_INITS = [
    ("qwen2.5:0.5b", "http://127.0.0.1:11434", 12),
    ("m", "http://localhost:11434/", 0.1),
    ("m", "http://[::1]:11434", 99),
    ("m", "https://127.0.0.1:11434", 12),
    ("m", "http://example.com", 12),
    ("m", "http://user:pw@127.0.0.1", 12),
    ("m", "HTTP://LOCALHOST", 12),
    ("m a", "http://127.0.0.1", 12),
    ("m:cloud", "http://127.0.0.1", 12),
    ("m-cloud", "http://127.0.0.1", 12),
    ("x" * 121, "http://127.0.0.1", 12),
    (5, "http://127.0.0.1", 12),
    ("m", "http://127.0.0.1:11434//", "7.5"),
    ("m", "http://127.0.0.1", 1.0),
    ("m", "http://127.0.0.1", 30.0),
    ("m", "http://127.0.0.1", 29.5),
]


def ai_requests_budget():
    import ai_advisor

    rows = []
    with tempfile.TemporaryDirectory() as temporary:
        budget = ai_advisor.RequestBudget(temporary)
        base = 1794000000.0
        stamps = [base, base + 30, base + 60, True, float("inf"), -1, 253402300800, "x"]
        stamps += [base + 120 + 60 * index for index in range(40)]
        stamps += [base + 86400 * 2, base + 86400 * 2 + 59.5]
        for stamp in stamps:
            outcome = ai_try(budget.reserve, stamp)
            rows.append(
                {
                    "now": stamp
                    if not isinstance(stamp, float) or stamp == stamp and abs(stamp) != float("inf")
                    else "inf",
                    "out": outcome,
                }
            )
    return rows


AI_KEY = "sk-or-v1-teste-sem-valor-real"


def ai_routed_case(
    name,
    settings,
    *,
    catalog=None,
    completion=None,
    key=AI_KEY,
    credential=None,
    which="C:/fake/opencode.exe",
    opencode_catalog=None,
    opencode_raw=b"",
    opencode_code=0,
    budget_error=None,
    ollama_result=None,
    calls=1,
):
    import ai_advisor
    import gpu_agent

    log = []

    def request_json(url, payload=None, key=None):
        log.append({"url": url, "payload": payload, "key": key is not None})
        if url.endswith("/api/v1/models"):
            if isinstance(catalog, Exception):
                raise catalog
            return {"data": catalog or []}
        if url.endswith("/zen/v1/models"):
            return {"data": opencode_catalog or []}
        if isinstance(completion, Exception):
            raise completion
        return completion

    def run(argv, **kwargs):
        log.append(
            {
                "run": argv[1:-1],
                "prompt_tail": argv[-1][-60:],
                "cwd_is_temp": "HashPilot-advice-" in str(kwargs["cwd"]),
                "env": {
                    key: ("<temp>" if key == "OPENCODE_CONFIG_DIR" else kwargs["env"][key])
                    for key in sorted(kwargs["env"])
                    if key.startswith("OPENCODE_")
                },
                "timeout": kwargs["timeout"],
            }
        )
        kwargs["stdout"].write(opencode_raw)
        return types.SimpleNamespace(returncode=opencode_code)

    def reserve(self, now=None):
        log.append({"budget": True})
        if budget_error:
            raise ValueError(budget_error)

    def ollama(self, context):
        log.append({"ollama_model": self.model, "context": context})
        if isinstance(ollama_result, Exception):
            raise ollama_result
        return ollama_result

    context = {
        "sample": {"temperature_c": 70.0, "power_w": 150.5, "nome": "x"},
        "benchmark": {"validated": False, "x": 1},
    }
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(ai_advisor, "request_json", request_json))
        stack.enter_context(mock.patch.object(ai_advisor, "credential", lambda provider: credential))
        stack.enter_context(mock.patch.object(ai_advisor.shutil, "which", lambda name: which))
        stack.enter_context(mock.patch.object(ai_advisor.subprocess, "run", run))
        stack.enter_context(mock.patch.object(ai_advisor.RequestBudget, "reserve", reserve))
        stack.enter_context(mock.patch.object(gpu_agent.OllamaAdvisor, "advise", ollama))
        stack.enter_context(mock.patch.object(ai_advisor.time, "time", lambda: 1794000000.0))
        advisor = ai_advisor.RoutedAdvisor("C:/estado", config_override=settings, key_override=key)
        results = [ai_try(advisor.advise, context) for _ in range(calls)]
        enabled = advisor.enabled()
    return {
        "kind": "routed",
        "name": name,
        "settings": settings,
        "key": key is not None,
        "credential": credential,
        "which": which,
        "catalog": None if isinstance(catalog, Exception) else catalog,
        "catalog_error": str(catalog) if isinstance(catalog, Exception) else None,
        "completion": None if isinstance(completion, Exception) else completion,
        "completion_error": str(completion) if isinstance(completion, Exception) else None,
        "opencode_catalog": opencode_catalog,
        "opencode_raw": opencode_raw.decode("utf-8", "replace"),
        "opencode_code": opencode_code,
        "budget_error": budget_error,
        "ollama_result": None if isinstance(ollama_result, Exception) else ollama_result,
        "ollama_error": str(ollama_result) if isinstance(ollama_result, Exception) else None,
        "calls": calls,
        "results": results,
        "label": advisor.label,
        "enabled": enabled,
        "log": log,
    }


def ai_routed_cases():
    free = [
        {"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "x/pago:free", "pricing": {"prompt": "0.1", "completion": "0"}},
        {"id": "x/sem:free", "pricing": {"prompt": "0"}},
        {"id": "x/texto:free", "pricing": {"prompt": "zero", "completion": "0"}},
        {"id": "x/num:free", "pricing": {"prompt": 0, "completion": 0.0}},
    ]
    good = {"choices": [{"message": {"content": '{"action":"clock_down","reason":"Quente"}'}}]}
    cloud = lambda model="openrouter/free", consent=True: {
        "ai_enabled": True,
        "ai": {"provider": "openrouter", "model": model, "cloud_consent": consent},
    }  # noqa: E731
    events = b'{"type":"step_start"}\n{"type":"text","part":{"text":"{\\"action\\":\\"hold\\","}}\n{"type":"text","part":{"text":"\\"reason\\":\\"ok\\"}"}}\n'
    oc = {"ai_enabled": True, "ai": {"provider": "opencode", "model": "opencode/glm-5-free", "cloud_consent": True}}
    return [
        ai_routed_case("desligada", {"ai": {"provider": "openrouter"}}),
        ai_routed_case("config_invalida", {"ai_enabled": True, "ai": {"provider": "x"}}),
        ai_routed_case("sem_consentimento", cloud(consent=False)),
        ai_routed_case("openrouter_ok", cloud(), catalog=free, completion=good, calls=2),
        ai_routed_case(
            "openrouter_credencial", cloud(), catalog=free, completion=good, key=None, credential="sk-cofre"
        ),
        ai_routed_case("openrouter_sem_chave", cloud(), catalog=free, completion=good, key=None, credential=None),
        ai_routed_case("openrouter_chave_vazia", cloud(), catalog=free, completion=good, key="", credential=""),
        ai_routed_case("modelo_pago", cloud("x/pago:free"), catalog=free, completion=good),
        ai_routed_case("modelo_sem_preco", cloud("x/sem:free"), catalog=free, completion=good),
        ai_routed_case("modelo_texto", cloud("x/texto:free"), catalog=free, completion=good),
        ai_routed_case("modelo_numerico", cloud("x/num:free"), catalog=free, completion=good),
        ai_routed_case("modelo_ausente", cloud("x/outro:free"), catalog=free, completion=good),
        ai_routed_case(
            "cota_esgotada",
            cloud(),
            catalog=free,
            completion=good,
            budget_error="Limite local de 40 consultas cloud/dia atingido",
        ),
        ai_routed_case(
            "resposta_truncada",
            cloud(),
            catalog=free,
            completion={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]},
        ),
        ai_routed_case(
            "catalogo_falha", cloud(), catalog=ValueError("Provedor sem conexão ou tempo de resposta excedido")
        ),
        ai_routed_case(
            "http_falha",
            cloud(),
            catalog=free,
            completion=ValueError("Limite gratuito atingido; controle adaptativo permanece ativo"),
        ),
        ai_routed_case(
            "ollama_ok",
            {"ai_enabled": True, "ai": {"provider": "ollama"}},
            ollama_result={"action": "hold", "reason": "ok"},
        ),
        ai_routed_case(
            "ollama_erro",
            {"ai_enabled": True, "ai": {"provider": "ollama", "model": "llama3:8b"}},
            ollama_result=ValueError("sem Ollama"),
        ),
        ai_routed_case("opencode_ok", oc, opencode_catalog=[{"id": "glm-5-free"}], opencode_raw=events),
        ai_routed_case("opencode_sem_cliente", oc, which=None),
        ai_routed_case("opencode_modelo_removido", oc, opencode_catalog=[{"id": "outro-free"}]),
        ai_routed_case(
            "opencode_codigo", oc, opencode_catalog=[{"id": "glm-5-free"}], opencode_raw=events, opencode_code=1
        ),
        ai_routed_case("opencode_evento_lista", oc, opencode_catalog=[{"id": "glm-5-free"}], opencode_raw=b"[1]\n"),
        ai_routed_case(
            "opencode_parte_invalida",
            oc,
            opencode_catalog=[{"id": "glm-5-free"}],
            opencode_raw=b'{"type":"text","part":{"text":5}}\n',
        ),
        ai_routed_case("opencode_grande", oc, opencode_catalog=[{"id": "glm-5-free"}], opencode_raw=b"x" * 65537),
        ai_routed_case(
            "opencode_sem_texto",
            oc,
            opencode_catalog=[{"id": "glm-5-free"}],
            opencode_raw=b'{"type":"step_start"}\r\n\n',
        ),
    ]


def ai_ollama_http_cases():
    import http.server
    import threading

    import gpu_agent

    scenarios = [
        ("ok", 200, json.dumps({"response": '{"action":"clock_up","reason":"Folga térmica"}'}).encode()),
        ("sem_response", 200, b'{"done":true}'),
        ("json_invalido", 200, b"nao json"),
        ("resposta_fora", 200, json.dumps({"response": '{"action":"voar","reason":"x"}'}).encode()),
        ("grande", 200, b'{"response":"' + b"x" * 65530 + b'"}'),
        ("limite_exato", 200, b'{"response":"' + b"x" * 65520 + b'","p":""}'),
        ("http_500", 500, b"erro"),
        ("http_404", 404, b""),
    ]
    cases = []
    for name, code, body in scenarios:
        received = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers["Content-Length"])
                received.update(
                    path=self.path, content_type=self.headers["Content-Type"], body=json.loads(self.rfile.read(length))
                )
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        advisor = gpu_agent.OllamaAdvisor(
            model="qwen2.5:0.5b", endpoint=f"http://127.0.0.1:{server.server_port}", timeout=5
        )
        context = {
            "sample": {"temperature_c": 71.25, "clock_mhz": 1650},
            "benchmark": {"validated": True},
            "nota": "ação",
        }
        outcome = ai_try(advisor.advise, context)
        thread.join()
        server.server_close()
        if "error" in outcome and outcome["error"].startswith("HTTPError"):
            outcome["error"] = outcome["error"].split(":", 1)[0] + ": " + outcome["error"].split(": ", 1)[1]
        cases.append(
            {
                "kind": "ollama_http",
                "name": name,
                "status": code,
                "body": body.decode(),
                "context": context,
                "received": received,
                "out": outcome,
            }
        )
    return cases


def ai_background_cases():
    import ai_advisor
    import gpu_agent

    class Scripted:
        label = "local_ollama"

        def __init__(self, answers):
            self.answers, self.contexts = list(answers), []

        def advise(self, context):
            self.contexts.append(context)
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    class Routed(ai_advisor.RoutedAdvisor):
        def __init__(self, answers, configs):
            super().__init__("C:/estado")
            self.answers, self.configs, self.contexts = list(answers), list(configs), []
            self.label = "IA opcional"

        def settings(self):
            config = self.configs[0] if len(self.configs) == 1 else self.configs.pop(0)
            if isinstance(config, Exception):
                raise config
            return config

        def advise(self, context):
            self.contexts.append(context)
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            self.label = "OpenRouter · Cloud Free"
            return answer

    ok = {"action": "hold", "reason": "ok"}
    cloud = {"ai_enabled": True, "ai": {"provider": "openrouter", "cloud_consent": True}}
    local = {"ai_enabled": True, "ai": {"provider": "ollama"}}
    scenarios = [
        (
            "local",
            lambda: Scripted([ok, ValueError("falhou"), ok, {"action": "clock_down", "reason": "quarta"}]),
            [0, 30, 59.9, 60, 100, 151, 160, 400],
            60,
        ),
        ("intervalo_minimo", lambda: Scripted([ok, ok, ok]), [0, 59, 61, 121], 5),
        ("intervalo_maior", lambda: Scripted([ok, ok]), [0, 100, 119, 120.5], 120),
        ("roteado_nuvem", lambda: Routed([ok, ok, ok], [cloud]), [0, 61, 3599, 3600, 3700], 60),
        ("roteado_troca", lambda: Routed([ok, ok, ok], [local, local, cloud, cloud]), [0, 10, 20, 30], 60),
        ("roteado_config_invalida", lambda: Routed([ok], [ValueError("x")]), [0], 60),
        ("erro_tipo", lambda: Scripted([TypeError("tipo"), KeyError("response"), ok]), [0, 60, 200], 60),
        ("erro_longo", lambda: Scripted([ValueError("é" * 170 + "fim")]), [0, 1], 60),
    ]
    cases = []
    for name, factory, stamps, interval in scenarios:
        advisor = factory()
        background = gpu_agent.BackgroundAdvisor(advisor, interval_seconds=interval)
        clock = {"now": 0.0}
        polls = []
        with mock.patch.object(gpu_agent.time, "monotonic", lambda: clock["now"]):
            for stamp in stamps:
                clock["now"] = float(stamp)
                result = background.poll({"sample": {"t": stamp}}, now=stamp)
                if background.thread is not None:
                    background.thread.join()
                polls.append({"now": stamp, "out": result})
        cases.append(
            {"kind": "background", "name": name, "interval": interval, "polls": polls, "contexts": advisor.contexts}
        )
    return cases


def ai_advisor_golden():
    import ai_advisor
    import gpu_agent

    cases = [{"kind": "configuration", "in": item, **ai_try(ai_advisor.configuration, item)} for item in AI_SETTINGS]
    cases += [{"kind": "telemetry", "in": item, **ai_try(ai_advisor.telemetry_context, item)} for item in AI_CONTEXTS]
    cases += [{"kind": "completion", "in": item, **ai_try(ai_advisor.completion_text, item)} for item in AI_COMPLETIONS]
    cases += [{"kind": "parse", "in": item, **ai_try(gpu_agent.OllamaAdvisor.parse, item)} for item in AI_PARSES]
    for model, endpoint, timeout in AI_OLLAMA_INITS:
        outcome = ai_try(lambda: vars(gpu_agent.OllamaAdvisor(model=model, endpoint=endpoint, timeout=timeout)))
        cases.append({"kind": "ollama_init", "in": [model, endpoint, timeout], **outcome})
    cases.append({"kind": "budget", "rows": ai_requests_budget()})
    cases += ai_routed_cases()
    cases += ai_ollama_http_cases()
    cases += ai_background_cases()
    write("ai_advisor", {"system": ai_advisor.SYSTEM, "providers": ai_advisor.PROVIDERS, "cases": cases})


# Agente da GPU (`gpu_agent.py`), parte sem hardware e sem IA: funções puras, leituras com
# nvidia-smi falso e o `GPUAgent` sobre SQLite, com relógio e uuid4 determinísticos.
AGENT_GPU = {
    "vendor": "NVIDIA",
    "uuid": "GPU-abc-123",
    "index": 0,
    "name": "NVIDIA GeForce RTX 3070 ✓",
    "supported_graphics_clocks_mhz": [1500, 1515, 1530, 1545, 1560, 1575, 1590, 1605, 1620, 1635, 1650],
    "power_min_w": 100.0,
    "power_max_w": 240.0,
    "power_default_w": 220.0,
    "temperature_limit_c": 93,
}
AGENT_BAD_GPU = {"vendor": "AMD", "uuid": "GPU-x"}


def agent_sample(temp, clock=1590, power=170.0, limit=220.0, util=100, at=None):
    sample = {
        "temperature_c": temp,
        "power_w": power,
        "power_limit_w": limit,
        "clock_mhz": clock,
        "utilization_pct": util,
    }
    if at is not None:
        sample["sampled_at"] = at
    return sample


def gpu_agent_pure_cases():
    import gpu_agent as ga

    cases = []

    def add(kind, args, call):
        try:
            out = {"out": call()}
        except (ValueError, RuntimeError, TypeError, KeyError) as error:
            out = {"error": f"{type(error).__name__}: {error}"}
        cases.append({"kind": kind, "in": args, **out})

    for sample in [
        agent_sample(70, at=5.0),
        {"temperature.gpu": "71.5", "power.draw": 160, "clocks.gr": "1600", "utilization.gpu": True},
        {"temperature_c": None, "temperature.gpu": 80},
        {"sampled_at": "abc"},
        {"power_w": float("inf"), "sampled_at": 3},
    ]:
        add(
            "normalize_sample",
            {"sample": {key: finite_or_text(value) for key, value in sample.items()}, "now": 100},
            lambda: ga.normalize_sample(sample, 100),
        )
    for limit in [None, 93, 50, 49, 110, 111, 0, "84", 80.5, True]:
        add(
            "thermal_limits",
            {"gpu": {"temperature_limit_c": limit}},
            lambda: list(ga.thermal_limits({"temperature_limit_c": limit})),
        )

    def rows(temps, start=0.0, step=4.0, valid=True):
        return [
            {"valid": valid, "stamp": start + step * index, "temperature": temp} for index, temp in enumerate(temps)
        ]

    windows = [
        rows([70.0] * 16),
        rows([70.0 + 0.05 * i for i in range(16)]),
        rows([70.0] * 14),
        rows([70.0] * 16, step=2.0),
        rows([70.0, 71.5] * 8),
        rows([70.0] * 16, valid=False),
        rows([70.0] * 8 + [None] * 4 + [70.0] * 8),
        rows([70.0] * 10, step=11.0),
        rows([60.0 + i * 0.1 for i in range(12)], start=10.0),
        [],
        rows([71.0] * 20, start=-30.0),
        # Fronteiras da estabilidade: 15 amostras, 45 s, intervalo de 10 s, 1 °C e subida de 0,2 °C.
        rows([70.0] * 15, start=4.0),
        rows([70.0] * 16, start=15.0, step=3.0),
        [
            {"valid": True, "stamp": stamp, "temperature": 70.0}
            for stamp in [0.0, 10.0] + [12.0 + 4 * i for i in range(13)]
        ],
        rows([71.0] * 5 + [70.0] * 11),
        rows([0.0] * 5 + [0.1] * 6 + [0.2] * 5),
        rows([0.0] * 5 + [0.1] * 6 + [0.25] * 5),
    ]
    for window in windows:
        add("thermal_window_summary", {"rows": window, "now": 60.0}, lambda: ga.thermal_window_summary(window, 60.0))
    stable = {"stable": True, "peak_c": 70.0}
    for sample, benchmark, profile, soft in [
        (agent_sample(70), {"validated": True, "thermal_window": stable}, "performance", 76.0),
        (agent_sample(75), {"validated": True, "thermal_window": stable}, "performance", 76.0),
        (
            agent_sample(74.9),
            {"validated": True, "thermal_window": {"stable": True, "peak_c": 75.0}},
            "performance",
            76.0,
        ),
        (agent_sample(72), {"validated": False, "thermal_window": stable}, "performance", 76.0),
        (agent_sample(73), {"validated": True}, "balanced", 76.0),
        (agent_sample(None), {}, "cool", 70.0),
        (agent_sample(70), {"validated": True, "thermal_window": {"stable": True}}, "performance", 76.0),
        (agent_sample(73.5), {"validated": True}, "balanced", 76.0),
        (agent_sample(72.5), {}, "balanced", 76.0),
    ]:
        add(
            "performance_probe_gate",
            {"sample": sample, "benchmark": benchmark, "profile": profile, "soft": soft},
            lambda: ga.performance_probe_gate(sample, benchmark, profile, soft),
        )
    gpus = {
        "gpu": AGENT_GPU,
        "sem_clocks": {**AGENT_GPU, "supported_graphics_clocks_mhz": []},
        "clocks_esparsos": {**AGENT_GPU, "supported_graphics_clocks_mhz": [1500.7, 1600, 0, -5, None, 1520]},
        "sem_potencia": {**AGENT_GPU, "power_min_w": None},
        "potencia_incoerente": {**AGENT_GPU, "power_default_w": 250.0},
        "denso": {**AGENT_GPU, "supported_graphics_clocks_mhz": list(range(1500, 1655, 5))},
    }
    plans = []
    for gpu_name in gpus:
        for sample in [
            agent_sample(70, 1590, limit=220.0),
            agent_sample(70, 1500, limit=100.0),
            agent_sample(70, 1650, limit=217.5),
            agent_sample(70, None, limit=None),
            agent_sample(70, 1597, limit=250.0),
            agent_sample(70, 1580, limit=102.0),
        ]:
            for action in ga.ACTIONS:
                plans.append((gpu_name, sample, action, {"clock_mhz": 1620}))
    plans += [
        ("gpu", agent_sample(70, 1590), "restore_best", {"clock_mhz": 1590}),
        ("gpu", agent_sample(70, 1590), "restore_best", {"clock_mhz": 1591}),
        ("gpu", agent_sample(70, 1590), "restore_best", None),
        ("gpu", agent_sample(70, 1590), "restore_best", {"clock_mhz": "1500"}),
        ("gpu", agent_sample(70), "turbo", None),
        ("ruim", agent_sample(70), "hold", None),
    ]
    gpus["ruim"] = AGENT_BAD_GPU
    for gpu_name, sample, action, best in plans:
        add(
            "validated_plan",
            {"gpu": gpu_name, "device": gpus[gpu_name], "sample": sample, "action": action, "best": best},
            lambda: ga.validated_plan(gpus[gpu_name], sample, action, best),
        )
    for sample, objective, profile, soft, advice in [
        (agent_sample(70), "performance", "performance", 76.0, None),
        (agent_sample(77), "performance", "performance", 76.0, None),
        (agent_sample(None), "efficiency", "balanced", 76.0, None),
        (agent_sample(70, limit=220.0), "efficiency", "balanced", 76.0, None),
        (agent_sample(70), "performance", "performance", 76.0, {"action": "hold"}),
        (agent_sample(70), "performance", "balanced", 76.0, {"action": "clock_up"}),
        (agent_sample(70), "performance", "balanced", 76.0, {"action": "clock_down"}),
        (agent_sample(70), "efficiency", "cool", 76.0, {"action": "hold"}),
        (agent_sample(70, 1650), "performance", "performance", 76.0, {"action": "clock_up"}),
        (agent_sample(70, limit=220.0), "efficiency", "performance", 76.0, {"action": "power_up"}),
    ]:
        add(
            "control_plan",
            {"sample": sample, "objective": objective, "profile": profile, "soft": soft, "advice": advice},
            lambda: ga.control_plan(AGENT_GPU, sample, objective, profile, soft, advice, None),
        )
    # statistics.mean é a média exata: casos em que a soma ingênua de floats erra.
    for values in [
        [1e16, 1.0, -1e16, 1.0],
        [0.1, 0.2, 0.3],
        [1e308, 1e308, -1e308],
        [74.84, 74.84, 74.85],
        [3.0],
        [1e-310, 3e-310],
        [0.1] * 10,
        [1e16, 3.0, 3.0, 3.0],
        [2.5e15 + 0.5, 2.5e15 + 0.5, 1.0],
    ]:
        naive = sum(values) / len(values) if all(abs(v) < 1e300 for v in values) else None
        add("mean", {"values": values, "naive": naive}, lambda: __import__("statistics").mean(values))
    return cases


def gpu_agent_read_cases():
    import gpu_agent as ga

    cases = []

    def scripted(output):
        def command(arguments):
            if isinstance(output, Exception):
                raise output
            return output

        return command

    outputs = [
        "70, 165.5, 220.00, 1590, 100",
        "70,165.5,220,1590",
        "130, 1, 2, 3, 4",
        "120, 1, 2, 3, 4",
        "0, 1, 2, 3, 4",
        "[N/A], 165, 220, 1590, 100",
        "-1, 1, 1, 1, 1",
        "",
        "71, 1, 2, 3, 4\n72, 9, 9, 9, 9",
        RuntimeError("driver"),
        " 65 , 1 , 2 , 3 , 4 ",
    ]
    for output in outputs:
        label = output if isinstance(output, str) else f"RuntimeError: {output}"
        for kind in ("read_sample", "read_fan", "read_memory_clocks", "read_memory_clock"):
            with mock.patch.object(ga.time, "time", lambda: 1234.5):
                try:
                    result = {"out": getattr(ga, kind)(AGENT_GPU, scripted(output))}
                except (RuntimeError, ValueError, IndexError) as error:
                    result = {"error": f"{type(error).__name__}: {error}"}
            cases.append({"kind": kind, "in": {"output": label}, **result})
    for output in ["45", "100", "0", "101", "[N/A]", "", "50.5\n60", "7000\n6801\n405\n0\n-1\n6801\nabc\n810.9"]:
        for kind in ("read_fan", "read_memory_clocks", "read_memory_clock"):
            cases.append(
                {"kind": kind, "in": {"output": output}, "out": getattr(ga, kind)(AGENT_GPU, scripted(output))}
            )
    with tempfile.TemporaryDirectory() as temporary:
        settings, finance = Path(temporary) / "settings.json", Path(temporary) / "finance-latest.json"
        for settings_text, finance_text in [
            (None, None),
            (
                '{"optimize_for": "profit", "electricity_price_brl_kwh": 0.9}',
                '{"forecast": {"brl_day": 12.0, "hashrate_ths": 80}}',
            ),
            (
                '{"optimize_for": "profit", "electricity_price_brl_kwh": "0,9"}',
                '{"forecast": {"brl_day": 12.0, "hashrate_ths": 80}}',
            ),
            (
                '{"optimize_for": "profit", "electricity_price_brl_kwh": -1}',
                '{"forecast": {"brl_day": 12, "hashrate_ths": 80}}',
            ),
            (
                '{"optimize_for": "profit", "electricity_price_brl_kwh": 0}',
                '{"forecast": {"brl_day": 0, "hashrate_ths": 80}}',
            ),
            ('{"optimize_for": "hashrate", "electricity_price_brl_kwh": 1}', '{"forecast": null}'),
            ('[1]', '[1]'),
            (
                '\ufeff{"optimize_for": "profit", "electricity_price_brl_kwh": 1.5}',
                '{"forecast": {"brl_day": "6", "hashrate_ths": "2"}}',
            ),
            ('{"target_temperature_c": 70, "gpu_settings": {"GPU-abc-123": {"target_temperature_c": "68.5"}}}', "x"),
            ('{"target_temperature_c": 70, "gpu_settings": {"GPU-abc-123": {"target_temperature_c": null}}}', "x"),
            ('{"target_temperature_c": "abc", "gpu_settings": []}', "x"),
            ('{"gpu_settings": {"GPU-abc-123": 5}}', "x"),
            ("{quebrado", "x"),
        ]:
            for target, content in ((settings, settings_text), (finance, finance_text)):
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_text(content, encoding="utf-8")
            cases.append(
                {
                    "kind": "read_optimization",
                    "in": {"settings": settings_text, "finance": finance_text},
                    "out": list(ga.read_optimization(settings, finance)),
                }
            )
            cases.append(
                {
                    "kind": "read_target_temperature",
                    "in": {"settings": settings_text, "finance": finance_text},
                    "out": ga.read_target_temperature(settings, "GPU-abc-123"),
                }
            )
    return cases


def gpu_agent_selection_cases():
    import gpu_agent as ga

    cases = []
    devices = [{**AGENT_GPU, "index": 0}, {**AGENT_GPU, "uuid": "GPU-def-456", "index": 1}]
    for uuid_value, index, miner_index in [
        (None, None, None),
        ("GPU-def-456", None, 3),
        ("GPU-def-456", 0, None),
        ("GPU-zzz", None, None),
        (None, 1, 256),
        (None, 5, None),
        ("GPU-abc-123", 0, 0),
        (None, None, -1),
        (None, None, 255),
    ]:
        args = types.SimpleNamespace(uuid=uuid_value, gpu=index, miner_index=miner_index)
        try:
            out = {"out": ga.select_device(devices, args)}
        except (RuntimeError, ValueError) as error:
            out = {"error": f"{type(error).__name__}: {error}"}
        cases.append(
            {
                "kind": "select_device",
                "in": {"devices": devices, "uuid": uuid_value, "index": index, "miner_index": miner_index},
                **out,
            }
        )
    gpu = AGENT_GPU
    for snapshot, count in [
        ({"rig": {"devices": {"GPU-abc-123": {"miner": {"hashrate": 1}}}}}, 1),
        ({"rig": {"devices": {"GPU-x": {}}}}, 1),
        ({"devices": [{"identity": {"uuid": "GPU-abc-123"}, "miner": {}}]}, 2),
        ({"devices": [5, {"gpu_uuid": "GPU-abc-123", "n": 2}]}, 2),
        ({"devices": [{"gpu": {"uuid": "GPU-x"}}]}, 1),
        ({"gpu": {"uuid": "GPU-abc-123"}, "miner": {}}, 3),
        ({"gpu_agent": {"gpu": {"uuid": "GPU-abc-123"}}}, 3),
        ({"miner": {}}, 1),
        ({"miner": {}}, 2),
        ({"gpu": {"uuid": "GPU-x"}}, 1),
        ({"rig": {"devices": {"GPU-abc-123": [1]}}}, 1),
        ({"miner": {}}, 0),
    ]:
        cases.append(
            {
                "kind": "snapshot_device_payload",
                "in": {"snapshot": snapshot, "count": count},
                "out": ga.snapshot_device_payload(snapshot, gpu, count),
            }
        )
    with tempfile.TemporaryDirectory() as temporary:
        stop = Path(temporary) / "gpu-agent-stop.json"
        for content, now in [
            (None, 100.0),
            ('{"gpu_uuid": "GPU-abc-123", "pid": "{self}", "at": 99}', 100.0),
            ('{"gpu_uuid": "GPU-abc-123", "pid": "{self}", "at": 30}', 100.0),
            ('{"gpu_uuid": "GPU-abc-123", "pid": "{self}", "at": 101.5}', 100.0),
            ('{"gpu_uuid": "GPU-abc-123", "pid": "{self}", "at": 103}', 100.0),
            ('{"gpu_uuid": "GPU-abc-123", "pid": "{self}", "at": 40}', 100.0),
            ('{"gpu_uuid": "GPU-x", "pid": "{self}", "at": 99}', 100.0),
            ('{"gpu_uuid": "GPU-abc-123", "pid": 1, "at": 99}', 100.0),
            ("[1]", 100.0),
            ("{x", 100.0),
            ('{"gpu_uuid": "GPU-abc-123", "pid": "{self}", "at": "99"}', 100.0),
        ]:
            if content is None:
                stop.unlink(missing_ok=True)
            else:
                stop.write_text(content.replace('"{self}"', str(os.getpid())), encoding="utf-8")
            cases.append(
                {
                    "kind": "requested_control_stop",
                    "in": {"content": content, "now": now, "started": 50.0},
                    "out": ga.requested_control_stop(temporary, "GPU-abc-123", 50.0, now),
                }
            )
        first, second = Path(temporary) / "a", Path(temporary) / "b"
        first.mkdir()
        second.mkdir()
        for files in [
            {},
            {"a/thermal-control.json": {"controller_pid": "{self}", "enabled": True}},
            {"a/gpu-agent-control.json": {"controller_pid": 4294967290, "enabled": True, "gpu_uuid": "GPU-abc-123"}},
            {"b/gpu-agent-control.json": {"controller_pid": None, "enabled": True, "gpu_uuid": "GPU-abc-123"}},
            {"b/gpu-agent-control.json": {"controller_pid": None, "enabled": False}},
            {"a/thermal-control.json": {"controller_pid": None, "enabled": True, "gpu": {"uuid": "GPU-x"}}},
            {"a/thermal-control.json": {"controller_pid": None, "enabled": True, "gpu_index": 1}},
            {"a/thermal-control.json": {"controller_pid": None, "enabled": True, "gpu_index": "abc"}},
            {"a/thermal-control.json": {"controller_pid": None, "enabled": 1, "gpu_index": "0"}},
            {"b/thermal-control.json": "texto"},
        ]:
            for folder in (first, second):
                for name in ("thermal-control.json", "gpu-agent-control.json"):
                    (folder / name).unlink(missing_ok=True)
            for name, payload in files.items():
                text = json.dumps(payload).replace('"{self}"', str(os.getpid()))
                (Path(temporary) / name).write_text("{x" if payload == "texto" else text, encoding="utf-8")
            for uuid_value, index in [("GPU-abc-123", 0), (None, None)]:
                cases.append(
                    {
                        "kind": "external_supervisor_active",
                        "in": {"files": files, "gpu_uuid": uuid_value, "gpu_index": index},
                        "out": ga.external_supervisor_active(first, second, gpu_uuid=uuid_value, gpu_index=index),
                    }
                )
    return cases


class _AgentAdvisor:
    def __init__(self, spec):
        self.spec = spec
        self.calls = []

    def advise(self, context):
        self.calls.append(context)
        if self.spec.startswith("ok:"):
            return {"action": self.spec[3:], "reason": "Sugestão da IA de teste"}
        raise ValueError("IA de teste indisponível " + "x" * 200)


def agent_tables(agent):
    tables = {}
    for table in ("benchmarks", "observations", "decisions", "trials", "adjustments"):
        tables[table] = [list(row) for row in agent.db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    return tables


def run_agent_trace(name, build):
    import uuid as uuid_module

    import gpu_agent as ga

    counter = itertools.count(1)
    ops = []
    with (
        tempfile.TemporaryDirectory() as temporary,
        mock.patch.object(ga.uuid, "uuid4", lambda: uuid_module.UUID(int=next(counter))),
        mock.patch.object(ga.time, "time", lambda: 5000.0),
    ):
        agent = ga.GPUAgent(temporary)
        gpus = {"gpu": AGENT_GPU, "ruim": AGENT_BAD_GPU}

        def run(op, gpu="gpu", **args):
            entry = {"op": op, "gpu": gpu, "args": args}
            try:
                if op == "set_advisor":
                    agent.advisor = _AgentAdvisor(args["spec"]) if args["spec"] else None
                    result = None
                elif op == "status":
                    result = agent.status(args["gpu_uuid"], now=args.get("now"))
                elif op == "recover_interrupted_trials":
                    result = agent.recover_interrupted_trials(
                        gpus[gpu], ownership_check=lambda: None, now=args.get("now")
                    )
                elif op == "export_policy":
                    result = agent.export_policy(gpus[gpu], Path(temporary) / "policy", now=args.get("now"))
                    policy = Path(temporary) / "policy" / "gpu-agent-policy.json"
                    entry["file"] = policy.read_text(encoding="utf-8") if policy.exists() else None
                else:
                    result = getattr(agent, op)(gpus[gpu], **args)
                entry["out"] = result
            except (ValueError, RuntimeError) as error:
                entry["error"] = f"{type(error).__name__}: {error}"
            if op == "recommend" and isinstance(agent.advisor, _AgentAdvisor) and agent.advisor.calls:
                entry["advisor_context"] = agent.advisor.calls.pop()
            ops.append(entry)
            return entry.get("out")

        build(run)
        tables = agent_tables(agent)
        agent.close()
    return {"kind": "agent", "name": name, "ops": ops, "tables": tables}


def trace_clean(run):
    run("status", gpu_uuid="GPU-abc-123", now=900.0)
    run("start_benchmark", duration_seconds=300, objective="performance", now=1000.0)
    for step in range(34):
        now = 1000.0 + step * 10
        hash_at = 1000.0 + (step // 2) * 20
        clock = 1590 if step < 17 else 1605
        run(
            "observe",
            sample=agent_sample(65.0 + (step % 3) * 0.3, clock, 170.0 + step % 4, at=now - 1),
            hashrate_ths=74.0 + (step % 5) * 0.37,
            accepted=step * 2,
            rejected=0,
            now=now,
            hash_sampled_at=hash_at,
            hw_errors=0,
        )
        if step % 4 == 0:
            run("recommend", sample=agent_sample(65.0, clock, at=now - 1), now=now + 0.5)
    run("status", gpu_uuid="GPU-abc-123", now=1400.0)
    run("export_policy", now=1400.0)
    run("recommend", sample=agent_sample(65.0, 1590, at=1400.0), now=1401.0)
    run("recommend", sample=agent_sample(65.0, 1590, at=1450.0), now=1450.0)


def trace_unstable(run):
    run("start_benchmark", duration_seconds=60, objective="efficiency", now=0.0)
    run("start_benchmark", duration_seconds=120, objective="efficiency", now=10.0)
    plan = [
        dict(sample=agent_sample(65, at=19), hashrate_ths=70, accepted=1, rejected=0, now=20.0, hash_sampled_at=5.0),
        dict(sample=agent_sample(65, at=29), hashrate_ths=70, accepted=2, rejected=0, now=30.0, hash_sampled_at=29.0),
        dict(sample=agent_sample(90, at=39), hashrate_ths=70, accepted=3, rejected=0, now=40.0, hash_sampled_at=39.0),
        dict(sample=agent_sample(65, at=20), hashrate_ths=70, accepted=4, rejected=0, now=50.0, hash_sampled_at=49.0),
        dict(sample=agent_sample(65, at=59), hashrate_ths=None, accepted=5, rejected=0, now=60.0, hash_sampled_at=59.0),
        dict(sample=agent_sample(65, at=69), hashrate_ths=70, accepted=6, rejected=0, now=70.0, hash_sampled_at=-200.0),
        dict(
            sample=agent_sample(65, power=0, at=79),
            hashrate_ths=70,
            accepted=7,
            rejected=0,
            now=80.0,
            hash_sampled_at=79.0,
        ),
        dict(sample=agent_sample(65, at=89), hashrate_ths=70, accepted=7.5, rejected=0, now=90.0, hash_sampled_at=89.0),
        dict(
            sample=agent_sample(65, at=99),
            hashrate_ths=70,
            accepted=8,
            rejected=0,
            now=100.0,
            hash_sampled_at=99.0,
            hw_errors=2,
        ),
        dict(
            sample=agent_sample(65, at=109), hashrate_ths=70, accepted=2, rejected=0, now=110.0, hash_sampled_at=109.0
        ),
        dict(
            sample=agent_sample(65, at=119), hashrate_ths=70, accepted=9, rejected=1, now=120.0, hash_sampled_at=119.0
        ),
        dict(
            sample=agent_sample(None, at=125), hashrate_ths=70, accepted=9, rejected=1, now=125.0, hash_sampled_at=124.0
        ),
        dict(
            sample=agent_sample(65, at=129),
            hashrate_ths="72.5",
            accepted="10",
            rejected=True,
            now=130.0,
            hash_sampled_at=129.0,
        ),
        dict(
            sample=agent_sample(65, at=135), hashrate_ths=70, accepted=-1, rejected=0, now=135.0, hash_sampled_at=134.0
        ),
        dict(
            sample=agent_sample(65, at=150), hashrate_ths=70, accepted=11, rejected=1, now=150.0, hash_sampled_at=149.0
        ),
    ]
    for args in plan:
        run("observe", **args)
        run("recommend", sample={**args["sample"], "sampled_at": args["now"] + 31}, now=args["now"] + 31)
    run(
        "observe",
        sample=agent_sample(65, at=160),
        hashrate_ths=70,
        accepted=12,
        rejected=1,
        now=160.0,
        hash_sampled_at=159.0,
    )
    run("start_benchmark", duration_seconds=30, objective="performance", now=170.0)
    run("start_benchmark", duration_seconds=300, objective="turbo", now=170.0)
    run("start_benchmark", gpu="ruim", duration_seconds=300, objective="performance", now=170.0)
    run("observe", gpu="ruim", sample=agent_sample(65), now=170.0)


def trace_recommend(run):
    run("recommend", sample=agent_sample(65, at=0), now=1.0)
    run("recommend", sample=agent_sample(65, at=0), now=20.0)
    run("recommend", sample=agent_sample(84, at=40), now=40.0)
    run("recommend", sample=agent_sample(80, at=41), now=41.0)
    run("recommend", sample=agent_sample(80, at=60), now=80.0)
    run("recommend", sample=agent_sample(76, at=100), now=100.0)
    run("start_benchmark", duration_seconds=300, objective="efficiency", now=200.0)
    for step in range(10):
        now = 200.0 + step * 10
        run(
            "observe",
            sample=agent_sample(66, 1590, at=now),
            hashrate_ths=75,
            accepted=step,
            rejected=0,
            now=now,
            hash_sampled_at=now - 1,
        )
    run("set_advisor", spec="ok:clock_down")
    run("recommend", sample=agent_sample(66, 1590, limit=220.0, at=300), ollama=True, now=300.0)
    run("recommend", sample=agent_sample(66, 1590, limit=220.0, at=340), ollama=True, now=340.0)
    run("set_advisor", spec="fail")
    run("recommend", sample=agent_sample(66, 1590, limit=220.0, at=380), ollama=True, now=380.0)
    run("set_advisor", spec="")
    run("recommend", sample=agent_sample(66, 1590, limit=220.0, at=420), ollama=True, now=420.0)
    run("set_advisor", spec="ok:power_up")
    run("recommend", sample=agent_sample(84, 1590, at=460), ollama=True, now=460.0)
    run("recommend", sample=agent_sample(66, 1590, util=50, at=500), ollama=True, now=500.0)
    run("start_benchmark", duration_seconds=300, objective="performance", now=600.0)
    for step in range(10):
        now = 600.0 + step * 10
        run(
            "observe",
            sample=agent_sample(66, 1590, at=now),
            hashrate_ths=75,
            accepted=step,
            rejected=0,
            now=now,
            hash_sampled_at=now - 1,
        )
    run("recommend", sample=agent_sample(66, 1590, at=700), now=700.0)
    run("recommend", sample=agent_sample(74, 1590, at=740), now=740.0)
    run("start_benchmark", duration_seconds=300, objective="performance", now=800.0)
    for now, rejected in [(840.0, 0), (850.0, 0), (860.0, 1)]:
        run(
            "observe",
            sample=agent_sample(66, 1590, at=now),
            hashrate_ths=75,
            accepted=5,
            rejected=rejected,
            now=now,
            hash_sampled_at=now - 1,
        )
    run("recommend", sample=agent_sample(66, 1590, at=870), now=870.0)


def trace_trials(run):
    before = agent_sample(66, 1590, at=0.0)
    plan_up = {
        "action": "clock_up",
        "clock_mhz": 1605,
        "power_limit_w": None,
        "blocked": None,
        "gpu_uuid": "GPU-abc-123",
        "at": 0.0,
    }
    run("assert_no_pending_trial")
    run("trial_feedback", sample=before, hashrate_ths=75, hash_sampled_at=0.0, now=1.0)
    run("record_application", plan=plan_up, before=before, baseline_hashrate=0, now=0.0)
    run("record_application", plan={**plan_up, "gpu_uuid": "GPU-x"}, before=before, baseline_hashrate=75, now=0.0)
    run("record_application", plan=plan_up, before=before, baseline_hashrate=75, now=0.0)
    run("assert_no_pending_trial")
    run("record_application", plan=plan_up, before=before, baseline_hashrate=75, now=1.0)
    for now, rate, hash_at in [
        (20.0, 74.0, 19.0),
        (40.0, 74.5, 35.0),
        (60.0, 74.5, 35.0),
        (80.0, 76.0, 79.0),
        (95.0, 75.0, 94.0),
    ]:
        run(
            "trial_feedback", sample=agent_sample(67, 1605, at=now), hashrate_ths=rate, hash_sampled_at=hash_at, now=now
        )
    run("record_application", plan=plan_up, before=before, baseline_hashrate=75, now=100.0)
    for now, rate, hash_at in [(140.0, 70.0, 139.0), (170.0, 71.0, 169.0), (200.0, 70.5, 199.0)]:
        run(
            "trial_feedback", sample=agent_sample(67, 1605, at=now), hashrate_ths=rate, hash_sampled_at=hash_at, now=now
        )
    plan_power = {
        "action": "power_down",
        "clock_mhz": None,
        "power_limit_w": 215.0,
        "blocked": None,
        "gpu_uuid": "GPU-abc-123",
        "at": 300.0,
    }
    run("record_application", plan=plan_power, before=before, baseline_hashrate=75, now=300.0)
    run(
        "trial_feedback",
        sample=agent_sample(67, at=310),
        hashrate_ths=75,
        hash_sampled_at=309.0,
        new_rejected_shares=1,
        now=310.0,
    )
    run("record_application", plan=plan_power, before=before, baseline_hashrate=75, now=400.0)
    run(
        "trial_feedback",
        sample=agent_sample(77, 1590, limit=215.0, at=410),
        hashrate_ths=75,
        hash_sampled_at=409.0,
        now=410.0,
    )
    run("record_application", plan=plan_up, before=before, baseline_hashrate=75, now=500.0)
    run("trial_feedback", sample=agent_sample(67, 1605, at=745), hashrate_ths=None, hash_sampled_at=None, now=745.0)
    run(
        "record_application", plan={**plan_up, "action": "restore_best"}, before=before, baseline_hashrate=75, now=800.0
    )
    run("cancel_trial", sample=agent_sample(67, 1605, at=805), now=805.0)
    run("cancel_trial", sample=agent_sample(67, 1605, at=806), now=806.0)
    run("record_application", plan=plan_power, before=before, baseline_hashrate=75, now=900.0)
    run("cancel_trial", sample=agent_sample(80, 1605, limit=215.0, at=905), now=905.0)
    run("record_application", plan=plan_up, before=before, baseline_hashrate=75, now=1000.0)
    run("recover_interrupted_trials", now=1001.0)
    run("recover_interrupted_trials", now=1002.0)
    run(
        "record_adjustment",
        before=before,
        after=agent_sample(68, 1605, at=1.0),
        plan=plan_up,
        hashrate=75.5,
        now=1003.0,
    )
    run(
        "record_adjustment",
        before=before,
        after=agent_sample(68, 1605, at=1.0),
        plan={"action": "clock_down"},
        hashrate="abc",
        now=1004.0,
    )
    run("record_adjustment", gpu="ruim", before=before, after=before, plan=plan_up, now=1005.0)


def trace_bench_bounds(run):
    # Empate de perfis (o primeiro vence), cobertura exata de 70 % e sensor com 10 s exatos.
    run("start_benchmark", duration_seconds=100, objective="performance", now=1000.0)
    for step in range(8):
        now = 1030.0 + 10 * step
        at = now - 10 if step == 0 else now
        run(
            "observe",
            sample=agent_sample(66, 1590 if step < 4 else 1605, at=at),
            hashrate_ths=75.0,
            accepted=step,
            rejected=0,
            now=now,
            hash_sampled_at=now,
        )
    run("status", gpu_uuid="GPU-abc-123", now=1100.0)
    # Sensor 2,5 s no futuro, intervalo de 15 s exatos e hashrate com 180 s exatos.
    run("start_benchmark", duration_seconds=300, objective="performance", now=2000.0)
    run(
        "observe",
        sample=agent_sample(66, at=2032.5),
        hashrate_ths=75.0,
        accepted=1,
        rejected=0,
        now=2030.0,
        hash_sampled_at=2030.0,
    )
    run(
        "observe",
        sample=agent_sample(66, at=2045.0),
        hashrate_ths=75.0,
        accepted=2,
        rejected=0,
        now=2045.0,
        hash_sampled_at=2045.0,
    )
    run(
        "observe",
        sample=agent_sample(66, at=2200.0),
        hashrate_ths=75.0,
        accepted=3,
        rejected=0,
        now=2200.0,
        hash_sampled_at=2020.0,
    )
    run("status", gpu_uuid="GPU-abc-123", now=2200.0)


def trace_recommend_bounds(run):
    run("recommend", sample=agent_sample(66, at=0.0), now=10.0)
    run("start_benchmark", duration_seconds=300, objective="performance", now=100.0)
    for now in (130.0, 140.0):
        run(
            "observe",
            sample=agent_sample(66, at=now),
            hashrate_ths=75.0,
            accepted=1,
            rejected=0,
            now=now,
            hash_sampled_at=now,
        )
    # 60 s exatos de benchmark, uso de 90 % e temperatura no limite suave menos 3 °C.
    run("recommend", sample=agent_sample(73, util=90, at=160.0), now=160.0)
    # 30 s exatos depois do último ajuste.
    run("recommend", sample=agent_sample(70, at=190.0), now=190.0)
    # Acima do limite suave a sugestão da IA não vale.
    run("set_advisor", spec="ok:power_up")
    run("recommend", sample=agent_sample(77, at=230.0), ollama=True, now=230.0)


def trace_trial_bounds(run):
    before = agent_sample(66, 1590, at=0.0)
    plan_up = {
        "action": "clock_up",
        "clock_mhz": 1605,
        "power_limit_w": None,
        "blocked": None,
        "gpu_uuid": "GPU-abc-123",
        "at": 0.0,
    }
    run("record_application", plan=plan_up, before=before, baseline_hashrate=100.0, now=0.0)
    # Hashrate com 30 s exatos do início e 180 s exatos de idade; perda de 2,5 % é aceita.
    run("trial_feedback", sample=agent_sample(67, 1605, at=210.0), hashrate_ths=97.5, hash_sampled_at=30.0, now=210.0)
    run("trial_feedback", sample=agent_sample(67, 1605, at=230.0), hashrate_ths=97.5, hash_sampled_at=50.0, now=230.0)
    run("record_application", plan=plan_up, before=before, baseline_hashrate=100.0, now=1000.0)
    # 240 s exatos ainda é validação.
    run("trial_feedback", sample=agent_sample(67, 1605, at=1240.0), hashrate_ths=None, hash_sampled_at=None, now=1240.0)


def gpu_agent_golden():
    cases = gpu_agent_pure_cases() + gpu_agent_read_cases() + gpu_agent_selection_cases()
    for name, build in [
        ("limpo", trace_clean),
        ("instavel", trace_unstable),
        ("recomendacoes", trace_recommend),
        ("testes", trace_trials),
        ("fronteiras_benchmark", trace_bench_bounds),
        ("fronteiras_recomendacao", trace_recommend_bounds),
        ("fronteiras_teste", trace_trial_bounds),
    ]:
        cases.append(run_agent_trace(name, build))
    write("gpu_agent", {"gpu": AGENT_GPU, "bad_gpu": AGENT_BAD_GPU, "cases": cases})


# Laço do supervisor (`gpu_agent.control`). Tudo fora do laço é simulado: nvidia-smi
# (temperatura segue o clock travado), relógio virtual, processo do minerador e leitura do
# minerador. O teste compara cada comando nvidia-smi, o status final e a memória térmica.
CONTROL_GPU = {
    "vendor": "NVIDIA",
    "uuid": "GPU-ctl-0001",
    "index": 0,
    "name": "NVIDIA GeForce RTX 3070",
    "supported_graphics_clocks_mhz": list(range(1410, 1891, 15)),
    "power_min_w": 100.0,
    "power_max_w": 240.0,
    "power_default_w": 220.0,
    "temperature_limit_c": 93,
}
CONTROL_T0 = 1794000000.0


def control_model_temp(spec, cycle, clock):
    temps = spec["temps"]
    return round(temps[min(cycle, len(temps) - 1)] + 0.02 * (clock - 1600), 1)


def control_model_rate(clock):
    return round(70 + 0.03 * (clock - 1500), 2)


CONTROL_SCENARIOS = {
    "baseline_sem_alvo": {"temps": [64.0], "alive": 175},
    "baseline_quente": {"temps": [70.0, 71.0, 72.0, 74.0, 75.0], "alive": 175},
    "alvo_com_memoria": {
        "temps": [74.0, 74.0, 73.0, 72.0],
        "alive": 700,
        "target": 70,
        "memory": [7001, 6801, 5001, 405],
    },
    "alvo_frio": {"temps": [60.0], "alive": 90, "target": 72, "memory": [7001], "remembered": {"clock_mhz": 1590}},
    "emergencia_com_alvo": {"temps": [86.0], "alive": 30, "target": 70},
    "quente_sem_alvo": {"temps": [84.0], "alive": 30},
    "parada_pedida": {
        "temps": [64.0],
        "alive": 50,
        "events": {4: [["gpu-agent-stop.json", {"gpu_uuid": "GPU-ctl-0001", "pid": "<pid>", "at": "<now>"}]]},
    },
    "parada_outra_gpu": {
        "temps": [64.0],
        "alive": 8,
        "events": {2: [["gpu-agent-stop.json", {"gpu_uuid": "GPU-x", "pid": "<pid>", "at": "<now>"}]]},
    },
    "pedido_benchmark": {
        "temps": [64.0],
        "alive": 30,
        "events": {
            3: [
                [
                    "gpu-agent-request.json",
                    {
                        "action": "benchmark",
                        "gpu_uuid": "GPU-ctl-0001",
                        "at": "<now>",
                        "profile": "balanced",
                        "objective": "efficiency",
                        "duration_seconds": 600,
                    },
                ]
            ],
            9: [
                [
                    "gpu-agent-request.json",
                    {"action": "benchmark", "gpu_uuid": "GPU-ctl-0001", "at": "<now>", "objective": "voar"},
                ]
            ],
            14: [["gpu-agent-request.json", "nao json"]],
        },
    },
    "troca_de_alvo": {
        "temps": [72.0],
        "alive": 40,
        "events": {
            7: [["<settings>", {"target_temperature_c": 70}]],
            26: [["<settings>", {"target_temperature_c": 75}]],
        },
    },
    "sem_admin": {"temps": [64.0], "alive": 5, "admin": False},
    "minerador_desconhecido": {"temps": [64.0], "alive": 5, "executable": "c:\\x\\outro.exe"},
    "minerador_morto": {"temps": [64.0], "alive": 0},
    "duracao_invalida": {"temps": [64.0], "alive": 5, "duration": 299},
    "outro_supervisor": {
        "temps": [64.0],
        "alive": 5,
        "files": {"thermal-control.json": {"enabled": True, "controller_pid": 4, "gpu_uuid": "GPU-ctl-0001"}},
    },
    "trava_legada": {
        "temps": [64.0],
        "alive": 5,
        "files": {"gpu-control-owner.json": {"pid": 4, "token": "x", "gpu_uuid": "GPU-ctl-0001"}},
    },
    "amostras": {"temps": [64.0], "alive": 50, "samples": 3},
    "alvo_muito_acima": {"temps": [70.0], "alive": 20, "target": 65},
    "partida_ociosa": {"temps": [55.0], "alive": 12, "target": 70, "start_clock": 900},
    "perfil_frio": {"temps": [66.0, 67.0, 68.0], "alive": 60, "profile": "cool"},
    "pedidos_limite": {
        "temps": [64.0],
        "alive": 20,
        "events": {
            2: [
                [
                    "gpu-agent-request.json",
                    {"action": "benchmark", "gpu_uuid": "GPU-ctl-0001", "at": "<now>", "duration_seconds": 3601},
                ]
            ],
            5: [
                [
                    "gpu-agent-request.json",
                    {"action": "benchmark", "gpu_uuid": "GPU-ctl-0001", "at": "<now-30>", "objective": "efficiency"},
                ]
            ],
        },
    },
    "trava_legada_morta": {
        "temps": [64.0],
        "alive": 3,
        "files": {"gpu-control-owner.json": {"pid": 4000000, "token": "x", "gpu_uuid": "GPU-ctl-0001"}},
    },
    "telemetria_velha": {"temps": [64.0], "alive": 160, "fresh": False},
    "subida_bloqueada": {"temps": [75.5], "alive": 40, "target": 80},
}


def run_control_scenario(name, spec):
    import component_status
    import config as hp_config
    import ctypes

    import gpu_agent as ga

    real_windll = ctypes.WinDLL
    state = {
        "now": CONTROL_T0,
        "cycle": 0,
        "clock": spec.get("start_clock", 1860),
        "limit": 220.0,
        "memory": (spec.get("memory") or [7001])[0],
    }
    log = []
    trace = []
    ids = iter(f"{number:032x}" for number in range(1, 10_000))

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        state_dir, global_dir = root / "gpu", root / "global"
        state_dir.mkdir()
        global_dir.mkdir()
        settings = global_dir / "settings.json"
        base_settings = {} if spec.get("target") is None else {"target_temperature_c": spec["target"]}
        settings.write_text(json.dumps(base_settings), encoding="utf-8")
        for file_name, payload in spec.get("files", {}).items():
            (state_dir / file_name).write_text(json.dumps(payload), encoding="utf-8")
        if spec.get("remembered"):
            (state_dir / "thermal-target.json").write_text(
                json.dumps({"gpu_uuid": CONTROL_GPU["uuid"], **spec["remembered"]}), encoding="utf-8"
            )

        def fake_smi(arguments, timeout=4):
            arguments = [str(item) for item in arguments]
            log.append(arguments)
            joined = " ".join(arguments)
            if "--query-gpu=temperature.gpu" in joined:
                temp = control_model_temp(spec, state["cycle"], state["clock"])
                return (
                    f"{temp:.1f}, {150 + (state['clock'] - 1500) / 10:.1f}, {state['limit']:.2f}, {state['clock']}, 100"
                )
            if "--query-gpu=fan.speed" in joined:
                return "61"
            if "--query-supported-clocks=memory" in joined:
                return "\n".join(str(item) for item in spec.get("memory") or [])
            if "--query-gpu=clocks.mem" in joined:
                return str(state["memory"])
            if "-lgc" in arguments:
                state["clock"] = int(arguments[arguments.index("-lgc") + 1].split(",")[0])
            elif "-pl" in arguments:
                state["limit"] = float(arguments[arguments.index("-pl") + 1])
            elif "-lmc" in arguments:
                state["memory"] = int(arguments[arguments.index("-lmc") + 1].split(",")[0])
            elif "-rgc" in arguments:
                state["clock"] = 1860
            return ""

        def substitute(value):
            if value == "<pid>":
                return os.getpid()
            if value == "<now>":
                return state["now"]
            if value == "<now-30>":
                return state["now"] - 30
            if isinstance(value, dict):
                return {key: substitute(item) for key, item in value.items()}
            return value

        def sleep(seconds):
            try:
                current = json.loads((state_dir / "gpu-agent-control.json").read_text(encoding="utf-8"))
            except OSError, ValueError:
                current = {}
            control = current.get("target_control") or {}
            trace.append(
                [
                    current.get("mode"),
                    current.get("writes"),
                    control.get("action"),
                    control.get("reason"),
                    control.get("ceiling_mhz"),
                ]
            )
            state["now"] += seconds
            state["cycle"] += 1
            for file_name, payload in spec.get("events", {}).get(state["cycle"], []):
                target = settings if file_name == "<settings>" else state_dir / file_name
                content = payload if isinstance(payload, str) else json.dumps(substitute(payload))
                target.write_text(content, encoding="utf-8")

        def read_miner(args, gpu, monitor, count, now=None):
            rate = control_model_rate(state["clock"])
            return {
                "hashrate": rate,
                "hashrate_at": state["now"] - 5,
                "accepted": 2 * state["cycle"],
                "rejected": 0,
                "hw_errors": 0,
            }, spec.get("fresh", True)

        class FakeShell:
            def IsUserAnAdmin(self):  # noqa: N802
                return spec.get("admin", True)

        def windll(name, *args, **kwargs):
            return FakeShell() if name == "shell32" else real_windll(name, *args, **kwargs)

        identity = {"created_filetime": 1, "executable": spec.get("executable", "c:\\mineradores\\srbminer-multi.exe")}
        args = types.SimpleNamespace(
            duration=spec.get("duration", 300),
            objective="performance",
            profile=spec.get("profile", "performance"),
            gpu=0,
            uuid=CONTROL_GPU["uuid"],
            miner_index=None,
            miner_pid=4242,
            samples=spec.get("samples", 0),
            ollama=False,
            log_dir=None,
            miner_log=None,
        )
        originals = {
            name: getattr(ga, name) for name in ("read_sample", "read_fan", "read_memory_clocks", "read_memory_clock")
        }
        with contextlib.ExitStack() as stack:
            patch = lambda target, attribute, value: stack.enter_context(mock.patch.object(target, attribute, value))  # noqa: E731
            patch(ga.time, "time", lambda: state["now"])
            patch(ga.time, "sleep", sleep)
            patch(ga.uuid, "uuid4", lambda: types.SimpleNamespace(hex=next(ids)))
            patch(ga, "smi_command", fake_smi)
            patch(ga, "detect_nvidia_gpus", lambda *a, **k: [dict(CONTROL_GPU)])
            for helper, original in originals.items():
                patch(ga, helper, lambda gpu, command=None, _original=original: _original(gpu, fake_smi))
            patch(ga, "process_alive", lambda pid: pid == 4242 and state["cycle"] < spec["alive"])
            patch(ga, "process_identity", lambda pid: dict(identity) if pid == 4242 else None)
            patch(ga, "read_miner_data", read_miner)
            patch(ctypes, "WinDLL", windll)
            patch(hp_config, "STATE_DIR", global_dir)
            patch(hp_config, "SETTINGS_PATH", settings)
            patch(component_status, "identity", lambda *a, **k: {"version": "teste"})
            try:
                outcome = {"code": ga.control(args, state_dir, state_dir)}
            except (OSError, ValueError, RuntimeError) as error:
                outcome = {"error": f"{type(error).__name__}: {error}"}

        def load(file_name):
            try:
                return json.loads((state_dir / file_name).read_text(encoding="utf-8-sig"))
            except OSError, ValueError:
                return None

        status = load("gpu-agent-control.json")
        if isinstance(status, dict) and status.get("controller_pid") == os.getpid():
            status["controller_pid"] = "<pid>"
        memory = load("thermal-target.json")
        tables = {}
        if (state_dir / "gpu-agent.sqlite3").exists():
            import sqlite3

            with contextlib.closing(sqlite3.connect(state_dir / "gpu-agent.sqlite3")) as db:
                for table in ("benchmarks", "observations", "decisions", "trials", "adjustments"):
                    tables[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return {
            "kind": "control",
            "name": name,
            "spec": {**spec, "events": {str(k): v for k, v in spec.get("events", {}).items()}},
            **outcome,
            "cycles": state["cycle"],
            "smi": log,
            "trace": trace,
            "status": status,
            "memory": memory,
            "owner_left": (state_dir / "gpu-control-owner.json").exists(),
            "tables": tables,
        }


def control_loop_golden():
    cases = [run_control_scenario(name, spec) for name, spec in CONTROL_SCENARIOS.items()]
    write("control_loop", {"gpu": CONTROL_GPU, "t0": CONTROL_T0, "cases": cases})


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
    "rig_manager": rig_manager_golden,
    "gpu_control": gpu_control_golden,
    "nvidia_detect": nvidia_detect_golden,
    "thermal_target": thermal_target_golden,
    "contribution": contribution_golden,
    "ai_advisor": ai_advisor_golden,
    "gpu_agent": gpu_agent_golden,
    "control_loop": control_loop_golden,
}
# Goldens que não dependem do fuso nem de caminhos e podem ser gerados no Windows.
PORTABLE = {"history", "finance", "rig_support", "nvidia_detect", "thermal_target", "contribution", "ai_advisor"}
# Goldens que usam a API real do Windows (shell32 `CommandLineToArgvW`, `GetFullPathNameW`).
WINDOWS_ONLY = {"process", "rig_manager", "gpu_control", "gpu_agent", "control_loop"}


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
