"""Construção e adaptação responsiva das abas GPU/Rendimentos do painel (mixin de Monitor).

Métodos movidos de monitor.py sem mudança de comportamento; não leem STATE_DIR,
LOG_DIR nem SETTINGS_PATH, então patches em `monitor.*` continuam valendo.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from monitor_chart import Chart
from monitor_theme import BG, BLUE, BODY_FONT, BORDER, MUTED, ORANGE, PANEL, PINK, TEAL, TEXT, TITLE_FONT


class MonitorLayoutMixin:
    def label(self, master, text="", **kwargs):
        return tk.Label(master, text=text, bg=kwargs.pop("bg", BG), fg=kwargs.pop("fg", TEXT),
                        font=kwargs.pop("font", (BODY_FONT, 10)), **kwargs)

    def metric_card(self, parent, title, color, column, values, notes, key):
        frame = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        frame.grid(row=0, column=column, sticky="nsew", padx=4)
        parent.columnconfigure(column, weight=1, uniform="metrics")
        frame.title_label = self.label(frame, title, bg=PANEL, fg=MUTED, font=(BODY_FONT, 9, "bold"))
        frame.title_label.pack(anchor="w", padx=12, pady=(8, 2))
        values[key] = self.label(frame, "—", bg=PANEL, fg=color, font=(TITLE_FONT, 23, "bold"))
        values[key].pack(anchor="w", padx=12)
        notes[key] = self.label(frame, "Aguardando dados", bg=PANEL, fg=MUTED, font=(BODY_FONT, 9))
        notes[key].pack(anchor="w", padx=12, pady=(1, 8))
        return frame

    def build_gpu(self):
        parent = self.live_tab
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        selector=tk.Frame(parent,bg=BG)
        selector.grid(row=0,column=0,sticky='ew',padx=5,pady=(4,0))
        self.gpu_selection=ttk.Combobox(selector,state='readonly',width=48)
        self.gpu_selection.pack(side='left',fill='x',expand=True)
        self.gpu_selection.bind('<<ComboboxSelected>>',lambda e:self.select_gpu(self.gpu_ids[self.gpu_selection.current()]) if self.gpu_selection.current()>=0 else None)
        tk.Button(selector,text='Identificação completa',command=self.selected_identity,bg=PANEL,fg=TEXT,relief='flat',font=(BODY_FONT,9)).pack(side='left',padx=5)
        tk.Button(selector,text='Parar esta GPU',command=lambda:self.toggle_gpu(self.selected_gpu_uuid,False) if getattr(self,'selected_gpu_uuid',None) else None,bg='#9E3333',fg=TEXT,relief='flat',font=(BODY_FONT,9)).pack(side='left',padx=5)
        cards = tk.Frame(parent, bg=BG)
        cards.grid(row=1, column=0, sticky="ew", pady=(5, 4))
        for index, (key, title, color) in enumerate((("hash", "HASHRATE", TEAL), ("temp", "TEMPERATURA", ORANGE), ("power", "POTÊNCIA", BLUE), ("fan", "VENTOINHAS", PINK), ("shares", "SHARES ACEITAS", TEAL))):
            self.metric_card(cards, title, color, index, self.card_values, self.card_notes, key)
        self.details = self.label(parent, "Lendo sensores…", fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 9))
        self.details.grid(row=2, column=0, sticky="ew", padx=5, pady=(0, 5))
        chart_grid = tk.Frame(parent, bg=BG)
        chart_grid.grid(row=3, column=0, sticky="nsew")
        parent.rowconfigure(2,weight=0);parent.rowconfigure(3,weight=1)
        chart_grid.columnconfigure((0, 1), weight=1, uniform="charts")
        chart_grid.rowconfigure((0, 1), weight=1, uniform="charts")
        self.charts = {}
        for index, (key, title, color, unit, fixed) in enumerate((("hash", "Hashrate · TH/s", TEAL, "TH/s", None), ("temp", "Temperatura da GPU", ORANGE, "°C", (25, 85)), ("power", "Potência da placa", BLUE, "W", None), ("fan", "Ventoinhas", PINK, "%", (0, 100)))):
            chart = Chart(chart_grid, title, color, unit, fixed, max_gap=180 if key == "hash" else 80, show_age=key == "hash")
            chart.grid(row=index//2, column=index%2, sticky="nsew", padx=4, pady=3)
            self.charts[key] = chart
        self.info = self.label(parent, "Pool e sessão: aguardando", fg=MUTED, anchor="w", font=(BODY_FONT, 8))
        self.info.grid(row=4, column=0, sticky="ew", padx=5, pady=(5, 3))
        self.log_title = self.label(parent, "ÚLTIMOS EVENTOS", fg=TEAL, anchor="w", font=(BODY_FONT, 8, "bold"))
        self.log_title.grid(row=5, column=0, sticky="ew", padx=5)
        self.logs = self.label(parent, "Aguardando atividade", bg=PANEL, fg=MUTED, anchor="nw", justify="left", font=("Consolas", 8), padx=8, pady=5)
        self.logs.grid(row=6, column=0, sticky="ew", padx=4, pady=(3, 7))

    def build_finance(self):
        parent = self.finance_tab
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        quote_row=tk.Frame(parent,bg=BG)
        quote_row.grid(row=0,column=0,sticky='ew',padx=5,pady=(7,4))
        self.finance_wallet_filter=ttk.Combobox(quote_row,state='readonly',width=23,values=['Todas as carteiras'])
        self.finance_wallet_filter.current(0)
        self.finance_wallet_filter.pack(side='right',padx=(6,0))
        self.finance_wallet_filter.bind('<<ComboboxSelected>>',lambda e:self.refresh_finance())
        self.quote = self.label(quote_row, "Consultando cotações PRL / BRL / USD / BTC…", fg=TEAL, anchor="w", font=(TITLE_FONT, 12, "bold"))
        self.quote.pack(side='left',fill='x',expand=True)
        self.quote_note = self.label(parent, "Cotações e pool a cada 60 s", fg=MUTED, anchor="w", font=(BODY_FONT, 8))
        self.quote_note.grid(row=1, column=0, sticky="ew", padx=5, pady=(0, 6))
        table_frame = tk.Frame(parent, bg=BG)
        table_frame.grid(row=2, column=0, sticky="nsew", padx=4)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure("Finance.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=25, font=(BODY_FONT, 10), borderwidth=0)
        style.configure("Finance.Treeview.Heading", background=BORDER, foreground=TEXT, font=(BODY_FONT, 9, "bold"), relief="flat")
        style.map("Finance.Treeview", background=[("selected", BORDER)], foreground=[("selected", TEXT)])
        self.finance_table = ttk.Treeview(table_frame, columns=("prl", "brl", "usd", "btc"), show="tree headings", height=9, style="Finance.Treeview", selectmode="none")
        self.finance_table.grid(row=0, column=0, sticky="nsew")
        self.finance_table.heading("#0", text="PERÍODO / TIPO", anchor="w")
        for key, title in (("prl", "PEARL · PRL"), ("brl", "REAIS · BRL"), ("usd", "DÓLARES · USD"), ("btc", "BITCOIN · BTC")):
            self.finance_table.heading(key, text=title, anchor="e")
            self.finance_table.column(key, width=190, minwidth=90, stretch=True, anchor="e")
        self.finance_table.column("#0", width=240, minwidth=170, stretch=True)
        self.finance_row_titles = {}
        for key, title in (("day", "Hoje"), ("week", "Semana · segunda até agora"), ("month", "Últimos 30 dias"), ("accumulated", "Acumulado · pago + saldo"), ("unpaid", "Saldo atual na pool"), ("confirmed", "Confirmado para envio"), ("paid", "Enviado pela pool"), ("forecast7", "Projeção · próximos 7 dias"), ("forecast30", "Projeção · próximos 30 dias")):
            self.finance_row_titles[key] = title
            self.finance_table.insert("", "end", iid=key, text=title, values=("N/D",)*4, tags=("forecast",) if key.startswith("forecast") else ())
        self.finance_table.tag_configure("forecast", foreground=ORANGE)
        self.money_chart = Chart(parent, "Créditos observados · em reais à cotação atual", TEAL, "R$", window=30*86400)
        self.money_chart.grid(row=3, column=0, sticky="nsew", padx=4, pady=(6, 5))
        parent.rowconfigure(3, weight=1)
        self.finance_pay_summary = self.label(parent, "Pagamento em PRL · aguardando dados", fg=TEAL, anchor="w", justify="left", font=(BODY_FONT, 9))
        self.finance_pay_summary.grid(row=4, column=0, sticky="ew", padx=5, pady=(2, 3))
        self.finance_explanation = self.label(parent, "", fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 8))
        self.finance_explanation.grid(row=5, column=0, sticky="ew", padx=5, pady=(0, 3))
        self.finance_sources = self.label(parent, "", fg=MUTED, anchor="w", justify="left", font=(BODY_FONT, 8))
        self.finance_sources.grid(row=6, column=0, sticky="ew", padx=5, pady=(0, 7))

    def adapt_layout(self, event=None):
        if event is not None and event.widget is not self:
            return
        width, height = self.winfo_width(), self.winfo_height()
        if width < 500:
            return
        # Moving/restoring the native window can repeat Configure at the same size.
        # Reconfiguring global ttk styles there invalidates the entire widget tree.
        if getattr(self,'_adapted_size',None)==(width,height):
            return
        self._adapted_size=(width,height)
        self.remember_window_state()
        logo_width = 360 if height>=1400 and width>=2800 else 240 if height>=740 and width>=1600 else 180
        if logo_width != self.logo_native_width:
            self.logo_native_width = logo_width
            self.brand_logo = self.logo_images[logo_width]
            self.brand_logo_label.configure(image=self.brand_logo)
        compact = height < 850 or width < 1450
        self.overview_activity.grid_remove()
        self.overview_tab.rowconfigure(4,weight=0)
        if height>=900 and width>=1450:
            self.overview_trends.grid();self.overview_tab.rowconfigure(5,weight=1)
        else:
            self.overview_trends.grid_remove();self.overview_tab.rowconfigure(5,weight=0)
        if hasattr(self,'rig_cards'):self.rig_cards.adapt(width-30)
        for widget in (self.payout_details,self.payout_source,self.payout_rule):
            if height<780:widget.pack_forget() if widget is not self.payout_rule else widget.grid_remove()
            elif widget is self.payout_rule:widget.grid()
            else:widget.pack(fill='x',pady=(3,0))
        body = 12 if compact else min(19, max(14, round(min(width/1650, height/1000)*14)))
        value_size = 29 if compact else min(46, max(34, round(body*2.4)))
        if not hasattr(self, "_font_baselines"):
            self._font_baselines = {}
        pending = list(self.winfo_children())
        while pending:
            widget = pending.pop()
            if widget.winfo_toplevel() is not self:
                continue
            pending.extend(widget.winfo_children())
            try:
                if "font" not in widget.keys():
                    continue
                if widget not in self._font_baselines:
                    font = tkfont.Font(font=widget.cget("font"))
                    self._font_baselines[widget] = (font.actual("family"), abs(font.actual("size")), font.actual("weight"))
                family, points, weight = self._font_baselines[widget]
                pixels = max(11, round(points*4/3*(body/12)))
                widget.configure(font=(family, -pixels, weight))
            except tk.TclError:
                pass
        for label in list(self.card_values.values())+list(self.overview_values.values()):
            label.configure(font=(TITLE_FONT, -value_size, "bold"))
        for label in list(self.card_notes.values())+list(self.overview_notes.values()):
            label.configure(font=(BODY_FONT, -body))
        if height>=780:
            self.support_status_label.pack(anchor='w',pady=(3,0))
            self.wallet_entry.master.master.configure(pady=8)
        if height<780:
            for label in self.overview_values.values():label.configure(font=(TITLE_FONT,-23,'bold'))
            for label in self.overview_notes.values():label.configure(font=(BODY_FONT,-10))
            for label in self.overview_values.values():label.master.title_label.configure(font=(BODY_FONT,-11,'bold'))
            for label in self.overview_values.values():
                label.master.title_label.pack_configure(pady=(3,0))
                self.overview_notes[next(key for key,value in self.overview_values.items() if value is label)].pack_configure(pady=(0,3))
            self.rig_cards.adapt(width-30)
            wallet_panel=self.wallet_entry.master.master
            wallet_panel.configure(pady=4)
            wallet_panel.winfo_children()[0].configure(font=(BODY_FONT,-10,'bold'))
            self.wallet_entry.configure(font=('Consolas',-11));self.wallet_entry.pack_configure(ipady=0)
            self.support_status_label.configure(font=(BODY_FONT,-10))
            self.support_status_label.pack_forget()
            for button in (self.wallet_copy_button,self.wallet_pool_button):button.configure(font=(BODY_FONT,-10),pady=2)
            for widget in (self.payout_state,self.payout_generated_text,self.payout_confirmed_text,self.payout_remaining):widget.configure(font=(BODY_FONT,-11))
            self.payout_eta.configure(font=(TITLE_FONT,-13,'bold'))
            self.payout_threshold.configure(font=(BODY_FONT,-10))
            ttk.Style(self).configure('Generated.Horizontal.TProgressbar',thickness=8)
            ttk.Style(self).configure('Confirmed.Horizontal.TProgressbar',thickness=8)
        for label in (self.details, self.overview_thermal, self.payout_generated_text, self.payout_confirmed_text, self.payout_remaining, self.payout_details):
            label.configure(font=(BODY_FONT, -body))
        half = max(450, (width-95)//2)
        for label in (self.payout_eta, self.payout_details, self.payout_remaining):
            label.configure(wraplength=half)
        for label in (self.details, self.overview_activity, self.logs, self.finance_pay_summary, self.finance_explanation, self.finance_sources):
            label.configure(wraplength=width-55)
        lineheight = tkfont.Font(family=BODY_FONT, size=-body).metrics("linespace")
        rowheight = lineheight+8
        ttk.Style(self).configure("Finance.Treeview", rowheight=rowheight, font=(BODY_FONT, -body))
        ttk.Style(self).configure("Finance.Treeview.Heading", font=(BODY_FONT, -body, "bold"))
        ttk.Style(self).configure("TNotebook.Tab", font=(BODY_FONT, -max(12,body), "bold"))
        for column, share in (("#0", .24), ("prl", .19), ("brl", .18), ("usd", .18), ("btc", .21)):
            self.finance_table.column(column, width=int((width-48)*share), minwidth=80)
        table_height = rowheight*9+31
        self.finance_tab.rowconfigure(2, weight=0, minsize=table_height)
        self.finance_tab.rowconfigure(3, weight=1, minsize=85)
        self.money_chart.configure(height=100 if compact else 170)
        self.layout_profile = {"width": width, "height": height, "compact": compact, "body_font": body, "rowheight": rowheight}
        for chart in list(self.charts.values())+[self.money_chart]:
            chart.title_px = max(13,body)
            chart.axis_px = max(11,body-2)
            chart.draw()
        if height<780:
            for widget in (self.payout_state,self.payout_generated_text,self.payout_confirmed_text,self.payout_remaining):widget.configure(font=(BODY_FONT,-11))
            self.payout_state.master.configure(pady=4)
            self.payout_remaining.configure(text=self.payout_remaining.cget('text').replace('\n',' · ').replace('Pendente:','Pend.').replace('Falta gerar:','Falta:'))
