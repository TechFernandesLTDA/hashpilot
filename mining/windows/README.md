# Mineração NVIDIA no Windows

O assistente do HashPilot permite instalar o minerador separadamente do monitor e escolher a carteira pública. **A carteira inicial é a do autor**; mantê-la direciona pagamentos para ele. Instalar o painel não inicia mineração automaticamente.

## Iniciador atual

`Instalar-Minerador.ps1` baixa o SRBMiner 3.8.0 da fonte oficial, confere o SHA256 e instala em `%LOCALAPPDATA%\Mining\Pearl`. A detecção exige NVIDIA GeForce GTX/RTX pelo driver. Isso não garante que toda GPU catalogada execute Pearl nessa versão do minerador.

`Iniciar-Pearl.ps1` usa **GPU0** e os ajustes atuais do driver. Não copia clocks, offset, memória ou ventoinhas da RTX 3070 para outros modelos. Depois de iniciar o minerador, inicia `gpu_agent.py --control` com baseline de **300 segundos**. O supervisor exige elevação e posse exclusiva: não deve concorrer com outro controlador. Consulte [agente NVIDIA](../../docs/gpu-agent.md).

O agente separa capacidades e medições por UUID. A interface atual continua coletando GPU0; não use hashrate agregado como benchmark de outra placa. Novo benchmark manual usa a GPU identificada, e os ajustes dependem dos limites informados pelo driver e das medições reais. Ollama na CPU é opcional; a política adaptativa não exige API paga.

O [catálogo de 158 modelos](../../docs/gpu-catalog.md) documenta fontes primárias NVIDIA. Não contém 158 presets validados nem certifica estabilidade, suporte do driver ou compatibilidade com Pearl.

## Referência RTX 3070 preservada

`Iniciar-Referencia-RTX3070.ps1` e `Parar-Referencia-RTX3070.ps1` preservam o fluxo histórico específico da unidade do autor e seu `thermal_controller.py`. Não são iniciadores genéricos. Uma mineração já ativa não é migrada ou reiniciada apenas por atualizar o aplicativo.

Perfil histórico medido: core 1740 MHz, offset +150 MHz, memória 5001 MHz, ventoinhas 100%, limite de fábrica 240 W; consumo aproximado 158 W, hashrate 76–79 TH/s e temperatura 74–75 °C. Esses resultados não garantem estabilidade em outra unidade ou ambiente. A pausa histórica em 78 °C foi substituída pelo supervisor adaptativo; consulte [controle térmico](../../CONTROLE-TERMICO.md) e `portable-data/miner-logs/validated-profile.json` na raiz do repositório.

## Downloads e dados

Os binários de terceiros não são versionados. `downloads.json` registra URLs oficiais e hashes esperados. O monitor não precisa de seed, chave privada ou arquivos internos da Pearl Wallet; não publique esses dados.

Os scripts de mineração e controle deste diretório são Windows/NVIDIA. Não configuram mineração em GPU Apple. Código sob [GPL-3.0-only](../../LICENSE); marca e componentes de terceiros seguem seus avisos em [NOTICE](../../NOTICE).
