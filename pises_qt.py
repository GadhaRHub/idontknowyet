#!/usr/bin/env python3
"""PiSES — PicoSonar EchoStacks: the desktop interface (Qt / PySide6).

    python pises_qt.py

Layout (left to right): navigation rail · inspector page (the inputs of
the chosen page, in collapsible sections) · plots with the console below
them. The top bar holds Open / Save / Import / Export and the Run button
(its ▾ menu has Run sweep and Cancel); the status bar shows progress.

This file only builds widgets and connects them. What happens when you
press something lives in pises_logic.py (class Logic), which this window
inherits. The physics is in diffr_model.py; unit conversion and input
checking in sample_input.py and units.py; files in outputs.py.
"""

import os
import queue
import sys
import threading

import matplotlib
matplotlib.use("QtAgg")
from PySide6 import QtCore, QtGui, QtWidgets

import diffr_model as M
import outputs as O
import sample_input as S
import units as U
import qt_theme as T
import qt_widgets as W
from qt_compat import (StringVar, BooleanVar, Scheduler, bind_spin,
                       messagebox)
from gui_common import (APP_NAME, APP_LONG, APP_SUB, CFG_GROUP_LASER,
                        CFG_GROUP_TIME, CFG_GROUP_GRID, PROP_HELP,
                        fmt_duration)
from pises_logic import Logic, B, BG_IMPORT_ERROR

Qt = QtCore.Qt

# the pages of the navigation rail, in order: (icon, label, page builder)
PAGES = [("experiment", "Experiment"), ("layers", "Layers"),
         ("interfaces", "Interfaces"), ("sweep", "Sweep"),
         ("background", "Background"), ("output", "Output")]
PAGE_EXPERIMENT, PAGE_LAYERS, PAGE_INTERFACES, PAGE_SWEEP, PAGE_BACKGROUND, \
    PAGE_OUTPUT = range(6)

LABEL_W = 184          # width of the label column in the inspector


class App(Logic, Scheduler, QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_LONG)
        self.setWindowIcon(T.app_icon())
        scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.resize(min(1560, scr.width() - 40), min(940, scr.height() - 60))

        # --- state (see pises_logic.Logic for how it is used)
        self.cache = S.DispersionCache()
        self.layers, self.sel, self._loading = [], None, False
        self.batches, self.run_counter = [], 0
        self.work_rate = self.bg_rate = None
        self.queue, self.worker = queue.Queue(), None
        self.cancel_ev = threading.Event()
        self.session_path = None
        self._pending = None

        # --- observable inputs
        self.cfg_vars = {}
        self.flag_vars = {k: BooleanVar(v) for k, v in S.default_flags().items()}
        self.show_components = BooleanVar(False)
        self.show_kernels = BooleanVar(False)
        self.show_strain = BooleanVar(False)
        self.overlay = BooleanVar(False)
        self.scale_exp = StringVar("3")
        self.stack_mode = StringVar("Equal widths")
        self.sweep_cmap = StringVar("Blues")
        self.drr_offset = StringVar("0")
        self.bg_offset = StringVar("0")
        for v in (self.drr_offset, self.bg_offset):
            v.trace_add("write", lambda *_: self._schedule_offset_redraw())
        self.stack_colors = {}
        self.view_run = StringVar("")
        self.out = {}
        for k, v in O.default_options().items():
            self.out[k] = BooleanVar(v) if isinstance(v, bool) else StringVar(v)
            self.out[k].trace_add("write", lambda *_: self.schedule_preview())
        self.sweep_layer = StringVar("")
        self.sweep_start, self.sweep_end = StringVar("100"), StringVar("600")
        self.sweep_step, self.sweep_unit = StringVar("100"), StringVar("nm")
        self.next_run_var = StringVar("1")
        # Sweep viewer
        self.viewer_mode = StringVar(self.VIEWER_MODES[0])
        self.viewer_signal = StringVar(self.VIEWER_SIGNALS[0])
        self.viewer_fps = StringVar("4")
        self.viewer_loop = BooleanVar(True)
        self.viewer_range = {k: StringVar("") for k in ("x0", "x1", "y0", "y1")}
        self.viewer_offset = StringVar("0")
        self.viewer_cur, self._viewer_bid = 0, None
        self._viewer_drag, self._play_job, self._viewer_job = False, None, None
        self._viewer_setting = self._viewer_lock = False
        self._viewer_range_job = None

        # --- layout
        central = QtWidgets.QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.rail = W.NavRail(PAGES)
        self._build_run_menu()
        outer.addWidget(self.rail)
        body = QtWidgets.QVBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        body.addWidget(self._build_topbar())
        split = QtWidgets.QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(4)
        body.addWidget(split, 1)
        insp = QtWidgets.QFrame()
        insp.setObjectName("inspector")
        il = QtWidgets.QVBoxLayout(insp)
        il.setContentsMargins(0, 0, 0, 0)
        self.pages = QtWidgets.QStackedWidget()
        il.addWidget(self.pages)
        split.addWidget(insp)
        split.addWidget(self._build_plot_area())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        insp.setMinimumWidth(340)
        self._hsplit = split
        body.addWidget(self._build_statusbar())

        for build in (self._page_experiment, self._page_layers,
                      self._page_interfaces, self._page_sweep,
                      self._page_background, self._page_output):
            self.pages.addWidget(self._scroll(build()))
        self.rail.page_changed.connect(self._on_page)
        self.rail.help_clicked.connect(self._help_menu)
        self.rail.select(PAGE_LAYERS)

        # --- follow-up wiring
        for w in ("pump", "probe"):
            for var in self.cfg_vars[f"lambda_{w}_nm"]:
                var.trace_add("write", lambda *_: self.schedule_file_nk())
        for v, u in self.cfg_vars.values():       # sizes and times depend on these
            v.trace_add("write", lambda *_: self.schedule_preview())
            u.trace_add("write", lambda *_: self.schedule_preview())
        self.show_strain.trace_add("write", lambda *_: self.schedule_preview())
        self.view_run.trace_add("write", lambda *_: self._update_bg_result())
        for v in (self.sweep_start, self.sweep_end, self.sweep_step,
                  self.sweep_unit):
            v.trace_add("write", lambda *_: (self.schedule_preview(),
                                             self.schedule_stack()))
        self.next_run_var.trace_add("write", lambda *_: self._on_counter_edit())
        for v in list(self.viewer_range.values()) + [self.viewer_offset]:
            v.trace_add("write", lambda *_: self._viewer_range_typed())
        for k in ("bg_cut", "bg_cut_unit", "bg_use_cut"):
            self.out[k].trace_add("write", lambda *_: self._update_bg_cut_label())
        self.out["bg_enabled"].trace_add("write", lambda *_: self._update_plot_tabs())
        for key, seq in ((self.run_single, "F5"), (self.run_sweep, "F6"),
                         (self.save_session, "Ctrl+S"),
                         (self.open_session, "Ctrl+O")):
            QtGui.QShortcut(QtGui.QKeySequence(seq), self,
                            activated=lambda f=key: f())
        self._update_bg_cut_label()
        self._update_plot_tabs()
        self.draw_drr()
        self.draw_viewer()
        self.load_example()
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(100)

    # =================================================================
    # Window hooks used by Logic
    # =================================================================
    @staticmethod
    def _show(widget, on):
        widget.setVisible(bool(on))

    def _set_running(self, on):
        self._running = on
        self.rail.run.set_state(self.run_mode, on)
        self.act_single.setEnabled(not on)
        self.act_sweep.setEnabled(not on)
        self.act_cancel.setEnabled(on)
        self.cancel_btn.setVisible(on)
        self.status_dot.setStyleSheet(f"color: {T.SIENNA if on else T.TEAL};")

    def _set_progress(self, frac):
        self.pbar.setValue(int(round(1000 * max(0.0, min(1.0, frac)))))

    def _set_status(self, text):
        self.status.setText(text)

    def _busy_cursor(self, on):
        if on:
            QtWidgets.QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QtWidgets.QApplication.processEvents()
        else:
            QtWidgets.QApplication.restoreOverrideCursor()

    def title(self, text):
        self.setWindowTitle(text)
        self._update_crumb()

    def _sweep_page_open(self):
        return self.rail.current == PAGE_SWEEP

    def _run_mode_changed(self):
        self.rail.run.set_state(self.run_mode, getattr(self, "_running", False))

    def _viewer_is_current(self):
        return self.ptabs.currentWidget() is self.viewer_panel

    def _go_to_layer(self, i):
        self.rail.select(PAGE_LAYERS)
        self._select_tree_row(i)
        self.select_layer(i)

    def log(self, text, tag=None):
        """Append to the console. tag: None, 'head', 'warn' or 'err'."""
        fmt = QtGui.QTextCharFormat()
        col = {"warn": "#f2b46d", "err": "#ff8a8a", "head": "#ffffff"}.get(
            tag, T.CONSOLE_FG)
        if tag == "head" and text.lstrip("— ").startswith("done"):
            col = T.TEAL
        fmt.setForeground(QtGui.QColor(col))
        fmt.setFontWeight(QtGui.QFont.Weight.Bold if tag == "head"
                          else QtGui.QFont.Weight.Normal)
        cur = self.log_w.textCursor()
        cur.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cur.insertText(text.rstrip("\n") + "\n", fmt)
        self.log_w.setTextCursor(cur)
        self.log_w.ensureCursorVisible()

    # =================================================================
    # Top bar, status bar, plot area
    # =================================================================
    def _tool(self, text, actions, tip=""):
        b = QtWidgets.QToolButton()
        b.setObjectName("tb")
        b.setText(W.amp(text))
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        m = QtWidgets.QMenu(b)
        for item in actions:
            if item is None:
                m.addSeparator()
            else:                       # call without Qt's 'checked' argument
                m.addAction(item[0], lambda f=item[1]: f())
        b.setMenu(m)
        return b

    def _build_topbar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("topbar")
        bar.setFixedHeight(54)
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(16, 6, 12, 6)
        h.setSpacing(8)
        h.addWidget(W.label(APP_NAME, "brand"))
        sub = W.label(APP_SUB, "brandsub")
        h.addSpacing(4)
        self.crumb = W.label("", "crumb")
        for w in (sub, self.crumb):     # may be cut short; never set a width
            w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                            QtWidgets.QSizePolicy.Policy.Preferred)
            w.setMinimumWidth(0)
        sub.setMinimumWidth(10)
        h.addWidget(sub, 1)
        h.addWidget(self.crumb, 3)
        h.addWidget(self._tool("📂 Open", [
            ("Open session…  (Ctrl+O)", self.open_session),
            ("New session (example stack)", self.new_session)]))
        h.addWidget(self._tool("💾 Save", [
            ("Save session  (Ctrl+S)", self.save_session),
            ("Save session as…", self.save_session_as)]))
        h.addWidget(self._tool("⇪ Import", [
            ("Parameter file (CFG / SAMPLE)…", self.import_params),
            ("fs-sonar conf_file…", self.import_conf)]))
        h.addWidget(self._tool("⤓ Export", [
            ("Selected run (.csv, .npz, .json)…", self.export_run),
            ("Sweep (combined file)…", self.export_sweep)]))
        return bar

    def _build_run_menu(self):
        """The menu of the ▶ Run item in the rail."""
        m = QtWidgets.QMenu(self)
        self.act_single = m.addAction("▶  Run model  (single run)\tF5",
                                      lambda: self.run_single())
        self.act_sweep = m.addAction("▶  Run thickness sweep\tF6",
                                     lambda: self.run_sweep())
        m.addSeparator()
        self.act_cancel = m.addAction("■  Cancel the calculation",
                                      lambda: self.cancel())
        self.act_cancel.setEnabled(False)
        m.addSeparator()
        m.addAction("List literature placeholders…",
                    lambda: self.show_placeholders())
        self.run_menu = m
        run = self.rail.run
        run.clicked.connect(lambda: m.popup(
            run.mapToGlobal(QtCore.QPoint(run.width() - 8, 6))))

    def _build_statusbar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("statusbar")
        bar.setFixedHeight(30)
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(12, 2, 12, 2)
        h.setSpacing(10)
        self.status_dot = QtWidgets.QLabel("●")
        self.status_dot.setStyleSheet(f"color: {T.TEAL};")
        h.addWidget(self.status_dot)
        self.status = W.label("Ready", "statustext")
        self.status.setMinimumWidth(120)
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                  QtWidgets.QSizePolicy.Policy.Preferred)
        self.pbar = QtWidgets.QProgressBar()
        self.pbar.setRange(0, 1000)
        self.pbar.setTextVisible(False)
        self.pbar.setFixedWidth(180)
        h.addWidget(self.pbar)
        h.addWidget(self.status, 1)
        self.cancel_btn = W.button("■ Cancel", self.cancel, "small")
        self.cancel_btn.setVisible(False)
        h.addWidget(self.cancel_btn)
        self.status_right = W.label("", "statusright")
        h.addWidget(self.status_right)
        return bar

    def _update_status_right(self):
        try:
            lam = self._wavelengths()
            dz = U.to_base(float(self.cfg_vars["dz_nm"][0].get()), "grid",
                           self.cfg_vars["dz_nm"][1].get())
            self.status_right.setText(f"λ pump {lam['pump']:.6g} nm · probe "
                                      f"{lam['probe']:.6g} nm · dz {dz:g} nm")
        except (ValueError, ZeroDivisionError, KeyError):
            self.status_right.setText("")

    def _chip(self, text, var, slot=None, tip=""):
        b = QtWidgets.QPushButton(W.amp(text))
        b.setObjectName("chip")
        b.setCheckable(True)
        b.setToolTip(tip)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        W.bind_check(b, var)
        if slot:
            b.clicked.connect(lambda *_: slot())
        return b

    def _card(self, panel):
        panel.setObjectName("plotcard")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        return panel

    def _build_plot_area(self):
        area = QtWidgets.QWidget()
        area.setObjectName("plotarea")
        area.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QtWidgets.QVBoxLayout(area)
        v.setContentsMargins(12, 10, 12, 0)
        v.setSpacing(8)
        # header: which run is shown, overlay, extra plots
        self.run_cb = QtWidgets.QComboBox()
        self.run_cb.setMinimumWidth(260)
        self.run_cb.activated.connect(lambda i: (
            self.view_run.set(self.run_cb.itemText(i)), self.redraw_run_plots()))
        # optional plot tabs: one menu with ticks, to keep the header short
        extra = QtWidgets.QToolButton()
        extra.setObjectName("tb")
        extra.setText("Extra plots ▾")
        extra.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        extra.setToolTip("Show more plot tabs (ΔR/R and the stack are always "
                         "shown)")
        menu = QtWidgets.QMenu(extra)
        for text, var in (("Components (the four contributions)",
                           self.show_components),
                          ("Kernels & absorption", self.show_kernels),
                          ("Strain map η(z,t) — switch on before the run",
                           self.show_strain)):
            a = menu.addAction(W.amp(text))
            a.setCheckable(True)
            a.setChecked(var.get())
            a.toggled.connect(lambda on, v=var: (v.set(on), self._update_plot_tabs()))
            var.trace_add("write", lambda *_, a=a, v=var: a.isChecked() != v.get()
                          and a.setChecked(v.get()))
        extra.setMenu(menu)
        self.run_cb.setMinimumWidth(200)
        v.addWidget(W.flow(
            QtWidgets.QLabel("Show run"), self.run_cb,
            W.hbox(self._chip("Overlay all runs", self.overlay, self.draw_drr,
                              "Show every run in memory on the ΔR/R plot"),
                   W.InfoButton(self, "overlay"), stretch=False),
            W.button("Clear runs", self.clear_runs, "small"), 12,
            W.hbox(extra, W.InfoButton(self, "extra_plots"), stretch=False)))

        vsplit = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        vsplit.setHandleWidth(6)
        v.addWidget(vsplit, 1)
        self.ptabs = QtWidgets.QTabWidget()
        self.ptabs.setDocumentMode(True)
        vsplit.addWidget(self.ptabs)

        # Stack & ΔR/R
        main = QtWidgets.QSplitter(Qt.Orientation.Vertical)
        main.setHandleWidth(6)
        self.stack_panel = self._card(W.PlotPanel(self, figsize=(8, 1.9),
                                                  colors=False))
        self.stack_panel.redraw = self.draw_stack
        # labels are fitted to the blocks at the drawn size: refit on resize
        self.stack_panel.canvas.mpl_connect("resize_event",
                                            lambda e: self.schedule_stack())
        self.stack_panel.controls.addWidget(QtWidgets.QLabel("Widths"))
        cb = W.combo(self.stack_mode, ["Equal widths", "Proportional",
                                       "Log thickness"])
        cb.activated.connect(lambda *_: self.draw_stack())
        self.stack_panel.controls.addWidget(cb)
        self.stack_panel.controls.addWidget(W.button(
            "Colours…", self.edit_stack_colors, "small",
            "Choose the colour of each material in the stack diagram. Layers "
            "of the same material share a colour."))
        self.stack_panel.canvas.mpl_connect("button_press_event",
                                            self._on_stack_click)
        main.addWidget(self.stack_panel)
        self.drr_panel = self._card(W.PlotPanel(self, figsize=(8, 4.5)))
        self.drr_panel.redraw = self.draw_drr
        r = self.drr_panel.row2
        r.addWidget(QtWidgets.QLabel("y scale"))
        cb = W.combo(self.scale_exp, ["0", "3", "4", "5", "6"], width=60)
        cb.activated.connect(lambda *_: (self.draw_drr(), self.redraw_run_plots()))
        r.addWidget(cb)
        r.addSpacing(10)
        r.addWidget(QtWidgets.QLabel("Offset"))
        e = W.line_edit(self.drr_offset, 70)
        e.setToolTip("Shift each curve by this amount (in the plotted units, "
                     "i.e. after the y scale) so overlapping curves can be told "
                     "apart: curve n is moved up by n × offset. 0 = no offset. "
                     "The saved data are never offset.")
        r.addWidget(e)
        r.addSpacing(10)
        r.addWidget(QtWidgets.QLabel("Sweep colours"))
        cb = W.combo(self.sweep_cmap, W.LINE_CMAPS, editable=True, width=130)
        cb.setToolTip("Colour map for the curves of a thickness sweep (thin to "
                      "thick). Any matplotlib colour map; add _r to reverse it.")
        cb.activated.connect(lambda *_: self.set_sweep_cmap(self.sweep_cmap.get()))
        r.addWidget(cb)
        r.addStretch(1)
        main.addWidget(self.drr_panel)
        main.setSizes([190, 520])
        self.ptabs.addTab(main, W.amp("Stack & ΔR/R"))

        self.comp_panel = self._card(W.PlotPanel(self))
        self.comp_panel.redraw = self.draw_components
        self.kern_panel = self._card(W.PlotPanel(self))
        self.kern_panel.redraw = self.draw_kernels
        self.strain_panel = self._card(W.PlotPanel(self))
        self.strain_panel.redraw = self.draw_strain
        self.bg_panel = self._card(W.PlotPanel(self))
        self.bg_panel.redraw = self.draw_background
        self.bg_panel.row2.addWidget(QtWidgets.QLabel("Offset"))
        e = W.line_edit(self.bg_offset, 70)
        e.setToolTip("Shift each thickness of a sweep by this amount (plotted "
                     "units) so the curves can be told apart. 0 = no offset.")
        self.bg_panel.row2.addWidget(e)
        self.bg_panel.row2.addStretch(1)
        self.ptabs.addTab(self.comp_panel, "Components")
        self.ptabs.addTab(self.kern_panel, W.amp("Kernels & absorption"))
        self.ptabs.addTab(self.strain_panel, "Strain map")
        self.ptabs.addTab(self.bg_panel, "Background")
        self._build_viewer()
        for pnl in (self.drr_panel, self.bg_panel, self.viewer_panel):
            pnl.canvas.mpl_connect("button_press_event",
                                   lambda e, q=pnl: self._on_sweep_press(q, e))
            pnl.canvas.mpl_connect("motion_notify_event",
                                   lambda e, q=pnl: self._on_sweep_motion(q, e))
            pnl.canvas.mpl_connect("button_release_event",
                                   lambda e: setattr(self, "_viewer_drag", False))

        # console
        con = QtWidgets.QFrame()
        con.setObjectName("console")
        cl = QtWidgets.QVBoxLayout(con)
        cl.setContentsMargins(14, 8, 14, 6)
        cl.setSpacing(4)
        clear = W.button("Clear", lambda: self.log_w.clear(), "small")
        clear.setStyleSheet("QPushButton { background: transparent; color: #8f89a8;"
                            " border: 1px solid #3a3446; }")
        cl.addWidget(W.hbox(W.label("Console", "consolehead"),
                            W.label("conversions · warnings · diagnostics",
                                    "consolesub"), None, clear, stretch=False))
        self.log_w = QtWidgets.QPlainTextEdit()
        self.log_w.setObjectName("consoletext")
        self.log_w.setReadOnly(True)
        self.log_w.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        cl.addWidget(self.log_w, 1)
        vsplit.addWidget(con)
        vsplit.setSizes([640, 150])
        return area

    def _build_viewer(self):
        vp = self.viewer_panel = self._card(W.PlotPanel(self))
        vp.redraw = self.draw_viewer
        self.ptabs.addTab(vp, "Sweep viewer")
        r = vp.row2
        for lab, var, vals in (("Show", self.viewer_mode, self.VIEWER_MODES),
                               ("Signal", self.viewer_signal, self.VIEWER_SIGNALS)):
            r.addWidget(QtWidgets.QLabel(lab))
            cb = W.combo(var, vals)
            cb.activated.connect(lambda *_: self.draw_viewer())
            r.addWidget(cb)
            r.addSpacing(8)
        r.addWidget(W.InfoButton(self, "sweep_viewer"))
        r.addStretch(1)
        self.play_btn = W.button("▶ Play", self.toggle_play, "accent")
        fps = QtWidgets.QSpinBox()
        fps.setRange(1, 30)
        fps.setFixedWidth(64)
        bind_spin(fps, self.viewer_fps)
        vp.top_rows.addWidget(W.flow(
            W.hbox(self.play_btn, QtWidgets.QLabel("frames/s"), fps,
                   W.check("loop", self.viewer_loop), stretch=False),
            W.button("Show all thicknesses", self._viewer_show_all, "small"),
            W.button("Save GIF…", self.save_gif, "small"),
            margins=(4, 0, 4, 0)))
        # each group stays on one line; the groups wrap when narrow
        rng = [W.hbox(QtWidgets.QLabel("x (delay) from"),
                      W.line_edit(self.viewer_range["x0"], 70), QtWidgets.QLabel("to"),
                      W.line_edit(self.viewer_range["x1"], 70), QtWidgets.QLabel("ps"),
                      stretch=False), 10,
               W.hbox(QtWidgets.QLabel("y from"), W.line_edit(self.viewer_range["y0"], 70),
                      QtWidgets.QLabel("to"), W.line_edit(self.viewer_range["y1"], 70),
                      stretch=False),
               W.button("Auto range", self._viewer_auto_range, "small",
                        "Forget the zoom: show the full delay range and all "
                        "curves"), 10, QtWidgets.QLabel("Offset")]
        off = W.line_edit(self.viewer_offset, 60)
        off.setToolTip("Shift the shown thicknesses apart (plotted units): the "
                       "n-th shown curve is moved up by n × offset. Used when "
                       "other curves are shown (not in 'Current curve only'). "
                       "0 = none.")
        vp.top_rows.addWidget(W.flow(*rng, off, margins=(4, 0, 4, 0)))
        self.viewer_scale = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.viewer_scale.valueChanged.connect(self._on_viewer_slide)
        self.viewer_pos_lbl = W.label("", "note")
        self.viewer_pos_lbl.setMinimumWidth(160)
        self.viewer_pos_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        row.addWidget(self.viewer_scale, 1)
        row.addWidget(self.viewer_pos_lbl)
        vp.bottom_rows.addLayout(row)
        vp.bottom_rows.addWidget(W.label(
            "Drag on the thickness colour bar (or the slider) to step through "
            "the sweep; right-click a colour to hide/show that thickness; ← → "
            "step one thickness (click the plot first).", "note", wrap=True))
        vp.canvas.mpl_connect("key_press_event", self._viewer_key)

    def _viewer_key(self, e):
        if e.key in ("left", "right"):
            self._viewer_step(-1 if e.key == "left" else 1)

    def _viewer_scale_update(self, vis, cur, batch=None):
        self._viewer_setting = True
        try:
            self.viewer_scale.setRange(0, max(1, len(vis) - 1))
            self.viewer_scale.setValue(vis.index(cur) if cur in vis else 0)
        finally:
            self._viewer_setting = False
        if batch is None or not vis:
            self.viewer_pos_lbl.setText("")
            return
        r = batch["runs"][cur]
        self.viewer_pos_lbl.setText(f"{r['sweep_value']:g} {r['sweep_unit']}"
                                    f"  ({vis.index(cur) + 1} of {len(vis)})")

    def _update_plot_tabs(self):
        if not hasattr(self, "bg_panel"):
            return
        for var, panel in ((self.show_components, self.comp_panel),
                           (self.show_kernels, self.kern_panel),
                           (self.show_strain, self.strain_panel),
                           (self.out["bg_enabled"], self.bg_panel)):
            self.ptabs.setTabVisible(self.ptabs.indexOf(panel), var.get())
            if var.get():
                panel.redraw()
        if self.show_strain.get() and self.batches:
            res = self.selected_res()
            if res is not None and res.get("eta_zt") is None:
                self.log("Strain map: the selected run has no η(z,t) recorded; "
                         "run again with 'Strain map' switched on.", "warn")

    def _set_run_list(self, labels, sel):
        self.run_cb.blockSignals(True)
        self.run_cb.clear()
        self.run_cb.addItems(labels)
        if sel:
            self.run_cb.setCurrentText(sel)
        self.run_cb.blockSignals(False)

    def _help_menu(self):
        m = QtWidgets.QMenu(self)
        m.addAction("How to use", lambda: self.show_help())
        m.addAction("Units and conversions", lambda: self.show_units())
        m.addAction("List literature placeholders…",
                    lambda: self.show_placeholders())
        m.addSeparator()
        m.addAction(f"About {APP_NAME}", lambda: messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_LONG}\n\nForward model of picosecond-acoustics pump-probe "
            f"ΔR/R in thin-film stacks.\nPhysics: diffr_model.py · interface: "
            f"pises_qt.py (Qt / PySide6)."))
        m.popup(self.rail.help.mapToGlobal(QtCore.QPoint(self.rail.help.width(), 0)))

    # =================================================================
    # Inspector pages
    # =================================================================
    def _scroll(self, page):
        sa = QtWidgets.QScrollArea()
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sa.setWidget(page)
        return sa

    def _page(self, title, sub=""):
        p = QtWidgets.QWidget()
        p.setObjectName("page")
        p.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        v = QtWidgets.QVBoxLayout(p)
        v.setContentsMargins(0, 12, 0, 16)
        v.setSpacing(0)
        head = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(head)
        hl.setContentsMargins(16, 0, 16, 8)
        hl.setSpacing(2)
        hl.addWidget(W.label(title, "pagehead"))
        if sub:
            hl.addWidget(W.label(sub, "pagesub", wrap=True))
        v.addWidget(head)
        return p, v

    @staticmethod
    def _conv_label():
        """Grey conversion text that may be cut short (full text in the
        tooltip) rather than make the page wider than the inspector."""
        c = W.label("", "conv")
        c.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                        QtWidgets.QSizePolicy.Policy.Preferred)
        c.setMinimumWidth(30)
        return c

    def _text(self, label, text, bad=False):
        label.setText(text)
        if label.objectName() == "conv":
            label.setToolTip(text)
        W.set_bad(label, bad)

    def _lab_info(self, text, key, width=LABEL_W):
        lab = QtWidgets.QLabel(text)
        lab.setWordWrap(True)          # a long name takes two lines, not cut
        w = W.hbox(lab, W.InfoButton(self, key), spacing=2)
        if width:
            w.setFixedWidth(width)
        return w

    def _switch_info(self, text, var, key):
        return W.hbox(W.switch(text, var), W.InfoButton(self, key), spacing=4)

    def _check_info(self, text, var, key):
        return W.hbox(W.check(text, var), W.InfoButton(self, key), spacing=4)

    def _on_page(self, i):
        self.pages.setCurrentIndex(i)
        QtCore.QTimer.singleShot(0, self._fit_inspector)
        self._update_crumb()
        self.schedule_stack()            # the swept layer is marked while planning

    def _update_crumb(self):
        if not hasattr(self, "crumb") or self.rail.current < 0:
            return
        sess = (os.path.splitext(os.path.basename(self.session_path))[0]
                if self.session_path else "untitled session")
        self.crumb.setText(f"{sess}  ›  <b style='color:{T.BERRY}'>"
                           f"{PAGES[self.rail.current][1]}</b>")

    # --- Experiment ----------------------------------------------------
    def _value_row(self, sec, key, label, var, unit_var, kind, hint=""):
        e = W.line_edit(var, 90)
        cb = W.combo(unit_var, U.choices(kind), width=110)
        conv = self._conv_label()

        def upd(*_):
            try:
                v = U.to_base(float(var.get()), kind, unit_var.get())
                self._text(conv, f"= {U.fmt(v)} {U.base_unit(kind)}")
            except (ValueError, ZeroDivisionError, KeyError):
                self._text(conv, "not a number" if var.get().strip() else "",
                           bad=True)
        var.trace_add("write", upd)
        unit_var.trace_add("write", upd)
        upd()
        lab = self._lab_info(label, key)
        if hint:
            lab.setToolTip(hint)
            e.setToolTip(hint)
        sec.add_row(lab, e, cb, conv)
        return e, conv

    def _page_experiment(self):
        p, v = self._page("Experiment", "Laser, time axis, grid and which "
                                        "contributions to ΔR/R are computed. "
                                        "Grey text: the value the model receives "
                                        "(nm, ps, SI).")
        info = {f[0]: f for f in S.CFG_FIELDS}
        for title, keys in (("Laser excitation", CFG_GROUP_LASER),
                            ("Time axis", CFG_GROUP_TIME),
                            ("Spatial grid & numerics", CFG_GROUP_GRID)):
            sec = W.Section(title)
            for key in keys:
                _, label, kind, val, unit, hint = info[key]
                var, uvar = StringVar(val), StringVar(unit)
                self.cfg_vars[key] = (var, uvar)
                self._value_row(sec, key, label, var, uvar, kind, hint)
            v.addWidget(sec)
        sec = W.Section("Contributions to ΔR/R")
        for k, lab in S.PHYSICS_FLAGS:
            sec.add_full(self._switch_info(lab, self.flag_vars[k], k))
        v.addWidget(sec)
        sec = W.Section("Acoustic loss")
        sec.add_full(self._switch_info(S.OPTION_FLAGS[0][1],
                                       self.flag_vars["include_loss"],
                                       "include_loss"))
        v.addWidget(sec)
        v.addStretch(1)
        return p

    # --- Layers --------------------------------------------------------
    def _layer_var(self, key):
        var = StringVar("")
        var.trace_add("write", lambda *_: self._on_layer_var(key))
        self.lv[key] = var
        return var

    def _page_layers(self):
        p, v = self._page("Stack", "Light enters through layer 1 (top of the "
                                   "list); the last layer is the semi-infinite "
                                   "substrate. Click a layer to edit it.")
        top = QtWidgets.QWidget()
        tl = QtWidgets.QVBoxLayout(top)
        tl.setContentsMargins(16, 0, 16, 10)
        tl.setSpacing(6)
        self.mini_stack = W.MiniStack()
        self.mini_stack.picked.connect(self._go_to_layer)
        tl.addWidget(self.mini_stack)
        btns = [W.button(t, f, "small", tip) for t, f, tip in (
            ("＋ Add", self.add_layer, "Add a layer below the selected one"),
            ("Duplicate", self.duplicate_layer, "Copy the selected layer"),
            ("Delete", self.delete_layer, "Delete the selected layer"),
            ("↑", lambda: self.move_layer(-1), "Move the layer up"),
            ("↓", lambda: self.move_layer(1), "Move the layer down"),
            ("Literature…", self.show_placeholders,
             "List every value that is still a literature pre-fill, for all "
             "layers. Double-click a layer to list only its values."))]
        tl.addWidget(W.hbox(*btns, spacing=4))
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["#", "Material", "Thickness", "n,k source",
                                   "Literature"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMaximumHeight(200)
        for c, w_ in enumerate(self.TREE_MIN_W):
            self.tree.setColumnWidth(c, w_)
        self.tree.currentItemChanged.connect(self._on_tree_select)
        self.tree.itemDoubleClicked.connect(
            lambda it, _c: self.show_placeholders(self.tree.indexOfTopLevelItem(it)))
        tl.addWidget(self.tree)
        v.addWidget(top)
        self.editor_title = W.label("", "pagehead")
        v.addWidget(W.hbox(self.editor_title, margins=(16, 6, 16, 4)))
        self._build_layer_editor(v)
        v.addStretch(1)
        return p

    def _build_layer_editor(self, v):
        self.lv, self.conv_upd, self.lit_marks = {}, {}, {}
        for key in S.new_layer():
            if key != "lit" and not key.startswith("grad_"):
                self._layer_var(key)
        lv = self.lv

        # --- layer
        sec = W.Section("Layer")
        self.mat_cb = W.combo(lv["material"], S.literature_names(), editable=True)
        self.mat_cb.setMinimumWidth(150)       # long material names stay readable
        self.mat_cb.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                  QtWidgets.QSizePolicy.Policy.Fixed)
        self.mat_cb.textActivated.connect(lambda *_: self.fill_literature(ask=True))
        fill_btn = W.button("Fill from literature",
                             lambda: self.fill_literature(ask=True), "small",
                             "Pre-fill the properties from the LITERATURE table "
                             "in diffr_model.py (Si3N4, SiO2, Ti, Si, Al, Au). "
                             "Values still equal to the pre-fill are reported "
                             "as literature placeholders when you run.")
        # name box and button share the value columns, so a wide name box
        # does not widen the columns of the rows below
        name_row = QtWidgets.QWidget()
        nh = QtWidgets.QHBoxLayout(name_row)
        nh.setContentsMargins(0, 0, 0, 0)
        nh.addWidget(self.mat_cb, 1)
        nh.addWidget(fill_btn)
        sec.add_row(self._lab_info("Material name", "material"), name_row)
        self.d_entry = W.line_edit(lv["d"], 90)
        self.d_unit_cb = W.combo(lv["d_unit"], U.choices("thickness"), width=110)
        self.d_conv = self._conv_label()

        def upd_d(*_):
            if self.sel is not None and self.sel == len(self.layers) - 1:
                self._text(self.d_conv, "")
                return
            try:
                d = U.to_base(float(lv["d"].get()), "thickness", lv["d_unit"].get())
                self._text(self.d_conv, f"= {U.fmt(d)} nm")
            except (ValueError, KeyError):
                self._text(self.d_conv, "not a number" if lv["d"].get().strip()
                           else "", bad=True)
        self.conv_upd["d"] = upd_d
        sec.add_row(self._lab_info("Thickness", "d"), self.d_entry,
                    self.d_unit_cb, self.d_conv)
        self.sub_note = W.label("", "note", wrap=True)
        sec.add_full(self.sub_note)
        v.addWidget(sec)

        # --- optical constants
        sec = W.Section("Optical constants  ñ = n + ik")
        sec.add_full(W.hbox(W.Segmented(lv["nk_mode"], [("typed", "Type n, k"),
                                                         ("file", "Dispersion file")]),
                            W.InfoButton(self, "nk_mode")))
        self.file_f = QtWidgets.QWidget()
        fg = QtWidgets.QGridLayout(self.file_f)
        fg.setContentsMargins(0, 0, 0, 0)
        fg.addWidget(self._lab_info("File", "nk_file", 80), 0, 0)
        fg.addWidget(W.line_edit(lv["nk_file"]), 0, 1, 1, 2)
        fg.addWidget(W.button("Browse…", self.browse_nk, "small"), 0, 3)
        fg.addWidget(self._lab_info("Column 1", "nk_file_unit", 80), 1, 0)
        ucb = W.combo(lv["nk_file_unit"], S.FILE_UNITS, width=90)
        ucb.setToolTip("Unit of the first column (wavelength or photon energy). "
                       "'auto' reads a '# unit: ...' line in the file, otherwise "
                       "guesses (values < 100 -> µm, > 1500 -> Å) and says so.")
        fg.addWidget(ucb, 1, 1)
        fg.addWidget(W.button("Reload file", self._refresh_file_nk, "small",
                              "Read the file again (e.g. after editing it)."), 1, 2)
        self.kfill_f = QtWidgets.QWidget()
        kg = QtWidgets.QHBoxLayout(self.kfill_f)
        kg.setContentsMargins(0, 0, 0, 0)
        kg.addWidget(self._lab_info("No k column; k at", "kfill", None))
        for w in ("pump", "probe"):
            kg.addWidget(QtWidgets.QLabel(w))
            kg.addWidget(W.line_edit(lv[f"kfill_{w}"], 70))
            self.lit_marks[f"kfill_{w}"] = W.label("", "lit")
            kg.addWidget(self.lit_marks[f"kfill_{w}"])
        kg.addStretch(1)
        fg.addWidget(self.kfill_f, 2, 0, 1, 4)
        self.file_info = W.label("", "hint", wrap=True)
        fg.addWidget(self.file_info, 3, 0, 1, 4)
        sec.add_full(self.file_f)
        self.nk_head = W.label("", "note", wrap=True)
        sec.add_full(self.nk_head)
        nk = QtWidgets.QWidget()
        ng = QtWidgets.QGridLayout(nk)
        ng.setContentsMargins(0, 0, 0, 0)
        ng.addWidget(W.label("n", "note"), 0, 1)
        ng.addWidget(W.label("k", "note"), 0, 2)
        self.nk_entries, self.nk_row_labels = [], {}
        for r, w in enumerate(("pump", "probe"), 1):
            self.nk_row_labels[w] = QtWidgets.QLabel(f"{w} λ")
            self.nk_row_labels[w].setFixedWidth(LABEL_W)
            ng.addWidget(self.nk_row_labels[w], r, 0)
            for c, q in ((1, "n"), (2, "k")):
                en = W.line_edit(lv[f"{q}_{w}"], 90)
                ng.addWidget(en, r, c)
                self.nk_entries.append(en)
            self.lit_marks[f"k_{w}"] = W.label("", "lit")
            ng.addWidget(self.lit_marks[f"k_{w}"], r, 3)
        ng.setColumnStretch(4, 1)
        sec.add_full(nk)
        v.addWidget(sec)

        # --- mechanical & thermal
        sec = W.Section("Mechanical & thermal")
        for key in ("rho", "v", "Cp", "alpha_lin", "B_bulk"):
            sec.add_full(self._prop_row(key))
        v.addWidget(sec)

        # --- optical coupling
        sec = W.Section("Optical coupling  (complex: Re + i·Im)")
        sec.add_full(W.hbox(QtWidgets.QLabel("Photoelastic input"),
                            W.Segmented(lv["pe_mode"], [("dn_deta", "dñ/dη"),
                                                        ("pe", "coefficient p")]),
                            W.InfoButton(self, "pe_mode")))
        self.pe_rows = {"dn_deta": self._prop_row("dn_deta"),
                        "pe": self._prop_row("pe")}
        sec.add_full(self.pe_rows["dn_deta"])
        sec.add_full(self.pe_rows["pe"])
        sec.add_full(self._prop_row("dn_dT"))
        v.addWidget(sec)

        # --- electronic
        sec = W.Section("Electronic (metals) — optional", expanded=False)
        sec.add_full(W.label("Blank Ce = no electron channel.", "note"))
        for key in ("Ce", "tau_ep_ps", "dn_dTe"):
            sec.add_full(self._prop_row(key))
        v.addWidget(sec)

        # --- acoustic loss
        sec = W.Section("Acoustic loss  — optional (α ∝ f²)")
        sec.add_full(W.hbox(W.Segmented(lv["loss_mode"], [
            ("none", "None"), ("viscosity", "Viscosity η"),
            ("attenuation", "Attenuation α")]), W.InfoButton(self, "loss_mode")))
        self.loss_rows = {
            "viscosity": W.hbox(self._lab_info("η", "visc", 60),
                                W.line_edit(lv["visc"], 90),
                                W.combo(lv["visc_unit"], U.choices("viscosity"),
                                        width=90)),
            "attenuation": W.hbox(self._lab_info("α", "att", 60),
                                  W.line_edit(lv["att"], 80),
                                  W.combo(lv["att_unit"], U.choices("attenuation"),
                                          width=90),
                                  QtWidgets.QLabel("at"),
                                  W.line_edit(lv["att_f"], 60),
                                  W.combo(lv["att_f_unit"], U.choices("frequency"),
                                          width=70))}
        for row in self.loss_rows.values():
            sec.add_full(row)
        self.loss_conv = W.label("", "hint", wrap=True)
        sec.add_full(self.loss_conv)
        v.addWidget(sec)

    def _prop_row(self, key):
        """One material property: label (ⓘ) | value (Re, Im) | unit |
        conversion | literature mark. Returns the row widget."""
        _, label, kind, _, required, default = S.PROP_INFO[key]
        cplx = key in {p[0] for p in S.COMPLEX_PROPS}
        lab = self._lab_info(label + ("" if required else " (opt.)"), key)
        lab.setToolTip(PROP_HELP.get(key, ""))
        parts = [lab]
        if cplx:
            parts += [W.line_edit(self.lv[key + "_re"], 64), QtWidgets.QLabel("+i·"),
                      W.line_edit(self.lv[key + "_im"], 64)]
        else:
            parts.append(W.line_edit(self.lv[key], 90))
        parts.append(W.combo(self.lv[key + "_unit"], U.choices(kind),
                              width=112 if cplx else 150))
        mark = W.label("", "lit")
        self.lit_marks[key] = mark
        parts.append(mark)
        # the conversion sits on its own line under the value, so long
        # texts never squeeze or overlap the literature mark
        conv = self._conv_label()
        row = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(row)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(W.hbox(*parts, spacing=6))
        col.addWidget(W.hbox(LABEL_W + 6, conv, spacing=0, stretch=False))

        def upd():
            unit = self.lv[key + "_unit"].get()
            try:
                if cplx:
                    re_s = self.lv[key + "_re"].get().strip()
                    im_s = self.lv[key + "_im"].get().strip()
                    if not re_s and not im_s:
                        raise LookupError
                    x = complex(float(re_s or 0), float(im_s or 0))
                else:
                    s = self.lv[key].get().strip()
                    if not s:
                        raise LookupError
                    x = float(s)
                val = U.to_base(x, kind, unit)
                self._text(conv, f"= {U.fmt(val)} {U.base_unit(kind)}")
            except LookupError:
                self._text(conv, "" if required else
                           f"blank → {U.fmt(default)} (default)")
            except (ValueError, KeyError):
                self._text(conv, "not a number", bad=True)
        self.conv_upd[key] = upd
        return row

    def _select_tree_row(self, i):
        self.tree.blockSignals(True)
        it = self.tree.topLevelItem(i)
        if it is not None:
            self.tree.setCurrentItem(it)
            self.tree.scrollToItem(it)
        self.tree.blockSignals(False)

    def _on_tree_select(self, cur, _prev):
        if cur is None:
            return
        i = self.tree.indexOfTopLevelItem(cur)
        if i != self.sel and 0 <= i < len(self.layers):
            self.select_layer(i)

    def _refresh_tree_row(self, i):
        it = self.tree.topLevelItem(i)
        if it is not None:
            for c, t in enumerate(self._tree_values(i)):
                it.setText(c, t)
                it.setToolTip(c, t)
            self._fit_tree_columns()
            if i == self.sel:
                self._text(self.editor_title, f"Layer {i + 1} · "
                           f"{self.layers[i]['material'].strip() or 'unnamed'}")
        self._update_mini_stack()

    TREE_MIN_W = (30, 90, 100, 100, 70)

    def _fit_tree_columns(self):
        """Columns as wide as their longest entry (never narrower than the
        defaults), so names and thicknesses are never cut short."""
        for c, w_ in enumerate(self.TREE_MIN_W):
            self.tree.resizeColumnToContents(c)
            if self.tree.columnWidth(c) < w_:
                self.tree.setColumnWidth(c, w_)

    def refresh_tree(self, select=None):
        self.tree.blockSignals(True)
        self.tree.clear()
        for i in range(len(self.layers)):
            it = QtWidgets.QTreeWidgetItem(list(self._tree_values(i)))
            it.setForeground(4, QtGui.QColor(T.LIT_C))
            for c in range(it.columnCount()):
                it.setToolTip(c, it.text(c))
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        self._fit_tree_columns()
        if self.layers:
            sel = min(select if select is not None else (self.sel or 0),
                      len(self.layers) - 1)
            self._select_tree_row(sel)
            self.select_layer(sel)
        else:
            self.sel = None
        self.schedule_stack()
        self._refresh_sweep_layers()
        self._refresh_interfaces()

    # --- Interfaces ----------------------------------------------------
    def _page_interfaces(self):
        p, v = self._page("Interfaces", "Layers meet at sharp interfaces unless "
                                        "you grade them here.")
        sec = W.Section("Graded interfaces")
        sec.add_full(self._switch_info("Graded interfaces (layers merge "
                                       "gradually)",
                                       self.flag_vars["graded_interfaces"],
                                       "graded_interfaces"))
        sec.add_full(W.label(
            "Switch an interface on and give the width of the graded region. "
            "Every property (ρ, C11, Cp, expansion, n, k, photoelastic and "
            "thermo-optic coefficients, loss) changes from one layer to the "
            "next across that width, centred on the nominal interface, so the "
            "layer thicknesses are unchanged. Linear: a straight ramp. erf: a "
            "smooth step (like interdiffusion or roughness); the width is the "
            "10–90 % distance. A width may reach at most through a whole "
            "neighbouring layer (≤ 2 × its thickness). Width 0 = sharp (the "
            "default).", "hint", wrap=True))
        v.addWidget(sec)
        self.iface_sec = W.Section("Interfaces (top to bottom)")
        v.addWidget(self.iface_sec)
        self.flag_vars["graded_interfaces"].trace_add(
            "write", lambda *_: (self._refresh_interfaces(), self.schedule_stack()))
        self._iface_vars = []
        v.addStretch(1)
        return p

    def _refresh_interfaces(self):
        """One row per interface between consecutive layers; the values are
        kept in the upper layer's dict (grad_on, grad_w, grad_w_unit,
        grad_shape), so they move with the layers."""
        if not hasattr(self, "iface_sec"):
            return
        g = self.iface_sec.grid
        while g.count():
            w = g.takeAt(0).widget()
            if w is not None:
                w.hide(); w.deleteLater()
        self._iface_vars = []
        on = self.flag_vars["graded_interfaces"].get()
        if len(self.layers) < 2:
            g.addWidget(QtWidgets.QLabel("Add at least two layers."), 0, 0)
            return
        for c, h in enumerate(("Interface", "Graded", "Width", "", "Profile",
                               "Limit / note")):
            g.addWidget(W.label(h, "note", bold=True), 0, c)
        ds = [S.parse_thickness(l) for l in self.layers[:-1]] + [None]
        for i, lay in enumerate(self.layers[:-1]):
            for k, val in S.GRAD_DEFAULTS.items():
                lay.setdefault(k, val)
            below = self.layers[i + 1]
            name = lambda l: l["material"].strip() or "?"
            r = i + 1
            g.addWidget(QtWidgets.QLabel(
                f"{i + 1}|{i + 2}  {name(lay)} | {name(below)}"
                + ("  (substrate)" if i + 1 == len(self.layers) - 1 else "")), r, 0)
            v_on, v_w = BooleanVar(bool(lay["grad_on"])), StringVar(lay["grad_w"])
            v_u, v_s = StringVar(lay["grad_w_unit"]), StringVar(lay["grad_shape"])
            sw = W.switch("", v_on)
            e = W.line_edit(v_w, 60)
            cu = W.combo(v_u, ["nm", "Å", "µm"], width=60)
            cs = W.combo(v_s, S.GRAD_SHAPES, width=80)
            for w in (sw, e, cu, cs):
                w.setEnabled(on)
            note = W.label("", "conv")
            for c, w in enumerate((sw, e, cu, cs, note), 1):
                g.addWidget(w, r, c)
            lim = [d for d in (ds[i], ds[i + 1]) if d]
            limit = 2 * min(lim) if lim else None

            def upd(*_, lay=lay, v_on=v_on, v_w=v_w, v_u=v_u, v_s=v_s,
                    note=note, limit=limit):
                lay.update(grad_on=v_on.get(), grad_w=v_w.get(),
                           grad_w_unit=v_u.get(), grad_shape=v_s.get())
                txt, bad = (f"≤ {limit:g} nm" if limit else ""), False
                try:
                    w_ = U.to_base(float(v_w.get() or 0), "thickness", v_u.get())
                    if w_ == 0:
                        txt = "0 = sharp; " + txt
                    elif w_ < 0:
                        txt, bad = "width must be ≥ 0", True
                    elif limit and w_ > limit + 1e-9:
                        txt, bad = f"too wide: at most {limit:g} nm here", True
                    elif v_u.get() != "nm":
                        txt = f"= {U.fmt(w_)} nm; " + txt
                except ValueError:
                    txt, bad = "not a number", True
                active = on and v_on.get()
                self._text(note, txt if active else
                           (f"≤ {limit:g} nm" if limit else ""), bad=bad and active)
                self.schedule_stack()
            for var in (v_on, v_w, v_u, v_s):
                var.trace_add("write", upd)
            self._iface_vars.append((v_on, v_w, v_u, v_s))
            upd()
        g.setColumnStretch(5, 1)

    # --- Sweep ----------------------------------------------------------
    def _page_sweep(self):
        p, v = self._page("Thickness sweep", "Vary the thickness of one layer, "
                                             "run the model for each value with "
                                             "everything else unchanged, and "
                                             "overlay the curves. What is saved "
                                             "is set on the Output page.")
        sec = W.Section("Thickness range")
        self.sweep_cb = QtWidgets.QComboBox()
        self.sweep_cb.activated.connect(self._on_sweep_choice)
        sec.add_row(self._lab_info("Layer", "sweep_layer", 90), self.sweep_cb)
        grid = QtWidgets.QWidget()
        gl = QtWidgets.QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        for c, (lab, var) in enumerate((("Start", self.sweep_start),
                                        ("End", self.sweep_end),
                                        ("Increment", self.sweep_step)), 1):
            gl.addWidget(W.label(lab, "note"), 0, c)
            gl.addWidget(W.line_edit(var, 80), 1, c)
        gl.addWidget(self._lab_info("Thickness", "sweep_range", 110), 1, 0)
        gl.addWidget(W.combo(self.sweep_unit, U.choices("thickness"), width=70), 1, 4)
        gl.setColumnStretch(5, 1)
        sec.add_full(grid)
        self.sweep_preview = W.label("", "hint", wrap=True)
        sec.add_full(self.sweep_preview)
        v.addWidget(sec)
        sec = W.Section("What this sweep will produce")
        self.sweep_summary = W.label("", "hint", wrap=True)
        self.sweep_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        sec.add_full(self.sweep_summary)
        sec.add_full(W.hbox(W.button("Output settings…",
                                     lambda: self.rail.select(PAGE_OUTPUT), "small")))
        v.addWidget(sec)
        hint = W.label("Start the sweep with ▶ in the left bar → Run thickness "
                       "sweep (or F6). The stack diagram then shows the sweep on "
                       "every page.", "hint", wrap=True)
        hint.setContentsMargins(16, 12, 16, 0)
        v.addWidget(hint)
        v.addStretch(1)
        return p

    def _refresh_sweep_layers(self):
        cands = self.layers[:-1]
        vals = [self._sweep_choice_text(i) for i in range(len(cands))]
        i = self.sweep_index()
        if i is None and cands:            # new stack, or the layer was deleted
            i = 0
            self._sweep_obj = cands[0]
        self.sweep_cb.blockSignals(True)
        self.sweep_cb.clear()
        self.sweep_cb.addItems(vals)
        if cands:
            self.sweep_cb.setCurrentIndex(i)
        self.sweep_cb.blockSignals(False)
        self.sweep_layer.set(vals[i] if cands else "")
        self.schedule_preview()
        self.schedule_stack()

    def _on_sweep_choice(self, k=None):
        k = self.sweep_cb.currentIndex() if k is None else k
        if 0 <= k < len(self.layers) - 1:
            self._sweep_obj = self.layers[k]
        self._refresh_sweep_layers()

    # --- Background -----------------------------------------------------
    def _page_background(self):
        p, v = self._page("Background", "Fit a double-exponential background "
                                        "f(t) = a·e^(b·t) + c·e^(d·t) to each "
                                        "ΔR/R trace and subtract it (t in ps, so "
                                        "b and d are in 1/ps). The result is an "
                                        "extra column next to ΔR/R in the file.")
        ov = self.out
        if B is None:
            v.addWidget(W.hbox(W.label(f"Background subtraction is not "
                                       f"available: {BG_IMPORT_ERROR}", "hint",
                                       wrap=True), margins=(16, 0, 16, 0)))
            v.addStretch(1)
            return p
        sec = W.Section("Subtraction")
        sw = W.switch("Subtract the background from every run", ov["bg_enabled"])
        sw.clicked.connect(lambda *_: self._on_bg_toggle())
        sec.add_full(W.hbox(sw, W.InfoButton(self, "bg_enabled")))
        self.bg_cut_conv = W.label("", "conv")
        sec.add_full(W.hbox(self._check_info("Fit only delays after",
                                             ov["bg_use_cut"], "bg_use_cut"),
                            W.line_edit(ov["bg_cut"], 70),
                            W.combo(ov["bg_cut_unit"], U.choices("time"), width=60),
                            self.bg_cut_conv))
        sec.add_full(W.label("(off: fit the whole time axis, including negative "
                             "delays)", "note"))
        sec.add_full(self._check_info("Force decaying exponentials (b ≤ 0, d ≤ 0)",
                                      ov["bg_force_decay"], "bg_force_decay"))
        sec.add_row(self._lab_info("Before the cut-off, the subtracted column "
                                   "holds", "bg_before", None),
                    W.combo(ov["bg_before"], list(B.BEFORE_CHOICES)))
        v.addWidget(sec)
        sec = W.Section("In the ΔR/R file and the plots")
        sec.add_full(self._lab_info("Extra columns next to dR_over_R", "bg_cols",
                                    None))
        sec.add_full(W.check("dR_over_R_minus_bg (ΔR/R − background)",
                             ov["bg_col_sub"]))
        sec.add_full(W.check("bg_fit (the fitted background)", ov["bg_col_fit"]))
        sec.add_full(W.label("The NPZ archive always holds drr_minus_bg, bg_fit "
                             "and bg_params; the metadata JSON holds a, b, c, d "
                             "and the settings used. A combined sweep file gets "
                             "one dR_over_R_minus_bg column per thickness.",
                             "note", wrap=True))
        cb = W.combo(ov["bg_plot_main"], ["original ΔR/R", "background-subtracted",
                                          "both"])
        cb.activated.connect(lambda *_: self.draw_drr())
        sec.add_row(self._lab_info("Main ΔR/R plot shows", "bg_plot_main"), cb)
        v.addWidget(sec)
        sec = W.Section("Result")
        self.bg_result = W.label("No run yet.", "hint", wrap=True)
        self.bg_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        sec.add_full(self.bg_result)
        sec.add_full(W.hbox(
            W.button("Fit selected run now",
                     lambda: self.refit_background(all_runs=False), "small"),
            W.button("Re-fit all runs", lambda: self.refit_background(all_runs=True),
                     "small")))
        sec.add_full(W.label("New runs are fitted automatically while 'Subtract' "
                             "is on. After changing settings, re-fit here; files "
                             "already saved are not rewritten (use Export to "
                             "write them again). Fitting engine: "
                             + B.FIT_ENGINE, "note", wrap=True))
        v.addWidget(sec)
        v.addStretch(1)
        return p

    # --- Output -----------------------------------------------------------
    def _page_output(self):
        p, v = self._page("Output", "Everything about saving; all of it is kept "
                                    "in the session.")
        ov = self.out
        sec = W.Section("Where")
        sec.add_row(self._lab_info("Folder", "out_folder", 80),
                    W.line_edit(ov["folder"]),
                    W.button("Browse…", self._browse_out_dir, "small"))
        v.addWidget(sec)
        sec = W.Section("When to save automatically, and file names")
        for flag, name, text, fields in (
                ("auto_single", "single_name", "After every single run (F5)",
                 O.RUN_FIELDS),
                ("auto_sweep", "sweep_name", "After every run of a sweep",
                 O.SWEEP_FIELDS),
                ("combined", "combined_name", "One combined file per sweep (all "
                 "thicknesses side by side)", O.COMBINED_FIELDS)):
            sec.add_full(self._check_info(text, ov[flag], "out_auto"))
            sec.add_row(W.label("name", "note"), W.line_edit(ov[name]))
            sec.add_row(None, W.label("fields: " + " ".join("{%s}" % f for f in fields),
                                      "note", wrap=True))
        sec.add_full(W.label(
            "{run} run/sweep number · {date} YYYYMMDD · {time} HHMMSS · "
            "{material} swept layer · {layer} its position · {d} thickness · "
            "{unit} thickness unit · {i} run within the sweep · {n} number of "
            "runs · {start} {end} {step} the range. Extensions are added "
            "automatically.", "note", wrap=True))
        v.addWidget(sec)
        sec = W.Section("What to save")
        sec.add_full(self._check_info("CSV table (opens in Excel, Origin, …)",
                                      ov["csv"], "out_csv"))
        sec.add_full(W.label("columns: time, ΔR/R, plus", "note"))
        sec.add_full(W.hbox(*[W.check(lab, ov[opt]) for opt, lab in (
                                ("comp_strain", "strain"),
                                ("comp_disp", "displacement"),
                                ("comp_lattice", "lattice T"),
                                ("comp_electron", "electron T"))],
                            margins=(18, 0, 0, 0)))
        sec.add_full(W.hbox(QtWidgets.QLabel("time unit"),
                            W.combo(ov["csv_time_unit"], list(O.TIME_UNITS), width=60),
                            QtWidgets.QLabel("separator"),
                            W.combo(ov["csv_delimiter"], list(O.DELIMITERS),
                                    width=100),
                            margins=(18, 0, 0, 0)))
        sec.add_full(W.hbox(QtWidgets.QLabel("format"),
                            W.line_edit(ov["csv_format"], 70),
                            W.check("header row", ov["csv_header"]),
                            margins=(18, 0, 0, 0)))
        sec.add_full(self._check_info("NPZ archive (all arrays + metadata, for "
                                      "Python)", ov["npz"], "out_npz"))
        sec.add_full(W.hbox(W.check("depth profiles", ov["npz_profiles"]),
                            W.check("strain map η(z,t), if recorded",
                                    ov["npz_strain"]), margins=(18, 0, 0, 0)))
        sec.add_full(self._check_info("Metadata JSON (inputs, converted values, "
                                      "placeholders, timing)",
                                      ov["meta_json"], "out_meta"))
        sec.add_full(self._check_info("Figures", ov["figures"], "out_figures"))
        sec.add_full(W.hbox(W.check("ΔR/R", ov["fig_drr"]),
                            W.check("stack diagram", ov["fig_stack"]),
                            W.check("extra plots that are shown", ov["fig_extra"]),
                            margins=(18, 0, 0, 0)))
        sec.add_full(W.hbox(QtWidgets.QLabel("format"),
                            W.combo(ov["fig_format"], O.FIG_FORMATS, width=70),
                            QtWidgets.QLabel("dpi"), W.line_edit(ov["fig_dpi"], 60),
                            margins=(18, 0, 0, 0)))
        sec.add_full(W.label("Figures are saved as they look on screen, with your "
                             "labels and colours. For a sweep, the ΔR/R figure "
                             "holds all thicknesses and takes the combined-file "
                             "name. Export uses these same choices.", "note",
                             wrap=True))
        v.addWidget(sec)
        sec = W.Section("Run / sweep number  ({run} in the names)")
        spin = QtWidgets.QSpinBox()
        spin.setRange(1, 999999)
        spin.setFixedWidth(90)
        spin.setToolTip("Single runs and sweeps share one counter. Choose what "
                        "the next run or sweep is called.")
        bind_spin(spin, self.next_run_var)
        self.counter_msg = W.label("", "conv")
        sec.add_row(self._lab_info("Next number", "out_counter", 110), spin,
                    W.button("Reset to 1…", self.reset_counter, "small"),
                    self.counter_msg)
        sec.add_full(self._check_info("Ask before overwriting existing files",
                                      ov["ask_overwrite"], "out_overwrite"))
        sec.add_full(W.label("Set the number back (e.g. to 1) to write again under "
                             "the same names as before. With the box above "
                             "ticked you are asked first; otherwise files are "
                             "overwritten without asking (the console still "
                             "lists them).", "note", wrap=True))
        v.addWidget(sec)
        sec = W.Section("Preview")
        self.out_preview = W.label("", "hint", wrap=True)
        self.out_preview.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        sec.add_full(self.out_preview)
        v.addWidget(sec)
        v.addStretch(1)
        return p

    # =================================================================
    # Closing
    # =================================================================
    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_sized", False):     # first show: inspector width
            self._sized = True
            w = min(580, max(420, int(self.width() * 0.36)))
            self._hsplit.setSizes([w, max(400, self.width() - 88 - w)])
            QtCore.QTimer.singleShot(0, self._fit_inspector)

    def _fit_inspector(self):
        """Widen the inspector when the page on show needs more room than it
        has (a page's width is only known once it has been shown), up to
        45 % of the window. Called on every page change, so rows added to a
        page later can never be cut off."""
        sa = self.pages.currentWidget()
        if sa is None or sa.widget() is None:
            return
        need = (sa.widget().minimumSizeHint().width()
                + sa.verticalScrollBar().sizeHint().width() + 2 * sa.frameWidth() + 2)
        a, b = self._hsplit.sizes()
        if a < need:
            new = min(need, int(self.width() * 0.45))
            if new > a:
                self._hsplit.setSizes([new, max(300, a + b - new)])

    def closeEvent(self, e):
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno("Quit", "A calculation is running. Quit "
                                               "anyway?"):
                e.ignore()
                return
            self.cancel_ev.set()
        self._stop_play()
        e.accept()


def main(argv=None):
    if sys.platform == "win32":                 # own taskbar entry and icon
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "PiSES.PicoSonarEchoStacks")
        except Exception:
            pass
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("pises")
    app.setWindowIcon(T.app_icon())
    T.ensure_assets()
    T.matplotlib_defaults()
    app.setStyleSheet(T.STYLE)
    try:
        win = App()
    except (AttributeError, ImportError, NameError, TypeError) as e:
        # almost always: the program files come from different versions
        # (e.g. a new pises_qt.py next to an old qt_widgets.py)
        here = os.path.dirname(os.path.abspath(__file__))
        msg = (f"PiSES could not start: {type(e).__name__}: {e}\n\n"
               f"This usually means the program files in\n{here}\n"
               f"come from different versions. Replace ALL of them together "
               f"(every .py file, plus assets/), not only some.")
        print(msg, file=sys.stderr)
        messagebox.showerror("PiSES — files out of step", msg)
        raise
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
