# Atualizações pelo aplicativo

O HashPilot consulta as releases públicas de `TechFernandesLTDA/hashpilot` em segundo plano, inicialmente após dez segundos e depois a cada seis horas. A consulta não usa credenciais nem envia carteiras ou dados do rig. Falta de internet ou limite da API não impede a mineração.

Abra **Configurar → Atualizações** para verificar manualmente. Na ausência de preferência salva, o canal acompanha a build instalada: a v0.3.5 é de pré-lançamento e considera outras releases experimentais. A tela informa esse canal. A opção **Incluir pré-lançamentos** pode ser desmarcada para considerar somente estáveis; a escolha salva prevalece nas próximas aberturas. Builds estáveis futuras devem declarar seu canal em `app_version.py`. Rascunhos, tags sem release, versões antigas e pacotes sem os assets obrigatórios são ignorados.

A versão em execução vem do `RELEASE-MANIFEST.json` ao lado do aplicativo instalado. Em um clone de desenvolvimento, usa `app_version.py`; o ponteiro `installation.json` não é usado para afirmar qual interface está em execução.

A partir da v0.3.6, a versão também aparece no cabeçalho e no título da janela, no resumo e no console. O snapshot local inclui `app.version`, `app.revision` e `app.source`. Instalar uma release com a interface antiga aberta mantém essa interface na revisão anterior até fechá-la pelo menu **Sair** e abrir a nova. Atualizar somente o painel não troca a versão do serviço de mineração que estiver em execução.

## Instalar e reabrir

1. Confira a versão oferecida e confirme **Instalar atualização**. Nada é instalado automaticamente.
2. O instalador local baixa o ZIP e `SHA256SUMS` da release selecionada, verifica o hash e o inventário, prepara uma pasta de versão nova e ativa os metadados atomicamente. A interface atual continua aberta em caso de erro.
3. Após a confirmação da instalação, clique em **Reabrir versão instalada**. A interface antiga salva e fecha normalmente; um helper aguarda seu encerramento antes de abrir a nova, sem repetir o onboarding.

Os históricos, carteiras, opções de ajuda, configurações de GPU, chaves locais de IA e logs permanecem nos diretórios atuais. O updater não escreve no `settings.json`, não importa dados padrão, não apaga bancos e não faz backup parcial de SQLite com WAL aberto. Preferências do canal ficam separadas, em `%LOCALAPPDATA%\PearlMonitor\update-preferences.json`. O log da instalação fica em `update-install.log` nessa pasta.

O serviço do rig, seus supervisores e os mineradores continuam executando o código atual. A atualização da interface não aplica silenciosamente código novo ao controle de hardware e não altera exceções do Defender. Uma atualização do serviço exige um procedimento separado, com consentimento e elevação do administrador, e pode interromper a mineração.

Na v0.4.0, **Configurar → Central de operação → Saúde e versões** identifica separadamente os componentes. O botão **Atualizar serviço (UAC)** executa esse procedimento e preserva o opt-in de referência da unidade autorizada. Versões antigas sem identidade publicada aparecem como desconhecidas. O UAC não é desativado nem previamente aprovado pelo programa.

## Limites e recuperação

As pastas anteriores ficam preservadas. Se o fechamento normal exceder o prazo, o helper não encerra processos à força: feche pelo menu **Sair** e abra o novo atalho. Se o download ou a verificação falhar, continue na interface atual e consulte o log.

Voltar à pasta anterior não desfaz uma migração de banco realizada por uma versão nova. Não há promessa de rollback automático de schema. Hashes publicados junto ao pacote protegem contra corrupção e divergências; não substituem assinatura de publicador ou auditoria independente.

Referências: [releases na API pública do GitHub](https://docs.github.com/en/rest/releases/releases), [limites de consulta sem autenticação](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), [verificação dos pacotes](verification.md).

## Atualiza??o do SRBMiner

Em **Configurar ? Atualiza??es ? Atualiza??es do minerador SRBMiner** ou na **Central de opera??o ? Atualizar minerador**, confira vers?o selecionada, vers?o em execu??o e release oficial. O painel consulta automaticamente a cada seis horas; a instala??o exige confirma??o. A ativa??o exige o servi?o atualizado e ativo. GPUs desativadas n?o s?o ligadas pela troca.

A vers?o anterior fica dispon?vel para voltar. O servi?o tenta rollback se uma GPU habilitada n?o apresentar hashrate v?lido na nova vers?o em 180 segundos. Se um processo n?o puder ser parado com identidade confirmada, a troca ? bloqueada. Confira Sa?de ap?s um rollback: selecionar a vers?o anterior n?o comprova sozinho que a minera??o retomou.

Apenas vers?es com hashes catalogados s?o instal?veis. O ZIP oficial e o execut?vel s?o verificados separadamente. N?o h? exce??o autom?tica no antiv?rus: se houver bloqueio, abra a revis?o do Defender e analise a detec??o. Uma exce??o concedida para a vers?o anterior n?o autoriza o arquivo da vers?o nova.
