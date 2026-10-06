"""GUI-independent input handling for the diffR model.

A layer, as the GUI edits it, is a plain dict of *strings* (exactly what the
user typed) plus the unit chosen for each quantity. That keeps sessions
lossless: a saved session reloads with the same numbers in the same units.

resolve_config() and resolve_layers() turn those strings into the model's
internal units (nm, ps, SI) and build the (stack, materials) the solver
takes, reporting every conversion and every literature placeholder.
"""

import json
import os
import re

import numpy as np

import diffr_model as M
import units as U


class InputError(Exception):
    """Invalid or missing user input; .errors lists every problem found."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

# Experiment / numerics: key in diffr_model.CFG, label, unit kind,
# default value (from the notebook's CFG), default unit, help text
CFG_FIELDS = [
    ("lambda_pump_nm", "Pump wavelength", "wavelength", "400", "nm",
     "pump wavelength"),
    ("lambda_probe_nm", "Probe wavelength", "wavelength", "514", "nm",
     "probe wavelength"),
    ("pump_fluence_J_m2", "Pump fluence", "fluence", "1.0", "J/m²",
     "scales the absolute amplitude of the absorbed energy W(z)"),
    ("pulse_fwhm_ps", "Pulse duration (FWHM)", "time", "0.20", "ps",
     "temporal width of the Gaussian laser pulse"),
    ("tau_th_ps", "Thermal decay time τ_th", "time", "500", "ps",
     "time for the lattice temperature to decay"),
    ("t_min_ps", "Start time t_min", "time", "-10", "ps",
     "negative, to capture the baseline before the pump arrives"),
    ("t_max_ps", "End time t_max", "time", "400", "ps",
     "length of the simulation"),
    ("dt_out_ps", "Output time step Δt", "time", "0.05", "ps",
     "output time axis spacing"),
    ("dz_nm", "Grid step dz", "grid", "1.0", "nm",
     "spatial step"),
    ("substrate_model_nm", "Modelled substrate depth", "grid", "4000", "nm",
     "must be > v_sound(substrate) × t_max"),
    ("sponge_nm", "Absorbing sponge layer", "grid", "800", "nm",
     "absorbing layer after the substrate chunk that damps the wave"),
    ("cfl", "CFL safety factor", "dimensionless", "0.40", "–",
     "leapfrog stability: dt = cfl · dz / v_max, must be < 1"),
]

PHYSICS_FLAGS = [
    ("include_strain", "Photoelastic (bulk strain wave)"),
    ("include_displacement", "Interface / surface displacement"),
    ("include_lattice_T", "Thermo-optic, lattice temperature"),
    ("include_electron_T", "Thermo-optic, electron temperature"),
]

# Real-valued layer properties:
# key, label, unit kind, default unit, required, value used when left blank
REAL_PROPS = [
    ("rho", "Density ρ", "density", "kg/m³", True, None),
    ("v", "Longitudinal sound velocity v", "velocity", "m/s", True, None),
    ("Cp", "Isobaric heat capacity Cp", "heat_capacity", "J/(kg·K)", True, None),
    ("alpha_lin", "Linear thermal expansion α", "expansion", "1e-6/K (ppm/K)",
     True, None),
    ("B_bulk", "Bulk modulus B", "modulus", "GPa", True, None),
    ("Ce", "Electron heat capacity Ce", "e_heat_capacity", "J/(m³·K)",
     False, 0.0),
    ("tau_ep_ps", "Electron–phonon time τ_ep", "tau", "ps", False, 1.0),
]

# Complex layer properties (entered as Re + Im), same columns
COMPLEX_PROPS = [
    ("dn_deta", "Photoelastic dñ/dη", "dimensionless", "–", True, None),
    ("pe", "Photoelastic coefficient p", "dimensionless", "–", True, None),
    ("dn_dT", "Thermo-optic dñ/dT", "per_kelvin", "1/K", True, None),
    ("dn_dTe", "Electronic dñ/dTₑ", "per_kelvin", "1/K", False, 0j),
]

PROP_INFO = {p[0]: p for p in REAL_PROPS + COMPLEX_PROPS}
NK_KEYS = ["n_pump", "k_pump", "n_probe", "k_probe"]
KFILL_KEYS = ["kfill_pump", "kfill_probe"]
FILE_UNITS = ["auto", "nm", "µm", "Å", "m", "eV"]
_FILE_UNIT_TO_MODEL = {"nm": "nm", "µm": "um", "Å": "A", "m": "m", "eV": "eV"}


def new_layer(material="", d="", d_unit="nm"):
    """An empty layer dict with every field present."""
    lay = dict(material=material, d=str(d), d_unit=d_unit,
               nk_mode="typed", nk_file="", nk_file_unit="auto",
               pe_mode="dn_deta", lit={})
    for k in NK_KEYS + KFILL_KEYS:
        lay[k] = ""
    for key, _, kind, unit, _, _ in REAL_PROPS:
        lay[key], lay[key + "_unit"] = "", unit
    for key, _, kind, unit, _, _ in COMPLEX_PROPS:
        lay[key + "_re"], lay[key + "_im"], lay[key + "_unit"] = "", "", unit
    return lay


def normalise_layer(lay):
    """Fill in any keys missing from an older session file."""
    out = new_layer()
    out.update(lay)
    out["lit"] = dict(lay.get("lit", {}))
    return out


def _num_str(x):
    return f"{x:.10g}"


def _prop_fields(key):
    """The string keys in a layer dict that hold one quantity."""
    if key in ("k_pump", "k_probe", "kfill_pump", "kfill_probe"):
        return [key]
    if key in {p[0] for p in COMPLEX_PROPS}:
        return [key + "_re", key + "_im", key + "_unit"]
    return [key, key + "_unit"]


def _set_prop(lay, key, value, unit=None):
    """Write a value given in internal units into a layer, in `unit`
    (defaults to the field's current unit). Returns the fields written."""
    info = PROP_INFO[key]
    unit = unit or lay[key + "_unit"]
    v = U.from_base(value, info[2], unit)
    if key in {p[0] for p in COMPLEX_PROPS}:
        v = complex(v)
        lay[key + "_re"], lay[key + "_im"] = _num_str(v.real), _num_str(v.imag)
    else:
        lay[key] = _num_str(float(np.real(v)))
    lay[key + "_unit"] = unit
    return _prop_fields(key)


def _is_empty(lay, key):
    return all(not str(lay[f]).strip() for f in _prop_fields(key)
               if not f.endswith("_unit"))


def literature_names():
    return sorted(M.LITERATURE)


def apply_literature(lay, only_empty=False, table=None):
    """Pre-fill a layer from a literature table (default
    diffr_model.LITERATURE), keyed by material name.

    Values go in each field's default display unit. The filled strings are
    remembered in lay["lit"], so a value still equal to them at run time is
    reported as a literature placeholder (as build_sample does). Returns the
    list of quantities filled.
    """
    lit = (M.LITERATURE if table is None else table).get(lay["material"].strip())
    if not lit:
        return []
    filled = []
    for key, val in lit.items():
        if key == "k":
            for f in ("k_pump", "k_probe", "kfill_pump", "kfill_probe"):
                if only_empty and str(lay[f]).strip():
                    continue
                lay[f] = _num_str(val)
                lay["lit"][f] = lay[f]
            filled.append("k")
            continue
        if key not in PROP_INFO:
            continue
        if only_empty and not _is_empty(lay, key):
            continue
        fields = _set_prop(lay, key, val, PROP_INFO[key][3])
        for f in fields:
            lay["lit"][f] = lay[f]
        filled.append(key)
    if "dn_deta" in lit and (not only_empty or lay["pe_mode"] == "dn_deta"):
        lay["pe_mode"] = "dn_deta"
    return filled


def is_placeholder(lay, key):
    """True when quantity `key` still holds its literature pre-fill."""
    fields = _prop_fields(key)
    lit = lay.get("lit", {})
    return bool(fields) and all(f in lit and str(lay[f]) == lit[f]
                                for f in fields)


def placeholder_rows(layers):
    """Every value that still holds its literature pre-fill, as
    (layer number, material, quantity, value text, unit) rows."""
    rows = []
    for i, lay in enumerate(layers):
        file_mode = lay["nk_mode"] == "file"
        keys = [k for k in PROP_INFO
                if not (k == "pe" and lay["pe_mode"] != "pe")
                and not (k == "dn_deta" and lay["pe_mode"] == "pe")]
        keys += (["kfill_pump", "kfill_probe"] if file_mode
                 else ["k_pump", "k_probe"])
        for key in keys:
            if not is_placeholder(lay, key):
                continue
            if key in ("k_pump", "k_probe", "kfill_pump", "kfill_probe"):
                w = key.split("_")[1]
                label = (f"k at {w} (used only if the file has no k column)"
                         if file_mode else f"k at {w}")
                rows.append((i + 1, lay["material"], label, lay[key], ""))
                continue
            label = PROP_INFO[key][1]
            if key in {p[0] for p in COMPLEX_PROPS}:
                val = f"{lay[key + '_re'] or 0} + {lay[key + '_im'] or 0}i"
            else:
                val = lay[key]
            rows.append((i + 1, lay["material"], label, val, lay[key + "_unit"]))
    return rows


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _float(s, what, errors):
    s = str(s).strip()
    if not s:
        errors.append(f"{what}: empty")
        return None
    try:
        return float(s)
    except ValueError:
        errors.append(f"{what}: '{s}' is not a number")
        return None


def parse_thickness(lay):
    """Thickness in nm, or None if blank/invalid (no error reporting)."""
    try:
        return U.to_base(float(lay["d"]), "thickness", lay["d_unit"])
    except (ValueError, KeyError):
        return None


def range_values(start, end, step, max_n=10000):
    """start, start+step, ... up to end (included when it lies on the grid).

    Values are rounded to 10 significant digits so 0.1-steps do not give
    names like 0.30000000000000004.
    """
    if step <= 0:
        raise ValueError("the increment must be > 0")
    if end < start:
        raise ValueError("the end must be >= the start")
    n = int(np.floor((end - start) / step + 1e-9)) + 1
    if n > max_n:
        raise ValueError(f"that gives {n} runs (more than {max_n})")
    return [float(f"{start + i * step:.10g}") for i in range(n)]


_BAD_FILE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# units written into file names without non-ASCII characters
FILE_UNIT_NAMES = {"µm": "um", "Å": "A"}


def format_name(template, **fields):
    """Fill a file-name template such as 'dRR_{material}_{d}{unit}' and make
    the result safe as a Windows/Linux file name. Raises ValueError for an
    unknown {field}, naming the fields that can be used."""
    try:
        name = template.format(**fields)
    except KeyError as e:
        avail = " ".join("{%s}" % k for k in fields)
        raise ValueError(f"unknown field {{{e.args[0]}}} in the file name; "
                         f"available here: {avail}") from None
    except (IndexError, ValueError) as e:
        raise ValueError(f"file name template: {e}") from None
    name = _BAD_FILE_CHARS.sub("_", name).strip().rstrip(".")
    if not name:
        raise ValueError("the file name is empty")
    return name


def parse_value_list(text):
    """'100, 200, 350' or 'start:stop:step' (stop included) -> list of floats."""
    text = text.strip()
    if ":" in text:
        parts = [float(p) for p in text.split(":")]
        if len(parts) != 3 or parts[2] == 0:
            raise ValueError("use start:stop:step with a non-zero step")
        a, b, st = parts
        n = int(np.floor((b - a) / st + 1e-9)) + 1
        if n < 1:
            raise ValueError("start:stop:step gives no values")
        return [a + i * st for i in range(n)]
    vals = [float(p) for p in text.replace(";", ",").replace(" ", ",").split(",")
            if p.strip()]
    if not vals:
        raise ValueError("no values given")
    return vals


# ---------------------------------------------------------------------------
# Experiment configuration
# ---------------------------------------------------------------------------

def default_config_entries():
    return {k: dict(value=v, unit=u) for k, _, _, v, u, _ in CFG_FIELDS}


def default_flags():
    return {k: bool(M.CFG.get(k, True)) for k, _ in PHYSICS_FLAGS}


def resolve_config(entries, flags):
    """GUI entries -> (cfg in model units, report lines). Raises InputError."""
    errors, report, cfg = [], [], dict(M.CFG)
    for key, label, kind, _, _, _ in CFG_FIELDS:
        e = entries[key]
        x = _float(e["value"], label, errors)
        if x is None:
            continue
        try:
            v = U.to_base(x, kind, e["unit"])
        except ValueError as ex:
            errors.append(f"{label}: {ex}")
            continue
        cfg[key] = float(v)
        report.append(f"  {label:<28} {e['value']} {e['unit']:<8} -> "
                      f"{U.fmt(cfg[key])} {U.base_unit(kind)}")
    cfg.update({k: bool(flags[k]) for k, _ in PHYSICS_FLAGS})
    if not errors:
        checks = [
            (cfg["lambda_pump_nm"] > 0, "Pump wavelength must be > 0"),
            (cfg["lambda_probe_nm"] > 0, "Probe wavelength must be > 0"),
            (cfg["pump_fluence_J_m2"] > 0, "Pump fluence must be > 0"),
            (cfg["pulse_fwhm_ps"] > 0, "Pulse duration must be > 0"),
            (cfg["tau_th_ps"] > 0, "Thermal decay time must be > 0"),
            (cfg["t_max_ps"] > cfg["t_min_ps"], "t_max must be > t_min"),
            (cfg["dt_out_ps"] > 0, "Output time step must be > 0"),
            (cfg["dt_out_ps"] < cfg["t_max_ps"] - cfg["t_min_ps"],
             "Output time step must be smaller than t_max - t_min"),
            (cfg["dz_nm"] > 0, "Grid step must be > 0"),
            (cfg["substrate_model_nm"] > 0, "Modelled substrate depth must be > 0"),
            (cfg["sponge_nm"] > 0, "Sponge layer must be > 0"),
            (0 < cfg["cfl"] < 1, "CFL factor must be between 0 and 1"),
        ]
        errors += [msg for ok, msg in checks if not ok]
    if errors:
        raise InputError(errors)
    return cfg, report


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

class DispersionCache:
    """Loads n,k tables once; reloads when the file changes on disk."""

    def __init__(self):
        self._c = {}

    def get(self, path, unit="auto"):
        path = os.path.normpath(path)
        mtime = os.path.getmtime(path)
        key = (path, unit)
        hit = self._c.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
        u = None if unit == "auto" else _FILE_UNIT_TO_MODEL[unit]
        tab = M.load_dispersion(path, u)
        self._c[key] = (mtime, tab)
        return tab


def unit_note(tab):
    """Why column 1 of a dispersion table was read in the unit it was
    (empty when the unit was given explicitly or is plainly nm)."""
    if not tab["why"]:
        return ""
    why = re.sub(r"pass units=\{.*?\}", "set the file's column-1 unit in the "
                 "layer editor", tab["why"])
    return f"column 1 of {os.path.basename(tab['path'])} read as {tab['unit']}: {why}"


def layer_nk(lay, cfg, cache, tag="layer"):
    """(n_pump, n_probe, notes) for one layer; raises InputError."""
    errors, notes, out = [], [], {}
    lam = {"pump": cfg["lambda_pump_nm"], "probe": cfg["lambda_probe_nm"]}
    if lay["nk_mode"] == "file":
        path = lay["nk_file"].strip()
        if not path:
            raise InputError([f"{tag}: no n,k file chosen"])
        if not os.path.isfile(path):
            raise InputError([f"{tag}: n,k file not found: {path}"])
        try:
            tab = cache.get(path, lay["nk_file_unit"])
        except Exception as e:
            raise InputError([f"{tag}: cannot read {path}: {e}"])
        if tab["why"]:
            notes.append(unit_note(tab))
        for w in ("pump", "probe"):
            try:
                n, k, note = M.interp_nk(tab, lam[w])
            except M.MissingMaterialData as e:
                errors.append(f"{tag}: {e}")
                continue
            if k is None:
                k = _float(lay[f"kfill_{w}"],
                           f"{tag}: k at {w} (file has no k column)", errors)
                if k is None:
                    continue
                note += ", k typed"
            out[w] = complex(n, k)
            notes.append(f"n_{w} = {U.fmt(out[w])} from "
                         f"{os.path.basename(path)} ({note})")
    else:
        for w in ("pump", "probe"):
            n = _float(lay[f"n_{w}"], f"{tag}: n at {w}", errors)
            k = _float(lay[f"k_{w}"], f"{tag}: k at {w}", errors)
            if n is not None and k is not None:
                out[w] = complex(n, k)
                notes.append(f"n_{w} = {U.fmt(out[w])} (typed)")
    for w, v in out.items():
        if v.real <= 0:
            errors.append(f"{tag}: n at {w} must be > 0 (got {v.real:g})")
        if v.imag < 0:
            notes.append(f"!! k at {w} is negative ({v.imag:g}); the model "
                         f"uses n + ik with k >= 0 for absorption")
    if errors:
        raise InputError(errors)
    return out["pump"], out["probe"], notes


def resolve_layers(layers, cfg, cache=None):
    """Layer dicts -> (stack, materials, report lines, warnings).

    Converts every value to model units. Raises InputError listing every
    problem. materials[key]._placeholder holds the quantities that are still
    literature pre-fills, as build_sample does.
    """
    cache = cache or DispersionCache()
    if not layers:
        raise InputError(["The stack is empty; add at least the substrate."])
    errors, report, warnings, built = [], [], [], []
    for i, lay in enumerate(layers):
        name = lay["material"].strip()
        tag = f"Layer {i + 1} ({name or 'unnamed'})"
        is_sub = i == len(layers) - 1
        if not name:
            errors.append(f"{tag}: material name is empty")
        report.append(f"{tag}" + ("  [substrate, semi-infinite]" if is_sub else ""))
        ph = set()

        d = None
        if not is_sub:
            x = _float(lay["d"], f"{tag}: thickness", errors)
            if x is not None:
                d = U.to_base(x, "thickness", lay["d_unit"])
                if d <= 0:
                    errors.append(f"{tag}: thickness must be > 0")
                report.append(f"    thickness   {lay['d']} {lay['d_unit']} -> "
                              f"{U.fmt(d)} nm")
        try:
            n_pu, n_pr, notes = layer_nk(lay, cfg, cache, tag)
            report += [f"    {s}" for s in notes]
            for w in ("pump", "probe"):
                if lay["nk_mode"] == "typed":
                    lit = is_placeholder(lay, f"k_{w}")
                else:   # the typed k only counts when the file has no k column
                    lit = (is_placeholder(lay, f"kfill_{w}") and
                           any(n.startswith(f"n_{w}") and "k typed" in n
                               for n in notes))
                if lit:
                    ph.add(f"k_{w}")
        except InputError as e:
            errors += e.errors
            n_pu = n_pr = None

        vals = {}
        for key, label, kind, _, required, default in REAL_PROPS:
            s, unit = str(lay[key]).strip(), lay[key + "_unit"]
            if not s and not required:
                vals[key] = default
                report.append(f"    {key:<10}  (blank) -> {U.fmt(default)} "
                              f"{U.base_unit(kind)} (default)")
                continue
            x = _float(s, f"{tag}: {label}", errors)
            if x is None:
                continue
            vals[key] = U.to_base(x, kind, unit)
            lit = is_placeholder(lay, key)
            if lit:
                ph.add(key)
            report.append(f"    {key:<10}  {s} {unit} -> {U.fmt(vals[key])} "
                          f"{U.base_unit(kind)}" + ("   * literature" if lit else ""))
        for key in ("rho", "v", "Cp"):
            if key in vals and vals[key] <= 0:
                errors.append(f"{tag}: {PROP_INFO[key][1]} must be > 0")
        if "B_bulk" in vals and vals["B_bulk"] < 0:
            errors.append(f"{tag}: bulk modulus must be >= 0")
        if "Ce" in vals and vals["Ce"] < 0:
            errors.append(f"{tag}: electron heat capacity must be >= 0")
        if "tau_ep_ps" in vals and vals["tau_ep_ps"] <= 0:
            errors.append(f"{tag}: τ_ep must be > 0")

        for key, label, kind, _, required, default in COMPLEX_PROPS:
            if key == "pe" and lay["pe_mode"] != "pe":
                continue
            if key == "dn_deta" and lay["pe_mode"] == "pe":
                continue
            re_s, im_s = str(lay[key + "_re"]).strip(), str(lay[key + "_im"]).strip()
            unit = lay[key + "_unit"]
            if not re_s and not im_s:
                if required:
                    errors.append(f"{tag}: {label}: empty")
                else:
                    vals[key] = default
                    report.append(f"    {key:<10}  (blank) -> 0 (default)")
                continue
            re_ = _float(re_s or "0", f"{tag}: {label} (Re)", errors)
            im_ = _float(im_s or "0", f"{tag}: {label} (Im)", errors)
            if re_ is None or im_ is None:
                continue
            vals[key] = complex(U.to_base(complex(re_, im_), kind, unit))
            lit = is_placeholder(lay, key)
            if lit:
                ph.add(key)
            report.append(f"    {key:<10}  ({re_s or 0}) + ({im_s or 0})i {unit} -> "
                          f"{U.fmt(vals[key])} {U.base_unit(kind)}"
                          + ("   * literature" if lit else ""))
        if lay["pe_mode"] == "pe" and "pe" in vals and n_pr is not None:
            vals["dn_deta"] = complex(M.pe_to_dn_deta(vals["pe"], n_pr))
            report.append(f"    dn_deta     = -p·n_probe³/2 = {U.fmt(vals['dn_deta'])}")
        built.append((name, d, n_pu, n_pr, vals, ph))

    if errors:
        raise InputError(errors)

    out = [M.Layer(name, d, rho=float(v["rho"]), v=float(v["v"]), Cp=float(v["Cp"]),
                   alpha_lin=float(v["alpha_lin"]), B_bulk=float(v["B_bulk"]),
                   n_pump=n_pu, n_probe=n_pr, dn_deta=complex(v["dn_deta"]),
                   dn_dT=complex(v["dn_dT"]), dn_dTe=complex(v["dn_dTe"]),
                   Ce=float(v["Ce"]), tau_ep_ps=float(v["tau_ep_ps"]))
           for name, d, n_pu, n_pr, v, _ in built]
    stack, materials = M.make_sample(out)
    for (key, _), b in zip(stack, built):
        materials[key]._placeholder = set(b[5])

    # the same consistency checks build_sample makes
    sub = materials[stack[-1][0]]
    if sub.v * cfg["t_max_ps"] * 1e-3 > cfg["substrate_model_nm"]:
        warnings.append(
            f"Modelled substrate ({cfg['substrate_model_nm']:g} nm) is shorter "
            f"than v_sound × t_max = {sub.v * cfg['t_max_ps'] * 1e-3:.0f} nm; "
            f"the strain pulse reaches the sponge before t_max.")
    if cfg.get("include_electron_T", True):
        g = M.build_grid(stack, materials, cfg)
        W, _ = M.absorption_profile(stack, materials, g, cfg)
        j = int(np.argmax([(W[g["idx_half"] == q].max()
                            if (g["idx_half"] == q).any() else 0.0)
                           for q in range(len(g["names"]))]))
        if materials[g["names"][j]].Ce == 0.0:
            warnings.append(
                f"{g['names'][j]} has the highest absorbed energy density but "
                f"Ce = 0, so the electron channel is silent. Give it Ce and "
                f"τ_ep, or untick the electron-temperature contribution.")
    placeholders = {k: sorted(v) for k, m in materials.items()
                    for v in [getattr(m, "_placeholder", set())] if v}
    if placeholders:
        lines = ["These values are LITERATURE PLACEHOLDERS, not measurements:"]
        lines += [f"    {k}: {', '.join(v)}" for k, v in placeholders.items()]
        touched = {q for v in placeholders.values() for q in v}
        if touched & {"v", "rho"}:
            lines.append("    v or rho is a guess -> echo periods (2d/v) and "
                         "Brillouin frequencies are guesses too.")
        if touched & {"dn_deta", "dn_dT", "dn_dTe"}:
            lines.append("    a coupling coefficient is a guess -> the dR/R "
                         "amplitude is not quantitative.")
        if touched & {"k_pump", "k_probe"}:
            lines.append("    k is a guess -> the absorption profile, and "
                         "therefore the strain source, is uncertain.")
        warnings.append("\n".join(lines))
    return stack, materials, report, warnings


def sample_table(stack, materials, cfg):
    """The table build_sample prints, as a string."""
    lam_p = cfg["lambda_probe_nm"]
    rows = [f"{'layer':<12}{'d (nm)':>9}{'n_probe':>19}{'rho':>9}{'v (m/s)':>10}"
            f"{'Z (MRayl)':>11}{'2d/v (ps)':>11}{'f_B (GHz)':>11}"]
    for key, d in stack:
        m = materials[key]
        ph = getattr(m, "_placeholder", set())
        rt = 2 * d * M.nm / m.v / M.ps if d else float("nan")
        fB = 2 * m.n_probe.real * m.v / (lam_p * M.nm) / 1e9
        ds = "inf" if d is None else f"{d:g}"
        npr = f"{m.n_probe.real:.4f}{m.n_probe.imag:+.4f}j"
        rho_s = f"{m.rho:.0f}" + ("*" if "rho" in ph else "")
        v_s = f"{m.v:.0f}" + ("*" if "v" in ph else "")
        rows.append(f"{key:<12}{ds:>9}{npr:>19}{rho_s:>9}{v_s:>10}"
                    f"{m.Z/1e6:>11.2f}{rt:>11.2f}{fB:>11.1f}")
    return "\n".join(rows)


def materials_as_json(stack, materials):
    """Resolved materials (model units) in a JSON-friendly form."""
    out = []
    for key, d in stack:
        m = materials[key]
        row = dict(layer=key, d_nm=d)
        for f in ("rho", "v", "Cp", "alpha_lin", "B_bulk", "n_pump", "n_probe",
                  "dn_deta", "dn_dT", "dn_dTe", "Ce", "tau_ep_ps"):
            x = getattr(m, f)
            row[f] = [x.real, x.imag] if isinstance(x, complex) else float(x)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# conf_file_*.txt import
# ---------------------------------------------------------------------------

def layers_from_conf(path, folder=None):
    """Read a fs-sonar conf file -> (layer dicts, folder used, messages).

    Thickness, dispersion file and peCoef come from the conf. rho, v, Cp,
    alpha, B (and the electronic constants) come from properties.txt / *.prop
    in the materials folder when present, otherwise from LITERATURE (marked
    as placeholders). `folder` overrides the folder written in the conf.
    """
    conf_layers, conf_folder = M.parse_conf(path)
    folder = folder or conf_folder
    msgs = []
    lib = None
    if folder and os.path.isdir(folder):
        lib = M.MaterialLibrary(folder, verbose=False)
        for name, why in sorted(lib.skipped.items()):
            msgs.append(f"{name}.txt skipped: {why}")
    else:
        msgs.append(f"materials folder not readable: {folder!r}; set each "
                    f"layer's n,k file by hand")
    out = []
    for c in conf_layers:
        lay = new_layer(c["material"], "" if c["d_nm"] is None else _num_str(c["d_nm"]))
        lay["nk_mode"] = "file"
        if lib is not None and c["nfile"] in lib.optical:
            lay["nk_file"] = lib.optical[c["nfile"]]["path"]
        else:
            lay["nk_file"] = os.path.join(folder or "", c["nfile"] + ".txt")
            msgs.append(f"{c['material']}: dispersion file {c['nfile']!r} not "
                        f"found in the materials folder")
        if not np.isnan(c["pe"]):
            lay["pe_mode"] = "pe"
            lay["pe_re"], lay["pe_im"] = _num_str(c["pe"]), "0"
        props = lib.props.get(c["material"], {}) if lib is not None else {}
        for key, val in props.items():
            if key in PROP_INFO:
                _set_prop(lay, key, val, U.base_unit(PROP_INFO[key][2]))
            elif key in ("k", "k_pump", "k_probe"):
                for w in ("pump", "probe"):
                    if key in ("k", f"k_{w}"):
                        lay[f"kfill_{w}"] = lay[f"k_{w}"] = _num_str(float(np.real(val)))
        filled = apply_literature(lay, only_empty=True)
        if lay["pe_mode"] == "pe":          # dn_deta comes from peCoef
            filled = [f for f in filled if f != "dn_deta"]
        if filled:
            msgs.append(f"{c['material']}: literature placeholders for "
                        f"{', '.join(filled)}")
        out.append(lay)
    if out and conf_layers[-1]["d_nm"] is not None:
        msgs.append("the last conf layer has a finite thickness; it is used "
                    "as the semi-infinite substrate")
    return out, folder, msgs


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

SESSION_VERSION = 1


def save_session(path, state):
    state = dict(state, version=SESSION_VERSION)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def load_session(path):
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state["layers"] = [normalise_layer(l) for l in state.get("layers", [])]
    cfg = default_config_entries()
    cfg.update(state.get("config", {}))
    state["config"] = cfg
    flags = default_flags()
    flags.update(state.get("flags", {}))
    state["flags"] = flags
    return state
