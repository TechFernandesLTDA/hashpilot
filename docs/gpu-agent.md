# Agente NVIDIA, benchmark e ajustes dinâmicos

O HashPilot combina um controlador adaptativo local com um consultor de IA opcional via Ollama. O controlador funciona sem conta, token ou API paga. O modelo de linguagem só escolhe entre ações enumeradas; o programa valida temperatura, dados recentes, limites reais do driver e posse exclusiva antes de escrever no hardware.

## O que é medido e o que foi validado

`gpu_agent.py` detecta GPUs pelo `nvidia-smi`, identifica cada unidade por UUID e consulta seus próprios clocks suportados e limites mínimo, máximo e de fábrica de potência. Uma especificação do catálogo não autoriza um ajuste. Campos que o driver não informa permanecem indisponíveis e bloqueiam o respectivo tipo de escrita.

O motor e o catálogo aceitam apenas NVIDIA. A telemetria e o parser de hashrate atuais da interface correspondem à **GPU 0**. O armazenamento separa UUIDs, mas isso não significa benchmark simultâneo validado de todas as GPUs de um rig. O supervisor exige correspondência de UUID no snapshot; o fallback sem UUID só é permitido com uma única GPU, índice 0.

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

## IA local gratuita

Quando habilitado no onboarding, o consultor usa Ollama local com `qwen2.5:0.5b`. A integração chama somente um endereço de loopback e não usa serviços de nuvem. A inferência solicita `num_gpu: 0`, contexto de 2.048 tokens e resposta curta, para preservar a GPU usada na mineração. Há consumo de CPU/RAM e download do runtime/modelo.

O modelo retorna JSON com uma ação: `hold`, `clock_up`, `clock_down`, `power_up`, `power_down` ou `restore_best`, mais uma justificativa. Não fornece comandos de shell, valores arbitrários de clock ou autorização de hardware. JSON inválido, indisponibilidade ou timeout deixam a política adaptativa funcionando e exibem o estado da IA como indisponível. **Uma sugestão de IA não equivale a um ajuste aplicado.**

Instalação e downloads opcionais são apresentados pelo onboarding. Um Ollama já configurado pode ser usado pelo CLI com `--ollama`. O cliente tem timeout limitado e desabilita proxies HTTP para manter a telemetria no computador.

No supervisor que escreve no hardware, a inferência roda em uma thread separada, com no máximo uma solicitação por minuto e aproveitamento temporário da última resposta. A leitura térmica e os controles continuam em seu ciclo de dois segundos enquanto o modelo responde. Toda sugestão é revalidada contra a telemetria atual antes de ser aplicada.

## Histórico e propriedade do controle

`gpu-agent.sqlite3` no diretório local do aplicativo usa WAL e `synchronous=FULL`. As tabelas `benchmarks`, `observations`, `decisions` e `trials` preservam sessões, medições, recomendações, cancelamentos e resultados por UUID. Novo benchmark não apaga sessões anteriores.

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

O modo `--control` é destinado ao iniciador que acompanha o ciclo de vida do minerador. Não deve ser iniciado em paralelo ao controlador histórico ou a outro utilitário de ajustes. Requer `--miner-pid`, `--state-dir`, `--monitor-state-dir` e elevação no Windows. Um retorno diferente de zero exige que o iniciador encerre o seu minerador.

Testes locais, sem escrever em uma GPU:

```powershell
mise exec -- python -m unittest -v test_gpu_agent.py
```

## Fontes técnicas

- [NVIDIA System Management Interface](https://docs.nvidia.com/deploy/nvidia-smi/): consultas, UUIDs, clocks suportados, potência e limitações por dispositivo/driver.
- [Ollama — Generate](https://docs.ollama.com/api/generate): API local, resposta não streaming e opções de geração.
- [Ollama — Structured outputs](https://docs.ollama.com/capabilities/structured-outputs): saída JSON com schema; o HashPilot revalida a resposta antes de usá-la.
