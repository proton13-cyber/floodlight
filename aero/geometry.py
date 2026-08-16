"""
Emit a valid AVL geometry deck from the floodlight shared design vector.

The design vector is the one in spaces/design_space.uav-medium.v1.json:

    S_ref [m^2], AR [-], t_c [-], sweep_c4 [deg], W0 [kg], P_SL [kW]

Only the first four touch the geometry. W0 and P_SL enter the flight condition and
the constraints, not the shape -- which is itself worth knowing: it means the AVL
surrogate is a function of a 4-D subspace, and any DOE that varies W0 or P_SL while
holding the other four fixed will re-run identical AVL cases. Cache accordingly.

WHAT IS ASSUMED, AND THEREFORE WHAT THIS SURROGATE CANNOT SEE
-------------------------------------------------------------
Everything not in the shared vector had to be pinned to something. These are the
pins, and every one of them is a claim the publication's `known_limitations` must
carry:

  * taper ratio 0.5, linear                      (not a design variable here)
  * washout -2 deg linear root->tip              (fixed; drives the e vs. AR trend)
  * NACA 24xx section, camber from the 4-digit   (t/c is the only section freedom)
  * no fuselage, no nacelle, no tail             (see `include_tail`)
  * no dihedral, no winglet
  * incompressible, Mach = 0

A VLM knows about induced drag and span loading. It does not know about profile
drag, separation, or CL_max. Do not ask this module for those.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

__all__ = ["WingGeometry", "design_vector_to_avl", "write_deck"]


@dataclass(frozen=True)
class WingGeometry:
    """Derived planform quantities. Returned alongside the deck so the caller can
    report span, MAC and reference point without re-parsing the file."""

    S_ref: float
    AR: float
    t_c: float
    sweep_c4_deg: float
    span: float
    c_root: float
    c_tip: float
    mac: float
    x_ref: float
    taper: float
    twist_tip_deg: float


def _planform(
    S_ref: float, AR: float, sweep_c4_deg: float, taper: float
) -> tuple[float, float, float, float, float, float]:
    b = math.sqrt(AR * S_ref)
    c_root = 2.0 * S_ref / (b * (1.0 + taper))
    c_tip = taper * c_root

    # Quarter-chord sweep is the design variable; the leading edge follows from it.
    half_b = 0.5 * b
    x_c4_tip = half_b * math.tan(math.radians(sweep_c4_deg))
    x_le_tip = 0.25 * c_root + x_c4_tip - 0.25 * c_tip

    mac = (2.0 / 3.0) * c_root * (1.0 + taper + taper**2) / (1.0 + taper)
    y_mac = (b / 6.0) * (1.0 + 2.0 * taper) / (1.0 + taper)
    x_le_mac = y_mac * (x_le_tip / half_b)
    x_ref = x_le_mac + 0.25 * mac
    return b, c_root, c_tip, mac, x_ref, x_le_tip


def design_vector_to_avl(
    S_ref: float,
    AR: float,
    t_c: float,
    sweep_c4: float,
    *,
    taper: float = 0.5,
    twist_tip_deg: float = -2.0,
    n_chord: int = 8,
    n_span: int = 24,
    span_spacing: float = -2.0,
    allow_dense_cosine: bool = False,
    camber_digits: str = "24",
    title: str = "floodlight uav-medium",
    include_tail: bool = False,
    tail_volume: float = 0.55,
    tail_arm_macs: float = 3.5,
) -> tuple[str, WingGeometry]:
    """Build the deck text and the derived geometry.

    Args:
        taper, twist_tip_deg: pinned planform assumptions (see module docstring).
        n_chord, n_span: vortex lattice density. 8 x 24 per semispan converges CL to
            well under a count for these aspect ratios; raising n_span moves `e` in the
            third decimal. Do not lower it to save time -- AVL runs in well under a
            second and a coarse lattice biases induced drag low.

            DO NOT RAISE n_span PAST 32 WITH COSINE SPACING. A single-precision AVL
            build (which is the standard build, including the stock Windows binaries)
            silently returns CLtot = NaN once the cosine distribution packs stations
            tightly enough that neighbouring strips collapse. Measured on AVL 3.32:
            clean at 32, NaN at 48, and the Trefftz block stays plausible the whole
            time so nothing looks wrong. If you truly need more resolution, set
            span_spacing = -1.0 (sine) which was verified clean at 48.
        span_spacing: AVL Sspace. -2.0 cosine (default), -1.0 sine, 0.0 equal,
            1.0 -1.0 = clustered at one end.
        camber_digits: first two digits of the NACA 4-series (max camber %, position
            in tenths). "24" = 2% camber at 40% chord.
        include_tail: add a horizontal tail sized by volume coefficient. Off by default:
            with no tail the pitching moment is the wing's alone and CL trim is a pure
            wing result, which is what the aero surrogate wants. Turn it on only if you
            are also going to trim the elevator, otherwise you are just adding drag from
            a surface at a made-up incidence.

    Returns:
        (deck_text, WingGeometry)
    """
    if not (0.0 < taper <= 1.0):
        raise ValueError("taper must be in (0, 1]")
    if t_c <= 0.0 or t_c >= 0.40:
        raise ValueError(f"implausible t/c: {t_c}")
    if AR <= 0 or S_ref <= 0:
        raise ValueError("S_ref and AR must be positive")
    if span_spacing <= -1.5 and n_span > 32 and not allow_dense_cosine:
        raise ValueError(
            f"n_span={n_span} with cosine spacing produces silent NaN totals on a "
            f"single-precision AVL build (measured on 3.32). Use n_span <= 32, or "
            f"span_spacing=-1.0. If your build is immune -- AVL 3.52 is; "
            f"aero/verify_avl.py reports which -- pass allow_dense_cosine=True."
        )

    b, c_root, c_tip, mac, x_ref, x_le_tip = _planform(S_ref, AR, sweep_c4, taper)

    # NACA 4-digit thickness is 2 digits: t/c in percent, rounded. This quantizes the
    # t_c design variable to 1% steps -- an honest limitation of using a 4-digit section
    # as the shape parameterization, and the reason the t_c sensitivity in the fitted
    # surrogate will look like a staircase if you sample it finely.
    tt = int(round(t_c * 100.0))
    tt = max(1, min(99, tt))
    naca = f"{camber_digits}{tt:02d}"

    L: list[str] = []
    A = L.append
    A(title)
    A("#Mach")
    A("0.0")
    A("#IYsym  IZsym  Zsym")
    A("0       0      0.0")
    A("#Sref   Cref   Bref")
    A(f"{S_ref:.6f}  {mac:.6f}  {b:.6f}")
    A("#Xref   Yref   Zref")
    A(f"{x_ref:.6f}  0.0  0.0")
    A("#CDp  (profile drag is NOT modelled here -- see aero/cases.py build-up)")
    A("0.0")
    A("")
    A("SURFACE")
    A("Wing")
    A("#Nchord  Cspace  Nspan  Sspace")
    A(f"{n_chord}  1.0  {n_span}  {span_spacing}")
    A("YDUPLICATE")
    A("0.0")
    A("SCALE")
    A("1.0  1.0  1.0")
    A("TRANSLATE")
    A("0.0  0.0  0.0")
    A("ANGLE")
    A("0.0")
    A("")
    A("SECTION")
    A("#Xle     Yle     Zle     Chord   Ainc")
    A(f"0.0  0.0  0.0  {c_root:.6f}  0.0")
    A("NACA")
    A(naca)
    A("")
    A("SECTION")
    A("#Xle     Yle     Zle     Chord   Ainc")
    A(f"{x_le_tip:.6f}  {0.5 * b:.6f}  0.0  {c_tip:.6f}  {twist_tip_deg:.4f}")
    A("NACA")
    A(naca)
    A("")

    if include_tail:
        l_t = tail_arm_macs * mac
        S_h = tail_volume * S_ref * mac / l_t
        AR_h = 4.5
        b_h = math.sqrt(AR_h * S_h)
        c_h = S_h / b_h
        A("SURFACE")
        A("Htail")
        A("#Nchord  Cspace  Nspan  Sspace")
        A("6  1.0  12  -2.0")
        A("YDUPLICATE")
        A("0.0")
        A("ANGLE")
        A("0.0")
        A("")
        A("SECTION")
        A(f"{x_ref + l_t:.6f}  0.0  0.0  {c_h:.6f}  0.0")
        A("NACA")
        A("0010")
        A("")
        A("SECTION")
        A(f"{x_ref + l_t:.6f}  {0.5 * b_h:.6f}  0.0  {c_h:.6f}  0.0")
        A("NACA")
        A("0010")
        A("")

    geom = WingGeometry(
        S_ref=S_ref,
        AR=AR,
        t_c=t_c,
        sweep_c4_deg=sweep_c4,
        span=b,
        c_root=c_root,
        c_tip=c_tip,
        mac=mac,
        x_ref=x_ref,
        taper=taper,
        twist_tip_deg=twist_tip_deg,
    )
    return "\n".join(L) + "\n", geom


def write_deck(path: str | Path, *args, **kwargs) -> WingGeometry:
    """design_vector_to_avl(), written to `path`."""
    text, geom = design_vector_to_avl(*args, **kwargs)
    Path(path).write_text(text)
    return geom
