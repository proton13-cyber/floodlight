#!/usr/bin/env python3
"""
Simulate an MDAO campaign where the ROUNDS ACTUALLY LEARN.

Three mechanisms carry information forward, on top of the damped fixed point:

  1. TRUST REGION  -- after each round the sampling box contracts onto the
     bounding box of the admissible archive (padded, floored, clipped to the
     validity box, which it may never leave).
  2. PERSISTENT ARCHIVE -- admissible designs accumulate across rounds instead of
     being rediscovered. Critically, the archive is REVALIDATED every round: when
     an event rewrites a discipline's surrogate, previously-admissible designs can
     stop being admissible, and they are evicted.
  3. BOUNDARY-SEEKING PROPOSALS -- the per-round budget is split between
     exploring the trust region, exploiting near archived designs that already sit
     on several active constraints (i.e. near corners), and a global fraction that
     never stops covering the full validity box, so a stale trust region after an
     event can always be escaped.

Two different quantities are reported and must not be confused:
  * YIELD  -- admissible share of the points the campaign actually sampled.
              Measures how good the SEARCH is. Should climb.
  * VOLUME -- admissible share of the whole validity box, measured with a separate
              uniform draw that is NOT part of the campaign budget. Measures how
              big the ANSWER is. Moves only when the regions themselves move.
A rising yield with flat volume is the search getting smarter, not the design
space getting better. Reporting only one of them would be misleading.
"""
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from mdao_contract import (load_space, load_publications, common_box,
                           margins, consistency)

ROOT = pathlib.Path(__file__).parent
ALPHA = 0.6           # fixed-point under-relaxation
ROUNDS = 12
BUDGET = 12_000       # points evaluated per round (the campaign's spend)
VOL_N = 40_000        # separate instrumented draw, not charged to the budget
SEED = 42
PAD = 0.18            # trust-region padding, fraction of admissible extent
MIN_FRAC = 0.05       # floor on trust-region width, fraction of validity width
MIX = {"tr": 0.50, "local": 0.30, "global": 0.20}
ARCH_CAP = 4000

space = load_space(ROOT / "spaces/design_space.uav-medium.v1.json")
pubs = load_publications(ROOT / "publications")
by_disc = {p.discipline: p for p in pubs}
DISCS = ["aerodynamics", "structures", "weights"]
NAMES = space.var_names


def set_cond(disc, cv, val):
    for c in by_disc[disc].raw["conditioning"]:
        if c["coupling_variable"] == cv:
            c["assumed_value"] = float(val)


def patch(disc, where, cid, old, new):
    p = by_disc[disc].raw
    items = p["constraints"] if where == "constraints" else p["provides"]
    key = "id" if where == "constraints" else "coupling_variable"
    for it in items:
        if it[key] == cid:
            assert old in it["surrogate"]["expression"], (cid, old)
            it["surrogate"]["expression"] = it["surrogate"]["expression"].replace(old, new)
            return
    raise KeyError(cid)


EVENTS = {
    3: ("Structures re-ran ASWING with the FAR-23 gust case",
        "Adding the gust case raised predicted tip deflection 42%. The structures "
        "region shrinks at high AR — and part of the archive is evicted.",
        "structures",
        lambda: patch("structures", "constraints", "STR.TIP_DEFL", "1.0e-9", "1.42e-9")),
    6: ("Structures switched the spar caps to carbon",
        "Primary structural mass falls 25% for the same stiffness. Weights is still "
        "conditioned on the OLD value, so the archive is gutted before it recovers.",
        "structures",
        lambda: patch("structures", "provides", "W_structure", "0.045 *", "0.0338 *")),
    9: ("Aerodynamics re-lofted the fuselage and rebuilt the wetted area",
        "CD0 build-up fell from 0.0140 to 0.0125 (-11%), relaxing cruise L/D across "
        "the whole domain and opening new territory to explore.",
        "aerodynamics",
        lambda: patch("aerodynamics", "constraints", "AER.LD_CRUISE", "0.014 +", "0.0125 +")),
}

set_cond("weights", "W_structure", 260.0)
set_cond("aerodynamics", "W_empty", 330.0)
set_cond("structures", "CL_max", 1.45)

VBOX, _ = common_box(pubs, space)
rng = np.random.default_rng(SEED)
iv = {v: i for i, v in enumerate(NAMES)}
LO = np.array([VBOX[v][0] for v in NAMES])
HI = np.array([VBOX[v][1] for v in NAMES])
VSPAN = HI - LO


def lhs_box(box, n):
    X = np.empty((n, len(NAMES)))
    for j, v in enumerate(NAMES):
        lo, hi = box[v]
        X[:, j] = lo + ((rng.permutation(n) + rng.random(n)) / n) * (hi - lo)
    return X


def check(X):
    """Full contract check at X: margins, fixed-point consistency, active counts."""
    G, _ = margins(pubs, space, X)
    keys = list(G.keys())
    Gm = np.vstack([G[k] for k in keys])
    feas = np.all(Gm <= 0, axis=0)
    R = consistency(pubs, space, X)
    cons = np.ones(len(X), bool)
    for v in R.values():
        cons &= (v["norm_residual"] <= 1.0) if v["status"] == "OK" else False
    n_active = np.sum(Gm > -0.05, axis=0)
    return feas, cons, feas & cons, Gm, keys, R, n_active


def trust_region(archive):
    if len(archive) == 0:
        return {v: (float(VBOX[v][0]), float(VBOX[v][1])) for v in NAMES}
    tr = {}
    for j, v in enumerate(NAMES):
        lo, hi = archive[:, j].min(), archive[:, j].max()
        w = hi - lo
        lo, hi = lo - PAD * w, hi + PAD * w
        floor = MIN_FRAC * VSPAN[j]
        if hi - lo < floor:
            c = 0.5 * (lo + hi)
            lo, hi = c - floor / 2, c + floor / 2
        tr[v] = (float(max(lo, LO[j])), float(min(hi, HI[j])))
    return tr


def propose(tr, archive, arch_active):
    n_glob = int(BUDGET * MIX["global"])
    n_loc = int(BUDGET * MIX["local"]) if len(archive) else 0
    n_tr = BUDGET - n_glob - n_loc
    parts = [lhs_box(tr, n_tr)]
    if n_loc:
        # exploit near archived designs that already sit on several constraints:
        # corners are what we want to resolve, and they are where the optimum is
        w = arch_active.astype(float) + 0.5
        idx = rng.choice(len(archive), n_loc, p=w / w.sum())
        width = np.array([tr[v][1] - tr[v][0] for v in NAMES])
        pts = archive[idx] + rng.normal(0, 0.09, (n_loc, len(NAMES))) * width
        parts.append(np.clip(pts, LO, HI))
    parts.append(lhs_box(VBOX, n_glob))          # never stop covering globally
    X = np.vstack(parts)
    tag = np.zeros(len(X), np.int8)              # 0=tr 1=local 2=global
    tag[n_tr:n_tr + n_loc] = 1
    tag[n_tr + n_loc:] = 2
    return X, tag


archive = np.empty((0, len(NAMES)))
rounds = []
prev_yield = None
best_hist = []

for r in range(ROUNDS):
    ev = None
    if r in EVENTS:
        title, detail, who, fn = EVENTS[r]
        fn()
        ev = {"title": title, "detail": detail, "discipline": who}

    # --- 1. revalidate the archive against the CURRENT publications ---
    arch_before = len(archive)
    if len(archive):
        _, _, still, aGm, _, _, aAct = check(archive)
        archive = archive[still]
        arch_active = aAct[still]
    else:
        arch_active = np.zeros(0, int)
    evicted = arch_before - len(archive)

    # --- 2. trust region from what survived ---
    tr = trust_region(archive)
    tr_frac = float(np.prod([(tr[v][1] - tr[v][0]) / VSPAN[j]
                             for j, v in enumerate(NAMES)]))

    # --- 3. propose and evaluate this round's budget ---
    X, tag = propose(tr, archive, arch_active)
    feas, cons, both, Gm, keys, R, n_act = check(X)

    yield_pct = float(both.mean()) * 100
    gmask = tag == 2
    ref = both if both.sum() else (feas if feas.sum() else np.ones(len(X), bool))

    # --- 4. instrumented volume measurement (NOT part of the budget) ---
    Xv = lhs_box(VBOX, VOL_N)
    fv, cv_, bv, *_ = check(Xv)
    vol_pct = float(bv.mean()) * 100

    # --- 5. grow the archive ---
    if both.sum():
        archive = np.vstack([archive, X[both]])
        arch_active = np.concatenate([arch_active, n_act[both]])
        if len(archive) > ARCH_CAP:
            keep = rng.choice(len(archive), ARCH_CAP, replace=False)
            archive, arch_active = archive[keep], arch_active[keep]

    # --- 6. corner + best design, from the whole archive ---
    corner, best_w0 = None, None
    if len(archive):
        _, _, ok, aGm, akeys, _, aAct = check(archive)
        if ok.sum():
            A, AA = archive[ok], aAct[ok]
            b = int(np.argmax(AA))
            corner = {v: float(A[b, iv[v]]) for v in NAMES}
            corner["active"] = [akeys[i] for i in range(len(akeys))
                                if aGm[i, np.where(ok)[0][b]] > -0.05]
            best_w0 = float(A[:, iv["W0"]].min())
    best_hist.append(best_w0)

    if corner is not None:
        slice_vals = {v: corner[v] for v in ["S_ref", "t_c", "sweep_c4", "P_SL"]}
        slice6 = {v: corner[v] for v in NAMES}
    else:
        slice_vals = {v: float(np.median(X[ref, iv[v]]))
                      for v in ["S_ref", "t_c", "sweep_c4", "P_SL"]}
        slice6 = {v: float(np.median(X[ref, iv[v]])) for v in NAMES}

    take = rng.choice(len(X), min(220, len(X)), replace=False)
    cls = np.zeros(len(X), int); cls[feas & ~cons] = 1; cls[both] = 2
    pts = [[round(float(X[i, iv["S_ref"]]), 2), round(float(X[i, iv["AR"]]), 3),
            round(float(X[i, iv["t_c"]]), 4), round(float(X[i, iv["sweep_c4"]]), 1),
            round(float(X[i, iv["W0"]]), 1), round(float(X[i, iv["P_SL"]]), 1),
            int(cls[i]), int(tag[i])] for i in take]
    apts = []
    if len(archive):
        at = rng.choice(len(archive), min(140, len(archive)), replace=False)
        apts = [[round(float(archive[i, iv["S_ref"]]), 2), round(float(archive[i, iv["AR"]]), 3),
                 round(float(archive[i, iv["t_c"]]), 4), round(float(archive[i, iv["sweep_c4"]]), 1),
                 round(float(archive[i, iv["W0"]]), 1), round(float(archive[i, iv["P_SL"]]), 1)] for i in at]

    assumed = {"W_empty": by_disc["aerodynamics"].cond_values()["W_empty"],
               "W_structure": by_disc["weights"].cond_values()["W_structure"],
               "CL_max": by_disc["structures"].cond_values()["CL_max"]}

    disc_feas = {d: round(float(np.mean(np.all(
        np.vstack([Gm[keys.index(k)] for k in keys if k.startswith(d + ":")]) <= 0,
        axis=0))) * 100, 1) for d in DISCS}

    msgs, notes = [], {}
    for k, v in R.items():
        if v["status"] != "OK":
            continue
        tgt = float(np.median(v["actual"][ref]))
        drift = abs(tgt - v["assumed"]) / max(abs(v["assumed"]), 1e-12)
        tol = v["tol"]["value"] if v["tol"]["kind"] == "relative" \
            else v["tol"]["value"] / max(abs(v["assumed"]), 1e-12)
        consumer, cvn = k.split("<-")
        rep = drift > tol
        msgs.append({"to": consumer, "cv": cvn, "was": round(v["assumed"], 1),
                     "target": round(tgt, 1), "drift": round(drift * 100, 1),
                     "verdict": "REPUBLISH" if rep else "hold"})
        notes[consumer] = {"status": "republish" if rep else "closed", "cv": cvn,
                           "drift": round(drift * 100, 1), "feas": disc_feas[consumer]}
        set_cond(consumer, cvn, v["assumed"] + ALPHA * (tgt - v["assumed"]))

    NICE = {"AER.LD_CRUISE": "cruise L/D", "AER.V_STALL": "stall speed",
            "STR.TIP_DEFL": "tip deflection", "STR.SPAR_DEPTH": "spar depth",
            "WTS.CLOSURE": "mass closure", "WTS.PWR_LOADING": "power loading"}
    binding = [NICE.get(k.split(":")[1], k) for k in (corner["active"] if corner else [])]
    dy = None if prev_yield is None else round(yield_pct - prev_yield, 2)
    prev_yield = yield_pct

    if ev:
        headline = ev["title"]
    elif evicted > 0:
        headline = (f"{evicted} archived designs evicted on revalidation — "
                    f"trust region re-expanded to {tr_frac*100:.1f}% of the box")
    elif not both.sum():
        headline = "No admissible designs — overlap exists but rests on bad premises"
    elif dy is not None and dy >= 3:
        headline = (f"Search sharpened — yield {prev_yield:.0f}% of samples "
                    f"in {tr_frac*100:.1f}% of the box; archive {len(archive)}")
    else:
        headline = (f"Yield {yield_pct:.0f}%, archive {len(archive)}"
                    + (f" — corner limited by {' ∧ '.join(binding)}" if binding else ""))

    rounds.append({
        "round": r, "event": ev, "headline": headline, "notes": notes,
        "assumed": {k: round(v, 1) for k, v in assumed.items()},
        "slice": {k: round(v, 3) for k, v in slice_vals.items()},
        "slice6": {k: round(v, 4) for k, v in slice6.items()},
        "stats": {"feas": round(float(feas.mean()) * 100, 2),
                  "cons": round(float(cons.mean()) * 100, 2),
                  "both": round(yield_pct, 2), "vol": round(vol_pct, 3),
                  "delta": dy, "tr_frac": round(tr_frac * 100, 2),
                  "archive": int(len(archive)), "evicted": int(evicted),
                  "best_w0": None if best_w0 is None else round(best_w0, 1),
                  "budget": BUDGET},
        "tr": {v: [round(tr[v][0], 3), round(tr[v][1], 3)] for v in NAMES},
        "disc_feas": disc_feas, "corner": corner,
        "points": pts, "archive_pts": apts, "messages": msgs,
    })
    print(f"r{r:2d} yield {yield_pct:5.1f}%  vol {vol_pct:5.2f}%  "
          f"TR {tr_frac*100:5.1f}%  arch {len(archive):4d}"
          f"{f' (-{evicted})' if evicted else '      '}  "
          f"best W0 {'' if best_w0 is None else f'{best_w0:.0f}':>4}  {'◆' if ev else ' '}")

out = {"box": {v: [round(VBOX[v][0], 3), round(VBOX[v][1], 3)] for v in NAMES},
       "alpha": ALPHA, "budget": BUDGET, "mix": MIX, "rounds": rounds}
(ROOT / "timelapse_data.json").write_text(json.dumps(out))
print("wrote timelapse_data.json,", len(json.dumps(out)) // 1024, "KB")
