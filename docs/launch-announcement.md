# Texto preparado para divulgação na comunidade Pearl

Publicar em um canal que permita compartilhar ferramentas, identificando a autoria. Este texto foi preparado; não foi enviado a uma comunidade pelo aplicativo.

## English

**HashPilot: open-source Windows dashboard for Pearl miners using NVIDIA GPUs**

I built HashPilot at Tech Fernandes to monitor my Pearl mining setup: NVIDIA telemetry, hashrate charts, persistent history, pool payout estimates and PRL/BRL/USD/BTC equivalents. It also includes per-device measurements and optional local Ollama AI running on CPU.

On my RTX 3070, a 15-minute observation measured **78.40 TH/s mean at 159.06 W mean**, with fans at 100% and a configured 240 W power limit. This is one device under its existing thermal supervisor, not a universal preset, A/B improvement or profitability claim. The repository includes methodology and aggregated evidence.

The current interface monitors GPU 0. Generic tuning remains experimental and driver-dependent. The catalogue covers 158 NVIDIA models with source links; that does not mean 158 validated overclocks.

The author wallet is prefilled and can be replaced in setup. With another wallet, the setup suggests an optional **5% mining-time contribution** to help maintain the tool; it can be unchecked before saving or disabled later. It is separate from SRBMiner's own fee. No seed/private key is requested.

Looking for NVIDIA miners willing to test and report reproducible issues. Windows 10/11 x64, GPL-3.0-only.

- Repository and installation: https://github.com/TechFernandesLTDA/hashpilot
- First release: https://github.com/TechFernandesLTDA/hashpilot/releases/tag/v0.1.0
- Demo: https://github.com/TechFernandesLTDA/hashpilot/blob/main/media/hashpilot-demo.mp4
- Observation: https://github.com/TechFernandesLTDA/hashpilot/blob/main/docs/performance/rtx3070.md

## Português

Criei o HashPilot na Tech Fernandes para acompanhar mineração Pearl no Windows com GPUs NVIDIA: sensores, gráficos, histórico permanente, estimativa de pagamento e equivalentes em reais, dólares e BTC. O repositório contém uma demonstração de 44 segundos e uma observação documentada da minha RTX 3070. Busco mineradores para testar e relatar problemas reproduzíveis.

A carteira do autor vem preenchida e pode ser trocada. A ajuda de 5% do tempo, sugerida ao escolher outra carteira, é opcional e pode ser desativada. A interface atual acompanha GPU0; resultados desta unidade e dados do catálogo não validam ajustes universais.
