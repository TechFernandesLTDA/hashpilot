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
- A coleta do painel é distinta das ações de instalação, mineração e controle de clocks. O perfil RTX 3070 validado não certifica outros dispositivos.
- O agente de controle exige ativação explícita e posse exclusiva da GPU; não contorne a detecção de supervisor concorrente. Ollama local é opcional e suas propostas continuam sujeitas aos limites do motor.
- O painel usa GPU0; catálogo e perfis por UUID não comprovam mineração estável ou coleta mult GPU.

Não envie arquivos internos de carteiras, senhas, tokens ou chaves privadas em issues, pull requests ou exemplos. O código e os testes comuns devem continuar utilizáveis sem esses segredos.

## Correções

Inclua regressão reproduzível e validação da correção. Quando uma mudança afetar configuração, persistência, downloads ou controle real do hardware, documente quais partes foram efetivamente testadas. Uma simulação ou entrada de catálogo não deve ser apresentada como teste de hardware real.
