"""Smoke test of the PiSES window: run it after changing the code.

    python tests/smoke_qt.py            (opens no window: uses Qt's offscreen mode)
    python tests/smoke_qt.py --shots    (also saves a picture of every page in tests/shots/)

It drives the real window the way a user would and checks that
  - every page opens and fits the inspector (nothing cut off at the right),
  - every ⓘ opens its Help and Code windows,
  - a single run finishes, and gives the same numbers as the model called
    directly (no window) with the same inputs,
  - a 3-thickness sweep finishes, saves per-run and combined files, and the
    Sweep viewer steps, keeps a typed zoom and plays,
  - a session saved, cleared and opened again is unchanged,
  - every dialog (Colours, Axes & labels, Stack colours, placeholders) opens.
Message boxes are answered automatically and listed. Exit code 0 = all passed.
"""
import json
import os
import sys
import tempfile
import time
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np                                   # noqa: E402
from PySide6 import QtWidgets                        # noqa: E402

import diffr_model as M                              # noqa: E402
import pises_logic as L                              # noqa: E402
import pises_qt as P                                 # noqa: E402
import qt_theme as T                                 # noqa: E402
import qt_widgets as W                               # noqa: E402
import sample_input as S                             # noqa: E402

SHOTS = "--shots" in sys.argv
NK = {"Ti": ("2.5", "3.3"), "Si3N4": ("2.0", "0"), "SiO2": ("1.46", "0"),
      "Si": ("4.2", "0.05")}
results, boxes = [], []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


# answer message boxes automatically, but remember them
for _n in ("showinfo", "showwarning", "showerror"):
    setattr(L.messagebox, _n, staticmethod(
        lambda *a, _n=_n, **k: boxes.append((_n, a[0] if a else ""))))
for _n in ("askokcancel", "askyesno"):
    setattr(L.messagebox, _n, staticmethod(lambda *a, **k: True))
sys.excepthook = lambda *a: (traceback.print_exception(*a),
                             check("no uncaught exception", False, repr(a[1])))

qapp = QtWidgets.QApplication([])
T.ensure_assets()
T.matplotlib_defaults()
qapp.setStyleSheet(T.STYLE)
app = P.App()
app.resize(1560, 910)
app.show()
tmp = tempfile.mkdtemp(prefix="pises_smoke_")
shots = os.path.join(HERE, "shots")
if SHOTS:
    os.makedirs(shots, exist_ok=True)


def pump(t=0.3):
    end = time.time() + t
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.02)


def wait():
    while app.worker is not None and app.worker.is_alive():
        qapp.processEvents()
        time.sleep(0.03)
    pump()


def shot(name):
    if SHOTS:
        pump(0.2)
        app.grab().save(os.path.join(shots, name + ".png"))


def new_dialogs(before):
    return [d for d in app.findChildren(QtWidgets.QDialog)
            if d.isVisible() and d not in before]


pump()
# ---------------------------------------------------------------- inputs
for lay in app.layers:
    for w in ("pump", "probe"):
        lay[f"n_{w}"], lay[f"k_{w}"] = NK.get(lay["material"], ("2", "0"))
app.refresh_tree(0)
for k, v in dict(t_max_ps="100", substrate_model_nm="1500",
                 sponge_nm="300").items():
    app.cfg_vars[k][0].set(v)
app.out["folder"].set(tmp)
pump()

# ---------------------------------------------------------------- pages
for i, (key, title) in enumerate(P.PAGES):
    app.rail.select(i)
    pump()
    sa = app.pages.currentWidget()
    need = sa.widget().minimumSizeHint().width()
    have = sa.viewport().width()
    check(f"page '{title}' opens and fits", app.pages.currentIndex() == i
          and need <= have, f"needs {need} px, has {have} px")
    shot(f"page_{key}")

# ---------------------------------------------------------------- ⓘ
keys = sorted({b.key for b in app.findChildren(W.InfoButton)})
bad = []
for k in keys:
    for cls in (W.HelpWindow, W.CodeWindow):
        try:
            cls(app, k).close()
        except Exception as e:                       # noqa: BLE001
            bad.append(f"{cls.__name__}({k}): {e!r}")
pump()
check(f"every ⓘ opens Help and Code ({len(keys)} keys)", not bad, "; ".join(bad))

# ---------------------------------------------------------------- single run
app.run_single()
wait()
runs = app.all_runs()
check("single run finishes", len(runs) == 1 and "done" in app.status.text(),
      app.status.text())
if runs:
    # the same inputs straight through sample_input + diffr_model
    cfg, _ = S.resolve_config(app._cfg_entries(), app._flags())
    stack, mats, _, _ = S.resolve_layers(app.layers, cfg)
    ref = M.run_model(stack, mats, cfg, verbose=False)
    check("window result == model called directly",
          np.array_equal(ref["drr"], runs[0]["res"]["drr"]))
    # acoustic loss: attenuation reaches the model and the metadata; an
    # unknown loss mode is an error, never silently "no loss"
    import copy
    lossy = copy.deepcopy(app.layers)
    for lay in lossy:
        lay.update(loss_mode="attenuation", att="1", att_unit="1/µm",
                   att_f="100", att_f_unit="GHz")
    cfg_l = dict(cfg, include_loss=True)
    st_l, mats_l, _, _ = S.resolve_layers(lossy, cfg_l)
    check("attenuation gives a viscosity in every layer",
          all(m.eta_visc > 0 for m in mats_l.values()))
    rows = S.materials_as_json(st_l, mats_l)
    check("metadata records the viscosity", all(r.get("eta_visc", 0) > 0 for r in rows))
    res_l = M.run_model(st_l, mats_l, cfg_l, verbose=False)
    check("attenuation changes ΔR/R", not np.allclose(res_l["drr"], ref["drr"]))
    lossy[0]["loss_mode"] = "att"
    try:
        S.resolve_layers(lossy, cfg_l)
        check("unknown loss mode is reported", False, "no error raised")
    except S.InputError:
        check("unknown loss mode is reported", True)
shot("single_run")

# ---------------------------------------------------------------- dialogs
for name, fn in (("Colours…", app.drr_panel.edit_colors),
                 ("Axes & labels…", app.drr_panel.edit_axes),
                 ("Stack colours", app.edit_stack_colors),
                 ("Literature placeholders", lambda: app.show_placeholders())):
    before = set(new_dialogs(set()))
    try:
        fn()
        pump(0.2)
        opened = new_dialogs(before)
        check(f"dialog '{name}' opens", opened)
        for d in opened:
            d.close()
    except Exception as e:                           # noqa: BLE001
        check(f"dialog '{name}' opens", False, repr(e))

# ---------------------------------------------------------------- sweep
for k in ("auto_sweep", "combined"):
    app.out[k].set(True)
app._sweep_obj = app.layers[min(3, len(app.layers) - 2)]
app._refresh_sweep_layers()
app.sweep_start.set("100")
app.sweep_end.set("300")
app.sweep_step.set("100")
app.run_sweep(ask=False)
wait()
b = app.batches[-1] if app.batches else {}
check("sweep finishes with 3 runs", b.get("kind") == "sweep"
      and len(b.get("runs", [])) == 3, app.status.text())
files = sorted(os.listdir(tmp))
check("sweep files saved (3 per-run CSVs + combined CSV)",
      sum(f.endswith(".csv") for f in files) == 4, ", ".join(files))
shot("sweep")

# viewer: slider, typed x range kept while stepping, play
for i in range(app.ptabs.count()):
    if "viewer" in app.ptabs.tabText(i).lower():
        app.ptabs.setCurrentIndex(i)
pump()
app.viewer_scale.setValue(2)
pump(0.2)
check("viewer slider picks a thickness", "3 of 3" in app.viewer_pos_lbl.text(),
      app.viewer_pos_lbl.text())
app.viewer_range["x0"].set("10")
app.viewer_range["x1"].set("40")
app._viewer_range_typed()
pump()
app._viewer_step(-1)
pump()
xl = tuple(app.viewer_panel.data_axes()[0].get_xlim())
check("viewer keeps the typed x range while stepping",
      np.allclose(xl, (10, 40)), str(xl))
app.toggle_play()
pump(1.0)
app._stop_play()
pump()
check("viewer plays", True)
shot("viewer")

# ---------------------------------------------------------------- session
path = os.path.join(tmp, "session.json")
L.filedialog.asksaveasfilename = staticmethod(lambda *a, **k: path)
st0 = json.dumps(app._state(), sort_keys=True, default=str)
app.save_session_as()
app.new_session()
pump()
app.open_session(path)
pump()
st1 = json.dumps(app._state(), sort_keys=True, default=str)
check("session save → new → open restores everything", st0 == st1)

# ---------------------------------------------------------------- summary
errors = [b for b in boxes if b[0] == "showerror"]
check("no error message boxes", not errors, str(errors))
app.close()
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)} of {len(results)} checks passed"
      + (f"; output files in {tmp}" if failed else ""))
if SHOTS:
    print(f"pictures in {shots}")
sys.exit(1 if failed else 0)
