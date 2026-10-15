"""Help texts and code locations for every input of the GUI.

HELP[key] = dict(
    title = short name,
    what  = what the input is and why it is there,
    fill  = how to fill it in (units, typical values, limits),
    empty = what happens if it is left empty / switched off,
    code  = [(file, regex), ...]  where the value is converted and used.
)

code_snippets() reads the program's own source files when called, so the
line numbers always match the files on disk.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

_REQ = ("Required: the run does not start and the problem is listed in the log "
        "and in a message box.")


def _cfg(key, conv=True):
    """Code locations for an Experiment setting: conversion + use."""
    out = [("diffr_model.py", re.escape(key))]
    if conv:
        out.insert(0, ("sample_input.py", r"cfg\[key\] = float\(v\)"))
    return out


HELP = {
    # ------------------------------------------------------------ experiment
    "lambda_pump_nm": dict(
        title="Pump wavelength",
        what="Wavelength of the pump pulse. The complex refractive index of every "
             "layer is taken at this wavelength, and the transfer-matrix "
             "calculation (tmm) gives the optical field of the pump inside the "
             "stack. From it comes the absorbed energy density W(z), which "
             "drives both the heating and the strain wave.",
        fill="A number with its unit: nm, µm, Å or eV (photon energy, converted "
             "with λ[nm] = 1239.84/E[eV]). It must lie inside the wavelength "
             "range of every n,k file used, because the tables are never "
             "extrapolated.",
        empty=_REQ,
        code=_cfg("lambda_pump_nm") + [("sample_input.py", r'"pump": cfg\["lambda_pump_nm"\]')]),
    "lambda_probe_nm": dict(
        title="Probe wavelength",
        what="Wavelength of the probe pulse. n,k at this wavelength set the "
             "probe field and therefore the sensitivity kernels f_η(z), f_T(z), "
             "f_Te(z) and the interface-displacement response — i.e. how "
             "strongly each depth contributes to ΔR/R. It also sets the "
             "Brillouin frequency (f_B = 2 n v / λ) and the conversion of a "
             "photoelastic constant p into dñ/dη.",
        fill="A number with its unit: nm, µm, Å or eV. It must lie inside the "
             "range of every n,k file.",
        empty=_REQ,
        code=_cfg("lambda_probe_nm")),
    "pump_fluence_J_m2": dict(
        title="Pump fluence",
        what="Energy per area of the pump pulse. It multiplies the absorbed "
             "energy density W(z), so every contribution to ΔR/R scales "
             "linearly with it (the model is linear).",
        fill="J/m², mJ/cm² (1 mJ/cm² = 10 J/m²) or µJ/cm². Must be > 0.",
        empty=_REQ,
        code=_cfg("pump_fluence_J_m2")),
    "pulse_fwhm_ps": dict(
        title="Pulse duration (FWHM)",
        what="Full width at half maximum of the Gaussian pump pulse. The "
             "electron and lattice temperature histories are convolved with "
             "this Gaussian, so it sets how fast the signal rises at t = 0.",
        fill="fs, ps or ns. Must be > 0. Typical: 0.05–0.5 ps.",
        empty=_REQ,
        code=_cfg("pulse_fwhm_ps")),
    "tau_th_ps": dict(
        title="Thermal decay time τ_th",
        what="Time constant of the lattice-temperature decay, exp(−t/τ_th): "
             "heat leaving the probed region. It shapes the slow thermal "
             "background of ΔR/R and the thermal stress that launches the "
             "strain wave.",
        fill="fs, ps or ns. Must be > 0. Fit it to experiment or estimate it "
             "from d²/(thermal diffusivity).",
        empty=_REQ,
        code=_cfg("tau_th_ps")),
    "t_min_ps": dict(
        title="Start time t_min",
        what="First delay of the output time axis. Negative values show the "
             "baseline before the pump arrives (t = 0).",
        fill="fs, ps or ns; usually a small negative value such as −10 ps.",
        empty=_REQ,
        code=_cfg("t_min_ps")),
    "t_max_ps": dict(
        title="End time t_max",
        what="Last delay of the simulation. The run time grows linearly with "
             "it. The strain pulse must not reach the absorbing sponge before "
             "t_max (see 'Modelled substrate depth').",
        fill="fs, ps or ns; must be larger than t_min.",
        empty=_REQ,
        code=_cfg("t_max_ps")),
    "dt_out_ps": dict(
        title="Output time step Δt",
        what="Spacing of the output time axis (the rows of the ΔR/R file). The "
             "solver itself uses a smaller internal step (from the CFL "
             "condition) and reports every Δt.",
        fill="fs, ps or ns; > 0. Typical 0.05 ps. Smaller = more rows, "
             "slightly slower.",
        empty=_REQ,
        code=_cfg("dt_out_ps")),
    "dz_nm": dict(
        title="Grid step dz",
        what="Depth step of the spatial grid. Layer interfaces are snapped to "
             "multiples of dz (a layer thinner than dz becomes one cell). The "
             "run time grows roughly as 1/dz² (more cells and a smaller time "
             "step).",
        fill="nm, µm or Å; > 0. Use a step clearly smaller than the thinnest "
             "layer and the optical absorption length (e.g. 0.5–1 nm).",
        empty=_REQ,
        code=_cfg("dz_nm") + [("diffr_model.py", r"d_snap")]),
    "substrate_model_nm": dict(
        title="Modelled substrate depth",
        what="How much of the (semi-infinite) substrate is put on the grid. "
             "After it comes the absorbing sponge. It must be deeper than the "
             "distance sound travels in t_max, otherwise the strain pulse "
             "reaches the sponge and its later echoes are lost; the log warns "
             "when this happens.",
        fill="nm, µm or Å; > v_sound(substrate) × t_max.",
        empty=_REQ,
        code=_cfg("substrate_model_nm") + [("sample_input.py", r"substrate_model_nm")]),
    "sponge_nm": dict(
        title="Absorbing sponge layer",
        what="Extra layer after the modelled substrate in which the strain "
             "wave is damped gradually to zero, so it is not reflected back "
             "from the end of the grid.",
        fill="nm, µm or Å; > 0. A few hundred nm is usually enough.",
        empty=_REQ,
        code=_cfg("sponge_nm")),
    "cfl": dict(
        title="CFL safety factor",
        what="Stability factor of the leapfrog solver: the internal time step "
             "is dt = cfl · dz / v_max. Values ≥ 1 make the solution unstable.",
        fill="A number between 0 and 1; 0.4 is safe. Smaller = slower.",
        empty=_REQ,
        code=_cfg("cfl")),
    # ------------------------------------------------------------ switches
    "include_strain": dict(
        title="Photoelastic (bulk strain wave)",
        what="Includes the change of n,k caused by the strain wave η(z,t) "
             "inside the layers (dñ/dη, the photoelastic effect). This gives "
             "the Brillouin oscillations and the echoes.",
        fill="Tick to include, untick to see ΔR/R without this part.",
        empty="Unticked: the strain contribution is set to zero; the others "
              "are unchanged.",
        code=[("diffr_model.py", r"include_strain|drr_eta")]),
    "include_displacement": dict(
        title="Interface / surface displacement",
        what="Includes the change of reflectivity because the surface and the "
             "interfaces move (the strain wave displaces them).",
        fill="Tick to include.",
        empty="Unticked: the displacement contribution is set to zero.",
        code=[("diffr_model.py", r"include_displacement|drr_u|def interface_sensitivity")]),
    "include_lattice_T": dict(
        title="Thermo-optic, lattice temperature",
        what="Includes the change of n,k with the lattice temperature (dñ/dT), "
             "the slow thermal part of ΔR/R.",
        fill="Tick to include.",
        empty="Unticked: the lattice-temperature contribution is zero.",
        code=[("diffr_model.py", r"include_lattice_T|drr_T\b")]),
    "include_electron_T": dict(
        title="Thermo-optic, electron temperature",
        what="Includes the change of n,k with the electron temperature "
             "(dñ/dTₑ), the fast peak right after t = 0 in metals. It needs Ce "
             "(and dñ/dTₑ) in the absorbing layer.",
        fill="Tick to include.",
        empty="Unticked: the electron contribution is zero. If ticked but the "
              "transducer has Ce = 0, the log warns that the channel is silent.",
        code=[("diffr_model.py", r"include_electron_T|drr_Te")]),
    # ------------------------------------------------------------ layers
    "material": dict(
        title="Material name",
        what="Name of the layer's material. It is used to look up literature "
             "values (Fill from literature), for the stack colours, the labels "
             "and the file names ({material}). Two layers with the same name "
             "get keys 'Si3N4' and 'Si3N4#2' inside the model; they can have "
             "different values.",
        fill="Any text, e.g. Si3N4. Choose from the list for materials that "
             "have literature values.",
        empty=_REQ,
        code=[("diffr_model.py", r"def make_sample|key = l\.material"),
              ("sample_input.py", r"name = lay\[\"material\"\]")]),
    "d": dict(
        title="Thickness",
        what="Thickness of the layer. It sets where the interfaces are, the "
             "optical phase in the transfer matrix and the acoustic echo times "
             "(2d/v). The last layer is the semi-infinite substrate and has no "
             "thickness.",
        fill="nm, µm, Å or m; > 0. It is snapped to the grid step dz.",
        empty=_REQ + " (Not needed for the substrate.)",
        code=[("sample_input.py", r'U\.to_base\(x, "thickness"'),
              ("diffr_model.py", r"def build_grid|d_snap|ds = \[0\.0\]")]),
    "nk_mode": dict(
        title="Optical constants ñ = n + ik",
        what="Complex refractive index of the layer at the pump and probe "
             "wavelengths. It decides how much light is absorbed where (the "
             "pump) and how sensitive ΔR/R is to each depth (the probe).",
        fill="Either type n and k for both wavelengths, or choose a dispersion "
             "file (columns: wavelength, n, k) — then n and k are interpolated "
             "at the two wavelengths and shown read-only. k ≥ 0 means "
             "absorption.",
        empty=_REQ + " Without n,k the layer cannot be modelled.",
        code=[("sample_input.py", r"def layer_nk|out\[w\] = complex"),
              ("diffr_model.py", r"def tmm|n_pump|n_probe")]),
    "nk_file": dict(
        title="Dispersion file",
        what="Text table with the layer's optical constants: column 1 "
             "wavelength (or photon energy), column 2 n, optional column 3 k. "
             "n,k are linearly interpolated at the pump and probe wavelengths, "
             "exactly as MaterialLibrary.nk does in the notebook.",
        fill="Browse to the file. Comma, semicolon, tab or space separated; "
             "lines starting with # are ignored. Wavelengths outside the "
             "table are refused (no extrapolation).",
        empty=_REQ + " (in file mode)",
        code=[("diffr_model.py", r"def load_dispersion|def interp_nk|np\.interp"),
              ("sample_input.py", r"M\.interp_nk")]),
    "nk_file_unit": dict(
        title="Column-1 unit",
        what="Unit of the first column of the dispersion file.",
        fill="'auto' reads a '# unit: …' line in the file, otherwise guesses: "
             "values < 100 → µm, > 1500 → Å, else nm (the guess is reported). "
             "Choose nm, µm, Å, m or eV to override.",
        empty="'auto' is used.",
        code=[("diffr_model.py", r"def guess_unit|def to_nm|unit = unit or declared")]),
    "kfill": dict(
        title="k when the file has no k column",
        what="Extinction coefficient used when the dispersion file only has n "
             "(e.g. a transparent dielectric).",
        fill="A number ≥ 0 for the pump and the probe; 0 for a transparent "
             "layer.",
        empty="Required only if the file has no k column; then the run does not "
              "start.",
        code=[("sample_input.py", r"kfill_")]),
    "rho": dict(
        title="Density ρ",
        what="Mass density. It enters the acoustic impedance Z = ρ·v (how much "
             "of the strain wave is reflected at each interface), the elastic "
             "modulus C11 = ρ·v², the equation of motion, and the heat "
             "capacity per volume ρ·Cp that turns absorbed energy into a "
             "temperature rise.",
        fill="kg/m³ or g/cm³; > 0.",
        empty=_REQ,
        code=[("diffr_model.py", r"\brho(_[nh])?\b")]),
    "v": dict(
        title="Longitudinal sound velocity v",
        what="Speed of the strain wave in the layer. It sets C11 = ρ·v², the "
             "impedance Z, the echo times 2d/v, the Brillouin frequency, and "
             "the solver's time step (through the largest v).",
        fill="m/s, km/s or nm/ps (1 nm/ps = 1 km/s); > 0.",
        empty=_REQ,
        code=[("diffr_model.py", r"self\.v\b|\bvmax\b|\.v for n|m\.v\b")]),
    "Cp": dict(
        title="Isobaric heat capacity Cp",
        what="Specific heat of the lattice. The lattice temperature rise is "
             "ΔT = W / (ρ·Cp), which sets both the thermo-optic signal and the "
             "thermal stress that launches the strain wave.",
        fill="J/(kg·K) or J/(g·K); > 0.",
        empty=_REQ,
        code=[("diffr_model.py", r"\bCp(_h)?\b")]),
    "alpha_lin": dict(
        title="Linear thermal expansion α",
        what="Thermal expansion coefficient. Together with B it gives the "
             "thermal stress coefficient B_th = 3·B·α, i.e. how strongly the "
             "heated layer pushes and launches the strain wave.",
        fill="1/K or 1e-6/K (= ppm/K).",
        empty=_REQ,
        code=[("diffr_model.py", r"alpha_lin|\bBth")]),
    "B_bulk": dict(
        title="Bulk modulus B",
        what="Bulk modulus, used for the thermal stress B_th = 3·B·α.",
        fill="Pa, GPa or Mbar; ≥ 0.",
        empty=_REQ,
        code=[("diffr_model.py", r"B_bulk|\bBth")]),
    "Ce": dict(
        title="Electron heat capacity Ce",
        what="Heat capacity of the electrons per volume. In metals the "
             "absorbed energy first heats the electrons: ΔTₑ = W / Ce. With "
             "dñ/dTₑ this gives the fast electronic peak of ΔR/R.",
        fill="J/(m³·K) or J/(cm³·K); ≥ 0. For Ti about 9.6e4 J/(m³·K).",
        empty="Optional. Blank = 0: the layer has no electron channel (right "
              "for insulators). If the most absorbing layer has Ce = 0 while "
              "the electron contribution is ticked, the log warns.",
        code=[("diffr_model.py", r"\bCe(_h)?\b")]),
    "tau_ep_ps": dict(
        title="Electron–phonon time τ_ep",
        what="Time for the hot electrons to pass their energy to the lattice. "
             "The value of the transducer (the layer with the highest absorbed "
             "energy density) sets the shapes of the electron and lattice "
             "temperature histories.",
        fill="fs or ps; > 0. Typical 0.5–1 ps for metals.",
        empty="Optional. Blank = 1 ps.",
        code=[("diffr_model.py", r"tau_ep")]),
    "pe_mode": dict(
        title="Photoelastic input",
        what="Choose how the photoelastic coupling is given: directly as "
             "dñ/dη, or as the photoelastic constant p (as in the fs-sonar "
             "conf files), which is converted with dñ/dη = −p·ñ³/2 at the "
             "probe wavelength.",
        fill="Pick the one you have values for.",
        empty="One of the two values is required.",
        code=[("diffr_model.py", r"def pe_to_dn_deta|-np\.asarray\(pe\)"),
              ("sample_input.py", r"pe_to_dn_deta")]),
    "dn_deta": dict(
        title="Photoelastic dñ/dη",
        what="Change of the complex refractive index per unit strain. It "
             "couples the strain wave to the probe: the photoelastic "
             "contribution to ΔR/R (Brillouin oscillations, echoes).",
        fill="Complex: real part + imaginary part, dimensionless.",
        empty=_REQ + " (when this input is chosen)",
        code=[("diffr_model.py", r"dn_deta|f\[\"eta\"\]")]),
    "pe": dict(
        title="Photoelastic coefficient p",
        what="Photoelastic constant in the indicatrix convention, "
             "d(1/n²) = p·η. Converted to dñ/dη = −p·ñ_probe³/2.",
        fill="Real (and optionally imaginary) part, dimensionless.",
        empty=_REQ + " (when this input is chosen)",
        code=[("diffr_model.py", r"def pe_to_dn_deta|-np\.asarray\(pe\)"),
              ("sample_input.py", r"pe_to_dn_deta")]),
    "dn_dT": dict(
        title="Thermo-optic dñ/dT",
        what="Change of the complex refractive index with the lattice "
             "temperature: the thermal (slow) contribution to ΔR/R.",
        fill="Complex, in 1/K, 1e-4/K or 1e-6/K.",
        empty=_REQ,
        code=[("diffr_model.py", r"dn_dT\b|f\[\"T\"\]")]),
    "dn_dTe": dict(
        title="Electronic dñ/dTₑ",
        what="Change of the complex refractive index with the electron "
             "temperature (metals): the fast electronic contribution.",
        fill="Complex, in 1/K, 1e-4/K or 1e-6/K.",
        empty="Optional. Blank = 0 (no electronic contribution from this "
              "layer).",
        code=[("diffr_model.py", r"dn_dTe|f\[\"Te\"\]")]),
    # ------------------------------------------------------------ sweep
    "sweep_layer": dict(
        title="Swept layer",
        what="The layer whose thickness is varied. Everything else stays as in "
             "the Layers and Experiment tabs. The sweep follows this layer if "
             "it is renamed or moved; it is marked on the stack diagram.",
        fill="Pick a layer above the substrate.",
        empty="The sweep cannot start without at least one layer above the "
              "substrate.",
        code=[("diffr_gui.py", r"def run_sweep|layers\[idx\]\[\"d\"\]")]),
    "sweep_range": dict(
        title="Thickness range",
        what="Thicknesses to run: start, start + increment, … up to the end "
             "(included when it falls on the step grid).",
        fill="Three numbers and a unit; increment > 0, end ≥ start, all > 0. "
             "The tab shows the list of runs before you start.",
        empty="The sweep does not start; the tab says what is missing.",
        code=[("sample_input.py", r"def range_values")]),
    # ------------------------------------------------------------ background
    "bg_enabled": dict(
        title="Subtract the background",
        what="Fits f(t) = a·e^(b·t) + c·e^(d·t) to each ΔR/R trace "
             "(minus_exp_fun_single3) after the simulation and subtracts it, "
             "leaving the oscillating (acoustic) part.",
        fill="Tick to switch on; you are asked about the fit range.",
        empty="Unticked: no fit, no extra columns.",
        code=[("background.py", r"def apply|minus_exp_fun_single3\(t\[mask\]")]),
    "bg_use_cut": dict(
        title="Fit only after a cut-off",
        what="Restricts the fit to delays after the cut-off (as "
             "mask = delay > cut in the notebook), leaving out the fast peak "
             "around t = 0 that a slow background cannot follow.",
        fill="Tick and give the delay in fs, ps or ns (e.g. 10 ps). Untick to "
             "fit the whole time axis.",
        empty="If ticked, the cut-off must be a number; at least 4 points must "
              "lie after it, otherwise that run gets an empty (NaN) column.",
        code=[("background.py", r"mask = t > cut_ps|use_cut")]),
    "bg_force_decay": dict(
        title="Force decaying exponentials",
        what="Constrains b ≤ 0 and d ≤ 0 in the fit, i.e. only decays — usually "
             "right for a background.",
        fill="Tick (default). Untick only if rising exponentials are needed.",
        empty="Unticked: b and d are free.",
        code=[("background.py", r"force_decay")]),
    "bg_before": dict(
        title="Before the cut-off",
        what="What the subtracted column holds for delays before the cut-off "
             "(the fit does not cover them).",
        fill="NaN (empty, like the notebook), the original ΔR/R, or the fit "
             "extrapolated back and subtracted.",
        empty="NaN.",
        code=[("background.py", r"before ==|before !=")]),
    "bg_cols": dict(
        title="Extra columns",
        what="Columns written right after dR_over_R in the ΔR/R file: "
             "dR_over_R_minus_bg (ΔR/R − background) and optionally bg_fit (the "
             "fitted background).",
        fill="Tick the columns you want.",
        empty="Unticked: the column is not written (the NPZ still has it).",
        code=[("outputs.py", r"dR_over_R_minus_bg|bg_fit")]),
    "bg_plot_main": dict(
        title="Main ΔR/R plot shows",
        what="Which trace the main ΔR/R plot shows when the background has "
             "been subtracted.",
        fill="original, background-subtracted, or both.",
        empty="original.",
        code=[("diffr_gui.py", r"bg_plot_main")]),
    # ------------------------------------------------------------ output
    "out_folder": dict(
        title="Output folder",
        what="Folder where automatic saves and figures are written.",
        fill="Browse to a folder; it is created if needed.",
        empty="Automatic saving cannot start without a folder.",
        code=[("outputs.py", r"os\.makedirs")]),
    "out_auto": dict(
        title="Automatic saving and file names",
        what="Saves each single run, each run of a sweep, and/or one combined "
             "file per sweep, named from the pattern. Fields such as {run}, "
             "{date}, {material}, {d}, {unit} are filled in per run.",
        fill="Tick what should be saved and edit the name pattern. Unknown "
             "fields and patterns that give two runs the same name are refused "
             "before the run starts.",
        empty="Unticked: nothing is saved automatically (File → Export still "
              "works).",
        code=[("sample_input.py", r"def format_name"),
              ("diffr_gui.py", r"def _sweep_plan|def _single_plan")]),
    "out_csv": dict(
        title="CSV table",
        what="Text table with the time axis, ΔR/R and the chosen columns; "
             "opens in Excel, Origin, etc.",
        fill="Choose the component columns, time unit, separator, number "
             "format (e.g. %.8e) and whether a header row is written.",
        empty="Unticked: no CSV.",
        code=[("outputs.py", r"def _csv|def write_run|cols\.append")]),
    "out_npz": dict(
        title="NPZ archive",
        what="NumPy archive with all arrays and the metadata, for further work "
             "in Python (np.load).",
        fill="Optionally include the depth profiles (kernels, absorption) and "
             "the strain map (only if recorded).",
        empty="Unticked: no NPZ.",
        code=[("outputs.py", r"np\.savez_compressed|payload")]),
    "out_meta": dict(
        title="Metadata JSON",
        what="Every input with its unit, the values converted to model units, "
             "literature placeholders, run times and the background fit "
             "parameters — everything needed to reproduce a run.",
        fill="Tick to write <name>_meta.json next to the data.",
        empty="Unticked: no JSON (the NPZ still holds the metadata).",
        code=[("outputs.py", r"def run_meta|_meta\.json")]),
    "out_figures": dict(
        title="Figures",
        what="Saves the plots as they look on screen (labels, colours, "
             "offsets).",
        fill="Choose which plots, the format (png, pdf, svg) and the dpi.",
        empty="Unticked: no figures are saved automatically (the toolbar's "
              "save button still works).",
        code=[("diffr_gui.py", r"def _save_figures|savefig")]),
    "out_overwrite": dict(
        title="Ask before overwriting",
        what="Whether you are asked before existing files are replaced.",
        fill="Tick (default) to be asked; untick to overwrite silently (the log "
             "lists what was replaced).",
        empty="Unticked: files are overwritten without asking.",
        code=[("diffr_gui.py", r"def _confirm_overwrite")]),
    "out_counter": dict(
        title="Run / sweep number",
        what="The number {run} puts into file names. Single runs and sweeps "
             "share it.",
        fill="Type the next number, or Reset to 1 to reuse earlier names.",
        empty="Must be a whole number ≥ 1.",
        code=[("diffr_gui.py", r"def _on_counter_edit|def reset_counter")]),
    # ------------------------------------------------------------ plots
    "overlay": dict(
        title="Overlay all runs",
        what="Shows all runs in memory together instead of only the latest. "
             "Runs from different inputs are tagged [inputs A], [inputs B].",
        fill="Tick to overlay.",
        empty="Unticked: only the latest run or sweep is shown.",
        code=[("diffr_gui.py", r"self\.overlay\.get\(\)|\[inputs ")]),
    "extra_plots": dict(
        title="Extra plots",
        what="Components: the four contributions to ΔR/R. Kernels & "
             "absorption: f_η(z) and W(z). Strain map: η(z,t) — it has to be "
             "ticked before the run, because recording it uses memory.",
        fill="Tick to show the tab.",
        empty="Unticked: the tab is hidden (the strain map is not recorded).",
        code=[("diffr_model.py", r"save_strain|eta_snap")]),
}


# ---------------------------------------------------------------------------
# Code lookup
# ---------------------------------------------------------------------------

def _indent(line):
    return len(line) - len(line.lstrip())


def _enclosing(lines, i):
    """Name of the def/class (or top-level assignment) a line belongs to."""
    ind = _indent(lines[i]) if lines[i].strip() else 10 ** 6
    m = re.match(r"\s*(def|class)\s+(\w+)", lines[i])
    if m:
        return f"{m.group(2)}()" if m.group(1) == "def" else f"class {m.group(2)}"
    m = re.match(r"([A-Za-z_]\w*)\s*=", lines[i])
    if m and ind == 0:
        return m.group(1)
    for j in range(i - 1, -1, -1):
        l = lines[j]
        if not l.strip() or _indent(l) >= ind:
            continue
        m = re.match(r"\s*(def|class)\s+(\w+)", l)
        if m:
            return f"{m.group(2)}()" if m.group(1) == "def" else f"class {m.group(2)}"
        m = re.match(r"([A-Za-z_]\w*)\s*=", l)
        if m and _indent(l) == 0:
            return m.group(1)
        ind = _indent(l)
    return "module level"


# Notebook-only helpers and tables that would bury the physics in the Code
# window (they are not used by the GUI's calculation).
_SKIP = {"build_sample()", "take()", "_missing_report()", "sample_table()",
         "placeholder_banner()", "sample_from_conf()", "plot_result()",
         "plot_kernels()", "LITERATURE", "class Layer", "as_material()",
         "_LAYER_KEYS", "_CORE", "_ELEC"}


def code_snippets(key, context=3, max_hits=40):
    """[(file, function, [(line number, text, is_hit, [(start, end) of matches])])]
    for the code locations of HELP[key]."""
    out = []
    for fname, pattern in HELP.get(key, {}).get("code", []):
        path = os.path.join(HERE, fname)
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        rx = re.compile(pattern)
        hits = [i for i, l in enumerate(lines)
                if rx.search(l) and not l.lstrip().startswith("#")][:max_hits]
        # merge hits into blocks with context
        blocks = []
        for i in hits:
            a, b = max(0, i - context), min(len(lines), i + context + 1)
            if blocks and a <= blocks[-1][1]:
                blocks[-1][1] = max(blocks[-1][1], b)
                blocks[-1][2].add(i)
            else:
                blocks.append([a, b, {i}])
        for a, b, hs in blocks:
            where = _enclosing(lines, min(hs))
            if where in _SKIP:
                continue
            rows = []
            for i in range(a, b):
                spans = [m.span() for m in rx.finditer(lines[i])] if i in hs else []
                rows.append((i + 1, lines[i], i in hs, spans))
            out.append((fname, where, rows))
    return out
