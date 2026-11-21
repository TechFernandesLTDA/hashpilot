"""Construção e adaptação responsiva das abas GPU/Rendimentos do painel (mixin de Monitor).

Métodos movidos de monitor.py sem mudança de comportamento; não leem STATE_DIR,
LOG_DIR nem SETTINGS_PATH, então patches em `monitor.*` continuam valendo.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import monitor_theme as theme
import ui_kit as ui
from monitor_chart import Chart
from monitor_theme import BORDER, CARD, SURFACE, TEXT, TEXT_SECONDARY, WARNING


class MonitorLayoutMixin:
    def label(self, master, text="", **kwargs):
        return tk.Label(master, text=text, bg=kwargs.pop("bg", SURFACE), fg=kwargs.pop("fg", TEXT),
                        font=kwargs.pop("font", ui.font("body")), **kwargs)

    def metric_card(self, parent, title, color, column, values, notes, key, count=None):
        """Cartão de indicador. `color` ficou por compatibilidade: o número é sempre `TEXT`."""
        card = ui.Card(parent, padding="lg")
        left = 0 if column == 0 else ui.px(self, 6)
        right = 0 if count is not None and column == count-1 else ui.px(self, 6)
        card.grid(row=0, column=column, sticky="nsew", padx=(left, right))
        parent.columnconfigure(column, weight=1, uniform="metrics")
        card.title_label = ui.label(card, title, "overline", tone="secondary", bg=card.bg)
        card.title_label.pack(anchor="w")
        values[key] = ui.label(card, "—", "metric", bg=card.bg)
        values[key].pack(anchor="w", pady=(ui.px(self, 4), 0))
        notes[key] = ui.label(card, "Aguardando dados", "small", tone="tertiary", bg=card.bg)
        notes[key].pack(anchor="w", pady=(ui.px(self, 2), 0))
        return card

    def build_gpu(self):
        parent = self.live_tab
        parent.columnconfigure(0, weight=1)
        gap = ui.px(self, 12)
        selector = tk.Frame(parent, bg=SURFACE)
        selector.grid(row=0, column=0, sticky="ew")
        self.gpu_selection = ttk.Combobox(selector, state="readonly", width=48)
        self.gpu_selection.pack(side="left", fill="x", expand=True)
        self.gpu_selection.bind("<<ComboboxSelected>>", lambda e: self.select_gpu(self.gpu_ids[self.gpu_selection.current()]) if self.gpu_selection.current() >= 0 else None)
        ui.Button(selector, "Identificação completa", self.selected_identity, variant="secondary", size="sm").pack(side="left", padx=(ui.px(self, 8), 0))
        ui.Button(selector, "Parar esta GPU", lambda: self.toggle_gpu(self.selected_gpu_uuid, False) if getattr(self, "selected_gpu_uuid", None) else None,
                  variant="danger_ghost", size="sm").pack(side="left", padx=(ui.px(self, 4), 0))
        cards = tk.Frame(parent, bg=SURFACE)
        cards.grid(row=1, column=0, sticky="ew", pady=(gap, gap))
        self.gpu_cards_row = cards
        metrics = (("hash", "HASHRATE"), ("temp", "TEMPERATURA"), ("power", "POTÊNCIA"), ("fan", "VENTOINHAS"), ("shares", "SHARES ACEITAS"))
        for index, (key, title) in enumerate(metrics):
            self.metric_card(cards, title, None, index, self.card_values, self.card_notes, key, count=len(metrics))
        self.details = ui.label(parent, "Lendo sensores…", "small", tone="secondary")
        self.details.grid(row=2, column=0, sticky="ew", pady=(0, ui.px(self, 8)))
        chart_grid = tk.Frame(parent, bg=SURFACE)
        chart_grid.grid(row=3, column=0, sticky="nsew")
        parent.rowconfigure(2, weight=0)
        parent.rowconfigure(3, weight=1)
        chart_grid.columnconfigure((0, 1), weight=1, uniform="charts")
        chart_grid.rowconfigure((0, 1), weight=1, uniform="charts")
        self.charts = {}
        half = ui.px(self, 6)
        for index, (key, title, unit, fixed) in enumerate((("hash", "Hashrate · TH/s", "TH/s", None), ("temp", "Temperatura da GPU", "°C", (25, 85)),
                                                          ("power", "Potência da placa", "W", None), ("fan", "Ventoinhas", "%", (0, 100)))):
            chart = Chart(chart_grid, title, theme.CHART[key], unit, fixed, max_gap=180 if key == "hash" else 80, show_age=key == "hash")
            row, column = divmod(index, 2)
            chart.grid(row=row, column=column, sticky="nsew", padx=(0, half) if column == 0 else (half, 0), pady=(0, half) if row == 0 else (half, 0))
            self.charts[key] = chart
        self.info = ui.label(parent, "Pool e sessão: aguardando", "caption", tone="tertiary")
        self.info.grid(row=4, column=0, sticky="ew", pady=(ui.px(self, 8), ui.px(self, 4)))
        self.log_title = ui.label(parent, "ÚLTIMOS EVENTOS", "overline", tone="secondary")
        self.log_title.grid(row=5, column=0, sticky="ew")
        log_card = ui.Card(parent, padding="md")
        log_card.grid(row=6, column=0, sticky="ew", pady=(ui.px(self, 4), 0))
        self.logs_card = log_card
        self.logs = tk.Label(log_card, text="Aguardando atividade", bg=CARD, fg=TEXT_SECONDARY, anchor="nw", justify="left", font=ui.font("small", mono=True))
        self.logs.pack(fill="x")

    def build_finance(self):
        parent = self.finance_tab
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)
        quote_row = tk.Frame(parent, bg=SURFACE)
        quote_row.grid(row=0, column=0, sticky="ew", pady=(0, ui.px(self, 4)))
        self.finance_wallet_filter = ttk.Combobox(quote_row, state="readonly", width=23, values=["Todas as carteiras"])
        self.finance_wallet_filter.current(0)
        self.finance_wallet_filter.pack(side="right", padx=(ui.px(self, 8), 0))
        self.finance_wallet_filter.bind("<<ComboboxSelected>>", lambda e: self.refresh_finance())
        self.quote = ui.label(quote_row, "Consultando cotações PRL / BRL / USD / BTC…", "h2")
        self.quote.pack(side="left", fill="x", expand=True)
        self.quote_note = ui.label(parent, "Cotações e pool a cada 60 s", "caption", tone="tertiary")
        self.quote_note.grid(row=1, column=0, sticky="ew", pady=(0, ui.px(self, 8)))
        table_frame = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
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
        self.finance_table.tag_configure("forecast", foreground=WARNING)
        self.money_chart = Chart(parent, "Créditos observados · em reais à cotação atual", theme.CHART["money"], "R$", window=30*86400)
        self.money_chart.grid(row=3, column=0, sticky="nsew", pady=(ui.px(self, 12), ui.px(self, 8)))
        parent.rowconfigure(3, weight=1)
        self.finance_pay_summary = ui.label(parent, "Pagamento em PRL · aguardando dados", "body", justify="left")
        self.finance_pay_summary.grid(row=4, column=0, sticky="ew", pady=(0, ui.px(self, 4)))
        self.finance_explanation = ui.label(parent, "", "small", tone="secondary")
        self.finance_explanation.grid(row=5, column=0, sticky="ew", pady=(0, ui.px(self, 4)))
        self.finance_sources = ui.label(parent, "", "caption", tone="tertiary")
        self.finance_sources.grid(row=6, column=0, sticky="ew")

    def sync_navigation(self, event=None):
        """Barra lateral e título da página seguem o Notebook (também ao restaurar a aba salva)."""
        try:
            current = self.tabs.index(self.tabs.select())
        except tk.TclError:
            return
        for index, item in enumerate(getattr(self, "nav_items", [])):
            item.set_selected(index == current)
        if hasattr(self, "page_title"):
            self.page_title.configure(text=self.tabs.tab(current, "text"))

    def sidebar_required_width(self):
        """Largura mínima da barra lateral para o conteúdo dela caber com o DPI atual (marca, itens e placa do logo)."""
        def horizontal_padding(widget):
            try:
                padx = widget.pack_info().get("padx", 0)
            except tk.TclError:
                return 0
            values = padx if isinstance(padx, (tuple, list)) else str(padx).split()
            values = [int(float(value)) for value in values] or [0]
            return sum(values) if len(values) > 1 else 2*values[0]
        need = 0
        for child in self.sidebar.winfo_children():
            if child.winfo_manager() != "pack":
                continue
            if child is self.brand_logo_label.master:  # a placa acompanha o PNG atual, ainda sem geometria recalculada
                width = self.brand_logo.width() + 2*int(child.cget("highlightthickness"))
            else:
                width = child.winfo_reqwidth()
            need = max(need, width + horizontal_padding(child))
        return need

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
        body = 12 if compact else min(19, max(14, round(min(width/1650, height/1000)*14)))
        sidebar_px = max(round(theme.SIDEBAR_WIDTH * body / 12), self.sidebar_required_width())
        self.sidebar.configure(width=sidebar_px)
        content_width = width - sidebar_px - 1
        self.overview_activity.grid_remove()
        self.overview_tab.rowconfigure(4,weight=0)
        # Os gráficos de tendência só entram quando sobra altura de verdade (o mínimo cresce com o DPI).
        if height>=max(900, ui.px(self, 800)) and width>=1450:
            self.overview_trends.grid();self.overview_tab.rowconfigure(5,weight=1)
        else:
            self.overview_trends.grid_remove();self.overview_tab.rowconfigure(5,weight=0)
        if hasattr(self,'rig_cards'):
            self.rig_cards.font_scale=body/12
            self.rig_cards.adapt(content_width-30)
        for widget in (self.payout_details,self.payout_source,self.payout_rule):
            if height<780:widget.pack_forget() if widget is not self.payout_rule else widget.grid_remove()
            elif widget is self.payout_rule:widget.grid()
            else:widget.pack(fill='x',pady=(3,0))
        value_size = 29 if compact else min(46, max(34, round(body*2.4)))
        if not hasattr(self, "_font_baselines"):
            self._font_baselines = {}
        pending = list(self.winfo_children())
        while pending:
            widget = pending.pop()
            # Cartões das GPUs têm escala própria; a barra lateral fica no tamanho do sistema (igual às Configurações).
            if widget.winfo_toplevel() is not self or widget is self.rig_cards or widget is self.sidebar:
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
            label.configure(font=ui.font("metric", size=-value_size))
        for label in list(self.card_notes.values())+list(self.overview_notes.values()):
            label.configure(font=ui.font("small", size=-body))
        wallet_panel = self.wallet_entry.master.master
        tight = height < 850
        card_pad = ui.px(self, 12 if tight else 16)
        if height>=780:
            self.support_status_label.pack(anchor='w',pady=(ui.px(self,4),0))
            wallet_panel.configure(pady=ui.px(self,8 if tight else 12))
            self.wallet_entry.pack_configure(ipady=ui.px(self,4))
            for card in {label.master for label in list(self.card_values.values())+list(self.overview_values.values())}:
                card.configure(pady=card_pad)
            for label in self.card_values.values():
                label.master.title_label.configure(font=ui.font("overline", size=-round(11*body/12)))
                label.master.title_label.pack_configure(pady=(0,0))
                label.pack_configure(pady=(ui.px(self,4),0))
            for label in self.card_notes.values():label.pack_configure(pady=(ui.px(self,2),0))
            self.page_header.grid_configure(pady=(ui.px(self,18),ui.px(self,12)))
            self.gpu_cards_row.grid_configure(pady=(ui.px(self,12),ui.px(self,12)))
            self.logs_card.configure(pady=ui.px(self,12))
        if height<780:
            small_pad = ui.px(self, 6)
            for label in self.overview_values.values():label.configure(font=ui.font("metric",size=-23))
            for label in self.overview_notes.values():label.configure(font=ui.font("small",size=-10))
            for label in self.overview_values.values():label.master.title_label.configure(font=ui.font("overline",size=-11))
            for key, label in self.overview_values.items():
                label.master.configure(pady=small_pad)
                label.master.title_label.pack_configure(pady=(0,0))
                label.pack_configure(pady=(0,0))
                self.overview_notes[key].pack_configure(pady=(0,0))
            for key, label in self.card_values.items():
                label.configure(font=ui.font("metric",size=-23))
                label.master.configure(pady=small_pad)
                label.master.title_label.configure(font=ui.font("overline",size=-11))
                label.master.title_label.pack_configure(pady=(0,0))
                label.pack_configure(pady=(0,0))
                self.card_notes[key].configure(font=ui.font("small",size=-10))
                self.card_notes[key].pack_configure(pady=(0,0))
            self.page_header.grid_configure(pady=(ui.px(self,8),ui.px(self,6)))
            self.gpu_cards_row.grid_configure(pady=(ui.px(self,6),ui.px(self,6)))
            self.logs_card.configure(pady=ui.px(self,4))
            self.rig_cards.adapt(content_width-30)
            wallet_panel.configure(pady=small_pad)
            wallet_panel.winfo_children()[0].configure(font=ui.font("overline",size=-10))
            self.wallet_entry.configure(font=ui.font("small",size=-11,mono=True));self.wallet_entry.pack_configure(ipady=0)
            self.support_status_label.configure(font=ui.font("small",size=-10))
            self.support_status_label.pack_forget()
            for button in (self.wallet_copy_button,self.wallet_pool_button):button.configure(font=ui.font("caption",size=-10,weight="semibold"),pady=2)
            for widget in (self.payout_state,self.payout_generated_text,self.payout_confirmed_text,self.payout_remaining):widget.configure(font=ui.font("small",size=-11))
            self.payout_eta.configure(font=ui.font("h2",size=-13))
            self.payout_threshold.configure(font=ui.font("small",size=-10))
            self.payout_state.master.configure(pady=small_pad)
        else:
            self.wallet_entry.configure(font=ui.font("body",size=-body,mono=True))
            self.payout_eta.configure(font=ui.font("h2",size=-max(13,body+1)))
            for button in (self.wallet_copy_button,self.wallet_pool_button):button.configure(pady=ui.px(self,4))
            self.payout_state.master.configure(pady=ui.px(self,12 if tight else 16))
        for label in (self.details, self.overview_thermal, self.payout_generated_text, self.payout_confirmed_text, self.payout_remaining, self.payout_details):
            label.configure(font=ui.font("small", size=-body))
        if height<780:
            for widget in (self.payout_state,self.payout_generated_text,self.payout_confirmed_text,self.payout_remaining):widget.configure(font=ui.font("small",size=-11))
        page_width = content_width - 2*ui.px(self, theme.CONTENT_PADDING)
        card_inner = page_width - 2*ui.px(self, 16) - 4
        half = max(300, card_inner//2 - ui.px(self, 12) - 4)
        for label in (self.payout_eta, self.payout_details, self.payout_remaining):
            label.configure(wraplength=half)
        page_wrap = page_width - 6
        log_wrap = page_wrap - 2*ui.px(self, 12) - 4
        for label in (self.details, self.overview_activity, self.finance_pay_summary, self.finance_explanation, self.finance_sources):
            label.configure(wraplength=page_wrap)
        self.logs.configure(wraplength=log_wrap)
        for notes in (self.overview_notes, self.card_notes):
            count = len(notes)
            card_width = (page_width - (count-1)*2*ui.px(self, 6))//count
            for label in notes.values():
                label.configure(wraplength=max(80, card_width - 2*ui.px(self, 16) - ui.px(self, 12)))  # folga para arredondamento da grade
        lineheight = tkfont.Font(family=theme.FONT_FAMILY, size=-body).metrics("linespace")
        rowheight = lineheight+8
        ttk.Style(self).configure("Finance.Treeview", rowheight=rowheight, font=(theme.FONT_FAMILY, -body))
        ttk.Style(self).configure("Finance.Treeview.Heading", font=ui.font("small", size=-body, weight="semibold"))
        for column, share in (("#0", .24), ("prl", .19), ("brl", .18), ("usd", .18), ("btc", .21)):
            self.finance_table.column(column, width=int((page_width-4)*share), minwidth=80)
        table_height = rowheight*9+31
        self.finance_tab.rowconfigure(2, weight=0, minsize=table_height)
        self.finance_tab.rowconfigure(3, weight=1, minsize=85)
        self.money_chart.configure(height=100 if compact else 170)
        self.layout_profile = {"width": width, "height": height, "compact": compact, "body_font": body, "rowheight": rowheight,
                               "sidebar": sidebar_px, "content_width": content_width, "page_wrap": page_wrap}
        for chart in list(self.charts.values())+list(getattr(self, 'overview_charts', {}).values())+[self.money_chart]:
            chart.title_px = max(13,body)
            chart.axis_px = max(11,body-2)
            chart.draw()
        if height<780:
            self.payout_remaining.configure(text=self.payout_remaining.cget('text').replace('\n',' · ').replace('Pendente:','Pend.').replace('Falta gerar:','Falta:'))
