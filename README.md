# diffR thin-film model — GUI

An interactive desktop GUI (Tkinter + matplotlib) for the pump–probe
differential-reflectivity forward model in
`diffR_thinfilm_model_custom.ipynb`.

```
pip install -r requirements.txt     # numpy, matplotlib (tkinter ships with Python)
python diffr_gui.py
```

## Files

| File | What it is |
|---|---|
| `diffr_gui.py` | The GUI. Run this. |
| `sample_input.py` | Turns what the user typed (numbers + units) into model units, builds the sample, imports conf files, saves/loads sessions. No GUI code. |
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
  layer is the semi-infinite substrate. For each layer give:
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
  step grid, and the tab shows the resulting list of runs. The curves are
  overlaid. To run many simulations unattended, tick *Save each run's
  reflectivity file*, choose a folder and a file-name pattern such as
  `dRR_{material}_{d}{unit}`. The fields are `{material}`, `{layer}`, `{d}`,
  `{unit}`, `{i}` (run number) and `{n}` (number of runs). Each run is then
  written as `.csv` + `.npz` as soon as it finishes, so a cancelled sweep
  keeps what was done. Optionally one combined file holds all the
  thicknesses (`{start}`, `{end}`, `{step}` are available for its name). The
  tab previews the file names, refuses patterns that would give two runs the
  same name, and asks before overwriting existing files.
- **Plots**: the stack diagram and the total ΔR/R are always shown. Click a
  layer in the diagram to edit it. Tick *Components*, *Kernels & absorption*
  or *Strain map η(z,t)* for more tabs (the strain map must be ticked before
  the run). Every plot has the matplotlib toolbar (zoom, pan, home, back,
  save image), mouse-wheel zoom, and *Axes & labels…* for the title, axis
  labels, limits, linear/log scale, grid, legend and font size. Your label
  edits are kept when the plot redraws.
- **File menu**:
  - import an fs-sonar `conf_file_*.txt`. Thicknesses, n,k files and peCoef
    come from the conf; the other constants come from the folder's
    `properties.txt` / `*.prop` files, or else from literature;
  - save or open the whole session as JSON;
  - export the selected run (`.npz` + `.csv`; the metadata holds every input
    with its unit and the converted material values);
  - export a sweep (`.csv` + `.npz`).

Runs execute in the background with a progress bar and a Cancel button.
