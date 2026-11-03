"""QA da janela única de Configurações: todas as seções, larga e estreita, sem salvar nada real.

Uso: mise exec -- python tools/qa_settings.py [--screenshots DIRETORIO]
Abre janelas reais; dados de estado ficam num diretório temporário.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

temporary = tempfile.TemporaryDirectory()
os.environ["LOCALAPPDATA"] = temporary.name  # config.STATE_DIR é lido na importação
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk
import settings_ui
from test_responsive_ui import inspect_widgets

shots = Path(sys.argv[sys.argv.index("--screenshots")+1]) if "--screenshots" in sys.argv else None
root = tk.Tk()
root.geometry("1x1+0+0")
root.last_data = {"rig": {"devices": []}}
reports = []
for width, height in ((1280, 760), (760, 620)):
    window = settings_ui.SettingsWindow(root)
    window.geometry(f"{width}x{height}+20+20")
    root.update()
    for name in settings_ui.SECTIONS:
        window.show(name)
        for _ in range(3):
            root.update()
            time.sleep(.05)
        slot = window.slots[name]
        failed = [child.cget("text") for child in slot.winfo_children()
                  if child.winfo_class() == "Label" and str(child.cget("text")).startswith("Não foi possível abrir")]
        reports.append({"size": [width, height], "section": name, "narrow": window.narrow,
                        "sidebar": bool(window.sidebar.winfo_ismapped()), "build_errors": failed,
                        "layout_errors": inspect_widgets(slot)})
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            target = shots / f"settings-{width}-{settings_ui.SECTIONS.index(name)}.png"
            # PrintWindow needs the Tk message loop running, so keep pumping events while it captures.
            capture = subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                        str(Path(__file__).with_name("capture_window.ps1")), "-ClientHandle", str(window.winfo_id()),
                                        "-OutputPath", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic()+30
            while capture.poll() is None and time.monotonic() < deadline:
                root.update()
                time.sleep(.05)

    window.close()
root.destroy()
temporary.cleanup()
print(json.dumps(reports, ensure_ascii=False, indent=1))
problems = [row for row in reports if row["build_errors"] or row["layout_errors"]]
narrow_ok = all(row["narrow"] == (row["size"][0] < settings_ui.NARROW) and row["sidebar"] != row["narrow"] for row in reports)
raise SystemExit(bool(problems) or not narrow_ok)
