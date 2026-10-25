# Auditoria de bugs da equipe — 25/10/2026

Revisamos o código da GPU, do histórico financeiro e da instalação a partir do commit `26e5b393ed3cec6fa399d84a97afdcab273e6646`. Três defeitos foram confirmados e corrigidos nesta árvore de trabalho:

Fluxo de revisão assistida por IA: três GPT-6 Luna investigaram áreas separadas; GPT-6 Sol consolidou e reproduziu os achados; GPT-6 Astra revisou o plano; o Sol implementou os ajustes e as regressões. A revisão final do mantenedor verificou a integração, inclusive o encerramento do worker após incompatibilidade de esquema. Essa divisão de trabalho não equivale a uma auditoria independente.

1. Um encerramento abrupto deixava o arquivo de posse NVIDIA e bloqueava novas sessões. O controle agora mantém uma trava exclusiva do sistema operacional, separada dos metadados de compatibilidade. Estado legado ambíguo continua bloqueado.
2. A chave do histórico por GPU não incluía carteira: amostras com mesmo UUID e horário podiam ser descartadas, e o gráfico misturava carteiras. O esquema migra em transação para chave por GPU, carteira e horário; as consultas e o gráfico recebem a carteira do dispositivo selecionado.
3. O instalador reutilizava qualquer pasta de versão existente, mesmo incompleta. Ele verifica o conteúdo contra a origem, prepara uma cópia íntegra no mesmo volume e, se a pasta antiga estiver inválida, ativa um caminho novo sem alterar o anterior.

A migração preserva as linhas existentes; ela não recupera amostras já descartadas pelo esquema antigo. Bancos com versão futura desconhecida são recusados, com erro visível e encerramento do worker sem novas escritas. O verificador de instalação também recusa arquivos de código extras fora do inventário esperado, preservando a árvore antiga.

As verificações locais usaram apenas bancos SQLite e diretórios temporários, mocks e subprocessos de fixture. Passaram os testes direcionados e a suíte completa de **255 testes** com `mise exec -- python -m unittest discover -q`. A suíte inclui recusa diagnosticada de banco com esquema futuro e encerramento do worker sem novas escritas. O instalador foi exercitado por extração de funções e bloco de instalação via AST PowerShell, sem executar bootstrap, rede, atalhos ou inicialização do aplicativo. A sintaxe PowerShell também foi analisada pelo parser.

Não executamos comandos NVIDIA, mineração, instalador real, migração de banco do usuário ou atualização de release. A recuperação de um controlador legado depende de prova de encerramento; quando o sistema não consegue provar, a exclusividade prevalece. Instalação real, GPU e migração de dados reais seguem sem validação nesta auditoria. Este relatório não é certificação externa.
