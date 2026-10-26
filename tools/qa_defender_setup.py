"""QA isolada da explicação/consentimento, com elevação e links simulados."""
import json
from pathlib import Path
import sys
import tkinter as tk
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import defender_setup
from test_responsive_ui import inspect_widgets

root=tk.Tk();root.geometry('1x1+0+0');root.update()
reports=[]
def inspect():
    window=next(child for child in root.winfo_children() if isinstance(child,tk.Toplevel))
    window.update_idletasks()
    assert window.winfo_width()>600 and window.winfo_height()>400
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    widgets=list(descendants(window))
    add=next(w for w in widgets if isinstance(w,tk.Button) and w.cget('text')=='Solicitar exceção (UAC)')
    remove=next(w for w in widgets if isinstance(w,tk.Button) and w.cget('text')=='Remover exceção (UAC)')
    consent=next(w for w in widgets if isinstance(w,tk.Checkbutton))
    with patch.object(defender_setup,'launch_exception_request') as launch:
        assert str(add.cget('state'))=='disabled'
        add.invoke();launch.assert_not_called()
        consent.invoke();assert str(add.cget('state'))=='normal'
        add.invoke();launch.assert_called_once_with('Add')
        consent.invoke();assert str(add.cget('state'))=='disabled'
        launch.reset_mock();remove.invoke();launch.assert_called_once_with('Remove')
    reports.append({'client':[window.winfo_width(),window.winfo_height()],'errors':inspect_widgets(window),'consent_checks_passed':True})
    window.destroy()
for width,height in [(1280,720),(1920,1080),(3840,2160)]:
    with patch.object(tk.Misc,'winfo_screenwidth',return_value=width),patch.object(tk.Misc,'winfo_screenheight',return_value=height):
        root.after(250,inspect)
        defender_setup.show_defender_setup(root)
root.destroy()
print(json.dumps(reports))
raise SystemExit(any(report['errors'] for report in reports))
