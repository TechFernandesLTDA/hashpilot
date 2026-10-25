# HashPilot — Tech Fernandes

[Português](README.md) · [Documentation](docs/index.md) · [Windows installation](docs/installation.md) · [LLM entry point](llms.txt)

![Tech Fernandes](assets/brand/tech-fernandes_github_banner-readme_escuro_1280x400.png)

A local desktop monitor for Pearl (PRL) mining: NVIDIA telemetry, miner activity, persistent history, and earnings expressed in **PRL, BRL, USD and BTC**. The monitoring UI uses Python/Tkinter and the Python standard library.

## Windows installation

Run in PowerShell on **Windows 10/11 x64**, with `winget` available:

```powershell
irm https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/main/install.ps1 | iex
```

The installer prepares mise/Python, installs into `%LOCALAPPDATA%\Programs\HashPilot`, creates shortcuts, and opens setup. Existing local settings and history are preserved. See the [installation guide](docs/installation.md) for requirements and troubleshooting.

**The author's receiving address is the default.** Keeping it sends mining payouts to the author. Enter your own public receiving address in setup before mining; setup can be reopened. No seed phrase or private key is needed. Miner installation is a separate option; installing the dashboard alone does not start mining.

## Dashboard

| Tab | Contents |
|---|---|
| Overview | Hashrate, temperature, power, observed credits, receiving address and next payout |
| GPU and mining | NVIDIA sensors, clocks, fans, thermal control, charts and recent events |
| Earnings | Observed periods, pool balances, PRL/BRL/USD/BTC equivalents and projections |

SQLite history survives restarts and has no automatic expiry. Windows includes a tray icon, compact summary and text console. Local collection runs every 2 seconds; public pool/price requests run every 60 seconds with timestamps and cache status.

Currency values are current-price equivalents, not executed exchanges. **Pool payouts remain in PRL.** Projections are estimates, and incomplete observation periods are marked partial.

## Hardware support

GPU telemetry requires **NVIDIA `nvidia-smi`**. A **Windows RTX 3070** is the validated reference mining profile. Its measured results do not validate other cards or operating conditions.

The [NVIDIA catalog](docs/gpu-catalog.md) contains **158 models with primary sources**. This validates source provenance, not overclocks on every card. The [local agent](docs/gpu-agent.md) identifies devices by driver/UUID, measures a 300-second baseline, and supports manual benchmarking and explicit adaptive control. CPU-based Ollama is optional and requires no paid API.

**The dashboard and default starter currently use GPU0.** The engine separates profiles by UUID; the UI is not a multi-GPU dashboard. Never attribute aggregate hashrate to another device. The NVIDIA starter uses current driver settings without copying RTX 3070 clocks; the historical validated profile remains a separate reference.

macOS can display pool data, prices and saved history. Apple GPUs do not provide NVIDIA telemetry, and the included Windows mining/clock scripts do not configure Apple Silicon mining. See [portability](PORTABILIDADE.md).

## From source

```text
mise install
mise exec -- python monitor.py
mise exec -- python -m unittest discover -v
```

The public repository includes the author's public address and an explicitly authorized historical snapshot in `portable-data/`. That snapshot is dated reference data, not earnings produced by your machine. Initial restoration never replaces an existing local database.

Windows data: `%LOCALAPPDATA%\PearlMonitor`. macOS data: `~/Library/Application Support/HashPilot`.

[Architecture](docs/architecture.md) · [Technical reference](docs/reference.md) · [FAQ](docs/faq.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Code is licensed **GPL-3.0-only**: [LICENSE](LICENSE). Tech Fernandes names and logos are not licensed under GPL; see [NOTICE](NOTICE) and [brand provenance](assets/brand/ORIGEM.md).
