# Capturas isoladas da v0.5.3

[Visão geral](overview.png) · [GPU e mineração](gpu-mining.png) · [Manifesto](capture-manifest.json)

As imagens mostram a interface v0.5.3 com um snapshot de telemetria de uma RTX 3070 real. São fotografias de um momento, não um benchmark independente nem garantia de desempenho. O renderizador alterou explicitamente a apresentação: carteira, identificadores, saldo, pagamento, previsões, informações da pool e logs foram ocultados para privacidade. Não são capturas integrais inalteradas. O vídeo anterior permanece histórico da v0.1.0.

Para reproduzir no Windows: `mise exec -- python tools/capture_current_private.py`. O teste cria uma janela isolada e usa `PrintWindow` somente no handle dessa janela; não captura o desktop ou outras janelas, não envia entrada, não inicia coletores nem altera mineração ou GPU. O snapshot e os últimos 15 minutos de telemetria da GPU são lidos localmente; o SQLite é aberto somente para leitura, sem consulta financeira. As escritas do renderizador ficam em diretório temporário. Revise as imagens e o manifesto antes de publicar.
