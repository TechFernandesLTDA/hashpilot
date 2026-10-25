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

Use **Configurar** para alterar depois. A mudança reinicia apenas o painel, preserva o banco e separa os dados por carteira. Se um minerador já estiver ativo, ele continua com a carteira com que foi iniciado; pare e reinicie esse minerador para mudar o destino das recompensas. Não há redirecionamento silencioso de um processo existente.

## Minerador e benchmark NVIDIA

Marque a opção de instalar e iniciar o minerador para baixar o SRBMiner oficial. O ZIP é conferido contra o SHA256 registrado em [`downloads.json`](../mining/windows/downloads.json). O perfil medido de referência é uma RTX 3070 desktop; consulte o [agente GPU](gpu-agent.md) para os limites de controle e benchmark de outras GPUs NVIDIA. Estar no [catálogo](gpu-catalog.md) não significa ter overclock validado.

O painel **Benchmark / IA** inicia a observação de cinco minutos automaticamente para uma GPU sem benchmark anterior e permite repetir a medição com foco em desempenho ou eficiência. O histórico identifica a GPU pelo UUID. Hashrate ausente, antigo ou medições instáveis impedem validar resultados. O painel observa; somente o supervisor exclusivo do minerador pode aplicar ajustes.

## IA gratuita e local

A opção de IA instala Ollama pelo winget e baixa `qwen2.5:0.5b`. Isso exige download adicional, espaço em disco e memória RAM. O HashPilot solicita inferência na **CPU** (`num_gpu=0`) para evitar disputar a GPU com a mineração. Não é necessário token ou assinatura. O modelo só sugere ações enumeradas; o código valida os limites antes de qualquer ajuste. Sem Ollama disponível, o estado informa a indisponibilidade e usa a política adaptativa.

## Atualização e histórico

Versões atuais aceitam uma solicitação de fechamento normal enviada pelo instalador, salvam o histórico e permitem abrir a nova versão automaticamente. Se uma versão antiga não responder, o instalador informa que ela precisa ser fechada pelo menu **Sair**; ele não encerra a mineração à força.

Execute o mesmo comando para instalar a revisão atual. Versões anteriores são mantidas e os atalhos passam a apontar para a nova versão. O assistente reabre; feche a versão antiga pelo menu **Sair** antes de abrir a nova quando houver atualização de código.

Dados permanentes ficam separados da instalação em `%LOCALAPPDATA%\PearlMonitor`: configurações, `history.sqlite3`, `gpu-agent.sqlite3` e caches. Atualizações não removem esses arquivos. No primeiro perfil vazio, o snapshot público do autor pode ser importado; ele é identificado por carteira. Consulte [portabilidade](../PORTABILIDADE.md).

## Problemas comuns

| Sintoma | Ação |
| --- | --- |
| `winget` não existe | Instale App Installer pela Microsoft Store e reabra o PowerShell. |
| Download bloqueado | Confira proxy, internet, GitHub e a mensagem do Windows. O instalador não desativa antivírus. |
| Sensores indisponíveis | Instale o driver NVIDIA adequado e confira `nvidia-smi`. |
| Sem hashrate no benchmark | Confira a pasta dos logs e aguarde amostras reais do SRBMiner. |
| IA indisponível | Aguarde o download do modelo e confirme Ollama em `127.0.0.1:11434`. |
| Configuração inválida | Revise o JSON em `%LOCALAPPDATA%\PearlMonitor\settings.json`. |

Para executar de um clone: `mise install`, depois `mise exec -- python monitor.py --setup`. macOS usa `zsh Abrir-HashPilot.command` para monitoramento; o instalador e o controle NVIDIA descritos aqui são para Windows.

Referências do instalador: [instalação oficial do mise](https://mise.jdx.dev/getting-started.html), [arquivos de repositório e ZIPs no GitHub](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip).
