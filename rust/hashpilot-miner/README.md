# Minerador Pearl do HashPilot em Rust

> **Estado: fase M1 compilada e validada em 08/11/2026** no PC Windows 11 da RTX 3070
> (Rust 1.99.1 `stable-x86_64-pc-windows-msvc`, Build Tools 2022), **só na CPU**: o
> motor de GPU ainda não existe. Compilou sem erro e sem aviso, sem mudança no código,
> contra o `zk-pow`/`pearl-blake3` oficiais no commit `d7c517d`.
>
> | Comando | Resultado |
> |---|---|
> | `cargo test --release` | `tests/official_parity.rs`: 3 de 3 passaram (bytes idênticos ao `try_mine_one`, jackpot de todos os tiles igual ao laço oficial, provas aceitas pelo `verify_plain_proof` e prova adulterada recusada) |
> | `cargo run --release -- selftest --attempts 20` | `hits: 20`, `verified: 20`, `rejected: []`, `tampered_rejected: true` (parâmetros do teste oficial, dificuldade permissiva, offline) |
> | `cargo run --release -- bench-cpu` | 0,045 TMAC/s em 16 threads (referência de CPU; a RTX 3070 faz ~79 TH/s com o SRBMiner) |
>
> Nas dependências git não há `build.rs`. No alvo Windows MSVC, o único script de
> build que compila código é o do `blake3` (rotinas SIMD em C/assembly); os demais
> só detectam o compilador ou a plataforma.

## Por que um minerador próprio, e por que não "converter o SRBMiner"

O SRBMiner é fechado: não há código para converter, e o HashPilot não desmonta o
binário. Um minerador próprio é viável porque a **referência oficial da Pearl é
aberta** (`pearl-research-labs/pearl`, licença ISC): a mineração de referência, o
ruído, o Merkle e o verificador já existem em Rust. Este crate usa esse código
**fixado no commit `d7c517d`** — o mesmo inspecionado em
[`docs/pearl-rtx3070-investigacao-2026-11-02.txt`](../../docs/pearl-rtx3070-investigacao-2026-11-02.txt) —
e reimplementa só a parte que um kernel de GPU vai substituir.

O FreeForGemPear **não** é usado: a licença dele proíbe modificação e exige manter
a taxa do desenvolvedor.

## O que a fase M1 contém

| Arquivo | Conteúdo |
|---|---|
| `src/job.rs` | Chave do job, seeds de ruído, partição em tiles e prova Merkle — porte das funções privadas de `zk-pow/src/ffi/mine.rs` (ISC; ver `NOTICE-pearl.md`) |
| `src/engine.rs` | Motor de tiles: NoisyGEMM + transcript "jackpot" (XOR do tile a cada fatia de `rank`, rotação de 13 bits). Trait `TileEngine`, que o motor de GPU vai implementar |
| `src/lib.rs` | Uma tentativa completa: compromisso, ruído oficial, jackpots, hash, dificuldade, `PlainProof` |
| `src/main.rs` | `selftest` e `bench-cpu`, ambos offline |
| `tests/official_parity.rs` | Paridade com o oficial (ver abaixo) |

Como a pool recebe a **prova simples** (`plain_proof`, ~1,5 MB) e não a prova zk, o
minerador não precisa gerar a prova pesada do `plonky2`.

## Como compilar e testar (na sua máquina)

Projeto separado do workspace de `rust/`; baixa o código oficial pelo git.

```text
cd rust/hashpilot-miner
cargo test --release
cargo run --release -- selftest --attempts 20
cargo run --release -- bench-cpu
```

O que esperar, se a fase estiver correta:

1. `same_rng_gives_the_same_proof_as_official_try_mine_one`: mesmo RNG → **bytes
   idênticos** à prova do `try_mine_one` oficial.
2. `every_tile_jackpot_matches_the_literal_official_loop`: transcript de todos os
   tiles igual ao laço oficial, inclusive com K que não é múltiplo do rank.
3. `official_verifier_accepts_our_proofs_and_rejects_tampering`: o
   `verify_plain_proof` oficial aceita cada acerto e recusa a prova com o cabeçalho
   adulterado.
4. `selftest`: `hits > 0`, `verified == hits`, `tampered_rejected: true`.
5. `bench-cpu`: alguns centésimos ou décimos de TMAC/s — a CPU serve para referência
   e verificação; a RTX 3070 faz ~79 TH/s.

Se algo falhar, mande a saída: a prioridade é a paridade, não a velocidade.

## Fases seguintes

| Fase | Conteúdo | Validação exigida |
|---|---|---|
| M2 | Motor de GPU SM86: `mma.m16n8k32` INT8 por PTX carregado pelo driver (mesmo padrão de `tools/experiments/ampere_int8_probe.py`) | Jackpot de cada tile **idêntico** ao `CpuEngine` em jobs aleatórios, na RTX 3070; depois TMAC/s contra o SRBMiner |
| M3 | Cliente Stratum da Kryptex: subscribe, authorize, trabalho, `mining.set_difficulty`, envio do `plain_proof` | O dialeto da Kryptex para Pearl **não é público**; precisa ser observado no tráfego do próprio usuário pelo proxy local do HashPilot. Toda share é conferida com `verify_plain_proof` (com a dificuldade da pool) **antes** de ser enviada |
| M4 | Integração no serviço do rig: escolher motor (SRBMiner ou próprio) por GPU; a contribuição 57/3 continua pelo mesmo proxy | A/B pelo agente do painel: TH/s líquido, watts, shares aceitas |
| M5 | Aplicativo único: interface Rust + serviço + minerador | Só substitui o SRBMiner como padrão depois de vencer o A/B na RTX 3070 |

Perguntas em aberto para M2: o valor com ruído cabe em INT8 ([-128, 127])? O sinal
vai de -64 a 64 e o ruído é `i8`; se +128 for possível, o kernel precisa da mesma
correção estudada em `tools/experiments/strassen_int8_feasibility.py`. A resposta
está no kernel oficial SM90 (`miner/pearl-gemm/csrc/gemm/pow_utils.hpp`).

## Regras de segurança

- O minerador nunca altera clocks: quem cuida da temperatura continua sendo o
  supervisor do HashPilot.
- Nenhuma share sai sem passar antes pelo verificador oficial.
- O motor próprio fica desligado por padrão até vencer o A/B.
