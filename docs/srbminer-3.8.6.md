# SRBMiner 3.8.6 na RTX 3070 — revisão de 8 de novembro de 2026

Fontes primárias: [release 3.8.6](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.6), [release 3.8.5](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.5) e [parâmetros](https://github.com/doktor83/SRBMiner-Multi/blob/master/Parameters) no commit `659c275` (07/11/2026). As duas releases saíram depois da [revisão da 3.8.4](srbminer-3.8.4.md).

**Estado: revisada, ainda não incluída no catálogo.** Falta o SHA-256 do executável, que só se obtém abrindo o pacote. Veja [O que falta para instalar](#o-que-falta-para-instalar).

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

| Pacote | SHA-256 exibido pelo GitHub | MD5 do texto da release |
|---|---|---|
| `SRBMiner-Multi-3-8-6-win64.zip` | `d44aa90bf857e526805c95e153b8c089c2188dae5ba0c4fad83d516168fb424b` | `7fa435946e7d9b3229307ee61a97290d` |
| `SRBMiner-Multi-3-8-5-win64.zip` | `10395ce7190af40d1a5ac524962f03e0a74af173a0c715645bea254af6545982` | `1f1509095ad8500d81ce05994b629123` |

São dois canais independentes: o digest que o GitHub calcula e o MD5 que o autor escreve na release. Os valores acima foram lidos da página da release e ainda não foram conferidos contra o pacote baixado, porque esta revisão foi feita num ambiente sem acesso aos downloads. A conferência é o primeiro passo abaixo.

## O que falta para instalar

O catálogo (`miner_update.CATALOG`) fixa três coisas: o SHA-256 do ZIP, o SHA-256 do executável e o caminho exato do executável dentro do pacote. A 3.8.4 mudou o layout (`.../Win/...`), então o caminho não pode ser presumido. No Windows, na raiz do repositório:

```text
mise exec -- python tools/inspect_miner_release.py 3.8.6 --archive-sha256 d44aa90bf857e526805c95e153b8c089c2188dae5ba0c4fad83d516168fb424b --md5 7fa435946e7d9b3229307ee61a97290d
```

A ferramenta baixa o ZIP oficial e recusa se o SHA-256 ou o MD5 divergirem. Localiza o único `SRBMiner-MULTI.exe` e calcula o hash dele **sem extrair nem executar nada**. A saída traz a linha pronta do catálogo. Com ela, a inclusão segue o padrão da 3.8.4:

- entrada em `miner_update.CATALOG`;
- versão e hashes em `Permissao-Defender.ps1`;
- versão em `Referencia-Comum.ps1`.

`test_reference_scripts.py` confere a coerência dos três. A versão padrão do instalador continua a 3.8.0; a 3.8.6 é selecionada pela atualização do minerador no aplicativo, que preserva a versão anterior.

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
