# floodlight

**Decentralized MDAO by agent consensus.**

Three asynchronous discipline agents — aerodynamics, structures, weights — search a shared
6-dimensional aircraft design space under a publish–intersect–verify protocol. Each agent
publishes a *region* of feasible designs (constraint margins over the shared design vector,
conditioned on assumed coupling values); an orchestrator intersects those regions; a
deterministic witness verifies both feasibility **and** self-consistency of the assumptions
behind them.

**[▶ Live interactive timelapse](https://proton13-cyber.github.io/floodlight/timelapse.html)** · **[Write-up](https://proton13-cyber.github.io/floodlight/writeup.html)** · **[PDF](https://proton13-cyber.github.io/floodlight/writeup.pdf)**

_Links go live once GitHub Pages is enabled: Settings → Pages → Source `main`, folder `/docs`._

## Quickstart

```bash
git clone https://github.com/proton13-cyber/floodlight.git
cd floodlight
pip install numpy
./build.sh                      # re-runs the campaign and rebuilds the viz
open docs/timelapse.html        # or just double-click it
```

Everything in `docs/` is self-contained — no build step, no server, no network. Open the HTML
files directly from disk and they work.

## Layout

```
mdao_contract.py                 validator + intersection/fixed-point engine (numpy only)
make_timelapse_data.py           the 12-round campaign simulator
build_report.py                  assembles the write-up
build.sh                         regenerate everything
schemas/                         the region-publication JSON Schema — the contract
spaces/                          canonical design vector + coupling registry
publications/                    the three discipline publications
viz/                             viz template (data injected at build time)
docs/                            built, self-contained outputs — this is what Pages serves
```

Run the engine standalone against the published regions:

```bash
python3 mdao_contract.py                 # intersect, verify, report
python3 mdao_contract.py --k-sigma 2.0   # reliability-aware: require g + 2sigma <= 0
```

---

## The five ideas the schema encodes

**1. One canonical design space, hash-gated.**
`design_space_ref` carries id + version + hash. Two publications whose hashes
differ describe sets over *different* spaces; the engine refuses to intersect
them rather than silently producing a plausible answer. This is the cheapest
high-value check in the whole system and agents will violate it constantly —
someone adds a variable, someone else is still on the old vector.

**2. Publish margins, not booleans.**
Every constraint is normalized to `g(x) ≤ 0`, with the normalization scale
declared. A feasible/infeasible classifier throws away exactly what the
orchestrator needs: *how much* margin, which constraint is closest, and where the
boundary is. Normalizing at publication time (not intersection time) is what makes
a structures margin comparable to an aero margin.

**3. A validity box is mandatory.**
Surrogates queried outside their training domain don't fail — they lie, fluently.
`validity.box` states where the discipline actually sampled, and
`extrapolation_policy: reject` tells the orchestrator to discard candidates
outside it. In the worked example the three disciplines' boxes intersect to only
**35% of the declared design-space volume**, and the report names which discipline
clipped which bound. That number is itself a finding: it tells you where to spend
the next DOE budget.

**4. Conditioning is the load-bearing field.**
A discipline's feasible region is drawn *assuming* values for coupling variables
it doesn't own. Aero's L/D constraint assumes an empty mass. Weights' closure
constraint assumes a structural mass. Change those and the region moves. So
`conditioning[]` records, per coupling variable: the assumed value, its source
(which publication, or "seed", or "requirement"), and a **drift tolerance** — how
far the true value may wander before the region must be re-derived.

This is what turns a static Venn diagram into a fixed point.

**5. `provides[]` closes the loop.**
Each discipline also publishes its *outputs* as surrogates over the shared vector.
That lets the orchestrator evaluate, at any candidate point x, what the supplier
actually produces there and compare it against what the consumer assumed. That
comparison is the consistency test, and without `provides[]` you cannot run it.

---

## What the demo shows

Three disciplines over a 6D UAV design vector, 200k samples inside the common
validity box:

```
feasible (margins only) ............  13.18%   26,357 pts
self-consistent (fixed point) ......   9.34%   18,673 pts
BOTH -- an admissible design region    1.31%    2,617 pts
```

The naive Venn intersection is **ten times larger** than the admissible set. Every
point in that gap satisfies all six disciplinary constraints and is still garbage,
because the regions were drawn under premises that don't hold there — weights
assumed a 165 kg structure at points where structures produces 320 kg.

Perturb weights' assumed `W_structure` from 165 to 260 kg and the engine reports:

```
feasible (margins only) ............   7.45%   4,471 pts
self-consistent (fixed point) ......   0.00%       0 pts

>>> The naive intersection is NON-EMPTY but every point in it violates at
    least one conditioning assumption.

REPUBLISH aerodynamics   re-derive with W_empty = 509.9 (was 390, drift 30.8%)
REPUBLISH weights        re-derive with W_structure = 158.0 (was 260, drift 39.2%)
```

That last block is the agent-messaging payload: a specific, numeric, addressed
instruction, not prose. It's derived, not authored, so it can't hallucinate.

**Empty feasible set is a first-class result.** Tighten the L/D requirement to
19.5 and the engine reports the least-infeasible point, the binding constraint
(`AER.LD_CRUISE`, g = +0.258), and what else is near-active there. "No designs
found" is a dead end; "aero is short 26% on cruise L/D, and stall speed and tip
deflection are both already active at the least-bad point" tells you which
requirement to renegotiate or which technology assumption to change.

**Corners, not interiors.** When the admissible set is non-empty the engine
reports the point where the most constraints go simultaneously active. In the
worked example that's a vertex where `AER.LD_CRUISE`, `STR.TIP_DEFL`, and
`WTS.CLOSURE` are all within 5% of their limits. Minimum-mass aircraft are
constraint-limited by construction; the optimum lives on that vertex, not in the
fat middle. A sampler that reports the centroid of the intersection is reporting
a design nobody would build.

---

## The orchestrator loop

```
round r:
  1. gate       every publication: schema-valid, design_space_ref matches,
                not superseded. Reject the round otherwise.
  2. domain     intersect validity boxes -> common domain D.
                If empty, name the disjoint pair and commission a DOE.
  3. margins    sample D, evaluate all g_i(x). Optionally g + kσ ≤ 0.
  4. fixpoint   for each conditioning entry, evaluate the supplier's provides[]
                surrogate at the same x; normalized residual ≤ 1 or the point
                is inadmissible.
  5. decide     admissible = feasible AND consistent.
                  empty + inconsistent  -> broadcast REPUBLISH with new targets
                  empty + consistent    -> broadcast binding conflict; this is a
                                           requirements problem, not a search problem
                  non-empty             -> characterize the boundary, publish the
                                           corner set, sample the next DOE near it
  6. broadcast  one message per discipline, containing numbers from step 5.
                Prose goes in the rationale field; it is never the payload.
```

Damping matters. Updating every discipline's conditioning to the new median at
once is a Jacobi step and will oscillate on the gross-mass loop. Under-relax:
`assumed_{r+1} = assumed_r + α(target − assumed_r)` with α ≈ 0.5, or update
disciplines in dependency order (Gauss-Seidel). The contract supports either;
the reference engine just reports the raw target.

---

## The campaign: making rounds actually learn

`make_timelapse_data.py` runs a 12-round campaign. Three mechanisms carry
information forward beyond the damped fixed point:

- **Trust region** — the sampling box contracts onto the bounding box of the
  admissible archive (padded 18%, floored at 5% per dimension, clipped to the
  validity box, which it may never leave).
- **Persistent archive** — admissible designs accumulate instead of being
  rediscovered, and are **revalidated every round**. When an event rewrites a
  surrogate, designs that are no longer admissible are evicted.
- **Boundary-seeking proposals** — the per-round budget splits 50% exploring the
  trust region, 30% perturbing archived designs that already sit on several
  active constraints (corners are what you want to resolve), and 20% uniform over
  the full validity box so a stale trust region can always be escaped.

Two numbers are reported and must not be conflated:

| | measures | behavior |
|---|---|---|
| **Yield** | admissible share of the points the campaign *sampled* | how good the search is — should climb |
| **Volume** | admissible share of the whole validity box, from a separate uniform draw **not charged to the budget** | how big the answer is — moves only when regions move |

Result: yield goes **1.8% → 20%** while volume stays flat around 1.2%. An 11×
improvement in search efficiency, and no improvement in the design space. Report
only yield and you'd be claiming a better aircraft; report only volume and you'd
miss that the search got smarter. Both, or neither.

Two findings fell out of the run that weren't designed in:

1. **A beneficial change can destroy the search.** At round 6 structures cuts
   structural mass 25% (carbon spar caps). Weights is still conditioned on the old
   value, so the drift blows past tolerance and **the entire 4,000-design archive
   is evicted in one round**. Yield → 0%, trust region → 100%, and the campaign
   restarts its search from scratch. Improving one discipline invalidated every
   other discipline's accumulated work.
2. **`t_c` and `sweep` never contract.** The trust region shrinks hard on `W0`,
   `P_SL`, and `S_ref` and stays at 100% on thickness and sweep — those variables
   simply aren't binding. That's the `active_subspace` field's claim confirmed
   empirically, and it says where *not* to spend the next DOE.

A caveat the run exposed: a tight drift tolerance acts like a constraint. It pins
each coupling variable near its assumed value, so the admissible set is "feasible
AND near the current assumption", which is narrower than the true feasible set.
After round 6 that pushed the best-found design from 619 kg to ~788 kg — the
campaign re-converged into a different basin. The fix is to tighten tolerances
only as the fixed point converges, or to treat consistency as a solve rather than
a filter.

## Deliberate omissions, and where they'd go

- **Surrogate artifacts.** The examples use `form: "analytic"` because it makes
  the demo self-contained. Production should use `form: "artifact"` with an ONNX
  or joblib blob and a sha256. Evaluating agent-authored expressions is a
  code-execution surface — allowlist it or drop it.
- **Uncertainty is present but shallow.** `sigma` supports a constant; a real GP
  publishes heteroscedastic variance, and then `g + kσ ≤ 0` becomes a genuine
  reliability constraint rather than a flat pad. The `--k-sigma` flag shows the
  shape of it.
- **No optimizer.** Deliberately. LHS + masking is enough to characterize a
  region, and the moment you want the corner precisely you should hand the
  margin surrogates to a real NLP solver or a constrained-EI acquisition — not
  to an LLM. Agents belong at the campaign level: choosing what to sample, fixing
  broken input decks, diagnosing divergence, proposing configuration changes.
- **Configuration variables.** The shared vector here is all continuous. Discrete
  and categorical choices (canard vs. conventional, engine count, tail
  architecture) are where LLM agents could genuinely beat classical MDO, since
  there are no gradients to exploit. The clean way to add them: one design space
  per configuration, and the configuration itself becomes the thing the
  orchestrator branches on.
- **Provenance is declared, not enforced.** `evidence.input_deck_refs` carries
  sha256s but nothing checks them. It should — asynchronous agents guarantee you
  will need to replay a six-week-old design.
- **Trust/quality weighting.** All disciplines are equally believed. In practice
  an empirical Raymer fit and an ASWING aeroelastic solve deserve different
  weight; `evidence.tool.fidelity` is the hook for that.

---

## Honest caveat on the physics

Every coefficient in the example publications is illustrative — plausible in form
and magnitude, fitted to nothing. `known_limitations` on each publication says so.
The point of the worked example is to exercise the *contract*, not to size an
aircraft. Swap in real ASWING and real group weights and the machinery is
unchanged, which is the whole idea.
