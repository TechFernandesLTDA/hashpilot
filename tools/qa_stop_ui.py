"""Parada: layout visível com 1/2/6 GPUs e estados de sucesso/erro, sem hardware."""
import gc
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import stop_ui as ui
from test_responsive_ui import inspect_widgets
root=tk.Tk();root.geometry('1x1+0+0');reports=[]
with tempfile.TemporaryDirectory() as temporary:
    for screen in ((1280,720),(1920,1080),(3840,2160)):
        for count in (1,2,6):
            for state in ('restored','partial'):
                devices=[{'uuid':f'GPU-qa-{i}','index':i,'name':'NVIDIA GeForce RTX 3070'} for i in range(count)]
                result={'requested_at':0,'requires_uac':False}
                value={'at':1,'state':state,'failed_components':['fans']}
                with patch.object(ui,'request_stop',return_value=result),patch.object(ui,'read',return_value=value),patch.object(ui,'STATE_DIR',Path(temporary)),patch.object(ui,'LOG_DIR',Path(temporary)),patch.object(tk.Misc,'winfo_screenwidth',return_value=screen[0]),patch.object(tk.Misc,'winfo_screenheight',return_value=screen[1]):
                    window=ui.show_stop(root,devices);window.update();gc.collect()
                    assert len(window.gpu_choices)==count and all(var.get() for var in window.gpu_choices.values())
                    def descendants(widget):
                        for child in widget.winfo_children():yield child;yield from descendants(child)
                    next(w for w in descendants(window) if isinstance(w,tk.Button) and w.cget('text')=='Parar TODAS e restaurar').invoke()
                    root.after(550,root.quit);root.mainloop();window.update()
                    assert window.winfo_ismapped()
                    reports.append({'screen':screen,'gpus':count,'state':state,'errors':inspect_widgets(window)})
                    window.destroy()
root.destroy();print(json.dumps(reports))
raise SystemExit(any(r['errors'] for r in reports))
