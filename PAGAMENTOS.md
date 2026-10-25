# Carteira, confirmação e próximo pagamento

A visão geral identifica a carteira pública de recebimento por completo, com cópia do endereço e acesso à página da pool. A rede é Pearl (PRL). Nenhuma chave privada é necessária para o monitor.

A Kryptex informa mínimo geral de 1 PRL e ciclos automáticos de pagamento uma vez por hora após atingir o mínimo. O HashPilot usa o `threshold` da própria carteira, pois esse valor pode ser diferente do mínimo geral. Fontes: [pool PRL](https://pool.kryptex.com/prl), [regras de pagamento](https://pool.kryptex.com/articles/how-to-mine-pearl-en) e [API pública](https://pool.kryptex.com/api).

O painel distingue:

- **Gerado:** saldo atual na pool, incluindo a parte pendente.
- **Confirmado:** parte confirmada para habilitar pagamento.
- **Pendente:** ainda não confirmada pela pool/rede.
- **Falta gerar:** diferença entre o mínimo e o saldo total atual.
- **Enviado:** pagamento registrado pela pool, com data e transação quando disponíveis. Isso não confirma, por si só, que a Pearl Wallet já sincronizou e mostrou o crédito.

## Estimativa de tempo

A API não publica um horário exato para o próximo envio desta carteira. O aplicativo estima o tempo para gerar o saldo faltante, inclui a maturação típica informada pela pool/rede e a espera pelo ciclo horário. Depois do envio, a confirmação da transação e a sincronização da carteira continuam necessárias.

O ritmo preferido é observado pela variação de **pago+saldo**, nos últimos 60 minutos, exigindo pelo menos 30 minutos de cobertura. Isso evita contar o pagamento novamente como receita. Se ainda não houver observações suficientes, usa-se a estimativa por hashrate/rede, identificada no painel.

`maturation_time` é uma duração típica: não é o tempo restante individual de cada crédito. Quando o total já atingiu o mínimo, mas ainda não confirmou, o prazo exato de confirmação continua indisponível. Se o saldo confirmado já atende ao mínimo, o painel mostra a espera pelo próximo ciclo, sem adicionar a maturação outra vez. Dados antigos ou ausentes não geram um cronômetro fictício.

A estimativa muda com o ritmo de crédito, a dificuldade e os dados da pool. O preço em reais não muda o mínimo expresso em PRL.

## Equivalentes em R$, US$ e BTC

A consulta CoinGecko usa `pearl-2` (Pearl Research Labs) e `bitcoin`. Os valores em reais e dólares são a quantidade de PRL multiplicada pela cotação correspondente. O equivalente em BTC usa `PRL×preçoPRL/USD÷preçoBTC/USD` (com alternativa pela cotação BRL quando necessário). Todos são valores indicativos atualizados, não conversões ou transferências executadas.

Consultas remotas a cada 60 s; o painel mostra data, origem e falhas/cache. Campo ausente aparece como N/D. O histórico financeiro e os dados públicos das fontes permanecem no SQLite após reinícios.
