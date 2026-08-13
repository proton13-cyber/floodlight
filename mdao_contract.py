#!/usr/bin/env python3
"""
Reference implementation of the disciplinary region-publication contract.

What this does, in order:
  1. Validates each publication against the canonical design space (hard gate on
     design_space_ref).
  2. Intersects the VALIDITY boxes. Surrogates outside their training domain do
     not fail, they lie -- so the common domain is computed first and everything
     else happens inside it.
  3. Samples the common domain and evaluates every discipline's NORMALIZED
     constraint margins g(x) <= 0. Feasible set = all margins satisfied.
  4. Checks the FIXED POINT. Each publication's region was drawn assuming values
     for coupling variables it does not own. At every candidate x, the supplier's
     surrogate is evaluated and compared against the consumer's assumed value.
     A point is CONSISTENT only if every such residual is inside the consumer's
     declared drift tolerance.
  5. Reports the difference. "Feasible" without "consistent" is the failure mode
     this whole contract exists to catch: a non-empty intersection of regions
     that were each drawn under mutually incompatible assumptions.

Only dependency is numpy.

SECURITY NOTE: form="analytic" surrogates are evaluated with a restricted eval.
That is fine for a demo and for closed-form empirical methods you wrote yourself.
It is a code-execution surface. Production agents should publish form="artifact"
(ONNX or joblib) with a sha256, and the orchestrator should refuse "analytic"
from any agent that is not on an allowlist.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

import numpy as np

# --------------------------------------------------------------------------
# restricted evaluation namespace for form="analytic" surrogates
# --------------------------------------------------------------------------

SAFE_NS: dict[str, Any] = {
    "sqrt": np.sqrt, "log": np.log, "log10": np.log10, "exp": np.exp,
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "arctan": np.arctan,
    "abs": np.abs, "minimum": np.minimum, "maximum": np.maximum,
    "clip": np.clip, "where": np.where, "radians": np.radians,
    "degrees": np.degrees, "pi": math.pi, "e_const": math.e,
}


def evaluate_surrogate(sur: dict, ns: dict[str, Any]) -> np.ndarray:
    """Evaluate a surrogate in the given variable namespace."""
    form = sur["form"]
    if form == "analytic":
        missing = [n for n in sur["inputs"] if n not in ns]
        if missing:
            raise KeyError(f"surrogate inputs not in namespace: {missing}")
        env = {"__builtins__": {}}
        env.update(SAFE_NS)
        env.update({k: ns[k] for k in sur["inputs"]})
        with np.errstate(all="ignore"):
            return np.asarray(eval(sur["expression"], env), dtype=float)  # noqa: S307
    raise NotImplementedError(
        f"surrogate form '{form}' not evaluable in the reference impl; "
        "wire an ONNX/joblib loader here for production artifacts."
    )


def surrogate_sigma(sur: dict, n: int) -> np.ndarray:
    s = sur.get("sigma")
    if not s:
        return np.zeros(n)
    if s.get("form") == "constant":
        return np.full(n, float(s["value"]))
    return np.zeros(n)


# --------------------------------------------------------------------------
# loading + validation
# --------------------------------------------------------------------------

@dataclass
class Space:
    raw: dict
    var_names: list[str]
    bounds: dict[str, tuple[float, float]]
    coupling: dict[str, dict]
    constants: dict[str, float]

    @property
    def ref_hash(self) -> str:
        return self.raw["hash"]


def load_space(path: pathlib.Path) -> Space:
    raw = json.loads(path.read_text())
    names = [v["name"] for v in raw["design_variables"]]
    bounds = {v["name"]: (float(v["bounds"][0]), float(v["bounds"][1]))
              for v in raw["design_variables"]}
    coupling = {c["name"]: c for c in raw["coupling_variables"]}
    consts = {k: float(v["value"]) for k, v in raw.get("constants", {}).items()}
    return Space(raw, names, bounds, coupling, consts)


@dataclass
class Publication:
    raw: dict
    path: pathlib.Path

    @property
    def pid(self) -> str: return self.raw["publication_id"]
    @property
    def discipline(self) -> str: return self.raw["discipline"]
    @property
    def constraints(self) -> list[dict]: return self.raw["constraints"]
    @property
    def conditioning(self) -> list[dict]: return self.raw.get("conditioning", [])
    @property
    def provides(self) -> list[dict]: return self.raw.get("provides", [])

    def cond_values(self) -> dict[str, float]:
        return {c["coupling_variable"]: float(c["assumed_value"])
                for c in self.conditioning}


def load_publications(d: pathlib.Path) -> list[Publication]:
    pubs = [Publication(json.loads(p.read_text()), p)
            for p in sorted(d.glob("*.json"))]
    return [p for p in pubs if p.raw.get("status") == "published"]


def validate(pubs: list[Publication], space: Space) -> list[str]:
    """Structural checks that must pass before any intersection is attempted."""
    errs: list[str] = []
    superseded = {p.raw.get("supersedes") for p in pubs if p.raw.get("supersedes")}

    for p in pubs:
        tag = f"[{p.pid}]"

        ref = p.raw["design_space_ref"]
        if ref["hash"] != space.ref_hash or ref["id"] != space.raw["id"] \
                or ref["version"] != space.raw["version"]:
            errs.append(f"{tag} design_space_ref does not match the canonical "
                        f"space -- this publication describes a set over a "
                        f"DIFFERENT space and must not be intersected.")

        if p.pid in superseded:
            errs.append(f"{tag} is superseded by a newer publication.")

        for v, (lo, hi) in p.raw["validity"]["box"].items():
            if v not in space.bounds:
                errs.append(f"{tag} validity box references unknown variable '{v}'.")
                continue
            slo, shi = space.bounds[v]
            if lo < slo - 1e-9 or hi > shi + 1e-9:
                errs.append(f"{tag} validity box for '{v}' [{lo},{hi}] exceeds "
                            f"the design space bounds [{slo},{shi}].")
        for v in space.var_names:
            if v not in p.raw["validity"]["box"]:
                errs.append(f"{tag} validity box omits design variable '{v}'.")

        for c in p.conditioning:
            cv = c["coupling_variable"]
            if cv not in space.coupling:
                errs.append(f"{tag} conditions on unknown coupling variable '{cv}'.")
            elif space.coupling[cv]["supplier"] == p.discipline:
                errs.append(f"{tag} conditions on '{cv}' which it supplies itself.")

        for pr in p.provides:
            cv = pr["coupling_variable"]
            if cv not in space.coupling:
                errs.append(f"{tag} provides unknown coupling variable '{cv}'.")
            elif space.coupling[cv]["supplier"] != p.discipline:
                errs.append(f"{tag} provides '{cv}' but the registry names "
                            f"'{space.coupling[cv]['supplier']}' as supplier.")

        for con in p.constraints:
            if con["sense"] != "le_zero":
                errs.append(f"{tag} constraint {con['id']} is not normalized to g<=0.")
            if con["normalization"]["scale"] <= 0:
                errs.append(f"{tag} constraint {con['id']} has non-positive scale.")

    return errs


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

def common_box(pubs: list[Publication], space: Space):
    box, blame = {}, {}
    for v in space.var_names:
        lo, hi = space.bounds[v]
        lo_src = hi_src = "design_space"
        for p in pubs:
            plo, phi = p.raw["validity"]["box"][v]
            if plo > lo:
                lo, lo_src = plo, p.discipline
            if phi < hi:
                hi, hi_src = phi, p.discipline
        box[v] = (lo, hi)
        blame[v] = (lo_src, hi_src)
    return box, blame


def lhs(box: dict[str, tuple[float, float]], names: list[str],
        n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    d = len(names)
    X = np.empty((n, d))
    for j, v in enumerate(names):
        lo, hi = box[v]
        cut = (rng.permutation(n) + rng.random(n)) / n
        X[:, j] = lo + cut * (hi - lo)
    return X


def namespace(X: np.ndarray, space: Space, pub: Publication) -> dict[str, Any]:
    ns: dict[str, Any] = {v: X[:, i] for i, v in enumerate(space.var_names)}
    ns.update(space.constants)
    ns.update(pub.cond_values())
    return ns


# --------------------------------------------------------------------------
# the two masks
# --------------------------------------------------------------------------

def margins(pubs, space, X, k_sigma=0.0):
    out: dict[str, np.ndarray] = {}
    meta: dict[str, dict] = {}
    for p in pubs:
        ns = namespace(X, space, p)
        for con in p.constraints:
            g = evaluate_surrogate(con["surrogate"], ns)
            g = np.broadcast_to(np.nan_to_num(g, nan=1e6), (len(X),)).copy()
            if k_sigma:
                g = g + k_sigma * surrogate_sigma(con["surrogate"], len(X))
            key = f"{p.discipline}:{con['id']}"
            out[key] = g
            meta[key] = {"pub": p.pid, "desc": con["description"],
                         "crit": con.get("criticality", "hard")}
    return out, meta


def consistency(pubs, space, X):
    """Fixed-point residuals: supplier-produced vs consumer-assumed, at the same x."""
    suppliers: dict[str, tuple[Publication, dict]] = {}
    for p in pubs:
        for pr in p.provides:
            suppliers[pr["coupling_variable"]] = (p, pr)

    res: dict[str, dict] = {}
    for p in pubs:
        for c in p.conditioning:
            cv = c["coupling_variable"]
            src_type = c["source"]["type"]
            if cv not in suppliers:
                # requirements-frozen couplings have no disciplinary supplier
                if src_type != "requirement":
                    res[f"{p.discipline}<-{cv}"] = {
                        "status": "UNSUPPLIED",
                        "assumed": c["assumed_value"],
                        "norm_residual": np.full(len(X), np.inf),
                        "tol": c["drift_tolerance"]["value"],
                    }
                continue
            sup_pub, pr = suppliers[cv]
            actual = evaluate_surrogate(pr["surrogate"], namespace(X, space, sup_pub))
            actual = np.broadcast_to(np.nan_to_num(actual, nan=1e9), (len(X),)).copy()
            assumed = float(c["assumed_value"])
            tol = c["drift_tolerance"]
            if tol["kind"] == "relative":
                norm = np.abs(actual - assumed) / max(abs(assumed), 1e-12) / tol["value"]
            else:
                norm = np.abs(actual - assumed) / tol["value"]
            res[f"{p.discipline}<-{cv}"] = {
                "status": "OK",
                "supplier": sup_pub.discipline,
                "assumed": assumed,
                "actual": actual,
                "norm_residual": norm,   # <= 1.0 means inside declared tolerance
                "tol": tol,
            }
    return res


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def bar(frac: float, width: int = 28) -> str:
    n = int(round(frac * width))
    return "#" * n + "." * (width - n)


def main() -> int:
    ap = argparse.ArgumentParser()
    root = pathlib.Path(__file__).parent
    ap.add_argument("--space", default=str(root / "spaces/design_space.uav-medium.v1.json"))
    ap.add_argument("--pubs", default=str(root / "publications"))
    ap.add_argument("-n", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--k-sigma", type=float, default=0.0,
                    help="reliability margin: require g + k*sigma <= 0")
    args = ap.parse_args()

    space = load_space(pathlib.Path(args.space))
    pubs = load_publications(pathlib.Path(args.pubs))

    print("=" * 74)
    print(f"DESIGN SPACE  {space.raw['id']} v{space.raw['version']}  "
          f"({len(space.var_names)}D)   mission {space.raw['mission_ref']['id']}")
    print(f"PUBLICATIONS  {', '.join(p.pid for p in pubs)}")
    print("=" * 74)

    errs = validate(pubs, space)
    if errs:
        print("\nVALIDATION FAILED -- refusing to intersect:")
        for e in errs:
            print("  ! " + e)
        return 1
    print(f"\n[1] validation ......... OK  ({len(pubs)} publications, "
          f"same design_space_ref)")

    box, blame = common_box(pubs, space)
    print("\n[2] common validity domain (intersection of training boxes)")
    frac_vol = 1.0
    for v in space.var_names:
        lo, hi = box[v]
        slo, shi = space.bounds[v]
        if hi <= lo:
            print(f"    {v:9s} EMPTY -- no overlap. Domains are disjoint.")
            return 1
        frac_vol *= (hi - lo) / (shi - slo)
        lo_s, hi_s = blame[v]
        note = []
        if lo_s != "design_space": note.append(f"lo by {lo_s}")
        if hi_s != "design_space": note.append(f"hi by {hi_s}")
        print(f"    {v:9s} [{lo:8.3f},{hi:8.3f}]   "
              f"{'; '.join(note) if note else ''}")
    print(f"    -> {frac_vol*100:.1f}% of the declared design-space volume is "
          f"jointly trusted")

    X = lhs(box, space.var_names, args.n, args.seed)
    G, meta = margins(pubs, space, X, args.k_sigma)
    Gm = np.vstack([G[k] for k in G])
    keys = list(G.keys())

    feas = np.all(Gm <= 0.0, axis=0)
    print(f"\n[3] disciplinary margins over {args.n:,} LHS points"
          f"{f' (k_sigma={args.k_sigma})' if args.k_sigma else ''}")
    for k in keys:
        sat = float(np.mean(G[k] <= 0))
        print(f"    {bar(sat)} {sat*100:5.1f}%  {k}")
    print(f"    {'-'*28} {np.mean(feas)*100:5.1f}%  ALL DISCIPLINES "
          f"(naive Venn intersection)")

    R = consistency(pubs, space, X)
    cons = np.ones(len(X), dtype=bool)
    print("\n[4] fixed-point consistency (supplier-produced vs consumer-assumed)")
    for k, r in R.items():
        if r["status"] == "UNSUPPLIED":
            print(f"    {'?'*28}   n/a  {k}  NO SUPPLIER PUBLICATION")
            cons &= False
            continue
        ok = r["norm_residual"] <= 1.0
        cons &= ok
        tolstr = (f"±{r['tol']['value']*100:.0f}%" if r["tol"]["kind"] == "relative"
                  else f"±{r['tol']['value']:g}")
        print(f"    {bar(float(np.mean(ok)))} {np.mean(ok)*100:5.1f}%  {k}"
              f"  assumed {r['assumed']:g} {tolstr}, supplier "
              f"{r['supplier']} produces {np.min(r['actual']):.1f}"
              f"..{np.max(r['actual']):.1f}")

    both = feas & cons
    print("\n[5] result")
    print(f"    feasible (margins only) ............ {np.mean(feas)*100:6.2f}%  "
          f"{int(feas.sum()):,} pts")
    print(f"    self-consistent (fixed point) ...... {np.mean(cons)*100:6.2f}%  "
          f"{int(cons.sum()):,} pts")
    print(f"    BOTH -- an admissible design region  {np.mean(both)*100:6.2f}%  "
          f"{int(both.sum()):,} pts")

    if feas.sum() and not both.sum():
        print("\n    >>> The naive intersection is NON-EMPTY but every point in it")
        print("        violates at least one conditioning assumption. Those regions")
        print("        were drawn under mutually incompatible premises. Reporting")
        print("        this as 'designs found' would be the central failure mode.")

    if not feas.sum():
        worst = np.max(Gm, axis=0)
        i = int(np.argmin(worst))
        who = keys[int(np.argmax(Gm[:, i]))]
        print("\n    >>> EMPTY feasible set. Least-infeasible point:")
        for j, v in enumerate(space.var_names):
            print(f"          {v:9s} = {X[i, j]:.3f}")
        print(f"        binding constraint: {who}  (g = {worst[i]:+.3f})")
        print(f"        -> {meta[who]['desc']}")
        act = [k for k in keys if G[k][i] > -0.02]
        print(f"        near-active there: {', '.join(act)}")

    if both.sum():
        idx = np.where(both)[0]
        Gb = Gm[:, idx]
        # corner-seeking: good designs sit where several constraints go active
        n_active = np.sum(Gb > -0.05, axis=0)
        best = idx[int(np.argmax(n_active))]
        print("\n    Most constrained admissible point (a corner of the region --")
        print("    this is where the optimum lives, not the fat middle):")
        for j, v in enumerate(space.var_names):
            print(f"          {v:9s} = {X[best, j]:.3f}")
        print("        margins:")
        for k in keys:
            flag = "  <== ACTIVE" if G[k][best] > -0.05 else ""
            print(f"          {k:28s} g = {G[k][best]:+.3f}{flag}")

        print("\n    Admissible ranges (project the region onto each axis):")
        for j, v in enumerate(space.var_names):
            col = X[idx, j]
            print(f"          {v:9s} [{col.min():8.3f},{col.max():8.3f}]  "
                  f"median {np.median(col):8.3f}")

    print("\n[6] next round -- what the orchestrator should broadcast")
    for k, r in R.items():
        if r["status"] != "OK":
            continue
        if both.sum():
            tgt = float(np.median(r["actual"][both]))
        else:
            tgt = float(np.median(r["actual"][feas])) if feas.sum() \
                else float(np.median(r["actual"]))
        drift = abs(tgt - r["assumed"]) / max(abs(r["assumed"]), 1e-12)
        verdict = "REPUBLISH" if drift > r["tol"]["value"] else "hold"
        consumer = k.split("<-")[0]
        print(f"    {verdict:9s} {consumer:14s} re-derive with "
              f"{k.split('<-')[1]} = {tgt:.1f} (was {r['assumed']:g}, "
              f"drift {drift*100:.1f}%)")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
