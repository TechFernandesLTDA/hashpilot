# Segurança

## Relatar uma vulnerabilidade

Envie uma descrição reproduzível para [contato@techfernandes.com.br](mailto:contato@techfernandes.com.br). Se o GitHub oferecer a opção de relato privado neste repositório, ela também pode ser usada. Não publique detalhes exploráveis ou credenciais em uma issue comum antes da análise.

Inclua a revisão afetada, plataforma, comportamento observado e impacto. O projeto não declara prazo de resposta ou SLA.

## Dados e limites

- O monitor usa endereços públicos e APIs públicas; não precisa de seed ou chave privada.
- A carteira pré-preenchida é a do autor, e mantê-la direciona pagamentos para ele. Confira o destinatário antes de iniciar mineração.
- O snapshot público incluído tem autorização do autor. Isso não autoriza publicar dados de outros usuários.
- Logs e diagnósticos podem conter carteira, caminhos locais e valores financeiros. Revise anexos antes de enviá-los.
- Os binários de minerador/carteira vêm de fontes de terceiros identificadas em `mining/windows/downloads.json`; o instalador confere o hash esperado.
- Uma detecção do Defender exige análise do arquivo; origem oficial não comprova segurança. O [assistente de permissão](docs/defender.md) só solicita exceção após consentimento e confirmação administrativa, limitada ao caminho do executável verificado. Não restaura quarentena nem desliga proteções globais. A exceção por caminho também afeta substituições futuras naquele caminho.
- A coleta do painel é distinta das ações de instalação, mineração e controle de clocks. O perfil RTX 3070 validado não certifica outros dispositivos.
- O agente de controle exige ativação explícita e posse exclusiva da GPU; não contorne a detecção de supervisor concorrente. Propostas de Ollama, OpenRouter ou OpenCode continuam sujeitas aos limites locais do motor.
- O painel acompanha até seis GPUs NVIDIA por UUID. A evidência física disponível cobre uma RTX 3070; testes multiGPU são simulados. Catálogo e perfis por UUID não comprovam estabilidade em hardware não testado.
- A contribuição opcional direciona aproximadamente 5% do tempo de mineração à carteira do autor quando habilitada. Pode ser desativada no assistente e é separada da taxa do minerador de terceiros.

## Distribuição e diagnóstico

As releases a partir de v0.3.2 incluem ZIP, manifesto por arquivo e `SHA256SUMS`. O instalador verifica o checksum, os caminhos do ZIP, a revisão e o inventário antes de usar o código baixado. Hashes publicados na mesma conta GitHub detectam divergências e corrupção; não são assinatura de publicador, auditoria independente nem defesa contra comprometimento dessa conta. O projeto ainda não declara distribuição Authenticode assinada.

O [guia de verificação](docs/verification.md) documenta reprodução do pacote, fronteiras de rede, diagnóstico por lista de campos permitidos e protocolo de estabilidade. O exportador novo não inclui configurações, bancos, logs brutos, carteiras, UUIDs, caminhos ou chaves. Isso não altera a sensibilidade dos arquivos originais: revise sempre qualquer anexo.

Não envie arquivos internos de carteiras, senhas, tokens ou chaves privadas em issues, pull requests ou exemplos. O código e os testes comuns devem continuar utilizáveis sem esses segredos.

## Correções

Inclua regressão reproduzível e validação da correção. Quando uma mudança afetar configuração, persistência, downloads ou controle real do hardware, documente quais partes foram efetivamente testadas. Uma simulação ou entrada de catálogo não deve ser apresentada como teste de hardware real.
