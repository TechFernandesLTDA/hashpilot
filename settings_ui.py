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

BG, PANEL, BORDER, TEXT, MUTED = "#0A1628", "#10213B", "#23446D", "#FFFFFF", "#B9C7DC"
TEAL, BLUE, ORANGE, DANGER = "#12A98E", "#1C64C8", "#ffbc66", "#9E3333"
FONT = "Segoe UI"
NARROW = 900
PREFERENCES = STATE_DIR / "settings-ui.json"
WALLET_PATTERN = re.compile(r"prl1[ac-hj-np-z02-9]{30,100}")
DANGER_WORDS = ("Remover", "Parar", "Desativar", "Reverter")
SECTIONS = ("Carteira e ajuda", "GPUs", "Minerador", "Segurança", "IA", "Atualizações", "Operação", "Sobre")


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
    for child in widget.winfo_children():
        if child.winfo_class() == "Button":
            role = button_role(child.cget("text"), child.cget("bg"))
            color = {"primary": TEAL, "danger": DANGER, "secondary": PANEL}[role]
            child.configure(bg=color, fg=TEXT, activebackground=BORDER if role == "secondary" else BLUE,
                            activeforeground=TEXT, relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
                            highlightthickness=2, highlightbackground=BG, highlightcolor=TEAL,
                            disabledforeground=MUTED, font=(FONT, -13, "bold" if role == "primary" else "normal"))
        elif child.winfo_class() == "Menubutton":
            child.configure(bg=PANEL, fg=TEXT, activebackground=BORDER, activeforeground=TEXT, relief="flat",
                            highlightthickness=0)
        apply_theme(child)


class Slot(tk.Frame):
    """Contêiner de uma seção; recebe pedidos de fechar e de navegar das telas montadas nele."""
    def __init__(self, master, settings, name):
        super().__init__(master, bg=BG)
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
        self.configure(bg=BG)
        try:
            self.iconbitmap(str(os.path.join(os.path.dirname(__file__), "assets/brand/hashpilot.ico")))
        except tk.TclError:
            pass
        preferences = read_preferences()
        width, height = min(1120, self.winfo_screenwidth()-60), min(740, self.winfo_screenheight()-90)
        self.geometry(preferences.get("geometry") or f"{width}x{height}")
        self.minsize(min(560, width), min(440, height))
        self.slots, self.nav, self.current, self.narrow = {}, {}, None, None
        self.style_ttk()
        self.columnconfigure(1, weight=1)
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

    def style_ttk(self):
        """ttk segue os mesmos tokens; o painel principal já usa o tema clam."""
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=TEXT,
                        bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL, selectbackground=BORDER, selectforeground=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)],
                  bordercolor=[("focus", TEAL)])
        style.configure("Ops.TButton", background=PANEL, foreground=TEXT, bordercolor=BORDER, lightcolor=PANEL,
                        darkcolor=PANEL, focuscolor=TEAL, padding=(12, 6), font=(FONT, 10))
        style.map("Ops.TButton", background=[("active", BORDER), ("disabled", BG)], foreground=[("disabled", MUTED)])
        style.configure("Ops.TNotebook", background=BG, bordercolor=BORDER, lightcolor=BG, darkcolor=BG, tabmargins=0)
        style.configure("Ops.TNotebook.Tab", background=PANEL, foreground=TEXT, bordercolor=BORDER, lightcolor=PANEL,
                        darkcolor=PANEL, padding=(14, 8), font=(FONT, 10))
        style.map("Ops.TNotebook.Tab", background=[("selected", BORDER)], foreground=[("selected", TEAL)])
        style.configure("Vertical.TScrollbar", background=PANEL, troughcolor=BG, bordercolor=BG, arrowcolor=TEXT,
                        lightcolor=PANEL, darkcolor=PANEL)
        for option, value in (("background", PANEL), ("foreground", TEXT), ("selectBackground", BORDER), ("selectForeground", TEXT)):
            self.option_add(f"*TCombobox*Listbox.{option}", value)

    # Layout ---------------------------------------------------------------

    def build_navigation(self):
        self.sidebar = tk.Frame(self, bg=PANEL, width=230)
        self.sidebar.grid(row=0, column=0, rowspan=2, sticky="nsw")
        self.sidebar.grid_propagate(False)
        tk.Label(self.sidebar, text="Configurações", bg=PANEL, fg=TEAL, font=(FONT, -20, "bold"), anchor="w").pack(fill="x", padx=16, pady=(16, 10))
        for name in SECTIONS:
            item = tk.Button(self.sidebar, text=name, anchor="w", justify="left", bg=PANEL, fg=TEXT, relief="flat", bd=0,
                             activebackground=BORDER, activeforeground=TEXT, cursor="hand2", padx=16, pady=7,
                             highlightthickness=2, highlightbackground=PANEL, highlightcolor=TEAL,
                             font=(FONT, -14), command=lambda name=name: self.show(name))
            item.pack(fill="x")
            self.nav[name] = item
        tk.Label(self.sidebar, text="Esc fecha · Ctrl+S salva", bg=PANEL, fg=MUTED, font=(FONT, -11), anchor="w").pack(side="bottom", fill="x", padx=16, pady=12)
        self.topbar = tk.Frame(self, bg=PANEL)
        tk.Label(self.topbar, text="Configurações", bg=PANEL, fg=TEAL, font=(FONT, -16, "bold")).pack(side="left", padx=(12, 8), pady=8)
        self.section_choice = tk.StringVar()
        chooser = ttk.Combobox(self.topbar, textvariable=self.section_choice, values=SECTIONS, state="readonly")
        chooser.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=8)
        chooser.bind("<<ComboboxSelected>>", lambda _: self.show(self.section_choice.get()))

    def build_content(self):
        holder = self.holder = tk.Frame(self, bg=BG)
        holder.grid(row=1, column=1, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.inner = tk.Frame(self.canvas, bg=BG)
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
                self.topbar.grid(row=0, column=0, columnspan=2, sticky="ew")
                self.holder.grid_configure(row=1, column=0, columnspan=2)
            else:
                self.topbar.grid_remove()
                self.sidebar.grid()
                self.holder.grid_configure(row=1, column=1, columnspan=1)
        self.after_idle(self.wrap_labels)


    def wrap_labels(self):
        """Textos acompanham a largura útil; antes ficavam presos à largura inicial da janela."""
        if not self.current or not self.winfo_exists():
            return
        width = max(280, self.canvas.winfo_width()-64)
        def walk(widget):
            for child in widget.winfo_children():
                if child.winfo_class() in ("Label", "TLabel", "Checkbutton"):
                    try:
                        if int(str(child.cget("wraplength")) or 0) > 0:
                            child.configure(wraplength=width)
                    except (tk.TclError, ValueError):
                        pass
                walk(child)
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
            item.configure(bg=BORDER if key == name else PANEL, fg=TEAL if key == name else TEXT,
                           font=(FONT, -14, "bold" if key == name else "normal"))
        self.canvas.yview_moveto(0)
        self.after_idle(self.wrap_labels)
        self.save_preferences()

    def build(self, name):
        slot = Slot(self.inner, self, name)
        builders = {"Carteira e ajuda": self.build_wallet, "GPUs": self.build_gpus, "Minerador": self.build_miner,
                    "Segurança": lambda s: __import__("defender_setup").show_defender_setup(self.root, container=s),
                    "IA": lambda s: __import__("ai_setup").show_ai_setup(self.root, container=s),
                    "Atualizações": lambda s: __import__("app_update").show_update_setup(self.root, container=s),
                    "Operação": lambda s: __import__("operations_ui").show_operations(self.root, container=s),
                    "Sobre": self.build_about}
        try:
            content = builders[name](slot)
        except Exception as error:  # A broken section must not take the whole window down.
            content = tk.Label(slot, text=f"Não foi possível abrir {name}: {error}", bg=BG, fg=ORANGE,
                               font=(FONT, -14), wraplength=600, justify="left")
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
        tk.Label(parent, text=title, bg=BG, fg=TEAL, font=(FONT, -22, "bold"), anchor="w").pack(fill="x", padx=24, pady=(18, 4))
        if text:
            tk.Label(parent, text=text, bg=BG, fg=MUTED, font=(FONT, -14), anchor="w", justify="left", wraplength=640).pack(fill="x", padx=24, pady=(0, 8))

    def build_wallet(self, slot):
        frame = tk.Frame(slot, bg=BG)
        settings = read_settings()
        loaded = {"wallet": settings.get("wallet", DEFAULT_WALLET), "log_dir": settings.get("log_dir") or str(LOG_DIR)}
        wallet, logs = tk.StringVar(value=loaded["wallet"]), tk.StringVar(value=loaded["log_dir"])
        support = tk.BooleanVar(value=settings.get("support_enabled", True) is True)
        self.heading(frame, "Carteira e ajuda", "Endereço que recebe os pagamentos da pool. GPUs sem carteira própria usam este endereço. O monitor nunca precisa de seed ou chave privada.")
        tk.Label(frame, text="Carteira global", bg=BG, fg=TEXT, font=(FONT, -14, "bold"), anchor="w").pack(fill="x", padx=24, pady=(8, 2))
        row = tk.Frame(frame, bg=BG)
        row.pack(fill="x", padx=24)
        entry = tk.Entry(row, textvariable=wallet, font=("Consolas", -14), bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                         highlightthickness=2, highlightbackground=BORDER, highlightcolor=TEAL)
        entry.pack(side="left", fill="x", expand=True, ipady=8)
        def paste():
            try:
                wallet.set(self.clipboard_get().strip())
            except tk.TclError:
                pass
        tk.Button(row, text="Colar", command=paste).pack(side="left", padx=(8, 0))
        feedback = tk.Label(frame, bg=BG, font=(FONT, -13), anchor="w", justify="left", wraplength=640)
        feedback.pack(fill="x", padx=24, pady=(4, 8))
        support_frame = tk.Frame(frame, bg=PANEL, padx=12, pady=8)
        tk.Checkbutton(support_frame, text="Contribuir até 5% do tempo conectado para ajudar pela ferramenta", variable=support,
                       bg=PANEL, fg=TEXT, selectcolor=BG, activebackground=PANEL, activeforeground=TEXT, anchor="w",
                       font=(FONT, -14), wraplength=600, justify="left").pack(fill="x")
        tk.Label(support_frame, text="Após 57 min autorizados para sua carteira, até 3 min vão para a carteira abaixo. Não retira saldo e não garante 5% das moedas. Vale no próximo início do minerador.",
                 bg=PANEL, fg=MUTED, font=(FONT, -12), anchor="w", justify="left", wraplength=600).pack(fill="x", pady=(2, 4))
        tk.Label(support_frame, text="Recebe a contribuição: " + DEFAULT_WALLET, bg=PANEL, fg=MUTED, font=("Consolas", -11), anchor="w").pack(fill="x")
        logs_title = tk.Label(frame, text="Pasta dos logs do SRBMiner", bg=BG, fg=TEXT, font=(FONT, -14, "bold"), anchor="w")
        logs_row = tk.Frame(frame, bg=BG)
        tk.Entry(logs_row, textvariable=logs, bg=PANEL, fg=TEXT, insertbackground=TEXT, relief="flat",
                 highlightthickness=2, highlightbackground=BORDER, highlightcolor=TEAL).pack(side="left", fill="x", expand=True, ipady=6)
        def browse():
            chosen = filedialog.askdirectory(parent=self, title="Pasta dos logs")
            if chosen:
                logs.set(chosen)
        tk.Button(logs_row, text="Escolher", command=browse).pack(side="left", padx=(8, 0))
        status = tk.Label(frame, text="", bg=BG, fg=MUTED, font=(FONT, -13), anchor="w", justify="left", wraplength=640)
        actions = tk.Frame(frame, bg=BG)

        def update(*_):
            state, text = wallet_feedback(wallet.get())
            feedback.configure(text=text, fg={"valid": TEAL, "author": ORANGE}.get(state, ORANGE))
            entry.configure(highlightbackground={"valid": TEAL, "author": ORANGE}.get(state, DANGER))
            if state == "valid":
                support_frame.pack(fill="x", padx=24, pady=4, before=logs_title)
            else:
                support_frame.pack_forget()
            self.after_idle(self.wrap_labels)
        wallet.trace_add("write", update)

        def save():
            state, text = wallet_feedback(wallet.get())
            if state == "invalid":
                status.configure(text=text, fg=ORANGE)
                return False
            try:
                before = read_settings()
                save_settings(wallet.get(), logs.get(), support_enabled=support.get())
                after = read_settings()
            except (ValueError, OSError) as error:
                messagebox.showerror("Configurações", str(error), parent=self)
                return False
            loaded.update(wallet=after.get("wallet"), log_dir=after.get("log_dir"))
            status.configure(text="✓ Salvo. GPUs que usam a carteira global passam a usá-la na próxima reconciliação do serviço.", fg=TEAL)
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
            status.configure(text="Alterações descartadas.", fg=MUTED)

        logs_title.pack(fill="x", padx=24, pady=(14, 2))
        logs_row.pack(fill="x", padx=24)
        actions.pack(fill="x", padx=24, pady=(18, 4))
        tk.Button(actions, text="Salvar", command=save).pack(side="right")
        tk.Button(actions, text="Descartar", command=discard).pack(side="right", padx=8)
        status.pack(fill="x", padx=24)
        frame.save = save
        update()
        return frame

    def build_gpus(self, slot):
        from onboarding import run_gpu_setup
        return run_gpu_setup(self.root, selected_uuid=getattr(self.root, "selected_gpu_uuid", None), container=slot)

    def build_miner(self, slot):
        frame = tk.Frame(slot, bg=BG)
        frame.pack(fill="x")
        self.heading(frame, "Minerador", "Instalar, iniciar e atualizar o SRBMiner são ações separadas das configurações: salvar uma opção nunca inicia a mineração.")
        state = tk.Label(frame, text=self.miner_summary(), bg=BG, fg=TEXT, font=(FONT, -14), anchor="w", justify="left", wraplength=640)
        state.pack(fill="x", padx=24, pady=4)
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
        tk.Button(frame, text="Instalar e iniciar o minerador…", command=install).pack(anchor="w", padx=24, pady=(6, 10))
        from miner_update_ui import show_miner_update
        return show_miner_update(self.root, container=slot)

    def build_about(self, slot):
        from component_status import identity
        frame = tk.Frame(slot, bg=BG)
        self.heading(frame, "Sobre", "HashPilot — Tech Fernandes. Código sob GPL-3.0-only; a marca e os logos não são licenciados sob GPL.")
        registry = self.registry()
        panel = identity()
        lines = [f"Painel: {panel['version']}" + (f" · {panel['revision'][:7]}" if panel.get("revision") else ""),
                 f"Serviço do rig: {self.service_summary(registry)}",
                 f"Minerador: {self.miner_summary()}",
                 f"Dados locais: {STATE_DIR}",
                 "",
                 "Atalhos: Ctrl+, abre as Configurações · Esc fecha · Ctrl+S salva a seção atual."]
        tk.Label(frame, text="\n".join(lines), bg=BG, fg=TEXT, font=(FONT, -14), anchor="w", justify="left", wraplength=640).pack(fill="x", padx=24, pady=6)
        actions = tk.Frame(frame, bg=BG)
        actions.pack(fill="x", padx=24, pady=10)
        tk.Button(actions, text="Abrir pasta de dados", command=lambda: os.startfile(STATE_DIR)).pack(side="left")
        def wizard():
            from onboarding import run_setup
            self.close()
            if run_setup(self.root) and callable(getattr(self.root, "close", None)):
                self.root.restart_requested = True
                self.root.close()
        tk.Button(actions, text="Refazer assistente inicial…", command=wizard).pack(side="left", padx=8)
        return frame

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
                "Minerador": self.miner_summary().removeprefix("SRBMiner "),
                "IA": "ativada" if settings.get("ai_enabled") else "desativada",
                "Atualizações": f"painel {VERSION}" + (f" · serviço {service}" if service and service != VERSION else ""),
                "Operação": self.service_summary(registry).split(" · ")[-1]}

    def refresh_status(self):
        if not self.winfo_exists():
            return
        try:
            for name, text in self.statuses().items():
                self.nav[name].configure(text=f"{name}\n{text}")
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
