# Perguntas frequentes

## A carteira inicial é minha?

Não. O endereço pré-preenchido é o endereço público do autor. Mantê-lo direciona os pagamentos para o autor. Para receber você mesmo, informe sua carteira pública Pearl no assistente e confira o endereço completo exibido em **Visão geral**. O aplicativo não solicita seed ou chave privada.

## Instalar o HashPilot já começa a minerar?

Não. A instalação prepara o painel e abre a configuração. O minerador é uma opção separada, com destino de recebimento e hardware identificados. Veja [instalação](installation.md).

## Qual GPU é suportada?

A telemetria usa NVIDIA `nvidia-smi`. A RTX 3070 no Windows é a referência de mineração validada neste repositório. Outros modelos exigem avaliação e medição no próprio hardware. Presença em catálogo não certifica compatibilidade do minerador, estabilidade ou overclock.

## Funciona no MacBook?

O painel pode exibir pool, cotações e histórico no macOS com Python/Tkinter. GPU Apple não disponibiliza esses sensores NVIDIA, e os scripts Windows não configuram mineração em Apple Silicon. [Portabilidade](../PORTABILIDADE.md).

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

O monitor e seus cálculos não precisam de um modelo de IA. O assistente opcional usa Ollama na CPU, sem API paga. A política adaptativa continua disponível sem o modelo. A IA propõe ações limitadas; não certifica estabilidade de qualquer GPU. Veja [agente NVIDIA](gpu-agent.md).

## O aplicativo acompanha todas as GPUs ao mesmo tempo?

Não. O painel e o iniciador padrão usam GPU0. O motor identifica placas por UUID e separa suas medições, mas não se deve atribuir o hashrate agregado de um minerador a outra GPU. O catálogo de 158 modelos documenta especificações e fontes; não representa 158 overclocks testados.

## Posso publicar meu backup?

O backup incluído pertence ao autor e foi autorizado para publicação. Um novo backup pode conter endereços públicos, histórico financeiro, configurações e caminhos locais de quem o gerou. Revise seu conteúdo antes de adicioná-lo a uma contribuição. Nunca inclua seed, chave privada ou credenciais. [Segurança](../SECURITY.md).

## Qual é a licença?

O código usa [GPL-3.0-only](../LICENSE). A marca e os logos Tech Fernandes não são licenciados sob GPL. Consulte [NOTICE](../NOTICE) e a [origem dos ativos](../assets/brand/ORIGEM.md).
