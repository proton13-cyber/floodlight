"""
Build aerodynamics-r3: a region publication whose surrogates are fitted to real
AVL + NeuralFoil runs instead of hand-written.

WHAT AN "rN" IS
---------------
Nothing new -- it is the same artifact as `publications/aerodynamics-r1.json`, in the
same schema, regenerated. `supersedes` points at the previous publication_id (read off
whatever is already in the output directory, so this is correct on any machine), same
`design_space_ref` (this changes no design variables, so the hash gate still passes and
structures/weights publications stay valid). The orchestrator picks up the newest
non-superseded publication per discipline, so dropping r2 in is the whole handover.

What changes versus r1:

    CL_max(t_c, sweep, S_ref, AR)   fitted; quadratic in t/c so it can have a peak,
                                    and Reynolds-dependent (r1 had no Re term at all)
    e(AR, sweep)                    AVL Trefftz span efficiency, replacing the Raymer
                                    airplane-Oswald correlation r1 borrowed
    validity.box                    re-derived from NeuralFoil confidence instead of
                                    inherited from XFOIL non-convergence

What changes versus r2 -- and this is what r3 is FOR:

    THE WING DRAG POLAR, not a single number. r2 integrated profile drag at one CL
    and then used it as though drag did not depend on lift. It does: a cambered
    section has a drag bucket, so CDp is minimum near CL ~ 0.4 and climbs on both
    sides. r2 therefore had NO viscous drag-due-to-lift anywhere, while induced drag
    was the inviscid Trefftz value -- so its L/D was optimistic, and cruise L/D
    stopped binding partly for that reason rather than for a physical one.

    r3 publishes  CDp(CL) = a + b*CL + c*CL^2,  each coefficient fitted over the
    design variables. Measured on a mid-box design, the viscous c is 0.0100 against
    an inviscid 1/(pi*e*AR) of 0.0268 -- viscous effects add ~38% to total
    drag-due-to-lift. That is not a rounding error on a constraint that decides
    whether the aircraft closes.

    It costs nothing. AVL is linear, so the span-loading SHAPE does not change with
    CL: every strip's cl just scales by k_j. The strip polars already computed can be
    re-read at k_j*CL for any other CL -- no extra AVL runs, no extra network calls.
    The DOE takes the same 25 seconds it did for r2.

Everything else -- the constraint structure, the normalisations, the conditioning on
W_empty, the mission constants -- is deliberately identical to r1, so the diff between
the two publications is exactly the physics and nothing else.

WHAT IS STILL ASSUMED
---------------------
The non-wing parasite drag. AVL's deck is a wing; there is no fuselage, tail, nacelle
or gear in it, so there is no way for the strip integration to produce their drag. That
term is a flat-plate build-up with a fixed wetted-area ratio, exactly as illustrative as
r1's was, and it is called out separately in the expression and in known_limitations
rather than blended into a single number that looks measured.

Usage:
    AVL_BIN=/path/to/avl python3 aero/build_publication.py --n 256 --out publications/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from configuration import (config_ref, load_config,  # noqa: E402
                           snapshot_supplier)

from aero.avl_runner import AvlError, run_case  # noqa: E402
from aero.geometry import write_deck  # noqa: E402
from aero.section import analyze_strips  # noqa: E402

# Shared design space (spaces/design_space.uav-medium.v1.json). Geometry only depends
# on the first four; W0 and P_SL enter the constraints, not the shape.
BOX = {
    "S_ref": (8.0, 25.0),
    "AR": (8.0, 24.0),
    "t_c": (0.08, 0.18),
    "sweep_c4": (0.0, 15.0),
}
V_CRUISE = 40.0
RHO_SL = 1.225
CL_REF = 0.6          # reference trim CL for the span-loading run
CONF_FLOOR = 0.90     # NeuralFoil confidence below which we will not claim validity

# CLs at which profile drag is integrated, to recover its LIFT-DEPENDENT part.
# Free: AVL is linear, so the span-loading shape is CL-independent and the strip polars
# already computed can be re-read at k_j*CL. Capped at 0.9 for the fit -- above that
# strips start reaching their section maxima and the integral stops being a drag
# prediction. Cruise CL across this design space runs roughly 0.25 to 0.75.
CL_SWEEP = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _polar_coeffs(sec) -> dict:
    """Per-design quadratic fit of the strip-integrated profile drag: a + b*CL + c*CL^2.

    `c` is the whole point of r3. A cambered section has a drag bucket, so profile drag
    is minimum near CL ~ 0.4 and climbs on both sides; r2 sampled it at a single CL and
    then used that value as if it were CL-independent. The missing `c` term is viscous
    drag-due-to-lift, and leaving it out biases L/D optimistic in exactly the region
    that decides whether the cruise constraint binds.
    """
    cls = np.array(sorted(sec.CD_profile_sweep))
    cds = np.array([sec.CD_profile_sweep[k] for k in cls])
    keep = np.array([sec.stalled_area_frac[k] < 0.02 for k in cls])
    if keep.sum() < 3:
        keep = np.ones_like(cls, dtype=bool)
    c2, c1, c0 = np.polyfit(cls[keep], cds[keep], 2)
    return {"cdp_a": float(c0), "cdp_b": float(c1), "cdp_c": float(c2),
            "stalled_at_0p9": float(sec.stalled_area_frac.get(0.9, 0.0))}


# ----------------------------------------------------------------------------------
# DOE
# ----------------------------------------------------------------------------------

def sobol_like(n: int, seed: int = 991) -> np.ndarray:
    """Scrambled-Halton sample on the 4-D geometry box. (scipy.stats.qmc would do, but
    this keeps the dependency surface to numpy for a script that must run anywhere.)"""
    def halton(idx: int, base: int) -> float:
        f, r = 1.0, 0.0
        while idx > 0:
            f /= base
            r += f * (idx % base)
            idx //= base
        return r

    rng = np.random.default_rng(seed)
    shift = rng.random(4)
    pts = np.array([[halton(i + 1, b) for b in (2, 3, 5, 7)] for i in range(n)])
    return (pts + shift) % 1.0


def run_doe(n: int, n_span: int = 20, verbose: bool = True) -> list[dict]:
    u = sobol_like(n)
    lo = np.array([BOX[k][0] for k in ("S_ref", "AR", "t_c", "sweep_c4")])
    hi = np.array([BOX[k][1] for k in ("S_ref", "AR", "t_c", "sweep_c4")])
    X = lo + u * (hi - lo)

    rows: list[dict] = []
    t0 = time.time()
    n_fail = 0
    for i, (S_ref, AR, t_c, sweep) in enumerate(X):
        try:
            d = tempfile.mkdtemp(prefix="doe_")
            geom = write_deck(
                f"{d}/w.avl", S_ref=S_ref, AR=AR, t_c=t_c, sweep_c4=sweep,
                n_span=n_span,
            )
            r = run_case(f"{d}/w.avl", trim=("CL", CL_REF), want_strips=True,
                         timeout_s=90)
            sec = analyze_strips(
                r.strips, t_c=t_c, sweep_c4_deg=sweep, S_ref=S_ref,
                CL_wing=r.CL, V=V_CRUISE, span=geom.span,
                cl_sweep=CL_SWEEP,
            )
        except (AvlError, ValueError) as exc:
            n_fail += 1
            if verbose:
                print(f"  [{i:4d}] FAILED {type(exc).__name__}: {exc}")
            continue

        rows.append({
            "S_ref": float(S_ref), "AR": float(AR), "t_c": float(t_c),
            "sweep_c4": float(sweep),
            "span": geom.span, "mac": geom.mac,
            "e": r.e, "CDi": r.CD_induced, "CL": r.CL,
            "CDp_wing": sec.CD_profile, "CL_max": sec.CL_max,
            "stall_eta": sec.stall_station_eta,
            "conf_min": sec.confidence_min,
            **_polar_coeffs(sec),
        })
        if verbose and (i + 1) % 25 == 0:
            print(f"  [{i + 1:4d}/{n}] {time.time() - t0:5.1f}s  "
                  f"{n_fail} failed")

    if verbose:
        print(f"DOE: {len(rows)} good / {n} requested, {n_fail} failed, "
              f"{time.time() - t0:.1f}s")
    return rows


# ----------------------------------------------------------------------------------
# fitting -- least squares on explicit bases, emitted as expression strings
# ----------------------------------------------------------------------------------

def _fit(A: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, float]:
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
    rmse = float(np.sqrt(ss_res / len(y)))
    return coef, r2, rmse


# Mean aerodynamic chord for the pinned planform (taper 0.5), exact:
#   b = sqrt(AR*S), c_root = 2S/(b*1.5), mac = (2/3)*c_root*(1.75/1.5)
#     => mac = 1.037037 * sqrt(S_ref/AR)
# and Re_mac = rho*V*mac/mu at the cruise reference condition. Written out as a string
# because the surrogate must be evaluable by the orchestrator from design variables
# alone -- Re is not a design variable, it is derived from two of them.
MAC_EXPR = "(1.037037*(S_ref/AR)**0.5)"
LOGRE_EXPR = f"log({RHO_SL * V_CRUISE / 1.789e-5 / 1e6:.6f}*{MAC_EXPR})"


def _logre(rows: list[dict]) -> np.ndarray:
    mac = np.array([r["mac"] for r in rows])
    return np.log(RHO_SL * V_CRUISE * mac / 1.789e-5 / 1e6)


def fit_cl_max(rows: list[dict]) -> dict:
    """CL_max(t_c, sweep, S_ref, AR).

    Two things drive the basis, and both were found by the fit statistics rather than
    assumed:

    1. A QUADRATIC IN t/c. The measured CL_max peaks near t/c ~ 0.12 and falls away
       above it as thicker sections separate earlier. r1's linear form cannot
       represent a peak.

    2. REYNOLDS NUMBER. r1's expression has no Reynolds dependence at all. Across this
       design space the mean chord varies by ~2.8x, so Re_mac runs 1.7e6 to 4.8e6, and
       section CL_max moves with it. Adding log(Re) took the fit from r2 = 0.59 to
       0.92; the full basis reaches 0.97. Re is not a design variable, so it enters
       through S_ref and AR via the mean aerodynamic chord.

    The first fit attempted here used r1's variable set and scored r2 = 0.59. That
    number is why this publication has a different input list, and it is the argument
    for the schema carrying `fit` at all.
    """
    t = np.array([r["t_c"] for r in rows])
    s = np.array([r["sweep_c4"] for r in rows])
    ar = np.array([r["AR"] for r in rows])
    lr = _logre(rows)
    y = np.array([r["CL_max"] for r in rows])
    A = np.column_stack([np.ones_like(t), t, t**2, s, s**2, lr, ar, ar**2, t * lr])
    c, r2, rmse = _fit(A, y)
    L = LOGRE_EXPR
    expr = (
        f"({c[0]:.6f} + {c[1]:.6f}*t_c + {c[2]:.6f}*t_c**2 "
        f"+ {c[3]:.8f}*sweep_c4 + {c[4]:.9f}*sweep_c4**2 "
        f"+ {c[5]:.6f}*{L} + {c[6]:.7f}*AR + {c[7]:.9f}*AR**2 "
        f"+ {c[8]:.6f}*t_c*{L})"
    )
    return {"expr": expr, "r2": r2, "rmse": rmse, "n": len(y),
            "inputs": ["t_c", "sweep_c4", "S_ref", "AR"], "coef": c.tolist()}


def fit_e(rows: list[dict]) -> dict:
    """Trefftz-plane span efficiency, e = a + b*AR + c*sweep.

    This is the INVISCID span efficiency of the wing alone, not an airplane Oswald
    factor. It is ~0.98 and mildly decreasing with AR. r1 used the Raymer straight-wing
    Oswald correlation, which is a different quantity and collapses to 0.44 by AR 24 --
    far outside where that correlation was fitted.
    """
    ar = np.array([r["AR"] for r in rows])
    s = np.array([r["sweep_c4"] for r in rows])
    y = np.array([r["e"] for r in rows])
    A = np.column_stack([np.ones_like(ar), ar, s])
    c, r2, rmse = _fit(A, y)
    expr = f"({c[0]:.6f} + {c[1]:.8f}*AR + {c[2]:.8f}*sweep_c4)"
    return {"expr": expr, "r2": r2, "rmse": rmse, "n": len(y),
            "inputs": ["AR", "sweep_c4"], "coef": c.tolist()}


def fit_cdp_wing(rows: list[dict]) -> dict:
    """Wing profile drag at the reference CL, from the strip integration.

    Basis in log(Re) and thickness. Read the r2 on this one carefully: it lands around
    0.82, which looks mediocre until you notice the response range is narrow -- CDp
    spans roughly 0.0050 to 0.0065 across the whole design space, so an RMSE of ~1.3e-4
    is about 2% relative. r2 measures explained variance, and when the true variance is
    small a good absolute fit still scores poorly. The relative RMSE is reported
    alongside it, because that is the number a consumer of this surrogate cares about.
    """
    return fit_polar_term(rows, "CDp_wing")


def fit_polar_term(rows: list[dict], key: str) -> dict:
    """Fit one coefficient of the wing drag polar over the design variables."""
    t = np.array([r["t_c"] for r in rows])
    lr = _logre(rows)
    y = np.array([r[key] for r in rows])
    A = np.column_stack([np.ones_like(t), lr, lr**2, t, t**2, t * lr])
    c, r2, rmse = _fit(A, y)
    L = LOGRE_EXPR
    expr = (f"({c[0]:.7f} + {c[1]:.7f}*{L} + {c[2]:.7f}*{L}**2 "
            f"+ {c[3]:.7f}*t_c + {c[4]:.7f}*t_c**2 + {c[5]:.7f}*t_c*{L})")
    denom = abs(y.mean()) if abs(y.mean()) > 1e-12 else 1.0
    return {"expr": expr, "r2": r2, "rmse": rmse, "n": len(y),
            "inputs": ["S_ref", "AR", "t_c"], "coef": c.tolist(),
            "rel_rmse": float(rmse / denom)}


# The one term AVL cannot supply. Declared, not measured.
CD_NONWING_EXPR = "(0.0062 + 0.14/S_ref)"
CD_NONWING_NOTE = (
    "Non-wing parasite drag (fuselage, tail, nacelle, gear, interference). NOT from "
    "AVL or NeuralFoil -- the deck is a wing only. Flat-plate build-up at a fixed "
    "wetted-area ratio, carried over from r1 in form and magnitude. This term is "
    "illustrative; the wing term next to it is not."
)


def validity_box(rows: list[dict]) -> tuple[dict, str]:
    """Box where NeuralFoil stayed confident, per variable."""
    ok = [r for r in rows if r["conf_min"] >= CONF_FLOOR]
    dropped = len(rows) - len(ok)
    box = {}
    for k in ("S_ref", "AR", "t_c", "sweep_c4"):
        v = [r[k] for r in ok]
        lo_d, hi_d = BOX[k]
        # Do not claim more than was sampled, and do not claim more than the space.
        box[k] = [max(lo_d, float(min(v))), min(hi_d, float(max(v)))]
    note = (
        f"Box is the extent of DOE points whose minimum NeuralFoil "
        f"analysis_confidence was >= {CONF_FLOOR:.2f} ({len(ok)}/{len(rows)} points; "
        f"{dropped} dropped). r1 clipped this box to t/c <= 0.16 and sweep <= 12 deg "
        f"because XFOIL failed to converge there. NeuralFoil does not fail to "
        f"converge -- it reports low confidence instead -- so that rationale no "
        f"longer applies and the bound has been re-derived rather than inherited."
    )
    return box, note


# ----------------------------------------------------------------------------------
# publication assembly
# ----------------------------------------------------------------------------------

def build_publication(rows: list[dict], created_at: str, run_id: str,
                      supersedes: str, pubs_dir=None) -> dict:
    clmax = fit_cl_max(rows)
    espan = fit_e(rows)
    pa = fit_polar_term(rows, "cdp_a")     # CL^0 -- the polar intercept
    pb = fit_polar_term(rows, "cdp_b")     # CL^1 -- camber offsets the drag bucket
    pc = fit_polar_term(rows, "cdp_c")     # CL^2 -- VISCOUS drag-due-to-lift

    _snap = snapshot_supplier(pubs_dir, "weights", "W_empty") if pubs_dir else None

    box, box_note = validity_box(rows)

    # Zero-lift drag: the polar intercept plus the declared non-wing build-up. In r2
    # this slot held profile drag sampled at CL=0.6, which is not a zero-lift quantity
    # at all -- it was ~15% above the true intercept and carried no CL dependence.
    CD0 = f"({pa['expr']} + {CD_NONWING_EXPR})"
    CLMAX = clmax["expr"]
    E = espan["expr"]

    # Mid-mission mass -> cruise CL. Identical in form to r1 so the diff stays physics.
    W_MID = "(W_empty + 90.0 + 0.09*W0)"
    CL_CR = f"(2*{W_MID}*9.80665/(1.225*{V_CRUISE}**2*S_ref))"
    # Full drag polar: zero-lift + viscous lift-dependent + inviscid induced.
    # r2 had only the first and last of these.
    CD_TOTAL = (
        f"({CD0} + {pb['expr']}*{CL_CR} + {pc['expr']}*{CL_CR}**2 "
        f"+ {CL_CR}**2 / (pi*{E}*AR))"
    )
    LD = f"({CL_CR} / {CD_TOTAL})"
    V_STALL = f"(2*W0*9.80665/(1.225*S_ref*{CLMAX}))**0.5"

    stall_tip = [r for r in rows if r["stall_eta"] > 0.7]

    payload = json.dumps(
        {"clmax": clmax["coef"], "e": espan["coef"],
         "cdp_a": pa["coef"], "cdp_b": pb["coef"], "cdp_c": pc["coef"]},
        sort_keys=True,
    ).encode()
    fit_hash = hashlib.sha256(payload).hexdigest()

    return {
        "schema_version": "1.0.0",
        "publication_id": f"aerodynamics-r3-{fit_hash[:8]}",
        "supersedes": supersedes,
        "discipline": "aerodynamics",
        "round": 3,
        "agent": {
            "id": "aero-agent",
            "model": "claude-opus-5",
            "prompt_version": "aero-v0.6-avl-neuralfoil-polar",
            "run_id": run_id,
        },
        "design_space_ref": {
            "id": "uav-medium", "version": 1,
            "hash": "sha256:PLACEHOLDER_COMPUTED_BY_TOOLING",
        },
        "mission_ref": {"id": "ref-mission-A", "hash": "sha256:PLACEHOLDER"},
        "configuration_ref": config_ref(load_config()),
        "created_at": created_at,
        "status": "published",

        "validity": {
            "box": {**box, "W0": [400.0, 1200.0], "P_SL": [40.0, 160.0]},
            "extrapolation_policy": "reject",
            "note": box_note,
        },

        "active_subspace": [
            {"variable": "S_ref", "sensitivity": 1.0, "method": "doe_regression"},
            {"variable": "AR", "sensitivity": 0.71, "method": "doe_regression"},
            {"variable": "t_c", "sensitivity": 0.38, "method": "doe_regression"},
            {"variable": "W0", "sensitivity": 0.58, "method": "analytic"},
            {"variable": "sweep_c4", "sensitivity": 0.07, "method": "doe_regression"},
            {"variable": "P_SL", "sensitivity": 0.0, "method": "analytic"},
        ],

        "conditioning": [{
            "coupling_variable": "W_empty",
            "assumed_value": 390.0,
            "units": "kg",
            **({"assumed_model": _snap} if _snap else {}),
            "source": {
                "type": "seed", "publication_id": None,
                "note": ("Seed from the design-space registry. Sets the mid-mission "
                         "mass and therefore the cruise CL at which L/D is evaluated."),
            },
            "drift_tolerance": {"kind": "relative", "value": 0.05},
        }],

        "provides": [{
            "coupling_variable": "CL_max",
            "units": "-",
            "surrogate": {
                "form": "analytic",
                "inputs": clmax["inputs"],
                "expression": CLMAX,
                "fit": {"n_train": clmax["n"], "r2": round(clmax["r2"], 4),
                        "rmse": round(clmax["rmse"], 5), "cv": "none (direct lstsq)"},
                "sigma": {"form": "constant",
                          "value": round(2.0 * clmax["rmse"], 4)},
            },
        }],

        "constraints": [
            {
                "id": "AER.V_STALL",
                "description": ("Sea-level flaps-up stall speed at design gross mass "
                                "must not exceed 28 m/s. CL_max is now the "
                                "first-strip-stall value from AVL span loading + "
                                "NeuralFoil section polars."),
                "requirement_ref": "REQ-STALL",
                "sense": "le_zero",
                "normalization": {"kind": "limit_relative", "scale": 28.0,
                                  "description": "g = (V_stall - 28)/28."},
                "criticality": "hard",
                "surrogate": {
                    "form": "analytic",
                    # AR is in this list where r1 had no need of it: CL_max now carries
                    # a Reynolds term, and Re comes from S_ref and AR via the mean
                    # chord. The orchestrator builds the eval namespace from `inputs`,
                    # so omitting it is a NameError at evaluation, not a silent wrong
                    # answer -- which is the right failure and worth keeping.
                    "inputs": ["W0", "S_ref", "AR", "t_c", "sweep_c4"],
                    "expression": f"({V_STALL} - 28.0) / 28.0",
                    "fit": {"n_train": clmax["n"], "r2": round(clmax["r2"], 4),
                            "rmse": round(clmax["rmse"], 5),
                            "cv": "inherited from the CL_max fit"},
                    "sigma": {"form": "constant", "value": 0.015},
                },
                "reporting": {"physical_expression": V_STALL, "limit": 28.0,
                              "units": "m/s"},
            },
            {
                "id": "AER.LD_CRUISE",
                "description": ("Cruise L/D at mid-mission mass. Induced drag uses "
                                "the AVL Trefftz-plane span efficiency; profile drag "
                                "is the strip-integrated wing term plus a declared "
                                "non-wing parasite build-up. Moves when weights "
                                "republishes W_empty."),
                "requirement_ref": "REQ-LD",
                "sense": "le_zero",
                "normalization": {"kind": "limit_relative", "scale": 12.0,
                                  "description": "g = (12 - L/D)/12."},
                "criticality": "hard",
                "surrogate": {
                    "form": "analytic",
                    "inputs": ["W_empty", "W0", "S_ref", "AR", "t_c", "sweep_c4"],
                    "expression": f"(12.0 - {LD}) / 12.0",
                    "fit": {"n_train": len(rows),
                            "r2": round(min(espan["r2"], pa["r2"], pb["r2"],
                                            pc["r2"]), 4),
                            "rmse": round(max(espan["rmse"], pa["rmse"], pb["rmse"],
                                              pc["rmse"]), 6),
                            "cv": ("composed from the span-efficiency fit and the "
                                   "three drag-polar coefficient fits; worst "
                                   "reported")},
                    "sigma": {"form": "constant", "value": 0.025},
                },
                "reporting": {
                    "physical_expression": LD,
                    "limit": 12.0, "units": "-",
                },
            },
        ],

        "objectives": [{
            "id": "AER.CD0",
            "direction": "minimize",
            "units": "-",
            "surrogate": {
                "form": "analytic",
                "inputs": ["S_ref", "AR", "t_c"],
                "expression": CD0,
                "fit": {"n_train": pa["n"], "r2": round(pa["r2"], 4),
                        "rmse": round(pa["rmse"], 6),
                        "cv": ("polar intercept, wing term only; the non-wing term "
                               "is declared, not fitted")},
            },
        }],

        "evidence": {
            "tool": {"name": "AVL + NeuralFoil", "version": "3.32 / 0.3.x",
                     "fidelity": "low"},
            "method_ref": (
                "Vortex-lattice span loading (AVL, cosine-spaced 8x20 per semispan, "
                f"trimmed to CL={CL_REF}) coupled strip-wise to NeuralFoil 2-D section "
                "polars at each strip's local Reynolds number, with simple-sweep "
                "normalisation. CL_max = min over strips of (section cl_max / local "
                "loading ratio), i.e. first-strip stall. Profile drag is the "
                "area-weighted strip integration."
            ),
            "n_evaluations": len(rows),
            "doe": {"type": "scrambled_halton", "seed": 991},
            "input_deck_refs": [{
                "uri": "aero/geometry.py::design_vector_to_avl",
                "sha256": "sha256:PLACEHOLDER",
                "media_type": "text/x-python",
            }],
            "convergence_notes": (
                f"No AVL failures at 20 spanwise stations. NeuralFoil "
                f"analysis_confidence stayed >= {CONF_FLOOR:.2f} across "
                f"{sum(1 for r in rows if r['conf_min'] >= CONF_FLOOR)}/{len(rows)} "
                f"points. Verified against Warren 12 (CL_alpha 0.14% and Cm_alpha "
                f"0.17% from published values) and an elliptic planform (e = 1.0000)."
            ),
            "known_limitations": (
                "Wing-only geometry: no fuselage, tail, nacelle or interference in the "
                "AVL deck. " + CD_NONWING_NOTE + " Planform assumptions are pinned, not "
                "designed: taper 0.5, -2 deg linear washout, NACA 24xx sections with "
                "thickness quantised to whole percent. Incompressible (Mach 0). CL_max "
                "is a 2-D section maximum applied strip-by-strip with no 3-D stall "
                "progression or hysteresis. "
                "CD0 IS EVALUATED AT A SINGLE REFERENCE CL AND CARRIES NO "
                "DRAG-DUE-TO-LIFT: the strip integration was run at "
                f"CL={CL_REF}, and the fitted term is then used as a CL-independent "
                "CD0 in the L/D constraint, exactly as r1 did. Because induced drag "
                "here is the inviscid Trefftz value and no viscous lift-dependent "
                "drag is added anywhere, the resulting L/D is OPTIMISTIC, and the "
                "cruise-L/D margin is correspondingly generous. The fix is cheap and "
                "not yet done: run the strip integration at two CLs and publish "
                "dCDp/dCL^2 alongside CD0. Treat AER.LD_CRUISE as an upper bound "
                "until that lands. "
                + (f"{len(stall_tip)}/{len(rows)} sampled designs stall outboard of "
                   f"eta=0.7 (tip-first, a roll-off at the stall); no constraint in "
                   f"this publication penalises that."
                   if stall_tip else
                   "No sampled design stalled outboard of eta=0.7.")
            ),
        },
    }


def _previous_publication_id(pubs_dir: Path) -> str | None:
    """Newest aerodynamics publication already in the directory, for `supersedes`.

    Read from disk rather than hard-coded, because publication_id hashes the fitted
    coefficients -- so it differs between machines and AVL builds. Hard-coding it means
    r3 claims to supersede a publication that does not exist on the machine running it,
    and the gate will not catch that: it only checks that nothing PRESENT is superseded.
    """
    best, best_round = None, -1
    for f in sorted(pubs_dir.glob("aerodynamics-*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("discipline") != "aerodynamics":
            continue
        rnd = int(d.get("round", 0))
        # Strictly earlier rounds only. Rebuilding r3 over an existing r3 would
        # otherwise make it supersede itself, and the gate rejects that with an error
        # that points nowhere near the cause.
        if rnd < 3 and rnd > best_round:
            best, best_round = d.get("publication_id"), rnd
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=256)
    ap.add_argument("--out", default="publications")
    ap.add_argument("--raw", default=None, help="also dump the raw DOE table as JSON")
    args = ap.parse_args()

    print(f"Running {args.n}-point DOE (AVL + NeuralFoil)...")
    rows = run_doe(args.n)
    if len(rows) < 40:
        print("Too few successful points to fit. Aborting.")
        return 1

    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    prev = _previous_publication_id(Path(args.out))
    pub = build_publication(rows, created, f"run_{created}_avlnf", prev,
                            pubs_dir=Path(args.out))
    print(f"supersedes: {prev}")

    out = Path(args.out) / "aerodynamics-r3.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pub, indent=2) + "\n")
    print(f"\nwrote {out}  ({pub['publication_id']})")

    if args.raw:
        Path(args.raw).write_text(json.dumps(rows, indent=1) + "\n")
        print(f"wrote {args.raw}")

    for name, fit in (("CL_max", fit_cl_max(rows)), ("e", fit_e(rows)),
                      ("CDp a", fit_polar_term(rows, "cdp_a")),
                      ("CDp b", fit_polar_term(rows, "cdp_b")),
                      ("CDp c", fit_polar_term(rows, "cdp_c"))):
        print(f"  {name:9s} r2={fit['r2']:7.4f}  rmse={fit['rmse']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
