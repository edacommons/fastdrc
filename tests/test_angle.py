"""Tests for native angle/corner rules.

A straight (``edge: true``) edge is ORIENTED, so its angle is a real quantity our
topology computes directly -- no KLayout edge ops. An angle rule is drawn as a
shape with an oriented edge plus an ``<edge>.angle`` range constraint; the
backend maps it to ``layer.with_angle(lo .. hi)`` (the GF180 "ACUTE / non-45"
checks). The drawn edge's angle is an EXAMPLE inside the forbidden range.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_klayout       # noqa: E402,F401  (registers "klayout")
import fastdrc.topology as topo      # noqa: E402
from fastdrc.backend import get      # noqa: E402
from fastdrc.rule import Rule        # noqa: E402


def edge(eid, x0, y0, x1, y1, straight=True):
    return {"id": eid, "start": [x0, y0], "end": [x1, y1], "edge": straight}


def test_edge_angle_orientation():
    assert topo.edge_angle(edge("h", 0, 0, 10, 0)) == 0.0       # horizontal
    assert topo.edge_angle(edge("v", 0, 0, 0, 10)) == 90.0      # vertical
    assert abs(topo.edge_angle(edge("d", 0, 0, 10, 10)) - 45) < 1e-9    # "/"
    assert abs(topo.edge_angle(edge("b", 0, 0, -10, 10)) - 135) < 1e-9  # "\"
    # undirected: reversing the edge gives the same orientation
    assert topo.edge_angle(edge("r", 10, 0, 0, 0)) == 0.0
    # acute, non-45 -> in (0, 45)
    a = topo.edge_angle(edge("a", 0, 0, 2000, 1000))
    assert 0 < a < 45 and abs(a - 26.565) < 1e-2


def test_corner_angle_right_and_chamfer():
    # CCW square corner: east edge then north edge -> +90 convex turn
    assert abs(topo.corner_angle(edge("e", 0, 0, 10, 0), edge("n", 10, 0, 10, 10)) - 90) < 1e-9
    # 135-degree chamfer (east then "/") -> +45 turn
    assert abs(topo.corner_angle(edge("e", 0, 0, 10, 0), edge("c", 10, 0, 20, 10)) - 45) < 1e-9


ACUTE_RULE = {
    "name": "comp_angle", "desc": "ACUTE : non 45 degree angle comp",
    "shapes": [{"name": "A", "edges": [
        edge("a0", 0, 0, 2000, 0, straight=False),       # don't-care boundary
        edge("a1", 2000, 0, 2000, 1000, straight=False),  # don't-care boundary
        edge("a2", 2000, 1000, 0, 0),      # acute hypotenuse (~26.57) -- the angle edge
    ]}],
    "measures": [],
    "constraints": [
        {"kind": "match", "expr": "A.layer == comp"},
        {"kind": "check", "expr": "a2.angle > 0 and a2.angle < 45"},
    ],
}


def test_angle_bounds_mined_from_constraint():
    r = Rule(ACUTE_RULE)
    assert r.angle_bounds() == [("a2", 0, 45)]
    # native eval: the drawn edge sits inside the forbidden range
    assert 0 < r.edge_angle("a2") < 45


def test_backend_emits_with_angle():
    r = Rule(ACUTE_RULE)
    drc = get("klayout", layermap={"comp": (22, 0)}).compile(r)
    line = next(ln for ln in drc.splitlines() if "with_angle" in ln)
    # matches the GF180 deck op exactly: comp.with_angle(0 .. 45)
    assert line.split("=", 1)[1].strip() == "comp.with_angle(0 .. 45)"


def test_integer_angle_formatting():
    # range bounds print without a trailing .0, like dimensional thresholds
    r = Rule(ACUTE_RULE)
    drc = get("klayout", layermap={"comp": (22, 0)}).compile(r)
    assert "with_angle(0.0" not in drc and "45.0" not in drc


def _square(name, layer, o=0):
    pts = [(o, 0), (o + 1000, 0), (o + 1000, 1000), (o, 1000)]
    n = len(pts)
    # a1.corner uses edges a0 and a1 (they form the corner at a1's start vertex) ->
    # those are real/straight; a2,a3 just close the shape (don't-care -> wavy).
    edges = [{"id": f"a{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % n]),
              "edge": i in (0, 1)} for i in range(n)]
    return {"name": "comp_corner", "desc": "flag 90-degree comp corners",
            "shapes": [{"name": name, "edges": edges}], "measures": [],
            "constraints": [{"kind": "match", "expr": f"{name}.layer == {layer}"},
                            {"kind": "check", "expr": "a1.corner != 90"}]}


def test_corner_bounds_and_native_eval():
    r = Rule(_square("A", "comp"))
    assert r.corner_bounds() == [("a1", 90, False)]       # the 90-degree corners are flagged
    assert abs(r.corner_angle("a1")) == 90                # native, sign-agnostic


def test_backend_emits_corners_both_signs():
    r = Rule(_square("A", "comp"))
    drc = get("klayout", layermap={"comp": (22, 0)}).compile(r)
    line = next(ln for ln in drc.splitlines() if "corners" in ln).split("=", 1)[1].strip()
    # matches PL.6's core: both convex and concave 90-degree corners
    assert line == "comp.corners(90).sized(0.1).or(comp.corners(-90).sized(0.1))"


def test_complete_pl6_corner_with_scope_wrap():
    # PL.6: poly2 90-deg corners, excluding ymtp_mk, inside comp.not(ymtp_mk).
    # The .not(ymtp_mk).inside(comp.not(ymtp_mk)) wrap is derived from a comp
    # context shape (with a ymtp_mk hole) that CONTAINS the poly2 subject.
    def sq(name, x0, y0, x1, y1, holes=None, straight=()):
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        n = len(pts)
        e = [{"id": f"{name.lower()}{i}", "start": list(pts[i]),
              "end": list(pts[(i + 1) % n]), "edge": i in straight} for i in range(n)]
        s = {"name": name, "edges": e}
        if holes:
            s["holes"] = holes
        return s
    rule = {"name": "PL.6", "desc": "PL.6",
            "shapes": [sq("A", 1000, 1000, 2000, 2000, straight=(0, 1)),  # the corner edges
                       sq("C", 0, 0, 5000, 5000, holes=[{"layer": "ymtp_mk"}])],  # context: wavy
            "measures": [],
            "constraints": [{"kind": "match", "expr": "A.layer == poly2"},
                            {"kind": "match", "expr": "C.layer == comp"},
                            {"kind": "check", "expr": "a1.corner != 90"}]}
    drc = get("klayout", layermap={"poly2": (30, 0), "comp": (22, 0),
                                   "ymtp_mk": (86, 17)}).compile(Rule(rule))
    line = next(ln for ln in drc.splitlines() if "corners" in ln).split("=", 1)[1].strip()
    assert line == ("poly2.corners(90).sized(0.1).or(poly2.corners(-90).sized(0.1))"
                    ".not(ymtp_mk).inside(comp.not(ymtp_mk))")


def test_corner_magnitude_is_winding_independent():
    # CCW vs CW draw of the same square -> same corner rule (sign incidental)
    ccw = _square("A", "comp")
    cw = _square("A", "comp")
    cw["shapes"][0]["edges"] = list(reversed(cw["shapes"][0]["edges"]))
    a = get("klayout", layermap={"comp": (22, 0)}).compile(Rule(ccw))
    b = get("klayout", layermap={"comp": (22, 0)}).compile(Rule(cw))
    ca = next(ln for ln in a.splitlines() if "corners" in ln).split("=", 1)[1].strip()
    cb = next(ln for ln in b.splitlines() if "corners" in ln).split("=", 1)[1].strip()
    assert ca == cb


def _length_rule(kind, expr, layer="comp"):
    return Rule({"name": "x", "desc": "x",
                 "shapes": [{"name": "A", "edges": [
                     edge("a0", 0, 0, 300, 0),    # the length-constrained edge -> straight
                     edge("a1", 300, 0, 300, 500, straight=False),
                     edge("a2", 300, 500, 0, 500, straight=False),
                     edge("a3", 0, 500, 0, 0, straight=False)]}],
                 "measures": [],
                 "constraints": [{"kind": "match", "expr": f"A.layer == {layer}"},
                                 {"kind": kind, "expr": expr}]})


def _length_op(r, layer="comp"):
    drc = get("klayout", layermap={layer: (22, 0)}).compile(r)
    return next(ln for ln in drc.splitlines() if "_length" in ln).split("=", 1)[1].strip()


def test_edge_length_filter_selects_what_matches():
    # a MATCH is a filter: the edges that satisfy it are the ones selected (and, with
    # no check in the rule, flagged).
    import fastdrc.topology as _t
    assert _t.edge_length(edge("h", 0, 0, 300, 0)) == 300.0
    r = _length_rule("match", "a0.length <= 0.34")
    assert r.length_bounds() == [("a0", "with", None, 0.34)]
    assert r.edge_length("a0") == 300.0
    assert _length_op(r) == "comp.edges.with_length(nil, 0.34.um)"
    assert _length_op(_length_rule("match", "a0.length == 0.22")) == "comp.edges.with_length(0.22.um)"
    assert _length_op(_length_rule("match", "a0.length >= 0.1 and a0.length <= 0.5")) == \
        "comp.edges.with_length(0.1.um, 0.5.um)"
    assert _length_op(_length_rule("match", "a0.length != 0.22")) == "comp.edges.without_length(0.22.um)"


def test_edge_length_check_flags_what_fails():
    # a CHECK is the assertion that must hold; the edges that FAIL it are flagged --
    # the same reading as `W >= 0.28` (flags the narrower) or `A.area >= v`.
    r = _length_rule("check", "a0.length == 0.22", "contact")          # CO.1: exact size
    assert r.length_bounds() == [("a0", "without", 0.22, 0.22)]
    assert _length_op(r, "contact") == "contact.edges.without_length(0.22.um)"
    assert _length_op(_length_rule("check", "a0.length != 0.22")) == "comp.edges.with_length(0.22.um)"
    assert _length_op(_length_rule("check", "a0.length >= 0.34")) == "comp.edges.with_length(nil, 0.34.um)"
    assert _length_op(_length_rule("check", "a0.length <= 0.5")) == "comp.edges.with_length(0.5.um, nil)"
    assert _length_op(_length_rule("check", "a0.length >= 0.1 and a0.length <= 0.5")) == \
        "comp.edges.without_length(0.1.um, 0.5.um)"


def _corner_rule(kind, expr):
    r = _square("A", "comp")
    r["constraints"][-1] = {"kind": kind, "expr": expr}
    return Rule(r)


def _corner_line(r, word="corners"):
    drc = get("klayout", layermap={"comp": (22, 0)}).compile(r)
    return next(ln for ln in drc.splitlines() if word in ln).split("=", 1)[1].strip()


def test_corner_kind_decides_the_reading():
    right = "comp.corners(90).sized(0.1).or(comp.corners(-90).sized(0.1))"
    # a FILTER selects the corners that satisfy it; a CHECK flags the ones that fail it
    assert _corner_line(_corner_rule("match", "a1.corner == 90")) == right
    assert _corner_line(_corner_rule("check", "a1.corner != 90")) == right
    assert _corner_line(_corner_rule("check", "a1.corner == 90")) == (
        "comp.corners.sized(0.1).not(comp.corners(90).sized(0.1))"
        ".not(comp.corners(-90).sized(0.1))")
    # the angle is the one BETWEEN the edges: a 45-degree corner is a 135-degree turn
    assert _corner_line(_corner_rule("match", "a1.corner == 45")) == (
        "comp.corners(135).sized(0.1).or(comp.corners(-135).sized(0.1))")


def test_acute_corner_rule_is_an_ordinary_check():
    # "no acute angles": every corner must be at least 45 degrees -> the sharper ones
    # fail -> the deck's comp.with_angle(0 .. 45), which selects CORNERS by the angle
    # their two edges enclose.
    r = _corner_rule("check", "a1.corner >= 45")
    assert r.corner_ranges() == [("a1", 0.0, 45)]
    assert _corner_line(r, "with_angle") == "comp.with_angle(0 .. 45)"
    assert _corner_line(_corner_rule("match", "a1.corner > 0 and a1.corner < 45"),
                        "with_angle") == "comp.with_angle(0 .. 45)"
    import fastdrc.backend_docs  # noqa: F401
    en = get("docs").compile(r)
    assert "every comp corner must be at least 45°" in en


def test_max_corner_check_does_not_flag_the_limit_itself():
    # `corner <= 90` holds for a right angle; only the wider corners fail it
    assert _corner_rule("check", "a1.corner <= 90").corner_ranges() == [("a1", 90.001, 180.0)]
