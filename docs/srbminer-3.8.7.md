# SRBMiner 3.8.7 na RTX 3070 — revisão de 11 de novembro de 2026

Fontes primárias: [release 3.8.7](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.7), publicada em 11/11/2026, e [parâmetros](https://github.com/doktor83/SRBMiner-Multi/blob/master/Parameters) lidos no mesmo dia. Revisão anterior: [SRBMiner 3.8.6](srbminer-3.8.6.md).

**Estado: incluída no catálogo verificado, sem ganho medido.** Os hashes foram conferidos contra o pacote oficial (ver [Inclusão no catálogo](#inclusão-no-catálogo)). A versão padrão do instalador continua a 3.8.0. Nenhum número desta página é medição: o ganho anunciado ainda depende de um A/B na placa.

## O que mudou

| Item do changelog | Aplicação na RTX 3070 |
|---|---|
| "Minor efficiency improvements on algorithm 'pearlhash' for NVIDIA 3000, 4000 and 5000 GPUs" | **É a família da placa.** O próprio texto chama a melhora de pequena e fala de eficiência (TH/s por watt), não de hashrate. A 3.8.6 anunciou o mesmo e, no [A/B de 08/11/2026](srbminer-3.8.6.md#resultado-do-ab-na-rtx-3070-teste-real-08112026), não mostrou ganho nesta placa. |
| "Fixes to efficiency fine-tuner" | Não se aplica: o afinador nativo segue desligado (ver abaixo). |
| Novo `--gpu-power-tuner-value` | Aplica um valor opaco medido pelo afinador, sem nova varredura e sem exigir clock travado. Não usado pelo HashPilot. |
| Perda de hashrate padrão do afinador passou a 3% | Não se aplica com o afinador desligado. |
| "Bug fixes" | Sem detalhe publicado; não dá para atribuir efeito. |

## Parâmetros

Os 18 parâmetros que o HashPilot passa ao minerador continuam no `Parameters` atual, sem mudança de nome: `--disable-cpu`, `--disable-msr-tweaks`, `--disable-worker-watchdog`, `--disable-gpu-amd`, `--disable-gpu-intel`, `--algorithm-gpu`, `--gpu-id`, `--pool`, `--wallet`, `--worker`, `--gpu-off-temperature`, `--log-file`, `--gpu-cclock0`, `--gpu-coffset0`, `--gpu-mclock0`, `--gpu-fan0`, `--gpu-plimit0` e `--gpu-reset-oc`.

O afinador nativo agora tem quatro parâmetros: `--gpu-power-tuner`, `--gpu-power-tuner-loss`, `--gpu-power-tuner-reset` e o novo `--gpu-power-tuner-value`. **Ele continua desligado no HashPilot**, pelo mesmo motivo da 3.8.4 e da 3.8.6: o minerador e o supervisor térmico disputariam a mesma GPU.

O `--gpu-power-tuner-value` muda um ponto dessa conta e fica como hipótese, não como decisão:

- o valor é aplicado sem varredura e não exige clock travado, então em tese convive com o controle por temperatura-alvo;
- o valor vem de uma varredura feita com um overclock fixo. A release avisa que um valor ótimo para um overclock pode travar a GPU em outro, e o supervisor do HashPilot muda o núcleo o tempo todo;
- usá-lo exigiria uma varredura com o supervisor parado, um A/B do valor obtido e uma regra para suspendê-lo quando o clock sair da faixa medida. Nada disso existe hoje.

## Hashes publicados

| Pacote | SHA-256 exibido pelo GitHub | MD5 do texto da release | Conferência |
|---|---|---|---|
| `SRBMiner-Multi-3-8-7-win64.zip` | `cbfc6fa4daf22641e0f845922f8fbccff7ff1dc144af7316061bca06b76cfb1f` | `ff5f70ca61872ab350af3b49ceb028cb` | Os dois conferem com o pacote baixado em 11/11/2026 |

São dois canais independentes: o digest que o GitHub calcula e o MD5 que o autor escreve na release. Um pacote só entra no catálogo quando os dois conferem.

## Inclusão no catálogo

O pacote oficial de 11/11/2026 (28.849.670 bytes) foi baixado da release e inspecionado pela CLI em Rust (`hashpilot release inspect 3.8.7 --archive-sha256 … --md5 …`), no Windows da RTX 3070, sem extrair nem executar nada:

| Campo | Valor |
|---|---|
| SHA-256 do ZIP | `cbfc6fa4daf22641e0f845922f8fbccff7ff1dc144af7316061bca06b76cfb1f` |
| SHA-256 do `SRBMiner-MULTI.exe` (29.029.888 bytes) | `dde3740fd6ee9e7fd3832edd98c9fb838df2344ef05b5ecbcd87997dfb5d3348` |
| Caminho no pacote | Padrão (`SRBMiner-Multi-3-8-7/SRBMiner-MULTI.exe`); a entrada não precisa de `archive_member` |

A inclusão segue o padrão da 3.8.6, agora também no serviço em Rust:

- entrada em `miner_update.CATALOG`;
- a mesma entrada em `rust/hashpilot-rig/src/miner_install.rs` (o golden `rig_support` confere que os dois catálogos são idênticos);
- versão e hashes em `Permissao-Defender.ps1`;
- versão em `Referencia-Comum.ps1`.

`test_reference_scripts.py` confere a coerência dos scripts. A 3.8.7 é selecionada pela atualização do minerador no aplicativo, que preserva a versão anterior para o retorno e reverte sozinha se não houver hashrate válido em 180 s.

## Plano de teste na RTX 3070

A 3.8.7 só vira a versão recomendada depois de um A/B na placa, com as lições do teste da 3.8.6:

- **Controle por temperatura-alvo ligado**, não preset fixo: o núcleo fixo em 1725 MHz não se sustenta termicamente nesta placa.
- **Mesmas condições** nas duas versões: mesma temperatura-alvo, mesma memória, mesma pool e mesma carteira.
- **Pelo menos 30 minutos por versão, em ordem alternada.** O teste da 3.8.6 teve cerca de 10 minutos por versão e uma só ordem; a diferença de potência ficou perto do ruído térmico.
- **O que comparar:** TH/s e watts médios, a razão TH/s/W, temperatura, clock efetivo, shares aceitas e rejeitadas e erros de hardware.
- **Critério:** a melhora anunciada é de eficiência. Watts menores com o mesmo TH/s já contam como ganho. Perda de TH/s, rejeições ou instabilidade: voltar à versão anterior pela seleção de versão.
