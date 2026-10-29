# Perguntas frequentes

## A carteira inicial é minha?

Não. O endereço pré-preenchido é o endereço público do autor. Mantê-lo direciona os pagamentos para o autor. Para receber você mesmo, informe sua carteira pública Pearl no assistente e confira o endereço completo exibido em **Visão geral**. O aplicativo não solicita seed ou chave privada.

## Instalar o HashPilot já começa a minerar?

Não. A instalação prepara o painel e abre a configuração. O minerador é uma opção separada, com destino de recebimento e hardware identificados. Veja [instalação](installation.md).

## Qual GPU é suportada?

A telemetria usa NVIDIA `nvidia-smi`. A RTX 3070 no Windows é a referência de mineração validada neste repositório. Outros modelos exigem avaliação e medição no próprio hardware. Presença em catálogo não certifica compatibilidade do minerador, estabilidade ou overclock.

## Qual sistema é suportado?

O HashPilot é exclusivo para Windows 10/11 x64. Para copiar dados entre computadores Windows, consulte [backup e transferência](../PORTABILIDADE.md).

## Há contribuição para o autor?

Há uma opção visível e desativável no assistente: com outra carteira, reserva 5% do tempo de mineração, em ciclos de 57 minutos para você e 3 para o autor. O proxy TCP alterna o destino sem reiniciar GPU ou minerador. Usar a carteira padrão do autor não adiciona outra divisão. A devfee de 2% do SRBMiner é separada. Projeções consideram a contribuição ativa; créditos reais não sofrem desconto duplicado. [Detalhes](contribution.md).

## Quando recebo PRL?

O saldo precisa atingir o mínimo **confirmado** da carteira na pool. O HashPilot separa total, confirmado e pendente, usa o limiar informado pela API e mostra uma faixa estimada. Maturação típica não é um cronômetro individual. Envio registrado pela pool também não prova que a carteira já sincronizou. [Pagamentos](../PAGAMENTOS.md).

## R$, US$ e BTC são enviados para minha carteira?

Não. São equivalentes às cotações atuais. O pagamento permanece em PRL, sem troca automática executada pelo monitor.

## Por que meu dia aparece como parcial ou N/D?

O aplicativo não possui observações suficientes para todo o período. Ele não distribui o saldo antigo por datas nem converte uma projeção em crédito realizado. N/D significa informação indisponível; não significa saldo zero.

## O histórico some ao fechar ou atualizar?

O banco permanente fica fora da pasta do aplicativo e não tem expurgo automático. Reinícios restauram séries e preferências. A instalação preserva os dados existentes; o snapshot incluído só é restaurado quando o destino não existe. [Arquitetura e caminhos](architecture.md#persistência).

## Os números do snapshot são do meu computador?

Não. `portable-data/` contém uma fotografia autorizada do histórico do autor. Confira a data em `backup-manifest.json`. As observações atuais são identificadas pela carteira, sessão e timestamps das fontes.

## O X encerra o minerador?

No Windows, o X recolhe o painel para a bandeja e a coleta continua. **Sair** encerra o monitor e grava o histórico, sem encerrar o minerador. Controle do processo de mineração é uma ação própria dos scripts/opções correspondentes.

## A interface fica borrada ou não maximiza?

O Windows usa PerMonitorV2 quando disponível, maximização nativa na área útil e PNGs oficiais em tamanhos nativos. Inclua resolução, DPI, tamanho do cliente e a seção `layout` do diagnóstico ao [relatar um bug](../CONTRIBUTING.md#relatar-um-problema). Não confunda um teste 4K virtual com validação em monitor físico 4K.

## A IA é obrigatória? Precisa de API paga?

O monitor, benchmark e controle adaptativo funcionam sem IA. Para CPU simples, há OpenRouter Cloud Free e OpenCode Cloud Free pelo cliente; Ollama local permanece opcional. Veja [provedores e cotas](ai-providers.md).

## O aplicativo acompanha todas as GPUs ao mesmo tempo?

O painel acompanha até seis GPUs NVIDIA, iguais ou diferentes, por UUID. Cada placa tem seus próprios sensores, log, minerador, supervisor e benchmark. Placas novas ficam habilitadas na configuração, mas abrir o painel não inicia mineração: o serviço exige início explícito. Dispositivos acima de seis são informados e ignorados. [Operação multiGPU](multi-gpu.md).

## Posso desabilitar uma placa ou usar carteiras diferentes?

Sim. A habilitação fica salva por UUID. Com o serviço de mineração autorizado em execução, desabilitar uma GPU encerra somente seus processos; as demais continuam. Cada placa herda a carteira global ou usa um endereço próprio. Duas placas iguais continuam sendo identificadas separadamente.

## Se várias placas usam minha carteira, o saldo será multiplicado?

Não. Consultas e saldos são deduplicados por endereço efetivo. Hashrate e potência são individuais e podem ser agregados, mas o aplicativo não distribui créditos reais por placa com base na proporção de hashrate. Uma consulta ausente fica indisponível/parcial; não vira zero.

## O suporte a seis GPUs foi validado em seis placas físicas?

Não neste computador. A evidência física disponível é de uma RTX 3070. Os cenários com seis GPUs usam testes simulados de identidade, processos, dados, carteira e benchmark. O catálogo de 158 modelos documenta fontes primárias; não representa 158 overclocks testados. Vídeo, capturas e observação de desempenho da v0.1.0 registram uma única placa.

## Posso publicar meu backup?

O backup incluído pertence ao autor e foi autorizado para publicação. Um novo backup pode conter endereços públicos, histórico financeiro, configurações e caminhos locais de quem o gerou. Revise seu conteúdo antes de adicioná-lo a uma contribuição. Nunca inclua seed, chave privada ou credenciais. [Segurança](../SECURITY.md).

## Qual é a licença?

O código usa [GPL-3.0-only](../LICENSE). A marca e os logos Tech Fernandes não são licenciados sob GPL. Consulte [NOTICE](../NOTICE) e a [origem dos ativos](../assets/brand/ORIGEM.md).


## O minerador parou; o computador hibernou?

Uma parada de mineração não prova suspensão. Cruze o horário dos logs com a continuidade de `history.sqlite3` e os eventos de energia do Windows. O desligamento da tela também não indica hibernação.

Se o controlador registrar `WinError 5` ao substituir um JSON de status, existe uma falha de acesso ao arquivo. Um bloqueio temporário de leitura ou de outro software pode impedir a substituição; o erro sozinho não identifica qual processo o causou. A gravação atômica tenta novamente por até um segundo, mantendo o JSON anterior completo. Um bloqueio persistente continua sendo reportado como erro, sem desativar as proteções do controlador.

O painel ignora `reset.log` ao escolher o log legado de mineração: esse arquivo pertence à restauração da GPU e pode usar uma conexão local propositalmente inválida. Sua mensagem de reconexão não descreve a saúde da conexão real com a pool.

AI providers / Provedores de IA: [OpenRouter Cloud Free, OpenCode Cloud Free e Ollama local](ai-providers.md). A política adaptativa funciona sem IA.
