# Parar mineração e devolver a GPU ao padrão

Na v0.5.0, **Parar** aparece no cabeçalho. A janela oferece **Parar TODAS e restaurar** e **Parar selecionadas e restaurar**, para até seis GPUs NVIDIA, iguais ou diferentes. A aba GPU e mineração tem **Parar esta GPU**, os cartões têm **Parar / Iniciar** e o menu da bandeja oferece **Parar toda mineração e restaurar padrões**.

As escolhas de parada são salvas antes de encerrar os processos. A GPU fica desabilitada para mineração até o usuário voltar a iniciá-la pelo cartão. Não há reinício automático dessa placa pelo controlador ou pela recuperação. As outras GPUs continuam com suas próprias carteiras e mineradores.

## O que é restaurado

Após confirmar o encerramento do minerador e do controlador daquela placa, o serviço adquire a mesma trava exclusiva usada pelo controlador e utiliza a NVML instalada pelo driver, selecionando o dispositivo pelo UUID:

- devolve todas as ventoinhas à política automática do driver, com leitura da política para confirmação;
- zera os offsets de núcleo e memória em todos os P-states suportados e confere os valores;
- libera os clocks travados de núcleo e memória;
- consulta e aplica o limite de potência de fábrica, com confirmação do valor atual.

O valor padrão vem do driver de cada unidade. Não usa 240 W como padrão universal e não impõe fan em 0%: automático pode resultar em 0% numa GPU fria ou em rotação maior conforme a temperatura. São utilizadas as funções de [controle da NVML](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlDeviceCommands.html) e as estruturas publicadas no [header oficial NVIDIA](https://github.com/NVIDIA/go-nvml/blob/main/pkg/nvml/nvml.h).

Uma restauração parcial permanece visível como incompleta, com os componentes que falharam. Não diz “restaurado” apenas porque o minerador encerrou ou a ventoinha ficou silenciosa. Cada etapa é tentada mesmo se outra falhar. Campos/API sem suporte no driver aparecem como falha/incompatibilidade; o catálogo não garante que todos os comandos funcionem em todos os modelos ou drivers.

## Permissão do Windows

Com o serviço atualizado e autorizado já ativo, a parada e a restauração ocorrem nele. Com o serviço parado ou antigo, a interface salva a desativação, solicita a parada que for possível e abre um helper específico de restauração com UAC. O administrador precisa aprovar no Windows. O helper não inicia mineradores nem se conecta a uma pool.

Se o UAC for cancelado, a interface informa a falha e permite tentar novamente. Cancelar não significa que os padrões foram restaurados. Cliques repetidos enquanto uma solicitação aguarda aprovação não devem abrir outra solicitação para a mesma GPU durante a janela de espera.

Processos externos ou cuja propriedade não pode ser confirmada são preservados, com erro explícito. Um PID reutilizado não autoriza encerrar outro programa. Não utiliza reinicialização do driver, reset de dispositivo, comandos globais de overclock ou um minerador temporário para restaurar as placas.

## Histórico e retomada

Carteiras, histórico de ganhos, benchmarks e preferências permanecem armazenados. O resultado por GPU fica no arquivo `gpu-defaults.json` do respectivo diretório. Começar outra sessão invalida a confirmação anterior, para que ela não seja apresentada como se ainda descrevesse a nova mineração.

Para retomar, use **Iniciar** no cartão da GPU desejada. O comando do cabeçalho inicia/reconcilia o serviço conforme as GPUs habilitadas; não reativa silenciosamente as que foram paradas.

O atalho `PARAR-PEARL.cmd` também solicita o novo fluxo de parada. O instalador inclui a lista completa de módulos do serviço em `data/rig-runtime.json`, cuja importação é testada em um diretório isolado do clone.
