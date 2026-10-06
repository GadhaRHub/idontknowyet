#!/usr/bin/env python3
"""Interactive GUI for the diffR thin-film pump-probe model.

    python diffr_gui.py

Every input is typed with a unit chosen from a menu; the grey text next to
each field shows the value converted to the model's internal units (nm, ps,
SI), which is what the solver in diffr_model.py receives.

Needs numpy and matplotlib; tkinter ships with Python.
"""

import copy
import os
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
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
try:
    import background as B          # needs scipy
except ImportError as _e:           # the rest of the GUI works without it
    B, BG_IMPORT_ERROR = None, str(_e)
import outputs as O
import param_import as PI
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

    Label/scale edits and colour choices are remembered and re-applied each
    time the panel is redrawn, until they are reset.
    """

    def __init__(self, master, figsize=(7, 4), colors=True):
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
        if colors:
            b = ttk.Button(bar, text="Colours…", command=self.edit_colors)
            b.pack(side="right", padx=2)
            Tooltip(b, "Change the colour of each curve, colour all curves "
                       "from a colour map, or change the colour map of an "
                       "image plot")
        b = ttk.Button(bar, text="Axes & labels…", command=self.edit_axes)
        b.pack(side="right", padx=(8, 2))
        Tooltip(b, "Edit title, axis labels, limits, log/linear scale, grid, "
                   "legend and font size")
        self.toolbar = NavigationToolbar2Tk(self.canvas, bar, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(side="left", fill="x", expand=True)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.overrides = {}
        self.styles = {}            # axes index -> colour choices
        self.colorbars = []
        self.canvas.mpl_connect("scroll_event", self._on_scroll)

    # --- drawing protocol: clear() ... draw your axes ... finish()
    def clear(self):
        self.fig.clear()
        self.colorbars = []
        return self.fig

    def finish(self):
        self.apply_styles()
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

    # --- user colour choices
    @staticmethod
    def data_lines(ax):
        """The curves of an axes (not zero lines or interface markers)."""
        return [l for l in ax.get_lines() if l.get_gid() != "ref"]

    @staticmethod
    def line_key(line, j):
        lab = line.get_label()
        return f"curve {j + 1}" if lab.startswith("_") else lab

    def apply_styles(self):
        axes = self.data_axes()
        for i, ax in enumerate(axes):
            st = self.styles.get(i)
            if not st:
                continue
            lines = self.data_lines(ax)
            if st.get("line_cmap"):
                for l, c in zip(lines, cmap_colors(st["line_cmap"], len(lines))):
                    l.set_color(c)
            for j, l in enumerate(lines):
                c = st.get("lines", {}).get(self.line_key(l, j))
                if c:
                    l.set_color(c)
            if st.get("image_cmap"):
                for im in ax.images:
                    im.set_cmap(st["image_cmap"])
            leg = ax.get_legend()
            if leg is not None:                 # legends copy the colours
                fs = leg.get_texts()[0].get_fontsize() if leg.get_texts() else None
                vis = leg.get_visible()
                new = ax.legend(fontsize=fs, ncols=getattr(leg, "_ncols", 1))
                new.set_draggable(True)
                new.set_visible(vis)
        for cb in self.colorbars:
            cb.update_normal(cb.mappable)

    def edit_colors(self):
        axes = self.data_axes()
        if not axes or not any(a.axison for a in axes):
            messagebox.showinfo("Colours", "Nothing plotted yet.", parent=self)
            return
        PlotColorDialog(self)

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


LINE_CMAPS = ["tab10", "Blues", "viridis", "plasma", "cividis", "magma",
              "coolwarm", "Greys", "Dark2", "Set1"]
IMAGE_CMAPS = ["RdBu_r", "coolwarm", "seismic", "PuOr_r", "bwr", "viridis",
               "inferno", "gray"]


def cmap_colors(name, n):
    """n colours from a matplotlib colour map (cycled for qualitative maps,
    spread over the map otherwise)."""
    cm = colormaps[name]
    if getattr(cm, "N", 256) <= 20:            # qualitative map, e.g. tab10
        return [cm(i % cm.N) for i in range(n)]
    if n == 1:
        return [cm(0.65)]
    return [cm(0.25 + 0.7 * i / (n - 1)) for i in range(n)]


class PlotColorDialog(tk.Toplevel):
    """Colours of the curves (and image colour map) of one plot panel."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.title("Plot colours")
        self.transient(panel.winfo_toplevel())
        self.minsize(420, 200)
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        self.which = tk.StringVar()
        axes = panel.data_axes()
        names = [f"{i + 1}: {a.get_title() or '(untitled)'}"
                 for i, a in enumerate(axes)]
        self.which.set(names[0])
        if len(axes) > 1:
            ttk.Label(top, text="Plot").pack(side="left")
            cb = ttk.Combobox(top, textvariable=self.which, values=names,
                              state="readonly", width=48)
            cb.pack(side="left", padx=4)
            cb.bind("<<ComboboxSelected>>", lambda e: self.build())
        self.body = ScrollableFrame(self)
        self.body.pack(fill="both", expand=True, padx=10)
        bb = ttk.Frame(self, padding=10)
        bb.pack(fill="x")
        ttk.Button(bb, text="Reset colours of this plot",
                   command=self.reset).pack(side="left")
        ttk.Button(bb, text="Close", command=self.destroy).pack(side="right")
        self.build()

    def idx(self):
        return int(self.which.get().split(":")[0]) - 1

    def style(self):
        return self.panel.styles.setdefault(self.idx(), {"lines": {}})

    def build(self):
        p = self.body.inner
        for w in p.winfo_children():
            w.destroy()
        ax = self.panel.data_axes()[self.idx()]
        lines = PlotPanel.data_lines(ax)
        r = 0
        if lines:
            ttk.Label(p, text="Curves", font=("TkDefaultFont", 9, "bold")).grid(
                row=r, column=0, sticky="w", pady=(0, 2))
            r += 1
            for j, l in enumerate(lines):
                key = PlotPanel.line_key(l, j)
                ttk.Label(p, text=key).grid(row=r, column=0, sticky="w", padx=(0, 8))
                sw = tk.Label(p, width=6, relief="solid", borderwidth=1,
                              background=matplotlib.colors.to_hex(l.get_color()),
                              cursor="hand2")
                sw.grid(row=r, column=1, pady=1)
                sw.bind("<Button-1>", lambda e, k=key, ln=l: self.pick(k, ln))
                ttk.Button(p, text="Change…", command=lambda k=key, ln=l:
                           self.pick(k, ln)).grid(row=r, column=2, padx=4)
                r += 1
            f = ttk.Frame(p)
            f.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))
            ttk.Label(f, text="Colour all curves from").pack(side="left")
            self.line_cmap = tk.StringVar(value=self.style().get("line_cmap") or
                                          LINE_CMAPS[0])
            ttk.Combobox(f, textvariable=self.line_cmap, values=LINE_CMAPS,
                         width=10).pack(side="left", padx=4)
            ttk.Button(f, text="Apply", command=self.apply_line_cmap).pack(side="left")
            r += 1
        if ax.images:
            f = ttk.Frame(p)
            f.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))
            ttk.Label(f, text="Image colour map").pack(side="left")
            self.img_cmap = tk.StringVar(value=self.style().get("image_cmap") or
                                         ax.images[0].get_cmap().name)
            cb = ttk.Combobox(f, textvariable=self.img_cmap, values=IMAGE_CMAPS,
                              width=10)
            cb.pack(side="left", padx=4)
            ttk.Button(f, text="Apply", command=self.apply_img_cmap).pack(side="left")
            ttk.Label(p, foreground=INK2, text="Any matplotlib colour map name "
                      "can be typed; add _r to reverse it.").grid(
                row=r + 1, column=0, columnspan=3, sticky="w")
        if not lines and not ax.images:
            ttk.Label(p, text="This plot has no curves.").grid(row=0, column=0)

    def _redraw(self):
        self.panel.apply_styles()
        self.panel.canvas.draw_idle()
        self.build()

    def pick(self, key, line):
        _, hexcol = colorchooser.askcolor(
            color=matplotlib.colors.to_hex(line.get_color()), parent=self,
            title=f"Colour for {key}")
        if hexcol:
            self.style().setdefault("lines", {})[key] = hexcol
            self._redraw()

    def apply_line_cmap(self):
        name = self.line_cmap.get().strip()
        if name not in colormaps:
            messagebox.showerror("Colours", f"Unknown colour map {name!r}.",
                                 parent=self)
            return
        st = self.style()
        st["line_cmap"], st["lines"] = name, {}
        self._redraw()

    def apply_img_cmap(self):
        name = self.img_cmap.get().strip()
        if name not in colormaps:
            messagebox.showerror("Colours", f"Unknown colour map {name!r}.",
                                 parent=self)
            return
        self.style()["image_cmap"] = name
        self._redraw()

    def reset(self):
        self.panel.styles.pop(self.idx(), None)
        if hasattr(self.panel, "redraw"):
            self.panel.redraw()
        self.build()


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


class StackColorDialog(tk.Toplevel):
    """Pick the colour of each material in the stack diagram."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Stack colours")
        self.transient(app)
        self.resizable(False, False)
        self.body = ttk.Frame(self, padding=10)
        self.body.pack(fill="both", expand=True)
        bb = ttk.Frame(self, padding=(10, 0, 10, 10))
        bb.pack(fill="x")
        ttk.Button(bb, text="Reset all to default", command=self.reset_all
                   ).pack(side="left")
        ttk.Button(bb, text="Close", command=self.destroy).pack(side="right")
        self.build()

    def build(self):
        for w in self.body.winfo_children():
            w.destroy()
        names = list(dict.fromkeys(l["material"].strip() for l in self.app.layers))
        ttk.Label(self.body, text="Colours are per material: layers of the "
                  "same material share one.", foreground=INK2).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        for r, name in enumerate(names, 1):
            col = self.app.material_color(name, names)
            ttk.Label(self.body, text=name or "(unnamed)").grid(
                row=r, column=0, sticky="w", padx=(0, 8))
            sw = tk.Label(self.body, width=6, background=col, relief="solid",
                          borderwidth=1, cursor="hand2")
            sw.grid(row=r, column=1, pady=2)
            sw.bind("<Button-1>", lambda e, n=name: self.pick(n))
            ttk.Button(self.body, text="Change…",
                       command=lambda n=name: self.pick(n)).grid(
                row=r, column=2, padx=4)
            state = "normal" if name in self.app.stack_colors else "disabled"
            ttk.Button(self.body, text="Default", state=state,
                       command=lambda n=name: self.reset(n)).grid(row=r, column=3)

    def pick(self, name):
        _, hexcol = colorchooser.askcolor(
            color=self.app.material_color(name), parent=self,
            title=f"Colour for {name}")
        if hexcol:
            self.app.stack_colors[name] = hexcol
            self.changed()

    def reset(self, name):
        self.app.stack_colors.pop(name, None)
        self.changed()

    def reset_all(self):
        self.app.stack_colors.clear()
        self.changed()

    def changed(self):
        self.app.draw_stack()
        self.build()


class CutoffDialog(tk.Toplevel):
    """Asked when background subtraction is switched on: fit only after a
    delay, or the whole trace?"""

    def __init__(self, app):
        super().__init__(app)
        self.app, self.ok = app, False
        ov = app.out
        self.title("Background subtraction — fit range")
        self.transient(app)
        self.resizable(False, False)
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, wraplength=420, justify="left", text=(
            "Should the double-exponential background be fitted only to "
            "delays after a cut-off (e.g. to leave out the electronic peak "
            "around t = 0)?")).grid(row=0, column=0, columnspan=4, sticky="w")
        self.use = tk.BooleanVar(value=ov["bg_use_cut"].get())
        self.cut = tk.StringVar(value=ov["bg_cut"].get())
        self.unit = tk.StringVar(value=ov["bg_cut_unit"].get())
        ttk.Radiobutton(f, text="Yes, only delays after", variable=self.use,
                        value=True).grid(row=1, column=0, sticky="w", pady=(8, 0))
        e = ttk.Entry(f, textvariable=self.cut, width=8)
        e.grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Combobox(f, textvariable=self.unit, width=5, state="readonly",
                     values=U.choices("time")).grid(row=1, column=2, sticky="w",
                                                    padx=2, pady=(8, 0))
        ttk.Radiobutton(f, text="No, fit the whole time axis", variable=self.use,
                        value=False).grid(row=2, column=0, columnspan=3,
                                          sticky="w", pady=(4, 0))
        self.msg = ttk.Label(f, foreground="#c00000")
        self.msg.grid(row=3, column=0, columnspan=4, sticky="w")
        bb = ttk.Frame(f)
        bb.grid(row=4, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(bb, text="OK", command=self.accept).pack(side="left", padx=4)
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="left")
        e.focus_set()
        self.bind("<Return>", lambda ev: self.accept())
        self.grab_set()
        self.wait_window()

    def accept(self):
        if self.use.get():
            try:
                float(self.cut.get())
            except ValueError:
                self.msg.config(text="Enter the cut-off delay as a number.")
                return
        ov = self.app.out
        ov["bg_use_cut"].set(self.use.get())
        ov["bg_cut"].set(self.cut.get())
        ov["bg_cut_unit"].set(self.unit.get())
        self.ok = True
        self.destroy()


class ImportReviewDialog(tk.Toplevel):
    """Shows everything read from a parameter file, where each value came
    from, and what is missing, before it replaces the current inputs."""

    SOURCE_TAGS = {"file": "file", "from n,k file": "file",
                   "literature (file)": "lit", "literature (built-in)": "lit",
                   "MISSING": "missing", "not in file (kept)": "kept",
                   "blank (default)": "kept"}

    def __init__(self, app, path):
        super().__init__(app)
        self.app, self.path, self.mat_dir = app, path, None
        self.title(f"Review import — {os.path.basename(path)}")
        self.transient(app)
        W = min(1100, app.winfo_screenwidth() - 80)
        H = min(700, app.winfo_screenheight() - 120)
        self.geometry(f"{W}x{H}")
        top = ttk.Frame(self, padding=(10, 8, 10, 0))
        top.pack(fill="x")
        self.head = ttk.Label(top, justify="left", wraplength=W - 40)
        self.head.pack(anchor="w")
        leg = ttk.Frame(top)
        leg.pack(anchor="w", pady=(4, 0))
        for text, col in (("from the file", INK), ("literature placeholder",
                          LIT_C), ("missing — fill in after import", "#c00000"),
                          ("not in the file: current value kept / default",
                           INK2)):
            ttk.Label(leg, text="■ " + text, foreground=col).pack(side="left",
                                                                 padx=(0, 14))
        mid = ttk.Frame(self, padding=(10, 6))
        mid.pack(fill="both", expand=True)
        cols = ("item", "value", "unit", "source", "note")
        self.tv = ttk.Treeview(mid, columns=cols, show="tree headings")
        self.tv.heading("#0", text="Section")
        self.tv.column("#0", width=170, stretch=False)
        for c, h, w in zip(cols, ("Quantity", "Value", "Unit", "Source", "Note"),
                           (230, 200, 110, 150, 300)):
            self.tv.heading(c, text=h)
            self.tv.column(c, width=w, minwidth=40, anchor="w")
        for tag, col in (("lit", LIT_C), ("missing", "#c00000"), ("kept", INK2),
                         ("file", INK)):
            self.tv.tag_configure(tag, foreground=col)
        ysb = ttk.Scrollbar(mid, orient="vertical", command=self.tv.yview)
        xsb = ttk.Scrollbar(mid, orient="horizontal", command=self.tv.xview)
        self.tv.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tv.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        self.warn = ttk.Label(self, foreground=LIT_C, justify="left",
                              wraplength=W - 40, padding=(10, 0))
        self.warn.pack(anchor="w")
        bb = ttk.Frame(self, padding=10)
        bb.pack(fill="x")
        b = ttk.Button(bb, text="Materials folder…", command=self.choose_folder)
        b.pack(side="left")
        Tooltip(b, "Folder that holds the n,k files named in the parameter "
                   "file (nfile=…). They are looked up by name, with or "
                   "without .txt.")
        ttk.Button(bb, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bb, text="Apply to the GUI", command=self.apply).pack(
            side="right", padx=6)
        self.load()

    def load(self):
        try:
            cfg, _ = S.resolve_config(self.app._cfg_entries(), self.app._flags())
        except S.InputError:
            cfg = None
        try:
            self.result = PI.read_file(self.path, cfg, self.mat_dir, self.app.cache)
        except Exception as e:
            messagebox.showerror("Import parameter file", str(e), parent=self)
            self.destroy()
            return
        r = self.result
        self.tv.delete(*self.tv.get_children())
        sections = {}
        for sec, item, val, unit, src, note in r["rows"]:
            if sec not in sections:
                sections[sec] = self.tv.insert("", "end", text=sec, open=True)
            self.tv.insert(sections[sec], "end", text="",
                           values=(item, val, unit, src, note),
                           tags=(self.SOURCE_TAGS.get(src, "file"),))
        n_miss = sum(r_[4] == "MISSING" for r_ in r["rows"])
        n_lit = sum(r_[4].startswith("literature") for r_ in r["rows"])
        n_file = sum(r_[4] in ("file", "from n,k file") for r_ in r["rows"])
        names = ", ".join(f"{v[0]} (line {v[1]})" for v in
                          r["found"]["names"].values())
        what = []
        if r["config"] or r["flags"]:
            what.append("the Experiment settings it contains")
        if r["layers"] is not None:
            what.append(f"all layers (the stack becomes {len(r['layers'])} "
                        f"layers)")
        self.head.config(text=(
            f"Read {names}.  {n_file} values from the file, {n_lit} literature "
            f"placeholders, {n_miss} missing.\nApplying replaces "
            + (" and ".join(what) or "nothing") + ". Check the values below; "
            "you can still change anything afterwards in the tabs."))
        self.warn.config(text="\n".join("⚠ " + w for w in r["warnings"]))

    def choose_folder(self):
        d = filedialog.askdirectory(title="Materials folder with the n,k files",
                                    parent=self)
        if d:
            self.mat_dir = d
            self.load()

    def apply(self):
        self.app.apply_import(self.result, self.path)
        self.destroy()


# ---------------------------------------------------------------------------
# The application
# ---------------------------------------------------------------------------

EXAMPLE_STACK = [("Ti", "15"), ("Si3N4", "9.44"), ("Ti", "14.37"),
                 ("Si3N4", "588"), ("SiO2", "1454"), ("Si", "")]


class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("diffR thin-film model")
        # fit the window to the screen (laptops, display scaling)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        W, H = min(1560, sw - 40), min(940, sh - 90)
        self.geometry(f"{W}x{H}+10+10")
        self.minsize(min(900, W), min(560, H))
        left_w = max(420, min(700, int(W * 0.45)))
        self.cache = S.DispersionCache()
        self.layers, self.sel, self._loading = [], None, False
        self.batches, self.run_counter = [], 0
        self.work_rate = None            # seconds per grid-cell update, measured
        self.bg_rate = None              # seconds per background fit, measured
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
        self.stack_colors = {}          # material name -> "#rrggbb" chosen by the user
        self.view_run = tk.StringVar()

        self._build_menu()
        try:                         # thicker, easier-to-grab dividers
            ttk.Style(self).configure("Sash", sashthickness=7)
        except tk.TclError:
            pass
        # A tk (not ttk) PanedWindow: each side has a minimum width, so the
        # controls can never be squeezed to nothing; drag the divider to resize.
        outer = tk.PanedWindow(self, orient="horizontal", sashwidth=8,
                               sashrelief="raised", showhandle=False,
                               borderwidth=0, opaqueresize=True)
        outer.pack(fill="both", expand=True)
        leftc = ttk.Frame(outer)
        right = ttk.Frame(outer)
        outer.add(leftc, minsize=380, width=left_w, stretch="never")
        outer.add(right, minsize=380, stretch="always")
        self.outer = outer
        # the Run bar is packed first, at the bottom, so it is always visible
        # however small the window
        self._build_run_bar(leftc)
        left = ttk.PanedWindow(leftc, orient="vertical")
        left.pack(fill="both", expand=True)

        top = ttk.Frame(left)
        left.add(top, weight=3)
        self.tabs = ttk.Notebook(top, width=left_w - 20)
        self.tabs.pack(fill="both", expand=True)
        self._build_experiment_tab()
        self._build_layers_tab()
        self._build_sweep_tab()
        self.out = {}                   # Output + Background settings
        for k, v in O.default_options().items():
            self.out[k] = (tk.BooleanVar(value=v) if isinstance(v, bool)
                           else tk.StringVar(value=v))
            self.out[k].trace_add("write", lambda *_: self.schedule_preview())
        self._build_background_tab()
        self._build_output_tab()
        logf = ttk.Frame(left)
        left.add(logf, weight=1)
        ttk.Label(logf, text="Log: conversions to model units, warnings, "
                  "run diagnostics").pack(anchor="w", padx=4)
        self.log_w = ScrolledText(logf, height=8, wrap="none",
                                  font=("Courier", 9))
        self.log_w.pack(fill="both", expand=True)
        self.log_w.tag_configure("warn", foreground=LIT_C)
        self.log_w.tag_configure("err", foreground="#c00000")
        self.log_w.tag_configure("head", font=("Courier", 9, "bold"))

        self._build_plots(right)
        for w in ("pump", "probe"):
            for var in self.cfg_vars[f"lambda_{w}_nm"]:
                var.trace_add("write", lambda *_: self.schedule_file_nk())
        for v, u in self.cfg_vars.values():       # sizes and times depend on these
            v.trace_add("write", lambda *_: self.schedule_preview())
            u.trace_add("write", lambda *_: self.schedule_preview())
        self.show_strain.trace_add("write", lambda *_: self.schedule_preview())
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
        fm.add_command(label="Import parameter file (CFG / SAMPLE)…",
                       command=self.import_params)
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
        hint = ttk.Label(tab, foreground=INK2, justify="left",
                         text="Light enters through layer 1 (top of the list). The "
                              "last layer is the semi-infinite substrate. Click a "
                              "layer to edit it, or click it in the stack diagram. "
                              "Drag the dividers to resize the list, the editor "
                              "and the whole left panel.")
        hint.pack(anchor="w", fill="x", padx=6, pady=(6, 2))
        hint.bind("<Configure>", lambda e: hint.config(wraplength=max(200, e.width - 8)))

        # list and editor share a draggable divider
        split = ttk.PanedWindow(tab, orient="vertical")
        split.pack(fill="both", expand=True)
        top = ttk.Frame(split)
        split.add(top, weight=1)

        # buttons: a 3 x 2 grid that stretches with the panel, so the labels
        # are never cut off
        bf = ttk.Frame(top)
        bf.pack(fill="x", padx=6, pady=(2, 4))
        buttons = (("Add layer", self.add_layer),
                   ("Duplicate", self.duplicate_layer),
                   ("Delete", self.delete_layer),
                   ("Move up ↑", lambda: self.move_layer(-1)),
                   ("Move down ↓", lambda: self.move_layer(1)),
                   ("Literature values…", self.show_placeholders))
        for k, (text, cmd) in enumerate(buttons):
            b = ttk.Button(bf, text=text, command=cmd)
            b.grid(row=k // 3, column=k % 3, sticky="ew", padx=1, pady=1)
            if text.startswith("Literature"):
                Tooltip(b, "List every value that is still a literature "
                           "pre-fill, for all layers. Double-click a layer to "
                           "list only its values.")
        for c in range(3):
            bf.columnconfigure(c, weight=1, uniform="btn")

        tf = ttk.Frame(top)
        tf.pack(fill="both", expand=True, padx=6)
        cols = ("n", "mat", "d", "nk", "lit")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=7,
                                 selectmode="browse")
        for c, h, w in zip(cols, ("#", "Material", "Thickness", "n,k source",
                                  "Literature values"), (34, 110, 110, 160, 120)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, minwidth=30, anchor="w", stretch=c != "n")
        ysb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        xsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double)

        self.editor = ScrollableFrame(split)
        split.add(self.editor, weight=3)
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
                       "value with everything else unchanged, and overlay the "
                       "ΔR/R curves. What is saved, and under which names, is "
                       "set in the Output tab.").grid(row=0, column=0, sticky="w",
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

        o = section(tab, "What this sweep will produce", 2)
        self.sweep_summary = ttk.Label(o, foreground=INK2, wraplength=520,
                                       justify="left")
        self.sweep_summary.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Button(o, text="Output settings…",
                   command=lambda: self.tabs.select(self.output_tab)).grid(
            row=1, column=0, sticky="w", pady=(6, 0))

        ttk.Button(tab, text="▶ Run sweep", command=self.run_sweep).grid(
            row=3, column=0, sticky="w", padx=8, pady=10)
        for v in (self.sweep_layer, self.sweep_start, self.sweep_end,
                  self.sweep_step, self.sweep_unit):
            v.trace_add("write", lambda *_: self.schedule_preview())

    # ------------------------------------------------------------ background
    def _build_background_tab(self):
        sf = ScrollableFrame(self.tabs)
        self.tabs.add(sf, text="Background")
        self.bg_tab = sf
        tab = sf.inner
        ov = self.out
        ttk.Label(tab, wraplength=560, justify="left", foreground=INK2, text=(
            "Fit a double-exponential background  f(t) = a·e^(b·t) + c·e^(d·t)  "
            "to each ΔR/R trace (minus_exp_fun_single3) and subtract it. The "
            "result is written as an extra column next to the original ΔR/R "
            "in the same file, so both can be used. t is in ps, so b and d "
            "are in 1/ps.")).grid(row=0, column=0, sticky="w", padx=8,
                                  pady=(6, 2))
        if B is not None:
            ttk.Label(tab, foreground=INK2, text="Fitting engine: " +
                      B.FIT_ENGINE).grid(row=4, column=0, sticky="w", padx=8,
                                         pady=(2, 8))
        if B is None:
            ttk.Label(tab, foreground="#c00000", wraplength=560, text=(
                f"Background subtraction needs scipy ({BG_IMPORT_ERROR}). "
                f"Install it with  pip install scipy  and restart.")).grid(
                row=1, column=0, sticky="w", padx=8)
            return
        g = section(tab, "Subtraction", 1)
        ttk.Checkbutton(g, text="Subtract the background from every run",
                        variable=ov["bg_enabled"],
                        command=self._on_bg_toggle).grid(
            row=0, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(g, text="Fit only delays after a cut-off:",
                        variable=ov["bg_use_cut"]).grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(g, textvariable=ov["bg_cut"], width=8).grid(
            row=1, column=1, sticky="w", padx=2, pady=(6, 0))
        ttk.Combobox(g, textvariable=ov["bg_cut_unit"], width=5,
                     state="readonly", values=U.choices("time")).grid(
            row=1, column=2, sticky="w", pady=(6, 0))
        self.bg_cut_conv = ttk.Label(g, foreground=INK2)
        self.bg_cut_conv.grid(row=1, column=3, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(g, foreground=INK2, text="(untick to fit the whole time axis, "
                  "including negative delays)").grid(row=2, column=0,
                                                     columnspan=4, sticky="w")
        ttk.Checkbutton(g, text="Force decaying exponentials (b ≤ 0, d ≤ 0)",
                        variable=ov["bg_force_decay"]).grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(g, text="Before the cut-off the subtracted column holds").grid(
            row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(g, textvariable=ov["bg_before"], width=26, state="readonly",
                     values=list(B.BEFORE_CHOICES)).grid(
            row=4, column=1, columnspan=3, sticky="w", pady=(6, 0))

        c = section(tab, "In the ΔR/R file and the plots", 2)
        ttk.Label(c, text="Extra columns next to dR_over_R:").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(c, text="dR_over_R_minus_bg (ΔR/R − background)",
                        variable=ov["bg_col_sub"]).grid(row=1, column=0,
                                                        sticky="w", padx=(18, 0))
        ttk.Checkbutton(c, text="bg_fit (the fitted background)",
                        variable=ov["bg_col_fit"]).grid(row=2, column=0,
                                                        sticky="w", padx=(18, 0))
        ttk.Label(c, foreground=INK2, wraplength=520, justify="left", text=(
            "The NPZ archive always holds drr_minus_bg, bg_fit and bg_params; "
            "the metadata JSON holds a, b, c, d and the settings used. A "
            "combined sweep file gets one dR_over_R_minus_bg column per "
            "thickness.")).grid(row=3, column=0, columnspan=3, sticky="w",
                                pady=(2, 6))
        ttk.Label(c, text="Main ΔR/R plot shows").grid(row=4, column=0, sticky="w")
        cb = ttk.Combobox(c, textvariable=ov["bg_plot_main"], width=24,
                          state="readonly", values=["original ΔR/R",
                                                    "background-subtracted",
                                                    "both"])
        cb.grid(row=4, column=1, sticky="w", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self.draw_drr())

        r = section(tab, "Result", 3)
        self.bg_result = ttk.Label(r, foreground=INK2, wraplength=540,
                                   justify="left", text="No run yet.")
        self.bg_result.grid(row=0, column=0, columnspan=3, sticky="w")
        bf = ttk.Frame(r)
        bf.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(bf, text="Fit selected run now",
                   command=lambda: self.refit_background(all_runs=False)).pack(
            side="left")
        ttk.Button(bf, text="Re-fit all runs",
                   command=lambda: self.refit_background(all_runs=True)).pack(
            side="left", padx=6)
        ttk.Label(r, foreground=INK2, wraplength=540, justify="left", text=(
            "New runs are fitted automatically while 'Subtract' is on. After "
            "changing settings, re-fit here; files that were already saved are "
            "not rewritten (use File → Export to write them again).")).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
        for k in ("bg_cut", "bg_cut_unit", "bg_use_cut"):
            ov[k].trace_add("write", lambda *_: self._update_bg_cut_label())
        ov["bg_enabled"].trace_add("write", lambda *_: self._update_plot_tabs())
        self._update_bg_cut_label()

    def _bg_settings(self, opts=None):
        """Settings for background.apply(), or None when switched off.
        Raises ValueError for an unusable cut-off."""
        opts = opts or self._opts()
        if not opts.get("bg_enabled") or B is None:
            return None
        cut = 0.0
        if opts["bg_use_cut"]:
            try:
                cut = U.to_base(float(opts["bg_cut"]), "time", opts["bg_cut_unit"])
            except ValueError:
                raise ValueError("Background tab: the cut-off delay must be a "
                                 "number (or untick 'Fit only delays after a "
                                 "cut-off').")
        return dict(use_cut=bool(opts["bg_use_cut"]), cut_ps=cut,
                    force_decay=bool(opts["bg_force_decay"]),
                    before=B.BEFORE_CHOICES.get(opts["bg_before"], "nan"))

    def _update_bg_cut_label(self):
        if not hasattr(self, "bg_cut_conv"):
            return
        try:
            s = self._bg_settings(dict(self._opts(), bg_enabled=True))
            self.bg_cut_conv.config(
                text=(f"= {s['cut_ps']:g} ps" if s["use_cut"] else "not used"),
                foreground=INK2)
        except ValueError:
            self.bg_cut_conv.config(text="not a number", foreground="#c00000")

    def _on_bg_toggle(self):
        """Switching subtraction on asks whether to fit only after a delay."""
        if self.out["bg_enabled"].get() and not CutoffDialog(self).ok:
            self.out["bg_enabled"].set(False)

    def _bg_res_text(self, res):
        if res is None:
            return "No run selected."
        if res.get("bg_error"):
            return f"Background fit could not be done: {res['bg_error']}"
        bg = res.get("bg")
        if not bg:
            return ("The selected run has no background fit. Press 'Fit "
                    "selected run now'.")
        rng = (f"delays > {bg['cut_ps']:g} ps" if bg["cut_ps"] is not None
               else "the whole time axis")
        return (f"{self.view_run.get()}: fitted on {rng} ({bg['n_points']} "
                f"points).\n{B.describe(bg)}")

    def _update_bg_result(self):
        if hasattr(self, "bg_result"):
            self.bg_result.config(text=self._bg_res_text(self.selected_res()))

    def refit_background(self, all_runs=False):
        if B is None:
            return
        try:
            st = self._bg_settings(dict(self._opts(), bg_enabled=True))
        except ValueError as e:
            messagebox.showerror("Background", str(e))
            return
        runs = self.all_runs() if all_runs else [
            r for r in self.all_runs() if r["label"] == self.view_run.get()]
        if not runs:
            messagebox.showinfo("Background", "No run to fit yet.")
            return
        self.config(cursor="watch")
        self.update_idletasks()
        t_all = time.perf_counter()
        try:
            for r in runs:
                self._fit_background(r["res"], st)
                self.log(f"{r['label']}: background re-fitted in "
                         f"{fmt_duration(r['res']['bg_time'])} — " +
                         (B.describe(r["res"]["bg"]) if r["res"].get("bg")
                          else r["res"]["bg_error"]))
        finally:
            self.config(cursor="")
        t_all = time.perf_counter() - t_all
        self.log(f"re-fit of {len(runs)} run(s) took {fmt_duration(t_all)}", "head")
        self.status.config(text=f"background re-fit — {len(runs)} run(s) in "
                                f"{fmt_duration(t_all)}")
        self.draw_drr()
        self.redraw_run_plots()
        self._update_bg_result()

    @staticmethod
    def _fit_background(res, st):
        """Fit + subtract; the time it took is kept in res["bg_time"]."""
        res.pop("bg", None)
        res.pop("bg_error", None)
        t0 = time.perf_counter()
        try:
            res["bg"] = B.apply(res["t_ps"], res["drr"], **st)
        except Exception as e:
            res["bg_error"] = str(e)
        res["bg_time"] = time.perf_counter() - t0

    # ---------------------------------------------------------------- output
    def _build_output_tab(self):
        sf = ScrollableFrame(self.tabs)
        self.tabs.add(sf, text="Output")
        self.output_tab = sf
        tab = sf.inner
        ov = self.out

        w = section(tab, "Where", 0)
        ttk.Label(w, text="Folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(w, textvariable=ov["folder"], width=44).grid(
            row=0, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(w, text="Browse…", command=self._browse_out_dir).grid(
            row=0, column=2, padx=2)
        w.columnconfigure(1, weight=1)

        n = section(tab, "When to save automatically, and file names", 1)
        rows = (("auto_single", "single_name", "After every single run (F5)",
                 O.RUN_FIELDS),
                ("auto_sweep", "sweep_name", "After every run of a sweep",
                 O.SWEEP_FIELDS),
                ("combined", "combined_name", "One combined file per sweep "
                 "(all thicknesses side by side)", O.COMBINED_FIELDS))
        for r, (flag, name, text, fields) in enumerate(rows):
            ttk.Checkbutton(n, text=text, variable=ov[flag]).grid(
                row=3 * r, column=0, columnspan=2, sticky="w", pady=(6 if r else 0, 0))
            ttk.Label(n, text="name").grid(row=3 * r + 1, column=0, sticky="e",
                                           padx=(18, 4))
            ttk.Entry(n, textvariable=ov[name], width=44).grid(
                row=3 * r + 1, column=1, sticky="ew", pady=1)
            ttk.Label(n, foreground=INK2, wraplength=440, text="fields: " +
                      " ".join("{%s}" % f for f in fields)).grid(
                row=3 * r + 2, column=1, sticky="w")
        n.columnconfigure(1, weight=1)
        ttk.Label(n, foreground=INK2, justify="left", wraplength=480, text=(
            "{run} run/sweep number · {date} YYYYMMDD · {time} HHMMSS · "
            "{material} swept layer · {layer} its position · {d} thickness · "
            "{unit} thickness unit · {i} run within the sweep · {n} number of "
            "runs · {start} {end} {step} the range. The extensions are added "
            "automatically.")).grid(row=9, column=0, columnspan=2, sticky="w",
                                    pady=(6, 0))

        c = section(tab, "What to save", 2)
        ttk.Checkbutton(c, text="CSV table (opens in Excel, Origin, …)",
                        variable=ov["csv"]).grid(row=0, column=0, columnspan=4,
                                                 sticky="w")
        cf = ttk.Frame(c)
        cf.grid(row=1, column=0, columnspan=4, sticky="w", padx=(18, 0))
        ttk.Label(cf, text="columns: time, total ΔR/R, plus").grid(
            row=0, column=0, columnspan=4, sticky="w")
        for k, (opt, lab) in enumerate((("comp_strain", "strain"),
                                        ("comp_disp", "displacement"),
                                        ("comp_lattice", "lattice T"),
                                        ("comp_electron", "electron T"))):
            ttk.Checkbutton(cf, text=lab, variable=ov[opt]).grid(
                row=1, column=k, sticky="w", padx=(0, 8))
        ff = ttk.Frame(c)
        ff.grid(row=2, column=0, columnspan=4, sticky="w", padx=(18, 0), pady=2)
        ttk.Label(ff, text="time unit").pack(side="left")
        ttk.Combobox(ff, textvariable=ov["csv_time_unit"], width=4,
                     state="readonly", values=list(O.TIME_UNITS)).pack(
            side="left", padx=(2, 10))
        ttk.Label(ff, text="separator").pack(side="left")
        ttk.Combobox(ff, textvariable=ov["csv_delimiter"], width=9,
                     state="readonly", values=list(O.DELIMITERS)).pack(
            side="left", padx=(2, 10))
        ttk.Label(ff, text="number format").pack(side="left")
        ttk.Entry(ff, textvariable=ov["csv_format"], width=7).pack(
            side="left", padx=(2, 10))
        ttk.Checkbutton(ff, text="header row", variable=ov["csv_header"]).pack(
            side="left")
        ttk.Checkbutton(c, text="NPZ archive (all arrays + metadata, for Python)",
                        variable=ov["npz"]).grid(row=3, column=0, columnspan=4,
                                                 sticky="w", pady=(6, 0))
        nf = ttk.Frame(c)
        nf.grid(row=4, column=0, columnspan=4, sticky="w", padx=(18, 0))
        ttk.Checkbutton(nf, text="depth profiles (kernels, absorption)",
                        variable=ov["npz_profiles"]).pack(side="left")
        ttk.Checkbutton(nf, text="strain map η(z,t), if recorded",
                        variable=ov["npz_strain"]).pack(side="left", padx=8)
        ttk.Checkbutton(c, text="Metadata JSON (inputs with units, converted "
                                "values, placeholders, run time)",
                        variable=ov["meta_json"]).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Checkbutton(c, text="Figures", variable=ov["figures"]).grid(
            row=6, column=0, sticky="w", pady=(6, 0))
        gf = ttk.Frame(c)
        gf.grid(row=7, column=0, columnspan=4, sticky="w", padx=(18, 0))
        ttk.Checkbutton(gf, text="ΔR/R", variable=ov["fig_drr"]).pack(side="left")
        ttk.Checkbutton(gf, text="stack diagram", variable=ov["fig_stack"]).pack(
            side="left", padx=6)
        ttk.Checkbutton(gf, text="extra plots that are shown",
                        variable=ov["fig_extra"]).pack(side="left", padx=6)
        gf2 = ttk.Frame(c)
        gf2.grid(row=8, column=0, columnspan=4, sticky="w", padx=(18, 0), pady=2)
        ttk.Label(gf2, text="format").pack(side="left")
        ttk.Combobox(gf2, textvariable=ov["fig_format"], width=5,
                     state="readonly", values=O.FIG_FORMATS).pack(
            side="left", padx=(2, 10))
        ttk.Label(gf2, text="dpi").pack(side="left")
        ttk.Entry(gf2, textvariable=ov["fig_dpi"], width=5).pack(side="left",
                                                                 padx=2)
        ttk.Label(c, foreground=INK2, wraplength=520, justify="left", text=(
            "Figures are saved as they look on screen, with your labels and "
            "colours. For a sweep, the ΔR/R figure holds all thicknesses and "
            "takes the combined-file name. File → Export uses these same "
            "choices.")).grid(row=9, column=0, columnspan=4, sticky="w",
                              pady=(4, 0))

        k = section(tab, "Run / sweep number  ({run} in the names)", 3)
        self.next_run_var = tk.StringVar(value="1")
        ttk.Label(k, text="Next number").grid(row=0, column=0, sticky="w")
        sp = ttk.Spinbox(k, from_=1, to=999999, increment=1, width=8,
                         textvariable=self.next_run_var)
        sp.grid(row=0, column=1, sticky="w", padx=4)
        Tooltip(sp, "Single runs and sweeps share one counter. Type a number "
                    "(or use the arrows) to choose what the next run or sweep "
                    "is called.")
        ttk.Button(k, text="Reset to 1…", command=self.reset_counter).grid(
            row=0, column=2, sticky="w", padx=4)
        self.counter_msg = ttk.Label(k, foreground=INK2)
        self.counter_msg.grid(row=0, column=3, sticky="w", padx=4)
        ttk.Checkbutton(k, text="Ask before overwriting existing files",
                        variable=ov["ask_overwrite"]).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Label(k, foreground=INK2, wraplength=500, justify="left", text=(
            "Set the number back (e.g. to 1) to write again under the same "
            "names as before, for example to replace an earlier series. With "
            "the box above ticked you are asked first; untick it to overwrite "
            "without asking (the log still lists what was replaced).")).grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(2, 0))
        self.next_run_var.trace_add("write", lambda *_: self._on_counter_edit())

        pv = section(tab, "Preview", 4)
        self.out_preview = ttk.Label(pv, foreground=INK2, wraplength=540,
                                     justify="left")
        self.out_preview.grid(row=0, column=0, sticky="w")
        self._preview_job = None

    # --- the run / sweep counter
    def _on_counter_edit(self):
        if getattr(self, "_setting_counter", False):
            return
        try:
            n = int(self.next_run_var.get())
            if n < 1:
                raise ValueError
        except ValueError:
            self.counter_msg.config(text="enter a whole number ≥ 1",
                                    foreground="#c00000")
            return
        self.run_counter = n - 1
        self.counter_msg.config(text="", foreground=INK2)
        self.schedule_preview()

    def _set_counter(self, run_counter):
        self.run_counter = run_counter
        self._setting_counter = True
        try:
            self.next_run_var.set(str(run_counter + 1))
        finally:
            self._setting_counter = False
        self.counter_msg.config(text="")
        self.schedule_preview()

    def reset_counter(self):
        if not messagebox.askyesno(
                "Reset counter",
                f"The next run/sweep is number {self.run_counter + 1}.\n\n"
                f"Reset it to 1? File names that use {{run}} will then repeat "
                f"names used before, so earlier files can be overwritten"
                + (" (you will be asked first)." if self.out["ask_overwrite"].get()
                   else " WITHOUT asking, because 'Ask before overwriting' is "
                        "off.")):
            return
        self._set_counter(0)
        self.log("run/sweep counter reset: the next one is number 1")

    def _unique_tag(self, tag):
        """'run 3' or 'sweep 3', made unique among the runs in memory (the
        counter may have been set back)."""
        labels = [r["label"] for r in self.all_runs()]
        taken = lambda t: any(l == t or l.startswith(t + ":") for l in labels)
        k, out = 2, tag
        while taken(out):
            out, k = f"{tag} ({k})", k + 1
        return out

    def _confirm_overwrite(self, existing, title):
        if not existing:
            return True
        if self.out["ask_overwrite"].get():
            return messagebox.askyesno(
                title, f"{len(existing)} output file(s) already exist, e.g.\n"
                       f"{existing[0]}\n\nOverwrite them?")
        self.log(f"overwriting {len(existing)} existing file(s), e.g. "
                 f"{existing[0]}", "warn")
        return True

    def _browse_out_dir(self):
        d = filedialog.askdirectory(title="Folder for the output files",
                                    initialdir=self.out["folder"].get() or None)
        if d:
            self.out["folder"].set(os.path.normpath(d))

    def _opts(self):
        return {k: v.get() for k, v in self.out.items()}

    # --- planning: names, file counts, sizes, time
    def _dims(self, layers):
        """(cfg, d_stack_nm, v_max) from the current inputs, or None."""
        try:
            cfg, _ = S.resolve_config(self._cfg_entries(), self._flags())
        except S.InputError:
            return None
        d = sum(S.parse_thickness(l) or 0.0 for l in layers[:-1])
        vs = []
        for l in layers:
            try:
                vs.append(U.to_base(float(l["v"]), "velocity", l["v_unit"]))
            except (ValueError, KeyError):
                pass
        return cfg, d, (max(vs) if vs else None)

    def _time_estimate(self, works):
        if not self.work_rate or not works:
            return "unknown until the first run has been timed"
        sim = self.work_rate * sum(works)
        txt = f"≈ {fmt_duration(sim)} simulation"
        if self._bg_shown():
            if self.bg_rate:
                bgt = self.bg_rate * len(works)
                txt += (f" + ≈ {fmt_duration(bgt)} background fit = ≈ "
                        f"{fmt_duration(sim + bgt)}")
            else:
                txt += " + background fit (not timed yet)"
        return txt + " (from the last run's speed)"

    def _single_plan(self):
        """Names and estimates for the next single run."""
        opts = self._opts()
        O.check_options(opts)
        base = None
        if opts["auto_single"]:
            if not opts["folder"].strip():
                raise ValueError("Choose an output folder.")
            base = os.path.join(opts["folder"], S.format_name(
                opts["single_name"], run=self.run_counter + 1, **O.stamp()))
        dims = self._dims(self.layers)
        nbytes, works, nfig = 0, [], 0
        if dims:
            cfg, d, vmax = dims
            N, nt = O.grid_size(cfg, d)
            if base:
                nbytes = O.estimate_run_bytes(opts, N, nt, self.show_strain.get())
                if opts["figures"]:
                    nfig = (opts["fig_drr"] + opts["fig_stack"] + (
                        opts["fig_extra"] * sum(v.get() for v in (
                            self.show_components, self.show_kernels,
                            self.show_strain))))
                    nbytes += O.figure_bytes(opts, nfig)
            if vmax:
                works = [O.run_work(cfg, d, vmax)]
        nfiles = (len(O.run_files(opts)) + nfig) if base else 0
        return dict(base=base, nfiles=nfiles, nbytes=nbytes, works=works,
                    nfig=nfig)

    def _sweep_plan(self):
        """Everything about the next sweep; raises ValueError with a message."""
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
        uname = S.FILE_UNIT_NAMES.get(unit, unit)
        mat = self.layers[idx]["material"].strip() or f"layer{idx + 1}"
        opts = self._opts()
        O.check_options(opts)
        folder = opts["folder"].strip()
        common = dict(run=self.run_counter + 1, material=mat, layer=idx + 1,
                      unit=uname, n=len(vals), **O.stamp())
        bases = None
        if opts["auto_sweep"] or opts["combined"] or opts["figures"]:
            if not folder:
                raise ValueError("Choose an output folder in the Output tab.")
        if opts["auto_sweep"]:
            bases = [os.path.join(folder, S.format_name(
                opts["sweep_name"], d=f"{v:g}", i=i + 1, **common))
                for i, v in enumerate(vals)]
            if len(set(bases)) != len(bases):
                raise ValueError("The per-run file name gives the same name to "
                                 "several runs; include {d} or {i}.")
        cname = os.path.join(folder, S.format_name(
            opts["combined_name"], start=f"{a:g}", end=f"{vals[-1]:g}",
            step=f"{st:g}", **common))
        cbase = cname if opts["combined"] else None
        figbase = cname if opts["figures"] else None
        # sizes and work, thickness by thickness
        nbytes, works = 0, []
        dims = self._dims(self.layers)
        if dims:
            cfg, d0, vmax = dims
            d_others = d0 - (S.parse_thickness(self.layers[idx]) or 0.0)
            for v in vals:
                d = d_others + U.to_base(v, "thickness", unit)
                N, nt = O.grid_size(cfg, d)
                if bases:
                    nbytes += O.estimate_run_bytes(opts, N, nt,
                                                   self.show_strain.get())
                if vmax:
                    works.append(O.run_work(cfg, d, vmax))
            if cbase:
                nbytes += O.estimate_combined_bytes(opts, nt, len(vals))
        nfig = (opts["fig_drr"] + opts["fig_stack"]) if figbase else 0
        nbytes += O.figure_bytes(opts, nfig)
        per_run = len(O.run_files(opts)) if bases else 0
        n_comb = 0
        if cbase:
            n_comb = (opts["csv"] or not opts["npz"]) + opts["npz"]
        return dict(idx=idx, vals=vals, unit=unit, bases=bases, cbase=cbase,
                    figbase=figbase, folder=folder, per_run=per_run,
                    n_comb=n_comb, nfig=nfig,
                    nfiles=per_run * len(vals) + n_comb + nfig,
                    nbytes=nbytes, works=works)

    def _describe_sweep(self, p):
        lines = [f"{len(p['vals'])} simulations of layer {p['idx'] + 1} "
                 f"({self.layers[p['idx']]['material']}): "
                 f"{p['vals'][0]:g} … {p['vals'][-1]:g} {p['unit']}"]
        if p["nfiles"]:
            parts = []
            if p["per_run"]:
                parts.append(f"{len(p['vals'])} × {p['per_run']} per-run "
                             f"({', '.join(O.run_files(self._opts()))})")
            if p["n_comb"]:
                parts.append(f"{p['n_comb']} combined")
            if p["nfig"]:
                parts.append(f"{p['nfig']} figure(s)")
            lines.append(f"Files: {p['nfiles']} = " + " + ".join(parts))
            lines.append(f"Estimated size: ≈ {O.human_size(p['nbytes'])} in "
                         f"{p['folder']}")
            names = []
            if p["bases"]:
                b = [os.path.basename(x) for x in p["bases"]]
                names.append("e.g. " + (", ".join(b) if len(b) <= 2 else
                                        f"{b[0]}, … {b[-1]}"))
            if p["cbase"] or p["figbase"]:
                names.append("combined/figure: " +
                             os.path.basename(p["cbase"] or p["figbase"]))
            if names:
                lines.append("Names: " + "; ".join(names))
        else:
            lines.append("Files: none are saved automatically (switch saving "
                         "on in the Output tab, or use File → Export later).")
        lines.append("Estimated time: " + self._time_estimate(p["works"]))
        return "\n".join(lines)

    def schedule_preview(self):
        if getattr(self, "_preview_job", None):
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(300, self._update_previews)

    def _update_previews(self):
        self._preview_job = None
        if not hasattr(self, "sweep_summary") or not hasattr(self, "out_preview"):
            return
        # sweep tab
        try:
            p = self._sweep_plan()
            shown = ", ".join(f"{v:g}" for v in p["vals"][:8]) + (
                f", … {p['vals'][-1]:g}" if len(p["vals"]) > 8 else "")
            self.sweep_preview.config(text=f"{len(p['vals'])} runs: {shown} "
                                           f"{p['unit']}", foreground=INK2)
            sweep_txt = self._describe_sweep(p)
            self.sweep_summary.config(text=sweep_txt, foreground=INK2)
        except ValueError as e:
            p = None
            self.sweep_preview.config(text=str(e), foreground="#c00000")
            self.sweep_summary.config(text="", foreground=INK2)
            sweep_txt = f"Sweep: {e}"
        # output tab
        try:
            sp = self._single_plan()
            if sp["base"]:
                single = (f"Next single run → {os.path.basename(sp['base'])}"
                          f" ({', '.join(O.run_files(self._opts()))}"
                          f"{', + %d figure(s)' % sp['nfig'] if sp['nfig'] else ''})"
                          f", {sp['nfiles']} file(s), ≈ "
                          f"{O.human_size(sp['nbytes'])}")
            else:
                single = ("Single runs are not saved automatically (File → "
                          "Export selected run saves one by hand).")
            col = INK2
        except ValueError as e:
            single, col = str(e), "#c00000"
        self.out_preview.config(text=single + "\n\nThickness sweep:\n" + sweep_txt,
                                foreground=col)

    def _refresh_sweep_layers(self):
        vals = [f"{i + 1}: {l['material']}" for i, l in enumerate(self.layers[:-1])]
        cur = self.sweep_layer.get()
        self.sweep_cb.config(values=vals)
        if cur not in vals:
            self.sweep_layer.set(vals[0] if vals else "")
        self.schedule_preview()

    # --------------------------------------------------------------- run bar
    def _build_run_bar(self, parent):
        f = ttk.Frame(parent, padding=(4, 6))
        f.pack(side="bottom", fill="x")
        self.run_btn = ttk.Button(f, text="▶ Run model (F5)", command=self.run_single)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(f, text="Cancel", command=self.cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        self.pbar = ttk.Progressbar(f, length=120, maximum=1.0)
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
        self.view_run.trace_add("write", lambda *_: self._update_bg_result())
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

        self.stack_panel = PlotPanel(main, figsize=(8, 1.9), colors=False)
        self.stack_panel.redraw = self.draw_stack
        main.add(self.stack_panel, weight=1)
        ttk.Label(self.stack_panel.controls, text="Widths").pack(side="left")
        cb = ttk.Combobox(self.stack_panel.controls, textvariable=self.stack_mode,
                          state="readonly", width=16,
                          values=["Equal widths", "Proportional", "Log thickness"])
        cb.pack(side="left", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self.draw_stack())
        b = ttk.Button(self.stack_panel.controls, text="Colours…",
                       command=self.edit_stack_colors)
        b.pack(side="left", padx=(6, 0))
        Tooltip(b, "Choose the colour of each material in the stack diagram. "
                   "Layers of the same material share a colour.")
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
        self.bg_panel = PlotPanel(self.ptabs)
        self.bg_panel.redraw = self.draw_background
        self.ptabs.add(self.bg_panel, text="Background")
        self._update_plot_tabs()
        self.draw_drr()

    def _bg_shown(self):
        return hasattr(self, "out") and self.out["bg_enabled"].get()

    def _update_plot_tabs(self):
        if not hasattr(self, "bg_panel"):
            return
        for var, panel in ((self.show_components, self.comp_panel),
                           (self.show_kernels, self.kern_panel),
                           (self.show_strain, self.strain_panel),
                           (self.out["bg_enabled"], self.bg_panel)):
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
        self.schedule_preview()          # file sizes depend on the thicknesses
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
            col = self.material_color(lay["material"].strip(), order)
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

    def material_color(self, name, order=None):
        """The user's colour for a material, else the default palette slot."""
        if name in self.stack_colors:
            return self.stack_colors[name]
        order = order or list(dict.fromkeys(l["material"].strip()
                                            for l in self.layers))
        return SERIES[order.index(name) % len(SERIES)] if name in order else SERIES[0]

    def edit_stack_colors(self):
        StackColorDialog(self)

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
        mode = self.out["bg_plot_main"].get() if self._bg_shown() else "original ΔR/R"
        n_sub = 0
        for r, c in zip(runs, self._run_colors(runs, sweep)):
            res = r["res"]
            bg = res.get("bg")
            if mode == "original ΔR/R" or not bg:
                ax.plot(res["t_ps"], res["drr"] * 10 ** exp, color=c, lw=1.2,
                        label=r["label"])
                continue
            n_sub += 1
            if mode == "both":
                ax.plot(res["t_ps"], res["drr"] * 10 ** exp, color=c, lw=.9,
                        alpha=.45, label=r["label"])
            ax.plot(res["t_ps"], bg["sub"] * 10 ** exp, color=c, lw=1.2,
                    label=r["label"] + " − bg" if mode == "both" else r["label"])
        tmax = max(r["res"]["t_ps"].max() for r in runs)
        ax.set_xlim(0, tmax)
        ax.axhline(0, color=GRID_C, lw=.8, zorder=0, gid="ref")
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel(drr_label(exp))
        ax.set_title("Modelled differential reflectivity"
                     + (f" — sweep of {batches[0]['info']}" if sweep else "")
                     + (" — background subtracted" if n_sub and mode ==
                        "background-subtracted" else ""))
        ax.grid(alpha=.6)
        if len(runs) > 1 or (n_sub and mode == "both"):
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
        ax.axhline(0, color=GRID_C, lw=.8, zorder=0, gid="ref")
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
                a.axvline(e / M.nm, color=INK2, lw=.7, ls="--", gid="ref")
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
            ax.axhline(e / M.nm, color=INK2, lw=.6, ls="--", gid="ref")
        ax.axhline(res["grid"]["z_sponge"] / M.nm, color=INK, lw=.8, ls=":",
                   gid="ref")
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel("depth z (nm)")
        ax.set_title(f"Strain η(z, t) — {self.view_run.get()}  "
                     f"(dotted line: start of the absorbing sponge)")
        cb = fig.colorbar(im, ax=ax)
        cb.ax._diffr_cbar = True
        p.colorbars.append(cb)
        cb.set_label("strain η")
        p.finish()

    def redraw_run_plots(self):
        for var, panel in ((self.show_components, self.comp_panel),
                           (self.show_kernels, self.kern_panel),
                           (self.show_strain, self.strain_panel),
                           (self.out["bg_enabled"], self.bg_panel)):
            if var.get():
                panel.redraw()
        self._update_bg_result()

    def draw_background(self):
        p = self.bg_panel
        res = self.selected_res()
        if res is None:
            p.empty("Run the model to see the background fit.")
            return
        bg = res.get("bg")
        if not bg:
            p.empty(self._bg_res_text(res))
            return
        exp = int(self.scale_exp.get())
        k = 10 ** exp
        ax = p.clear().add_subplot()
        t = res["t_ps"]
        ax.plot(t, res["drr"] * k, color=SERIES[0], lw=1.2, label="data (ΔR/R)")
        ax.plot(t, bg["fit"] * k, color=SERIES[1], lw=1.4, ls="--",
                label="fitted background" if bg["params"] is not None
                else "mean (fit failed)")
        ax.plot(t, bg["sub"] * k, color=INK, lw=1.2, label="ΔR/R − background")
        if bg["cut_ps"] is not None:
            ax.axvline(bg["cut_ps"], color=INK2, lw=.8, ls=":", gid="ref")
        ax.axhline(0, color=GRID_C, lw=.8, zorder=0, gid="ref")
        ax.set_xlim(t[0], t[-1])
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel(drr_label(exp))
        ax.set_title(f"Exponential background subtraction — {self.view_run.get()}"
                     + (f"  (fit on t > {bg['cut_ps']:g} ps, dotted line)"
                        if bg["cut_ps"] is not None else ""))
        ax.grid(alpha=.6)
        ax.legend(fontsize=8).set_draggable(True)
        p.finish()

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

    def _existing(self, bases, suffixes):
        return [b + x for b in bases for x in suffixes if os.path.exists(b + x)]

    def run_single(self):
        if self._busy():
            return
        try:
            self._bg_settings()
        except ValueError as e:
            messagebox.showerror("Background", str(e))
            return
        try:
            plan = self._single_plan()
        except ValueError as e:
            messagebox.showerror("Output settings", str(e))
            return
        prep = self._prepare(self.layers)
        if prep is None:
            return
        cfg, stack, mats = prep
        base = plan["base"]
        if base:
            sfx = O.run_files(self._opts()) + [
                f"_{k}.{self.out['fig_format'].get()}" for k in FIGURE_PANELS]
            if not self._confirm_overwrite(self._existing([base], sfx),
                                           "Overwrite?"):
                return
        label = self._unique_tag(f"run {self.run_counter + 1}")
        self._set_counter(self.run_counter + 1)
        self.log(f"— {label}: started; estimated time: "
                 f"{self._time_estimate(plan['works'])}", "head")
        if base:
            self.log(f"   will save {plan['nfiles']} file(s), ≈ "
                     f"{O.human_size(plan['nbytes'])}: {base}*")
        self._start([dict(label=label, cfg=cfg, stack=stack, materials=mats,
                          inputs=self._snapshot(self.layers), save_base=base)],
                    dict(kind="single", info="", figbase=base
                         if self.out["figures"].get() else None))

    def run_sweep(self):
        if self._busy():
            return
        try:
            self._bg_settings()
        except ValueError as e:
            messagebox.showerror("Background", str(e))
            return
        try:
            p = self._sweep_plan()
        except ValueError as e:
            messagebox.showerror("Sweep", str(e))
            return
        idx, vals, unit, bases = p["idx"], p["vals"], p["unit"], p["bases"]
        # tell the user what is about to happen before anything runs
        if not messagebox.askokcancel("Start sweep?", self._describe_sweep(p)):
            return
        fmt = self.out["fig_format"].get()
        ex = self._existing(bases or [], O.run_files(self._opts()))
        if p["cbase"]:
            ex += self._existing([p["cbase"]], [".csv", ".npz"])
        if p["figbase"]:
            ex += self._existing([p["figbase"]], [f"_dRR.{fmt}", f"_stack.{fmt}"])
        if not self._confirm_overwrite(ex, "Sweep"):
            return
        name = self.layers[idx]["material"]
        jobs = []
        tag = self._unique_tag(f"sweep {self.run_counter + 1}")
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
        self._set_counter(self.run_counter + 1)
        self.log(f"— {tag}: started", "head")
        self.log("   " + self._describe_sweep(p).replace("\n", "\n   "))
        self._start(jobs, dict(kind="sweep", combined=p["cbase"],
                               figbase=p["figbase"],
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
        opts = self._opts()
        st = self._bg_settings(opts)            # validated by the callers
        for job in jobs:
            job["bg_settings"] = st
        self._pending = dict(batch, runs=[], n_jobs=len(jobs), opts=opts,
                             t0=time.perf_counter(), sim_time=0.0,
                             bg_time=0.0, bg_done=0)
        self.worker = threading.Thread(target=self._work, args=(jobs,), daemon=True)
        self.worker.start()

    def _work(self, jobs):
        """Worker thread: only numpy here, all Tk work goes through the queue.

        Phase 1 runs every simulation; phase 2 (if switched on) fits and
        subtracts the background of every run. Each part is timed on its own.
        """
        n = len(jobs)
        for j, job in enumerate(jobs):
            def prog(f, j=j, lab=job["label"]):
                self.queue.put(("progress", f, f"Simulation {j + 1}/{n} — {lab}"))
            t0 = time.perf_counter()
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
            job["elapsed"] = time.perf_counter() - t0
            job["res"] = res
            self.queue.put(("sim", job, res))
        st = jobs[0].get("bg_settings") if jobs else None
        if st:
            for j, job in enumerate(jobs):
                if self.cancel_ev.is_set():
                    self.queue.put(("cancelled", None, None))
                    return
                self.queue.put(("progress", j / n,
                                f"Background fit {j + 1}/{n} — {job['label']}"))
                self._fit_background(job["res"], st)
                self.queue.put(("bg", job, job["res"]))
        self.queue.put(("done", None, None))

    def _poll(self):
        try:
            while True:
                kind, a, b = self.queue.get_nowait()
                if kind == "progress":
                    self.pbar["value"] = a
                    el = time.perf_counter() - self._pending["t0"]
                    self.status.config(text=f"{b}: {100 * a:.0f} %  "
                                            f"({fmt_duration(el)} elapsed)")
                elif kind == "sim":
                    pend = self._pending
                    if not pend["runs"]:
                        self.log("Phase 1 — simulation", "head")
                    a["res"] = b
                    pend["runs"].append(a)
                    pend["sim_time"] += a["elapsed"]
                    work = len(b["t_ps"]) * b["grid"]["N"] * b["n_sub"]
                    self.work_rate = a["elapsed"] / work
                    self.log(f"{a['label']}: simulation finished in "
                             f"{fmt_duration(a['elapsed'])}")
                    self.log(M.run_summary(b))
                    if not a.get("bg_settings") and a.get("save_base"):
                        a["saved"] = self._save_run(a, a["save_base"], pend["opts"])
                elif kind == "bg":
                    pend = self._pending
                    if pend["bg_done"] == 0:
                        self.log(f"Phase 2 — background subtraction (simulation "
                                 f"took {fmt_duration(pend['sim_time'])})", "head")
                    pend["bg_done"] += 1
                    dt = b.get("bg_time", 0.0)
                    pend["bg_time"] += dt
                    self.bg_rate = dt
                    if b.get("bg"):
                        self.log(f"{a['label']}: background subtraction finished "
                                 f"in {fmt_duration(dt)}")
                        self.log("  " + B.describe(b["bg"]))
                    else:
                        self.log(f"{a['label']}: background fit not possible "
                                 f"({b.get('bg_error')}) — {fmt_duration(dt)}",
                                 "warn")
                    if a.get("save_base"):
                        a["saved"] = self._save_run(a, a["save_base"], pend["opts"])
                elif kind in ("done", "cancelled", "error"):
                    self._finish(kind, a, b)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _finish(self, kind, a, b):
        self.run_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        batch = self._pending
        total = time.perf_counter() - batch["t0"]
        batch["elapsed"] = total
        if kind == "error":
            self.log(f"{b} failed:\n{a}", "err")
            messagebox.showerror("Model error", a.strip().splitlines()[-1])
        elif kind == "cancelled":
            self.log(f"cancelled after {len(batch['runs'])} of "
                     f"{batch['n_jobs']} simulations and {batch['bg_done']} "
                     f"background fits", "warn")
            # simulations that were not fitted are still saved (without the
            # background columns)
            for r in batch["runs"]:
                if r.get("save_base") and not r.get("saved"):
                    r["saved"] = self._save_run(r, r["save_base"], batch["opts"])
        if batch["runs"] and batch.get("combined"):
            try:
                files = O.write_sweep(batch["runs"], batch["combined"],
                                      batch["opts"], batch["info"])
                self.log("written: " + ", ".join(files))
            except Exception as e:
                self.log(f"could not write the combined file: "
                         f"{type(e).__name__}: {e}", "err")
        if batch["runs"]:
            self.batches.append(batch)
            self._refresh_run_list()
            self.draw_drr()
            self.redraw_run_plots()
            if batch.get("figbase"):
                self._save_figures(batch["figbase"], batch["opts"],
                                   extra=batch["kind"] == "single")
        n, nb = len(batch["runs"]), batch["bg_done"]
        per = lambda t, k: f" ({fmt_duration(t / k)} per run)" if k > 1 else ""
        parts = []
        if n:
            parts.append(f"simulation {fmt_duration(batch['sim_time'])}"
                         + per(batch["sim_time"], n))
        if nb:
            parts.append(f"background {fmt_duration(batch['bg_time'])}"
                         + per(batch["bg_time"], nb))
        runs = f"{n} run{'s' if n != 1 else ''}: " if batch["kind"] == "sweep" else ""
        msg = (runs + ", ".join(parts) + f"; total {fmt_duration(total)}"
               if parts else f"after {fmt_duration(total)}")
        word = {"done": "done", "cancelled": "cancelled", "error": "stopped"}[kind]
        self.log(f"{word} — {msg}", "head")
        self.pbar["value"] = 1.0 if kind == "done" else 0
        self.status.config(text=f"{word} — {msg}")
        self.schedule_preview()          # time estimates now use this speed

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

    def _save_run(self, run, base, opts):
        meta = O.run_meta(run["res"], run["label"], run["inputs"],
                          run.get("elapsed"))
        try:
            files = O.write_run(run["res"], base, opts, meta)
        except Exception as e:
            self.log(f"could not save {base}: {type(e).__name__}: {e}", "err")
            return False
        self.log("written: " + ", ".join(files))
        return True

    def _save_figures(self, base, opts, extra=True):
        """Save the chosen figures as they are on screen."""
        panels = []
        if opts["fig_drr"]:
            panels.append(("dRR", self.drr_panel))
        if opts["fig_stack"]:
            panels.append(("stack", self.stack_panel))
        if opts["fig_extra"] and extra:
            for var, key in ((self.show_components, "components"),
                             (self.show_kernels, "kernels"),
                             (self.show_strain, "strain_map"),
                             (self.out["bg_enabled"], "background")):
                if var.get():
                    panels.append((key, FIGURE_PANELS_MAP(self)[key]))
        try:
            dpi = int(opts["fig_dpi"])
        except ValueError:
            dpi = 150
        written = []
        for key, panel in panels:
            path = f"{base}_{key}.{opts['fig_format']}"
            try:
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                panel.fig.savefig(path, dpi=dpi)
                written.append(path)
            except Exception as e:
                self.log(f"could not save {path}: {type(e).__name__}: {e}", "err")
        if written:
            self.log("written: " + ", ".join(written))

    def export_run(self):
        _, run = self._selected_run()
        if run is None:
            messagebox.showinfo("Export", "No run to export yet.")
            return
        opts = self._opts()
        try:
            O.check_options(opts)
        except ValueError as e:
            messagebox.showerror("Output settings", str(e))
            return
        if not (opts["csv"] or opts["npz"] or opts["meta_json"]):
            messagebox.showinfo("Export", "Nothing is selected under 'What to "
                                          "save' in the Output tab.")
            return
        p = filedialog.asksaveasfilename(
            title="Export run — base name (extensions are added from the "
                  "Output tab choices)",
            initialdir=opts["folder"] or None,
            initialfile=S.format_name(run["label"].replace(" ", "_")),
            filetypes=[("All files", "*.*")])
        if p:
            self._save_run(run, os.path.splitext(p)[0], opts)

    def export_sweep(self):
        b, _ = self._selected_run()
        if b is None or b["kind"] != "sweep":
            sweeps = [x for x in self.batches if x["kind"] == "sweep"]
            if not sweeps:
                messagebox.showinfo("Export sweep", "No sweep has been run yet.")
                return
            b = sweeps[-1]
        opts = self._opts()
        try:
            O.check_options(opts)
        except ValueError as e:
            messagebox.showerror("Output settings", str(e))
            return
        p = filedialog.asksaveasfilename(
            title="Export sweep — base name for the combined file",
            initialdir=opts["folder"] or None, initialfile="sweep",
            filetypes=[("All files", "*.*")])
        if not p:
            return
        try:
            files = O.write_sweep(b["runs"], os.path.splitext(p)[0], opts, b["info"])
            self.log("written: " + ", ".join(files))
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
                               stack_colors=self.stack_colors,
                               scale_exp=self.scale_exp.get(),
                               overlay=self.overlay.get()),
                    sweep=dict(layer=self.sweep_layer.get(),
                               start=self.sweep_start.get(),
                               end=self.sweep_end.get(),
                               step=self.sweep_step.get(),
                               unit=self.sweep_unit.get()),
                    output=self._opts(),
                    next_run=self.run_counter + 1,
                    plot_styles={name: {str(k): v for k, v in pan.styles.items()}
                                 for name, pan in FIGURE_PANELS_MAP(self).items()})

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
        self.stack_colors = dict(pl.get("stack_colors", {}))
        self.scale_exp.set(pl.get("scale_exp", "3"))
        self.overlay.set(pl.get("overlay", False))
        sw = st.get("sweep", {})
        for var, key in ((self.sweep_start, "start"), (self.sweep_end, "end"),
                         (self.sweep_step, "step"), (self.sweep_unit, "unit")):
            if key in sw:
                var.set(sw[key])
        out = O.default_options()
        # sessions saved before the Output tab kept these with the sweep
        for old, new in (("folder", "folder"), ("name", "sweep_name"),
                         ("save", "auto_sweep"), ("combined", "combined"),
                         ("combined_name", "combined_name")):
            if old in sw:
                out[new] = sw[old]
        out.update({k: v for k, v in st.get("output", {}).items() if k in out})
        for k, v in out.items():
            self.out[k].set(v)
        if "next_run" in st:
            self._set_counter(max(0, int(st["next_run"]) - 1))
        for name, pan in FIGURE_PANELS_MAP(self).items():
            pan.styles = {int(k): v for k, v in
                          st.get("plot_styles", {}).get(name, {}).items()}
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

    def import_params(self):
        p = filedialog.askopenfilename(
            title="Import parameter file (CFG = dict(...), SAMPLE = [...])",
            filetypes=[("Text / Python", "*.txt *.py *.cfg *.dat"),
                       ("All", "*.*")])
        if p:
            ImportReviewDialog(self, p)

    def apply_import(self, r, path):
        for key, e in r["config"].items():
            v, u = self.cfg_vars[key]
            u.set(e["unit"])
            v.set(e["value"])
        for key, val in r["flags"].items():
            self.flag_vars[key].set(val)
        if r["layers"] is not None:
            self.layers = r["layers"]
            self.sel = None
            self.refresh_tree(0)
        n_miss = sum(x[4] == "MISSING" for x in r["rows"])
        self.log(f"imported {os.path.basename(path)}: "
                 f"{len(r['config'])} settings, "
                 f"{0 if r['layers'] is None else len(r['layers'])} layers", "head")
        for sec, item, val, unit, src, note in r["rows"]:
            if src == "MISSING":
                self.log(f"  missing: {sec} — {item}" + (f" ({note})" if note
                                                          else ""), "err")
        for w in r["warnings"]:
            self.log("  " + w, "warn")
        lit = [x for x in r["rows"] if x[4].startswith("literature")]
        if lit:
            self.log(f"  {len(lit)} values are literature placeholders "
                     f"(marked ● lit.; Layers → Literature values… lists them)",
                     "warn")
        if n_miss:
            messagebox.showwarning(
                "Imported — please complete",
                f"{n_miss} value(s) were not in the file and are still missing "
                f"(listed in red in the log). Fill them in before running.")

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


# figure-file suffix -> panel, for saving figures and plot styles
FIGURE_PANELS = ("dRR", "stack", "components", "kernels", "strain_map",
                 "background")


def FIGURE_PANELS_MAP(app):
    return {"dRR": app.drr_panel, "stack": app.stack_panel,
            "components": app.comp_panel, "kernels": app.kern_panel,
            "strain_map": app.strain_panel, "background": app.bg_panel}


def fmt_duration(sec):
    if sec < 10:
        return f"{sec:.2f} s"
    if sec < 60:
        return f"{sec:.1f} s"
    m, s_ = divmod(int(round(sec)), 60)
    if m < 60:
        return f"{m} min {s_:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


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

4. Thickness sweep tab: pick a layer and give start, end and increment.
   Before it starts you are told how many simulations and files it
   gives, the estimated size and the estimated time.

5. Output tab: folder, automatic saving (single runs, each sweep run,
   one combined sweep file), file-name patterns, and what to save
   (CSV columns/format, NPZ contents, metadata JSON, figures).

6. File menu: import a conf_file, save/open the whole session, export
   the selected run or a sweep (with the Output tab's choices).
   'Colours…' on each plot changes curve colours and colour maps."""


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
