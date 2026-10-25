# Backup, fork e transferência entre computadores Windows

Este repositório público inclui o endereço **público do autor** em `config.py` e `settings.example.json`, o código, a identidade visual oficial, os scripts usados na RTX 3070 e um snapshot autorizado do histórico em `portable-data`. Manter a carteira padrão direciona os pagamentos ao autor; o assistente permite trocar o endereço de recebimento.

O snapshot contém o banco SQLite íntegro, as amostras e eventos já coletados, os dados financeiros, as configurações públicas e os logs da mineração. É uma fotografia no instante registrado em `portable-data/backup-manifest.json`. Novas amostras continuam sendo salvas localmente; atualizar o GitHub exige uma nova exportação e um commit.

Não inclui frases de recuperação, chaves privadas ou arquivos internos da Pearl Wallet. Os instaladores de terceiros são obtidos pelas URLs oficiais e hashes em `mining/windows/downloads.json`, inclusive o instalador da carteira que ultrapassa o limite de100MB do GitHub.

## Primeiro uso

Os lançadores restauram o snapshot somente quando o perfil local ainda não tem banco de histórico. Um banco existente nunca é substituído automaticamente. A restauração preserva os dados locais já coletados; não mescla dois bancos diferentes.

O HashPilot suporta somente Windows 10/11 x64. Os dados ficam em `%LOCALAPPDATA%\PearlMonitor`. Para personalizar carteira, pool ou pasta dos logs, use o assistente ou edite `settings.json` no diretório local, tendo `settings.example.json` como referência.

Para transferir dados entre dois computadores Windows, gere um backup consistente pelo exportador abaixo. No destino, preserve qualquer banco já existente antes de importar. A restauração automática atende apenas perfis sem banco/cache local: não mescla históricos. Revise os caminhos de logs e a GPU detectada no destino; resultados e ajustes da unidade de origem não validam outra placa.

## Atualizar o backup versionado

Com o monitor aberto ou fechado, execute na raiz do projeto:

```text
mise exec -- python tools/export_portable_snapshot.py
```

O exportador usa a API online de backup SQLite para criar um banco consistente sem copiar arquivos WAL/SHM abertos. Antes do commit, confira as alterações de `portable-data`: uma nova exportação pode incluir endereços, histórico financeiro e caminhos locais de quem a gerou. O snapshot do autor foi autorizado para publicação; isso não autoriza publicar dados de outros usuários.

Para instalar no Windows, siga o [guia de instalação](docs/installation.md). O [índice de documentação](docs/index.md) reúne arquitetura, pagamentos e contribuição.
