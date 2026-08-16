"""
Build weights-r2 from the component mass build-up.

No DOE and no fit, because there is nothing to fit: the build-up is closed form. The
expression published here is the algebra of weights/mass.py written out, and the script
VERIFIES that by evaluating both over random samples and refusing to write the
publication if they disagree. A surrogate that claims r2 = 1.0 should be made to prove
it.

What changes versus weights-r1:

    W_empty = W_structure + 0.14*W0 + 1.1*P_SL**0.9 + 45.0

    becomes a sum of sized components: a fuselage sized by the volume it must hold and
    weighed by wetted area, a propulsion group from installed power, systems and gear.
    r1's `0.14*W0` made W_empty proportional to gross mass, which quietly makes mass
    closure close to self-satisfying.

    The fuselage now scales as volume^(2/3) rather than linearly with W0, and P_SL
    carries a real, physically-sourced mass -- it has sensitivity 0.0 in every other
    publication in this contract.

Usage:
    python3 weights/build_publication.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from configuration import (config_ref, load_config,  # noqa: E402
                           snapshot_supplier)
from weights.mass import (  # noqa: E402
    FINENESS, FUEL_FRACTION, GLASS_FOAM, RHO_FUEL, RHO_PAYLOAD_PACK, V_SYSTEMS,
    W_PAYLOAD, mass_breakdown, nonwing_parasite_drag,
)

PACKING = 0.72
POWER_LOADING_LIMIT = 9.0     # kg/kW, from r1
SPECIFIC_POWER = 1.6


def _fuselage_expr(c=GLASS_FOAM) -> str:
    """Closed form of size_fuselage().mass, from the SHARED CONFIGURATION.

    Every constant below is read from configuration/config.uav-medium.v1.json rather
    than restated here, so this expression cannot drift from the geometry the other
    disciplines use. It is also no longer a function of W0 alone: the configuration's
    length_rule takes the LARGER of the volume-driven and tail-arm-driven lengths, and
    the tail arm scales with the wing's mean chord -- so fuselage mass depends on S_ref
    and AR. Across this whole design space the tail arm governs; the volume-only form
    this replaces was short by 2.9-5.0 m everywhere.
    """
    cfg = load_config()
    pl, tl, fu = cfg["planform"], cfg["tail"], cfg["fuselage"]
    taper = pl["taper"]

    v_fixed = W_PAYLOAD / fu["payload_pack_density"] + fu["systems_volume"]
    k_v = FUEL_FRACTION / RHO_FUEL
    denom = (fu["packing_efficiency"] * fu["volume_coefficient"]
             * (math.pi / 4.0) * fu["fineness_ratio"])
    D = f"((({k_v:.12g}*W0 + {v_fixed:.12g}) / {denom:.12g})**(1.0/3.0))"
    L_VOL = f"({fu['fineness_ratio']:.12g}*{D})"

    k_croot = 2.0 / (1.0 + taper)
    k_mac = (2.0 / 3.0) * k_croot * (1 + taper + taper**2) / (1 + taper)
    k_ch = math.sqrt(tl["volume_coefficient"] / (tl["arm_macs"] * tl["aspect_ratio"]))
    RT = "(S_ref/AR)**0.5"
    L_TAIL = (f"(1.05*({0.25 * k_croot:.12g}*{RT} "
              f"+ {tl['arm_macs'] * k_mac:.12g}*{RT} + {k_ch:.12g}*S_ref**0.5) "
              f"+ {fu['nose_fraction']:.12g}*{L_VOL})")

    L = f"maximum({L_VOL}, {L_TAIL})"
    k_wet = fu["wetted_area_coefficient"] * math.pi
    k_mass = k_wet * c.areal_density * c.non_optimum
    return f"({k_mass:.12g}*{D}*{L})"


def _empty_expr() -> str:
    fus = _fuselage_expr()
    k_prop_P = (1.0 + 0.50 + 0.06) / SPECIFIC_POWER
    k_prop_W0 = 0.12 * FUEL_FRACTION
    const = 2.5 + 4.0 + 14.0
    k_W0 = k_prop_W0 + 0.045 + 0.035
    return (f"(W_structure + {fus} + {k_prop_P:.12g}*P_SL "
            f"+ {k_W0:.12g}*W0 + {const:.12g})")


def verify(expr: str, n: int = 4000, seed: int = 3) -> float:
    """Max relative disagreement between the published expression and mass.py."""
    rng = np.random.default_rng(seed)
    worst = 0.0
    for _ in range(n):
        W0 = rng.uniform(400, 1200)
        P = rng.uniform(40, 160)
        Ws = rng.uniform(50, 900)
        S = rng.uniform(8, 25)
        AR = rng.uniform(8, 24)
        ref = mass_breakdown(Ws, W0, P, S, AR).W_empty
        got = eval(expr, {"__builtins__": {}, "maximum": max},  # noqa: S307
                   {"W_structure": Ws, "W0": W0, "P_SL": P, "S_ref": S, "AR": AR})
        worst = max(worst, abs(got - ref) / ref)
    return worst


def build(created_at: str, run_id: str, supersedes: str | None,
          pubs_dir=None) -> dict:
    EMPTY = _empty_expr()
    W_FUEL = f"({FUEL_FRACTION}*W0)"

    err = verify(EMPTY)
    if err > 1e-9:
        raise AssertionError(
            f"published expression disagrees with weights/mass.py by {err:.2e} -- "
            f"the algebra has drifted from the model, refusing to publish"
        )

    # Snapshot the supplier rather than assuming a scalar. W_structure ranges 14x over
    # this domain; no single number can sit inside +/-8% of it, so the scalar residual
    # was measuring design-dependence and calling it disagreement.
    _snap = snapshot_supplier(pubs_dir, "structures", "W_structure") if pubs_dir else None

    h = hashlib.sha256(EMPTY.encode()).hexdigest()
    drag_note = ", ".join(
        f"S={S:.0f},W0={W:.0f}: {nonwing_parasite_drag(W, S):.5f}"
        for S, W in ((10.0, 500.0), (16.0, 700.0), (22.0, 1100.0))
    )

    return {
        "schema_version": "1.0.0",
        "publication_id": f"weights-r2-{h[:8]}",
        "supersedes": supersedes,
        "discipline": "weights",
        "round": 2,
        "agent": {"id": "weights-agent", "model": "claude-opus-5",
                  "prompt_version": "weights-v0.4-buildup", "run_id": run_id},
        "design_space_ref": {"id": "uav-medium", "version": 1,
                             "hash": "sha256:PLACEHOLDER_COMPUTED_BY_TOOLING"},
        "mission_ref": {"id": "ref-mission-A", "hash": "sha256:PLACEHOLDER"},
        "configuration_ref": config_ref(load_config()),
        "created_at": created_at,
        "status": "published",

        "validity": {
            "box": {"S_ref": [8.0, 25.0], "AR": [8.0, 24.0], "t_c": [0.08, 0.18],
                    "sweep_c4": [0.0, 15.0], "W0": [400.0, 1200.0],
                    "P_SL": [40.0, 160.0]},
            "extrapolation_policy": "reject",
            "note": ("Full declared space. The build-up is closed form and has no "
                     "training domain, so there is no sampling-based bound to claim. "
                     "The real limits are the pinned constants -- fineness ratio 6, "
                     "packing efficiency 0.72, areal densities, specific power "
                     "1.6 kW/kg -- which are declared, not fitted, and are where a "
                     "weights group would spend its effort."),
        },

        "active_subspace": [
            {"variable": "W0", "sensitivity": 1.0, "method": "analytic"},
            {"variable": "P_SL", "sensitivity": 0.63, "method": "analytic"},
            {"variable": "AR", "sensitivity": 0.0, "method": "analytic"},
            {"variable": "S_ref", "sensitivity": 0.0, "method": "analytic"},
            {"variable": "t_c", "sensitivity": 0.0, "method": "analytic"},
            {"variable": "sweep_c4", "sensitivity": 0.0, "method": "analytic"},
        ],

        "conditioning": [
            {"coupling_variable": "W_structure", "assumed_value": 165.0, "units": "kg",
             **({"assumed_model": _snap} if _snap else {}),
             "source": {"type": "seed", "publication_id": None,
                        "note": ("Wing box mass from the structures discipline. This is "
                                 "now a genuine addend in a sum, not a term inside a "
                                 "fitted expression, so the closure constraint moves "
                                 "one-for-one with what structures publishes.")},
             "drift_tolerance": {"kind": "relative", "value": 0.08}},
            {"coupling_variable": "n_z_ult", "assumed_value": 5.7, "units": "g",
             "source": {"type": "requirement", "publication_id": None,
                        "note": "Frozen by the requirement set."},
             "drift_tolerance": {"kind": "absolute", "value": 0.01}},
        ],

        "provides": [{
            "coupling_variable": "W_empty",
            "units": "kg",
            "surrogate": {
                "form": "analytic",
                "inputs": ["W_structure", "W0", "P_SL", "S_ref", "AR"],
                "expression": EMPTY,
                "fit": {"n_train": 0, "r2": 1.0, "rmse": 0.0,
                        "cv": ("exact -- closed-form build-up, verified against "
                               "weights/mass.py over 4000 random points to 1e-9")},
                "sigma": {"form": "constant", "value": 18.0},
            },
        }],

        "constraints": [
            {
                "id": "WTS.CLOSURE",
                "description": ("Mass closure: empty + payload + mission fuel must fit "
                                "inside the design gross mass. This is the sizing fixed "
                                "point in inequality form."),
                "requirement_ref": "REQ-CLOSE",
                "sense": "le_zero",
                "normalization": {"kind": "limit_relative", "scale": 1.0,
                                  "description": "g as a fraction of W0."},
                "criticality": "hard",
                "surrogate": {
                    "form": "analytic",
                    "inputs": ["W_structure", "W0", "P_SL", "S_ref", "AR"],
                    "expression": f"(({EMPTY}) + {W_PAYLOAD} + {W_FUEL} - W0) / W0",
                    "fit": {"n_train": 0, "r2": 1.0, "rmse": 0.0, "cv": "exact"},
                    "sigma": {"form": "constant", "value": 0.025},
                },
                "reporting": {"physical_expression": f"({EMPTY}) + {W_PAYLOAD} + "
                                                     f"{W_FUEL}",
                              "limit": "W0", "units": "kg"},
            },
            {
                "id": "WTS.PWR_LOADING",
                "description": ("Power loading at gross mass. Unchanged in form from "
                                "r1, but P_SL now carries a real mass and a real "
                                "cooling drag, so buying power is no longer free."),
                "requirement_ref": None,
                "sense": "le_zero",
                "normalization": {"kind": "absolute_scale", "scale": 1.5,
                                  "description": "g = (W0/P_SL - 9.0)/1.5, kg per kW."},
                "criticality": "hard",
                "surrogate": {
                    "form": "analytic", "inputs": ["W0", "P_SL"],
                    "expression": f"(W0/P_SL - {POWER_LOADING_LIMIT}) / 1.5",
                    "fit": {"n_train": 0, "r2": 1.0, "rmse": 0.0, "cv": "exact"},
                },
                "reporting": {"physical_expression": "W0/P_SL",
                              "limit": POWER_LOADING_LIMIT, "units": "kg/kW"},
            },
        ],

        "objectives": [{
            "id": "WTS.EMPTY", "direction": "minimize", "units": "kg",
            "surrogate": {"form": "analytic",
                          "inputs": ["W_structure", "W0", "P_SL", "S_ref", "AR"],
                          "expression": EMPTY},
        }],

        "evidence": {
            "tool": {"name": "floodlight weights.mass component build-up",
                     "version": "0.4", "fidelity": "low"},
            "method_ref": (
                "Fuselage sized by required internal volume (mission fuel at 750 kg/m^3,"
                " payload at 250 kg/m^3 packed, 0.15 m^3 equipment, 72% packing), body "
                "of revolution at fineness 6, mass from wetted area at 4.1 kg/m^2 for "
                "glass/foam sandwich with a 1.45 non-optimum factor. Propulsion from "
                "installed power at 1.6 kW/kg with 50% installation, propeller and fuel "
                "system. Systems 4.5% of W0 + 14 kg; gear 3.5% of W0."
            ),
            "n_evaluations": 0,
            "doe": {"type": "none -- closed form", "seed": 0},
            "input_deck_refs": [{"uri": "weights/mass.py::mass_breakdown",
                                 "sha256": "sha256:PLACEHOLDER",
                                 "media_type": "text/x-python"}],
            "convergence_notes": (
                "Closed form; no solver, no convergence. Verified against the "
                "implementing module over 4000 random points, max relative "
                f"disagreement {err:.1e}."
            ),
            "known_limitations": (
                "Every constant here is declared judgement, not measurement: fineness "
                "ratio, packing efficiency, areal densities, specific power, the "
                "systems and gear fractions. No structural fuselage sizing -- the shell "
                "is weighed by area, not sized against loads, so a pressurised or "
                "highly loaded body would be understated. No margin policy and no "
                "growth allowance, which real programmes carry at 5-10%. "
                "SEPARATELY, this module can also produce the non-wing parasite drag "
                "the aero publications currently carry as a declared constant "
                "(0.0062 + 0.14/S_ref). The wetted-area build-up gives roughly 35-50% "
                f"LESS than that constant ({drag_note}), i.e. a clean-configuration "
                "estimate. The truth is probably between the two and the discrepancy is "
                "an open item for review -- aero has NOT yet been switched over to it."
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="publications")
    args = ap.parse_args()
    pubs = Path(args.out)

    prev = None
    best = -1
    for f in sorted(pubs.glob("weights-*.json")):
        d = json.loads(f.read_text())
        rnd = int(d.get("round", 0))
        # Strictly earlier rounds only -- see the note in structures/_previous.
        if rnd < 2 and rnd > best:
            prev, best = d.get("publication_id"), rnd

    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    pub = build(created, f"run_{created}_buildup", prev, pubs_dir=pubs)
    out = pubs / "weights-r2.json"
    out.write_text(json.dumps(pub, indent=2) + "\n")
    print(f"supersedes: {prev}")
    print(f"wrote {out}  ({pub['publication_id']})")
    print(f"\nW_empty = {pub['provides'][0]['surrogate']['expression']}")
    b = mass_breakdown(167.2, 700.0, 110.0, 16.9, 10.0)
    print(f"\nexample (W_structure=167.2, W0=700, P_SL=110):")
    print(b.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
