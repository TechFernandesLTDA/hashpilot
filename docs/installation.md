# Instalação do HashPilot no Windows

Windows 10/11 de 64 bits, conexão com a internet e driver NVIDIA. Abra o PowerShell e execute:

```powershell
irm https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/main/install.ps1 | iex
```

O comando baixa e executa o instalador público deste repositório. Você pode [ler o script](../install.ps1) antes de executar. Não é necessário clonar o repositório, instalar Git ou configurar Python manualmente. Se o Windows não tiver `winget`, instale o **App Installer**, da Microsoft Store, e repita o comando.

## O que é instalado

1. O instalador reutiliza o mise existente ou instala `jdx.mise` pelo winget. O Python 3.14.9 é gerenciado pelo mise, sem alterar seus perfis de shell ou a versão global do Python.
2. Resolve o commit atual de `main` e baixa o ZIP desse commit do GitHub por HTTPS. A revisão fica registrada em `installation.json`.
3. Instala o aplicativo em `%LOCALAPPDATA%\Programs\HashPilot\versions\<commit>` e cria atalhos no menu Iniciar e na Área de Trabalho.
4. Abre o assistente para conferir a carteira, a pasta dos logs, a instalação opcional do minerador e a IA local.

A aplicação usa a biblioteca padrão do Python e Tkinter. O instalador não modifica a política global de execução do PowerShell. A etapa de controle do minerador pode solicitar elevação pelo Windows para comandos NVIDIA.

## Carteira e primeira abertura

O endereço público do autor vem preenchido por padrão, por escolha explícita do mantenedor:

```text
prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t
```

**Se mantiver esse endereço, a mineração iniciada pelo assistente usará a carteira do autor.** Substitua pelo seu endereço público para acompanhar e receber na sua própria carteira. O aplicativo nunca solicita seed ou chave privada.

Use **Configurar** para alterar depois. A mudança preserva o banco e separa os dados por carteira. Um minerador externo ou legado continua com a carteira com que foi iniciado; ele precisa de uma parada e novo início próprios para mudar o destino. Nas instâncias que o serviço do rig gerencia, a reconciliação da carteira efetiva ocorre somente na GPU correspondente. Não há apropriação silenciosa de um processo externo.

Em rigs, até seis GPUs podem herdar essa carteira global ou usar um endereço próprio por UUID. Confira a carteira efetiva de cada placa antes de iniciar. O serviço do rig reconcilia mudanças somente nas instâncias que gerencia; um minerador externo não é assumido pela mudança de configuração. [Configuração multiGPU](multi-gpu.md).

Ao selecionar outra carteira, o assistente oferece **ajudar a manter a ferramenta com 5% do tempo de mineração**. A sugestão aparece marcada quando ainda não há uma escolha salva; desmarque para desativar. O destino é a carteira padrão do autor, exibida no assistente. A divisão usa 57 minutos para a carteira principal e 3 para o autor por hora conectada, sem reiniciar o processo do minerador. São 5% aproximados de tempo, não uma cobrança em dinheiro nem garantia de exatamente 5% das moedas. Configurações antigas sem escolha salva continuam sem contribuição. [Detalhes e desativação](contribution.md).

## Minerador e benchmark NVIDIA

Marque a opção de instalar e iniciar o minerador para baixar o SRBMiner oficial. O ZIP é conferido contra o SHA256 registrado em [`downloads.json`](../mining/windows/downloads.json). O perfil medido de referência é uma RTX 3070 desktop; consulte o [agente GPU](gpu-agent.md) para os limites de controle e benchmark de outras GPUs NVIDIA. Estar no [catálogo](gpu-catalog.md) não significa ter overclock validado.

O painel **Benchmark / IA** inicia a observação de cinco minutos automaticamente para uma GPU sem benchmark anterior e permite repetir a medição com foco em desempenho ou eficiência. O histórico identifica a GPU pelo UUID. Hashrate ausente, antigo ou medições instáveis impedem validar resultados. O painel observa; somente o supervisor exclusivo do minerador pode aplicar ajustes.

A interface detecta até seis GPUs NVIDIA iguais ou diferentes e mantém habilitação, carteira e benchmark por unidade. GPUs novas ficam habilitadas na configuração, mas **abrir a interface não inicia mineração**. Inicie explicitamente o serviço de mineração após conferir a seleção. Com o serviço autorizado em execução, desabilitar uma GPU encerra somente seus próprios processos; as demais continuam. Dispositivos além de seis são informados e ignorados. Os ajustes começam pelas condições atuais do driver, sem copiar o perfil histórico da RTX 3070.

## IA gratuita e local

A IA pode usar OpenRouter Cloud Free, OpenCode Cloud Free pelo cliente ou Ollama local. Escolha o provedor no assistente; somente a opção local baixa Ollama/modelo. Veja [configuração e condições](ai-providers.md).

## Atualização e histórico

Versões atuais aceitam uma solicitação de fechamento normal enviada pelo instalador, salvam o histórico e permitem abrir a nova versão automaticamente. Se uma versão antiga não responder, o instalador informa que ela precisa ser fechada pelo menu **Sair**; ele não encerra a mineração à força.

Execute o mesmo comando para instalar a revisão atual. Versões anteriores são mantidas e os atalhos passam a apontar para a nova versão. O assistente reabre; feche a versão antiga pelo menu **Sair** antes de abrir a nova quando houver atualização de código.

Dados permanentes ficam separados da instalação em `%LOCALAPPDATA%\PearlMonitor`: configurações, `history.sqlite3`, estado do gerenciador e caches. Logs e bancos `gpu-agent.sqlite3` individuais ficam sob `LOG_DIR/gpus/<UUID>/`; bancos anteriores permanecem preservados. Atualizações não removem esses arquivos. No primeiro perfil vazio, o snapshot público do autor pode ser importado; ele é identificado por carteira. Consulte [portabilidade](../PORTABILIDADE.md).

## Problemas comuns

| Sintoma | Ação |
| --- | --- |
| `winget` não existe | Instale App Installer pela Microsoft Store e reabra o PowerShell. |
| Download bloqueado | Confira proxy, internet, GitHub e a mensagem do Windows. O instalador não desativa antivírus. |
| Sensores indisponíveis | Instale o driver NVIDIA adequado e confira `nvidia-smi`. |
| Sem hashrate no benchmark | Confira a pasta dos logs e aguarde amostras reais do SRBMiner. |
| IA indisponível | Aguarde o download do modelo e confirme Ollama em `127.0.0.1:11434`. |
| Configuração inválida | Revise o JSON em `%LOCALAPPDATA%\PearlMonitor\settings.json`. |

Para executar de um clone no Windows: `mise install`, depois `mise exec -- python monitor.py --setup`. O aplicativo está disponível somente para Windows 10/11 de 64 bits.

Referências do instalador: [instalação oficial do mise](https://mise.jdx.dev/getting-started.html), [arquivos de repositório e ZIPs no GitHub](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip).

AI providers / Provedores de IA: [OpenRouter Cloud Free, OpenCode Cloud Free e Ollama local](ai-providers.md). A política adaptativa funciona sem IA.
