"""Probe de co-emissão DP4A + tensor core (MMA) na SM86 — medição, não mineração.

Reaproveita o Driver/telemetria de ampere_int8_probe.py (mesmo padrão seguro:
restrito à SM86, recusa rodar com minerador ativo, PTX pelo JIT do driver, cada
saída conferida contra referência de CPU). NÃO move dados de GEMM, não gera
seeds, ruído, BLAKE3 ou prova, e não envia shares.

Pergunta (ideia 1): a DP4A (4 MACs INT8 por thread nos núcleos CUDA) pode somar
throughput enquanto o tensor core faz MMA? Mede três tempos — só MMA, só DP4A,
e os dois intercalados — e reporta se o tempo combinado fica abaixo da soma
(indício de sobreposição) ou perto dela (serializado). A interpretação fica no
README; aqui só saem os números medidos e conferidos.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import time
import ctypes as c

from ampere_int8_probe import Driver, telemetry

PACK = 16843009  # 0x01010101: replica um byte assinado nas 4 lanes de um u32.


def preamble() -> str:
    # Operandos uniformes por warp, idênticos aos do probe MMA: va=warp%7-3, vb=warp%5-2.
    return """.reg .u64 ptr, addr, offset;
.reg .u32 tid, bid, linear, warp, va, vb, a, b, count, limit;
.reg .pred again;
ld.param.u64 ptr, [out];
ld.param.u32 limit, [iterations];
mov.u32 tid, %tid.x;
mov.u32 bid, %ctaid.x;
mad.lo.u32 linear, bid, 128, tid;
shr.u32 warp, linear, 5;
rem.u32 va, warp, 7;
sub.u32 va, va, 3;
rem.u32 vb, warp, 5;
sub.u32 vb, vb, 2;
and.b32 a, va, 255;
and.b32 b, vb, 255;
mul.lo.u32 a, a, 16843009;
mul.lo.u32 b, b, 16843009;"""


def ptx(mode: str, chains: int) -> str:
    mma_regs = [f"d{i}" for i in range(4 * chains)] if mode in ("mma", "both") else []
    dp_regs = [f"e{i}" for i in range(chains)] if mode in ("dp4a", "both") else []
    decls = "\n".join(f".reg .s32 {r};" for r in mma_regs + dp_regs)
    init = "\n".join(f"mov.s32 {r}, {n};" for n, r in enumerate(mma_regs)) + "\n" + \
           "\n".join(f"mov.s32 {r}, {n};" for n, r in enumerate(dp_regs))
    body = []
    for j in range(chains):
        if mode in ("mma", "both"):
            d = ", ".join(f"d{4*j+k}" for k in range(4))
            body.append(f"mma.sync.aligned.m16n8k32.row.col.s32.s8.s8.s32 {{{d}}}, {{a,a,a,a}}, {{b,b}}, {{{d}}};")
        if mode in ("dp4a", "both"):
            body.append(f"dp4a.s32.s32 e{j}, a, b, e{j};")
    stores, slot = [], 0
    for r in mma_regs + dp_regs:
        stores.append(f"st.global.u32 [addr+{4*slot}], {r};")
        slot += 1
    stride = 16 * chains if mode == "mma" else 4 * chains if mode == "dp4a" else 20 * chains
    return f""".version 7.1
.target sm_86
.address_size 64
.visible .entry probe(.param .u64 out, .param .u32 iterations) {{
{preamble()}
{decls}
{init}
mov.u32 count, 0;
LOOP:
{chr(10).join(body)}
add.u32 count, count, 1;
setp.lt.u32 again, count, limit;
@again bra LOOP;
mul.wide.u32 offset, linear, {stride};
add.u64 addr, ptr, offset;
{chr(10).join(stores)}
ret;
}}
"""


def measure(driver, sm_count, mode, chains, repetitions, waves):
    module, function = c.c_void_p(), c.c_void_p()
    memory = c.c_uint64()
    events = []
    blocks, iterations, batch = sm_count * waves, 2048, 10
    per_thread = (4 * chains if mode == "mma" else chains if mode == "dp4a" else 5 * chains)
    count = blocks * 128 * per_thread
    source = c.create_string_buffer(ptx(mode, chains).encode("ascii"))
    try:
        log = c.create_string_buffer(8192)
        options = (c.c_int * 2)(5, 6)
        values = (c.c_void_p * 2)(c.addressof(log), len(log))
        try:
            driver.call("cuModuleLoadDataEx", c.byref(module), source, 2, options, values)
        except RuntimeError as error:
            raise RuntimeError(f"{error}: {log.value.decode(errors='replace')}") from error
        driver.call("cuModuleGetFunction", c.byref(function), module, b"probe")
        registers = c.c_int()
        driver.call("cuFuncGetAttribute", c.byref(registers), 4, function)
        driver.call("cuMemAlloc_v2", c.byref(memory), count * 4)
        n = c.c_uint(iterations)
        args = (c.c_void_p * 2)(c.addressof(memory), c.addressof(n))

        def launch():
            driver.call("cuLaunchKernel", function, blocks, 1, 1, 128, 1, 1, 0, None, args, None)

        for _ in range(2):
            event = c.c_void_p()
            driver.call("cuEventCreate", c.byref(event), 0)
            events.append(event)
        for _ in range(4):
            launch()
        milliseconds = []
        for _ in range(repetitions):
            driver.call("cuEventRecord", events[0], None)
            for _ in range(batch):
                launch()
            driver.call("cuEventRecord", events[1], None)
            driver.call("cuEventSynchronize", events[1])
            duration = c.c_float()
            driver.call("cuEventElapsedTime", c.byref(duration), *events)
            per_kernel = duration.value / batch
            if not 0 < per_kernel < 100:
                raise RuntimeError("Kernel duration outside 0..100 ms; probe stopped")
            milliseconds.append(per_kernel)
        data = (c.c_int * count)()
        driver.call("cuMemcpyDtoH_v2", data, memory, count * 4)
        mma_macs = dp_macs = 0
        for thread in range(blocks * 128):
            warp = thread // 32
            product = (warp % 7 - 3) * (warp % 5 - 2)
            base = thread * per_thread
            slot = 0
            if mode in ("mma", "both"):
                for j in range(4 * chains):
                    expected = j + iterations * 32 * product
                    if data[base + slot] != expected:
                        raise RuntimeError(f"MMA mismatch at {thread}/{j}: {data[base+slot]} != {expected}")
                    slot += 1
            if mode in ("dp4a", "both"):
                for j in range(chains):
                    expected = j + iterations * 4 * product
                    if data[base + slot] != expected:
                        raise RuntimeError(f"DP4A mismatch at {thread}/{j}: {data[base+slot]} != {expected}")
                    slot += 1
        if mode in ("mma", "both"):
            mma_macs = blocks * 4 * chains * iterations * 16 * 8 * 32
        if mode in ("dp4a", "both"):
            dp_macs = blocks * 128 * chains * iterations * 4
        total = mma_macs + dp_macs
        rates = [total / (ms / 1000) / 1e12 for ms in milliseconds]
        return {"mode": mode, "chains": chains, "waves": waves, "blocks": blocks,
                "registers_per_thread": registers.value,
                "ptx_sha256": hashlib.sha256(source.value).hexdigest(),
                "iterations": iterations, "verified_outputs": count,
                "mma_tmac_fraction": (mma_macs / total) if total else 0,
                "kernel_ms": milliseconds, "total_tmac_s": rates,
                "median_total_tmac_s": statistics.median(rates),
                "correctness": "all outputs exact; warp-uniform operands; not a GEMM layout or PoW proof test"}
    finally:
        for event in events:
            driver.call("cuEventDestroy_v2", event)
        if memory.value:
            driver.call("cuMemFree_v2", memory)
        if module.value:
            driver.call("cuModuleUnload", module)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--repetitions", type=int, default=9)
    parser.add_argument("--chains", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 50:
        parser.error("repetitions must be in 3..50")
    if not 1 <= args.chains <= 16:
        parser.error("chains must be in 1..16")
    tasks = subprocess.check_output(["nvidia-smi", "--query-compute-apps=process_name", "--format=csv,noheader"], text=True, timeout=10)
    if any(word in tasks.lower() for word in ("srbminer", "freeforgem", "pearl-miner")):
        raise RuntimeError("An active miner is visible; stop it through its manager before probing")
    driver = Driver()
    driver.call("cuInit", 0)
    device, context = c.c_int(), c.c_void_p()
    driver.call("cuDeviceGet", c.byref(device), args.device)
    major, minor = (driver.attribute(device, key) for key in (75, 76))
    if (major, minor) != (8, 6):
        raise RuntimeError("This research probe is restricted to SM86")
    name = c.create_string_buffer(256)
    driver.call("cuDeviceGetName", name, len(name), device)
    sms = driver.attribute(device, 16)
    driver_version = c.c_int()
    driver.call("cuDriverGetVersion", c.byref(driver_version))
    report = {"kind": "DP4A + MMA co-issue probe; NOT Pearl hashrate, NOT a proof test",
              "sampled_at": time.time(),
              "cuda_driver_api_version": driver_version.value,
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "gpu": name.value.decode(), "sm_count": sms, "sm": "86", "chains": args.chains, "results": [],
              "reading": "Se median(both) ~ max(mma, dp4a): sobreposicao. Se ~ mma+dp4a: serializado. "
                         "DP4A e MMA disputam a emissao do warp scheduler; o INT32 tambem e usado pelo BLAKE3 real.",
              "limitations": ["runs curtos, sem aquecimento termico controlado",
                              "telemetria amostrada fora dos kernels cronometrados",
                              "operandos uniformes validam aritmetica, nao layouts de matriz arbitrarios",
                              "sem movimento de memoria de GEMM; o co-issue real num minerador sofre pressao extra de LD/ST"]}
    driver.call("cuDevicePrimaryCtxRetain", c.byref(context), device)
    try:
        driver.call("cuCtxSetCurrent", context)
        for waves in (1, 4):
            for mode in ("mma", "dp4a", "both"):
                before = telemetry(args.device)
                result = measure(driver, sms, mode, args.chains, args.repetitions, waves)
                result.update(telemetry_before=before, telemetry_after=telemetry(args.device))
                report["results"].append(result)
                print(json.dumps({k: result[k] for k in ("mode", "waves", "median_total_tmac_s", "correctness")}))
    finally:
        driver.call("cuDevicePrimaryCtxRelease_v2", device)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
