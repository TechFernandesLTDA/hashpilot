# Rig com até seis GPUs NVIDIA

O HashPilot acompanha até **seis placas NVIDIA GeForce**, iguais ou de modelos diferentes, no Windows 10/11 x64. A identidade persistente é o **UUID informado pelo driver**, e não o nome do modelo ou a posição na lista. Duas RTX 3070 são dois dispositivos diferentes mesmo quando seus nomes são idênticos.

O catálogo de 158 modelos auxilia a identificação e documenta fontes; ele não certifica suporte de mineração ou ajuste em cada produto. Neste computador há **uma RTX 3070 física**. Os cenários com seis placas são testados com respostas simuladas, não apresentados como um rig físico medido.

## Começar

1. [Instale o aplicativo](installation.md), confira a carteira e a pasta dos logs.
2. Confira as GPUs detectadas. Cada placa nova fica **habilitada por padrão na configuração**. Isso não inicia mineração por conta própria.
3. Mantenha a carteira global ou configure uma carteira própria para uma GPU. Confira o endereço efetivo antes de iniciar.
4. Inicie explicitamente o serviço de mineração do rig. O gerenciador inicia uma instância do minerador e um supervisor para cada GPU habilitada e compatível.
5. Acompanhe a baseline e os resultados por placa. Se quiser, solicite novo benchmark de desempenho ou eficiência na GPU selecionada.

Abrir o painel, detectar hardware ou selecionar uma GPU é uma leitura. **O serviço de mineração só é iniciado por uma ação explícita.** Depois de iniciado, ele reconcilia a configuração: habilitar uma placa permite iniciar seus processos; desabilitá-la encerra somente o minerador, supervisor e proxy que pertencem àquela GPU.

GPUs além do limite de seis são informadas como excedentes e ignoradas. Elas não recebem processos ou perfis silenciosamente. Modelos de outros fabricantes não são controlados.

## Identificação e índices

O painel pode exibir nome, UUID, índice NVIDIA, barramento PCI, identificador PCI/subsistema, driver, VBIOS, memória e arquitetura quando disponíveis. Serial e outros campos que o driver não expõe aparecem como indisponíveis, sem valores inventados.

- **UUID:** chave de configuração, histórico e posse dos processos. Deve continuar identificando a mesma placa quando a ordem da enumeração mudar.
- **Índice NVIDIA:** posição atual utilizada nas consultas do driver. O controlador exige correspondência com o UUID antes de escrever.
- **Índice SRBMiner:** posição reconhecida pelo minerador. Pode diferir do índice NVIDIA; a associação é confirmada pelo barramento PCI antes do início.
- **Nome do modelo:** descrição humana, insuficiente para distinguir duas placas iguais.

O controlador envia comandos NVIDIA usando o UUID. Logs de uma instância exclusiva podem chamar sua placa de `GPU0`, mesmo quando o índice físico é outro. O parser só aceita esse alias local em um log isolado; em logs compartilhados, não atribui `Total:` ou médias globais a uma placa.

## Carteira global e por GPU

Configuração em `%LOCALAPPDATA%\PearlMonitor\settings.json`:

```json
{
  "pool": "prl.kryptex.network:7048",
  "wallet": "prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t",
  "log_dir": "",
  "support_enabled": false,
  "gpu_settings": {}
}
```

O endereço acima é **a carteira pública do autor**. Mantê-lo direciona os pagamentos ao autor. Você pode trocá-lo pela sua carteira pública. O aplicativo não solicita seed ou chave privada.

`gpu_settings` é um objeto indexado pelo UUID real. A estrutura de uma entrada é `"UUID_REAL": {"enabled": true, "wallet": null}`. `wallet: null` ou ausência de override herda a carteira global; um endereço público válido define o destino daquela GPU. Não copie um UUID fictício como se fosse sua placa.

A habilitação é individual e persistente. Um erro de configuração na entrada de uma GPU a desabilita com uma mensagem; não deve redirecionar mineração silenciosamente para outra carteira. Mudanças de carteira efetiva exigem reconciliar/reiniciar a instância correspondente do minerador; um processo externo não passa a ser controlado apenas porque o JSON mudou.

## O que é agregado e o que permanece individual

| Informação | Escopo |
| --- | --- |
| Hashrate, potência, shares e sensores | Por UUID; totais conhecidos podem ser somados para o rig |
| Temperatura, clock, ventoinha e benchmark | Individuais; uma placa não usa o perfil medido de outra |
| Minerador, supervisor e proxy opcional | Uma instância própria por GPU gerenciada |
| Carteira e saldo da pool | Por endereço efetivo, deduplicado |
| Ganhos realizados em PRL/BRL/USD/BTC | Variação observada da carteira; sem distribuição inventada por hashrate |
| Projeção de mineração | Estimativa identificada como tal; não é crédito recebido |

Se quatro GPUs usam a mesma carteira, o saldo dessa carteira aparece **uma vez** na soma financeira. Se duas outras usam um segundo endereço, ele também aparece uma vez. Uma resposta ausente da pool não vira zero nem faz um total parcial parecer completo.

O aplicativo não consegue inferir quanto uma placa específica recebeu apenas pela proporção de hashrate quando várias placas compartilham um endereço. Criar uma carteira por GPU permite observação financeira separada pelo endereço, sem mudar essa regra.

## Benchmark e ajuste dinâmico

Cada GPU mantém sua própria baseline, observações, decisões e experimentos. O supervisor exige uma janela inicial de pelo menos 300 segundos com cobertura real e atualizações independentes de hashrate. O usuário pode repetir o benchmark na placa selecionada, escolhendo desempenho ou eficiência; o pedido contém o UUID e não reinicia a medição das outras placas.

Um resultado insuficiente ou instável não valida um perfil. O controle usa limites e clocks suportados informados pelo driver daquela unidade, faz um ajuste pequeno por vez e observa o resultado antes de continuar. Uma capacidade ausente bloqueia o respectivo tipo de escrita. O catálogo e a IA não podem criar permissões que o hardware não informou.

Ollama local na CPU é opcional. A mesma integração pode atender aos consultores por GPU, mas há uso adicional de CPU/RAM; a telemetria e a proteção térmica continuam independentes da resposta do modelo. Veja [critérios e limites do agente](gpu-agent.md).

## Processos e isolamento

O gerenciador registra os processos que criou por UUID, com identidade de processo além do PID. Uma placa desabilitada não deve encerrar a mineração das demais. Um PID reutilizado por outro programa não pertence mais à sessão antiga.

O supervisor verifica a criação e o executável do minerador que acompanha. A parada normal é dirigida ao UUID e ao PID do supervisor, com timestamp recente, para ele sair e tentar restaurar os ajustes da própria GPU. Falha de controlador ou emergência deve parar somente o minerador daquela unidade. Não existe comando genérico de encerrar todos os processos `SRBMiner` da máquina.

Processos externos ou sessões antigas não são assumidos silenciosamente. A migração do fluxo legado de uma RTX 3070 exige ação própria e verificação de identidade; os scripts históricos permanecem como referência, sem se tornarem presets universais.

## Arquivos e histórico

`STATE_DIR` é `%LOCALAPPDATA%\PearlMonitor`. `LOG_DIR` é a raiz escolhida para logs; o padrão Windows continua sob `%LOCALAPPDATA%\Mining\Pearl\logs`.

| Caminho | Conteúdo |
| --- | --- |
| `STATE_DIR/settings.json` | Carteira global, contribuição opcional e `gpu_settings` por UUID |
| `STATE_DIR/rig-manager.json` | Estado do gerenciador e registro dos dispositivos/processos |
| `STATE_DIR/rig-requests/` | Pedidos estruturados de iniciar, parar, reconciliar ou medir |
| `STATE_DIR/history.sqlite3` | Arquivo permanente; `gpu_devices` e `gpu_telemetry` separam identidade e medições por UUID |
| `LOG_DIR/gpus/<UUID>/mining-*.log` | Logs da instância de mineração daquela GPU |
| `LOG_DIR/gpus/<UUID>/latest.json` | Snapshot individual de sensores e minerador |
| `LOG_DIR/gpus/<UUID>/gpu-agent.sqlite3` | Benchmarks, observações, decisões e experimentos daquela GPU |
| `LOG_DIR/gpus/<UUID>/gpu-agent-control.json` | Estado do supervisor e erros de restauração |
| `LOG_DIR/gpus/<UUID>/gpu-agent-request.json` | Pedido recente de benchmark para o mesmo UUID |
| `LOG_DIR/gpus/<UUID>/gpu-agent-stop.json` | Pedido de encerramento dirigido ao supervisor daquela GPU |
| `LOG_DIR/gpus/<UUID>/support-mining*.json` | Estado, ledger e posse do proxy opcional daquela GPU |

As tabelas anteriores, os logs antigos e o histórico de uma GPU continuam preservados. A nova organização não deve apagar ou atribuir automaticamente dados de uma unidade a outra. Copiar configurações para outro computador exige revisar os UUIDs; uma posição `GPU0` não identifica a mesma placa em máquinas diferentes.

Para backup, consulte [portabilidade](../PORTABILIDADE.md). A publicação do snapshot do autor não autoriza publicar automaticamente históricos, caminhos, seriais ou dados financeiros de quem instala.

## Limitações da evidência

A evidência física disponível neste ambiente é de uma RTX 3070. Os testes multiGPU cobrem seis identidades simuladas, modelos iguais/diferentes, carteiras compartilhadas/distintas e isolamento de processos, telemetria e benchmark. Não são apresentados como teste de seis placas físicas, desempenho agregado real ou lucro garantido. A [observação histórica de desempenho](performance/rtx3070.md) e a mídia da v0.1.0 continuam documentando uma única unidade.
