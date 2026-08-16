# Running AVL from an agent

AVL is Mark Drela's vortex-lattice code: interactive, Fortran, prompt-driven, written
for a human at a terminal in 1988 and essentially unchanged since. It has no API, no
exit codes worth reading, and no concept of a caller that isn't watching the screen.

Everything below was measured, not assumed. Transcripts are at the bottom.

**The one-line version: AVL's exit code is a lie, and so is a forces file that exists.
Success is "the file parsed, every number is finite, and the alpha in it is the alpha
you asked for". Nothing less.**

---

## Use the wrapper

```python
from aero.avl_runner import run_case, AvlError

r = run_case("wing.avl", trim=("CL", 0.6), timeout_s=60)
print(r.CL, r.CD_induced, r.e, r.LD)
```

Do not build stdin strings by hand. Every guardrail in this document is already
implemented in `avl_runner.py`, and the failure modes it prevents are not ones you
will notice going wrong.

If you are about to write `subprocess.run(["avl"], input=...)` yourself: don't. Read
the transcripts first, then decide.

---

## Getting a binary you can actually run

`avl352.exe` is a Windows executable. It runs from `cmd.exe` or PowerShell on the
Windows host. It does **not** run:

- in a Linux container (cloud sessions, CI),
- in the Cowork "on your computer" local VM, which is Linux with no Wine.

So the binary you downloaded and the binary an agent needs are usually not the same
file. Options, in order of how little they hurt:

| Where the agent runs | What it needs |
|---|---|
| Windows host directly | `avl352.exe`, `AVL_BIN=C:\path\to\avl352.exe` |
| Linux container / CI | a Linux build, `AVL_BIN=/opt/avl/bin/avl` |
| Cowork local VM | a Linux build placed in a mounted folder |

Building a Linux AVL, verified working:

```bash
git clone https://github.com/RobotLocomotion/avl        # AVL 3.32 + plotlib + cmake
cd avl && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
make -j avl
# needs: gfortran, libX11-dev (plotlib links X11 even though we never plot)
```

`AVL_BIN` is read by `find_avl()`. `run_case` refuses a non-executable file with a
message that names this problem specifically, rather than a confusing OSError.

---

## The five failure modes

### 1. Stream desync — the dangerous one

If the output file already exists, AVL interrupts your command stream:

```
File exists.  Append/Overwrite/Cancel  (A/O/C)?
```

and **consumes the next line of your script as the answer**. Every command after
that point is shifted by one. AVL keeps running. It executes a different case than
you asked for and writes a file that looks completely normal.

There is no error, no warning, and nothing in the output file that identifies the
case as wrong unless you happen to check the alpha.

**Rule: one fresh scratch directory per run, always.** Never run two cases in the
same directory, never reuse an output filename, never `cd` into the user's project
folder and run there. `run_case` mkdtemps for every call and copies inputs in.

### 2. Exit code 0 is not success

A geometry file that fails to load:

```
 ** Open error on file: nope.avl.avl
 ** File not processed. Current geometry may be corrupted.
```

AVL **continues to the next prompt and exits 0**. Note "Current geometry may be
corrupted" — if a previous geometry was loaded, AVL will happily run cases against
whatever is left in memory.

**Rule: never branch on `returncode`.** Success is a parsed forces file whose values
are finite and whose alpha matches the request.

### 3. Plot commands abort a headless run

Any of `G`, `T`, hardcopy, or `MOVIE` opens an X11 window. With no display:

```
 Cannot open display...aborting
```

exit 1, mid-stream, with whatever you asked for afterwards silently undone.

**Rule: no plot commands, ever, from an agent.** `_build_commands` raises on them
before AVL is even launched, and `DISPLAY` is stripped from the child environment so
that a stray one fails instantly and loudly instead of blocking on an X connection.

If you need a picture, write the strip forces (`FS`) to a file and plot them in
matplotlib.

### 4. EOF at a prompt is a crash

Running out of stdin while AVL is waiting:

```
At line 145 of file userio.f (unit = 5, file = 'stdin')
Fortran runtime error: End of file
```

exit 2, plus a backtrace that looks alarming and means nothing.

**Rule: always terminate the command stream so AVL exits on its own.** From inside
`OPER` that is a blank line and then `QUIT` — see failure 6 for why it is not two
`QUIT`s.

### 6. A blank line is not a no-op — it is "go up one menu"

`QUIT` is a **top-level** command. It is not recognised inside `OPER`. The only way
out of `OPER` is a **blank line**, which means "up one level" throughout AVL's menus.

That makes stray blank lines actively dangerous, because the symptom appears far from
the cause. This looks reasonable and is broken:

```
FT totals.ft
                <- "harmless" trailing blank... actually exits OPER
FS strips.fs    <- now at top level: "FS command not recognized"
QUIT
```

No strip file is ever written. AVL exits 0. The only trace is one line of
`FS command not recognized` buried in a log nobody reads.

The correct ending, verified exit 0 with zero unrecognised commands:

```
LOAD wing.avl
OPER
A C 0.6
X
FT totals.ft
FS strips.fs
                <- leave OPER
QUIT            <- leave AVL
```

This one is worth dwelling on because it *appears* to work. An earlier version of
this harness ended with `FT`, blank, `QUIT`, `QUIT` and exited cleanly every time —
the blank left `OPER`, the first `QUIT` exited AVL, the second went nowhere. Totals
were correct, so nothing looked wrong. It only surfaced when a second output command
was added after `FT` and silently produced no file.

### 5. Silent NaN — the one that poisons surrogates

This is the worst of the five, because the output file looks right.

On a **single-precision** AVL build — which is the standard build, including the
stock Windows binaries — a dense cosine-spaced spanwise lattice makes the near-field
force integration produce NaN, while the Trefftz-plane block immediately below it
stays perfectly plausible:

```
  CLtot =       NaN
  CDtot =       NaN
  CDvis =   0.00000     CDind =       NaN
  CLff  =   0.39967     CDff  =   0.00654    | Trefftz
  CYff  =   0.00000         e =    0.9720    | Plane
```

Exit code 0. No warning. Nothing in the log. A parser that reads `CLff` instead of
`CLtot` would never notice, and a surrogate fit would swallow it whole.

**This one is build-specific, and it has been fixed upstream.** Measured on a
rectangular AR-8 wing with cosine spanwise spacing (`Sspace = -2.0`):

| spanwise stations / semispan | AVL 3.32 (single precision, Linux) | AVL 3.52 (Windows) |
|---|---|---|
| 16, 24, 32 | 0.39913 ✓ | 0.39913 ✓ |
| 48, 64 | **NaN** | 0.39913 ✓ |

So 3.52 is immune and the `n_span ≤ 32` cap is conservative there — pass
`allow_dense_cosine=True` to lift it. `verify_avl.py` reports which behaviour your
build has rather than assuming; it fails only if a NaN is produced *and* let through.

Do not delete the guard because your build is fine. The point of the harness is that
it runs on whatever binary is present, and the 3.32-era builds are still what most
Linux/CI environments get from source.

It is driven by **station count with cosine clustering**, not by total vortex count:
20×20 (800 vortices) is clean while 8×48 (768 vortices) is NaN. AVL's compiled limits
(`NVMAX=6000`, `NSMAX=400`) are nowhere near being hit. The cause is cosine spacing
packing the root and tip strips together until they collapse in single precision.

Changing the distribution fixes it — same 48 stations:

| `Sspace` | CLtot |
|---|---|
| −2.0 cosine | **NaN** |
| −1.0 sine | 0.39913 ✓ |
| 0.0 equal | 0.40171 ✓ |
| 1.0 | 0.39911 ✓ |

**Rules:**
- Keep `n_span ≤ 32` per semispan with cosine spacing. `geometry.py` raises if you
  ask for more.
- The parser rejects any non-finite field with `AvlNumericalFailure` before the
  values can reach a fit.
- **Verify this threshold on your own binary.** A different AVL version or a
  double-precision build will have a different one. `verify_avl.py` includes the
  regression test.

---

## Before you trust any number

Two suites, and they answer different questions. Run both.

```bash
AVL_BIN=/path/to/avl python3 aero/verify_avl.py    # is the harness sane?
AVL_BIN=/path/to/avl python3 aero/benchmark.py     # is it right?
```

On Windows, against the binary you downloaded:

```powershell
cd C:\Users\jason\OneDrive\Documents\floodlight
$env:AVL_BIN = "C:\Users\jason\Downloads\avl352.exe"
python aero\benchmark.py
python aero\verify_avl.py
```

### benchmark.py -- external ground truth

`verify_avl.py` cannot catch a systematic error, because everything it compares was
produced by the code under test. `benchmark.py` compares against numbers from
outside this repository. Measured on AVL 3.32:

| Case | Reference | AVL | Error |
|---|---|---|---|
| Warren 12, CL_α | 2.743 /rad | 2.7468 | 0.14% |
| Warren 12, Cm_α | −3.10 /rad | −3.0948 | 0.17% |
| Elliptic planform, e | 1.0 exactly (Prandtl) | 1.0000 | 0.00% |
| Generator area vs AVL's own integration | requested S_ref | — | <0.03% |

**Warren 12** is the standard VLM verification planform: taper 1/3, 53.54° LE sweep,
AR = 2√2, flat plate. Reference values from Mason, *Applied Computational
Aerodynamics*, ch. 6. It comes with a **nonstandard reference convention** that is
part of the test: reference chord = average chord = 1.0 (not the MAC), and the moment
reference point at the **wing apex** (not the quarter-MAC). Report the same run about
the quarter-MAC instead and Cm_α reads −0.16 rather than −3.09 — a 95% error from a
reference-point choice, with no warning from anything. If an agent ever has to match
a published Cm, this is the failure it will hit.

**The generator area check is the non-circular one.** `geometry.py` writes Sref into
the deck header and AVL echoes it back, so comparing those proves nothing. AVL also
reports the area it gets by integrating the SECTION cards (the `FN` output), and that
is what gets compared. It is the check that catches sweep/chord/span trigonometry
that is quietly wrong.

Note the trap inside that check: the `FN` file contains the same area column in two
different tables. A regex over the whole file returns exactly 2× the true area, which
reads as a 100% geometry error and is really a parsing bug. Parse the first table
only. (This one was hit while writing the check.)

### verify_avl.py -- internal sanity

12 checks: 4 plumbing, 6 physics, 2 on the NaN guard. Exit 0 or do not run a DOE.
The physics ones are worth stating because they are the ones that catch a deck
generator that is silently wrong:

- symmetric untwisted wing gives CL(0°) = 0
- CL_α lands **just below** lifting-line theory (measured 4.574 vs 5.006 /rad, ratio
  0.914). A VLM is always a few percent under LLT, because LLT assumes a 2-D lift
  slope of exactly 2π and zero chordwise extent. AVL coming out *above* LLT, or more
  than ~15% below, means the deck is wrong.
- CDi matches CL²/(πARe) to 0.5%
- doubling AR cuts induced drag at fixed CL (catches AR wired into the deck wrong)
- CL trim hits its target exactly
- CL moves <0.01% when the lattice is refined

---

## What AVL can and cannot tell you

A vortex lattice solves potential flow over a thin lifting surface. It is genuinely
good at induced drag, span loading, span efficiency, and stability derivatives.

It knows nothing about viscosity. Therefore:

| Quantity | Source |
|---|---|
| CL, CDi, e, Cm, stability derivatives | AVL |
| CD0 / profile drag | flat-plate build-up or XFOIL polars, **not AVL** |
| CL_max, stall | **not AVL** — it will happily report CL = 4.0 |
| compressibility | not modelled here (Mach = 0) |

The `CDvis` field in the output is just the constant `CDp` you put in the deck header
being echoed back. It is not a calculation. If you publish an L/D that depends on it,
you are publishing your own assumption with AVL's name on it.

That gap is what `section.py` closes, using NeuralFoil for the 2-D half.

---

## The viscous half: NeuralFoil (`section.py`)

`pip install neuralfoil` (it pulls AeroSandbox). Then:

```python
r = run_case(deck, trim=("CL", 0.6), want_strips=True)
sec = analyze_strips(r.strips, t_c=0.12, sweep_c4_deg=5.0, S_ref=16.0,
                     CL_wing=r.CL, V=40.0, span=geom.span)
print(sec.report())
```

AVL gives every strip its local cl, chord and area. Each strip gets its own Reynolds
number and its own NeuralFoil polar (one batched call, deduplicated over the mirrored
half). From that: `cd` by inverting cl(α) on the **pre-stall branch**, and the section
`cl_max` as the peak.

CL_max falls out of AVL being linear: strip loading scales with wing loading, so
`cl_j = k_j · CL`, and the wing stalls when the first strip hits its own maximum —
`CL_max = min_j(cl_max_j / k_j)`. That also names the station, which matters: a
root-first stall is benign, a tip-first stall drops a wing, and no margin in the
floodlight contract would catch the difference.

Cost, measured: **~86 ms per design** end to end with a warm airfoil cache (AVL ~50 ms,
NeuralFoil the rest). A 300-point DOE is under half a minute.

Three things to hold onto:

- **`CD_profile` is the wing only.** No fuselage, tail, nacelle or interference — they
  are not in the deck, so they are not in the number. A parasite build-up still has to
  supply them. Publishing this as the aircraft CD0 would understate drag.
- **Invert on the pre-stall branch.** Past the lift peak the curve turns over, and a
  naive `interp` on cl will happily hand back a post-stall α for a modest cl, with a
  drag to match. `section.py` slices at the peak before interpolating.
- **`analysis_confidence` replaces "it didn't converge".** Aero's r1 validity box was
  clipped to t/c ≤ 0.16 and Λ ≤ 12° *because XFOIL cases failed to converge there*.
  NeuralFoil never fails to converge — it returns a low confidence instead. So that
  clipping rationale no longer exists and the box has to be re-derived from a
  confidence threshold. `SectionResult` reports min and mean confidence so that call
  can be made on evidence rather than inherited.

### What it changes

`aerodynamics-r1.json` declares `tool: "AVL + XFOIL"` while carrying hand-written
analytic expressions. The CL_max one is `1.35 + 2.2·t_c − 0.012·Λ` — **monotonically
increasing in thickness**. Measured against the coupled method at AR 12:

| t/c | Λ | coupled | r1 expression | error |
|---|---|---|---|---|
| 0.08 | 0° | 1.550 | 1.526 | +1.6% |
| 0.12 | 0° | 1.643 | 1.614 | +1.8% |
| 0.16 | 0° | 1.631 | 1.702 | **−4.2%** |
| 0.18 | 0° | 1.616 | 1.746 | **−7.4%** |

The real CL_max **peaks near t/c ≈ 0.12 and then falls** as thicker sections separate
earlier. A straight line cannot represent a peak, so the r1 expression is decent in the
middle and increasingly optimistic toward the thick end of the declared design space —
and optimistic on CL_max is the unsafe direction, because it *understates* V_stall.

It matters twice over: CL_max sets aero's own `AER.V_STALL` constraint **and** is the
coupling variable structures consumes with a ±10% tolerance. A 7.4% bias at t/c = 0.18
eats most of that tolerance before any real disagreement has happened.

---

## Command reference (only what an agent should use)

Top level:

| Command | Effect |
|---|---|
| `LOAD file.avl` | read geometry. Check the log for "File not processed". |
| `MASS file.mass` | read mass file |
| `MSET` then `0` | apply mass file to all run cases |
| `OPER` | enter the run-case menu |
| `QUIT` | exit |

Inside `OPER`:

| Command | Effect |
|---|---|
| `A A 5.0` | constrain alpha, set it to 5° |
| `A C 0.6` | constrain alpha, solve for CL = 0.6 |
| `X` | execute the run case |
| `FT file` | total forces to file |
| `FS file` | strip forces to file (per-strip cl, chord, area) |
| `ST file` | stability derivatives to file |
| *(blank line)* | back to top level — **not** `QUIT`, which is top-level only |

Never: `G`, `T`, `MOVIE`, hardcopy, or anything interactive.

Unrecognised commands are harmless — AVL prints `BOGU command not recognized` and
re-prompts. It is the *recognised* ones arriving in the wrong order that hurt.

---

## Transcripts

```
$ printf 'load allegro.avl\noper\na a 5.0\nx\nft forces.txt\n\nquit\nquit\n' | avl
exit=0, forces.txt written, CLtot = 0.91017          # the good case

$ # forces.txt now exists; identical command list re-run:
203: File exists.  Append/Overwrite/Cancel  (A/O/C)?  C
exit=2                                                # stream desync

$ printf 'load nope.avl\nquit\n' | avl
 ** Open error on file: nope.avl.avl
 ** File not processed. Current geometry may be corrupted.
exit=0                                                # exit 0 on failure

$ printf 'load allegro.avl\noper\ng\n\nquit\nquit\n' | avl     # DISPLAY unset
 Cannot open display...aborting
exit=1

$ printf 'load allegro.avl\noper\na a 3\nx\n' | avl            # no QUIT
Fortran runtime error: End of file
exit=2
```

Verified against AVL 3.32 (RobotLocomotion mirror, gfortran 13.2, single precision,
Linux x86-64). Jason's `avl352.exe` is a later 3.5x build: the command interface is
unchanged, but **re-run `verify_avl.py` against it** — particularly the NaN threshold,
which is build-dependent.
