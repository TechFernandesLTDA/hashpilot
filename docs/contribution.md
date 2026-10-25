# Contribuição opcional de 5% do tempo de mineração

Ao configurar outra carteira, o HashPilot oferece uma opção visível para contribuir com a manutenção do projeto. Quando ainda não existe uma escolha salva, o onboarding apresenta a opção pré-marcada como sugestão, junto da explicação de 57/3 minutos e do endereço do autor. O usuário pode desmarcá-la antes de salvar e pode mudar a escolha depois. **O backend só habilita a contribuição após salvar o booleano JSON `support_enabled: true`.** Configurações antigas sem essa chave, texto `"true"`, números e configuração inválida não habilitam contribuição retroativamente.

Quando a carteira configurada é a do próprio autor, não existe contribuição adicional: a mineração já está destinada a esse endereço.

Carteira pública do autor:

```text
prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t
```

## Como funciona

Com a opção selecionada no início da mineração, o minerador conecta a um proxy local em `127.0.0.1`, usando uma porta atribuída pelo Windows. O proxy conecta somente à pool Pearl `prl.kryptex.network:7048` e alterna:

| Tempo conectado elegível por ciclo | Carteira autorizada na pool |
|---|---|
| Primeiros 57 minutos | Carteira principal configurada pelo usuário |
| Próximos 3 minutos | Carteira pública do autor |

Ao alternar, o proxy encerra a conexão Stratum para o próprio minerador reconectar. **Não reinicia o processo do minerador, a GPU ou o supervisor de clocks.** A próxima autorização usa a carteira da nova fase. Há uma breve reconexão de rede em cada transição.

Os 5% são uma divisão aproximada de **tempo conectado**, e não uma promessa de exatamente 5% das moedas, shares, hashrate útil, saldo ou lucro. Sorte da pool, dificuldade, reconexões, shares rejeitadas e períodos internos de taxa do minerador podem alterar o resultado. Nenhuma moeda é retirada da carteira do usuário e nenhuma chave privada é necessária.

A contribuição do HashPilot é distinta da taxa própria do SRBMiner, exibida no log — 2% para pearlhash na versão utilizada pelo projeto. A divisão de tempo não elimina a taxa do minerador nem a taxa da pool. O aplicativo deve mostrar a carteira principal e a fase efetivamente ativa separadamente, sem apresentar a mineração para o autor como saldo do usuário.

## Habilitar, desabilitar e trocar de carteira

- **Desabilitar:** um proxy já ativo recarrega a configuração em até dois segundos. Se estava na fase do autor, encerra essa conexão e a próxima autorização volta para a carteira principal.
- **Habilitar novamente com proxy ativo:** a programação retoma a fase persistida, contando somente o tempo conectado em que a opção está habilitada.
- **Habilitar em uma mineração que começou diretamente na pool:** passa a valer no próximo início do minerador pelo iniciador do HashPilot. A opção não passa a controlar um processo externo silenciosamente.
- **Trocar a carteira enquanto uma mineração antiga está ativa:** não reescreve a carteira principal do processo antigo. Se a configuração no disco passa a se referir a outra carteira, a contribuição desse proxy é desligada. O novo endereço exige o próximo início do minerador.

Desabilitar congela o contador elegível; tempo offline e tempo com a opção desligada não criam uma dívida ou avanço retroativo de contribuição. O histórico financeiro de cada carteira permanece separado.

## Transparência e persistência

`support-mining.json` no diretório de logs informa PID do proxy, porta local, estado pronto/conectado, opção habilitada, carteira principal, carteira de apoio, carteira ativa, fase ativa, fase programada, tempo elegível e segundos até a próxima troca. `routing_active: false` indica que não há uma conexão roteada naquele instante; uma opção marcada não é prova de contribuição em andamento.

`support-mining-ledger.json` mantém o tempo elegível e a fase por carteira. A escrita substitui o JSON atomicamente. Reiniciar o proxy não acrescenta o intervalo em que estava fechado. Um encerramento abrupto pode perder no máximo o pequeno intervalo ainda não salvo, normalmente cerca de um segundo.

`support-mining-owner.json` é protegido por uma trava de arquivo do Windows durante toda a sessão. Outro proxy no mesmo diretório é recusado antes de publicar status ou alterar o ledger. O Windows libera a trava ao encerrar o processo, inclusive em uma interrupção abrupta; metadados de um PID antigo não impedem a recuperação. Após adquirir a trava, o proxy relê o ledger mais recente.

Configuração malformada, carteira incompatível ou ledger inválido desligam o redirecionamento ao autor. Um ledger corrompido é preservado para investigação; o proxy continua usando a carteira principal, sem zerar silenciosamente o ciclo para cobrar uma contribuição nova.

## Protocolo e limites

O proxy aceita uma sessão de minerador por vez, em loopback, para o fluxo atual da GPU 0. Reconhece autenticações JSON por linha `mining.authorize`, `eth_submitLogin`, `login` e `mining.login`. Substitui somente o campo de carteira na autenticação e, quando presente, no envio de shares `mining.submit`. A carteira precisa ser exatamente a principal ou a principal seguida de `.worker`; outros campos e dados das shares não são alterados.

O dialeto Pearl documentado pela Suprnova usa parâmetros nomeados: `mining.authorize.params.wallet` autoriza a carteira, um campo `worker` separado continua preservado e `mining.submit.params` pode conter apenas `job_id` e `plain_proof`. O proxy aceita linhas de até 4 MiB e preserva integralmente provas grandes. Os testes locais incluem autorização `wallet.worker` e uma prova de 1,5 milhão de caracteres encaminhada sem alterações. Essa evidência cobre a compatibilidade do formato em uma pool simulada; não equivale a um teste real de redirecionamento ou crédito na Kryptex. Consulte a [especificação Pearl Stratum da Suprnova](https://prl.suprnova.cc/stratum-spec.html).

Autenticação desconhecida ou incompatível encerra a conexão antes de autorizar trabalho. Não existe fallback que mande uma carteira desconhecida para o autor. Isso pode exigir adaptação para um protocolo futuro do minerador; não se deve contornar a verificação desabilitando a validação.

O proxy não expõe uma porta na rede local, não permite configurar outro upstream por linha de comando, não contém uma carteira privada e não concede acesso remoto à máquina. O iniciador acompanha seu próprio PID e o encerra quando sua mineração termina.

## Desenvolvimento e teste

O módulo não importa `config.py`; `AUTHOR_WALLET` é a fonte única do endereço padrão. Exemplo de execução controlada pelo iniciador:

```powershell
python contribution.py --wallet SEU_ENDERECO_PUBLICO --settings CAMINHO_SETTINGS_JSON --state-dir CAMINHO_LOGS --port 0
```

O iniciador aguarda `support-mining.json` com `ready: true`, confere o PID que abriu e só então informa `127.0.0.1:porta` ao SRBMiner.

Testes usam uma pool TCP simulada localmente e não mineram:

```powershell
mise exec -- python -m unittest -v test_contribution.py
```

Os parâmetros oficiais do SRBMiner não documentam uma opção de taxa personalizada para a carteira de um aplicativo; por isso, esta implementação usa divisão explícita de tempo em um proxy local, separada das opções do minerador. Consulte [SRBMiner — Parameters](https://github.com/doktor83/SRBMiner-Multi/blob/master/Parameters).
