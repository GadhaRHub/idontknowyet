"""Writing results to disk according to the user's Output settings, and
estimating how many files / how many bytes a run or a sweep will produce.

Everything here is GUI-independent: `opts` is a plain dict (see
default_options()), so the same code can be called from a script.
"""

import datetime
import json
import math
import os

import numpy as np

import diffr_model as M
import sample_input as S

TIME_UNITS = {"ps": 1.0, "fs": 1e3, "ns": 1e-3}       # multiply ps by this
DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t", "space": " "}
COMPONENTS = [("drr_strain", "strain", "comp_strain"),
              ("drr_disp", "displacement", "comp_disp"),
              ("drr_lattice", "lattice_T", "comp_lattice"),
              ("drr_electron", "electron_T", "comp_electron")]
FIG_FORMATS = ["png", "pdf", "svg"]
# rough size of one saved figure, for the estimate only
_FIG_BYTES = {"png": 120_000, "pdf": 60_000, "svg": 400_000}

RUN_FIELDS = ("run", "date", "time")
SWEEP_FIELDS = RUN_FIELDS + ("material", "layer", "d", "unit", "i", "n")
COMBINED_FIELDS = RUN_FIELDS + ("material", "layer", "start", "end", "step",
                                "unit", "n")


def default_options():
    return dict(
        folder=os.getcwd(),
        auto_single=False, single_name="dRR_run{run}_{date}-{time}",
        auto_sweep=False, sweep_name="dRR_{material}_{d}{unit}",
        combined=False, combined_name="dRR_{material}_sweep_{start}-{end}{unit}",
        csv=True, comp_strain=True, comp_disp=True, comp_lattice=True,
        comp_electron=True, csv_time_unit="ps", csv_delimiter="comma",
        csv_format="%.8e", csv_header=True,
        npz=True, npz_profiles=True, npz_strain=True,
        meta_json=True,
        figures=False, fig_format="png", fig_dpi="150", fig_drr=True,
        fig_stack=False, fig_extra=False,
        ask_overwrite=True,
        # background subtraction (Background tab)
        bg_enabled=False, bg_use_cut=True, bg_cut="10", bg_cut_unit="ps",
        bg_force_decay=True, bg_before="NaN (empty)", bg_col_sub=True,
        bg_col_fit=False, bg_plot_main="original ΔR/R",
    )


def stamp():
    """{date} and {time} fields for file names."""
    now = datetime.datetime.now()
    return dict(date=now.strftime("%Y%m%d"), time=now.strftime("%H%M%S"))


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def check_options(opts):
    """Raise ValueError for settings that cannot work."""
    if opts["csv"]:
        try:
            opts["csv_format"] % 1.0
        except (TypeError, ValueError):
            raise ValueError(f"CSV number format {opts['csv_format']!r} is not "
                             f"a valid format such as %.8e or %.6g")
    try:
        if int(opts["fig_dpi"]) <= 0:
            raise ValueError
    except ValueError:
        raise ValueError("Figure dpi must be a positive whole number")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def run_meta(res, label, inputs, elapsed=None):
    """Everything needed to reproduce a run, JSON-serialisable."""
    cfg = {k: v for k, v in res["cfg"].items()}
    return dict(label=label, created=datetime.datetime.now().isoformat(
                    timespec="seconds"),
                runtime_s=elapsed, simulation_runtime_s=elapsed, cfg=cfg,
                stack=[[n, t] for n, t in res["stack"]],
                transducer=res.get("transducer"),
                R_probe=float(abs(res["sol_probe"]["r"]) ** 2),
                R_pump=float(abs(res["sol_pump"]["r"]) ** 2),
                placeholders=M.placeholder_summary(res.get("materials", {})),
                materials_model_units=S.materials_as_json(res["stack"],
                                                          res["materials"]),
                background=bg_meta(res),
                gui_inputs=inputs)


def bg_meta(res):
    """Background-subtraction settings and result of a run, or None."""
    bg = res.get("bg")
    if not bg:
        return None if not res.get("bg_error") else dict(error=res["bg_error"])
    p = bg["params"]
    return dict(model="a*exp(b*t) + c*exp(d*t), t in ps",
                params=None if p is None else dict(zip("abcd", p)),
                fit_failed_mean_subtracted=p is None,
                fit_only_after_ps=bg["cut_ps"], points_used=bg["n_points"],
                force_decay=bg["force_decay"], before_cutoff=bg["before"],
                runtime_s=res.get("bg_time"))


def _csv(path, cols, names, opts):
    delim = DELIMITERS[opts["csv_delimiter"]]
    np.savetxt(path, np.column_stack(cols), delimiter=delim,
               header=delim.join(names) if opts["csv_header"] else "",
               comments="", fmt=opts["csv_format"], encoding="utf-8")


def write_run(res, base, opts, meta):
    """Write one run as chosen in opts. Returns the list of files written."""
    files = []
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    tu = opts["csv_time_unit"]
    if opts["csv"]:
        cols = [res["t_ps"] * TIME_UNITS[tu], res["drr"]]
        names = [f"t_{tu}", "dR_over_R"]
        for key, name, opt in COMPONENTS:
            if opts[opt]:
                cols.append(res[key])
                names.append(name)
        if res.get("bg"):
            if opts.get("bg_col_sub", True):
                cols.append(res["bg"]["sub"])
                names.append("dR_over_R_minus_bg")
            if opts.get("bg_col_fit"):
                cols.append(res["bg"]["fit"])
                names.append("bg_fit")
        _csv(base + ".csv", cols, names, opts)
        files.append(base + ".csv")
    if opts["npz"]:
        payload = dict(t_ps=res["t_ps"], drr=res["drr"],
                       **{k: res[k] for k, _, _ in COMPONENTS},
                       meta=json.dumps(meta))
        if opts["npz_profiles"]:
            payload.update(z_nm=res["z_nm"], f_eta=res["f_eta"], f_T=res["f_T"],
                           W=res["W"])
        if res.get("bg"):
            p = res["bg"]["params"]
            payload.update(drr_minus_bg=res["bg"]["sub"], bg_fit=res["bg"]["fit"],
                           bg_params=np.array(p if p is not None else [np.nan] * 4))
        if opts["npz_strain"] and res.get("eta_zt") is not None:
            payload.update(eta_zt=res["eta_zt"], eta_z_nm=res["eta_z_nm"],
                           eta_t_ps=res["eta_t_ps"])
        np.savez_compressed(base + ".npz", **payload)
        files.append(base + ".npz")
    if opts["meta_json"]:
        with open(base + "_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        files.append(base + "_meta.json")
    return files


def write_sweep(runs, base, opts, info):
    """One file with every thickness of a sweep side by side."""
    files = []
    os.makedirs(os.path.dirname(base) or ".", exist_ok=True)
    t = runs[0]["res"]["t_ps"]
    tu = opts["csv_time_unit"]
    cols = lambda k: np.column_stack([r["res"][k] for r in runs])
    names = [f"t_{tu}"] + [f"dR_over_R_d={r['sweep_value']:g}"
                           f"{S.FILE_UNIT_NAMES.get(r['sweep_unit'], r['sweep_unit'])}"
                           for r in runs]
    has_bg = all(r["res"].get("bg") for r in runs)
    data = [t * TIME_UNITS[tu], cols("drr")]
    if has_bg and opts.get("bg_col_sub", True):
        data.append(np.column_stack([r["res"]["bg"]["sub"] for r in runs]))
        names += [n.replace("dR_over_R_", "dR_over_R_minus_bg_")
                  for n in names[1:len(runs) + 1]]
    if opts["csv"] or not opts["npz"]:
        _csv(base + ".csv", data, names, opts)
        files.append(base + ".csv")
    if opts["npz"]:
        extra = {}
        if has_bg:
            extra = dict(drr_minus_bg=np.column_stack([r["res"]["bg"]["sub"]
                                                       for r in runs]),
                         bg_fit=np.column_stack([r["res"]["bg"]["fit"]
                                                 for r in runs]))
        np.savez_compressed(
            base + ".npz", t_ps=t, drr=cols("drr"),
            thickness_nm=np.array([r["sweep_nm"] for r in runs]),
            **{k: cols(k) for k, _, _ in COMPONENTS}, **extra,
            labels=np.array([r["label"] for r in runs]),
            meta=json.dumps(dict(swept=info, gui_inputs=runs[0]["inputs"],
                                 runtime_s=[r.get("elapsed") for r in runs],
                                 background=[bg_meta(r["res"]) for r in runs])))
        files.append(base + ".npz")
    return files


# ---------------------------------------------------------------------------
# Estimates
# ---------------------------------------------------------------------------

def grid_size(cfg, d_stack_nm):
    """(N depth cells, nt output times) the solver will use."""
    N = int(round((d_stack_nm + cfg["substrate_model_nm"] + cfg["sponge_nm"])
                  / cfg["dz_nm"]))
    nt = len(np.arange(cfg["t_min_ps"], cfg["t_max_ps"] + 1e-9, cfg["dt_out_ps"]))
    return N, nt


def run_work(cfg, d_stack_nm, v_max):
    """Number of grid-cell updates of one run (for time estimates)."""
    N, nt = grid_size(cfg, d_stack_nm)
    dt_cfl = cfg["cfl"] * cfg["dz_nm"] * 1e-9 / v_max
    n_sub = max(1, math.ceil(cfg["dt_out_ps"] * 1e-12 / dt_cfl))
    return N * nt * n_sub


def run_files(opts):
    """File suffixes one run produces with these options."""
    out = []
    if opts["csv"]:
        out.append(".csv")
    if opts["npz"]:
        out.append(".npz")
    if opts["meta_json"]:
        out.append("_meta.json")
    return out


def estimate_run_bytes(opts, N, nt, strain=False):
    b = 0
    if opts["csv"]:
        width = len(opts["csv_format"] % -1.2345e-5) + 1
        ncol = 2 + sum(bool(opts[o]) for _, _, o in COMPONENTS)
        if opts.get("bg_enabled"):
            ncol += bool(opts.get("bg_col_sub")) + bool(opts.get("bg_col_fit"))
        b += nt * ncol * width + 200
    if opts["npz"]:
        # compressed float arrays rarely shrink much; count them in full
        b += 8 * nt * 6 + (8 * N * 4 if opts["npz_profiles"] else 0) + 2000
        if strain and opts["npz_strain"]:
            nst = len(range(0, nt, max(1, nt // 800)))
            nsz = len(range(0, N, max(1, N // 2000)))
            b += 4 * nst * nsz
        b += 20_000                                     # metadata string
    if opts["meta_json"]:
        b += 20_000
    return b


def estimate_combined_bytes(opts, nt, n_runs):
    b = 0
    if opts["csv"] or not opts["npz"]:
        k = 2 if (opts.get("bg_enabled") and opts.get("bg_col_sub")) else 1
        b += nt * (1 + k * n_runs) * (len(opts["csv_format"] % -1.2345e-5) + 1)
    if opts["npz"]:
        b += 8 * nt * (1 + 5 * n_runs) + 20_000
    return b


def figure_bytes(opts, n_figs):
    return n_figs * _FIG_BYTES.get(opts["fig_format"], 150_000)
