"""Ícone nativo na bandeja do Windows, sem dependências e sem chamadas Tk."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import queue
import threading


class WindowsTray:
    """Callbacks publicam open/summary/console/exit na fila da interface.

    start() cria uma thread com message pump; ready indica conclusão da tentativa,
    registered é um Event que só fica marcado após NIM_ADD bem-sucedido.
    stop() é seguro e remove o ícone antes de terminar a thread.
    """

    def __init__(self, icon_path: Path, title: str, actions: queue.Queue):
        self.icon_path, self.title, self.actions = Path(icon_path), title, actions
        self.registered = threading.Event()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.error = None
        self.hwnd = None
        self.thread = None
        self._stop_requested = threading.Event()
        self._wndproc = None
        self._icon = None

    @property
    def registered_ok(self):
        return self.registered.is_set()

    def start(self):
        if self.thread and self.thread.is_alive():
            return self
        self.thread = threading.Thread(target=self._run, name="hashpilot-windows-tray", daemon=True)
        self.thread.start()
        return self

    def stop(self, timeout=3):
        self._stop_requested.set()
        hwnd = self.hwnd
        if hwnd:
            ctypes.WinDLL("user32", use_last_error=True).PostMessageW(wintypes.HWND(hwnd), 0x0010, 0, 0)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def _run(self):
        if not hasattr(ctypes, "WinDLL"):
            self.error = "Bandeja disponível somente no Windows."
            self.ready.set()
            self.stopped.set()
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        LRESULT, WPARAM, LPARAM = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte*8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                        ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                        ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE),
                        ("szTip", wintypes.WCHAR*128), ("dwState", wintypes.DWORD),
                        ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR*256),
                        ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR*64),
                        ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                        ("hBalloonIcon", wintypes.HANDLE)]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        user32.UnregisterClassW.restype = wintypes.BOOL
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
            wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.DestroyIcon.argtypes = [wintypes.HANDLE]
        user32.DestroyIcon.restype = wintypes.BOOL
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterWindowMessageW.restype = wintypes.UINT
        user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.TranslateMessage.restype = wintypes.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.restype = LRESULT
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        user32.SetMenuDefaultItem.restype = wintypes.BOOL
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.TrackPopupMenu.restype = wintypes.UINT
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        callback_message = 0x8000+37
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.uID = 1
        nid.uFlags = 0x1 | 0x2 | 0x4  # MESSAGE | ICON | TIP
        nid.uCallbackMessage = callback_message
        nid.szTip = self.title[:127]
        class_name = f"HashPilotTray_{id(self)}"
        hinstance = kernel32.GetModuleHandleW(None)
        class_registered = False

        def add_icon():
            if shell32.Shell_NotifyIconW(0, ctypes.byref(nid)):
                self.registered.set()
                self.error = None
            else:
                self.registered.clear()
                self.error = "Não foi possível registrar o ícone na bandeja do Windows."

        def show_menu(hwnd):
            menu = user32.CreatePopupMenu()
            if not menu:
                return
            try:
                for identifier, label in ((1, "Abrir painel"), (2, "Resumo"), (3, "Console resumido")):
                    user32.AppendMenuW(menu, 0, identifier, label)
                user32.AppendMenuW(menu,0,5,'Parar toda mineração e restaurar padrões')
                user32.AppendMenuW(menu, 0x0800, 0, None)
                user32.AppendMenuW(menu, 0, 4, "Sair do monitor")
                user32.SetMenuDefaultItem(menu, 2, 0)
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                user32.SetForegroundWindow(hwnd)
                selected = user32.TrackPopupMenu(menu, 0x0100 | 0x0002, point.x, point.y, 0, hwnd, None)
                action = {1: "open", 2: "summary", 3: "console", 4: "exit",5:'stop_all'}.get(selected)
                if action:
                    self.actions.put(action)
                user32.PostMessageW(hwnd, 0, 0, 0)
            finally:
                user32.DestroyMenu(menu)

        @WNDPROC
        def wndproc(hwnd, message, wparam, lparam):
            try:
                if message == taskbar_created:
                    add_icon()
                    return 0
                if message == callback_message:
                    if lparam in (0x0205, 0x007B):  # right-up / context menu
                        show_menu(hwnd)
                    elif lparam == 0x0203:  # double-left click
                        self.actions.put("summary")
                    return 0
                if message == 0x0010:  # WM_CLOSE
                    user32.DestroyWindow(hwnd)
                    return 0
                if message == 0x0002:  # WM_DESTROY
                    shell32.Shell_NotifyIconW(2, ctypes.byref(nid))
                    self.registered.clear()
                    user32.PostQuitMessage(0)
                    return 0
            except Exception as error:
                self.error = str(error)
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._wndproc = wndproc
        try:
            if not self.icon_path.is_file():
                raise FileNotFoundError(f"Ícone não encontrado: {self.icon_path}")
            self._icon = user32.LoadImageW(None, str(self.icon_path.resolve()), 1, 32, 32, 0x0010)
            if not self._icon:
                raise ctypes.WinError(ctypes.get_last_error())
            nid.hIcon = self._icon
            window_class = WNDCLASSW()
            window_class.lpfnWndProc, window_class.hInstance = wndproc, hinstance
            window_class.lpszClassName = class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise ctypes.WinError(ctypes.get_last_error())
            class_registered = True
            # Hidden top-level tool window also receives Explorer's TaskbarCreated.
            hwnd = user32.CreateWindowExW(0x0080, class_name, self.title, 0, 0, 0, 0, 0,
                                         None, None, hinstance, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self.hwnd = hwnd
            nid.hWnd = hwnd
            add_icon()
            self.ready.set()
            if self._stop_requested.is_set():
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result < 0:
                    raise ctypes.WinError(ctypes.get_last_error())
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as error:
            self.error = str(error)
        finally:
            if nid.hWnd:
                shell32.Shell_NotifyIconW(2, ctypes.byref(nid))
                if self.hwnd:
                    user32.DestroyWindow(self.hwnd)
            self.hwnd = None
            self.registered.clear()
            if self._icon:
                user32.DestroyIcon(self._icon)
                self._icon = None
            if class_registered:
                user32.UnregisterClassW(class_name, hinstance)
            self.ready.set()
            self.stopped.set()
