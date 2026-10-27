<img src="assets/pises_128.png" width="96" align="right" alt="PiSES icon">

# PiSES — PicoSonar EchoStacks

Picosecond acoustics and ΔR/R of thin-film stacks: an interactive desktop
program for the pump–probe differential-reflectivity forward model in
`diffR_thinfilm_model_custom.ipynb`.

```
pip install -r requirements.txt     # numpy, matplotlib, PySide6 (+ scipy, optional)
python pises_qt.py
```

This README has two parts:

1. **[Using PiSES](#part-1--using-pises)**: installing, the window, every page,
   the plots, files, units.
2. **[Changing the code](#part-2--changing-the-code)**: how the program is
   built, what each file does, how data flows from a typed number to a saved
   file, step-by-step recipes for common changes, the rules that keep the
   window working, and how to test a change.

---

# Part 1 — Using PiSES

## Installing and starting

PiSES needs Python 3.9 or newer and:

| Package | Why |
|---|---|
| numpy | all numerics |
| matplotlib | every plot |
| PySide6 | the window (Qt 6) |
| scipy *(optional)* | used for the background fit when installed; otherwise a built-in numpy fitter does the same job |
| Pillow | GIF export (installed together with matplotlib) |

```
pip install -r requirements.txt
python pises_qt.py
```

On Windows you can start it without a console window with
`pythonw pises_qt.py`.

**Linux note.** Qt needs a few system libraries that every desktop already
has. On a minimal system (a container or a server) install them first, e.g.
on Debian/Ubuntu: `sudo apt install libegl1 libgl1 libxkbcommon-x11-0
libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0`.

### Icon and app menu entry

The window and taskbar use the PiSES icon (`assets/`: SVG, PNGs from 16 to
512 px, and `pises.ico` for Windows).

- **Linux:** `sh packaging/install_linux.sh` adds PiSES to your application
  menu / dock with its icon (for your user only, in `~/.local/share`). It
  writes `packaging/pises.desktop` with this folder's path filled in.
- **Windows:** for a desktop shortcut, right-click the desktop → New →
  Shortcut, target `pythonw C:\path\to\pises_qt.py`, then Properties →
  Change Icon → `assets\pises.ico`.

## The window

```
┌──────┬──────────────────────────────────────────────────────────────────┐
│ icon │ PiSES  PicoSonar EchoStacks   session › Page                     │
│      │                                     Open  Save  Import  Export   │
│ rail ├───────────────────┬──────────────────────────────────────────────┤
│      │ inspector page    │ Show run [….]  Overlay  Clear   Extra plots  │
│ Expe-│ (inputs of the    │ ┌ Stack & ΔR/R │ Components │ … │ Sweep viewer│
│ riment│ chosen page, in  │ │   plots (matplotlib, with toolbar)         │
│ Layers│ collapsible      │ │                                            │
│ Inter-│ sections)        │ ├────────────────────────────────────────────┤
│ faces │                  │ │ Console: conversions · warnings · timings  │
│ Sweep │                  │ └────────────────────────────────────────────┘
│ …    │                   │                                              │
│ (▶)  │                   │                                              │
│ Run  ├───────────────────┴──────────────────────────────────────────────┤
│ Help │ ● status                    progress     λ pump · probe · dz      │
└──────┴──────────────────────────────────────────────────────────────────┘
```

- **Navigation rail** (left): Experiment, Layers, Interfaces, Sweep,
  Background, Output, then the sienna **▶ Run** button. *Help* at the
  bottom opens "How to use", the unit table, the list of literature
  placeholders and "About".
- **▶ Run** (in the rail): click it for *Run model (single run)*, *Run
  thickness sweep*, *Cancel the calculation* and the placeholder list. Its
  label shows the run mode: "Run model" or "Run sweep" ("Running…" while
  a calculation runs). Choosing *Run thickness sweep* switches to sweep
  mode: from then on the stack diagram shows the sweep on **every** page,
  until you choose *Run model* again. The mode is saved with the session.
- **Top bar**: *Open* (open a session, start a new one from the example
  stack, **New window**, *Open session in a new window…*), *Save* (save /
  save as / *Name this session…*), *Import* (parameter file, fs-sonar
  conf_file), *Export* (the selected run, or a sweep). Keys: **F5** run,
  **F6** sweep, **Ctrl+S** save, **Ctrl+O** open, **Ctrl+N** new window.
- **Session name** (top bar, next to the page name): click it (✎) or use
  *Save → Name this session…* to name the session without saving it. It is
  shown in the top bar and the window title, so parallel windows are easy
  to tell apart, and it is kept in the session file. It is empty ("unnamed
  session") until you type one; importing a parameter file or conf_file
  names the session after that file, unless you have typed a name (clear
  the name to let the next import name it again).
- **Several sessions at once**: *New window* (Ctrl+N) starts another PiSES
  as its own program, with its own session. Calculations in different
  windows run in parallel on different CPU cores, and one window crashing
  does not affect the others. `python pises_qt.py my_session.json` opens a
  session directly.
- **Inspector**: the inputs of the page chosen in the rail. Every block can
  be folded by clicking its title. Drag the divider to its right to make it
  wider or narrower; it widens by itself when a page needs more room (up to
  45 % of the window), so nothing on a page is cut off.
- **Plots**: tabs for the stack and ΔR/R, plus optional tabs. Above them:
  which run is shown, *Overlay all runs*, *Clear runs*, and the
  **Extra plots ▾** menu that switches on the Components, Kernels &
  absorption and Strain map tabs.
- **Console** (dark, under the plots): every conversion to model units, the
  sample table, warnings, run diagnostics and timings. Drag the divider above
  it to give it more or less room; *Clear* empties it.
- **Status bar**: what is happening, the progress bar, *■ Cancel* while a
  calculation runs, and the current wavelengths and grid step.

Every input has a small **ⓘ**. Click it for a menu:
- **Help**: what the input is and why it is there, how to fill it in (units,
  limits, typical values), and what happens if it is left empty or switched
  off.
- **Code**: the lines of the program where the value is converted and used,
  read from the source files when you open the window (so the line numbers
  match your copy), with the parameter highlighted.

Grey text next to a field (under it, for material properties) shows the
value converted to the model's units (nm, ps, SI); it turns red when the
field is not a number. Hover over it to read it in full if it is cut short.

## Pages

### Experiment
Pump and probe wavelengths, fluence, pulse FWHM, thermal decay time, the
time axis, the grid and the CFL factor. Switches choose the four
contributions to ΔR/R (photoelastic strain, interface/surface displacement,
lattice and electron temperature) and whether **acoustic loss** is used.

### Layers
The **stack**: a coloured bar (click a block to select that layer), buttons
(*＋ Add*, *Duplicate*, *Delete*, *↑*, *↓*, *Literature…*) and the list of
layers from the top (the light side) down. The last layer is the
semi-infinite substrate. Below it, the **editor** of the selected layer:

- **Layer**: material name and thickness. *Fill from literature* pre-fills
  the properties from the `LITERATURE` table (Si3N4, SiO2, Ti, Si, Al, Au).
- **Optical constants**: *Type n, k*, or *Dispersion file*. A file has the
  same format as the files in the notebook's materials folder: columns
  wavelength, n, k (k optional). n and k at the pump and probe wavelengths
  are interpolated and filled in straight away; they update when you change
  the wavelengths or the file's column-1 unit, and the run uses exactly
  these values. Nothing is extrapolated: a wavelength outside the file's
  range is reported in red. If the file has no k column, you type k.
- **Mechanical & thermal**: ρ, v, Cp, α, B.
- **Optical coupling**: dñ/dη, or the photoelastic constant p (converted as
  dñ/dη = −p·ñ³/2 at the probe wavelength); dñ/dT.
- **Electronic (metals)**, optional: Ce, τ_ep, dñ/dTₑ.
- **Acoustic loss**, optional (default *None*, values 0): a viscosity η
  (Pa·s), or an amplitude attenuation α at a frequency (1/µm, 1/nm, 1/m,
  1/cm or dB/µm, at GHz/THz). The stress gets a viscous term,
  σ = C11·η + η_visc·∂η/∂t, so α(f) = η_visc·ω²/(2ρv³) grows as f²: later
  echoes come back smaller and broader. α is converted with
  η_visc = 2ρv³α/ω²; the grey line shows η_visc and α at 50 / 100 / 200 GHz.
  A large viscosity reduces the time step automatically to keep the solver
  stable.

A value that still equals its literature pre-fill is marked **● lit.** and
reported as a literature placeholder when you run, and recorded as one in
the exported metadata. n is never pre-filled. *Literature…* (or a
double-click on a layer) lists every placeholder, and can copy the list.

### Interfaces
Switch on *Graded interfaces*, then switch on each graded interface and give
its width (nm, Å or µm) and profile: *linear* (a straight ramp) or *erf* (a
smooth step; the width is the 10–90 % distance). By default every interface
is sharp (width 0). Across the width, centred on the nominal interface,
every property (ρ, C11, Cp, expansion, n and k at pump and probe,
photoelastic and thermo-optic coefficients, loss) is a linear mix of the
two layers; thicknesses stay as given. The optics treat a graded region as
thin sublayers (one per grid step) in the transfer matrix. A width may
reach at most through a whole neighbouring layer (≤ 2 × its thickness); the
page shows the limit and flags a width that is too large. The settings
belong to the upper layer of each interface, so they move with the layers.
The stack diagram draws a graded interface as a colour blend with its width
written below (to scale with *Proportional* widths).

### Sweep
Pick a layer and give a start, an end and an increment in any thickness
unit. The sweep follows that layer when it is renamed or moved; runs are
labelled "Si3N4 (layer 4) = 100 nm". While this page is open, and on
every page in sweep mode (▶ Run → *Run thickness sweep*), the stack diagram
marks the swept layer ("◀ thickness swept ▶", dashed frame, range, number of
runs): the sweep as set up now, or the sweep just run when nothing has
changed since. The sweep is started from ▶ Run (or F6), not from this
page. The page lists what the
sweep will produce: number of simulations, files and their names, the
estimated disk space and time. The same summary is shown for confirmation
before the sweep starts. The time estimate uses the speed of the last run.

### Background
Subtracts a double-exponential background, f(t) = a·e^(b·t) + c·e^(d·t),
from every run with `minus_exp_fun_single3`.
- Switching it on asks whether to fit only delays after a cut-off (and
  which) or the whole time axis; both can be changed later on the page.
- Other settings: force decaying exponentials (b, d ≤ 0), and what the
  subtracted column holds before the cut-off.
- The result is written next to the original in the same file:
  `t, dR_over_R, dR_over_R_minus_bg[, bg_fit], strain, …`, for single runs
  and every thickness of a sweep. A combined sweep file gets a pair of
  columns per thickness. A failed fit gives a NaN column and a console line
  saying why. The NPZ holds `drr_minus_bg`, `bg_fit`, `bg_params`; the
  metadata JSON holds a, b, c, d (b, d in 1/ps) and the settings.
- The *Background* plot tab shows data, fit and subtracted trace (all
  thicknesses for a sweep). The main ΔR/R plot can show the original, the
  subtracted trace, or both.
- *Fit selected run now* / *Re-fit all runs* apply changed settings to runs
  in memory; files already saved are not rewritten.

### Output
- **Where**: the output folder.
- **When**: save after every single run, after every run of a sweep (each
  file as soon as its run finishes, so a cancelled sweep keeps what was
  done), and/or one combined file per sweep.
- **Names**: a pattern for each kind of file. Fields: `{run}`, `{date}`,
  `{time}`, and for sweeps `{material}`, `{layer}`, `{d}`, `{unit}`, `{i}`,
  `{n}` (combined file: `{start}`, `{end}`, `{step}`). Problems (unknown
  fields, two runs getting the same name) are caught before anything runs.
- **What**: a CSV table (component columns, time unit, separator, number
  format, header row), an NPZ archive (optionally depth profiles and the
  strain map), a metadata JSON (every input with its unit, the converted
  values, literature placeholders, run time) and figures (ΔR/R, stack, the
  extra plots shown; PNG/PDF/SVG at a chosen dpi, as they look on screen).
- **Run / sweep number**: `{run}` in the names; single runs and sweeps share
  it. Type the next number, or *Reset to 1…* to reuse earlier names. *Ask
  before overwriting existing files* (on by default) decides whether you are
  asked first.
- **Preview**: the next file names, number of files and size.

## Running

A run happens in two timed phases: the simulation of every run, then (when
switched on) the background subtraction of every run. The console names each
phase and each run's time; the status bar ends with the split, e.g. "done —
6 runs: simulation 14 min (2.3 min per run), background 2.1 s (0.35 s per
run); total 14 min". Cancelling during the background phase keeps and saves
the finished simulations. When a run starts, the result plots are cleared
until the new results are in; earlier runs stay under *Show run*. Every run
is computed only from the inputs at the moment you press Run (tested: a
sample run after another sample gives the same numbers as in a fresh
session).

## Plots

- **Stack & ΔR/R**: the stack diagram (click a layer to edit it; *Widths*:
  equal, proportional or log; *Colours…* per material, substrate included)
  and the total ΔR/R (*y scale*, *Offset*, *Sweep colours*).
- **Extra tabs** (from *Extra plots ▾*): Components, Kernels & absorption,
  Strain map (switch it on *before* the run). The Background tab appears
  only while background subtraction is switched on (Background page); the
  Sweep viewer tab is always there.
- **Every plot** has the matplotlib toolbar (home, back, forward, pan, zoom,
  subplots, save), mouse-wheel zoom, *Axes & labels…* (title, labels,
  limits, log/linear, grid, legend, font size), *Colours…* and *Reset
  labels*. Label and colour edits are kept when the plot redraws and are
  saved with the session.
- **Sweeps**: a thickness colour bar instead of a legend. **Right-click** a
  block to hide that thickness (hatched) in every sweep plot; again to show
  it. Sweep colours are shared by the ΔR/R, Background and Sweep viewer
  plots; *Colours…* on any of them sets one thickness's colour or all from a
  colour map. Runs from different inputs are tagged `[inputs A]`, `[inputs
  B]`, … when overlaid.
- **Saving a plot**: the colour bar or legend gets its own strip to the
  right; the saved image is made wider for it, so the plot area keeps its
  on-screen size.
- **Offsets**: *Offset* shifts curve n up by n × offset (plotted units); the
  y label says so. Saved data are never offset.

### Sweep viewer
Steps through a sweep one thickness at a time.
- *Signal*: ΔR/R − background by default (when fitted), or ΔR/R.
- *Show*: current curve only, current + others faint, or all shown curves.
  The y range stays fixed while stepping.
- Pick the thickness by **dragging along the thickness colour bar**, with
  the slider, or with ← → (click the plot first). *Play* runs through it
  like a video (frames/s, loop); *Show all thicknesses* un-hides everything.
- **Zoom is kept** while stepping and playing; the *x from … to …* and
  *y from … to …* boxes show and set it (blank = automatic). *Auto range*
  or the toolbar Home button goes back to the full view.
- *Offset* separates the shown curves; *Save GIF…* writes one frame per
  shown thickness with the current zoom and offset.

## Files

| File | What you do with it |
|---|---|
| sessions (`*.json`, *Save*) | the whole state: session name, inputs, layers, page settings, plot styles, run counter, run mode |
| parameter files (*Import*) | `CFG = dict(...)`, `SAMPLE = [...]`, `LITERATURE = {...}` written the notebook way (see `examples/Parameter_info_example.txt`); nothing in the file is executed |
| conf files (*Import*) | fs-sonar `conf_file_*.txt`: thicknesses, n,k files and peCoef; other constants from `properties.txt` / `*.prop` or literature |
| results (*Export*, automatic saving) | CSV, NPZ, metadata JSON, figures, as set on the Output page |

**Parameter files** in detail: values are in the model's units (nm, ps, SI).
Blocks are recognised by their content; other text is ignored. A comma
missing at the end of an entry is repaired and reported with its line.
`nfile="path/name"` can use a variable defined in the file
(`path = C:\...\materials`, quotes optional), with or without `.txt`; if not
found, *Materials folder…* in the review window searches another folder.
Per layer, `visc=1e-4` (Pa·s) or `att=5e5, att_f=1e11` (α in 1/m at f in
Hz) set the acoustic loss, and `grad=4` or `grad=(4, "erf")` grades the
interface to the layer below (width in nm). Missing properties come from
the file's `LITERATURE` table, else the built-in one, and are marked as
placeholders. A review window lists every value with its source before
anything changes.

## Units

| Quantity | Units offered | Model unit |
|---|---|---|
| Wavelength | nm, µm, Å, eV | nm (eV via λ = 1239.841984/E) |
| Thickness | nm, µm, Å, m | nm |
| Time (t, Δt, FWHM, τ_th) | fs, ps, ns | ps |
| Fluence | J/m², mJ/cm², µJ/cm² | J/m² |
| Density ρ | kg/m³, g/cm³ | kg/m³ |
| Sound velocity v | m/s, km/s, nm/ps | m/s |
| Cp | J/(kg·K), J/(g·K) | J/(kg·K) |
| α_lin | 1/K, 1e-6/K (ppm/K) | 1/K |
| Bulk modulus B | Pa, GPa, Mbar | Pa |
| Ce | J/(m³·K), J/(cm³·K) | J/(m³·K) |
| τ_ep | fs, ps | ps |
| dñ/dT, dñ/dTₑ | 1/K, 1e-4/K, 1e-6/K | 1/K |
| dñ/dη, p, CFL | dimensionless | – |
| dz, substrate, sponge | nm, µm, Å | nm |
| Viscosity η | Pa·s, mPa·s | Pa·s |
| Attenuation α (amplitude) | 1/m, 1/µm, 1/nm, 1/cm, dB/µm | 1/m |
| Frequency | Hz, GHz, THz | Hz |

---

# Part 2 — Changing the code

## How the program is built

```
              ┌────────────── the window (Qt) ───────────────┐
              │ pises_qt.py      builds widgets, connects them│
              │ pises_logic.py   what pressing things does    │
              │ qt_widgets.py    reusable widgets, dialogs,   │
              │                  PlotPanel (matplotlib)        │
              │ qt_theme.py      colours, style sheet, icons  │
              │ qt_compat.py     variables, timers, dialogs   │
              │ gui_common.py    shared texts and constants   │
              └───────────────┬───────────────────────────────┘
                              │ plain Python data (dicts, strings)
     ┌────────────────────────┼─────────────────────────────────┐
     │ sample_input.py  typed text + units → model units, checks │
     │ units.py         unit menus and factors                    │
     │ param_import.py  reads parameter files (never executes)    │
     │ help_info.py     ⓘ texts and code locations                │
     └────────────────────────┼─────────────────────────────────┘
                              │ stack, materials, cfg (SI / nm / ps)
     ┌────────────────────────┼─────────────────────────────────┐
     │ diffr_model.py   the physics: TMM optics, absorption,      │
     │                  sensitivity kernels, leapfrog acoustics,  │
     │                  loss, graded interfaces                   │
     │ background.py    double-exponential background fit        │
     │ outputs.py       CSV / NPZ / JSON writing and estimates    │
     └────────────────────────────────────────────────────────────┘
```

The bottom two layers know nothing about any window: they can be used from
a script or a notebook (see [Using the model without the window](#using-the-model-without-the-window)).
The window never does physics itself; it only collects inputs, calls the
lower layers and shows the results.

### Files

| File | Contents | Imports a GUI toolkit? |
|---|---|---|
| `pises_qt.py` | `App`: the main window. Builds the rail, top bar, every inspector page, the plot area, console and status bar, and connects widgets to variables and to `Logic` methods. Entry point `main()`. | yes (PySide6) |
| `pises_logic.py` | `Logic`: everything that happens: layer editing behaviour, planning (names, sizes, times), running (worker thread), all plot drawing, the Sweep viewer, export, sessions, imports. Mixed into `App`. | only for dialogs, via `qt_compat` / `qt_widgets` |
| `qt_widgets.py` | `Section` (collapsible block), `ToggleSwitch`, `Segmented`, `NavRail`, `MiniStack`, `InfoButton`, `PlotPanel` (figure + toolbar + label/colour editing + saving), and the dialogs (Axes & labels, Colours, Stack colours, Help, Code, Placeholders, background cut-off, import review). | yes |
| `qt_theme.py` | The palette, the Qt style sheet built from it, the rail icons (SVG), the app icon, matplotlib defaults. | yes |
| `qt_compat.py` | `StringVar` / `BooleanVar` (values with change callbacks) and `bind_*` helpers that tie them to widgets; `Scheduler` (`after`, `after_idle`, `after_cancel`); `messagebox`, `filedialog`, `colorchooser` shortcuts. | yes |
| `gui_common.py` | Name, example stack, default stack colours, Experiment groups, property tooltips, "How to use" text, `drr_label`, `fmt_duration`. | no |
| `sample_input.py` | Field lists (`CFG_FIELDS`, `PHYSICS_FLAGS`, `OPTION_FLAGS`, `REAL_PROPS`, `COMPLEX_PROPS`, loss and gradient defaults); `new_layer()`; `resolve_config()`, `resolve_layers()` (+ loss, gradients): typed text → model units, with a report and every error found; literature pre-fill; sessions; conf import. | no |
| `units.py` | `UNITS`: for each kind of quantity, its unit labels and factors to the model unit. `to_base`, `from_base`, `fmt`. | no |
| `param_import.py` | Reads `CFG` / `SAMPLE` / `LITERATURE` blocks with `ast` (no code is executed), repairs missing commas, expands path variables, finds n,k files, builds GUI inputs plus review rows. | no |
| `help_info.py` | `HELP[key]`: title, what, fill, empty, code locations for every ⓘ. `code_snippets(key)` reads the source files live. | no |
| `diffr_model.py` | The physics (see [The physics in diffr_model.py](#the-physics-in-diffr_modelpy)). | no |
| `background.py` | `minus_exp_fun_single3` (unchanged) and `apply()` used by the window; scipy or a numpy fitter. | no |
| `outputs.py` | `default_options()` (every Output-page setting), `write_run`, `write_sweep`, metadata, file-count / size / time estimates. | no |
| `assets/` | Icon files (`pises.svg`, PNGs, `.ico`); `check.svg`, `chev_down.svg`, `chev_up.svg` (tick mark and arrows used by the style sheet; rewritten by `qt_theme.ensure_assets()` if missing). | – |
| `packaging/` | Linux menu entry (`pises.desktop`, `install_linux.sh`). | – |
| `examples/` | An example parameter file. | – |
| `tests/smoke_qt.py` | Drives the real window without a screen and checks pages, every ⓘ, a run (against the model called directly), a sweep with saving, the Sweep viewer, sessions and dialogs. See [Testing a change](#testing-a-change). | yes |

### From a typed number to a result

1. **Typing.** Each field is bound to a variable (`StringVar`). For the
   current layer the variables are `self.lv[key]`; for the Experiment page
   `self.cfg_vars[key] = (value, unit)`; switches `self.flag_vars[key]`;
   Output and Background settings `self.out[key]`. Editing a layer field
   writes straight into the layer's dict (`self.layers[i][key]`) through
   `Logic._on_layer_var`. Everything the user enters is kept **as text with
   its unit**, never converted in place.
2. **Pressing Run** (`Logic.run_single` / `run_sweep`):
   - `_prepare(layers)` calls `sample_input.resolve_config()` and
     `resolve_layers()`. They convert text + unit → model units (nm, ps,
     SI), check everything, and return `cfg` (dict), `stack` (list of
     `(name, thickness_nm)`), `materials` (dict of `diffr_model.Material`)
     plus a report for the console. Any problem raises `InputError` with
     **all** the messages; the window shows them and nothing runs.
   - a *job* per simulation is built (one, or one per thickness).
3. **The worker thread** (`Logic._work`) calls `diffr_model.run_model()` for
   each job, then the background fit if switched on. It never touches a
   widget: it puts messages (`progress`, `sim`, `bg`, `done`, `cancelled`,
   `error`) on `self.queue`.
4. **The main thread** reads the queue every 100 ms (`Logic._poll`), logs,
   saves files as each run finishes (`outputs.write_run`), and at the end
   (`_finish`) stores the *batch* in `self.batches`, writes combined files
   and figures, and redraws the plots.
5. **Plots** are drawn from `self.batches` by the `draw_*` methods. Each
   draws into a `PlotPanel`: `fig = panel.clear()`, draw with matplotlib,
   `panel.finish()` (re-applies the user's label/colour edits and redraws).

Data kept in memory:

| Name | What |
|---|---|
| `self.layers` | list of layer dicts (`sample_input.new_layer()` gives every key). The last is the substrate. |
| `self.sel` | index of the layer in the editor |
| `self.batches` | list of finished runs/sweeps: `dict(kind="single"/"sweep", runs=[run, …], …)`; each run has `label`, `res` (the dict `run_model` returns), `inputs` (snapshot), and for sweeps `sweep_value`, `sweep_unit` |
| `self.view_run` | label of the run shown (the *Show run* menu) |
| `self.out` | every Output / Background setting (keys of `outputs.default_options()`) |
| `self.cfg_vars`, `self.flag_vars` | Experiment values with units, and switches |

## Recipes for common changes

Each recipe lists every place to touch. After any change, run the checks in
[Testing a change](#testing-a-change).

### Add an Experiment setting (a number with a unit)
1. `sample_input.py` → `CFG_FIELDS`: add
   `("my_param_ps", "My parameter", "time", "5.0", "ps", "tooltip text")`
   (model key, label, unit kind from `units.UNITS`, default text, default
   unit, hover text).
2. `gui_common.py` → add the key to one of `CFG_GROUP_LASER`,
   `CFG_GROUP_TIME`, `CFG_GROUP_GRID` (that decides the section). **Every
   key in `CFG_FIELDS` must be in exactly one group**, otherwise it has no
   field on the page.
3. `diffr_model.py` → add a default to `CFG` and use `cfg["my_param_ps"]`.
4. Optionally add a check in `resolve_config` (e.g. "must be > 0").
5. `help_info.py` → add `HELP["my_param_ps"]` (the ⓘ needs it; see below).

The field, unit menu, grey conversion, sessions and parameter-file import
then work automatically.

### Add an on/off physics switch
- In the *Contributions* block: add `("include_x", "Label")` to
  `PHYSICS_FLAGS` in `sample_input.py`; add `include_x=True` to `CFG` in
  `diffr_model.py` and use `cfg["include_x"]`; add `HELP["include_x"]`.
- Elsewhere: add it to `OPTION_FLAGS` instead and place a switch yourself in
  `pises_qt.py` with
  `self._switch_info("Label", self.flag_vars["include_x"], "include_x")`.

### Add a material property (per layer)
1. `sample_input.py` → `REAL_PROPS` (or `COMPLEX_PROPS` for Re + i·Im):
   `("kappa", "Thermal conductivity κ", "<kind>", "W/(m·K)", True, None)`
   (key, label, unit kind, default unit, required?, value when blank). If the
   unit kind is new, add it to `units.UNITS` first (first entry = model unit).
2. `diffr_model.py` → add the field to `Material` **and** `Layer` (with a
   default) and pass it in `Layer.as_material`.
3. `sample_input.resolve_layers` → pass it when the `M.Layer(...)` objects
   are built (look for `eta_visc=`). `build_sample` in `diffr_model.py` does
   the same for the notebook path.
4. `pises_qt.py` → `_build_layer_editor`: add `sec.add_full(self._prop_row("kappa"))`
   to the section where it belongs. `_prop_row` builds the label, ⓘ, entry,
   unit menu, conversion and literature mark.
5. `help_info.py` → `HELP["kappa"]`; optionally `gui_common.PROP_HELP`.
6. Optionally literature values in `diffr_model.LITERATURE`.

Sessions store layers as dicts; `sample_input.normalise_layer` fills keys
that old sessions do not have, so old sessions keep opening.

### Change or extend the physics
Edit `diffr_model.py` as you would the notebook. The window calls only
`run_model(stack, materials, cfg, verbose, progress, cancel)` and reads the
returned dict (`t_ps`, `drr`, `drr_strain`, `drr_disp`, `drr_lattice`,
`drr_electron`, `z_nm`, `f_eta`, `W`, `grid`, `transducer`, `eta_zt`, …).
Keep those keys and the window keeps working. A new contribution: add it to
the dict (`res["drr_x"]`) and to the sum `drr`; to show and save it see the
next two recipes. Keep calling `progress(fraction)` and checking
`cancel.is_set()` inside long loops (raise `Cancelled`), or the progress bar
and Cancel stop working.

### Add a column to the saved files
`outputs.py` → `COMPONENTS`: `("drr_x", "x_contribution", "comp_x")`
(key in the result, column name, option name) and `comp_x=True` in
`default_options()`. Then a check box on the Output page in `_page_output`
(`pises_qt.py`): add `("comp_x", "x")` to the list of component boxes.

### Add a plot tab
1. In `pises_qt._build_plot_area`: `self.x_panel = self._card(W.PlotPanel(self))`,
   `self.x_panel.redraw = self.draw_x`, `self.ptabs.addTab(self.x_panel, "X")`.
2. In `pises_logic.py`: write `draw_x(self)`:
   ```python
   def draw_x(self):
       p = self.x_panel
       res = self.selected_res()
       if res is None:
           p.empty("Run the model to see X.")
           return
       ax = p.clear().add_subplot()
       ax.plot(res["t_ps"], res["drr_x"])
       p.finish()
   ```
3. Call it from `redraw_run_plots()`. For an optional tab: add a
   `BooleanVar` in `App.__init__` (like `self.show_components`), add
   `("X", self.show_x)` to the list that fills the *Extra plots ▾* menu in
   `_build_plot_area`, and add `(self.show_x, self.x_panel)` to the list in
   `_update_plot_tabs` (it shows/hides the tab and redraws it).
4. To save it as a figure / keep its styles in sessions, add it to
   `Logic.figure_panels()` and `gui_common.FIGURE_PANELS`.

Use `gid="ref"` on lines that are not data (zero lines, markers) so the
Colours dialog leaves them alone.

### Add a page to the rail
1. `qt_theme.RAIL_ICONS["mypage"]`: an SVG fragment in a 24×24 box, with
   `{c}` where the colour goes.
2. `pises_qt.PAGES`: append `("mypage", "My page")`, and a constant like
   `PAGE_MYPAGE` if code refers to it.
3. `App.__init__`: add the builder to the tuple of page builders **in the
   same order as `PAGES`**.
4. Write `_page_mypage(self)` like the others: `p, v = self._page(title,
   subtitle)`, add `W.Section`s to `v`, `v.addStretch(1)`, `return p`.

### Add a field to an existing page
```python
sec = W.Section("My block")                     # or reuse an existing one
sec.add_row(self._lab_info("Label", "help_key"),  # label + ⓘ
            W.line_edit(var, 90),                  # a StringVar
            W.combo(unit_var, U.choices("time"), width=80),
            W.label("", "conv"))                   # grey text
sec.add_full(self._switch_info("Text", bool_var, "help_key"))
v.addWidget(sec)
```
Helpers: `W.line_edit`, `W.combo` (`editable=True` for typing), `W.check`,
`W.switch`, `W.Segmented(var, [(value, text), …])`, `W.button(text, slot,
"small"|"accent")`, `W.label(text, "hint"|"note"|"conv", wrap=True)`,
`W.hbox(...)` (ints = spacing, `None` = stretch). React to changes with
`var.trace_add("write", lambda *_: …)`; to keep a value in sessions add it
to `Logic._state()` and `_apply_state()`.

Width: labels made with `_lab_info` wrap onto two lines instead of being
cut, and the inspector widens itself to fit the widest row of the page on
show. Still, keep a row narrower than about 530 px (split a long row of
boxes into two rows, as on the Output page), so the inspector need not grow
on small screens. `tests/smoke_qt.py` fails if a page does not fit.

In the plot area, build rows of controls with `W.flow(...)` (same arguments
as `W.hbox`) rather than `W.hbox`: a flow row wraps onto more lines when the
window is narrow, so it never sets a minimum width for the whole window
(PlotPanel's `row2` and toolbar row are flow rows already). Group things
that must stay together with an inner `W.hbox(..., stretch=False)`; a label
always stays with the widget after it. The smoke test checks that the window
can still be made narrower than 1000 px. Plot titles and the stack's block
labels are fitted to the space automatically (`qt_widgets.fit_titles`,
`Logic._fit_stack_labels`).

### Add a help entry (ⓘ)
`help_info.py` → `HELP["key"] = dict(title=…, what=…, fill=…, empty=…,
code=[("file.py", r"regex"), …])`. Every key passed to `InfoButton` /
`_lab_info` / `_switch_info` / `_check_info` **must** exist in `HELP`, or the
ⓘ fails when clicked. The code locations are regular expressions searched in
the named files when the Code window opens.

### Add a unit
`units.py` → add `"label": factor` to the kind in `UNITS` (factor converts to
the model unit, i.e. the first entry). Menus pick it up everywhere.

### Change colours or the look
- Window colours: the constants at the top of `qt_theme.py` (`BERRY`,
  `INDIGO`, `SIENNA`, `TEAL`, `BG`, …); the style sheet `STYLE` is built from
  them. Widget looks are selected by object names (`setObjectName("…")`) in
  `STYLE`.
- Plot colours: `qt_theme.SERIES` (curves), `PLOT_INK`, `GRID_C`; matplotlib
  defaults in `matplotlib_defaults()`.
- Stack colours: `gui_common.DEFAULT_MATERIAL_COLORS`, `SUBSTRATE_DEFAULT`.
- Icon: `assets/pises.svg` (regenerate the PNG sizes and `pises.ico` from it).

### Add a literature material
`diffr_model.LITERATURE["Name"] = dict(rho=…, v=…, Cp=…, alpha_lin=…,
B_bulk=…, dn_deta=…, dn_dT=…)` (model units). It appears in the material
menu and in *Fill from literature*.

## Rules that keep the window working

1. **Widgets are touched only by the main thread.** The worker thread
   (`_work`) only computes and puts messages on `self.queue`. Never call a
   widget or `self.log` from `_work`, `run_model` or a progress callback.
2. **The physics layer stays GUI-free.** `diffr_model.py`, `sample_input.py`,
   `outputs.py`, `background.py`, `units.py`, `param_import.py` must not
   import PySide6. This keeps them usable from scripts and testable.
3. **Inputs are stored as typed text + unit**, converted only in
   `resolve_config` / `resolve_layers`. Do not write converted numbers back
   into fields.
4. **Conversion reports every error at once** (`InputError(list)`), so the
   user sees all problems in one go. Follow that pattern in new checks.
5. **Layer dicts keep every key.** Create layers with
   `sample_input.new_layer()`; add new keys with defaults there (or in
   `LOSS_DEFAULTS`, `GRAD_DEFAULTS`). The interface fields (`grad_*`) are
   edited on the Interfaces page and are deliberately not in `self.lv`.
6. **Plot protocol**: `fig = panel.clear()` → draw → `panel.finish()`. Lines
   that are not data get `gid="ref"`; colour bars get `cb.ax._diffr_cbar =
   True` and are appended to `panel.colorbars`.
7. **Calling Logic from Qt signals**: Qt may pass extra arguments (e.g.
   `checked`). Connect with `lambda *_: self.method()` (as `W.button` and the
   menus do), so a method with optional parameters is not called with a
   stray `False`.
8. **Setting a variable from code calls its callbacks.** When code changes a
   variable while loading (e.g. `select_layer`), guard with a flag
   (`self._loading`, `self._viewer_lock`, `self._setting_counter`) so the
   callback does not treat it as a user edit.
9. **Keys must match**: page builders ↔ `PAGES` order; `HELP` ↔ every ⓘ;
   `figure_panels()` ↔ `FIGURE_PANELS`; `CFG_FIELDS` ↔ the Experiment groups.
10. **Sessions are backward compatible**: `_apply_state` reads every key with
    a default (`.get(key, default)`), and `normalise_layer` fills missing
    layer keys. Keep doing that for new keys.

## Using the model without the window

```python
import sample_input as S, diffr_model as M

lays = []
for name, d in (("Ti", "15"), ("Si3N4", "300"), ("Si", "")):
    l = S.new_layer(name, d); S.apply_literature(l)   # fills ρ, v, … (not n, k)
    l["n_pump"], l["k_pump"] = "2.5", "3.3"            # give n, k yourself
    l["n_probe"], l["k_probe"] = "2.5", "3.3"
    lays.append(l)
ent = S.default_config_entries()
ent["t_max_ps"]["value"] = "150"
cfg, report = S.resolve_config(ent, S.default_flags())
stack, materials, layer_report, warnings = S.resolve_layers(lays, cfg)
res = M.run_model(stack, materials, cfg)
print(res["t_ps"], res["drr"])
```

Or with model units directly: build `M.Layer(...)` objects, call
`M.make_sample(layers)` and `M.run_model(stack, materials, cfg)`. For graded
interfaces pass `cfg["gradients"] = [dict(interface=1, width_nm=4,
shape="erf")]`; for loss set `materials[name].eta_visc` (Pa·s).

## The physics in diffr_model.py

| Step | Function |
|---|---|
| grid, snapped interfaces, graded-region weights, optical slabs | `build_grid`, `_mix_weights`, `_optical_slabs`, `check_gradients` |
| material property on the grid (mixed across graded interfaces) | `prop_map` |
| optics: transfer matrix over the slabs, field E(z), Poynting flux | `optical_solution`, `_tmm`, `optical_field`, `poynting` |
| absorbed energy density W(z) | `absorption_profile` |
| sensitivity kernels f_η, f_T, f_Te; interface displacement | `sensitivity`, `interface_sensitivity` |
| electron / lattice temperature shapes | `temperature_shapes` |
| acoustics: leapfrog σ = C11·η + η_visc·∂η/∂t − B_th·ΔT, absorbing sponge | `run_model` |
| loss conversions | `attenuation_from_viscosity`, `viscosity_from_attenuation` |
| diagnostics | `run_summary` |

With loss and graded interfaces switched off, results are bit-for-bit those
of the notebook's model.

## Testing a change

1. **Syntax:** `python -m py_compile *.py`.
2. **The automatic check of the window:**
   ```
   python tests/smoke_qt.py            # about 15 s, opens no window
   python tests/smoke_qt.py --shots    # also saves pictures of every page in tests/shots/
   ```
   It prints one PASS/FAIL line per check and ends with "N of N checks
   passed" (exit code 0). It checks that every page opens and **fits the
   inspector**, that **every ⓘ** opens its Help and Code windows (so a
   missing `HELP` key is caught), that a single run gives **exactly** the
   numbers of `diffr_model.run_model` called directly with the same inputs,
   that a 3-thickness sweep finishes and saves its files, that the Sweep
   viewer steps, keeps a typed zoom and plays, that a session survives
   save → new → open unchanged, and that the dialogs open. When you add a
   feature, add a check for it at the end of the script in the same style
   (`check("what", condition, "detail")`).
3. **The model alone** (no window): run the script in
   [Using the model without the window](#using-the-model-without-the-window)
   before and after your change and compare `res["drr"]` (e.g.
   `numpy.array_equal`). A change that should not alter results must give
   identical arrays.
4. **By hand** (`python pises_qt.py`) for anything the script does not cover,
   e.g.:
   - every page opens; every ⓘ opens Help and Code;
   - a single run (F5) finishes and the console shows "done";
   - a short sweep (Sweep page, 3 thicknesses) finishes; the Sweep viewer
     steps and plays;
   - Save a session, start a new one, open the saved one: everything is
     back;
   - if you touched saving: switch on automatic saving and check the files.
5. **Without a screen** (servers, CI): `tests/smoke_qt.py` sets
   `QT_QPA_PLATFORM=offscreen` itself. Your own scripts can do the same:
   create `App()`, set variables, call `run_single()`, process events until
   `app.worker` has finished, `app.grab().save("shot.png")`; the smoke test
   shows how, including answering message boxes automatically.
