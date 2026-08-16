"""
Strip-wise viscous coupling: AVL supplies the span loading, NeuralFoil supplies the
2-D section polars. Together they give the two things a vortex lattice cannot produce
on its own -- wing profile drag, and a CL_max with an actual basis.

This is the method `aerodynamics-r1.json` already claims in its evidence block
("VLM trim sweep, 2D section polars swept to 3D"). Until now that claim was carried
by hand-written analytic expressions. This module makes it true.

HOW IT WORKS
------------
One AVL run gives every strip's local cl, chord and area. For each strip:

    local Reynolds number      Re_j = rho * V * c_j / mu
    simple-sweep normalisation cl_n = cl_j / cos^2(sweep),  Re_n = Re_j * cos(sweep)

NeuralFoil is then evaluated on an alpha sweep at each strip's own Reynolds number --
one batched call for the whole wing -- giving each strip its own polar. From that
polar we take:

    cd_j   by inverting cl(alpha) at the strip's operating cl, on the pre-stall branch
    clmax_j  the peak of that strip's lift curve

Profile drag is the area-weighted sum, with the simple-sweep cos^3 factor:

    CD_profile = sum_j  cd_j * cos^3(sweep) * A_j / Sref

CL_max comes out of the linearity of the vortex lattice. AVL is a linear solver, so
each strip's loading scales with the wing's:  cl_j = k_j * CL, with k_j read off the
single run. The wing stalls when the FIRST strip reaches its own section maximum:

    CL_max = min_j ( clmax_j / k_j )

which also tells you WHERE it stalls -- a root-first stall is benign, a tip-first
stall drops a wing. That station is reported, not just the number.

WHAT THIS DOES AND DOES NOT COVER
---------------------------------
CD_profile here is the WING only. Fuselage, tail, nacelle, gear and interference are
not in the AVL deck and therefore not in this number; a parasite build-up still has to
supply them, and whatever consumes this must say so. Reporting a strip-integrated wing
CDp as though it were the aircraft CD0 would be a straight understatement.

CL_max is a 2-D section maximum applied strip-by-strip. It has no 3-D stall
progression, no separation hysteresis, and no Reynolds-number scaling beyond what
NeuralFoil's own training covers. It is a far better number than a fitted line, and it
is still not a wind tunnel.

ON `analysis_confidence`
------------------------
NeuralFoil returns a confidence with every evaluation. This matters for floodlight
specifically: aero's r1 validity box was clipped to t/c <= 0.16 and sweep <= 12 deg
because "XFOIL cases failed to converge" there. NeuralFoil does not fail to converge --
it returns a low confidence instead. So that clipping rationale no longer exists, and
the box has to be re-derived from a confidence threshold rather than inherited. This
module reports the minimum confidence over all strips so that decision can be made on
evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

__all__ = ["StripAero", "SectionResult", "analyze_strips", "naca_from_tc"]

# Sea-level ISA. Passed explicitly everywhere it is used; these are only defaults.
RHO_SL = 1.225        # kg/m^3
MU_SL = 1.789e-5      # Pa.s


@lru_cache(maxsize=64)
def _airfoil(name: str):
    """Build (and cache) the AeroSandbox Airfoil object.

    Constructing it re-derives the coordinate set and its Kulfan parameterisation every
    time, which in a DOE is pure repeated work: t/c is quantised to whole percent, so a
    few hundred designs share a handful of distinct aerofoils.
    """
    import aerosandbox as asb

    return asb.Airfoil(name)


def naca_from_tc(t_c: float, camber_digits: str = "24") -> str:
    """NACA 4-digit name for a thickness ratio. Must match aero/geometry.py, which
    quantises t/c to whole percent -- keep the two in step or the section polar will
    describe a different aerofoil than the one in the AVL deck."""
    tt = max(1, min(99, int(round(t_c * 100.0))))
    return f"naca{camber_digits}{tt:02d}"


@dataclass
class StripAero:
    """Per-strip viscous result."""

    j: int
    y: float
    chord: float
    area: float
    cl: float             # operating section cl, from AVL
    Re: float
    cd: float             # section profile drag at that cl
    cl_max: float         # section maximum, from the NeuralFoil polar
    confidence: float
    k: float              # cl_j / CL_wing -- the loading ratio

    @property
    def stall_margin(self) -> float:
        """cl / cl_max at the current operating point. 1.0 = stalled."""
        return self.cl / self.cl_max if self.cl_max > 0 else float("inf")


@dataclass
class SectionResult:
    CD_profile: float           # wing profile drag, referred to Sref
    CL_max: float               # wing CL at first-strip stall
    stall_station_y: float      # spanwise position of that strip, m
    stall_station_eta: float    # ... as a fraction of semispan
    confidence_min: float
    confidence_mean: float
    airfoil: str
    strips: list[StripAero] = field(repr=False, default_factory=list)
    # wing CL -> strip-integrated profile drag at that CL. Empty unless cl_sweep given.
    CD_profile_sweep: dict[float, float] = field(default_factory=dict)
    # fraction of wing area whose strips were past their section stall, per swept CL
    stalled_area_frac: dict[float, float] = field(repr=False, default_factory=dict)

    def report(self) -> str:
        eta = self.stall_station_eta
        where = "root" if eta < 0.35 else ("mid-span" if eta < 0.7 else "TIP")
        lines = [
            f"airfoil            {self.airfoil}",
            f"CD_profile (wing)  {self.CD_profile:.5f}   (wing only -- no fuselage/tail)",
            f"CL_max             {self.CL_max:.3f}",
            f"first stall at     eta = {eta:.2f} ({where}), y = {self.stall_station_y:.2f} m",
            f"NeuralFoil conf.   min {self.confidence_min:.2f}, mean {self.confidence_mean:.2f}",
        ]
        if where == "TIP":
            lines.append(
                "  WARNING: tip-first stall. Aerodynamically this is a roll-off at the "
                "stall, and no margin in the contract will catch it."
            )
        return "\n".join(lines)


def analyze_strips(
    strips,
    *,
    t_c: float,
    sweep_c4_deg: float,
    S_ref: float,
    CL_wing: float,
    V: float,
    span: float,
    rho: float = RHO_SL,
    mu: float = MU_SL,
    camber_digits: str = "24",
    model_size: str = "medium",
    alpha_grid: tuple[float, float, float] = (-8.0, 22.0, 0.25),
    min_area_frac: float = 0.002,
    cl_sweep: tuple[float, ...] = (),
) -> SectionResult:
    """Evaluate NeuralFoil on every AVL strip and integrate.

    Args:
        strips: the `strips` list from an `avl_runner.run_case(..., want_strips=True)`.
        CL_wing: the wing CL that AVL was trimmed to (used to form k_j = cl_j / CL).
        V: true airspeed, m/s. Sets every strip's Reynolds number.
        span: full span, m -- only used to report the stall station as eta.
        min_area_frac: strips smaller than this fraction of Sref are excluded from the
            CL_max search. The outermost cosine-spaced strip carries almost no area and
            almost no load; including it makes CL_max a lottery on the tip cell.
        cl_sweep: extra wing CLs at which to integrate profile drag, giving the
            LIFT-DEPENDENT part of it. This is nearly free: AVL is linear, so the span
            loading SHAPE does not change with CL -- every strip's cl just scales by
            k_j. So the strip polars already computed can be re-interpolated at
            k_j * CL for any other CL, with no further AVL runs and no further network
            evaluations. Without this, profile drag is a single number taken at one CL
            and used as if it were CL-independent, which makes any L/D built on it
            optimistic.

    Returns:
        SectionResult
    """
    import neuralfoil as nf  # imported here so the AVL half of the package has no NN dep

    if not strips:
        raise ValueError("no strips -- call run_case(..., want_strips=True)")
    if CL_wing <= 0:
        raise ValueError("CL_max extrapolation needs a positive trimmed CL_wing")

    airfoil = naca_from_tc(t_c, camber_digits)
    cos_sweep = math.cos(math.radians(sweep_c4_deg))

    chord = np.array([s.chord for s in strips])
    area = np.array([s.area for s in strips])
    cl_avl = np.array([s.cl for s in strips])

    # Simple sweep theory: the section behaves as if it saw only the velocity component
    # normal to the quarter-chord line.
    Re = rho * V * chord / mu
    Re_n = Re * cos_sweep
    cl_n = cl_avl / cos_sweep**2

    a0, a1, da = alpha_grid
    alphas = np.arange(a0, a1 + 0.5 * da, da)
    n_a, n_s = alphas.size, len(strips)

    # A symmetric wing hands back both halves, and mirrored strips have identical
    # chord and therefore identical Reynolds number -- so half the polars would be
    # computed twice. Solve on the unique Reynolds numbers and index back. Exactly
    # halves the NN cost on any YDUPLICATE wing, which is all of them here.
    Re_u, inverse = np.unique(np.round(Re_n, 3), return_inverse=True)
    n_u = Re_u.size

    aero = nf.get_aero_from_airfoil(
        _airfoil(airfoil),
        alpha=np.tile(alphas, n_u),
        Re=np.repeat(Re_u, n_a),
        model_size=model_size,
    )
    CL = np.asarray(aero["CL"]).reshape(n_u, n_a)[inverse]
    CD = np.asarray(aero["CD"]).reshape(n_u, n_a)[inverse]
    CONF = np.asarray(aero["analysis_confidence"]).reshape(n_u, n_a)[inverse]

    out: list[StripAero] = []
    polars: list[tuple[np.ndarray, np.ndarray]] = []   # (cl, cd) on the pre-stall branch
    for i, s in enumerate(strips):
        cl_curve, cd_curve = CL[i], CD[i]
        i_peak = int(np.argmax(cl_curve))
        cl_max_n = float(cl_curve[i_peak])

        # Invert on the pre-stall branch only. Past the peak the curve turns over and a
        # naive interpolation would happily return a post-stall alpha for a modest cl.
        pre = slice(0, i_peak + 1)
        cl_pre, cd_pre = cl_curve[pre], cd_curve[pre]
        order = np.argsort(cl_pre)
        cl_sorted, cd_sorted = cl_pre[order], cd_pre[order]
        polars.append((cl_sorted, cd_sorted))
        target = float(np.clip(cl_n[i], cl_sorted[0], cl_sorted[-1]))
        cd_n = float(np.interp(target, cl_sorted, cd_sorted))

        out.append(
            StripAero(
                j=s.j, y=s.y, chord=s.chord, area=s.area,
                cl=float(cl_n[i]), Re=float(Re_n[i]),
                cd=cd_n,
                cl_max=cl_max_n,
                confidence=float(np.mean(CONF[i][pre])),
                k=float(cl_n[i] / CL_wing),
            )
        )

    # Profile drag, area-weighted, with the simple-sweep cos^3 factor.
    CD_profile = float(
        sum(st.cd * cos_sweep**3 * st.area for st in out) / S_ref
    )

    # First strip to reach its own section maximum as the wing is loaded up.
    big = [st for st in out if st.area / S_ref >= min_area_frac and st.k > 1e-6]
    if not big:
        # This message used to say only "no strips large enough", which points at the
        # area filter when the cause is almost always the OTHER half of the test: every
        # strip came back with zero lift because the FS table was parsed by position
        # against an AVL build whose columns differ, putting `cl` on the `cdv` column
        # (0.0000 on every row). Report both halves so the real cause is visible.
        area_frac = sum(st.area for st in out) / S_ref
        raise ValueError(
            f"no strips usable for CL_max: {len(out)} strips, "
            f"sum(area)/S_ref = {area_frac:.3f} (expect ~1.0), "
            f"max |cl| = {max((abs(st.cl) for st in out), default=0.0):.4f} "
            f"(expect ~{CL_wing:.2f}), "
            f"max area/S_ref = {max((st.area / S_ref for st in out), default=0.0):.4f} "
            f"(threshold {min_area_frac}). "
            f"If cl is ~0 or the area fraction is far from 1.0, the AVL strip-force "
            f"table was parsed wrong -- check the FS column names against your AVL "
            f"version."
        )
    cl_max_candidates = [(st.cl_max / st.k, st) for st in big]
    CL_max, stall_strip = min(cl_max_candidates, key=lambda t: t[0])

    # Lift-dependent profile drag, from the polars already in hand.
    sweep_cd: dict[float, float] = {}
    sweep_stalled: dict[float, float] = {}
    total_area = float(sum(st.area for st in out))
    for CL_t in cl_sweep:
        acc = 0.0
        stalled = 0.0
        for st, (cls, cds) in zip(out, polars):
            want = st.k * CL_t
            if want > cls[-1]:
                # Past this strip's section maximum. Clamping keeps the integral finite,
                # but the number is no longer a drag prediction -- it is a lower bound
                # on a stalled section. Report how much area is in that state so a
                # consumer can refuse the point rather than quietly believe it.
                stalled += st.area
            want = float(np.clip(want, cls[0], cls[-1]))
            acc += float(np.interp(want, cls, cds)) * cos_sweep**3 * st.area
        sweep_cd[float(CL_t)] = acc / S_ref
        sweep_stalled[float(CL_t)] = stalled / total_area if total_area > 0 else 0.0

    conf = np.array([st.confidence for st in out])
    return SectionResult(
        CD_profile_sweep=sweep_cd,
        stalled_area_frac=sweep_stalled,
        CD_profile=CD_profile,
        CL_max=float(CL_max),
        stall_station_y=abs(stall_strip.y),
        stall_station_eta=abs(stall_strip.y) / (0.5 * span),
        confidence_min=float(conf.min()),
        confidence_mean=float(conf.mean()),
        airfoil=airfoil,
        strips=out,
    )
