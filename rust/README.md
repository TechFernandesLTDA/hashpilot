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

## Estado: fases 1 a 3 de 6

| Fase | Conteúdo | Estado |
|---|---|---|
| 1 | **Núcleo de lógica pura** (`hashpilot-core`) e **CLI somente leitura** (`hashpilot`) | Feita, com paridade testada |
| 2 | **Histórico SQLite** (`hashpilot-history`): ler/gravar o `history.sqlite3` existente com a mesma chave SHA-256 e as mesmas migrações | Feita em 08/11/2026: paridade com o Python real, mutação e banco real de 2,8 GB |
| 3 | **Finanças** (`hashpilot-finance`): Kryptex/CoinGecko com corpo limitado, cache e ciclo de 60 s | Feita em 08/11/2026: paridade com o Python real, mutação, respostas reais da API e cache real |
| 4 | Serviço do rig, em quatro etapas: **4a** processos (`hashpilot-rig`), 4b `RigManager` por UUID, 4c supervisor térmico, 4d proxy de contribuição | **4a feita em 08/11/2026** (identidade e encerramento seguro, provados contra a API do Windows e o rig real). **4b-1 e 4b-2 feitas em 08/11/2026** (dependências e lógica do `RigManager`, provadas com backend falso). **4b-3a feita em 09/11/2026** (laço `serve`, atualização do minerador com reversão, snapshots e trava do serviço). **4b-3b-1 feita em 09/11/2026** (restauração da GPU pelo NVML, provada com driver falso e lida no driver real sem escrita). **4b-3b-2 feita em 09/11/2026** (`hashpilot rig --serve`, com detector e sensores reais; o primeiro uso real fica para o Renan presente). **4c-1 feita em 09/11/2026** (`thermal_target`: controle por temperatura-alvo e explorador de memória). **4d feita em 09/11/2026** (proxy de contribuição `hashpilot contribution`, que o serviço em Rust já inicia). **Motor Rust em uso no PC desde 09/11/2026** (`Iniciar-Rig.ps1 -Motor Rust`). **4c-2a feita em 09/11/2026** (`gpu_agent`: leituras, planos e o `GPUAgent` sobre o mesmo SQLite, sem hardware nem IA). **4c-2b feita em 09/11/2026** (consultor de IA, atuador e laço `hashpilot gpu-agent --control`, que o serviço em Rust já inicia no lugar do `gpu_agent.py`). Com isso a fase 4 está completa |
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

### O que a fase 3 porta (`hashpilot-finance`)

Porta o `finance.py` inteiro. Só endereços públicos de carteira; nenhuma conta,
chave ou credencial.

| Parte | O que é igual ao Python |
|---|---|
| `model` | `normalize_finance_state`, `financial_model`, `portfolio_model` e `portfolio_snapshots`. Iguais: créditos por dia, semana e 30 dias, com a fronteira local em UTC−3 e a tolerância de 120 s; projeção por rede, com taxa da pool, devfee de 2% e contribuição; equivalentes em BRL, USD e BTC; tipos inteiro e float (`fetched_at` ausente vira `0`); e o `sum()` compensado do Python 3.12+, bit a bit |
| `fetch` | GET com `User-Agent` do HashPilot (versão lida do `app_version.py`), `Accept: application/json`, 9 s, corpo de no máximo 2 MB (o excesso é erro, nunca truncado), BOM tolerado, `HTTP Error 503: Service Unavailable` como o `urllib`, proxy do Windows e nenhuma compressão. Também igual: cotação e rede compartilhadas por 45 s entre carteiras |
| `collector` | As seis consultas em paralelo, na ordem do Python. A validação usa as mesmas mensagens, inclusive o `TypeError` da cotação nula. O snapshot sai no máximo a cada 45 s. O cache `finance-cache.json` guarda 35 dias, com escrita atômica e o texto do `json.dumps` padrão. Também iguais: a fusão com o arquivo SQLite (o cache vence no mesmo instante), a fila de duas amostras e o portfólio, com uma pasta por carteira, três consultas por vez e carteiras inválidas descartadas |

Diferenças conscientes:

- **Erros de rede:** só o texto de DNS, TLS e tempo-limite difere (vem do `ureq`). HTTP com código de erro tem o mesmo texto.
- **Erro dentro de uma rodada:** no Python, encerra a thread do coletor em silêncio. No Rust, a rodada é descartada e o ciclo continua.
- **Ordem de `forecast_missing_gpu_uuids` no portfólio consolidado:** o Python percorre as carteiras na ordem de um `set`, aleatória por processo; o Rust usa a ordem alfabética.
- **Carteira que não é texto:** num dispositivo, cai na carteira principal.

### O que a etapa 4b-2 porta (`hashpilot-rig::manager`)

Porta o `RigManager` do `rig.py`, método a método:
- `start_gpu`, `start_controller`, `recover_controller`, `stop_gpu` e `reconcile`;
- `reconcile_successor`, `manual_start`, `adopt_legacy` e `migrate_legacy`;
- `process_requests`, `observe_health` e `publish`;
- as funções `write_request`, `support_configuration` e `software_identity`.

O registro, os pedidos, o `settings.json` e o `operations.sqlite3` são os mesmos do
Python, então os dois serviços leem o estado um do outro.

Os processos passam pela interface `Backend`. O controlador (`gpu_agent.py`) e o
proxy (`contribution.py`) continuam em Python, iniciados pelo Rust, até as
etapas 4c e 4d.

### O que a etapa 4b-3a porta

- **Laço `serve`** (`RigManager::serve`): um ciclo a cada 2 s, com pedidos,
  inventário, `reconcile`, snapshots, validação da atualização, saúde e o sinal
  contra a suspensão. Ao sair, por pedido ou por erro, para todas as GPUs. O erro
  de um ciclo fica registrado e o laço continua.
- **Atualização do minerador:** `activate_miner`, `validate_miner_update`, com
  reversão automática depois de 180 s sem hashrate válido, e a ação `miner_update`.
- **Catálogo** (`miner_install`): o mesmo `CATALOG` do `miner_update.py`, a versão
  selecionada e o executável conferido pelo SHA-256, com junções recusadas. O
  backend real passa a conferir o executável antes de iniciar.
- **Snapshots** (`snapshots`): `collect_snapshots` e `read_device_payload`, com o
  `latest.json` de cada GPU.
- **Trava do serviço** (`lease`): a mesma trava do `ProxyLease`, no primeiro byte
  de `rig-manager-owner.json`. O serviço em Rust e o em Python se excluem.
- **CLI:** `hashpilot miner` mostra a versão selecionada e se o SHA-256 do
  executável confere. Só lê o arquivo; nunca o executa.

### O que a etapa 4b-3b-1 porta

- **NVML** (`nvml`): só as funções do `gpu_defaults.py`, carregadas do
  `System32\nvml.dll`, atrás da interface `Nvml`, com `nvmlClockOffset_v1` de 24
  bytes. Todo o `unsafe` novo fica nesse arquivo.
- **Restauração** (`gpu_defaults`): ventoinhas em política automática, offsets zero
  em todos os P-states, clocks destravados e potência de fábrica, cada etapa
  conferida por leitura e sem parar as outras. O backend real passa a usá-la.
- **Trava de controle por GPU** (`ControlLease`): a mesma do `NvidiaActuator`
  (`gpu-control-owner.lock` e `.json`). Se outra sessão controla a GPU, a
  restauração falha sem abrir o driver.
- **CLI:** `hashpilot nvml <uuid>` lê ventoinhas, P-states, offsets e potência.
  Nunca chama uma função de escrita.

### O que a etapa 4b-3b-2 porta

- **Detector e sensores** (`nvidia`): `detect_nvidia_gpus` (capacidades com cache de
  600 s e identidade do `data/nvidia-gpus.json`) e `read_gpu_samples`, iguais ao
  `gpu_agent.py` e ao `gpu_telemetry.py`. O `nvidia-smi` real (`run_smi`) passou da CLI
  para cá.
- **`hashpilot rig`**, com os mesmos argumentos do `rig.py`:
  - `--request` grava um pedido em `rig-requests/`, como `rig.write_request`;
  - `--serve` liga o `RigManager` ao `nvidia-smi`, aos logs (hora local) e ao
    minerador reais. Exige administrador e o `--python` que roda `gpu_agent.py` e
    `contribution.py`. Ctrl+C ou fechar o console grava um pedido `shutdown`: o ciclo
    seguinte para e restaura todas as GPUs;
  - `--serve --check` confere administrador, scripts, executável do minerador (pelo
    SHA-256), GPUs e sensores **sem** criar o gerenciador, gravar nada ou iniciar
    processo.

### Escolher o motor do serviço

O `Iniciar-Rig.ps1` aceita `-Motor Python|Rust`. Sem o parâmetro, ele usa a última
escolha, gravada em `%LOCALAPPDATA%\PearlMonitor\rig-motor.txt`, e na primeira vez usa o
Python. O motor Rust usa `rust\target\release\hashpilot.exe` (`cargo build --release -p
hashpilot-cli`).

Se o outro motor estiver ativo, o script grava um pedido `shutdown`, espera até 60 s
pelo desligamento limpo (que para e restaura as GPUs) e só então inicia o escolhido.
Ele nunca encerra processos à força. O `Atualizar-Rig.ps1` reconhece os dois motores,
e no Rust recompila antes de parar o serviço atual. Os dois nunca rodam juntos: a
trava do serviço recusa o segundo.

```text
powershell -File mining\windows\Iniciar-Rig.ps1 -Motor Rust     # troca para o Rust
powershell -File mining\windows\Iniciar-Rig.ps1 -Motor Python   # volta ao Python
```

No motor Rust, o proxy de contribuição é o `hashpilot contribution` (etapa 4d) e o supervisor
térmico é o `hashpilot gpu-agent --control` (etapa 4c-2b). O motor Python continua com o
`contribution.py` e o `gpu_agent.py`.

### O que a etapa 4b-1 porta (`hashpilot-rig`)

São as dependências do `RigManager`, sem iniciar nem parar nada:

| Módulo | Python | O que faz |
|---|---|---|
| `health` | `mining_health.py` | Saúde pelo ACK da pool e `OperationalStore`: orçamento de recuperação (3 por hora, espera de 60 s a 900 s), reserva de componentes e incidentes. Usa o mesmo `operations.sqlite3` e o mesmo esquema |
| `continuity` | `mining_continuity.py` | Sensor seguro (abaixo de 70 °C, até 15 s), janela de inventário (uma GPU sumida fica 90 s) e bloqueio só da suspensão automática (`SetThreadExecutionState`) |
| `successor` | `miner_identity.py` | Reconhece um SRBMiner reiniciado pela sessão gravada (log, carteira, pool, worker e GPU), sem confiar no PID |
| `reference` | `reference_profile.py`, `gpu_profiles.py` | Perfil opt-in da RTX 3070 de referência e perfis de objetivo |
| `fsutil` | `fsutil.py` | `atomic_json`: troca atômica, com novas tentativas só na troca. O `json.dumps(indent=2)` byte a byte ficou em `pyjson::encode_indented` |

Os números seguem os tipos do Python, porque o painel lê esse JSON. Por exemplo,
`max(0, x)` devolve o inteiro `0`, e os limites 900 e 7200 saem inteiros quando
prevalecem sobre a mediana. O teste compara inteiro e `float` como coisas diferentes.

### O que a etapa 4a porta (`hashpilot-rig`)

Porta o `process_backend.py` inteiro. Nada nesta etapa inicia mineração sozinho:
`start` só cria o processo que o chamador pedir.

| Parte | O que é igual ao Python |
|---|---|
| `argv` | `CommandLineToArgvW` em Rust puro, sem FFI: o executável tem regra própria, `2n`/`2n+1` barras antes de aspas e três aspas seguidas viram uma aspa literal. Provado em 31 linhas contra a API real (golden `process`) |
| `win32` | Única parte com `unsafe` do port (o crate usa `deny`, e o resto do workspace `forbid`). São quatro chamadas Win32 com handle fechado no `Drop`. A identidade é PID, horário de criação em FILETIME (texto) e executável, o mesmo formato do `rig-manager.json` |
| `process` | `ProcessBackend`: `inspect` (com a linha de comando lida pelo CIM e cache de 64), `start` (sem janela, saída anexada ao log, `launch_id`), `is_owned`, `stop`, `wait`, `assert_gpu_available` (falha fechada: minerador ilegível ou sem `--gpu-id` bloqueia), `legacy_candidates` e `miner_device_map`. `stop` confere a identidade de novo no mesmo handle que encerra, então um PID reutilizado nunca é atingido |

O `normalized_path` usa o `GetFullPathNameW` (via `std::path::absolute`), como o
`ntpath.abspath`, e tira a barra final, menos na raiz da unidade (`c:\`).

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

**Golden das finanças** (`finance.json`, 76 casos). Roda o `finance.py` real com
relógio fixo por rodada, consultor falso e um histórico falso que registra cada
chamada. Tem quatro grupos de casos:

- **Normalização e `financial_model`:** estado vazio e malformado, com textos
  numéricos, negativos, snapshots duplicados ou futuros e `-0.0`. Também cobre rede
  antiga ou no futuro, erros, taxa nula, alta ou ausente, estouro para infinito,
  cobertura de 120 s contra 150 s, segunda-feira, domingo e o arredondamento ao
  microssegundo do `fromtimestamp`.
- **`portfolio_model` e `portfolio_snapshots`:** legado, uma, duas e três
  carteiras, seleção, GPUs paradas, pausadas, sem leitura ou com hashrate
  negativo, e somas que dependem da compensação.
- **Coletor:** três rodadas completas, todas as respostas inválidas, cache com BOM,
  inválido ou de outra carteira, poda de 35 dias, carteira vazia e fusão com o
  arquivo SQLite.
- **Portfólio de coletores:** duas carteiras com cache compartilhado e erro HTTP.

Estado, amostras, arquivos gravados e chamadas ao histórico são comparados como
texto, byte a byte. Os modelos para a interface são comparados por valor, com o
tipo inteiro/float exigido fora do bloco `payout`, que vem da struct da fase 1.

**Mutações:** de 16 propositais, 15 foram acusadas; a que sobrou é equivalente
(hashrate zero já é descartado dentro do `financial_model`). Três escaparam na
primeira rodada e ganharam casos novos: cobertura de 120 s, folga de 5 s e fração
de contribuição inteira. Gerável no Windows:
`python tools/rust_parity/generate_goldens.py finance`.

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
hashpilot finance model [--state finance-cache.json] [--hashrate TH/s] [--now EPOCH] [--wallet W]
hashpilot finance fetch [--wallet W] [--replay respostas.json] [--now EPOCH]
hashpilot process inspect <PID> [--command-line] | miners | owned [--manager rig-manager.json]
hashpilot recovery --gpu <UUID> [--operations operations.sqlite3] [--now EPOCH]
hashpilot miner [--base <pasta>]
hashpilot nvml <UUID>                              # só leitura pelo NVML
hashpilot rig --request AÇÃO --state-dir D [--gpu-uuid U] [--profile P] [--objective O]
hashpilot rig --serve [--check] --state-dir D --log-dir L --settings S --miner-path M --python PY [--project-dir P] [--adopt-legacy] [--migrate-legacy] [--legacy-uuid U] [--reference-rtx3070]
```

`process` só lê: identidade de um processo, mineradores em execução com as GPUs do
`--gpu-id` e se os processos do `rig-manager.json` ainda são os mesmos. Nunca
encerra nada.

`finance model` calcula o modelo financeiro do painel a partir do cache:

- créditos por período;
- projeção;
- pagamento;
- equivalentes.

`finance fetch` faz uma rodada real de consultas públicas e mostra o estado. Não
grava cache nem histórico. Com `--replay`, usa respostas gravadas
(`{url: json}`), sem rede.

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

## Validação da etapa 4d (Windows 11, 09/11/2026)

O `contribution.py` foi portado para `hashpilot-rig::contribution`, com o comando `hashpilot
contribution` (os mesmos argumentos do Python). O serviço em Rust inicia esse proxy no lugar do
`contribution.py`; o `rig.py` continua iniciando o Python. Partes portadas:
- o ledger da cota;
- a reescrita Stratum, que troca a carteira só nos campos de autorização e de share;
- o laço por conexão;
- o servidor em loopback;
- a trava do diretório, compatível com a do Python.

- **Golden `contribution`:** 95 casos executados pelo Python real.
  - **Ledger:** 20 arquivos (válidos, migração da versão 1, `NaN`, JSON quebrado e cada regra de recusa), cada um com 31 operações. O teste compara o resultado, o tipo numérico (`int` ou `float`, como no Python) e o arquivo gravado byte a byte.
  - **Reescrita:** 60 combinações de mensagem, carteira ativa e autenticação, com saída `ensure_ascii` igual (inclusive `\x7f` e emoji).
  - **Conexões:** 15 roteiros com sockets reais e uma pool simulada, os mesmos executados no Python. Eles cobrem:
    - autorização principal e do autor;
    - recusa da pool (que desliga a contribuição);
    - share antes da autorização;
    - outra carteira;
    - método desconhecido;
    - id duplicado;
    - JSON inválido;
    - `error: 0`, que vale como sucesso porque `0 == False` no Python.
- **Mutações:** 14 mutações, e 12 são detectadas. As 2 que restam são equivalentes:
  - o resto de divisão com número negativo, porque os contadores nunca são negativos;
  - o tipo do erro do `record_route` dentro do `account`, porque o intervalo ali está sempre entre 0 e 5 s.
- **Uso real do binário:** com a contribuição desligada, numa pasta temporária, `hashpilot contribution`:
  - anunciou a porta com o PID certo;
  - recusou a segunda instância pela trava;
  - aceitou uma conexão local e conectou à pool real;
  - encerrou como o rig encerra os filhos.
- **Diferenças conhecidas, só no texto do erro:**
  - JSON malformado e erros de socket têm a mensagem do decodificador de cada linguagem;
  - o Rust recusa inteiros maiores que 64 bits e surrogates soltos, que o Python aceitaria.

**Não testado:** a contribuição ligada com mineração real. Ela está desligada neste rig, e ligar a contribuição é decisão do Renan.

## Validação da etapa 4c-2b (Windows 11, 09/11/2026)

Duas partes, as duas portadas para `hashpilot-rig`:
- `ai_advisor`: o `ai_advisor.py` e os consultores `OllamaAdvisor` e `BackgroundAdvisor` do
  `gpu_agent.py`. Só medições numéricas saem do computador, só modelos gratuitos, cota local de
  40 consultas por dia na mesma `ai-requests.sqlite3`, e a chave lida do Cofre do Windows nunca
  aparece em erro nem log.
- `controller`: o `NvidiaActuator` (trava compatível com a do Python, plano revalidado antes de
  cada escrita), a leitura do minerador e o laço `control()`, com o comando
  `hashpilot gpu-agent --control` e os mesmos argumentos do `gpu_agent.py`. O serviço em Rust
  (`manager.controller_command`) inicia esse comando; o `rig.py` continua iniciando o Python.

- **Golden `ai_advisor`:** 117 casos do Python real. A nuvem e o OpenCode são simulados; o Ollama
  fala HTTP de verdade com um servidor local. Mutações: 23 de 24 detectadas; a restante
  (`ensure_ascii`) é equivalente, porque só texto ASCII passa por ali.
- **Golden `control_loop`:** 24 roteiros do `control()` do Python real. Os dois lados usam a
  mesma simulação:
  - nvidia-smi falso, em que a temperatura acompanha o clock travado;
  - relógio virtual;
  - processo do minerador e leitura do minerador.

  O teste compara cada comando nvidia-smi (leituras e escritas, na ordem), um resumo do status a
  cada ciclo, o status final, a memória térmica, a posse da GPU e a contagem das cinco tabelas.
  Cobrem:
  - baseline com e sem alvo, alívio térmico, perfil `cool`, emergência com alvo (desiste depois de
    10 s) e sem alvo;
  - partida segura com a GPU ociosa, clock lembrado, exploração da memória, troca de alvo durante
    o laço e subida bloqueada pelo limite suave;
  - pedidos de parada e de benchmark (válidos, inválidos e nas fronteiras de 3600 s e 60 s),
    telemetria velha, minerador desconhecido ou morto, outro supervisor, trava legada viva e
    morta, falta de administrador e duração inválida.
- **Mutações do laço:** 27, e 26 são detectadas. A restante zera o teto aprendido quando a memória
  muda; nenhum roteiro chega a definir esse teto. Ela fica sem cobertura.
- **Uso real:**
  - o Ollama deste PC respondeu pelo Rust com uma ação válida;
  - o `hashpilot gpu-agent` recusou rodar sem administrador e sem `--miner-pid`.
- **Diferenças conhecidas:**
  - mensagens do decodificador JSON e de SQLite têm o texto de cada linguagem;
  - Ctrl+C sai pelo mesmo caminho do `KeyboardInterrupt` (restaura e solta a posse, sem marcar
    falha), com código 1.

**Não testado:** OpenRouter e OpenCode de verdade, porque não há chave nem cliente instalados.

## Validação da etapa 4c-2a (Windows 11, 09/11/2026)

A parte do `gpu_agent.py` que não escreve no hardware nem fala com a IA foi portada para
`hashpilot-rig::gpu_agent`:
- leituras do `nvidia-smi` (`read_sample`, `read_fan`, clocks de memória) e das configurações;
- planos limitados (`validated_plan`, `control_plan`), limites e janela térmica;
- o `GpuAgent`, que grava benchmarks, observações, decisões e testes de ajuste no mesmo
  `gpu-agent.sqlite3`, com o mesmo esquema e o mesmo texto JSON do Python (`ensure_ascii`
  onde o Python usa, inclusive surrogates); os dois motores leem o banco um do outro;
- seleção da GPU, pedido de parada e detecção de outro supervisor.

A IA entra por um trait `Advisor`; ainda não há implementação em Rust. O laço do supervisor e o
atuador ficam para a etapa 4c-2b, e o serviço continua usando o `gpu_agent.py`.

- **Golden `gpu_agent`:** 435 casos executados pelo Python real, com `time.time` fixo e `uuid4`
  em sequência.
  - **Funções puras:** 222 planos (6 GPUs NVIDIA, inclusive clocks densos, esparsos e potência
    incoerente, × 6 amostras × 6 ações, mais restauração e uma GPU recusada), janela térmica em cada fronteira de estabilidade, e
    `statistics.mean` exata.
  - **Leituras:** `nvidia-smi` falso (respostas incompletas, `[N/A]`, fronteiras de 0, 100 e
    120, falha do comando) e arquivos de configuração quebrados, com BOM ou sem os campos.
  - **Roteiros do agente:** 7, comparando cada resposta, o contexto entregue à IA, o arquivo da
    política e o conteúdo final das cinco tabelas. Cobrem benchmark validado e instável, contadores
    reiniciados, erro de hardware, IA (sugestão, falha e ausente), emergência, a espera de 30 s, e
    testes aceitos, rejeitados, vencidos, cancelados e interrompidos, além das fronteiras exatas
    (10 s de sensor, 180 s de hashrate, 70 % de cobertura, empate de perfis, 97 % e 240 s).
- **Média exata:** `statistics.mean` soma frações exatas e arredonda uma vez; o Rust soma as
  mantissas num inteiro grande. Em 3 dos 9 casos do golden (por exemplo `[0.1, 0.2, 0.3]`) a
  soma de floats erra.
- **Mutações:** 46 mutações, todas detectadas. As 27 que escaparam dos roteiros naturais ganharam
  casos de fronteira.
- **Diferenças conhecidas:**
  - em JSON que o Python nem aceitaria (configuração de supervisor que não é objeto), o Rust
    ignora o arquivo em vez de levantar `AttributeError`;
  - erros de SQLite e de disco têm o texto de cada linguagem.

**Não testado:** nada desta etapa escreve no hardware. O uso com a GPU real vem com o laço da
etapa 4c-2b, com o Renan presente.

## Validação da etapa 4c-1 (Windows 11, 09/11/2026)

O `thermal_target.py` foi portado para `hashpilot-core::thermal_target`. São dois componentes:
- `TargetTemperatureController` decide subir, descer ou manter o clock do núcleo pela temperatura-alvo e aprende tetos;
- `MemoryExplorer` mede cada clock de memória candidato e trava no melhor.

- **Golden `thermal_target`:** 23 casos executados pelo Python real.
  - **Traços longos:** 5 do controlador, cada um com uma placa simulada que esquenta conforme o clock e com as decisões aplicadas por `notify_applied`; 5 do explorador, com clock que o driver não aplica, erros de hardware, objetivo por lucro e reexploração depois de 6 h.
  - **Roteiros de fronteira:**
    - controlador: empate no arredondamento, ventoinha exatamente em 95 %, estouro dentro e fora da janela de 90 s, o menor de dois tetos, folga reiniciada, subida esperando a descida e ganho mínimo no limite;
    - explorador: empate de memória a 0,4 % e a 0,6 %, só 2 amostras, soma compensada do Python 3.12 e a reexploração.
  - O teste compara cada decisão (ação, motivo e teto) e o estado final.
- **Paridade fina:**
  - `round()` e `:.0f` arredondam empates para o par;
  - a ventoinha aparece como `str(float)`;
  - a média usa a soma compensada (Neumaier) do `sum()` do Python 3.12+.
- **Mutações:** 21 mutações. As 7 que escaparam dos traços naturais ganharam roteiros de fronteira, e 20 são detectadas agora. A que sobra é equivalente: tirar o "melhor primeiro" da fila de reexploração não muda nada, porque o Python remove esse clock da fila logo em seguida.

## Validação da etapa 4b-3b-2 (Windows 11, 09/11/2026)

- **Golden `nvidia_detect`:** 19 cenários, executados pelo `detect_nvidia_gpus` e pelo `read_gpu_samples` reais com um `nvidia-smi` simulado. O teste Rust compara o resultado, o erro e cada chamada ao comando. Cobre:
  - duas GPUs com catálogo pelo nome e pelo PCI;
  - consulta campo a campo quando o driver recusa o lote, e campos `[N/A]`;
  - PCI divergente e nome desconhecido;
  - linhas inválidas e nome entre aspas com vírgula;
  - UUID vazio ou inválido, que derruba a detecção como no Python;
  - limite de placas e cache de capacidades (validade de 600 s, relógio voltando);
  - sensores: GPU ausente, linha de outra GPU, índice trocado, temperatura inválida, linha repetida e falha do comando.
- **Argumentos do `rig`:** testes de unidade com as mesmas recusas do `argparse` do `rig.py` e o mesmo pedido gravado, na mesma ordem de chaves.
- **`rig --serve --check` real**, com os caminhos do `Iniciar-Rig.ps1`:
  - SRBMiner 3.8.4 conferido pelo SHA-256;
  - RTX 3070 detectada com `name_and_pci` e 114 clocks suportados;
  - sensores lidos (76,9 °C, 177 W);
  - nenhum pedido gravado, pasta de estado idêntica antes e depois.
- **Não testado:** o `--serve` real. Ele inicia e para a mineração e restaura a GPU; o primeiro uso fica para quando o Renan estiver presente.

## Validação da etapa 4b-3b-1 (Windows 11, 09/11/2026)

- **Golden `rig_support`, tipo `nvml`:** 27 configurações de um driver falso, executadas pelo `NvidiaDefaults.restore()` real (só o `call` é trocado). O teste Rust compara o relatório e a sequência completa de chamadas ao NVML. Cobre:
  - falha de cada escrita e de cada leitura, função ausente e tudo falhando;
  - escrita aceita sem efeito (ventoinha, offset e potência), conferida pela releitura;
  - limites de ventoinhas (0, 16 e 17), de potência (999, 1000, 2000000 e 2000001) e de offset;
  - P-states repetidos, inválidos ou ausentes, e offsets já zerados.
- **Golden `gpu_control`** (só Windows): 17 cenários da trava por GPU contra o `NvidiaActuator` real, com trava real: arquivo legado com processo vivo, morto, sem PID ou com PID inválido, arquivo corrompido, trava ocupada, posse trocada ou apagada, versão `2.0`.
- **Ordem:** a trava vem antes de abrir o driver; o driver é fechado antes de soltar a trava; com a trava ocupada, o driver nunca é aberto.
- **Leitura real, sem escrita:** `hashpilot nvml` na RTX 3070 lê as mesmas políticas de ventoinha, P-states, offsets com limites e potência que o `gpu_defaults.py` real lê no mesmo driver.
- **Mutações:** 15 mutações na restauração e na trava, todas detectadas. A que escapou no início (recusar `lease_version` 2.0) ganhou um cenário com processo vivo.
- **Nenhuma escrita real** no NVML, e nenhum processo real foi iniciado ou parado.

## Validação da etapa 4b-3a (Windows 11, 09/11/2026)

- **Golden `rig_manager`** (só Windows): 44 cenários. Os 8 novos cobrem:
  - atualização validada, revertida, bloqueada, com GPU desabilitada, pela mesma versão e por pedido do painel;
  - executável adulterado e seleção adulterada;
  - coleta de snapshots com hashrate, pausa e versão lidos do log.
  - O gerador roda com `TZ=UTC`, e o teste Rust lê o log com o mesmo fuso, então o golden não depende do fuso da máquina.
- **Golden `rig_support`:** o catálogo embutido no Rust é igual ao `CATALOG` e ao `DEFAULT_VERSION` do `miner_update.py`.
- **Testes do `serve`** (com o backend falso):
  - ciclos até o pedido de desligamento, com todas as GPUs paradas e restauradas no fim;
  - erro de ciclo registrado sem parar o laço;
  - referência não confirmada recusada antes de qualquer processo;
  - espera mínima de 0,1 s;
  - sinal contra a suspensão só com mineração ativa.
- **Testes reais (só leitura ou inofensivos):**
  - `hashpilot miner` confere o SRBMiner 3.8.4 instalado neste PC e chega à mesma versão e ao mesmo caminho que o Python;
  - com `HASHPILOT_PYTHON` definido, a trava do Rust e o `ProxyLease` real do Python se excluem nos dois sentidos;
  - uma junção real criada com `mklink /J` é recusada.
- **Mutações:** 22 mutações no código novo. As que escaparam ganharam cenários ou testes. Fica uma não detectada: gravar o `latest.json` indentado em vez de compacto, porque o teste compara o JSON interpretado e não o texto.
- **Diferença conhecida:** no Python, só `OSError`, `RuntimeError`, `ValueError` e `SubprocessError` ficam presos no ciclo. No Rust, todo erro de um ciclo é registrado e o laço continua.
- **Nenhum processo real foi iniciado ou parado.**

## Validação da etapa 4b-2 (Windows 11, 08/11/2026)

- **Golden `rig_manager`** (só Windows):
  - 36 cenários descritos em dados, executados pelo `RigManager` Python real com o `FakeBackend` do `test_rig.py`;
  - os cenários incluem todos os do `test_rig.py` e outros mais: pedidos de todos os tipos, inclusive inválidos, antigos e repetidos; sucessor com e sem pai, ambíguo e ausente; recuperação do controlador; executável bloqueado e liberado; reinício automático pela saúde, com e sem sensor seguro; registro cheio; GPU desconectada no meio da lista; sete GPUs;
  - o teste Rust interpreta os mesmos passos com um backend falso idêntico;
  - depois de cada passo, compara o resultado, o registro de GPUs (inclusive a ordem), o estado do gerenciador e o que o backend fez; no fim, compara os arquivos gravados e o `operations.sqlite3`.
- **Mutações:** 26 mutações aplicadas à mão no `manager.rs`. As que escaparam no início ganharam cenários, e todas são detectadas agora.
- **Diferenças conhecidas:**
  - o texto do erro de JSON malformado (registro, pedido ou `settings.json`) vem do `serde_json`, e não do `json` do Python;
  - um `at` não numérico num pedido gera a mensagem genérica "Pedido de rig antigo ou inválido".
- **Nenhum processo real foi iniciado.**

## Validação da etapa 4b-1 (Windows 11, 08/11/2026)

- **Golden `rig_support`** (2.394 casos, gerado no Windows e no Linux):
  - 2.304 combinações de `health`: estados, limites e tipos, incluindo `True`, `1` e `None` em `running`;
  - uma sequência do `OperationalStore` com o resultado de cada operação e o banco final, tabela por tabela;
  - a janela de continuidade, o sensor seguro, o perfil de referência, os perfis de objetivo e o JSON indentado.
- **Golden `process`:** ganhou 24 casos de `match_successor`, que dependem de caminhos do Windows.
- **Mutações:** 16 mutações aplicadas à mão. As que escaparam no início ganharam casos no limite exato (180 s, 60 s, 90 s e 7200 s). Duas são equivalentes e não mudam o comportamento: `>= 300`, que o limite mínimo de 900 s torna inalcançável, e `count > 1`, que com contagem zero devolve o mesmo `None`.
- **Dados reais:** numa cópia do `operations.sqlite3` do rig (216 recuperações e 1.002 incidentes), `budget` e `recent_events` deram o mesmo resultado no Python e no Rust em 1.339 instantes. Os instantes cobrem os limites de 60 s e de 1 h em torno de cada registro real.

## Validação da etapa 4a (Windows 11, 08/11/2026)

- **Golden `process`:** 31 linhas de comando e 5 caminhos, gerados pela API real do
  Windows. Ele só é gerado no Windows. O teste de goldens desatualizados passou a
  rodar também no Windows, para `history`, `finance` e `process`.
- **Processos reais inofensivos** (`PING.EXE` no loopback, nunca o minerador):
  - `start` com log e identidade completa;
  - um segundo backend, como o serviço depois de reiniciar, reconhece o processo pela
    linha de comando real;
  - identidades adulteradas são recusadas: `created_at`, `argv` e `launch_id` vazio;
  - `stop` pelo backend que criou o processo e por outro, via `TerminateProcess`;
  - `wait`;
  - PIDs inválidos;
  - o próprio processo de teste, que não é do backend, nunca é encerrado.
- **Rig real, só leitura:** no SRBMiner e no supervisor que estão minerando, o
  `inspect` do Rust deu PID, criação, executável e argv **idênticos** aos do Python.
  O `created_at` bate com o gravado pelo serviço Python no `rig-manager.json`.
  Daqui, sem elevação, os dois dizem "não é próprio": o serviço roda como
  administrador e o Windows esconde a linha de comando de processo elevado. A
  verificação com `owned: true` exige rodar a CLI elevada.

## Validação da fase 3 (Windows 11, 08/11/2026)

- `cargo test`, `cargo clippy --all-targets -- -D warnings` e `cargo fmt --check`
  passam no workspace inteiro.
- **Respostas reais (teste real):** as seis respostas reais da Kryptex e da
  CoinGecko para a carteira configurada foram gravadas pelo `fetch_json` do Python.
  Com as mesmas respostas, o coletor Python e o Rust produziram o **mesmo estado,
  texto idêntico**, sem erros.
- **Consulta ao vivo pelo Rust (teste real):** seis fontes, sem erros, em 0,9 s. As
  chaves são as mesmas do Python e a cotação também (R$ 9,27 por PRL, com segundos
  de diferença entre as duas consultas).
- **Cache real:** `financial_model` sobre o `finance-cache.json` em uso (20.688
  snapshots), sem e com hashrate (76,57 TH/s), deu resultado **idêntico** ao do
  Python.
- Não testado: o coletor Rust rodando no lugar do Python, gravando no histórico
  real. Isso entra na troca da fase 4.

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
Tools do Visual Studio. As finanças usam `ureq` 3 com `rustls` (provedor `ring`) e as
raízes `webpki-roots`, sem gzip. O `Cargo.lock` é versionado para builds reprodutíveis. Código sob [GPL-3.0-only](../LICENSE).
