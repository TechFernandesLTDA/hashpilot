"""Telas de configuração que funcionam como janela própria ou como seção da janela única de Configurações."""
from __future__ import annotations
import tkinter as tk

BG = "#0A1628"


class Section(tk.Frame):
    """Frame com a interface mínima de janela usada pelas telas antigas.

    Fechar ou cancelar dentro da seção a recria com os dados salvos; só a janela
    de Configurações destrói a seção de verdade (`release`).
    """
    embedded = True

    def __init__(self, container, *, on_close=None, navigate=None):
        super().__init__(container, bg=BG)
        self.on_close, self.navigate_to, self.release = on_close, navigate, False
        self.close_handler = None

    def title(self, *_):
        return None

    def geometry(self, *_):
        return None

    def minsize(self, *_):
        return None

    def maxsize(self, *_):
        return None

    def resizable(self, *_):
        return None

    def transient(self, *_):
        return None

    def grab_set(self):
        return None

    def grab_release(self):
        return None

    def iconbitmap(self, *_):
        return None

    def lift(self, *_):
        return None

    def protocol(self, name=None, handler=None):
        if name == "WM_DELETE_WINDOW":
            self.close_handler = handler

    def navigate(self, name):
        if self.navigate_to:
            self.navigate_to(name)

    def destroy(self):
        if not self.release and self.on_close:
            self.on_close(self)
            return
        super().destroy()


def open_window(parent, container, title):
    if container is not None:
        return Section(container, on_close=getattr(container, "section_closed", None),
                       navigate=getattr(container, "navigate", None))
    window = tk.Toplevel(parent) if parent else tk.Tk()
    window.title(title)
    return window


def embedded(window):
    return getattr(window, "embedded", False)


def present(parent, window, *, modal=True):
    """Janela própria: modal como antes. Seção: retorna sem bloquear."""
    if embedded(window):
        return
    if parent:
        window.transient(parent)
        if modal:
            window.grab_set()
            parent.wait_window(window)
    else:
        window.mainloop()
