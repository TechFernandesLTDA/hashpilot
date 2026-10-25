# HashPilot — Tech Fernandes

[English](README.en.md) · [Documentação](docs/index.md) · [Instalação](docs/installation.md) · [Para LLMs](llms.txt)

![Tech Fernandes](assets/brand/tech-fernandes_github_banner-readme_escuro_1280x400.png)

Monitor local de mineração Pearl (PRL): telemetria NVIDIA, atividade do minerador, histórico permanente e rendimentos equivalentes em **PRL, BRL, USD e BTC**. Interface desktop Python/Tkinter, sem servidor web ou dependências Python adicionais para o monitor.

## Instalar no Windows

No PowerShell do **Windows 10/11 x64**, com `winget` disponível:

```powershell
irm https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/main/install.ps1 | iex
```

O instalador prepara o mise/Python, instala o aplicativo em `%LOCALAPPDATA%\Programs\HashPilot`, cria atalhos e abre o assistente de configuração. Configurações e histórico existentes são preservados. [Passos, requisitos e solução de problemas](docs/installation.md).

**Carteira padrão: a do autor.** Se você mantiver esse endereço, os pagamentos irão para o autor. Troque pela sua carteira pública no assistente antes de minerar; a configuração pode ser reaberta. O monitor não precisa de seed ou chave privada. A instalação do minerador é uma opção separada: não começa apenas por instalar o painel.

## O que acompanha

| Aba | Informações |
|---|---|
| **Visão geral** | Hashrate, temperatura, consumo, créditos observados, carteira e próximo pagamento |
| **GPU e mineração** | Sensores NVIDIA, clocks, ventoinhas, controle térmico, gráficos e eventos |
| **Rendimentos** | Períodos observados, saldo da pool, valores em PRL/BRL/USD/BTC e projeções |

- Histórico SQLite preservado após reinícios; sem expurgo automático.
- Bandeja do Windows, resumo compacto e console textual.
- Coleta local a cada 2 s; pool e cotações públicas a cada 60 s, com idade/cache visíveis.
- Projeções são estimativas. Valores em USD/BTC são equivalentes; **o pagamento continua em PRL**.

## Hardware e plataformas

O monitor de hardware usa **NVIDIA `nvidia-smi`**. A **RTX 3070 no Windows** é o perfil de mineração validado de referência deste projeto. Os resultados dessa unidade não certificam outras placas ou ambientes.

O [catálogo NVIDIA](docs/gpu-catalog.md) reúne **158 modelos com fontes primárias**. Isso valida a origem dos dados, não o overclock de cada placa. O [agente local](docs/gpu-agent.md) identifica GPUs pelo driver/UUID, mede uma baseline de 300 s e permite benchmark manual e controle adaptativo explícito. Ollama na CPU é opcional, sem API paga.

**O painel e o iniciador padrão acompanham GPU0.** O motor separa perfis por UUID, mas isso não torna a interface um painel mult GPU: não atribua hashrate agregado a outra placa. O iniciador NVIDIA usa os ajustes atuais do driver, sem copiar clocks da RTX 3070; o perfil histórico continua separado como referência.

No macOS, o painel pode acompanhar pool, cotações e histórico. GPU Apple não recebe telemetria NVIDIA, e os scripts de mineração/clock Windows não configuram mineração em Apple Silicon. [Portabilidade e snapshot](PORTABILIDADE.md).

## Código e dados

```text
mise install
mise exec -- python monitor.py
mise exec -- python -m unittest discover -v
```

O snapshot em `portable-data/` pertence ao autor e foi incluído com autorização, assim como seu endereço público. Ele é uma fotografia datada, não o rendimento do seu computador. A restauração inicial não substitui um banco local existente. [Arquitetura](docs/architecture.md) · [Referência técnica](docs/reference.md).

Dados locais Windows: `%LOCALAPPDATA%\PearlMonitor`. macOS: `~/Library/Application Support/HashPilot`.

## Participar

[Contribuir](CONTRIBUTING.md) · [Reportar bug](https://github.com/TechFernandesLTDA/hashpilot/issues/new?template=bug_report.yml) · [Pedir recurso](https://github.com/TechFernandesLTDA/hashpilot/issues/new?template=feature_request.yml) · [Segurança](SECURITY.md)

O código está sob **GPL-3.0-only**: [LICENSE](LICENSE). A marca e os logos Tech Fernandes não são licenciados sob GPL; veja [NOTICE](NOTICE) e a [origem dos ativos](assets/brand/ORIGEM.md).
