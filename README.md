# diffR thin-film model — GUI

An interactive desktop GUI (Tkinter + matplotlib) for the pump–probe
differential-reflectivity forward model in
`diffR_thinfilm_model_custom.ipynb`.

```
pip install -r requirements.txt     # numpy, matplotlib, scipy (tkinter ships with Python)
python diffr_gui.py
```

## Files

| File | What it is |
|---|---|
| `diffr_gui.py` | The GUI. Run this. |
| `sample_input.py` | Turns what the user typed (numbers + units) into model units, builds the sample, imports conf files, saves/loads sessions. No GUI code. |
| `background.py` | Double-exponential background subtraction: `minus_exp_fun_single3` (unchanged) plus the wrapper the GUI uses. Needs scipy. |
| `param_import.py` | Reads parameter files (`CFG = dict(...)`, `SAMPLE = [...]`, `LITERATURE = {...}`) without executing them. |
| `outputs.py` | Writes results the way the Output tab says (CSV, NPZ, metadata JSON) and estimates file counts, sizes and run times. No GUI code. |
| `units.py` | The unit menus and conversion factors. |
| `diffr_model.py` | The physics, taken unchanged from the notebook (TMM, absorption, sensitivity kernels, leapfrog solver, save/plot). |
| `diffR_thinfilm_model_custom.ipynb` | The original notebook. |

`diffr_model.py` makes the same calculation as the notebook. The only changes:
`run_model` takes the stack and materials explicitly, reports progress, can be
cancelled, and keeps a decimated strain map. `save_result` needs an output path
and can add metadata. A single-file n,k loader (`load_dispersion`) was split
out of `MaterialLibrary`, which uses it.

## Units

Each input has a unit menu. The grey text next to the field shows the value
converted to the model's units (nm, ps, SI). The full conversion report is
written to the log on every run.

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

To add a unit, add one entry to `UNITS` in `units.py`.

## Using it

- **Experiment tab**: pump/probe wavelengths, fluence, pulse FWHM, thermal
  decay time, time axis, grid and CFL factor, plus checkboxes for the four
  ΔR/R contributions.
- **Layers tab**: list the layers from the top (the light side) down. The last
  layer is the semi-infinite substrate. The buttons stretch with the panel.
  Drag the divider between the layer list and the editor, or between the
  left panel and the plots, to resize them. For each layer give:
  - n, k at the pump and probe wavelengths, typed in or read from a dispersion
    file. A file has the same format as the files in the notebook's materials
    folder: columns wavelength, n, k (k is optional). When you browse to one,
    n and k at the pump and probe wavelengths are interpolated and filled in
    straight away, as `MaterialLibrary.nk` does. They update when you change
    the wavelengths or the file's column-1 unit, and the run uses exactly
    these values. Nothing is extrapolated: a wavelength outside the file's
    range is reported in red. If the file has no k column, you type k
    yourself;
  - ρ, v, Cp, α, B;
  - dñ/dη, or the photoelastic constant p, converted as dñ/dη = −p·ñ³/2 at the
    probe wavelength;
  - dñ/dT;
  - optionally Ce, τ_ep and dñ/dTₑ for metals.

  *Fill from literature* pre-fills from the `LITERATURE` table (Si3N4, SiO2,
  Ti, Si, Al, Au). Every field stays editable. A value that still equals its
  pre-fill is marked **● lit.** and reported as a literature placeholder when
  you run, and it is recorded as one in the exported metadata. n is never
  pre-filled. *Literature values…* (or a double-click on a layer) lists
  every value that is still a placeholder, with its unit, and can copy the
  list to the clipboard. The list also opens after *Fill from literature*.
- **Thickness sweep tab**: pick a layer and give a start, an end and an
  increment in any thickness unit. The end is included when it falls on the
  step grid. The tab shows what the sweep will produce: the number of
  simulations, the number of files and their names, the estimated disk space
  and the estimated time. Before the sweep starts, the same summary is shown
  for confirmation. The time estimate uses the speed of the last run, so it
  appears once one run has been timed.
- **Background tab**: subtracts a double-exponential background,
  f(t) = a·e^(b·t) + c·e^(d·t), from every run with `minus_exp_fun_single3`.
  - When you switch it on, you are asked whether to fit only delays after a
    cut-off (and which one, in fs/ps/ns) or the whole time axis. Both can be
    changed in the tab later.
  - Other settings: force decaying exponentials (b, d ≤ 0), and what the
    subtracted column holds before the cut-off (empty/NaN, the original
    ΔR/R, or the extrapolated fit subtracted).
  - The result is written next to the original in the same ΔR/R file:
    `dR_over_R_minus_bg` and optionally `bg_fit`. The NPZ holds
    `drr_minus_bg`, `bg_fit` and `bg_params`, and the metadata JSON holds
    a, b, c, d (b, d in 1/ps) and the settings. A combined sweep file gets
    one `dR_over_R_minus_bg_d=…` column per thickness.
  - The fit parameters are printed in the log and in the tab ("Exponential
    fit params: a=…, b=…, c=…, d=…", or "fit failed — mean subtracted
    instead"). A *Background* plot tab shows data, fit and subtracted trace.
    The main ΔR/R plot can show the original, the subtracted trace, or both.
  - *Fit selected run now* / *Re-fit all runs* apply changed settings to
    runs already in memory. Files already saved are not rewritten.
- **Output tab**: everything about saving, all of it kept in the session.
  - *Where*: the output folder.
  - *When*: save automatically after every single run, after every run of a
    sweep (each file is written as soon as its run finishes, so a cancelled
    sweep keeps what was done), and/or one combined file per sweep with all
    thicknesses side by side.
  - *Names*: a pattern for each kind of file. Fields: `{run}`, `{date}`,
    `{time}`, and for sweeps `{material}`, `{layer}`, `{d}`, `{unit}`, `{i}`,
    `{n}` (combined file: `{start}`, `{end}`, `{step}`). Unknown fields and
    patterns that would give two runs the same name are caught before
    anything runs, and you are asked before files are overwritten.
  - *What*: a CSV table (choose the component columns, the time unit,
    separator, number format and header row), an NPZ archive (optionally
    with depth profiles and the strain map), a metadata JSON (every input
    with its unit, the converted values, literature placeholders, run time),
    and figures (ΔR/R, stack diagram, the extra plots shown; PNG/PDF/SVG at a
    chosen dpi). Figures are saved as they look on screen, with your labels
    and colours.
  - *Run / sweep number*: single runs and sweeps share one counter, which
    `{run}` puts in the names. Type the next number yourself, or press
    *Reset to 1…* (it asks for confirmation) to reuse earlier names, e.g. to
    replace an earlier series of files. *Ask before overwriting existing
    files* (on by default) decides whether you are asked before files are
    replaced; when it is off, the log lists what was overwritten. The
    counter is saved with the session.
  - A preview lists the next file names, how many files and roughly how much
    space. File → Export uses the same choices.
- **Run time**: the status bar shows the time elapsed while running, and
  afterwards how long the run took (for a sweep, the total and the time per
  run). Each run's time is also logged and stored in its metadata.
- **Plots**: the stack diagram and the total ΔR/R are always shown. Click a
  layer in the diagram to edit it. *Colours…* above the diagram sets the
  colour of each material (layers of the same material share one); the
  colours are saved with the session. Tick *Components*, *Kernels & absorption*
  or *Strain map η(z,t)* for more tabs (the strain map must be ticked before
  the run). Every plot has the matplotlib toolbar (zoom, pan, home, back,
  save image), mouse-wheel zoom, and *Axes & labels…* for the title, axis
  labels, limits, linear/log scale, grid, legend and font size. *Colours…* on
  each plot changes the colour of any curve, colours all curves from a colour
  map (e.g. viridis for a sweep), or changes the colour map of the strain map.
  Label and colour edits are kept when the plot redraws and are saved with
  the session.
- **Import parameter file** (File menu): reads a text file written the
  notebook way (see `examples/Parameter_info_example.txt`):
  `CFG = dict(...)` for the experiment settings,
  `SAMPLE = [("Si3N4", 9.37, dict(nfile=..., pe=..., rho=..., ...)), ...,
  ("Si", None, dict(...))]` for the stack, and optionally a
  `LITERATURE = {...}` table. Values are in the model's units (nm, ps, SI).
  Nothing in the file is executed. Blocks are recognised by their content,
  and any other text is ignored.
  - `nfile="path/name"` uses a variable defined in the file
    (`path = C:\...\materials`, quotes optional). The file is found with or
    without `.txt`. If it isn't found, *Materials folder…* in the review
    window looks for it by name in another folder.
  - Properties the stack doesn't give are taken from the file's
    `LITERATURE` table, else from the built-in one, and are marked as
    literature placeholders.
  - Before anything changes, a review window lists every value with its unit
    and source (file / literature / missing), plus notes such as the file's
    `# comments` and any warnings. *Apply to the GUI* fills the fields; then
    check them in the tabs.
- **File menu**:
  - import an fs-sonar `conf_file_*.txt`. Thicknesses, n,k files and peCoef
    come from the conf; the other constants come from the folder's
    `properties.txt` / `*.prop` files, or else from literature;
  - save or open the whole session as JSON;
  - export the selected run or a sweep, with the Output tab's choices.

Runs execute in the background with a progress bar and a Cancel button.
