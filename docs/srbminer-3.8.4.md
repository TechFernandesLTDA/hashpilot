# SRBMiner 3.8.4 na RTX 3070 — revisão de 6 de novembro de 2026

Fontes primárias: [release completa 3.8.4](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.4), [histórico oficial](https://github.com/doktor83/SRBMiner-Multi/releases), [parâmetros](https://github.com/doktor83/SRBMiner-Multi/blob/master/Parameters) e ajuda do executável Windows cujo hash foi verificado. A consulta à API abrangeu 100 releases disponíveis; a análise de aplicabilidade priorizou Pearl e a placa física existente.

## O que muda

| Item do changelog 3.8.4 | Aplicação nesta máquina |
|---|---|
| Memória bloqueada em 405 MHz para algoritmos dependentes do núcleo, incluindo Pearl nas séries RTX 30/40/50 | Principal hipótese de eficiência a testar na RTX 3070; não implica ganho garantido de hashrate. |
| Afinador de eficiência melhorado e disponível em Windows, ainda muito experimental | Não ativar junto com o supervisor de clocks do HashPilot. |
| Perda tolerada configurável por GPU no afinador nativo | Só faria sentido num modo experimental separado, sem dois responsáveis por alterar clocks. |

O autor do minerador informa que o afinador pode provocar reinícios e, em falhas de recuperação, exigir reinício do computador. Recomenda drivers das linhas 595/615 e uso de `--gpu-reset-oc` com esse recurso. A máquina usa 617.14; isso não comprova incompatibilidade, mas também não equivale a teste declarado pelo autor. Não há motivo demonstrado para trocar o driver nesta atualização.

## Histórico relevante para Pearl

O histórico registra correções antigas de shares e validação de provas, mudanças obrigatórias do protocolo e sucessivas otimizações por arquitetura. Na sequência recente, 3.7.8 e 3.8.0 trouxeram mudanças para RTX 30; 3.8.1 também incluiu essa família. A 3.8.2 destacou RTX 50, e a 3.8.3 corrigiu flutuações de hashrate/potência. Essas mudanças já estavam no 3.8.3 instalado; não devem ser contabilizadas como ganhos novos da 3.8.4.

Há menções históricas a `--pearl-k2`, inclusive para 3070/3070 Ti. A opção não apareceu na documentação de parâmetros nem na ajuda atual do binário consultado. Portanto, esse histórico não basta para ativá-la cegamente nesta versão ou afirmar que ainda seleciona um kernel alternativo útil para esta placa.

## Plano aplicado no HashPilot

A configuração de referência mantém o núcleo sob o supervisor existente. A alteração de memória ocorre no início da sessão, em um experimento separado do afinador nativo. A opção é local, reversível e restrita ao UUID da placa autorizada do autor. Não é aplicada automaticamente a placas de outros usuários.

A comparação usa o mesmo preset de núcleo de 1710 MHz. Primeiro mede-se a sessão anterior com memória em 5001 MHz; depois, a 3.8.4 com memória em 405 MHz. Como versão e memória mudam juntas, o resultado mede o conjunto dessas alterações e não permite atribuir todo o ganho isoladamente à memória. Ambiente e carga gráfica também podem influenciar o resultado.

Registrar médias de hashrate e potência, TH/s por watt, temperaturas, clocks efetivos e respostas da pool. Usar ao menos três amostras independentes após aquecimento, conferir se o driver realmente aplicou 405 MHz e comparar enquanto o supervisor ainda não mudou o núcleo. Se houver perda significativa de rendimento, erros ou instabilidade, restaurar a memória para 5001 MHz e, se necessário, selecionar novamente o 3.8.3.

As medições anteriores de 30 minutos em 1710 MHz foram aproximadamente 78,984 TH/s, 159,17 W e máximo de 74 °C, em 20 linhas de log. São observações de produção, não um benchmark de laboratório. Os resultados posteriores devem ser consultados nas evidências da release e não inferidos das promessas do changelog.

## Integridade e reversão

O SHA-256 do arquivo oficial foi conferido com o digest publicado na API de releases. O executável extraído foi identificado por caminho único e hash próprio fixado no catálogo; arquivos adicionais do ZIP não são executados. Isso verifica identidade e integridade, não substitui uma auditoria de segurança do minerador fechado.

A seleção de versão preserva o minerador anterior. A troca da memória para 5001 MHz reabre somente a GPU de referência; a parada manual restaura offsets, clocks destravados, potência de fábrica e ventilação automática. Nenhuma gravação de VBIOS ou alteração permanente é necessária.

## Resultados locais observados

| Configuração e etapa | Hashrate | Consumo | Observação |
|---|---:|---:|---|
| 3.8.4, núcleo 1710 MHz, memória 405 MHz | 34,22 e 33,40 TH/s | 84,9 e 83,4 W | Duas leituras; experimento interrompido precocemente pela perda severa de rendimento. |
| 3.8.4, núcleo 1710 MHz, memória restaurada a 5001 MHz | média 74,93 TH/s | média 151,83 W | Três amostras após aquecimento; máximo de 71 °C nesse intervalo curto. |
| Primeiro ajuste automático, 1710 → 1725 MHz, memória 5001 MHz | referência 74,86 → média 75,53 TH/s | aproximadamente 153 W após o ajuste | Duas amostras independentes, 182 segundos de validação, 73 °C, sem rejeições ou erros de hardware nessa etapa. |

O teste de 405 MHz falhou nesta combinação de RTX 3070 e driver 617.14. O ajuste foi desfeito e o opt-in local ficou em 5001 MHz. Isso não prova que todas as placas da série terão o mesmo comportamento, nem identifica sozinho a causa interna da perda de desempenho.

O período anterior em 3.8.3 e a sessão nova não constituem uma comparação de laboratório: não é válido usar os 78,984 TH/s históricos para atribuir ganho ou perda exclusivamente à atualização. A pequena diferença medida pelo ajuste automático de núcleo também não estabelece o máximo absoluto da GPU. O controle continua explorando degraus suportados de até 15 MHz, com validação posterior, margem térmica e reversão quando sua validação falha. O driver não indicava limitação ativa por potência ou temperatura na consulta realizada.

O afinador nativo experimental permanece desligado. O supervisor do HashPilot mantém o controle dinâmico; a memória fixa e o preset de partida foram configurados explicitamente para esta unidade. A evidência pública agregada fica em `VALIDATION-RESULTS.json` na [release v0.5.11](https://github.com/TechFernandesLTDA/hashpilot/releases/tag/v0.5.11), incluindo os resultados negativos.
