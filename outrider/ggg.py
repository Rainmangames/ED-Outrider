"""Potential green gas giants (the author's ask, 2026-10-12). Pure.

A green gas giant looks like any other in the FSS: nothing flags it. The community's "seven-layer ladder" model says
which ones render green: from a giant's surface temperature, mass and radius come seven evenly spaced cloud-layer
temperatures; when one lands exactly on a colour border of its class, the giant is green. Exactly means in the game's
32-bit float arithmetic: some green windows are 0.00002 K wide, so only a journal's full-precision temperature will do
(never Spansh's rounded one).

The model is CMDR Regza's (with AI assistance), from a hint by Dr. K. Ross and CMDR Arcanic (whose edGGG catalogue it
was fitted to), as published by Canonn (github.com/canonn-science/EDMC-Canonn issue 222, 2026-10-05). It is a
reconstruction, not the game's own code: a prediction is "potential", and a codex entry always beats it. Written here
from those published rules; the class rules are a table so corrections can land without touching the arithmetic.
"""
import math
import struct

M_EARTH_KG = 5.9722e24
UNCAPPED_TOLERANCE_K = 0.0002   # an uncapped ladder's hit (its arithmetic is not known to the bit): this close counts
RUNGS = 7

# (class, lowest K or None, highest K (exclusive) or None, rule). A rule: ceiling (the ladder's top, K; None: uncapped),
# min_step (K or None), borders (K), sure (the rule confirmed by catalogue bodies, else derived). "nudged": the game
# moves the bottom rung by an unexplained offset there, so nothing can be said. A class or range not listed: unknown.
RULES = [
    ("class1", None, 80, {"ceiling": 340, "borders": (115, 250, 270), "sure": True}),
    ("class1", 80, 113, "nudged"),
    ("class1", 113, 330, {"ceiling": 340, "borders": (115, 250, 270), "sure": True}),
    ("ammonia_life", None, 80, {"ceiling": 340, "borders": (175, 250, 270), "sure": True}),
    ("ammonia_life", 80, 113, "nudged"),
    ("ammonia_life", 113, None, {"ceiling": 340, "borders": (175, 250, 270), "sure": True}),
    ("water_life", 150, 250, {"ceiling": 340, "borders": (210, 270), "sure": True}),
    ("water_giant", 157.999, 158.001, {"ceiling": 340, "borders": (210,), "sure": True}),
    ("class2", None, None, "nudged"),
    ("class3", None, 370, "nudged"),
    ("class3", 370, 370.0001, {"ceiling": None, "borders": (370,), "sure": True}),
    ("class3", 370.0001, 415, "nudged"),
    ("class3", 490, 680, {"ceiling": 700, "min_step": 30, "borders": (700,), "sure": True}),
    ("class3", 680, 900, {"ceiling": 1200, "min_step": 30, "borders": (700, 900), "sure": False}),
    ("class3", 900, None, {"ceiling": 1500, "min_step": 30, "borders": (1400,), "sure": False}),
    ("class4", 900, None, {"ceiling": 1500, "borders": (900, 1400), "sure": True}),
    ("class5", None, None, "nudged"),
    ("helium", None, None, "nudged"),
]
# the game's codex names for a green one (Tourist): a body with one of these is green, no guess needed
CODEX_GREEN = ("Green Gas Giant", "Green Class I Gas Giant", "Green Class II Gas Giant", "Green Class III Gas Giant",
               "Green Class IV Gas Giant", "Green Class V Gas Giant", "Green Water Giant", "Green Gas Giant with Ammonia Life")
SPOKEN_CLASS = {"class1": "a class one gas giant", "class3": "a class three gas giant", "class4": "a class four gas giant",
                "water_life": "a gas giant with water-based life", "ammonia_life": "a gas giant with ammonia-based life",
                "water_giant": "a water giant"}


def f32(v):
    """v rounded through a 32-bit float, as the game computes."""
    return struct.unpack("f", struct.pack("f", float(v)))[0]


def gas_class(planet_class):
    """The rule table's class for a journal PlanetClass ("Sudarsky class I gas giant" -> "class1"), or None."""
    s = str(planet_class or "").lower().replace("-", " ")
    if "helium" in s:
        return "helium"
    if "water giant" in s:
        return "water_giant"
    if "life" in s:
        return "ammonia_life" if "ammonia" in s else "water_life" if "water" in s else None
    for roman, key in (("class v ", "class5"), ("class iv ", "class4"), ("class iii ", "class3"), ("class ii ", "class2"),
                       ("class i ", "class1")):
        if roman in s + " ":
            return key
    return None


def rule_for(cls, temp):
    for c, lo, hi, rule in RULES:
        if c == cls and (lo is None or temp >= lo) and (hi is None or temp < hi):
            return rule
    return None


def predict(scan):
    """A journal Scan (PlanetClass, SurfaceTemperature, MassEM, Radius in metres) -> None (not green, or nothing can
    be said) or {"chance": "likely" (a confirmed rule) | "possible" (a derived one), "class", "rung" (1..7),
    "border" (K), "temperature"}."""
    if not isinstance(scan, dict):
        return None
    cls, t = gas_class(scan.get("PlanetClass")), scan.get("SurfaceTemperature")
    mass, radius = scan.get("MassEM"), scan.get("Radius")
    if cls is None or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (t, mass, radius)) \
            or mass <= 0 or radius <= 0:
        return None
    rule = rule_for(cls, float(t))
    if not isinstance(rule, dict):
        return None
    density = mass * M_EARTH_KG / (4.0 / 3.0 * math.pi * float(radius) ** 3)   # kg/m³
    depth = 5.0 / 9.0 * 1300.0 * (t / 1300.0) ** 1.2 * density ** 0.2      # the ladder's span above the surface (K)
    ceiling, min_step = rule.get("ceiling"), rule.get("min_step")
    capped = ceiling is not None and t + depth >= ceiling
    hit = None
    if capped or min_step is not None:   # exact, in the game's float32 arithmetic
        t32, top32 = f32(t), f32(ceiling if capped else t + depth)
        step = f32(f32(top32 - t32) / f32(RUNGS))
        if min_step is not None:
            step = max(step, f32(min_step))
        for k in range(RUNGS):
            rung = f32(t32 + f32(f32(k) * step))
            hit = hit or next(((k + 1, b) for b in rule["borders"] if rung == f32(b)), None)
    else:                                # an uncapped ladder: within a hair of a border
        step = depth / RUNGS
        for k in range(RUNGS):
            hit = hit or next(((k + 1, b) for b in rule["borders"] if abs(t + k * step - b) <= UNCAPPED_TOLERANCE_K), None)
    if not hit:
        return None
    return {"chance": "likely" if rule["sure"] else "possible", "class": cls, "rung": hit[0], "border": hit[1],
            "temperature": t}


def confirmed(codex_names):
    """Whether a body's codex entries say it is green."""
    return any(str(n or "").strip() in CODEX_GREEN for n in codex_names or ())


def predict_record(r):
    """predict() for one of Outrider's body records (yours from the journal, or Spansh's from someone else's scan:
    both keep the journal's own temperature): the class from ed.PlanetClass (or subtype), radius_km back to metres."""
    if not isinstance(r, dict) or r.get("type") != "Planet":
        return None
    ed = r.get("ed") if isinstance(r.get("ed"), dict) else {}
    km = r.get("radius_km")
    return predict({"PlanetClass": ed.get("PlanetClass") or r.get("subtype"), "SurfaceTemperature": r.get("temperature"),
                    "MassEM": ed.get("MassEM"), "Radius": km * 1000 if isinstance(km, (int, float)) else None})
