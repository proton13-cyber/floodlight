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
from configuration import fit_snapshot

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
    """Set a SCALAR assumption. Still used for couplings that stay scalar (CL_max)."""
    for c in by_disc[disc].raw["conditioning"]:
        if c["coupling_variable"] == cv:
            c["assumed_value"] = float(val)


def cond_entry(disc, cv):
    for c in by_disc[disc].raw["conditioning"]:
        if c["coupling_variable"] == cv:
            return c
    raise KeyError(cv)


def mis_seed(disc, cv, factor):
    """Start the campaign believing something wrong.

    With a scalar assumption, being wrong meant naming the wrong number. With an
    assumed MODEL it means holding a distorted picture of the supplier -- so scale the
    snapshot. 1.58 is "weights thinks structures is 58% heavier than it is": the same
    premise error the original campaign opened with, against a function not a point.
    """
    c = cond_entry(disc, cv)
    if "assumed_model" in c:
        m = c["assumed_model"]
        m["expression"] = f"({factor}*({m['expression']}))"
        m.setdefault("provenance", {})["mis_seeded_by"] = factor
    c["assumed_value"] = float(c["assumed_value"]) * factor


def relax_cond(disc, cv, supplier_disc, alpha):
    """Damped update of a MODEL belief -- toward a FRESH LOW-ORDER FIT, not verbatim.

    Blending toward the supplier's raw expression would let the consumer gradually
    acquire the expert's entire model; within a few rounds the belief is a perfect
    copy, consistency goes uniform in space, and the gold regions dissolve again. A
    receiving team does not accumulate its partner's model -- it refreshes its
    SIMPLIFIED picture at each broadcast. So: refit the linear internalization against
    the supplier's current expression and premises, then damp toward that. The belief
    stays permanently crude (spatial structure survives) while still catching up after
    events (staleness heals).
    """
    c = cond_entry(disc, cv)
    if "assumed_model" not in c:
        return False
    sup_raw = by_disc[supplier_disc].raw
    sup = None
    for pr in sup_raw.get("provides", []):
        if pr["coupling_variable"] == cv:
            sup = pr["surrogate"]
    if sup is None or sup.get("form") != "analytic":
        return False
    frozen = {cc["coupling_variable"]: float(cc["assumed_value"])
              for cc in sup_raw.get("conditioning", [])}
    fresh = fit_snapshot(sup["expression"], sup["inputs"], frozen,
                         sup_raw["validity"]["box"])
    m = c["assumed_model"]
    m["expression"] = (f"({1 - alpha}*({m['expression']}) "
                       f"+ {alpha}*({fresh['expression']}))")
    m["inputs"] = sorted(set(m.get("inputs", [])) | set(fresh["inputs"]))
    m.pop("frozen_inputs", None)   # fitted beliefs carry premises in their coefficients
    return True


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


def scale_constraint(disc, cid, k):
    """Scale the PHYSICAL quantity behind a normalised le_zero constraint by k.

    For g = (q - L)/L, scaling q by k gives g' = k*g + (k-1). Exact, and independent
    of the fitted coefficients -- which is the point. String-patching a coefficient
    ("0.045 *" -> "0.0338 *") only works while that literal exists, so it breaks the
    moment a publication is refitted, on a different machine, or by a different AVL
    build. Declared CONSTANTS are safe to string-patch; fitted expressions are not.
    """
    for it in by_disc[disc].raw["constraints"]:
        if it["id"] == cid:
            e = it["surrogate"]["expression"]
            it["surrogate"]["expression"] = f"({k}*({e}) + {k - 1.0})"
            return
    raise KeyError(cid)


def scale_provides(disc, cv, k):
    """Scale a published coupling variable by k. The expression IS the physical
    quantity here, so this is a straight multiply."""
    for it in by_disc[disc].raw["provides"]:
        if it["coupling_variable"] == cv:
            e = it["surrogate"]["expression"]
            it["surrogate"]["expression"] = f"({k}*({e}))"
            return
    raise KeyError(cv)




# ---------------------------------------------------------------------------
# truth grids for the viz. The dashboard template used to carry its own copy of
# the physics in JavaScript -- the original hand-written r1 surrogates, frozen.
# Every frame anyone ever looked at was real archive points drawn over an
# r1-physics backdrop: admissible planforms in "infeasible" red, tooltips 19x
# off. The viz was an undeclared consumer with a verbatim snapshot that went
# stale eleven revisions ago -- exactly the failure class this repo is about.
# Fix: Python evaluates the CURRENT publications on grids; the JS only paints.
# ---------------------------------------------------------------------------
import base64 as _b64

_G_ORDER = ["AER.V_STALL", "AER.LD_CRUISE", "STR.TIP_DEFL",
            "STR.SPAR_DEPTH", "WTS.CLOSURE", "WTS.PWR_LOADING"]
_R_ORDER = ["W_empty", "W_structure", "CL_max"]


def _b64u8(a):
    return _b64.b64encode(bytes(bytearray(np.asarray(a, dtype=np.uint8)))).decode()


def _make_dist(raw):
    """Histogram the belief/actual pairs onto per-coupling axes FIXED across rounds,
    normalized by one global max per coupling -- scrubbing shows the silhouettes
    moving, never the axes rescaling under them."""
    items = []
    for k in raw[0]:
        cons, cv = k.split("<-")
        sup = raw[0][k]["supplier"]
        allv = np.concatenate([np.concatenate([r[k]["b"], r[k]["a"]]) for r in raw])
        lo, hi = np.percentile(allv, [0.5, 99.5])
        pad = 0.05 * (hi - lo) or 1.0
        lo, hi = float(lo - pad), float(hi + pad)
        if cv.startswith("W_"):
            lo = max(0.0, lo)   # a linear belief can extrapolate negative mass at the
                                # box corners; the axis should not dignify that
        NB = 36
        edges = np.linspace(lo, hi, NB + 1)
        # A scalar belief is a delta function: histogrammed, its one bin holds every
        # sample and a SHARED normalization squashes the partner's real distribution
        # flat. So: each curve normalized by its own max across rounds, and a scalar
        # belief shipped as a value ("bv") to be drawn as a needle, not as a
        # degenerate histogram.
        scal = [float(np.ptp(r[k]["b"])) < 1e-9 for r in raw]
        hb = [None if sc else np.histogram(np.clip(r[k]["b"], lo, hi), bins=edges)[0]
              for sc, r in zip(scal, raw)]
        ha = [np.histogram(np.clip(r[k]["a"], lo, hi), bins=edges)[0] for r in raw]
        mxa = max(1, max(int(h.max()) for h in ha))
        mxb = max([1] + [int(h.max()) for h in hb if h is not None])
        rounds = []
        for sc, b, a, r in zip(scal, hb, ha, raw):
            rec = {"a": np.round(a / mxa * 100).astype(int).tolist(),
                   "ok": round(float(r[k]["ok"]), 1)}
            # WHERE the pointwise failures live, binned by the supplier's value.
            # Consistency is design-by-design, so two near-identical silhouettes can
            # still fail badly -- errors that change sign across the box cancel in the
            # marginal, and a RELATIVE tolerance is harshest where the value is small.
            # Without this strip the panel invites exactly that misreading.
            av = np.clip(r[k]["a"], lo, hi)
            idx = np.clip(np.digitize(av, edges) - 1, 0, NB - 1)
            fail = np.zeros(NB)
            for bi in range(NB):
                m = idx == bi
                if m.any():
                    fail[bi] = float(np.mean(r[k]["nr"][m] > 1.0))
            rec["f"] = np.round(fail * 100).astype(int).tolist()
            if sc:
                rec["bv"] = round(float(r[k]["b"][0]), 4)
            else:
                rec["b"] = np.round(b / mxb * 100).astype(int).tolist()
            rounds.append(rec)
        items.append({"cv": cv, "consumer": cons, "supplier": sup,
                      "lo": round(lo, 3), "hi": round(hi, 3),
                      "unit": "kg" if cv.startswith("W_") else "",
                      "rounds": rounds})
    return {"bins": 36, "items": items}


def _grid_eval(Xg):
    G, _ = margins(pubs, space, Xg)
    R = consistency(pubs, space, Xg)
    fa = np.ones(len(Xg), bool); fs = fa.copy(); fw = fa.copy()
    for k, g in G.items():
        d = k.split(":")[0]; m = g <= 0
        if d == "aerodynamics": fa &= m
        elif d == "structures": fs &= m
        else: fw &= m
    cons = np.ones(len(Xg), bool)
    for v in R.values():
        cons &= (v["norm_residual"] <= 1.0) if v["status"] == "OK" else False
    adm = fa & fs & fw & cons
    byte = (fa.astype(np.uint8) | (fs.astype(np.uint8) << 1)
            | (fw.astype(np.uint8) << 2) | (cons.astype(np.uint8) << 3)
            | (adm.astype(np.uint8) << 4))
    return G, R, byte


def _slice_X(fixed, vx, vy, nx, ny):
    bx, by = VBOX[vx], VBOX[vy]
    xs = bx[0] + (np.arange(nx) + .5) / nx * (bx[1] - bx[0])
    ys = by[1] - (np.arange(ny) + .5) / ny * (by[1] - by[0])   # top row = high
    X = np.empty((nx * ny, len(NAMES)))
    for v in NAMES:
        X[:, iv[v]] = fixed[v]
    X[:, iv[vx]] = np.tile(xs, ny)
    X[:, iv[vy]] = np.repeat(ys, nx)
    return X


def viz_grids(slice6):
    # main map, AR x W0
    FW, FH = 96, 96
    _, _, byte = _grid_eval(_slice_X(slice6, "AR", "W0", FW, FH))
    field = {"w": FW, "h": FH, "m": _b64u8(byte)}
    # tooltip values, coarser
    TW, TH = 36, 24
    G, R, _ = _grid_eval(_slice_X(slice6, "AR", "W0", TW, TH))
    gp = []
    for suf in _G_ORDER:
        g = next(v for k, v in G.items() if k.endswith(suf))
        gp.append(_b64u8(np.clip(np.round(g * 20) + 128, 0, 255)))
    rp_ = []
    for suf in _R_ORDER:
        v = next((v for k, v in R.items()
                  if v["status"] == "OK" and k.endswith("<-" + suf)), None)
        arr = v["norm_residual"] if v is not None else np.zeros(TW * TH)
        rp_.append(_b64u8(np.clip(np.round(arr * 16), 0, 255)))
    tt = {"w": TW, "h": TH, "g": gp, "r": rp_}
    # pairwise panels, lower triangle of PGV order (must match the template loop)
    PGV = ["AR", "W0", "S_ref", "P_SL", "t_c", "sweep_c4"]
    PR = 24
    panels = []
    for jj in range(6):
        for ii in range(jj):
            _, _, byte = _grid_eval(_slice_X(slice6, PGV[ii], PGV[jj], PR, PR))
            panels.append(_b64u8(byte))
    return field, tt, {"pr": PR, "m": panels}


EVENTS = {
    3: ("Structures added the FAR-23 gust load case",
        "Adding the gust case raises required cap area 42%. The structures "
        "region shrinks at high AR — and part of the archive is evicted.",
        "structures",
        lambda: scale_constraint("structures", "STR.TIP_DEFL", 1.42)),
    6: ("Structures switched the spar caps to carbon",
        "Primary structural mass falls 53% for the same stiffness. Weights is still "
        "conditioned on the OLD value, so the archive is gutted before it recovers.",
        "structures",
        # Carbon caps. The beam model measures -53% on a stiffness-critical wing, not
        # the -25% this event originally asserted -- carbon wins twice, on modulus and
        # on density, and the saving grows with aspect ratio.
        lambda: scale_provides("structures", "W_structure", 0.47)),
    # Retargeted for aerodynamics-r2. r1's CD0 was one blended constant ("0.014 + ..."),
    # so the fuselage re-loft was patched onto a number that mixed wing and non-wing
    # drag. r2 splits them: the wing term is fitted from strip integration and the
    # non-wing parasite term is the declared build-up -- which is the only part a
    # fuselage re-loft can actually touch. Same -11% on the term that represents the
    # fuselage, applied to the term that represents the fuselage.
    9: ("Aerodynamics re-lofted the fuselage and rebuilt the wetted area",
        "Non-wing parasite build-up fell 11%, relaxing cruise L/D across "
        "the whole domain and opening new territory to explore.",
        "aerodynamics",
        lambda: patch("aerodynamics", "constraints", "AER.LD_CRUISE",
                      "0.0062 + 0.14/S_ref", "0.0055 + 0.1246/S_ref")),
}

# Open on bad premises, as the original campaign did -- expressed against whatever
# form the conditioning actually takes.
mis_seed("weights", "W_structure", 260.0 / 165.0)
mis_seed("aerodynamics", "W_empty", 330.0 / 390.0)
set_cond("structures", "CL_max", 1.45)

VBOX, _ = common_box(pubs, space)

# Fixed sample for the belief-distribution panel: the SAME points every round, so the
# histograms move only when beliefs or suppliers move, never from sampling noise.
_rngd = np.random.default_rng(7)
_dist_raw = []
_X_DIST = None
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

    # Truth grids for the viz, computed BEFORE the broadcast relaxes beliefs --
    # the map must show the world this round actually sampled, or the stats panel
    # ("no admissible designs") and the picture (a gold region) contradict.
    _viz_field, _viz_tt, _viz_pg = viz_grids(slice6)

    # Belief distributions: push each discipline's ASSUMED picture of its partners,
    # and the partner's ACTUAL published function, through the same fixed sample of
    # the jointly trusted box. Two histograms per coupling per round. A scalar belief
    # shows up as a needle; a lossy model as a spread; staleness as the two
    # silhouettes separating. This is the conditioning mechanism drawn directly.
    if _X_DIST is None:
        _X_DIST = LO + _rngd.random((3000, len(NAMES))) * (HI - LO)
    _Rd = consistency(pubs, space, _X_DIST)
    _dr = {}
    for _k, _v in _Rd.items():
        if _v["status"] != "OK":
            continue
        _b = _v.get("assumed_arr")
        _b = np.full(len(_X_DIST), _v["assumed"]) if _b is None else np.asarray(_b, float)
        _dr[_k] = {"supplier": _v["supplier"], "b": _b.copy(),
                   "a": np.asarray(_v["actual"], float).copy(),
                   "nr": np.asarray(_v["norm_residual"], float).copy(),
                   "ok": 100 * float(np.mean(_v["norm_residual"] <= 1.0))}
    _dist_raw.append(_dr)

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
        # BOTH updates, not either/or. The model is what the consistency check
        # evaluates; the scalar is what namespace() feeds this discipline's OWN
        # surrogates when it acts as a supplier. An either/or here left the scalar
        # frozen at its mis-seeded value once a model existed, which poisoned the
        # supplier chain (weights kept producing W_empty with W_structure=260 baked
        # in) and zeroed every round of the campaign.
        relax_cond(consumer, cvn, v["supplier"], ALPHA)
        if np.isfinite(tgt):
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
        "field": _viz_field, "tt": _viz_tt, "pg": _viz_pg,
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
       "alpha": ALPHA, "budget": BUDGET, "mix": MIX, "rounds": rounds,
       "dist": _make_dist(_dist_raw)}
(ROOT / "timelapse_data.json").write_text(json.dumps(out))
print("wrote timelapse_data.json,", len(json.dumps(out)) // 1024, "KB")
