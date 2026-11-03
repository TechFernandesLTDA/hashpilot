# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

HashPilot é um monitor desktop (Python/Tkinter, só biblioteca padrão) de mineração Pearl (PRL) com SRBMiner em GPUs NVIDIA. **Exclusivo para Windows 10/11 x64**: `config.py` levanta `RuntimeError` fora do Windows, então testes e imports também só rodam no Windows. Docs, mensagens e comentários são em português do Brasil (há `README.en.md` e `docs/en/` paralelos).

## Comandos

Python 3.14 vem do mise (`mise.toml`). Rode tudo pela raiz do repositório.

```text
mise install
mise exec -- python -m unittest discover -v          # suíte completa (test_*.py na raiz)
mise exec -- python -m unittest -v test_rig.py        # um arquivo
mise exec -- python -m unittest -v test_rig.RigConfigurationTests.test_six_same_or_mixed_models_have_independent_uuid_paths_and_default_wallet   # um teste
mise exec -- python monitor.py                         # painel (também --summary, --console, --setup, --snapshot, --smoke-test)
mise exec -- python tools/check_documentation.py      # links locais dos .md/llms.txt e JSONs públicos
```

- QA de interface é opt-in e abre janelas reais com dados temporários: `test_responsive_ui.py --run --native-dpi`, `test_windowing.py --run --native-dpi`, e os `tools/qa_*.py`. Relate dimensões pedidas/reais, área útil, DPI e se 4K foi virtual ou físico.
- Release: `tools/release_package.py build --ref vX.Y.Z --version vX.Y.Z --output dist/...` gera um ZIP determinístico só a partir dos blobs do commit (com `RELEASE-MANIFEST.json`); `verify` confere um ZIP contra `SHA256SUMS`. A versão do código fica em `app_version.py`; `install.ps1` e README fixam a tag da release (atualize os três juntos).
- Antes de publicar (ver `docs/verification.md`): suíte de testes, `check_documentation.py`, análise sintática dos `.ps1` e verificação de segredos (gitleaks).
- Não há CI: validação é local. Não crie workflows em `.github/workflows`. O hook `.githooks/pre-push` roda testes, `check_documentation.py`, `tools/check_powershell.ps1` e gitleaks; ative com `git config core.hooksPath .githooks`.
- Módulo novo usado pelo serviço do rig precisa entrar em `data/rig-runtime.json` (o runtime instalado só copia essa lista; `test_stop_defaults` confere).
- Arquivos `.py` são LF no repositório (`.gitattributes`); ao editar por script no Windows, grave com `newline='\n'`.

- `dist/` é ignorado pelo git e guarda evidências locais de QA/releases (resultados de testes, pacotes reproduzidos). Não é fonte de verdade do código.

## Arquitetura

Layout plano: módulos e `test_*.py` ficam todos na raiz e se importam diretamente (`from rig import RigManager`). Diagrama e tabela de módulos em `docs/architecture.md`; contratos multiGPU em `docs/multi-gpu.md`.

Separações que devem ser preservadas:

- **Monitor (só leitura) × controle de hardware.** `monitor.py` (Tk, três abas) + `gpu_telemetry.py` leem `nvidia-smi` e logs do SRBMiner; abrir ou fechar o painel nunca inicia/para mineração nem altera clocks. Iniciar é sempre explícito.
- **Rig por UUID.** `rig.py` (`--serve` é o serviço; `--request` enfileira pedidos em `rig-requests/`) gerencia até seis GPUs por UUID persistente (`rig_config.py`, `MAX_GPUS`), com um SRBMiner, log, supervisor e `gpu-agent.sqlite3` por GPU em `LOG_DIR/gpus/<UUID>/`. O índice do SRBMiner pode diferir do índice NVIDIA (mapeamento pelo barramento PCI). O gerenciador só encerra processos que ele criou (protege contra PID reutilizado e processos alheios); nunca dois supervisores na mesma GPU.
- **Política pura × aplicação real.** `thermal.py` (decisão de clock a partir de temperatura/tempo) é testável sem hardware; `thermal_controller.py` e `gpu_agent.py --control` escrevem na GPU e exigem elevação. O fluxo histórico da RTX 3070 do autor (`*-Referencia-RTX3070.ps1`, `REFERENCE_RTX3070_UUID`) é separado do iniciador genérico e não deve ter seus clocks copiados para outros modelos.
- **Finanças.** `finance.py` consulta Kryptex/CoinGecko (cache, ciclo de 60 s), `payout.py`/`valuation.py` calculam ETA e equivalentes BRL/USD/BTC. Créditos observados = variação de (total pago + saldo), deduplicados **por carteira efetiva**; nunca derivar ganho por GPU a partir de hashrate. Regras em `PAGAMENTOS.md`.
- **Contribuição** (`contribution.py`): proxy TCP alterna 57 min para a carteira do usuário e 3 min para `AUTHOR_WALLET`; desativável; sem divisão extra se a carteira já é a do autor. A carteira padrão (`config.DEFAULT_WALLET`) é a do autor, de propósito.
- **Persistência.** Estado em `%LOCALAPPDATA%\PearlMonitor` (nome legado, mantido por compatibilidade). `history.py` grava SQLite (WAL, `synchronous=NORMAL`) numa thread própria, com linhas de log em lote e posições em `history-log-offsets.json`; a chave SHA-256 das linhas de log é o que deduplica entre reinícios, não a mude; sem expurgo automático; migrações preservam histórico e isolamento por carteira/sessão/UUID. `operations.sqlite3` guarda ACKs e orçamento de reconexão (`mining_health.py`, `mining_continuity.py`, `operations_ui.py`).
- **Configurações.** `settings_ui.py` é a janela única (navegação lateral, seções responsivas). As telas de GPUs, minerador, Defender, IA, atualizações e Central de operação continuam nos seus módulos e recebem `container=`; `ui_host.Section` dá a elas a interface de janela, e fechar/cancelar dentro de uma seção a recria. Botões que abririam outra janela usam `window.navigate('<seção>')` quando embutidos. QA: `tools/qa_settings.py`.
- **Atualizações.**
 `app_update.py` (painel, a partir do manifesto verificado da release) e `miner_update.py`/`miner_update_ui.py` (SRBMiner com validação por GPU e rollback) são fluxos distintos; atualizar o painel não reinicia o serviço silenciosamente.
- Scripts PowerShell de instalação/operação ficam em `mining/windows/`; `downloads.json` fixa URLs oficiais e SHA-256 dos binários de terceiros (que não são versionados).

## Regras do projeto (de CONTRIBUTING.md)

- Dado ausente ou desatualizado é estado explícito, nunca zero. Estimativa não é crédito realizado; entrada no catálogo (`data/nvidia-gpus.json`, 158 modelos) não é overclock validado.
- Só uma RTX 3070 foi validada fisicamente; cenários de 2–6 GPUs são simulados. Em docs e relatórios, diferencie teste real de simulação e diga qual GPU foi testada.
- `portable-data/` é o snapshot público e autorizado do autor (restaurado por `bootstrap.py` só em perfil sem banco). Não sobrescreva com dados locais; atualizar é intencional via `tools/export_portable_snapshot.py`, revisando o diff.
- Testes unitários não instalam/executam minerador nem alteram clocks.
- Marca e logos em `assets/brand/` não são GPL (ver `NOTICE`, `assets/brand/ORIGEM.md`): use os assets oficiais, não redesenhe.
- Mudou configuração, instalação, contrato de dados ou suporte de hardware: atualize a documentação correspondente (e `llms.txt` quando aplicável).
