# HashPilot em Rust

Reescrita **em andamento** do HashPilot em Rust, convivendo com o aplicativo
Python. Nada aqui substitui o painel, o serviço do rig ou os scripts: o produto
em uso continua sendo o Python até a reescrita alcançar paridade comprovada e
validação física na RTX 3070.

**Política: toda melhoria nova é feita aqui, em Rust.** O Python fica em
manutenção até ser substituído: recebe só correção de bug e de segurança e dados
de que o produto atual precisa (como a entrada de uma release nova do SRBMiner no
catálogo verificado). Ferramenta nova vira subcomando da CLI `hashpilot`. Correção
num módulo já portado vale nos dois lados, com os goldens regerados.

## Estado: fases 1 e 2 de 6

| Fase | Conteúdo | Estado |
|---|---|---|
| 1 | **Núcleo de lógica pura** (`hashpilot-core`) e **CLI somente leitura** (`hashpilot`) | Feita, com paridade testada |
| 2 | **Histórico SQLite** (`hashpilot-history`): ler/gravar o `history.sqlite3` existente com a mesma chave SHA-256 e as mesmas migrações | Feita em 08/11/2026: paridade com o Python real, mutação e banco real de 2,8 GB |
| 3 | Finanças: Kryptex/CoinGecko com corpo limitado, cache e ciclo de 60 s | Planejada |
| 4 | Serviço do rig: processos Win32 por UUID, identidade de processo, supervisor e proxy de contribuição | Planejada — maior risco, exige Windows e GPU |
| 5 | Interface: três abas, bandeja, configurações (provável `egui`/`eframe`) | Planejada |
| 6 | Instalador, atualizador e troca do Python | Só depois das fases 1–5 validadas |

### Minerador próprio (projeto separado)

[`hashpilot-miner/`](hashpilot-miner/README.md) começa um minerador Pearl em Rust
sobre a referência oficial (`zk-pow`, licença ISC, fixada num commit), para o
aplicativo único. Fase M1 (motor de CPU com paridade contra o `try_mine_one` e o
`verify_plain_proof` oficiais) **compilada e validada na CPU em 08/11/2026**, no
Windows da RTX 3070: `cargo test --release` com 3 de 3 testes de paridade oficiais e
`selftest --attempts 20` com 20 acertos, 20 verificados e prova adulterada recusada.
Fica fora do workspace até se decidir incluí-la (ver o README do crate).

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

### O que a fase 2 porta (`hashpilot-history`)

Porta o `history.py` inteiro. Um mesmo `history.sqlite3` pode alternar entre o
Python e o Rust sem conversão, sem duplicar linhas e sem perder a posição dos logs.

| Parte | O que é igual ao Python |
|---|---|
| `schema` | Os comandos SQL são os mesmos, caractere a caractere (o SQLite guarda o texto em `sqlite_master`). Inclui `user_version` 3, recusa de versão futura, migração v2 → v3 da chave de `gpu_telemetry` com contagem de linhas, WAL e `synchronous=NORMAL` |
| `keys` | `log:` + SHA-256 de `caminho\nlinha` e `thermal:` + SHA-256 de `caminho\n` + linha bruta |
| `pyjson` | `json.dumps(ensure_ascii=False)` compacto, byte a byte (`1e+16`, `1e-07`, `100.0`), e o texto dos erros comuns do Python |
| `HistoryStore` | Fila e thread `history-writer`. O mesmo trabalho é repetido se o banco estiver ocupado. Amostra malformada vai para `history-rejected.jsonl` e o gravador segue. Esquema futuro fecha o histórico. O offset de cada log só é persistido depois do commit (`history-log-offsets.json`). Também iguais: o evento `status_changed`, a carteira de reserva, o limite de seis GPUs por UUID e os campos brutos do minerador arquivados uma vez só |
| `read` | `restore`, `restore_gpu` (hashrate medido até 180 s antes), `gpu_stats`, `finance_state` e `stats`, com o mesmo formato e os mesmos erros capturados ou propagados |
| `HistoryReader` | Leitura **somente leitura** para a CLI: nunca cria nem altera o banco |

Diferenças conscientes, só em entradas que o aplicativo não produz:

- **Mensagens de JSON e UTF-8 inválidos:** o texto difere (vem do `serde_json`). Isso vale numa linha do `thermal.jsonl` ou numa coluna lida; o tipo do evento e a chave são os mesmos.
- **Lista ou objeto num parâmetro SQL:** o Rust rejeita a amostra. O Python repetiria o trabalho para sempre (`ProgrammingError` cai no ramo transitório).
- **Texto do caminho:** é usado como recebido. Para deduplicar contra o arquivo do Python no Windows, passe o caminho com `\`, como o `str(Path)` do Python.

Comportamento do Python que o Rust reproduz e que vale corrigir nos dois lados:
`restore`/`restore_gpu` levantam `TypeError` (sem capturar) numa linha antiga com
`gpu_json` NULL. O gravador nunca grava NULL ali, então só um banco editado à mão
chega a esse caso; o golden `migracao_gpu_v2` o cobre.

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

**Golden do histórico** (`history.json`). O gerador roda o `HistoryStore` real em
seis cenários:

| Cenário | O que cobre |
|---|---|
| `basico` | Telemetria, status, rig, eventos, finanças, preferências e consultas |
| `arquivo_de_log` | BOM, `\r\n`, linha incompleta, UTF-8 inválido, rotação, reabertura e `thermal.jsonl` |
| `rejeitadas` | Amostras que o gravador recusa |
| `migracao_gpu_v2` | Migração da chave de `gpu_telemetry` |
| `versao_futura` | Banco de versão mais nova, recusado |
| `esquema_gpu_desconhecido` | Chave de `gpu_telemetry` que esta versão não conhece |

De cada cenário ele despeja o banco inteiro: SQL do esquema, linhas, offsets e
rejeitadas. O `uuid4`, o relógio e o fuso do carimbo são fixos. O
[`tests/golden.rs`](hashpilot-history/tests/golden.rs) repete as operações e exige o
mesmo banco, com o JSON comparado como texto. Doze mutações propositais foram
acusadas, entre elas:

- espaço no SQL;
- formato de float;
- BOM;
- `rstrip`;
- janela de 180 s;
- carteira vazia;
- offset confirmado;
- rotação do log;
- versão futura.

Duas mutações escaparam na primeira rodada e ganharam casos novos. Esse golden não
depende do fuso nem de caminhos, então também pode ser gerado no Windows:
`python tools/rust_parity/generate_goldens.py history`.

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
hashpilot release inspect X.Y.Z --archive-sha256 HEX [--md5 HEX] [--file ZIP]
hashpilot history stats|restore|gpu <UUID>|gpus [<UUID>]|finance [--db ARQ] [--wallet W] [--since EPOCH]
```

`history` abre o `history.sqlite3` **somente para leitura**. Ele não grava nem o
evento de sessão que o painel grava. Sem `--db`, usa o banco da pasta de estado;
sem `--wallet`, a carteira do `settings.json`, como o `config.WALLET` do Python.

`release inspect` levanta a entrada do catálogo de uma release nova do SRBMiner
(`miner_update.CATALOG`). Valida os argumentos antes de baixar e confere o SHA-256
do ZIP contra o digest do GitHub e, com `--md5`, contra o MD5 do texto da release.
Localiza o **único** `SRBMiner-MULTI.exe` do pacote e calcula o hash dele lendo o
ZIP em memória: nada é extraído nem executado. Sem `--file`, baixa com o `curl` do
sistema para a pasta temporária e apaga o arquivo no fim. Substituiu a antiga
`tools/inspect_miner_release.py`; a saída é idêntica à dela, nos pacotes de teste
e no pacote oficial 3.8.6.

Exemplo com os dados públicos (a partir da raiz do repositório):

```text
rust/target/debug/hashpilot payout --state portable-data/finance-cache.json --now 1792900000
```

## Validação da fase 2 (Windows 11, 08/11/2026)

- `cargo test` (paridade com o golden do histórico, unitários e todas as provas da
  fase 1), `cargo clippy --all-targets -- -D warnings` e `cargo fmt --check` passam.
  A fase 1 continua verde com os recursos `preserve_order` e `float_roundtrip` do
  `serde_json`, que o histórico ativa no workspace.
- **Banco real:** um retrato consistente do `history.sqlite3` em uso (API de backup
  do SQLite, sem escrita na origem) tinha 2,8 GB: 632 mil amostras de telemetria,
  625 mil de GPU, 94 mil eventos e 21 mil pontos de saldo. Nele, `hashpilot history`
  e o Python real deram respostas **idênticas** nas seis consultas: `stats`,
  `restore` (6 h e tudo, 54 MB de JSON), `gpus`, `gpu` e `finance`. O Rust foi igual
  ou mais rápido (no `restore` completo, 6,9 s contra 9,7 s).
- Esse banco expôs um defeito que os cenários pequenos não pegavam: a montagem das
  séries era quadrática (busca linear por chave). Foi trocada por índice por hash.
- Não testado: gravação concorrente com o painel Python aberto. Os dois usam WAL e
  `busy_timeout=200`, mas a troca de quem grava fica para a fase 4.

## Validação da fase 1

- Linux (container), sem GPU: `cargo test`, `clippy -D warnings` e `fmt` passam;
  `cargo check`/`clippy` também passam para `x86_64-pc-windows-msvc`.
- A CLI rodou sobre o log real 3.8.0 e o estado financeiro real e reproduziu os
  valores do Python (76,08 TH/s, 8 aceitas, médias 76,22/74,10).
- `release inspect` baixou e conferiu o pacote oficial do SRBMiner 3.8.6 (SHA-256 e
  MD5) e deu o mesmo hash de executável que a ferramenta Python anterior.
- **Não executado no Windows nem com GPU**: os trechos `cfg(windows)` (janela
  oculta do `nvidia-smi`, caminhos Windows, `%VAR%` com o ambiente real) só foram
  compilados. `hashpilot gpus`/`snapshot` precisam ser rodados na RTX 3070.

## Dependências

Diferente do Python (só biblioteca padrão), o núcleo usa quatro crates amplamente
usados: `regex`, `chrono`, `serde` e `serde_json`. A CLI acrescenta `zip` (com
deflate em Rust puro, sem C), `sha2` e `md-5` para o `release inspect`. O histórico
usa `rusqlite` com o recurso `bundled`, que compila o SQLite oficial (C) junto. Assim
não depende de uma DLL do sistema, mas exige um compilador C: no Windows, as Build
Tools do Visual Studio. O `Cargo.lock` é versionado para builds reprodutíveis. Código sob [GPL-3.0-only](../LICENSE).
