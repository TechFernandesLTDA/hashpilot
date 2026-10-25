# Documentação do HashPilot

[Português](../README.md) · [English](../README.en.md) · [Mapa para LLMs](../llms.txt)

## Começar

1. [Instalar e configurar](installation.md): Windows, comando único, carteira e opção de mineração.
2. [Entender pagamentos](../PAGAMENTOS.md): mínimo, confirmação, saldo pendente e estimativa de envio.
3. [Perguntas frequentes](faq.md): carteira do autor, dados, hardware e diagnóstico.

## Entender e contribuir

| Documento | Uso |
|---|---|
| [Arquitetura](architecture.md) | Módulos, fluxo de dados e limites entre monitor e controle da GPU |
| [Agente NVIDIA](gpu-agent.md) | Baseline, benchmark manual, controle explícito e IA local opcional |
| [Catálogo NVIDIA](gpu-catalog.md) | 158 modelos documentados com fontes primárias; não são perfis de overclock validados |
| [Referência técnica](reference.md) | Conteúdo detalhado preservado da documentação anterior |
| [Controle térmico](../CONTROLE-TERMICO.md) | Política e supervisor do perfil Windows de referência |
| [Scripts de mineração](../mining/windows/README.md) | Instalador do minerador, perfil validado e scripts históricos |
| [Backup e transferência](../PORTABILIDADE.md) | Snapshot, fork e transferência entre computadores Windows |
| [Contribuição opcional](contribution.md) | Divisão de tempo de mineração, configuração e impacto nas projeções |
| [Contribuição](../CONTRIBUTING.md) | Ambiente, testes e revisão |
| [Segurança](../SECURITY.md) | Relato de vulnerabilidades e conteúdo sensível |
| [Origem da marca](../assets/brand/ORIGEM.md) | Logos, derivados e tokens oficiais |

## Fatos essenciais

- A carteira inicial é a do autor. Mantê-la direciona os pagamentos para ele; substitua-a no assistente se deseja receber em outro endereço.
- Pagamentos da pool são em Pearl (PRL). R$, US$ e BTC são equivalentes calculados.
- O snapshot incluído é histórico do autor, não o rendimento do computador de quem instala.
- Telemetria de GPU depende de NVIDIA `nvidia-smi`; o perfil validado de mineração é a RTX 3070 no Windows.
- O catálogo e o benchmark não devem ser interpretados como validação universal de overclock.
- A interface e o iniciador padrão usam GPU0; perfis por UUID no motor não significam telemetria mult GPU na interface.
- Código GPL-3.0-only; marca e logos separados conforme [NOTICE](../NOTICE).
- Produto exclusivo Windows 10/11 x64.
- Instalação ou simples monitoramento não são autorização implícita para alterar clocks de outra GPU.
