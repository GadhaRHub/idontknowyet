"""Look of the PiSES Qt interface: the Berry & indigo palette, the Qt style
sheet, the navigation-rail icons and the matplotlib defaults.

Change a colour here and the whole window follows: every widget style
in the style sheet below is built from these names.
"""

import os

import matplotlib
from PySide6 import QtCore, QtGui, QtSvg

# --- palette --------------------------------------------------------------
BG = "#F6F2F5"          # window background (around the panels)
PANEL = "#FFFFFF"       # panels, cards, plot areas
FIELD = "#F6F1F4"       # input fields
LINE = "#E4DBE1"        # hairlines between sections
BERRY = "#7A1F5C"       # accent: active rail item, tabs, focus, switches
BERRY_LIGHT = "#F2E1EC"  # pill behind the active rail icon, selections
INDIGO = "#2E2A6B"      # section titles, strong text
SIENNA = "#A0522D"      # Run model / Run sweep / Play
TEAL = "#6CC5BA"        # progress, "ok" text in the console
TEAL_LIGHT = "#DDF2EF"
INK = "#1D1A1C"         # text
INK2 = "#5C5359"        # secondary text
LIT_C = "#B35900"       # literature placeholder marks
ERR = "#C00000"         # errors
CONSOLE_BG = "#211D2B"
CONSOLE_FG = "#D8D3E8"

# plot colours (matplotlib), shared with the Tk interface
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
          "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
PLOT_INK, PLOT_INK2, GRID_C = "#0b0b0b", "#52514e", "#d9d8d4"

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "assets")


def _asset(name):
    """Path of a file in assets/ written the way style sheets want it."""
    return os.path.join(ASSETS, name).replace(os.sep, "/")

FONT = "Segoe UI, Cantarell, Ubuntu, DejaVu Sans, Arial, sans-serif"


def mix(c, w, f):
    """Blend colour c towards w by fraction f (hover / pressed shades)."""
    a, b = matplotlib.colors.to_rgb(c), matplotlib.colors.to_rgb(w)
    return matplotlib.colors.to_hex([x + (y - x) * f for x, y in zip(a, b)])


def matplotlib_defaults():
    matplotlib.rcParams.update({
        "font.size": 9, "axes.edgecolor": PLOT_INK2,
        "axes.labelcolor": PLOT_INK, "xtick.color": PLOT_INK2,
        "ytick.color": PLOT_INK2, "grid.color": GRID_C, "axes.titlesize": 10,
        "legend.frameon": False, "lines.linewidth": 1.4,
        # the Sweep viewer uses ← → itself
        "keymap.back": ["backspace"], "keymap.forward": [],
    })


# --- style sheet ----------------------------------------------------------
STYLE = f"""
* {{ font-family: {FONT}; font-size: 10pt; color: {INK}; }}
QMainWindow, #central {{ background: {BG}; }}
QToolTip {{ background: #fffdf0; color: {INK}; border: 1px solid {LINE};
           padding: 4px; }}

/* top bar */
#topbar {{ background: {PANEL}; border-bottom: 1px solid {LINE}; }}
#brand {{ font-size: 13pt; font-weight: bold; color: {INDIGO}; }}
#brandsub {{ color: {INK2}; font-size: 9pt; }}
#crumb {{ color: {INK2}; }}
QPushButton#tb, QToolButton#tb {{ background: {PANEL}; border: 1px solid {LINE};
    border-radius: 8px; padding: 5px 12px; }}
QPushButton#tb:hover, QToolButton#tb:hover {{ background: {BERRY_LIGHT}; }}
QToolButton#tb::menu-indicator {{ image: none; }}
/* navigation rail */
#rail {{ background: {BG}; border-right: 1px solid {LINE}; }}
#railitem {{ background: transparent; border: none; }}
#raillabel {{ color: {INK2}; font-size: 9pt; }}
#raillabel[active="true"] {{ color: {INK}; font-weight: bold; }}
#runlabel {{ color: {SIENNA}; font-size: 9pt; font-weight: bold; }}
#runlabel[busy="true"] {{ color: {INK2}; }}
#railsep {{ background: {LINE}; }}

/* inspector panels */
#inspector {{ background: {PANEL}; border-right: 1px solid {LINE}; }}
QScrollArea {{ background: {PANEL}; border: none; }}
#page {{ background: {PANEL}; }}
#pagehead {{ font-size: 12pt; font-weight: bold; color: {INK}; }}
#pagesub {{ color: {INK2}; }}
#section {{ border-top: 1px solid {LINE}; background: {PANEL}; }}
#sechead {{ background: transparent; border: none; text-align: left;
    color: {INDIGO}; font-weight: bold; font-size: 9pt; padding: 8px 0; }}
#sechead:hover {{ color: {BERRY}; }}
#hint, #conv {{ color: {INK2}; }}
#conv[bad="true"], #hint[bad="true"] {{ color: {ERR}; }}
#lit {{ color: {LIT_C}; font-size: 8pt; }}
#note {{ color: {INK2}; font-size: 9pt; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {FIELD}; border: 1px solid transparent; border-radius: 7px;
    padding: 4px 8px; min-height: 18px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    background: {PANEL}; border: 1px solid {BERRY}; }}
QLineEdit:read-only {{ color: {INK2}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: #a39aa0; }}
QComboBox {{ padding-right: 20px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({_asset("chev_down.svg")}); width: 12px; height: 12px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border;
    subcontrol-position: top right; width: 18px; border: none; background: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border;
    subcontrol-position: bottom right; width: 18px; border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({_asset("chev_up.svg")});
    width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({_asset("chev_down.svg")});
    width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE};
    selection-background-color: {BERRY_LIGHT}; selection-color: {INK}; }}

QPushButton {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px;
    padding: 5px 12px; }}
QPushButton:hover {{ background: {BERRY_LIGHT}; }}
QPushButton:pressed {{ background: {mix(BERRY_LIGHT, BERRY, .2)}; }}
QPushButton:disabled {{ color: #a39aa0; }}
QPushButton#accent {{ background: {SIENNA}; color: white; border: none;
    font-weight: bold; }}
QPushButton#accent:hover {{ background: {mix(SIENNA, "white", .15)}; }}
QPushButton#small {{ padding: 3px 8px; border-radius: 6px; }}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
QCheckBox::indicator {{ border: 1px solid {mix(INK2, "white", .4)};
    border-radius: 4px; background: {PANEL}; }}
QCheckBox::indicator:checked {{ background: {BERRY}; border-color: {BERRY};
    image: url({_asset("check.svg")}); }}

/* segmented control */
#seg {{ background: #F1EBEF; border-radius: 8px; }}
#segbtn {{ background: transparent; border: none; border-radius: 6px;
    padding: 4px 10px; color: {INK}; }}
#segbtn:checked {{ background: {PANEL}; color: {BERRY}; font-weight: bold; }}

/* layer list */
QTreeWidget {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px;
    alternate-background-color: #fbf8fa; }}
QTreeWidget::item {{ padding: 3px 2px; }}
QTreeWidget::item:selected {{ background: {BERRY_LIGHT}; color: {INK}; }}
QHeaderView::section {{ background: {PANEL}; border: none;
    border-bottom: 1px solid {LINE}; padding: 4px; color: {INDIGO};
    font-weight: bold; }}

/* plot area */
#plotarea {{ background: {BG}; }}
#plotcard {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {INK2}; padding: 6px 14px;
    margin-right: 4px; border-radius: 8px; }}
QTabBar::tab:selected {{ background: {BERRY}; color: white; font-weight: bold; }}
QTabBar::tab:hover:!selected {{ background: {BERRY_LIGHT}; }}
#chip {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 12px;
    padding: 3px 10px; }}
#chip:checked {{ background: {BERRY_LIGHT}; border-color: {BERRY}; color: {BERRY}; }}

/* console and status bar */
#console {{ background: {CONSOLE_BG}; border-top-left-radius: 12px;
    border-top-right-radius: 12px; }}
#consolehead {{ color: white; font-weight: bold; }}
#consolesub {{ color: #8f89a8; }}
#consoletext {{ background: {CONSOLE_BG}; color: {CONSOLE_FG}; border: none;
    font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 9pt; }}
#statusbar {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
#statustext, #statusright {{ color: {INK2}; font-size: 9pt; }}
QProgressBar {{ background: {TEAL_LIGHT}; border: none; border-radius: 3px;
    max-height: 6px; }}
QProgressBar::chunk {{ background: {TEAL}; border-radius: 3px; }}

QSplitter::handle {{ background: {BG}; }}
QToolBar QToolButton {{ border: none; border-radius: 6px; padding: 3px; }}
QToolBar QToolButton:hover {{ background: {BERRY_LIGHT}; }}
QToolBar QToolButton:checked {{ background: {mix(BERRY_LIGHT, BERRY, .15)}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #d8ccd5; border-radius: 5px;
    min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #d8ccd5; border-radius: 5px;
    min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QMenu {{ background: {PANEL}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {BERRY_LIGHT}; color: {INK}; }}
QDialog, #dlgbody {{ background: {PANEL}; }}
"""

# --- rail icons (24x24 SVG paths, drawn in the given colour) --------------
RAIL_ICONS = {
    "experiment": '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 '
                  '2-3l-5-9V3" fill="none" stroke="{c}" stroke-width="2"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z" fill="{c}"/><path d="M3 12l9 5 9-5'
              'M3 16l9 5 9-5" fill="none" stroke="{c}" stroke-width="2"/>',
    "interfaces": '<rect x="3" y="4" width="18" height="16" rx="3" fill="none" '
                  'stroke="{c}" stroke-width="2"/><path d="M12 4v16" '
                  'stroke="{c}" stroke-width="2" stroke-dasharray="2 2"/>',
    "sweep": '<path d="M2 12c3-8 5-8 7 0s4 8 7 0 4-8 6 0" fill="none" '
             'stroke="{c}" stroke-width="2"/>',
    "background": '<path d="M3 5c4 12 8 14 18 14" fill="none" stroke="{c}" '
                  'stroke-width="2"/>',
    "output": '<path d="M12 3v12m-5-5l5 5 5-5M4 20h16" fill="none" '
              'stroke="{c}" stroke-width="2"/>',
    "run": '<path d="M8 5.5v13l10.5-6.5z" fill="{c}" stroke="{c}" '
           'stroke-width="1.5" stroke-linejoin="round"/>',
    "help": '<circle cx="12" cy="12" r="9" fill="none" stroke="{c}" '
            'stroke-width="2"/><path d="M9.5 10a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 '
            '.8-1 1.7M12 16.5v.5" stroke="{c}" stroke-width="2" fill="none"/>',
}


def svg_icon(svg_body, size=24, color=INK2):
    """QIcon from an SVG fragment (24x24 user units) in one colour."""
    data = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'{svg_body.replace("{c}", color)}</svg>').encode()
    r = QtSvg.QSvgRenderer(QtCore.QByteArray(data))
    pm = QtGui.QPixmap(size * 2, size * 2)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(2)
    return QtGui.QIcon(pm)


def app_icon():
    icon = QtGui.QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        path = os.path.join(ASSETS, f"pises_{s}.png")
        if os.path.exists(path):
            icon.addFile(path, QtCore.QSize(s, s))
    return icon


_SMALL_SVGS = {
    # check mark inside ticked check boxes
    "check.svg": '<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="white" '
                 'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
    # arrows of combo boxes and spin boxes
    "chev_down.svg": f'<path d="M4 6.5l4 4 4-4" fill="none" stroke="{INK2}" '
                     'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
    "chev_up.svg": f'<path d="M4 10l4-4 4 4" fill="none" stroke="{INK2}" '
                   'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>',
}


def ensure_assets():
    """Write the small SVGs the style sheet uses (check mark, arrows) if
    they are missing from assets/."""
    for name, body in _SMALL_SVGS.items():
        path = os.path.join(ASSETS, name)
        if os.path.exists(path):
            continue
        try:
            os.makedirs(ASSETS, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write('<svg xmlns="http://www.w3.org/2000/svg" '
                        f'viewBox="0 0 16 16">{body}</svg>')
        except OSError:
            pass
