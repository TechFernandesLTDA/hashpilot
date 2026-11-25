"""Componentes de interface do painel (Tkinter puro) sobre os tokens de `monitor_theme`.

Uso típico:

    import ui_kit as ui
    ui.apply_styles(root)                      # uma vez por processo, depois de criar o Tk
    card = ui.Card(parent)
    ui.label(card, "Hashrate", "small", tone="secondary", bg=card.bg).pack(anchor="w")
    ui.Button(card, "Salvar", command=save, variant="primary").pack(side="right")

Regras: só biblioteca padrão; cantos retos com borda de 1 px (o Tk não arredonda
widgets); tamanhos de fonte em pontos (o Tk aplica o DPI); medidas em px lógicos
passam por `px()`. A especificação completa está em ESPEC.md da reforma visual.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import monitor_theme as theme
from monitor_theme import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_PRESSED,
    ACCENT_SOFT,
    ACCENT_TEXT,
    BORDER,
    BORDER_STRONG,
    CARD,
    CARD_HOVER,
    CONTROL,
    CONTROL_HOVER,
    CONTROL_PRESSED,
    DANGER,
    DANGER_HOVER,
    ERROR,
    ERROR_SOFT,
    FIELD,
    INFO,
    INFO_SOFT,
    NEUTRAL,
    NEUTRAL_SOFT,
    OK,
    OK_SOFT,
    ON_ACCENT,
    ON_DANGER,
    SIDEBAR,
    SURFACE,
    TEXT,
    TEXT_DISABLED,
    TEXT_SECONDARY,
    TEXT_TERTIARY,
    WARNING,
    WARNING_SOFT,
)

__all__ = [
    "Button",
    "Card",
    "Pill",
    "Separator",
    "SidebarItem",
    "TextField",
    "apply_styles",
    "brand_icon",
    "check",
    "font",
    "icon_font",
    "label",
    "menu",
    "progress",
    "radio",
    "px",
    "section_title",
    "style_option_menu",
    "theme_buttons",
    "ICONS",
    "TONES",
]

# Cor do texto por "tom".
TONES = {
    "primary": TEXT,
    "secondary": TEXT_SECONDARY,
    "tertiary": TEXT_TERTIARY,
    "disabled": TEXT_DISABLED,
    "accent": ACCENT_TEXT,
    "ok": OK,
    "warning": WARNING,
    "error": ERROR,
    "info": INFO,
}

# Pílulas de estado: tipo -> (texto, fundo).
PILL_KINDS = {
    "neutral": (NEUTRAL, NEUTRAL_SOFT),
    "ok": (OK, OK_SOFT),
    "accent": (ACCENT_TEXT, ACCENT_SOFT),
    "warning": (WARNING, WARNING_SOFT),
    "error": (ERROR, ERROR_SOFT),
    "info": (INFO, INFO_SOFT),
}

# Cores antigas e novas que chegam por `configure(fg=...)` numa pílula -> tipo.
_COLOR_KINDS = {
    ACCENT.lower(): "ok",
    ACCENT_TEXT.lower(): "ok",
    WARNING.lower(): "warning",
    "#ffbc66": "warning",
    ERROR.lower(): "error",
    DANGER.lower(): "error",
    "#9e3333": "error",
    INFO.lower(): "info",
    "#1c64c8": "info",
    TEXT.lower(): "neutral",
    TEXT_SECONDARY.lower(): "neutral",
    "#ffffff": "neutral",
    "#e8edf5": "neutral",
    "#b9c7dc": "neutral",
}

# Ícones (Segoe Fluent Icons no Windows 11; Segoe MDL2 Assets no Windows 10; mesmos códigos).
ICONS = {
    "home": "",
    "gpu": "",
    "money": "",
    "chart": "",
    "settings": "",
    "more": "",
    "play": "",
    "stop": "",
    "refresh": "",
    "copy": "",
    "open": "",
    "shield": "",
    "info": "",
    "warning": "",
    "check": "",
    "close": "",
    "wallet": "",
    "update": "",
    "sparkle": "",
    "temperature": "",
}

_semibold_ok: dict[str, bool] = {}


def _family_exists(family: str) -> bool:
    if family not in _semibold_ok:
        try:
            _semibold_ok[family] = family in set(tkfont.families())
        except tk.TclError, RuntimeError:
            return True  # sem Tk ainda: assume a família da escala
    return _semibold_ok[family]


def font(role: str = "body", *, size: int | None = None, weight: str | None = None, mono: bool = False) -> tuple:
    """Fonte da escala tipográfica: `font("h1")`, `font("body", weight="semibold")`, `font("small", mono=True)`.

    Retorna uma tupla (família, pontos, peso) aceita por qualquer widget Tk.
    `size` em pontos substitui o da escala; use negativo só para pixels exatos.
    """
    points, default_weight = theme.TYPE[role]
    points = points if size is None else size
    weight = weight or default_weight
    display = role in theme.DISPLAY_ROLES
    if mono:
        return (theme.MONO_FAMILY, points, "bold" if weight in ("semibold", "bold") else "normal")
    if weight == "semibold":
        family = theme.DISPLAY_SEMIBOLD if display else theme.FONT_SEMIBOLD
        if _family_exists(family):
            return (family, points, "normal")
        return (theme.DISPLAY_FAMILY if display else theme.FONT_FAMILY, points, "bold")
    return (theme.DISPLAY_FAMILY if display else theme.FONT_FAMILY, points, weight)


def icon_font(size: int = 11) -> tuple:
    """Fonte dos glifos de `ICONS` (Fluent no Windows 11, MDL2 no Windows 10)."""
    family = "Segoe Fluent Icons" if _family_exists("Segoe Fluent Icons") else "Segoe MDL2 Assets"
    return (family, size, "normal")


def px(widget: tk.Misc, value: float) -> int:
    """Converte px lógicos (96 dpi) para px reais do monitor da janela."""
    try:
        factor = float(widget.winfo_fpixels("1i")) / 96.0
    except tk.TclError:
        factor = 1.0
    return int(round(value * max(factor, 1.0)))


# Estilos ttk ---------------------------------------------------------------------------


def apply_styles(root: tk.Misc, *, force: bool = False) -> ttk.Style:
    """Aplica tema clam + tokens a todos os widgets ttk e os padrões dos widgets Tk clássicos.

    Idempotente por interpretador Tk: reaplicar reconfigura a árvore inteira e é caro,
    então a segunda chamada só retorna o `ttk.Style` (use `force=True` para refazer).
    """
    top = root.winfo_toplevel()
    interp = top.nametowidget(".")
    style = ttk.Style(top)
    if getattr(interp, "_hashpilot_styles", False) and not force:
        return style
    if style.theme_use() != "clam":
        style.theme_use("clam")
    body = font("body")
    label_font = font("label")
    small_strong = font("small", weight="semibold")
    linespace = tkfont.Font(root=top, font=body).metrics("linespace")
    rowheight = linespace + px(top, 12)

    style.configure(
        ".",
        background=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        troughcolor=BORDER,
        focuscolor=ACCENT,
        selectbackground=CARD_HOVER,
        selectforeground=TEXT,
        fieldbackground=FIELD,
        insertcolor=TEXT,
        font=body,
    )
    style.map(".", foreground=[("disabled", TEXT_DISABLED)])

    for name, bg in (("TFrame", SURFACE), ("Card.TFrame", CARD), ("Sidebar.TFrame", SIDEBAR)):
        style.configure(name, background=bg)
    style.configure("Card.TFrame", bordercolor=BORDER, relief="solid", borderwidth=1)

    for prefix, bg in (("", SURFACE), ("Card.", CARD), ("Sidebar.", SIDEBAR)):
        style.configure(f"{prefix}TLabel", background=bg, foreground=TEXT, font=body)
        style.configure(f"{prefix}Muted.TLabel", background=bg, foreground=TEXT_SECONDARY, font=body)
        style.configure(f"{prefix}Caption.TLabel", background=bg, foreground=TEXT_TERTIARY, font=font("caption"))
    style.configure("Title.TLabel", background=SURFACE, foreground=TEXT, font=font("h1"))
    style.configure("Section.TLabel", background=SURFACE, foreground=TEXT, font=font("h2"))

    # Botões: secundário (padrão), primário, fantasma e perigo.
    pad = (px(top, 14), px(top, 6))
    variants = {
        "TButton": (CONTROL, TEXT, BORDER_STRONG, CONTROL_HOVER, CONTROL_PRESSED),
        "Primary.TButton": (ACCENT, ON_ACCENT, ACCENT, ACCENT_HOVER, ACCENT_PRESSED),
        "Ghost.TButton": (SURFACE, TEXT_SECONDARY, SURFACE, CARD_HOVER, BORDER),
        "Danger.TButton": (DANGER, ON_DANGER, DANGER, DANGER_HOVER, DANGER),
    }
    for name, (bg, fg, border, hover, pressed) in variants.items():
        style.configure(
            name,
            background=bg,
            foreground=fg,
            bordercolor=border,
            lightcolor=bg,
            darkcolor=bg,
            focuscolor=fg,
            focusthickness=0,
            padding=pad,
            relief="flat",
            font=label_font,
            anchor="center",
        )
        style.map(
            name,
            background=[("disabled", SURFACE), ("pressed", pressed), ("active", hover)],
            foreground=[("disabled", TEXT_DISABLED), ("active", TEXT if name == "Ghost.TButton" else fg)],
            bordercolor=[("disabled", BORDER), ("focus", ACCENT)],
            lightcolor=[("pressed", pressed), ("active", hover)],
            darkcolor=[("pressed", pressed), ("active", hover)],
        )
    # Estilos com prefixo (Ops.TButton, Ops.TLabel, Ops.TFrame...) herdam do estilo base no ttk:
    # basta não reconfigurá-los com cores próprias.

    for prefix, bg in (("", SURFACE), ("Card.", CARD)):
        for kind in ("TCheckbutton", "TRadiobutton"):
            style.configure(
                prefix + kind,
                background=bg,
                foreground=TEXT,
                indicatorbackground=FIELD,
                indicatorforeground=ON_ACCENT,
                upperbordercolor=BORDER_STRONG,
                lowerbordercolor=BORDER_STRONG,
                focuscolor=bg,
                font=body,
            )
            style.map(
                prefix + kind,
                background=[("active", bg)],
                indicatorbackground=[("selected", ACCENT), ("disabled", SURFACE)],
                upperbordercolor=[("selected", ACCENT), ("focus", ACCENT)],
                lowerbordercolor=[("selected", ACCENT), ("focus", ACCENT)],
            )

    field = dict(
        fieldbackground=FIELD,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=FIELD,
        darkcolor=FIELD,
        insertcolor=TEXT,
        padding=(px(top, 8), px(top, 5)),
        selectbackground=ACCENT_PRESSED,
        selectforeground=TEXT,
    )
    field_map = dict(
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", FIELD)],
        fieldbackground=[("readonly", FIELD), ("disabled", SURFACE)],
        foreground=[("disabled", TEXT_DISABLED)],
    )
    style.configure("TEntry", **field)
    style.map("TEntry", **field_map)
    style.configure("TSpinbox", **field, background=FIELD, arrowcolor=TEXT_SECONDARY, arrowsize=px(top, 12))
    style.map("TSpinbox", **field_map)
    style.configure("TCombobox", **field, background=FIELD, arrowcolor=TEXT_SECONDARY, arrowsize=px(top, 12))
    style.map(
        "TCombobox",
        **field_map,
        background=[("active", CARD_HOVER)],
        selectbackground=[("readonly", FIELD)],
        selectforeground=[("readonly", TEXT)],
    )
    for option, value in (
        ("background", CARD),
        ("foreground", TEXT),
        ("selectBackground", CARD_HOVER),
        ("selectForeground", TEXT),
        ("font", body),
        ("borderWidth", 0),
        ("relief", "flat"),
    ):
        top.option_add(f"*TCombobox*Listbox.{option}", value)

    # Tabelas.
    for name in ("Treeview", "Finance.Treeview", "Ops.Treeview"):
        style.configure(
            name,
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=CARD,
            darkcolor=CARD,
            rowheight=rowheight,
            font=body,
            borderwidth=0,
        )
        style.map(name, background=[("selected", CARD_HOVER)], foreground=[("selected", TEXT)])
        style.configure(
            name + ".Heading",
            background=CARD,
            foreground=TEXT_SECONDARY,
            bordercolor=BORDER,
            lightcolor=CARD,
            darkcolor=CARD,
            relief="flat",
            font=small_strong,
            padding=(px(top, 8), px(top, 6)),
        )
        style.map(name + ".Heading", background=[("active", CARD_HOVER)])

    # Barras de progresso finas, sem borda aparente.
    for variant, color in (("", ACCENT), ("Accent.", ACCENT), ("Info.", INFO), ("Warning.", WARNING)):
        for thickness_name, thickness in (("", 6), ("Thin.", 4)):
            style.configure(
                f"{thickness_name}{variant}Horizontal.TProgressbar",
                troughcolor=BORDER,
                background=color,
                bordercolor=BORDER,
                lightcolor=color,
                darkcolor=color,
                thickness=px(top, thickness),
            )
    # Nomes usados hoje pelo cartão de pagamento (monitor.py).
    for name, color in (("Generated", ACCENT), ("Confirmed", INFO)):
        style.configure(
            f"{name}.Horizontal.TProgressbar",
            troughcolor=BORDER,
            background=color,
            bordercolor=BORDER,
            lightcolor=color,
            darkcolor=color,
            thickness=px(top, 6),
        )

    # Barras de rolagem discretas, sem setas.
    for orient, sticky in (("Vertical", "ns"), ("Horizontal", "ew")):
        style.layout(
            f"{orient}.TScrollbar",
            [
                (
                    f"{orient}.Scrollbar.trough",
                    {"sticky": sticky, "children": [(f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]},
                )
            ],
        )
        style.configure(
            f"{orient}.TScrollbar",
            background=CARD_HOVER,
            troughcolor=SURFACE,
            bordercolor=SURFACE,
            lightcolor=CARD_HOVER,
            darkcolor=CARD_HOVER,
            gripcount=0,
            arrowsize=px(top, 10),
        )
        style.map(f"{orient}.TScrollbar", background=[("active", BORDER_STRONG), ("pressed", BORDER_STRONG)])

    # Abas segmentadas (sub-abas dentro de uma página) e Notebook sem abas (navegação lateral).
    for name in ("TNotebook", "Ops.TNotebook"):
        style.configure(
            name,
            background=SURFACE,
            borderwidth=0,
            bordercolor=SURFACE,
            lightcolor=SURFACE,
            darkcolor=SURFACE,
            tabmargins=(0, 0, 0, px(top, 8)),
        )
        style.configure(
            name + ".Tab",
            background=SURFACE,
            foreground=TEXT_SECONDARY,
            bordercolor=SURFACE,
            lightcolor=SURFACE,
            darkcolor=SURFACE,
            padding=(px(top, 14), px(top, 6)),
            font=label_font,
        )
        style.map(
            name + ".Tab",
            background=[("selected", CARD_HOVER), ("active", CARD)],
            foreground=[("selected", TEXT), ("active", TEXT)],
            bordercolor=[("selected", BORDER)],
            lightcolor=[("selected", CARD_HOVER)],
            expand=[("selected", (0, 0, 0, 0))],
        )
    style.configure("Hidden.TNotebook", background=SURFACE, borderwidth=0, tabmargins=0, bordercolor=SURFACE)
    style.layout("Hidden.TNotebook.Tab", [])

    style.configure("TSeparator", background=BORDER)
    style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE)
    style.configure("TLabelframe.Label", background=SURFACE, foreground=TEXT_SECONDARY, font=small_strong)

    _classic_defaults(top, body)
    interp._hashpilot_styles = True  # type: ignore[attr-defined]
    return style


def _classic_defaults(top: tk.Misc, body: tuple) -> None:
    """Padrões do banco de opções para widgets Tk clássicos criados sem cores explícitas."""
    options = {
        "*Toplevel.background": SURFACE,
        "*Frame.background": SURFACE,
        "*Canvas.background": SURFACE,
        "*Canvas.highlightThickness": 0,
        "*Label.background": SURFACE,
        "*Label.foreground": TEXT,
        "*Button.background": CONTROL,
        "*Button.foreground": TEXT,
        "*Button.activeBackground": CONTROL_PRESSED,
        "*Button.activeForeground": TEXT,
        "*Button.disabledForeground": TEXT_DISABLED,
        "*Button.relief": "flat",
        "*Button.borderWidth": 0,
        "*Button.highlightThickness": 1,
        "*Button.highlightBackground": CONTROL,
        "*Button.highlightColor": ACCENT,
        "*Button.cursor": "hand2",
        "*Checkbutton.background": SURFACE,
        "*Checkbutton.foreground": TEXT,
        "*Checkbutton.activeBackground": SURFACE,
        "*Checkbutton.activeForeground": TEXT,
        "*Checkbutton.selectColor": FIELD,
        "*Checkbutton.highlightThickness": 0,
        "*Radiobutton.background": SURFACE,
        "*Radiobutton.foreground": TEXT,
        "*Radiobutton.activeBackground": SURFACE,
        "*Radiobutton.activeForeground": TEXT,
        "*Radiobutton.selectColor": FIELD,
        "*Radiobutton.highlightThickness": 0,
        "*Entry.background": FIELD,
        "*Entry.foreground": TEXT,
        "*Entry.insertBackground": TEXT,
        "*Entry.readonlyBackground": CARD,
        "*Entry.disabledBackground": SURFACE,
        "*Entry.selectBackground": ACCENT_PRESSED,
        "*Entry.selectForeground": TEXT,
        "*Entry.relief": "flat",
        "*Entry.highlightThickness": 1,
        "*Entry.highlightBackground": BORDER,
        "*Entry.highlightColor": ACCENT,
        "*Spinbox.background": FIELD,
        "*Spinbox.foreground": TEXT,
        "*Spinbox.buttonBackground": CARD,
        "*Spinbox.insertBackground": TEXT,
        "*Spinbox.relief": "flat",
        "*Spinbox.highlightThickness": 1,
        "*Spinbox.highlightBackground": BORDER,
        "*Spinbox.highlightColor": ACCENT,
        "*Text.background": CARD,
        "*Text.foreground": TEXT,
        "*Text.insertBackground": TEXT,
        "*Text.selectBackground": ACCENT_PRESSED,
        "*Text.relief": "flat",
        "*Text.highlightThickness": 1,
        "*Text.highlightBackground": BORDER,
        "*Text.highlightColor": BORDER_STRONG,
        "*Listbox.background": CARD,
        "*Listbox.foreground": TEXT,
        "*Listbox.selectBackground": CARD_HOVER,
        "*Listbox.selectForeground": TEXT,
        "*Listbox.relief": "flat",
        "*Menu.background": CARD,
        "*Menu.foreground": TEXT,
        "*Menu.activeBackground": CARD_HOVER,
        "*Menu.activeForeground": TEXT,
        "*Menu.relief": "flat",
        "*Menubutton.background": CONTROL,
        "*Menubutton.foreground": TEXT,
        "*Menubutton.activeBackground": CARD_HOVER,
        "*Menubutton.activeForeground": TEXT,
        "*Menubutton.relief": "flat",
        "*Menubutton.highlightThickness": 1,
        "*Menubutton.highlightBackground": BORDER_STRONG,
    }
    for pattern, value in options.items():
        top.option_add(pattern, value, "widgetDefault")
    for widget_class in (
        "Label",
        "Button",
        "Checkbutton",
        "Radiobutton",
        "Entry",
        "Spinbox",
        "Listbox",
        "Menu",
        "Menubutton",
    ):
        top.option_add(f"*{widget_class}.font", body, "widgetDefault")


# Componentes ---------------------------------------------------------------------------

_BUTTON_VARIANTS = {
    # variante: (fundo, texto, fundo no hover, fundo pressionado, borda); None = fundo da superfície
    "primary": (ACCENT, ON_ACCENT, ACCENT_HOVER, ACCENT_PRESSED, ACCENT),
    "secondary": (CONTROL, TEXT, CONTROL_HOVER, CONTROL_PRESSED, None),
    "ghost": (None, TEXT_SECONDARY, CARD_HOVER, BORDER, None),
    "danger": (DANGER, ON_DANGER, DANGER_HOVER, DANGER, DANGER),
    "danger_ghost": (None, ERROR, ERROR_SOFT, ERROR_SOFT, None),
}
_BUTTON_SIZES = {"sm": (10, 3), "md": (14, 6), "lg": (18, 9)}


def _paint_button(button: tk.Button, variant: str, surface: str, size: str | None = None) -> None:
    bg, fg, hover, pressed, border = _BUTTON_VARIANTS[variant]
    bg = surface if bg is None else bg
    disabled = str(button.cget("state")) == "disabled"
    if disabled and variant in ("primary", "danger"):
        bg = CONTROL
    # O tk.Button do Windows só desenha o anel de destaque com foco: ele serve de anel de foco no acento.
    border = bg if border is None else border
    options = dict(
        bg=bg,
        fg=fg,
        activebackground=pressed,
        activeforeground=TEXT if variant == "ghost" else fg,
        disabledforeground=TEXT_DISABLED,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=border,
        highlightcolor=ACCENT,
        cursor="arrow" if disabled else "hand2",
        takefocus=1,
    )
    if size is not None:
        padx, pady = _BUTTON_SIZES[size]
        options.update(
            padx=px(button, padx),
            pady=px(button, pady),
            font=font("label" if size != "sm" else "small", weight="semibold"),
        )
    tk.Button.configure(button, **options)
    button._hp_look = (variant, surface, hover)  # type: ignore[attr-defined]
    if not getattr(button, "_hp_hover_bound", False):
        button.bind("<Enter>", _button_enter, add="+")
        button.bind("<Leave>", _button_leave, add="+")
        button._hp_hover_bound = True  # type: ignore[attr-defined]


def _button_enter(event: tk.Event) -> None:
    button = event.widget
    look = getattr(button, "_hp_look", None)
    if look and str(button.cget("state")) != "disabled":
        tk.Button.configure(button, bg=look[2], fg=TEXT if look[0] == "ghost" else button.cget("fg"))


def _button_leave(event: tk.Event) -> None:
    button = event.widget
    look = getattr(button, "_hp_look", None)
    if look:
        _paint_button(button, look[0], look[1])


class Button(tk.Button):
    """Botão plano. Variantes: primary, secondary (padrão), ghost, danger, danger_ghost.

    `bg` é a cor da superfície por trás (importa para ghost/danger_ghost). Mudar
    `state` por `configure`/`btn["state"]` repinta o botão (primário desabilitado
    vira neutro). Continua sendo um `tk.Button`: `invoke()`, `cget("text")` etc.
    """

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        command=None,
        *,
        variant: str = "secondary",
        size: str = "md",
        bg: str = SURFACE,
        **kwargs,
    ) -> None:
        self._hp_variant, self._hp_surface, self._hp_size = variant, bg, size
        super().__init__(master, text=text, command=command, **kwargs)
        _paint_button(self, variant, bg, size)

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        result = super().configure(cnf, **kwargs)
        keys = set(kwargs) | (set(cnf) if isinstance(cnf, dict) else set())
        if "state" in keys and hasattr(self, "_hp_variant"):
            _paint_button(self, self._hp_variant, self._hp_surface)
        return result

    config = configure

    def set_variant(self, variant: str) -> None:
        self._hp_variant = variant
        _paint_button(self, variant, self._hp_surface)


def theme_buttons(widget: tk.Misc, role=None, surface: str | None = None) -> int:
    """Aplica o visual do kit a `tk.Button` comuns já criados dentro de `widget` (recursivo).

    `role(text, bg) -> "primary" | "secondary" | "danger" | "ghost"` escolhe a variante;
    sem `role`, todos viram secundários. Botões `ui_kit.Button` são preservados.
    `surface` é a cor por trás; por padrão, o fundo do pai. Retorna quantos mudaram.
    """
    count = 0
    for child in widget.winfo_children():
        if child.winfo_class() == "Button" and not isinstance(child, Button):
            variant = role(child.cget("text"), child.cget("bg")) if role else "secondary"
            variant = variant if variant in _BUTTON_VARIANTS else "secondary"
            try:
                behind = surface or widget.cget("bg")
            except tk.TclError:
                behind = SURFACE
            _paint_button(child, variant, behind, "md")
            count += 1
        count += theme_buttons(child, role, surface)
    return count


class Card(tk.Frame):
    """Cartão: fundo CARD, borda de 1 px, respiro interno. Filhos usam `bg=card.bg`.

    `elevated=True` usa CARD_HOVER (cartão dentro de cartão, menus). `set_selected`
    troca a borda pelo acento (seleção de GPU, item ativo).
    """

    def __init__(
        self, master: tk.Misc, *, padding: str | int = "lg", elevated: bool = False, hover: bool = False, **kwargs
    ) -> None:
        self.bg = CARD_HOVER if elevated else CARD
        space = theme.SPACE[padding] if isinstance(padding, str) else padding
        self._border = BORDER
        kwargs.setdefault("padx", px(master, space))
        kwargs.setdefault("pady", px(master, space))
        super().__init__(
            master, bg=self.bg, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER, **kwargs
        )
        self._selected = False
        if hover:
            self.bind("<Enter>", lambda _e: self._outline(BORDER_STRONG), add="+")
            self.bind("<Leave>", lambda _e: self._outline(BORDER), add="+")

    def _outline(self, color: str) -> None:
        if not self._selected:
            self.configure(highlightbackground=color, highlightcolor=color)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        color = ACCENT if selected else BORDER
        self.configure(highlightbackground=color, highlightcolor=color)


class SidebarItem(tk.Frame):
    """Item da barra lateral: indicador de 2 px no acento, ícone opcional, título e subtítulo.

    Selecionado: fundo CARD_HOVER, título em TEXT, ícone e indicador no acento (o peso
    da fonte não muda, para não brigar com quem redimensiona fontes).
    Normal: fundo da barra, título TEXT_SECONDARY; hover em CARD. Focável por Tab,
    ativa com clique, Enter ou Espaço.
    """

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command=None,
        *,
        icon: str | None = None,
        subtitle: str | None = None,
        bg: str = SIDEBAR,
    ) -> None:
        super().__init__(
            master,
            bg=bg,
            takefocus=1,
            highlightthickness=1,
            highlightbackground=bg,
            highlightcolor=ACCENT,
            cursor="hand2",
        )
        self.command, self.surface, self.selected, self._hover = command, bg, False, False
        self.indicator = tk.Frame(self, bg=bg, width=px(self, 2))
        self.indicator.pack(side="left", fill="y")
        self.icon = None
        if icon:
            self.icon = tk.Label(
                self, text=ICONS.get(icon, icon), font=icon_font(11), bg=bg, fg=TEXT_SECONDARY, width=2
            )
            self.icon.pack(side="left", padx=(px(self, 8), 0))
        column = tk.Frame(self, bg=bg)
        column.pack(side="left", fill="x", expand=True, padx=(px(self, 8), px(self, 10)), pady=px(self, 6))
        self.column = column
        self.title = tk.Label(
            column, text=text, bg=bg, fg=TEXT_SECONDARY, font=font("body"), anchor="w", justify="left"
        )
        self.title.pack(fill="x")
        self.subtitle = tk.Label(
            column, text=subtitle or "", bg=bg, fg=TEXT_TERTIARY, font=font("caption"), anchor="w", justify="left"
        )
        if subtitle:
            self.subtitle.pack(fill="x")
        for widget in (self, self.indicator, column, self.title, self.subtitle, *([self.icon] if self.icon else [])):
            widget.bind("<Button-1>", self._activate, add="+")
            widget.bind("<Enter>", lambda _e: self._set_hover(True), add="+")
            widget.bind("<Leave>", lambda _e: self._set_hover(False), add="+")
        self.bind("<Return>", self._activate)
        self.bind("<space>", self._activate)

    def _activate(self, _event=None):
        self.focus_set()
        if self.command:
            self.command()
        return "break"

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._paint()

    def _paint(self) -> None:
        bg = CARD_HOVER if self.selected else CARD if self._hover else self.surface
        for widget in (self, self.column, self.title, self.subtitle, *([self.icon] if self.icon else [])):
            widget.configure(bg=bg)
        self.configure(highlightbackground=bg)
        self.indicator.configure(bg=ACCENT if self.selected else bg)
        # Só cor e fundo mudam: a fonte fica intacta para quem redimensiona fontes (monitor.adapt_layout).
        self.title.configure(fg=TEXT if self.selected else TEXT_SECONDARY)
        if self.icon:
            self.icon.configure(fg=ACCENT_TEXT if self.selected else TEXT_SECONDARY)

    def set_selected(self, selected: bool) -> None:
        self.selected = bool(selected)
        self._paint()

    def set_text(self, text: str) -> None:
        self.title.configure(text=text)

    def set_subtitle(self, text: str | None) -> None:
        self.subtitle.configure(text=text or "")
        if text and not self.subtitle.winfo_manager():
            self.subtitle.pack(fill="x")
        elif not text:
            self.subtitle.pack_forget()


class Pill(tk.Label):
    """Pílula de estado: texto curto colorido sobre fundo suave do mesmo tom.

    Tipos: neutral, ok, accent, warning, error, info. `set(text, kind)` atualiza.
    `configure(fg=<cor de estado>)` também troca o tipo, para quem ainda passa cores.
    """

    def __init__(self, master: tk.Misc, text: str = "", kind: str = "neutral", **kwargs) -> None:
        self.kind = kind if kind in PILL_KINDS else "neutral"
        fg, bg = PILL_KINDS[self.kind]
        kwargs.setdefault("font", font("small", weight="semibold"))
        super().__init__(master, text=text, fg=fg, bg=bg, padx=px(master, 8), pady=px(master, 2), bd=0, **kwargs)

    def set(self, text: str | None = None, kind: str | None = None) -> None:
        options: dict = {}
        if text is not None:
            options["text"] = text
        if kind is not None:
            self.kind = kind if kind in PILL_KINDS else "neutral"
            options["fg"], options["bg"] = PILL_KINDS[self.kind]
        tk.Label.configure(self, **options)

    def configure(self, cnf=None, **kwargs):  # type: ignore[override]
        color = kwargs.get("fg", kwargs.get("foreground"))
        if isinstance(color, str) and color.lower() in _COLOR_KINDS:
            kwargs.pop("fg", None)
            kwargs.pop("foreground", None)
            self.kind = _COLOR_KINDS[color.lower()]
            kwargs["fg"], kwargs["bg"] = PILL_KINDS[self.kind]
        return super().configure(cnf, **kwargs)

    config = configure


class Separator(tk.Frame):
    """Linha de 1 px na cor de borda (horizontal por padrão)."""

    def __init__(self, master: tk.Misc, *, vertical: bool = False, color: str = BORDER) -> None:
        if vertical:
            super().__init__(master, bg=color, width=1)
        else:
            super().__init__(master, bg=color, height=1)


class TextField(tk.Entry):
    """Campo de texto: fundo FIELD, borda de 1 px que vira acento no foco.

    O respiro interno vem de uma borda plana de 6 px na cor do campo (`bd=0` remove);
    para um campo mais alto, some `ipady` no gerenciador de geometria.
    `set_validation("ok" | "warning" | "error" | None)` pinta a borda conforme a validação.
    """

    def __init__(
        self, master: tk.Misc, textvariable=None, *, mono: bool = False, readonly: bool = False, **kwargs
    ) -> None:
        # Os padrões do tema cedem a quem passa a mesma opção (ex.: `fg=TEXT_TERTIARY`).
        options = dict(
            font=font("body", mono=mono),
            bg=FIELD,
            fg=TEXT,
            insertbackground=TEXT,
            readonlybackground=CARD,
            disabledbackground=SURFACE,
            disabledforeground=TEXT_DISABLED,
            selectbackground=ACCENT_PRESSED,
            selectforeground=TEXT,
            relief="flat",
            # Borda plana na cor do fundo = respiro interno (o tk.Entry não tem padx); o anel fica no destaque.
            bd=px(master, 6),
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=ACCENT,
        )
        for alias, name in (("background", "bg"), ("foreground", "fg"), ("borderwidth", "bd")):
            if alias in kwargs:
                options.pop(name, None)
        options.update(kwargs)
        super().__init__(master, textvariable=textvariable, **options)
        if readonly:
            self.configure(state="readonly")

    def set_validation(self, state: str | None) -> None:
        color = {"ok": ACCENT, "warning": WARNING, "error": ERROR}.get(state or "")
        self.configure(highlightbackground=color or BORDER, highlightcolor=color or ACCENT)


def label(
    master: tk.Misc, text: str = "", role: str = "body", *, tone: str = "primary", bg: str = SURFACE, **kwargs
) -> tk.Label:
    """Texto da escala: `label(parent, "Título", "h2")`, `label(card, "nota", "small", tone="secondary", bg=card.bg)`."""
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("justify", "left")
    # `fg=`/`font=` explícitos substituem o tom e o papel em vez de colidir com eles.
    fg = kwargs.pop("fg", kwargs.pop("foreground", TONES.get(tone, tone)))
    kwargs.setdefault("font", font(role))
    return tk.Label(master, text=text, bg=kwargs.pop("background", bg), fg=fg, **kwargs)


def section_title(
    master: tk.Misc, title: str, subtitle: str | None = None, *, bg: str = SURFACE, wrap: int = 640
) -> tk.Frame:
    """Título de seção (h2) com descrição opcional em texto secundário. Retorna o Frame (`.title`, `.subtitle`)."""
    frame = tk.Frame(master, bg=bg)
    frame.title = label(frame, title, "h2", bg=bg)  # type: ignore[attr-defined]
    frame.title.pack(fill="x")  # type: ignore[attr-defined]
    frame.subtitle = None  # type: ignore[attr-defined]
    if subtitle:
        frame.subtitle = label(frame, subtitle, "body", tone="secondary", bg=bg, wraplength=wrap)  # type: ignore[attr-defined]
        frame.subtitle.pack(fill="x", pady=(px(master, 4), 0))  # type: ignore[attr-defined]
    return frame


def progress(master: tk.Misc, *, variant: str = "accent", thin: bool = False, **kwargs) -> ttk.Progressbar:
    """Barra de progresso fina (6 px; 4 px com `thin=True`). Variantes: accent, info, warning."""
    prefix = {"accent": "Accent.", "info": "Info.", "warning": "Warning."}.get(variant, "Accent.")
    style = ("Thin." if thin else "") + prefix + "Horizontal.TProgressbar"
    kwargs.setdefault("maximum", 100)
    kwargs.setdefault("mode", "determinate")
    return ttk.Progressbar(master, style=style, **kwargs)


def check(master: tk.Misc, text: str, variable=None, *, bg: str = SURFACE, **kwargs) -> tk.Checkbutton:
    """Caixa de seleção clássica (`tk.Checkbutton`, que as ferramentas de QA procuram) no tema."""
    kwargs.setdefault("font", font("body"))
    kwargs.setdefault("anchor", "w")
    kwargs.setdefault("justify", "left")
    return tk.Checkbutton(
        master,
        text=text,
        variable=variable,
        bg=bg,
        fg=TEXT,
        selectcolor=FIELD,
        activebackground=bg,
        activeforeground=TEXT,
        disabledforeground=TEXT_DISABLED,
        highlightthickness=0,
        bd=0,
        cursor="hand2",
        **kwargs,
    )


def radio(master: tk.Misc, text: str, variable=None, value=None, *, bg: str = SURFACE, **kwargs) -> tk.Radiobutton:
    """Botão de opção clássico (`tk.Radiobutton`) no tema."""
    kwargs.setdefault("font", font("body"))
    kwargs.setdefault("anchor", "w")
    return tk.Radiobutton(
        master,
        text=text,
        variable=variable,
        value=value,
        bg=bg,
        fg=TEXT,
        selectcolor=FIELD,
        activebackground=bg,
        activeforeground=TEXT,
        disabledforeground=TEXT_DISABLED,
        highlightthickness=0,
        bd=0,
        cursor="hand2",
        **kwargs,
    )


def style_option_menu(widget: tk.Menubutton) -> tk.Menubutton:
    """Ajusta um `tk.OptionMenu` existente (botão e lista) ao tema, sem trocar o widget."""
    widget.configure(
        bg=CONTROL,
        fg=TEXT,
        activebackground=CONTROL_HOVER,
        activeforeground=TEXT,
        relief="flat",
        bd=0,
        highlightthickness=0,
        # O indicador nativo do Windows é uma barrinha em relevo que não aceita cor: sem ele, vira um botão plano.
        indicatoron=0,
        font=font("body"),
        padx=px(widget, 10),
        pady=px(widget, 4),
        cursor="hand2",
    )
    try:
        widget["menu"].configure(bg=CARD, fg=TEXT, activebackground=CARD_HOVER, activeforeground=TEXT, bd=0)
    except tk.TclError, KeyError:
        pass
    return widget


def menu(master: tk.Misc) -> tk.Menu:
    """Menu suspenso no tema (use `tk_popup` como antes)."""
    return tk.Menu(
        master,
        tearoff=0,
        bg=CARD,
        fg=TEXT,
        activebackground=CARD_HOVER,
        activeforeground=TEXT,
        disabledforeground=TEXT_DISABLED,
        font=font("body"),
        bd=0,
        relief="flat",
    )


def brand_icon(master: tk.Misc, size: int = 32, *, bg: str = SIDEBAR) -> tk.Label:
    """Ícone oficial do app (icon-256.png, fundo transparente nos cantos) reduzido por fator inteiro.

    `size` em px lógicos; usa o maior divisor inteiro de 256 que não fique abaixo do
    tamanho pedido em px reais, para não ampliar nem reamostrar de forma fracionária.
    """
    target = px(master, size)
    factor = max(1, 256 // max(target, 1))
    image = tk.PhotoImage(master=master, file=str(theme.ASSET_DIR / "icon-256.png"))
    if factor > 1:
        image = image.subsample(factor)
    widget = tk.Label(master, image=image, bg=bg, bd=0)
    widget.image = image  # type: ignore[attr-defined]
    return widget
