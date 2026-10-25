"""QA opt-in do assistente, sem salvar configuração ou iniciar mineração/IA."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import monitor
import onboarding
from test_responsive_ui import inspect_widgets

monitor.enable_native_dpi_awareness()
root=tk.Tk()
root.withdraw()
reports=[]
def inspect():
    window=next(child for child in root.winfo_children() if isinstance(child,tk.Toplevel))
    window.update_idletasks()
    reports.append({'client':[window.winfo_width(),window.winfo_height()], 'errors':inspect_widgets(window)})
    window.destroy()
for settings in ({'wallet':onboarding.DEFAULT_WALLET},{'wallet':'prl1'+'q'*60},{'wallet':'prl1'+'q'*60,'support_enabled':False}):
    for screen in [(1280,720),(1920,1080),(3840,2160)]:
        with patch.object(tk.Misc,'winfo_screenwidth',return_value=screen[0]),patch.object(tk.Misc,'winfo_screenheight',return_value=screen[1]),patch.object(onboarding,'read_settings',return_value=settings):
            root.after(250,inspect)
            assert onboarding.run_setup(root) is False
root.destroy()
output=Path(tempfile.gettempdir())/'HashPilot-onboarding-qa.json'
output.write_text(json.dumps(reports,indent=2),encoding='utf-8')
print(json.dumps(reports))
raise SystemExit(any(item['errors'] for item in reports))
