"""Small helpers that let the PiSES Qt interface keep the structure of the
original Tkinter one: observable variables bound to widgets, dialog
shortcuts (message boxes, file and colour pickers) and timers.

Nothing here knows about the physics; pises_qt.py builds on it.
"""

from PySide6 import QtCore, QtGui, QtWidgets


# ---------------------------------------------------------------------------
# Observable values (like tk.StringVar / tk.BooleanVar)
# ---------------------------------------------------------------------------

class Var:
    """A value with change callbacks. trace_add("write", cb) calls cb() after
    every set(), as tkinter variables do."""

    def __init__(self, value=None):
        self._value = value
        self._cbs = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for cb in list(self._cbs):
            cb()

    def trace_add(self, mode, cb):
        self._cbs.append(lambda: cb(None, None, mode))


class StringVar(Var):
    def __init__(self, value=""):
        super().__init__("" if value is None else str(value))

    def set(self, value):
        super().set("" if value is None else str(value))


class BooleanVar(Var):
    def __init__(self, value=False):
        super().__init__(bool(value))

    def get(self):
        return bool(self._value)

    def set(self, value):
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "yes", "on")
        super().set(bool(value))


def _quiet(widget, fn):
    """Change a widget without its change signal coming back to the var."""
    old = widget.blockSignals(True)
    try:
        fn()
    finally:
        widget.blockSignals(old)


def bind_line(edit, var):
    """QLineEdit <-> StringVar."""
    _quiet(edit, lambda: edit.setText(var.get()))
    edit.textChanged.connect(lambda s: var.get() != s and var.set(s))
    var.trace_add("write", lambda *_: edit.text() != var.get() and _quiet(
        edit, lambda: edit.setText(var.get())))
    return edit


def bind_combo(combo, var):
    """QComboBox (editable or not) <-> StringVar. A value not in the list is
    shown as the editable text, or added for a read-only box."""
    def to_widget():
        v = var.get()
        if combo.isEditable():
            if combo.currentText() != v:
                _quiet(combo, lambda: combo.setCurrentText(v))
            return
        i = combo.findText(v)
        if i < 0 and v:
            _quiet(combo, lambda: combo.addItem(v))
            i = combo.findText(v)
        if combo.currentIndex() != i:
            _quiet(combo, lambda: combo.setCurrentIndex(i))
    to_widget()
    if combo.isEditable():
        combo.currentTextChanged.connect(
            lambda s: var.get() != s and var.set(s))
    else:
        combo.currentIndexChanged.connect(
            lambda i: i >= 0 and var.get() != combo.itemText(i)
            and var.set(combo.itemText(i)))
    var.trace_add("write", lambda *_: to_widget())
    return combo


def bind_check(box, var):
    """QAbstractButton (checkable) <-> BooleanVar."""
    _quiet(box, lambda: box.setChecked(var.get()))
    box.toggled.connect(lambda b: var.get() != b and var.set(b))
    var.trace_add("write", lambda *_: box.isChecked() != var.get() and _quiet(
        box, lambda: (box.setChecked(var.get()), box.update())))
    return box


def bind_spin(spin, var):
    """QSpinBox / QDoubleSpinBox <-> StringVar (text of the number)."""
    def to_widget():
        try:
            v = float(var.get())
        except ValueError:
            return
        if spin.value() != v:
            _quiet(spin, lambda: spin.setValue(type(spin.value())(v)))
    to_widget()
    spin.valueChanged.connect(lambda v: var.set(f"{v:g}"))
    var.trace_add("write", lambda *_: to_widget())
    return spin


# ---------------------------------------------------------------------------
# Timers (like tk's after / after_idle / after_cancel)
# ---------------------------------------------------------------------------

class Scheduler:
    """Mixin for a QObject: after(ms, fn) -> id, after_idle(fn) -> id,
    after_cancel(id)."""

    def _timers(self):
        if not hasattr(self, "_sched"):
            self._sched, self._sched_n = {}, 0
        return self._sched

    def after(self, ms, fn):
        timers = self._timers()
        self._sched_n += 1
        key = self._sched_n
        t = QtCore.QTimer(self)
        t.setSingleShot(True)

        def fire():
            timers.pop(key, None)
            t.deleteLater()
            fn()
        t.timeout.connect(fire)
        timers[key] = t
        t.start(max(0, int(ms)))
        return key

    def after_idle(self, fn):
        return self.after(0, fn)

    def after_cancel(self, key):
        t = self._timers().pop(key, None)
        if t is not None:
            t.stop()
            t.deleteLater()


# ---------------------------------------------------------------------------
# Dialog shortcuts (the tkinter.messagebox / filedialog / colorchooser API)
# ---------------------------------------------------------------------------

def _parent(parent):
    return parent or QtWidgets.QApplication.activeWindow()


class messagebox:
    @staticmethod
    def showerror(title, text, parent=None):
        QtWidgets.QMessageBox.critical(_parent(parent), title, text)

    @staticmethod
    def showwarning(title, text, parent=None):
        QtWidgets.QMessageBox.warning(_parent(parent), title, text)

    @staticmethod
    def showinfo(title, text, parent=None):
        QtWidgets.QMessageBox.information(_parent(parent), title, text)

    @staticmethod
    def askyesno(title, text, parent=None):
        B = QtWidgets.QMessageBox.StandardButton
        return QtWidgets.QMessageBox.question(
            _parent(parent), title, text, B.Yes | B.No, B.No) == B.Yes

    @staticmethod
    def askokcancel(title, text, parent=None):
        B = QtWidgets.QMessageBox.StandardButton
        return QtWidgets.QMessageBox.question(
            _parent(parent), title, text, B.Ok | B.Cancel, B.Ok) == B.Ok

    @staticmethod
    def askyesnocancel(title, text, parent=None):
        B = QtWidgets.QMessageBox.StandardButton
        r = QtWidgets.QMessageBox.question(
            _parent(parent), title, text, B.Yes | B.No | B.Cancel, B.Cancel)
        return None if r == B.Cancel else r == B.Yes


def _filter(filetypes):
    """[("Text", "*.txt *.dat"), ...] -> Qt filter string."""
    return ";;".join(f"{name} ({pat})" for name, pat in (filetypes or [])) or ""


class filedialog:
    @staticmethod
    def askopenfilename(title="Open", initialdir=None, filetypes=None,
                        parent=None, **_):
        p, _f = QtWidgets.QFileDialog.getOpenFileName(
            _parent(parent), title, initialdir or "", _filter(filetypes))
        return p

    @staticmethod
    def asksaveasfilename(title="Save", initialdir=None, initialfile="",
                          defaultextension="", filetypes=None, parent=None, **_):
        import os
        start = os.path.join(initialdir or "", initialfile or "")
        p, _f = QtWidgets.QFileDialog.getSaveFileName(
            _parent(parent), title, start, _filter(filetypes))
        if p and defaultextension and not os.path.splitext(p)[1]:
            p += defaultextension
        return p

    @staticmethod
    def askdirectory(title="Folder", initialdir=None, parent=None, **_):
        return QtWidgets.QFileDialog.getExistingDirectory(
            _parent(parent), title, initialdir or "")


class colorchooser:
    @staticmethod
    def askcolor(color=None, parent=None, title="Colour", **_):
        c = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(color or "#ffffff"), _parent(parent), title)
        if not c.isValid():
            return None, None
        return (c.red(), c.green(), c.blue()), c.name()
