#!/usr/bin/env python3
"""Interactive GUI for the diffR thin-film pump-probe model.

    python diffr_gui.py

Every input is typed with a unit chosen from a menu; the grey text next to
each field shows the value converted to the model's internal units (nm, ps,
SI), which is what the solver in diffr_model.py receives.

Needs numpy and matplotlib; tkinter ships with Python.
"""

import copy
import json
import os
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib import colormaps
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)

import diffr_model as M
import sample_input as S
import units as U

# Categorical series colours, assigned in fixed order (never cycled for data
# series; the stack diagram reuses them per material).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK2, GRID_C, LIT_C = "#0b0b0b", "#52514e", "#d9d8d4", "#b35900"

matplotlib.rcParams.update({
    "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "grid.color": GRID_C,
    "axes.titlesize": 10, "legend.frameon": False, "lines.linewidth": 1.4,
})

_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def drr_label(exp):
    return "ΔR/R" if exp == 0 else f"ΔR/R × 10{str(exp).translate(_SUP)}"


# ---------------------------------------------------------------------------
# Small widgets
# ---------------------------------------------------------------------------

class Tooltip:
    """Hover text for a widget."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip = widget, text, None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", background="#ffffe8",
                 relief="solid", borderwidth=1, wraplength=380,
                 padx=6, pady=3).pack()

    def hide(self, _=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ScrollableFrame(ttk.Frame):
    """A frame with a vertical scrollbar; put widgets in .inner."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            win, width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _):
        self.bind_all("<MouseWheel>", self._wheel)
        self.bind_all("<Button-4>", self._wheel)
        self.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _):
        for s in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.unbind_all(s)

    def _wheel(self, e):
        if getattr(e, "num", None) == 4:
            step = -1
        elif getattr(e, "num", None) == 5:
            step = 1
        else:
            step = -1 if e.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")


def section(parent, text, row):
    lf = ttk.LabelFrame(parent, text=text, padding=(8, 4))
    lf.grid(row=row, column=0, sticky="ew", padx=6, pady=4)
    parent.columnconfigure(0, weight=1)
    return lf


# ---------------------------------------------------------------------------
# Interactive plot panel
# ---------------------------------------------------------------------------

class PlotPanel(ttk.Frame):
    """A matplotlib figure with the standard toolbar (zoom, pan, home, save),
    scroll-wheel zoom, and an editor for titles, labels, limits and scales.

    Label/scale edits are remembered and re-applied each time the panel is
    redrawn, until "Reset labels" is pressed.
    """

    def __init__(self, master, figsize=(7, 4)):
        super().__init__(master)
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        # packed first so they keep their room when the window is narrow;
        # the matplotlib toolbar (and its coordinate read-out) takes the rest
        self.controls = ttk.Frame(bar)
        self.controls.pack(side="right", padx=4)
        ttk.Button(bar, text="Reset labels", command=self.reset_overrides
                   ).pack(side="right", padx=2)
        b = ttk.Button(bar, text="Axes & labels…", command=self.edit_axes)
        b.pack(side="right", padx=(8, 2))
        Tooltip(b, "Edit title, axis labels, limits, log/linear scale, grid, "
                   "legend and font size")
        self.toolbar = NavigationToolbar2Tk(self.canvas, bar, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side="left", fill="x", expand=True)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.overrides = {}
        self.canvas.mpl_connect("scroll_event", self._on_scroll)

    # --- drawing protocol: clear() ... draw your axes ... finish()
    def clear(self):
        self.fig.clear()
        return self.fig

    def finish(self):
        self.apply_overrides()
        self.canvas.draw_idle()
        self.toolbar.update()          # resets the home view to this drawing

    def data_axes(self):
        return [a for a in self.fig.axes if not getattr(a, "_diffr_cbar", False)]

    def empty(self, msg):
        ax = self.clear().add_subplot()
        ax.text(0.5, 0.5, msg, ha="center", va="center", color=INK2,
                transform=ax.transAxes, wrap=True)
        ax.set_axis_off()
        self.canvas.draw_idle()

    # --- user label/axis edits
    def apply_overrides(self):
        for i, ax in enumerate(self.data_axes()):
            o = self.overrides.get(i)
            if not o:
                continue
            ax.set_title(o["title"])
            ax.set_xlabel(o["xlabel"])
            ax.set_ylabel(o["ylabel"])
            try:
                ax.set_xscale(o["xscale"])
                ax.set_yscale(o["yscale"])
            except ValueError:
                pass
            ax.grid(o["grid"], alpha=.6)
            leg = ax.get_legend()
            if leg is not None:
                leg.set_visible(o["legend"])
            elif o["legend"] and ax.get_legend_handles_labels()[0]:
                ax.legend().set_draggable(True)
            fs = o["fontsize"]
            ax.title.set_fontsize(fs + 1)
            ax.xaxis.label.set_fontsize(fs)
            ax.yaxis.label.set_fontsize(fs)
            ax.tick_params(labelsize=fs - 1)
            if ax.get_legend() is not None:
                for t in ax.get_legend().get_texts():
                    t.set_fontsize(fs - 1)
            if o.get("keep_limits"):
                ax.set_xlim(o["xlim"])
                ax.set_ylim(o["ylim"])

    def reset_overrides(self):
        self.overrides.clear()
        if hasattr(self, "redraw"):
            self.redraw()

    def edit_axes(self):
        axes = self.data_axes()
        if not axes or not any(a.axison for a in axes):
            messagebox.showinfo("Axes & labels", "Nothing plotted yet.",
                                parent=self)
            return
        AxesDialog(self, axes)

    def _on_scroll(self, e):
        ax = e.inaxes
        if ax is None or e.xdata is None or getattr(ax, "_diffr_cbar", False):
            return
        f = 1 / 1.25 if e.button == "up" else 1.25
        self.toolbar.push_current()

        def zoom(lim, c, scale):
            if scale == "log":
                lo, hi, c = np.log10(lim[0]), np.log10(lim[1]), np.log10(c)
                return 10 ** (c + (lo - c) * f), 10 ** (c + (hi - c) * f)
            return c + (lim[0] - c) * f, c + (lim[1] - c) * f
        if ax.get_xscale() in ("linear", "log"):
            ax.set_xlim(zoom(ax.get_xlim(), e.xdata, ax.get_xscale()))
        if ax.get_yscale() in ("linear", "log"):
            ax.set_ylim(zoom(ax.get_ylim(), e.ydata, ax.get_yscale()))
        self.canvas.draw_idle()


class AxesDialog(tk.Toplevel):
    """Edit title, labels, limits, scales, grid, legend, font size."""

    def __init__(self, panel, axes):
        super().__init__(panel)
        self.title("Axes & labels")
        self.panel, self.axes = panel, axes
        self.transient(panel.winfo_toplevel())
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        r = 0
        self.which = tk.StringVar()
        names = [f"{i + 1}: {a.get_title() or '(untitled)'}"
                 for i, a in enumerate(axes)]
        if len(axes) > 1:
            ttk.Label(f, text="Plot").grid(row=r, column=0, sticky="w")
            cb = ttk.Combobox(f, textvariable=self.which, values=names,
                              state="readonly", width=40)
            cb.grid(row=r, column=1, columnspan=3, sticky="ew", pady=2)
            cb.bind("<<ComboboxSelected>>", lambda e: self.load())
            r += 1
        self.which.set(names[0])
        self.v = {k: tk.StringVar() for k in
                  ("title", "xlabel", "ylabel", "xmin", "xmax", "ymin", "ymax",
                   "xscale", "yscale", "fontsize")}
        self.b = {k: tk.BooleanVar() for k in ("grid", "legend", "keep_limits")}
        for key, lab in (("title", "Title"), ("xlabel", "X label"),
                         ("ylabel", "Y label")):
            ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w")
            ttk.Entry(f, textvariable=self.v[key], width=44).grid(
                row=r, column=1, columnspan=3, sticky="ew", pady=2)
            r += 1
        for ax_, lab in (("x", "X range"), ("y", "Y range")):
            ttk.Label(f, text=lab).grid(row=r, column=0, sticky="w")
            ttk.Entry(f, textvariable=self.v[ax_ + "min"], width=12).grid(
                row=r, column=1, sticky="w", pady=2)
            ttk.Entry(f, textvariable=self.v[ax_ + "max"], width=12).grid(
                row=r, column=2, sticky="w", pady=2)
            ttk.Combobox(f, textvariable=self.v[ax_ + "scale"], width=8,
                         values=["linear", "log", "symlog"], state="readonly"
                         ).grid(row=r, column=3, sticky="w")
            r += 1
        ttk.Label(f, text="Font size").grid(row=r, column=0, sticky="w")
        ttk.Spinbox(f, from_=5, to=24, textvariable=self.v["fontsize"],
                    width=6).grid(row=r, column=1, sticky="w", pady=2)
        r += 1
        ttk.Checkbutton(f, text="Grid", variable=self.b["grid"]).grid(
            row=r, column=1, sticky="w")
        ttk.Checkbutton(f, text="Legend", variable=self.b["legend"]).grid(
            row=r, column=2, sticky="w")
        r += 1
        ttk.Checkbutton(f, text="Keep these limits when the plot is redrawn",
                        variable=self.b["keep_limits"]).grid(
            row=r, column=1, columnspan=3, sticky="w")
        r += 1
        bb = ttk.Frame(f)
        bb.grid(row=r, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(bb, text="Apply", command=self.apply).pack(side="left", padx=3)
        ttk.Button(bb, text="OK", command=lambda: (self.apply(), self.destroy())
                   ).pack(side="left", padx=3)
        ttk.Button(bb, text="Close", command=self.destroy).pack(side="left", padx=3)
        self.load()

    def idx(self):
        return int(self.which.get().split(":")[0]) - 1

    def load(self):
        ax = self.axes[self.idx()]
        self.v["title"].set(ax.get_title())
        self.v["xlabel"].set(ax.get_xlabel())
        self.v["ylabel"].set(ax.get_ylabel())
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        for k, val in (("xmin", x0), ("xmax", x1), ("ymin", y0), ("ymax", y1)):
            self.v[k].set(f"{val:.6g}")
        self.v["xscale"].set(ax.get_xscale())
        self.v["yscale"].set(ax.get_yscale())
        self.v["fontsize"].set(str(int(round(ax.xaxis.label.get_fontsize()))))
        self.b["grid"].set(any(l.get_visible() for l in ax.get_xgridlines()))
        leg = ax.get_legend()
        self.b["legend"].set(leg is not None and leg.get_visible())
        o = self.panel.overrides.get(self.idx(), {})
        self.b["keep_limits"].set(o.get("keep_limits", False))

    def apply(self):
        try:
            lim = [float(self.v[k].get()) for k in ("xmin", "xmax", "ymin", "ymax")]
            fs = float(self.v["fontsize"].get())
        except ValueError:
            messagebox.showerror("Axes & labels", "Limits and font size must "
                                 "be numbers.", parent=self)
            return
        if (self.v["xscale"].get() == "log" and min(lim[:2]) <= 0) or \
                (self.v["yscale"].get() == "log" and min(lim[2:]) <= 0):
            messagebox.showerror("Axes & labels", "A log axis needs positive "
                                 "limits.", parent=self)
            return
        o = dict(title=self.v["title"].get(), xlabel=self.v["xlabel"].get(),
                 ylabel=self.v["ylabel"].get(), xscale=self.v["xscale"].get(),
                 yscale=self.v["yscale"].get(), grid=self.b["grid"].get(),
                 legend=self.b["legend"].get(), fontsize=fs,
                 keep_limits=self.b["keep_limits"].get(),
                 xlim=lim[:2], ylim=lim[2:])
        self.panel.overrides[self.idx()] = o
        self.panel.apply_overrides()
        ax = self.axes[self.idx()]
        ax.set_xlim(lim[:2])
        ax.set_ylim(lim[2:])
        self.panel.canvas.draw_idle()


class PlaceholderDialog(tk.Toplevel):
    """Table of every value that is still a literature pre-fill."""

    def __init__(self, master, rows, title, note=""):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.geometry("780x380")
        f = ttk.Frame(self, padding=8)
        f.pack(fill="both", expand=True)
        layers = sorted({r[0] for r in rows})
        head = (f"{len(rows)} literature placeholder value(s) in "
                f"{len(layers)} layer(s). These are typical values from the "
                f"LITERATURE table, not measurements of your sample."
                if rows else "No literature placeholders: every value was "
                             "typed, read from a file or edited.")
        ttk.Label(f, text=head + ("\n" + note if note else ""), wraplength=690,
                  justify="left").pack(anchor="w", pady=(0, 6))
        cols = ("layer", "q", "val", "unit")
        tf = ttk.Frame(f)
        tf.pack(fill="both", expand=True)
        tv = ttk.Treeview(tf, columns=cols, show="headings", height=12)
        for c, h, w in zip(cols, ("Layer", "Quantity", "Value", "Unit"),
                           (140, 300, 140, 150)):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(tf, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for n, mat, q, val, unit in rows:
            tv.insert("", "end", values=(f"{n}: {mat}", q, val, unit))
        self.text = "\n".join(f"{n}: {mat}\t{q}\t{val}\t{unit}"
                              for n, mat, q, val, unit in rows)
        bb = ttk.Frame(f)
        bb.pack(fill="x", pady=(6, 0))
        ttk.Label(bb, foreground=INK2, text="Edit a value in the Layers tab to "
                  "replace a placeholder.").pack(side="left")
        ttk.Button(bb, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="Copy to clipboard", command=self.copy
                   ).pack(side="right", padx=4)

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append("layer\tquantity\tvalue\tunit\n" + self.text)


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------

EXAMPLE_STACK = [("Ti", "15"), ("Si3N4", "9.44"), ("Ti", "14.37"),
                 ("Si3N4", "588"), ("SiO2", "1454"), ("Si", "")]


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("diffR thin-film model")
        self.geometry("1560x940")
        self.minsize(1100, 700)
        self.cache = S.DispersionCache()
        self.layers, self.sel, self._loading = [], None, False
        self.batches, self.run_counter = [], 0
        self.queue, self.worker = queue.Queue(), None
        self.cancel_ev = threading.Event()
        self._stack_job = None
        self.session_path = None

        self.cfg_vars = {}
        self.flag_vars = {k: tk.BooleanVar(value=v)
                          for k, v in S.default_flags().items()}
        self.show_components = tk.BooleanVar(value=False)
        self.show_kernels = tk.BooleanVar(value=False)
        self.show_strain = tk.BooleanVar(value=False)
        self.overlay = tk.BooleanVar(value=False)
        self.scale_exp = tk.StringVar(value="3")
        self.stack_mode = tk.StringVar(value="Equal widths")
        self.view_run = tk.StringVar()

        self._build_menu()
        outer = ttk.PanedWindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True)
        left = ttk.PanedWindow(outer, orient="vertical")
        right = ttk.Frame(outer)
        outer.add(left, weight=0)
        outer.add(right, weight=1)

        top = ttk.Frame(left)
        left.add(top, weight=3)
        self.tabs = ttk.Notebook(top, width=600)
        self.tabs.pack(fill="both", expand=True)
        self._build_experiment_tab()
        self._build_layers_tab()
        self._build_sweep_tab()
        self._build_run_bar(top)
        logf = ttk.Frame(left)
        left.add(logf, weight=1)
        ttk.Label(logf, text="Log: conversions to model units, warnings, "
                  "run diagnostics").pack(anchor="w", padx=4)
        self.log_w = ScrolledText(logf, height=12, wrap="none",
                                  font=("Courier", 9))
        self.log_w.pack(fill="both", expand=True)
        self.log_w.tag_configure("warn", foreground=LIT_C)
        self.log_w.tag_configure("err", foreground="#c00000")
        self.log_w.tag_configure("head", font=("Courier", 9, "bold"))

        self._build_plots(right)
        for w in ("pump", "probe"):
            for var in self.cfg_vars[f"lambda_{w}_nm"]:
                var.trace_add("write", lambda *_: self.schedule_file_nk())
        self.load_example()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll)

    # ------------------------------------------------------------------ menu
    def _build_menu(self):
        mb = tk.Menu(self)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="New session (example stack)", command=self.new_session)
        fm.add_command(label="Open session…", command=self.open_session,
                       accelerator="Ctrl+O")
        fm.add_command(label="Save session", command=self.save_session,
                       accelerator="Ctrl+S")
        fm.add_command(label="Save session as…", command=self.save_session_as)
        fm.add_separator()
        fm.add_command(label="Import conf_file…", command=self.import_conf)
        fm.add_separator()
        fm.add_command(label="Export selected run (.npz + .csv)…",
                       command=self.export_run)
        fm.add_command(label="Export sweep (.csv + .npz)…", command=self.export_sweep)
        fm.add_separator()
        fm.add_command(label="Quit", command=self.on_close)
        mb.add_cascade(label="File", menu=fm)
        rm = tk.Menu(mb, tearoff=0)
        rm.add_command(label="Run model", command=self.run_single,
                       accelerator="F5")
        rm.add_command(label="Run thickness sweep", command=self.run_sweep)
        rm.add_command(label="Cancel", command=self.cancel)
        rm.add_separator()
        rm.add_command(label="List literature placeholders…",
                       command=self.show_placeholders)
        mb.add_cascade(label="Run", menu=rm)
        hm = tk.Menu(mb, tearoff=0)
        hm.add_command(label="Units and conversions", command=self.show_units)
        hm.add_command(label="How to use", command=self.show_help)
        mb.add_cascade(label="Help", menu=hm)
        self.config(menu=mb)
        self.bind_all("<F5>", lambda e: self.run_single())
        self.bind_all("<Control-s>", lambda e: self.save_session())
        self.bind_all("<Control-o>", lambda e: self.open_session())

    # ------------------------------------------------------------ experiment
    def _value_row(self, parent, r, label, var, unit_var, kind, hint=""):
        """label | entry | unit menu | '= converted value' ."""
        lab = ttk.Label(parent, text=label)
        lab.grid(row=r, column=0, sticky="w", pady=1)
        e = ttk.Entry(parent, textvariable=var, width=12)
        e.grid(row=r, column=1, sticky="w", padx=2)
        cb = ttk.Combobox(parent, textvariable=unit_var, values=U.choices(kind),
                          state="readonly", width=15)
        cb.grid(row=r, column=2, sticky="w", padx=2)
        conv = ttk.Label(parent, foreground=INK2)
        conv.grid(row=r, column=3, sticky="w", padx=4)

        def upd(*_):
            try:
                v = U.to_base(float(var.get()), kind, unit_var.get())
                conv.config(text=f"= {U.fmt(v)} {U.base_unit(kind)}",
                            foreground=INK2)
            except (ValueError, ZeroDivisionError, KeyError):
                conv.config(text="not a number" if var.get().strip() else "",
                            foreground="#c00000")
        var.trace_add("write", upd)
        unit_var.trace_add("write", upd)
        upd()
        if hint:
            Tooltip(lab, hint)
            Tooltip(e, hint)
        return e, conv

    def _build_experiment_tab(self):
        sf = ScrollableFrame(self.tabs)
        self.tabs.add(sf, text="Experiment")
        p = sf.inner
        groups = [("Laser excitation", CFG_GROUP_LASER),
                  ("Time axis", CFG_GROUP_TIME),
                  ("Spatial grid & numerics", CFG_GROUP_GRID)]
        info = {f[0]: f for f in S.CFG_FIELDS}
        for g, (title, keys) in enumerate(groups):
            lf = section(p, title, g)
            for r, key in enumerate(keys):
                _, label, kind, val, unit, hint = info[key]
                v, u = tk.StringVar(value=val), tk.StringVar(value=unit)
                self.cfg_vars[key] = (v, u)
                self._value_row(lf, r, label, v, u, kind, hint)
        lf = section(p, "Contributions to ΔR/R", len(groups))
        for r, (k, lab) in enumerate(S.PHYSICS_FLAGS):
            ttk.Checkbutton(lf, text=lab, variable=self.flag_vars[k]).grid(
                row=r, column=0, sticky="w")
        ttk.Label(p, foreground=INK2, wraplength=540, justify="left",
                  text="Type each value in the unit selected next to it. The grey "
                       "text shows what the model receives (nm, ps, SI). Hover a "
                       "label for an explanation.").grid(
            row=len(groups) + 1, column=0, sticky="w", padx=8, pady=6)

    # ---------------------------------------------------------------- layers
    def _build_layers_tab(self):
        tab = ttk.Frame(self.tabs)
        self.tabs.add(tab, text="Layers")
        ttk.Label(tab, foreground=INK2, wraplength=580, justify="left",
                  text="Light enters through layer 1 (top of the list). The last "
                       "layer is the semi-infinite substrate. Click a layer to "
                       "edit it, or click it in the stack diagram.").pack(
            anchor="w", padx=6, pady=(6, 2))
        tf = ttk.Frame(tab)
        tf.pack(fill="x", padx=6)
        cols = ("n", "mat", "d", "nk", "lit")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=7,
                                 selectmode="browse")
        for c, h, w in zip(cols, ("#", "Material", "Thickness", "n,k source",
                                  "Literature values"), (34, 110, 110, 150, 150)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w", stretch=c == "nk")
        self.tree.pack(side="left", fill="x", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        bf = ttk.Frame(tf)
        bf.pack(side="left", fill="y", padx=(6, 0))
        for text, cmd in (("Add layer", self.add_layer),
                          ("Duplicate", self.duplicate_layer),
                          ("Delete", self.delete_layer),
                          ("Up ↑", lambda: self.move_layer(-1)),
                          ("Down ↓", lambda: self.move_layer(1))):
            ttk.Button(bf, text=text, command=cmd, width=14).pack(pady=1)
        b = ttk.Button(bf, text="Literature values…", width=14,
                       command=self.show_placeholders)
        b.pack(pady=(6, 1))
        Tooltip(b, "List every value that is still a literature pre-fill, "
                   "for all layers. Double-click a layer to list only its "
                   "values.")
        self.tree.bind("<Double-1>", self._on_tree_double)

        self.editor = ScrollableFrame(tab)
        self.editor.pack(fill="both", expand=True, pady=(6, 0))
        self._build_layer_editor(self.editor.inner)

    def _layer_var(self, key):
        v = tk.StringVar()
        v.trace_add("write", lambda *_: self._on_layer_var(key))
        self.lv[key] = v
        return v

    def _build_layer_editor(self, p):
        self.lv, self.conv_upd, self.lit_marks = {}, {}, {}
        for key in S.new_layer():
            if key != "lit":
                self._layer_var(key)

        # --- general
        g = section(p, "Layer", 0)
        ttk.Label(g, text="Material name").grid(row=0, column=0, sticky="w")
        self.mat_cb = ttk.Combobox(g, textvariable=self.lv["material"], width=16,
                                   values=S.literature_names())
        self.mat_cb.grid(row=0, column=1, columnspan=2, sticky="w", padx=2)
        self.mat_cb.bind("<<ComboboxSelected>>",
                         lambda e: self.fill_literature(ask=True))
        b = ttk.Button(g, text="Fill from literature",
                       command=lambda: self.fill_literature(ask=True))
        b.grid(row=0, column=3, sticky="w", padx=4)
        Tooltip(b, "Pre-fill the properties from the LITERATURE table in "
                   "diffr_model.py (Si3N4, SiO2, Ti, Si, Al, Au). Values still "
                   "equal to the pre-fill are reported as literature "
                   "placeholders when you run.")
        self.d_entry, self.d_conv = self._value_row(
            g, 1, "Thickness", self.lv["d"], self.lv["d_unit"], "thickness")
        self.sub_note = ttk.Label(g, foreground=INK2)
        self.sub_note.grid(row=2, column=0, columnspan=4, sticky="w")

        # --- optical constants
        o = section(p, "Optical constants  ñ = n + ik", 1)
        rf = ttk.Frame(o)
        rf.grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Radiobutton(rf, text="Type n, k", value="typed",
                        variable=self.lv["nk_mode"]).pack(side="left")
        ttk.Radiobutton(rf, text="Dispersion file (interpolated)", value="file",
                        variable=self.lv["nk_mode"]).pack(side="left", padx=10)
        self.nk_f = ttk.Frame(o)
        self.nk_f.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.nk_head = ttk.Label(self.nk_f, foreground=INK2)
        self.nk_head.grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(self.nk_f, text="n").grid(row=1, column=1)
        ttk.Label(self.nk_f, text="k").grid(row=1, column=2)
        self.nk_entries, self.nk_row_labels = [], {}
        for r, w in enumerate(("pump", "probe"), 2):
            self.nk_row_labels[w] = ttk.Label(self.nk_f, text=f"at {w} wavelength")
            self.nk_row_labels[w].grid(row=r, column=0, sticky="w")
            for c, q in ((1, "n"), (2, "k")):
                en = ttk.Entry(self.nk_f, textvariable=self.lv[f"{q}_{w}"], width=12)
                en.grid(row=r, column=c, padx=2, pady=1)
                self.nk_entries.append(en)
            self.lit_marks[f"k_{w}"] = ttk.Label(self.nk_f, foreground=LIT_C)
            self.lit_marks[f"k_{w}"].grid(row=r, column=3, sticky="w")
        self.file_f = ttk.Frame(o)
        self.file_f.grid(row=1, column=0, sticky="ew")
        ttk.Label(self.file_f, text="File").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.file_f, textvariable=self.lv["nk_file"], width=40).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=2)
        ttk.Button(self.file_f, text="Browse…", command=self.browse_nk).grid(
            row=0, column=4, padx=2)
        lab = ttk.Label(self.file_f, text="Column-1 unit")
        lab.grid(row=1, column=0, sticky="w")
        Tooltip(lab, "Unit of the first column (wavelength or photon energy). "
                     "'auto' reads a '# unit: ...' line in the file, otherwise "
                     "guesses (values < 100 -> µm, > 1500 -> Å) and says so "
                     "in the log.")
        ttk.Combobox(self.file_f, textvariable=self.lv["nk_file_unit"],
                     values=S.FILE_UNITS, state="readonly", width=8).grid(
            row=1, column=1, sticky="w", padx=2, pady=1)
        b = ttk.Button(self.file_f, text="Reload file",
                       command=self._refresh_file_nk)
        b.grid(row=1, column=2, sticky="w", padx=2)
        Tooltip(b, "Read the file again (e.g. after editing it). n and k at "
                   "the pump and probe wavelengths are filled in below "
                   "automatically whenever the file, its unit or the "
                   "wavelengths change.")
        self.kfill_f = ttk.Frame(self.file_f)
        self.kfill_f.grid(row=2, column=0, columnspan=5, sticky="w")
        ttk.Label(self.kfill_f, text="This file has no k column. k to use:"
                  ).grid(row=0, column=0, columnspan=4, sticky="w")
        for c, w in enumerate(("pump", "probe")):
            ff = ttk.Frame(self.kfill_f)
            ff.grid(row=1, column=c, sticky="w", padx=(0, 12))
            ttk.Label(ff, text=f"at {w}").pack(side="left")
            ttk.Entry(ff, textvariable=self.lv[f"kfill_{w}"], width=10).pack(
                side="left", padx=2)
            self.lit_marks[f"kfill_{w}"] = ttk.Label(ff, foreground=LIT_C)
            self.lit_marks[f"kfill_{w}"].pack(side="left")
        self.file_info = ttk.Label(self.file_f, foreground=INK2, wraplength=520,
                                   justify="left")
        self.file_info.grid(row=4, column=0, columnspan=5, sticky="w")
        self._nk_job = None

        # --- mechanical & thermal
        m = section(p, "Mechanical & thermal", 2)
        for r, key in enumerate(("rho", "v", "Cp", "alpha_lin", "B_bulk")):
            self._prop_row(m, r, key)

        # --- optical coupling
        c = section(p, "Optical coupling coefficients (complex: Re + i·Im)", 3)
        pf = ttk.Frame(c)
        pf.grid(row=0, column=0, columnspan=7, sticky="w")
        ttk.Label(pf, text="Photoelastic input:").pack(side="left")
        ttk.Radiobutton(pf, text="dñ/dη", value="dn_deta",
                        variable=self.lv["pe_mode"]).pack(side="left", padx=4)
        rb = ttk.Radiobutton(pf, text="coefficient p", value="pe",
                             variable=self.lv["pe_mode"])
        rb.pack(side="left", padx=4)
        Tooltip(rb, "Photoelastic constant p (indicatrix convention, "
                    "d(1/n²) = p·η). Converted at the probe wavelength: "
                    "dñ/dη = −p·ñ_probe³/2.")
        self.pe_rows = {"dn_deta": self._prop_row(c, 1, "dn_deta"),
                        "pe": self._prop_row(c, 2, "pe")}
        self._prop_row(c, 3, "dn_dT")

        # --- electronic
        e = section(p, "Electronic (metals) — optional: blank Ce = no electron "
                       "channel", 4)
        self._prop_row(e, 0, "Ce")
        self._prop_row(e, 1, "tau_ep_ps")
        self._prop_row(e, 2, "dn_dTe")

    def _prop_row(self, parent, r, key):
        """One material property: label, value (Re/Im), unit, conversion,
        literature marker. Returns the widgets (to show/hide)."""
        _, label, kind, _, required, default = S.PROP_INFO[key]
        cplx = key in {p[0] for p in S.COMPLEX_PROPS}
        w = []
        lab = ttk.Label(parent, text=label + ("" if required else " (opt.)"))
        lab.grid(row=r, column=0, sticky="w", pady=1)
        w.append(lab)
        if cplx:
            for col, suff in ((1, "_re"), (3, "_im")):
                en = ttk.Entry(parent, textvariable=self.lv[key + suff], width=9)
                en.grid(row=r, column=col, padx=1)
                w.append(en)
            pl = ttk.Label(parent, text="+ i·")
            pl.grid(row=r, column=2)
            w.append(pl)
        else:
            en = ttk.Entry(parent, textvariable=self.lv[key], width=9)
            en.grid(row=r, column=1, padx=1)
            w.append(en)
        cb = ttk.Combobox(parent, textvariable=self.lv[key + "_unit"],
                          values=U.choices(kind), state="readonly", width=12)
        cb.grid(row=r, column=4, padx=2)
        conv = ttk.Label(parent, foreground=INK2)
        conv.grid(row=r, column=5, sticky="w", padx=3)
        mark = ttk.Label(parent, foreground=LIT_C)
        mark.grid(row=r, column=6, sticky="w")
        w += [cb, conv, mark]
        self.lit_marks[key] = mark
        Tooltip(lab, PROP_HELP.get(key, ""))

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
                v = U.to_base(x, kind, unit)
                conv.config(text=f"= {U.fmt(v)} {U.base_unit(kind)}",
                            foreground=INK2)
            except LookupError:
                conv.config(text="" if required else
                            f"blank → {U.fmt(default)} (default)",
                            foreground=INK2)
            except ValueError:
                conv.config(text="not a number", foreground="#c00000")
        self.conv_upd[key] = upd
        return w

    # --- layer list operations
    def _on_layer_var(self, key):
        if self._loading or self.sel is None:
            return
        lay = self.layers[self.sel]
        lay[key] = self.lv[key].get()
        if key == "nk_mode":
            self._show_nk_mode()
        if key == "pe_mode":
            self._show_pe_mode()
        base = key[:-5] if key.endswith("_unit") else key
        base = base[:-3] if base.endswith(("_re", "_im")) else base
        if base in self.conv_upd:
            self.conv_upd[base]()
        self._refresh_lit_marks()
        self._refresh_tree_row(self.sel)
        if key in ("material", "d", "d_unit"):
            self.schedule_stack()
        if key in ("nk_mode", "nk_file", "nk_file_unit", "kfill_pump",
                   "kfill_probe"):
            self.schedule_file_nk()

    def _show_nk_mode(self):
        file_mode = self.lv["nk_mode"].get() == "file"
        if file_mode:
            self.file_f.grid()
            self.nk_head.config(text="n, k interpolated from the file "
                                     "(read-only; switch to 'Type n, k' to edit):")
        else:
            self.file_f.grid_remove()
            self.nk_head.config(text="")
        for en in self.nk_entries:
            en.config(state="readonly" if file_mode else "normal")
        self._update_nk_labels()

    def _wavelengths(self):
        """Pump and probe wavelengths in nm from the Experiment tab."""
        lam = {}
        for w in ("pump", "probe"):
            v, u = self.cfg_vars[f"lambda_{w}_nm"]
            lam[w] = U.to_base(float(v.get()), "wavelength", u.get())
            if not lam[w] > 0:
                raise ValueError
        return lam

    def _update_nk_labels(self):
        try:
            lam = self._wavelengths()
        except (ValueError, ZeroDivisionError):
            lam = {}
        for w, lab in self.nk_row_labels.items():
            lab.config(text=f"at {w} λ = {lam[w]:.6g} nm" if w in lam
                       else f"at {w} wavelength")

    def schedule_file_nk(self):
        if self._nk_job:
            self.after_cancel(self._nk_job)
        self._nk_job = self.after(250, self._refresh_file_nk)

    def _refresh_file_nk(self):
        """File mode: read the dispersion file and put n, k at the pump and
        probe wavelengths into the layer, as the model will use them."""
        self._nk_job = None
        self._update_nk_labels()
        if self.sel is None or self.layers[self.sel]["nk_mode"] != "file":
            return
        lay = self.layers[self.sel]
        path = lay["nk_file"].strip()
        vals = {k: "" for k in S.NK_KEYS}
        lines, bad = [], False
        if not path:
            self.kfill_f.grid_remove()
            lines.append("Choose an n,k file with Browse…")
        else:
            try:
                tab = self.cache.get(path, lay["nk_file_unit"])
            except Exception as e:
                tab, bad = None, True
                self.kfill_f.grid_remove()
                lines.append(f"Cannot read {path}: {e}" if os.path.isfile(path)
                             else f"File not found: {path}")
            if tab is not None:
                has_k = tab["k"] is not None
                (self.kfill_f.grid_remove if has_k else self.kfill_f.grid)()
                lines.append(f"{os.path.basename(path)}: {len(tab['lam'])} rows, "
                             f"{tab['lam'][0]:.6g}–{tab['lam'][-1]:.6g} nm, "
                             f"{'n and k' if has_k else 'n only'}")
                if S.unit_note(tab):
                    lines.append(S.unit_note(tab))
                try:
                    lam = self._wavelengths()
                except (ValueError, ZeroDivisionError):
                    lam, bad = {}, True
                    lines.append("Set valid pump and probe wavelengths in the "
                                 "Experiment tab.")
                for w, lw in lam.items():
                    try:
                        n, k, note = M.interp_nk(tab, lw)
                    except M.MissingMaterialData:
                        bad = True
                        lines.append(f"{w} λ = {lw:.6g} nm is outside the "
                                     f"file's range; the model will not "
                                     f"extrapolate.")
                        continue
                    vals[f"n_{w}"] = f"{n:.6g}"
                    vals[f"k_{w}"] = (f"{k:.6g}" if k is not None
                                      else lay[f"kfill_{w}"].strip())
                    lines.append(f"{w}: n = {n:.6g}, k = "
                                 f"{vals[f'k_{w}'] or '?'}  ({note}"
                                 f"{'' if k is not None else ', k typed above'})")
        for key, s in vals.items():
            if self.lv[key].get() != s:
                self.lv[key].set(s)
        self.file_info.config(text="\n".join(lines),
                              foreground="#c00000" if bad else INK2)

    def _show_pe_mode(self):
        mode = self.lv["pe_mode"].get()
        for k, ws in self.pe_rows.items():
            for w in ws:
                (w.grid if k == mode else w.grid_remove)()

    def _refresh_lit_marks(self):
        if self.sel is None:
            return
        lay = self.layers[self.sel]
        file_mode = lay["nk_mode"] == "file"
        for key, mark in self.lit_marks.items():
            hidden = (key in ("k_pump", "k_probe") and file_mode or
                      key in ("kfill_pump", "kfill_probe") and not file_mode)
            mark.config(text="● lit." if S.is_placeholder(lay, key)
                        and not hidden else "")

    def _tree_values(self, i):
        lay = self.layers[i]
        last = i == len(self.layers) - 1
        d = "∞ (substrate)" if last else f"{lay['d']} {lay['d_unit']}"
        if lay["nk_mode"] == "file":
            nk = "file: " + (os.path.basename(lay["nk_file"]) or "(none)")
        else:
            nk = "typed"
        n_lit = sum(S.is_placeholder(lay, k) for k in
                    list(S.PROP_INFO) + ["k_pump", "k_probe"])
        return (i + 1, lay["material"], d, nk, f"{n_lit} fields" if n_lit else "")

    def _refresh_tree_row(self, i):
        self.tree.item(str(i), values=self._tree_values(i))

    def refresh_tree(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for i in range(len(self.layers)):
            self.tree.insert("", "end", iid=str(i), values=self._tree_values(i))
        if self.layers:
            sel = min(select if select is not None else (self.sel or 0),
                      len(self.layers) - 1)
            self.tree.selection_set(str(sel))
            self.tree.see(str(sel))
            self.select_layer(sel)
        else:
            self.sel = None
        self.schedule_stack()
        self._refresh_sweep_layers()

    def show_placeholders(self, layer=None):
        rows = S.placeholder_rows(self.layers)
        title = "Literature placeholders"
        if layer is not None:
            rows = [r for r in rows if r[0] == layer + 1]
            title += f" — layer {layer + 1} ({self.layers[layer]['material']})"
        PlaceholderDialog(self, rows, title)

    def _on_tree_double(self, e):
        row = self.tree.identify_row(e.y)
        if row:
            self.show_placeholders(int(row))

    def _on_tree_select(self, _):
        s = self.tree.selection()
        if s and int(s[0]) != self.sel:
            self.select_layer(int(s[0]))

    def select_layer(self, i):
        self.sel = i
        lay = self.layers[i]
        self._loading = True
        try:
            for key, var in self.lv.items():
                var.set(lay.get(key, ""))
        finally:
            self._loading = False
        last = i == len(self.layers) - 1
        self.d_entry.config(state="disabled" if last else "normal")
        self.sub_note.config(text="This is the last layer: the semi-infinite "
                                  "substrate (no thickness)." if last else "")
        if last:
            self.d_conv.config(text="")
        self._show_nk_mode()
        self._show_pe_mode()
        for f in self.conv_upd.values():
            f()
        self._refresh_lit_marks()
        self.file_info.config(text="")
        self._refresh_file_nk()
        self.schedule_stack()           # moves the selection frame

    def add_layer(self):
        lay = S.new_layer(d="10")
        if not self.layers:
            self.layers.append(lay)
            self.refresh_tree(0)
            return
        pos = self.sel + 1 if self.sel is not None else len(self.layers) - 1
        pos = min(pos, len(self.layers) - 1)       # keep the substrate last
        self.layers.insert(pos, lay)
        self.refresh_tree(pos)
        self.mat_cb.focus_set()

    def duplicate_layer(self):
        if self.sel is None:
            return
        lay = copy.deepcopy(self.layers[self.sel])
        pos = min(self.sel + 1, len(self.layers) - 1)
        if not lay["d"].strip():
            lay["d"] = "10"
        self.layers.insert(pos, lay)
        self.refresh_tree(pos)

    def delete_layer(self):
        if self.sel is None:
            return
        if not messagebox.askyesno("Delete layer",
                                   f"Delete layer {self.sel + 1} "
                                   f"({self.layers[self.sel]['material']})?"):
            return
        del self.layers[self.sel]
        self.refresh_tree(max(0, self.sel - 1))

    def move_layer(self, step):
        i = self.sel
        if i is None or not 0 <= i + step < len(self.layers):
            return
        self.layers[i], self.layers[i + step] = self.layers[i + step], self.layers[i]
        self.refresh_tree(i + step)

    def fill_literature(self, ask=True):
        if self.sel is None:
            return
        lay = self.layers[self.sel]
        name = lay["material"].strip()
        if name not in M.LITERATURE:
            messagebox.showinfo(
                "Literature values", f"No literature entry for '{name}'.\n"
                f"Available: {', '.join(S.literature_names())}.\n\n"
                f"Type the properties for this material yourself.")
            return
        has_values = any(not S._is_empty(lay, k) for k in S.PROP_INFO)
        only_empty = False
        if ask and has_values:
            ans = messagebox.askyesnocancel(
                "Literature values",
                f"Fill {name} from the literature table.\n\n"
                f"Yes: overwrite all properties with literature values\n"
                f"No: fill only the empty fields")
            if ans is None:
                return
            only_empty = not ans
        filled = S.apply_literature(lay, only_empty=only_empty)
        self.select_layer(self.sel)
        self._refresh_tree_row(self.sel)
        self.log(f"Layer {self.sel + 1} ({name}): literature values for "
                 f"{', '.join(filled) or 'nothing (all fields already set)'}; "
                 f"n is never pre-filled.")
        rows = [r for r in S.placeholder_rows(self.layers) if r[0] == self.sel + 1]
        PlaceholderDialog(self, rows, f"Literature values — layer {self.sel + 1} "
                                      f"({name})",
                          note="" if filled else "Nothing new was filled; all "
                          "fields already had values.")

    def browse_nk(self):
        cur = self.lv["nk_file"].get().strip()
        p = filedialog.askopenfilename(
            title="n,k dispersion file",
            initialdir=os.path.dirname(cur) if cur else None,
            filetypes=[("Text tables", "*.txt *.dat *.csv *.nk"), ("All", "*.*")])
        if p:
            self.lv["nk_mode"].set("file")
            self.lv["nk_file"].set(os.path.normpath(p))
            self._refresh_file_nk()

    # ----------------------------------------------------------------- sweep
    def _build_sweep_tab(self):
        sf = ScrollableFrame(self.tabs)
        self.tabs.add(sf, text="Thickness sweep")
        tab = sf.inner
        ttk.Label(tab, wraplength=560, justify="left", foreground=INK2,
                  text="Vary the thickness of one layer, run the model for each "
                       "value with everything else unchanged, overlay the ΔR/R "
                       "curves, and (optionally) save one reflectivity file "
                       "per thickness.").grid(row=0, column=0, sticky="w",
                                               padx=8, pady=(6, 2))
        g = section(tab, "Thickness range", 1)
        ttk.Label(g, text="Layer").grid(row=0, column=0, sticky="w")
        self.sweep_layer = tk.StringVar()
        self.sweep_cb = ttk.Combobox(g, textvariable=self.sweep_layer,
                                     state="readonly", width=30)
        self.sweep_cb.grid(row=0, column=1, columnspan=4, sticky="w", pady=2)
        self.sweep_start = tk.StringVar(value="100")
        self.sweep_end = tk.StringVar(value="600")
        self.sweep_step = tk.StringVar(value="100")
        self.sweep_unit = tk.StringVar(value="nm")
        for c, (lab, var) in enumerate((("Start", self.sweep_start),
                                        ("End", self.sweep_end),
                                        ("Increment", self.sweep_step))):
            ttk.Label(g, text=lab).grid(row=1, column=1 + c, sticky="w")
            ttk.Entry(g, textvariable=var, width=10).grid(
                row=2, column=1 + c, sticky="w", padx=(0, 6))
        ttk.Label(g, text="Thickness").grid(row=2, column=0, sticky="w")
        ttk.Combobox(g, textvariable=self.sweep_unit, state="readonly", width=6,
                     values=U.choices("thickness")).grid(row=2, column=4,
                                                         sticky="w")
        self.sweep_preview = ttk.Label(g, foreground=INK2, wraplength=520,
                                       justify="left")
        self.sweep_preview.grid(row=3, column=0, columnspan=5, sticky="w",
                                pady=(4, 0))

        o = section(tab, "Output files", 2)
        self.sweep_save = tk.BooleanVar(value=False)
        ttk.Checkbutton(o, text="Save each run's reflectivity file "
                               "(.csv + .npz) automatically",
                        variable=self.sweep_save).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(o, text="Folder").grid(row=1, column=0, sticky="w")
        self.sweep_dir = tk.StringVar(value=os.getcwd())
        ttk.Entry(o, textvariable=self.sweep_dir, width=42).grid(
            row=1, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(o, text="Browse…", command=self._browse_sweep_dir).grid(
            row=1, column=2, padx=2)
        lab = ttk.Label(o, text="File name")
        lab.grid(row=2, column=0, sticky="w")
        self.sweep_name = tk.StringVar(value="dRR_{material}_{d}{unit}")
        ttk.Entry(o, textvariable=self.sweep_name, width=42).grid(
            row=2, column=1, sticky="ew", padx=2, pady=2)
        tip = ("Fields you can use in the names:\n"
               "  {material}  name of the swept layer\n"
               "  {layer}     its number in the stack\n"
               "  {d}         thickness, in the unit chosen above\n"
               "  {unit}      that unit (nm, um, A, m)\n"
               "  {i}         run number 1, 2, 3 …\n"
               "  {n}         number of runs\n"
               ".csv and .npz are added automatically.")
        Tooltip(lab, tip)
        self.sweep_combined = tk.BooleanVar(value=True)
        ttk.Checkbutton(o, text="Also save all thicknesses in one combined file:",
                        variable=self.sweep_combined).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(o, text="Combined").grid(row=4, column=0, sticky="w")
        self.sweep_cname = tk.StringVar(value="dRR_{material}_sweep_{start}-{end}{unit}")
        ttk.Entry(o, textvariable=self.sweep_cname, width=42).grid(
            row=4, column=1, sticky="ew", padx=2, pady=2)
        ttk.Label(o, foreground=INK2, justify="left", text=tip.replace(
            "\n  {i}         run number 1, 2, 3 …", "").replace(
            "  {d}         thickness, in the unit chosen above\n",
            "  {d}         thickness (per-run files)\n"
            "  {start} {end} {step}  the range (combined file)\n")).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.name_preview = ttk.Label(o, foreground=INK2, wraplength=520,
                                      justify="left")
        self.name_preview.grid(row=6, column=0, columnspan=3, sticky="w",
                               pady=(4, 0))
        o.columnconfigure(1, weight=1)

        ttk.Button(tab, text="▶ Run sweep", command=self.run_sweep).grid(
            row=3, column=0, sticky="w", padx=8, pady=10)
        for v in (self.sweep_layer, self.sweep_start, self.sweep_end,
                  self.sweep_step, self.sweep_unit, self.sweep_save,
                  self.sweep_dir, self.sweep_name, self.sweep_combined,
                  self.sweep_cname):
            v.trace_add("write", lambda *_: self._update_sweep_preview())

    def _browse_sweep_dir(self):
        d = filedialog.askdirectory(title="Folder for the sweep files",
                                    initialdir=self.sweep_dir.get() or None)
        if d:
            self.sweep_dir.set(os.path.normpath(d))

    def _sweep_plan(self):
        """(layer index, values, unit, per-run base paths or None,
        combined base path or None); raises ValueError with a message."""
        sl = self.sweep_layer.get()
        if not sl:
            raise ValueError("Add at least one layer above the substrate to "
                             "sweep.")
        idx = int(sl.split(":")[0]) - 1
        try:
            a, b, st = (float(v.get()) for v in
                        (self.sweep_start, self.sweep_end, self.sweep_step))
        except ValueError:
            raise ValueError("Start, end and increment must be numbers.")
        vals = S.range_values(a, b, st)
        if vals[0] <= 0:
            raise ValueError("Thicknesses must be > 0.")
        unit = self.sweep_unit.get()
        uname = FILE_UNIT_NAMES.get(unit, unit)
        mat = self.layers[idx]["material"].strip() or f"layer{idx + 1}"
        bases = cbase = None
        folder = self.sweep_dir.get().strip()
        if self.sweep_save.get() or self.sweep_combined.get():
            if not folder:
                raise ValueError("Choose a folder for the output files.")
        if self.sweep_save.get():
            bases = [os.path.join(folder, S.format_name(
                self.sweep_name.get(), material=mat, layer=idx + 1, d=f"{v:g}",
                unit=uname, i=i + 1, n=len(vals))) for i, v in enumerate(vals)]
            if len(set(bases)) != len(bases):
                raise ValueError("The file name gives the same name to several "
                                 "runs; include {d} or {i}.")
        if self.sweep_combined.get():
            try:
                cbase = os.path.join(folder, self.sweep_cname.get().format(
                    material=mat, layer=idx + 1, unit=uname, n=len(vals),
                    start=f"{a:g}", end=f"{vals[-1]:g}", step=f"{st:g}"))
            except (KeyError, IndexError, ValueError) as e:
                raise ValueError(f"Combined file name: unknown field {e}; use "
                                 f"{{material}} {{layer}} {{start}} {{end}} "
                                 f"{{step}} {{unit}} {{n}}")
            cbase = os.path.join(folder, S.format_name(os.path.basename(cbase)))
        return idx, vals, unit, bases, cbase

    def _update_sweep_preview(self):
        try:
            idx, vals, unit, bases, cbase = self._sweep_plan()
        except ValueError as e:
            self.sweep_preview.config(text=str(e), foreground="#c00000")
            self.name_preview.config(text="")
            return
        shown = ", ".join(f"{v:g}" for v in vals[:8]) + (
            f", … {vals[-1]:g}" if len(vals) > 8 else "")
        self.sweep_preview.config(
            text=f"{len(vals)} runs: {shown} {unit}", foreground=INK2)
        lines = []
        if bases:
            names = [os.path.basename(b) + ".csv" for b in bases]
            lines.append("Files: " + (", ".join(names) if len(names) <= 3 else
                                      f"{names[0]}, {names[1]}, … {names[-1]}"))
        if cbase:
            lines.append(f"Combined: {os.path.basename(cbase)}.csv")
        if not lines:
            lines.append("No files are written automatically (you can still "
                         "use File → Export sweep afterwards).")
        self.name_preview.config(text="\n".join(lines))

    def _refresh_sweep_layers(self):
        vals = [f"{i + 1}: {l['material']}" for i, l in enumerate(self.layers[:-1])]
        cur = self.sweep_layer.get()
        self.sweep_cb.config(values=vals)
        if cur not in vals:
            self.sweep_layer.set(vals[0] if vals else "")
        self._update_sweep_preview()

    # --------------------------------------------------------------- run bar
    def _build_run_bar(self, parent):
        f = ttk.Frame(parent, padding=(4, 6))
        f.pack(fill="x")
        self.run_btn = ttk.Button(f, text="▶ Run model (F5)", command=self.run_single)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(f, text="Cancel", command=self.cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        self.pbar = ttk.Progressbar(f, length=180, maximum=1.0)
        self.pbar.pack(side="left", padx=6)
        self.status = ttk.Label(f, text="ready", foreground=INK2)
        self.status.pack(side="left")

    # ----------------------------------------------------------------- plots
    def _build_plots(self, parent):
        bar = ttk.Frame(parent, padding=(4, 4))
        bar.pack(fill="x")
        bar2 = ttk.Frame(parent, padding=(4, 0, 4, 4))
        bar2.pack(fill="x")
        ttk.Label(bar, text="Show run").pack(side="left")
        self.run_cb = ttk.Combobox(bar, textvariable=self.view_run, width=34,
                                   state="readonly")
        self.run_cb.pack(side="left", padx=4)
        self.run_cb.bind("<<ComboboxSelected>>", lambda e: self.redraw_run_plots())
        ttk.Checkbutton(bar, text="Overlay all runs", variable=self.overlay,
                        command=self.draw_drr).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear runs", command=self.clear_runs).pack(side="left")
        ttk.Label(bar2, text="Extra plots (ΔR/R and the stack are always "
                             "shown):").pack(side="left")
        for text, var in (("Components", self.show_components),
                          ("Kernels & absorption", self.show_kernels),
                          ("Strain map η(z,t)", self.show_strain)):
            ttk.Checkbutton(bar2, text=text, variable=var,
                            command=self._update_plot_tabs).pack(side="left", padx=3)

        self.ptabs = ttk.Notebook(parent)
        self.ptabs.pack(fill="both", expand=True)
        main = ttk.PanedWindow(self.ptabs, orient="vertical")
        self.ptabs.add(main, text="Stack & ΔR/R")

        self.stack_panel = PlotPanel(main, figsize=(8, 1.9))
        self.stack_panel.redraw = self.draw_stack
        main.add(self.stack_panel, weight=1)
        ttk.Label(self.stack_panel.controls, text="Widths").pack(side="left")
        cb = ttk.Combobox(self.stack_panel.controls, textvariable=self.stack_mode,
                          state="readonly", width=16,
                          values=["Equal widths", "Proportional", "Log thickness"])
        cb.pack(side="left", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self.draw_stack())
        self.stack_panel.canvas.mpl_connect("button_press_event", self._on_stack_click)

        self.drr_panel = PlotPanel(main, figsize=(8, 4.5))
        self.drr_panel.redraw = self.draw_drr
        main.add(self.drr_panel, weight=3)
        ttk.Label(self.drr_panel.controls, text="y scale").pack(side="left")
        cb = ttk.Combobox(self.drr_panel.controls, textvariable=self.scale_exp,
                          state="readonly", width=4,
                          values=["0", "3", "4", "5", "6"])
        cb.pack(side="left", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self.draw_drr())

        self.comp_panel = PlotPanel(self.ptabs)
        self.comp_panel.redraw = self.draw_components
        self.kern_panel = PlotPanel(self.ptabs)
        self.kern_panel.redraw = self.draw_kernels
        self.strain_panel = PlotPanel(self.ptabs)
        self.strain_panel.redraw = self.draw_strain
        self.ptabs.add(self.comp_panel, text="Components")
        self.ptabs.add(self.kern_panel, text="Kernels & absorption")
        self.ptabs.add(self.strain_panel, text="Strain map")
        self._update_plot_tabs()
        self.draw_drr()

    def _update_plot_tabs(self):
        for var, panel in ((self.show_components, self.comp_panel),
                           (self.show_kernels, self.kern_panel),
                           (self.show_strain, self.strain_panel)):
            if var.get():
                self.ptabs.add(panel)          # re-shows a hidden tab
                panel.redraw()
            else:
                self.ptabs.hide(panel)
        if self.show_strain.get() and self.batches:
            res = self.selected_res()
            if res is not None and res.get("eta_zt") is None:
                self.log("Strain map: the selected run has no η(z,t) recorded; "
                         "run again with 'Strain map' ticked.", "warn")

    # --- stack diagram
    def schedule_stack(self):
        if self._stack_job:
            self.after_cancel(self._stack_job)
        self._stack_job = self.after(150, self.draw_stack)

    def draw_stack(self):
        self._stack_job = None
        p = self.stack_panel
        if not self.layers:
            p.empty("No layers. Use 'Add layer' in the Layers tab.")
            return
        fig = p.clear()
        ax = fig.add_subplot()
        mode = self.stack_mode.get()
        ds = [S.parse_thickness(l) for l in self.layers[:-1]]
        known = [d for d in ds if d and d > 0]
        typical = float(np.median(known)) if known else 10.0
        dd = [d if d and d > 0 else typical for d in ds]
        if mode == "Proportional":
            widths = dd + [max(0.25 * sum(dd), max(dd, default=typical))]
        elif mode == "Log thickness":
            widths = [np.log10(d + 1) + 0.3 for d in dd]
            widths.append(max(widths, default=1.0))
        else:
            widths = [1.0] * len(dd) + [1.3]
        total = sum(widths)
        order = list(dict.fromkeys(l["material"].strip() for l in self.layers))
        air = 0.12 * total
        x = 0.0
        self._stack_x = []
        for i, (lay, w) in enumerate(zip(self.layers, widths)):
            name = lay["material"].strip() or "?"
            col = SERIES[order.index(lay["material"].strip()) % len(SERIES)]
            sub = i == len(self.layers) - 1
            ax.add_patch(Rectangle((x, 0), w, 1, facecolor=col, alpha=.85,
                                   edgecolor="white", lw=2,
                                   hatch="///" if sub else None))
            if sub:
                txt = f"{name}\nsubstrate →∞"
            else:
                d = ds[i]
                txt = f"{name}\n{lay['d']} {lay['d_unit']}" if d else f"{name}\n? nm"
            narrow = w / total < 0.07
            if w / total < 0.02 and not sub:     # hairline: no room for a label
                txt = ""
            ax.text(x + w / 2, 0.5, txt, ha="center", va="center",
                    rotation=90 if narrow else 0, fontsize=8 if narrow else 9,
                    color="white" if _dark(col) else INK, clip_on=True,
                    bbox=dict(facecolor=col, edgecolor="none", alpha=.85, pad=1)
                    if sub else None)
            if i == self.sel:
                ax.add_patch(Rectangle((x, -0.06), w, 1.12, fill=False,
                                       edgecolor=INK, lw=1.6))
            self._stack_x.append((x, x + w))
            x += w
        ax.annotate("", xy=(0, 0.5), xytext=(-air, 0.5),
                    arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.5))
        ax.text(-air / 2, 0.62, "pump\nprobe", ha="center", va="bottom",
                fontsize=8, color=INK2)
        ax.text(-air / 2, 0.38, "air", ha="center", va="top", fontsize=8,
                color=INK2)
        ax.set_xlim(-air, total)
        ax.set_ylim(-0.1, 1.1)
        ax.set_yticks([])
        for s in ("left", "right", "top"):
            ax.spines[s].set_visible(False)
        if mode == "Proportional":
            ax.set_xlabel("depth z (nm) — substrate drawn truncated; "
                          "zoom in to see thin layers")
        else:
            ax.set_xticks([])
            ax.spines["bottom"].set_visible(False)
        ax.set_title(f"Sample stack ({len(self.layers)} layers, "
                     f"{sum(d for d in ds if d):g} nm above the substrate; "
                     f"widths: {mode.lower()})")
        p.finish()

    def _on_stack_click(self, e):
        if e.inaxes is None or e.xdata is None or self.stack_panel.toolbar.mode:
            return
        for i, (a, b) in enumerate(getattr(self, "_stack_x", [])):
            if a <= e.xdata < b:
                self.tabs.select(1)
                self.tree.selection_set(str(i))
                self.tree.see(str(i))
                self.select_layer(i)
                return

    # --- result plots
    def all_runs(self):
        return [r for b in self.batches for r in b["runs"]]

    def selected_res(self):
        lab = self.view_run.get()
        for r in self.all_runs():
            if r["label"] == lab:
                return r["res"]
        return None

    def _run_colors(self, runs, sweep):
        if len(runs) == 1:
            return [SERIES[0]]
        if sweep or len(runs) > len(SERIES):
            cmap = colormaps["Blues"]
            return [cmap(0.35 + 0.65 * i / max(1, len(runs) - 1))
                    for i in range(len(runs))]
        return SERIES[:len(runs)]

    def draw_drr(self):
        p = self.drr_panel
        batches = self.batches if self.overlay.get() else self.batches[-1:]
        runs = [r for b in batches for r in b["runs"]]
        if not runs:
            p.empty("Run the model (F5) to see ΔR/R here.")
            return
        exp = int(self.scale_exp.get())
        sweep = len(batches) == 1 and batches[0]["kind"] == "sweep"
        ax = p.clear().add_subplot()
        for r, c in zip(runs, self._run_colors(runs, sweep)):
            res = r["res"]
            ax.plot(res["t_ps"], res["drr"] * 10 ** exp, color=c, lw=1.2,
                    label=r["label"])
        tmax = max(r["res"]["t_ps"].max() for r in runs)
        ax.set_xlim(0, tmax)
        ax.axhline(0, color=GRID_C, lw=.8, zorder=0)
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel(drr_label(exp))
        ax.set_title("Modelled differential reflectivity"
                     + (f" — sweep of {batches[0]['info']}" if sweep else ""))
        ax.grid(alpha=.6)
        if len(runs) > 1:
            ax.legend(fontsize=8, ncols=2 if len(runs) > 8 else 1).set_draggable(True)
        p.finish()

    def draw_components(self):
        p = self.comp_panel
        res = self.selected_res()
        if res is None:
            p.empty("Run the model to see the individual contributions.")
            return
        exp = int(self.scale_exp.get())
        ax = p.clear().add_subplot()
        for (k, lab), c in zip((("drr_strain", "strain (photoelastic)"),
                                ("drr_disp", "interface displacement"),
                                ("drr_lattice", "lattice temperature"),
                                ("drr_electron", "electron temperature")), SERIES):
            ax.plot(res["t_ps"], res[k] * 10 ** exp, color=c, lw=1.2, label=lab)
        zoom = min(60.0, res["t_ps"].max())
        ax.set_xlim(0, zoom)
        ax.axhline(0, color=GRID_C, lw=.8, zorder=0)
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel(drr_label(exp))
        ax.set_title(f"Components of ΔR/R — {self.view_run.get()} "
                     f"(first {zoom:g} ps; zoom out with the toolbar)")
        ax.grid(alpha=.6)
        ax.legend(fontsize=8).set_draggable(True)
        p.finish()

    def draw_kernels(self):
        p = self.kern_panel
        res = self.selected_res()
        if res is None:
            p.empty("Run the model to see the sensitivity kernel and the "
                    "pump absorption profile.")
            return
        fig = p.clear()
        ax = fig.subplots(1, 2)
        zmax = res["grid"]["d_stack"] / M.nm * 1.25 + 50
        fe = res["f_eta"]
        ax[0].plot(res["z_nm"], fe / (np.abs(fe).max() or 1), color=SERIES[0], lw=1.2)
        ax[0].set_ylabel("f_η(z) (normalised)")
        ax[0].set_title("Photoelastic sensitivity kernel")
        ax[1].plot(res["z_nm"], res["W"] / (res["W"].max() or 1), color=SERIES[1],
                   lw=1.2)
        ax[1].set_ylabel("absorbed energy density (norm.)")
        ax[1].set_title(f"Pump absorption — transducer: {res['transducer']}")
        for a in ax:
            for e in res["grid"]["edges"][1:]:
                a.axvline(e / M.nm, color=INK2, lw=.7, ls="--")
            a.set_xlim(0, zmax)
            a.set_xlabel("depth z (nm)")
            a.grid(alpha=.6)
        p.finish()

    def draw_strain(self):
        p = self.strain_panel
        res = self.selected_res()
        if res is None or res.get("eta_zt") is None:
            p.empty("Tick 'Strain map η(z,t)' and run the model to record the "
                    "strain field." if res is None else
                    "This run was made without recording η(z,t).\n"
                    "Keep 'Strain map η(z,t)' ticked and run again.")
            return
        fig = p.clear()
        ax = fig.add_subplot()
        eta, t, z = res["eta_zt"], res["eta_t_ps"], res["eta_z_nm"]
        vmax = float(np.percentile(np.abs(eta), 99.5)) or 1.0
        im = ax.imshow(eta.T, aspect="auto", origin="upper", cmap="RdBu_r",
                       vmin=-vmax, vmax=vmax, interpolation="nearest",
                       extent=[t[0], t[-1], z[-1], z[0]])
        for e in res["grid"]["edges"][1:]:
            ax.axhline(e / M.nm, color=INK2, lw=.6, ls="--")
        ax.axhline(res["grid"]["z_sponge"] / M.nm, color=INK, lw=.8, ls=":")
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel("depth z (nm)")
        ax.set_title(f"Strain η(z, t) — {self.view_run.get()}  "
                     f"(dotted line: start of the absorbing sponge)")
        cb = fig.colorbar(im, ax=ax)
        cb.ax._diffr_cbar = True
        cb.set_label("strain η")
        p.finish()

    def redraw_run_plots(self):
        for var, panel in ((self.show_components, self.comp_panel),
                           (self.show_kernels, self.kern_panel),
                           (self.show_strain, self.strain_panel)):
            if var.get():
                panel.redraw()

    def _refresh_run_list(self, select_last=True):
        labels = [r["label"] for r in self.all_runs()]
        self.run_cb.config(values=labels)
        if labels and (select_last or self.view_run.get() not in labels):
            self.view_run.set(labels[-1])
        elif not labels:
            self.view_run.set("")

    def clear_runs(self):
        if self.batches and not messagebox.askyesno(
                "Clear runs", "Remove all runs from the plots? Export anything "
                              "you want to keep first."):
            return
        self.batches = []
        self._refresh_run_list()
        self.draw_drr()
        self.redraw_run_plots()

    # ------------------------------------------------------------ running
    def _cfg_entries(self):
        return {k: dict(value=v.get(), unit=u.get())
                for k, (v, u) in self.cfg_vars.items()}

    def _flags(self):
        return {k: v.get() for k, v in self.flag_vars.items()}

    def _prepare(self, layers, verbose=True):
        """Convert inputs and build the sample; returns (cfg, stack, materials)
        or None after reporting the problems."""
        try:
            cfg, cfg_rep = S.resolve_config(self._cfg_entries(), self._flags())
            stack, mats, rep, warns = S.resolve_layers(layers, cfg, self.cache)
        except S.InputError as e:
            self.log("Cannot run — please fix these inputs:", "err")
            for m in e.errors:
                self.log("  • " + m, "err")
            shown = e.errors[:25] + ([f"… and {len(e.errors) - 25} more (see log)"]
                                     if len(e.errors) > 25 else [])
            messagebox.showerror("Input problems", "\n".join(shown))
            return None
        except Exception as e:
            self.log(traceback.format_exc(), "err")
            messagebox.showerror("Error", f"{type(e).__name__}: {e}")
            return None
        if self.show_strain.get():
            cfg["save_strain"] = True
        if verbose:
            self.log("Experiment parameters (input -> model units)", "head")
            self.log("\n".join(cfg_rep))
            self.log("Layers (input -> model units)", "head")
            self.log("\n".join(rep))
            self.log(S.sample_table(stack, mats, cfg))
        for w in warns:
            self.log("!! " + w, "warn")
        return cfg, stack, mats

    def _snapshot(self, layers):
        return dict(config=self._cfg_entries(), flags=self._flags(),
                    layers=copy.deepcopy(layers))

    def run_single(self):
        if self._busy():
            return
        prep = self._prepare(self.layers)
        if prep is None:
            return
        cfg, stack, mats = prep
        self.run_counter += 1
        label = f"run {self.run_counter}"
        self.log(f"— {label}: started", "head")
        self._start([dict(label=label, cfg=cfg, stack=stack, materials=mats,
                          inputs=self._snapshot(self.layers))],
                    dict(kind="single", info=""))

    def run_sweep(self):
        if self._busy():
            return
        try:
            idx, vals, unit, bases, cbase = self._sweep_plan()
        except ValueError as e:
            messagebox.showerror("Sweep", str(e))
            return
        if len(vals) > 50 and not messagebox.askyesno(
                "Sweep", f"This starts {len(vals)} simulations. Continue?"):
            return
        existing = [p for b in (bases or []) + ([cbase] if cbase else [])
                    for p in (b + ".csv", b + ".npz") if os.path.exists(p)]
        if existing and not messagebox.askyesno(
                "Sweep", f"{len(existing)} output file(s) already exist, e.g.\n"
                         f"{existing[0]}\n\nOverwrite them?"):
            return
        for b in (bases or []) + ([cbase] if cbase else []):
            os.makedirs(os.path.dirname(b) or ".", exist_ok=True)
        name = self.layers[idx]["material"]
        jobs = []
        self.run_counter += 1
        tag = f"sweep {self.run_counter}"
        for j, v in enumerate(vals):
            layers = copy.deepcopy(self.layers)
            layers[idx]["d"], layers[idx]["d_unit"] = f"{v:g}", unit
            prep = self._prepare(layers, verbose=(j == 0))
            if prep is None:
                return
            cfg, stack, mats = prep
            jobs.append(dict(label=f"{tag}: L{idx + 1} {name} = {v:g} {unit}",
                             cfg=cfg, stack=stack, materials=mats,
                             inputs=self._snapshot(layers),
                             sweep_value=v, sweep_unit=unit,
                             sweep_nm=U.to_base(v, "thickness", unit),
                             save_base=bases[j] if bases else None))
        self.log(f"— {tag}: layer {idx + 1} ({name}) at {len(vals)} thicknesses: "
                 f"{vals[0]:g} … {vals[-1]:g} {unit}", "head")
        if bases or cbase:
            self.log(f"   output folder: {os.path.dirname((bases or [cbase])[0])}")
        self._start(jobs, dict(kind="sweep", combined=cbase,
                               info=f"layer {idx + 1} ({name}) thickness"))

    def _busy(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Busy", "A calculation is running. Wait or "
                                        "press Cancel.")
            return True
        return False

    def _start(self, jobs, batch):
        self.cancel_ev.clear()
        self.run_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.pbar["value"] = 0
        self._pending = dict(batch, runs=[], n_jobs=len(jobs))
        self.worker = threading.Thread(target=self._work, args=(jobs,), daemon=True)
        self.worker.start()

    def _work(self, jobs):
        """Worker thread: only numpy here, all Tk work goes through the queue."""
        n = len(jobs)
        for j, job in enumerate(jobs):
            def prog(f, j=j, lab=job["label"]):
                self.queue.put(("progress", (j + f) / n, lab))
            try:
                res = M.run_model(job["stack"], job["materials"], job["cfg"],
                                  verbose=False, progress=prog,
                                  cancel=self.cancel_ev)
            except M.Cancelled:
                self.queue.put(("cancelled", None, None))
                return
            except Exception:
                self.queue.put(("error", traceback.format_exc(), job["label"]))
                return
            self.queue.put(("one", job, res))
        self.queue.put(("done", None, None))

    def _poll(self):
        try:
            while True:
                kind, a, b = self.queue.get_nowait()
                if kind == "progress":
                    self.pbar["value"] = a
                    self.status.config(text=f"{b}: {100 * a:.0f} %")
                elif kind == "one":
                    a["res"] = b
                    self._pending["runs"].append(a)
                    self.log(f"{a['label']} finished")
                    self.log(M.run_summary(b))
                    if a.get("save_base"):
                        self._save_run(a, a["save_base"])
                elif kind in ("done", "cancelled", "error"):
                    self._finish(kind, a, b)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self, kind, a, b):
        self.run_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        batch = self._pending
        if kind == "error":
            self.log(f"{b} failed:\n{a}", "err")
            messagebox.showerror("Model error", a.strip().splitlines()[-1])
        elif kind == "cancelled":
            self.log(f"cancelled after {len(batch['runs'])} of "
                     f"{batch['n_jobs']} runs", "warn")
        if batch["runs"] and batch.get("combined"):
            try:
                self._write_sweep(batch, batch["combined"])
            except Exception as e:
                self.log(f"could not write the combined file: "
                         f"{type(e).__name__}: {e}", "err")
        if batch["runs"]:
            self.batches.append(batch)
            self._refresh_run_list()
            self.draw_drr()
            self.redraw_run_plots()
        self.pbar["value"] = 1.0 if kind == "done" else 0
        self.status.config(text={"done": "done", "cancelled": "cancelled",
                                 "error": "error"}[kind])

    def cancel(self):
        if self.worker is not None and self.worker.is_alive():
            self.cancel_ev.set()
            self.status.config(text="cancelling…")

    # ------------------------------------------------------------- export
    def _selected_run(self):
        lab = self.view_run.get()
        for b in self.batches:
            for r in b["runs"]:
                if r["label"] == lab:
                    return b, r
        return None, None

    def _save_run(self, run, base):
        res = run["res"]
        try:
            files = M.save_result(
                res, base, save_strain_map=res.get("eta_zt") is not None,
                extra_meta=dict(label=run["label"], gui_inputs=run["inputs"],
                                materials_model_units=S.materials_as_json(
                                    res["stack"], res["materials"])))
        except Exception as e:
            self.log(f"could not save {base}: {type(e).__name__}: {e}", "err")
            return False
        self.log("written: " + ", ".join(files))
        return True

    def _write_sweep(self, b, base):
        t = b["runs"][0]["res"]["t_ps"]
        cols = lambda k: np.column_stack([r["res"][k] for r in b["runs"]])
        d_nm = np.array([r["sweep_nm"] for r in b["runs"]])
        header = "t_ps," + ",".join(f"dR_over_R_d={r['sweep_value']:g}"
                                    f"{FILE_UNIT_NAMES.get(r['sweep_unit'], r['sweep_unit'])}"
                                    for r in b["runs"])
        np.savetxt(base + ".csv", np.column_stack([t, cols("drr")]), delimiter=",",
                   header=header, comments="", fmt="%.8e", encoding="utf-8")
        np.savez_compressed(
            base + ".npz", t_ps=t, drr=cols("drr"), thickness_nm=d_nm,
            drr_strain=cols("drr_strain"), drr_disp=cols("drr_disp"),
            drr_lattice=cols("drr_lattice"), drr_electron=cols("drr_electron"),
            labels=np.array([r["label"] for r in b["runs"]]),
            meta=json.dumps(dict(swept=b["info"],
                                 gui_inputs=b["runs"][0]["inputs"])))
        self.log(f"written: {base}.csv, {base}.npz")

    def export_run(self):
        _, run = self._selected_run()
        if run is None:
            messagebox.showinfo("Export", "No run to export yet.")
            return
        p = filedialog.asksaveasfilename(
            title="Export run — basename for .npz and .csv",
            defaultextension=".npz", initialfile=S.format_name(
                run["label"].replace(" ", "_")),
            filetypes=[("NumPy archive + CSV", "*.npz")])
        if p:
            self._save_run(run, os.path.splitext(p)[0])

    def export_sweep(self):
        b, _ = self._selected_run()
        if b is None or b["kind"] != "sweep":
            sweeps = [x for x in self.batches if x["kind"] == "sweep"]
            if not sweeps:
                messagebox.showinfo("Export sweep", "No sweep has been run yet.")
                return
            b = sweeps[-1]
        p = filedialog.asksaveasfilename(
            title="Export sweep — basename for .csv and .npz",
            defaultextension=".csv", initialfile="sweep",
            filetypes=[("CSV + NumPy archive", "*.csv")])
        if not p:
            return
        try:
            self._write_sweep(b, os.path.splitext(p)[0])
        except Exception as e:
            messagebox.showerror("Export sweep", f"{type(e).__name__}: {e}")

    # ----------------------------------------------------------- sessions
    def _state(self):
        return dict(config=self._cfg_entries(), flags=self._flags(),
                    layers=self.layers,
                    plots=dict(components=self.show_components.get(),
                               kernels=self.show_kernels.get(),
                               strain=self.show_strain.get(),
                               stack_mode=self.stack_mode.get(),
                               scale_exp=self.scale_exp.get(),
                               overlay=self.overlay.get()),
                    sweep=dict(layer=self.sweep_layer.get(),
                               start=self.sweep_start.get(),
                               end=self.sweep_end.get(),
                               step=self.sweep_step.get(),
                               unit=self.sweep_unit.get(),
                               save=self.sweep_save.get(),
                               folder=self.sweep_dir.get(),
                               name=self.sweep_name.get(),
                               combined=self.sweep_combined.get(),
                               combined_name=self.sweep_cname.get()))

    def _apply_state(self, st):
        for k, (v, u) in self.cfg_vars.items():
            e = st["config"].get(k)
            if e:
                v.set(e["value"])
                u.set(e["unit"])
        for k, v in self.flag_vars.items():
            v.set(bool(st["flags"].get(k, True)))
        pl = st.get("plots", {})
        self.show_components.set(pl.get("components", False))
        self.show_kernels.set(pl.get("kernels", False))
        self.show_strain.set(pl.get("strain", False))
        self.stack_mode.set(pl.get("stack_mode", "Equal widths"))
        self.scale_exp.set(pl.get("scale_exp", "3"))
        self.overlay.set(pl.get("overlay", False))
        sw = st.get("sweep", {})
        for var, key in ((self.sweep_start, "start"), (self.sweep_end, "end"),
                         (self.sweep_step, "step"), (self.sweep_unit, "unit"),
                         (self.sweep_dir, "folder"), (self.sweep_name, "name"),
                         (self.sweep_cname, "combined_name"),
                         (self.sweep_save, "save"),
                         (self.sweep_combined, "combined")):
            if key in sw:
                var.set(sw[key])
        self.layers = st["layers"]
        self.sel = None
        self.refresh_tree(0)
        if sw.get("layer") in self.sweep_cb.cget("values"):
            self.sweep_layer.set(sw["layer"])
        self._update_plot_tabs()

    def save_session(self):
        if not self.session_path:
            return self.save_session_as()
        try:
            S.save_session(self.session_path, self._state())
        except Exception as e:
            messagebox.showerror("Save session", f"{type(e).__name__}: {e}")
            return
        self.log(f"session saved: {self.session_path}")
        self.title(f"diffR thin-film model — {os.path.basename(self.session_path)}")

    def save_session_as(self):
        p = filedialog.asksaveasfilename(
            title="Save session", defaultextension=".json",
            filetypes=[("diffR session", "*.json")])
        if p:
            self.session_path = p
            self.save_session()

    def open_session(self):
        p = filedialog.askopenfilename(title="Open session",
                                       filetypes=[("diffR session", "*.json"),
                                                  ("All", "*.*")])
        if not p:
            return
        try:
            st = S.load_session(p)
        except Exception as e:
            messagebox.showerror("Open session", f"{type(e).__name__}: {e}")
            return
        self._apply_state(st)
        self.session_path = p
        self.title(f"diffR thin-film model — {os.path.basename(p)}")
        self.log(f"session loaded: {p}")

    def new_session(self):
        if not messagebox.askyesno("New session", "Discard the current inputs and "
                                                  "start from the example stack?"):
            return
        st = dict(config=S.default_config_entries(), flags=S.default_flags(),
                  layers=[])
        self._apply_state(st)
        self.session_path = None
        self.title("diffR thin-film model")
        self.load_example()

    def load_example(self):
        self.layers = []
        for name, d in EXAMPLE_STACK:
            lay = S.new_layer(name, d)
            S.apply_literature(lay)
            self.layers.append(lay)
        self.sel = None
        self.refresh_tree(0)
        self.log("Example stack (notebook section 13) loaded with literature "
                 "values pre-filled (marked ● lit.). n is never pre-filled: "
                 "type n, k for each layer or choose a dispersion file.", "warn")

    def import_conf(self):
        p = filedialog.askopenfilename(title="Import fs-sonar conf file",
                                       filetypes=[("conf file", "*.txt"),
                                                  ("All", "*.*")])
        if not p:
            return
        try:
            _, folder = M.parse_conf(p)
            if not folder or not os.path.isdir(folder):
                messagebox.showinfo(
                    "Materials folder",
                    f"The conf file names the materials folder\n\n{folder}\n\n"
                    f"which is not readable here. Choose the folder that holds "
                    f"the n,k files and properties.txt.")
                folder = filedialog.askdirectory(title="Materials folder") or folder
            layers, used, msgs = S.layers_from_conf(p, folder)
        except Exception as e:
            self.log(traceback.format_exc(), "err")
            messagebox.showerror("Import conf", f"{type(e).__name__}: {e}")
            return
        if not layers:
            messagebox.showerror("Import conf", "No layers found in the file.")
            return
        if self.layers and not messagebox.askyesno(
                "Import conf", f"Replace the current {len(self.layers)} layers "
                               f"with the {len(layers)} layers from the conf file?"):
            return
        self.layers = layers
        self.sel = None
        self.refresh_tree(0)
        self.log(f"imported {os.path.basename(p)} (materials folder: {used})",
                 "head")
        for m in msgs:
            self.log("  " + m, "warn")

    # --------------------------------------------------------------- misc
    def log(self, text, tag=None):
        self.log_w.insert("end", text.rstrip("\n") + "\n", tag or ())
        self.log_w.see("end")

    def show_units(self):
        lines = ["Every value is converted to the model's internal unit before "
                 "the run:\n"]
        names = dict(wavelength="Wavelength", thickness="Layer thickness",
                     grid="Grid lengths (dz, substrate, sponge)",
                     time="Times (t, Δt, FWHM, τ_th)", tau="τ_ep",
                     fluence="Fluence", density="Density ρ",
                     velocity="Sound velocity v", heat_capacity="Cp",
                     expansion="α_lin", modulus="Bulk modulus B",
                     e_heat_capacity="Ce", per_kelvin="dñ/dT, dñ/dTₑ",
                     dimensionless="dñ/dη, p, CFL")
        for kind, us in U.UNITS.items():
            base = U.base_unit(kind)
            conv = ", ".join(
                f"1 {u} = {f:g} {base}" if f != "eV" else f"λ[nm] = {U.HC_EV_NM}/E[eV]"
                for u, f in us.items() if u != base)
            lines.append(f"{names[kind]}: model unit {base}"
                         + (f";  {conv}" if conv else ""))
        messagebox.showinfo("Units and conversions", "\n".join(lines))

    def show_help(self):
        messagebox.showinfo("How to use", HELP_TEXT)

    def on_close(self):
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno("Quit", "A calculation is running. Quit "
                                               "anyway?"):
                return
            self.cancel_ev.set()
        self.destroy()


# units written into file names without non-ASCII characters
FILE_UNIT_NAMES = {"µm": "um", "Å": "A"}


def _dark(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5


CFG_GROUP_LASER = ["lambda_pump_nm", "lambda_probe_nm", "pump_fluence_J_m2",
                   "pulse_fwhm_ps", "tau_th_ps"]
CFG_GROUP_TIME = ["t_min_ps", "t_max_ps", "dt_out_ps"]
CFG_GROUP_GRID = ["dz_nm", "substrate_model_nm", "sponge_nm", "cfl"]

PROP_HELP = dict(
    rho="Mass density of the layer.",
    v="Longitudinal sound velocity. Sets echo times (2d/v) and Brillouin "
      "frequencies.",
    Cp="Isobaric specific heat capacity of the lattice.",
    alpha_lin="Linear thermal expansion coefficient; the thermal stress is "
              "B_th = 3·B·α.",
    B_bulk="Bulk modulus; the thermal stress is B_th = 3·B·α.",
    Ce="Electron heat capacity per volume. Leave blank (0) for insulators; "
       "then the layer has no electron channel.",
    tau_ep_ps="Electron–phonon coupling time. The transducer layer's value "
              "sets the electron/lattice temperature shapes. Blank = 1 ps.",
    dn_deta="Photoelastic coupling d(n+ik)/dη (dimensionless).",
    pe="Photoelastic constant p; converted to dñ/dη = −p·ñ³/2 at the probe "
       "wavelength.",
    dn_dT="Thermo-optic coefficient d(n+ik)/dT_lattice.",
    dn_dTe="Electronic thermo-optic coefficient d(n+ik)/dT_electron "
           "(metals). Blank = 0.",
)

HELP_TEXT = """1. Experiment tab: pump/probe wavelengths, fluence, pulse length, time
   axis and grid. Choose the unit next to each value; the grey text shows
   the value the model receives (nm, ps, SI).

2. Layers tab: build the stack from the top (light side) down. The last
   layer is the semi-infinite substrate. For each layer give:
   - n, k at the pump and probe wavelengths, typed or from a dispersion
     file (interpolated, never extrapolated);
   - ρ, v, Cp, α, B;
   - dñ/dη (or the photoelastic constant p) and dñ/dT;
   - optionally Ce, τ_ep, dñ/dTₑ for metals.
   'Fill from literature' pre-fills known materials; such values are
   marked ● lit. and reported as placeholders when you run.
   'Literature values…' (or double-clicking a layer) lists them all.

3. Run (F5). ΔR/R and the stack are always shown; tick Components,
   Kernels or Strain map for more plots. Use the toolbar (zoom, pan,
   home, save image), the mouse wheel to zoom, and 'Axes & labels…' to
   edit titles, labels, limits and scales.

4. Thickness sweep tab: pick a layer, give start, end and increment,
   and optionally a folder and a file-name pattern (e.g.
   dRR_{material}_{d}{unit}) to save one reflectivity file per
   thickness plus one combined file.

5. File menu: import a conf_file, save/open the whole session, export
   the selected run (.npz + .csv, with all inputs in the metadata) or a
   sweep."""


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
