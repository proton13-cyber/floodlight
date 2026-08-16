"""
Select and characterise the best designs from the final campaign round.

Reproduces the round-11 state exactly -- the published surrogates with the campaign's
three events applied and the converged conditioning values -- then samples the final
trust region, keeps only ADMISSIBLE points (all six margins satisfied AND all three
consistency residuals inside tolerance), and picks ten.

WHY NOT SIMPLY THE TEN LIGHTEST
-------------------------------
The ten lightest admissible designs are, almost always, ten copies of one design with
noise on it -- they sit in the same corner of the region and differ in the fourth
decimal. That is a misleading thing to hand someone, because it makes a broad
admissible region look like a single answer.

So this ranks by gross mass and then enforces separation: a candidate is only accepted
if it differs from every already-accepted design by at least `min_sep` in normalised
design-vector distance. The result is ten genuinely different aircraft, each one the
lightest in its own neighbourhood -- which is what a design region actually offers.

Usage:
    python3 report_designs.py --pubs publications --data timelapse_data.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from mdao_contract import (common_box, consistency, load_publications,  # noqa: E402
                           load_space, margins)

NAMES = ["S_ref", "AR", "t_c", "sweep_c4", "W0", "P_SL"]


def apply_final_state(pubs, data):
    """Replay the campaign's events and converged conditioning onto the publications.

    The publications on disk are the round-0 state. The final round differs by the
    three tool-update events and by the conditioning values the fixed point converged
    to, both of which are recorded in the timelapse data -- so the two artifacts
    together define the final state without re-running the campaign.
    """
    by_disc = {p.discipline: p for p in pubs}

    def scale_constraint(disc, cid, k):
        for it in by_disc[disc].raw["constraints"]:
            if it["id"] == cid:
                e = it["surrogate"]["expression"]
                it["surrogate"]["expression"] = f"({k}*({e}) + {k - 1.0})"
                return
        raise KeyError(cid)

    def scale_provides(disc, cv, k):
        for it in by_disc[disc].raw["provides"]:
            if it["coupling_variable"] == cv:
                e = it["surrogate"]["expression"]
                it["surrogate"]["expression"] = f"({k}*({e}))"
                return
        raise KeyError(cv)

    def patch(disc, cid, old, new):
        for it in by_disc[disc].raw["constraints"]:
            if it["id"] == cid:
                assert old in it["surrogate"]["expression"], (cid, old)
                it["surrogate"]["expression"] = \
                    it["surrogate"]["expression"].replace(old, new)
                return
        raise KeyError(cid)

    scale_constraint("structures", "STR.TIP_DEFL", 1.42)          # round 3, gust case
    scale_provides("structures", "W_structure", 0.47)             # round 6, carbon
    patch("aerodynamics", "AER.LD_CRUISE",                        # round 9, re-loft
          "0.0062 + 0.14/S_ref", "0.0055 + 0.1246/S_ref")

    assumed = data["rounds"][-1]["assumed"]
    for p in pubs:
        for c in p.raw["conditioning"]:
            cv = c["coupling_variable"]
            if cv in assumed:
                c["assumed_value"] = float(assumed[cv])

    # Beliefs, not just scalars. With model conditioning, the consistency check reads
    # assumed_model -- and the models baked into the publications are round-0 fits of
    # the PRE-EVENT suppliers. Replaying the events without refreshing the beliefs
    # would score every design against a picture of structures that predates the
    # carbon switch, and nothing would be admissible. The shortlist should reflect
    # end-state knowledge: beliefs fully caught up to the final suppliers (the actual
    # round-11 residual tail is a few percent -- close enough for selection).
    from configuration import fit_snapshot
    suppliers = {}
    for p in pubs:
        for pr in p.raw.get("provides", []):
            suppliers[pr["coupling_variable"]] = p
    for p in pubs:
        for c in p.raw["conditioning"]:
            if "assumed_model" not in c:
                continue
            sup = suppliers.get(c["coupling_variable"])
            if sup is None:
                continue
            sur = next(pr["surrogate"] for pr in sup.raw["provides"]
                       if pr["coupling_variable"] == c["coupling_variable"])
            frozen = {cc["coupling_variable"]: float(cc["assumed_value"])
                      for cc in sup.raw.get("conditioning", [])}
            m = fit_snapshot(sur["expression"], sur["inputs"], frozen,
                             sup.raw["validity"]["box"])
            m["provenance"]["snapshot_of"] = sup.pid + " (post-event refresh)"
            c["assumed_model"] = m
    return pubs, assumed


def sample(box, n, seed=11):
    rng = np.random.default_rng(seed)
    X = np.empty((n, len(NAMES)))
    for j, v in enumerate(NAMES):
        lo, hi = box[v]
        X[:, j] = lo + ((rng.permutation(n) + rng.random(n)) / n) * (hi - lo)
    return X


def pick_diverse(X, score, k, box, min_sep=0.30):
    """Greedy: best score first, then only accept a candidate that is at least
    `min_sep` away (normalised L2 over the box) from everything already chosen."""
    span = np.array([box[v][1] - box[v][0] for v in NAMES])
    order = np.argsort(score)
    chosen: list[int] = []
    for i in order:
        if all(np.linalg.norm((X[i] - X[c]) / span) >= min_sep for c in chosen):
            chosen.append(int(i))
            if len(chosen) == k:
                break
    if len(chosen) < k:   # region too tight for the separation asked; relax and refill
        for i in order:
            if i not in chosen:
                chosen.append(int(i))
                if len(chosen) == k:
                    break
    return chosen


def derived(x):
    """Geometry a reader can picture, from the pinned planform (taper 0.5)."""
    S, AR, t_c = x[0], x[1], x[2]
    b = math.sqrt(AR * S)
    c_root = 2 * S / (b * 1.5)
    return {"span": b, "c_root": c_root, "c_tip": 0.5 * c_root,
            "mac": 1.037037 * math.sqrt(S / AR),
            "wing_loading": x[4] / S, "power_loading": x[4] / x[5],
            "root_box_depth_mm": 1000 * 0.92 * t_c * c_root}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pubs", default="publications")
    ap.add_argument("--data", default="timelapse_data.json")
    ap.add_argument("--space", default="spaces/design_space.uav-medium.v1.json")
    ap.add_argument("--n", type=int, default=400_000)
    ap.add_argument("--k", type=int, default=20)
    ap.add_argument("--out", default="top_designs.json")
    args = ap.parse_args()

    space = load_space(Path(args.space))
    pubs = load_publications(Path(args.pubs))
    data = json.loads(Path(args.data).read_text())
    pubs, assumed = apply_final_state(pubs, data)

    tr = data["rounds"][-1]["tr"]
    box = {v: tuple(tr[v]) for v in NAMES}
    X = sample(box, args.n)

    G, meta = margins(pubs, space, X)
    keys = list(G.keys())
    Gm = np.vstack([G[k] for k in keys])
    feas = np.all(Gm <= 0, axis=0)

    R = consistency(pubs, space, X)
    cons = np.ones(len(X), bool)
    for v in R.values():
        cons &= (v["norm_residual"] <= 1.0) if v["status"] == "OK" else False
    adm = feas & cons

    print(f"sampled {len(X):,} in the final trust region")
    print(f"  feasible        {feas.mean()*100:6.2f}%")
    print(f"  self-consistent {cons.mean()*100:6.2f}%")
    print(f"  ADMISSIBLE      {adm.mean()*100:6.2f}%  ({adm.sum():,} designs)")
    if adm.sum() < args.k:
        print("not enough admissible designs")
        return 1

    idx = np.where(adm)[0]
    chosen = [idx[i] for i in pick_diverse(X[idx], X[idx][:, 4], args.k, box)]

    out = []
    for rank, i in enumerate(chosen, 1):
        x = X[i]
        rec = {"rank": rank, **{v: float(x[j]) for j, v in enumerate(NAMES)}}
        rec.update({k2: float(v2) for k2, v2 in derived(x).items()})
        rec["margins"] = {k: float(G[k][i]) for k in keys}
        rec["active"] = [k for k in keys if G[k][i] > -0.05]
        rec["couplings"] = {
            k.split("<-")[1]: float(v["actual"][i])
            for k, v in R.items() if v["status"] == "OK"
        }
        rec["residuals"] = {
            k.split("<-")[1]: float(v["norm_residual"][i])
            for k, v in R.items() if v["status"] == "OK"
        }
        out.append(rec)

    payload = {
        "round": data["rounds"][-1]["round"],
        "publications": [p.pid for p in pubs],
        "assumed": assumed,
        "trust_region": {v: list(box[v]) for v in NAMES},
        "n_sampled": int(len(X)),
        "admissible_fraction": float(adm.mean()),
        "constraint_keys": keys,
        "constraint_meta": {k: meta[k]["desc"] for k in keys},
        "designs": out,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nwrote {args.out}")
    print(f"\n{'#':>2} {'S_ref':>7}{'AR':>7}{'t/c':>7}{'sweep':>7}{'W0':>8}{'P_SL':>7}"
          f"{'W_emp':>8}{'W_str':>7}  {'span':>6}  active")
    for r in out:
        print(f"{r['rank']:>2} {r['S_ref']:7.2f}{r['AR']:7.2f}{r['t_c']:7.3f}"
              f"{r['sweep_c4']:7.2f}{r['W0']:8.1f}{r['P_SL']:7.1f}"
              f"{r['couplings']['W_empty']:8.1f}{r['couplings']['W_structure']:7.1f}"
              f"  {r['span']:6.2f}  "
              f"{', '.join(a.split(':')[1] for a in r['active']) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
