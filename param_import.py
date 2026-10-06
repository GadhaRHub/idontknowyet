"""Read a parameter / stack description written the notebook way, e.g.

    CFG = dict(lambda_pump_nm = 960.0, pump_fluence_J_m2 = 12.7, ...)
    SAMPLE = [("Si3N4", 9.37, dict(nfile="path/krystof_Si3N4", pe=0.047,
                                  rho=2850., v=9679., ...)), ...,
              ("Si", None, dict(...))]
    LITERATURE = {"Si3N4": dict(rho=3200., ...), ...}

and turn it into GUI inputs (config entries, flags and layer dicts) plus a
review table saying where every value came from.

Nothing in the file is executed: the blocks are parsed with `ast` and only
numbers, strings, None/True/False, complex arithmetic (3.0 + 1.0j), tuples,
lists, dicts and dict(...) are accepted. Other text in the file (headings,
notes) is ignored. Blocks are recognised by their content, not their names:
  * a dict whose keys are experiment settings (lambda_pump_nm, t_max_ps, ...)
  * a list of (material, thickness_nm or None[, dict(overrides)]) tuples
  * a dict of material -> dict(properties)  (a literature table)
Values are in the model's units (nm, ps, SI), as in the notebook.
"""

import ast
import glob
import os
import re

import diffr_model as M
import sample_input as S
import units as U

CFG_KEYS = {f[0] for f in S.CFG_FIELDS}
FLAG_KEYS = {f[0] for f in S.PHYSICS_FLAGS}
_CFG_INFO = {f[0]: f for f in S.CFG_FIELDS}
_LAYER_KEYS = set(S.PROP_INFO) | {"nfile", "k", "k_pump", "k_probe",
                                  "n_pump", "n_probe"}


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------------------
# Safe parsing
# ---------------------------------------------------------------------------

def _value(node):
    """Evaluate a literal-ish expression node without executing code."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex, str, bool)) or node.value is None:
            return node.value
    elif isinstance(node, ast.Name) and node.id in ("None", "True", "False",
                                                    "inf", "nan"):
        return {"None": None, "True": True, "False": False,
                "inf": float("inf"), "nan": float("nan")}[node.id]
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _value(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    elif isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
        a, b = _value(node.left), _value(node.right)
        if not all(isinstance(x, (int, float, complex)) and not isinstance(x, bool)
                   for x in (a, b)):
            raise ValueError("arithmetic on non-numbers")
        return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b,
                ast.Mult: lambda: a * b, ast.Div: lambda: a / b,
                ast.Pow: lambda: a ** b}[type(node.op)]()
    elif isinstance(node, (ast.Tuple, ast.List)):
        return [_value(e) for e in node.elts]
    elif isinstance(node, ast.Dict):
        return {_value(k): _value(v) for k, v in zip(node.keys, node.values)}
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
          and node.func.id in ("dict", "complex")):
        if node.func.id == "dict" and not node.args:
            return {kw.arg: _value(kw.value) for kw in node.keywords}
        if node.func.id == "complex" and not node.keywords:
            return complex(*[_value(a) for a in node.args])
    raise ValueError(f"unsupported expression: {ast.unparse(node)[:60]}")


def _blocks(text):
    """[(name, expression text, first line number)] for every
    `NAME = <bracketed expression>` in the text."""
    out = []
    for m in re.finditer(r"(?m)^[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*", text):
        i = m.end()
        j = i
        while j < len(text) and (text[j].isalnum() or text[j] == "_"):
            j += 1                                  # dict( / complex( …
        if j >= len(text) or text[j] not in "([{":
            continue
        depth, k, in_str, quote = 0, j, False, ""
        while k < len(text):
            c = text[k]
            if in_str:
                if c == "\\":
                    k += 1
                elif c == quote:
                    in_str = False
            elif c in "'\"":
                in_str, quote = True, c
            elif c == "#":
                nl = text.find("\n", k)
                k = len(text) if nl < 0 else nl
                continue
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if depth != 0:
            continue
        out.append((m.group(1), text[i:k + 1], text.count("\n", 0, m.start()) + 1))
    return out


def _comments(block):
    """{key: comment} for 'key = value,  # comment' lines, and the comment of
    every line in order (for list items)."""
    by_key, lines = {}, []
    for line in block.splitlines():
        code, sep, com = line.partition("#")
        com = com.strip() if sep else ""
        m = re.match(r"\s*([A-Za-z_]\w*)\s*=", code)
        if m and com:
            by_key[m.group(1)] = com
        lines.append((code.strip(), com))
    return by_key, lines


def _variables(text):
    """Simple one-line assignments such as
        path = C:\\Users\\me\\materials        (quotes optional)
    -> {"path": "C:\\Users\\me\\materials"}. Used to expand nfile="path/…"."""
    out = {}
    for m in re.finditer(r"(?m)^[ \t]*([A-Za-z_]\w*)[ \t]*=[ \t]*(.+?)[ \t]*$", text):
        rhs = m.group(2)
        if rhs[0] in "([{" or re.match(r"(dict|complex)\s*\(", rhs):
            continue
        rhs = re.split(r"\s+#", rhs)[0].strip()          # trailing comment
        if not rhs or rhs.endswith(","):                 # inside a dict(...)
            continue
        q = re.match(r"""^[rRuU]?(['"])(.*)\1$""", rhs)
        if q:
            rhs = q.group(2)
        out[m.group(1)] = rhs
    return out


def expand_path(nfile, variables):
    """'path/krystof_Si' with path = C:\\x -> 'C:\\x/krystof_Si'; also accepts
    {path}/…, $path/… and path\\…"""
    m = re.match(r"^(?:\{(\w+)\}|\$(\w+)|(\w+))[\\/](.*)$", nfile)
    if m:
        name = m.group(1) or m.group(2) or m.group(3)
        if name in variables:
            base = variables[name].rstrip("\\/")
            sep = "\\" if "\\" in base else "/"
            return base + sep + m.group(4), name
    return nfile, None


def parse_text(text):
    """Find the config, stack and literature blocks. Returns a dict with
    cfg, cfg_notes, sample, sample_notes, literature, variables, names,
    warnings."""
    found = dict(cfg=None, cfg_notes={}, sample=None, sample_notes=[],
                 literature=None, names={}, warnings=[],
                 variables=_variables(text))
    for name, expr, line in _blocks(text):
        try:
            val = _value(ast.parse(expr.strip(), mode="eval").body)
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError) as e:
            found["warnings"].append(f"line {line}: {name} could not be read "
                                     f"({e}); skipped")
            continue
        if isinstance(val, dict) and len(CFG_KEYS & set(val)) >= 2:
            kind = "cfg"
        elif (isinstance(val, list) and val and
              all(isinstance(r, list) and len(r) >= 2 and isinstance(r[0], str)
                  for r in val)):
            kind = "sample"
        elif (isinstance(val, dict) and val and
              all(isinstance(k, str) and isinstance(v, dict) for k, v in val.items())):
            kind = "literature"
        else:
            continue
        if found[kind] is not None:
            found["warnings"].append(f"line {line}: a second {kind} block "
                                     f"({name}) was found; the later one is used")
        found[kind] = val
        found["names"][kind] = (name, line)
        if kind == "cfg":
            found["cfg_notes"] = _comments(expr)[0]
        elif kind == "sample":
            # one comment per stack entry: lines that start a tuple
            notes = [c for code, c in _comments(expr)[1]
                     if code.lstrip("[").lstrip().startswith(("(", "["))]
            found["sample_notes"] = notes
    return found


# ---------------------------------------------------------------------------
# Building GUI inputs + the review table
# ---------------------------------------------------------------------------

def find_nfile(nfile, base_dirs):
    """Path of a dispersion file named in the parameter file, or None.
    Tries the path as given (+ .txt), then its basename in each folder."""
    cands = []
    for d in [""] + [b for b in base_dirs if b]:
        p = nfile if (os.path.isabs(nfile) or not d) else os.path.join(d, nfile)
        cands += [p, p + ".txt"]
    if os.sep == "/" and "\\" in nfile:          # a Windows path read elsewhere
        cands += [nfile.replace("\\", "/"), nfile.replace("\\", "/") + ".txt"]
    stem = os.path.basename(nfile.replace("\\", "/"))
    for d in base_dirs:
        if d:
            cands += [os.path.join(d, stem), os.path.join(d, stem + ".txt")]
            cands += sorted(glob.glob(os.path.join(glob.escape(d),
                                                   glob.escape(stem) + ".*")))
    for p in cands:
        if os.path.isfile(p):
            return os.path.normpath(p)
    return None


def _fmt(x):
    if isinstance(x, complex):
        return U.fmt(x)
    if isinstance(x, float):
        return f"{x:.10g}"
    return str(x)


def build(found, current_cfg=None, base_dirs=(), cache=None):
    """GUI inputs from parse_text() output.

    Returns dict(config, flags, layers, rows, warnings) where rows are
    (section, item, value, unit, source, note) for the review table. Sources:
    'file', 'literature (file)', 'literature (built-in)', 'MISSING',
    'not in file (kept)'.
    """
    cache = cache or S.DispersionCache()
    rows, warnings = [], list(found["warnings"])
    config, flags = {}, {}

    # --- experiment settings
    cfg_in = found["cfg"] or {}
    notes = found["cfg_notes"]
    for key, label, kind, _, _, _ in S.CFG_FIELDS:
        if key in cfg_in:
            v = cfg_in[key]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                warnings.append(f"{key} = {v!r} is not a number; not imported")
                continue
            config[key] = dict(value=_fmt(float(v)), unit=U.base_unit(kind))
            rows.append(("Experiment", label, _fmt(float(v)), U.base_unit(kind),
                         "file", notes.get(key, "")))
        elif found["cfg"] is not None:
            rows.append(("Experiment", label, "", "", "not in file (kept)", ""))
    for key, label in S.PHYSICS_FLAGS:
        if key in cfg_in:
            flags[key] = bool(cfg_in[key])
            rows.append(("Experiment", label, "on" if flags[key] else "off", "",
                         "file", notes.get(key, "")))
    for key in cfg_in:
        if key not in CFG_KEYS and key not in FLAG_KEYS:
            warnings.append(f"setting {key} is not used by the GUI; ignored")

    # wavelengths for the n,k preview: the file's, else the current ones
    lam = {}
    for w in ("pump", "probe"):
        k = f"lambda_{w}_nm"
        lam[w] = (float(cfg_in[k]) if k in cfg_in else
                  (current_cfg or {}).get(k))

    # --- stack
    lit_file = found["literature"] or {}
    layers = []
    sample = found["sample"] or []
    snotes = found["sample_notes"]
    for i, spec in enumerate(sample):
        name, d = spec[0], spec[1]
        over = dict(spec[2]) if len(spec) > 2 and isinstance(spec[2], dict) else {}
        last = i == len(sample) - 1
        sec = f"Layer {i + 1}: {name}"
        note = snotes[i] if i < len(snotes) else ""
        if d is None and not last:
            warnings.append(f"{sec}: thickness None (substrate) but it is not "
                            f"the last layer; give it a thickness")
        if d is not None and last:
            warnings.append(f"{sec}: the last layer is used as the semi-infinite "
                            f"substrate; its thickness {d:g} nm is ignored")
        lay = S.new_layer(name, "" if (d is None or last) else _fmt(float(d)))
        rows.append((sec, "Thickness", "substrate (∞)" if last else _fmt(d),
                     "" if last else "nm", "file", note))
        src = {}

        for key, val in over.items():
            if key not in _LAYER_KEYS:
                warnings.append(f"{sec}: {key} is not a layer property the GUI "
                                f"knows; ignored")
                continue
            try:
                if key == "nfile":
                    full, var = expand_path(str(val), found.get("variables", {}))
                    path = find_nfile(full, base_dirs)
                    lay["nk_mode"] = "file"
                    lay["nk_file"] = path or full
                    src["nfile"] = (str(val), path, full, var)
                elif key in ("n_pump", "n_probe"):
                    v = complex(val)
                    w = key.split("_")[1]
                    lay[f"n_{w}"], lay[f"k_{w}"] = _fmt(v.real), _fmt(v.imag)
                    src[key] = v
                elif key in ("k", "k_pump", "k_probe"):
                    for w in ("pump", "probe"):
                        if key in ("k", f"k_{w}"):
                            lay[f"kfill_{w}"] = lay[f"k_{w}"] = _fmt(float(val))
                    src[key] = val
                elif key == "pe":
                    lay["pe_mode"] = "pe"
                    v = complex(val)
                    lay["pe_re"], lay["pe_im"] = _fmt(v.real), _fmt(v.imag)
                    src["pe"] = val
                else:
                    S._set_prop(lay, key, val, S.PROP_INFO[key][3])
                    src[key] = val
            except (TypeError, ValueError) as e:
                warnings.append(f"{sec}: {key} = {val!r} could not be used ({e})")
        if "n_pump" in src or "n_probe" in src:
            if "nfile" not in src:
                lay["nk_mode"] = "typed"

        # literature for whatever the file leaves open: the file's own table
        # first, then the built-in one; both are marked as placeholders
        lit_src = {}
        if name in lit_file:
            for q in S.apply_literature(lay, only_empty=True, table=lit_file):
                lit_src[q] = "literature (file)"
        for q in S.apply_literature(lay, only_empty=True):
            lit_src.setdefault(q, "literature (built-in)")
        if "pe" in src:
            lay["pe_mode"] = "pe"

        # --- review rows for this layer
        if lay["nk_mode"] == "file":
            given, path, full, var = src.get("nfile", ("", None, "", None))
            via = f" with {var} = {found['variables'][var]}" if var else ""
            if path:
                rows.append((sec, "n,k file", path, "", "file",
                             f"named '{given}' in the file{via}"))
                try:
                    tab = cache.get(path, "auto")
                    for w in ("pump", "probe"):
                        if lam[w]:
                            n, k, _ = M.interp_nk(tab, lam[w])
                            if k is None:
                                k = lay[f"kfill_{w}"] or "?"
                            rows.append((sec, f"n, k at {w} ({lam[w]:g} nm)",
                                         f"{n:.6g}, {k if isinstance(k, str) else f'{k:.6g}'}",
                                         "", "from n,k file", ""))
                except Exception as e:
                    rows.append((sec, "n,k file", "", "", "MISSING",
                                 f"cannot be used: {e}"))
            else:
                rows.append((sec, "n,k file", full or given, "", "MISSING",
                             f"not found (looked for {full} and {full}.txt"
                             f"{via}) — choose the materials folder, or browse "
                             f"to it in the Layers tab"))
        else:
            for w in ("pump", "probe"):
                if lay[f"n_{w}"]:
                    rows.append((sec, f"n, k at {w}",
                                 f"{lay[f'n_{w}']}, {lay[f'k_{w}'] or '?'}", "",
                                 "file", ""))
                else:
                    rows.append((sec, f"n, k at {w}", "", "", "MISSING",
                                 "type n, k or choose a dispersion file"))
        for key, label, kind, _, required, default in S.REAL_PROPS + S.COMPLEX_PROPS:
            if key == "pe" and lay["pe_mode"] != "pe":
                continue
            if key == "dn_deta" and lay["pe_mode"] == "pe":
                continue
            fields = S._prop_fields(key)
            if key in S.PROP_INFO and key not in [p[0] for p in S.COMPLEX_PROPS]:
                val = lay[key]
            else:
                re_, im_ = lay[key + "_re"], lay[key + "_im"]
                val = "" if not (re_ or im_) else (
                    re_ if (im_ in ("", "0")) else f"{re_} + {im_}i")
            unit = lay[fields[-1]] if fields[-1].endswith("_unit") else ""
            if key in src:
                source = "file"
            elif key in lit_src:
                source = lit_src[key]
            elif val:
                source = "file"
            elif required:
                source = "MISSING"
            else:
                source = "blank (default)"
                val = U.fmt(default)
                unit = U.base_unit(kind)
            rows.append((sec, label, val, unit if val else "", source,
                         "converted to dñ/dη = −p·ñ_probe³/2 at run time"
                         if key == "pe" else ""))
        layers.append(lay)

    if found["sample"] is None:
        warnings.append("no stack (list of (material, thickness, …) entries) "
                        "found; the current layers are kept")
    if found["cfg"] is None:
        warnings.append("no experiment settings (CFG) found; the current ones "
                        "are kept")
    return dict(config=config, flags=flags, layers=layers if sample else None,
                rows=rows, warnings=warnings)


def read_file(path, current_cfg=None, materials_dir=None, cache=None):
    text, _ = M.read_text_lines(path)
    found = parse_text("\n".join(text))
    if found["cfg"] is None and found["sample"] is None:
        raise ImportError_("No experiment settings (CFG = dict(...)) and no "
                           "stack (SAMPLE = [(...), ...]) were found in the file.")
    dirs = [os.path.dirname(os.path.abspath(path))]
    if materials_dir:
        dirs.insert(0, materials_dir)
    out = build(found, current_cfg, dirs, cache)
    out["found"] = found
    return out
