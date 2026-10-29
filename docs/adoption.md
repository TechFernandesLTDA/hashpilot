# Descoberta, adoção e divulgação responsável

O objetivo inicial é obter cinco experiências externas reais e identificar obstáculos de instalação e uso. Isso ainda é uma meta, não um número de usuários comprovado.

## Onde começar

- [Página pública em português e inglês](https://techfernandesltda.github.io/hashpilot/).
- [Discussions: dúvidas, ideias e experiências](https://github.com/TechFernandesLTDA/hashpilot/discussions).
- [Convite e roteiro para testes externos](external-testing.md).
- [Formulário de experiência real](https://github.com/TechFernandesLTDA/hashpilot/issues/new?template=external_test.yml).
- [Texto de divulgação pronto para revisão](launch-announcement.md).
- [Estado da análise do minerador](security-review.md).

## Medir interesse sem confundir com utilização

O coletor abaixo consulta somente dados agregados do GitHub usando a autenticação existente do `gh`. Não acessa o aplicativo dos usuários e não envia telemetria do computador.

```powershell
mise exec -- python tools/repository_metrics.py
```

Cada execução cria um arquivo com horário UTC em `dist/adoption-metrics/`, ignorado pelo Git. Registra estrelas, forks, downloads de assets das últimas 100 releases e as janelas de 14 dias de visualizações/clones, quando a conta tem permissão para consultá-las. Não cria tarefas agendadas, credenciais ou workflows.

**Não some janelas sobrepostas nem visitantes únicos de dias diferentes.** Clones e downloads podem vir de automações ou do próprio mantenedor; não comprovam instalação, mineração ativa ou retenção. A referência local consultada em 28/10/2026 foi de 21 visualizações e 4 visitantes únicos na janela de 14 dias, sem comprovação de usuários recorrentes.

Para avaliar adoção, peça relatos voluntários: a pessoa conseguiu instalar, configurou a própria carteira, entendeu a contribuição opcional, preservou o histórico e continuou usando após sete dias? Não exija endereço de carteira, ganhos, credenciais, logs brutos ou dados pessoais. Um relato de instalação não é prova de pagamento nem certificação de segurança.

## Publicar somente onde houver permissão

No repositório do próprio projeto, use Discussions para anunciar versões e solicitar experiências. Em fóruns e comunidades de terceiros, leia as regras e procure uma categoria explicitamente destinada a projetos/ferramentas; solicite revisão se houver dúvida. Um convite preparado não significa que foi publicado.

A Kryptex informou que não compartilha ferramentas ou links não testados/verificados. Essa recusa deve ser respeitada. A existência de testes internos, hashes, um site ou um pedido de análise não substitui uma revisão aceita pelos moderadores. Não reenvie propaganda nem declare endosso da pool.

Não compre estrelas, avaliações, clones ou relatos. Não use contas falsas, mensagens em massa, mineração escondida, promessa de lucro, ou teste externo fictício. Publicações devem identificar a autoria e explicar a carteira padrão do autor e a ajuda opcional.
