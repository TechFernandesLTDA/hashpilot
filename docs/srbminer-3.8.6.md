# SRBMiner 3.8.6 na RTX 3070 — revisão de 8 de novembro de 2026

Fontes primárias: [release 3.8.6](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.6), [release 3.8.5](https://github.com/doktor83/SRBMiner-Multi/releases/tag/3.8.5) e [parâmetros](https://github.com/doktor83/SRBMiner-Multi/blob/master/Parameters) no commit `659c275` (07/11/2026). As duas releases saíram depois da [revisão da 3.8.4](srbminer-3.8.4.md).

**Estado: incluída no catálogo verificado, não recomendada.** Os hashes foram conferidos contra o pacote oficial (ver [Inclusão no catálogo](#inclusão-no-catálogo)). A versão padrão do instalador continua a 3.8.0. O [A/B na RTX 3070](#resultado-do-ab-na-rtx-3070-teste-real-08112026) de 08/11/2026 não mostrou ganho de eficiência e teve rejeições: a 3.8.4 continua a versão ativa.

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

Para as próximas releases, o levantamento é um subcomando da CLI em Rust. A partir de `rust/`:

```text
cargo run --release -p hashpilot-cli -- release inspect <versão> --archive-sha256 <hex> --md5 <hex> [--file <zip>]
```

Ela recusa argumentos malformados antes de baixar e recusa o pacote se o SHA-256 ou o MD5 divergirem. Depois localiza o único `SRBMiner-MULTI.exe` e imprime a linha pronta do catálogo, sem extrair nem executar nada. Sem `--file`, baixa com o `curl` do Windows para a pasta temporária e apaga o arquivo ao terminar. Os valores da 3.8.6 acima saíram dela e foram reproduzidos, sobre os mesmos bytes, pela versão Python anterior da ferramenta.

## Plano de teste na RTX 3070

A 3.8.6 só vira a versão recomendada depois de um A/B na placa:

- **Mesmas condições:** núcleo de 1725 MHz (o último degrau validado), memória a 5001 MHz (os 405 MHz falharam na 3.8.4), mesma pool, mesma carteira e ventoinhas em 100%. A versão é a única variável.
- **O que comparar** contra a 3.8.4 na mesma sessão:
  - TH/s e watts médios e a razão TH/s/W;
  - temperatura e clock efetivo;
  - shares aceitas, rejeitadas e erros de hardware;
  - pelo menos três amostras após o aquecimento.
- **Critério:** a melhora prometida é de eficiência. Watts menores com o mesmo TH/s já contam como ganho, porque dão folga térmica ao supervisor para subir o núcleo. Se houver perda de TH/s, rejeições ou instabilidade, volte à 3.8.4 pela seleção de versão (o pacote anterior é preservado).

## Resultado do A/B na RTX 3070 (teste real, 08/11/2026)

**Conclusão: a 3.8.6 não trouxe ganho de eficiência nesta placa e teve rejeições. A 3.8.4 continua ativa; a 3.8.6 não é recomendada.** A 3.8.6 segue no catálogo e instalada, para um novo teste.

Placa de referência (UUID `GPU-bafb0ad5…fc75`), driver 621.14, pool `prl.kryptex.network:7048`, mesma carteira, rig na `main` em `eb9c946`. A versão foi trocada pela ativação do serviço do rig (`miner_update.request_activation`), que preservou o outro pacote.

### O preset fixo do plano não se sustenta

A primeira tentativa usou o preset do plano: núcleo de 1725 MHz, memória a 5001 MHz, ventoinha a 100% e PL 240, sem o controle por temperatura. Às 20:25:28 o minerador iniciou; às 20:25:36 a placa já estava em 82 °C e o teste foi interrompido. É o mesmo comportamento que causava o laço de cortes em 83 °C. Por isso não dá para comparar as versões com o núcleo fixo em 1725 MHz.

### Condições usadas

As duas versões rodaram em sequência, com o controle por temperatura-alvo em 78 °C, a memória em 810 MHz (o melhor clock escolhido pelo explorador; empate de hashrate com 5001 e 6801 a menos potência) e a ventoinha ajustada pelo minerador. Com a temperatura igualada, ganho de eficiência apareceria como mais TH/s ou menos watts. As amostras são as linhas do log do SRBMiner depois do aquecimento (temperatura estável em 76–78 °C).

| Versão | Amostras estáveis (TH/s) | Potência | TH/s/W | Temperatura | Clock efetivo | Aceitas / rejeitadas / HW |
|---|---|---|---|---|---|---|
| 3.8.4 | 78,32 · 78,44 · 78,37 (média 78,38) | 167,0–167,8 W (≈167,5) | ≈0,468 | 76 °C | 1695 MHz | 3 / 0 / 0 em ~10 min |
| 3.8.6 | 78,68 · 78,98 · 78,69 (média 78,78) | 171,0–172,4 W (≈171,9) | ≈0,458 | 77–78 °C | 1710 MHz | 3 / 2 (*stale share*) / 0 em ~10 min |

- **Hashrate:** +0,5% na 3.8.6, explicado pelo controle ter subido o núcleo um degrau (1695 → 1710 MHz).
- **Eficiência:** cerca de 2% pior na 3.8.6 (≈4,4 W a mais), o contrário do que o changelog promete.
- **Rejeições:** 2 de 5 shares da 3.8.6 foram *stale* (solução enviada depois da troca de trabalho); a 3.8.4 não teve nenhuma. A amostra é pequena, mas pelo critério acima já basta para voltar à 3.8.4.

Limites honestos: cerca de 10 minutos por versão, uma sessão, sem repetição em ordem inversa. A diferença de potência está perto do ruído térmico; a de rejeições, não, mas tem poucas shares. Um novo A/B mais longo (≥ 30 min por versão, ordem alternada) pode reabrir a decisão.

Revisão seguinte: [SRBMiner 3.8.7](srbminer-3.8.7.md).
