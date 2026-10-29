# Verificação, distribuição e evidências

Este documento permite inspecionar e reproduzir o que o mantenedor entrega. Não é uma certificação de segurança nem uma revisão independente. A revisão interna de código/testes e medições do autor devem ser distinguidas de resultados de terceiros.

## Matriz de evidências

| Área | Evidência disponível | Limite |
|---|---|---|
| RTX 3070 física | [Observação de 15 minutos](performance/rtx3070.md), com 448 amostras de sensores e 10 atualizações de hashrate; benchmark posterior de 300 s no [relatório de QA](qa/2026-10-25-auditoria.md) | Uma unidade, sem comparação A/B; não é teste de 24/72 horas |
| Duas a seis GPUs, iguais ou diferentes | Testes simulados de UUID, seleção, processos, histórico e carteira | Nenhum rig físico com múltiplas GPUs validado pelo mantenedor |
| Outros modelos NVIDIA | Catálogo com fontes primárias, descoberta pelo driver e testes de política | Entrada no catálogo não comprova compatibilidade de mineração ou overclock |
| Instalação Windows limpa | Verificador do instalador exercitado com pacote válido e adulterado | Instalação completa em máquina limpa ainda precisa de teste externo |
| Release | ZIP determinístico, inventário com hashes e teste de reprodução | Sem Authenticode ou auditoria independente |
| Estabilidade 24/72 h | Coletor passivo e protocolo abaixo | Só há resultado quando uma sessão terminar e for revisada |

## Análise de segurança independente

A revisão do SRBMiner 3.8.1 está [pendente](security-review.md); o Defender colocou a amostra em quarentena. Não declare falso positivo confirmado ou aprovação de terceiros. A mídia pública atual oculta dados financeiros para privacidade e informa essa alteração; cenários de seis GPUs continuam simulados.

## Conferir uma release

Baixe `hashpilot-v0.5.4.zip`, `hashpilot-v0.5.4.manifest.json` e `SHA256SUMS` da mesma [release](https://github.com/TechFernandesLTDA/hashpilot/releases/tag/v0.5.4). Leia o código e as limitações antes de executar. Verifique o hash no PowerShell:

```powershell
Get-FileHash .\hashpilot-v0.5.4.zip -Algorithm SHA256
Get-Content .\SHA256SUMS
```

Os valores precisam coincidir. Para conferir também cada membro do ZIP usando uma cópia inspecionada do repositório:

```powershell
mise exec -- python tools/release_package.py verify .\hashpilot-v0.5.4.zip --checksums .\SHA256SUMS
```

O manifesto inclui o commit exato e os hashes/tamanhos de todos os arquivos. A lista de hashes não autentica o publicador por uma autoridade externa: alguém com controle da conta poderia substituir pacote e lista. Não contorne alertas do Windows ou desative antivírus.

## Reproduzir o pacote

Em um clone confiável, obtenha a tag e confirme que seu commit corresponde ao manifesto. Execute:

```powershell
git fetch origin tag v0.5.4
mise exec -- python tools/release_package.py build --ref v0.5.4 --version v0.5.4 --output dist/reproduced
```

O empacotador usa somente blobs do commit Git indicado: não inclui `.env`, configurações locais ou modificações não commitadas. O conteúdo público de `portable-data/`, já versionado e autorizado pelo autor, continua incluído. Os membros têm ordem, data e permissões fixas, sem compressão dependente de versão de biblioteca. Compare SHA-256 do ZIP gerado ao publicado. Isso torna o **pacote de código-fonte** reproduzível; não declara build reproduzível de Python, drivers ou SRBMiner.

Antes de publicar: execute a suíte de testes, `tools/check_documentation.py`, análise sintática PowerShell e verificação de segredos. Anexe resultados identificando commit, sistema e eventuais skips; não reutilize resultados de outra revisão como se fossem atuais.

## Diagnóstico que pode ser revisado para compartilhar

No diretório do repositório, com o painel em execução:

```powershell
mise exec -- python tools/validation_capture.py diagnostic --output diagnostic.json
```

O comando recusa sobrescrever arquivos existentes. Exporta apenas horários, modelo NVIDIA reconhecido por padrão restrito, versão numérica do driver, estado de mineração e campos numéricos de telemetria/shares. Campos desconhecidos são descartados, não apenas mascarados por nome. Erros de leitura viram um estado genérico, sem mensagem que revele caminhos. Não exporta seed, API key, carteira, UUID, hostname, usuário, configuração, saldos, banco ou logs brutos. Ainda revela modelo e condições operacionais: revise o JSON antes de anexar. Nada é enviado automaticamente.

## Protocolo de estabilidade de 24 e 72 horas

1. Registre release/commit **realmente em execução**, Windows, driver, modelo e quantidade de GPUs. Confira carteira e opção da ajuda de 5%. Use ambiente em que você autorizou mineração e controle. O teste não exige divulgar seu endereço.
2. Teste primeiro com configuração original do driver. Depois faça uma sessão separada com controle adaptativo. Mantenha pool, versão do minerador, temperatura ambiente e duração comparáveis; anote mudanças. Não confunda os 5% de tempo de contribuição com rejeições ou devfee do minerador.
3. Colete 24 horas sem alterar parâmetros para melhorar artificialmente o resultado. O coletor abaixo apenas lê snapshots; não inicia minerador, IA ou comandos NVIDIA. Troque `COMMIT_DE_40_CARACTERES` pela revisão observada:

```powershell
mise exec -- python tools/validation_capture.py --revision COMMIT_DE_40_CARACTERES observe --hours 24 --interval 30 --output observation-24h.jsonl
```

4. Interromper o comando, encerrar o Windows ou dormir/hibernar deixa uma sessão incompleta ou com lacunas. O arquivo é gravado a cada amostra. Não substitua lacunas por zeros ou interpolações; preserve o arquivo e inicie outra sessão em outro nome. O alias `slot` representa a ordem na captura, não identidade estável após uma mudança de hardware; mudanças exigem sessões separadas.
5. Analise sem retomar a mineração:

```powershell
mise exec -- python tools/validation_capture.py summarize observation-24h.jsonl --hours 24 --interval 30
```

6. Revise duração, cobertura, lacunas, amostras saudáveis, máximo térmico, hashrates independentes e contadores brutos de shares. Os contadores são cumulativos: não some contadores de cada amostra. Registre resets/reinícios e calcule deltas por segmento. Depois repita com `--hours 72` e outro arquivo, se a sessão anterior estiver adequada.

O resumo usa critérios conservadores: snapshot/sensor até 30 s, hashrate até 180 s, mineração ativa, nenhuma pausa, temperatura abaixo de 84 °C, contador de erros de hardware igual a zero e cobertura temporal mínima de 99%. O teto de 84 °C é um alerta desta avaliação, não uma temperatura recomendada universal; os limites de cada hardware podem ser menores. `needs_review` exige explicar cada ocorrência, não esconder ou descartar amostras. `completed_with_observations` só indica os critérios do coletor; a taxa de rejeição, estabilidade da pool, energia na tomada e o histórico de intervenção ainda precisam de revisão humana.

`--revision` é uma declaração do operador. O coletor não prova qual processo produziu um snapshot, não mede consumo na tomada e não valida pagamento recebido. Não chame seu resultado de auditoria externa, certificação ou garantia de desempenho.

## Superfícies de rede e privilégios

| Função | Destino/dado | Ativação |
|---|---|---|
| Pacote/atualização | GitHub; versão e arquivos públicos | Instalação explícita |
| Runtime | Distribuição do mise/winget e Python | Instalador; sem mudança global de execution policy |
| Pool e pagamentos | API pública `pool.kryptex.com`; endereço público configurado | Coleta do painel |
| Cotação | `api.coingecko.com`; IDs de moedas | Coleta financeira |
| Minerador | Pool configurada; endereços públicos e shares, por TCP | Início explícito do serviço |
| Ajuda opcional | Proxy TCP em `127.0.0.1`; alterna destino entre carteira principal e autor | Opção visível e desativável |
| Ollama | `127.0.0.1:11434`; contexto de telemetria | Escolha explícita de IA local |
| OpenRouter | `openrouter.ai`; autenticação e contexto do consultor | Escolha/consentimento cloud |
| OpenCode | Catálogo `opencode.ai` e cliente oficial/provedor configurado | Escolha/consentimento cloud; consulte a configuração do cliente |
| Coletor de validação | Nenhuma rede; somente `latest.json` local | Comando explícito |

O painel e o diagnóstico não precisam de seed ou chave privada. Controle NVIDIA pode exigir elevação; o modo diagnóstico não a solicita. Não foi realizada captura independente de tráfego: a tabela descreve os caminhos implementados, não certifica componentes de terceiros. As [condições dos provedores de IA](ai-providers.md) e o [fluxo de arquitetura](architecture.md) trazem detalhes.

## Teste externo e revisão independente

Use o formulário **Validação de hardware** no GitHub para registrar uma sessão voluntária, identificando o commit, hardware, evidências e falhas. Um teste do mantenedor não deve ser rotulado como independente; contribuições de terceiros devem indicar origem e consentimento para publicação. Não inclua dados financeiros, sementes ou credenciais. Um revisor independente poderá avaliar código, rede e artefatos; hoje essa revisão permanece pendente. Não atribua aprovação da Kryptex ou de qualquer fornecedor ao projeto.
