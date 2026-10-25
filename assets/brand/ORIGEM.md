# Identidade oficial — HashPilot / Tech Fernandes

Fonte: repositório **TechFernandesLTDA/design**, documentos `README.md`, `MARCA-TOKENS.md`, `00-marca-nucleo/00-guia-de-uso.md` e `03-kit-github/00-guia-de-uso.md`. Consulta em 24/10/2026. A biblioteca original não foi alterada.

Arquivos originais copiados sem recoloração ou redesenho:

| Arquivo local | Origem no repositório de design |
|---|---|
| `logo-horizontal-escuro.png` | `00-marca-nucleo/logo-horizontal-escuro.png` |
| `icone-app.png` | `00-marca-nucleo/icone-app.png` |
| `tech-fernandes_github_banner-readme_escuro_1280x400.png` | `03-kit-github/tech-fernandes_github_banner-readme_escuro_1280x400.png` |

Derivações técnicas proporcionais, sem recoloração:

- `icon-256.png`: ícone oficial 1024×1024 reduzido para 256×256.
- `hashpilot.ico`: o mesmo PNG 256×256 encapsulado em ICO para aplicativo, bandeja e atalhos.
- `logo-header.png`: recorte do fundo externo `(240,94)-(1360,426)`, preservando respiro, e redução de 1120×332 para 280×83. A composição interna não foi alterada.

Tokens: marinho `#0A1628`, azul `#1C64C8`, turquesa `#12A98E`, neutro `#E8EDF5` e branco `#FFFFFF`. Tons intermediários de superfície/borda e laranja de alerta servem à leitura operacional.

Tipografia: Sora 700 para títulos e Plus Jakarta Sans para texto quando instaladas; nesta máquina, fallback oficial Segoe UI. O aplicativo não instala fontes.

Os ativos seguem as regras da Tech Fernandes, sem licença adicional por estarem neste projeto.

Correção de nitidez do cabeçalho: 'logo-header-source.png' preserva o recorte oficial de 1120×332. As variantes 'logo-header-180.png', 'logo-header-240.png' e 'logo-header-360.png' são geradas diretamente desse recorte em seus tamanhos nativos, com antialias de alta qualidade e proporção preservada. A interface não reduz novamente o bitmap antigo nem amplia uma miniatura.

A geração das novas variantes pode ser reproduzida no Windows com 'tools/build_brand_headers.ps1'. O aplicativo consome apenas os PNGs prontos; não depende desse script nem de bibliotecas de imagem adicionais para executar.
