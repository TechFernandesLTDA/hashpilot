# Auditoria de bugs — 25/10/2026

Revisão de estabilidade do HashPilot a partir de `9a21999`. Foram corrigidos **12 bugs principais e 60 microbugs/casos de borda**, com 72 regressões específicas em `test_bug_audit.py`. A suíte completa tem **229 testes**.

Principal significa efeito em controle, histórico, integração de IA ou interpretação financeira. Microbug significa um defeito pontual de validação, formatação, diagnóstico ou isolamento. São reproduções com fixtures; não se afirma que os 72 ocorreram na máquina física.

O teste de conexão OpenRouter foi realizado com a configuração do usuário, sem imprimir/exportar a chave e sem aplicar sua resposta ao hardware. O benchmark manual `4c6bf1f7031e4a59880ac71a9316838e` concluiu validado: 300 s configurados, três medições independentes, média de 75,0467 TH/s, 154,4567 W, perfil 1710 MHz e limite de 240 W, sem amostras instáveis. Isso é evidência de uma RTX 3070, não de seis placas físicas.

## Bugs principais

| ID | Reprodução / correção | Regressão |
|---|---|---|
| B01 | Espera entre consultas cloud aparecia como análise eternamente pendente. | [Teste B01](../../test_bug_audit.py#L47) |
| B02 | Troca de provedor/modelo podia reaproveitar resposta ou cache da configuração anterior. | [Teste B02](../../test_bug_audit.py#L57) |
| B03 | Configuração inválida de IA podia interromper o caminho de controle em vez de só desativar o consultor. | [Teste B03](../../test_bug_audit.py#L69) |
| B04 | Testar conexão gravava preferências/chave antes do usuário salvar; cancelar não desfazia. | [Teste B04](../../test_bug_audit.py#L76) |
| B05 | Falha ao encerrar processo apagava sua identidade e permitia duplicação de trabalhadores. | [Teste B05](../../test_bug_audit.py#L85) |
| B06 | Linha de hashrate com data futura bloqueava medições atuais posteriores. | [Teste B06](../../test_bug_audit.py#L96) |
| B07 | Tail de arquivo UTF-16 grande perdia o BOM e decodificava como UTF-8. | [Teste B07](../../test_bug_audit.py#L100) |
| B08 | Job malformado podia matar ou bloquear permanentemente a thread do histórico. | [Teste B08](../../test_bug_audit.py#L105) |
| B09 | Projeção financeira usava rede/cotação antiga como se estivesse atual. | [Teste B09](../../test_bug_audit.py#L116) |
| B10 | Snapshots financeiros fora de ordem podiam inverter ou omitir o ganho do período. | [Teste B10](../../test_bug_audit.py#L120) |
| B11 | Benchmark não recebia o contador de erros de hardware do minerador e podia validar uma sessão defeituosa. | [Teste B11](../../test_bug_audit.py#L124) |

| B12 | Orçamento de geração muito pequeno podia esgotar a saída em raciocínio e devolver conteúdo vazio; orçamento ampliado e resposta final validada, mantendo teto de preço zero. | [Teste B12](../../test_bug_audit.py#L135) |

## Microbugs e casos de borda

| ID | Reprodução / correção | Regressão |
|---|---|---|
| M01 | Raiz de configuração de IA em array causava erro de atributo. | [Teste M01](../../test_bug_audit.py#L151) |
| M02 | Tipo inválido de provedor escapava da validação controlada. | [Teste M02](../../test_bug_audit.py#L153) |
| M03 | Modelo booleano falso era substituído silenciosamente por outro modelo. | [Teste M03](../../test_bug_audit.py#L155) |
| M04 | Texto false podia virar consentimento cloud verdadeiro. | [Teste M04](../../test_bug_audit.py#L157) |
| M05 | Texto false podia ativar IA por coerção booleana. | [Teste M05](../../test_bug_audit.py#L159) |
| M06 | Contexto de IA não estruturado causava erro de atributo. | [Teste M06](../../test_bug_audit.py#L161) |
| M07 | Sensor booleano era aceito como uma medida numérica real. | [Teste M07](../../test_bug_audit.py#L163) |
| M08 | Inteiro muito grande derrubava a sanitização de sensores. | [Teste M08](../../test_bug_audit.py#L165) |
| M09 | Potência negativa era enviada como telemetria válida. | [Teste M09](../../test_bug_audit.py#L167) |
| M10 | Virada UTC reiniciava indevidamente o intervalo global de consultas. | [Teste M10](../../test_bug_audit.py#L169) |
| M11 | Timestamp booleano podia entrar no contador de cota como data real. | [Teste M11](../../test_bug_audit.py#L174) |
| M12 | JSON inválido do provedor produzia diagnóstico genérico de parser. | [Teste M12](../../test_bug_audit.py#L178) |
| M13 | Chave com quebra de linha chegava ao transporte HTTP. | [Teste M13](../../test_bug_audit.py#L186) |
| M14 | Justificativa vazia só com espaços era aceita como sugestão válida. | [Teste M14](../../test_bug_audit.py#L190) |
| M15 | Resposta de IA não textual não tinha validação de contrato adequada. | [Teste M15](../../test_bug_audit.py#L192) |
| M16 | Modelo cloud podia ser escolhido pelo caminho anunciado como Ollama local. | [Teste M16](../../test_bug_audit.py#L194) |
| M17 | Seção de amostra não estruturada não era tratada com segurança. | [Teste M17](../../test_bug_audit.py#L196) |
| M18 | Conversão de inteiros enormes gerava OverflowError em validadores numéricos. | [Teste M18](../../test_bug_audit.py#L198) |
| M19 | Conversão monetária podia produzir infinito. | [Teste M19](../../test_bug_audit.py#L200) |
| M20 | Duração não finita podia quebrar a previsão de pagamento. | [Teste M20](../../test_bug_audit.py#L202) |
| M21 | Saldo com data futura era considerado atual. | [Teste M21](../../test_bug_audit.py#L204) |
| M22 | Pagamento futuro podia ser apresentado como já enviado. | [Teste M22](../../test_bug_audit.py#L207) |
| M23 | Cotação BTC com data futura não era marcada como desatualizada. | [Teste M23](../../test_bug_audit.py#L210) |
| M24 | Hashrate negativo gerava projeção negativa de mineração. | [Teste M24](../../test_bug_audit.py#L213) |
| M25 | Hashrate de rede textual zero causava divisão por zero. | [Teste M25](../../test_bug_audit.py#L215) |
| M26 | Taxa de pool fora da faixa gerava projeção negativa. | [Teste M26](../../test_bug_audit.py#L218) |
| M27 | Cotação numérica textual quebrava multiplicações do financeiro. | [Teste M27](../../test_bug_audit.py#L221) |
| M28 | Snapshot futuro entrava no rendimento atual. | [Teste M28](../../test_bug_audit.py#L224) |
| M29 | Snapshot malformado derrubava o cálculo dos períodos. | [Teste M29](../../test_bug_audit.py#L227) |
| M30 | Duplicata da observação de abertura podia usar a baseline antiga. | [Teste M30](../../test_bug_audit.py#L230) |
| M31 | Cache financeiro com BOM não era restaurado. | [Teste M31](../../test_bug_audit.py#L233) |
| M32 | Cache com raiz em array quebrava a construção do coletor. | [Teste M32](../../test_bug_audit.py#L237) |
| M33 | Ponto inválido no histórico de saldo interrompia a estimativa de ritmo. | [Teste M33](../../test_bug_audit.py#L241) |
| M34 | Contador malformado no log descartava também o hashrate válido. | [Teste M34](../../test_bug_audit.py#L243) |
| M35 | Hashrate sem timestamp era exposto como medição identificada. | [Teste M35](../../test_bug_audit.py#L245) |
| M36 | Taxa de hash inválida substituía uma medição anterior válida. | [Teste M36](../../test_bug_audit.py#L248) |
| M37 | Resposta vazia do driver vazava StopIteration sem diagnóstico de sensor. | [Teste M37](../../test_bug_audit.py#L250) |
| M38 | Índice inválido de outro fabricante bloqueava GPUs NVIDIA válidas. | [Teste M38](../../test_bug_audit.py#L252) |
| M39 | Override inválido de GPU não podia ser reparado explicitamente pela atualização normal. | [Teste M39](../../test_bug_audit.py#L255) |
| M40 | Override de carteira falso herdava silenciosamente a carteira do autor. | [Teste M40](../../test_bug_audit.py#L258) |
| M41 | UUIDs com prefixo igual produziam o mesmo nome de worker. | [Teste M41](../../test_bug_audit.py#L261) |
| M42 | Hashrate NaN era contado como GPU minerando. | [Teste M42](../../test_bug_audit.py#L266) |
| M43 | Total de potência não indicava que sensores de algumas GPUs estavam ausentes. | [Teste M43](../../test_bug_audit.py#L269) |
| M44 | Temperatura fora da faixa contaminava o máximo do rig. | [Teste M44](../../test_bug_audit.py#L271) |
| M45 | Amostra não numérica de gráfico lançava exceção no caminho da interface. | [Teste M45](../../test_bug_audit.py#L273) |
| M46 | Booleano podia aparecer formatado como valor monetário conhecido. | [Teste M46](../../test_bug_audit.py#L277) |
| M47 | Formatação de sensor N/A podia lançar exceção na interface. | [Teste M47](../../test_bug_audit.py#L279) |
| M48 | Snapshot futuro do monitor era considerado atual pelo console. | [Teste M48](../../test_bug_audit.py#L281) |
| M49 | Timestamp de sensor NaN era substituído pelo horário atual. | [Teste M49](../../test_bug_audit.py#L284) |
| M50 | Contador inválido de shares interrompia a coleta do benchmark. | [Teste M50](../../test_bug_audit.py#L292) |
| M51 | Timestamp corrompido de status podia esconder toda a telemetria do rig. | [Teste M51](../../test_bug_audit.py#L301) |
| M52 | Status do runtime em array escapava como se fosse um objeto. | [Teste M52](../../test_bug_audit.py#L315) |
| M53 | Payload enfileirado do histórico podia mudar antes de ser gravado. | [Teste M53](../../test_bug_audit.py#L321) |
| M54 | Evento de status de carteira individual era arquivado na carteira padrão. | [Teste M54](../../test_bug_audit.py#L335) |
| M55 | Erro financeiro de outra carteira era atribuído à carteira padrão. | [Teste M55](../../test_bug_audit.py#L344) |
| M56 | Falha de leitura financeira devolvia a identidade da carteira errada. | [Teste M56](../../test_bug_audit.py#L353) |
| M57 | Histórico de GPU perdia identificação completa recebida em objeto aninhado. | [Teste M57](../../test_bug_audit.py#L361) |
| M58 | Data de calendário inválida interrompia arquivamento de logs. | [Teste M58](../../test_bug_audit.py#L370) |
| M59 | Chave não ASCII podia vazar em diagnóstico de codificação de cabeçalho. | [Teste M59](../../test_bug_audit.py#L379) |
| M60 | Timestamp térmico NaN podia contaminar ou bloquear a importação do histórico. | [Teste M60](../../test_bug_audit.py#L384) |

## Verificação adicional

- Interface: 48 cenários do painel, 9 do assistente de IA e 9 do onboarding, de HD a 4K, sem overflow detectado.
- O QA do assistente também aciona o teste de conexão com provedor simulado e verifica que o arquivo de preferências permanece idêntico.
- Scripts PowerShell passam por análise sintática. Links relativos da documentação e verificação local de segredos são executados antes da publicação.
- Dados válidos e históricos existentes são preservados. Jobs inválidos do histórico são diagnosticados em `history-rejected.jsonl`, permitindo continuar gravando amostras posteriores.
- A instalação e a mineração em produção são verificadas após publicar o mesmo commit. O resultado de uma janela curta não certifica estabilidade prolongada.

A atualização do serviço usa `mining/windows/Atualizar-Rig.ps1`: pede elevação antes de solicitar encerramento, confirma identidade/estado do gerenciador e aguarda saída normal. O teste `test_service_update.py` cobre cinco cenários com processos fictícios, sem encerrar processos reais.

A verificação real adicional encontrou resposta sem conteúdo final na rota gratuita do OpenRouter. Foi ampliado o orçamento de geração e acrescentado diagnóstico explícito de truncamento, sem aceitar raciocínio interno como ação. [Comportamento documentado pelo provedor](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
