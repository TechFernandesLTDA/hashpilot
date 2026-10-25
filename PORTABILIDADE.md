# Backup, fork e transferência entre computadores Windows

Este repositório público inclui o endereço **público do autor** em `config.py` e `settings.example.json`, o código, a identidade visual oficial, os scripts usados na RTX 3070 e um snapshot autorizado do histórico em `portable-data`. Manter a carteira padrão direciona os pagamentos ao autor; o assistente permite trocar o endereço de recebimento.

O snapshot contém o banco SQLite íntegro, as amostras e eventos já coletados, os dados financeiros, as configurações públicas e os logs da mineração. É uma fotografia no instante registrado em `portable-data/backup-manifest.json`. Novas amostras continuam sendo salvas localmente; atualizar o GitHub exige uma nova exportação e um commit.

Não inclui frases de recuperação, chaves privadas ou arquivos internos da Pearl Wallet. Os instaladores de terceiros são obtidos pelas URLs oficiais e hashes em `mining/windows/downloads.json`, inclusive o instalador da carteira que ultrapassa o limite de100MB do GitHub.

## Primeiro uso

Os lançadores restauram o snapshot somente quando o perfil local ainda não tem banco de histórico. Um banco existente nunca é substituído automaticamente. A restauração preserva os dados locais já coletados; não mescla dois bancos diferentes.

O HashPilot suporta somente Windows 10/11 x64. Os dados ficam em `%LOCALAPPDATA%\PearlMonitor`. Para personalizar carteira, pool ou pasta dos logs, use o assistente ou edite `settings.json` no diretório local, tendo `settings.example.json` como referência.

Para transferir dados entre dois computadores Windows, gere um backup consistente pelo exportador abaixo. No destino, preserve qualquer banco já existente antes de importar. A restauração automática atende apenas perfis sem banco/cache local: não mescla históricos. Revise os caminhos de logs e a GPU detectada no destino; resultados e ajustes da unidade de origem não validam outra placa.

## Rigs e identidades físicas

O painel atual acompanha até seis GPUs por UUID. Preserve `settings.json` com `gpu_settings`, o banco `history.sqlite3` com as tabelas `gpu_devices`/`gpu_telemetry` e os diretórios individuais `LOG_DIR/gpus/<UUID>/`, incluindo cada `gpu-agent.sqlite3`. Um UUID de outra máquina não deve ser substituído pelo índice de uma placa apenas porque ambas aparecem como `GPU0` ou têm o mesmo nome.

`rig-manager.json` e `rig-requests/` são estado operacional, não autorização para executar os mesmos PIDs no destino. Não copie uma sessão ativa como se seus processos continuassem válidos em outro computador. Instale o aplicativo, revise UUIDs, habilitação e carteiras e inicie explicitamente o serviço no destino. O histórico legado de uma placa é mantido; a migração não deve apagar registros nem atribuí-los a outra unidade.

O exportador inclui os dados que sua versão conhece. Antes de uma transferência multiGPU, confira o manifesto e preserve separadamente qualquer diretório por UUID que não esteja listado. Não presuma que um snapshot antigo contém bancos ou logs criados em versões posteriores. [Mapa completo dos arquivos](docs/multi-gpu.md#arquivos-e-histórico).

## Atualizar o backup versionado

Com o monitor aberto ou fechado, execute na raiz do projeto:

```text
mise exec -- python tools/export_portable_snapshot.py
```

O exportador usa a API online de backup SQLite para criar um banco consistente sem copiar arquivos WAL/SHM abertos. Antes do commit, confira as alterações de `portable-data`: uma nova exportação pode incluir endereços, histórico financeiro e caminhos locais de quem a gerou. O snapshot do autor foi autorizado para publicação; isso não autoriza publicar dados de outros usuários.

Para instalar no Windows, siga o [guia de instalação](docs/installation.md). O [índice de documentação](docs/index.md) reúne arquitetura, pagamentos e contribuição.
