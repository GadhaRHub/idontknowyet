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
from matplotlib.cm import ScalarMappable
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.transforms import blended_transform_factory
from matplotlib.backends.backend_tkagg import (FigureCanvasTkAgg,
                                               NavigationToolbar2Tk)

import diffr_model as M
import help_info as HI
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

# "Berry & indigo" palette for the window theme
BG = "#FAF6F9"                  # panels (very light pink-white)
BERRY = "#7A1F5C"               # buttons, unselected tabs, scrollbars
INDIGO = "#2E2A6B"              # selected tab, frames, dividers
SIENNA = "#A0522D"              # Run model / Run sweep / Play
TEAL = "#6CC5BA"                # ticks, progress bar, selections (dark text)
LIGHT = "#EBD9E5"               # light berry tint for menus / headers
DISABLED_BG, DISABLED_FG = "#e7e1e5", "#9a949a"


def _mix(c, w, f):
    """Blend colour c towards w by fraction f (for hover / pressed shades)."""
    a, b = matplotlib.colors.to_rgb(c), matplotlib.colors.to_rgb(w)
    return matplotlib.colors.to_hex([x + (y - x) * f for x, y in zip(a, b)])


def apply_theme(root):
    """Window theme from the palette: berry buttons and tabs with white text,
    indigo for the selected tab and frames, sienna for the Run buttons, light
    teal for ticks, progress and selections. Plot drawing areas stay white."""
    st = ttk.Style(root)
    try:
        st.theme_use("clam")       # the native themes ignore most colours
    except tk.TclError:
        return
    hover, pressed = _mix(BERRY, "white", .25), _mix(BERRY, "black", .25)
    st.configure(".", background=BG, foreground=INK, bordercolor=INDIGO,
                 lightcolor=BG, darkcolor=INDIGO, troughcolor=BG,
                 focuscolor=TEAL, selectbackground=TEAL,
                 selectforeground=INK)
    for w in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton", "TNotebook",
              "TPanedwindow"):
        st.configure(w, background=BG)
    for w in ("TCheckbutton", "TRadiobutton"):
        st.map(w, background=[("active", BG)],
               indicatorbackground=[("selected", TEAL), ("!selected", "white")])
    st.configure("TLabelframe", background=BG, bordercolor=INDIGO,
                 lightcolor=INDIGO, darkcolor=INDIGO)
    st.configure("TLabelframe.Label", background=BG, foreground=INK)
    st.configure("TButton", background=BERRY, foreground="white",
                 bordercolor=pressed, lightcolor=BERRY, darkcolor=BERRY,
                 padding=(8, 3))
    st.map("TButton", background=[("disabled", DISABLED_BG),
                                  ("pressed", pressed), ("active", hover)],
           foreground=[("disabled", DISABLED_FG)])
    st.configure("Accent.TButton", background=SIENNA, foreground="white",
                 lightcolor=SIENNA, darkcolor=SIENNA,
                 font=("TkDefaultFont", 9, "bold"))
    st.map("Accent.TButton", background=[("disabled", DISABLED_BG),
                                         ("pressed", _mix(SIENNA, "black", .25)),
                                         ("active", _mix(SIENNA, "white", .2))],
           foreground=[("disabled", DISABLED_FG), ("!disabled", "white")])
    st.configure("TNotebook", bordercolor=INDIGO, tabmargins=(2, 4, 2, 0))
    st.configure("TNotebook.Tab", background=BERRY, foreground="white",
                 bordercolor=INDIGO, lightcolor=BERRY, padding=(8, 3))
    st.map("TNotebook.Tab", background=[("selected", INDIGO), ("active", hover)],
           lightcolor=[("selected", INDIGO)])
    for w in ("TEntry", "TCombobox", "TSpinbox"):
        st.configure(w, fieldbackground="white", background=BERRY,
                     bordercolor=INDIGO, arrowcolor="white")
    st.map("TCombobox", fieldbackground=[("readonly", "white")],
           background=[("active", hover)])
    st.map("TSpinbox", background=[("active", hover)])
    st.configure("Treeview", background="white", fieldbackground="white",
                 bordercolor=INDIGO)
    st.map("Treeview", background=[("selected", TEAL)],
           foreground=[("selected", INK)])
    st.configure("Treeview.Heading", background=BERRY, foreground="white",
                 bordercolor=pressed)
    st.map("Treeview.Heading", background=[("active", hover)])
    st.configure("TProgressbar", background=TEAL, troughcolor="white",
                 bordercolor=INDIGO, lightcolor=TEAL, darkcolor=TEAL)
    st.configure("TScrollbar", background=BERRY, troughcolor=BG,
                 bordercolor=INDIGO, arrowcolor="white")
    st.map("TScrollbar", background=[("active", hover)])
    st.configure("Sash", sashthickness=7, background=INDIGO)
    st.configure("TSeparator", background=INDIGO)
    st.configure("Info.TLabel", foreground=INK2, font=("TkDefaultFont", 11))
    root.configure(background=BG)
    root.option_add("*Toplevel.background", BG)
    root.option_add("*TCombobox*Listbox.background", "white")
    root.option_add("*TCombobox*Listbox.selectBackground", TEAL)
    root.option_add("*TCombobox*Listbox.selectForeground", INK)


# default stack colours (can be changed in Colours… above the stack diagram)
DEFAULT_MATERIAL_COLORS = {"Ti": "thistle", "Si3N4": "paleturquoise",
                           "SiO2": "darkkhaki"}
SUBSTRATE_KEY, SUBSTRATE_DEFAULT = "__substrate__", "saddlebrown"

matplotlib.rcParams.update({
    "font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "grid.color": GRID_C,
    "axes.titlesize": 10, "legend.frameon": False, "lines.linewidth": 1.4,
})

_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def outside_legend(ax, fontsize=8):
    """Legend outside the axes, to the right, one column: it never covers
    the curves (the layout makes room for it)."""
    leg = ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0),
                    borderaxespad=0.0, ncols=1, fontsize=fontsize)
    leg.set_draggable(True)
    ax._diffr_outside_legend = True
    return leg


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
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                background=BG)
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
        for w in [self.toolbar] + self.toolbar.winfo_children():   # tk widgets
            try:
                w.configure(background=BG)
            except tk.TclError:
                pass
        self.toolbar.pack(side="left", fill="x", expand=True)
        # second row for plot-specific settings (scale, offset, colour map),
        # so the toolbar keeps its room; empty rows take no space
        self.row2 = ttk.Frame(self, padding=(4, 0, 4, 2))
        self.row2.pack(side="top", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.overrides = {}
        self.styles = {}            # axes index -> colour choices
        self.colorbars = []
        self._sweep = None          # thickness colour bar of a sweep plot
        # every save (toolbar button or automatic) keeps the plot's size
        self._orig_savefig = self.fig.savefig
        self.fig.savefig = self._savefig_keep_plot_size
        self.canvas.mpl_connect("scroll_event", self._on_scroll)

    def _side_inches(self):
        """Width (inches) that colour bars and outside legends take to the
        right of the plots on screen."""
        try:
            self.canvas.draw()
            r = self.canvas.get_renderer()
        except Exception:
            return 0.0
        data = [a for a in self.data_axes() if a.axison]
        if not data:
            return 0.0
        right = max(a.get_window_extent(r).x1 for a in data)
        side = [cb.ax.get_window_extent(r).x1 for cb in self.colorbars]
        side += [a.get_legend().get_window_extent(r).x1 for a in data
                 if a.get_legend() is not None
                 and getattr(a, "_diffr_outside_legend", False)]
        if not side or max(side) <= right:
            return 0.0
        return (max(side) - right) / self.fig.dpi + 0.15

    def _savefig_keep_plot_size(self, *args, **kw):
        """Save with the colour bar / legend beside the plot without making
        the plot smaller: the saved figure is widened by their width."""
        extra = self._side_inches()
        if extra <= 0:
            return self._orig_savefig(*args, **kw)
        w, h = self.fig.get_size_inches()
        self.fig.set_size_inches(w + extra, h, forward=False)
        try:
            return self._orig_savefig(*args, **kw)
        finally:
            self.fig.set_size_inches(w, h, forward=False)
            self.canvas.draw_idle()

    # --- drawing protocol: clear() ... draw your axes ... finish()
    def clear(self):
        self.fig.clear()
        self.colorbars = []
        self._sweep = None
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
                outside_legend(ax)
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
            if i == 0 and self._sweep is not None:
                lines = []          # sweep colours are set by the app
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
                if getattr(ax, "_diffr_outside_legend", False):
                    new = outside_legend(ax, fontsize=fs or 8)
                else:
                    new = ax.legend(fontsize=fs, ncols=getattr(leg, "_ncols", 1))
                    new.set_draggable(True)
                new.set_visible(vis)
        for cb in self.colorbars:
            cb.update_normal(cb.mappable)
            fixed = getattr(cb, "_diffr_ticks", None)   # thickness labels
            if fixed:
                cb.set_ticks(fixed[0], labels=fixed[1])

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


def _all_cmaps(first):
    """The given colour maps first, then every other matplotlib colour map
    (reversed '_r' versions can still be typed)."""
    rest = sorted((n for n in colormaps if not n.endswith("_r") and n not in first),
                  key=str.lower)
    return [n for n in first if n in colormaps] + rest


LINE_CMAPS = _all_cmaps(
    ["Blues", "viridis", "plasma", "ocean", "bwr", "coolwarm", "seismic",
     "RdBu", "Spectral", "jet", "rainbow", "turbo", "cool", "hot", "copper",
     "winter", "summer", "autumn", "spring", "terrain", "cubehelix", "magma",
     "inferno", "cividis", "Greens", "Reds", "Purples", "Oranges", "Greys",
     "tab10", "tab20", "Dark2", "Set1", "Set2", "Paired"])
IMAGE_CMAPS = _all_cmaps(
    ["RdBu_r", "bwr", "coolwarm", "seismic", "PuOr_r", "ocean", "viridis",
     "inferno", "jet", "gray"])


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

    def _sweep_batch(self):
        """The sweep shown in this plot (its colours belong to the app)."""
        sw = self.panel._sweep
        if sw is None or self.idx() != 0:
            return None
        return sw.get("batch")

    def _build_sweep(self, p, batch):
        app = self.panel.winfo_toplevel()
        runs = batch["runs"]
        cols = app._run_colors(runs, True)
        hidden = batch.get("hidden", set())
        ttk.Label(p, text="Thicknesses", font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        r = 1
        for i, run in enumerate(runs):
            txt = f"{run['sweep_value']:g} {run.get('sweep_unit', '')}"
            ttk.Label(p, text=txt + ("  (hidden)" if i in hidden else "")).grid(
                row=r, column=0, sticky="w", padx=(0, 8))
            sw = tk.Label(p, width=6, relief="solid", borderwidth=1,
                          background=matplotlib.colors.to_hex(cols[i]),
                          cursor="hand2")
            sw.grid(row=r, column=1, pady=1)
            pick = lambda e=None, rn=run, c=cols[i], t=txt: self.pick_run(rn, c, t)
            sw.bind("<Button-1>", pick)
            ttk.Button(p, text="Change…", command=pick).grid(row=r, column=2, padx=4)
            r += 1
        f = ttk.Frame(p)
        f.grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Label(f, text="Colour all thicknesses from").pack(side="left")
        self.line_cmap = tk.StringVar(value=app.sweep_cmap.get())
        ttk.Combobox(f, textvariable=self.line_cmap, values=LINE_CMAPS,
                     height=20, width=10).pack(side="left", padx=4)
        ttk.Button(f, text="Apply", command=lambda: self._after_app(
            app.set_sweep_cmap, self.line_cmap.get())).pack(side="left")
        ttk.Label(p, foreground=INK2, wraplength=380, justify="left",
                  text="These colours are shared by the ΔR/R, Background and "
                       "Sweep viewer plots and their thickness colour bars.").grid(
            row=r + 1, column=0, columnspan=3, sticky="w")
        return r + 2

    def _after_app(self, fn, *a):
        fn(*a)
        self.build()

    def pick_run(self, run, color, txt):
        _, hexcol = colorchooser.askcolor(color=matplotlib.colors.to_hex(color),
                                          parent=self, title=f"Colour for {txt}")
        if hexcol:
            self._after_app(self.panel.winfo_toplevel().set_sweep_run_color,
                            run, hexcol)

    def build(self):
        p = self.body.inner
        for w in p.winfo_children():
            w.destroy()
        ax = self.panel.data_axes()[self.idx()]
        lines = PlotPanel.data_lines(ax)
        r = 0
        batch = self._sweep_batch()
        if batch is not None:
            r = self._build_sweep(p, batch)
            lines = []
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
                         height=20,
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
                              height=20,
                              width=10)
            cb.pack(side="left", padx=4)
            ttk.Button(f, text="Apply", command=self.apply_img_cmap).pack(side="left")
            ttk.Label(p, foreground=INK2, text="Any matplotlib colour map name "
                      "can be typed; add _r to reverse it.").grid(
                row=r + 1, column=0, columnspan=3, sticky="w")
        if not lines and not ax.images and batch is None:
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
        batch = self._sweep_batch()
        if batch is not None:
            for run in batch["runs"]:
                run.pop("color", None)
            self._after_app(self.panel.winfo_toplevel()._redraw_sweep_plots)
            return
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
        names = list(dict.fromkeys(l["material"].strip()
                                   for l in self.app.layers[:-1]))
        sub = self.app.layers[-1]["material"].strip() if self.app.layers else ""
        ttk.Label(self.body, text="Colours are per material: layers of the "
                  "same material share one. The substrate has its own.",
                  foreground=INK2).grid(row=0, column=0, columnspan=4,
                                        sticky="w", pady=(0, 6))
        for r, name in enumerate(names + [SUBSTRATE_KEY], 1):
            is_sub = name == SUBSTRATE_KEY
            col = matplotlib.colors.to_hex(
                self.app.material_color(name, names, substrate=is_sub))
            ttk.Label(self.body, text=f"Substrate ({sub or 'last layer'})"
                      if is_sub else (name or "(unnamed)")).grid(
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
            color=matplotlib.colors.to_hex(self.app.material_color(
                name, substrate=name == SUBSTRATE_KEY)), parent=self,
            title="Colour for the substrate" if name == SUBSTRATE_KEY
            else f"Colour for {name}")
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


class HelpWindow(tk.Toplevel):
    """Detailed help for one input (from help_info.HELP)."""

    def __init__(self, app, key):
        super().__init__(app)
        h = HI.HELP[key]
        self.title(f"Help — {h['title']}")
        self.transient(app)
        self.geometry("620x420")
        t = tk.Text(self, wrap="word", padx=14, pady=10, relief="flat",
                    background="white", font=("TkDefaultFont", 10))
        sb = ttk.Scrollbar(self, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        bb = ttk.Frame(self, padding=8)
        bb.pack(side="bottom", fill="x")
        ttk.Button(bb, text="Code — where it is used",
                   command=lambda: (CodeWindow(app, key))).pack(side="left")
        ttk.Button(bb, text="Close", command=self.destroy).pack(side="right")
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.tag_configure("title", font=("TkDefaultFont", 13, "bold"),
                        spacing3=6)
        t.tag_configure("head", font=("TkDefaultFont", 10, "bold"),
                        foreground=INDIGO, spacing1=8, spacing3=2)
        t.insert("end", h["title"] + "\n", "title")
        for head, field in (("What it is and why it is there", "what"),
                            ("How to fill it in", "fill"),
                            ("If it is left empty / switched off", "empty")):
            if h.get(field):
                t.insert("end", head + "\n", "head")
                t.insert("end", h[field] + "\n")
        t.configure(state="disabled")
        self.bind("<Escape>", lambda e: self.destroy())


class CodeWindow(tk.Toplevel):
    """The lines of the program where an input is converted and used, read
    from the source files now (so the line numbers match the files)."""

    def __init__(self, app, key):
        super().__init__(app)
        h = HI.HELP[key]
        self.title(f"Code — {h['title']}")
        self.transient(app)
        W = min(1000, app.winfo_screenwidth() - 80)
        H = min(720, app.winfo_screenheight() - 120)
        self.geometry(f"{W}x{H}")
        ttk.Label(self, padding=(10, 8, 10, 4), wraplength=W - 30,
                  justify="left", text=(
                      f"Where '{h['title']}' is converted and used. The lines "
                      f"are read from the program's files just now, so the "
                      f"line numbers match your copy. Highlighted lines contain "
                      f"the parameter; a few lines around them are shown for "
                      f"context.")).pack(anchor="w")
        bb = ttk.Frame(self, padding=8)
        bb.pack(side="bottom", fill="x")
        ttk.Button(bb, text="Help", command=lambda: HelpWindow(app, key)).pack(
            side="left")
        ttk.Button(bb, text="Close", command=self.destroy).pack(side="right")
        f = ttk.Frame(self)
        f.pack(fill="both", expand=True, padx=8)
        t = tk.Text(f, wrap="none", font=("Courier", 10), background="white",
                    relief="flat", padx=6, pady=4)
        ys = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        xs = ttk.Scrollbar(f, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        t.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        t.tag_configure("file", font=("Courier", 10, "bold"),
                        background=LIGHT, spacing1=8, spacing3=2)
        t.tag_configure("num", foreground=INK2)
        t.tag_configure("hit", background="#d9f1ee")
        t.tag_configure("match", background="#e7b8fd", font=("Courier", 10, "bold"))
        blocks = HI.code_snippets(key)
        if not blocks:
            t.insert("end", "No code location is recorded for this input.")
        for fname, where, rows in blocks:
            t.insert("end", f" {fname} — {where}, line {rows[0][0]} \n", "file")
            for num, text, hit, spans in rows:
                start = t.index("end-1c")
                t.insert("end", f"{num:5d}  ", "num")
                col0 = len(f"{num:5d}  ")
                t.insert("end", text + "\n", "hit" if hit else ())
                line = int(start.split(".")[0])
                for a, b in spans:
                    t.tag_add("match", f"{line}.{col0 + a}", f"{line}.{col0 + b}")
            t.insert("end", "\n")
        t.configure(state="disabled")
        self.bind("<Escape>", lambda e: self.destroy())


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
        apply_theme(self)
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
        self.sweep_cmap = tk.StringVar(value="Blues")
        self.drr_offset = tk.StringVar(value="0")
        self.bg_offset = tk.StringVar(value="0")
        for v in (self.drr_offset, self.bg_offset):
            v.trace_add("write", lambda *_: self._schedule_offset_redraw())
        self.stack_colors = {}          # material name -> "#rrggbb" chosen by the user
        self.view_run = tk.StringVar()

        self._build_menu()
        # A tk (not ttk) PanedWindow: each side has a minimum width, so the
        # controls can never be squeezed to nothing; drag the divider to resize.
        outer = tk.PanedWindow(self, orient="horizontal", sashwidth=8,
                               sashrelief="raised", showhandle=False,
                               borderwidth=0, opaqueresize=True,
                               background=INDIGO)
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
        self._build_interfaces_tab()
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
        rm.add_command(label="Run thickness sweep", command=self.run_sweep,
                       accelerator="F6")
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
        self.bind_all("<F6>", lambda e: self.run_sweep())
        self.bind_all("<Control-s>", lambda e: self.save_session())
        self.bind_all("<Control-o>", lambda e: self.open_session())

    # ------------------------------------------------------------ ⓘ buttons
    def _info(self, parent, key):
        """Small ⓘ next to an input; a click opens a floating Help / Code
        menu (it closes when you click elsewhere or pick an option)."""
        b = ttk.Label(parent, text="ⓘ", style="Info.TLabel", cursor="hand2")
        b.bind("<Button-1>", lambda e, k=key: self._info_menu(e, k))
        b.bind("<Enter>", lambda e: b.configure(foreground=BERRY))
        b.bind("<Leave>", lambda e: b.configure(foreground=INK2))
        return b

    def _info_menu(self, e, key):
        title = HI.HELP[key]["title"]
        m = tk.Menu(self, tearoff=0, background="white",
                    activebackground=LIGHT, activeforeground=INK)
        m.add_command(label=f"Help — {title}",
                      command=lambda: HelpWindow(self, key))
        m.add_command(label="Code — where it is used",
                      command=lambda: CodeWindow(self, key))
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _lab_info(self, parent, text, key, **kw):
        """(frame, label): a label followed by its ⓘ."""
        f = ttk.Frame(parent)
        lab = ttk.Label(f, text=text, **kw)
        lab.pack(side="left")
        self._info(f, key).pack(side="left", padx=(3, 0))
        return f, lab

    def _chk_info(self, parent, text, var, key, **kw):
        """A checkbutton followed by its ⓘ (clicking the text still ticks
        the box; only the ⓘ opens the menu). Returns the frame."""
        f = ttk.Frame(parent)
        ttk.Checkbutton(f, text=text, variable=var, **kw).pack(side="left")
        self._info(f, key).pack(side="left", padx=(3, 0))
        return f

    # ------------------------------------------------------------ experiment
    def _value_row(self, parent, r, label, var, unit_var, kind, hint="",
                   key=None):
        """label (ⓘ) | entry | unit menu | '= converted value' ."""
        if key:
            labf, lab = self._lab_info(parent, label, key)
            labf.grid(row=r, column=0, sticky="w", pady=1)
        else:
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
                self._value_row(lf, r, label, v, u, kind, hint, key=key)
        lf = section(p, "Contributions to ΔR/R", len(groups))
        for r, (k, lab) in enumerate(S.PHYSICS_FLAGS):
            self._chk_info(lf, lab, self.flag_vars[k], k).grid(
                row=r, column=0, sticky="w")
        lf = section(p, "Acoustic loss", len(groups) + 1)
        self._chk_info(lf, S.OPTION_FLAGS[0][1], self.flag_vars["include_loss"],
                       "include_loss").grid(row=0, column=0, sticky="w")
        ttk.Label(p, foreground=INK2, wraplength=540, justify="left",
                  text="Type each value in the unit selected next to it. The grey "
                       "text shows what the model receives (nm, ps, SI). Click ⓘ "
                       "for help and the code where a value is used. Hover a "
                       "label for an explanation.").grid(
            row=len(groups) + 2, column=0, sticky="w", padx=8, pady=6)

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

    # ------------------------------------------------------------ interfaces
    def _build_interfaces_tab(self):
        sf = ScrollableFrame(self.tabs)
        self.tabs.add(sf, text="Interfaces")
        self.iface_tab = sf
        p = sf.inner
        top = ttk.Frame(p)
        top.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 2))
        self._chk_info(top, "Graded interfaces (layers merge gradually "
                            "instead of meeting at a sharp boundary)",
                       self.flag_vars["graded_interfaces"],
                       "graded_interfaces").pack(anchor="w")
        hint = ttk.Label(p, foreground=INK2, justify="left", text=(
            "Tick the interfaces that are graded and give the width of the "
            "graded region. Every property (ρ, C11, Cp, expansion, n, k, "
            "photoelastic and thermo-optic coefficients, loss) changes from "
            "one layer to the next across that width, centred on the nominal "
            "interface, so the layer thicknesses are unchanged. Linear: a "
            "straight ramp. erf: a smooth step (like interdiffusion or "
            "roughness); the width is the 10–90 % distance. A width may reach "
            "at most through a whole neighbouring layer (≤ 2 × its "
            "thickness). Width 0 = sharp (the default)."))
        hint.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 4))
        hint.bind("<Configure>", lambda e: hint.config(
            wraplength=max(200, e.width - 8)))
        self.iface_frame = section(p, "Interfaces (top to bottom)", 2)
        self.flag_vars["graded_interfaces"].trace_add(
            "write", lambda *_: (self._refresh_interfaces(), self.schedule_stack()))
        self._iface_vars = []

    def _refresh_interfaces(self):
        """One row per interface between consecutive layers; the values are
        kept in the upper layer's dict (grad_on, grad_w, grad_w_unit,
        grad_shape), so they move with the layers."""
        if not hasattr(self, "iface_frame"):
            return
        f = self.iface_frame
        for w in f.winfo_children():
            w.destroy()
        self._iface_vars = []
        on = self.flag_vars["graded_interfaces"].get()
        st = "normal" if on else "disabled"
        if len(self.layers) < 2:
            ttk.Label(f, text="Add at least two layers.").grid(row=0, column=0)
            return
        for c, h in enumerate(("Interface", "Graded", "Width", "", "Profile",
                               "Limit / note")):
            ttk.Label(f, text=h, font=("TkDefaultFont", 9, "bold")).grid(
                row=0, column=c, sticky="w", padx=3)
        ds = [S.parse_thickness(l) for l in self.layers[:-1]] + [None]
        for i, lay in enumerate(self.layers[:-1]):
            for k, v in S.GRAD_DEFAULTS.items():
                lay.setdefault(k, v)
            below = self.layers[i + 1]
            name = lambda l: l["material"].strip() or "?"
            r = i + 1
            ttk.Label(f, text=f"{i + 1}|{i + 2}  {name(lay)} | {name(below)}"
                      + ("  (substrate)" if i + 1 == len(self.layers) - 1
                         else "")).grid(row=r, column=0, sticky="w", padx=3)
            v_on = tk.BooleanVar(value=bool(lay["grad_on"]))
            v_w = tk.StringVar(value=lay["grad_w"])
            v_u = tk.StringVar(value=lay["grad_w_unit"])
            v_s = tk.StringVar(value=lay["grad_shape"])
            ttk.Checkbutton(f, variable=v_on, state=st).grid(row=r, column=1)
            ttk.Entry(f, textvariable=v_w, width=7, state=st).grid(
                row=r, column=2, padx=2)
            ttk.Combobox(f, textvariable=v_u, values=["nm", "Å", "µm"],
                         state="readonly" if on else "disabled", width=4).grid(
                row=r, column=3)
            ttk.Combobox(f, textvariable=v_s, values=S.GRAD_SHAPES,
                         state="readonly" if on else "disabled", width=7).grid(
                row=r, column=4, padx=2)
            note = ttk.Label(f, foreground=INK2)
            note.grid(row=r, column=5, sticky="w", padx=3)
            lim = [d for d in (ds[i], ds[i + 1]) if d]
            limit = 2 * min(lim) if lim else None

            def upd(*_, lay=lay, v_on=v_on, v_w=v_w, v_u=v_u, v_s=v_s,
                    note=note, limit=limit):
                lay.update(grad_on=v_on.get(), grad_w=v_w.get(),
                           grad_w_unit=v_u.get(), grad_shape=v_s.get())
                txt, col = (f"≤ {limit:g} nm" if limit else ""), INK2
                try:
                    w = U.to_base(float(v_w.get() or 0), "thickness", v_u.get())
                    if w == 0:
                        txt = "0 = sharp; " + txt
                    elif w < 0:
                        txt, col = "width must be ≥ 0", "#c00000"
                    elif limit and w > limit + 1e-9:
                        txt, col = (f"too wide: at most {limit:g} nm here",
                                    "#c00000")
                    elif v_u.get() != "nm":
                        txt = f"= {U.fmt(w)} nm; " + txt
                except ValueError:
                    txt, col = "not a number", "#c00000"
                note.config(text=txt if on and v_on.get() else
                            (f"≤ {limit:g} nm" if limit else ""), foreground=col
                            if on and v_on.get() else INK2)
                self.schedule_stack()
            for v in (v_on, v_w, v_u, v_s):
                v.trace_add("write", upd)
            self._iface_vars.append((v_on, v_w, v_u, v_s))
            upd()

    def _layer_var(self, key):
        v = tk.StringVar()
        v.trace_add("write", lambda *_: self._on_layer_var(key))
        self.lv[key] = v
        return v

    def _build_layer_editor(self, p):
        self.lv, self.conv_upd, self.lit_marks = {}, {}, {}
        for key in S.new_layer():
            if key != "lit" and not key.startswith("grad_"):
                self._layer_var(key)

        # --- general
        g = section(p, "Layer", 0)
        self._lab_info(g, "Material name", "material")[0].grid(
            row=0, column=0, sticky="w")
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
            g, 1, "Thickness", self.lv["d"], self.lv["d_unit"], "thickness",
            key="d")
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
        self._info(rf, "nk_mode").pack(side="left")
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
        self._lab_info(self.file_f, "File", "nk_file")[0].grid(
            row=0, column=0, sticky="w")
        ttk.Entry(self.file_f, textvariable=self.lv["nk_file"], width=40).grid(
            row=0, column=1, columnspan=3, sticky="ew", padx=2)
        ttk.Button(self.file_f, text="Browse…", command=self.browse_nk).grid(
            row=0, column=4, padx=2)
        labf, lab = self._lab_info(self.file_f, "Column-1 unit", "nk_file_unit")
        labf.grid(row=1, column=0, sticky="w")
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
        self._lab_info(self.kfill_f, "This file has no k column. k to use:",
                       "kfill")[0].grid(row=0, column=0, columnspan=4, sticky="w")
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
        self._info(pf, "pe_mode").pack(side="left")
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

        # --- acoustic loss
        a = section(p, "Acoustic loss — optional (frequency-dependent "
                       "damping, α ∝ f²)", 5)
        lf = ttk.Frame(a)
        lf.grid(row=0, column=0, columnspan=6, sticky="w")
        for val, txt in (("none", "None"), ("viscosity", "Viscosity η"),
                         ("attenuation", "Attenuation α at a frequency")):
            ttk.Radiobutton(lf, text=txt, value=val,
                            variable=self.lv["loss_mode"]).pack(side="left",
                                                                padx=(0, 8))
        self._info(lf, "loss_mode").pack(side="left")
        self.loss_rows = {}
        r1 = ttk.Frame(a)
        self._lab_info(r1, "η", "visc")[0].pack(side="left")
        ttk.Entry(r1, textvariable=self.lv["visc"], width=10).pack(side="left",
                                                                   padx=2)
        ttk.Combobox(r1, textvariable=self.lv["visc_unit"], state="readonly",
                     values=U.choices("viscosity"), width=7).pack(side="left")
        self.loss_rows["viscosity"] = r1
        r2 = ttk.Frame(a)
        self._lab_info(r2, "α", "att")[0].pack(side="left")
        ttk.Entry(r2, textvariable=self.lv["att"], width=9).pack(side="left",
                                                                 padx=2)
        ttk.Combobox(r2, textvariable=self.lv["att_unit"], state="readonly",
                     values=U.choices("attenuation"), width=7).pack(side="left")
        ttk.Label(r2, text="at").pack(side="left", padx=4)
        ttk.Entry(r2, textvariable=self.lv["att_f"], width=7).pack(side="left")
        ttk.Combobox(r2, textvariable=self.lv["att_f_unit"], state="readonly",
                     values=U.choices("frequency"), width=5).pack(side="left",
                                                                  padx=2)
        self.loss_rows["attenuation"] = r2
        self.loss_conv = ttk.Label(a, foreground=INK2, wraplength=520,
                                   justify="left")
        self.loss_conv.grid(row=2, column=0, columnspan=6, sticky="w")

    def _prop_row(self, parent, r, key):
        """One material property: label, value (Re/Im), unit, conversion,
        literature marker. Returns the widgets (to show/hide)."""
        _, label, kind, _, required, default = S.PROP_INFO[key]
        cplx = key in {p[0] for p in S.COMPLEX_PROPS}
        w = []
        labf, lab = self._lab_info(parent, label + ("" if required else " (opt.)"),
                                   key)
        labf.grid(row=r, column=0, sticky="w", pady=1)
        w.append(labf)
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
        if key in self.LOSS_KEYS:
            self._update_loss_view()
        base = key[:-5] if key.endswith("_unit") else key
        base = base[:-3] if base.endswith(("_re", "_im")) else base
        if base in self.conv_upd:
            self.conv_upd[base]()
        self._refresh_lit_marks()
        self._refresh_tree_row(self.sel)
        if key in ("material", "d", "d_unit"):
            self.schedule_stack()
            self._refresh_sweep_layers()      # names/thickness in the sweep list
            self._refresh_interfaces()
        if key in ("nk_mode", "nk_file", "nk_file_unit", "kfill_pump",
                   "kfill_probe"):
            self.schedule_file_nk()

    LOSS_KEYS = ("loss_mode", "visc", "visc_unit", "att", "att_unit", "att_f",
                 "att_f_unit", "rho", "rho_unit", "v", "v_unit")

    def _update_loss_view(self):
        """Show the row of the chosen loss input and what it means: η and
        the amplitude attenuation at 50 / 100 / 200 GHz."""
        mode = self.lv["loss_mode"].get()
        for m, row in self.loss_rows.items():
            if m == mode:
                row.grid(row=1, column=0, columnspan=6, sticky="w", pady=2)
            else:
                row.grid_remove()
        if self.sel is None or mode == "none":
            self.loss_conv.config(text="No acoustic loss: echoes keep their "
                                       "amplitude (apart from transmission).",
                                  foreground=INK2)
            return
        lay = self.layers[self.sel]
        try:
            rho = U.to_base(float(lay["rho"]), "density", lay["rho_unit"])
            v = U.to_base(float(lay["v"]), "velocity", lay["v_unit"])
            if mode == "viscosity":
                eta = U.to_base(float(lay["visc"] or 0), "viscosity",
                                lay["visc_unit"])
                head = f"= {U.fmt(eta)} Pa·s"
            else:
                alpha = U.to_base(float(lay["att"] or 0), "attenuation",
                                  lay["att_unit"])
                f_hz = U.to_base(float(lay["att_f"]), "frequency", lay["att_f_unit"])
                eta = M.viscosity_from_attenuation(alpha, rho, v, f_hz)
                head = f"→ η = 2ρv³α/ω² = {U.fmt(eta)} Pa·s"
            al = [M.attenuation_from_viscosity(eta, rho, v, f) * 1e-6
                  for f in (50e9, 100e9, 200e9)]
            self.loss_conv.config(
                text=f"{head};  amplitude attenuation α = {al[0]:.3g} / "
                     f"{al[1]:.3g} / {al[2]:.3g} per µm at 50 / 100 / 200 GHz",
                foreground=INK2)
        except (ValueError, ZeroDivisionError, KeyError):
            self.loss_conv.config(text="Enter numbers (and ρ, v above) to see "
                                       "the attenuation.", foreground="#c00000")

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
        self._refresh_interfaces()

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
        self._update_loss_view()
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
        was_swept = self.layers[self.sel] is getattr(self, "_sweep_obj", None)
        del self.layers[self.sel]
        self.refresh_tree(max(0, self.sel - 1))
        if was_swept and len(self.layers) > 1:
            self.log(f"The deleted layer was the swept layer; the sweep now "
                     f"uses {self.layer_title(self.sweep_index() or 0)}.", "warn")

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
        self.sweep_tab = sf
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.schedule_stack())
        tab = sf.inner
        ttk.Label(tab, wraplength=560, justify="left", foreground=INK2,
                  text="Vary the thickness of one layer, run the model for each "
                       "value with everything else unchanged, and overlay the "
                       "ΔR/R curves. What is saved, and under which names, is "
                       "set in the Output tab.").grid(row=0, column=0, sticky="w",
                                                      padx=8, pady=(6, 2))
        g = section(tab, "Thickness range", 1)
        self._lab_info(g, "Layer", "sweep_layer")[0].grid(row=0, column=0, sticky="w")
        self.sweep_layer = tk.StringVar()
        self.sweep_cb = ttk.Combobox(g, textvariable=self.sweep_layer,
                                     state="readonly", width=38)
        self.sweep_cb.bind("<<ComboboxSelected>>", self._on_sweep_choice)
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
        self._lab_info(g, "Thickness", "sweep_range")[0].grid(row=2, column=0,
                                                               sticky="w")
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
            v.trace_add("write", lambda *_: (self.schedule_preview(),
                                             self.schedule_stack()))

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
        self._chk_info(g, "Subtract the background from every run",
                       ov["bg_enabled"], "bg_enabled",
                       command=self._on_bg_toggle).grid(
            row=0, column=0, columnspan=4, sticky="w")
        self._chk_info(g, "Fit only delays after a cut-off:", ov["bg_use_cut"],
                       "bg_use_cut").grid(
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
        self._chk_info(g, "Force decaying exponentials (b ≤ 0, d ≤ 0)",
                       ov["bg_force_decay"], "bg_force_decay").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._lab_info(g, "Before the cut-off the subtracted column holds",
                       "bg_before")[0].grid(
            row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(g, textvariable=ov["bg_before"], width=26, state="readonly",
                     values=list(B.BEFORE_CHOICES)).grid(
            row=4, column=1, columnspan=3, sticky="w", pady=(6, 0))

        c = section(tab, "In the ΔR/R file and the plots", 2)
        self._lab_info(c, "Extra columns next to dR_over_R:", "bg_cols")[0].grid(
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
        self._lab_info(c, "Main ΔR/R plot shows", "bg_plot_main")[0].grid(
            row=4, column=0, sticky="w")
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
        self._lab_info(w, "Folder", "out_folder")[0].grid(row=0, column=0, sticky="w")
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
            self._chk_info(n, text, ov[flag], "out_auto").grid(
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
        self._chk_info(c, "CSV table (opens in Excel, Origin, …)", ov["csv"],
                       "out_csv").grid(row=0, column=0, columnspan=4,
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
        self._chk_info(c, "NPZ archive (all arrays + metadata, for Python)",
                       ov["npz"], "out_npz").grid(row=3, column=0, columnspan=4,
                                                 sticky="w", pady=(6, 0))
        nf = ttk.Frame(c)
        nf.grid(row=4, column=0, columnspan=4, sticky="w", padx=(18, 0))
        ttk.Checkbutton(nf, text="depth profiles (kernels, absorption)",
                        variable=ov["npz_profiles"]).pack(side="left")
        ttk.Checkbutton(nf, text="strain map η(z,t), if recorded",
                        variable=ov["npz_strain"]).pack(side="left", padx=8)
        self._chk_info(c, "Metadata JSON (inputs with units, converted values, "
                          "placeholders, run time)", ov["meta_json"],
                       "out_meta").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._chk_info(c, "Figures", ov["figures"], "out_figures").grid(
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
        self._lab_info(k, "Next number", "out_counter")[0].grid(row=0, column=0,
                                                                 sticky="w")
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
        self._chk_info(k, "Ask before overwriting existing files",
                       ov["ask_overwrite"], "out_overwrite").grid(
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
        idx = self.sweep_index()
        if idx is None:
            raise ValueError("Add at least one layer above the substrate to "
                             "sweep.")
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
        lines = [f"{len(p['vals'])} simulations, varying the thickness of "
                 f"{self.layer_title(p['idx'])}: "
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
        lines.append(self._bg_plan_text())
        lines.append("Estimated time: " + self._time_estimate(p["works"]))
        return "\n".join(lines)

    def _bg_plan_text(self):
        """One line on whether / how the background will be subtracted."""
        if not self._bg_shown():
            return ("Background subtraction: off (switch it on in the "
                    "Background tab)")
        try:
            st = self._bg_settings()
        except ValueError as e:
            return f"Background subtraction: {e}"
        rng = (f"fit on delays > {st['cut_ps']:g} ps" if st["use_cut"]
               else "fit on the whole time axis")
        o = self._opts()
        cols = []
        if o["bg_col_sub"]:
            cols.append("dR_over_R_minus_bg")
        if o["bg_col_fit"]:
            cols.append("bg_fit")
        where = (f"; {' and '.join(cols)} written right after each dR_over_R "
                 f"column" if cols else "; no extra column selected")
        return f"Background subtraction: on, for every thickness ({rng}){where}"

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

    def layer_title(self, i, layers=None):
        """'Si3N4 (layer 4)' — never just a number."""
        layers = layers or self.layers
        name = layers[i]["material"].strip() or "unnamed layer"
        return f"{name} (layer {i + 1})"

    def _sweep_choice_text(self, i):
        lay = self.layers[i]
        name = lay["material"].strip() or "(no material name yet)"
        d = f"{lay['d']} {lay['d_unit']}" if lay["d"].strip() else "no thickness"
        return f"Layer {i + 1}: {name}  ({d})"

    def sweep_index(self):
        """Index of the swept layer. The sweep follows the layer itself
        (renames, moves), not a position or a text."""
        obj = getattr(self, "_sweep_obj", None)
        for i, lay in enumerate(self.layers[:-1]):
            if lay is obj:
                return i
        return None

    def _refresh_sweep_layers(self):
        cands = self.layers[:-1]
        vals = [self._sweep_choice_text(i) for i in range(len(cands))]
        self.sweep_cb.config(values=vals)
        i = self.sweep_index()
        if i is None and cands:            # new stack, or the layer was deleted
            i = 0
            self._sweep_obj = cands[0]
        self._setting_sweep = True
        try:
            self.sweep_layer.set(vals[i] if cands else "")
        finally:
            self._setting_sweep = False
        self.schedule_preview()
        self.schedule_stack()

    def _on_sweep_choice(self, _=None):
        k = self.sweep_cb.current()
        if 0 <= k < len(self.layers) - 1:
            self._sweep_obj = self.layers[k]
        self._refresh_sweep_layers()

    # --------------------------------------------------------------- run bar
    def _build_run_bar(self, parent):
        f = ttk.Frame(parent, padding=(4, 6))
        f.pack(side="bottom", fill="x")
        self.run_btn = ttk.Button(f, text="▶ Run model (F5)", command=self.run_single,
                                  style="Accent.TButton")
        self.run_btn.pack(side="left")
        self.sweep_btn = ttk.Button(f, text="▶ Run sweep (F6)",
                                    command=self.run_sweep, style="Accent.TButton")
        self.sweep_btn.pack(side="left", padx=(4, 0))
        Tooltip(self.sweep_btn, "Run the thickness sweep set up in the "
                                "Thickness sweep tab, from any tab")
        self.cancel_btn = ttk.Button(f, text="Cancel", command=self.cancel,
                                     state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        self.pbar = ttk.Progressbar(f, length=100, maximum=1.0)
        self.pbar.pack(side="left", padx=6, fill="x", expand=True)
        # the status gets its own line so long messages (times) fit
        self.status = ttk.Label(parent, text="ready", foreground=INK2,
                                padding=(6, 0, 6, 4), anchor="w")
        self.status.pack(side="bottom", fill="x", before=f)
        self.status.bind("<Configure>", lambda e: self.status.config(
            wraplength=max(200, e.width - 12)))

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
        self._chk_info(bar, "Overlay all runs", self.overlay, "overlay",
                       command=self.draw_drr).pack(side="left", padx=6)
        ttk.Button(bar, text="Clear runs", command=self.clear_runs).pack(side="left")
        ttk.Label(bar2, text="Extra plots (ΔR/R and the stack are always "
                             "shown):").pack(side="left")
        self._info(bar2, "extra_plots").pack(side="left", padx=(2, 6))
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
        ttk.Label(self.drr_panel.row2, text="y scale").pack(side="left")
        cb = ttk.Combobox(self.drr_panel.row2, textvariable=self.scale_exp,
                          state="readonly", width=4,
                          values=["0", "3", "4", "5", "6"])
        cb.pack(side="left", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self.draw_drr())
        ttk.Label(self.drr_panel.row2, text="Offset").pack(side="left",
                                                            padx=(12, 0))
        e = ttk.Entry(self.drr_panel.row2, textvariable=self.drr_offset,
                      width=6)
        e.pack(side="left", padx=2)
        Tooltip(e, "Shift each curve by this amount (in the plotted units, "
                   "i.e. after the y scale) so overlapping curves can be told "
                   "apart: curve n is moved up by n × offset. 0 = no offset. "
                   "The saved data are never offset.")
        ttk.Label(self.drr_panel.row2, text="Sweep colours").pack(
            side="left", padx=(12, 0))
        cb = ttk.Combobox(self.drr_panel.row2, textvariable=self.sweep_cmap,
                          width=11, height=20, values=LINE_CMAPS)
        cb.pack(side="left", padx=2)
        cb.bind("<<ComboboxSelected>>",
                lambda e: self.set_sweep_cmap(self.sweep_cmap.get()))
        cb.bind("<Return>", lambda e: self.set_sweep_cmap(self.sweep_cmap.get()))
        Tooltip(cb, "Colour map for the curves of a thickness sweep (thin to "
                    "thick). Any matplotlib colour map; add _r to reverse it.")

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
        ttk.Label(self.bg_panel.row2, text="Offset").pack(side="left")
        e = ttk.Entry(self.bg_panel.row2, textvariable=self.bg_offset,
                      width=6)
        e.pack(side="left", padx=2)
        Tooltip(e, "Shift each thickness of a sweep by this amount (plotted "
                   "units) so the curves can be told apart. 0 = no offset.")
        self.ptabs.add(self.bg_panel, text="Background")
        self._build_viewer()
        for pnl in (self.drr_panel, self.bg_panel, self.viewer_panel):
            pnl.canvas.mpl_connect("button_press_event",
                                   lambda e, q=pnl: self._on_sweep_press(q, e))
            pnl.canvas.mpl_connect("motion_notify_event",
                                   lambda e, q=pnl: self._on_sweep_motion(q, e))
            pnl.canvas.mpl_connect("button_release_event",
                                   lambda e: setattr(self, "_viewer_drag", False))
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
        mark = self._sweep_marker()
        for i, (lay, w) in enumerate(zip(self.layers, widths)):
            name = lay["material"].strip() or "?"
            sub = i == len(self.layers) - 1
            col = self.material_color(lay["material"].strip(), order, substrate=sub)
            ax.add_patch(Rectangle((x, 0), w, 1, facecolor=col, alpha=.85,
                                   edgecolor="white", lw=2,
                                   hatch="///" if sub else None))
            if sub:
                txt = f"{name}\nsubstrate →∞"
            else:
                d = ds[i]
                txt = f"{name}\n{lay['d']} {lay['d_unit']}" if d else f"{name}\n? nm"
            swept = mark is not None and i == mark["idx"] and not sub
            if swept:
                txt = f"{name}\n{mark['range']}"
            narrow = w / total < 0.07
            if w / total < 0.02 and not sub and not swept:   # hairline
                txt = ""
            ax.text(x + w / 2, 0.5, txt, ha="center", va="center",
                    rotation=90 if narrow else 0, fontsize=8 if narrow else 9,
                    color="white" if _dark(col) else INK, clip_on=True,
                    bbox=dict(facecolor=col, edgecolor="none", alpha=.85, pad=1)
                    if sub else None)
            if i == self.sel:
                ax.add_patch(Rectangle((x, -0.06), w, 1.12, fill=False,
                                       edgecolor=INK, lw=1.6))
            if swept:
                ax.add_patch(Rectangle((x + 0.01 * w, 0.02), 0.98 * w, 0.96,
                                       fill=False, edgecolor=INK, lw=2.2,
                                       ls=(0, (4, 2))))
                ax.text(x + w / 2, 1.08, "◀ thickness swept ▶", ha="center",
                        va="bottom", fontsize=8, fontweight="bold", color=INK)
            self._stack_x.append((x, x + w))
            x += w
        n_grad = self._draw_stack_gradients(ax, mode, order)
        ax.annotate("", xy=(0, 0.5), xytext=(-air, 0.5),
                    arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.5))
        ax.text(-air / 2, 0.62, "pump\nprobe", ha="center", va="bottom",
                fontsize=8, color=INK2)
        ax.text(-air / 2, 0.38, "air", ha="center", va="top", fontsize=8,
                color=INK2)
        ax.set_xlim(-air, total)
        ax.set_ylim(-0.2 if n_grad else -0.1, 1.25 if mark else 1.1)
        ax.set_yticks([])
        for s in ("left", "right", "top"):
            ax.spines[s].set_visible(False)
        if mode == "Proportional":
            ax.set_xlabel("depth z (nm) — substrate drawn truncated; "
                          "zoom in to see thin layers")
        else:
            ax.set_xticks([])
            ax.spines["bottom"].set_visible(False)
        title = (f"Sample stack ({len(self.layers)} layers, "
                 f"{sum(d for d in ds if d):g} nm above the substrate; "
                 f"widths: {mode.lower()})")
        if n_grad:
            title += f"; {n_grad} graded interface{'s' if n_grad > 1 else ''}"
        if mark:
            title += (f"\nThickness sweep{' (planned)' if mark['planned'] else ''}"
                      f": {mark['title']} — {mark['range_long']}")
        ax.set_title(title)
        p.finish()

    def _draw_stack_gradients(self, ax, mode, order):
        """Graded interfaces on the stack diagram: the two colours blend
        across the graded region (to scale in 'Proportional', otherwise a
        fixed share of the neighbouring blocks), with the width written
        below. Returns how many were drawn."""
        if not self.flag_vars["graded_interfaces"].get():
            return 0
        n = 0
        last = len(self.layers) - 1
        for i, lay in enumerate(self.layers[:-1]):
            if not lay.get("grad_on"):
                continue
            try:
                w_nm = U.to_base(float(lay["grad_w"]), "thickness",
                                 lay["grad_w_unit"])
            except (ValueError, KeyError):
                continue
            if w_nm <= 0 or i + 1 >= len(self._stack_x):
                continue
            (a0, a1), (b0, b1) = self._stack_x[i], self._stack_x[i + 1]
            if mode == "Proportional":
                h_up = h_dn = w_nm / 2
            else:                          # not to scale: a visible band
                h_up, h_dn = 0.3 * (a1 - a0), 0.3 * (b1 - b0)
            h_up, h_dn = min(h_up, a1 - a0), min(h_dn, b1 - b0)
            c_up = matplotlib.colors.to_rgb(self.material_color(
                lay["material"].strip(), order))
            c_dn = matplotlib.colors.to_rgb(self.material_color(
                self.layers[i + 1]["material"].strip(), order,
                substrate=i + 1 == last))
            xs = np.linspace(-h_up, h_dn, 96)
            if mode == "Proportional":     # to scale: x is in nm
                frac = M.gradient_profile(xs, w_nm, lay["grad_shape"])
            else:                          # the band is the graded width
                u = np.where(xs < 0, xs / max(h_up, 1e-12), xs / max(h_dn, 1e-12))
                frac = M.gradient_profile(u, 2.0, lay["grad_shape"])
            img = (1 - frac)[None, :, None] * np.array(c_up) + \
                frac[None, :, None] * np.array(c_dn)
            ax.imshow(img, extent=(a1 - h_up, a1 + h_dn, 0, 1), aspect="auto",
                      alpha=.85, zorder=2, interpolation="bilinear")
            ax.plot([a1 - h_up, a1 + h_dn], [-0.05, -0.05], color=INK2, lw=1,
                    marker="|", ms=6)
            ax.text(a1, -0.08, f"{lay['grad_w']} {lay['grad_w_unit']} "
                    f"{lay['grad_shape']}", ha="center", va="top", fontsize=7,
                    color=INK2)
            n += 1
        return n

    def material_color(self, name, order=None, substrate=False):
        """Colour of a layer in the stack diagram: the user's choice, else
        the defaults (Ti thistle, Si3N4 paleturquoise, SiO2 darkkhaki,
        substrate saddlebrown), else the next palette slot."""
        if substrate:
            return self.stack_colors.get(SUBSTRATE_KEY, SUBSTRATE_DEFAULT)
        if name in self.stack_colors:
            return self.stack_colors[name]
        if name in DEFAULT_MATERIAL_COLORS:
            return DEFAULT_MATERIAL_COLORS[name]
        order = [n for n in (order or dict.fromkeys(
            l["material"].strip() for l in self.layers))
            if n not in DEFAULT_MATERIAL_COLORS]
        return SERIES[order.index(name) % len(SERIES)] if name in order else SERIES[0]

    def _sweep_marker(self):
        """What to mark as swept on the stack diagram, or None.

        While the Thickness sweep tab is open: the sweep being set up.
        Otherwise, if the ΔR/R plot shows a sweep: the sweep as it was run.
        """
        planned = (hasattr(self, "sweep_tab")
                   and self.tabs.select() == str(self.sweep_tab))
        if planned:
            idx = self.sweep_index()
            if idx is None:
                return None
            try:
                p = self._sweep_plan()
                vals, unit = p["vals"], p["unit"]
                step = float(self.sweep_step.get())
            except (ValueError, TypeError):
                return dict(idx=idx, planned=True, title=self.layer_title(idx),
                            range="(range not valid yet)",
                            range_long="range not valid yet")
        else:
            b = self.batches[-1] if self.batches else None
            if not b or b.get("kind") != "sweep" or "sweep_idx" not in b:
                return None
            idx, vals, unit = b["sweep_idx"], b["sweep_vals"], b["sweep_unit"]
            step = b.get("sweep_step")
            if idx >= len(self.layers) - 1:
                return None
            # only while the stack on screen is the one that was swept
            if b.get("stack_fp") != self._fingerprint(skip_d=idx, stack_only=True):
                return None
        n = len(vals)
        rng = f"{vals[0]:g} → {vals[-1]:g} {unit}" if n > 1 else f"{vals[0]:g} {unit}"
        step_txt = f" in steps of {step:g} {unit}" if step and n > 1 else ""
        title = (self.layer_title(idx) if planned else
                 f"{b.get('sweep_name') or self.layers[idx]['material']} "
                 f"(layer {idx + 1})")
        return dict(idx=idx, planned=planned, title=title,
                    range=f"{rng}\n({n} runs)",
                    range_long=f"{rng}{step_txt}, {n} runs")

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

    def _schedule_offset_redraw(self):
        if getattr(self, "_offset_job", None):
            self.after_cancel(self._offset_job)
        self._offset_job = self.after(400, self._offset_redraw)

    def _offset_redraw(self):
        self._offset_job = None
        self.draw_drr()
        if self._bg_shown():
            self.draw_background()

    @staticmethod
    def _offset_value(var):
        try:
            v = float(var.get())
            return v if np.isfinite(v) else 0.0
        except ValueError:
            return 0.0

    @staticmethod
    def sweep_title(batch):
        """'Thickness sweep of layer 4 (Si3N4) from 100 to 600 nm'."""
        r = batch["runs"]
        name = batch.get("sweep_name") or "?"
        idx = batch.get("sweep_idx", 0)
        unit = r[0].get("sweep_unit", "")
        return (f"Thickness sweep of layer {idx + 1} ({name}) from "
                f"{r[0]['sweep_value']:g} to {r[-1]['sweep_value']:g} {unit}")

    def _add_sweep_cbar(self, fig, axes, batch, cols, cur=None):
        """Thickness colour bar for a sweep plot (instead of a legend): one
        colour block per thickness, hidden thicknesses hatched out, the
        current one framed. Block i spans y = i ± 0.5 on the colour bar."""
        runs = batch["runs"]
        n = len(runs)
        cmap = ListedColormap([matplotlib.colors.to_rgba(c) for c in cols])
        sm = ScalarMappable(norm=BoundaryNorm(np.arange(n + 1) - 0.5, n), cmap=cmap)
        cb = fig.colorbar(sm, ax=axes, fraction=0.05, pad=0.02, aspect=30)
        cb.ax._diffr_cbar = True
        step = max(1, int(np.ceil(n / 14)))
        ticks = list(range(0, n, step))
        if ticks[-1] != n - 1:
            ticks.append(n - 1)
        labels = [f"{runs[i]['sweep_value']:g}" for i in ticks]
        cb.set_ticks(ticks, labels=labels)
        cb._diffr_ticks = (ticks, labels)     # re-applied after restyling
        unit = runs[0].get("sweep_unit", "")
        cb.set_label(f"{batch.get('sweep_name', '')} (layer "
                     f"{batch.get('sweep_idx', 0) + 1}) thickness ({unit})")
        tr = blended_transform_factory(cb.ax.transAxes, cb.ax.transData)
        for i in batch.setdefault("hidden", set()):
            cb.ax.add_patch(Rectangle((0, i - 0.5), 1, 1, transform=tr,
                                      facecolor="white", alpha=.8, hatch="xxx",
                                      edgecolor=INK2, lw=0, zorder=5))
        if cur is not None:
            cb.ax.add_patch(Rectangle((0, cur - 0.5), 1, 1, transform=tr,
                                      fill=False, edgecolor=INK, lw=2.4,
                                      clip_on=False, zorder=6))
        return cb

    def _register_sweep_cbar(self, panel, cb, batch):
        panel.colorbars.append(cb)
        panel._sweep = dict(cbar_ax=cb.ax, batch=batch)

    def _on_sweep_press(self, panel, e):
        """Right-click on a thickness in the colour bar hides/shows it in all
        sweep plots. In the Sweep viewer, left-click/drag picks the thickness
        shown."""
        info = panel._sweep
        if info is None or panel.toolbar.mode or e.ydata is None:
            return
        if e.inaxes is not info["cbar_ax"]:
            return
        batch = info["batch"]
        i = int(round(e.ydata))
        if not 0 <= i < len(batch["runs"]):
            return
        if e.button == 3:
            hidden = batch.setdefault("hidden", set())
            hidden.symmetric_difference_update({i})
            if len(hidden) == len(batch["runs"]):
                hidden.discard(i)            # keep at least one
            self._redraw_sweep_plots()
        elif e.button == 1 and panel is getattr(self, "viewer_panel", None):
            self._viewer_drag = True
            self._viewer_pick(i)

    def _on_sweep_motion(self, panel, e):
        if not getattr(self, "_viewer_drag", False) or e.ydata is None:
            return
        info = panel._sweep
        if info and e.inaxes is info["cbar_ax"]:
            self._viewer_pick(int(round(e.ydata)))

    def _redraw_sweep_plots(self):
        self.draw_drr()
        if self._bg_shown():
            self.draw_background()
        self.draw_viewer()

    def _run_colors(self, runs, sweep):
        if len(runs) == 1:
            return [SERIES[0]]
        if sweep or len(runs) > len(SERIES):
            name = self.sweep_cmap.get()
            cols = cmap_colors(name if name in colormaps else "Blues", len(runs))
            if sweep:           # thicknesses given their own colour
                cols = [r.get("color") or c for r, c in zip(runs, cols)]
            return cols
        return SERIES[:len(runs)]

    def set_sweep_cmap(self, name):
        """Colour map of every sweep plot (ΔR/R, Background, Sweep viewer and
        their colour bars); forgets single-thickness colour picks."""
        name = name.strip()
        if name not in colormaps:
            messagebox.showerror("Sweep colours", f"Unknown colour map {name!r}.")
            return
        self.sweep_cmap.set(name)
        for b in self.batches:
            for r in b["runs"]:
                r.pop("color", None)
        self._redraw_sweep_plots()

    def set_sweep_run_color(self, run, color):
        """One thickness of a sweep in its own colour (None: back to the
        colour map), in every sweep plot."""
        if color:
            run["color"] = color
        else:
            run.pop("color", None)
        self._redraw_sweep_plots()

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
        off = self._offset_value(self.drr_offset)
        # runs made from different inputs (another sample or experiment) are
        # tagged [inputs A], [inputs B], … so they are not mistaken for each other
        # compare everything except the thicknesses that a shown sweep varies
        # (those are in the sweep labels already)
        skip = {b["sweep_idx"] for b in batches if b.get("kind") == "sweep"
                and "sweep_idx" in b}
        def fp(b):
            inp = b["runs"][0]["inputs"]
            return self._fingerprint(inp["layers"], skip, inp["config"],
                                     inp["flags"])
        keys = {id(b): fp(b) for b in batches}
        fps = list(dict.fromkeys(keys.values()))
        tag = {}
        if len(fps) > 1:
            for b in batches:
                tag[id(b)] = f"  [inputs {chr(65 + fps.index(keys[id(b)]))}]"
        n_sub = 0
        cols = self._run_colors(runs, sweep)
        hidden = batches[0].setdefault("hidden", set()) if sweep else set()
        k = 0
        for b in batches:
            for r in b["runs"]:
                res, c, sh = r["res"], cols[k], k * off
                lab = r["label"] + tag.get(id(b), "")
                k += 1
                if k - 1 in hidden:
                    continue
                bg = res.get("bg")
                if mode == "original ΔR/R" or not bg:
                    ax.plot(res["t_ps"], res["drr"] * 10 ** exp + sh, color=c,
                            lw=1.2, label=lab)
                    continue
                n_sub += 1
                if mode == "both":
                    ax.plot(res["t_ps"], res["drr"] * 10 ** exp + sh, color=c,
                            lw=.9, alpha=.45, label=lab)
                ax.plot(res["t_ps"], bg["sub"] * 10 ** exp + sh, color=c, lw=1.2,
                        label=lab + " − bg" if mode == "both" else lab)
        tmax = max(r["res"]["t_ps"].max() for r in runs)
        ax.set_xlim(0, tmax)
        ax.axhline(0, color=GRID_C, lw=.8, zorder=0, gid="ref")
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel(drr_label(exp) + (f"  (curves offset by {off:g})"
                                         if off and len(runs) > 1 else ""))
        sub_txt = (" — background subtracted" if n_sub and mode ==
                   "background-subtracted" else
                   " — faint: original, solid: background subtracted"
                   if n_sub and mode == "both" else "")
        if sweep:
            ax.set_title(self.sweep_title(batches[0]) + sub_txt
                         + (f"\n{len(hidden)} thickness(es) hidden — right-click "
                            f"the colour bar to show them" if hidden else ""))
            cb = self._add_sweep_cbar(p.fig, ax, batches[0], cols)
            self._register_sweep_cbar(p, cb, batches[0])
        else:
            ax.set_title("Modelled differential reflectivity" + sub_txt
                         + ("\nOverlaid runs come from different inputs: "
                            "compare only runs with the same [inputs …] tag"
                            if tag else ""))
        ax.grid(alpha=.6)
        if not sweep and (len(runs) > 1 or (n_sub and mode == "both")):
            outside_legend(ax)
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
        outside_legend(ax)
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
        self.draw_viewer()
        self._update_bg_result()

    # ---------------------------------------------------------- sweep viewer
    VIEWER_MODES = ["Current curve only", "Current + others faint",
                    "All shown curves"]
    VIEWER_SIGNALS = ["ΔR/R − background", "ΔR/R"]

    def _build_viewer(self):
        vp = self.viewer_panel = PlotPanel(self.ptabs)
        vp.redraw = self.draw_viewer
        self.ptabs.add(vp, text="Sweep viewer")
        self.viewer_mode = tk.StringVar(value=self.VIEWER_MODES[0])
        self.viewer_signal = tk.StringVar(value=self.VIEWER_SIGNALS[0])
        self.viewer_fps = tk.StringVar(value="4")
        self.viewer_loop = tk.BooleanVar(value=True)
        self.viewer_cur, self._viewer_bid = 0, None
        self._viewer_drag, self._play_job, self._viewer_job = False, None, None
        self._viewer_setting = False
        r = vp.row2
        for lab, var, vals, w in (("Show", self.viewer_mode, self.VIEWER_MODES, 21),
                                  ("Signal", self.viewer_signal,
                                   self.VIEWER_SIGNALS, 17)):
            ttk.Label(r, text=lab).pack(side="left", padx=(6, 2))
            cb = ttk.Combobox(r, textvariable=var, values=vals, state="readonly",
                              width=w)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self.draw_viewer())
        self._info(r, "sweep_viewer").pack(side="left", padx=6)
        r3 = ttk.Frame(vp, padding=(4, 0, 4, 2))
        r3.pack(side="top", fill="x", before=vp.canvas.get_tk_widget())
        self.play_btn = ttk.Button(r3, text="▶ Play", command=self.toggle_play,
                                   style="Accent.TButton")
        self.play_btn.pack(side="left")
        ttk.Label(r3, text="frames/s").pack(side="left", padx=(8, 2))
        ttk.Spinbox(r3, from_=1, to=30, textvariable=self.viewer_fps,
                    width=4).pack(side="left")
        ttk.Checkbutton(r3, text="loop", variable=self.viewer_loop).pack(
            side="left", padx=6)
        ttk.Button(r3, text="Show all thicknesses",
                   command=self._viewer_show_all).pack(side="left", padx=6)
        ttk.Button(r3, text="Save GIF…", command=self.save_gif).pack(
            side="left", padx=6)
        # zoom range (kept while stepping) and offset
        self.viewer_range = {k: tk.StringVar() for k in ("x0", "x1", "y0", "y1")}
        self.viewer_offset = tk.StringVar(value="0")
        self._viewer_lock = False          # True while the GUI fills the boxes
        self._viewer_range_job = None
        r4 = ttk.Frame(vp, padding=(4, 0, 4, 2))
        r4.pack(side="top", fill="x", before=vp.canvas.get_tk_widget())
        for lab, a, b, unit in (("x (delay) from", "x0", "x1", "ps"),
                                ("y from", "y0", "y1", "")):
            ttk.Label(r4, text=lab).pack(side="left", padx=(6, 2))
            ttk.Entry(r4, textvariable=self.viewer_range[a], width=8).pack(
                side="left")
            ttk.Label(r4, text="to").pack(side="left", padx=2)
            ttk.Entry(r4, textvariable=self.viewer_range[b], width=8).pack(
                side="left")
            if unit:
                ttk.Label(r4, text=unit).pack(side="left", padx=(2, 0))
        b = ttk.Button(r4, text="Auto range", command=self._viewer_auto_range)
        b.pack(side="left", padx=6)
        Tooltip(b, "Forget the zoom: show the full delay range and all curves")
        ttk.Label(r4, text="Offset").pack(side="left", padx=(10, 2))
        e = ttk.Entry(r4, textvariable=self.viewer_offset, width=6)
        e.pack(side="left")
        Tooltip(e, "Shift the shown thicknesses apart (plotted units): the n-th "
                   "shown curve is moved up by n × offset. Used when other "
                   "curves are shown (not in 'Current curve only'). 0 = none.")
        for v in list(self.viewer_range.values()) + [self.viewer_offset]:
            v.trace_add("write", lambda *_: self._viewer_range_typed())
        sf = ttk.Frame(vp, padding=(6, 2, 6, 4))
        sf.pack(side="bottom", fill="x", before=vp.canvas.get_tk_widget())
        self.viewer_scale = ttk.Scale(sf, from_=0, to=1, orient="horizontal",
                                      command=self._on_viewer_slide)
        self.viewer_scale.pack(side="left", fill="x", expand=True)
        self.viewer_pos_lbl = ttk.Label(sf, width=26, anchor="e")
        self.viewer_pos_lbl.pack(side="left", padx=6)
        ttk.Label(vp, foreground=INK2, padding=(6, 0, 6, 2), text=(
            "Drag on the thickness colour bar (or the slider) to "
            "step through the sweep; right-click a colour to hide/show that "
            "thickness; ← → keys step one thickness.")).pack(
            side="bottom", fill="x", before=sf)
        self.bind_all("<Left>", lambda e: self._viewer_key(e, -1), add="+")
        self.bind_all("<Right>", lambda e: self._viewer_key(e, 1), add="+")

    def _viewer_batch(self):
        b = self._batch_of_selected()
        if b is not None and b["kind"] == "sweep" and len(b["runs"]) > 1:
            return b
        for b in reversed(self.batches):
            if b["kind"] == "sweep" and len(b["runs"]) > 1:
                return b
        return None

    @staticmethod
    def _visible(batch):
        hidden = batch.setdefault("hidden", set())
        return [i for i in range(len(batch["runs"])) if i not in hidden]

    def _viewer_uses_bg(self, batch):
        """Background-subtracted data by default, when the sweep has it."""
        return (self.viewer_signal.get() == self.VIEWER_SIGNALS[0]
                and any(r["res"].get("bg") for r in batch["runs"]))

    def _draw_viewer_frame(self, fig, batch, cur):
        """Draw one frame (screen and GIF). Returns the colour bars/axes."""
        runs = batch["runs"]
        n = len(runs)
        vis = self._visible(batch)
        exp = int(self.scale_exp.get())
        k = 10 ** exp
        use_bg = self._viewer_uses_bg(batch)
        sig = (lambda r: r["res"]["bg"]["sub"] if r["res"].get("bg") else None) \
            if use_bg else (lambda r: r["res"]["drr"])
        cols = self._run_colors(runs, True)
        unit = runs[0].get("sweep_unit", "")
        t = runs[0]["res"]["t_ps"]
        ylab = drr_label(exp) + (" − background" if use_bg else "")
        note = ""
        if (self.viewer_signal.get() == self.VIEWER_SIGNALS[0] and not use_bg):
            note = "  (no background fit in this sweep: original ΔR/R shown)"
        head = (f"{self.sweep_title(batch)}\nshowing "
                f"{runs[cur]['sweep_value']:g} {unit}  "
                f"({vis.index(cur) + 1} of {len(vis)})" + note)
        ax = fig.add_subplot()
        mode = self.viewer_mode.get()
        off = (self._offset_value(self.viewer_offset)
               if mode != self.VIEWER_MODES[0] else 0.0)
        shift = {i: j * off for j, i in enumerate(vis)}
        curves = {i: (None if sig(runs[i]) is None else sig(runs[i]) * k + shift[i])
                  for i in vis}
        k = 1                                 # curves are scaled already
        # y fits the shown curves inside the chosen delay range
        x0, x1 = self._viewer_lims("x0", "x1")
        win = ((t >= (t[0] if x0 is None else x0))
               & (t <= (t[-1] if x1 is None else x1)))
        if win.sum() < 2:
            win = np.ones_like(t, bool)
        allv = [c[win] for c in curves.values()
                if c is not None and np.isfinite(c[win]).any()]
        if allv:
            lo = float(np.nanmin([np.nanmin(c) for c in allv]))
            hi = float(np.nanmax([np.nanmax(c) for c in allv]))
            pad = 0.05 * (hi - lo or abs(hi) or 1.0)
            ax.set_ylim(lo - pad, hi + pad)       # fixed: the curve moves, not the axes
        if off:
            ylab += f"  (curves offset by {off:g})"
        if mode != self.VIEWER_MODES[0]:
            for i in vis:
                if i == cur or curves[i] is None:
                    continue
                if mode == self.VIEWER_MODES[1]:
                    ax.plot(t, curves[i] * k, color="#c8c8c8", lw=0.8)
                else:
                    ax.plot(t, curves[i] * k, color=cols[i], lw=1.0, alpha=.85)
        if curves.get(cur) is not None:
            ax.plot(t, curves[cur] * k, color=cols[cur],
                    lw=2.2 if mode != self.VIEWER_MODES[0] else 1.6)
        else:
            ax.text(0.5, 0.5, "no background fit for this thickness",
                    transform=ax.transAxes, ha="center", color=LIT_C)
        ax.axhline(0, color=GRID_C, lw=.8, zorder=0, gid="ref")
        ax.set_xlim(t[0], t[-1])
        ax.set_xlabel("delay (ps)")
        ax.set_ylabel(ylab)
        ax.set_title(head)
        ax.grid(alpha=.6)
        cb = self._add_sweep_cbar(fig, ax, batch, cols, cur=cur)
        auto = (ax.get_xlim(), ax.get_ylim())
        self._apply_viewer_range(ax, y=True)
        return dict(cb=cb, ax=ax, auto=auto)

    def draw_viewer(self):
        self._viewer_job = None
        p = self.viewer_panel
        batch = self._viewer_batch()
        if batch is None:
            p.empty("Run a thickness sweep: its curves can then be stepped "
                    "through here one at a time, like a video.")
            self._viewer_scale_update([], 0)
            return
        if id(batch) != self._viewer_bid:
            self._viewer_bid, self.viewer_cur = id(batch), 0
        vis = self._visible(batch)
        if self.viewer_cur not in vis:
            self.viewer_cur = min(vis, key=lambda i: abs(i - self.viewer_cur))
        # a y range belongs to one signal and scale: forget it when they change
        ykey = (id(batch), self.viewer_signal.get(), self.scale_exp.get())
        if getattr(self, "_viewer_ykey", ykey) != ykey:
            self._set_viewer_range(y=None)
        self._viewer_ykey = ykey
        fig = p.clear()
        d = self._draw_viewer_frame(fig, batch, self.viewer_cur)
        self._register_sweep_cbar(p, d["cb"], batch)
        p.finish()
        ax = d["ax"]
        p._viewer_auto = d["auto"]
        shown = (ax.get_xlim(), ax.get_ylim())
        if shown != d["auto"]:
            # toolbar Home / Back go to the full view, not to the zoom
            ax.set_xlim(d["auto"][0]); ax.set_ylim(d["auto"][1])
            p.toolbar.push_current()
            ax.set_xlim(shown[0]); ax.set_ylim(shown[1])
            p.toolbar.push_current()
        ax.callbacks.connect("xlim_changed", self._viewer_zoomed)
        ax.callbacks.connect("ylim_changed", self._viewer_zoomed)
        self._viewer_scale_update(vis, self.viewer_cur, batch)

    # --- zoom range of the viewer: the boxes follow the zoom (scroll, zoom
    # tool, pan, Home) and are applied to every frame, so stepping or
    # playing keeps the zoom
    def _viewer_lims(self, a, b):
        out = []
        for key in (a, b):
            try:
                v = float(self.viewer_range[key].get())
                out.append(v if np.isfinite(v) else None)
            except ValueError:
                out.append(None)
        return out

    def _apply_viewer_range(self, ax, y=True):
        for axis, (a, b) in (("x", ("x0", "x1")), ("y", ("y0", "y1"))):
            if axis == "y" and not y:
                continue
            lo, hi = self._viewer_lims(a, b)
            cur = ax.get_xlim() if axis == "x" else ax.get_ylim()
            lo = cur[0] if lo is None else lo
            hi = cur[1] if hi is None else hi
            if hi > lo:
                (ax.set_xlim if axis == "x" else ax.set_ylim)(lo, hi)

    def _set_viewer_range(self, x="keep", y="keep"):
        self._viewer_lock = True
        try:
            for val, keys in ((x, ("x0", "x1")), (y, ("y0", "y1"))):
                if isinstance(val, str):
                    continue
                for key, v in zip(keys, val or (None, None)):
                    self.viewer_range[key].set("" if v is None else f"{v:.5g}")
        finally:
            self._viewer_lock = False

    def _viewer_zoomed(self, ax):
        if not self._viewer_lock and self._viewer_range_job is None:
            self._viewer_range_job = self.after_idle(self._viewer_sync_boxes)

    def _viewer_sync_boxes(self):
        self._viewer_range_job = None
        p = self.viewer_panel
        axes = p.data_axes()
        info = getattr(p, "_viewer_auto", None)
        if not axes or info is None:
            return
        auto = info
        ax = axes[0]
        same = lambda a, b: np.allclose(a, b, rtol=1e-6, atol=1e-12)
        xl, yl = ax.get_xlim(), ax.get_ylim()
        self._set_viewer_range(x=None if same(xl, auto[0]) else xl,
                               y=None if same(yl, auto[1]) else yl)

    def _viewer_range_typed(self):
        if self._viewer_lock:
            return
        if getattr(self, "_viewer_type_job", None):
            self.after_cancel(self._viewer_type_job)
        self._viewer_type_job = self.after(500, self._viewer_typed_redraw)

    def _viewer_typed_redraw(self):
        self._viewer_type_job = None
        self.draw_viewer()

    def _viewer_auto_range(self):
        self._set_viewer_range(x=None, y=None)
        self.draw_viewer()

    def _viewer_scale_update(self, vis, cur, batch=None):
        self._viewer_setting = True
        try:
            self.viewer_scale.configure(to=max(1, len(vis) - 1))
            self.viewer_scale.set(vis.index(cur) if cur in vis else 0)
        finally:
            self._viewer_setting = False
        if batch is None or not vis:
            self.viewer_pos_lbl.config(text="")
            return
        r = batch["runs"][cur]
        self.viewer_pos_lbl.config(text=f"{r['sweep_value']:g} {r['sweep_unit']}"
                                        f"  ({vis.index(cur) + 1} of {len(vis)})")

    def _schedule_viewer_draw(self):
        if self._viewer_job is None:
            self._viewer_job = self.after_idle(self.draw_viewer)

    def _viewer_pick(self, i):
        batch = self._viewer_batch()
        if batch is None:
            return
        vis = self._visible(batch)
        i = min(max(i, 0), len(batch["runs"]) - 1)
        if i not in vis:
            i = min(vis, key=lambda j: abs(j - i))
        if i != self.viewer_cur:
            self.viewer_cur = i
            self._schedule_viewer_draw()

    def _on_viewer_slide(self, v):
        if self._viewer_setting:
            return
        batch = self._viewer_batch()
        if batch is None:
            return
        vis = self._visible(batch)
        pos = min(max(int(round(float(v))), 0), len(vis) - 1)
        self._viewer_pick(vis[pos])

    def _viewer_key(self, e, step):
        w = self.focus_get()
        if isinstance(w, (ttk.Entry, ttk.Spinbox, ttk.Combobox, tk.Entry, tk.Text)):
            return
        if self.ptabs.select() != str(self.viewer_panel):
            return
        batch = self._viewer_batch()
        if batch is None:
            return
        vis = self._visible(batch)
        pos = vis.index(self.viewer_cur) if self.viewer_cur in vis else 0
        self._viewer_pick(vis[min(max(pos + step, 0), len(vis) - 1)])

    def _viewer_show_all(self):
        b = self._viewer_batch()
        if b is not None:
            b["hidden"] = set()
            self._redraw_sweep_plots()

    def toggle_play(self):
        if self._play_job is not None:
            self._stop_play()
            return
        if self._viewer_batch() is None:
            return
        self.play_btn.config(text="⏸ Pause")
        self._play_job = self.after(10, self._play_step)

    def _stop_play(self):
        if getattr(self, "_play_job", None) is not None:
            self.after_cancel(self._play_job)
        self._play_job = None
        if hasattr(self, "play_btn"):
            self.play_btn.config(text="▶ Play")

    def _play_step(self):
        batch = self._viewer_batch()
        if batch is None:
            return self._stop_play()
        vis = self._visible(batch)
        pos = vis.index(self.viewer_cur) if self.viewer_cur in vis else -1
        if pos + 1 >= len(vis):
            if not self.viewer_loop.get():
                return self._stop_play()
            pos = -1
        self.viewer_cur = vis[pos + 1]
        self.draw_viewer()
        # start the frame timer only once the canvas has really been drawn
        # (draw_idle runs first among the idle tasks), so frames never pile
        # up and the window always stays responsive
        self._play_job = self.after_idle(self._play_schedule)

    def _play_schedule(self):
        try:
            fps = min(max(float(self.viewer_fps.get()), 0.2), 30.0)
        except ValueError:
            fps = 4.0
        self._play_job = self.after(max(int(1000 / fps), 30), self._play_step)

    def save_gif(self):
        """The shown thicknesses as an animated GIF (one frame each)."""
        batch = self._viewer_batch()
        if batch is None:
            messagebox.showinfo("Save GIF", "Run a thickness sweep first.")
            return
        from matplotlib.animation import PillowWriter
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        r = batch["runs"]
        unit = S.FILE_UNIT_NAMES.get(r[0]["sweep_unit"], r[0]["sweep_unit"])
        name = S.format_name(f"sweep_{batch.get('sweep_name', 'layer')}_"
                             f"{r[0]['sweep_value']:g}-{r[-1]['sweep_value']:g}"
                             f"{unit}")
        path = filedialog.asksaveasfilename(
            title="Save the sweep as an animated GIF", defaultextension=".gif",
            initialdir=self.out["folder"].get() or None,
            initialfile=name + ".gif", filetypes=[("GIF animation", "*.gif")])
        if not path:
            return
        try:
            fps = min(max(float(self.viewer_fps.get()), 0.2), 30.0)
        except ValueError:
            fps = 4.0
        w, h = self.viewer_panel.fig.get_size_inches()
        fig = Figure(figsize=(w, h), dpi=100, layout="constrained")
        FigureCanvasAgg(fig)
        vis = self._visible(batch)
        t0 = time.perf_counter()
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            writer = PillowWriter(fps=fps)
            with writer.saving(fig, path, dpi=100):
                for i in vis:
                    fig.clear()
                    self._draw_viewer_frame(fig, batch, i)
                    writer.grab_frame()
        except Exception as e:
            messagebox.showerror("Save GIF", f"{type(e).__name__}: {e}")
            return
        finally:
            self.config(cursor="")
        self.log(f"written: {path} ({len(vis)} frames at {fps:g} frames/s, "
                 f"{fmt_duration(time.perf_counter() - t0)})")

    def _batch_of_selected(self):
        lab = self.view_run.get()
        for b in self.batches:
            if any(r["label"] == lab for r in b["runs"]):
                return b
        return None

    def draw_background(self):
        p = self.bg_panel
        res = self.selected_res()
        if res is None:
            p.empty("Run the model to see the background fit.")
            return
        batch = self._batch_of_selected()
        if batch is not None and batch["kind"] == "sweep" and len(batch["runs"]) > 1:
            return self._draw_background_sweep(batch)
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
        outside_legend(ax)
        p.finish()

    def _draw_background_sweep(self, batch):
        """All thicknesses of a finished sweep: data with the fitted
        background (top) and ΔR/R − background (bottom), same colours."""
        p = self.bg_panel
        runs = batch["runs"]
        fitted = [r for r in runs if r["res"].get("bg")]
        if not fitted:
            p.empty("This sweep has no background fits. Tick 'Subtract the "
                    "background' and press 'Re-fit all runs', or run it again.")
            return
        exp = int(self.scale_exp.get())
        k = 10 ** exp
        off = self._offset_value(self.bg_offset)
        cols = self._run_colors(runs, True)
        hidden = batch.setdefault("hidden", set())
        fig = p.clear()
        ax1, ax2 = fig.subplots(2, 1, sharex=True)
        cut = None
        for n, (r, c) in enumerate(zip(runs, cols)):
            res, bg = r["res"], r["res"].get("bg")
            lab = r["label"].split(": ", 1)[-1]
            if not bg or n in hidden:
                continue
            sh = n * off
            ax1.plot(res["t_ps"], res["drr"] * k + sh, color=c, lw=1.1, label=lab)
            ax1.plot(res["t_ps"], bg["fit"] * k + sh, color=c, lw=1.0, ls="--")
            ax2.plot(res["t_ps"], bg["sub"] * k + sh, color=c, lw=1.1, label=lab)
            cut = bg["cut_ps"] if bg["cut_ps"] is not None else cut
        t = runs[0]["res"]["t_ps"]
        for ax in (ax1, ax2):
            if cut is not None:
                ax.axvline(cut, color=INK2, lw=.8, ls=":", gid="ref")
            ax.axhline(0, color=GRID_C, lw=.8, zorder=0, gid="ref")
            ax.set_xlim(t[0], t[-1])
            ax.grid(alpha=.6)
        shift = f"  (curves offset by {off:g})" if off else ""
        ax1.set_ylabel(drr_label(exp) + shift)
        ax2.set_ylabel(drr_label(exp) + " − bg" + shift)
        ax2.set_xlabel("delay (ps)")
        ax1.set_title(f"{self.sweep_title(batch)} — background fits\n"
                      f"solid: data, dashed: fitted background"
                      + (f", dotted line: fit starts at t = {cut:g} ps"
                         if cut is not None else ""), fontsize=9)
        ax2.set_title("ΔR/R − background")
        cb = self._add_sweep_cbar(fig, [ax1, ax2], batch, cols)
        self._register_sweep_cbar(p, cb, batch)
        missing = len(runs) - len(fitted)
        if missing:
            ax2.text(0.01, 0.02, f"{missing} run(s) without a fit (see the log)",
                     transform=ax2.transAxes, fontsize=8, color=LIT_C)
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
        self.schedule_stack()
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

    def _fingerprint(self, layers=None, skip_d=None, cfg=None, flags=None,
                     stack_only=False):
        """Identity of the inputs: config, switches and layers (literature
        bookkeeping left out; for a sweep, the swept layer's thickness left
        out). Equal fingerprints = same sample and experiment."""
        layers = self.layers if layers is None else layers
        skip = skip_d if isinstance(skip_d, (set, frozenset)) else {skip_d}
        lays = []
        for i, l in enumerate(layers):
            d = {k: v for k, v in l.items() if k != "lit"}
            if i in skip:
                d.pop("d", None)
                d.pop("d_unit", None)
            lays.append(d)
        if stack_only:
            return json.dumps(lays, sort_keys=True)
        return json.dumps(dict(cfg=cfg or self._cfg_entries(),
                               flags=flags or self._flags(), layers=lays),
                          sort_keys=True)

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
            jobs.append(dict(label=f"{tag}: {self.layer_title(idx)} = {v:g} {unit}",
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
                               info=f"{self.layer_title(idx)} thickness",
                               sweep_idx=idx, sweep_name=name,
                               sweep_vals=list(vals), sweep_unit=unit,
                               sweep_step=float(self.sweep_step.get())))

    def _clear_result_plots(self, batch):
        """A new run/sweep is starting: take the old results off the plots
        so they cannot be mistaken for the new ones."""
        what = ("the sweep" if batch.get("kind") == "sweep" else "the run")
        msg = (f"Running {what}…\nThe plots appear here when it has "
               f"finished (earlier runs stay under 'Show run').")
        self._stop_play()
        for panel in (self.drr_panel, self.comp_panel, self.kern_panel,
                      self.strain_panel, self.bg_panel, self.viewer_panel):
            panel.empty(msg)
        if hasattr(self, "bg_result"):
            self.bg_result.config(text="Running…")

    def _busy(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Busy", "A calculation is running. Wait or "
                                        "press Cancel.")
            return True
        return False

    def _start(self, jobs, batch):
        self.cancel_ev.clear()
        self.run_btn.config(state="disabled")
        self.sweep_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.pbar["value"] = 0
        opts = self._opts()
        st = self._bg_settings(opts)            # validated by the callers
        for job in jobs:
            job["bg_settings"] = st
        self._pending = dict(batch, runs=[], n_jobs=len(jobs), opts=opts,
                             t0=time.perf_counter(), sim_time=0.0,
                             bg_time=0.0, bg_done=0,
                             fingerprint=self._fingerprint(
                                 skip_d=batch.get("sweep_idx")),
                             stack_fp=self._fingerprint(
                                 skip_d=batch.get("sweep_idx"), stack_only=True))
        self._clear_result_plots(batch)
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
        self.sweep_btn.config(state="normal")
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
            self.draw_stack()            # marks the swept layer (saved figures too)
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
                               sweep_cmap=self.sweep_cmap.get(),
                               drr_offset=self.drr_offset.get(),
                               bg_offset=self.bg_offset.get(),
                               viewer_offset=self.viewer_offset.get(),
                               stack_colors=self.stack_colors,
                               scale_exp=self.scale_exp.get(),
                               overlay=self.overlay.get()),
                    sweep=dict(layer_index=self.sweep_index(),
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
            v.set(bool(st["flags"].get(k, S.default_flags()[k])))
        pl = st.get("plots", {})
        self.show_components.set(pl.get("components", False))
        self.show_kernels.set(pl.get("kernels", False))
        self.show_strain.set(pl.get("strain", False))
        self.stack_mode.set(pl.get("stack_mode", "Equal widths"))
        self.sweep_cmap.set(pl.get("sweep_cmap", "Blues"))
        self.drr_offset.set(pl.get("drr_offset", "0"))
        self.bg_offset.set(pl.get("bg_offset", "0"))
        self.viewer_offset.set(pl.get("viewer_offset", "0"))
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
        k = sw.get("layer_index")
        if k is None and isinstance(sw.get("layer"), str):   # older sessions
            try:
                k = int(sw["layer"].split(":")[0]) - 1
            except ValueError:
                k = None
        if isinstance(k, int) and 0 <= k < len(self.layers) - 1:
            self._sweep_obj = self.layers[k]
            self._refresh_sweep_layers()
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
                 "background", "sweep_viewer")


def FIGURE_PANELS_MAP(app):
    return {"dRR": app.drr_panel, "stack": app.stack_panel,
            "components": app.comp_panel, "kernels": app.kern_panel,
            "strain_map": app.strain_panel, "background": app.bg_panel,
            "sweep_viewer": app.viewer_panel}


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


def _dark(color):
    r, g, b = matplotlib.colors.to_rgb(color)
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
