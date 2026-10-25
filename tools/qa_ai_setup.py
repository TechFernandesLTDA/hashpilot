"""UI geometry QA without real keys, cloud calls, installations or GPU writes."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import monitor
import ai_setup
from ai_setup import show_ai_setup,save_ai,MODELS
from test_responsive_ui import inspect_widgets

monitor.enable_native_dpi_awareness()
root=tk.Tk();root.geometry('10x10+0+0');root.update()
reports=[]
original=None
def inspect():
    window=next(c for c in root.winfo_children() if isinstance(c,tk.Toplevel))
    window.update()
    reports.append({'client':[window.winfo_width(),window.winfo_height()],'errors':inspect_widgets(window),
                    'test_did_not_save':path.read_bytes()==original})
    window.destroy()
with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'settings.json'
    for provider in ('openrouter','opencode','ollama'):
        save_ai(path,False,provider,MODELS[provider][0],False)
        original=path.read_bytes()
        for width,height in [(1280,720),(1920,1080),(3840,2160)]:
            with patch.object(tk.Misc,'winfo_screenwidth',return_value=width),patch.object(tk.Misc,'winfo_screenheight',return_value=height):
                def exercise_test():
                    window=next(c for c in root.winfo_children() if isinstance(c,tk.Toplevel))
                    if provider=='openrouter':
                        for widget in window.winfo_children():
                            if isinstance(widget,tk.Checkbutton):widget.invoke()
                        for frame in window.winfo_children():
                            for widget in frame.winfo_children():
                                if isinstance(widget,tk.Button) and widget.cget('text')=='Testar conexão':widget.invoke()
                    root.after(250,inspect)
                with patch.object(ai_setup,'RoutedAdvisor') as advisor:
                    advisor.return_value.advise.return_value={'action':'hold','reason':'Teste simulado'}
                    root.after(250,exercise_test);show_ai_setup(root,path)
root.destroy()
print(json.dumps(reports))
raise SystemExit(any(report['errors'] or not report['test_did_not_save'] for report in reports))
