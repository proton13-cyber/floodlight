"""
Fully-stressed wing-box beam sizing, driven by the AVL spanload.

structures-r1 says, in its own `known_limitations`: "Spanload held at the seeded CL_max
distribution". That is the assumption this module removes. The aero agent already
computes the real spanwise loading -- AVL hands back per-strip cl, chord and area -- so
structures can size the box against the load the wing actually carries instead of an
assumed distribution.

THE MODEL
---------
Classical, and about as far as you can go analytically before you need a real FE code.
For the ultimate manoeuvre case (n_z_ult * W0):

    1. Distribute lift over the strips in proportion to (cl_j * area_j), scaled so the
       total equals n_z_ult * W0 * g. Optionally subtract inertia relief from the wing's
       own mass, which is a real effect and always reduces root bending.
    2. Integrate outboard-in for shear V(y) and bending moment M(y).
    3. Box depth h(y) = box_depth_frac * t_c * c(y).
    4. Size the spar caps fully-stressed: A_cap(y) = M(y) / (sigma_allow * h(y)),
       subject to a minimum gauge.
    5. Second moment of the cap pair: I = 2 * A_cap * (h/2)^2 = A_cap * h^2 / 2.
    6. Deflection by integrating curvature twice.
    7. Mass = cap volume * density, times a non-optimum factor for webs, skins, ribs,
       joints and fasteners, plus a carry-through and fuselage-frame allowance.

THE PROPERTY WORTH KNOWING
--------------------------
For a fully-stressed beam the curvature collapses to

    kappa(y) = M/(E*I) = M / (E * (M/(sigma*h)) * h^2/2) = 2*sigma / (E*h(y))

The bending moment cancels. Deflection depends only on the allowable stress, the
modulus, and the DEPTH DISTRIBUTION -- not on the load. So a fully-stressed wing's tip
deflection does not change when you increase the load factor; only its mass does. That
is a genuine and slightly counter-intuitive result, it is exactly the sort of thing a
fitted polynomial silently gets wrong, and it is checked in the tests below.

It also means STR.TIP_DEFL and STR.MASS are far less correlated than r1's expressions
imply -- r1 has deflection proportional to n_z_ult * W0, which for a fully-stressed
box is not right.

WHAT THIS IS NOT
----------------
No torsion, no aeroelastic feedback (the spanload is rigid-wing), no buckling (caps are
sized on stress alone, which for a real box is optimistic at the compression cap), no
gust cases, no fatigue, no cut-outs, no landing loads. It is a strength-sized beam. It
is enormously more real than a fitted power law, and it is still not ASWING.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

__all__ = ["Material", "ALUMINIUM_7075", "CARBON_UNI", "BeamResult", "size_wing_box"]

G = 9.80665


@dataclass(frozen=True)
class Material:
    name: str
    E: float           # Young's modulus, Pa
    sigma_allow: float  # allowable stress at ultimate, Pa
    rho: float         # density, kg/m^3
    min_gauge_area: float = 4.0e-5   # m^2, minimum cap area per cap (~40 mm^2)


# 7075-T6: E 71.7 GPa, Ftu 572 MPa. Allowable taken at ~0.6*Ftu for a cap carrying
# combined bending and the usual knockdowns for joints and holes.
ALUMINIUM_7075 = Material("aluminium 7075-T6", 71.7e9, 340e6, 2810.0)

# Unidirectional carbon/epoxy cap, ~60% fibre volume. Allowable is compression-driven
# (fibre microbuckling), which is why it is well below the tensile capability.
CARBON_UNI = Material("carbon/epoxy UD", 135e9, 600e6, 1600.0)


@dataclass
class BeamResult:
    W_structure: float        # kg, total structural mass as sized (see stiffness_factor)
    W_wingbox: float          # kg, spar caps + non-optimum, both wings
    tip_deflection: float     # m, of the STRENGTH-sized box
    tip_defl_frac: float      # deflection / semispan, strength-sized
    root_box_depth: float     # m
    root_bending_moment: float  # N.m
    root_cap_area: float      # m^2, per cap
    material: str
    stiffness_factor: float = 1.0   # cap area multiplier needed to meet defl_limit_frac
    W_structure_strength: float = 0.0   # kg, before any stiffness scaling
    y: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))
    M: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))
    A_cap: np.ndarray = field(repr=False, default_factory=lambda: np.array([]))

    def report(self) -> str:
        return "\n".join([
            f"material           {self.material}",
            f"root bending       {self.root_bending_moment / 1e3:.1f} kN.m",
            f"root box depth     {self.root_box_depth * 1000:.1f} mm",
            f"root cap area      {self.root_cap_area * 1e4:.2f} cm^2 per cap",
            f"tip deflection     {self.tip_deflection:.3f} m "
            f"({self.tip_defl_frac * 100:.2f}% of semispan)  [strength-sized]",
            f"stiffness factor   x{self.stiffness_factor:.2f} cap area to meet the limit",
            f"W_structure        {self.W_structure:.1f} kg "
            f"(strength-only would be {self.W_structure_strength:.1f} kg)",
        ])


def size_wing_box(
    strips,
    *,
    W0: float,
    n_z_ult: float,
    t_c: float,
    span: float,
    material: Material = ALUMINIUM_7075,
    box_depth_frac: float = 0.92,
    non_optimum: float = 1.9,
    carrythrough_frac: float = 0.22,
    fuselage_frames: float = 38.0,
    inertia_relief: bool = True,
    defl_limit_frac: float | None = None,
    max_iter: int = 12,
) -> BeamResult:
    """Size the wing box against the AVL spanload and return mass and deflection.

    Args:
        strips: `strips` from `avl_runner.run_case(..., want_strips=True)`. Only the
            SHAPE of the loading is taken from these; the magnitude is set by
            n_z_ult * W0, so the AVL run does not need to be trimmed to the manoeuvre.
        W0: design gross mass, kg.
        n_z_ult: ultimate load factor.
        t_c: streamwise thickness ratio.
        span: full span, m.
        box_depth_frac: structural box depth as a fraction of the section max thickness.
            0.92 is a front-spar-to-rear-spar box on a conventional section.
        non_optimum: multiplier on ideal cap mass covering webs, skins, ribs, joints,
            fasteners and manufacturing minimums. 1.9 is a conventional conceptual
            value and it is the single largest piece of judgement in this module.
        carrythrough_frac: centre-section carry-through, as a fraction of wing box mass.
        fuselage_frames: fixed allowance, kg, for frames and backup structure. Part of
            W_structure per the design-space definition, and not a wing quantity at all.
        inertia_relief: subtract the wing's own inertial load from the airload. Real,
            always reduces root bending, and needs iteration because it depends on the
            mass being computed. Converges in a handful of passes.

    Returns:
        BeamResult
    """
    if not strips:
        raise ValueError("no strips -- call run_case(..., want_strips=True)")

    # Work on one semispan. AVL hands back both halves for a YDUPLICATE wing.
    half = [s for s in strips if s.y >= 0.0] or strips
    order = np.argsort([s.y for s in half])
    y = np.array([half[i].y for i in order])
    chord = np.array([half[i].chord for i in order])
    area = np.array([half[i].area for i in order])
    cl = np.array([half[i].cl for i in order])

    semispan = 0.5 * span
    h = box_depth_frac * t_c * chord              # structural box depth per strip
    h = np.maximum(h, 1e-4)

    lift_shape = np.maximum(cl * area, 0.0)
    if lift_shape.sum() <= 0:
        raise ValueError("degenerate spanload: all strip lift is zero or negative")
    lift_shape = lift_shape / lift_shape.sum()    # fraction of semispan lift per strip

    total_lift_half = 0.5 * n_z_ult * W0 * G      # N carried by one semispan

    W_box_half = 0.0   # kg, one semispan, caps + non-optimum
    result = None
    for _ in range(max_iter):
        F = lift_shape * total_lift_half
        if inertia_relief and W_box_half > 0:
            # The wing's own weight acts down under the same load factor, relieving
            # bending. Distribute it like the cap material, i.e. like the airload --
            # a reasonable first approximation and conservative at the tip.
            F = F - lift_shape * (W_box_half * G * n_z_ult)

        # Outboard-in integration: shear then moment, about each station.
        V = np.array([F[i:].sum() for i in range(len(F))])
        M = np.array([float(np.sum(F[i:] * (y[i:] - y[i]))) for i in range(len(F))])
        M = np.maximum(M, 0.0)

        A_cap = np.maximum(M / (material.sigma_allow * h), material.min_gauge_area)

        # Cap mass: two caps per box, integrated along the semispan.
        edges = np.concatenate(([0.0], 0.5 * (y[:-1] + y[1:]), [semispan]))
        dy = np.diff(edges)
        cap_mass_half = float(np.sum(2.0 * A_cap * dy) * material.rho)
        new_W_box_half = cap_mass_half * non_optimum

        converged = abs(new_W_box_half - W_box_half) < 1e-4 * max(new_W_box_half, 1.0)
        W_box_half = new_W_box_half
        if converged:
            break

    # Deflection. For a fully-stressed cap the curvature is 2*sigma/(E*h) wherever the
    # cap is stress-critical; where minimum gauge governs, the section is stiffer than
    # fully-stressed and the curvature must come from M/(E*I) instead. Use the smaller.
    I = A_cap * h**2 / 2.0
    kappa_fs = 2.0 * material.sigma_allow / (material.E * h)
    kappa_mg = M / (material.E * np.maximum(I, 1e-12))
    kappa = np.minimum(kappa_fs, kappa_mg)

    # Tip deflection = integral of kappa(y) * (semispan - y) dy  (moment-area).
    edges = np.concatenate(([0.0], 0.5 * (y[:-1] + y[1:]), [semispan]))
    dy = np.diff(edges)
    tip_defl = float(np.sum(kappa * (semispan - y) * dy))

    W_box = 2.0 * W_box_half                       # both wings
    W_strength = W_box * (1.0 + carrythrough_frac) + fuselage_frames

    # Stiffness sizing. A strength-sized aluminium wing at ultimate load really does
    # deflect ~20% of semispan -- the fully-stressed cap strain is sigma/E ~ 0.005, and
    # curvature 2*strain/h integrates to a lot over a long semispan. That is why real
    # high-aspect-ratio wings are STIFFNESS-critical: they carry far more cap area than
    # strength requires, and the extra material is a mass penalty that a strength-only
    # model simply does not see.
    #
    # Adding cap area scales curvature as 1/lambda, so the factor needed is just the
    # ratio of achieved to allowed deflection.
    lam = 1.0
    W_structure = W_strength
    if defl_limit_frac is not None and defl_limit_frac > 0:
        lam = max(1.0, (tip_defl / semispan) / defl_limit_frac)
        W_structure = W_box * lam * (1.0 + carrythrough_frac) + fuselage_frames

    return BeamResult(
        stiffness_factor=lam,
        W_structure_strength=W_strength,
        W_structure=W_structure,
        W_wingbox=W_box,
        tip_deflection=tip_defl,
        tip_defl_frac=tip_defl / semispan,
        root_box_depth=float(h[0]),
        root_bending_moment=float(M[0]),
        root_cap_area=float(A_cap[0]),
        material=material.name,
        y=y, M=M, A_cap=A_cap,
    )
