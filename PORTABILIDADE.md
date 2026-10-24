# Dados portáteis e fork para o MacBook

Este repositório privado inclui o endereço **público** de recebimento em `config.py` e `settings.example.json`, o código completo, a identidade visual oficial, os scripts usados na RTX 3070 e um snapshot do histórico em `portable-data`.

O snapshot contém o banco SQLite íntegro, as amostras e eventos já coletados, os dados financeiros, as configurações públicas e os logs da mineração. É uma fotografia no instante registrado em `portable-data/backup-manifest.json`. Novas amostras continuam sendo salvas localmente; atualizar o GitHub exige uma nova exportação e um commit.

Não inclui frases de recuperação, chaves privadas ou arquivos internos da Pearl Wallet. Os instaladores de terceiros são obtidos pelas URLs oficiais e hashes em `mining/windows/downloads.json`, inclusive o instalador da carteira que ultrapassa o limite de100MB do GitHub.

## Primeiro uso

Os lançadores restauram o snapshot somente quando o perfil local ainda não tem banco de histórico. Um banco existente nunca é substituído automaticamente. A restauração preserva os dados locais já coletados; não mescla dois bancos diferentes.

Windows mantém os dados existentes em `%LOCALAPPDATA%\PearlMonitor`. No macOS o diretório é `~/Library/Application Support/HashPilot`. Para personalizar carteira, pool ou pasta dos logs, copie `settings.example.json` para `settings.json` no diretório local e edite-o.

A versão macOS pode exibir pool, cotação, rendimentos e histórico. A telemetria NVIDIA depende de `nvidia-smi` disponível naquela máquina. Os scripts de clock e a instalação do minerador incluídos são específicos do Windows e do perfil de RTX 3070 testado; não instalam mineração para o hardware Apple.

## Atualizar o backup versionado

Com o monitor aberto ou fechado, execute na raiz do projeto:

```text
mise exec -- python tools/export_portable_snapshot.py
```

O exportador usa a API online de backup SQLite para criar um banco consistente sem copiar arquivos WAL/SHM abertos. Antes do commit, confira as alterações de `portable-data`. Mantenha o repositório privado: ele contém histórico financeiro e o endereço público solicitado.
