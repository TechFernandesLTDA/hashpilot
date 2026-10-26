# Defender: análise e exceção opcional do minerador

O SRBMiner é um executável de terceiros. A detecção do Defender não permite concluir, sozinha, que existe infecção nem que houve falso positivo. Um SHA-256 correspondente à distribuição oficial confirma a origem do pacote, mas não garante segurança. O HashPilot não certifica o executável do SRBMiner.

Para `Trojan:Win32/Sabsik.FL.A!ml`, a [Microsoft publica uma descrição genérica e informa que não há detalhes técnicos disponíveis](https://www.microsoft.com/en-us/wdsi/threats/malware-encyclopedia-description?Name=Trojan%3AWin32%2FSabsik.FL.A%21ml). A classificação do arquivo específico continua dependendo de análise; não se deve prometer que ele é seguro.

## Fluxo no aplicativo

Abra **Configurar → Segurança do minerador**. O recurso também está disponível na configuração das GPUs.

1. Leia a explicação e, preferencialmente, use **Solicitar análise à Microsoft** para abrir o [portal oficial](https://www.microsoft.com/en-us/wdsi/filesubmission). Abrir o portal não envia nenhum arquivo automaticamente.
2. **Histórico de proteção** abre a interface do Windows para revisar a ocorrência. Se o protocolo do Windows estiver indisponível, abre a orientação oficial no navegador.
3. Para solicitar uma exceção local, marque explicitamente a caixa de consentimento, inicialmente desmarcada, e clique em **Solicitar exceção (UAC)**.
4. O Windows solicita elevação quando o processo não é administrador. O administrador precisa confirmar novamente o caminho completo em um diálogo cujo padrão é **Não**. Cancelar não cria a exceção.
5. O script verifica o hash fixado do executável SRBMiner 3.8.0. Se o executável estiver ausente, exige o ZIP oficial com SHA-256 fixado antes de permitir a exceção para o caminho futuro. Arquivo divergente, pacote ausente/divergente, link ou junction no caminho impedem a inclusão.
6. O Defender precisa confirmar que a exceção aparece em sua configuração. Políticas da organização ou proteção do sistema podem impedir a operação; o aplicativo não tenta contorná-las.

O único caminho elegível é `%LOCALAPPDATA%\Mining\Pearl\SRBMiner-Multi-3-8-0\SRBMiner-MULTI.exe`, do usuário que iniciou a solicitação. A pasta original é preservada ao elevar com credenciais de outro administrador.

**A exceção é por caminho, não por hash:** depois de concedida, substituições futuras nesse caminho também ficam excluídas da verificação antivírus. Conferir o hash antes da inclusão não muda essa limitação. Nenhuma pasta inteira, processo Python, configuração de proteção em tempo real ou lista global de ameaças permitidas é alterada. A Microsoft explica que [exceções reduzem a proteção e devem ser usadas somente após esclarecer a causa](https://learn.microsoft.com/en-us/defender-endpoint/defender-endpoint-false-positives-negatives).

**Remover exceção (UAC)** remove somente esse caminho, sem exigir que o executável ou ZIP ainda exista. Outras exceções são preservadas. A remoção também precisa ser confirmada pelo Defender.

O fluxo não restaura quarentena, não reinstala o executável nem inicia mineração. Se você restaurar ou reinstalar posteriormente, o serviço do rig que já estiver ativo pode retomar a mineração automaticamente. A exceção local não é aprovação da Microsoft nem imunidade a outros controles ou políticas do Windows.

## Validação

As funções reais do script são carregadas por AST em fixtures temporárias com cmdlets do Defender simulados. Os casos verificam hashes divergentes, pacote ausente, leitura negada, junction, bloqueio por política, inclusão limitada a um caminho, remoção sem binário e preservação de outras exceções. Os testes não alteram o antivírus real. A interface é verificada em cenários de resolução HD, Full HD e 4K, incluindo consentimento desmarcado e botão inicialmente bloqueado. UAC real e aplicação de política em uma máquina administrativa não são comprovados por essas fixtures.
