# Análise do executável de terceiros: estado e material de submissão

## Estado atual

**Pendente: nenhuma análise independente ou aprovação da Microsoft foi obtida nesta sessão.** Este documento prepara uma solicitação; não é um protocolo de envio e não contém um resultado da Microsoft. Não atribua aprovação de segurança, falso positivo confirmado ou endosso da Kryptex ao HashPilot.

O SRBMiner é distribuído por seu fornecedor, não pela Tech Fernandes. Em 28/10/2026, o Defender local colocou a versão 3.8.1 em quarentena como **Trojan:Win32/Wacatac.H!ml**. A atualização foi bloqueada antes da ativação; a versão 3.8.0 continuou selecionada. A atualização do HashPilot e a do SRBMiner são operações diferentes.

O pacote foi obtido da [release oficial 3.8.1](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.1), com digest SHA-256 publicado na API de assets do GitHub. O hash do executável abaixo foi calculado a partir do membro no ZIP verificado, antes da tentativa de instalação. Isso estabelece a integridade do download comparado à distribuição consultada, **não ausência de malware**.

| Campo | Valor para identificar a amostra |
|---|---|
| Produto de terceiros | SRBMiner-MULTI 3.8.1, Windows x64 |
| ZIP | `SRBMiner-Multi-3-8-1-win64.zip` |
| SHA-256 do ZIP | `16bd455557bd3698e004d1f6b5d19290b9b59e9f9c8ef9245acafdaeb2413e79` |
| Executável no pacote | `SRBMiner-Multi-3-8-1/SRBMiner-MULTI.exe` |
| SHA-256 do executável | `2f7e60a17fcc65ddd3c7f5821666a6ae11ef6c1f2c95331bb7e46793c032b688` |
| Detecção observada | `Trojan:Win32/Wacatac.H!ml` |
| Ação observada | Quarentena; nenhuma exceção concedida nesta validação |

## Texto preparado para solicitar análise

Use o [portal oficial Microsoft Security Intelligence](https://www.microsoft.com/en-us/wdsi/filesubmission) com sua conta e indique corretamente sua relação com o arquivo. O mantenedor do HashPilot integra um minerador de terceiros; não deve se apresentar como autor do SRBMiner.

> Please analyze SRBMiner-MULTI 3.8.1 for Windows x64, obtained from the vendor's official GitHub release. Microsoft Defender detected Trojan:Win32/Wacatac.H!ml during an explicitly requested installation. The detected executable's SHA-256 is 2f7e60a17fcc65ddd3c7f5821666a6ae11ef6c1f2c95331bb7e46793c032b688. The official ZIP's SHA-256 is 16bd455557bd3698e004d1f6b5d19290b9b59e9f9c8ef9245acafdaeb2413e79. We maintain HashPilot, a separate open-source monitoring/integration tool, and are not the authors of SRBMiner. We have not established whether this detection is correct or a false positive. The installation was stopped before activation and the sample was quarantined. Please provide an assessment of this exact file. Vendor release: https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.1

Envie somente a amostra oficial que o portal solicitar, após revisar o formulário. Não envie a pasta de dados do HashPilot, configurações, carteira, logs financeiros, bancos, credenciais, seeds ou chaves privadas. Não restaure nem execute uma amostra em quarentena apenas para preencher o formulário. O ZIP original pode identificar a distribuição sem restaurar o executável no computador.

Após um envio real, registre o identificador do protocolo e a data; publique o resultado somente quando recebido e vinculado ao hash analisado. O parecer sobre uma amostra não certifica outras versões nem todo o aplicativo.

[Como revisar um bloqueio no aplicativo](defender.md) · [Evidências e limites](verification.md)
