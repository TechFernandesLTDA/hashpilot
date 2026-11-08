# HashPilot em Rust

Reescrita **em andamento** do HashPilot em Rust, convivendo com o aplicativo
Python. Nada aqui substitui o painel, o serviço do rig ou os scripts: o produto
em uso continua sendo o Python até a reescrita alcançar paridade comprovada e
validação física na RTX 3070.

## Estado: fase 1 de 6

| Fase | Conteúdo | Estado |
|---|---|---|
| 1 | **Núcleo de lógica pura** (`hashpilot-core`) e **CLI somente leitura** (`hashpilot`) | Feita, com paridade testada |
| 2 | Histórico SQLite: ler/gravar o `history.sqlite3` existente com a mesma chave SHA-256 e as mesmas migrações | Planejada |
| 3 | Finanças: Kryptex/CoinGecko com corpo limitado, cache e ciclo de 60 s | Planejada |
| 4 | Serviço do rig: processos Win32 por UUID, identidade de processo, supervisor e proxy de contribuição | Planejada — maior risco, exige Windows e GPU |
| 5 | Interface: três abas, bandeja, configurações (provável `egui`/`eframe`) | Planejada |
| 6 | Instalador, atualizador e troca do Python | Só depois das fases 1–5 validadas |

### Minerador próprio (projeto separado)

[`hashpilot-miner/`](hashpilot-miner/README.md) começa um minerador Pearl em Rust
sobre a referência oficial (`zk-pow`, licença ISC, fixada num commit), para o
aplicativo único. Fase M1 (motor de CPU com paridade contra o `try_mine_one` e o
`verify_plain_proof` oficiais) **escrita, mas ainda não compilada**. Fica fora do
workspace para não quebrar o núcleo e a CLI, que estão verificados.

### O que a fase 1 porta

| Módulo Rust | Origem Python | O que faz |
|---|---|---|
| `miner_log` | `miner_log.py`, `gpu_telemetry._parse_gpu_log` | Parser do log do SRBMiner: hashrate, shares, médias, conexão, pausa, reinício, linhas futuras, mapa PCI |
| `thermal` | `thermal.py` | Política térmica pura; limites coerentes verificados **na compilação** |
| `payout` | `payout.py` | Progresso até o mínimo confirmado e janela estimada de envio |
| `valuation` | `valuation.py` | Equivalentes BRL/USD/BTC (o pagamento continua em PRL) |
| `wallet` | `contribution.py` (partes puras) | Validação de carteira e regras da contribuição opcional |
| `rig_config` | `rig_config.py` | Até seis GPUs por UUID, carteira efetiva, perfil, caminhos isolados |
| `config` | `config.py` | Pasta de estado, `settings.json`, `log_dir` com `%VAR%`/`$VAR` |
| `numbers`, `text` | semântica do Python | `float()`, `splitlines()`, `str()` e `repr(float)` |

As regras do projeto valem aqui: dado ausente ou inválido é `None`/erro
explícito, nunca zero; nada no núcleo acessa GPU, processos ou rede.

## Como a equivalência com o Python é provada

1. `tools/rust_parity/generate_goldens.py` roda os **módulos Python reais** sobre
   entradas fixas — inclusive os logs públicos de `portable-data/miner-logs/` e o
   estado financeiro de `portable-data/finance-cache.json` — e grava as respostas
   em [`hashpilot-core/tests/golden/`](hashpilot-core/tests/golden). Usa `TZ=UTC`,
   porque o carimbo do log do SRBMiner é hora local.
2. [`tests/parity.rs`](hashpilot-core/tests/parity.rs) exige que o Rust devolva o
   mesmo resultado: números, textos em português, ausências e mensagens de erro.
3. `test_rust_parity_goldens.py` (suíte Python) regera as respostas num diretório
   temporário e falha se as commitadas estiverem desatualizadas.
4. A suíte foi submetida a **testes de mutação** (erros propositais em bordas como
   +5 s, 75/83 °C, janela de 900 s, empate de pagamento, limite de seis GPUs): cada
   mutação precisa ser acusada. Duas escaparam na primeira rodada e ganharam casos
   de borda novos.

Se o Python mudar de propósito: regere (`python tools/rust_parity/generate_goldens.py`),
revise o diff dos JSON e ajuste o Rust até `cargo test` passar.

Diferenças conhecidas, só em entradas que o SRBMiner e o `settings.json` não
produzem: dígitos não ASCII (`float("١٢")` funciona no Python), `str()` de listas e
objetos (sai JSON) e hora local inexistente na mudança de horário de verão.

## Comandos

Requer Rust 1.85+ (`rustup`). Rode a partir de `rust/`:

```text
cargo test                                   # unitários + paridade + log real
cargo clippy --all-targets -- -D warnings
cargo fmt --check
cargo check --target x86_64-pc-windows-msvc  # compila para Windows sem linker (rustup target add …)
cargo run -p hashpilot-cli -- --help
```

A CLI `hashpilot` é **somente leitura**: não inicia nem para mineração, não altera
clocks, potência ou ventoinhas e não grava nos dados do aplicativo.

```text
hashpilot gpus                                     # sensores NVIDIA (nvidia-smi), JSON
hashpilot parse-log <log> [--index N] [--shared] [--now EPOCH] [--utc]
hashpilot payout [--state finance-cache.json] [--now EPOCH] [--price-brl X]
hashpilot snapshot [--log-dir PASTA]               # sensores + log mais recente de cada GPU
```

Exemplo com os dados públicos (a partir da raiz do repositório):

```text
rust/target/debug/hashpilot payout --state portable-data/finance-cache.json --now 1792900000
```

## Validação desta fase

- Linux (container), sem GPU: `cargo test`, `clippy -D warnings` e `fmt` passam;
  `cargo check`/`clippy` também passam para `x86_64-pc-windows-msvc`.
- A CLI rodou sobre o log real 3.8.0 e o estado financeiro real e reproduziu os
  valores do Python (76,08 TH/s, 8 aceitas, médias 76,22/74,10).
- **Não executado no Windows nem com GPU**: os trechos `cfg(windows)` (janela
  oculta do `nvidia-smi`, caminhos Windows, `%VAR%` com o ambiente real) só foram
  compilados. `hashpilot gpus`/`snapshot` precisam ser rodados na RTX 3070.

## Dependências

Diferente do Python (só biblioteca padrão), o port usa quatro crates amplamente
usados: `regex`, `chrono`, `serde` e `serde_json`. O `Cargo.lock` é versionado para
builds reprodutíveis. Código sob [GPL-3.0-only](../LICENSE).
