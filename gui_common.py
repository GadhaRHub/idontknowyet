"""Constants and small helpers shared by the PiSES interface (no GUI toolkit
is imported here, so any interface can use them)."""

import matplotlib

APP_NAME = "PiSES"
APP_LONG = "PiSES — PicoSonar EchoStacks"
APP_SUB = "PicoSonar EchoStacks"

# the example stack loaded at start (notebook section 13); "" = substrate
EXAMPLE_STACK = [("Ti", "15"), ("Si3N4", "9.44"), ("Ti", "14.37"),
                 ("Si3N4", "588"), ("SiO2", "1454"), ("Si", "")]

# default stack colours (can be changed with Colours… above the stack)
DEFAULT_MATERIAL_COLORS = {"Ti": "thistle", "Si3N4": "paleturquoise",
                           "SiO2": "darkkhaki"}
SUBSTRATE_KEY, SUBSTRATE_DEFAULT = "__substrate__", "saddlebrown"

# how the Experiment settings are grouped (keys of sample_input.CFG_FIELDS)
CFG_GROUP_LASER = ["lambda_pump_nm", "lambda_probe_nm", "pump_fluence_J_m2",
                   "pulse_fwhm_ps", "tau_th_ps"]
CFG_GROUP_TIME = ["t_min_ps", "t_max_ps", "dt_out_ps"]
CFG_GROUP_GRID = ["dz_nm", "substrate_model_nm", "sponge_nm", "cfl"]

# figure-file suffix of each plot (for saved figures and plot styles)
FIGURE_PANELS = ("dRR", "stack", "components", "kernels", "strain_map",
                 "background", "sweep_viewer")

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

HELP_TEXT = """1. Experiment: pump/probe wavelengths, fluence, pulse length, time axis
   and grid. Choose the unit next to each value; the grey text shows the
   value the model receives (nm, ps, SI). The switches choose which
   contributions to ΔR/R are computed and whether acoustic loss is used.

2. Layers: build the stack from the top (light side) down. The last layer
   is the semi-infinite substrate. For each layer give:
   - n, k at the pump and probe wavelengths, typed or from a dispersion
     file (interpolated, never extrapolated);
   - ρ, v, Cp, α, B;
   - dñ/dη (or the photoelastic constant p) and dñ/dT;
   - optionally Ce, τ_ep, dñ/dTₑ for metals, and an acoustic loss.
   'Fill from literature' pre-fills known materials; such values are
   marked ● lit. and reported as placeholders when you run.

3. Interfaces: optionally grade chosen interfaces (width, linear or erf).

4. Run: ▶ in the left bar → Run model (or F5). ΔR/R and the stack are
   always shown; switch on Components, Kernels or Strain map (Extra plots ▾)
   for more plots. Use the toolbar (zoom, pan, home,
   save image), the mouse wheel to zoom, and 'Axes & labels…' to edit
   titles, labels, limits and scales.

5. Sweep: pick a layer and give start, end and increment. Before it starts
   you are told how many simulations and files it gives, the estimated
   size and the estimated time. Start it with ▶ Run in the left bar →
   Run thickness sweep (or F6); the stack then shows the sweep on
   every page until you choose Run model again.

6. Background: fit and subtract a double-exponential background.

7. Output: folder, automatic saving, file-name patterns, and what to save.

Open / Save keep the whole session; Import reads parameter files and
fs-sonar conf files; Export writes the selected run or a sweep. Every ⓘ
opens help for that input and the code where it is used."""

_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def drr_label(exp):
    return "ΔR/R" if exp == 0 else f"ΔR/R × 10{str(exp).translate(_SUP)}"


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


def is_dark(color):
    r, g, b = matplotlib.colors.to_rgb(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5
