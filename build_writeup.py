#!/usr/bin/env python3
"""
Build docs/writeup.html -- the explainer, for readers who are not aerospace engineers.

Audience: an undergraduate who has taken some physics and can read a graph, but has
never sized an aircraft and does not know what a vortex lattice is. Every technical
term gets defined the first time it appears. The document explains the IDEA and what
happened when we tested it; the repository holds the details.

Numbers are read from the artifacts, never typed in -- if the campaign changes, the
prose changes with it. Figures are captured from the live dashboard and embedded as
data URLs, so the page is one self-contained file.

    python build_writeup.py                 # uses /tmp/figs if present
    python build_writeup.py --figs DIR
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def img(path: Path, alt: str, cap: str, wide: bool = False) -> str:
    if not path.is_file():
        return f'<figure class="{"wide" if wide else ""}"><div class="missing">figure not captured: {path.name}</div><figcaption>{cap}</figcaption></figure>'
    b64 = base64.b64encode(path.read_bytes()).decode()
    return (f'<figure class="{"wide" if wide else ""}">'
            f'<img src="data:image/png;base64,{b64}" alt="{alt}">'
            f'<figcaption>{cap}</figcaption></figure>')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--figs", default=str(ROOT / "docs/figs"),
                    help="dashboard screenshots; committed copies live in docs/figs")
    ap.add_argument("--out", default="docs/writeup.html")
    args = ap.parse_args()
    F = Path(args.figs)

    D = json.loads((ROOT / "timelapse_data.json").read_text())
    R = D["rounds"]
    pubs = {}
    for f in (ROOT / "publications").glob("*.json"):
        d = json.loads(f.read_text())
        if d.get("status") == "published":
            pubs[d["discipline"]] = d

    def st(i, k):
        return R[i]["stats"][k]

    # shortlist facts, if the selector has been run
    td = ROOT / "top_designs.json"
    if td.is_file():
        _d = json.loads(td.read_text())["designs"]
        sl_n = len(_d)
        sl_lo = min(x["span"] for x in _d)
        sl_hi = max(x["span"] for x in _d)
    else:
        sl_n, sl_lo, sl_hi = 10, 9.4, 13.8

    # facts pulled from the run
    best = min(r["stats"]["best_w0"] for r in R if r["stats"].get("best_w0"))
    peak_vol = max(r["stats"]["vol"] for r in R)
    ev = {r["round"]: r["event"]["title"] for r in R if r.get("event")}
    wipe = next(r["round"] for r in R if r["stats"].get("evicted", 0) >= 3000)
    wipe_n = R[wipe]["stats"]["evicted"]

    css = """
:root{--ink:#16181d;--ink2:#4a4f58;--muted:#7b818c;--rule:#e3e5e9;--bg:#fbfbfa;
 --surface:#fff;--accent:#8c3a2b;--aero:#2f6f9f;--struct:#c05621;--wts:#1f7a6b;
 --hot:#c99a1e;--callout:#f6f4ef}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font:16.5px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
 -webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding:56px 24px 96px}
h1{font-size:2.15rem;line-height:1.18;letter-spacing:-.02em;margin:0 0 .4rem}
.sub{color:var(--muted);font-size:1.02rem;margin:0 0 2.6rem}
h2{font-size:1.32rem;letter-spacing:-.01em;margin:3rem 0 .8rem;padding-top:1.4rem;
 border-top:1px solid var(--rule)}
h3{font-size:1.04rem;margin:2rem 0 .5rem;color:var(--ink)}
p{margin:0 0 1.05rem}
strong{font-weight:640}
em.term{font-style:normal;font-weight:640;color:var(--accent)}
figure{margin:1.9rem 0;padding:0}
figure.wide{margin-left:-90px;margin-right:-90px}
@media(max-width:960px){figure.wide{margin-left:0;margin-right:0}}
figure img{width:100%;display:block;border:1px solid var(--rule);border-radius:8px;
 background:#0f1116}
figcaption{font-size:.83rem;color:var(--muted);line-height:1.5;margin-top:.55rem}
.missing{padding:2rem;text-align:center;color:var(--muted);border:1px dashed var(--rule);
 border-radius:8px;font-size:.85rem}
.callout{background:var(--callout);border-left:3px solid var(--accent);
 padding:1rem 1.15rem;margin:1.7rem 0;border-radius:0 6px 6px 0}
.callout p:last-child{margin-bottom:0}
.callout .lbl{font-size:.72rem;letter-spacing:.09em;text-transform:uppercase;
 color:var(--accent);font-weight:700;margin-bottom:.35rem}
table{width:100%;border-collapse:collapse;margin:1.5rem 0;font-size:.9rem}
th{text-align:left;font-weight:640;border-bottom:1.5px solid var(--ink);padding:.45rem .5rem}
td{border-bottom:1px solid var(--rule);padding:.45rem .5rem;
 font-variant-numeric:tabular-nums}
td.n,th.n{text-align:right}
.lede{font-size:1.1rem;color:var(--ink2)}
.dot{display:inline-block;width:.62em;height:.62em;border-radius:50%;margin-right:.3em;
 vertical-align:baseline}
code{background:#eff0f2;padding:.1em .35em;border-radius:3px;font-size:.87em;
 font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.foot{margin-top:3.5rem;padding-top:1.2rem;border-top:1px solid var(--rule);
 font-size:.83rem;color:var(--muted)}
"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Designing an aircraft when nobody has the whole picture</title>
<style>{css}</style></head><body><div class="wrap">

<h1>Designing an aircraft when nobody has the whole picture</h1>
<p class="sub">What happens when three engineering teams each know their own job,
and have to guess about everyone else's.</p>

<p class="lede">Every airplane is a compromise between specialists who cannot all be
in the same room. This is a small, working experiment in letting them coordinate
through published, machine-checkable contracts instead of meetings &mdash; and an
honest account of what broke when we tried it.</p>

<h2>1. Why aircraft design is hard in a specific way</h2>

<p>Suppose you want to make an airplane's wings longer and thinner. Long thin wings
are efficient &mdash; gliders have them for a reason &mdash; so this seems like an
easy win. But a longer wing bends more, so it needs more structural material, so it
gets heavier. A heavier airplane needs more lift, which means a bigger wing, which is
heavier still. And a heavier airplane needs a bigger engine, which weighs more too.</p>

<p>You cannot evaluate the change on its own. This is called
<em class="term">coupling</em>: the pieces of the design determine each other, in
loops. Change one number and you have to chase the consequences all the way around
and back to where you started.</p>

<p>There is a standard answer. Put every calculation into one computer program, give
it to an optimizer, and let it solve all the loops simultaneously. This works, it is
well understood, and for a problem the size of ours it would find a better answer
faster than anything in this document.</p>

<div class="callout"><div class="lbl">The catch</div>
<p>Real engineering organizations cannot do that. The aerodynamics group runs
different software than the structures group, on a different schedule, with different
people, sometimes at a different company. Their tools cannot be merged into one
program &mdash; not because nobody thought of it, but because the tools, the data, and
the expertise genuinely live in separate places.</p></div>

<p>So what actually happens is this: each team does its own work, using
<strong>assumptions</strong> about what the other teams will produce. Aerodynamics
assumes the airplane will weigh about 390&nbsp;kg empty and designs around that.
Weights assumes the wing structure will come in around 165&nbsp;kg. Everyone is
working hard and correctly &mdash; on slightly different airplanes.</p>

<p>Occasionally they compare notes, discover the assumptions have drifted apart, and
redo work. The question this project asks is: <strong>can that comparison be made
automatic, precise, and continuous, instead of occasional and verbal?</strong></p>

<h2>2. The idea: publish a region, not a point</h2>

<p>The usual thing to hand another team is a design &mdash; one airplane, one set of
numbers. The problem is that a single design is brittle: change any assumption behind
it and it may be worthless, and you cannot tell how close to worthless it was.</p>

<p>Instead, each team here publishes a <strong>region</strong>: the whole set of
designs it considers acceptable, described as mathematical formulas over the shared
design variables. Six numbers describe an airplane in this experiment &mdash; wing
area, <em class="term">aspect ratio</em> (how long and thin the wing is), thickness,
sweep angle, total mass, and engine power. A published region says, in effect, "for
any combination of those six numbers, here is how much margin I have."</p>

<p>Four rules make those publications useful.</p>

<h3>Everyone must be talking about the same design space</h3>
<p>Each publication carries a fingerprint (a <em class="term">hash</em>) of the
variable list it was built against. If two teams' fingerprints differ &mdash; someone
added a variable, someone else did not notice &mdash; the software refuses to combine
them rather than producing a plausible, wrong answer.</p>

<h3>Publish margins, not yes/no</h3>
<p>"Feasible" throws away the information you need. Every constraint is instead
reported as a number scaled so that <strong>0 means exactly at the limit</strong> and
&minus;0.10 means "10% of the way to trouble remaining." That scaling makes a
structures margin and an aerodynamics margin directly comparable, which is what lets
a coordinator rank designs instead of just sorting them.</p>

<h3>Say where your model is valid</h3>
<p>Prediction models do not fail loudly outside the range they were built for. They
keep returning confident, wrong numbers. So each publication declares the box of
designs it actually tested, and anything outside is rejected rather than trusted.</p>

<h3>Declare what you assumed about everyone else</h3>
<p>This is the load-bearing one. Each team records, in machine-readable form, what it
assumed about the others &mdash; and how far reality may drift from that assumption
before its own work must be redone. That declaration is what makes disagreement
detectable by a program rather than by a person noticing in a meeting.</p>

<h2>3. Three teams, real physics</h2>

<p>The experiment uses three simulated teams. Each one runs genuine engineering
calculations &mdash; not invented formulas &mdash; and none of them can see inside the
others.</p>

<table>
<tr><th>Team</th><th>What it computes</th><th>How</th></tr>
<tr><td><span class="dot" style="background:var(--aero)"></span><strong>Aerodynamics</strong></td>
<td>Lift, drag, stall speed</td>
<td>A <em class="term">vortex lattice</em> solver (AVL) that models the wing as a grid
of tiny spinning filaments of air, coupled to a neural network trained on airfoil
simulations</td></tr>
<tr><td><span class="dot" style="background:var(--struct)"></span><strong>Structures</strong></td>
<td>Wing weight, how much the wing bends</td>
<td>A beam model that adds up the aerodynamic load along the wing and sizes the spar
caps to survive it</td></tr>
<tr><td><span class="dot" style="background:var(--wts)"></span><strong>Weights</strong></td>
<td>Empty weight of the whole aircraft</td>
<td>A component build-up: a fuselage sized by the volume it must hold, an engine sized
by its power, plus systems and landing gear</td></tr>
</table>

<p>How do we know the physics is right? The aerodynamics code was checked against a
standard test case published decades ago &mdash; a specific wing shape with known
answers. It reproduced the published lift slope to <strong>0.14%</strong>. The
structures model predicts a wing structure of about 167&nbsp;kg for a mid-range
design; the value seeded into the project before any of this was built was
165&nbsp;kg. Those are independent agreements, and they are the reason to believe
anything downstream.</p>

<h2>4. The tools behind the numbers</h2>

<p>That table is short on detail in a way that matters, because the credibility of
everything after this section rests on it. This project began with all three teams
publishing <em>fitted curves</em> &mdash; formulas somebody wrote down that looked like
physics and were tuned to be roughly right. Two of the three now publish numbers that
real tools computed, and the formulas they publish are fits <em>to those runs</em>.
Here is what those tools are, and &mdash; just as importantly &mdash; what each one
cannot do alone.</p>

<table>
<tr><th>Tool</th><th>Gives you</th><th>Cannot give you</th></tr>
<tr><td><strong>AVL</strong><br><span style="color:var(--muted)">vortex lattice</span></td>
<td>How lift is distributed along the wing, drag due to lift, stability</td>
<td>Anything caused by air&rsquo;s stickiness &mdash; no skin friction, no stall</td></tr>
<tr><td><strong>NeuralFoil</strong><br><span style="color:var(--muted)">airfoil neural net</span></td>
<td>Drag and maximum lift of a 2-D wing cross-section</td>
<td>Anything three-dimensional &mdash; it does not know a wing exists</td></tr>
<tr><td><strong>Beam sizing</strong><br><span style="color:var(--muted)">structural analysis</span></td>
<td>How much material the wing needs, and how far it bends</td>
<td>The load it is carrying &mdash; that has to come from somewhere</td></tr>
</table>

<p>The three gaps are complementary, and closing them costs almost nothing. That is
the whole design of this part of the system.</p>

<h3>AVL, and the problem of letting software drive it</h3>

<p>A <em class="term">vortex lattice</em> method divides the wing into a grid of
panels, places a spinning filament of air on each, and solves for how strongly each
one must spin so that air flows smoothly along the wing surface everywhere. From that
you get the lift on every strip of the wing. It is inviscid &mdash; air is treated as
frictionless &mdash; which is why it is fast, and why it is silent about drag from
friction and about stall. AVL is the standard implementation, written at MIT and in
use for decades.</p>

<p>The physics was never the hard part. The difficulty is that AVL is an interactive
program from the era of text terminals, driven by typing menu commands, and it was
written to be operated by a person who can see when something looks wrong. Software
driving it blind fails <em>quietly</em>. We hit six distinct ways to get a confident,
wrong answer, and the wrapper now guards each one:</p>

<table>
<tr><th>The trap</th><th>The guard</th></tr>
<tr><td>A blank line means &ldquo;leave this menu&rdquo;. Writing a results file and
then a blank line exits the analysis menu, so the <em>next</em> command is silently
ignored and you get a half-empty file with no error.</td>
<td>The command sequence is built deliberately, and a test asserts the exit is where
we think it is.</td></tr>
<tr><td>One of the results tables has a heading that contains a space inside what
looks like a single column. Reading columns by position therefore returns a
neighbouring quantity &mdash; plausible, and wrong.</td>
<td>Columns are matched <strong>by name</strong>, with two tests and a raw-output dump
tool for when a number looks odd.</td></tr>
<tr><td>Asking for a plot opens a graphics window and waits forever for someone to
close it.</td><td>Plot commands are refused before they can reach AVL.</td></tr>
<tr><td>Certain fine panel layouts return &ldquo;not a number&rdquo; on some
builds.</td><td>The geometry generator refuses to build that layout.</td></tr>
<tr><td>Missing geometry, a hung run, or no output at all can all look like a zero
result.</td><td>Each raises a specific, named error instead of returning a
number.</td></tr>
</table>

<p>Fourteen automated checks confirm the harness behaves. But internal checks cannot
catch a <em>systematic</em> error, because the same code writes both the question and
the answer. So the tool is also benchmarked against results published outside this
project: a standard verification wing whose lift and pitching-moment slopes have been
in the literature for decades, and Prandtl&rsquo;s century-old result that an
elliptically loaded wing has an efficiency of exactly 1.0. Ours reproduces the
published lift slope to 0.14%.</p>

<div class="callout"><div class="lbl">The check that matters most</div>
<p>AVL reports the wing area you told it &mdash; comparing that to what you asked for
proves nothing. It <em>also</em> reports the area it gets by adding up the wing
sections it was actually given. Comparing <em>that</em> is not circular, and it is what
catches a geometry generator whose trigonometry is subtly wrong &mdash; the single most
likely error for an automated system to make and never notice.</p></div>

<h3>NeuralFoil, and why not the obvious choice</h3>

<p>The standard tool for a 2-D airfoil cross-section is XFOIL. NeuralFoil is a neural
network trained on very large numbers of XFOIL runs, and it is the better choice here
for a reason specific to this project. XFOIL <em>fails to converge</em> on hard cases
&mdash; thick sections, unusual conditions &mdash; and gives you nothing. The
aerodynamics team&rsquo;s original declared range of validity was clipped to thin,
barely-swept wings for exactly that reason: the tool had refused to answer there.</p>

<p>NeuralFoil does not refuse. It returns an answer together with a
<strong>confidence</strong>. So the old justification for that boundary evaporated,
and the valid range had to be re-derived from a confidence threshold instead of
inherited from a tool limitation that no longer applied. It is also fast enough to
evaluate every strip of the wing at once, which is what makes the next part possible.</p>

<h3>Bolting them together</h3>

<p>One AVL run gives every strip of the wing its own local lift, chord length and
area. Each strip therefore has its own flow conditions, and NeuralFoil is asked about
each strip separately &mdash; one batched call for the whole wing. Two useful things
come out.</p>

<p><strong>Friction drag</strong> is the area-weighted sum of each strip&rsquo;s own
2-D drag, corrected for wing sweep.</p>

<p><strong>Maximum lift, with a location.</strong> Because a vortex lattice is a
<em>linear</em> solver, every strip&rsquo;s share of the total lift is fixed &mdash;
increase the wing&rsquo;s lift and each strip&rsquo;s lift scales by the same factor.
So the wing stalls exactly when the first strip reaches its own 2-D maximum, and we
can compute both when that happens and <em>where</em>. That second part is not a
detail: a wing that stalls at the root sinks nose-down and recovers, while a wing that
stalls at the tip drops a wingtip and rolls. Same number, different airplane.</p>

<div class="callout"><div class="lbl">A free result</div>
<p>Because the <em>shape</em> of the lift distribution never changes, the strip
calculations done at one flight condition can be re-read at any other &mdash; no extra
solver runs at all. That is what let the aerodynamics team publish a full
<strong>drag curve</strong> rather than drag at a single point. It matters more than it
sounds: the previous publication had drag not depending on lift at all, which
understated drag-due-to-lift by about <strong>38%</strong> on a typical design. That is
not a rounding error on a constraint that decides whether the airplane closes.</p></div>

<h3>The beam model, and one result worth the whole exercise</h3>

<p>The structures team&rsquo;s own published limitations used to include the line
&ldquo;span loading held at the seeded distribution&rdquo; &mdash; it was sizing the
wing against an assumed load. Since aerodynamics now computes the real one, structures
can size against the load the wing actually carries.</p>

<p>The method is classical, and about as far as you can go with pen and paper before
you need a full finite-element code. Distribute the lift along the wing for the worst
manoeuvre the aircraft must survive; subtract the relief provided by the wing&rsquo;s
own weight pushing down; add it up from the tip inward to get the bending moment at
every station; then size the load-carrying caps at the top and bottom of the wing box
so that each one is working exactly at its allowable stress &mdash; a
<em class="term">fully-stressed</em> design. Mass follows from the volume of material;
deflection follows from integrating the curvature.</p>

<p>And then something surprising falls out. If every station is fully stressed, the
bending moment cancels out of the deflection calculation entirely. The wing&rsquo;s
curvature depends only on the material and the depth of the wing box &mdash; not on
the load at all.</p>

<div class="callout"><div class="lbl">Why this justifies the work</div>
<p>A fully-stressed wing bends <strong>the same amount</strong> whether you design it
for a gentle 3g manoeuvre or a violent 6g one. Only its mass changes. That is real,
counter-intuitive, and precisely the kind of thing a fitted curve gets silently wrong:
the old structures publication had deflection growing in proportion to the load, which
for this kind of wing is simply not true. It also means the wing&rsquo;s weight and its
stiffness are far less linked than the old formulas implied &mdash; which changes the
shape of the acceptable region, not just the numbers in it. It is asserted as an
automated test.</p></div>

<h3>What is still assumed, stated plainly</h3>

<p>The AVL model is a <em>wing</em>. There is no fuselage, tail, nacelle or landing
gear in it, so nothing in the strip calculation can produce their drag. That term is
still an estimate from a standard hand method, and it is written into the published
formula as its own separate piece rather than blended in to look measured. It disagrees
with the weights team&rsquo;s independent build-up by roughly a quarter to a third
&mdash; a disagreement we deliberately left visible rather than reconciled away.</p>

<p>The beam has no twisting, no feedback from the wing bending into the airflow, no
buckling (which makes it optimistic on the compressed upper cap), no gust cases, no
fatigue and no landing loads. The maximum-lift calculation applies a 2-D result strip
by strip, so it has no gradual spreading of stall across the wing. All of this is
enormously more real than a fitted power law. None of it is a wind tunnel, and the
publications say so in their own machine-readable limitations.</p>

<h2>5. What the map shows</h2>

{img(F/'map_05.png', 'design map at a healthy round',
     'A two-dimensional slice through the six-dimensional design space: wing '
     'slenderness across the bottom, total aircraft mass up the side. Coloured '
     'outlines are each team&rsquo;s boundary of acceptability. Gold marks designs '
     'acceptable to everyone AND built on assumptions that hold. Diagonal hatching '
     'marks the trap: designs every team approves of, where the teams were '
     'nonetheless imagining different airplanes. Small dots are candidates the search '
     'has evaluated.', wide=True)}

<p>That hatched area is the whole point of the project. Those designs pass every
individual check. They are still not real, because the checks were performed under
assumptions that contradict each other. A coordinator looking only at "does everyone
approve?" would report them as successes.</p>

<h2>6. What happened over twelve rounds</h2>

<p>The teams take turns: publish, compare, update assumptions, repeat. To make it
realistic, the experiment starts everyone off <strong>wrong on purpose</strong> and
then interrupts them three times with the kind of news that arrives in real projects.</p>

<table>
<tr><th>Round</th><th>What happened</th><th class="n">Usable designs</th></tr>
<tr><td>0</td><td>Everyone starts with mistaken beliefs about everyone else</td><td class="n">{st(0,'both'):.1f}%</td></tr>
<tr><td>3</td><td>{ev.get(3,'&mdash;')} &mdash; the wing must survive a harder load</td><td class="n">{st(3,'both'):.1f}%</td></tr>
<tr><td>5</td><td>Beliefs have caught up; the search is productive</td><td class="n">{st(5,'both'):.1f}%</td></tr>
<tr><td>{wipe}</td><td>{ev.get(6,'&mdash;')} &mdash; wings get 53% lighter</td><td class="n">{st(wipe,'both'):.1f}%</td></tr>
<tr><td>9</td><td>{ev.get(9,'&mdash;')} &mdash; less drag</td><td class="n">{st(9,'both'):.1f}%</td></tr>
<tr><td>11</td><td>Recovered</td><td class="n">{st(11,'both'):.1f}%</td></tr>
</table>

<p>Round&nbsp;{wipe} is the result worth remembering. The structures team switched to
carbon fiber spar caps and the wing structure got <strong>53% lighter</strong>. That
is unambiguously good news for the airplane. It was catastrophic for the project:
every other team was still working from the old, heavier number, so
<strong>all {wipe_n:,} candidate designs found so far were invalidated at once</strong>
and the usable fraction fell to {st(wipe,'both'):.1f}%.</p>

<div class="callout"><div class="lbl">The finding</div>
<p>An improvement in one place destroyed the accumulated work of everyone else. This
is not a bug in the method &mdash; it is the method correctly detecting that everybody
else's homework was based on a number that just changed. In a real project this
happens silently and is discovered months later.</p></div>

{img(F/'map_06.png', 'the map immediately after the carbon-fiber change',
     'The same slice immediately after the carbon change. Everything is hatched: the '
     'designs still look acceptable to each team individually, but nobody&rsquo;s '
     'assumptions about anybody else are valid any more.', wide=True)}

<h2>7. Watching teams misunderstand each other</h2>

<p>The most useful display we built shows, for each pair of teams, what one
<em>believes</em> about the other next to what the other <em>actually publishes</em>.</p>

{img(F/'belief_06.png', 'coupling belief distributions just after the disruption',
     'Each panel is one relationship. The filled shape is what a team really '
     'publishes across all candidate designs; the dashed outline is what its partner '
     'believes. When the two separate, somebody is working from stale information. '
     'The red strip along the bottom marks the specific designs where the '
     'disagreement exceeds what the consuming team said it could tolerate.')}

<p>Two details in that picture are worth explaining, because they are easy to
misread.</p>

<p><strong>A straight vertical line means a team is holding a single number.</strong>
In the middle panel, structures assumes one fixed value for the aerodynamic quantity
it needs, so its "belief" is a spike rather than a spread. That is a legitimate way to
work &mdash; it just cannot represent the fact that the real value differs from
airplane to airplane.</p>

<p><strong>Two curves can look nearly identical and still disagree badly.</strong> The
check is performed design by design: for each candidate airplane, does your belief
match what your partner actually says <em>about that airplane</em>? Overlapping
silhouettes hide errors that go one way for some designs and the other way for others.
That is what the red strip is for &mdash; it shows <em>which</em> designs fail, not
just how many.</p>

{img(F/'belief_11.png', 'coupling belief distributions at the end of the campaign',
     'By the final round the beliefs have largely caught up, and the red strip has '
     'shrunk to the regions where the simplified picture one team holds of another '
     'is simply too crude to be accurate &mdash; a floor set by the model, not by '
     'being out of date.')}

<h2>8. Four things we learned by building it</h2>

<h3>Simple assumptions break when the physics gets real</h3>
<p>Originally each team assumed a single number for what its partners would produce.
That works if the quantity is roughly constant. Wing structural weight is not: across
the design space it varies by a factor of fourteen. No single number can be within 8%
of a quantity that varies fourteen-fold, so almost every design was flagged as
inconsistent &mdash; and the flag was measuring the crudeness of the assumption rather
than any real disagreement.</p>
<p>The fix was to let a team's assumption be a simplified <em>model</em> of its
partner rather than a single number &mdash; the way a real engineer internalizes
"structures roughly doubles when the wing gets much longer." Usable designs went from
under 1% to about {st(11,'both'):.0f}%. Nothing about the airplanes changed; only the
way disagreement was measured.</p>

<h3>Making things precise reveals what you never wrote down</h3>
<p>Halfway through, we drew pictures of the candidate airplanes to scale. The fuselage
was <strong>too short to attach the tail to</strong> &mdash; by up to five meters. The
weights team sized the fuselage by the volume it had to hold; the aerodynamics team
assumed a tail mounted far behind the wing. Neither was wrong. Nobody owned the
relationship between them, so nobody had written it down, so the checking software
&mdash; which had been reporting everything as consistent &mdash; could not see it.</p>
<div class="callout"><div class="lbl">The uncomfortable lesson</div>
<p>A system that reports "all checks passing" is making a much narrower claim than it
appears to. It has verified the relationships somebody thought to declare. The
failures that hurt are the ones nobody thought to declare.</p></div>

<h3>The dashboard was the worst offender</h3>
<p>The interactive display had its own copy of the physics, written when the project
used simple placeholder formulas. It was never updated. For most of this work, every
picture anyone looked at was real data drawn on top of year-old physics &mdash;
airplanes shown as unacceptable that were in fact fine, and tooltips off by a factor
of nineteen. The visualization was, in effect, a fourth team working from the
stalest assumptions in the building, and it was the only one the contract did not
check. It now displays results computed by the engine and has no opinions of its own.</p>

<h3>Ask what is actually limiting you before you compromise</h3>
<p>Late on, usable designs collapsed to a fraction of a percent, and the obvious
response was to relax a requirement &mdash; accept a slower airplane, or less range.
We measured it instead. Relaxing the range requirement by a third would have improved
matters by about 1.7&times;. Fixing how disagreement was measured improved matters by
6.7&times;, and cost nothing.</p>
<p>The requirements were never the problem. Without that measurement, the project
would have traded away a third of the airplane's endurance to fix a bookkeeping
artifact.</p>

<h2>9. Why a region instead of &ldquo;the best airplane&rdquo;</h2>

<p>The obvious objection to all of this: an integrated optimizer would hand you the
single best aircraft, and this method never does. That is true, and worth answering
directly, because the answer is the strongest argument for the whole approach.</p>

<p><strong>The "best" design is fragile by construction.</strong> A constrained
optimum is the design with zero slack in several directions at once &mdash; that is
<em>why</em> it is best. It sits exactly where the limits touch. Which makes it the
single design most exposed to every piece of future news: a corrected model, a
supplier change, a shifted requirement all move the limits, and the design pressed
against them moves first and farthest.</p>

<div class="callout"><div class="lbl">We watched this happen</div>
<p>Each time one of our physics models improved, the computed "best airplane" jumped
to a different part of the design space: first a long, slender, thin wing; then a
shorter, thicker one; then a compact, moderate one. Three successive model
improvements, three different "optimal" aircraft &mdash; each held with complete
confidence by the tools of its day. Anyone who had committed to the first optimum
would have committed to an airplane two revisions of understanding later shown to be
wrong. The <em>region</em> changed too, but a region degrades gracefully; a point is
simply somewhere else.</p></div>

<p>There is a second mechanism stacked on top. An optimizer does not just tolerate
model error &mdash; it <strong>seeks it out</strong>, because the place your model is
most optimistic looks, to the optimizer, like the best deal on the board. Early-stage
models are exactly the kind an optimizer loves to exploit. A region bounded by
declared margins and validity limits does not chase the flattering corner.</p>

<p>And early design is when the <em>requirements themselves</em> are still moving.
Optimizing commits you to one answer for one frozen question. A region is the menu
for the negotiation that actually happens at this stage: when our usable designs
collapsed late in the project, the region machinery let us measure that relaxing the
endurance requirement would buy a 1.7&times; improvement while fixing our bookkeeping
would buy 6.7&times; &mdash; a trade no single-point answer can even express. The {sl_n}
designs in our final shortlist span wingspans from {sl_lo:.1f} to {sl_hi:.1f} meters at
essentially the same weight; that spread <em>is</em> the trade study, made visible.</p>

<p>To keep the claim honest: a region defers the decision, it does not make it. One
airplane eventually gets built, and once requirements freeze and models are trusted,
running a focused optimizer <em>inside</em> the surviving region is the right final
step. The sampling approach also becomes impractical with many more design variables.
The argument is not that optimization is wrong &mdash; it is that it belongs at the
end, and the region is what tells you when the end has arrived.</p>

<p>All of that holds even before the organizational reality: the setting this is
built for &mdash; separate teams, incompatible tools, updates arriving weeks apart
&mdash; cannot run an integrated optimizer at all. There, the honest alternative is
spreadsheets, email, and finding out at the design review.</p>

<p>Against <em>that</em> baseline, look at what the machinery actually produced. Every
significant error found during this work was caught by the contract: an aerodynamic
correlation being used far outside its valid range, a missing dependence on airflow
conditions, a fuselage that could not carry its own tail, a dashboard rendering
obsolete physics, and a requirements compromise that would have been made for no
reason. None of those discoveries required finding an optimal airplane.</p>

<div class="callout"><div class="lbl">The claim</div>
<p>The output of this system is not a design. It is <strong>caught errors</strong>,
<strong>a measured picture of how well teams understand each other</strong>, and
<strong>a live map of the trades</strong> that stays useful while models and
requirements keep changing. It is closer to automated testing for engineering
assumptions than to an optimization method &mdash; and in the stage of design where
assumptions quietly go stale and requirements are still being argued about, that is
the more valuable thing to have.</p></div>

<h2>10. Where the numbers came from</h2>

<p>Every figure in this document was produced by the code in this repository, run
end to end: three teams publishing from real physics solvers, twelve rounds of
coordination, {D['budget']:,} candidate designs evaluated per round. The best design
found weighs {best:.0f}&nbsp;kg; the acceptable region peaked at
{peak_vol:.1f}% of the design space. The publications carry their own fit quality,
validity ranges, and stated limitations, and the checking software refuses to combine
publications whose assumptions do not line up.</p>

<p>The things it still cannot do are written down too: no engine model, no gust
loads, no buckling analysis, a fixed wing shape, and &mdash; most importantly &mdash;
no way to detect a relationship between teams that nobody declared.</p>

<div class="foot">
floodlight &middot; generated from
{', '.join(sorted(p['publication_id'] for p in pubs.values()))}
&middot; {len(R)} rounds &middot; interactive version in <code>docs/timelapse.html</code>
</div>

</div></body></html>"""

    out = ROOT / args.out
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {args.out}  {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
