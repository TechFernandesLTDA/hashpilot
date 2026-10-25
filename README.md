# HashPilot — Tech Fernandes

![Tech Fernandes](assets/brand/tech-fernandes_github_banner-readme_escuro_1280x400.png)

Aplicativo Windows local para acompanhar a RTX 3070 e o SRBMiner em tempo real. Usa Python/Tkinter e biblioteca padrão, sem pacotes adicionais ou servidor. Consultas públicas à Kryptex e ao CoinGecko alimentam a aba de rendimentos.

Abra **HashPilot** no menu Iniciar, ou execute `python monitor.py` nesta pasta usando o Python gerenciado pelo mise. As configurações locais ficam em `%LOCALAPPDATA%\PearlMonitor\settings.json`; use `settings.example.json` como referência. O diretório legado `PearlMonitor` é mantido para preservar banco e histórico existentes.

## Organização da interface

O painel principal tem três abas, com grids e tipografia adaptados à área útil da janela:

1. **Visão geral:** indicadores principais, carteira de recebimento, saldo e progresso para o próximo pagamento.
2. **GPU e mineração:** telemetria, controle térmico, quatro gráficos e atividade recente do minerador.
3. **Rendimentos:** tabela em PRL, reais, dólares e BTC, períodos observados, projeções e gráfico financeiro.

As abas não usam barras de rolagem; o layout foi preparado para HD a 4K, respeitando a área útil do sistema e o dimensionamento da janela. Valores em USD/BTC são equivalentes à cotação atual. O pagamento da pool continua sendo em PRL na carteira configurada.

Veja [pagamentos, carteira e previsão de recebimento](PAGAMENTOS.md).

## Bandeja, resumo e console

- Minimizar ou clicar no **X** recolhe o painel para a bandeja do Windows, mantendo coleta e histórico ativos.
- O ícone oferece **Abrir painel**, **Resumo**, **Console resumido** e **Sair**. Duplo clique abre o resumo compacto.
- O resumo mostra hashrate, temperatura, consumo, clocks, ventoinhas, shares, ganhos observados, acumulado, projeções, cotação e histórico.
- **Sair** encerra somente o HashPilot, remove o ícone e aguarda a gravação do histórico. Não encerra o minerador.
- `python monitor.py --summary` abre o resumo; se o monitor estiver ativo, reabre essa sessão sem coletores duplicados.
- `python monitor.py --console` mostra um resumo atualizado no mesmo lugar a cada 2 segundos. Lê os snapshots do monitor, sem novo coletor/gravador; informa se o registro estiver desatualizado. **Ctrl+C** fecha apenas o console.
- `Abrir-Monitor.ps1` aceita `-Resumo` e `-Console` para atalhos. O ícone está em `assets/brand/hashpilot.ico`.

Ativos e tokens seguem a identidade oficial. Consulte [assets/brand/ORIGEM.md](assets/brand/ORIGEM.md).

## macOS

Com Python/Tkinter disponível pelo mise, execute `zsh Abrir-HashPilot.command` (ou `mise exec -- python monitor.py`). O launcher é relativo e pode acompanhar um fork em qualquer pasta. O caminho local de dados é definido por `config.py` para cada sistema; preserve/restaure o banco existente ao migrar entre máquinas.

No primeiro clone, o aplicativo restaura o snapshot incluído em `portable-data/` apenas se o banco/cache local ainda não existir. Dados locais existentes nunca são sobrescritos. O repositório privado inclui o endereço público e o snapshot autorizado do histórico; não contém chaves privadas ou frase de recuperação.

Painel, resumo, cotação, pool e histórico usam a mesma base Python. `nvidia-smi` só é usado se estiver disponível; em MacBook com GPU Apple, os sensores NVIDIA ficam explicitamente indisponíveis. A consulta de processo usa `pgrep` no macOS, sem comandos de alteração de hardware. Isso não configura nem promete mineração em Apple Silicon.

A bandeja nativa é exclusiva do Windows. No macOS, recolher mantém a janela minimizada no Dock; o botão Console abre um resumo textual na própria interface, e `--console` também funciona ao executar pelo Terminal. A compatibilidade de código foi revisada, mas não houve validação em um Mac físico nesta sessão.

- Sensores NVIDIA a cada 2 segundos: temperatura, potência, limite, clocks, utilização, VRAM e ventoinhas.
- Hashrate, médias, shares, rejeições e erros do log mais recente do SRBMiner.
- Quatro gráficos com até 15 minutos de histórico. Hashrate usa o intervalo real do minerador (normalmente 30–90 segundos), aceita medições atrasadas e mostra a idade da última amostra no gráfico.
- Indica ausência do processo, reconexão e hashrate com mais de 180 segundos sem atualização; a idade da taxa continua visível.
- Somente leitura: fechar o aplicativo não encerra a mineração nem modifica clocks.

Logs lidos: `%LOCALAPPDATA%\Mining\Pearl\logs\*.log`. Snapshot de diagnóstico: `%LOCALAPPDATA%\PearlMonitor\latest.json`.

Validação: `python monitor.py --snapshot` lê uma amostra real; `python monitor.py --smoke-test` abre a janela por sete segundos.

## Rendimentos em reais, dólares e BTC

A aba **Rendimentos** mostra créditos observados hoje, na semana (segunda-feira até agora, São Paulo) e na janela móvel dos últimos 30 dias, além do acumulado da pool e projeções para os próximos 7 e 30 dias.

Os valores realizados vêm da variação de **total pago + saldo atual da pool**, incluindo saldo não confirmado. Isso impede contar pagamentos duas vezes quando uma transferência reduz o saldo e aumenta o total pago. Não é uma contagem baseada em shares ou hashrate. Recompensas pendentes ainda podem sofrer ajustes da pool.

O histórico local começa na primeira amostra do monitor. Um período sem cobertura completa é marcado **parcial desde...**; antes de duas amostras, exibe **N/D**. A pool atualmente retorna um gráfico de recompensas vazio para esta carteira, portanto o aplicativo não inventa rendimentos anteriores ao acompanhamento. O acumulado da pool é exibido separadamente e inclui créditos anteriores à primeira amostra.

**Os equivalentes em reais, dólares e BTC usam as cotações atuais** da Pearl e do Bitcoin e são reavaliados quando ela muda; não representam o câmbio histórico de um saque. São consultados dados públicos, sem credenciais, a cada 60 segundos. Falhas conservam o cache com aviso e data; valores ausentes são N/D.

A projeção é uma estimativa por rede: `hashrate / hashrate da rede × recompensa do bloco × 86400 / tempo de bloco × (1 − taxa da pool) × (1 − 0,02)`. Os 2% são a taxa pearlhash do SRBMiner exibida no log. Ela pressupõe 24 h/dia, o hashrate médio de 1 minuto atual e preço/dificuldade/recompensa constantes. Não promete preço futuro nem é crédito realizado.

Fontes verificadas:

- [Documentação pública Kryptex](https://pool.kryptex.com/api) e [OpenAPI](https://pool.kryptex.com/openapi.yaml): `prl/api/v1/miner/balance/{carteira}`, `miner/payouts/{carteira}/stats`, `miner/reward-chart/{carteira}` e `pool/info`.
- [Pearl no CoinGecko](https://www.coingecko.com/en/coins/pearl-2): API ID **pearl-2**, site oficial **pearlresearch.ai**. Não confundir com Perle, outro ativo de ticker PRL.
- [Endpoint de cotação](https://api.coingecko.com/api/v3/simple/price?ids=pearl-2,bitcoin&vs_currencies=brl,usd&include_last_updated_at=true): preço BRL/USD e instante `last_updated_at`.

Persistência: `%LOCALAPPDATA%\PearlMonitor\finance-cache.json` mantém uma janela de 35 dias para cálculo rápido e cache das fontes; `finance-latest.json` contém o resumo calculado. O histórico completo permanece no banco descrito abaixo. Não há chaves privadas ou senhas nesses arquivos.

Testes das regras monetárias: `python -m unittest -v test_finance.py`.

## Histórico permanente e reinícios

O arquivo `%LOCALAPPDATA%\PearlMonitor\history.sqlite3` arquiva permanentemente as amostras de sensores, hashrate, médias, shares, erros, estado do minerador, mudanças de estado, novas linhas de log e dados financeiros, separados por carteira e sessão do aplicativo. **Não existe expurgo automático de registros antigos.** Os gráficos mostram uma janela de 15 minutos, mas isso não apaga os dados anteriores do banco.

Ao abrir, o aplicativo restaura as séries dos gráficos, o histórico financeiro, a aba e a posição/tamanho da janela. Os caches anteriores e as medições existentes nos logs são migrados sem duplicação; pontos recuperados de logs conservam a origem `imported_miner_log`, sem inventar medições NVIDIA que não foram coletadas. O banco não cria amostras dos períodos em que o monitor ficou fechado.

A escrita usa SQLite em modo **WAL**, `synchronous=FULL` e transações curtas em uma thread separada. Se o banco estiver ocupado, a fila tenta novamente e mostra a falha; a interface continua respondendo. O fechamento normal espera os dados pendentes serem gravados. Ao encerrar o processo à força, todos os commits concluídos continuam no banco. Linhas de log são deduplicadas por hash, e as respostas financeiras são arquivadas no ciclo remoto de 60 segundos, sem copiar o log inteiro em cada amostra de sensores.

`latest.json` expõe o caminho do banco, sessão, contagens por tabela, último commit, fila pendente e quantidade de pontos restaurados. Testes de durabilidade, recuperação, banco ocupado e isolamento por carteira: `python -m unittest -v test_history.py`.

O supervisor Windows agora ajusta o clock gradualmente conforme temperatura, em vez de pausar normalmente a mineração. Veja [controle térmico adaptativo](CONTROLE-TERMICO.md).
