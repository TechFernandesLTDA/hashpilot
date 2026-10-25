"""QA opt-in de maximização NATIVA em janela isolada, sem coletores/mineração."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch


def settle(root):
    deadline = time.monotonic()+.25
    while time.monotonic()<deadline:
        root.update()
        time.sleep(.01)


def native_frame(root):
    user = ctypes.WinDLL("user32",use_last_error=True)
    user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]
    user.GetAncestor.restype=wintypes.HWND
    hwnd=user.GetAncestor(root.winfo_id(),2)
    rectangle=wintypes.RECT()
    dwm=ctypes.WinDLL("dwmapi",use_last_error=True)
    dwm.DwmGetWindowAttribute.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
    if dwm.DwmGetWindowAttribute(hwnd,9,ctypes.byref(rectangle),ctypes.sizeof(rectangle)):
        raise RuntimeError("Não foi possível ler os limites visíveis da janela de teste")
    return [rectangle.left,rectangle.top,rectangle.right,rectangle.bottom]


def physical_work_area(root, logical_area):
    user=ctypes.WinDLL("user32",use_last_error=True)
    user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT]
    user.GetAncestor.restype=wintypes.HWND
    user.LogicalToPhysicalPointForPerMonitorDPI.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
    hwnd=user.GetAncestor(root.winfo_id(),2)
    start=wintypes.POINT(*logical_area[:2])
    end=wintypes.POINT(*logical_area[2:])
    assert user.LogicalToPhysicalPointForPerMonitorDPI(hwnd,ctypes.byref(start))
    assert user.LogicalToPhysicalPointForPerMonitorDPI(hwnd,ctypes.byref(end))
    return [start.x,start.y,end.x,end.y]


def run_native_report(native_dpi=False):
    if os.name!="nt":
        return {"skipped":"Native Windows maximize requires Windows"}
    import monitor
    if native_dpi:
        monitor.enable_native_dpi_awareness()
    from test_responsive_ui import build_harness, inspect_widgets
    with tempfile.TemporaryDirectory() as directory:
        temporary=Path(directory)
        root,_=build_harness(monitor,temporary)
        saved=[]
        root.history.preferences=saved.append
        try:
            root.restore_normal_geometry("1200x740+80+60")
            settle(root)
            normal_geometry=root.geometry()
            normal={"state":root.state(),"client":[root.winfo_width(),root.winfo_height()],"visible_frame":native_frame(root),"geometry":normal_geometry,"logo":[root.brand_logo.width(),root.brand_logo.height()]}
            root.state("zoomed")
            settle(root)
            area=list(monitor.work_area(root))
            frame=native_frame(root)
            physical_area=physical_work_area(root,area)
            assert all(abs(a-b)<=2 for a,b in zip(frame,physical_area)),(frame,physical_area)
            assert abs(root.winfo_width()-(area[2]-area[0]))<=2
            root.save_preferences()
            preferences=saved[-1]
            assert preferences["window_state"]=="zoomed"
            assert preferences["geometry"]==normal_geometry
            maximized={"state":root.state(),"client_logical":[root.winfo_width(),root.winfo_height()],"visible_frame_physical":frame,"work_area_logical":area,"work_area_physical":physical_area,"logo":[root.brand_logo.width(),root.brand_logo.height()]}
            root.tray.registered_ok=True
            root.hide_to_tray()
            settle(root)
            assert root.state()=="withdrawn"
            root.show_panel()
            settle(root)
            assert root.state()=="zoomed"
            root.state("normal")
            settle(root)
            assert root.geometry()==normal_geometry,(root.geometry(),normal_geometry)
            root.restore_window_preferences(preferences)
            settle(root)
            assert root.state()=="zoomed"
            for width,image in root.logo_images.items():
                assert image.width()==width
            return {"normal":normal,"maximized":maximized,"preferences":preferences,"tray_roundtrip":"zoomed","normal_roundtrip":"same geometry","restart_preferences":"zoomed","native_logo_widths":list(root.logo_images),"dpi_awareness":monitor.DPI_MODE}
        finally:
            root.destroy()


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--native-dpi",action="store_true")
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if not args.run:
        parser.print_help()
        raise SystemExit(0)
    result=run_native_report(args.native_dpi)
    if args.output:
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=True,indent=2))
