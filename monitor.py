"""Monitor local de leitura: NVIDIA SMI + logs do SRBMiner. Sem dependências externas."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import shutil
import platform
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import sys
from tkinter import ttk
from collections import deque
from finance import FinanceCollector, financial_model
from history import HistoryStore
from config import WALLET, POOL, LOG_DIR, STATE_DIR
from tray import WindowsTray
from bootstrap import restore_bundled_data

APP_NAME = "HashPilot — Tech Fernandes"
ASSET_DIR = Path(__file__).resolve().parent / "assets/brand"
BODY_FONT, TITLE_FONT = "Segoe UI", "Segoe UI"
BG, PANEL, BORDER, TEXT, MUTED = "#0A1628", "#10213B", "#23446D", "#FFFFFF", "#E8EDF5"
TEAL, BLUE, ORANGE, PINK = "#12A98E", "#1C64C8", "#ffbc66", "#E8EDF5"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
STAMP = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]")
RATE = re.compile(r"GPU0\s+.*?:\s*([\d.]+)\s+([kMGTPE]?H)/s\s+\[(.*?)\]")


def run(command: list[str], timeout: float = 3) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip()[:220])
    return result.stdout


def number(value: str) -> float | None:
    try:
        return float(value.strip())
    except (ValueError, TypeError):
        return None


def read_gpu() -> dict:
    fields = ["name", "driver_version", "temperature.gpu", "power.draw", "power.limit",
              "clocks.gr", "clocks.mem", "utilization.gpu", "utilization.memory",
              "memory.used", "memory.total", "fan.speed", "pstate"]
    smi = shutil.which("nvidia-smi")
    if not smi and os.name == "nt":
        fallback = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/nvidia-smi.exe"
        smi = str(fallback) if fallback.exists() else None
    if not smi:
        raise RuntimeError("Sensores NVIDIA indisponíveis" + (" neste Mac ("+platform.machine()+"). GPU Apple não é monitorada pelo nvidia-smi." if sys.platform == "darwin" else ": nvidia-smi não encontrado."))
    output = run([smi, "-i", "0", "--query-gpu=" + ",".join(fields), "--format=csv,noheader,nounits"])
    row = next(csv.reader(io.StringIO(output)))
    result = dict(zip(fields, [x.strip() for x in row]))
    for field in fields[2:-1]:
        result[field] = number(result.get(field, ""))
    result["sampled_at"] = time.time()
    return result


def tail(path: Path, limit: int = 160_000) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        data = handle.read()
    text = data.decode("utf-8-sig", errors="replace")
    return text.split("\n", 1)[-1] if size > limit else text


def parse_log(text: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    result = {"hashrate": None, "hashrate_at": None, "accepted": 0, "rejected": 0,
              "hw_errors": 0, "average_1m": None, "average_15m": None, "started_at": None,
              "last_share_at": None, "connection": "Aguardando conexão", "paused": False, "rate_points": []}
    for line in text.splitlines():
        stamp_match = STAMP.match(line)
        stamp = dt.datetime.strptime(stamp_match[1], "%Y-%m-%d %H:%M:%S").timestamp() if stamp_match else None
        if "Miner version:" in line:
            result["started_at"] = stamp
        if "Connected to " in line:
            result["connection"] = "Conectado à pool"
        if any(word in line.lower() for word in ("disconnected", "connection failed", "reconnecting", "connection lost")):
            result["connection"] = "Reconectando à pool"
        if "share accepted" in line:
            result["last_share_at"] = stamp
        if "GPU0 turned off" in line:
            result["paused"] = True
        if "GPU0 turned on" in line:
            result["paused"] = False
        match = RATE.search(line)
        if match:
            multiplier = {"H": 1e-12, "kH": 1e-9, "MH": 1e-6, "GH": 1e-3, "TH": 1, "PH": 1e3, "EH": 1e6}[match[2]]
            result["hashrate"] = float(match[1]) * multiplier
            result["hashrate_at"] = stamp
            values = dict(re.findall(r"([A-Z]+):([\d.]+)", match[3]))
            for key, log_key in (("accepted", "A"), ("rejected", "R"), ("hw_errors", "HW")):
                result[key] = int(float(values.get(log_key, 0)))
            if stamp and now - 900 <= stamp <= now + 5:
                result["rate_points"].append((stamp, result["hashrate"]))
        average = re.search(r"Average hashrate: 1m ([\d.]+) TH/s \| 15m ([\d.]+) TH/s", line)
        if average:
            result["average_1m"], result["average_15m"] = map(float, average.groups())
    result["recent_logs"] = "\n".join(text.splitlines()[-35:])
    return result


class Collector:
    def __init__(self, history=None):
        self.samples: queue.Queue = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.log_path: Path | None = None
        self.log_signature = None
        self.log_data: dict = parse_log("")
        self.start_times: dict[str, float | None] = {}
        self.history = history

    def sample(self) -> dict:
        result = {"collected_at": time.time(), "gpu": None, "gpu_error": None,
                  "miner_running": None, "process_error": None, "log_error": None}
        try:
            result["gpu"] = read_gpu()
        except Exception as error:
            result["gpu_error"] = str(error)
        try:
            if os.name == "nt":
                processes = run(["tasklist", "/FI", "IMAGENAME eq SRBMiner-MULTI.exe", "/FO", "CSV", "/NH"])
                result["miner_running"] = "srbminer-multi.exe" in processes.lower()
            else:
                process = subprocess.run(["pgrep", "-i", "SRBMiner"], capture_output=True, text=True, timeout=3)
                if process.returncode not in (0, 1):
                    raise RuntimeError(process.stderr.strip() or "Não foi possível consultar o processo")
                result["miner_running"] = process.returncode == 0
        except Exception as error:
            result["process_error"] = str(error)
        try:
            files = list(LOG_DIR.glob("*.log"))
            path = max(files, key=lambda p: p.stat().st_mtime) if files else None
            if path:
                stat = path.stat()
                signature = (str(path), stat.st_mtime_ns, stat.st_size)
                if signature != self.log_signature:
                    text = tail(path)
                    self.log_data = parse_log(text)
                    if self.history:
                        self.history.archive_log(path)
                    if str(path) not in self.start_times:
                        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                            first = handle.readline()
                        stamp = STAMP.match(first)
                        self.start_times[str(path)] = dt.datetime.strptime(stamp[1], "%Y-%m-%d %H:%M:%S").timestamp() if stamp else None
                    self.log_signature = signature
                    self.log_path = path
                self.log_data["started_at"] = self.start_times.get(str(path))
            else:
                result["log_error"] = "Nenhum log de mineração encontrado."
        except Exception as error:
            result["log_error"] = str(error)
        result["miner"] = self.log_data.copy()
        result["log_path"] = str(self.log_path) if self.log_path else None
        return result

    def work(self):
        while not self.stop.is_set():
            started = time.monotonic()
            data = self.sample()
            if self.history:
                self.history.telemetry(data)
            try:
                self.samples.put_nowait(data)
            except queue.Full:
                try:
                    self.samples.get_nowait()
                except queue.Empty:
                    pass
                self.samples.put_nowait(data)
            self.stop.wait(max(0.2, 2 - (time.monotonic() - started)))


def fmt(value, suffix="", precision=0):
    return "—" if value is None else f"{value:,.{precision}f}".replace(",", " ") + suffix


class Chart(tk.Canvas):
    def __init__(self, master, title, color, unit, fixed=None, window=900):
        super().__init__(master, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, height=185)
        self.title, self.color, self.unit, self.fixed = title, color, unit, fixed
        self.window = window
        self.points = deque(maxlen=50000 if window > 900 else 900)
        self.bind("<Configure>", lambda _: self.draw())

    def add(self, timestamp, value):
        if self.points and timestamp <= self.points[-1][0]:
            return
        self.points.append((timestamp, value))

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40:
            return
        self.create_text(16, 18, anchor="w", text=self.title, fill=TEXT, font=(BODY_FONT, 10, "bold"))
        cutoff = time.time() - self.window
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()
        values = [v for _, v in self.points if v is not None]
        if not values:
            self.create_text(w/2, h/2, text="Aguardando amostras reais", fill=MUTED, font=(BODY_FONT, 10))
            return
        ymin, ymax = self.fixed or (max(0, min(values) * .85), max(values) * 1.1)
        if ymax <= ymin:
            ymax = ymin + 1
        left, right, top, bottom = 50, w-18, 44, h-28
        for i in range(4):
            y = top + (bottom-top)*i/3
            value = ymax - (ymax-ymin)*i/3
            self.create_line(left, y, right, y, fill=BORDER)
            self.create_text(left-8, y, text=f"{value:.2f}" if abs(ymax) < 2 else f"{value:.0f}", anchor="e", fill=MUTED, font=(BODY_FONT, 8))
        now = time.time()
        start = max(cutoff, min(p[0] for p in self.points))
        span = max(60, now-start)
        segment = []
        previous = None
        for timestamp, value in self.points:
            if value is None or (previous and timestamp - previous > 80):
                if len(segment) >= 4:
                    self.create_line(*segment, fill=self.color, width=2)
                segment = []
            if value is not None:
                x = left + (timestamp-start)/span*(right-left)
                y = bottom - (value-ymin)/(ymax-ymin)*(bottom-top)
                segment.extend([x, y])
            previous = timestamp
        if len(segment) >= 4:
            self.create_line(*segment, fill=self.color, width=2)
        if segment:
            x, y = segment[-2:]
            self.create_oval(x-3, y-3, x+3, y+3, fill=self.color, outline="")
        self.create_text(left, h-12, text=dt.datetime.fromtimestamp(start).strftime("%H:%M:%S"), anchor="w", fill=MUTED, font=(BODY_FONT, 8))
        self.create_text(right, h-12, text="agora · " + self.unit, anchor="e", fill=MUTED, font=(BODY_FONT, 8))


class Monitor(tk.Tk):
    def __init__(self):
        restore_bundled_data()
        super().__init__()
        global BODY_FONT, TITLE_FONT
        families = set(tkfont.families(self))
        BODY_FONT = "Plus Jakarta Sans" if "Plus Jakarta Sans" in families else "Segoe UI"
        TITLE_FONT = "Sora" if "Sora" in families else "Segoe UI"
        self.title(APP_NAME)
        self.app_icon = tk.PhotoImage(file=ASSET_DIR / "icon-256.png")
        self.iconphoto(True, self.app_icon)
        self.geometry("1280x930")
        self.minsize(1050, 800)
        self.configure(bg=BG)
        self.history = HistoryStore(STATE_DIR / "history.sqlite3", WALLET)
        self.history_restore = queue.Queue()
        self.restored_points = {}
        self.ui_restored = False
        self.preference_timer = None
        self.collector = Collector(self.history)
        self.finance_collector = FinanceCollector(STATE_DIR, self.history)
        self.finance_state = {}
        self.last_data = None
        self.card_values = {}
        self.card_notes = {}
        self.log_contents = None
        self.summary_window = None
        self.console_window = None
        self.summary_values = {}
        self.tray_actions = queue.Queue()
        self.last_ui_request = time.time()
        self.tray = WindowsTray(ASSET_DIR / "hashpilot.ico", APP_NAME, self.tray_actions).start()
        self.build()
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.bind("<Unmap>", self.on_minimize)
        self.bind("<Configure>", self.schedule_preferences)
        self.tabs.bind("<<NotebookTabChanged>>", self.schedule_preferences)
        threading.Thread(target=self.restore_history, daemon=True, name="history-loader").start()
        threading.Thread(target=self.collector.work, daemon=True).start()
        threading.Thread(target=self.finance_collector.work, daemon=True).start()
        self.after(200, self.poll)

    def hide_to_tray(self):
        if self.tray.registered_ok:
            self.save_preferences()
            self.withdraw()
        else:
            # Nunca esconda a única janela se o ícone não pôde ser registrado.
            self.iconify()

    def on_minimize(self, event):
        if event.widget is self and self.state() == "iconic" and self.tray.registered_ok:
            self.after(80, self.hide_to_tray)

    def show_panel(self):
        self.deiconify()
        self.state("normal")
        self.lift()

    def show_summary(self):
        if self.summary_window and self.summary_window.winfo_exists():
            self.summary_window.deiconify()
            self.summary_window.lift()
            return
        window = self.summary_window = tk.Toplevel(self)
        window.title("HashPilot · Resumo ao vivo")
        window.geometry("590x620")
        window.minsize(540, 600)
        window.configure(bg=BG)
        window.iconphoto(True, self.app_icon)
        self.label(window, "HashPilot", font=(TITLE_FONT, 23, "bold"), fg=TEAL).pack(anchor="w", padx=22, pady=(18, 2))
        self.label(window, "TECH FERNANDES  /  RESUMO AO VIVO", fg=MUTED, font=(BODY_FONT, 9, "bold")).pack(anchor="w", padx=23, pady=(0, 14))
        grid = tk.Frame(window, bg=PANEL, padx=16, pady=12)
        grid.pack(fill="both", expand=True, padx=22)
        grid.columnconfigure(1, weight=1)
        for row, (key, title) in enumerate([
            ("status", "Estado"), ("hash", "Hashrate"), ("gpu", "GPU · temperatura · consumo"),
            ("clock", "Núcleo / memória / fan"), ("shares", "Aceitas / rejeitadas / erros"),
            ("today", "Hoje · observado parcial"), ("pool", "Acumulado na pool"),
            ("week", "Projeção · próximos 7 dias"), ("month", "Projeção · próximos 30 dias"),
            ("price", "Cotação atual · 1 PRL"), ("history", "Histórico permanente")]):
            self.label(grid, title, bg=PANEL, fg=MUTED, anchor="w", font=(BODY_FONT, 9)).grid(row=row, column=0, sticky="w", pady=9)
            value = self.label(grid, "—", bg=PANEL, fg=TEXT, font=(BODY_FONT, 10, "bold"), anchor="e")
            value.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=9)
            self.summary_values[key] = value
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=22, pady=16)
        for text, command in (("Abrir painel", self.show_panel), ("Console resumido", self.open_console), ("Recolher resumo", window.withdraw)):
            tk.Button(buttons, text=text, command=command, bg=PANEL, fg=TEXT, activebackground=BORDER,
                      activeforeground=TEXT, relief="flat", padx=12, pady=9, font=(BODY_FONT, 9)).pack(side="left", padx=(0, 8))
        self.refresh_summary()

    def open_console(self):
        if os.name != "nt":
            if self.console_window and self.console_window.winfo_exists():
                self.console_window.deiconify()
                self.console_window.lift()
                return
            window = self.console_window = tk.Toplevel(self)
            window.title(APP_NAME + " · Console resumido")
            window.geometry("900x420")
            window.configure(bg=BG)
            self.console_widget = tk.Text(window, bg=BG, fg=TEXT, font=("Menlo", 10), padx=16, pady=16, wrap="word", state="disabled")
            self.console_widget.pack(fill="both", expand=True)
            self.refresh_summary()
            return
        python = Path(sys.executable).with_name("python.exe")
        subprocess.Popen([str(python), str(Path(__file__).resolve()), "--console"],
                         cwd=Path(__file__).resolve().parent, creationflags=subprocess.CREATE_NEW_CONSOLE)

    def refresh_summary(self):
        if self.console_window and self.console_window.winfo_exists():
            self.console_widget.configure(state="normal")
            self.console_widget.delete("1.0", "end")
            self.console_widget.insert("end", console_text(getattr(self, "last_snapshot", {}), getattr(self, "last_finance_model", {})))
            self.console_widget.configure(state="disabled")
        if not self.summary_window or not self.summary_window.winfo_exists():
            return
        data = self.last_data or {}
        gpu, miner = data.get("gpu") or {}, data.get("miner") or {}
        model = getattr(self, "last_finance_model", {})
        forecast = model.get("forecast") or {}
        money = lambda value: "N/D" if value is None else "R$ " + f"{value:.2f}".replace(".", ",")
        values = {
            "status": self.status.cget("text").replace("●  ", ""),
            "hash": self.card_values["hash"].cget("text"),
            "gpu": fmt(gpu.get("temperature.gpu"), " °C") + "  /  " + fmt(gpu.get("power.draw"), " W", 1),
            "clock": f"{fmt(gpu.get('clocks.gr'))} / {fmt(gpu.get('clocks.mem'))} MHz / {fmt(gpu.get('fan.speed'), '%')}",
            "shares": f"{miner.get('accepted', '—')} / {miner.get('rejected', '—')} / {miner.get('hw_errors', '—')}",
            "today": money(model.get("periods", {}).get("day", {}).get("brl")),
            "pool": money(model.get("accumulated_brl")),
            "week": money(forecast.get("brl_7days")), "month": money(forecast.get("brl_30days")),
            "price": money(model.get("price_brl")),
            "history": f"{self.history.info.get('telemetry', (0,))[0]} amostras · {'gravando' if not self.history.last_error else 'aguardando'}",
        }
        for key, value in values.items():
            self.summary_values[key].configure(text=value)

    def restore_history(self):
        self.history.ready.wait(2)
        try:
            previous = json.loads((STATE_DIR / "latest.json").read_text(encoding="utf-8"))
            if previous.get("collected_at"):
                self.history.telemetry(previous, source="legacy_snapshot")
        except (OSError, ValueError):
            pass
        saved = self.history.restore(time.time()-900)
        offsets = saved.get("preferences", {}).get("import_offsets", {})
        # Recupera também medições reais que já estavam nos logs antes de a
        # persistência existir. Offset só é salvo após enfileirar todo o arquivo.
        for path in LOG_DIR.glob("*.log"):
            try:
                stat = path.stat()
                offset = offsets.get(str(path), 0)
                if offset > stat.st_size:
                    offset = 0
                with path.open("rb") as handle:
                    handle.seek(offset)
                    while line_bytes := handle.readline():
                        line = line_bytes.decode("utf-8-sig", errors="replace").rstrip()
                        stamp_match = STAMP.match(line)
                        stamp = dt.datetime.strptime(stamp_match[1], "%Y-%m-%d %H:%M:%S").timestamp() if stamp_match else stat.st_mtime
                        parsed = parse_log(line, now=stamp)
                        if parsed.get("hashrate") is not None:
                            match = RATE.search(line)
                            fields = dict(re.findall(r"([A-Z]+):([\d.]+)", match[3]))
                            gpu = {key: number(fields.get(field, "")) for key, field in (
                                ("temperature.gpu", "T"), ("power.draw", "P"), ("fan.speed", "FAN"),
                                ("clocks.gr", "CC"), ("clocks.mem", "MC"))}
                            self.history.telemetry({"collected_at": stamp, "gpu": gpu, "miner": parsed,
                                "miner_running": None, "log_path": str(path)}, source="imported_miner_log")
                        else:
                            import hashlib
                            key = "log:"+hashlib.sha256((str(path)+"\n"+line).encode()).hexdigest()
                            self.history.event(stamp, "miner_log", {"line": line, "path": str(path)}, key=key)
                        if self.history.jobs.qsize() > 500:
                            self.history.flush(1)
                    offsets[str(path)] = handle.tell()
            except OSError as error:
                self.history.event(time.time(), "import_error", {"path": str(path), "error": str(error)})
        self.history.preferences({"import_offsets": offsets})
        self.history.flush(4)
        restored = self.history.restore(time.time()-900)
        self.history_restore.put(restored)

    def schedule_preferences(self, event=None):
        if not self.ui_restored:
            return
        if event is not None and event.widget not in (self, self.tabs):
            return
        if self.preference_timer:
            self.after_cancel(self.preference_timer)
        self.preference_timer = self.after(700, self.save_preferences)

    def save_preferences(self):
        self.preference_timer = None
        self.history.preferences({"geometry": self.geometry(), "tab": self.tabs.index(self.tabs.select())})

    def label(self, master, text="", **kwargs):
        return tk.Label(master, text=text, bg=kwargs.pop("bg", BG), fg=kwargs.pop("fg", TEXT),
                        font=kwargs.pop("font", (BODY_FONT, 10)), **kwargs)

    def build(self):
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=24, pady=(16, 8))
        self.brand_logo = tk.PhotoImage(file=ASSET_DIR / "logo-header.png")
        tk.Label(head, image=self.brand_logo, bg=BG).pack(side="left", padx=(0, 22))
        title = tk.Frame(head, bg=BG)
        title.pack(side="left")
        self.label(title, "HashPilot", fg=TEXT, font=(TITLE_FONT, 25, "bold")).pack(anchor="w")
        self.label(title, "MINERAÇÃO EM TEMPO REAL", fg=TEAL, font=(BODY_FONT, 9, "bold")).pack(anchor="w", pady=(5, 0))
        controls = tk.Frame(head, bg=BG)
        controls.pack(side="right", padx=4)
        self.status = self.label(controls, "●  Conectando aos sensores…", fg=MUTED, font=(BODY_FONT, 10, "bold"))
        self.status.pack(anchor="e", pady=(0, 10))
        buttons = tk.Frame(controls, bg=BG)
        buttons.pack(anchor="e")
        for text, command in (("Resumo", self.show_summary), ("Console", self.open_console), ("Recolher", self.hide_to_tray), ("Sair", self.close)):
            tk.Button(buttons, text=text, command=command, bg=PANEL, fg=MUTED, activebackground=BLUE,
                      activeforeground=TEXT, relief="flat", padx=10, pady=5, font=(BODY_FONT, 9)).pack(side="left", padx=(5, 0))
        self.subtitle = self.label(self, "RTX 3070 · PRL / pearlhash · atualização a cada 2 s", fg=MUTED)
        self.subtitle.pack(anchor="w", padx=25, pady=(0, 14))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(22, 9), font=(BODY_FONT, 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", BORDER)], foreground=[("selected", TEAL)])
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=4)
        self.live_tab, self.finance_tab = tk.Frame(self.tabs, bg=BG), tk.Frame(self.tabs, bg=BG)
        self.tabs.add(self.live_tab, text="GPU e mineração")
        self.tabs.add(self.finance_tab, text="Rendimentos em reais")
        cards = tk.Frame(self.live_tab, bg=BG)
        cards.pack(fill="x", padx=20)
        definitions = [("hash", "HASHRATE", TEAL), ("temp", "TEMPERATURA", ORANGE),
                       ("power", "POTÊNCIA", BLUE), ("fan", "VENTOINHAS", PINK), ("shares", "SHARES ACEITAS", TEAL)]
        for column, (key, title, color) in enumerate(definitions):
            cards.columnconfigure(column, weight=1, uniform="cards")
            frame = tk.Frame(cards, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
            frame.grid(row=0, column=column, sticky="nsew", padx=4)
            self.label(frame, title, bg=PANEL, fg=MUTED, font=(BODY_FONT, 9, "bold")).pack(anchor="w", padx=14, pady=(12, 5))
            self.card_values[key] = self.label(frame, "—", bg=PANEL, fg=color, font=(TITLE_FONT, 24, "bold"))
            self.card_values[key].pack(anchor="w", padx=14)
            self.card_notes[key] = self.label(frame, "Aguardando dados", bg=PANEL, fg=MUTED, font=(BODY_FONT, 9))
            self.card_notes[key].pack(anchor="w", padx=14, pady=(3, 12))
        self.details = self.label(self.live_tab, "Lendo sensores…", fg=MUTED, anchor="w", justify="left")
        self.details.pack(fill="x", padx=25, pady=13)
        chart_grid = tk.Frame(self.live_tab, bg=BG)
        chart_grid.pack(fill="both", expand=True, padx=20)
        chart_grid.columnconfigure((0, 1), weight=1, uniform="charts")
        chart_grid.rowconfigure((0, 1), weight=1)
        self.charts = {}
        for index, (key, title, color, unit, fixed) in enumerate([
            ("hash", "Hashrate · TH/s", TEAL, "TH/s", None),
            ("temp", "Temperatura da GPU", ORANGE, "°C", (25, 85)),
            ("power", "Potência da placa", BLUE, "W", None),
            ("fan", "Velocidade das ventoinhas", PINK, "%", (0, 100))]):
            chart = Chart(chart_grid, title, color, unit, fixed)
            chart.grid(row=index//2, column=index%2, sticky="nsew", padx=4, pady=4)
            self.charts[key] = chart
        self.info = self.label(self.live_tab, "", fg=MUTED, justify="left", anchor="w", font=(BODY_FONT, 9))
        self.info.pack(fill="x", padx=25, pady=(10, 6))
        self.label(self.live_tab, "Recebimento: " + WALLET, fg=MUTED, anchor="w", font=("Consolas", 9)).pack(fill="x", padx=25, pady=(0, 7))
        lower = tk.Frame(self.live_tab, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        lower.pack(fill="x", padx=24, pady=(0, 10))
        self.log_title = self.label(lower, "ATIVIDADE DO MINERADOR", bg=PANEL, fg=MUTED, font=(BODY_FONT, 9, "bold"))
        self.log_title.pack(anchor="w", padx=12, pady=(8, 4))
        self.logs = tk.Text(lower, height=5, bg=PANEL, fg="#b4c7df", font=("Consolas", 9),
                            relief="flat", wrap="none", state="disabled", padx=12, pady=4)
        self.logs.pack(fill="x")
        self.footer = self.label(self.live_tab, "Somente leitura · histórico de até 15 minutos · sem mudanças no minerador ou na GPU", fg=MUTED, font=(BODY_FONT, 9))
        self.footer.pack(anchor="w", padx=25, pady=(0, 12))
        self.build_finance()
        self.tabs.select(self.finance_tab)

    def build_finance(self):
        viewport = tk.Canvas(self.finance_tab, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.finance_tab, orient="vertical", command=viewport.yview)
        scrollbar.pack(side="right", fill="y")
        viewport.pack(side="left", fill="both", expand=True)
        viewport.configure(yscrollcommand=scrollbar.set)
        parent = tk.Frame(viewport, bg=BG)
        inner = viewport.create_window((0, 0), window=parent, anchor="nw")
        parent.bind("<Configure>", lambda _: viewport.configure(scrollregion=viewport.bbox("all")))
        viewport.bind("<Configure>", lambda event: viewport.itemconfigure(inner, width=event.width))
        self.finance_viewport = viewport
        self.bind("<MouseWheel>", lambda event: viewport.yview_scroll(-int(event.delta/120), "units") if self.tabs.select() == str(self.finance_tab) else None)
        self.quote = self.label(parent, "Consultando cotação da Pearl…", fg=TEAL, font=(BODY_FONT, 17, "bold"), anchor="w")
        self.quote.pack(fill="x", padx=24, pady=(18, 3))
        self.quote_note = self.label(parent, "Pool e cotação atualizam a cada 60 s; sensores continuam a cada 2 s.", fg=MUTED, anchor="w")
        self.quote_note.pack(fill="x", padx=24, pady=(0, 14))
        grid = tk.Frame(parent, bg=BG)
        grid.pack(fill="x", padx=20)
        self.money_values, self.money_notes = {}, {}
        for index, (key, name, color) in enumerate([
            ("day", "HOJE · CRÉDITO OBSERVADO", TEAL), ("week", "SEMANA · SEGUNDA ATÉ AGORA", TEAL),
            ("month", "ÚLTIMOS 30 DIAS", TEAL), ("balance", "ACUMULADO NA POOL", BLUE),
            ("forecast7", "PRÓXIMOS 7 DIAS · PROJEÇÃO", ORANGE), ("forecast30", "PRÓXIMOS 30 DIAS · PROJEÇÃO", ORANGE)]):
            column = index%3
            grid.columnconfigure(column, weight=1, uniform="money")
            frame = tk.Frame(grid, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            frame.grid(row=index//3, column=column, sticky="nsew", padx=4, pady=4)
            self.label(frame, name, bg=PANEL, fg=MUTED, font=(BODY_FONT, 9, "bold")).pack(anchor="w", padx=15, pady=(13, 4))
            self.money_values[key] = self.label(frame, "N/D", bg=PANEL, fg=color, font=(TITLE_FONT, 26, "bold"))
            self.money_values[key].pack(anchor="w", padx=15)
            self.money_notes[key] = self.label(frame, "Aguardando dados reais", bg=PANEL, fg=MUTED, justify="left", font=(BODY_FONT, 9))
            self.money_notes[key].pack(anchor="w", padx=15, pady=(4, 13))
        self.finance_explanation = self.label(parent, "", fg=MUTED, anchor="w", justify="left", wraplength=1180)
        self.finance_explanation.pack(fill="x", padx=25, pady=12)
        self.money_chart = Chart(parent, "Créditos observados acumulados · valorizados em reais agora", TEAL, "R$", window=30*86400)
        self.money_chart.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        self.finance_sources = self.label(parent, "", fg=MUTED, anchor="w", justify="left", wraplength=1180, font=(BODY_FONT, 9))
        self.finance_sources.pack(fill="x", padx=25, pady=(0, 14))
        def resize_finance(event):
            for widget in (self.finance_explanation, self.finance_sources):
                widget.configure(wraplength=max(500, event.width-54))
            viewport.configure(scrollregion=viewport.bbox("all"))
        parent.bind("<Configure>", resize_finance)

    def refresh_finance(self):
        def money(value):
            return "N/D" if value is None else "R$ " + f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        def timestamp(value):
            return dt.datetime.fromtimestamp(value).strftime("%d/%m %H:%M:%S") if value else "não disponível"
        state = self.finance_state
        miner = self.last_data.get("miner", {}) if self.last_data else {}
        fresh = bool(self.last_data and self.last_data.get("miner_running") and miner.get("hashrate_at") and time.time()-miner["hashrate_at"] < 180 and not miner.get("paused"))
        hashrate = miner.get("average_1m") if fresh else None
        model = financial_model(state, hashrate)
        self.last_finance_model = model
        self.quote.configure(text="1 PRL (Pearl) = " + money(model["price_brl"]))
        price_stale = bool(model["price_at"] and time.time()-model["price_at"] > 180)
        pool_stale = bool(model["pool_at"] and time.time()-model["pool_at"] > 180)
        cached = " · CACHE DESATUALIZADO" if price_stale or pool_stale or model["errors"] else ""
        self.quote_note.configure(text=f"Cotação CoinGecko: {timestamp(model['price_at'])} · Pool: {timestamp(model['pool_at'])} · atualização remota 60 s{cached}", fg=ORANGE if cached else MUTED)
        for key in ("day", "week", "month"):
            period = model["periods"][key]
            self.money_values[key].configure(text=money(period["brl"]))
            since = dt.datetime.fromtimestamp(period["since"]).strftime("%d/%m %H:%M") if period["since"] else "—"
            amount = "aguardando 2ª amostra" if period["prl"] is None else f"{period['prl']:.8f} PRL"
            self.money_notes[key].configure(text=f"{'Parcial desde '+since if period['partial'] else 'Período observado'}\n{amount}")
        self.money_values["balance"].configure(text=money(model["accumulated_brl"]))
        total = model["accumulated_prl"]
        paid, unpaid = model["paid_prl"], model["unpaid_prl"]
        self.money_notes["balance"].configure(text=(f"{total:.8f} PRL · pago + saldo\nPago: {paid:.8f} · saldo: {unpaid:.8f}" if total is not None else "Aguardando resposta da pool"))
        forecast = model["forecast"]
        for key, field, days in (("forecast7", "brl_7days", 7), ("forecast30", "brl_30days", 30)):
            self.money_values[key].configure(text=money(forecast[field] if forecast else None))
            self.money_notes[key].configure(text=f"{forecast['prl_day']*days:.4f} PRL · 24 h/dia\n{forecast['hashrate_ths']:.2f} TH/s · preço/rede atuais" if forecast else "Requer hashrate, rede e cotação atuais")
        self.finance_explanation.configure(text="Realizado = variação de (total pago + saldo pendente/confirmado) observada na pool, sem contar saques duas vezes. Os períodos sem histórico completo são parciais desde o início do monitoramento; valores anteriores não são inventados.\nTodos os valores em R$ são reavaliados à cotação atual, não representam dinheiro já sacado. As projeções mantêm 24 h/dia, hashrate, preço e dificuldade atuais, descontando a taxa da pool e 2% do minerador.")
        snapshots = state.get("snapshots", [])
        self.money_chart.points.clear()
        if len(snapshots) >= 2 and model["price_brl"]:
            baseline = snapshots[0]["total_prl"]
            for point in snapshots:
                self.money_chart.add(point["timestamp"], (point["total_prl"]-baseline)*model["price_brl"])
        self.money_chart.draw()
        fee = f"{forecast['pool_fee']*100:.1f}%" if forecast else "N/D"
        unconfirmed = model["unconfirmed_prl"]
        pending = f"{unconfirmed:.8f} PRL" if unconfirmed is not None else "N/D"
        errors = " | ".join(f"{key}: {value}" for key, value in model["errors"].items())
        self.finance_sources.configure(text=f"Fontes: Kryptex Pool API (saldo, total pago e rede) · CoinGecko API ID pearl-2 (Pearl Research Labs). Taxa pool: {fee}. Pendente de confirmação: {pending}.\nCalendário: São Paulo; semana começa segunda-feira. Últimos 30 dias: janela móvel. Histórico local persistido em AppData/PearlMonitor.\n" + ("Falhas: " + errors if errors else "Projeção: hash / hashrate da rede × recompensa × blocos/dia × (1 − taxa pool) × 0,98. Histórico da pool ainda pode não incluir créditos pendentes."))
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            (STATE_DIR / "finance-latest.json").write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def apply(self, data):
        self.last_data = data
        now = time.time()
        gpu, miner = data.get("gpu") or {}, data["miner"]
        stamp = miner.get("hashrate_at")
        age = now-stamp if stamp else None
        fresh = age is not None and -5 <= age <= 180 and data["miner_running"] is True
        status, color = "Aguardando mineração", MUTED
        if data["miner_running"] is False:
            status, color = "Minerador parado", ORANGE
        elif data["miner_running"] is None:
            status, color = "Estado do processo indisponível", ORANGE
        elif "Reconectando" in miner["connection"]:
            status, color = miner["connection"], ORANGE
        elif miner.get("paused"):
            status, color = "GPU pausada para resfriar", ORANGE
        elif fresh:
            status, color = "Minerando · dados atuais", TEAL
        elif stamp:
            status, color = "Sem hashrate recente", ORANGE
        self.status.configure(text="●  " + status, fg=color)
        hashrate = miner.get("hashrate") if fresh else None
        self.card_values["hash"].configure(text=fmt(hashrate, " TH/s", 1))
        self.card_notes["hash"].configure(text=("Média 1 min: " + fmt(miner.get("average_1m"), " TH/s", 1)) if fresh else "Hashrate não atualizado")
        self.card_values["temp"].configure(text=fmt(gpu.get("temperature.gpu"), " °C"))
        self.card_notes["temp"].configure(text="Sensor NVIDIA" if gpu else "Sensor indisponível")
        self.card_values["power"].configure(text=fmt(gpu.get("power.draw"), " W", 1))
        self.card_notes["power"].configure(text="Limite: " + fmt(gpu.get("power.limit"), " W"))
        self.card_values["fan"].configure(text=fmt(gpu.get("fan.speed"), " %"))
        self.card_notes["fan"].configure(text="Utilização GPU: " + fmt(gpu.get("utilization.gpu"), " %"))
        self.card_values["shares"].configure(text=str(miner["accepted"]) if stamp else "—")
        self.card_notes["shares"].configure(text=f"Rej.: {miner['rejected']}  ·  HW: {miner['hw_errors']}" + ("" if fresh else " · último log"))
        efficiency = hashrate/gpu["power.draw"] if hashrate and gpu.get("power.draw") else None
        self.details.configure(text=f"Núcleo  {fmt(gpu.get('clocks.gr'), ' MHz')}    •    Memória  {fmt(gpu.get('clocks.mem'), ' MHz')}    •    VRAM  {fmt(gpu.get('memory.used'))} / {fmt(gpu.get('memory.total'), ' MiB')}    •    Eficiência  {fmt(efficiency, ' TH/s/W', 3)}\nDriver  {gpu.get('driver_version', '—')}    •    Estado  {gpu.get('pstate', '—')}    •    Média 15 min  {fmt(miner.get('average_15m') if fresh else None, ' TH/s', 1)}")
        self.subtitle.configure(text=f"{gpu.get('name', 'GPU indisponível')} · PRL / pearlhash · atualização a cada 2 s")
        for timestamp, rate in miner.get("rate_points", []):
            self.charts["hash"].add(timestamp, rate)
        if not fresh:
            self.charts["hash"].add(now, None)
        for key, field in (("temp", "temperature.gpu"), ("power", "power.draw"), ("fan", "fan.speed")):
            self.charts[key].add(now, gpu.get(field))
        for chart in self.charts.values():
            chart.draw()
        uptime = int(now-miner["started_at"]) if miner.get("started_at") and data["miner_running"] else None
        duration = f"{uptime//3600:02d}:{uptime%3600//60:02d}:{uptime%60:02d}" if uptime is not None else "—"
        self.info.configure(text=f"Pool: {POOL}    •    Sessão: {duration}    •    Último hashrate: {int(age)} s atrás" if age is not None else f"Pool: {POOL}    •    Sessão: {duration}    •    Sem hashrate no log")
        logs = miner.get("recent_logs", "")
        if logs != self.log_contents:
            self.logs.configure(state="normal")
            self.logs.delete("1.0", "end")
            self.logs.insert("end", logs)
            self.logs.see("end")
            self.logs.configure(state="disabled")
            self.log_contents = logs
        filename = Path(data["log_path"]).name if data["log_path"] else "nenhum log"
        self.log_title.configure(text="ATIVIDADE DO MINERADOR  ·  " + filename)
        errors = [data.get(key) for key in ("gpu_error", "process_error", "log_error") if data.get(key)]
        if self.history.last_error:
            errors.append("Histórico aguardando gravação: " + self.history.last_error)
        archived = self.history.info.get("telemetry", (0, None, None))[0]
        self.footer.configure(text=" | ".join(errors) if errors else f"Somente leitura · histórico salvo em SQLite: {archived} amostras · gráficos mostram 15 min · atualização {dt.datetime.fromtimestamp(data['collected_at']).strftime('%H:%M:%S')}")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {k: v for k, v in data.items() if k != "miner"}
        snapshot["miner"] = {k: v for k, v in miner.items() if k not in ("recent_logs", "rate_points")}
        snapshot["monitor_status"] = status
        snapshot["app"] = {"name": APP_NAME, "tray_registered": self.tray.registered_ok,
            "tray_error": self.tray.error, "panel_state": self.state(),
            "pid": os.getpid(),
            "summary_open": bool(self.summary_window and self.summary_window.winfo_exists())}
        snapshot["window"] = {"width": self.winfo_width(), "height": self.winfo_height()}
        snapshot["history"] = {"database": str(self.history.path), "session": self.history.session,
            "counts": self.history.info, "last_commit": self.history.last_commit,
            "pending_jobs": self.history.jobs.qsize(), "error": self.history.last_error,
            "restored_points": self.restored_points, "retention": "permanent_no_automatic_deletion"}
        self.last_snapshot = snapshot
        try:
            (STATE_DIR / "latest.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def poll(self):
        changed = False
        try:
            request = json.loads((STATE_DIR / "ui-request.json").read_text(encoding="utf-8"))
            if request.get("at", 0) > self.last_ui_request and request.get("action") in ("open", "summary"):
                self.last_ui_request = request["at"]
                self.tray_actions.put(request["action"])
        except (OSError, ValueError):
            pass
        try:
            while True:
                action = self.tray_actions.get_nowait()
                if action == "open":
                    self.show_panel()
                elif action == "summary":
                    self.show_summary()
                elif action == "console":
                    self.open_console()
                elif action == "exit":
                    self.close()
        except queue.Empty:
            pass
        try:
            restored = self.history_restore.get_nowait()
            for key, points in restored.get("points", {}).items():
                self.restored_points[key] = len(points)
                combined = dict(points)
                combined.update(dict(self.charts[key].points))
                self.charts[key].points.clear()
                for stamp, value in sorted(combined.items()):
                    self.charts[key].add(stamp, value)
                self.charts[key].draw()
            preferences = restored.get("preferences", {})
            geometry = preferences.get("geometry")
            if isinstance(geometry, str) and re.fullmatch(r"\d+x\d+(?:[+-]\d+){2}", geometry):
                self.geometry(geometry)
            if preferences.get("tab") in (0, 1):
                self.tabs.select(preferences["tab"])
            self.ui_restored = True
            changed = True
        except queue.Empty:
            pass
        try:
            data = self.collector.samples.get_nowait()
            self.apply(data)
            changed = True
        except queue.Empty:
            pass
        try:
            self.finance_state = self.finance_collector.samples.get_nowait()
            changed = True
        except queue.Empty:
            pass
        if changed:
            self.refresh_finance()
            self.refresh_summary()
        self.after(250, self.poll)

    def close(self):
        if getattr(self, "closing", False):
            return
        self.closing = True
        self.save_preferences()
        self.collector.stop.set()
        self.finance_collector.stop.set()
        self.title("HashPilot · salvando histórico…")
        self.close_thread = threading.Thread(target=lambda: (self.tray.stop(), self.history.close()), daemon=True)
        self.close_thread.start()
        self.after(100, self.finish_close)

    def finish_close(self):
        if self.history.worker.is_alive():
            self.after(100, self.finish_close)
        else:
            self.destroy()


def console_text(snapshot, finance):
    gpu, miner = snapshot.get("gpu") or {}, snapshot.get("miner") or {}
    age = time.time()-snapshot.get("collected_at", 0)
    active = age <= 8
    money = lambda value: "N/D" if value is None else "R$ " + f"{value:.2f}".replace(".", ",")
    forecast = finance.get("forecast") or {}
    today = finance.get("periods", {}).get("day", {})
    history = snapshot.get("history", {})
    return "\n".join([
        "HASHPILOT  |  TECH FERNANDES",
        "Resumo local do monitor. Ctrl+C fecha somente este console.",
        "="*82,
        (snapshot.get("monitor_status", "Aguardando monitor") if active else "MONITOR SEM ATUALIZACAO - ultimo registro abaixo"),
        f"Hashrate: {fmt(miner.get('hashrate'), ' TH/s', 2)}    Media 1 min: {fmt(miner.get('average_1m'), ' TH/s', 2)}",
        f"GPU: {fmt(gpu.get('temperature.gpu'), ' C')}  |  Potencia: {fmt(gpu.get('power.draw'), ' W', 1)}  |  Fan: {fmt(gpu.get('fan.speed'), '%')}",
        f"Core: {fmt(gpu.get('clocks.gr'), ' MHz')}  |  Memoria: {fmt(gpu.get('clocks.mem'), ' MHz')}",
        f"Shares aceitas: {miner.get('accepted', 'N/D')}  |  Rejeitadas: {miner.get('rejected', 'N/D')}  |  HW: {miner.get('hw_errors', 'N/D')}",
        f"Hoje observado {'(parcial)' if today.get('partial') else ''}: {money(today.get('brl'))}  |  Acumulado pool: {money(finance.get('accumulated_brl'))}",
        f"Projecao 7 dias: {money(forecast.get('brl_7days'))}  |  Projecao 30 dias: {money(forecast.get('brl_30days'))}",
        f"Cotacao atual 1 PRL: {money(finance.get('price_brl'))}  |  Previsoes a preco/rede atuais, 24 h/dia.",
        f"Historico: {history.get('counts', {}).get('telemetry', [0])[0]} amostras  |  Fila: {history.get('pending_jobs', 'N/D')}  |  Banco: {'OK' if not history.get('error') else history['error']}",
        f"Registro do painel: {int(age)} s atras. Sensores 2 s / cotacao e pool 60 s.",
        "="*82,
    ])


def run_console(once=False):
    mode = None
    kernel = None
    handle = None
    icon = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetConsoleTitleW(APP_NAME + " · Console")
        kernel.GetStdHandle.restype = wintypes.HANDLE
        handle = kernel.GetStdHandle(-11)
        mode = wintypes.DWORD()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 4)
        else:
            mode = None
        kernel.GetConsoleWindow.restype = wintypes.HWND
        window = kernel.GetConsoleWindow()
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user.LoadImageW.restype = wintypes.HANDLE
        user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        icon = user.LoadImageW(None, str(ASSET_DIR / "hashpilot.ico"), 1, 32, 32, 0x10)
        if window and icon:
            user.SendMessageW(window, 0x80, 0, icon)
            user.SendMessageW(window, 0x80, 1, icon)
    try:
        while True:
            documents = []
            for name in ("latest.json", "finance-latest.json"):
                try:
                    documents.append(json.loads((STATE_DIR / name).read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    documents.append({})
            text = console_text(*documents)
            if once:
                print(text)
                return
            print("\x1b[H\x1b[2J\x1b[?25l" + text, end="", flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        if not once:
            print("\x1b[?25h\nConsole encerrado. A mineracao e o monitor continuam.")
        if kernel and mode is not None:
            kernel.SetConsoleMode(handle, mode.value)
        if icon:
            user.DestroyIcon(icon)


def handoff_to_running_monitor(action):
    """Reabre a sessão atual ao usar atalhos; não inicia outro coletor."""
    if os.name != "nt":
        return False
    try:
        data = json.loads((STATE_DIR / "latest.json").read_text(encoding="utf-8"))
        app = data.get("app", {})
        if time.time()-data["collected_at"] > 10 or app.get("name") != APP_NAME or not app.get("pid"):
            return False
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(app["pid"]))
        if not handle:
            return False
        code = wintypes.DWORD()
        alive = bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
        kernel.CloseHandle(handle)
        if not alive:
            return False
        temporary = STATE_DIR / "ui-request.tmp"
        temporary.write_text(json.dumps({"action": action, "at": time.time()}), encoding="utf-8")
        temporary.replace(STATE_DIR / "ui-request.json")
        return True
    except (OSError, ValueError, KeyError):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--console", action="store_true", help="Resumo em console do monitor ativo, sem novos coletores")
    parser.add_argument("--console-once", action="store_true", help="Imprime uma unica amostra do resumo")
    parser.add_argument("--summary", action="store_true", help="Abre o monitor com o resumo compacto")
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(Collector().sample(), ensure_ascii=False, indent=2))
        return
    if args.console or args.console_once:
        run_console(once=args.console_once)
        return
    if not args.smoke_test and handoff_to_running_monitor("summary" if args.summary else "open"):
        return
    app = Monitor()
    if args.summary:
        app.after(800, lambda: (app.show_summary(), app.hide_to_tray()))
    if args.smoke_test:
        app.after(7000, app.close)
    app.mainloop()


if __name__ == "__main__":
    main()
