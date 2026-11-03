"""Independent SM86 INT8 instruction probe; no mining, firmware or clock changes.

Uses the installed NVIDIA CUDA driver JIT, not NVCC or third-party kernels.
Measures repeated register-resident MMA, NOT GEMM or Pearl hashrate.
Every output is checked against an exact CPU integer reference (uniform tiles).
"""
from __future__ import annotations
import argparse
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time


def ptx(chains: int) -> str:
    declarations = "\n".join(f".reg .s32 d{i};" for i in range(4 * chains))
    initialize = "\n".join(f"mov.s32 d{i}, {i};" for i in range(4 * chains))
    operations = []
    stores = []
    for j in range(chains):
        d = ", ".join(f"d{4*j+k}" for k in range(4))
        operations.append(
            "mma.sync.aligned.m16n8k32.row.col.s32.s8.s8.s32 "
            f"{{{d}}}, {{a,a,a,a}}, {{b,b}}, {{{d}}};"
        )
    for i in range(4 * chains):
        stores.append(f"st.global.u32 [addr+{4*i}], d{i};")
    return f""".version 7.1
.target sm_86
.address_size 64
.visible .entry probe(.param .u64 out, .param .u32 iterations) {{
.reg .u64 ptr, addr, offset;
.reg .u32 tid, bid, linear, warp, va, vb, a, b, count, limit;
.reg .pred again;
{declarations}
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
mul.lo.u32 b, b, 16843009;
{initialize}
mov.u32 count, 0;
LOOP:
{chr(10).join(operations)}
add.u32 count, count, 1;
setp.lt.u32 again, count, limit;
@again bra LOOP;
mul.wide.u32 offset, linear, {16 * chains};
add.u64 addr, ptr, offset;
{chr(10).join(stores)}
ret;
}}
"""


class Driver:
    def __init__(self):
        self.lib = c.WinDLL("nvcuda.dll") if os.name == "nt" else c.CDLL("libcuda.so.1")
        signatures = {
            "cuInit": [c.c_uint],
            "cuDeviceGet": [c.POINTER(c.c_int), c.c_int],
            "cuDeviceGetName": [c.c_void_p, c.c_int, c.c_int],
            "cuDeviceGetAttribute": [c.POINTER(c.c_int), c.c_int, c.c_int],
            "cuDriverGetVersion": [c.POINTER(c.c_int)],
            "cuDevicePrimaryCtxRetain": [c.POINTER(c.c_void_p), c.c_int],
            "cuDevicePrimaryCtxRelease_v2": [c.c_int],
            "cuCtxSetCurrent": [c.c_void_p],
            "cuModuleLoadDataEx": [c.POINTER(c.c_void_p), c.c_void_p, c.c_uint, c.c_void_p, c.c_void_p],
            "cuModuleGetFunction": [c.POINTER(c.c_void_p), c.c_void_p, c.c_char_p],
            "cuModuleUnload": [c.c_void_p],
            "cuFuncGetAttribute": [c.POINTER(c.c_int), c.c_int, c.c_void_p],
            "cuOccupancyMaxActiveBlocksPerMultiprocessor": [c.POINTER(c.c_int), c.c_void_p, c.c_int, c.c_size_t],
            "cuMemAlloc_v2": [c.POINTER(c.c_uint64), c.c_size_t],
            "cuMemFree_v2": [c.c_uint64],
            "cuLaunchKernel": [c.c_void_p] + [c.c_uint] * 7 + [c.c_void_p, c.c_void_p, c.c_void_p],
            "cuMemcpyDtoH_v2": [c.c_void_p, c.c_uint64, c.c_size_t],
            "cuEventCreate": [c.POINTER(c.c_void_p), c.c_uint],
            "cuEventRecord": [c.c_void_p, c.c_void_p],
            "cuEventSynchronize": [c.c_void_p],
            "cuEventElapsedTime": [c.POINTER(c.c_float), c.c_void_p, c.c_void_p],
            "cuEventDestroy_v2": [c.c_void_p],
        }
        for name, args in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, c.c_int

    def call(self, name, *args):
        code = getattr(self.lib, name)(*args)
        if code:
            raise RuntimeError(f"{name}: CUDA error {code}")

    def attribute(self, device, attribute):
        value = c.c_int()
        self.call("cuDeviceGetAttribute", c.byref(value), attribute, device)
        return value.value


def telemetry(index):
    fields = ["clocks.sm", "power.draw", "temperature.gpu", "utilization.gpu"]
    line = subprocess.check_output(
        ["nvidia-smi", "-i", str(index), "--query-gpu=" + ",".join(fields), "--format=csv,noheader,nounits"],
        text=True, timeout=10,
    ).strip()
    values = [float(v.strip()) for v in line.split(",")]
    result = dict(zip(fields, values))
    if result["temperature.gpu"] >= 78:
        raise RuntimeError("Temperature >= 78 C; probe stopped")
    return result


def measure(driver, sm_count, chains, repetitions, waves):
    module, function = c.c_void_p(), c.c_void_p()
    memory = c.c_uint64()
    events = []
    blocks, iterations, batch = sm_count * waves, 2048, 10
    count = blocks * 128 * chains * 4
    source = c.create_string_buffer(ptx(chains).encode("ascii"))
    try:
        log = c.create_string_buffer(8192)
        options = (c.c_int * 2)(5, 6)  # CU_JIT_ERROR_LOG_BUFFER / SIZE_BYTES
        values = (c.c_void_p * 2)(c.addressof(log), len(log))
        try:
            driver.call("cuModuleLoadDataEx", c.byref(module), source, 2, options, values)
        except RuntimeError as error:
            raise RuntimeError(f"{error}: {log.value.decode(errors='replace')}") from error
        driver.call("cuModuleGetFunction", c.byref(function), module, b"probe")
        registers, resident = c.c_int(), c.c_int()
        driver.call("cuFuncGetAttribute", c.byref(registers), 4, function)
        driver.call("cuOccupancyMaxActiveBlocksPerMultiprocessor", c.byref(resident), function, 128, 0)
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
        for thread in range(blocks * 128):
            warp = thread // 32
            product = (warp % 7 - 3) * (warp % 5 - 2)
            for j in range(chains * 4):
                expected = j + iterations * 32 * product
                actual = data[thread * chains * 4 + j]
                if actual != expected:
                    raise RuntimeError(f"Integer mismatch at {thread}/{j}: {actual} != {expected}")
        macs = blocks * 4 * chains * iterations * 16 * 8 * 32
        rates = [macs / (ms / 1000) / 1e12 for ms in milliseconds]
        return {"chains": chains, "waves": waves, "blocks": blocks, "warps_per_block": 4,
                "registers_per_thread": registers.value,
                "maximum_resident_blocks_per_sm": resident.value,
                "resource_occupancy_upper_bound": min(resident.value, waves) * 4 / 48,
                "occupancy_note": "resource/grid bound, NOT measured SM occupancy",
                "ptx_sha256": hashlib.sha256(source.value).hexdigest(),
                "iterations": iterations, "verified_outputs": count,
                "kernel_ms": milliseconds, "tmac_s": rates,
                "mean_tmac_s": statistics.mean(rates), "median_tmac_s": statistics.median(rates),
                "median_dense_tops": 2 * statistics.median(rates),
                "correctness": "all outputs exact; warp-uniform signed operands; not a layout or PoW proof test"}
    finally:
        for event in events:
            driver.call("cuEventDestroy_v2", event)
        if memory.value:
            driver.call("cuMemFree_v2", memory)
        if module.value:
            driver.call("cuModuleUnload", module)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--repetitions", type=int, default=9)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 50:
        parser.error("repetitions must be in 3..50")
    # Avoid running on top of an active miner; do not stop any processes.
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
    report = {"kind": "synthetic INT8 MMA; NOT Pearl hashrate", "sampled_at": time.time(),
              "cuda_driver_api_version": driver_version.value,
              "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "gpu": name.value.decode(), "sm_count": sms, "sm": "86", "results": [],
              "limitations": ["brief runs; no controlled thermal warm-up", "telemetry sampled outside timed kernels",
                              "no GEMM data movement, seed generation, BLAKE3 or proof verification",
                              "uniform operands validate instruction arithmetic, not arbitrary matrix layouts"]}
    driver.call("cuDevicePrimaryCtxRetain", c.byref(context), device)
    try:
        driver.call("cuCtxSetCurrent", context)
        for waves in (1, 4):
            for chains in (1, 2, 4, 8):
                before = telemetry(args.device)
                result = measure(driver, sms, chains, args.repetitions, waves)
                result.update(telemetry_before=before, telemetry_after=telemetry(args.device))
                report["results"].append(result)
                print(json.dumps({k: result[k] for k in ("chains", "waves", "median_tmac_s", "correctness")}))
    finally:
        driver.call("cuDevicePrimaryCtxRelease_v2", device)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
