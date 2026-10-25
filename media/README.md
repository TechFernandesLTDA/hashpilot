# Capturas e demonstração do HashPilot

[Vídeo MP4 de 44 segundos](hashpilot-demo.mp4) · [Legendas em inglês](hashpilot-demo.en.srt) · [Manifesto das capturas](capture-manifest.json)

As imagens e os segmentos de vídeo capturam a interface Tkinter real do projeto. Uma janela isolada renderiza snapshots locais atualizados do monitor, o cache financeiro e os gráficos recuperados do SQLite em modo somente leitura. Não foram criados valores fictícios de sensores, hashrate ou receitas. A janela de captura não iniciou coletores, mineradores ou comandos de ajuste de GPU; escritas temporárias da UI ficaram fora do perfil real.

O vídeo foi editado a partir dessas capturas e contém cartões explicativos de instalação e resultados. O cartão do comando PowerShell é uma instrução de instalação, não uma gravação de terminal executando o instalador. A tela de configuração foi fechada sem salvar. Os botões de benchmark no renderizador não disparam novos testes; o estado mostrado vem do agente do aplicativo ativo.

Os valores exibidos são uma fotografia datada da unidade do autor. Créditos financeiros são históricos parciais e equivalentes à cotação consultada naquele momento, não ganhos garantidos para quem instala. A medição agregada de 15 minutos tem [relatório próprio](../docs/performance/rtx3070.md).

| Arquivo | Conteúdo |
|---|---|
| [overview.png](screenshots/overview.png) | Visão geral e pagamento estimado |
| [gpu-mining.png](screenshots/gpu-mining.png) | Sensores, gráficos e eventos reais |
| [earnings.png](screenshots/earnings.png) | Créditos e equivalentes monetários |
| [onboarding.png](screenshots/onboarding.png) | Carteira editável e opções de instalação |
| [benchmark.png](screenshots/benchmark.png) | Estado real do benchmark e consultor local |

Reprodução no Windows com FFmpeg instalado:

```powershell
mise exec -- python tools/capture_public_demo.py --ffmpeg C:\caminho\ffmpeg.exe
mise exec -- python tools/build_demo_video.py --ffmpeg C:\caminho\ffmpeg.exe
```

FFmpeg e fontes do Windows são usados localmente, sem redistribuir seus binários ou arquivos de fonte. Os segmentos intermediários ficam em `media/segments/` e não são versionados. O manifesto registra os títulos e horários das capturas; seus caminhos de segmentos referem-se a esses intermediários reproduzíveis. Capturas e vídeo preservam a identidade visual conforme [NOTICE](../NOTICE).
