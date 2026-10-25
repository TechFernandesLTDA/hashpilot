# Agente NVIDIA, benchmark e ajustes dinâmicos

O HashPilot combina um controlador adaptativo local com consultores opcionais: Ollama local, OpenRouter Cloud Free e OpenCode Cloud Free pelo cliente. O programa valida cada sugestão antes de alterar o hardware.

## O que é medido e o que foi validado

`gpu_agent.py` detecta GPUs pelo `nvidia-smi`, identifica cada unidade por UUID e consulta seus próprios clocks suportados e limites mínimo, máximo e de fábrica de potência. Uma especificação do catálogo não autoriza um ajuste. Campos que o driver não informa permanecem indisponíveis e bloqueiam o respectivo tipo de escrita.

O motor e o catálogo aceitam apenas NVIDIA. O painel acompanha **até seis GPUs**, com telemetria e parser de hashrate isolados por UUID. Cada GPU tem sua instância do minerador, log, supervisor e banco de benchmark. O índice do minerador é associado ao barramento PCI e pode diferir do índice `nvidia-smi`. Um snapshot agregado jamais serve como hashrate individual; o fallback legado sem UUID só é permitido com uma única GPU, índice 0. [Operação multiGPU](multi-gpu.md).

As evidências físicas incluídas no repositório são da RTX 3070 do autor. Testes automatizados de outros dispositivos usam respostas simuladas. A presença de um modelo no catálogo não promete suporte a todos os comandos do driver, estabilidade de clocks, suporte do minerador ou rentabilidade.

## Benchmark automático e manual

O painel identifica a GPU e inicia uma observação de cinco minutos quando ainda não há sessão. **Benchmark / IA** permite repetir a medição quando o usuário quiser, escolhendo desempenho ou eficiência. Em uma mineração iniciada com o novo supervisor, o mesmo pedido reinicia também a calibração do supervisor, desfazendo um experimento pendente antes de coletar nova referência.

Uma sessão só é validada quando possui:

- o tempo configurado, normalmente 300 segundos;
- cobertura de sensores de pelo menos 70% da janela, sem contar lacunas maiores que 15 segundos;
- pelo menos três atualizações independentes de hashrate após o aquecimento inicial;
- pelo menos três medições em um perfil de clock/potência para comparar os resultados;
- ausência de erros de sensor, superaquecimento, reinício do minerador ou novas shares rejeitadas após o aquecimento inicial.

O mesmo hashrate repetido em um log não vira várias medições. Um reinício não preenche o intervalo em que o aplicativo ficou fechado. Falta de amostras resulta em benchmark insuficiente, sem inventar um perfil validado.

No supervisor, uma janela encerrada sem dados suficientes inicia automaticamente outra referência, preservando a tentativa anterior. A falta de hashrate no começo da instalação não deixa o controle preso permanentemente na primeira janela.

Desempenho usa a média de TH/s; eficiência usa TH/s por watt medido. O resultado é do algoritmo, do minerador e das condições observadas. Não é um benchmark de jogos nem uma previsão de preço de PRL.

## Ciclo de controle

O painel de monitoramento permanece em observação e recomendação. O iniciador do minerador abre um **supervisor exclusivo** elevado quando o usuário seleciona mineração e ajuste. O supervisor:

1. mede uma referência de pelo menos cinco minutos antes de experimentar aumentos;
2. propõe apenas um degrau por vez, de até 15 MHz no core ou 5 W na potência;
3. usa somente clocks listados pelo driver e nunca eleva a potência acima do limite de fábrica;
4. verifica o resultado por 90–240 segundos, com pelo menos duas novas medições de hashrate;
5. propõe reversão do último degrau se houver novas rejeições, calor, falta de medições ou perda de hashrate maior que 3%;
6. inicia uma nova referência para continuar comparando os resultados.

A validação curta confirma estabilidade inicial; não substitui testes prolongados. O motor não altera tensão, offset de memória, BIOS ou curva de ventoinha. Cada capacidade precisa ser suportada pelo driver; falha de um comando encerra o supervisor para o iniciador parar seu próprio minerador.

O alívio térmico tem prioridade durante a própria medição: a partir de 76 °C, ou antes caso o driver exponha um limite menor, reduz o core em até 15 MHz a cada seis segundos. Aos 83 °C, ou no limite menor calculado para o dispositivo, o supervisor retorna falha. O iniciador deve então encerrar **somente o minerador que ele abriu**. Ao sair, tenta restaurar clock automático e a potência inicial dentro dos limites do dispositivo; qualquer erro de restauração fica no status.

O perfil histórico RTX 3070 possui limites e histerese próprios, documentados em [CONTROLE-TERMICO.md](../CONTROLE-TERMICO.md). O agente recusa adquirir controle se detectar esse supervisor em execução. Atualizar o monitor não troca o controlador de uma mineração já em andamento.

## IA local ou Cloud Free

Escolha OpenRouter Cloud Free, OpenCode Cloud Free pelo cliente oficial ou Ollama local em **Benchmark / IA > Configurar IA**. Para rigs com CPU simples, OpenRouter evita inferência local. A política adaptativa, o benchmark e a proteção térmica continuam independentes do modelo. Consulte [provedores, cadastro, cotas e dados enviados](ai-providers.md).

As respostas são sugestões JSON com ação enumerada e justificativa. O controlador revalida a telemetria atual e limita cada ajuste. A inferência roda em segundo plano. Não há fallback pago; a chave OpenRouter fica no cofre do Windows. Ollama Cloud possui créditos iniciais e preços por uso, sem promessa de gratuidade contínua.

## Histórico e propriedade do controle

Cada diretório `LOG_DIR/gpus/<UUID>/` possui seu `gpu-agent.sqlite3`, em WAL e `synchronous=FULL`. As tabelas `benchmarks`, `observations`, `decisions` e `trials` preservam sessões, medições, recomendações, cancelamentos e resultados por UUID. Novo benchmark não apaga sessões anteriores. O banco legado permanece preservado; resultados não são transferidos automaticamente para outra identidade física.

`gpu-control-owner.json` funciona como trava exclusiva. Se o processo for encerrado abruptamente e deixar a trava, o sistema bloqueia outro controlador. Antes de recuperar, confirme que o PID registrado não está mais executando, que o minerador foi parado e que nenhum utilitário está ajustando a GPU; então arquive a trava para permitir nova sessão. O aplicativo não apaga automaticamente uma trava cuja posse esteja em dúvida.

`gpu-agent-control.json` contém estado, benchmark, experimento, contador de escritas e erros do supervisor. `gpu-agent-request.json` aceita apenas um pedido recente de benchmark para o mesmo UUID; não aceita comandos de texto. `gpu-agent-policy.json`, quando exportado, é uma sugestão temporária baseada em resultado validado e contém `authorization: false`.

## Comandos para desenvolvimento

Detecção, sem alteração de hardware:

```powershell
mise exec -- python gpu_agent.py --snapshot
```

Medição somente leitura do minerador que já está rodando:

```powershell
mise exec -- python gpu_agent.py --benchmark --duration 300 --objective performance
```

O modo `--control` é destinado ao iniciador que acompanha o ciclo de vida do minerador. Não deve ser iniciado em paralelo ao controlador histórico ou a outro utilitário de ajustes da mesma GPU. Requer `--miner-pid`, `--state-dir`, `--monitor-state-dir` e elevação no Windows. O gerenciador fornece também `--uuid`, `--index`, `--miner-index` e `--miner-log` para impedir mistura entre dispositivos. Divergência de UUID/índice é recusada; reutilização do PID por outro processo encerra a supervisão. Um retorno diferente de zero exige que o iniciador encerre somente o minerador que possui.

Uma parada normal usa `gpu-agent-stop.json`, com UUID, PID do supervisor e timestamp recentes. O supervisor rejeita pedidos antigos ou destinados a outra GPU e tenta restaurar os ajustes ao sair. Essa fila não aceita comandos de shell.

Testes locais, sem escrever em uma GPU:

```powershell
mise exec -- python -m unittest -v test_gpu_agent.py
```

## Fontes técnicas

- [NVIDIA System Management Interface](https://docs.nvidia.com/deploy/nvidia-smi/): consultas, UUIDs, clocks suportados, potência e limitações por dispositivo/driver.
- [Ollama — Generate](https://docs.ollama.com/api/generate): API local, resposta não streaming e opções de geração.
- [Ollama — Structured outputs](https://docs.ollama.com/capabilities/structured-outputs): saída JSON com schema; o HashPilot revalida a resposta antes de usá-la.
