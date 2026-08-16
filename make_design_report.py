"""
Render the final-round design shortlist as a PDF.

Reads top_designs.json (from report_designs.py) and produces a document a reviewer can
read without the repository open: what was selected and why, the shortlisted designs with their
geometry and margins, and -- deliberately given as much space as the results -- what the
numbers do not mean.

    python3 report_designs.py --out top_designs.json
    python3 make_design_report.py --data top_designs.json --out designs.pdf
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak,  # noqa: E402
                                Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#6b6b6b")
RULE = colors.HexColor("#d8d8d8")
BAND = colors.HexColor("#f4f4f2")
ACCENT = colors.HexColor("#8c3a2b")

SHORT = {"aerodynamics:AER.V_STALL": "V_stall",
         "aerodynamics:AER.LD_CRUISE": "L/D",
         "structures:STR.TIP_DEFL": "stiffness",
         "structures:STR.SPAR_DEPTH": "spar depth",
         "weights:WTS.CLOSURE": "closure",
         "weights:WTS.PWR_LOADING": "power"}


def styles():
    s = getSampleStyleSheet()
    base = dict(fontName="Helvetica", alignment=TA_LEFT)
    return {
        "title": ParagraphStyle("t", parent=s["Title"], fontName="Helvetica-Bold",
                                fontSize=20, leading=24, textColor=INK,
                                spaceAfter=2),
        "sub": ParagraphStyle("s", fontSize=9.5, leading=13, textColor=MUTED, **base),
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=12, leading=15,
                            textColor=INK, spaceBefore=14, spaceAfter=5,
                            alignment=TA_LEFT),
        "b": ParagraphStyle("b", fontSize=9.5, leading=13.5, spaceAfter=6, textColor=INK, **base),
        "small": ParagraphStyle("sm", fontSize=8.2, leading=11, textColor=MUTED, **base),
        "cap": ParagraphStyle("c", fontSize=8, leading=10.5, textColor=MUTED,
                              spaceBefore=3, **base),
    }


def margin_chart(designs, keys, path):
    """One row per design, one marker per constraint, at its margin value."""
    n = len(designs)
    fig, ax = plt.subplots(figsize=(7.0, 0.30 * n + 0.9), dpi=200)
    cols = plt.get_cmap("tab10")
    for ci, k in enumerate(keys):
        xs = [max(d["margins"][k], -1.05) for d in designs]
        ys = [d["rank"] for d in designs]
        ax.scatter(xs, ys, s=26, color=cols(ci % 10), label=SHORT.get(k, k),
                   zorder=3, edgecolor="white", linewidth=0.5)
    ax.axvline(0, color="#8c3a2b", lw=1.2, zorder=2)
    ax.set_yticks([d["rank"] for d in designs])
    ax.set_ylim(n + 0.6, 0.4)
    ax.set_xlim(-1.1, 0.08)
    ax.set_xlabel("normalised margin  g   (negative = satisfied; 0 = on the limit)",
                  fontsize=8.5)
    ax.set_ylabel("design", fontsize=8.5)
    ax.tick_params(labelsize=8)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="x", color="#e8e8e8", lw=0.6, zorder=0)
    ax.legend(fontsize=7.5, ncol=6, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, 1.16))
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def planform_chart(designs, path):
    fig, ax = plt.subplots(figsize=(7.0, 2.6), dpi=200)
    sc = ax.scatter([d["span"] for d in designs], [d["wing_loading"] for d in designs],
                    c=[d["t_c"] for d in designs], cmap="viridis", s=70,
                    edgecolor="white", linewidth=0.7, zorder=3)
    for d in designs:
        ax.annotate(str(d["rank"]), (d["span"], d["wing_loading"]),
                    fontsize=7, color="white", ha="center", va="center", zorder=4)
    cb = fig.colorbar(sc, ax=ax, pad=0.02)
    cb.set_label("t/c", fontsize=8)
    cb.ax.tick_params(labelsize=7.5)
    ax.set_xlabel("span, m", fontsize=8.5)
    ax.set_ylabel("wing loading, kg/m$^2$", fontsize=8.5)
    ax.tick_params(labelsize=8)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(color="#eeeeee", lw=0.6, zorder=0)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def planform_sheet(designs, path):
    """Scale silhouettes of the shortlist, top view, on one common scale.

    Every dimension comes from a model that is actually in the contract:

      wing      taper 0.5 and quarter-chord sweep, from aero/geometry.py -- the same
                planform the AVL decks were written with
      tail      sized by aero/geometry.py's own volume-coefficient rule
                (V_h = 0.55, arm = 3.5 MAC, AR_h = 4.5), which the deck generator
                already carries as an option
      fuselage  length and diameter from weights/mass.py, sized by the volume it has
                to hold at that gross mass

    The dashboard now draws from this same shared geometry -- it once carried its own
    (taper 0.45, an over-long fuselage), which is documented in the write-up as a
    cautionary tale.
    """
    import math as _m
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from configuration import Geometry, load_config
    _cfg = load_config()
    _GEO = Geometry(_cfg)
    _NOSE_FRAC = _cfg["fuselage"]["nose_fraction"]

    V_H, ARM_MACS, AR_H = 0.55, 3.5, 4.5

    def parts(d):
        S, AR, sw = d["S_ref"], d["AR"], d["sweep_c4"]
        b = d["span"]
        c_root = 2 * S / (b * 1.5)
        c_tip = 0.5 * c_root
        s_half = 0.5 * b
        x_le_tip = 0.25 * c_root + s_half * _m.tan(_m.radians(sw)) - 0.25 * c_tip
        mac = 1.037037 * _m.sqrt(S / AR)
        l_t = ARM_MACS * mac
        S_h = V_H * S * mac / l_t
        b_h = _m.sqrt(AR_H * S_h)
        c_h = S_h / b_h
        # Body straight from the shared Geometry -- the same source the physics uses.
        # Its length rule guarantees the fuselage reaches the tail mount, and its
        # nose sits at nose_fraction of the VOLUME-driven length. An earlier version
        # of this drawing used a fraction of TOTAL length, which kept rendering a
        # phantom "boom" aft of the body even after the sizing was fixed -- the
        # drawing disagreeing with the model it illustrates.
        gg = _GEO.derive(S_ref=S, AR=AR, t_c=d["t_c"], sweep_c4=sw, W0=d["W0"])
        x_h = 0.25 * c_root + l_t
        nose = -_NOSE_FRAC * gg.fuselage_length_volume
        body_end = nose + gg.fuselage_length
        tail_end = (x_h + c_h) * 1.05
        return dict(b=b, c_root=c_root, c_tip=c_tip, s_half=s_half,
                    x_le_tip=x_le_tip, l_t=l_t, b_h=b_h, c_h=c_h,
                    L=gg.fuselage_length, dia=gg.fuselage_diameter,
                    x_h=x_h, nose=nose, body_end=body_end, tail_end=tail_end)

    P = [parts(d) for d in designs]
    half = 0.5 * max(p["b"] for p in P) * 1.06
    fore = min(p["nose"] for p in P)
    aft = max(max(p["body_end"], p["tail_end"]) for p in P)
    pad = 0.05 * (aft - fore)

    import math as _mm
    nrows = _mm.ceil(len(designs) / 5)
    fig, axes = plt.subplots(nrows, 5, figsize=(7.2, 1.66 * nrows), dpi=200)
    axlist = axes.ravel() if hasattr(axes, "ravel") else [axes]
    for ax in axlist[len(designs):]:
        ax.axis("off")
    for ax, d, g in zip(axlist, designs, P):
        # fuselage: one full-width body, guaranteed by the config length rule to
        # reach the tail mount -- no boom, because there is nothing left to patch over
        wf = g["dia"]
        ax.fill([-wf / 2, wf / 2, wf / 2, -wf / 2],
                [g["nose"], g["nose"], g["body_end"], g["body_end"]],
                facecolor="#e9e3d9", edgecolor="#8a8175", linewidth=0.6, zorder=2)
        # horizontal tail
        yh = 0.5 * g["b_h"]
        tn_h = _m.tan(_m.radians(12.0))
        ys = [0, yh, yh, 0]
        xs = [g["x_h"], g["x_h"] + yh * tn_h,
              g["x_h"] + yh * tn_h + 0.65 * g["c_h"], g["x_h"] + g["c_h"]]
        ax.fill(ys + [-y for y in ys[::-1]], xs + xs[::-1],
                facecolor="#d3dde5", edgecolor="#4a6b84", linewidth=0.6, zorder=3)
        # wing
        ys = [0, g["s_half"], g["s_half"], 0]
        xs = [0, g["x_le_tip"], g["x_le_tip"] + g["c_tip"], g["c_root"]]
        ax.fill(ys + [-y for y in ys[::-1]], xs + xs[::-1],
                facecolor="#c3d4e0", edgecolor="#2f5470", linewidth=0.8, zorder=4)

        ax.set_xlim(-half, half)
        ax.set_ylim(aft + pad, fore - pad)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(f"{d['rank']}   b {d['span']:.1f} m   "
                     f"{d['couplings']['W_empty']:.0f} kg\n"
                     f"S {d['S_ref']:.1f}  AR {d['AR']:.1f}\n"
                     f"t/c {d['t_c']:.3f}  $\\Lambda$ {d['sweep_c4']:.0f}$\\degree$",
                     fontsize=5.9, color="#1a1a1a", pad=2, linespacing=1.35)

    fig.tight_layout(h_pad=0.4, w_pad=0.15)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def build(data: dict, out: Path) -> None:
    st = styles()
    D = data["designs"]
    N = len(D)
    keys = data["constraint_keys"]
    import math as _mm
    _rows = _mm.ceil(N / 5)
    sheet_h = 172 * (1.66 * _rows) / 7.2          # mm, matches the figure aspect
    mchart_h = 165 * (0.30 * N + 0.9) / 7.0
    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="floodlight - final-round design shortlist",
                            author="floodlight")
    S: list = []

    S.append(Paragraph("Final-round design shortlist", st["title"]))
    S.append(Paragraph(
        f"floodlight &middot; round {data['round']} &middot; "
        f"{', '.join(data['publications'])}", st["sub"]))
    S.append(Spacer(1, 10))

    S.append(Paragraph(
        f"{N} admissible designs drawn from the final campaign round. "
        f"{data['n_sampled']:,} points were sampled inside the round-{data['round']} "
        f"trust region; {data['admissible_fraction']*100:.2f}% of them are "
        f"<b>admissible</b> &mdash; all six disciplinary margins satisfied "
        f"<i>and</i> all three coupling assumptions self-consistent. These are the "
        f"lightest members of separated neighbourhoods within that set, so they are "
        f"{N} genuinely different aircraft rather than {N} samples of one.", st["b"]))

    S.append(Paragraph("What backs these numbers", st["h"]))
    S.append(Paragraph(
        "Aerodynamics is an AVL vortex-lattice solve coupled strip-by-strip to "
        "NeuralFoil section polars, verified against the Warren&nbsp;12 planform to "
        "0.14% on lift-curve slope. Structures is a fully-stressed wing-box beam sized "
        "against the AVL spanload, with cap area scaled up wherever stiffness rather "
        "than strength governs. Weights is a component build-up: a fuselage sized by "
        "the volume it must hold and weighed by wetted area, plus propulsion from "
        "installed power. No coefficient in this document was hand-written.", st["b"]))

    S.append(Paragraph(f"The {N} designs", st["h"]))
    head = ["#", "S_ref\nm²", "AR", "t/c", "sweep\ndeg", "W0\nkg", "P_SL\nkW",
            "span\nm", "W/S\nkg/m²", "W_empty\nkg", "W_str\nkg"]
    rows = [head]
    for d in D:
        rows.append([
            str(d["rank"]), f"{d['S_ref']:.2f}", f"{d['AR']:.2f}", f"{d['t_c']:.3f}",
            f"{d['sweep_c4']:.1f}", f"{d['W0']:.0f}", f"{d['P_SL']:.0f}",
            f"{d['span']:.2f}", f"{d['wing_loading']:.1f}",
            f"{d['couplings']['W_empty']:.0f}",
            f"{d['couplings']['W_structure']:.0f}",
        ])
    t = Table(rows, hAlign="LEFT", colWidths=[8 * mm] + [16 * mm] * 10)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.4),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8.2),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    S.append(t)
    S.append(Paragraph(
        "W_empty and W_str are produced by the weights and structures publications at "
        "each design point, not assumed.", st["cap"]))

    S.append(PageBreak())

    S.append(Paragraph("The aircraft, to scale", st["h"]))
    sheet = out.parent / "_planforms.png"
    planform_sheet(D, sheet)
    S.append(KeepTogether([
        Image(str(sheet), width=172 * mm, height=sheet_h * mm),
        Paragraph(
            "Top views to a single common scale, so spans compare directly. Wing "
            "planform and tail from the shared configuration (taper 0.5, tail volume "
            "0.55); fuselage from the weights build-up, whose length now obeys the "
            "config rule max(volume-driven, tail-arm-driven) &mdash; every body here "
            "is long enough to carry its own tail, and the dashboard draws from the "
            "same geometry. These are the dimensions the publications were built on.",
            st["cap"])]))

    S.append(Paragraph("How they differ", st["h"]))
    pchart = out.parent / "_planform.png"
    planform_chart(D, pchart)
    S.append(KeepTogether([
        Image(str(pchart), width=158 * mm, height=59 * mm),
        Paragraph(
            f"Span runs from {min(d['span'] for d in D):.1f}&nbsp;m to "
            f"{max(d['span'] for d in D):.1f}&nbsp;m at essentially the same gross "
            "mass. That spread is the point: the admissible set is a region, and a "
            "single 'optimum' would misrepresent it.", st["cap"])]))

    S.append(PageBreak())
    S.append(Paragraph("Margins", st["h"]))
    mchart = out.parent / "_margins.png"
    margin_chart(D, keys, mchart)
    S.append(KeepTogether([
        Image(str(mchart), width=165 * mm, height=mchart_h * mm),
        Paragraph(
            "Every constraint is normalised so that g&nbsp;&le;&nbsp;0 means satisfied "
            "and the value is the fraction of the limit remaining. A structures margin "
            "of &minus;0.10 and an aero margin of &minus;0.10 mean the same thing.",
            st["cap"])]))

    S.append(PageBreak())
    S.append(Paragraph("Read this before using any of it", st["h"]))

    lo, hi = data["trust_region"]["W0"]
    active_counts = sum(len(d["active"]) for d in D)
    wres = [d["residuals"]["W_empty"] for d in D]

    for headline, body in [
        ("These are not optima.",
         f"Only {active_counts} constraint{'' if active_counts==1 else 's are'} active across the shortlist. A true " if active_counts==1 else f"Only {active_counts} constraints are active across the shortlist. A true "
         f"optimum sits on a corner with several constraints simultaneously active. "
         f"These do not, because gross mass is bounded below at {lo:.0f}&nbsp;kg by "
         f"the sampling region &mdash; structures' declared validity floor &mdash; "
         f"not by physics: the lightest design here is "
         f"{D[0]['W0']:.0f}&nbsp;kg, and it is light because the search box stops "
         f"there. The real minimum is outside the region the campaign converged on."),
        ("Admissibility now means \"where the teams' beliefs hold\".",
         f"Consistency is checked against each discipline's LOSSY internalization of "
         f"its partners &mdash; a deliberately low-order fit, the way a receiving team "
         f"actually holds an expert's model. These designs sit where that simplified "
         f"picture is valid: W_empty residuals run {min(wres):.2f}&ndash;"
         f"{max(wres):.2f} of tolerance here. Designs outside the shortlist are often "
         f"rejected not because the aircraft fails, but because the teams' current "
         f"understanding of each other cannot vouch for that corner of the space. "
         f"That is the intended meaning, and it is worth keeping distinct from "
         f"physical infeasibility."),
        ("The endurance requirement is not enforced anywhere.",
         "The reference mission asks for 8 hours at 40&nbsp;m/s. No constraint in any "
         "publication checks it; cruise L/D stands in as a proxy and fuel fraction is "
         "a fixed 18% constant rather than an outcome. No propulsion discipline "
         "exists yet, so nothing owns P_SL or closes the range equation."),
        ("The planform is pinned, not designed.",
         "Taper 0.5, &minus;2&deg; washout, NACA&nbsp;24xx sections, no fuselage or "
         "tail in the aerodynamic model. Thickness is quantised to whole percent by "
         "the 4-digit section. These are assumptions shared by all three disciplines "
         "and they are not design variables."),
        ("Structures ignores buckling.",
         "Caps are sized on stress alone, which is optimistic for the compression cap. "
         "The validity box is clipped to where the cap area stays under 3&times; "
         "strength-sized, but within that region the model still has no torsion, no "
         "aeroelastic feedback, no gust cases and no landing loads."),
        ("The fuselage/tail mismatch was caught and fixed.",
         "Earlier in this project, weights sized the body on internal volume alone "
         "while aerodynamics assumed a tail mounted 2.9&ndash;5.0&nbsp;m aft of where "
         "that body ended &mdash; roughly 43&nbsp;kg of structure nobody was paying "
         "for, invisible to every consistency check because the interface was never "
         "declared. The shared configuration now sizes every fuselage to "
         "max(volume-driven, tail-arm-driven) length, weights carries the mass, and "
         "the drawings above use the same rule. It is listed here because the class "
         "of error &mdash; an undeclared interface between teams &mdash; is not "
         "fixable in general, only findable."),
        ("Non-wing drag is unresolved.",
         "The aerodynamic publication carries a declared parasite-drag constant for "
         "the fuselage and tail. A wetted-area build-up from the weights model gives "
         "25&ndash;37% less. The two have not been reconciled, and the aero "
         "publication has deliberately not been switched over to the lower number."),
    ]:
        S.append(Paragraph(f"<b>{headline}</b> {body}", st["b"]))

    S.append(Spacer(1, 6))
    S.append(Paragraph(
        f"Conditioning at round {data['round']}: "
        + ", ".join(f"{k} = {v}" for k, v in data["assumed"].items())
        + ". Every design above satisfies all three within the tolerances the "
          "publications declare.", st["small"]))

    doc.build(S)
    for p in (mchart, pchart, sheet):
        p.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="top_designs.json")
    ap.add_argument("--out", default="designs.pdf")
    args = ap.parse_args()
    build(json.loads(Path(args.data).read_text()), Path(args.out))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
