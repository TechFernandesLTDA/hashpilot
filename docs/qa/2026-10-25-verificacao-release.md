# Verificação da distribuição — v0.3.2

Base de implementação: `51fce0e57c1f3d735de641cc994d313ed821528c`. O manifesto da release identifica o commit final que inclui este relatório e as ferramentas. Revisão interna do mantenedor, não auditoria independente.

## Verificações executadas

- Suíte anterior: 229 testes; foram acrescentados 13 testes de distribuição, diagnóstico e avaliação de observações.
- Fixture Git com arquivo local secreto e código não commitado: ambos excluídos do pacote; os blobs do commit foram preservados.
- Duas construções do mesmo commit em diretórios diferentes: ZIPs com SHA-256 idêntico.
- Pacote válido aceito pelo verificador Python e pela função exata do instalador PowerShell, extraída por AST sem executar a instalação.
- ZIP adulterado rejeitado; membro alterado com checksum externo recalculado rejeitado pelo manifesto; caminho `../escape` rejeitado antes da extração.
- Diagnóstico descarta carteira, UUID, chave fictícia, caminho e log; texto injetado em modelo/driver e números inválidos não são exportados.
- Dados ausentes/corrompidos produzem estado genérico, sem conteúdo ou caminhos da exceção.
- Sessão curta, hibernação/lacuna, hashrate antigo, temperatura elevada e erro de hardware não produzem conclusão saudável de 24 horas. Consultas repetidas não multiplicam medições independentes de hashrate.
- Captura local real: uma RTX 3070 minerando, snapshot com aproximadamente 1,2 s de idade no momento da leitura; a amostra isolada foi corretamente classificada como `incomplete`.
- Análise sintática PowerShell e verificação local de links/documentos/segredos.

As fixtures e dados privados de teste ficam fora do pacote e do Git. Os testes não iniciam mineração, não alteram clocks e não enviam chaves a provedores.

## Ainda pendente

- Resultado concluído de 24/72 horas, com revisão dos contadores e intervenções.
- Instalação em Windows limpo e testes físicos de outras placas/rigs multiGPU por voluntários.
- Assinatura Authenticode e revisão independente de código/rede.

O protocolo, as ferramentas e o formulário público permitem produzir essas evidências sem antecipar resultados. Consulte [verificação e limites](../verification.md).
