# SRBMiner 3.8.6 na RTX 3070 — revisão de 8 de novembro de 2026

Fontes primárias: [release 3.8.6](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.6), [release 3.8.5](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.5) e [parâmetros](https://github.com/doktor83/SRBMiner-Multi/blob/master/Parameters) no commit `659c275` (07/11/2026). As duas releases saíram depois da [revisão da 3.8.4](srbminer-3.8.4.md).

**Estado: incluída no catálogo verificado, ainda não recomendada.** Os hashes foram conferidos contra o pacote oficial (ver [Inclusão no catálogo](#inclusão-no-catálogo)). A versão padrão do instalador continua a 3.8.0, e a 3.8.6 só vira a recomendada depois do [A/B na RTX 3070](#plano-de-teste-na-rtx-3070).

## O que mudou

| Release | Item do changelog | Aplicação na RTX 3070 |
|---|---|---|
| 3.8.6 (08/11) | "Improved efficiency on algorithm 'pearlhash' for NVIDIA 3000, 4000 and 5000 GPUs" | **Principal hipótese a testar**: é a família da placa. Eficiência é TH/s por watt; o texto não promete mais hashrate. |
| 3.8.6 | Melhorias no algoritmo `quantus` (NVIDIA 2000/5000 e AMD) | Não se aplica: o HashPilot minera só Pearl. |
| 3.8.5 (07/11) | Correções no afinador de eficiência e novo `--gpu-power-tuner-reset` | Não se aplica enquanto o afinador nativo seguir desligado (ver abaixo). |
| 3.8.5 | "Bug fixes" | Sem detalhe publicado; não dá para atribuir efeito. |

## Parâmetros

Os 18 parâmetros que o HashPilot passa ao minerador continuam no `Parameters` atual, sem mudança de nome: `--disable-cpu`, `--disable-msr-tweaks`, `--disable-worker-watchdog`, `--disable-gpu-amd`, `--disable-gpu-intel`, `--algorithm-gpu`, `--gpu-id`, `--pool`, `--wallet`, `--worker`, `--gpu-off-temperature`, `--log-file`, `--gpu-cclock0`, `--gpu-coffset0`, `--gpu-mclock0`, `--gpu-fan0`, `--gpu-plimit0` e `--gpu-reset-oc`.

A mudança desde o fim de outubro é a documentação completa do **afinador nativo** (`--gpu-power-tuner`, `--gpu-power-tuner-loss` por GPU e `--gpu-power-tuner-reset`). Ele continua experimental e exige clock travado. Avisa que o ajuste leva de 5 a 20 minutos, reinicia o minerador e pode exigir reiniciar o computador. Também pede que nenhuma outra ferramenta mude clocks enquanto ajusta.

Isso confirma a decisão da 3.8.4: **o afinador nativo fica desligado**. Ligar os dois faria o minerador e o supervisor do HashPilot disputarem a mesma GPU.

## Hashes publicados

| Pacote | SHA-256 exibido pelo GitHub | MD5 do texto da release | Conferência |
|---|---|---|---|
| `SRBMiner-Multi-3-8-6-win64.zip` | `d44aa90bf857e526805c95e153b8c089c2188dae5ba0c4fad83d516168fb424b` | `7fa435946e7d9b3229307ee61a97290d` | Os dois conferem com o pacote baixado em 08/11/2026 |
| `SRBMiner-Multi-3-8-5-win64.zip` | `10395ce7190af40d1a5ac524962f03e0a74af173a0c715645bea254af6545982` | `1f1509095ad8500d81ce05994b629123` | Não baixado: a 3.8.5 não entra no catálogo |

São dois canais independentes: o digest que o GitHub calcula e o MD5 que o autor escreve na release. Um pacote só entra no catálogo quando os dois conferem.

## Inclusão no catálogo

O pacote oficial de 08/11/2026 (28.669.814 bytes) foi baixado da release e inspecionado sem extrair nem executar nada:

| Campo | Valor |
|---|---|
| SHA-256 do ZIP | `d44aa90bf857e526805c95e153b8c089c2188dae5ba0c4fad83d516168fb424b` |
| SHA-256 do `SRBMiner-MULTI.exe` (28.849.664 bytes) | `d072fc7012fe1d56f27253d7e41931afd0e3df349fbcc947f2243151baa6ab29` |
| Caminho no pacote | `SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe` |

A 3.8.4 tinha mudado o executável para `.../Win/...`; a 3.8.6 voltou ao caminho padrão, então a entrada não precisa de `archive_member`. O executável é o único `SRBMiner-MULTI.exe` do pacote. O preparo real do atualizador (`miner_update._stage`, com o pacote local numa pasta temporária, sem executar nada) aceitou o pacote e conferiu os dois hashes; o teste foi feito em Linux, sem GPU.

A inclusão segue o padrão da 3.8.4:

- entrada em `miner_update.CATALOG`;
- versão e hashes em `Permissao-Defender.ps1`;
- versão em `Referencia-Comum.ps1`.

`test_reference_scripts.py` confere a coerência dos três. A 3.8.6 é selecionada pela atualização do minerador no aplicativo, que preserva a versão anterior para o retorno.

Para as próximas releases, `tools/inspect_miner_release.py <versão> --archive-sha256 <hex> --md5 <hex>` faz o mesmo levantamento e imprime a linha pronta do catálogo.

## Plano de teste na RTX 3070

A 3.8.6 só vira a versão recomendada depois de um A/B na placa:

- **Mesmas condições:** núcleo de 1725 MHz (o último degrau validado), memória a 5001 MHz (os 405 MHz falharam na 3.8.4), mesma pool, mesma carteira e ventoinhas em 100%. A versão é a única variável.
- **O que comparar** contra a 3.8.4 na mesma sessão:
  - TH/s e watts médios e a razão TH/s/W;
  - temperatura e clock efetivo;
  - shares aceitas, rejeitadas e erros de hardware;
  - pelo menos três amostras após o aquecimento.
- **Critério:** a melhora prometida é de eficiência. Watts menores com o mesmo TH/s já contam como ganho, porque dão folga térmica ao supervisor para subir o núcleo. Se houver perda de TH/s, rejeições ou instabilidade, volte à 3.8.4 pela seleção de versão (o pacote anterior é preservado).

Até lá, nenhum número de ganho deve ser publicado para a 3.8.6.
