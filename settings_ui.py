"""Janela única de Configurações: navegação lateral, seções responsivas e estado de cada área.

As telas especializadas (GPUs, minerador, Defender, IA, atualizações, Central de operação)
são montadas como seções via `ui_host.Section`; suas confirmações e regras não mudam.
"""
from __future__ import annotations
import json
import os
import re
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config import STATE_DIR, LOG_DIR, DEFAULT_WALLET, SETTINGS_PATH
from fsutil import atomic_json
from file_cache import cached_load
from onboarding import read_settings, save_settings
from ui_host import Section
import ui_kit as ui
from monitor_theme import (ACCENT, ACCENT_TEXT, BRAND_NAVY, ERROR, BORDER, CONTROL, FIELD, SIDEBAR, SURFACE, TEAL,
                           TEXT, TEXT_SECONDARY, TEXT_TERTIARY, WARNING, ASSET_DIR)

ICON = {"Carteira e ajuda": "wallet", "GPUs": "gpu", "Temperatura": "temperature", "Minerador": "play",
        "Segurança": "shield", "IA": "sparkle", "Atualizações": "update", "Operação": "chart", "Sobre": "info"}
ROLE_TO_VARIANT = {"primary": "primary", "danger": "danger_ghost", "secondary": "secondary"}
NARROW = 900
PREFERENCES = STATE_DIR / "settings-ui.json"
WALLET_PATTERN = re.compile(r"prl1[ac-hj-np-z02-9]{30,100}")
DANGER_WORDS = ("Remover", "Parar", "Desativar", "Reverter")
SECTIONS = ("Carteira e ajuda", "GPUs", "Temperatura", "Minerador", "Segurança", "IA", "Atualizações", "Operação", "Sobre")


def wallet_feedback(value):
    """(estado, texto) da validação ao vivo do endereço Pearl."""
    value = value.strip()
    if not value:
        return "invalid", "Informe um endereço público Pearl (começa com prl1)."
    if not WALLET_PATTERN.fullmatch(value):
        return "invalid", "Endereço inválido: use o endereço público Pearl, começando com prl1. Nunca a seed."
    if value == DEFAULT_WALLET:
        return "author", "⚠ Esta é a carteira do autor: os pagamentos vão para ele, não para você."
    return "valid", "✓ Endereço válido. Os pagamentos irão para esta carteira."


def needs_restart(before, after):
    """Carteira global e pasta dos logs são lidas pelo painel na inicialização."""
    return any(str(before.get(key) or "") != str(after.get(key) or "") for key in ("wallet", "log_dir"))


def read_preferences(path=PREFERENCES):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def button_role(text, background):
    if str(background).lower() == TEAL.lower() or text.startswith("Salvar"):
        return "primary"
    if any(word in text for word in DANGER_WORDS):
        return "danger"
    return "secondary"


def apply_theme(widget):
    """Um só estilo para os botões das seções, com foco de teclado visível."""
    ui.theme_buttons(widget, role=lambda text, bg: ROLE_TO_VARIANT[button_role(text, bg)])
    apply_theme_menus(widget)


def apply_theme_menus(widget):
    for child in widget.winfo_children():
        if child.winfo_class() == "Menubutton":
            ui.style_option_menu(child)
        apply_theme_menus(child)


class Slot(tk.Frame):
    """Contêiner de uma seção; recebe pedidos de fechar e de navegar das telas montadas nele."""
    def __init__(self, master, settings, name):
        super().__init__(master, bg=SURFACE)
        self.settings, self.name = settings, name

    def section_closed(self, _section):
        self.settings.after_idle(lambda: self.settings.rebuild(self.name))

    def navigate(self, name):
        self.settings.after_idle(lambda: self.settings.show(name))


class SettingsWindow(tk.Toplevel):
    def __init__(self, root, section=None):
        super().__init__(root)
        self.root = root
        self.title("Configurações — HashPilot")
        self.configure(bg=SURFACE)
        ui.apply_styles(self)
        try:
            self.iconbitmap(str(os.path.join(os.path.dirname(__file__), "assets/brand/hashpilot.ico")))
        except tk.TclError:
            pass
        preferences = read_preferences()
        width, height = min(1120, self.winfo_screenwidth()-60), min(740, self.winfo_screenheight()-90)
        self.geometry(preferences.get("geometry") or f"{width}x{height}")
        self.minsize(min(560, width), min(440, height))
        self.slots, self.nav, self.current, self.narrow = {}, {}, None, None
        self.columnconfigure(2, weight=1)
        self.rowconfigure(1, weight=1)
        self.build_navigation()
        self.build_content()
        self.bind("<Configure>", self.on_resize, add="+")
        self.bind("<Escape>", lambda _: self.close())
        self.bind("<Control-s>", self.save_current)
        self.bind("<Control-S>", self.save_current)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.show(section if section in SECTIONS else preferences.get("section") if preferences.get("section") in SECTIONS else SECTIONS[0])
        self.refresh_status()
        self.focus_force()

    # Layout ---------------------------------------------------------------

    def build_navigation(self):
        self.sidebar = tk.Frame(self, bg=SIDEBAR, width=ui.px(self, 240))
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        self.sidebar.grid_propagate(False)
        self.sidebar_line = ui.Separator(self, vertical=True)
        self.sidebar_line.grid(row=0, column=1, rowspan=2, sticky="ns")
        ui.label(self.sidebar, "Configurações", "h2", bg=SIDEBAR).pack(fill="x", padx=ui.px(self, 16), pady=(ui.px(self, 18), ui.px(self, 12)))
        for name in SECTIONS:
            item = ui.SidebarItem(self.sidebar, name, lambda name=name: self.show(name), icon=ICON[name])
            item.pack(fill="x", padx=ui.px(self, 8), pady=1)
            self.nav[name] = item
        ui.label(self.sidebar, "Esc fecha · Ctrl+S salva", "caption", tone="tertiary", bg=SIDEBAR).pack(
            side="bottom", fill="x", padx=ui.px(self, 16), pady=ui.px(self, 12))
        self.topbar = tk.Frame(self, bg=SIDEBAR)
        ui.label(self.topbar, "Configurações", "h3", bg=SIDEBAR).pack(side="left", padx=(ui.px(self, 12), ui.px(self, 8)), pady=ui.px(self, 8))
        self.section_choice = tk.StringVar()
        chooser = ttk.Combobox(self.topbar, textvariable=self.section_choice, values=SECTIONS, state="readonly")
        chooser.pack(side="left", fill="x", expand=True, padx=(0, ui.px(self, 12)), pady=ui.px(self, 8))
        chooser.bind("<<ComboboxSelected>>", lambda _: self.show(self.section_choice.get()))
        self.topbar_line = ui.Separator(self)

    def build_content(self):
        holder = self.holder = tk.Frame(self, bg=SURFACE)
        holder.grid(row=1, column=2, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(holder, bg=SURFACE, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.inner = tk.Frame(self.canvas, bg=SURFACE)
        self.inner_id = self.canvas.create_window(0, 0, anchor="nw", window=self.inner)
        self.inner.bind("<Configure>", lambda _: self.fit_content())
        self.canvas.bind("<Configure>", lambda _: self.fit_content())
        self.bind_all("<MouseWheel>", self.on_wheel, add="+")

    def fit_content(self):
        if not self.winfo_exists():
            return
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        needed = self.inner.winfo_reqheight()
        self.canvas.itemconfigure(self.inner_id, width=width, height=max(needed, height))
        self.canvas.configure(scrollregion=(0, 0, width, max(needed, height)))
        if needed > height:
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()

    def on_wheel(self, event):
        try:
            if self.winfo_exists() and str(event.widget).startswith(str(self)) and self.inner.winfo_reqheight() > self.canvas.winfo_height():
                self.canvas.yview_scroll(int(-event.delta/120), "units")
        except (tk.TclError, AttributeError):
            pass

    def on_resize(self, event=None):
        if event is not None and event.widget is not self:
            return
        narrow = self.winfo_width() < NARROW
        if narrow != self.narrow:
            self.narrow = narrow
            if narrow:
                self.sidebar.grid_remove()
                self.sidebar_line.grid_remove()
                self.topbar.grid(row=0, column=0, columnspan=3, sticky="ew")
                self.topbar_line.grid(row=0, column=0, columnspan=3, sticky="sew")
                self.holder.grid_configure(row=1, column=0, columnspan=3)
            else:
                self.topbar.grid_remove()
                self.topbar_line.grid_remove()
                self.sidebar.grid()
                self.sidebar_line.grid()
                self.holder.grid_configure(row=1, column=2, columnspan=1)
        self.after_idle(self.wrap_labels)


    def wrap_labels(self):
        """Textos acompanham a largura útil; antes ficavam presos à largura inicial da janela."""
        if not self.current or not self.winfo_exists():
            return
        self.update_idletasks()
        right = self.canvas.winfo_rootx() + self.canvas.winfo_width() - self.margin() - ui.px(self, 4)
        def walk(widget, limit=right):
            if isinstance(widget, tk.Frame):  # respiro interno (cartões) reduz a largura útil dos filhos
                try:
                    inner = int(str(widget.cget("padx"))) + int(str(widget.cget("highlightthickness")))
                    limit = min(limit, widget.winfo_rootx() + widget.winfo_width() - inner)
                except (tk.TclError, ValueError):
                    pass
            for child in widget.winfo_children():
                if child.winfo_class() in ("Label", "TLabel", "Checkbutton"):
                    try:
                        if int(str(child.cget("wraplength")) or 0) > 0:
                            # Largura disponível a partir da posição real do texto (cartões têm respiro interno).
                            child.configure(wraplength=max(280, limit - child.winfo_rootx()))
                    except (tk.TclError, ValueError):
                        pass
                walk(child, limit)
        walk(self.slots[self.current])
        self.fit_content()

    # Sections -------------------------------------------------------------
    def show(self, name):
        if name not in SECTIONS or not self.winfo_exists():
            return
        if self.current and self.current in self.slots:
            self.slots[self.current].pack_forget()
        if name not in self.slots:
            self.slots[name] = self.build(name)
        self.slots[name].pack(fill="both", expand=True)
        self.current = name
        self.section_choice.set(name)
        for key, item in self.nav.items():
            item.set_selected(key == name)
        self.canvas.yview_moveto(0)
        self.after_idle(self.wrap_labels)
        self.save_preferences()

    def build(self, name):
        slot = Slot(self.inner, self, name)
        builders = {"Carteira e ajuda": self.build_wallet, "GPUs": self.build_gpus, "Temperatura": self.build_temperature,
                    "Minerador": self.build_miner,
                    "Segurança": lambda s: __import__("defender_setup").show_defender_setup(self.root, container=s),
                    "IA": lambda s: __import__("ai_setup").show_ai_setup(self.root, container=s),
                    "Atualizações": lambda s: __import__("app_update").show_update_setup(self.root, container=s),
                    "Operação": lambda s: __import__("operations_ui").show_operations(self.root, container=s),
                    "Sobre": self.build_about}
        try:
            content = builders[name](slot)
        except Exception as error:  # A broken section must not take the whole window down.
            content = ui.label(slot, f"Não foi possível abrir {name}: {error}", "body", tone="warning", bg=SURFACE, wraplength=600)
        if content is not None and content.winfo_manager() == "":
            content.pack(fill="both", expand=True)
        apply_theme(slot)
        return slot

    def rebuild(self, name):
        old = self.slots.pop(name, None)
        if old is not None:
            self.release(old)
            old.destroy()
        if self.current == name:
            self.current = None
        self.show(name)

    @staticmethod
    def release(widget):
        for child in widget.winfo_children():
            if isinstance(child, Section):
                child.release = True
            SettingsWindow.release(child)

    def heading(self, parent, title, text=None):
        ui.label(parent, title, "h1").pack(fill="x", padx=self.margin(), pady=(ui.px(self, 24), ui.px(self, 4)))
        if text:
            ui.label(parent, text, "body", tone="secondary", wraplength=640).pack(fill="x", padx=self.margin(), pady=(0, ui.px(self, 8)))

    def margin(self):
        return ui.px(self, 32)

    def field_label(self, parent, text, **pack):
        pack.setdefault("padx", self.margin())
        pack.setdefault("pady", (ui.px(self, 12), ui.px(self, 6)))
        label = ui.label(parent, text, "label")
        label.pack(fill="x", **pack)
        return label

    def build_wallet(self, slot):
        frame = tk.Frame(slot, bg=SURFACE)
        settings = read_settings()
        m = self.margin()
        loaded = {"wallet": settings.get("wallet", DEFAULT_WALLET), "log_dir": settings.get("log_dir") or str(LOG_DIR)}
        wallet, logs = tk.StringVar(value=loaded["wallet"]), tk.StringVar(value=loaded["log_dir"])
        support = tk.BooleanVar(value=settings.get("support_enabled", True) is True)
        self.heading(frame, "Carteira e ajuda", "Endereço que recebe os pagamentos da pool. GPUs sem carteira própria usam este endereço. O monitor nunca precisa de seed ou chave privada.")
        self.field_label(frame, "Carteira global", pady=(ui.px(self, 8), ui.px(self, 6)))
        row = tk.Frame(frame, bg=SURFACE)
        row.pack(fill="x", padx=m)
        entry = ui.TextField(row, wallet, mono=True)
        entry.pack(side="left", fill="x", expand=True, ipady=ui.px(self, 1))
        def paste():
            try:
                wallet.set(self.clipboard_get().strip())
            except tk.TclError:
                pass
        ui.Button(row, "Colar", paste, variant="secondary", bg=SURFACE).pack(side="left", padx=(ui.px(self, 8), 0))
        feedback = ui.label(frame, "", "small", wraplength=640)
        feedback.pack(fill="x", padx=m, pady=(ui.px(self, 6), ui.px(self, 8)))
        support_frame = ui.Card(frame, padding="md")
        ui.check(support_frame, "Contribuir até 5% do tempo conectado para ajudar pela ferramenta", support, bg=support_frame.bg,
                 anchor="w", wraplength=600, justify="left").pack(fill="x")
        ui.label(support_frame, "Após 57 min autorizados para sua carteira, até 3 min vão para a carteira abaixo. Não retira saldo e não garante 5% das moedas. Vale no próximo início do minerador.",
                 "small", tone="secondary", bg=support_frame.bg, wraplength=600).pack(fill="x", pady=(ui.px(self, 2), ui.px(self, 4)))
        tk.Label(support_frame, text="Recebe a contribuição: " + DEFAULT_WALLET, bg=support_frame.bg, fg=TEXT_TERTIARY,
                 font=ui.font("caption", mono=True), anchor="w").pack(fill="x")
        logs_title = self.field_label(frame, "Pasta dos logs do SRBMiner", pady=(ui.px(self, 16), ui.px(self, 6)))
        logs_row = tk.Frame(frame, bg=SURFACE)
        ui.TextField(logs_row, logs).pack(side="left", fill="x", expand=True, ipady=ui.px(self, 1))
        def browse():
            chosen = filedialog.askdirectory(parent=self, title="Pasta dos logs")
            if chosen:
                logs.set(chosen)
        ui.Button(logs_row, "Escolher", browse, variant="secondary", bg=SURFACE).pack(side="left", padx=(ui.px(self, 8), 0))
        status = ui.label(frame, "", "small", tone="secondary", wraplength=640)
        actions = tk.Frame(frame, bg=SURFACE)

        def update(*_):
            state, text = wallet_feedback(wallet.get())
            tone, validation = {"valid": (ACCENT_TEXT, "ok"), "author": (WARNING, "warning")}.get(state, (ERROR, "error"))
            feedback.configure(text=text, fg=tone)
            entry.set_validation(validation)
            if state == "valid":
                support_frame.pack(fill="x", padx=m, pady=ui.px(self, 4), before=logs_title)
            else:
                support_frame.pack_forget()
            self.after_idle(self.wrap_labels)
        wallet.trace_add("write", update)

        def save():
            state, text = wallet_feedback(wallet.get())
            if state == "invalid":
                status.configure(text=text, fg=WARNING)
                return False
            try:
                before = read_settings()
                save_settings(wallet.get(), logs.get(), support_enabled=support.get())
                after = read_settings()
            except (ValueError, OSError) as error:
                messagebox.showerror("Configurações", str(error), parent=self)
                return False
            loaded.update(wallet=after.get("wallet"), log_dir=after.get("log_dir"))
            status.configure(text="✓ Salvo. GPUs que usam a carteira global passam a usá-la na próxima reconciliação do serviço.", fg=ACCENT_TEXT)
            self.refresh_status()
            if needs_restart(before, after) and callable(getattr(self.root, "close", None)) and messagebox.askyesno(
                    "Reiniciar o painel",
                    "A carteira global ou a pasta dos logs mudou. O painel lê essas opções ao abrir, para histórico e rendimentos.\n\n"
                    "Reiniciar o painel agora? A mineração não é interrompida.", parent=self):
                self.close()
                self.root.restart_requested = True
                self.root.close()
            return True

        def discard():
            wallet.set(loaded["wallet"])
            logs.set(loaded["log_dir"])
            support.set(read_settings().get("support_enabled", True) is True)
            status.configure(text="Alterações descartadas.", fg=TEXT_SECONDARY)

        logs_row.pack(fill="x", padx=m)
        actions.pack(fill="x", padx=m, pady=(ui.px(self, 18), ui.px(self, 4)))
        ui.Button(actions, "Salvar", save, variant="primary", bg=SURFACE).pack(side="right")
        ui.Button(actions, "Descartar", discard, variant="ghost", bg=SURFACE).pack(side="right", padx=ui.px(self, 8))
        status.pack(fill="x", padx=m)
        frame.save = save
        update()
        return frame

    def build_temperature(self, slot):
        from thermal_target import MIN_TARGET_C
        frame = tk.Frame(slot, bg=SURFACE)
        settings = read_settings()
        m = self.margin()
        current = settings.get("target_temperature_c")
        enabled = tk.BooleanVar(value=isinstance(current, (int, float)))
        value = tk.IntVar(value=int(current) if isinstance(current, (int, float)) else 72)
        self.heading(frame, "Temperatura-alvo",
                     "Você escolhe a temperatura; o HashPilot ajusta o clock sozinho, a cada 2 s, para tirar o máximo sem passar dela.")
        ui.check(frame, "Controlar o clock pela temperatura-alvo", enabled, anchor="w").pack(fill="x", padx=m, pady=(ui.px(self, 8), ui.px(self, 4)))
        row = tk.Frame(frame, bg=SURFACE)
        row.pack(fill="x", padx=m, pady=ui.px(self, 4))
        ui.label(row, "Alvo (°C)", "label").pack(side="left")
        spin = tk.Spinbox(row, from_=int(MIN_TARGET_C), to=80, increment=1, textvariable=value, width=5, font=ui.font("body"),
                          bg=FIELD, fg=TEXT, insertbackground=TEXT, buttonbackground=CONTROL, relief="flat",
                          highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
        spin.pack(side="left", padx=(ui.px(self, 10), 0), ipady=ui.px(self, 4))
        rules = ui.Card(frame, padding="md")
        rules.pack(fill="x", padx=m, pady=(ui.px(self, 12), ui.px(self, 4)))
        ui.label(rules, (
            "• Acima do alvo com a ventoinha no máximo: reduz o clock levemente (15 MHz).\n"
            "• Bem acima do alvo (+2 °C): reduz mesmo antes da ventoinha chegar ao máximo.\n"
            "• Sobrando 2 °C ou mais por 20 s: sobe o clock levemente.\n"
            "• Aprende: um clock que estourou o alvo, ou que não aumentou o hashrate, vira teto por 10 min.\n"
            "Faixa permitida: 50–80 °C. As proteções de emergência (pausa em 83 °C) continuam valendo. "
            "Desmarcado, vale o controle automático anterior."), "small", tone="secondary", bg=rules.bg, wraplength=640).pack(fill="x")
        objective = tk.StringVar(value="profit" if settings.get("optimize_for") == "profit" else "hashrate")
        price_value = settings.get("electricity_price_brl_kwh")
        price = tk.StringVar(value=f"{price_value:.2f}".replace(".", ",") if isinstance(price_value, (int, float)) else "")
        self.field_label(frame, "Objetivo da otimização (núcleo e memória)", pady=(ui.px(self, 16), ui.px(self, 4)))
        for key, label in (("hashrate", "Máximo hashrate — mais TH/s dentro do alvo"),
                           ("profit", "Máximo lucro — receita menos o custo da energia")):
            ui.radio(frame, label, objective, key, anchor="w").pack(fill="x", padx=m)
        price_row = tk.Frame(frame, bg=SURFACE)
        price_row.pack(fill="x", padx=m, pady=(ui.px(self, 8), 0))
        ui.label(price_row, "Preço da energia (R$/kWh)", "body").pack(side="left")
        ui.TextField(price_row, price, width=8).pack(side="left", padx=(ui.px(self, 10), 0))
        ui.label(frame, "O lucro usa a previsão de receita do HashPilot e o consumo medido de cada clock de memória. "
                 "Sem preço informado, vale o máximo hashrate.", "small", tone="secondary", wraplength=640).pack(
                     fill="x", padx=m, pady=(ui.px(self, 4), ui.px(self, 4)))
        status = ui.label(frame, "", "small", tone="secondary", wraplength=640)

        def save():
            text = price.get().strip().replace(",", ".")
            try:
                price_kwh = float(text) if text else None
            except ValueError:
                price_kwh = -1
            if price_kwh is not None and not 0 <= price_kwh <= 20:
                status.configure(text="Preço da energia inválido: use algo como 0,90.", fg=WARNING)
                return False
            if objective.get() == "profit" and price_kwh is None:
                status.configure(text="Para otimizar lucro, informe o preço da energia.", fg=WARNING)
                return False
            try:
                chosen = int(value.get())
            except (tk.TclError, ValueError):
                status.configure(text="Informe um número inteiro entre 50 e 80.", fg=WARNING)
                return False
            if enabled.get() and not MIN_TARGET_C <= chosen <= 80:
                status.configure(text="Informe um número inteiro entre 50 e 80.", fg=WARNING)
                return False
            try:
                data = read_settings()
                if enabled.get():
                    data["target_temperature_c"] = chosen
                else:
                    data.pop("target_temperature_c", None)
                data["optimize_for"] = objective.get()
                if price_kwh is None:
                    data.pop("electricity_price_brl_kwh", None)
                else:
                    data["electricity_price_brl_kwh"] = round(price_kwh, 4)
                atomic_json(SETTINGS_PATH, data, durable=True)
            except (ValueError, OSError) as error:
                messagebox.showerror("Configurações", str(error), parent=self)
                return False
            status.configure(text=(f"✓ Salvo. O serviço aplica o alvo de {chosen} °C em até 10 s." if enabled.get()
                                   else "✓ Salvo. Controle automático anterior reativado em até 10 s."), fg=ACCENT_TEXT)
            self.refresh_status()
            return True

        actions = tk.Frame(frame, bg=SURFACE)
        actions.pack(fill="x", padx=m, pady=(ui.px(self, 18), ui.px(self, 4)))
        ui.Button(actions, "Salvar", save, variant="primary", bg=SURFACE).pack(side="right")
        status.pack(fill="x", padx=m)
        frame.save = save
        return frame

    def build_gpus(self, slot):
        from onboarding import run_gpu_setup
        return run_gpu_setup(self.root, selected_uuid=getattr(self.root, "selected_gpu_uuid", None), container=slot)

    def build_miner(self, slot):
        frame = tk.Frame(slot, bg=SURFACE)
        frame.pack(fill="x")
        self.heading(frame, "Minerador", "Instalar, iniciar e atualizar o SRBMiner são ações separadas das configurações: salvar uma opção nunca inicia a mineração.")
        state = ui.label(frame, self.miner_summary(), "body", wraplength=640)
        state.pack(fill="x", padx=self.margin(), pady=ui.px(self, 4))
        def install():
            from gpu_agent import detect_nvidia_gpus
            from onboarding import confirm_setup_mining, launch_mining_setup
            settings = read_settings()
            wallet = settings.get("wallet", DEFAULT_WALLET)
            try:
                if not messagebox.askyesno("Instalar e iniciar o minerador",
                        "Baixar o SRBMiner oficial (SHA-256 conferido), instalar e iniciar a mineração com a carteira configurada?\n\n"
                        "O Windows pode pedir aprovação do administrador.", parent=self):
                    return
                if not confirm_setup_mining(self, wallet, settings, detect_nvidia_gpus(max_devices=6)):
                    return
                launch_mining_setup(wallet)
            except (ValueError, OSError, RuntimeError) as error:
                messagebox.showerror("Minerador", str(error), parent=self)
        ui.Button(frame, "Instalar e iniciar o minerador…", install, variant="secondary", bg=SURFACE).pack(
            anchor="w", padx=self.margin(), pady=(ui.px(self, 6), ui.px(self, 10)))
        from miner_update_ui import show_miner_update
        return show_miner_update(self.root, container=slot)

    def build_about(self, slot):
        from component_status import identity
        frame = tk.Frame(slot, bg=SURFACE)
        self.brand_plate(frame)
        self.heading(frame, "Sobre", "HashPilot — Tech Fernandes. Código sob GPL-3.0-only; a marca e os logos não são licenciados sob GPL.")
        registry = self.registry()
        panel = identity()
        lines = [f"Painel: {panel['version']}" + (f" · {panel['revision'][:7]}" if panel.get("revision") else ""),
                 f"Serviço do rig: {self.service_summary(registry)}",
                 f"Minerador: {self.miner_summary()}",
                 f"Dados locais: {STATE_DIR}",
                 "",
                 "Atalhos: Ctrl+, abre as Configurações · Esc fecha · Ctrl+S salva a seção atual."]
        ui.label(frame, "\n".join(lines), "body", wraplength=640).pack(fill="x", padx=self.margin(), pady=ui.px(self, 6))
        actions = tk.Frame(frame, bg=SURFACE)
        actions.pack(fill="x", padx=self.margin(), pady=ui.px(self, 10))
        ui.Button(actions, "Abrir pasta de dados", lambda: os.startfile(STATE_DIR), variant="secondary", bg=SURFACE).pack(side="left")
        def wizard():
            from onboarding import run_setup
            self.close()
            if run_setup(self.root) and callable(getattr(self.root, "close", None)):
                self.root.restart_requested = True
                self.root.close()
        ui.Button(actions, "Refazer assistente inicial…", wizard, variant="ghost", bg=SURFACE).pack(side="left", padx=ui.px(self, 8))
        return frame

    def brand_plate(self, parent):
        """Assinatura Tech Fernandes: o PNG tem fundo azul-marinho embutido, então vira uma placa do mesmo tamanho."""
        try:
            self.about_logo = tk.PhotoImage(master=self, file=str(ASSET_DIR / "logo-header-240.png"))
        except (tk.TclError, OSError):
            return
        plate = tk.Frame(parent, bg=BRAND_NAVY, highlightthickness=1, highlightbackground=BORDER,
                         width=self.about_logo.width() + 2, height=self.about_logo.height() + 2)
        plate.pack(anchor="w", padx=self.margin(), pady=(ui.px(self, 24), 0))
        plate.pack_propagate(False)
        tk.Label(plate, image=self.about_logo, bg=BRAND_NAVY, bd=0).pack()

    # Status ---------------------------------------------------------------
    @staticmethod
    def registry():
        try:
            value = json.loads((STATE_DIR / "rig-manager.json").read_text(encoding="utf-8-sig"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def service_summary(registry):
        fresh = 0 <= time.time()-float(registry.get("sampled_at") or 0) <= 15 and registry.get("ready")
        version = (registry.get("software") or {}).get("version")
        return f"{version or 'versão não informada'} · {'ativo' if fresh else 'parado ou sem resposta'}"

    @staticmethod
    def miner_summary():
        try:
            from miner_update import active_executable, selected_version
            version = selected_version()
            present = active_executable(verify=False).is_file()
            return f"SRBMiner {version} · {'instalado' if present else 'não encontrado'}"
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            return f"indisponível ({error})"

    def statuses(self):
        settings = cached_load(SETTINGS_PATH, read_settings) if SETTINGS_PATH.exists() else {}
        devices = ((getattr(self.root, "last_data", None) or {}).get("rig") or {}).get("devices") or []
        registry = self.registry()
        from app_version import VERSION
        service = (registry.get("software") or {}).get("version")
        return {"Carteira e ajuda": "carteira do autor" if settings.get("wallet", DEFAULT_WALLET) == DEFAULT_WALLET else "sua carteira",
                "GPUs": f"{len(devices)} detectada{'s' if len(devices) != 1 else ''}",
                "Temperatura": f"alvo {settings['target_temperature_c']:.0f} °C" if isinstance(settings.get("target_temperature_c"), (int, float)) else "automática",
                "Minerador": self.miner_summary().removeprefix("SRBMiner "),
                "IA": "ativada" if settings.get("ai_enabled") else "desativada",
                "Atualizações": f"painel {VERSION}" + (f" · serviço {service}" if service and service != VERSION else ""),
                "Operação": self.service_summary(registry).split(" · ")[-1]}

    def refresh_status(self):
        if not self.winfo_exists():
            return
        try:
            for name, text in self.statuses().items():
                self.nav[name].set_subtitle(text)
        except (OSError, ValueError, tk.TclError):
            pass
        self.after(5000, self.refresh_status)

    # Lifecycle ------------------------------------------------------------
    def save_current(self, _event=None):
        content = self.slots.get(self.current)
        save = next((getattr(child, "save", None) for child in (content.winfo_children() if content else []) if callable(getattr(child, "save", None))), None)
        if save:
            save()
        return "break"

    def save_preferences(self):
        try:
            atomic_json(PREFERENCES, {"section": self.current, "geometry": self.geometry()})
        except OSError:
            pass

    def close(self):
        if not self.winfo_exists():
            return
        if getattr(self.root, "update_install_busy", False):
            messagebox.showinfo("Atualização em andamento", "Aguarde a conclusão da instalação antes de fechar. A mineração continua independente.", parent=self)
            return
        self.save_preferences()
        self.release(self)
        self.unbind_all("<MouseWheel>")
        self.destroy()


def show_settings(root, section=None):
    """Abre (ou traz para frente) a única janela de Configurações do painel."""
    window = getattr(root, "settings_window", None)
    if window is not None and window.winfo_exists():
        window.deiconify()
        window.lift()
        window.focus_force()
        if section:
            window.show(section)
        return window
    root.settings_window = SettingsWindow(root, section)
    return root.settings_window
