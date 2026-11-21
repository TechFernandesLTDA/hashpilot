# SRBMiner 3.8.9 na RTX 3070 — revisão de 18 de novembro de 2026

Fontes primárias: [release 3.8.9](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.9) (publicada em 17/11/2026), [release 3.8.8](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.8) (13/11/2026) e [parâmetros](https://github.com/doktor83/SRBMiner-Multi/blob/master/Parameters). Revisão anterior: [SRBMiner 3.8.7](srbminer-3.8.7.md).

**Estado: incluída no catálogo verificado, sem ganho medido.** Os hashes foram conferidos nos dois canais oficiais (ver [Inclusão no catálogo](#inclusão-no-catálogo)). A versão padrão do instalador continua a 3.8.0. Nenhum número desta página afirma ganho de eficiência sem A/B medido na placa.

## O que mudou (3.8.8 → 3.8.9)

| Item do changelog | Aplicação na RTX 3070 |
|---|---|
| "Efficiency improvements on algorithm 'pearlhash' for NVIDIA 3000, 4000 and 5000 GPUs" (3.8.8 e 3.8.9) | **É a família da placa.** Fala de eficiência (TH/s por watt); depende de A/B controlado na RTX 3070 para confirmar se reduz watts na mesma temperatura-alvo. |
| "NVIDIA 2000 series and CMP40/50HX can now use mem clocks 405/810MHz on algorithm 'pearlhash' with minimal hashrate loss" (3.8.9) | Não altera a RTX 3070 (Ampere já operava em 810 MHz), mas beneficia Turing se testada no futuro pelo `MemoryExplorer`. |
| Novo `--gpu-vddc-offset` (3.8.9) | Offset nativo de tensão no minerador. Não usado pelo HashPilot para evitar disputa de controle com o supervisor térmico NVML/`nvidia-smi`. |
| Remoção de `--gpu-extra-config` (3.8.8) | Sem impacto: o HashPilot nunca usou esse parâmetro. |
| Correções para AMD RDNA3/RDNA4 e Linux | Não se aplicam (produto exclusivo para NVIDIA no Windows 10/11 x64). |

## Parâmetros

Os 18 parâmetros que o HashPilot passa ao minerador continuam presentes (`--disable-cpu`, `--disable-msr-tweaks`, `--disable-worker-watchdog`, `--disable-gpu-amd`, `--disable-gpu-intel`, `--algorithm-gpu`, `--gpu-id`, `--pool`, `--wallet`, `--worker`, `--gpu-off-temperature`, `--log-file`, `--gpu-cclock0`, `--gpu-coffset0`, `--gpu-mclock0`, `--gpu-fan0`, `--gpu-plimit0` e `--gpu-reset-oc`). O afinador nativo e `--gpu-vddc-offset` permanecem desligados.

## Hashes publicados

| Pacote | SHA-256 exibido pelo GitHub | MD5 do texto da release | Conferência |
|---|---|---|---|
| `SRBMiner-Multi-3-8-9-win64.zip` | `4bee6f04077b59275ff227864d2ebb231543c758480baf928446c011fe87b5f9` | `1e7f95297859f11800dda1dfdbbc9708` | Os dois conferem com o pacote oficial inspecionado em 18/11/2026 |

## Inclusão no catálogo

O pacote oficial de 17/11/2026 (28.847.529 bytes) foi inspecionado pela CLI em Rust (`hashpilot release inspect 3.8.9 --archive-sha256 … --md5 …`), sem extrair nem executar nada:

| Campo | Valor |
|---|---|
| SHA-256 do ZIP | `4bee6f04077b59275ff227864d2ebb231543c758480baf928446c011fe87b5f9` |
| SHA-256 do `SRBMiner-MULTI.exe` (29.038.592 bytes) | `9d1da3dc8697480d468244bb8e46e2e2a9e432f551454384023c9fdb7267dbb3` |
| Caminho no pacote | Padrão (`SRBMiner-Multi-3-8-9/SRBMiner-MULTI.exe`); não exige `archive_member` |

Entradas sincronizadas em `miner_update.CATALOG`, `rust/hashpilot-rig/src/miner_install.rs` (golden `rig_support`), `Permissao-Defender.ps1` e `Referencia-Comum.ps1`. A ativação pelo serviço preserva a versão anterior e reverte automaticamente se não houver hashrate válido em 180 s.
