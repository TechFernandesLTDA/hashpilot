"""Contrato do ui_kit: tokens, estilos idempotentes e componentes que os módulos de interface reutilizam."""

import tkinter as tk
from tkinter import ttk
import unittest

import monitor_theme as theme
from testing_support import windows_only
import ui_kit as ui


class ThemeTokenTests(unittest.TestCase):
    def test_legacy_names_point_to_new_tokens(self):
        self.assertEqual(theme.BG, theme.SURFACE)
        self.assertEqual(theme.PANEL, theme.CARD)
        self.assertEqual(theme.MUTED, theme.TEXT_SECONDARY)
        self.assertEqual(theme.TEAL, "#12A98E")
        self.assertIn(theme.BODY_FONT, ("Segoe UI Variable Text", "Segoe UI"))
        self.assertTrue((theme.ASSET_DIR / "icon-256.png").is_file())

    def test_type_scale_roles_resolve_to_tk_fonts(self):
        for role in theme.TYPE:
            family, size, weight = ui.font(role)
            self.assertIsInstance(family, str)
            self.assertGreater(size, 0)
            self.assertIn(weight, ("normal", "bold"))
        self.assertEqual(ui.font("body", mono=True)[0], theme.MONO_FAMILY)


@windows_only("Tk com desktop e fontes Segoe")
class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_apply_styles_is_idempotent_and_hides_sidebar_notebook_tabs(self):
        style = ui.apply_styles(self.root)
        self.assertIs(type(ui.apply_styles(self.root)), type(style))
        self.assertNotIn("Notebook.tab", str(style.layout("Hidden.TNotebook.Tab")))
        book = ttk.Notebook(self.root, style="Hidden.TNotebook")
        for name in ("Visão geral", "GPU e mineração"):
            book.add(tk.Frame(book), text=name)
        book.select(1)
        self.assertEqual(book.index(book.select()), 1)
        self.assertEqual(book.tab(0, "text"), "Visão geral")

    def test_button_stays_a_tk_button_and_repaints_when_disabled(self):
        clicked = []
        button = ui.Button(self.root, "Salvar", command=lambda: clicked.append(True), variant="primary")
        self.assertIsInstance(button, tk.Button)
        self.assertEqual(button.cget("bg").lower(), theme.ACCENT.lower())
        button.invoke()
        self.assertEqual(clicked, [True])
        button.configure(state="disabled")
        self.assertEqual(button.cget("bg").lower(), theme.CONTROL.lower())
        button["state"] = "normal"
        self.assertEqual(button.cget("bg").lower(), theme.ACCENT.lower())

    def test_theme_buttons_restyles_plain_buttons_by_role(self):
        frame = tk.Frame(self.root, bg=theme.SURFACE)
        plain = tk.Button(frame, text="Remover exceção (UAC)")
        kit = ui.Button(frame, "Salvar", variant="primary")
        changed = ui.theme_buttons(frame, role=lambda text, bg: "danger" if "Remover" in text else "secondary")
        self.assertEqual(changed, 1)
        self.assertEqual(plain.cget("bg").lower(), theme.DANGER.lower())
        self.assertEqual(kit.cget("bg").lower(), theme.ACCENT.lower())

    def test_pill_maps_legacy_state_colors(self):
        pill = ui.Pill(self.root, "● Aguardando", "ok")
        pill.configure(text="● Parada solicitada", fg="#ffbc66")
        self.assertEqual(pill.kind, "warning")
        self.assertEqual(pill.cget("bg").lower(), theme.WARNING_SOFT.lower())
        pill.set(kind="error")
        self.assertEqual(pill.cget("fg").lower(), theme.ERROR.lower())

    def test_sidebar_item_selection_keeps_font(self):
        calls = []
        item = ui.SidebarItem(self.root, "Rendimentos", lambda: calls.append(1), icon="wallet", subtitle="sua carteira")
        before = item.title.cget("font")
        item.set_selected(True)
        self.assertEqual(item.title.cget("font"), before)
        self.assertEqual(item.indicator.cget("bg").lower(), theme.ACCENT.lower())
        item._activate()
        self.assertEqual(calls, [1])
        item.set_subtitle(None)
        self.assertEqual(item.subtitle.winfo_manager(), "")

    def test_text_field_validation_border(self):
        field = ui.TextField(self.root, mono=True)
        field.set_validation("error")
        self.assertEqual(field.cget("highlightbackground").lower(), theme.ERROR.lower())
        field.set_validation(None)
        self.assertEqual(field.cget("highlightbackground").lower(), theme.BORDER.lower())

    def test_explicit_color_and_font_override_theme_defaults(self):
        field = ui.TextField(self.root, fg=theme.TEXT_TERTIARY, font=ui.font("caption", mono=True))
        self.assertEqual(field.cget("fg").lower(), theme.TEXT_TERTIARY.lower())
        text = ui.label(self.root, "nota", "small", tone="secondary", fg=theme.WARNING, font=ui.font("caption"))
        self.assertEqual(text.cget("fg").lower(), theme.WARNING.lower())

    def test_brand_icon_uses_integer_reduction(self):
        icon = ui.brand_icon(self.root, 32)
        self.assertLessEqual(icon.image.width(), 256)
        self.assertGreaterEqual(icon.image.width(), ui.px(self.root, 32))  # nunca abaixo do pedido


if __name__ == "__main__":
    unittest.main()
