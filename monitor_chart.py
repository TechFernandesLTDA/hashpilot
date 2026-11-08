"""Gráfico de linha do painel (Canvas Tk), com eixos de tempo e valores ausentes explícitos."""
from __future__ import annotations

from collections import deque
import datetime as dt
import time
import tkinter as tk

from monitor_format import safe_number
from monitor_theme import BODY_FONT, BORDER, MUTED, PANEL, TEXT


class Chart(tk.Canvas):
    def __init__(self, master, title, color, unit, fixed=None, window=900, max_gap=80, show_age=False):
        super().__init__(master, bg=PANEL, highlightthickness=1, highlightbackground=BORDER, height=185)
        self.title, self.color, self.unit, self.fixed = title, color, unit, fixed
        self.window = window
        self.max_gap, self.show_age = max_gap, show_age
        self.points = deque(maxlen=50000 if window > 900 else 900)
        self.dirty = False
        self._layout = None
        self.bind("<Configure>", lambda _: self.draw(force=True))

    def redraw_if_dirty(self):
        if self.dirty and self.winfo_viewable():
            self.draw()

    def _build_static(self, w, h, title_size, axis_size):
        """Título, grade e rótulos do eixo só são recriados quando tamanho ou fonte mudam."""
        self.delete("all")
        left, right, top, bottom = 50, w-18, 44, h-28
        self.create_text(16, 18, anchor="w", text=self.title, fill=TEXT, font=(BODY_FONT, title_size, "bold"))
        self.grid_items, self.axis_items = [], []
        for i in range(4):
            y = top + (bottom-top)*i/3
            self.grid_items.append(self.create_line(left, y, right, y, fill=BORDER))
            self.axis_items.append(self.create_text(left-8, y, text="", anchor="e", fill=MUTED, font=(BODY_FONT, axis_size)))

    def add(self, timestamp, value):
        timestamp=safe_number(timestamp)
        if timestamp is None or timestamp<0:return
        if value is not None:
            value=safe_number(value)
            if value is None:return
        if not self.points or timestamp > self.points[-1][0]:
            self.points.append((timestamp, value))
            return
        # Logs podem ser descarregados depois de uma amostra de indisponibilidade.
        # O horário real da medição prevalece sobre a ordem de chegada ao painel.
        points = dict(self.points)
        if timestamp in points and points[timestamp] is not None and value is None:
            return
        points[timestamp] = value
        self.points.clear()
        self.points.extend(sorted(points.items()))

    def segments(self):
        groups, group, previous = [], [], None
        for timestamp, value in self.points:
            if value is None:
                if group:
                    groups.append(group)
                group, previous = [], None
                continue
            if previous is not None and timestamp-previous > self.max_gap:
                if group:
                    groups.append(group)
                group = []
            group.append((timestamp, value))
            previous = timestamp
        if group:
            groups.append(group)
        return groups

    def sample_info(self):
        measured = [(stamp, value) for stamp, value in self.points if value is not None]
        return {"point_count": len(self.points), "latest_sample_at": measured[-1][0] if measured else None,
                "latest_value": measured[-1][1] if measured else None, "gap_limit_seconds": self.max_gap}

    def draw(self, force=False):
        # Hidden tabs and the tray only mark the chart; it is drawn when it becomes visible.
        if not force and not self.winfo_viewable():
            self.dirty = True
            return
        self.dirty = False
        title_size = -getattr(self, "title_px", 13)
        axis_size = -getattr(self, "axis_px", 11)
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40:
            return
        layout = (w, h, title_size, axis_size, self.title)
        if layout != self._layout:
            self._build_static(w, h, title_size, axis_size)
            self._layout = layout
        self.delete("dynamic")
        cutoff = time.time() - self.window
        while self.points and self.points[0][0] < cutoff:
            self.points.popleft()
        values = [v for _, v in self.points if v is not None]
        if not values:
            for item in self.grid_items+self.axis_items:
                self.itemconfigure(item, state="hidden")
            self.create_text(w/2, h/2, text="Aguardando amostras reais", fill=MUTED, font=(BODY_FONT, title_size), tags="dynamic")
            return
        ymin, ymax = self.fixed or (max(0, min(values) * .85), max(values) * 1.1)
        if ymax <= ymin:
            ymax = ymin + 1
        left, right, top, bottom = 50, w-18, 44, h-28
        for i in range(4):
            value = ymax - (ymax-ymin)*i/3
            self.itemconfigure(self.grid_items[i], state="normal")
            self.itemconfigure(self.axis_items[i], state="normal", text=f"{value:.2f}" if abs(ymax) < 2 else f"{value:.0f}")
        now = time.time()
        start = max(cutoff, min(p[0] for p in self.points))
        span = max(60, now-start)
        for group in self.segments():
            coordinates = []
            for timestamp, value in group:
                x = left + (timestamp-start)/span*(right-left)
                y = bottom - (value-ymin)/(ymax-ymin)*(bottom-top)
                coordinates.extend([x, y])
            if len(coordinates) >= 4:
                self.create_line(*coordinates, fill=self.color, width=2, tags=("series", "dynamic"))
            x, y = coordinates[-2:]
            self.create_oval(x-3, y-3, x+3, y+3, fill=self.color, outline="", tags=("series-point", "dynamic"))
        if self.show_age:
            latest = self.sample_info()["latest_sample_at"]
            age = max(0, int(now-latest))
            self.create_text(right, 18, anchor="e", text=f"última amostra há {age} s", fill=MUTED,
                             font=(BODY_FONT, axis_size), tags=("sample-age", "dynamic"))
        self.create_text(left, h-12, text=dt.datetime.fromtimestamp(start).strftime("%H:%M:%S"), anchor="w", fill=MUTED, font=(BODY_FONT, axis_size), tags=("time-axis", "dynamic"))
        self.create_text(right, h-12, text="agora · " + self.unit, anchor="e", fill=MUTED, font=(BODY_FONT, axis_size), tags=("time-axis", "dynamic"))
