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
import webbrowser
from urllib.parse import quote
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import sys
import math
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


def read_thermal_status(path=None, now=None):
    path = Path(path) if path is not None else LOG_DIR / "thermal-control.json"
    now = time.time() if now is None else now
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Status térmico precisa ser um objeto JSON")
        stamp = float(data["sampled_at"])
        if not math.isfinite(stamp):
            raise ValueError("Horário do supervisor inválido")
        data["age_seconds"] = max(0, now-stamp)
        data["stale"] = now-stamp > 12 or stamp-now > 5
        data["available"] = True
        return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"available": False, "error": str(error), "stale": True, "age_seconds": None}


def thermal_description(control, compact=False):
    if control is None:
        return "Supervisor não iniciado"
    if control.get("error"):
        return "Erro: " + str(control["error"])[:(45 if compact else 140)]
    if not control.get("enabled"):
        return "Controle desativado"
    modes = {"hold": "Mantendo", "cooling": "Resfriando", "anticipating": "Antecipando calor",
             "recovering": "Recuperando", "emergency": "Emergência"}
    mode = modes.get(control.get("mode"), str(control.get("mode", "Aguardando")))
    clock = control.get("current_clock_mhz", "—")
    desired = control.get("desired_clock_mhz")
    clock_text = f"{clock} → {desired} MHz" if desired is not None and desired != clock else f"{clock} MHz"
    age = control.get("age_seconds")
    age_text = f"{age:.0f} s" if age is not None else "sem horário"
    text = f"{mode} · {clock_text} · {age_text}"
    if control.get("stale"):
        text += " · desatualizado"
    if not compact and control.get("reason"):
        text += " — " + str(control["reason"])
    return text


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
        result["thermal_control"] = read_thermal_status()
        return result

    def work(self):
        while not self.stop.is_set():
            started = time.monotonic()
            data = self.sample()
            if self.history:
                self.history.telemetry(data)
                try:
                    self.history.archive_thermal(LOG_DIR / "thermal-control-events.jsonl")
                except OSError as error:
                    self.history.event(time.time(), "thermal_import_error", {"error": str(error)})
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


def safe_number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def format_prl(value):
    value = safe_number(value)
    return "N/D" if value is None else f"{value:.8f}".replace(".", ",") + " PRL"


def format_brl(value):
    value = safe_number(value)
    return "N/D" if value is None else "R$ " + f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def work_area(root):
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            rectangle = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rectangle), 0):
                return rectangle.left, rectangle.top, rectangle.right, rectangle.bottom
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()-40


class Chart(tk.Canvas):
    def __init__(self, master, title, color, unit, fixed=None, window=900, max_gap=80, show_age=False):
        super().__init__(master, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, height=185)
        self.title, self.color, self.unit, self.fixed = title, color, unit, fixed
        self.window = window
        self.max_gap, self.show_age = max_gap, show_age
        self.points = deque(maxlen=50000 if window > 900 else 900)
        self.bind("<Configure>", lambda _: self.draw())

    def add(self, timestamp, value):
        timestamp = float(timestamp)
        value = float(value) if value is not None else None
        if not math.isfinite(timestamp) or (value is not None and not math.isfinite(value)):
            return
        if not self.points or timestamp > self.points[-1][0]:
            self.points.append((timestamp, value))
            return
        # Logs podem ser descarregados depois de uma amostra de indisponibilidade.
        # O horário real da medição prevalece sobre a ordem de chegada ao painel.
        points = dict(self.points)
        if timestamp in points and points[timestamp] is not None and value is None:
            return
        points[timestamp] = value
        self.points.clear()
        self.points.extend(sorted(points.items()))

    def segments(self):
        groups, group, previous = [], [], None
        for timestamp, value in self.points:
            if value is None:
                if group:
                    groups.append(group)
                group, previous = [], None
                continue
            if previous is not None and timestamp-previous > self.max_gap:
                if group:
                    groups.append(group)
                group = []
            group.append((timestamp, value))
            previous = timestamp
        if group:
            groups.append(group)
        return groups

    def sample_info(self):
        measured = [(stamp, value) for stamp, value in self.points if value is not None]
        return {"point_count": len(self.points), "latest_sample_at": measured[-1][0] if measured else None,
                "latest_value": measured[-1][1] if measured else None, "gap_limit_seconds": self.max_gap}

    def draw(self):
        title_size = -getattr(self, "title_px", 13)
        axis_size = -getattr(self, "axis_px", 11)
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40:
            return
        self.create_text(16, 18, anchor="w", text=self.title, fill=TEXT, font=(BODY_FONT, title_size, "bold"))
        cutoff = time.time() - self.window
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()
        values = [v for _, v in self.points if v is not None]
        if not values:
            self.create_text(w/2, h/2, text="Aguardando amostras reais", fill=MUTED, font=(BODY_FONT, title_size))
            return
        ymin, ymax = self.fixed or (max(0, min(values) * .85), max(values) * 1.1)
        if ymax <= ymin:
            ymax = ymin + 1
        left, right, top, bottom = 50, w-18, 44, h-28
        for i in range(4):
            y = top + (bottom-top)*i/3
            value = ymax - (ymax-ymin)*i/3
            self.create_line(left, y, right, y, fill=BORDER)
            self.create_text(left-8, y, text=f"{value:.2f}" if abs(ymax) < 2 else f"{value:.0f}", anchor="e", fill=MUTED, font=(BODY_FONT, axis_size))
        now = time.time()
        start = max(cutoff, min(p[0] for p in self.points))
        span = max(60, now-start)
        for group in self.segments():
            coordinates = []
            for timestamp, value in group:
                x = left + (timestamp-start)/span*(right-left)
                y = bottom - (value-ymin)/(ymax-ymin)*(bottom-top)
                coordinates.extend([x, y])
            if len(coordinates) >= 4:
                self.create_line(*coordinates, fill=self.color, width=2, tags="series")
            x, y = coordinates[-2:]
            self.create_oval(x-3, y-3, x+3, y+3, fill=self.color, outline="", tags="series-point")
        if self.show_age:
            latest = self.sample_info()["latest_sample_at"]
            age = max(0, int(now-latest))
            self.create_text(right, 18, anchor="e", text=f"última amostra há {age} s", fill=MUTED,
                             font=(BODY_FONT, axis_size), tags="sample-age")
        self.create_text(left, h-12, text=dt.datetime.fromtimestamp(start).strftime("%H:%M:%S"), anchor="w", fill=MUTED, font=(BODY_FONT, axis_size))
        self.create_text(right, h-12, text="agora · " + self.unit, anchor="e", fill=MUTED, font=(BODY_FONT, axis_size))


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
        self.geometry("1280x800")
        self.minsize(1120, 620)
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
        self.bind("<Configure>", self.schedule_preferences, add="+")
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
        area = work_area(self)
        summary_width, summary_height = min(660, area[2]-area[0]-20), min(700, area[3]-area[1]-44)
        window.geometry(f"{summary_width}x{summary_height}")
        window.minsize(min(600,summary_width),min(620,summary_height))
        window.configure(bg=BG)
        window.iconphoto(True, self.app_icon)
        self.label(window, "HashPilot", font=(TITLE_FONT, -24, "bold"), fg=TEAL).pack(anchor="w", padx=22, pady=(18, 2))
        self.label(window, "TECH FERNANDES  /  RESUMO AO VIVO", fg=MUTED, font=(BODY_FONT, -11, "bold")).pack(anchor="w", padx=23, pady=(0, 14))
        grid = tk.Frame(window, bg=PANEL, padx=16, pady=12)
        grid.pack(fill="both", expand=True, padx=22)
        grid.columnconfigure(1, weight=1)
        for row, (key, title) in enumerate([
            ("wallet", "Carteira de recebimento"), ("status", "Estado"), ("hash", "Hashrate"), ("gpu", "GPU · temperatura · consumo"),
            ("clock", "Núcleo / memória / fan"), ("shares", "Aceitas / rejeitadas / erros"),
            ("today", "Hoje · observado parcial"), ("pool", "Acumulado na pool"),
            ("week", "Projeção · próximos 7 dias"), ("month", "Projeção · próximos 30 dias"),
            ("payout_eta", "Próximo pagamento · estimativa"), ("payout_remaining", "Falta gerar para o mínimo"),
            ("price", "Cotação atual · 1 PRL"), ("thermal", "Controle térmico"), ("history", "Histórico permanente")]):
            self.label(grid, title, bg=PANEL, fg=MUTED, anchor="w", font=(BODY_FONT, -12)).grid(row=row, column=0, sticky="w", pady=5)
            value = self.label(grid, "—", bg=PANEL, fg=TEXT, font=(BODY_FONT, -13, "bold"), anchor="e", wraplength=325, justify="right")
            value.grid(row=row, column=1, sticky="e", padx=(12, 0), pady=5)
            self.summary_values[key] = value
        buttons = tk.Frame(window, bg=BG)
        buttons.pack(fill="x", padx=22, pady=16)
        for text, command in (("Abrir painel", self.show_panel), ("Copiar carteira", self.copy_wallet), ("Console", self.open_console), ("Recolher", window.withdraw)):
            tk.Button(buttons, text=text, command=command, bg=PANEL, fg=TEXT, activebackground=BORDER,
                      activeforeground=TEXT, relief="flat", padx=12, pady=9, font=(BODY_FONT, -12)).pack(side="left", padx=(0, 8))
        self.refresh_summary()

    def copy_wallet(self):
        wallet = self.wallet_var.get().strip()
        if not wallet:
            return
        self.clipboard_clear()
        self.clipboard_append(wallet)
        self.wallet_copy_button.configure(text="Copiado ✓")
        self.after(1600, lambda: self.wallet_copy_button.configure(text="Copiar"))

    def open_wallet_pool(self):
        wallet = self.wallet_var.get().strip()
        if wallet:
            webbrowser.open("https://pool.kryptex.com/prl/miner/stats/" + quote(wallet, safe=""))

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
            self.console_widget = tk.Text(window, bg=BG, fg=TEXT, font=("Menlo", -12), padx=16, pady=16, wrap="word", state="disabled")
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
        payout = model.get("payout") or {}
        wallet = str(model.get("wallet") or WALLET or "")
        money = lambda value: "N/D" if value is None else "R$ " + f"{value:.2f}".replace(".", ",")
        values = {
            "wallet": (wallet[:14] + "…" + wallet[-10:]) if len(wallet) > 28 else (wallet or "Não configurada"),
            "status": self.status.cget("text").replace("●  ", ""),
            "hash": self.card_values["hash"].cget("text"),
            "gpu": fmt(gpu.get("temperature.gpu"), " °C") + "  /  " + fmt(gpu.get("power.draw"), " W", 1),
            "clock": f"{fmt(gpu.get('clocks.gr'))} / {fmt(gpu.get('clocks.mem'))} MHz / {fmt(gpu.get('fan.speed'), '%')}",
            "shares": f"{miner.get('accepted', '—')} / {miner.get('rejected', '—')} / {miner.get('hw_errors', '—')}",
            "today": money(model.get("periods", {}).get("day", {}).get("brl")),
            "pool": money(model.get("accumulated_brl")),
            "week": money(forecast.get("brl_7days")), "month": money(forecast.get("brl_30days")),
            "price": money(model.get("price_brl")),
            "payout_eta": payout.get("eta_label") or "Aguardando dados da pool",
            "payout_remaining": format_prl(payout.get("remaining_prl")),
            "thermal": thermal_description(data.get("thermal_control"), compact=True),
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
        self.history.preferences({"geometry": self.geometry(), "tab": self.tabs.index(self.tabs.select()), "tab_schema": "three-tabs-v1"})

    def label(self, master, text="", **kwargs):
        return tk.Label(master, text=text, bg=kwargs.pop("bg", BG), fg=kwargs.pop("fg", TEXT),
                        font=kwargs.pop("font", (BODY_FONT, 10)), **kwargs)

    def metric_card(self, parent, title, color, column, values, notes, key):
        frame = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        frame.grid(row=0, column=column, sticky="nsew", padx=4)
        parent.columnconfigure(column, weight=1, uniform="metrics")
        frame.title_label = self.label(frame, title, bg=PANEL, fg=MUTED, font=(BODY_FONT, 9, "bold"))
        frame.title_label.pack(anchor="w", padx=12, pady=(8, 2))
        values[key] = self.label(frame, "—", bg=PANEL, fg=color, font=(TITLE_FONT, 23, "bold"))
        values[key].pack(anchor="w", padx=12)
        notes[key] = self.label(frame, "Aguardando dados", bg=PANEL, fg=MUTED, font=(BODY_FONT, 9))
        notes[key].pack(anchor="w", padx=12, pady=(1, 8))
        return frame

    def build(self):
        left,top,right,bottom = work_area(self)
        self.maxsize(right-left-16,bottom-top-44)
        self.minsize(min(1120,right-left-16),min(620,bottom-top-44))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        head = tk.Frame(self, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 6))
        self.brand_logo = tk.PhotoImage(file=ASSET_DIR / "logo-header.png").subsample(2, 2)
        tk.Label(head, image=self.brand_logo, bg=BG).pack(side="left", padx=(0, 14))
        product = tk.Frame(head, bg=BG)
        product.pack(side="left")
        self.label(product, "HashPilot", font=(TITLE_FONT, 20, "bold")).pack(anchor="w")
        self.subtitle = self.label(product, "Mineração e recebimentos", fg=MUTED, font=(BODY_FONT, 9))
        self.subtitle.pack(anchor="w")
        controls = tk.Frame(head, bg=BG)
        controls.pack(side="right")
        self.status = self.label(controls, "● Aguardando sensores", fg=TEAL, font=(BODY_FONT, 10, "bold"))
        self.status.pack(anchor="e", pady=(0, 4))
        buttons = tk.Frame(controls, bg=BG)
        buttons.pack(anchor="e")
        for text, command in (("Resumo", self.show_summary), ("Console", self.open_console), ("Recolher", self.hide_to_tray), ("Sair", self.close)):
            tk.Button(buttons, text=text, command=command, bg=PANEL, fg=TEXT, activebackground=BLUE,
                      activeforeground=TEXT, relief="flat", padx=10, pady=4, font=(BODY_FONT, 9)).pack(side="left", padx=(5, 0))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(20, 7), font=(BODY_FONT, 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", BORDER)], foreground=[("selected", TEAL)])
        style.configure("Generated.Horizontal.TProgressbar", troughcolor=BG, background=TEAL, bordercolor=BORDER, lightcolor=TEAL, darkcolor=TEAL)
        style.configure("Confirmed.Horizontal.TProgressbar", troughcolor=BG, background=BLUE, bordercolor=BORDER, lightcolor=BLUE, darkcolor=BLUE)
        self.tabs = ttk.Notebook(self)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12)
        self.overview_tab = tk.Frame(self.tabs, bg=BG)
        self.live_tab = tk.Frame(self.tabs, bg=BG)
        self.finance_tab = tk.Frame(self.tabs, bg=BG)
        for tab, name in ((self.overview_tab, "Visão geral"), (self.live_tab, "GPU e mineração"), (self.finance_tab, "Rendimentos")):
            self.tabs.add(tab, text=name)
        self.build_overview()
        self.build_gpu()
        self.build_finance()
        self.footer = self.label(self, "Histórico permanente · somente leitura", fg=MUTED, anchor="w", font=(BODY_FONT, 8))
        self.footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(5, 7))
        self.tabs.select(self.overview_tab)
        self.bind("<Configure>", self.adapt_layout, add="+")
        self.after_idle(self.adapt_layout)

    def build_overview(self):
        parent = self.overview_tab
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)
        self.overview_values, self.overview_notes = {}, {}
        cards = tk.Frame(parent, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(9, 6))
        for index, (key, title, color) in enumerate((("hash", "HASHRATE", TEAL), ("temp", "TEMPERATURA", ORANGE), ("power", "CONSUMO DA GPU", BLUE), ("today", "GERADO HOJE · BRL", TEAL))):
            frame = self.metric_card(cards, title, color, index, self.overview_values, self.overview_notes, key)
            if key == "today":
                self.overview_today_title = frame.title_label
        self.overview_thermal = self.label(parent, "Controle térmico: aguardando supervisor", fg=MUTED, anchor="w", font=(BODY_FONT, 9))
        self.overview_thermal.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))
        wallet_panel = tk.Frame(parent, bg=PANEL, padx=12, pady=8, highlightthickness=1, highlightbackground=BORDER)
        wallet_panel.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 8))
        self.label(wallet_panel, "CARTEIRA CONFIGURADA PARA RECEBER PEARL (PRL)", bg=PANEL, fg=TEAL, font=(BODY_FONT, 9, "bold")).pack(anchor="w", pady=(0, 4))
        row = tk.Frame(wallet_panel, bg=PANEL)
        row.pack(fill="x")
        actions = tk.Frame(row, bg=PANEL)
        actions.pack(side="right", padx=(10, 0))
        self.wallet_copy_button = tk.Button(actions, text="Copiar", command=self.copy_wallet, bg=BG, fg=TEXT, relief="flat", padx=10, pady=5, font=(BODY_FONT, 9))
        self.wallet_copy_button.pack(side="left", padx=(0, 6))
        self.wallet_pool_button = tk.Button(actions, text="Abrir na pool", command=self.open_wallet_pool, bg=BG, fg=TEXT, relief="flat", padx=10, pady=5, font=(BODY_FONT, 9))
        self.wallet_pool_button.pack(side="left")
        self.wallet_var = tk.StringVar(value=WALLET)
        self.wallet_entry = tk.Entry(row, textvariable=self.wallet_var, state="readonly", readonlybackground=PANEL, foreground=TEXT, selectbackground=BLUE,
                                    selectforeground=TEXT, relief="flat", font=("Consolas", 10), width=64)
        self.wallet_entry.pack(side="left", fill="x", expand=True, ipady=4)
        payout = tk.Frame(parent, bg=PANEL, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER)
        payout.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 8))
        payout.columnconfigure((0, 1), weight=1, uniform="payout")
        self.label(payout, "Próximo pagamento", bg=PANEL, font=(TITLE_FONT, 14, "bold")).grid(row=0, column=0, sticky="w")
        self.payout_threshold = self.label(payout, "Mínimo: N/D", bg=PANEL, fg=MUTED, anchor="e", font=(BODY_FONT, 9))
        self.payout_threshold.grid(row=0, column=1, sticky="e")
        self.payout_state = self.label(payout, "Aguardando pool", bg=PANEL, fg=MUTED, anchor="w", font=(BODY_FONT, 9))
        self.payout_state.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 7))
        left, right = tk.Frame(payout, bg=PANEL), tk.Frame(payout, bg=PANEL)
        left.grid(row=2, column=0, sticky="nsew", padx=(0, 12))
        right.grid(row=2, column=1, sticky="nsew", padx=(12, 0))
        self.payout_generated_text = self.label(left, "Gerado na pool: N/D", bg=PANEL, anchor="w", font=(BODY_FONT, 9))
        self.payout_generated_text.pack(fill="x")
        self.payout_generated_bar = ttk.Progressbar(left, style="Generated.Horizontal.TProgressbar", maximum=100, mode="determinate")
        self.payout_generated_bar.pack(fill="x", pady=(3, 7))
        self.payout_confirmed_text = self.label(left, "Confirmado para envio: N/D", bg=PANEL, anchor="w", font=(BODY_FONT, 9))
        self.payout_confirmed_text.pack(fill="x")
        self.payout_confirmed_bar = ttk.Progressbar(left, style="Confirmed.Horizontal.TProgressbar", maximum=100, mode="determinate")
        self.payout_confirmed_bar.pack(fill="x", pady=(3, 6))
        self.payout_remaining = self.label(left, "Pendente / restante: N/D", bg=PANEL, fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 9))
        self.payout_remaining.pack(fill="x")
        self.payout_eta = self.label(right, "Estimativa: aguardando dados", bg=PANEL, fg=TEAL, font=(TITLE_FONT, 12, "bold"), anchor="w", justify="left")
        self.payout_eta.pack(fill="x")
        self.payout_details = self.label(right, "", bg=PANEL, fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 9))
        self.payout_details.pack(fill="x", pady=(4, 0))
        self.payout_source = self.label(right, "Fonte: pool", bg=PANEL, fg=MUTED, anchor="w", font=(BODY_FONT, 8))
        self.payout_source.pack(fill="x", pady=(4, 0))
        self.payout_rule = self.label(payout, "Envio automático a cada hora após mínimo confirmado; crédito depende da rede.", bg=PANEL, fg=MUTED, anchor="w", font=(BODY_FONT, 8))
        self.payout_rule.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.overview_activity = self.label(parent, "Atividade: aguardando dados", bg=PANEL, fg=MUTED, anchor="nw", justify="left", padx=12, pady=9, font=(BODY_FONT, 9))
        self.overview_activity.grid(row=4, column=0, sticky="nsew", padx=4, pady=(0, 7))

    def build_gpu(self):
        parent = self.live_tab
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        cards = tk.Frame(parent, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(9, 5))
        for index, (key, title, color) in enumerate((("hash", "HASHRATE", TEAL), ("temp", "TEMPERATURA", ORANGE), ("power", "POTÊNCIA", BLUE), ("fan", "VENTOINHAS", PINK), ("shares", "SHARES ACEITAS", TEAL))):
            self.metric_card(cards, title, color, index, self.card_values, self.card_notes, key)
        self.details = self.label(parent, "Lendo sensores…", fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 9))
        self.details.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 5))
        chart_grid = tk.Frame(parent, bg=BG)
        chart_grid.grid(row=2, column=0, sticky="nsew")
        chart_grid.columnconfigure((0, 1), weight=1, uniform="charts")
        chart_grid.rowconfigure((0, 1), weight=1, uniform="charts")
        self.charts = {}
        for index, (key, title, color, unit, fixed) in enumerate((("hash", "Hashrate · TH/s", TEAL, "TH/s", None), ("temp", "Temperatura da GPU", ORANGE, "°C", (25, 85)), ("power", "Potência da placa", BLUE, "W", None), ("fan", "Ventoinhas", PINK, "%", (0, 100)))):
            chart = Chart(chart_grid, title, color, unit, fixed, max_gap=180 if key == "hash" else 80, show_age=key == "hash")
            chart.grid(row=index//2, column=index%2, sticky="nsew", padx=4, pady=3)
            self.charts[key] = chart
        self.info = self.label(parent, "Pool e sessão: aguardando", fg=MUTED, anchor="w", font=(BODY_FONT, 8))
        self.info.grid(row=3, column=0, sticky="ew", padx=5, pady=(5, 3))
        self.log_title = self.label(parent, "ÚLTIMOS EVENTOS", fg=TEAL, anchor="w", font=(BODY_FONT, 8, "bold"))
        self.log_title.grid(row=4, column=0, sticky="ew", padx=5)
        self.logs = self.label(parent, "Aguardando atividade", bg=PANEL, fg=MUTED, anchor="nw", justify="left", font=("Consolas", 8), padx=8, pady=5)
        self.logs.grid(row=5, column=0, sticky="ew", padx=4, pady=(3, 7))

    def build_finance(self):
        parent = self.finance_tab
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        self.quote = self.label(parent, "Consultando cotações PRL / BRL / USD / BTC…", fg=TEAL, anchor="w", font=(TITLE_FONT, 12, "bold"))
        self.quote.grid(row=0, column=0, sticky="ew", padx=5, pady=(10, 4))
        self.quote_note = self.label(parent, "Cotações e pool a cada 60 s", fg=MUTED, anchor="w", font=(BODY_FONT, 8))
        self.quote_note.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 6))
        table_frame = tk.Frame(parent, bg=BG)
        table_frame.grid(row=2, column=0, sticky="nsew", padx=4)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure("Finance.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=25, font=(BODY_FONT, 10), borderwidth=0)
        style.configure("Finance.Treeview.Heading", background=BORDER, foreground=TEXT, font=(BODY_FONT, 9, "bold"), relief="flat")
        style.map("Finance.Treeview", background=[("selected", BORDER)], foreground=[("selected", TEXT)])
        self.finance_table = ttk.Treeview(table_frame, columns=("prl", "brl", "usd", "btc"), show="tree headings", height=9, style="Finance.Treeview", selectmode="none")
        self.finance_table.grid(row=0, column=0, sticky="nsew")
        self.finance_table.heading("#0", text="PERÍODO / TIPO", anchor="w")
        for key, title in (("prl", "PEARL · PRL"), ("brl", "REAIS · BRL"), ("usd", "DÓLARES · USD"), ("btc", "BITCOIN · BTC")):
            self.finance_table.heading(key, text=title, anchor="e")
            self.finance_table.column(key, width=190, minwidth=90, stretch=True, anchor="e")
        self.finance_table.column("#0", width=240, minwidth=170, stretch=True)
        self.finance_row_titles = {}
        for key, title in (("day", "Hoje"), ("week", "Semana · segunda até agora"), ("month", "Últimos 30 dias"), ("accumulated", "Acumulado · pago + saldo"), ("unpaid", "Saldo atual na pool"), ("confirmed", "Confirmado para envio"), ("paid", "Enviado pela pool"), ("forecast7", "Projeção · próximos 7 dias"), ("forecast30", "Projeção · próximos 30 dias")):
            self.finance_row_titles[key] = title
            self.finance_table.insert("", "end", iid=key, text=title, values=("N/D",)*4, tags=("forecast",) if key.startswith("forecast") else ())
        self.finance_table.tag_configure("forecast", foreground=ORANGE)
        self.money_chart = Chart(parent, "Créditos observados · em reais à cotação atual", TEAL, "R$", window=30*86400)
        self.money_chart.grid(row=3, column=0, sticky="nsew", padx=4, pady=(6, 5))
        parent.rowconfigure(3, weight=1)
        self.finance_pay_summary = self.label(parent, "Pagamento em PRL · aguardando dados", fg=TEAL, anchor="w", justify="left", font=(BODY_FONT, 9))
        self.finance_pay_summary.grid(row=4, column=0, sticky="ew", padx=5, pady=(2, 3))
        self.finance_explanation = self.label(parent, "", fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 8))
        self.finance_explanation.grid(row=5, column=0, sticky="ew", padx=5, pady=(0, 3))
        self.finance_sources = self.label(parent, "", fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 8))
        self.finance_sources.grid(row=6, column=0, sticky="ew", padx=5, pady=(0, 7))

    def adapt_layout(self, event=None):
        if event is not None and event.widget is not self:
            return
        width, height = self.winfo_width(), self.winfo_height()
        if width < 500:
            return
        compact = height < 850 or width < 1450
        if height < 780:
            self.overview_activity.grid_remove()
            self.overview_tab.rowconfigure(4, weight=0)
        else:
            self.overview_activity.grid()
            self.overview_tab.rowconfigure(4, weight=1)
        body = 12 if compact else min(19, max(14, round(min(width/1650, height/1000)*14)))
        value_size = 29 if compact else min(46, max(34, round(body*2.4)))
        if not hasattr(self, "_font_baselines"):
            self._font_baselines = {}
        pending = list(self.winfo_children())
        while pending:
            widget = pending.pop()
            if widget.winfo_toplevel() is not self:
                continue
            pending.extend(widget.winfo_children())
            try:
                if "font" not in widget.keys():
                    continue
                if widget not in self._font_baselines:
                    font = tkfont.Font(font=widget.cget("font"))
                    self._font_baselines[widget] = (font.actual("family"), abs(font.actual("size")), font.actual("weight"))
                family, points, weight = self._font_baselines[widget]
                pixels = max(11, round(points*4/3*(body/12)))
                widget.configure(font=(family, -pixels, weight))
            except tk.TclError:
                pass
        for label in list(self.card_values.values())+list(self.overview_values.values()):
            label.configure(font=(TITLE_FONT, -value_size, "bold"))
        for label in list(self.card_notes.values())+list(self.overview_notes.values()):
            label.configure(font=(BODY_FONT, -body))
        for label in (self.details, self.overview_thermal, self.payout_generated_text, self.payout_confirmed_text, self.payout_remaining, self.payout_details):
            label.configure(font=(BODY_FONT, -body))
        half = max(450, (width-95)//2)
        for label in (self.payout_eta, self.payout_details, self.payout_remaining):
            label.configure(wraplength=half)
        for label in (self.details, self.overview_activity, self.logs, self.finance_pay_summary, self.finance_explanation, self.finance_sources):
            label.configure(wraplength=width-55)
        lineheight = tkfont.Font(family=BODY_FONT, size=-body).metrics("linespace")
        rowheight = lineheight+8
        ttk.Style(self).configure("Finance.Treeview", rowheight=rowheight, font=(BODY_FONT, -body))
        ttk.Style(self).configure("Finance.Treeview.Heading", font=(BODY_FONT, -body, "bold"))
        ttk.Style(self).configure("TNotebook.Tab", font=(BODY_FONT, -max(12,body), "bold"))
        for column, share in (("#0", .24), ("prl", .19), ("brl", .18), ("usd", .18), ("btc", .21)):
            self.finance_table.column(column, width=int((width-48)*share), minwidth=80)
        table_height = rowheight*9+31
        self.finance_tab.rowconfigure(2, weight=0, minsize=table_height)
        self.finance_tab.rowconfigure(3, weight=1, minsize=85)
        self.money_chart.configure(height=100 if compact else 170)
        self.layout_profile = {"width": width, "height": height, "compact": compact, "body_font": body, "rowheight": rowheight}
        for chart in list(self.charts.values())+[self.money_chart]:
            chart.title_px = max(13,body)
            chart.axis_px = max(11,body-2)
            chart.draw()

    def refresh_finance(self):
        def timestamp(value):
            value = safe_number(value)
            return dt.datetime.fromtimestamp(value).strftime("%d/%m %H:%M") if value else "N/D"
        miner = self.last_data.get("miner", {}) if self.last_data else {}
        fresh = bool(self.last_data and self.last_data.get("miner_running") and miner.get("hashrate_at") and time.time()-miner["hashrate_at"] < 180 and not miner.get("paused"))
        model = financial_model(self.finance_state, miner.get("average_1m") if fresh else None)
        self.last_finance_model = model
        wallet = str(model.get("wallet") or WALLET or "")
        if self.wallet_var.get() != wallet:
            self.wallet_var.set(wallet)
        for button in (self.wallet_copy_button, self.wallet_pool_button):
            button.configure(state="normal" if wallet else "disabled")
        payout = model.get("payout") or {}
        price = safe_number(model.get("price_brl"))
        threshold = safe_number(payout.get("threshold_prl"))
        self.payout_threshold.configure(text="Mínimo: " + format_prl(threshold) + " · " + format_brl(payout.get("threshold_brl")))
        self.payout_state.configure(text=payout.get("state_label") or "Aguardando saldo e confirmação da pool")
        for widget, bar, key, pct_key, label in ((self.payout_generated_text,self.payout_generated_bar,"total_prl","progress_pct","Gerado"),(self.payout_confirmed_text,self.payout_confirmed_bar,"confirmed_prl","confirmed_progress_pct","Confirmado")):
            pct = safe_number(payout.get(pct_key))
            pct_text = f"{pct:.1f}%".replace(".", ",") if pct is not None else "N/D"
            widget.configure(text=f"{label}: {format_prl(payout.get(key))} / {format_prl(threshold)} · {pct_text}")
            bar.configure(value=max(0,min(100,pct)) if pct is not None else 0)
            bar.state(["!disabled"] if pct is not None else ["disabled"])
        self.payout_remaining.configure(text="Pendente: " + format_prl(payout.get("unconfirmed_prl")) + "\nFalta gerar: " + format_prl(payout.get("remaining_prl")) + " · " + format_brl(payout.get("remaining_brl")))
        self.payout_eta.configure(text=payout.get("eta_label") or "Estimativa indisponível")
        details = [str(payout[key]) for key in ("eta_details","rate_label") if payout.get(key)]
        self.payout_details.configure(text="\n".join(details))
        self.payout_source.configure(text="Pool atualizada: " + timestamp(payout.get("source_at")) + (" · desatualizada" if payout.get("stale") else ""))
        currencies = model.get("currencies") or {}
        for key in self.finance_table.get_children():
            partial_period = model.get("periods",{}).get(key,{}).get("partial")
            self.finance_table.item(key,text=self.finance_row_titles[key]+(" · parcial" if partial_period else ""))
            row = currencies.get(key) or {}
            if not row:
                legacy = {"accumulated":model.get("accumulated_prl"),"unpaid":model.get("unpaid_prl"),"confirmed":payout.get("confirmed_prl"),"paid":model.get("paid_prl")}
                prl = model.get("periods",{}).get(key,{}).get("prl",legacy.get(key))
                if key in ("forecast7","forecast30") and model.get("forecast"):
                    prl = model["forecast"]["prl_day"]*(7 if key=="forecast7" else 30)
                row = {"prl":prl,"brl":prl*price if prl is not None and price is not None else None}
            prl,usd,btc = safe_number(row.get("prl")),safe_number(row.get("usd")),safe_number(row.get("btc"))
            values = ((f"{prl:.8f}".replace(".",",") if prl is not None else "N/D"),format_brl(row.get("brl")),("US$ "+f"{usd:,.2f}".replace(",","_").replace(".",",").replace("_",".") if usd is not None else "N/D"),(f"{btc:.8f}".replace(".",",") if btc is not None else "N/D"))
            self.finance_table.item(key, values=values)
        today = currencies.get("day") or {}
        today_partial = model.get("periods",{}).get("day",{}).get("partial")
        self.overview_today_title.configure(text="HOJE · PARCIAL · BRL" if today_partial else "GERADO HOJE · BRL")
        today_brl = today.get("brl",model.get("periods",{}).get("day",{}).get("brl"))
        self.overview_values["today"].configure(text=format_brl(today_brl))
        today_usd, today_btc = safe_number(today.get("usd")),safe_number(today.get("btc"))
        usd_text = "US$ "+f"{today_usd:.2f}".replace(".",",") if today_usd is not None else "USD N/D"
        btc_text = f"{today_btc:.8f} BTC".replace(".",",") if today_btc is not None else "BTC N/D"
        self.overview_notes["today"].configure(text=usd_text+" · "+btc_text)
        quote_usd = safe_number(model.get("price_usd"))
        self.quote.configure(text="1 PRL = "+format_brl(price)+(f" · US$ {quote_usd:.4f}".replace(".",",") if quote_usd is not None else " · USD N/D")+"     |     1 BTC = "+format_brl(model.get("price_btc_brl")))
        stale = bool(model.get("errors") or model.get("btc_price_stale") or (model.get("price_at") and time.time()-model["price_at"]>180))
        self.quote_note.configure(text="Cotação PRL: "+timestamp(model.get("price_at"))+" · BTC: "+timestamp(model.get("btc_price_at"))+" · Pool: "+timestamp(model.get("pool_at"))+(" · cache/desatualizado" if stale else " · atualização 60 s"),fg=ORANGE if stale else MUTED)
        self.finance_pay_summary.configure(text="Pagamento real em PRL · Carteira "+(wallet[:12]+"—"+wallet[-8:] if wallet else "não configurada")+" · Mínimo "+format_prl(threshold)+" · Falta "+format_prl(payout.get("remaining_prl"))+"\n"+str(payout.get("eta_label") or "Estimativa aguardando dados"))
        partial = model.get("periods",{}).get("day",{})
        self.finance_explanation.configure(text=("Observado parcialmente desde "+timestamp(partial.get("since"))+". " if partial.get("partial") else "")+"Conversões equivalentes à cotação atual; o pagamento permanece em PRL. Projeções: 24 h/dia e condições atuais.")
        self.finance_sources.configure(text="Fontes: Kryptex Pool API · CoinGecko (Pearl pearl-2 / Bitcoin). Semana: segunda até agora, São Paulo. Histórico permanente local.")
        snapshots = self.finance_state.get("snapshots",[])
        self.money_chart.points.clear()
        if len(snapshots)>=2 and price:
            baseline=snapshots[0]["total_prl"]
            for point in snapshots:
                self.money_chart.add(point["timestamp"],(point["total_prl"]-baseline)*price)
        self.money_chart.draw()
        try:
            STATE_DIR.mkdir(parents=True,exist_ok=True)
            (STATE_DIR/"finance-latest.json").write_text(json.dumps(model,ensure_ascii=False,indent=2),encoding="utf-8")
        except OSError:
            pass

    def apply(self, data):
        self.last_data = data
        now = time.time()
        gpu, miner = data.get("gpu") or {}, data["miner"]
        stamp = miner.get("hashrate_at")
        age = now-stamp if stamp else None
        fresh = age is not None and -5 <= age <= 180 and data["miner_running"] is True and not miner.get("paused")
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
        for key in ("hash", "temp", "power"):
            self.overview_values[key].configure(text=self.card_values[key].cget("text"))
            self.overview_notes[key].configure(text=self.card_notes[key].cget("text"))
        efficiency = hashrate/gpu["power.draw"] if hashrate and gpu.get("power.draw") else None
        thermal_text = thermal_description(data.get("thermal_control"))
        self.overview_thermal.configure(text="Controle térmico: " + thermal_description(data.get("thermal_control"), compact=True))
        self.details.configure(text=f"Núcleo {fmt(gpu.get('clocks.gr'), ' MHz')} · Memória {fmt(gpu.get('clocks.mem'), ' MHz')} · VRAM {fmt(gpu.get('memory.used'))} / {fmt(gpu.get('memory.total'), ' MiB')} · Eficiência {fmt(efficiency, ' TH/s/W', 3)}\nDriver {gpu.get('driver_version', '—')} · Estado {gpu.get('pstate', '—')} · Uso GPU {fmt(gpu.get('utilization.gpu'), '%')} / memória {fmt(gpu.get('utilization.memory'), '%')} · Média 15 min {fmt(miner.get('average_15m') if fresh else None, ' TH/s', 1)}\nControle térmico: {thermal_text}", wraplength=max(700, self.winfo_width()-60))
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
            recent_lines = [line for line in logs.splitlines() if line.strip()][-2:]
            self.logs.configure(text="\n".join(recent_lines) or "Sem eventos recentes")
            self.overview_activity.configure(text="ATIVIDADE RECENTE\n" + ("\n".join(recent_lines) or "Aguardando minerador"))
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
        snapshot["charts"] = {key: chart.sample_info() for key, chart in self.charts.items()}
        snapshot["monitor_status"] = status
        snapshot["app"] = {"name": APP_NAME, "tray_registered": self.tray.registered_ok,
            "tray_error": self.tray.error, "panel_state": self.state(),
            "pid": os.getpid(),
            "summary_open": bool(self.summary_window and self.summary_window.winfo_exists())}
        snapshot["window"] = {"width": self.winfo_width(), "height": self.winfo_height()}
        snapshot["layout"] = {"tab_count": len(self.tabs.tabs()), "tab_names": [self.tabs.tab(index,"text") for index in range(len(self.tabs.tabs()))],
            "selected_tab": self.tabs.index(self.tabs.select()), "client_size": [self.winfo_width(),self.winfo_height()],
            "profile": getattr(self,"layout_profile",{}), "font_units": "pixels", "scrollbars": False,
            "work_area": list(work_area(self))}
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
                match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
                width,height,x,y = map(int,match.groups())
                left,top,right,bottom = work_area(self)
                width,height = min(width,right-left-16),min(height,bottom-top-44)
                x,y = max(left,min(x,right-width-16)),max(top,min(y,bottom-height-44))
                self.geometry(f"{width}x{height}+{x}+{y}")
            if preferences.get("tab") in (0, 1, 2):
                index = preferences["tab"]
                if preferences.get("tab_schema") != "three-tabs-v1" and index in (0,1):
                    index += 1
                self.tabs.select(index)
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
    payout = finance.get("payout") or {}
    wallet = str(finance.get("wallet") or WALLET or "não configurada")
    history = snapshot.get("history", {})
    return "\n".join([
        "HASHPILOT  |  TECH FERNANDES",
        "Resumo local do monitor. Ctrl+C fecha somente este console.",
        "="*82,
        (snapshot.get("monitor_status", "Aguardando monitor") if active else "MONITOR SEM ATUALIZACAO - ultimo registro abaixo"),
        "Carteira de recebimento configurada (Pearl PRL): " + wallet,
        f"Hashrate: {fmt(miner.get('hashrate'), ' TH/s', 2)}    Media 1 min: {fmt(miner.get('average_1m'), ' TH/s', 2)}",
        f"GPU: {fmt(gpu.get('temperature.gpu'), ' C')}  |  Potencia: {fmt(gpu.get('power.draw'), ' W', 1)}  |  Fan: {fmt(gpu.get('fan.speed'), '%')}",
        f"Core: {fmt(gpu.get('clocks.gr'), ' MHz')}  |  Memoria: {fmt(gpu.get('clocks.mem'), ' MHz')}",
        "Termico: " + thermal_description(snapshot.get("thermal_control"), compact=True),
        f"Shares aceitas: {miner.get('accepted', 'N/D')}  |  Rejeitadas: {miner.get('rejected', 'N/D')}  |  HW: {miner.get('hw_errors', 'N/D')}",
        f"Hoje observado {'(parcial)' if today.get('partial') else ''}: {money(today.get('brl'))}  |  Acumulado pool: {money(finance.get('accumulated_brl'))}",
        f"Projecao 7 dias: {money(forecast.get('brl_7days'))}  |  Projecao 30 dias: {money(forecast.get('brl_30days'))}",
        f"Cotacao atual 1 PRL: {money(finance.get('price_brl'))}  |  Previsoes a preco/rede atuais, 24 h/dia.",
        "Proximo pagamento - estimativa: " + str(payout.get("eta_label") or "aguardando dados da pool"),
        "Falta gerar: " + format_prl(payout.get("remaining_prl")) + "  |  Confirmado: " + format_prl(payout.get("confirmed_prl")) + "  |  Pendente: " + format_prl(payout.get("unconfirmed_prl")),
        "Envio automatico a cada hora apos minimo confirmado; credito depende da rede.",
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
