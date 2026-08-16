"""
Shared configuration: the single source of geometric truth.

WHY THIS EXISTS
---------------
floodlight's first idea is that there is one canonical design space, referenced by
hash, and that two publications whose hashes differ describe sets over different spaces
and must not be intersected. That machinery works, and it caught real errors.

It only covers the design VARIABLES. Everything else about the aircraft's shape --
taper, washout, section family, tail volume, tail arm, fineness ratio, materials,
lattice density -- was duplicated across four places with no mechanism to detect
disagreement. They did disagree:

  * aero/geometry.py wrote AVL decks at taper 0.50; the dashboard drew silhouettes at
    0.45, and a fuselage 51-71% longer than the one weights sized.
  * weights sized the fuselage on internal volume alone, producing a body 2.9-5.0 m
    too short to mount the tail aero assumed -- about 43 kg of skin nobody paid for,
    more than the entire fuselage mass weights reported.

Not one of the three consistency residuals could have caught either. They were green
throughout. **The contract can only verify couplings someone thought to declare**, and
neither of these was ever written down. It took rendering the geometry and looking at
it.

So: same mechanism, applied to shape instead of variables. Publications carry a
`configuration_ref` alongside `design_space_ref`, the engine gates on it, and a
discipline that quietly assumes a different taper becomes a gate failure rather than a
picture someone notices.

USAGE
-----
    from configuration import load_config, Geometry

    cfg = load_config()
    g = Geometry(cfg).derive(S_ref=16.0, AR=12.0, t_c=0.12, sweep_c4=5.0, W0=700.0)
    g.fuselage_length     # obeys the length_rule -- volume AND tail arm
    g.mac, g.span, g.tail_arm, g.c_root
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

__all__ = ["load_config", "config_hash", "config_ref", "Geometry", "DerivedGeometry"]

_DEFAULT = Path(__file__).resolve().parent / "config.uav-medium.v1.json"


def config_hash(cfg: dict) -> str:
    """Hash of the configuration with the hash field itself excluded.

    Same convention the design-space ref uses. Comments are included deliberately:
    if someone edits a `$comment` explaining WHY a value is what it is, that is a
    change to the shared understanding and downstream publications should be re-checked.
    """
    payload = {k: v for k, v in cfg.items() if k != "hash"}
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_config(path: str | Path | None = None) -> dict:
    cfg = json.loads(Path(path or _DEFAULT).read_text())
    cfg["hash"] = config_hash(cfg)      # computed, never trusted from the file
    return cfg


def config_ref(cfg: dict) -> dict:
    """The block a publication embeds so the gate can check it."""
    return {"id": cfg["id"], "version": cfg["version"], "hash": cfg["hash"]}


@dataclass
class DerivedGeometry:
    span: float
    c_root: float
    c_tip: float
    mac: float
    x_ref: float
    x_le_tip: float
    tail_arm: float
    tail_area: float
    tail_span: float
    tail_chord: float
    fuselage_length: float
    fuselage_length_volume: float
    fuselage_length_tail: float
    fuselage_diameter: float
    fuselage_wetted: float
    length_driver: str          # "volume" or "tail_arm" -- which requirement governed


class Geometry:
    """Derives every shared geometric quantity from the design vector plus the config.

    Every discipline calls this instead of re-deriving. That is the whole point: the
    duplication is what allowed the disagreement.
    """

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.p = cfg["planform"]
        self.t = cfg["tail"]
        self.f = cfg["fuselage"]

    def derive(self, *, S_ref: float, AR: float, t_c: float, sweep_c4: float,
               W0: float, fuel_fraction: float = 0.18, rho_fuel: float = 750.0,
               w_payload: float = 90.0) -> DerivedGeometry:
        taper = self.p["taper"]
        b = math.sqrt(AR * S_ref)
        c_root = 2.0 * S_ref / (b * (1.0 + taper))
        c_tip = taper * c_root
        half_b = 0.5 * b
        x_le_tip = (0.25 * c_root + half_b * math.tan(math.radians(sweep_c4))
                    - 0.25 * c_tip)
        mac = (2.0 / 3.0) * c_root * (1 + taper + taper**2) / (1 + taper)
        y_mac = (b / 6.0) * (1 + 2 * taper) / (1 + taper)
        x_ref = y_mac * (x_le_tip / half_b) + 0.25 * mac

        # --- tail, by volume coefficient -------------------------------------------
        l_t = self.t["arm_macs"] * mac
        S_h = self.t["volume_coefficient"] * S_ref * mac / l_t
        b_h = math.sqrt(self.t["aspect_ratio"] * S_h)
        c_h = S_h / b_h

        # --- fuselage: BOTH requirements, then the declared rule --------------------
        v_fuel = fuel_fraction * W0 / rho_fuel
        v_pay = w_payload / self.f["payload_pack_density"]
        v_req = (v_fuel + v_pay + self.f["systems_volume"]) / self.f["packing_efficiency"]
        fineness = self.f["fineness_ratio"]
        d = (v_req / (self.f["volume_coefficient"] * (math.pi / 4.0) * fineness)) \
            ** (1.0 / 3.0)
        L_volume = fineness * d

        # Length needed to actually mount the tail: nose ahead of the wing, plus the
        # arm, plus the tail chord. This is the number that was never computed.
        nose = self.f["nose_fraction"] * L_volume
        L_tail = (0.25 * c_root + l_t + c_h) * 1.05 + nose

        rule = self.f["length_rule"]
        if rule == "max(volume_driven, tail_arm_driven)":
            L = max(L_volume, L_tail)
        elif rule == "volume_driven":
            L = L_volume
        else:
            raise ValueError(f"unknown fuselage length_rule: {rule!r}")
        driver = "tail_arm" if L > L_volume + 1e-9 else "volume"

        s_wet = self.f["wetted_area_coefficient"] * math.pi * d * L

        return DerivedGeometry(
            span=b, c_root=c_root, c_tip=c_tip, mac=mac, x_ref=x_ref,
            x_le_tip=x_le_tip, tail_arm=l_t, tail_area=S_h, tail_span=b_h,
            tail_chord=c_h, fuselage_length=L, fuselage_length_volume=L_volume,
            fuselage_length_tail=L_tail, fuselage_diameter=d,
            fuselage_wetted=s_wet, length_driver=driver,
        )


# ----------------------------------------------------------------------------------
# assumed-model snapshots
# ----------------------------------------------------------------------------------

def fit_snapshot(expression: str, inputs: list, frozen: dict, box: dict,
                 n: int = 4000, seed: int = 7) -> dict:
    """Fit a LINEAR internalization of a supplier surrogate over a box.

    This is deliberately cruder than the supplier's truth, and the crudeness is the
    point. A verbatim snapshot cannot disagree with its source anywhere in space, so
    consistency collapses to a pure freshness check and the gold/hatched map goes
    binary. A linear belief -- a plane through a curved function -- is what a receiving
    team actually internalizes of an expert's model ("carbon roughly halves it, grows
    with AR"), and it is wrong by a spatially varying amount: good near the middle of
    the box, poor at the corners where the true function curves away. THAT spatial
    error is what makes the admissible region a region again, and it means something:
    the part of the design space where your simplified picture of your partner holds.

    Linear in PHYSICAL space, not log space -- structures' mass law IS a power law, so
    a log-linear fit would recover it exactly and smuggle verbatim back in.

    Fit quality is returned in provenance: "how well I understand my partner" is a
    published number, not a vibe.
    """
    import numpy as np

    _env_base = {"__builtins__": {}, "sqrt": np.sqrt, "log": np.log,
                 "log10": np.log10, "exp": np.exp, "sin": np.sin, "cos": np.cos,
                 "tan": np.tan, "abs": np.abs, "minimum": np.minimum,
                 "maximum": np.maximum, "radians": np.radians, "pi": math.pi}
    rng = np.random.default_rng(seed)
    free = [v for v in inputs if v not in frozen and v in box]
    cols = {v: rng.uniform(box[v][0], box[v][1], n) for v in free}
    env = dict(_env_base); env.update(frozen); env.update(cols)
    y = np.asarray(eval(expression, env), dtype=float)  # noqa: S307
    A = np.column_stack([np.ones(n)] + [cols[v] for v in free])
    c, *_ = np.linalg.lstsq(A, y, rcond=None)
    pred = A @ c
    rel_rmse = float(np.sqrt(np.mean((pred - y) ** 2)) / max(abs(y.mean()), 1e-12))
    worst = float(np.max(np.abs(pred - y) / np.maximum(np.abs(y), 1e-12)))
    terms = [f"{c[0]:.8g}"] + [f"{ci:+.8g}*{v}" for ci, v in zip(c[1:], free)]
    return {
        "form": "analytic",
        "inputs": free,
        "expression": "(" + " ".join(terms) + ")",
        "provenance": {
            "kind": "linear_internalization",
            "fit_rel_rmse": round(rel_rmse, 4),
            "fit_worst_rel_err": round(worst, 4),
            "note": ("Deliberately low-order belief about the supplier, refit at each "
                     "broadcast. The residual it produces varies in space (where the "
                     "belief is too crude) AND in time (when it goes stale)."),
        },
    }


def snapshot_supplier(pubs_dir, discipline: str, coupling_variable: str) -> dict | None:
    """Take a snapshot of a supplier's published surrogate, for use as an assumed_model.

    This is the consumer copying down its belief about the supplier at publish time --
    deliberately a COPY, not a reference. When the supplier republishes, this snapshot
    does not follow, the residual grows, and the orchestrator broadcasts. That staleness
    is the thing the fixed point is supposed to measure; a live lookup would erase it
    and turn the whole protocol back into monolithic MDO.

    Returns None if no supplier publication is on disk yet -- in which case the consumer
    falls back to a scalar assumption, which is the honest state for round 0.
    """
    import json
    from pathlib import Path

    best, best_round = None, -1
    for f in sorted(Path(pubs_dir).glob(f"{discipline}-*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if d.get("status") != "published":
            continue
        if int(d.get("round", 0)) > best_round:
            best, best_round = d, int(d.get("round", 0))
    if best is None:
        return None
    for pr in best.get("provides", []):
        if pr["coupling_variable"] == coupling_variable:
            sur = pr["surrogate"]
            if sur.get("form") != "analytic":
                return None
            # Lossy by default: fit a linear internalization over the supplier's own
            # validity box, with the supplier's current premises baked in. The fitted
            # model has NO frozen_inputs -- the premises are inside the coefficients,
            # which also means a refit naturally absorbs the supplier's premise drift.
            frozen = {c["coupling_variable"]: float(c["assumed_value"])
                      for c in best.get("conditioning", [])}
            box = best["validity"]["box"]
            m = fit_snapshot(sur["expression"], sur["inputs"], frozen, box)
            m["provenance"]["snapshot_of"] = best["publication_id"]
            return m
    return None
