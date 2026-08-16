"""
Component mass build-up, and the fuselage wetted-area model that feeds it.

weights-r1 publishes W_empty as one fitted line:

    W_empty = W_structure + 0.14*W0 + 1.1*P_SL**0.9 + 45.0

`0.14*W0` is everything that is not wing or engine, rolled into a fraction of gross
mass. That form has a structural problem beyond accuracy: with W_empty proportional to
W0, mass closure becomes nearly self-satisfying, and it hides the fact that P_SL --
which has sensitivity 0.0 in every other publication -- ought to cost something real.

This module sizes the pieces instead:

    fuselage    from a body wrapped around the payload and fuel, by wetted area and an
                areal density for the construction you name
    propulsion  engine from installed power and a specific power, plus propeller,
                installation and fuel system
    systems     avionics, actuators, wiring -- close to fixed at this scale
    gear        the conventional fraction of gross mass

and it exports the fuselage/tail parasite drag that falls out of the same wetted-area
calculation. That last part matters beyond weights: every aero publication so far has
carried a DECLARED, unmeasured non-wing drag term (`0.0062 + 0.14/S_ref`) because the
AVL deck is a wing and cannot produce it. Once weights knows the fuselage size, that
term stops being an assumption -- one model closing a gap in two disciplines.

WHAT IS PINNED
--------------
Fineness ratio 6.0, payload packing density, a fixed systems allowance, and the areal
densities below. These are declared constants, not fitted, and they are where a real
weights group would spend its time. The point is that they are now VISIBLE and each one
is a number someone can argue with, rather than being absorbed into 0.14*W0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["Construction", "GLASS_FOAM", "CARBON_SANDWICH", "MassBreakdown",
           "size_fuselage", "mass_breakdown", "nonwing_parasite_drag"]

RHO_FUEL = 750.0          # kg/m^3, avgas
RHO_PAYLOAD_PACK = 250.0  # kg/m^3, packaged sensor/payload bay density
V_SYSTEMS = 0.15          # m^3, avionics and equipment bay
FINENESS = 6.0            # fuselage length / diameter
W_PAYLOAD = 90.0
FUEL_FRACTION = 0.18


@dataclass(frozen=True)
class Construction:
    name: str
    areal_density: float   # kg/m^2 of wetted skin, including core, plies and bonding
    non_optimum: float     # frames, bulkheads, hatches, hardpoints, joints


# Glass/foam sandwich: cheap, heavy, entirely conventional for a UAV of this size.
GLASS_FOAM = Construction("glass/foam sandwich", 4.1, 1.45)
# Carbon/nomex sandwich: the same shell in the material the wing caps switch to at
# round 6, so the campaign event can touch the fuselage too if you want it to.
CARBON_SANDWICH = Construction("carbon/nomex sandwich", 2.7, 1.40)


@dataclass
class Fuselage:
    length: float          # m
    diameter: float        # m
    volume_required: float  # m^3
    wetted_area: float     # m^2
    mass: float            # kg


@dataclass
class MassBreakdown:
    W_empty: float
    W_structure: float     # wing box, from the structures discipline
    W_fuselage: float
    W_propulsion: float
    W_systems: float
    W_gear: float
    fuselage: Fuselage
    construction: str

    def report(self) -> str:
        return "\n".join([
            f"  wing structure   {self.W_structure:7.1f} kg   (from structures)",
            f"  fuselage         {self.W_fuselage:7.1f} kg   "
            f"({self.fuselage.length:.2f} m x {self.fuselage.diameter:.2f} m, "
            f"{self.fuselage.wetted_area:.1f} m^2 wetted)",
            f"  propulsion       {self.W_propulsion:7.1f} kg",
            f"  systems          {self.W_systems:7.1f} kg",
            f"  landing gear     {self.W_gear:7.1f} kg",
            f"  {'-' * 40}",
            f"  W_empty          {self.W_empty:7.1f} kg   [{self.construction}]",
        ])


def size_fuselage(W0: float, S_ref: float, AR: float,
                  construction: Construction = GLASS_FOAM) -> Fuselage:
    """Size the body and weigh its skin, from the SHARED configuration.

    Two requirements set the length and the configuration's `length_rule` says to take
    the larger:

      volume    it has to hold the fuel, the payload and the equipment
      tail arm  it has to be long enough to mount the empennage at the arm the
                configuration declares

    An earlier version of this function sized on volume alone and took only W0. That
    produced a body 2.9-5.0 m too short to carry its own tail across this entire design
    space -- roughly 43 kg of unpaid-for skin, more than the fuselage mass it reported.
    Nothing caught it: the interface between aero's tail arm and the weights fuselage
    was never declared, so no consistency residual covered it.

    It is why this now takes S_ref and AR: fuselage length depends on the wing's mean
    chord, so W_empty is a function of wing geometry. It always was; the old signature
    just could not express it.
    """
    from configuration import Geometry, load_config

    g = Geometry(load_config()).derive(
        S_ref=S_ref, AR=AR, t_c=0.12, sweep_c4=0.0, W0=W0,
        fuel_fraction=FUEL_FRACTION, rho_fuel=RHO_FUEL, w_payload=W_PAYLOAD)
    mass = g.fuselage_wetted * construction.areal_density * construction.non_optimum
    return Fuselage(length=g.fuselage_length, diameter=g.fuselage_diameter,
                    volume_required=0.0, wetted_area=g.fuselage_wetted, mass=mass)


def propulsion_mass(P_SL: float, W0: float,
                    specific_power: float = 1.6) -> float:
    """Engine, propeller, installation and fuel system.

    specific_power in kW/kg: 1.6 is a normally-aspirated piston UAV engine. The
    installation multiplier covers mounts, cowling, cooling, exhaust and controls, and
    is where most of the mass actually is.
    """
    m_engine = P_SL / specific_power
    m_installation = 0.50 * m_engine
    m_prop = 0.06 * m_engine + 2.5
    m_fuel_system = 0.12 * (FUEL_FRACTION * W0) + 4.0   # tanks, pumps, lines
    return m_engine + m_installation + m_prop + m_fuel_system


def mass_breakdown(W_structure: float, W0: float, P_SL: float,
                   S_ref: float, AR: float, *,
                   construction: Construction = GLASS_FOAM) -> MassBreakdown:
    """Assemble W_empty from components. W_structure comes from the structures
    discipline -- this is the coupling, and it is now a genuine sum rather than a
    fitted expression that happens to contain the supplier's value."""
    fus = size_fuselage(W0, S_ref, AR, construction)
    prop = propulsion_mass(P_SL, W0)
    systems = 0.045 * W0 + 14.0     # avionics, actuators, wiring, links
    gear = 0.035 * W0
    empty = W_structure + fus.mass + prop + systems + gear
    return MassBreakdown(W_empty=empty, W_structure=W_structure,
                         W_fuselage=fus.mass, W_propulsion=prop,
                         W_systems=systems, W_gear=gear, fuselage=fus,
                         construction=construction.name)


def nonwing_parasite_drag(W0: float, S_ref: float, AR: float = 12.0,
                          P_SL: float = 110.0,
                          V: float = 40.0,
                          rho: float = 1.225, mu: float = 1.789e-5,
                          construction: Construction = GLASS_FOAM,
                          fixed_gear: bool = False) -> float:
    """CD0 contribution of everything that is not the wing, referred to S_ref.

    Flat-plate skin friction with a form factor, on the fuselage the weights model just
    sized, plus a tail allowance scaled to wing area and an interference margin. This is
    the term the aero publications have been carrying as a declared constant.

    Not a high-fidelity drag build-up -- no excrescences, no cooling drag, no antenna
    farm -- but it is at least a function of the actual body, so it moves when the
    design moves. `0.0062 + 0.14/S_ref` did not.
    """
    fus = size_fuselage(W0, S_ref, AR, construction)
    Re_l = rho * V * fus.length / mu
    Cf = 0.455 / (math.log10(Re_l) ** 2.58)          # turbulent flat plate
    f = FINENESS
    FF = 1.0 + 60.0 / f**3 + f / 400.0               # body form factor
    d_fus = Cf * FF * fus.wetted_area

    # Tail: wetted area conventionally ~0.28*S_ref for a tail volume of this class,
    # at a thin-section form factor.
    s_wet_tail = 0.28 * S_ref
    Re_t = rho * V * 0.6 / mu
    Cf_t = 0.455 / (math.log10(Re_t) ** 2.58)
    d_tail = Cf_t * 1.25 * s_wet_tail

    # Skin friction alone is not a drag estimate. The terms below are the ones that
    # separate a wetted-area calculation from an aircraft, and each is named so it can
    # be argued with rather than buried in a single fudge factor.
    d_gear = (0.006 if fixed_gear else 0.0008) * S_ref   # fixed gear, or wells/doors
    excrescence = 1.35        # rivets, gaps, seams, antennas, roughness -- production
    cooling = 0.00025 * P_SL  # radiator/cooling drag scales with rejected heat, i.e.
                              # with installed power. Gives P_SL a drag cost as well as
                              # a mass cost; it currently has neither anywhere else.
    payload_turret = 0.0015 * S_ref   # the 90 kg sensor payload has to see out

    return (excrescence * (d_fus + d_tail + d_gear)
            + cooling + payload_turret) / S_ref
