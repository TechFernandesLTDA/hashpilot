"""Gera as respostas de referência (goldens) que o port em Rust precisa reproduzir.

Roda os módulos Python REAIS do HashPilot sobre entradas fixas e grava JSON em
rust/hashpilot-core/tests/golden/. Os testes de `cargo test` comparam o Rust com
esses arquivos: se o comportamento do Python mudar, regere e revise o diff.

Executa com TZ=UTC (o carimbo do log do SRBMiner é hora local; fixar o fuso torna
os goldens independentes da máquina) e com estado temporário isolado.

Uso (na raiz do repositório):
    python tools/rust_parity/generate_goldens.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
# HASHPILOT_GOLDEN_OUT permite regerar num diretório temporário (test_rust_parity_goldens.py).
OUT = Path(os.environ.get("HASHPILOT_GOLDEN_OUT") or ROOT / "rust/hashpilot-core/tests/golden")
LOGS = ROOT / "portable-data/miner-logs"

os.environ["TZ"] = "UTC"
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


if __name__ == "__main__":
    numbers()
    miner_logs()
    thermal_sequences()
    valuations()
    payout_states()
    rig()
    configs()
