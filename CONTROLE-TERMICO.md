# Controle térmico adaptativo da RTX 3070

78 °C não é o máximo da placa: a especificação NVIDIA informa 93 °C, e o driver desta unidade informa alvo 83 °C, máximo operacional 93 °C, redução de proteção 95 °C e desligamento 98 °C. Esses são limites distintos; o controlador trabalha antes deles. Fonte: https://www.nvidia.com/en-us/geforce/graphics-cards/30-series/rtx-3070-3070ti/

O corte original do SRBMiner em 78 °C interrompia toda a mineração e aguardava resfriar 10 °C. Após aquecimento prolongado, isso ocasionou uma pausa de 10 s. O teste inicial curto não havia capturado essa condição.

Agora o supervisor elevado executa `thermal_controller.py` enquanto o processo correto do minerador existir. A política pura em `thermal.py` recebe amostras a cada 2 s:

| Condição | Ação |
|---|---|
| A partir de 78 °C | Reduz 15 MHz, com intervalo mínimo de 6 s |
| A partir de 77 °C e subida rápida ≥0,4 °C/s | Antecipa redução de 30 MHz |
| A partir de 80 °C | Reduz 60 MHz, a cada 4 s |
| A partir de 83 °C | Recua imediatamente para 1500 MHz |
| Até 75 °C durante 60 s contínuos | Recupera 15 MHz a cada 20 s |
| Demais condições | Mantém o clock, evitando oscilações |

Faixa de 1500–1800 MHz, partida em 1740 MHz. Offset +150 MHz, memória 5001 MHz, ventoinhas 100% e limite de fábrica 240 W são preservados. Não existe sobretensão manual. O SRBMiner mantém pausa de emergência em 84 °C; não é a ação normal de controle.

Três erros consecutivos de sensor/controle acionam 1500 MHz / 150 W. A potência só retorna após três leituras válidas; o clock volta gradualmente. Se o controlador encerrar com erro fatal, o supervisor encerra o processo do minerador e restaura a GPU. O recurso não altera as proteções do driver.

Estado atual em `logs/thermal-control.json`; mudanças e erros em `thermal-control-events.jsonl`. O HashPilot mostra estado, idade da amostra e clock atual/alvo. Eventos térmicos também ficam no histórico permanente do monitor.

O controlador e os comandos `nvidia-smi` de escrita são específicos do Windows com esta RTX 3070. O aplicativo está disponível somente para Windows.

Teste da política sem alterar hardware: `python -m unittest -v test_thermal.py`. Diagnóstico real sem escritaGPU: `python thermal_controller.py --miner-pid PID_DO_MINERADOR --status-dir DIRETORIO_DE_QA --dry-run --samples 2`.
