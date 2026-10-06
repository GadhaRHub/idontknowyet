"""Double-exponential background subtraction for dR/R traces.

minus_exp_fun_single3 is the user's function, unchanged. apply() wraps it for
the GUI: it applies the delay cut-off, and builds full-length columns for the
output file (the original trace keeps its full time axis; points before the
cut-off are filled as the user chooses).
"""

import numpy as np
from scipy.optimize import curve_fit


def minus_exp_fun_single3(time, signal, showfig=False, force_decay=True):
    """
    Subtract a double-exponential background from a 1D signal.

    Model:
        f(t) = a*exp(b*t) + c*exp(d*t)

    This version is compatible with your notebook call:
        diffRs_minus_fit, exp_fit_params = minus_exp_fun_single2(delay[mask], diffRs[mask])

    Parameters
    ----------
    time : array_like
        Time axis.
    signal : array_like
        Signal to subtract the exponential background from.
    showfig : bool, optional
        If True, shows raw data and fit/subtraction plots.
    force_decay : bool, optional
        If True, forces b <= 0 and d <= 0, which is usually correct
        for decay/background subtraction. Set False if you need rising
        exponentials as well.

    Returns
    -------
    signal_subtracted : np.ndarray
        Input signal with fitted exponential background removed.
    fit_params : tuple or None
        Fitted parameters (a, b, c, d), or None if fitting failed.
    """
    time = np.asarray(time, dtype=float).ravel()
    signal = np.asarray(signal, dtype=float).ravel()

    if time.size != signal.size:
        raise ValueError("time and signal must have the same number of elements.")

    # Optional raw-data plot
    if showfig:
        import matplotlib.pyplot as plt
        plt.figure()
        plt.plot(time, signal)
        plt.grid(True)
        plt.title("Raw data")

    fit_params = None
    fitData = None
    signal_subtracted = None
    done = False

    # Empty input -> return empty arrays
    if time.size == 0:
        signal_subtracted = signal.copy()
        fitData = np.array([], dtype=float)

    else:
        mask = np.isfinite(time) & np.isfinite(signal)
        mean_value = float(np.mean(signal[mask])) if np.any(mask) else 0.0

        # Need at least 4 finite points to fit 4 parameters
        if np.count_nonzero(mask) >= 4:
            t = time[mask]
            y = signal[mask]

            t_min = float(np.min(t))
            t_max = float(np.max(t))
            span = t_max - t_min

            if span > 0 and np.isfinite(span):
                # Normalize time internally for numerical stability.
                # Returned parameters are converted back to original time units.
                scale = span
                x = (t - t_min) / scale

                def _model(x_vals, a, b, c, d):
                    bx = np.clip(b * x_vals, -700, 700)
                    dx = np.clip(d * x_vals, -700, 700)
                    return a * np.exp(bx) + c * np.exp(dx)

                def _prony_seed():
                    """
                    Estimate initial decay rates using a simple Prony/linear-prediction idea.
                    This is useful when the signal oscillates or changes sign.
                    """
                    if y.size < 4:
                        raise ValueError("Not enough points for Prony seeding.")

                    dt = float(np.median(np.diff(t)))
                    if not np.isfinite(dt) or dt <= 0:
                        raise ValueError("Invalid time spacing for Prony seeding.")

                    # Linear prediction: y[n] = c1*y[n-1] + c2*y[n-2]
                    A = np.column_stack((y[1:-1], y[:-2]))
                    rhs = y[2:]

                    if A.shape[0] < 2:
                        raise ValueError("Not enough samples for linear prediction.")

                    c1, c2 = np.linalg.lstsq(A, rhs, rcond=None)[0]

                    roots = np.roots([1.0, -c1, -c2])

                    with np.errstate(divide="ignore", invalid="ignore"):
                        rates = np.real(np.log(roots.astype(complex)) / dt)

                    if not np.all(np.isfinite(rates)):
                        raise ValueError("Non-finite Prony rates.")

                    b_orig, d_orig = sorted(rates, reverse=True)

                    if force_decay:
                        if b_orig >= 0:
                            b_orig = -abs(b_orig) if abs(b_orig) > 1e-12 else -1.0 / span
                        if d_orig >= 0:
                            d_orig = -abs(d_orig) if abs(d_orig) > 1e-12 else -10.0 / span

                    b_norm = b_orig * scale
                    d_norm = d_orig * scale

                    if np.isclose(b_norm, d_norm, rtol=1e-6, atol=1e-12):
                        d_norm = 2.0 * b_norm if b_norm != 0 else -1.0

                    M = np.column_stack((
                        np.exp(np.clip(b_norm * x, -700, 700)),
                        np.exp(np.clip(d_norm * x, -700, 700))
                    ))

                    a_norm, c_norm = np.linalg.lstsq(M, y, rcond=None)[0]

                    if not np.all(np.isfinite([a_norm, c_norm])):
                        raise ValueError("Non-finite Prony amplitudes.")

                    return [a_norm, b_norm, c_norm, d_norm]

                # Heuristic guesses
                tail_len = max(1, y.size // 10)
                baseline_guess = float(np.mean(y[-tail_len:]))
                amplitude_guess = float(y[0] - baseline_guess)

                if not np.isfinite(amplitude_guess) or amplitude_guess == 0:
                    amplitude_guess = float(np.ptp(y))

                if amplitude_guess == 0 or not np.isfinite(amplitude_guess):
                    amplitude_guess = 1.0

                guesses = []

                # Try Prony-based seed first
                try:
                    guesses.append(_prony_seed())
                except Exception:
                    pass

                # Additional robust starting points
                guesses.extend([
                    [amplitude_guess, -1.0, baseline_guess, -0.1],
                    [amplitude_guess, -5.0, baseline_guess, -0.5],
                    [amplitude_guess, -0.3, baseline_guess, -3.0],
                    [amplitude_guess, -10.0, baseline_guess, -1.0],
                    [amplitude_guess, -1.0, 0.0, -0.1],
                    [float(np.mean(y)), -1.0, float(np.mean(y)), -0.1],
                ])

                if force_decay:
                    lower_bounds = [-np.inf, -1000.0, -np.inf, -1000.0]
                    upper_bounds = [np.inf, 0.0, np.inf, 0.0]
                else:
                    lower_bounds = [-np.inf, -np.inf, -np.inf, -np.inf]
                    upper_bounds = [np.inf, np.inf, np.inf, np.inf]

                def _prepare_p0(p0):
                    p0 = np.asarray(p0, dtype=float)
                    if p0.size != 4 or not np.all(np.isfinite(p0)):
                        return None

                    if force_decay:
                        if p0[1] >= 0:
                            p0[1] = -abs(p0[1]) if abs(p0[1]) > 1e-12 else -1.0
                        if p0[3] >= 0:
                            p0[3] = -abs(p0[3]) if abs(p0[3]) > 1e-12 else -0.1

                        if np.isclose(p0[1], p0[3], rtol=1e-6, atol=1e-12):
                            p0[3] = 2.0 * p0[1] if p0[1] != 0 else -1.0

                    p0_out = p0.copy()

                    for i in range(4):
                        lo = lower_bounds[i]
                        hi = upper_bounds[i]

                        if np.isfinite(lo) and p0_out[i] <= lo:
                            p0_out[i] = lo + 1e-9 * max(1.0, abs(lo))

                        if np.isfinite(hi) and p0_out[i] >= hi:
                            p0_out[i] = hi - 1e-9 * max(1.0, abs(hi))

                    return p0_out

                best_params = None
                best_cost = np.inf

                for p0 in guesses:
                    p0 = _prepare_p0(p0)
                    if p0 is None:
                        continue

                    try:
                        if force_decay:
                            popt, _ = curve_fit(
                                _model,
                                x,
                                y,
                                p0=p0,
                                bounds=(lower_bounds, upper_bounds),
                                maxfev=20000
                            )
                        else:
                            popt, _ = curve_fit(
                                _model,
                                x,
                                y,
                                p0=p0,
                                maxfev=20000
                            )

                        y_hat = _model(x, *popt)
                        cost = float(np.sum((y - y_hat) ** 2))

                        if np.isfinite(cost) and cost < best_cost:
                            best_cost = cost
                            best_params = popt

                    except Exception:
                        pass

                if best_params is not None:
                    try:
                        # Fit curve on full original time axis
                        x_all = (time - t_min) / scale
                        fitData = _model(x_all, *best_params)
                        signal_subtracted = signal - fitData

                        # Convert normalized parameters back to original time units
                        a_norm, b_norm, c_norm, d_norm = best_params

                        b_orig = float(b_norm / scale)
                        d_orig = float(d_norm / scale)

                        a_orig = float(a_norm * np.exp(np.clip(-b_orig * t_min, -700, 700)))
                        c_orig = float(c_norm * np.exp(np.clip(-d_orig * t_min, -700, 700)))

                        fit_params = (a_orig, b_orig, c_orig, d_orig)

                        done = bool(
                            np.all(np.isfinite(fitData[np.isfinite(time)])) and
                            np.all(np.isfinite(np.array(fit_params)))
                        )

                    except Exception:
                        done = False

        # Fallback: subtract mean if fit failed
        if not done:
            print("Fitting was not successful — subtracting mean instead.")
            fitData = np.full_like(time, mean_value, dtype=float)
            signal_subtracted = signal - mean_value
            fit_params = None

    # Optional result plot
    if showfig:
        import matplotlib.pyplot as plt

        plt.figure()
        plt.plot(time, signal)
        if fitData is not None:
            plt.plot(time, fitData, "--")
        if signal_subtracted is not None:
            plt.plot(time, signal_subtracted, "k")

        if done:
            plt.legend(["data", "fitted exp.fce", "subtracted fit"])
        else:
            plt.legend(["data", "mean", "subtracted mean"])

        plt.grid(True)
        plt.title("Exponential background subtraction")
        plt.show()

    return signal_subtracted, fit_params


subtract_background = minus_exp_fun_single3

BEFORE_CHOICES = {
    "NaN (empty)": "nan",
    "original ΔR/R": "original",
    "extrapolated fit subtracted": "extrapolated",
}


def apply(t_ps, drr, use_cut=True, cut_ps=10.0, force_decay=True,
          before="nan"):
    """Fit and subtract the background from one trace.

    The fit uses the points with t > cut_ps (all points if use_cut is False),
    exactly as `mask = delay > cut; minus_exp_fun_single3(delay[mask],
    diffRs[mask])`. Returns a dict with full-length arrays (same length as
    t_ps):
        sub   ΔR/R − background (points before the cut-off: see `before`)
        fit   the fitted background (NaN before the cut-off unless
              'extrapolated')
    plus params (a, b, c, d) in 1/ps for b, d, or None when the fit failed
    and the mean was subtracted, the cut-off and the number of points used.
    """
    t = np.asarray(t_ps, float)
    y = np.asarray(drr, float)
    mask = t > cut_ps if use_cut else np.ones(t.size, bool)
    n = int(mask.sum())
    if n < 4:
        raise ValueError(f"only {n} points after the cut-off "
                         f"({cut_ps:g} ps); at least 4 are needed")
    sub_m, params = minus_exp_fun_single3(t[mask], y[mask],
                                          force_decay=force_decay)
    fit_m = y[mask] - sub_m
    sub = np.full_like(y, np.nan)
    fit = np.full_like(y, np.nan)
    sub[mask], fit[mask] = sub_m, fit_m
    if use_cut and before != "nan":
        early = ~mask
        if before == "original":
            sub[early] = y[early]
        elif before == "extrapolated":
            if params is not None:
                a, b, c, d = params
                with np.errstate(over="ignore", invalid="ignore"):
                    f_early = (a * np.exp(np.clip(b * t[early], -700, 700))
                               + c * np.exp(np.clip(d * t[early], -700, 700)))
            else:
                f_early = np.full(early.sum(), float(np.mean(fit_m)))
            fit[early] = f_early
            sub[early] = y[early] - f_early
    return dict(sub=sub, fit=fit, params=params, use_cut=use_cut,
                cut_ps=cut_ps if use_cut else None, n_points=n,
                force_decay=force_decay, before=before)


def describe(bg):
    """The line the notebook printed."""
    p = bg["params"]
    if p is None:
        return "Exponential fit failed — mean subtracted instead."
    return (f"Exponential fit params: a={p[0]:.4e}, b={p[1]:.4e}, "
            f"c={p[2]:.4e}, d={p[3]:.4e}  (b, d in 1/ps)")
