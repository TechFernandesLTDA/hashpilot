# Avaliação da contribuição de 5% — 25/10/2026

## Conclusão e escopo

O mecanismo anterior **não foi aprovado** nesta avaliação: duas falhas de tempo foram reproduzidas, e a v0.3.4 substitui o agendamento operacional por quota verificada e reserva antecipada. O resultado corrigido comprova roteamento e proteções nos cenários abaixo. **Pagamento monetário de 5% ainda não foi comprovado de ponta a ponta.** Esta é uma revisão interna assistida por IA, não auditoria externa ou garantia de ausência de falhas.

Não há neste mecanismo operação para sacar saldo já existente, seed/chave privada, assinatura de transação ou chamada de alteração de carteira de pagamento. A contribuição muda a autorização de **trabalho futuro**. A [Kryptex informa que não transfere moedas entre endereços e paga para o endereço em que a mineração ocorreu](https://pool.kryptex.com/en/articles/how-pool-works-en). Isso não protege um usuário que confirme a carteira inicial do autor como sua carteira principal: nessa configuração a mineração inteira já é destinada ao autor, independentemente da ajuda de 5%. Esse aviso deve permanecer visível no onboarding.

## Defeitos reproduzidos antes da correção

1. Conexão TCP sem autorização confirmada pela pool acumulava tempo de usuário. Em fixture de sockets locais com relógio injetado, 3400 s sem autorizar foram contados como mineração elegível.
2. Durante uma fase do autor, avançar o relógio injetado em 3600 s devolvia o mesmo resultado de módulo do ciclo. A conexão continuava ativa em vez de ser encerrada ao exceder sua janela. Isso podia distorcer a divisão de trabalho futuro; não foi observado saque de saldo de carteira.

Os dois testes falharam antes do patch. O relógio foi simulado; não se tratou de medir uma GPU durante uma hora nem de registrar desvio real de pagamentos de terceiros.

## Correção e evidências

- Tempo principal confirmado por resposta de autorização com ID correspondente; pedidos com IDs pendentes duplicados e métodos fora do protocolo permitido são recusados.
- Reserva durável de até 180 s por 3420 s verificados antes de autorização para o autor. A reserva não volta após reinício, mesmo que a janela seja desperdiçada.
- Prazo monotônico da janela independente da fase modular. Não há encaminhamento após o prazo; falhas favorecem a carteira principal.
- Migração de ledger v1 para v2 sem reclassificar tempo legado como nova quota. Reservas inválidas desativam a contribuição sem sobrescrever o arquivo original.
- Recusa de autorização do autor comprovada em TCP local: conexões seguintes usam o usuário, sem reservar de novo.
- Ciclo de TCP com relógio real e durações reduzidas para 1,9 s / 0,1 s: usuário → autor → usuário, com resposta de share fictícia aceita somente pela pool simulada. Essa share não é PoW válido nem crédito real.
- Dez quotas completas exercitadas em fixtures: soma reservada nunca excede a proporção de 5% sobre tempo principal mais reservado. Reinícios após reserva e falha de armazenamento não liberam quota duplicada nem autorização sem persistência.
- Testes de desativação, configuração inválida, carteira do próprio autor, prova grande preservada e autenticação desconhecida continuam cobertos.

Reprodução local, sem GPU ou pool externa:

Resultado da validação: **265 testes da suíte completa passaram**, incluindo **22 testes específicos** de contribuição e segurança de quota. Links locais e busca de segredos também foram conferidos.

```powershell
mise exec -- python -m unittest test_contribution test_contribution_safety -v
```

## Sondagens da pool real

Foram feitas duas conexões de autorização em `prl.kryptex.network:7048`: uma direta e outra através do proxy corrigido. A segunda usou carteira principal **sintética** e quota pré-carregada **somente na fixture temporária**, para testar a substituição para a carteira pública válida do autor. A pool respondeu `result: true` à autorização para o autor. Não foi iniciado minerador nem enviada share, prova, transação ou pedido de saque.

Esse resultado comprova compatibilidade da autorização/reescrita com a resposta real da Kryptex no momento do ensaio. Não comprova crédito de mineração nem pagamento. O formato da resposta é compatível com o [protocolo Pearl documentado pela Suprnova](https://prl.suprnova.cc/stratum-spec.html); isso não implica que os servidores sejam idênticos.

Na leitura somente de status da máquina do mantenedor, a carteira principal era a do próprio autor e não havia proxy de contribuição ativo. A mineração existente, portanto, não é um experimento com duas carteiras nem evidência de recebimento da contribuição de um usuário. Nenhum processo ou dado de produção foi modificado por esta avaliação.

## O que falta para comprovar pagamento

Um ensaio separado precisa de duas carteiras públicas distintas sob controle do operador, com consentimento para o destino de cada fase. Deve registrar versão/worker, shares válidas e dificuldade, respostas aceitas da pool, créditos por carteira e transações de pagamento após maturação/limiar. Créditos da mineração normal do autor devem ser separados dos créditos desse worker de teste. A razão de moedas não precisa ser exatamente 5%: o produto divide tempo e não faz transferência de 5% de saldo.

Sem essa evidência, não se deve anunciar “pagamentos de 5% certificados” ou “recebimento garantido”.

Mesmo com o ensaio concluído, **não é um teto financeiro de 5%**. O proxy mede conexão autorizada, não prova utilização contínua da GPU. Pausas, dificuldade e aceitação de shares podem concentrar os créditos de um período curto em uma fase, inclusive na fase do autor. O saldo anterior do usuário não é movido, mas a proporção de créditos novos pode diferir da proporção de tempo. Para um teto exato de 5% do valor seria necessário outro mecanismo, como divisão nativa e verificável pela pool; não se deve prometer esse comportamento com o proxy temporal. A explicação do onboarding foi ajustada para tornar essa diferença visível.
