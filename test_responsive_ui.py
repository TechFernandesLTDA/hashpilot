"""QA opt-in da interface, sem coletores, GPU, tray ou gravação em AppData.

Execute `python test_responsive_ui.py --run` no Windows com desktop aberto.
Os testes comuns não abrem janelas: a inspeção visual/geometria é opt-in.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import copy
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from types import SimpleNamespace
from unittest.mock import patch


def work_area(root):
    if os.name == "nt":
        rectangle = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rectangle), 0):
            return rectangle.right-rectangle.left, rectangle.bottom-rectangle.top
    return root.winfo_screenwidth(), root.winfo_screenheight()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def read_optional(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build_harness(monitor, temporary, scaling=None):
    root = monitor.Monitor.__new__(monitor.Monitor)
    tk.Tk.__init__(root)
    if scaling is not None:
        root.tk.call("tk", "scaling", scaling)
    root.configure(bg=monitor.BG)
    root.app_icon = tk.PhotoImage(file=monitor.ASSET_DIR/"icon-256.png")
    root.card_values, root.card_notes = {}, {}
    root.log_contents = None
    root.summary_window = root.console_window = None
    root.summary_values = {}
    root.restored_points = {}
    root.history_restore = queue.Queue()
    root.ui_restored = False
    root.preference_timer = None
    root.tray_actions = queue.Queue()
    root.tray = SimpleNamespace(registered_ok=False, error=None)
    fixture = read_optional(Path(tempfile.gettempdir())/"HashPilot-layout-finance-qa.json")
    latest = fixture.get("snapshot") or read_optional(monitor.STATE_DIR/"latest.json")
    root.history = SimpleNamespace(path=temporary/"not-opened.sqlite3", session="isolated-layout-qa",
        info=latest.get("history", {}).get("counts", {}), last_error=None, last_commit=None,
        jobs=SimpleNamespace(qsize=lambda: 0), preferences=lambda data: None)
    root.finance_state = read_optional(monitor.STATE_DIR/"finance-cache.json")
    root.qa_finance = fixture.get("finance")
    root.last_data = None
    root.last_finance_model = {}
    root.build()
    root.title("TESTE de layout HashPilot — não altera mineração")
    return root, latest


def inspect_widgets(root):
    errors = []
    client_w, client_h = root.winfo_width(), root.winfo_height()
    origin_x, origin_y = root.winfo_rootx(), root.winfo_rooty()
    widgets = list(descendants(root))
    for widget in widgets:
        if widget.winfo_class() in ("Scrollbar", "TScrollbar"):
            errors.append({"kind": "scrollbar", "widget": str(widget)})
        if not widget.winfo_ismapped():
            continue
        x, y = widget.winfo_rootx()-origin_x, widget.winfo_rooty()-origin_y
        w, h = widget.winfo_width(), widget.winfo_height()
        if x < -2 or y < -2 or x+w > client_w+2 or y+h > client_h+2:
            errors.append({"kind": "bounds", "widget": str(widget), "class": widget.winfo_class(),
                           "rect": [x, y, w, h], "client": [client_w, client_h]})
        if isinstance(widget, tk.Canvas):
            for item in widget.find_all():
                if widget.type(item) != "text":
                    continue
                bbox = widget.bbox(item)
                if bbox and (bbox[0] < -2 or bbox[1] < -2 or bbox[2] > w+2 or bbox[3] > h+2):
                    errors.append({"kind": "canvas_text_clipped", "widget": str(widget),
                        "text": widget.itemcget(item, "text")[:90], "bbox": list(bbox), "size": [w, h]})
            age_items = widget.find_withtag("sample-age")
            if age_items:
                age_bbox = widget.bbox(age_items[0])
                for item in widget.find_all():
                    if widget.type(item) != "text" or item in age_items:
                        continue
                    bbox = widget.bbox(item)
                    if bbox and age_bbox and max(bbox[0],age_bbox[0]) < min(bbox[2],age_bbox[2]) and max(bbox[1],age_bbox[1]) < min(bbox[3],age_bbox[3]):
                        errors.append({"kind":"chart_title_age_overlap","widget":str(widget),"text":widget.itemcget(item,"text")})
        if widget.winfo_class() == "Treeview":
            from tkinter import ttk
            style = ttk.Style(root)
            style_name = str(widget.cget("style")) or "Treeview"
            font = style.lookup(style_name, "font")
            rowheight = int(style.lookup(style_name, "rowheight") or 20)
            font_height = int(root.tk.call("font", "metrics", font, "-linespace"))
            if font_height > rowheight:
                errors.append({"kind": "table_row_font_clipped", "widget": str(widget),
                               "font_height": font_height, "rowheight": rowheight})
            for row in widget.get_children():
                if not widget.bbox(row):
                    errors.append({"kind": "table_row_not_visible", "widget": str(widget), "row": row})
                for column in ["#0"]+list(widget.cget("columns")):
                    value = str(widget.item(row, "text")) if column == "#0" else str(widget.set(row, column))
                    cell_width = int(widget.column(column, "width"))
                    measured = int(root.tk.call("font", "measure", font, value))+8
                    if measured > cell_width:
                        errors.append({"kind": "table_cell_clipped", "widget": str(widget), "row": row,
                                       "column": column, "text": value, "measured": measured, "available": cell_width})
        if widget.winfo_class() in ("Entry", "TEntry"):
            try:
                value = str(widget.get())
                font = widget.cget("font")
                measured = int(root.tk.call("font", "measure", font, value))+10
                if value and measured > w:
                    errors.append({"kind": "entry_text_requires_horizontal_scroll", "widget": str(widget),
                                   "characters": len(value), "measured": measured, "available": w})
            except tk.TclError:
                pass
        if widget.winfo_class() in ("Label", "TLabel", "Button", "TButton"):
            try:
                text = str(widget.cget("text"))
                wrap = float(str(widget.cget("wraplength"))) if "wraplength" in widget.keys() else 0
                if text and wrap and widget.winfo_reqheight() > h+2:
                    errors.append({"kind":"wrapped_text_height_clipped","widget":str(widget),"text":text[:90],
                                   "required":widget.winfo_reqheight(),"available":h})
                if text and not wrap:
                    font = widget.cget("font")
                    text_width = max(int(root.tk.call("font", "measure", font, line)) for line in text.splitlines())
                    if text_width > w+2:
                        errors.append({"kind": "text_clipped", "widget": str(widget), "text": text[:90],
                                       "measured": text_width, "available": w})
                    line_height = int(root.tk.call("font", "metrics", font, "-linespace"))*max(1, len(text.splitlines()))
                    if line_height > h+2:
                        errors.append({"kind": "text_height_clipped", "widget": str(widget), "text": text[:90],
                                       "measured": line_height, "available": h})
            except (tk.TclError, ValueError):
                pass
    return errors


def inspect_required_widgets(root, index):
    names = (
        ["wallet_entry", "payout_eta", "payout_generated_bar", "payout_confirmed_bar", "overview_thermal"],
        ["details", "logs", "footer"],
        ["finance_table", "quote", "quote_note", "finance_pay_summary", "finance_explanation", "finance_sources"],
    )[index]
    result = []
    for name in names:
        widget = getattr(root, name, None)
        if widget is None or not widget.winfo_ismapped():
            result.append({"kind": "essential_not_visible", "attribute": name})
    return result


def run_report(sizes=None, virtual_4k=False, scaling=None, simulated_work_area=None):
    import monitor
    reports = []
    simulated = patch.object(monitor, "work_area", return_value=(0,0,*simulated_work_area)) if simulated_work_area else nullcontext()
    with tempfile.TemporaryDirectory() as directory, simulated:
        temporary = Path(directory)
        root, latest = build_harness(monitor, temporary, scaling)
        available = simulated_work_area or work_area(root)
        # Writes by apply()/refresh_finance() go only to the temporary QA dir.
        with patch.object(monitor, "STATE_DIR", temporary):
            if latest.get("miner"):
                root.apply(copy.deepcopy(latest))
            if root.qa_finance:
                with patch.object(monitor, "financial_model", return_value=root.qa_finance):
                    root.refresh_finance()
            else:
                root.refresh_finance()
            for width, height in (sizes or [(1280, 720), (1366, 768), (1920, 1080), (3840, 2160)]):
                if virtual_4k and width >= 3840:
                    root.maxsize(9000, 9000)
                root.geometry(f"{width}x{height}+0+0")
                root.update()
                root.update_idletasks()
                actual = root.winfo_width(), root.winfo_height()
                for index in range(len(root.tabs.tabs())):
                    root.tabs.select(index)
                    root.update()
                    root.update_idletasks()
                    reports.append({"requested": [width, height], "actual_client": list(actual),
                        "work_area": list(available), "physically_visible": actual[0] <= available[0] and actual[1] <= available[1],
                        "virtual_4k": bool(virtual_4k and width >= 3840), "tk_scaling": float(root.tk.call("tk", "scaling")),
                        "simulated_work_area": bool(simulated_work_area),
                        "tab": root.tabs.tab(index, "text"), "errors": inspect_widgets(root)+inspect_required_widgets(root,index)})
        root.destroy()
    return reports


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--virtual-4k", action="store_true", help="Teste de geometria fora da tela, nao evidencia fisica 4K")
    parser.add_argument("--scaling", type=float)
    parser.add_argument("--work-area", help="Simula WorkArea WIDTHxHEIGHT no maxsize do app")
    arguments = parser.parse_args()
    if not arguments.run:
        parser.print_help()
        raise SystemExit(0)
    simulated = tuple(map(int, arguments.work_area.lower().split("x"))) if arguments.work_area else None
    report = run_report(virtual_4k=arguments.virtual_4k, scaling=arguments.scaling, simulated_work_area=simulated)
    result = {"reports": report, "overflow_count": sum(len(row["errors"]) for row in report)}
    if arguments.output:
        arguments.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(bool(result["overflow_count"]))
