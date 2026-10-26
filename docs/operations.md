# Central de operação

A partir da v0.4.0, abra **Configurar → Central de operação**. As três abas principais continuam disponíveis; a central reúne as operações menos frequentes.

## Saúde e recuperação

O indicador distingue processo ativo, conexão, hashrate atual e share aceita. Uma resposta de aceitação registrada no log é evidência da pool, mas não comprova crédito final nem pagamento. O último ACK pertence à instância daquela GPU, sem atribuir o saldo de uma carteira compartilhada a uma placa específica.

Sem pelo menos três intervalos históricos conhecidos, o alerta usa 15 minutos. Depois, usa seis vezes a mediana dos intervalos, entre 15 e 120 minutos. O início tem carência de cinco minutos. Dificuldade desconhecida é indicada; ausência de share não prova falha da pool. ACKs persistem em `operations.sqlite3`, separados por UUID; registros anteriores à sessão atual não confirmam a sessão nova.

O serviço pode reconectar uma GPU que fique sem conexão/hashrate por cinco minutos, ou sem shares por mais de duas janelas de alerta. Exige GPU habilitada, processo sob sua propriedade, sensor atual abaixo de 70 °C e ausência de pausa térmica. Processos encerrados também respeitam a verificação térmica e o orçamento. Não troca carteira, pool ou política do Defender.

No máximo três tentativas automáticas por GPU em uma hora, com espera crescente. O orçamento é gravado antes de iniciar a tentativa e sobrevive ao reinício do serviço. O botão de reconexão manual respeita o mesmo orçamento e não reativa uma GPU desabilitada. Outras GPUs continuam trabalhando.

## Componentes e perfis

Painel, serviço e controlador mostram suas próprias versões, obtidas do manifesto local de cada processo. O minerador informa a versão do seu log, separada do pacote fixado. Uma versão antiga que não publica identidade aparece como desconhecida. **Atualizar serviço (UAC)** usa o procedimento separado de parada confirmada e início do serviço; pode interromper a mineração e requer aprovação do administrador. A atualização do painel não altera silenciosamente o serviço.

Perfis são persistidos por UUID e podem ser aplicados enquanto o controlador está ativo, com novo benchmark de cinco minutos. Máximo desempenho busca desempenho medido. Equilibrado prioriza eficiência e antecipa alívio térmico em 3 °C. Menos calor / ruído antecipa em 7 °C e prioriza redução de potência; a política da ventoinha continua a do minerador/driver, portanto redução de ruído não é garantida. Nenhum perfil copia clocks de outro modelo. A referência da RTX 3070 permanece restrita à unidade explicitamente autorizada.

O histórico mostra ajustes aplicados, medições antes/depois, hashrate de referência, aceitação/rejeição e motivo. Aplicado significa que o comando foi executado; validado exige amostras independentes posteriores. Ajustes térmicos e reversões também são registrados. O histórico antigo continua disponível, mesmo quando não tinha todos os campos de medição.

## Ajuda de 5%

A auditoria mostra carteira configurada, rota atual somente quando confirmada, fase, próxima troca, tempos verificados/reservados e shares aceitas/rejeitadas por fase. Com proxy parado, o ledger persistente continua consultável, identificado como histórico. Sem dados, mostra indisponibilidade em vez de inventar confirmação.

São até três minutos de ajuda para 57 minutos primários verificados. Tempo reservado inclui reservas já utilizadas e não é saldo em moedas. A proporção não garante 5% das moedas, não retira saldo e continua desativável. Mantendo a carteira padrão do autor, a mineração vai diretamente a ele, sem contribuição adicional. O ledger não é restaurado de backup: reservas antigas não podem ser reutilizadas.

## Backup, restauração e diagnóstico

O backup privado inclui configurações permitidas e snapshots dos bancos globais e por GPU via API SQLite de backup, incluindo dados confirmados no WAL. Cada banco tem snapshot consistente; não há promessa de um instante transacional único entre todos os arquivos. Chaves e configuração de provedores de IA ficam separadas; o diagnóstico público não é um backup.

A restauração valida inventário, limites, caminhos, SHA-256 e integridade SQLite antes da aplicação. Encerra o painel e aguarda os processos do rig identificados, sem finalização forçada. Cria um backup do estado anterior antes de substituir dados. Preserva a pasta de logs e as credenciais locais atuais. Se falhar, consulte `restore-result.json` e o backup `before-restore-*`; não há promessa de transação atômica entre todos os bancos. Depois de restaurar, o painel reabre e solicita conferir carteiras antes de iniciar o rig. Restaurar um backup antigo substitui os históricos pelos daquele backup; os dados anteriores ficam no backup de segurança.

O diagnóstico usa uma lista explícita de campos: modelo normalizado, driver, sensores e contadores. A prévia aparece antes de salvar; nenhum envio pela internet é realizado. Não inclui carteiras, UUIDs, caminhos, configurações, chaves, texto livre dos logs ou respostas da IA.

## Rendimentos

A aba de rendimentos indica origem, idade e disponibilidade das fontes. Confirmado/pendente vêm da pool; BRL, USD e BTC são conversões. Os períodos históricos podem ser parciais. Valores futuros são projeções variáveis de hashrate, rede e cotação atuais, não dinheiro já creditado.
