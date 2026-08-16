"""
Acceptance test for the AVL integration. Run this FIRST, on any machine, before you
trust a single number that came out of avl_runner.

It answers two separate questions that agents habitually conflate:

  1. Does AVL run here at all, and do the guardrails fire?   (plumbing)
  2. Is it producing aerodynamics or noise?                  (physics)

Usage:
    AVL_BIN=/path/to/avl python3 aero/verify_avl.py

Exit code 0 means both. Anything else means do not proceed to a DOE.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aero.avl_runner import (  # noqa: E402
    AvlError,
    AvlLoadFailed,
    AvlNotFound,
    find_avl,
    run_case,
)
from aero.geometry import design_vector_to_avl, write_deck  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((PASS if ok else FAIL, name, detail))
    print(f"[{PASS if ok else FAIL}] {name}" + (f"  --  {detail}" if detail else ""))


def main() -> int:
    try:
        exe = find_avl()
    except AvlNotFound as e:
        print(f"[FAIL] no AVL binary: {e}")
        return 2
    print(f"AVL binary: {exe}\n")

    tmp = Path(tempfile.mkdtemp(prefix="avl_verify_"))

    # ---------------------------------------------------------------- plumbing
    # A rectangular, untwisted, symmetric-section wing -- the one case where
    # closed-form theory is trustworthy enough to test against.
    rect = tmp / "rect.avl"
    AR = 8.0
    write_deck(
        rect, S_ref=16.0, AR=AR, t_c=0.12, sweep_c4=0.0,
        taper=1.0, twist_tip_deg=0.0, camber_digits="00", n_span=32, n_chord=10,
    )

    try:
        r0 = run_case(rect, trim=("alpha", 0.0), timeout_s=60)
        check("headless run completes", True, f"CL(0deg) = {r0.CL:+.5f}")
    except AvlError as e:
        check("headless run completes", False, str(e))
        return 1

    check(
        "symmetric untwisted wing gives CL(0) ~ 0",
        abs(r0.CL) < 1e-3,
        f"CL = {r0.CL:+.2e} (should be ~0 by symmetry)",
    )

    # guardrail: a nonexistent geometry must raise, NOT return exit 0 with no data
    try:
        run_case(tmp / "does_not_exist.avl", timeout_s=30)
        check("missing geometry raises instead of silently passing", False,
              "no exception raised")
    except AvlLoadFailed:
        check("missing geometry raises instead of silently passing", True)
    except AvlError as e:
        check("missing geometry raises instead of silently passing", True, type(e).__name__)

    # guardrail: plot commands are refused before they ever reach AVL
    try:
        run_case(rect, extra_oper=["G"], timeout_s=30)
        check("plot command is refused", False, "it was allowed through")
    except ValueError:
        check("plot command is refused", True, "blocked in command builder")
    except AvlError as e:
        check("plot command is refused", False, f"reached AVL: {e}")

    # ---------------------------------------------------------------- physics
    # Lifting-line: CL_alpha = a0 / (1 + a0/(pi*AR*e)) with a0 = 2*pi per radian.
    #
    # The tolerance here is asymmetric on purpose. LLT assumes an infinitely thin
    # chord and a 2-D lift slope of exactly 2*pi, so it OVERPREDICTS; a vortex lattice
    # with real chordwise discretisation always sits a few percent below it. Measured
    # here: 4.57 vs 5.01 /rad, i.e. 8.6% low, which is the textbook VLM offset and NOT
    # a bug. What would be a bug is AVL coming out ABOVE LLT, or more than ~15% below.
    r5 = run_case(rect, trim=("alpha", 5.0), timeout_s=60)
    cla_avl = (r5.CL - r0.CL) / math.radians(5.0)
    a0 = 2.0 * math.pi
    cla_llt = a0 / (1.0 + a0 / (math.pi * AR * 0.98))
    ratio = cla_avl / cla_llt
    check(
        "CL_alpha sits just below lifting-line theory, as a VLM should",
        0.85 <= ratio <= 1.00,
        f"AVL {cla_avl:.4f} /rad vs LLT {cla_llt:.4f} /rad (ratio {ratio:.3f})",
    )

    check(
        "span efficiency is physical",
        0.90 < r5.e <= 1.02,
        f"e = {r5.e:.4f} (rectangular wing: expect ~0.95-0.99)",
    )

    # Induced drag must follow CL^2 / (pi AR e). Test the scaling, not the value.
    cdi_theory = r5.CL**2 / (math.pi * AR * r5.e)
    err_cdi = abs(r5.CD_induced - cdi_theory) / cdi_theory
    check(
        "CDi follows CL^2/(pi AR e)",
        err_cdi < 0.05,
        f"AVL {r5.CD_induced:.5f} vs theory {cdi_theory:.5f} ({err_cdi*100:.1f}% off)",
    )

    # Higher AR must give lower induced drag at the same CL. If this fails the deck
    # generator is wiring AR into the geometry wrong.
    hi = tmp / "hi_ar.avl"
    write_deck(hi, S_ref=16.0, AR=16.0, t_c=0.12, sweep_c4=0.0,
               taper=1.0, twist_tip_deg=0.0, camber_digits="00", n_span=32, n_chord=10)
    r_lo = run_case(rect, trim=("CL", 0.6), timeout_s=60)
    r_hi = run_case(hi, trim=("CL", 0.6), timeout_s=60)
    check(
        "doubling AR cuts induced drag at fixed CL",
        r_hi.CD_induced < 0.6 * r_lo.CD_induced,
        f"AR8 CDi={r_lo.CD_induced:.5f} -> AR16 CDi={r_hi.CD_induced:.5f}",
    )

    # CL trim actually hits its target
    check(
        "CL trim converges to the requested CL",
        abs(r_lo.CL - 0.6) < 1e-3,
        f"asked 0.600, got {r_lo.CL:.5f} at alpha = {r_lo.alpha_deg:.3f} deg",
    )

    # Lattice convergence, refined as far as this build tolerates.
    fine = tmp / "fine.avl"
    write_deck(fine, S_ref=16.0, AR=AR, t_c=0.12, sweep_c4=0.0,
               taper=1.0, twist_tip_deg=0.0, camber_digits="00", n_span=32, n_chord=16)
    r_fine = run_case(fine, trim=("alpha", 5.0), timeout_s=120)
    drift = abs(r_fine.CL - r5.CL) / r5.CL
    check(
        "lattice is converged (CL moves <1% when refined)",
        drift < 0.01,
        f"10x32 CL={r5.CL:.5f} -> 16x32 CL={r_fine.CL:.5f} ({drift*100:.2f}%)",
    )

    # Probe the dense-cosine-lattice case and find out which way THIS build behaves.
    #
    # A single-precision AVL (3.32, and the stock builds generally) returns CLtot = NaN
    # here with exit code 0 and no warning. Later builds do not. Both are acceptable --
    # what is NOT acceptable is producing a NaN and letting it through, so that is the
    # only condition this fails on. Written as a probe rather than an assertion because
    # a test pinned to the presence of a bug fails when the bug gets fixed, which is
    # exactly backwards.
    from aero.avl_runner import AvlNumericalFailure  # noqa: E402

    bad = tmp / "dense_cosine.avl"
    text, _ = design_vector_to_avl(
        16.0, AR, 0.12, 0.0, taper=1.0, twist_tip_deg=0.0,
        camber_digits="00", n_span=32, n_chord=8,
    )
    bad.write_text(text.replace("8  1.0  32  -2.0", "8  1.0  64  -2.0"))
    try:
        r_bad = run_case(bad, trim=("alpha", 5.0), timeout_s=120)
        sane = math.isfinite(r_bad.CL) and abs(r_bad.CL - r5.CL) / r5.CL < 0.02
        check(
            "dense cosine lattice: NaN caught, or build is immune",
            sane,
            f"this build returns a finite CL={r_bad.CL:.5f} at 64 cosine stations "
            f"-- it does NOT have the single-precision NaN defect, so the n_span<=32 "
            f"cap is conservative here (pass allow_dense_cosine=True to lift it)",
        )
    except AvlNumericalFailure:
        check("dense cosine lattice: NaN caught, or build is immune", True,
              "this build DOES produce NaN at 64 cosine stations, and it was caught")
    except AvlError as e:
        check("dense cosine lattice: NaN caught, or build is immune", False,
              f"unexpected error: {type(e).__name__}: {e}")

    # Strip forces: the table that feeds the NeuralFoil coupling. Both checks exist
    # because a column-shift between AVL versions is silent -- the file parses, the
    # rows look plausible, and the numbers are from the wrong columns.
    r_strip = run_case(rect, trim=("CL", 0.5), want_strips=True, timeout_s=60)
    area_frac = sum(s.area for s in r_strip.strips) / r_strip.Sref if r_strip.strips else 0.0
    check(
        "strip areas sum to the reference area",
        0.97 < area_frac < 1.01,
        f"{len(r_strip.strips)} strips, sum(area)/Sref = {area_frac:.4f} "
        f"(a value near 0.5 means only one wing half was parsed)",
    )
    if r_strip.strips:
        w_cl = (sum(s.cl * s.area for s in r_strip.strips)
                / sum(s.area for s in r_strip.strips))
    else:
        w_cl = 0.0
    check(
        "strip cl column is the lift column, not a zero column",
        abs(w_cl - r_strip.CL) < 0.02,
        f"area-weighted strip cl = {w_cl:.4f} vs wing CL = {r_strip.CL:.4f} "
        f"(~0.0 means `cl` was read off `cdv` -- an FS column shift)",
    )

    # And the generator refuses to build that deck in the first place.
    try:
        design_vector_to_avl(16.0, AR, 0.12, 0.0, n_span=64)
        check("deck generator refuses a NaN-prone lattice", False, "it built it anyway")
    except ValueError:
        check("deck generator refuses a NaN-prone lattice", True)

    print()
    n_fail = sum(1 for s, _, _ in results if s == FAIL)
    print(f"{len(results) - n_fail}/{len(results)} checks passed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
