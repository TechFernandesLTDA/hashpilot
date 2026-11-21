"""Tokens visuais do painel: fonte única de cores, tipografia, espaçamentos e raios.

Tema escuro neutro (grafite) com um único acento: o turquesa da marca Tech
Fernandes. Os componentes que usam estes tokens ficam em `ui_kit.py`. Este
módulo não importa tkinter, para poder ser lido também pelo runtime do rig.

Os nomes antigos (`BG`, `PANEL`, `BORDER`, `TEXT`, `MUTED`, `TEAL`, `BLUE`,
`ORANGE`, `PINK`, `BODY_FONT`, `TITLE_FONT`, `ASSET_DIR`) continuam exportados
e apontam para os tokens novos equivalentes.
"""

import os
from pathlib import Path

ASSET_DIR = Path(__file__).resolve().parent / "assets/brand"

# Superfícies, da mais funda para a mais alta.
SIDEBAR = "#131315"  # barra lateral e faixas de navegação
SURFACE = "#18181B"  # fundo do conteúdo (janelas, abas, seções)
CARD = "#1F1F23"  # cartões, tabelas, gráficos, campos somente leitura
CARD_HOVER = "#27272C"  # cartão elevado, hover, item de navegação selecionado
FIELD = "#141417"  # fundo de campo de texto editável
CONTROL = "#2A2A30"  # botão secundário (preenchido: o tk.Button do Windows não desenha borda colorida)
CONTROL_HOVER = "#33333A"
CONTROL_PRESSED = "#3B3B42"
BRAND_NAVY = "#0A1628"  # só atrás dos PNGs da marca, que têm esse fundo embutido

# Bordas de 1 px, baixo contraste.
BORDER = "#2C2C32"
BORDER_STRONG = "#3B3B42"

# Texto: hierarquia por tamanho e peso; a cor só separa três níveis.
TEXT = "#ECECEF"
TEXT_SECONDARY = "#A1A1AA"
TEXT_TERTIARY = "#878791"
TEXT_DISABLED = "#52525B"

# Acento único (turquesa da marca), usado com parcimônia.
ACCENT = "#12A98E"  # preenchimento do botão primário, indicador da navegação, foco
ACCENT_HOVER = "#17BC9E"
ACCENT_PRESSED = "#0F927B"
ACCENT_TEXT = "#2FC6A8"  # acento como texto/link sobre superfícies escuras
ACCENT_SOFT = "#15302B"  # fundo de pílula/realce com acento
ON_ACCENT = "#05130F"  # texto sobre o acento (branco não tem contraste suficiente)

# Estados. "ok" reaproveita o acento para não introduzir um segundo verde.
OK, OK_SOFT = ACCENT_TEXT, ACCENT_SOFT
WARNING, WARNING_SOFT = "#E8A23D", "#33281A"
ERROR, ERROR_SOFT = "#EF5A5F", "#3A1C1E"
INFO, INFO_SOFT = "#7AA2F7", "#1C2536"
NEUTRAL, NEUTRAL_SOFT = TEXT_SECONDARY, "#26262B"
DANGER = "#C93C41"  # preenchimento de ação destrutiva confirmada
DANGER_HOVER = "#D9474C"
ON_DANGER = "#FFFFFF"

# Séries de gráfico: cada métrica mantém a sua cor; o hashrate usa o acento.
CHART = {"hash": ACCENT, "temp": WARNING, "power": INFO, "fan": TEXT_SECONDARY, "money": ACCENT}
CHART_GRID = "#26262B"


def _windows_font_present(file_name):
    """Confere o arquivo da fonte sem depender de tkinter (o tema é importado antes da janela)."""
    folders = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        folders.append(Path(local) / "Microsoft/Windows/Fonts")
    return any((folder / file_name).is_file() for folder in folders)


# Segoe UI Variable (Windows 11) com queda para Segoe UI (Windows 10).
# O Tk só conhece normal/negrito; o seminegrito é uma família própria.
if _windows_font_present("SegUIVar.ttf"):
    FONT_FAMILY = "Segoe UI Variable Text"
    FONT_SEMIBOLD = "Segoe UI Variable Text Semibold"
    DISPLAY_FAMILY = "Segoe UI Variable Display"
    DISPLAY_SEMIBOLD = "Segoe UI Variable Display Semib"
else:
    FONT_FAMILY = DISPLAY_FAMILY = "Segoe UI"
    FONT_SEMIBOLD = DISPLAY_SEMIBOLD = "Segoe UI Semibold"
MONO_FAMILY = "Cascadia Mono" if _windows_font_present("CascadiaMono.ttf") else "Consolas"

# Escala tipográfica em pontos (o Tk converte pelo DPI): papel -> (pontos, peso).
# Peso: "normal", "semibold" ou "bold". Fonte "display" nos tamanhos de título.
TYPE = {
    "caption": (8, "normal"),  # rodapé, eixos, notas de fonte de dados
    "small": (9, "normal"),  # texto secundário, notas dos cartões
    "body": (10, "normal"),  # texto corrido, células de tabela
    "label": (10, "semibold"),  # rótulos de campo, botões, itens de navegação
    "overline": (8, "semibold"),  # títulos de cartão em caixa alta curta
    "h3": (11, "semibold"),  # título de cartão
    "h2": (13, "semibold"),  # título de seção
    "h1": (17, "semibold"),  # título de página
    "metric_sm": (15, "semibold"),  # número em cartão compacto (GPU)
    "metric": (24, "semibold"),  # número principal dos cartões de indicador
}
DISPLAY_ROLES = frozenset({"h1", "h2", "metric_sm", "metric"})

# Espaçamentos (px lógicos a 96 dpi; use ui_kit.px() para escalar).
SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
# Raios: o Tk não arredonda widgets; cantos retos com borda de 1 px, exceto Canvas.
RADIUS = {"sm": 4, "md": 6, "lg": 8}
SIDEBAR_WIDTH = 216  # px lógicos
CONTENT_PADDING = 24  # margem do conteúdo das páginas

# Nomes antigos, mantidos até todos os módulos migrarem.
BG = SURFACE
PANEL = CARD
MUTED = TEXT_SECONDARY
TEAL = ACCENT
BLUE = INFO
ORANGE = WARNING
PINK = TEXT_SECONDARY
BODY_FONT = FONT_FAMILY
TITLE_FONT = DISPLAY_FAMILY
