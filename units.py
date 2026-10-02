"""Unit menus for the GUI and conversion to the model's internal units.

Internal (model) units, as used throughout diffr_model.py:
    wavelength, thickness, grid lengths   nm
    times                                 ps
    everything else                       SI (kg/m^3, m/s, J/(kg K), 1/K, Pa, ...)

Every unit is a multiplicative factor to the internal unit, except eV for
wavelength, which is converted with lambda[nm] = hc / E.
"""

HC_EV_NM = 1239.841984          # h c / e in eV nm (same constant as diffr_model)

# kind -> {unit label: factor to internal unit}; first entry is the internal unit
UNITS = {
    "wavelength":      {"nm": 1.0, "µm": 1e3, "Å": 0.1, "eV": "eV"},
    "thickness":       {"nm": 1.0, "µm": 1e3, "Å": 0.1, "m": 1e9},
    "grid":            {"nm": 1.0, "µm": 1e3, "Å": 0.1},
    "time":            {"ps": 1.0, "fs": 1e-3, "ns": 1e3},
    "tau":             {"ps": 1.0, "fs": 1e-3},
    "fluence":         {"J/m²": 1.0, "mJ/cm²": 10.0, "µJ/cm²": 0.01},
    "density":         {"kg/m³": 1.0, "g/cm³": 1e3},
    "velocity":        {"m/s": 1.0, "km/s": 1e3, "nm/ps": 1e3},
    "heat_capacity":   {"J/(kg·K)": 1.0, "J/(g·K)": 1e3},
    "expansion":       {"1/K": 1.0, "1e-6/K (ppm/K)": 1e-6},
    "modulus":         {"Pa": 1.0, "GPa": 1e9, "Mbar": 1e11},
    "e_heat_capacity": {"J/(m³·K)": 1.0, "J/(cm³·K)": 1e6},
    "per_kelvin":      {"1/K": 1.0, "1e-4/K": 1e-4, "1e-6/K": 1e-6},
    "dimensionless":   {"–": 1.0},
}


def base_unit(kind):
    """The internal unit of a quantity kind."""
    return next(iter(UNITS[kind]))


def choices(kind):
    return list(UNITS[kind])


def to_base(value, kind, unit):
    """value given in `unit` -> value in the internal unit. Complex is fine
    for multiplicative units."""
    try:
        f = UNITS[kind][unit]
    except KeyError:
        raise ValueError(f"unknown unit {unit!r} for {kind}; "
                         f"choose one of {choices(kind)}") from None
    if f == "eV":
        if value <= 0:
            raise ValueError("a photon energy must be > 0 eV")
        return HC_EV_NM / value
    return value * f


def from_base(value, kind, unit):
    """Inverse of to_base."""
    f = UNITS[kind][unit]
    if f == "eV":
        return HC_EV_NM / value
    return value / f


def fmt(x):
    """Compact text for a real or complex number."""
    if isinstance(x, complex):
        if x.imag == 0:
            return f"{x.real:.6g}"
        return f"{x.real:.6g}{x.imag:+.6g}i"
    return f"{x:.6g}"
