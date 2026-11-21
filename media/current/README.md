# Capturas do visual atual (dados simulados)

[Visão geral](overview.png) · [GPU e mineração](gpu-mining.png) · [Manifesto](capture-manifest.json)

As imagens mostram o visual atual (barra lateral, tema grafite) preenchido com dados simulados de demonstração: uma GPU fictícia (`test_rig_ui.fixture(1)`), sem saldos nem previsões. Não são medições de hardware, benchmark nem garantia de desempenho. A carteira exibida é a padrão do autor, já pública neste repositório; nenhum dado local do dono (snapshot, histórico, cache financeiro ou logs) foi lido. A validação física documentada continua sendo a da [RTX 3070](../../docs/performance/rtx3070.md). O vídeo anterior permanece histórico da v0.1.0.

Como foram geradas no Windows: janela isolada do painel montada pelo `build_harness` de `test_responsive_ui.py`, com `LOCALAPPDATA` temporário, sem coletores, sem rede e sem acionar botões, em 1500×960 px com DPI nativo; `tools/capture_window.ps1` usa `PrintWindow` somente no handle dessa janela. Revise as imagens e o manifesto antes de publicar.
