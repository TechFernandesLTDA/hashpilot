"""Monitor local de leitura: NVIDIA SMI + logs do SRBMiner. Sem dependências externas."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import webbrowser
from urllib.parse import quote
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import sys
import math
from tkinter import ttk
from finance import financial_model, PortfolioFinanceCollector, portfolio_model, portfolio_snapshots
from history import HistoryStore, log_key
from miner_log import COUNTERS, RATE, log_timestamp
from app_logging import configure as configure_logging, exception_once, get_logger
from config import WALLET, POOL, LOG_DIR, STATE_DIR, SETTINGS_PATH
from fsutil import atomic_json
from contribution import read_support_status, read_settings as read_support_settings
from file_cache import cached_load, signature as file_signature


from tray import WindowsTray
from bootstrap import restore_bundled_data
from rig_panel import RigCards, project_selected, totals, show_identity
from app_update import build_identity
from monitor_theme import ASSET_DIR, BODY_FONT, TITLE_FONT, BG, PANEL, BORDER, TEXT, MUTED, TEAL, BLUE, ORANGE, PINK  # noqa: F401
from monitor_format import fmt, safe_number, format_prl, format_brl, payout_for_local_state, thermal_description  # noqa: F401
from monitor_sensors import NO_WINDOW, selected_gpu_context, run, miner_process_ids, number, read_gpu, tail, parse_log  # noqa: F401
from monitor_chart import Chart  # noqa: F401
from monitor_layout import MonitorLayoutMixin


APP_NAME = "HashPilot — Tech Fernandes"
APP_BUILD = build_identity()
DPI_MODE = {"requested":"PerMonitorV2","enabled":False,"fallback":True}


LOG = get_logger("monitor")
CLOSE_TIMEOUT_SECONDS = 20


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


class Collector:
    def __init__(self, history=None):
        self.samples: queue.Queue = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.log_path: Path | None = None
        self.log_signature = None
        self.log_data: dict = parse_log("")
        self.start_times: dict[str, float | None] = {}
        self.history = history
        from gpu_runtime import RigTelemetry
        self.rig_reader=RigTelemetry()
        self.selected_uuid=None

    def sample(self) -> dict:
        processes=None
        process_error=None
        try:
            processes=miner_process_ids()
        except Exception as error:
            exception_once(LOG, 'processes', 'Falha ao listar processos do minerador')
            process_error=str(error)
        try:
            rig=self.rig_reader.sample(processes)
        except Exception as error:
            exception_once(LOG, 'rig', 'Falha ao ler telemetria do rig')
            rig={'schema_version':2,'devices':[],'totals':totals([]),'ignored_devices':[],'error':str(error)}
        now=time.time()
        support_settings=cached_load(SETTINGS_PATH,read_support_settings)
        for device in rig['devices']:
            directory=Path(device['log_dir'])
            if device.get('state')=='legacy_readonly':directory=LOG_DIR
            device['support_contribution']=read_support_status(directory,device['wallet'],support_settings)
            if self.history and device.get('log_path'):
                try:self.history.archive_log(Path(device['log_path']),gpu_uuid=device['uuid'],wallet=device['wallet'])
                except OSError:pass
        from miner_update import active_executable
        miner_file=active_executable(verify=False)
        result={'collected_at':now,'rig':rig,'gpu':None,'gpu_error':rig.get('error'),'miner':parse_log(''),
            'miner_executable_available':miner_file.is_file(),
            'miner_running':None,'process_error':process_error,'log_error':None,'log_path':None,'thermal_control':None,'support_contribution':{}}
        return project_selected(result,self.selected_uuid)

    def sample_legacy(self) -> dict:
        result = {"collected_at": time.time(), "gpu": None, "gpu_error": None,
                  "miner_running": None, "process_error": None, "log_error": None}
        try:
            result["gpu"] = read_gpu()
        except Exception as error:
            exception_once(LOG, "legacy-gpu", "Falha ao ler sensores NVIDIA (modo legado)")
            result["gpu_error"] = str(error)
        try:
            result["miner_running"] = bool(miner_process_ids())
        except Exception as error:
            exception_once(LOG, "legacy-processes", "Falha ao listar processos do minerador (modo legado)")
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
                        self.start_times[str(path)] = log_timestamp(first)
                    self.log_signature = signature
                    self.log_path = path
                self.log_data["started_at"] = self.start_times.get(str(path))
            else:
                result["log_error"] = "Nenhum log de mineração encontrado."
        except Exception as error:
            exception_once(LOG, "legacy-log", "Falha ao ler log do minerador (modo legado)")
            result["log_error"] = str(error)
        result["miner"] = self.log_data.copy()
        result["log_path"] = str(self.log_path) if self.log_path else None
        result["thermal_control"] = read_thermal_status()
        result["support_contribution"] = read_support_status(LOG_DIR,WALLET,cached_load(SETTINGS_PATH,read_support_settings))
        return result

    def work(self):
        while not self.stop.is_set():
            started = time.monotonic()
            data = self.sample()
            if self.history:
                if hasattr(self.history,'rig_telemetry'):
                    self.history.rig_telemetry(data['rig'])
                legacy=next((d for d in data['rig']['devices'] if d['index']==0),None)
                if legacy:
                    self.history.telemetry({**data,'wallet':legacy['wallet'],**{k:legacy.get(k) for k in ('gpu','miner','miner_running','log_path')}},source='live')
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


def enable_native_dpi_awareness():
    """Configura somente este processo, antes de criar qualquer janela Tk."""
    global DPI_MODE
    if os.name != "nt":
        DPI_MODE = {"requested":"platform-native","enabled":True,"fallback":False}
        return DPI_MODE
    try:
        import ctypes
        from ctypes import wintypes
        user=ctypes.WinDLL("user32",use_last_error=True)
        user.SetProcessDpiAwarenessContext.argtypes=[wintypes.HANDLE]
        user.SetProcessDpiAwarenessContext.restype=wintypes.BOOL
        user.GetThreadDpiAwarenessContext.restype=wintypes.HANDLE
        user.AreDpiAwarenessContextsEqual.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
        user.AreDpiAwarenessContextsEqual.restype=wintypes.BOOL
        desired=ctypes.c_void_p(-4)
        changed=bool(user.SetProcessDpiAwarenessContext(desired))
        native=bool(user.AreDpiAwarenessContextsEqual(user.GetThreadDpiAwarenessContext(),desired))
        DPI_MODE={"requested":"PerMonitorV2","enabled":native,"fallback":not native,"configured_now":changed,
                  "error":None if native else ctypes.get_last_error()}
    except (AttributeError,OSError) as error:
        DPI_MODE={"requested":"PerMonitorV2","enabled":False,"fallback":True,"error":str(error)}
    return DPI_MODE


def work_area(root, geometry=None):
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            user = ctypes.WinDLL("user32", use_last_error=True)
            class MonitorInfo(ctypes.Structure):
                _fields_ = [("cbSize",wintypes.DWORD),("rcMonitor",wintypes.RECT),("rcWork",wintypes.RECT),("dwFlags",wintypes.DWORD)]
            user.GetAncestor.argtypes = [wintypes.HWND,wintypes.UINT]
            user.GetAncestor.restype = wintypes.HWND
            user.MonitorFromWindow.argtypes = [wintypes.HWND,wintypes.DWORD]
            user.MonitorFromWindow.restype = wintypes.HANDLE
            user.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT),wintypes.DWORD]
            user.MonitorFromRect.restype = wintypes.HANDLE
            user.GetMonitorInfoW.argtypes = [wintypes.HANDLE,ctypes.POINTER(MonitorInfo)]
            match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)",geometry or "")
            if match:
                width,height,x,y = map(int,match.groups())
                position = wintypes.RECT(x,y,x+min(width,200),y+min(height,200))
                handle = user.MonitorFromRect(ctypes.byref(position),2)
            else:
                window = user.GetAncestor(root.winfo_id(),2)
                handle = user.MonitorFromWindow(window,2)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if handle and user.GetMonitorInfoW(handle,ctypes.byref(info)):
                area = info.rcWork
                return area.left,area.top,area.right,area.bottom
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()-40


class Monitor(MonitorLayoutMixin, tk.Tk):
    def __init__(self):
        enable_native_dpi_awareness()
        restore_bundled_data()
        super().__init__()
        global BODY_FONT, TITLE_FONT
        families = set(tkfont.families(self))
        BODY_FONT = "Plus Jakarta Sans" if "Plus Jakarta Sans" in families else "Segoe UI"
        TITLE_FONT = "Sora" if "Sora" in families else "Segoe UI"
        self.title(APP_NAME+' · '+APP_BUILD['version'])
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
        self._panel_return_state = "normal"
        self._normal_geometry = None
        self.collector = Collector(self.history)
        self.finance_collector = PortfolioFinanceCollector(STATE_DIR, self.history)
        self.finance_state = {}
        from gpu_runtime import GPURuntime
        self.gpu_runtime = GPURuntime()
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
        self.collector_thread = threading.Thread(target=self.collector.work, daemon=True, name="collector")
        self.collector_thread.start()
        threading.Thread(target=self.finance_collector.work, daemon=True).start()
        self.after(200, self.poll)
        self.after(1500,self.show_restore_result)
        from app_update import UpdateChecker
        self.update_checker = UpdateChecker(STATE_DIR)
        from miner_update import MinerUpdateChecker
        self.miner_update_checker=MinerUpdateChecker(STATE_DIR)
        self.after(10000, self.poll_updates)

    def show_restore_result(self):
        path=STATE_DIR/'restore-result.json'
        try:
            result=json.loads(path.read_text(encoding='utf-8-sig'))
            if result.get('seen'):return
            from tkinter import messagebox
            if result.get('ok'):
                messagebox.showinfo('Backup restaurado','Históricos e configurações restaurados. Credenciais locais preservadas. Confira as carteiras em Configurações antes de iniciar o rig.\nBackup anterior: '+str(result.get('rollback','')),parent=self)
            else:messagebox.showerror('Restauração não concluída',str(result.get('error','Verifique o backup anterior antes de continuar.')),parent=self)
            result['seen']=True
            atomic_json(path,result)
        except (OSError,ValueError):pass

    def poll_updates(self):
        if getattr(self,'closing',False):return
        if self.grab_current() not in (None,self):
            self.after(1000,self.poll_updates);return
        self.update_checker.start()
        try:
            result=self.update_checker.results.get_nowait()
            release=result.get('release')
            from app_update import read_preferences
            if result['include_prereleases']!=read_preferences(STATE_DIR)['include_prereleases']:
                release=None;self.update_checker.next_check=0
            if release and release['version'] not in self.update_checker.offered:
                self.update_checker.offered.add(release['version'])
                from tkinter import messagebox
                if messagebox.askyesno('Atualização disponível',f"HashPilot {release['version']} disponível"+(' (pré-lançamento)' if release['prerelease'] else '')+'. Abrir o assistente de atualização? Nenhuma instalação será feita sem sua confirmação.',parent=self):
                    from app_update import show_update_setup
                    show_update_setup(self)
        except queue.Empty:pass
        self.miner_update_checker.start()
        try:
            result=self.miner_update_checker.results.get_nowait()
            if result.get('newer') and result['version'] not in self.miner_update_checker.offered:
                self.miner_update_checker.offered.add(result['version'])
                from tkinter import messagebox
                if messagebox.askyesno('Atualização do minerador',f"SRBMiner {result['version']} disponível. Abrir o assistente? A instalação depende da sua confirmação e da revisão de compatibilidade do HashPilot.",parent=self):
                    from miner_update_ui import show_miner_update
                    show_miner_update(self)
        except queue.Empty:pass
        self.after(1000,self.poll_updates)

    def hide_to_tray(self):
        if self.tray.registered_ok:
            self.remember_window_state()
            self.save_preferences()
            self.withdraw()
        else:
            # Nunca esconda a única janela se o ícone não pôde ser registrado.
            self.iconify()

    def on_minimize(self, event):
        if event.widget is self and self.state() == "iconic" and self.tray.registered_ok:
            self.after(80, self.hide_to_tray)

    def show_panel(self):
        desired = "zoomed" if self.state()=="zoomed" else getattr(self,"_panel_return_state","normal")
        self.deiconify()
        try:
            self.state(desired)
        except tk.TclError:
            self.state("normal")
        self.lift()

    def configure_setup(self, section=None):
        from settings_ui import show_settings
        show_settings(self, section)

    def open_gpu_agent(self):
        from gpu_runtime import show_gpu_panel
        show_gpu_panel(self, self.gpu_runtime)

    def select_gpu(self,identifier):
        self.selected_gpu_uuid=identifier
        if hasattr(self,'collector'):self.collector.selected_uuid=identifier
        if hasattr(self,'gpu_runtime'):self.gpu_runtime.select(identifier)
        if self.last_data:
            self.apply(project_selected(self.last_data,identifier))

    def configure_gpu(self,identifier):
        self.select_gpu(identifier)
        self.configure_setup('GPUs')

    def show_more_menu(self, anchor):
        menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=TEXT, activebackground=BLUE, activeforeground=TEXT,
                       font=(BODY_FONT, 10), bd=0)
        for text, command in (("Benchmark / IA", self.open_gpu_agent), ("Resumo compacto", self.show_summary),
                              ("Console", self.open_console)):
            menu.add_command(label=text, command=command)
        menu.add_separator()
        menu.add_command(label="Recolher para a bandeja", command=self.hide_to_tray)
        menu.add_command(label="Sair do HashPilot", command=self.close)
        try:
            menu.tk_popup(anchor.winfo_rootx(), anchor.winfo_rooty()+anchor.winfo_height())
        finally:
            menu.grab_release()

    def toggle_gpu(self,identifier,enabled):
        from tkinter import messagebox
        try:
            if not enabled:
                from stop_mining import request_stop
                result=request_stop([identifier],STATE_DIR,LOG_DIR,SETTINGS_PATH)
                if result['requires_uac']:self.status.configure(text='● Parada solicitada · aprove o UAC para restaurar padrões',fg=ORANGE)
                return
            from start_mining import request_start
            from onboarding import confirm_author_wallet, read_settings
            if not confirm_author_wallet(self,read_settings(SETTINGS_PATH),[identifier]): return
            request_start([identifier],STATE_DIR,LOG_DIR,SETTINGS_PATH)
            if enabled and not self.rig_service_running():
                from onboarding import launch_rig_service
                launch_rig_service()
            self.status.configure(text='● Preferência salva · aguardando serviço do rig',fg=ORANGE)
            if hasattr(self,'collector'):self.collector.rig_reader.detected_at=0
        except (OSError,ValueError,RuntimeError) as error:messagebox.showerror('Configuração da GPU',str(error),parent=self)

    def rig_service_running(self):
        try:
            status=json.loads((STATE_DIR/'rig-manager.json').read_text(encoding='utf-8-sig'))
            pid=int(status.get('pid',0))
            if not status.get('ready') or pid<=0 or not 0<=time.time()-float(status.get('sampled_at',0))<=15:return False
            try:
                from process_list import pid_running
                return pid_running(pid)
            except OSError:
                output=run(['tasklist','/FI',f'PID eq {pid}','/FO','CSV','/NH'])
                return any(len(row)>1 and row[1]==str(pid) for row in csv.reader(output.splitlines()))
        except (OSError,ValueError,RuntimeError):return False

    def start_rig(self):
        from onboarding import launch_rig_service
        from start_mining import request_start
        from tkinter import messagebox
        try:
            devices=((self.last_data or {}).get('rig') or {}).get('devices') or []
            if not devices:
                from gpu_agent import detect_nvidia_gpus
                devices=detect_nvidia_gpus(max_devices=6)
            identifiers=[device['uuid'] for device in devices if device.get('uuid')]
            if not identifiers:raise ValueError('Nenhuma GPU NVIDIA detectada para iniciar')
            from onboarding import confirm_author_wallet, read_settings
            if not confirm_author_wallet(self,read_settings(SETTINGS_PATH),identifiers): return
            request_start(identifiers,STATE_DIR,LOG_DIR,SETTINGS_PATH)
            if not self.rig_service_running():launch_rig_service()
            self.status.configure(text='● Mineração solicitada · aguardando partida das GPUs',fg=ORANGE)
        except (OSError,ValueError,RuntimeError) as error:messagebox.showerror('Iniciar rig',str(error),parent=self)

    def stop_mining(self,all_devices=False):
        from stop_ui import show_stop
        existing=getattr(self,'stop_window',None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            if all_devices:existing.stop_all()
            return existing
        devices=((self.last_data or {}).get('rig') or {}).get('devices') or []
        self.show_panel()
        self.stop_window=show_stop(self,devices,immediate_all=all_devices)
        return self.stop_window

    def selected_identity(self):
        devices=((self.last_data or {}).get('rig') or {}).get('devices') or []
        device=next((d for d in devices if d['uuid']==getattr(self,'selected_gpu_uuid',None)),devices[0] if devices else {})
        show_identity(self,device)

    def remember_window_state(self):
        state = self.state()
        if state in ("normal","zoomed"):
            self._panel_return_state = state
        if state == "normal" and self.winfo_width()>100:
            self._normal_geometry = self.geometry()

    def restore_normal_geometry(self, geometry):
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)",geometry or "")
        if not match:
            return
        width,height,x,y = map(int,match.groups())
        left,top,right,bottom = work_area(self,geometry)
        width,height = min(width,right-left-16),min(height,bottom-top-44)
        x,y = max(left,min(x,right-width-16)),max(top,min(y,bottom-height-44))
        self._normal_geometry = f"{width}x{height}{x:+d}{y:+d}"
        self.geometry(self._normal_geometry)

    def restore_window_preferences(self, preferences):
        self.restore_normal_geometry(preferences.get("geometry"))
        desired = preferences.get("window_state","normal")
        self._panel_return_state = desired if desired in ("normal","zoomed") else "normal"
        if self._panel_return_state == "zoomed":
            try:
                self.state("zoomed")
            except tk.TclError:
                self._panel_return_state = "normal"

    def show_summary(self):
        if self.summary_window and self.summary_window.winfo_exists():
            self.summary_window.deiconify()
            self.summary_window.lift()
            return
        window = self.summary_window = tk.Toplevel(self)
        window.title("HashPilot · Resumo ao vivo · "+APP_BUILD['version'])
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
        wallet = str(model.get("wallet") or (f"{model.get('wallet_count',0)} carteiras · Rendimentos" if model.get('scope')=='portfolio' else WALLET) or "")
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
            "thermal": 'Inativo · minerador parado' if data.get('miner_running') is False else thermal_description(data.get("thermal_control"), compact=True),
            "history": f"{self.history.info.get('telemetry', (0,))[0]} amostras · {'gravando' if not self.history.last_error else 'aguardando'}",
        }
        if data.get('rig'):
            rig=data['rig'];aggregate=rig.get('totals') or {}
            values['status']=f"Rig: {aggregate.get('mining_count',0)}/{len(rig.get('devices',[]))} minerando · GPU {gpu.get('index',0)} selecionada"
            values['hash']=fmt(aggregate.get('hashrate_ths'),' TH/s',1)+' · total do rig'
            values['gpu']=fmt(aggregate.get('max_temperature_c'),' °C máx')+' / '+fmt(aggregate.get('power_w'),' W total',1)
        for key, value in values.items():
            self.summary_values[key].configure(text=value)

    def restore_history(self):
        self.history.ready.wait(2)
        try:
            previous = json.loads((STATE_DIR / "latest.json").read_text(encoding="utf-8"))
            # New rig snapshots may project any selected UUID; their committed
            # samples already live in gpu_telemetry, never import them as GPU0.
            if previous.get("collected_at") and not previous.get('rig'):
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
                        stamp = log_timestamp(line)
                        if stamp is None:
                            stamp = stat.st_mtime
                        parsed = parse_log(line, now=stamp)
                        match = RATE.search(line)
                        if parsed.get("hashrate") is not None and match:
                            fields = dict(COUNTERS.findall(match[4]))
                            gpu = {key: number(fields.get(field, "")) for key, field in (
                                ("temperature.gpu", "T"), ("power.draw", "P"), ("fan.speed", "FAN"),
                                ("clocks.gr", "CC"), ("clocks.mem", "MC"))}
                            self.history.telemetry({"collected_at": stamp, "gpu": gpu, "miner": parsed,
                                "miner_running": None, "log_path": str(path)}, source="imported_miner_log")
                        else:
                            self.history.event(stamp, "miner_log", {"line": line, "path": str(path)}, key=log_key(path, line))
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
        self.remember_window_state()
        self.history.preferences({"geometry": getattr(self,"_normal_geometry",None) or self.geometry(),
            "window_state": getattr(self,"_panel_return_state","normal"),
            "tab": self.tabs.index(self.tabs.select()), "tab_schema": "three-tabs-v1",
            "selected_gpu_uuid":getattr(self,'selected_gpu_uuid',None)})


    def build(self):
        left,top,right,bottom = work_area(self)
        self.minsize(min(1120,right-left-16),min(620,bottom-top-44))
        self.restore_normal_geometry(self.geometry())
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        head = tk.Frame(self, bg=BG)
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(8, 6))
        self.logo_images = {width:tk.PhotoImage(file=ASSET_DIR / f"logo-header-{width}.png") for width in (180,240,360)}
        self.logo_native_width = 180
        self.brand_logo = self.logo_images[180]
        self.brand_logo_label = tk.Label(head,image=self.brand_logo,bg=BG)
        self.brand_logo_label.pack(side="left",padx=(0,14))
        product = tk.Frame(head, bg=BG)
        product.pack(side="left")
        self.label(product, "HashPilot "+APP_BUILD['version'], font=(TITLE_FONT, 20, "bold")).pack(anchor="w")
        self.subtitle = self.label(product, "Mineração e recebimentos", fg=MUTED, font=(BODY_FONT, 9))
        self.subtitle.pack(anchor="w")
        controls = tk.Frame(head, bg=BG)
        controls.pack(side="right")
        self.status = self.label(controls, "● Aguardando sensores", fg=TEAL, font=(BODY_FONT, 10, "bold"))
        self.status.pack(anchor="e", pady=(0, 4))
        buttons = tk.Frame(controls, bg=BG)
        buttons.pack(anchor="e")
        # Primary actions stay visible; everything else lives in the "⋯" menu so the header fits narrow windows.
        for text, command, color in (("Iniciar todas", self.start_rig, TEAL), ("Parar", self.stop_mining, '#9E3333'),
                                     ("Configurações", self.configure_setup, PANEL), ("⋯", None, PANEL)):
            button = tk.Button(buttons, text=text, command=command, bg=color, fg=TEXT, activebackground=BLUE,
                               activeforeground=TEXT, relief="flat", padx=12 if text != "⋯" else 10, pady=4, cursor="hand2",
                               highlightthickness=2, highlightbackground=BG, highlightcolor=TEAL,
                               font=(BODY_FONT, 10 if text == "⋯" else 9, "bold"))
            button.pack(side="left", padx=(5, 0))
            if text == "⋯":
                button.configure(command=lambda b=button: self.show_more_menu(b))
                self.more_button = button
        self.bind_all("<Control-comma>", lambda _: self.configure_setup())

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
        parent.rowconfigure(2, weight=0)
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
        wallet_panel.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 5))
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
        self.support_status_label=self.label(wallet_panel,'Ajuda pela ferramenta: aguardando configuração',bg=PANEL,fg=MUTED,font=(BODY_FONT,9))
        self.support_status_label.pack(anchor='w',pady=(3,0))
        payout = tk.Frame(parent, bg=PANEL, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER)
        payout.grid(row=4, column=0, sticky="ew", padx=4, pady=(0, 5))
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
        generated_track=tk.Frame(left,bg=BG,height=8)
        generated_track.pack(fill='x',pady=(3,4));generated_track.pack_propagate(False)
        self.payout_generated_bar = ttk.Progressbar(generated_track, style="Generated.Horizontal.TProgressbar", maximum=100, mode="determinate")
        self.payout_generated_bar.place(x=0,y=0,relwidth=1,height=8)
        self.payout_confirmed_text = self.label(left, "Confirmado para envio: N/D", bg=PANEL, anchor="w", font=(BODY_FONT, 9))
        self.payout_confirmed_text.pack(fill="x")
        confirmed_track=tk.Frame(left,bg=BG,height=8)
        confirmed_track.pack(fill='x',pady=(3,4));confirmed_track.pack_propagate(False)
        self.payout_confirmed_bar = ttk.Progressbar(confirmed_track, style="Confirmed.Horizontal.TProgressbar", maximum=100, mode="determinate")
        self.payout_confirmed_bar.place(x=0,y=0,relwidth=1,height=8)
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
        self.rig_cards=RigCards(parent,self.select_gpu,self.toggle_gpu,self.configure_gpu)
        self.rig_cards.grid(row=2,column=0,sticky='nsew',padx=2,pady=(0,4))
        self.overview_trends=tk.Frame(parent,bg=BG)
        self.overview_trends.grid(row=5,column=0,sticky='nsew',padx=2,pady=(0,5))
        self.overview_trends.columnconfigure((0,1),weight=1,uniform='overview-trends')
        self.overview_trends.rowconfigure(0,weight=1)
        parent.rowconfigure(5,weight=1)
        self.overview_charts={}
        for column,(key,title,color,unit,gap) in enumerate((('hash','Hashrate do rig · esta sessão',TEAL,'TH/s',180),('power','Potência do rig · esta sessão',BLUE,'W',10))):
            chart=Chart(self.overview_trends,title,color,unit,max_gap=gap)
            chart.grid(row=0,column=column,sticky='nsew',padx=3,pady=2)
            self.overview_charts[key]=chart


    def refresh_finance(self):
        def timestamp(value):
            value = safe_number(value)
            return dt.datetime.fromtimestamp(value).strftime("%d/%m %H:%M") if value else "N/D"
        miner = self.last_data.get("miner", {}) if self.last_data else {}
        fresh = bool(self.last_data and self.last_data.get("miner_running") and miner.get("hashrate_at") and time.time()-miner["hashrate_at"] < 180 and not miner.get("paused"))
        devices=((self.last_data or {}).get('rig') or {}).get('devices') or []
        portfolio=None
        if self.finance_state.get('schema_version')==2:
            portfolio=portfolio_model(self.finance_state,devices)
            wallets=list(portfolio.get('wallet_models',{}))
            previous=getattr(self,'_finance_wallets',[])
            index=self.finance_wallet_filter.current()
            selected=previous[index-1] if 0<index<=len(previous) else None
            self._finance_wallets=wallets
            self.finance_wallet_filter.configure(values=['Todas as carteiras']+[w[:12]+'…'+w[-8:] for w in wallets])
            self.finance_wallet_filter.current(wallets.index(selected)+1 if selected in wallets else 0)
            model=portfolio_model(self.finance_state,devices,selected_wallet=selected)
        else:
            model = financial_model(self.finance_state, miner.get("average_1m") if fresh else None)
        model={**model,'payout':payout_for_local_state(model,self.last_data or {})}
        self.last_finance_model = model
        wallet = str(model.get("wallet") or ('' if model.get('scope')=='portfolio' else WALLET) or "")
        if self.wallet_var.get() != wallet:
            self.wallet_var.set(wallet or f"{model.get('wallet_count',0)} carteiras · selecione em Rendimentos")
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
        self.payout_remaining.configure(text="Pendente: " + format_prl(payout.get("unconfirmed_prl")) + (" · " if self.winfo_height()<780 else "\n") + "Falta gerar: " + format_prl(payout.get("remaining_prl")) + " · " + format_brl(payout.get("remaining_brl")))
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
        if portfolio:today=(portfolio.get('currencies') or {}).get('day') or {}
        today_partial = (portfolio or model).get("periods",{}).get("day",{}).get("partial")
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
        support_note=' Ajuda opcional de 5% configurada já descontada da projeção.' if (model.get('support_contribution') or {}).get('enabled') else ''
        forecast_note=('Projeções: 24 h/dia e condições atuais.' if model.get('forecast') else
            'Projeções N/D: rig local parado; saldo e histórico continuam visíveis.' if devices and not any(d.get('miner_running') for d in devices) else
            'Projeções N/D: faltam dados atuais de hashrate, preço ou rede.')
        self.finance_explanation.configure(text=("Observado parcialmente desde "+timestamp(partial.get("since"))+". " if partial.get("partial") else "")+"Conversões à cotação atual; pagamento em PRL. "+forecast_note+support_note)
        from earnings_quality import quality
        source_quality=quality(self.finance_state)
        model['source_quality']=source_quality
        age=source_quality['oldest_age_seconds']
        self.finance_sources.configure(text=f"Kryptex / CoinGecko · {source_quality['status']} · dado mais antigo: {int(age)} s" if age is not None else 'Kryptex / CoinGecko · fontes indisponíveis')
        graph_wallets=[model['selected_wallet']] if model.get('scope')=='wallet' and model.get('selected_wallet') else list(model.get('wallet_models',{})) or None
        snapshots = portfolio_snapshots(self.finance_state,graph_wallets)
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
        devices=(data.get('rig') or {}).get('devices') or []
        if 'rig' in data and not devices:
            self.rig_cards.render([],None)
            self.gpu_ids=[];self.gpu_selection.configure(values=[]);self.gpu_selection.set('Nenhuma GPU NVIDIA detectada')
            self.selected_gpu_uuid=None
        if devices:
            selected=getattr(self,'selected_gpu_uuid',None)
            if selected not in {d['uuid'] for d in devices}:selected=devices[0]['uuid']
            self.selected_gpu_uuid=selected
            data=project_selected(data,selected)
            if hasattr(self,'collector'):self.collector.selected_uuid=selected
            if hasattr(self,'gpu_runtime'):
                self.gpu_runtime.select(selected);self.gpu_runtime.update(data)
            if hasattr(self,'finance_collector') and hasattr(self.finance_collector,'configure_wallets'):
                self.finance_collector.configure_wallets({d['wallet'] for d in devices})
            self.rig_cards.render(devices,selected)
            self.gpu_ids=[d['uuid'] for d in devices]
            self.gpu_selection.configure(values=[f"GPU {d['index']} · {d['name']} · {d['uuid'][-8:]} · {'ativa' if d['enabled'] else 'desativada'}" for d in devices])
            self.gpu_selection.current(self.gpu_ids.index(selected))
            chart_context=selected_gpu_context(data,selected,self.history.wallet)
            if getattr(self,'_chart_context',None)!=chart_context:
                self._chart_context=chart_context
                for chart in self.charts.values():chart.points.clear()
                if hasattr(self.history,'restore_gpu'):
                    restored=self.history.restore_gpu(selected,time.time()-900,chart_context[1])
                    for key,points in restored.get('points',{}).items():
                        if key in self.charts:
                            for stamp,value in points:self.charts[key].add(stamp,value)
        self.last_data = data
        support=data.get('support_contribution') or {}
        if support.get('ready') and support.get('enabled') and support.get('connected'):
            note='Ajuda pela ferramenta: 5% ativa · Janela atual: '+('autor' if support.get('active_role')=='support' else 'sua carteira')
        elif support.get('ready') and support.get('enabled'):
            note='Ajuda pela ferramenta: 5% habilitada · Aguardando conexão com a pool'
        elif support.get('enabled') or support.get('configured_enabled'):
            note='Ajuda pela ferramenta: 5% habilitada · Aplicável ao iniciar pelo novo minerador'
        else:
            note='Ajuda pela ferramenta: sem contribuição adicional · Altere em Configurações'
        self.support_status_label.configure(text=note)
        now = time.time()
        gpu, miner = data.get("gpu") or {}, data["miner"]
        stamp = miner.get("hashrate_at")
        age = now-stamp if stamp else None
        fresh = age is not None and -5 <= age <= 180 and data["miner_running"] is True and not miner.get("paused")
        status, color = "Aguardando mineração", MUTED
        if data["miner_running"] is False:
            status, color = ("Minerador indisponível" if data.get('miner_executable_available') is False else "Minerador parado"), ORANGE
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
        from mining_health import health
        mining_health=data.get('health') or health(miner,data['miner_running'],now)
        if fresh and not miner.get('paused'):
            status=mining_health['label']
            color=TEAL if mining_health['state']=='confirmed' else ORANGE
        if data['miner_running'] is False:
            restored=(data.get('restoration') or {}).get('state')
            if restored=='restored':status,color='Parada · padrões restaurados',TEAL
            elif restored in ('partial','failed'):status,color='Parada · restauração incompleta',ORANGE
            elif restored in ('pending','restoring'):status,color='Parando · restaurando padrões',ORANGE
        self.status.configure(text="●  " + status, fg=color)
        hashrate = miner.get("hashrate") if fresh else None
        self.card_values["hash"].configure(text=fmt(hashrate, " TH/s", 1))
        self.card_notes["hash"].configure(text=("Média 1 min: " + fmt(miner.get("average_1m"), " TH/s", 1)) if fresh else
            'Arquivo ausente ou bloqueado' if data.get('miner_executable_available') is False and data['miner_running'] is False else "Hashrate não atualizado")
        if fresh:
            accepted_at=mining_health.get('last_accepted_at')
            self.card_notes['hash'].configure(text=('Última aceita: '+dt.datetime.fromtimestamp(accepted_at).strftime('%H:%M:%S')) if accepted_at else 'Aguardando primeira share aceita')
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
        if devices:
            aggregate=(data['rig'].get('totals') or totals(devices))
            self.overview_values['hash'].configure(text=fmt(aggregate.get('hashrate_ths'),' TH/s',1))
            self.overview_notes['hash'].configure(text=f"Rig · {aggregate.get('mining_count',0)}/{len(devices)} minerando")
            self.overview_values['power'].configure(text=fmt(aggregate.get('power_w'),' W',1))
            self.overview_notes['power'].configure(text='Consumo parcial · sensores ausentes' if aggregate.get('power_partial') else 'Consumo de todas as GPUs detectadas')
            self.overview_values['temp'].configure(text=fmt(aggregate.get('max_temperature_c'),' °C'))
            self.overview_notes['temp'].configure(text='Maior temperatura do rig')
            # A new aggregate hash point requires a new underlying miner log
            # timestamp. Sensor polling must not multiply repeated log samples.
            signature=tuple((d['uuid'],(d.get('miner') or {}).get('hashrate_at'),bool(d.get('miner_running')),bool(d.get('enabled'))) for d in devices)
            if signature!=getattr(self,'_overview_hash_signature',None):
                self._overview_hash_signature=signature
                observed=[(d.get('miner') or {}).get('hashrate_at') for d in devices if d.get('enabled') and d.get('miner_running') and not (d.get('miner') or {}).get('paused')]
                observed=[stamp for stamp in observed if isinstance(stamp,(int,float)) and -5<=now-stamp<=180]
                if observed:self.overview_charts['hash'].add(max(observed),aggregate.get('hashrate_ths'))
            if aggregate.get('hashrate_ths') is None:self.overview_charts['hash'].add(now,None)
            self.overview_charts['power'].add(data.get('collected_at',now),aggregate.get('power_w'))
            for chart in self.overview_charts.values():chart.draw()
        ignored=(data.get('rig') or {}).get('ignored_devices') or []
        efficiency = hashrate/gpu["power.draw"] if hashrate and gpu.get("power.draw") else None
        thermal_text = 'Supervisor inativo · minerador parado' if data['miner_running'] is False else thermal_description(data.get("thermal_control"))
        self.overview_thermal.configure(text="Controle térmico: " + ('inativo · minerador parado' if data['miner_running'] is False else thermal_description(data.get("thermal_control"), compact=True)))
        if self.winfo_height()<780:
            contribution='Ajuda 5% ativa' if support.get('ready') and support.get('enabled') else 'Ajuda 5% configurada' if support.get('enabled') or support.get('configured_enabled') else 'Sem ajuda adicional'
            self.overview_thermal.configure(text=self.overview_thermal.cget('text')+' · '+contribution)
        if ignored:self.overview_thermal.configure(text=f"Limite: 6 GPUs · {len(ignored)} dispositivos adicionais fora do rig",fg=ORANGE)
        self.details.configure(text=f"Núcleo {fmt(gpu.get('clocks.gr'), ' MHz')} · Memória {fmt(gpu.get('clocks.mem'), ' MHz')} · VRAM {fmt(gpu.get('memory.used'))} / {fmt(gpu.get('memory.total'), ' MiB')} · Eficiência {fmt(efficiency, ' TH/s/W', 3)}\nDriver {gpu.get('driver_version', '—')} · Estado {gpu.get('pstate', '—')} · Uso GPU {fmt(gpu.get('utilization.gpu'), '%')} / memória {fmt(gpu.get('utilization.memory'), '%')} · Média 15 min {fmt(miner.get('average_15m') if fresh else None, ' TH/s', 1)}\nControle térmico: {thermal_text}", wraplength=max(700, self.winfo_width()-60))
        self.subtitle.configure(text=f"{len(devices)} NVIDIA · GPU {gpu.get('index',0)} selecionada · PRL" if devices else f"{gpu.get('name', 'GPU indisponível')} · PRL")
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
        if data.get('miner_executable_available') is False and data['miner_running'] is False:
            errors=['Minerador indisponível: arquivo ausente ou bloqueado. Abra Configurações > Segurança.']
        if self.history.last_error:
            errors.append("Histórico aguardando gravação: " + self.history.last_error)
        self.footer.configure(text=" | ".join(errors) if errors else f"Histórico permanente · gráficos mostram 15 min · atualização {dt.datetime.fromtimestamp(data['collected_at']).strftime('%H:%M:%S')}")
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {k: v for k, v in data.items() if k != "miner"}
        snapshot["miner"] = {k: v for k, v in miner.items() if k not in ("recent_logs", "rate_points")}
        snapshot["charts"] = {key: chart.sample_info() for key, chart in self.charts.items()}
        snapshot["monitor_status"] = status
        snapshot["app"] = {"name": APP_NAME, **APP_BUILD, "tray_registered": self.tray.registered_ok,
            "tray_error": self.tray.error, "panel_state": self.state(),
            "pid": os.getpid(),
            "summary_open": bool(self.summary_window and self.summary_window.winfo_exists())}
        snapshot["window"] = {"width": self.winfo_width(), "height": self.winfo_height()}
        snapshot["layout"] = {"tab_count": len(self.tabs.tabs()), "tab_names": [self.tabs.tab(index,"text") for index in range(len(self.tabs.tabs()))],
            "selected_tab": self.tabs.index(self.tabs.select()), "client_size": [self.winfo_width(),self.winfo_height()],
            "profile": getattr(self,"layout_profile",{}), "font_units": "pixels", "scrollbars": False,
            "work_area": list(work_area(self)), "window_state": self.state(),
            "dpi_awareness": DPI_MODE,
            "logo": {"variant_width": self.logo_native_width,"native_size": [self.brand_logo.width(),self.brand_logo.height()],
                     "file": f"logo-header-{self.logo_native_width}.png"}}
        if hasattr(self, "gpu_runtime"):
            snapshot["gpu_agent"] = self.gpu_runtime.snapshot()
        snapshot["history"] = {"database": str(self.history.path), "session": self.history.session,
            "counts": self.history.info, "last_commit": self.history.last_commit,
            "pending_jobs": self.history.jobs.qsize(), "error": self.history.last_error,
            "restored_points": self.restored_points, "retention": "permanent_no_automatic_deletion"}
        self.last_snapshot = snapshot
        try:
            # Atomic: the console and the GPU agent read this file every 2 s.
            atomic_json(STATE_DIR / "latest.json", snapshot, compact=True)
        except OSError:

            pass

    def poll(self):
        changed = False
        finance_changed = False
        request_signature = file_signature(STATE_DIR / "ui-request.json")
        if request_signature is not None and request_signature != getattr(self, "_ui_request_signature", None):
            try:
                request = json.loads((STATE_DIR / "ui-request.json").read_text(encoding="utf-8"))
                self._ui_request_signature = request_signature
                if request.get("at", 0) > self.last_ui_request and request.get("action") in ("open", "summary", "setup", "exit"):
                    self.last_ui_request = request["at"]
                    self.tray_actions.put(request["action"])
            except (OSError, ValueError):
                pass
        try:
            while True:
                action = self.tray_actions.get_nowait()
                if action == "open":
                    self.show_panel()
                elif action == "setup":
                    self.show_panel()
                    self.configure_setup()
                elif action == "summary":
                    self.show_summary()
                elif action == "console":
                    self.open_console()
                elif action == "exit":
                    self.close()
                elif action=='stop_all':
                    self.stop_mining(all_devices=True)
        except queue.Empty:
            pass
        try:
            restored = self.history_restore.get_nowait()
            if self.last_data is None:
                self.history_restore.put(restored)
                raise queue.Empty
            devices=((self.last_data or {}).get('rig') or {}).get('devices') or []
            selected=getattr(self,'selected_gpu_uuid',None)
            context=selected_gpu_context(self.last_data,selected,self.history.wallet)
            if devices:
                if context is not None and context==getattr(self,'_chart_context',None) and hasattr(self.history,'restore_gpu'):
                    restored_points=self.history.restore_gpu(selected,time.time()-900,context[1])
                    restored['points']=restored_points.get('points',{})
                else:restored['points']={}
            for key, points in restored.get("points", {}).items():
                self.restored_points[key] = len(points)
                combined = dict(points)
                combined.update(dict(self.charts[key].points))
                self.charts[key].points.clear()
                for stamp, value in sorted(combined.items()):
                    self.charts[key].add(stamp, value)
                self.charts[key].draw()
            preferences = restored.get("preferences", {})
            if preferences.get('selected_gpu_uuid'):self.selected_gpu_uuid=preferences['selected_gpu_uuid']
            self.restore_window_preferences(preferences)
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
            changed = finance_changed = True
        except queue.Empty:
            pass
        if changed:
            # Pool data changes every 60 s; telemetry alone refreshes earnings at most every 10 s.
            if finance_changed or time.monotonic()-getattr(self, "_finance_refreshed_at", 0) >= 10:
                self.refresh_finance()
                self._finance_refreshed_at = time.monotonic()
            self.refresh_summary()
        for chart in self.all_charts():
            chart.redraw_if_dirty()
        visible = self.state() not in ("withdrawn", "iconic")
        self.after(250 if visible else 1000, self.poll)

    def all_charts(self):
        charts = [*getattr(self, "charts", {}).values(), *getattr(self, "overview_charts", {}).values()]
        if getattr(self, "money_chart", None) is not None:
            charts.append(self.money_chart)
        return charts

    def close(self):
        if getattr(self,'update_install_busy',False):
            from tkinter import messagebox
            messagebox.showinfo('Atualização em andamento','Aguarde a conclusão da instalação antes de sair. A mineração continua independente.',parent=self)
            return
        if getattr(self, "closing", False):
            return
        self.closing = True
        self.save_preferences()
        self.collector.stop.set()
        self.finance_collector.stop.set()
        if hasattr(self, "gpu_runtime"):
            self.gpu_runtime.stop.set()
        self.title("HashPilot · salvando histórico…")
        self.close_deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
        self.close_thread = threading.Thread(target=self.shutdown_background, daemon=True, name="shutdown")
        self.close_thread.start()
        self.after(100, self.finish_close)

    def shutdown_background(self):
        """Ordem: bandeja, última amostra do coletor e só então o histórico (sem escrita após o fechamento)."""
        self.tray.stop()
        collector = getattr(self, "collector_thread", None)
        if collector is not None:
            collector.join(timeout=5)
        self.history.close()

    def finish_close(self):
        busy = self.history.worker.is_alive() or (hasattr(self,"gpu_runtime") and self.gpu_runtime.thread.is_alive())
        if not busy:
            self.destroy()
            return
        if time.monotonic() >= getattr(self, "close_deadline", float("inf")) and not getattr(self, "close_withdrawn", False):
            # Prazo esgotado: a janela some, mas gravações pendentes do histórico nunca são descartadas.
            LOG.warning("Gravações pendentes após %s s; janela oculta até concluir", CLOSE_TIMEOUT_SECONDS)
            self.close_withdrawn = True
            self.withdraw()
        self.after(100, self.finish_close)


def console_text(snapshot, finance):
    gpu, miner = snapshot.get("gpu") or {}, snapshot.get("miner") or {}
    age = time.time()-snapshot.get("collected_at", 0)
    active = -5<=age<=8
    money = lambda value: "N/D" if value is None else "R$ " + f"{value:.2f}".replace(".", ",")
    forecast = finance.get("forecast") or {}
    today = finance.get("periods", {}).get("day", {})
    payout = finance.get("payout") or {}
    wallet = str(finance.get("wallet") or (f"{finance.get('wallet_count',0)} carteiras; selecione uma em Rendimentos" if finance.get('scope')=='portfolio' else WALLET) or "não configurada")
    history = snapshot.get("history", {})
    rig=snapshot.get('rig') or {}
    aggregate=rig.get('totals') or {}
    return "\n".join([
        "HASHPILOT "+str((snapshot.get('app') or {}).get('version') or 'versão não informada')+"  |  TECH FERNANDES",
        "Resumo local do monitor. Ctrl+C fecha somente este console.",
        "="*82,
        (snapshot.get("monitor_status", "Aguardando monitor") if active else "MONITOR SEM ATUALIZACAO - ultimo registro abaixo"),
        f"Rig total: {fmt(aggregate.get('hashrate_ths'),' TH/s',2)} | {fmt(aggregate.get('power_w'),' W',1)} | {aggregate.get('mining_count',0)} minerando. GPU selecionada: {gpu.get('index',0)} {snapshot.get('selected_gpu_uuid','legado GPU0')}",
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
    parser.add_argument("--setup", action="store_true", help="Configura carteira e instalação opcional do minerador")
    parser.add_argument("--restart", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.snapshot:
        print(json.dumps(Collector().sample(), ensure_ascii=False, indent=2))
        return
    if args.console or args.console_once:
        run_console(once=args.console_once)
        return
    if not args.smoke_test and not args.restart and handoff_to_running_monitor("setup" if args.setup else "summary" if args.summary else "open"):
        return
    if not args.smoke_test:
        from onboarding import run_setup, read_settings
        if args.setup or not read_settings().get("onboarding_complete"):
            enable_native_dpi_awareness()
            if run_setup():
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--restart"], cwd=Path(__file__).resolve().parent)
                return
            if not read_settings().get("onboarding_complete"):
                return
    # Só o processo do painel (instância única) escreve o diagnóstico: --console/--snapshot e
    # processos de passagem não disputam a rotação do mesmo arquivo no Windows.
    configure_logging(LOG_DIR)
    app = Monitor()
    if args.summary:
        app.after(800, lambda: (app.show_summary(), app.hide_to_tray()))
    if args.smoke_test:
        app.after(7000, app.close)
    app.mainloop()
    if getattr(app, "restart_requested", False):
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--restart"], cwd=Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
