"""QA da central em resoluções HD a 4K, sem tocar em hardware nem dados reais."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import gc
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import operations_ui as ui
from test_responsive_ui import inspect_widgets

root=tk.Tk();root.geometry('1x1+0+0');reports=[]
with tempfile.TemporaryDirectory() as temporary:
    for width,height in ((1280,720),(1920,1080),(3840,2160)):
        registry={'devices':{'GPU-qa':{'index':0,'name':'NVIDIA GeForce RTX 3070','log_dir':temporary}},'ready':True,'sampled_at':__import__('time').time()}
        def fixture(path):return registry if Path(path).name=='rig-manager.json' else {}
        with patch.object(ui,'STATE_DIR',Path(temporary)),patch.object(ui,'LOG_DIR',Path(temporary)),patch.object(ui,'read_json',side_effect=fixture):
            window=ui.show_operations(root)
            gc.collect()
            assert window.selected_gpu_variable.get(),'Seleção da GPU precisa sobreviver após criar a janela'
            window.geometry(f'{min(980,width-40)}x{min(620,height-70)}+0+0')
            notebook=next(child for child in window.winfo_children() if isinstance(child,ui.ttk.Notebook))
            for tab in notebook.tabs():
                notebook.select(tab);root.update()
                assert window.winfo_ismapped(),'Janela de QA precisa estar visível'
                reports.append({'screen':[width,height],'tab':notebook.tab(tab,'text'),'errors':inspect_widgets(window)})
            window.destroy()
root.destroy()
print(json.dumps(reports,ensure_ascii=True))
raise SystemExit(any(row['errors'] for row in reports))
