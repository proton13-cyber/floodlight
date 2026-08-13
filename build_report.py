#!/usr/bin/env python3
"""Assemble the campaign write-up as a single self-contained HTML report."""
import base64
import pathlib

TMP = pathlib.Path("/tmp")
OUT = pathlib.Path(__file__).parent / "mdao_campaign_writeup.html"


def img(name):
    b = (TMP / name).read_bytes()
    return f"data:image/jpeg;base64,{base64.b64encode(b).decode()}"


CSS = """
:root{color-scheme:light dark}
body{margin:0;background:#f7f5f2;color:#191713;
  font:16px/1.62 system-ui,-apple-system,"Segoe UI",sans-serif}
@media (prefers-color-scheme:dark){body{background:#12101c;color:#e8e4f0}}
.wrap{max-width:880px;margin:0 auto;padding:48px 28px 80px}
h1{font-size:30px;line-height:1.2;letter-spacing:-.015em;margin:0 0 6px}
.sub{color:#6d6659;font-size:15px;margin-bottom:36px}
@media (prefers-color-scheme:dark){.sub{color:#9a92ab}}
h2{font-size:21px;letter-spacing:-.01em;margin:44px 0 10px;
  border-bottom:1px solid rgba(128,120,140,.25);padding-bottom:6px}
h3{font-size:16.5px;margin:26px 0 6px}
p{margin:0 0 14px}
figure{margin:20px 0 26px}
figure img{width:100%;border-radius:10px;border:1px solid rgba(128,120,140,.25);display:block}
figcaption{font-size:13.5px;color:#6d6659;margin-top:8px;line-height:1.5}
@media (prefers-color-scheme:dark){figcaption{color:#9a92ab}}
table{border-collapse:collapse;width:100%;margin:14px 0 20px;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid rgba(128,120,140,.25);
  vertical-align:top;font-variant-numeric:tabular-nums}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:#6d6659}
@media (prefers-color-scheme:dark){th{color:#9a92ab}}
code{font:.9em ui-monospace,Menlo,Consolas,monospace;
  background:rgba(128,120,140,.14);padding:1px 5px;border-radius:4px}
.callout{border-left:3px solid #e08a00;background:rgba(224,138,0,.08);
  padding:12px 16px;border-radius:0 8px 8px 0;margin:18px 0}
.callout.warn{border-color:#c92a86;background:rgba(201,42,134,.07)}
.rnum{display:inline-block;background:#e08a00;color:#12101c;font-weight:700;
  border-radius:6px;padding:0 8px;margin-right:8px;font-size:14px}
strong{font-weight:650}
@media print{
  body{background:#fff;color:#111}
  .wrap{max-width:none;padding:0 8mm}
  h1{font-size:24px} h2{font-size:18px;margin-top:26px} h3{font-size:15px}
  h2,h3{break-after:avoid;page-break-after:avoid}
  figure{break-inside:avoid;page-break-inside:avoid;margin:14px 0 18px}
  figure img{border:1px solid #ccc}
  figcaption{color:#444}
  table{break-inside:avoid;page-break-inside:avoid;font-size:12px}
  th{color:#444} p{orphans:3;widows:3;font-size:13.5px;margin-bottom:11px}
  .sub{color:#444}
}
"""

R = {i: img(f"round_{i:02d}.jpg") for i in range(12)}
G = {i: img(f"grid_{i:02d}.jpg") for i in [1,2,3,6,8,11]}
TRACE = img("trace_full.jpg")

HTML = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Decentralized MDAO by Agent Consensus — Campaign Write-up</title>
<style>{CSS}</style></head><body><div class="wrap">

<h1>Decentralized MDAO by Agent Consensus</h1>
<div class="sub">A 12-round design campaign for a medium fixed-wing UAV, run by three asynchronous
discipline agents under a publish–intersect–verify protocol. Write-up of the concept, the
machinery, and what every round did. August 2026.</div>

<h2>1 · The concept</h2>

<p>Classical multidisciplinary design optimization puts one optimizer in charge of everything:
a single monolithic loop owns every variable, calls every discipline's analysis, and converges to
a point. Real aircraft organizations do not work that way. Aerodynamics, structures, and weights
groups work <em>asynchronously</em>, each with its own tools, its own schedule, and its own view of
the design — and the design that ships is the one all of them can live with. This project asks
whether that organizational structure can be made computational: each discipline is an autonomous
agent, and the aircraft emerges from their <strong>consensus</strong> rather than from any single
optimizer's trajectory.</p>

<p>The first design decision, and the one everything else hangs on: agents do not exchange
<em>designs</em>, they exchange <strong>regions</strong>. A single design point is nearly useless
as a message — in a 6-dimensional space, two agents sampling independently will essentially never
propose the same point, so "cross-validate the designs both teams found" is a dead operation.
A region is a live one. Each agent publishes the entire set of designs it believes feasible,
expressed as signed constraint margins g(x) ≤ 0 over a shared design vector, and the system
intersects those sets. This is the N-dimensional generalization of the constraint diagram every
aircraft designer already draws — T/W versus W/S with takeoff, climb, and landing boundaries —
with one addition the paper version never had to face.</p>

<p>That addition is <strong>conditioning</strong>. A discipline's region is not absolute: it was
computed <em>assuming</em> values for quantities that discipline does not own. Aerodynamics
evaluated cruise L/D at an assumed empty weight. Weights checked mass closure against an assumed
structural weight. Change those assumptions and the regions move. So a non-empty intersection of
published regions means nothing by itself — every point in it might rest on assumptions that
contradict what the other disciplines actually produce there. The campaign therefore enforces two
independent tests, and a design is <strong>admissible</strong> only if it passes both: every
discipline's margins are satisfied (<em>feasible</em>), and at that same point, what each supplier
discipline actually produces matches what each consumer discipline assumed, within a declared
tolerance (<em>self-consistent</em>). The gap between those two tests is enormous — in this
campaign the naive overlap was routinely ten times larger than the admissible set — and designs
in the gap are precisely the ones that look fine in every department's review and fail in
integration.</p>

<h2>2 · The cast: agents and scaffolding</h2>

<p>Five kinds of actor appear in the visualization. Three are discipline agents; two are
scaffolding roles that own no physics at all. The separation is deliberate: the scaffolding is
what keeps a society of enthusiastic, fallible specialists from converging on a beautiful
collective mistake.</p>

<h3>The orchestrator</h3>
<p>The orchestrator runs the round loop and speaks only in derived numbers. At the top of each
round it <strong>gates</strong> incoming publications: schema-valid, referencing the same
hash-stamped design space (two publications over different spaces must never be intersected — the
engine refuses rather than producing a plausible wrong answer), and not superseded by a newer
version. It then intersects the disciplines' validity boxes to find the jointly-trusted domain,
allocates the round's evaluation budget across that domain (how it allocates is Section 5),
invokes the witness for verdicts, and closes the round by <strong>broadcasting one message per
discipline</strong>: either <em>hold</em> — your assumption survived — or <em>REPUBLISH</em> with
a specific numeric target, as in <code>W_empty → 502.9, was 330, drift 52.4%</code>. Every number
in every message is computed from supplier surrogates evaluated at actual sample points. The
orchestrator is structurally incapable of inventing a value, which is the point: in a system where
agents may be language models, the coordination channel is the one place hallucination must be
impossible by construction.</p>

<h3>The witness</h3>
<p>The witness is the system's notary, and it is <strong>deterministic code, not a model</strong>.
It evaluates every published constraint margin and every fixed-point residual at every sampled
point, classifies each point as infeasible / feasible-but-inconsistent / admissible, and — just as
important — <strong>revalidates the archive</strong> every round, evicting previously-admissible
designs that a republished region has since invalidated. A design is admissible because the
witness's arithmetic says so, never because an agent asserted it. In a full LLM deployment the
honest version of this role is an agent that <em>writes</em> checks which then run as ordinary
code: a model attesting that a design closes is not the same thing as the design closing, and the
witness exists to keep those two things from being confused.</p>

<h3>Coder and reviewer</h3>
<p>Each discipline node carries two satellite agents. The <strong>coder</strong> does the work
that actually consumes an engineer's week: writing and repairing tool wrappers and input decks
(ASWING decks are famously finicky), scripting the design-of-experiments sweep, diagnosing why a
case diverged, and fitting the surrogate that becomes the published region. The
<strong>reviewer</strong> holds the other key: before a publication ships it checks units, checks
that every constraint is normalized to the g ≤ 0 convention (so a structures margin is comparable
to an aero margin), checks that the validity box honestly reflects where the tools were actually
run rather than where the coder hopes they extrapolate, and checks that the evidence block —
tool versions, deck hashes, fit quality — is complete enough that the run could be reproduced
six weeks later. Nothing reaches the blackboard on one signature. In this demo the pair is
depicted rather than implemented; their work product is the publication JSON itself.</p>

<h3>The discipline agents</h3>
<p>Each discipline owns its tools, chooses its own sampling plan, declares where its results are
valid, and decides how much drift in its assumptions it can tolerate before its region must be
recomputed. Those declarations are contractual, machine-checked, and each one shaped the campaign:</p>

<table>
<tr><th>discipline</th><th>tools (nominal)</th><th>publishes</th><th>consumes (tolerance)</th><th>notable decisions</th></tr>
<tr><td><strong>Aerodynamics</strong></td><td>AVL + XFOIL — vortex-lattice trim sweeps, 2-D section polars</td>
<td>stall-speed margin, cruise-L/D margin; supplies CL_max</td>
<td>W_empty (±5%)</td>
<td>Clipped its validity box to t/c ≤ 0.16 and Λ ≤ 12° where XFOIL cases failed to converge;
declared the tightest tolerance in the campaign, since mid-mission mass sets the cruise CL that
L/D is evaluated at.</td></tr>
<tr><td><strong>Structures</strong></td><td>ASWING — nonlinear beam + lifting line, static aeroelastic</td>
<td>tip-deflection margin, spar-depth margin; supplies W_structure</td>
<td>CL_max (±10%), n_z_ult (frozen)</td>
<td>Excluded AR &gt; 22 after convergence failures and tightened its box accordingly; re-ran with
the FAR-23 gust case at round 3 (+42% predicted deflection); switched spar caps to carbon at
round 6 (−25% structural mass).</td></tr>
<tr><td><strong>Weights</strong></td><td>Raymer-class statistical group weights, re-fit to class</td>
<td>mass-closure margin, power-loading margin; supplies W_empty</td>
<td>W_structure (±8%)</td>
<td>Its closure constraint <em>is</em> the sizing fixed point in inequality form; its ±8%
tolerance on structural mass is what detonated the archive at round 6.</td></tr>
</table>

<p>One boundary honesty note: ASWING is not really an "aero tool" or a "structures tool" — it is
a coupled beam–aerodynamics code that spans both. Assigning it to structures here means the
structures agent implicitly owns the static-aeroelastic coupling. In a production deployment that
seam either gets a coupled aero-structures agent or it leaks.</p>

<h2>3 · The design problem and its criteria</h2>

<p>The reference mission (a placeholder, chosen to exercise the machinery rather than to matter in
itself): carry a 90 kg payload for 8 hours at 40 m/s at 3,000 m ISA, operating from an 800 m
field. The shared design vector has six variables — wing area S, aspect ratio AR, thickness ratio
t/c, quarter-chord sweep Λ, gross mass W₀, and installed power P — and the mission compiles down
to six normalized constraints, two owned by each discipline:</p>

<table>
<tr><th>constraint</th><th>owner</th><th>criterion</th><th>tends to bind at</th></tr>
<tr><td>V_stall</td><td>aero</td><td>stall ≤ 28 m/s at W₀, sea level, flaps up</td><td>high wing loading</td></tr>
<tr><td>cruise L/D</td><td>aero</td><td>L/D ≥ 12 at mid-mission mass</td><td>low AR, small span</td></tr>
<tr><td>tip deflection</td><td>structures</td><td>≤ 10% semispan at ultimate load (5.7 g)</td><td>high AR, thin sections, high W₀</td></tr>
<tr><td>spar depth</td><td>structures</td><td>root box depth ≥ 55 mm for buildability</td><td>thin high-AR wings</td></tr>
<tr><td>mass closure</td><td>weights</td><td>empty + payload + fuel ≤ W₀</td><td>small W₀ (nothing left for fuel)</td></tr>
<tr><td>power loading</td><td>weights</td><td>W₀/P ≤ 9 kg/kW for the climb requirement</td><td>heavy, underpowered corners</td></tr>
</table>

<p>Every constraint is published as a <em>margin</em> rather than a verdict, and this deserves
unpacking because it is the quiet workhorse of the whole protocol. The raw physical quantities
live in incompatible units: stall speed in m/s, tip deflection as a fraction of semispan, mass
closure in kilograms. If each discipline published "pass" or "fail," the orchestrator could
intersect the answers but would know nothing else — not which design is closest to trouble, not
which constraint to relax, not which direction to search. So instead each discipline converts its
constraint to a common dimensionless form before publishing:</p>

<p style="text-align:center"><code>g&nbsp;=&nbsp;(actual&nbsp;−&nbsp;limit)&nbsp;/&nbsp;scale</code></p>

<p>with the convention that <strong>negative is good</strong> (under the limit, constraint
satisfied) and <strong>positive is bad</strong> (over the limit, constraint violated), and where
<em>scale</em> is a reference size the discipline declares in its publication — usually the limit
itself. A concrete example with the stall constraint, whose limit is 28 m/s and whose declared
scale is also 28 m/s:</p>

<table>
<tr><th>this design's stall speed</th><th>arithmetic</th><th>published margin g</th><th>reading</th></tr>
<tr><td>25.2 m/s</td><td>(25.2 − 28) / 28</td><td>−0.10</td><td>satisfied, with 10% of the scale in hand</td></tr>
<tr><td>28.0 m/s</td><td>(28.0 − 28) / 28</td><td>0.00</td><td>exactly on the limit — <em>active</em></td></tr>
<tr><td>30.8 m/s</td><td>(30.8 − 28) / 28</td><td>+0.10</td><td>violated, and the violation is 10% deep</td></tr>
</table>

<p>The payoff is that after this conversion, <em>one number means the same thing everywhere</em>.
A structures margin of −0.10 (tip deflection at 9% of semispan against a 10% limit) and an aero
margin of −0.10 (stall at 25.2 against 28) both say: satisfied, with 10% of the way to trouble
remaining. The orchestrator can now do things that are impossible with pass/fail answers: rank
designs by how much room they have, spot that a design has +0.02 on closure but −0.45 on power
loading and know <em>which</em> discipline to negotiate with, define "active" uniformly as
g &gt; −0.05 (within 5% of the limit) when hunting corners, and steer the sampler toward the
boundary where margins approach zero — which is where minimum-mass designs live. A pass/fail
publication throws exactly this information away, which is why the contract forbids it. The
conversion happens at publication time, inside the discipline that understands its own units,
not at intersection time by an orchestrator that doesn't.</p>

<p>On top of the six margins sit the three consistency criteria, normalized the same way but
against a <em>tolerance</em> instead of a limit: at any candidate design, structures' produced
W_structure must sit within ±8% of what weights assumed, weights' produced W_empty within ±5% of
what aero assumed, and aero's produced CL_max within ±10% of what structures assumed — each
published as a residual where 1.0× means exactly at tolerance, below is consistent, above is not.
<strong>Admissible = all six margins ≤ 0 and all three residuals inside tolerance.</strong>
Everything in the campaign — the glowing region, the planforms, the archive — uses that
definition and no other.</p>

<p>One more concept the rounds keep returning to: the <strong>corner</strong>. Among admissible
designs, the one with the most constraints simultaneously within 5% of their limits. Minimum-mass
aircraft are constraint-limited by construction, so the interesting designs live on the boundary
of the admissible set, usually at a vertex where several disciplines' limits meet — not in the
comfortable middle. The corner is reported with its full six-variable identity and rendered as a
to-scale planform. It is an achieved design pulled from the admissible archive, not an
extrapolated target — though with finite sampling it is a design <em>near</em> the true vertex
rather than exactly on it; resolving the vertex precisely is a job for a gradient solver working
on the published margins, not for more random samples.</p>

<h2>4 · How to read the round snapshots</h2>

<p>Each snapshot shows the shared design space as a 2-D slice (AR horizontal, W₀ vertical) taken
<em>through the current corner design</em>, so the ringed aircraft always sits inside the region
it annotates; the slice's fixed values for S, t/c, Λ, and P are printed above the map. Colored
contours are each discipline's feasibility boundary. Gray hatching marks overlap that rests on
inconsistent premises — the trap region. The warm glowing area is the admissible region on this
slice. Dots are full 6-D samples classified on their own values (so a dot may legitimately
disagree with the slice beneath it); designs that pass both tests earn a planform, drawn to scale
from their own span, sweep, chord, and fuselage variables. Faint gold dots are the archive carried
in from earlier rounds; the dashed box is the trust region the next round will sample. Around the
map, the agent ring shows publish pulses (discipline color), witness sweeps, and verdict messages
— magenta ⟳ REPUBLISH with the new target, gray ✓ hold.</p>

<p>It bears repeating that the AR × W₀ map is <em>one cut of fifteen</em>. The admissible region
is a six-dimensional solid, and every check the witness runs is evaluated on full 6-D points; no
single plot can show the object itself. The interactive timelapse therefore carries a companion
panel below the trace: a scatterplot-matrix of <strong>all fifteen pairwise slices</strong>, every
one cut through the corner design and animating in sync with the main map. Its diagonal shows each
variable's trust-region squeeze — and reading it is the fastest way to see which dimensions the
campaign actually learned about: W₀, power, and wing area contract hard; thickness and sweep
never contract at all.</p>

<h2>5 · Making rounds learn</h2>

<p>A first version of this campaign resampled the same full domain every round, uniformly, forever
— the only memory between rounds was three conditioning scalars. The current engine carries three
mechanisms forward. A <strong>trust region</strong>: after each round the sampling box contracts
onto the bounding box of the admissible archive, padded 18%, floored at 5% of each dimension, and
clipped to the validity box it may never leave. A <strong>persistent archive</strong>: admissible
designs accumulate across rounds instead of being rediscovered — and are revalidated by the
witness every round, so a republished region evicts the designs it no longer supports. And
<strong>boundary-seeking proposals</strong>: each round's budget of 12,000 evaluations splits
50% exploring the trust region, 30% perturbing archived designs that already sit on several active
constraints (corners are what need resolving), and 20% always-uniform over the full box, so the
search can escape a trust region an event has made stale.</p>

<p>Two different performance numbers come out of this, and conflating them would be the report's
biggest lie, so they are separated everywhere: <strong>yield</strong> is the admissible share of
the points the campaign actually sampled — it measures the search, and it should climb.
<strong>Volume</strong> is the admissible share of the whole validity box, measured by a separate
uniform draw not charged to the campaign's budget — it measures the answer, and it moves only when
the disciplines' regions themselves move.</p>

<figure><img src="{TRACE}" alt="Campaign trace: yield vs volume across 12 rounds">
<figcaption><strong>The campaign in one chart.</strong> Solid gold: yield. Dashed gray: volume.
Diamonds: tool re-runs. Yield climbs from 1.8% to ~20% while volume stays flat near 1.2% — an
11× improvement in search efficiency and no improvement in the design space, which is exactly
what the two-line presentation is there to keep honest. The magenta point at round 6 is the
archive wipe.</figcaption></figure>

<h2>6 · The campaign, round by round</h2>

<h3><span class="rnum">0</span>Cold start on bad premises</h3>
<figure><img src="{R[0]}" alt="Round 0"></figure>
<p>The campaign opens deliberately mis-seeded: weights assumes a 260 kg structure (the truth is
near 190), aero assumes a 330 kg empty mass (truth near 435), structures assumes CL_max = 1.45.
The three regions overlap generously — and every pixel of that overlap is hatched. Feasible points
exist (2.75% of samples pass all six margins); admissible points do not, because nothing survives
the consistency check. This is the founding failure mode of decentralized design made visible:
<strong>every department signs off, and the airplane still doesn't exist</strong>, because each
department signed off on a different airplane. The orchestrator's first broadcast does the
correcting: ⟳ REPUBLISH aero, W_empty → 502.9 (off 52.4%); ⟳ REPUBLISH weights, W_structure →
143.6 (off 44.8%); structures holds at 7.3% drift. Note the targets overshoot the eventual truth —
they are medians over a feasible set computed under wrong premises. The damping (α = 0.6) exists
precisely because early targets are this unreliable.</p>

<h3><span class="rnum">1</span>First admissible designs</h3>
<figure><img src="{R[1]}" alt="Round 1"></figure>
<figure><img src="{G[1]}" alt="Round 1 pairwise matrix">
<figcaption><strong>The same round, all fifteen cuts.</strong> The first admissible gold appears
simultaneously across every projection — thin slivers in the AR and W₀ panels, full-width bands
wherever t/c or Λ is an axis. Every diagonal bar still reads 100%: the search knows designs exist
but has learned nothing yet about where to stop looking.</figcaption></figure>
<p>One damped correction later the conditioning sits near the fixed point (W_empty 433.7,
W_structure 190.1) and a sliver of genuinely admissible space opens: yield 1.8%, 211 designs
archived, best gross mass 633 kg. The first corner appears at AR 19.4, W₀ 641 kg with
<strong>three constraints active at once</strong> — cruise L/D, tip deflection, and mass closure,
one from each discipline. That triple-active vertex is the signature of a real sizing problem:
the minimum-mass aircraft is being squeezed simultaneously by aerodynamic efficiency, structural
stiffness, and the mass budget. All three verdicts flip to hold, with drifts under 2.1%. The
fixed point converged in one step — smooth surrogates and mild damping will do that; real tools
would take several.</p>

<h3><span class="rnum">2</span>The search sharpens 10×</h3>
<figure><img src="{R[2]}" alt="Round 2"></figure>
<figure><img src="{G[2]}" alt="Round 2 pairwise matrix">
<figcaption><strong>The design space learns its shape.</strong> One round after first contact, the
dashed trust-region boxes have snapped down in every panel that involves W₀, S, or P — while the
t/c and Λ bars sit untouched at 100%. Compare the W₀ × P panel (a compact gold blob, box hugging
it) with the Λ × t/c panel (gold everywhere, box irrelevant): the campaign is already telling you
which four of the fifteen views matter.</figcaption></figure>
<p>Now the learning machinery engages. The trust region contracts to 34.5% of the box — squeezing
W₀ into [569, 1055] and power into [84, 160] while leaving t/c and sweep untouched at full width,
an early empirical hint about which variables actually matter — and yield jumps 1.8% → 17.0%,
nearly a factor of ten, while volume barely moves (1.93%). Same design space, same budget,
dramatically better spent. The archive grows to 2,239; planforms now visibly cluster along the
diagonal where the structures boundary runs. Thirteen archived designs are evicted on
revalidation, the first sign of a subtlety that recurs all campaign: even a "hold" round nudges
the conditioning, and designs that were marginal against a consistency band die when the band
shifts a few kilograms.</p>

<h3><span class="rnum">3</span>◆ The gust case bites</h3>
<figure><img src="{R[3]}" alt="Round 3"></figure>
<figure><img src="{G[3]}" alt="Round 3 pairwise matrix">
<figcaption><strong>The gust case, seen from everywhere.</strong> The eviction of the high-AR
archive is visible as a retreat of the gold and the archive dots away from the right edge of every
AR panel (left column) — and only there. Cuts that do not involve AR barely move. A single
disciplinary re-run reshapes the region anisotropically, and the matrix shows exactly along which
axes.</figcaption></figure>
<p>First event: structures re-runs its aeroelastic sweep with the FAR-23 gust case included, and
predicted tip deflection rises 42%. The orange boundary sweeps left; the witness's revalidation
evicts <strong>1,050 archived designs — 37% of everything found so far</strong> — almost all of
them high-aspect-ratio. Volume halves (1.93% → 0.99%): that lost space is not mislaid, it is
<em>gone</em>, because the physics claim changed. The corner retreats from AR 21.6 to AR 15.3.
Meanwhile all three coupling verdicts still read hold — the fixed point is undisturbed because the
gust case changed a <em>constraint</em>, not a supplied coupling quantity. The protocol
distinguishes those two kinds of change automatically, which is exactly what you want: a
requirements shock and a coupling shock propagate through different channels.</p>

<h3><span class="rnum">4</span>Recovery and a new corner flavor</h3>
<figure><img src="{R[4]}" alt="Round 4"></figure>
<p>The 20% global sampling fraction and the re-expanded trust region absorb the shock: the archive
refills to its 4,000 cap and yield recovers to 14.6%. The corner that emerges is a different
animal — AR 17.3 but W₀ 873 kg, with <strong>power loading</strong> active in place of mass
closure. The gust case didn't just shrink the admissible set, it changed <em>which corner of it is
sharpest</em>. Steady eviction churn continues (138 this round) as the conditioning drifts by
single kilograms.</p>

<h3><span class="rnum">5</span>The lightest aircraft of the campaign</h3>
<figure><img src="{R[5]}" alt="Round 5"></figure>
<p>A quiet consolidation round, and quietly the campaign's best: <strong>best admissible gross
mass 618.7 kg</strong>, never beaten afterward. The corner sits at AR 13.7 with a thin 9.2%
section — the boundary-seeking sampler probing the thin-wing edge of the space. Worth pausing on
why this round's record survives the rest of the campaign: nothing after this round makes the
design space worse (round 6's carbon spars make it better), yet no later round finds a lighter
airplane. The reason is coming.</p>

<h3><span class="rnum">6</span>◆ The improvement that destroyed the search</h3>
<figure><img src="{R[6]}" alt="Round 6"></figure>
<figure><img src="{G[6]}" alt="Round 6 pairwise matrix">
<figcaption><strong>The wipe is a 6-D event.</strong> All fifteen panels hatched at once, zero
gold anywhere, every diagonal bar re-expanded to 100%. This frame is the strongest argument for
the matrix view: on the single AR × W₀ map the collapse could be mistaken for something local to
that plane, but here it is unmistakable — the admissible region ceased to exist in every
projection simultaneously, because the inconsistency it rests on lives in the couplings, not in
any particular pair of variables.</figcaption></figure>
<p>Structures switches the spar caps to carbon: 25% less structural mass at equal stiffness. An
unambiguous engineering improvement. The campaign's response: <strong>total collapse</strong>.
Weights is still conditioned on the old structural mass, so the supplied value now misses its
assumption by 40% against an 8% tolerance — ⟳ REPUBLISH — and the witness, revalidating the
archive against the new supply curve, evicts <strong>all 4,000 designs</strong>. Yield to zero,
trust region back to 100%, the map fully hatched again, exactly like round 0. This is the round
that justifies the entire consistency apparatus. Without it, the campaign would have kept
reporting thousands of "valid" designs whose mass budgets were built on a structure that no longer
exists. The improvement was real; every conclusion resting on the old number was not. A
coordination system has to be able to say that, and say it the same round the change lands.</p>

<h3><span class="rnum">7</span>Rebuilding in a different basin</h3>
<figure><img src="{R[7]}" alt="Round 7"></figure>
<p>The search starts over from the global fraction: 103 designs found, yield 0.9% — but notice
the best gross mass is now <strong>832 kg</strong>, far above round 5's 619. The consistency
filter is doing something subtle and double-edged here. With W_structure's assumption re-converging
toward ~138 kg, the ±8% band admits only designs whose structures actually come out near that
value — and at the new corner's geometry those are heavier aircraft. The tolerance, meant as a
staleness guard, is acting as a <em>constraint</em>: "admissible" now means <em>feasible and near
the current assumption</em>, which is narrower than plain feasible. The campaign has re-converged
into a different basin of the same design space.</p>

<h3><span class="rnum">8</span>Peak search efficiency, smallest answer</h3>
<figure><img src="{R[8]}" alt="Round 8"></figure>
<figure><img src="{G[8]}" alt="Round 8 pairwise matrix">
<figcaption><strong>Peak focus, two rounds after annihilation.</strong> The trust boxes are at
their tightest of the whole campaign (10.1% of the box overall: W₀ squeezed to 52% of its range,
P to 44%, S to 59%) and the archive dots cluster densely inside the gold in every informative
panel. Set this against the round-6 frame above — same protocol, 24,000 evaluations apart — for
the fastest possible summary of what recovery looks like.</figcaption></figure>
<p>The clearest yield-versus-volume divergence of the campaign, in one frame: the trust region
crunches to <strong>10.1% of the box</strong> — its tightest of the whole run — and yield hits
20.3%, while volume sits at 0.79%, its second-lowest. The search has never been better;
the answer has rarely been smaller. Either number alone would tell a false story. The corner
(AR 17.2, W₀ 975 kg, L/D and tip deflection active) is the identical archived design as round 7 —
with the archive persistent, a corner survives until something displaces or evicts it.</p>

<h3><span class="rnum">9</span>◆ The drag cleanup moves the binding constraint</h3>
<figure><img src="{R[9]}" alt="Round 9"></figure>
<p>Aerodynamics re-lofts the fuselage; the CD0 build-up drops 11%. This event <em>grows</em> the
space — volume rises 0.79% → 1.30% — and the search barely stumbles (68 evicted, yield 20.9%),
because a relaxation invalidates far less than a tightening. The diagnostic detail is in the
corner's active set: <strong>cruise L/D leaves it</strong>. The new corner at AR 16.4, W₀ 1067 kg
is pinned by stall speed, tip deflection, and power loading instead. Relax a constraint and it
stops binding; the binding role migrates to the next limit in line. That migration — visible in
the campaign journal as a change in which criteria the corner reports — is precisely the signal a
chief engineer uses to decide where the next unit of technology effort goes: after this round,
more drag cleanup buys nothing at the corner. Stall and stiffness do.</p>

<h3><span class="rnum">10</span>Peak volume, heavier corners</h3>
<figure><img src="{R[10]}" alt="Round 10"></figure>
<p>Volume peaks at 1.41% as the fixed point absorbs the drag improvement; the trust region eases
open to 19.4% as the archive spreads into the newly opened territory. The corner migrates to
AR 13.6, W₀ 1130 kg — two active constraints, stall and tip deflection. Note what the corner is
<em>not</em>: the best aircraft. The lightest admissible design is 783 kg. The corner is the most
<em>constrained</em> design, the sharpest vertex of the region; the lightest design is a different
point entirely, and both are reported because they answer different questions — "where is the
region hardest-pressed?" versus "what's the best aircraft found?"</p>

<h3><span class="rnum">11</span>Steady state</h3>
<figure><img src="{R[11]}" alt="Round 11"></figure>
<figure><img src="{G[11]}" alt="Round 11 pairwise matrix">
<figcaption><strong>Where the campaign ends.</strong> The settled region in all fifteen cuts:
compact gold in the W₀/P/S views, unbounded bands in thickness and sweep, trust boxes eased
slightly open after round 9's drag improvement admitted new territory. This matrix — not any
single plot — is the campaign's actual answer: a six-dimensional admissible set, characterized
well enough that four of its six dimensions are known to be the ones that matter.</figcaption></figure>
<p>The campaign ends converged: all three couplings hold with drift ≤ 0.4% (W_empty 418.9 kg,
W_structure 137.3 kg, CL_max 1.6), yield steady near 19.5%, archive at cap, eviction churn down to
low double digits. Final tally: roughly 144,000 designs evaluated across twelve rounds, 4,000
admissible designs in hand, best gross mass 788 kg, and a corner at AR 13.6 / W₀ 1133 kg pinned by
stall speed and tip deflection. The remaining ~1.2% volume with ~19.5% yield says the search is
now spending its budget about fifteen times more densely inside the answer than a uniform sampler
would — that ratio, yield over volume, is the cleanest single measure of how much the campaign
actually learned.</p>

<h2>7 · What the campaign taught</h2>

<p><strong>The naive intersection lies, an order of magnitude.</strong> Throughout the campaign
the region where all six margins pass ran ~10× larger than the region that is also
self-consistent. Any decentralized scheme that intersects regions without carrying each region's
assumptions will report that gap as designs. Round 0 and round 6 are what the gap looks like when
it collapses: 100% of the "found" designs, gone.</p>

<p><strong>An improvement in one discipline can destroy every other discipline's accumulated
work — and the system must notice the same round.</strong> Round 6's carbon spars were pure
upside for structures and wiped a 4,000-design archive, because every one of those designs
encoded the old structural mass in its closure logic. The eviction was not overhead; it was the
protocol preventing thousands of stale conclusions from surviving a change in premises. Team-scale
lesson: the cost of a mid-campaign improvement is not the re-run, it is the invalidation cascade,
and a coordination system that can't price that cascade will systematically discourage
improvements.</p>

<p><strong>Consistency tolerance is secretly a constraint, and it steers.</strong> The ±8% band
that guards against stale assumptions also pins the search to the neighborhood of the current
fixed point. After round 6 the campaign re-converged into a heavier basin (best mass 619 → 788 kg)
and never found its way back, because designs near the old optimum were "inconsistent" with the
new assumption even though they were feasible. The mechanism that protects the campaign from
incoherence also limits its exploration. The engineering fix is to schedule the tolerance —
loose while the fixed point is moving, tight as it settles — or to treat consistency as a solve
(iterate the couplings at each candidate) rather than a filter.</p>

<p><strong>The trust region discovered the active subspace by accident.</strong> Across every
round, the box contracted hard on W₀, power, and wing area and never contracted at all on
thickness or sweep. Nobody told it those variables don't bind; the archive's geometry said so.
That is the disciplines' declared <code>active_subspace</code> confirmed empirically, and it is
actionable: the next campaign should not spend a uniform share of its budget varying sweep.</p>

<p><strong>Binding constraints migrate, and the corner's active set is the tell.</strong> The
corner's criteria list changed at every event: closure gave way to power loading after the gust
case, L/D left the set after the drag cleanup, stall entered late. Watching <em>which constraints
are active at the corner</em> across a campaign is a requirements-negotiation instrument — it says,
at any moment, which requirement is actually costing mass and which technology program would
actually move the design.</p>

<h2>8 · What is real here and what is not</h2>

<p>An honesty appendix, because the visualization's production values outrun its physics. The
contract, the schema, the intersection engine, the fixed-point residuals, the trust
region/archive/eviction machinery, the yield-versus-volume instrumentation, and every number in
every orchestrator message are real computation, reproducible from the bundle
(<code>make_timelapse_data.py</code>, numpy only, fixed seed). The physics is not: no ASWING, AVL,
or XFOIL process ever ran. The "surrogates" are hand-written algebraic expressions shaped like the
methods they cite — Raymer-<em>form</em>, not Raymer — with coefficients tuned for a legible demo.
The tool names, versions, fit statistics (r², RMSE, n_train) and convergence anecdotes in the
publication files are set dressing standing where real provenance would go, and the three campaign
events are one-line coefficient edits standing where real re-runs would go. The pipeline was built
so that swapping any one discipline's expressions for a genuine tool wrapper changes nothing
upstream or downstream of that discipline — that swap (weights first, via Raymer's actual GA
group-weight equations; then a real vortex-lattice; structures last) is the honest next step, and
until it happens the aircraft in these pictures are illustrations of a protocol, not designs.</p>

</div></body></html>
"""

OUT.write_text(HTML)
print(f"wrote {OUT} ({len(HTML)//1024} KB)")
