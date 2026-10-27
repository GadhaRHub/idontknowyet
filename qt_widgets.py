"""Reusable widgets of the PiSES Qt interface.

- Section        collapsible block with an UPPERCASE title (the inspector
                 "boxes")
- ToggleSwitch   on/off switch (a QCheckBox, so bind_check works on it)
- Segmented      a row of mutually exclusive buttons bound to a StringVar
- NavRail        the navigation rail on the left
- MiniStack      the clickable coloured bar of the stack
- PlotPanel      a matplotlib figure with toolbar, scroll zoom and the
                 label / colour editors (same protocol as the Tk version:
                 clear() ... draw ... finish())
- dialogs        Axes & labels, plot colours, stack colours, help, code,
                 literature placeholders, background cut-off, import review
"""

import os

import numpy as np
import matplotlib
from matplotlib import colormaps
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import (FigureCanvasQTAgg,
                                               NavigationToolbar2QT)
from PySide6 import QtCore, QtGui, QtWidgets

import help_info as HI
import units as U
import qt_theme as T
from qt_compat import (StringVar, BooleanVar, bind_line, bind_combo,
                       bind_check, messagebox, colorchooser)

Qt = QtCore.Qt


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------

def amp(text):
    """Qt shows '&x' as an underlined shortcut letter; '&&' is a real '&'."""
    return text.replace("&", "&&")


def label(text="", name=None, wrap=False, bold=False):
    w = QtWidgets.QLabel(text)
    if name:
        w.setObjectName(name)
    if wrap:
        w.setWordWrap(True)
    if bold:
        f = w.font()
        f.setBold(True)
        w.setFont(f)
    return w


def set_bad(widget, bad):
    """Red text for a conversion / hint label (style sheet property)."""
    widget.setProperty("bad", "true" if bad else "false")
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def hbox(*widgets, spacing=6, margins=(0, 0, 0, 0), stretch=True):
    w = QtWidgets.QWidget()
    lay = QtWidgets.QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


def line_edit(var, width=None, placeholder=""):
    e = QtWidgets.QLineEdit()
    if width:
        e.setFixedWidth(width)
    if placeholder:
        e.setPlaceholderText(placeholder)
    return bind_line(e, var)


def combo(var, values, editable=False, width=None):
    c = QtWidgets.QComboBox()
    c.setEditable(editable)
    c.addItems([str(v) for v in values])
    c.setMaxVisibleItems(20)
    if width:
        c.setFixedWidth(width)
    return bind_combo(c, var)


def check(text, var):
    return bind_check(QtWidgets.QCheckBox(amp(text)), var)


def button(text, slot=None, name=None, tip=None):
    b = QtWidgets.QPushButton(amp(text))
    if name:
        b.setObjectName(name)
    if slot:
        b.clicked.connect(lambda *_: slot())
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class InfoButton(QtWidgets.QToolButton):
    """Small ⓘ: a click opens a floating Help / Code menu for one input."""

    def __init__(self, app, key):
        super().__init__()
        self.app, self.key = app, key
        self.setText("ⓘ")
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"QToolButton {{ border: none; color: {T.INK2}; "
                           f"font-size: 11pt; padding: 0 2px; }}"
                           f"QToolButton:hover {{ color: {T.BERRY}; }}")
        self.clicked.connect(self.popup)

    def popup(self):
        title = HI.HELP[self.key]["title"]
        m = QtWidgets.QMenu(self)
        m.addAction(f"Help — {title}", lambda: HelpWindow(self.app, self.key))
        m.addAction("Code — where it is used",
                    lambda: CodeWindow(self.app, self.key))
        m.popup(self.mapToGlobal(QtCore.QPoint(0, self.height())))


class Section(QtWidgets.QFrame):
    """A collapsible block: a title bar (click to fold) over a grid body.
    .grid is the QGridLayout to fill; .head_extra holds widgets put at the
    right of the title (e.g. a switch)."""

    def __init__(self, title, expanded=True):
        super().__init__()
        self.setObjectName("section")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(16, 0, 16, 8)
        v.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setObjectName("sechead")
        # "Optical constants  ñ = n + ik": only the part before two spaces is
        # upper-cased (Greek letters and units keep their case)
        first, _, rest = title.partition("  ")
        self.toggle.setText(amp(first.upper() + ("  " + rest if rest else "")))
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.toggle.clicked.connect(lambda: self.set_expanded(not self.expanded))
        head.addWidget(self.toggle)
        head.addStretch(1)
        self.head_extra = QtWidgets.QHBoxLayout()
        head.addLayout(self.head_extra)
        self.chev = QtWidgets.QLabel()
        self.chev.setStyleSheet(f"color: {T.INK2};")
        head.addWidget(self.chev)
        v.addLayout(head)
        self.body = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 0, 4)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(5)
        v.addWidget(self.body)
        self.title = title
        self.set_expanded(expanded)

    def set_expanded(self, on):
        self.expanded = on
        self.body.setVisible(on)
        self.chev.setText("▾" if on else "▸")

    def add_row(self, *widgets, row=None, span_last=True):
        """Put widgets in the next grid row (None leaves a cell empty)."""
        r = self.grid.rowCount() if row is None else row
        for c, w in enumerate(widgets):
            if w is None:
                continue
            last = c == len(widgets) - 1
            self.grid.addWidget(w, r, c, 1, (6 - c) if (last and span_last) else 1)
        return r

    def add_full(self, widget):
        r = self.grid.rowCount()
        self.grid.addWidget(widget, r, 0, 1, 6)
        return r


class ToggleSwitch(QtWidgets.QCheckBox):
    """An on/off switch drawn like a phone toggle (still a QCheckBox)."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._w, self._h = 36, 20

    def sizeHint(self):
        fm = self.fontMetrics()
        tw = fm.horizontalAdvance(self.text()) + 10 if self.text() else 0
        return QtCore.QSize(self._w + tw + 2, max(self._h + 2, fm.height() + 4))

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        y = (self.height() - self._h) / 2
        on = self.isChecked()
        col = QtGui.QColor(T.BERRY if on else "#d9cfd5")
        if not self.isEnabled():
            col = QtGui.QColor("#e7e1e5")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QtCore.QRectF(1, y, self._w, self._h), self._h / 2,
                          self._h / 2)
        d = self._h - 4
        x = 1 + (self._w - d - 2 if on else 2)
        p.setBrush(QtGui.QColor("white"))
        p.drawEllipse(QtCore.QRectF(x, y + 2, d, d))
        if self.text():
            p.setPen(QtGui.QColor(T.INK if self.isEnabled() else "#a39aa0"))
            r = self.rect().adjusted(self._w + 10, 0, 0, 0)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       self.text())
        p.end()


def switch(text, var):
    return bind_check(ToggleSwitch(text), var)


class Segmented(QtWidgets.QFrame):
    """Mutually exclusive buttons ("Typed | File") bound to a StringVar."""

    def __init__(self, var, options):
        """options: [(value, text), ...]"""
        super().__init__()
        self.setObjectName("seg")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.var, self.buttons = var, {}
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for val, text in options:
            b = QtWidgets.QPushButton(amp(text))
            b.setObjectName("segbtn")
            b.setCheckable(True)
            f = b.font()
            f.setBold(True)                 # room for the bold checked state
            b.setMinimumWidth(QtGui.QFontMetrics(f).horizontalAdvance(text) + 26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            group.addButton(b)
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, v=val: var.get() != v and var.set(v))
            self.buttons[val] = b
        var.trace_add("write", lambda *_: self._sync())
        self._sync()

    def _sync(self):
        b = self.buttons.get(self.var.get())
        if b is not None and not b.isChecked():
            b.setChecked(True)


# ---------------------------------------------------------------------------
# Navigation rail
# ---------------------------------------------------------------------------

class RailItem(QtWidgets.QWidget):
    clicked = QtCore.Signal()

    def __init__(self, icon_key, text):
        super().__init__()
        self.icon_key = icon_key
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)
        self.pill = QtWidgets.QLabel()
        self.pill.setFixedSize(56, 32)
        self.pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = QtWidgets.QLabel(text)
        self.text.setObjectName("raillabel")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(text)
        self.set_active(False)

    def set_active(self, on):
        col = T.BERRY if on else T.INK2
        self.pill.setPixmap(T.svg_icon(T.RAIL_ICONS[self.icon_key], 24, col)
                            .pixmap(24, 24))
        self.pill.setStyleSheet(f"background: {T.BERRY_LIGHT if on else 'transparent'};"
                                f"border-radius: 16px;")
        self.text.setProperty("active", "true" if on else "false")
        self.text.style().unpolish(self.text)
        self.text.style().polish(self.text)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class NavRail(QtWidgets.QFrame):
    """The rail: app icon on top, one item per page, Help at the bottom."""
    page_changed = QtCore.Signal(int)
    help_clicked = QtCore.Signal()

    def __init__(self, pages):
        """pages: [(icon_key, text), ...]"""
        super().__init__()
        self.setObjectName("rail")
        self.setFixedWidth(88)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 12, 4, 12)
        v.setSpacing(10)
        logo = QtWidgets.QLabel()
        logo.setPixmap(T.app_icon().pixmap(40, 40))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(logo)
        v.addSpacing(4)
        self.items = []
        for i, (key, text) in enumerate(pages):
            it = RailItem(key, text)
            it.clicked.connect(lambda i=i: self.select(i))
            v.addWidget(it)
            self.items.append(it)
        v.addStretch(1)
        self.help = RailItem("help", "Help")
        self.help.clicked.connect(self.help_clicked.emit)
        v.addWidget(self.help)
        self.current = -1

    def select(self, i):
        if i == self.current:
            return
        self.current = i
        for k, it in enumerate(self.items):
            it.set_active(k == i)
        self.page_changed.emit(i)


# ---------------------------------------------------------------------------
# Mini stack bar
# ---------------------------------------------------------------------------

class MiniStack(QtWidgets.QWidget):
    """Coloured bar of the stack (equal widths, substrate wider); click a
    block to select that layer."""
    picked = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(30)
        self.blocks, self.sel = [], None      # [(name, colour)]
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_blocks(self, blocks, sel):
        self.blocks, self.sel = blocks, sel
        self.update()

    def _rects(self):
        n = len(self.blocks)
        if not n:
            return []
        ws = [1.0] * (n - 1) + [1.4]
        tot, x, out = sum(ws), 0.0, []
        W = self.width()
        for w in ws:
            out.append(QtCore.QRectF(x * W / tot, 0, w * W / tot, self.height()))
            x += w
        return out

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        path = QtGui.QPainterPath()
        path.addRoundedRect(QtCore.QRectF(self.rect()), 7, 7)
        p.setClipPath(path)
        for i, (r, (name, col)) in enumerate(zip(self._rects(), self.blocks)):
            c = QtGui.QColor(matplotlib.colors.to_hex(col))
            p.fillRect(r, c)
            dark = (0.2126 * c.redF() + 0.7152 * c.greenF()
                    + 0.0722 * c.blueF()) < 0.5
            p.setPen(QtGui.QColor("white" if dark else "#222222"))
            f = p.font()
            f.setPointSizeF(7.5)
            f.setBold(i == self.sel)
            p.setFont(f)
            txt = name + (" ∞" if i == len(self.blocks) - 1 else "")
            p.drawText(r, Qt.AlignmentFlag.AlignCenter,
                       QtGui.QFontMetrics(f).elidedText(
                           txt, Qt.TextElideMode.ElideRight, int(r.width()) - 4))
            if i == self.sel:
                p.setPen(QtGui.QPen(QtGui.QColor(T.INK), 2.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r.adjusted(1, 1, -1, -1))
        p.end()

    def mousePressEvent(self, e):
        for i, r in enumerate(self._rects()):
            if r.contains(e.position()):
                self.picked.emit(i)
                return


# ---------------------------------------------------------------------------
# Plot panel
# ---------------------------------------------------------------------------

def outside_legend(ax, fontsize=8):
    """Legend outside the axes, to the right, one column: it never covers
    the curves (the layout makes room for it)."""
    leg = ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0),
                    borderaxespad=0.0, ncols=1, fontsize=fontsize)
    leg.set_draggable(True)
    ax._diffr_outside_legend = True
    return leg


def _all_cmaps(first):
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
    if getattr(cm, "N", 256) <= 20:
        return [cm(i % cm.N) for i in range(n)]
    if n == 1:
        return [cm(0.65)]
    return [cm(0.25 + 0.7 * i / (n - 1)) for i in range(n)]


TOOL_ICONS = {
    "Home": '<path d="M4 11l8-7 8 7v9h-5v-6h-6v6H4z" fill="none" stroke="{c}" '
            'stroke-width="1.8" stroke-linejoin="round"/>',
    "Back": '<path d="M15 5l-7 7 7 7" fill="none" stroke="{c}" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round"/>',
    "Forward": '<path d="M9 5l7 7-7 7" fill="none" stroke="{c}" stroke-width="2" '
               'stroke-linecap="round" stroke-linejoin="round"/>',
    "Pan": '<path d="M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3'
           'M18 9l3 3-3 3" fill="none" stroke="{c}" stroke-width="1.8" '
           'stroke-linecap="round" stroke-linejoin="round"/>',
    "Zoom": '<circle cx="10" cy="10" r="6" fill="none" stroke="{c}" '
            'stroke-width="1.8"/><path d="M14.5 14.5L20 20M7.5 10h5M10 7.5v5" '
            'stroke="{c}" stroke-width="1.8" stroke-linecap="round"/>',
    "Subplots": '<path d="M4 7h16M4 12h16M4 17h16" stroke="{c}" stroke-width="1.6"/>'
                '<circle cx="9" cy="7" r="2" fill="white" stroke="{c}" stroke-width="1.6"/>'
                '<circle cx="15" cy="12" r="2" fill="white" stroke="{c}" stroke-width="1.6"/>'
                '<circle cx="7" cy="17" r="2" fill="white" stroke="{c}" stroke-width="1.6"/>',
    "Save": '<path d="M5 4h11l3 3v13H5z M8 4v5h7V4 M8 20v-6h8v6" fill="none" '
            'stroke="{c}" stroke-width="1.7" stroke-linejoin="round"/>',
}


class Toolbar(NavigationToolbar2QT):
    """matplotlib toolbar without the 'edit axes' (we have our own editor),
    drawn with the app's own line icons."""
    toolitems = [t for t in NavigationToolbar2QT.toolitems
                 if t[0] in ("Home", "Back", "Forward", None, "Pan", "Zoom",
                             "Subplots", "Save")]

    def __init__(self, canvas, parent):
        super().__init__(canvas, parent)
        for a in self.actions():
            if a.text() in TOOL_ICONS:
                a.setIcon(T.svg_icon(TOOL_ICONS[a.text()], 20, T.INK2))


class PlotPanel(QtWidgets.QWidget):
    """A matplotlib figure with the standard toolbar (zoom, pan, home, save),
    scroll-wheel zoom, and an editor for titles, labels, limits and scales.

    Label/scale edits and colour choices are remembered and re-applied each
    time the panel is redrawn, until they are reset. Drawing protocol:
    fig = panel.clear(); ... draw ...; panel.finish().
    """

    def __init__(self, app, figsize=(7, 4), colors=True):
        super().__init__()
        self.app = app
        self.fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        self.fig.patch.set_facecolor("white")
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setMinimumHeight(120)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(2)
        bar = QtWidgets.QHBoxLayout()
        self.toolbar = Toolbar(self.canvas, self)
        self.toolbar.setIconSize(QtCore.QSize(18, 18))
        self.toolbar.setStyleSheet("QToolBar { border: none; background: transparent; }")
        bar.addWidget(self.toolbar, 1)
        self.controls = QtWidgets.QHBoxLayout()
        bar.addLayout(self.controls)
        b = button("Axes & labels…", self.edit_axes, "small",
                   "Edit title, axis labels, limits, log/linear scale, grid, "
                   "legend and font size")
        bar.addWidget(b)
        if colors:
            bar.addWidget(button("Colours…", self.edit_colors, "small",
                                 "Change the colour of each curve, colour all "
                                 "curves from a colour map, or change the "
                                 "colour map of an image plot"))
        bar.addWidget(button("Reset labels", self.reset_overrides, "small"))
        v.addLayout(bar)
        # extra rows for plot-specific settings; empty rows take no space
        self.row2 = QtWidgets.QHBoxLayout()
        self.row2.setContentsMargins(4, 0, 4, 0)
        v.addLayout(self.row2)
        self.top_rows = QtWidgets.QVBoxLayout()
        v.addLayout(self.top_rows)
        v.addWidget(self.canvas, 1)
        self.bottom_rows = QtWidgets.QVBoxLayout()
        v.addLayout(self.bottom_rows)
        self.overrides, self.styles, self.colorbars = {}, {}, []
        self._sweep = None
        self._orig_savefig = self.fig.savefig
        self.fig.savefig = self._savefig_keep_plot_size
        self.canvas.mpl_connect("scroll_event", self._on_scroll)

    # --- saving keeps the plot area the size it has on screen
    def _side_inches(self):
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

    # --- drawing protocol
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
        ax.text(0.5, 0.5, msg, ha="center", va="center", color=T.PLOT_INK2,
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
        for i, ax in enumerate(self.data_axes()):
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


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

class Dialog(QtWidgets.QDialog):
    """Non-modal dialog that deletes itself when closed."""

    def __init__(self, parent, title, w=None, h=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if w and h:
            self.resize(w, h)
        self.lay = QtWidgets.QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)

    def buttons(self, *specs, left=()):
        """specs: (text, slot) on the right; left: the same on the left."""
        row = QtWidgets.QHBoxLayout()
        for text, slot in left:
            row.addWidget(button(text, slot))
        row.addStretch(1)
        for text, slot in specs:
            row.addWidget(button(text, slot))
        self.lay.addLayout(row)


def swatch(color, slot):
    b = QtWidgets.QPushButton()
    b.setFixedSize(48, 20)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"QPushButton {{ background: {matplotlib.colors.to_hex(color)};"
                    f" border: 1px solid #888; border-radius: 4px; }}")
    b.clicked.connect(lambda *_: slot())
    return b


class PlotColorDialog(Dialog):
    """Colours of the curves (and image colour map) of one plot panel. For a
    sweep plot it lists the thicknesses: their colours are shared by every
    sweep plot."""

    def __init__(self, panel):
        super().__init__(panel, "Plot colours", 460, 420)
        self.panel = panel
        axes = panel.data_axes()
        self.names = [f"{i + 1}: {a.get_title() or '(untitled)'}"
                      for i, a in enumerate(axes)]
        self.which = QtWidgets.QComboBox()
        self.which.addItems(self.names)
        if len(axes) > 1:
            self.lay.addWidget(hbox(QtWidgets.QLabel("Plot"), self.which))
        self.which.currentIndexChanged.connect(lambda *_: self.build())
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.lay.addWidget(self.scroll, 1)
        self.buttons(("Close", self.close),
                     left=(("Reset colours of this plot", self.reset),))
        self.build()
        self.show()

    def idx(self):
        return max(0, self.which.currentIndex())

    def style_(self):
        return self.panel.styles.setdefault(self.idx(), {"lines": {}})

    def _sweep_batch(self):
        sw = self.panel._sweep
        if sw is None or self.idx() != 0:
            return None
        return sw.get("batch")

    def build(self):
        body = QtWidgets.QWidget()
        g = QtWidgets.QGridLayout(body)
        g.setVerticalSpacing(4)
        self.scroll.setWidget(body)
        axes = self.panel.data_axes()
        if self.idx() >= len(axes):
            return
        ax = axes[self.idx()]
        lines = PlotPanel.data_lines(ax)
        app = self.panel.app
        batch = self._sweep_batch()
        r = 0
        if batch is not None:
            lines = []
            runs = batch["runs"]
            cols = app._run_colors(runs, True)
            hidden = batch.get("hidden", set())
            g.addWidget(label("Thicknesses", bold=True), r, 0)
            r += 1
            for i, run in enumerate(runs):
                txt = f"{run['sweep_value']:g} {run.get('sweep_unit', '')}"
                g.addWidget(QtWidgets.QLabel(txt + ("  (hidden)" if i in hidden
                                                    else "")), r, 0)
                pick = (lambda rn=run, c=cols[i], t=txt: self.pick_run(rn, c, t))
                g.addWidget(swatch(cols[i], pick), r, 1)
                g.addWidget(button("Change…", pick, "small"), r, 2)
                r += 1
            self.line_cmap = StringVar(app.sweep_cmap.get())
            g.addWidget(hbox(QtWidgets.QLabel("Colour all thicknesses from"),
                             combo(self.line_cmap, LINE_CMAPS, editable=True, width=120),
                             button("Apply", lambda: self._after_app(
                                 app.set_sweep_cmap, self.line_cmap.get()), "small")),
                        r, 0, 1, 3)
            r += 1
            g.addWidget(label("These colours are shared by the ΔR/R, Background "
                              "and Sweep viewer plots and their thickness colour "
                              "bars.", "note", wrap=True), r, 0, 1, 3)
            r += 1
        if lines:
            g.addWidget(label("Curves", bold=True), r, 0)
            r += 1
            for j, l in enumerate(lines):
                key = PlotPanel.line_key(l, j)
                g.addWidget(QtWidgets.QLabel(key), r, 0)
                pick = (lambda k=key, ln=l: self.pick(k, ln))
                g.addWidget(swatch(l.get_color(), pick), r, 1)
                g.addWidget(button("Change…", pick, "small"), r, 2)
                r += 1
            self.line_cmap = StringVar(self.style_().get("line_cmap") or LINE_CMAPS[0])
            g.addWidget(hbox(QtWidgets.QLabel("Colour all curves from"),
                             combo(self.line_cmap, LINE_CMAPS, editable=True, width=120),
                             button("Apply", self.apply_line_cmap, "small")),
                        r, 0, 1, 3)
            r += 1
        if ax.images:
            self.img_cmap = StringVar(self.style_().get("image_cmap") or
                                      ax.images[0].get_cmap().name)
            g.addWidget(hbox(QtWidgets.QLabel("Image colour map"),
                             combo(self.img_cmap, IMAGE_CMAPS, editable=True, width=120),
                             button("Apply", self.apply_img_cmap, "small")),
                        r, 0, 1, 3)
            r += 1
            g.addWidget(label("Any matplotlib colour map name can be typed; add "
                              "_r to reverse it.", "note"), r, 0, 1, 3)
            r += 1
        if not lines and not ax.images and batch is None:
            g.addWidget(QtWidgets.QLabel("This plot has no curves."), 0, 0)
        g.setRowStretch(r, 1)

    def _after_app(self, fn, *a):
        fn(*a)
        self.build()

    def pick_run(self, run, color, txt):
        _, hexcol = colorchooser.askcolor(color=matplotlib.colors.to_hex(color),
                                          parent=self, title=f"Colour for {txt}")
        if hexcol:
            self._after_app(self.panel.app.set_sweep_run_color, run, hexcol)

    def _redraw(self):
        self.panel.apply_styles()
        self.panel.canvas.draw_idle()
        self.build()

    def pick(self, key, line):
        _, hexcol = colorchooser.askcolor(
            color=matplotlib.colors.to_hex(line.get_color()), parent=self,
            title=f"Colour for {key}")
        if hexcol:
            self.style_().setdefault("lines", {})[key] = hexcol
            self._redraw()

    def apply_line_cmap(self):
        name = self.line_cmap.get().strip()
        if name not in colormaps:
            messagebox.showerror("Colours", f"Unknown colour map {name!r}.",
                                 parent=self)
            return
        st = self.style_()
        st["line_cmap"], st["lines"] = name, {}
        self._redraw()

    def apply_img_cmap(self):
        name = self.img_cmap.get().strip()
        if name not in colormaps:
            messagebox.showerror("Colours", f"Unknown colour map {name!r}.",
                                 parent=self)
            return
        self.style_()["image_cmap"] = name
        self._redraw()

    def reset(self):
        self.panel.styles.pop(self.idx(), None)
        batch = self._sweep_batch()
        if batch is not None:
            for run in batch["runs"]:
                run.pop("color", None)
            self._after_app(self.panel.app._redraw_sweep_plots)
            return
        if hasattr(self.panel, "redraw"):
            self.panel.redraw()
        self.build()


class AxesDialog(Dialog):
    """Edit title, labels, limits, scales, grid, legend, font size."""

    def __init__(self, panel, axes):
        super().__init__(panel, "Axes & labels")
        self.panel, self.axes = panel, axes
        f = QtWidgets.QGridLayout()
        self.lay.addLayout(f)
        r = 0
        self.which = QtWidgets.QComboBox()
        self.which.addItems([f"{i + 1}: {a.get_title() or '(untitled)'}"
                             for i, a in enumerate(axes)])
        if len(axes) > 1:
            f.addWidget(QtWidgets.QLabel("Plot"), r, 0)
            f.addWidget(self.which, r, 1, 1, 3)
            r += 1
        self.which.currentIndexChanged.connect(lambda *_: self.load())
        self.v = {k: StringVar() for k in
                  ("title", "xlabel", "ylabel", "xmin", "xmax", "ymin", "ymax",
                   "xscale", "yscale", "fontsize")}
        self.b = {k: BooleanVar() for k in ("grid", "legend", "keep_limits")}
        for key, lab in (("title", "Title"), ("xlabel", "X label"),
                         ("ylabel", "Y label")):
            f.addWidget(QtWidgets.QLabel(lab), r, 0)
            e = line_edit(self.v[key])
            e.setMinimumWidth(360)
            f.addWidget(e, r, 1, 1, 3)
            r += 1
        for ax_, lab in (("x", "X range"), ("y", "Y range")):
            f.addWidget(QtWidgets.QLabel(lab), r, 0)
            f.addWidget(line_edit(self.v[ax_ + "min"], 110), r, 1)
            f.addWidget(line_edit(self.v[ax_ + "max"], 110), r, 2)
            f.addWidget(combo(self.v[ax_ + "scale"], ["linear", "log", "symlog"]),
                        r, 3)
            r += 1
        f.addWidget(QtWidgets.QLabel("Font size"), r, 0)
        f.addWidget(line_edit(self.v["fontsize"], 60), r, 1)
        r += 1
        f.addWidget(check("Grid", self.b["grid"]), r, 1)
        f.addWidget(check("Legend", self.b["legend"]), r, 2)
        r += 1
        f.addWidget(check("Keep these limits when the plot is redrawn",
                          self.b["keep_limits"]), r, 1, 1, 3)
        self.buttons(("Apply", self.apply), ("OK", lambda: (self.apply(),
                                                            self.close())),
                     ("Close", self.close))
        self.load()
        self.show()

    def idx(self):
        return max(0, self.which.currentIndex())

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


class PlaceholderDialog(Dialog):
    """Table of every value that is still a literature pre-fill."""

    def __init__(self, parent, rows, title, note=""):
        super().__init__(parent, title, 780, 400)
        layers = sorted({r[0] for r in rows})
        head = (f"{len(rows)} literature placeholder value(s) in "
                f"{len(layers)} layer(s). These are typical values from the "
                f"LITERATURE table, not measurements of your sample."
                if rows else "No literature placeholders: every value was "
                             "typed, read from a file or edited.")
        self.lay.addWidget(label(head + ("\n" + note if note else ""), wrap=True))
        tv = QtWidgets.QTreeWidget()
        tv.setHeaderLabels(["Layer", "Quantity", "Value", "Unit"])
        tv.setRootIsDecorated(False)
        for n, mat, q, val, unit in rows:
            QtWidgets.QTreeWidgetItem(tv, [f"{n}: {mat}", q, str(val), unit])
        for c, w in enumerate((140, 300, 140, 150)):
            tv.setColumnWidth(c, w)
        self.lay.addWidget(tv, 1)
        self.text = "\n".join(f"{n}: {mat}\t{q}\t{val}\t{unit}"
                              for n, mat, q, val, unit in rows)
        self.lay.addWidget(label("Edit a value in the Layers page to replace a "
                                 "placeholder.", "note"))
        self.buttons(("Copy to clipboard", self.copy), ("Close", self.close))
        self.show()

    def copy(self):
        QtWidgets.QApplication.clipboard().setText(
            "layer\tquantity\tvalue\tunit\n" + self.text)


class StackColorDialog(Dialog):
    """Pick the colour of each material in the stack diagram."""

    def __init__(self, app):
        super().__init__(app, "Stack colours")
        self.app = app
        self.body = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.body)
        self.lay.addWidget(self.body)
        self.buttons(("Close", self.close),
                     left=(("Reset all to default", self.reset_all),))
        self.build()
        self.show()

    def build(self):
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        names = list(dict.fromkeys(l["material"].strip()
                                   for l in self.app.layers[:-1]))
        sub = self.app.layers[-1]["material"].strip() if self.app.layers else ""
        self.grid.addWidget(label("Colours are per material: layers of the same "
                                  "material share one. The substrate has its "
                                  "own.", "note", wrap=True), 0, 0, 1, 4)
        key_sub = self.app.SUBSTRATE_KEY
        for r, name in enumerate(names + [key_sub], 1):
            is_sub = name == key_sub
            col = matplotlib.colors.to_hex(
                self.app.material_color(name, names, substrate=is_sub))
            self.grid.addWidget(QtWidgets.QLabel(
                f"Substrate ({sub or 'last layer'})" if is_sub
                else (name or "(unnamed)")), r, 0)
            self.grid.addWidget(swatch(col, lambda n=name: self.pick(n)), r, 1)
            self.grid.addWidget(button("Change…", lambda n=name: self.pick(n),
                                       "small"), r, 2)
            b = button("Default", lambda n=name: self.reset(n), "small")
            b.setEnabled(name in self.app.stack_colors)
            self.grid.addWidget(b, r, 3)

    def pick(self, name):
        sub = name == self.app.SUBSTRATE_KEY
        _, hexcol = colorchooser.askcolor(
            color=matplotlib.colors.to_hex(self.app.material_color(
                name, substrate=sub)), parent=self,
            title="Colour for the substrate" if sub else f"Colour for {name}")
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


def _html(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class HelpWindow(Dialog):
    """Detailed help for one input (from help_info.HELP)."""

    def __init__(self, app, key):
        h = HI.HELP[key]
        super().__init__(app, f"Help — {h['title']}", 640, 440)
        t = QtWidgets.QTextBrowser()
        t.setOpenExternalLinks(True)
        parts = [f"<h2 style='color:{T.INK}'>{_html(h['title'])}</h2>"]
        for head, field in (("What it is and why it is there", "what"),
                            ("How to fill it in", "fill"),
                            ("If it is left empty / switched off", "empty")):
            if h.get(field):
                parts.append(f"<h4 style='color:{T.INDIGO}'>{head}</h4>"
                             f"<p>{_html(h[field])}</p>")
        t.setHtml("".join(parts))
        self.lay.addWidget(t, 1)
        self.buttons(("Close", self.close),
                     left=(("Code — where it is used", lambda: CodeWindow(app, key)),))
        self.show()


class CodeWindow(Dialog):
    """The lines of the program where an input is converted and used, read
    from the source files now (so the line numbers match the files)."""

    def __init__(self, app, key):
        h = HI.HELP[key]
        super().__init__(app, f"Code — {h['title']}", 1000, 700)
        self.lay.addWidget(label(
            f"Where '{h['title']}' is converted and used. The lines are read "
            f"from the program's files just now, so the line numbers match "
            f"your copy. Highlighted lines contain the parameter; a few lines "
            f"around them are shown for context.", "note", wrap=True))
        t = QtWidgets.QTextBrowser()
        t.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        blocks = HI.code_snippets(key)
        out = ["<pre style='font-family: DejaVu Sans Mono, Consolas, monospace; "
               "font-size: 9pt'>"]
        if not blocks:
            out.append("No code location is recorded for this input.")
        for fname, where, rows in blocks:
            out.append(f"<span style='background:{T.BERRY_LIGHT}; font-weight:bold'>"
                       f" {_html(fname)} — {_html(where)}, line {rows[0][0]} </span>\n")
            for num, text, hit, spans in rows:
                pieces, last = [], 0
                for a, b in spans:
                    pieces.append(_html(text[last:a]))
                    pieces.append(f"<b style='background:#e7b8fd'>{_html(text[a:b])}</b>")
                    last = b
                pieces.append(_html(text[last:]))
                body = "".join(pieces)
                if hit:
                    body = f"<span style='background:#d9f1ee'>{body}</span>"
                out.append(f"<span style='color:{T.INK2}'>{num:5d}  </span>{body}\n")
            out.append("\n")
        out.append("</pre>")
        t.setHtml("".join(out))
        self.lay.addWidget(t, 1)
        self.buttons(("Close", self.close),
                     left=(("Help", lambda: HelpWindow(app, key)),))
        self.show()


class CutoffDialog(QtWidgets.QDialog):
    """Asked when background subtraction is switched on: fit only after a
    delay, or the whole trace? Modal; .ok tells whether OK was pressed."""

    def __init__(self, app):
        super().__init__(app)
        self.app, self.ok = app, False
        ov = app.out
        self.setWindowTitle("Background subtraction — fit range")
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(label("Should the double-exponential background be fitted "
                            "only to delays after a cut-off (e.g. to leave out "
                            "the electronic peak around t = 0)?", wrap=True))
        self.use = BooleanVar(ov["bg_use_cut"].get())
        self.cut = StringVar(ov["bg_cut"].get())
        self.unit = StringVar(ov["bg_cut_unit"].get())
        self.r_yes = QtWidgets.QRadioButton("Yes, only delays after")
        self.r_no = QtWidgets.QRadioButton("No, fit the whole time axis")
        (self.r_yes if self.use.get() else self.r_no).setChecked(True)
        lay.addWidget(hbox(self.r_yes, line_edit(self.cut, 80),
                           combo(self.unit, U.choices("time"))))
        lay.addWidget(self.r_no)
        self.msg = label("", "conv")
        set_bad(self.msg, True)
        lay.addWidget(self.msg)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("OK", self.accept_))
        row.addWidget(button("Cancel", self.reject))
        lay.addLayout(row)
        self.exec()

    def accept_(self):
        use = self.r_yes.isChecked()
        if use:
            try:
                float(self.cut.get())
            except ValueError:
                self.msg.setText("Enter the cut-off delay as a number.")
                return
        ov = self.app.out
        ov["bg_use_cut"].set(use)
        ov["bg_cut"].set(self.cut.get())
        ov["bg_cut_unit"].set(self.unit.get())
        self.ok = True
        self.accept()


class ImportReviewDialog(Dialog):
    """Shows everything read from a parameter file, where each value came
    from, and what is missing, before it replaces the current inputs."""

    SOURCE_COL = {"file": T.INK, "from n,k file": T.INK,
                  "literature (file)": T.LIT_C, "literature (built-in)": T.LIT_C,
                  "MISSING": T.ERR, "not in file (kept)": T.INK2,
                  "blank (default)": T.INK2}

    def __init__(self, app, path, read_file, cache):
        super().__init__(app, f"Review import — {os.path.basename(path)}", 1100, 700)
        self.app, self.path, self.mat_dir = app, path, None
        self.read_file, self.cache = read_file, cache
        self.head = label("", wrap=True)
        self.lay.addWidget(self.head)
        leg = []
        for text, col in (("from the file", T.INK), ("literature placeholder", T.LIT_C),
                          ("missing — fill in after import", T.ERR),
                          ("not in the file: current value kept / default", T.INK2)):
            l_ = QtWidgets.QLabel("■ " + text)
            l_.setStyleSheet(f"color: {col};")
            leg.append(l_)
        self.lay.addWidget(hbox(*leg, spacing=14))
        self.tv = QtWidgets.QTreeWidget()
        self.tv.setHeaderLabels(["Section", "Quantity", "Value", "Unit", "Source",
                                 "Note"])
        for c, w in enumerate((170, 230, 200, 110, 150, 300)):
            self.tv.setColumnWidth(c, w)
        self.lay.addWidget(self.tv, 1)
        self.warn = label("", wrap=True)
        self.warn.setStyleSheet(f"color: {T.LIT_C};")
        self.lay.addWidget(self.warn)
        b = button("Materials folder…", self.choose_folder, tip=(
            "Folder that holds the n,k files named in the parameter file "
            "(nfile=…). They are looked up by name, with or without .txt."))
        self.buttons(("Apply to the GUI", self.apply), ("Cancel", self.close))
        self.lay.itemAt(self.lay.count() - 1).layout().insertWidget(0, b)
        if self.load():
            self.show()

    def load(self):
        try:
            self.result = self.read_file(self.path, self.mat_dir)
        except Exception as e:
            messagebox.showerror("Import parameter file", str(e), parent=self)
            self.close()
            return False
        r = self.result
        self.tv.clear()
        sections = {}
        for sec, item, val, unit, src, note in r["rows"]:
            if sec not in sections:
                sections[sec] = QtWidgets.QTreeWidgetItem(self.tv, [sec])
                sections[sec].setExpanded(True)
            it = QtWidgets.QTreeWidgetItem(sections[sec],
                                           ["", item, str(val), unit, src, note])
            col = QtGui.QColor(self.SOURCE_COL.get(src, T.INK))
            for c in range(6):
                it.setForeground(c, col)
        n_miss = sum(r_[4] == "MISSING" for r_ in r["rows"])
        n_lit = sum(r_[4].startswith("literature") for r_ in r["rows"])
        n_file = sum(r_[4] in ("file", "from n,k file") for r_ in r["rows"])
        names = ", ".join(f"{v[0]} (line {v[1]})" for v in
                          r["found"]["names"].values())
        what = []
        if r["config"] or r["flags"]:
            what.append("the Experiment settings it contains")
        if r["layers"] is not None:
            what.append(f"all layers (the stack becomes {len(r['layers'])} layers)")
        self.head.setText(
            f"Read {names}.  {n_file} values from the file, {n_lit} literature "
            f"placeholders, {n_miss} missing.\nApplying replaces "
            + (" and ".join(what) or "nothing") + ". Check the values below; "
            "you can still change anything afterwards.")
        self.warn.setText("\n".join("⚠ " + w for w in r["warnings"]))
        return True

    def choose_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Materials folder with the n,k files")
        if d:
            self.mat_dir = d
            self.load()

    def apply(self):
        self.app.apply_import(self.result, self.path)
        self.close()
