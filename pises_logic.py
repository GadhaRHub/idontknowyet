"""Behaviour of the PiSES window: everything that is not building widgets.

The App class in pises_qt.py inherits from Logic. Logic never creates
widgets directly; it reads and writes the observable variables (self.lv,
self.cfg_vars, self.out, …) and calls a few small methods that the window
provides (see "window hooks" at the end of this docstring).

    planning   names, file counts, sizes and time estimates (Output, Sweep)
    layers     the layer list and the layer editor's behaviour
    running    input conversion, the worker thread, progress, results
    plots      stack diagram, ΔR/R, components, kernels, strain map,
               background, sweep viewer (all matplotlib, via PlotPanel)
    files      export, sessions, parameter / conf import

Window hooks (implemented in pises_qt.App):
    _text(label, text, bad=False)   set a label's text (red when bad)
    _show(widget, on)               show or hide a widget
    _set_running(on)                Run buttons off / Cancel on, and back
    _set_progress(fraction)         progress bar 0..1
    _set_status(text)               status bar text
    _busy_cursor(on)                wait cursor
    title(text)                     window title
    refresh_tree(select)            rebuild the layer list
    _refresh_tree_row(i)            update one row of the layer list
    _refresh_sweep_layers()         refill the Sweep page's layer menu
    _refresh_interfaces()           rebuild the Interfaces page rows
    _set_run_list(labels, select)   fill the "Show run" menu
    _update_plot_tabs()             show/hide optional plot tabs
    _viewer_scale_update(vis, cur, batch)   Sweep viewer slider and label
    _viewer_is_current()            is the Sweep viewer tab on screen?
    _sweep_page_open()              is the Sweep page on screen?
    _run_mode_changed()             the run mode (single / sweep) changed
    _go_to_layer(i)                 show the Layers page with layer i
"""

import copy
import json
import os
import queue
import threading
import time
import traceback

import numpy as np
import matplotlib
from matplotlib import colormaps
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.cm import ScalarMappable
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.transforms import blended_transform_factory
from PySide6 import QtWidgets

import diffr_model as M
try:
    import background as B          # works with numpy alone (scipy optional)
    BG_IMPORT_ERROR = None
except ImportError as _e:
    B, BG_IMPORT_ERROR = None, str(_e)
import outputs as O
import param_import as PI
import sample_input as S
import units as U
import qt_theme as T
import qt_widgets as W
from qt_compat import messagebox, filedialog
from gui_common import (APP_NAME, APP_LONG, EXAMPLE_STACK,
                        DEFAULT_MATERIAL_COLORS, SUBSTRATE_KEY,
                        SUBSTRATE_DEFAULT, FIGURE_PANELS, HELP_TEXT, drr_label,
                        fmt_duration, is_dark)

SERIES = T.SERIES
INK, INK2, GRID_C, LIT_C = T.PLOT_INK, T.PLOT_INK2, T.GRID_C, T.LIT_C
outside_legend = W.outside_legend
cmap_colors = W.cmap_colors


class Logic:
    SUBSTRATE_KEY = SUBSTRATE_KEY
    # "single" or "sweep": what Run last did. In sweep mode the stack diagram
    # shows the sweep on every page, not only on the Sweep page.
    run_mode = "single"
    VIEWER_MODES = ["Current curve only", "Current + others faint",
                    "All shown curves"]
    VIEWER_SIGNALS = ["ΔR/R − background", "ΔR/R"]

    # =================================================================
    # Background settings
    # =================================================================
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
                raise ValueError("Background page: the cut-off delay must be a "
                                 "number (or switch off 'Fit only delays after "
                                 "a cut-off').")
        return dict(use_cut=bool(opts["bg_use_cut"]), cut_ps=cut,
                    force_decay=bool(opts["bg_force_decay"]),
                    before=B.BEFORE_CHOICES.get(opts["bg_before"], "nan"))

    def _update_bg_cut_label(self):
        if not hasattr(self, "bg_cut_conv"):
            return
        try:
            s = self._bg_settings(dict(self._opts(), bg_enabled=True))
            self._text(self.bg_cut_conv,
                       f"= {s['cut_ps']:g} ps" if s["use_cut"] else "not used")
        except ValueError:
            self._text(self.bg_cut_conv, "not a number", bad=True)

    def _on_bg_toggle(self):
        """Switching subtraction on asks whether to fit only after a delay."""
        if self.out["bg_enabled"].get() and not W.CutoffDialog(self).ok:
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
            self._text(self.bg_result, self._bg_res_text(self.selected_res()))

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
        self._busy_cursor(True)
        t_all = time.perf_counter()
        try:
            for r in runs:
                self._fit_background(r["res"], st)
                self.log(f"{r['label']}: background re-fitted in "
                         f"{fmt_duration(r['res']['bg_time'])} — " +
                         (B.describe(r["res"]["bg"]) if r["res"].get("bg")
                          else r["res"]["bg_error"]))
        finally:
            self._busy_cursor(False)
        t_all = time.perf_counter() - t_all
        self.log(f"re-fit of {len(runs)} run(s) took {fmt_duration(t_all)}", "head")
        self._set_status(f"background re-fit — {len(runs)} run(s) in "
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

    # =================================================================
    # Run / sweep counter and output planning
    # =================================================================
    def _on_counter_edit(self):
        if getattr(self, "_setting_counter", False):
            return
        try:
            n = int(float(self.next_run_var.get()))
            if n < 1:
                raise ValueError
        except ValueError:
            self._text(self.counter_msg, "enter a whole number ≥ 1", bad=True)
            return
        self.run_counter = n - 1
        self._text(self.counter_msg, "")
        self.schedule_preview()

    def _set_counter(self, run_counter):
        self.run_counter = run_counter
        self._setting_counter = True
        try:
            self.next_run_var.set(str(run_counter + 1))
        finally:
            self._setting_counter = False
        self._text(self.counter_msg, "")
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
                raise ValueError("Choose an output folder on the Output page.")
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
                         "on on the Output page, or use Export later).")
        lines.append(self._bg_plan_text())
        lines.append("Estimated time: " + self._time_estimate(p["works"]))
        return "\n".join(lines)

    def _bg_plan_text(self):
        """One line on whether / how the background will be subtracted."""
        if not self._bg_shown():
            return ("Background subtraction: off (switch it on on the "
                    "Background page)")
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
        try:
            p = self._sweep_plan()
            shown = ", ".join(f"{v:g}" for v in p["vals"][:8]) + (
                f", … {p['vals'][-1]:g}" if len(p["vals"]) > 8 else "")
            self._text(self.sweep_preview, f"{len(p['vals'])} runs: {shown} "
                                           f"{p['unit']}")
            sweep_txt = self._describe_sweep(p)
            self._text(self.sweep_summary, sweep_txt)
        except ValueError as e:
            self._text(self.sweep_preview, str(e), bad=True)
            self._text(self.sweep_summary, "")
            sweep_txt = f"Sweep: {e}"
        bad = False
        try:
            sp = self._single_plan()
            if sp["base"]:
                single = (f"Next single run → {os.path.basename(sp['base'])}"
                          f" ({', '.join(O.run_files(self._opts()))}"
                          f"{', + %d figure(s)' % sp['nfig'] if sp['nfig'] else ''})"
                          f", {sp['nfiles']} file(s), ≈ "
                          f"{O.human_size(sp['nbytes'])}")
            else:
                single = ("Single runs are not saved automatically (Export → "
                          "selected run saves one by hand).")
        except ValueError as e:
            single, bad = str(e), True
        self._text(self.out_preview, single + "\n\nThickness sweep:\n" + sweep_txt,
                   bad=bad)
        self._update_status_right()

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

    # =================================================================
    # Layers: the editor's behaviour
    # =================================================================
    LOSS_KEYS = ("loss_mode", "visc", "visc_unit", "att", "att_unit", "att_f",
                 "att_f_unit", "rho", "rho_unit", "v", "v_unit")

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
            self._refresh_sweep_layers()
            self._refresh_interfaces()
        if key in ("nk_mode", "nk_file", "nk_file_unit", "kfill_pump",
                   "kfill_probe"):
            self.schedule_file_nk()

    def _update_loss_view(self):
        """Show the row of the chosen loss input and what it means: η and
        the amplitude attenuation at 50 / 100 / 200 GHz."""
        mode = self.lv["loss_mode"].get()
        for m, row in self.loss_rows.items():
            self._show(row, m == mode)
        if self.sel is None or mode == "none":
            self._text(self.loss_conv, "No acoustic loss: echoes keep their "
                                       "amplitude (apart from transmission).")
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
            self._text(self.loss_conv,
                       f"{head};  amplitude attenuation α = {al[0]:.3g} / "
                       f"{al[1]:.3g} / {al[2]:.3g} per µm at 50 / 100 / 200 GHz")
        except (ValueError, ZeroDivisionError, KeyError):
            self._text(self.loss_conv, "Enter numbers (and ρ, v above) to see "
                                       "the attenuation.", bad=True)

    def _show_nk_mode(self):
        file_mode = self.lv["nk_mode"].get() == "file"
        self._show(self.file_f, file_mode)
        self._text(self.nk_head, "n, k interpolated from the file (read-only; "
                                 "switch to 'Typed' to edit):" if file_mode else "")
        self._show(self.nk_head, file_mode)
        for en in self.nk_entries:
            en.setReadOnly(file_mode)
        self._update_nk_labels()

    def _wavelengths(self):
        """Pump and probe wavelengths in nm from the Experiment page."""
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
            lab.setText(f"{w} λ = {lam[w]:.6g} nm" if w in lam else f"{w} λ")

    def schedule_file_nk(self):
        if getattr(self, "_nk_job", None):
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
            self._show(self.kfill_f, False)
            lines.append("Choose an n,k file with Browse…")
        else:
            try:
                tab = self.cache.get(path, lay["nk_file_unit"])
            except Exception as e:
                tab, bad = None, True
                self._show(self.kfill_f, False)
                lines.append(f"Cannot read {path}: {e}" if os.path.isfile(path)
                             else f"File not found: {path}")
            if tab is not None:
                has_k = tab["k"] is not None
                self._show(self.kfill_f, not has_k)
                lines.append(f"{os.path.basename(path)}: {len(tab['lam'])} rows, "
                             f"{tab['lam'][0]:.6g}–{tab['lam'][-1]:.6g} nm, "
                             f"{'n and k' if has_k else 'n only'}")
                if S.unit_note(tab):
                    lines.append(S.unit_note(tab))
                try:
                    lam = self._wavelengths()
                except (ValueError, ZeroDivisionError):
                    lam, bad = {}, True
                    lines.append("Set valid pump and probe wavelengths on the "
                                 "Experiment page.")
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
        self._text(self.file_info, "\n".join(lines), bad=bad)

    def _show_pe_mode(self):
        mode = self.lv["pe_mode"].get()
        for k, row in self.pe_rows.items():
            self._show(row, k == mode)

    def _refresh_lit_marks(self):
        if self.sel is None:
            return
        lay = self.layers[self.sel]
        file_mode = lay["nk_mode"] == "file"
        for key, mark in self.lit_marks.items():
            hidden = (key in ("k_pump", "k_probe") and file_mode or
                      key in ("kfill_pump", "kfill_probe") and not file_mode)
            mark.setText("● lit." if S.is_placeholder(lay, key)
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
        return (str(i + 1), lay["material"], d, nk,
                f"{n_lit} lit." if n_lit else "")

    def show_placeholders(self, layer=None):
        rows = S.placeholder_rows(self.layers)
        title = "Literature placeholders"
        if layer is not None:
            rows = [r for r in rows if r[0] == layer + 1]
            title += f" — layer {layer + 1} ({self.layers[layer]['material']})"
        W.PlaceholderDialog(self, rows, title)

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
        self.d_entry.setEnabled(not last)
        self.d_unit_cb.setEnabled(not last)
        self._text(self.sub_note, "This is the last layer: the semi-infinite "
                                  "substrate (no thickness)." if last else "")
        self._show(self.sub_note, last)
        self._text(self.editor_title, f"Layer {i + 1} · "
                                      f"{lay['material'].strip() or 'unnamed'}")
        self._show_nk_mode()
        self._show_pe_mode()
        self._update_loss_view()
        for f in self.conv_upd.values():
            f()
        if last:
            self._text(self.d_conv, "")
        self._refresh_lit_marks()
        self._text(self.file_info, "")
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
        self.mat_cb.setFocus()

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
        W.PlaceholderDialog(self, rows, f"Literature values — layer "
                                        f"{self.sel + 1} ({name})",
                            note="" if filled else "Nothing new was filled; all "
                            "fields already had values.")

    def browse_nk(self):
        cur = self.lv["nk_file"].get().strip()
        p = filedialog.askopenfilename(
            title="n,k dispersion file",
            initialdir=os.path.dirname(cur) if cur else None,
            filetypes=[("Text tables", "*.txt *.dat *.csv *.nk"), ("All", "*")])
        if p:
            self.lv["nk_mode"].set("file")
            self.lv["nk_file"].set(os.path.normpath(p))
            self._refresh_file_nk()

    # =================================================================
    # Stack diagram
    # =================================================================
    def _bg_shown(self):
        return hasattr(self, "out") and self.out["bg_enabled"].get()

    def schedule_stack(self):
        if getattr(self, "_stack_job", None):
            self.after_cancel(self._stack_job)
        self._stack_job = self.after(150, self.draw_stack)

    def draw_stack(self):
        self._stack_job = None
        self.schedule_preview()          # file sizes depend on the thicknesses
        self._update_mini_stack()
        p = self.stack_panel
        if not self.layers:
            p.empty("No layers. Use 'Add layer' on the Layers page.")
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
                    color="white" if is_dark(col) else INK, clip_on=True,
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

    def _update_mini_stack(self):
        if not hasattr(self, "mini_stack"):
            return
        order = list(dict.fromkeys(l["material"].strip() for l in self.layers))
        last = len(self.layers) - 1
        self.mini_stack.set_blocks(
            [(l["material"].strip() or "?",
              self.material_color(l["material"].strip(), order, substrate=i == last))
             for i, l in enumerate(self.layers)], self.sel)

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

        The sweep as it was run, if the last result is a sweep of this stack.
        While the Sweep page is open, or in sweep mode (Run → Run thickness
        sweep), the sweep as it is set up now, unless it is the one just run.
        """
        done = self._run_sweep_marker()
        if not (self._sweep_page_open() or self.run_mode == "sweep"):
            return done
        planned = self._planned_sweep_marker()
        if planned is None or (done and done["key"] == planned["key"]):
            return done
        return planned

    @staticmethod
    def _marker(idx, vals, unit, step, title, planned):
        n = len(vals)
        rng = f"{vals[0]:g} → {vals[-1]:g} {unit}" if n > 1 else f"{vals[0]:g} {unit}"
        step_txt = f" in steps of {step:g} {unit}" if step and n > 1 else ""
        return dict(idx=idx, planned=planned, title=title,
                    range=f"{rng}\n({n} runs)",
                    range_long=f"{rng}{step_txt}, {n} runs",
                    key=(idx, tuple(round(v, 9) for v in vals), unit))

    def _planned_sweep_marker(self):
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
                        range_long="range not valid yet", key=None)
        return self._marker(idx, vals, unit, step, self.layer_title(idx), True)

    def _run_sweep_marker(self):
        b = self.batches[-1] if self.batches else None
        if not b or b.get("kind") != "sweep" or "sweep_idx" not in b:
            return None
        idx = b["sweep_idx"]
        if idx >= len(self.layers) - 1:
            return None
        if b.get("stack_fp") != self._fingerprint(skip_d=idx, stack_only=True):
            return None
        title = (f"{b.get('sweep_name') or self.layers[idx]['material']} "
                 f"(layer {idx + 1})")
        return self._marker(idx, b["sweep_vals"], b["sweep_unit"],
                            b.get("sweep_step"), title, False)

    def edit_stack_colors(self):
        W.StackColorDialog(self)

    def _on_stack_click(self, e):
        if e.inaxes is None or e.xdata is None or self.stack_panel.toolbar.mode:
            return
        for i, (a, b) in enumerate(getattr(self, "_stack_x", [])):
            if a <= e.xdata < b:
                self._go_to_layer(i)
                return

    # =================================================================
    # Result plots
    # =================================================================
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
        # tagged [inputs A], [inputs B], … so they are not mistaken for each
        # other; a shown sweep's varied thickness is left out of the comparison
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
            p.empty("Switch on 'Strain map' and run the model to record the "
                    "strain field." if res is None else
                    "This run was made without recording η(z,t).\n"
                    "Keep 'Strain map' switched on and run again.")
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

    # --- background plot
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
            p.empty("This sweep has no background fits. Switch on 'Subtract "
                    "the background' and press 'Re-fit all runs', or run it "
                    "again.")
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
        if labels and (select_last or self.view_run.get() not in labels):
            sel = labels[-1]
        elif labels:
            sel = self.view_run.get()
        else:
            sel = ""
        self._set_run_list(labels, sel)
        self.view_run.set(sel)

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

    # =================================================================
    # Sweep viewer
    # =================================================================
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
        """Draw one frame (screen and GIF). Returns the colour bar and axes."""
        runs = batch["runs"]
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
            ax.set_ylim(lo - pad, hi + pad)   # fixed: the curve moves, not the axes
        if off:
            ylab += f"  (curves offset by {off:g})"
        if mode != self.VIEWER_MODES[0]:
            for i in vis:
                if i == cur or curves[i] is None:
                    continue
                if mode == self.VIEWER_MODES[1]:
                    ax.plot(t, curves[i], color="#c8c8c8", lw=0.8)
                else:
                    ax.plot(t, curves[i], color=cols[i], lw=1.0, alpha=.85)
        if curves.get(cur) is not None:
            ax.plot(t, curves[cur], color=cols[cur],
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
            ax.set_xlim(d["auto"][0])
            ax.set_ylim(d["auto"][1])
            p.toolbar.push_current()
            ax.set_xlim(shown[0])
            ax.set_ylim(shown[1])
            p.toolbar.push_current()
        ax.callbacks.connect("xlim_changed", self._viewer_zoomed)
        ax.callbacks.connect("ylim_changed", self._viewer_zoomed)
        self._viewer_scale_update(vis, self.viewer_cur, batch)

    # zoom range of the viewer: the boxes follow the zoom (scroll, zoom
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
        auto = getattr(p, "_viewer_auto", None)
        if not axes or auto is None:
            return
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

    def _viewer_step(self, step):
        """← / → in the Sweep viewer: one shown thickness back / on."""
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
        self.play_btn.setText("⏸ Pause")
        self._play_job = self.after(10, self._play_step)

    def _stop_play(self):
        if getattr(self, "_play_job", None) is not None:
            self.after_cancel(self._play_job)
        self._play_job = None
        if hasattr(self, "play_btn"):
            self.play_btn.setText("▶ Play")

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
        # draw now, then start the frame timer: frames never pile up and the
        # window stays responsive at any frame rate
        self.viewer_panel.canvas.draw()
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
        self._busy_cursor(True)
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
            self._busy_cursor(False)
        self.log(f"written: {path} ({len(vis)} frames at {fps:g} frames/s, "
                 f"{fmt_duration(time.perf_counter() - t0)})")

    # =================================================================
    # Running
    # =================================================================
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

    @staticmethod
    def _existing(bases, suffixes):
        return [b + x for b in bases for x in suffixes if os.path.exists(b + x)]

    def _set_run_mode(self, mode):
        if mode != self.run_mode:
            self.run_mode = mode
            self.schedule_stack()
            self._run_mode_changed()

    def run_single(self):
        if self._busy():
            return
        self._set_run_mode("single")
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

    def run_sweep(self, ask=True):
        if self._busy():
            return
        self._set_run_mode("sweep")
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
        if ask and not messagebox.askokcancel("Start sweep?", self._describe_sweep(p)):
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
            self._text(self.bg_result, "Running…")

    def _busy(self):
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Busy", "A calculation is running. Wait or "
                                        "press Cancel.")
            return True
        return False

    def _start(self, jobs, batch):
        self.cancel_ev.clear()
        self._set_running(True)
        self._set_progress(0)
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
        """Worker thread: only numpy here; everything for the window goes
        through the queue (Qt widgets may only be touched by the main thread).

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
        """Called every 100 ms by a timer: handle what the worker sent."""
        try:
            while True:
                kind, a, b = self.queue.get_nowait()
                if kind == "progress":
                    self._set_progress(a)
                    el = time.perf_counter() - self._pending["t0"]
                    self._set_status(f"{b}: {100 * a:.0f} %  "
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

    def _finish(self, kind, a, b):
        self._set_running(False)
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
        self.log(f"{word} — {msg}", "head" if kind == "done" else "warn")
        self._set_progress(1.0 if kind == "done" else 0)
        self._set_status(f"{word} — {msg}")
        self.schedule_preview()          # time estimates now use this speed

    def cancel(self):
        if self.worker is not None and self.worker.is_alive():
            self.cancel_ev.set()
            self._set_status("cancelling…")

    # =================================================================
    # Export
    # =================================================================
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

    def figure_panels(self):
        """figure-file suffix -> plot panel."""
        return {"dRR": self.drr_panel, "stack": self.stack_panel,
                "components": self.comp_panel, "kernels": self.kern_panel,
                "strain_map": self.strain_panel, "background": self.bg_panel,
                "sweep_viewer": self.viewer_panel}

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
                    panels.append((key, self.figure_panels()[key]))
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
                                          "save' on the Output page.")
            return
        p = filedialog.asksaveasfilename(
            title="Export run — base name (extensions are added from the "
                  "Output page choices)",
            initialdir=opts["folder"] or None,
            initialfile=S.format_name(run["label"].replace(" ", "_")),
            filetypes=[("All files", "*")])
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
            filetypes=[("All files", "*")])
        if not p:
            return
        try:
            files = O.write_sweep(b["runs"], os.path.splitext(p)[0], opts, b["info"])
            self.log("written: " + ", ".join(files))
        except Exception as e:
            messagebox.showerror("Export sweep", f"{type(e).__name__}: {e}")

    # =================================================================
    # Sessions and imports
    # =================================================================
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
                    run_mode=self.run_mode,
                    plot_styles={name: {str(k): v for k, v in pan.styles.items()}
                                 for name, pan in self.figure_panels().items()})

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
        for name, pan in self.figure_panels().items():
            pan.styles = {int(k): v for k, v in
                          st.get("plot_styles", {}).get(name, {}).items()}
        self.layers = [S.normalise_layer(l) for l in st["layers"]]
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
        self.run_mode = st.get("run_mode", "single")
        self._run_mode_changed()
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
        self.title(f"{APP_NAME} — {os.path.basename(self.session_path)}")

    def save_session_as(self):
        p = filedialog.asksaveasfilename(
            title="Save session", defaultextension=".json",
            filetypes=[("PiSES session", "*.json")])
        if p:
            self.session_path = p
            self.save_session()

    def open_session(self, path=None):
        p = path or filedialog.askopenfilename(
            title="Open session", filetypes=[("PiSES session", "*.json"),
                                             ("All", "*")])
        if not p:
            return
        try:
            st = S.load_session(p)
        except Exception as e:
            messagebox.showerror("Open session", f"{type(e).__name__}: {e}")
            return
        self._apply_state(st)
        self.session_path = p
        self.title(f"{APP_NAME} — {os.path.basename(p)}")
        self.log(f"session loaded: {p}")

    def new_session(self):
        if not messagebox.askyesno("New session", "Discard the current inputs and "
                                                  "start from the example stack?"):
            return
        st = dict(config=S.default_config_entries(), flags=S.default_flags(),
                  layers=[])
        self._apply_state(st)
        self.session_path = None
        self.title(APP_LONG)
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

    def import_params(self, path=None):
        p = path or filedialog.askopenfilename(
            title="Import parameter file (CFG = dict(...), SAMPLE = [...])",
            filetypes=[("Text / Python", "*.txt *.py *.cfg *.dat"), ("All", "*")])
        if not p:
            return

        def read(path_, mat_dir):
            try:
                cfg, _ = S.resolve_config(self._cfg_entries(), self._flags())
            except S.InputError:
                cfg = None
            return PI.read_file(path_, cfg, mat_dir, self.cache)
        return W.ImportReviewDialog(self, p, read, self.cache)

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
                     f"(marked ● lit.; Layers → Literature… lists them)", "warn")
        if n_miss:
            messagebox.showwarning(
                "Imported — please complete",
                f"{n_miss} value(s) were not in the file and are still missing "
                f"(listed in red in the console). Fill them in before running.")

    def import_conf(self, path=None):
        p = path or filedialog.askopenfilename(
            title="Import fs-sonar conf file",
            filetypes=[("conf file", "*.txt"), ("All", "*")])
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

    # =================================================================
    # Misc
    # =================================================================
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
                     dimensionless="dñ/dη, p, CFL", viscosity="Viscosity η",
                     attenuation="Attenuation α", frequency="Frequency")
        for kind, us in U.UNITS.items():
            base = U.base_unit(kind)
            conv = ", ".join(
                f"1 {u} = {f:g} {base}" if f != "eV" else f"λ[nm] = {U.HC_EV_NM}/E[eV]"
                for u, f in us.items() if u != base)
            lines.append(f"{names.get(kind, kind)}: model unit {base}"
                         + (f";  {conv}" if conv else ""))
        messagebox.showinfo("Units and conversions", "\n".join(lines))

    def show_help(self):
        messagebox.showinfo("How to use", HELP_TEXT)
