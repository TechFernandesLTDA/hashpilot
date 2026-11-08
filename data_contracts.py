"""Contratos de dados compartilhados (TypedDict), conferidos por mypy nos módulos tipados.

Só descrevem formas já existentes; não mudam serialização nem validação.
Campos ausentes/None continuam significando "não disponível", nunca zero.
"""

from __future__ import annotations

from typing import Any, TypedDict


class GpuPaths(TypedDict):
    log_dir: str
    state_dir: str
    latest_path: str
    controller_status_path: str
    proxy_settings_path: str
    benchmark_request_path: str


class DetectedGpu(TypedDict, total=False):
    """Saída de `gpu_agent.detect_nvidia_gpus` usada pelo rig (campos extras são preservados)."""

    uuid: str
    index: int
    name: str
    vendor: str
    pci_bus_id: str
    power_default_w: float
    supported_graphics_clocks_mhz: list[int]


class RigDevice(GpuPaths, total=False):
    """GPU resolvida por `rig_config.resolve_devices`: detecção + caminhos + configuração."""

    uuid: str
    index: int
    name: str
    vendor: str
    enabled: bool
    profile: str
    wallet: str
    effective_wallet: str
    wallet_override: str | None
    uses_default_wallet: bool
    uses_author_wallet: bool
    device_key: str
    worker_name: str
    configuration_error: str | None


class CurrencyValues(TypedDict):
    prl: float | None
    brl: float | None
    usd: float | None
    btc: float | None


JsonObject = dict[str, Any]
