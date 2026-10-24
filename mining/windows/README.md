# Mineração de referência no Windows

Scripts usados e resultados medidos da RTX 3070 deste PC. Não controlam o hardware do Mac.

`Instalar-Minerador.ps1` baixa o SRBMiner 3.8.0 do projeto oficial, confere o SHA256 e instala em `%LOCALAPPDATA%\Mining\Pearl`. Só aceita uma RTX 3070 e não começa a minerar automaticamente. Os scripts históricos de ajuste são fornecidos para auditoria; não precisam ser executados para usar o monitor.

Perfil selecionado: core1740MHz,offset+150MHz,memória5001MHz,ventoinhas100%,limite de fábrica240W,pausa térmica78°C. Consumo real medido ~158W,hashrate76–79TH/s,temperatura74–75°C. Este resultado não garante estabilidade em outra unidade/ambiente. Ver `portable-data/miner-logs/validated-profile.json`.

Os binários de terceiros não são versionados. `downloads.json` guarda as URLs oficiais e hashes dos instaladores usados; o instalador da carteira tem mais de100MB. Nenhuma seed,chaveprivada ou arquivo interno da Pearl Wallet faz parte deste repositório.
