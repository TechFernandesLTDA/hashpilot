"""Mapped-window layout QA, with no downloads, miner launches or real settings."""
import json
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import miner_update_ui as ui
from test_responsive_ui import inspect_widgets

root=tk.Tk();root.geometry('1x1+0+0');reports=[]
with tempfile.TemporaryDirectory() as temporary:
    for width,height in ((1280,720),(1920,1080),(3840,2160)):
        for scenario,newer,allowed in (('new',True,True),('current',False,True),('unreviewed',True,False)):
            result={'version':'3.8.1','current':'3.8.0','newer':newer,'installable':allowed}
            with patch.object(ui,'STATE_DIR',Path(temporary)),patch.object(ui.update,'latest',return_value=result),patch.object(ui.update,'selected_version',return_value='3.8.0'),patch.object(tk.Misc,'winfo_screenwidth',return_value=width),patch.object(tk.Misc,'winfo_screenheight',return_value=height):
                window=ui.show_miner_update(root)
                def inspect():
                    root.update_idletasks()
                    reports.append({'screen':[width,height],'scenario':scenario,'errors':inspect_widgets(window)})
                    window.destroy();root.quit()
                root.after(650,inspect);root.mainloop()
root.destroy();print(json.dumps(reports))
raise SystemExit(any(row['errors'] for row in reports))
