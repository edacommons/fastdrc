"""Wavy-edge measures as topological SELECTIONS (outside / inside).

A measure across STRAIGHT edges is a real distance (a dimensional check). A
measure across WAVY edges is not a distance but a topological assertion: a `>=`
bound means the two shapes don't overlap but may TOUCH -> a subject SELECTION
(KLayout `outside`/`inside`, which keep touching polygons), as opposed to a
fully-separate no-measure shape, which is a `not_interacting` scope MARKER (drops
touching). This is what distinguishes PL.1's `poly2.outside(plfuse)` from a
voltage `not_interacting(v5_xtor)` -- they are topologically identical otherwise.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_klayout       # noqa: E402,F401  (registers "klayout")
from fastdrc.backend import get      # noqa: E402
from fastdrc.rule import Rule        # noqa: E402

LM = {"poly2": (30, 0), "plfuse": (125, 5)}


def _edges(name, x0, y0, x1, y1, straight):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    n = 4
    return [{"id": f"{name}{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % n]),
             "edge": (i in straight)} for i in range(n)]


def _pl1(g_op=">="):
    # poly2 wire (width on straight right/left edges) + plfuse apart, joined by a
    # WAVY measure G (top of A to bottom of P).
    A = {"name": "A", "edges": _edges("a", 0, 0, 150, 3000, straight={1, 3})}
    P = {"name": "P", "edges": _edges("p", 0, 4000, 150, 5000, straight=set())}
    return {"name": "PL.1core", "desc": "poly2 width outside plfuse",
            "shapes": [A, P],
            "measures": [{"name": "W", "from": "a3", "to": "a1"},
                         {"name": "G", "from": "a2", "to": "p0"}],
            "constraints": [{"kind": "match", "expr": "A.layer == poly2"},
                            {"kind": "match", "expr": "P.layer == plfuse"},
                            {"kind": "check", "expr": "W >= 0.18"},
                            {"kind": "match", "expr": f"G {g_op} 0"}]}


def test_straight_vs_wavy_measure_classification():
    r = Rule(_pl1())
    assert r.measure_is_straight("W") is True        # real width
    assert r.measure_is_straight("G") is False       # topological
    assert r.relation_selections() == [("A", "P")]


def test_wavy_measure_emits_outside_selection():
    drc = get("klayout", layermap=LM).compile(Rule(_pl1()))
    line = next(ln for ln in drc.splitlines() if ".width(" in ln).split("=", 1)[1].strip()
    # the wavy measure is an outside SELECTION on the subject, NOT a separation
    assert line == "poly2.outside(plfuse).width(0.18.um, euclidian).polygons(0.001)"
    assert ".separation(" not in line


def test_wavy_measure_is_not_counted_as_dimensional_check():
    # G must not produce a second (separation) check line -- only the width.
    drc = get("klayout", layermap=LM).compile(Rule(_pl1()))
    checks = [ln for ln in drc.splitlines() if ".output(" in ln]
    assert len(checks) == 1


def _edges3(name, x0, y0, x1, y1, straight):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    n = 4
    return [{"id": f"{name}{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % n]),
             "edge": (i in straight)} for i in range(n)]


def test_inside_selection_is_faithful_not_and():
    # A comp wire inside two context layers, joined by WAVY measures, must emit
    # .inside(ctx) selections -- NOT .and(ctx) -- so straddling polys are excluded
    # (a clipped intersection would false-positive). MDN.15a-style.
    lm = {"comp": (22, 0), "mvsd": (210, 0), "ldmos_xtor": (226, 0)}
    A = {"name": "A", "edges": _edges3("a", 1000, 1000, 1180, 3000, {1, 3})}
    M = {"name": "M", "edges": _edges3("m", 0, 0, 5000, 5000, set())}
    L = {"name": "L", "edges": _edges3("l", 0, 0, 5000, 5000, set())}
    rule = {"name": "x", "desc": "x", "shapes": [A, M, L],
            "measures": [{"name": "W", "from": "a3", "to": "a1"},
                         {"name": "G1", "from": "a2", "to": "m0"},
                         {"name": "G2", "from": "a0", "to": "l0"}],
            "constraints": [{"kind": "match", "expr": "A.layer == comp"},
                            {"kind": "match", "expr": "M.layer == mvsd"},
                            {"kind": "match", "expr": "L.layer == ldmos_xtor"},
                            {"kind": "check", "expr": "W >= 0.22"},
                            {"kind": "match", "expr": "G1 >= 0"},
                            {"kind": "match", "expr": "G2 >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if ".width(" in ln)
    assert ".inside(mvsd)" in line and ".inside(ldmos_xtor)" in line
    assert ".and(" not in line


def test_separation_argument_carries_its_own_selection():
    # The separation ARG (not just the subject) gets a wavy-measure selection:
    # sab.separation(comp.outside(otp_mk)). MDP.4a / SB.3-style derived arg.
    lm = {"sab": (75, 0), "comp": (22, 0), "otp_mk": (173, 5)}
    A = {"name": "A", "edges": _edges3("a", 0, 0, 1000, 1000, {1})}
    B = {"name": "B", "edges": _edges3("b", 1500, 0, 2500, 1000, {3})}
    C = {"name": "C", "edges": _edges3("c", 1500, 2000, 2500, 3000, set())}
    rule = {"name": "x", "desc": "x", "shapes": [A, B, C],
            "measures": [{"name": "S", "from": "a1", "to": "b3"},
                         {"name": "G", "from": "b2", "to": "c0"}],
            "constraints": [{"kind": "match", "expr": "A.layer == sab"},
                            {"kind": "match", "expr": "B.layer == comp"},
                            {"kind": "match", "expr": "C.layer == otp_mk"},
                            {"kind": "check", "expr": "S >= 0.22"},
                            {"kind": "match", "expr": "G >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if "separation" in ln)
    assert "separation(comp.outside(otp_mk)" in line


def _outside_rule(px0, py0, px1, py1):
    # poly2 wire at (0,0)-(150,3000); plfuse drawn at the given box, joined by a
    # WAVY gap measure (>= 0). The example may touch or even overlap; the >=0
    # asserts non-overlap, so a touching example must still be `outside`.
    A = {"name": "A", "edges": _edges("a", 0, 0, 150, 3000, {1, 3})}
    P = {"name": "P", "edges": _edges("p", px0, py0, px1, py1, set())}
    return {"name": "x", "desc": "x", "shapes": [A, P],
            "measures": [{"name": "W", "from": "a3", "to": "a1"},
                         {"name": "G", "from": "a2", "to": "p0"}],
            "constraints": [{"kind": "match", "expr": "A.layer == poly2"},
                            {"kind": "match", "expr": "P.layer == plfuse"},
                            {"kind": "check", "expr": "W >= 0.18"},
                            {"kind": "match", "expr": "G >= 0"}]}


def _sel_op(rule):
    line = next(ln for ln in get("klayout", layermap=LM).compile(Rule(rule)).splitlines()
                if ".width(" in ln)
    return line


def test_touching_example_is_still_outside_not_interacting():
    # plfuse abutting the wire's top edge -> relation "touch", but >= 0 means
    # non-overlap-may-touch, so it must be `outside`, never `interacting`.
    line = _sel_op(_outside_rule(0, 3000, 150, 4000))
    assert ".outside(plfuse)" in line and ".interacting(" not in line


def test_apart_example_is_outside():
    line = _sel_op(_outside_rule(0, 4000, 150, 5000))
    assert ".outside(plfuse)" in line


def test_subject_and_arg_share_a_selection_context():
    # MDP.4a-style: subject AND separation-arg both inside the SAME two contexts.
    # A shared context must be selectable by both (not consumed by the first).
    lm = {"nwell": (21, 0), "dnwell": (12, 0), "dualgate": (55, 0), "ldmos_xtor": (226, 0)}
    A = {"name": "A", "edges": _edges("a", 0, 0, 1000, 1000, {1})}
    B = {"name": "B", "edges": _edges("b", 2000, 0, 3000, 1000, {3})}
    C1 = {"name": "C1", "edges": _edges("c", -1000, -1000, 5000, 5000, set())}
    C2 = {"name": "C2", "edges": _edges("d", -1000, -1000, 5000, 5000, set())}
    rule = {"name": "x", "desc": "x", "shapes": [A, B, C1, C2],
            "measures": [{"name": "S", "from": "a1", "to": "b3"},
                         {"name": "GA1", "from": "a2", "to": "c0"},
                         {"name": "GA2", "from": "a0", "to": "d0"},
                         {"name": "GB1", "from": "b2", "to": "c1"},
                         {"name": "GB2", "from": "b0", "to": "d1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == nwell"},
                            {"kind": "match", "expr": "B.layer == dnwell"},
                            {"kind": "match", "expr": "C1.layer == dualgate"},
                            {"kind": "match", "expr": "C2.layer == ldmos_xtor"},
                            {"kind": "check", "expr": "S >= 2.5"},
                            {"kind": "match", "expr": "GA1 >= 0"},
                            {"kind": "match", "expr": "GA2 >= 0"},
                            {"kind": "match", "expr": "GB1 >= 0"},
                            {"kind": "match", "expr": "GB2 >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if "separation" in ln).split("=", 1)[1].strip()
    assert line.startswith("nwell.inside(dualgate).inside(ldmos_xtor).separation(")
    assert "separation(dnwell.inside(dualgate).inside(ldmos_xtor)" in line


def test_or_of_variants_unions_subrule_checks():
    # An OR rule has alternative sub-rules; their check expressions are unioned
    # into one output (`expr1.or(expr2)`). Matches the deck's varA.or(varB) form.
    lm = {"comp": (22, 0), "poly2": (30, 0)}

    def width_sub(pfx, layer):
        return {"shapes": [{"name": "A", "edges": _edges(pfx, 0, 0, 150, 3000, {1, 3})}],
                "measures": [{"name": "W", "from": f"{pfx}3", "to": f"{pfx}1"}],
                "constraints": [{"kind": "match", "expr": f"A.layer == {layer}"},
                                {"kind": "check", "expr": "W >= 0.22"}]}

    rule = {"name": "ORrule", "desc": "either",
            "or": [width_sub("a", "comp"), width_sub("b", "poly2")]}
    checks = [ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
              if ".output(" not in ln and ".width(" in ln]
    assert len(checks) == 1                                  # single unioned output
    expr = checks[0].split("=", 1)[1].strip()
    assert expr == ("comp.width(0.22.um, euclidian).polygons(0.001)"
                    ".or(poly2.width(0.22.um, euclidian).polygons(0.001))")


def test_drc_width_selector_from_max_bound():
    # A MAX-bound intra-shape width (W <= V) is a narrow-region SELECTOR ->
    # layer.drc(width <= V), distinct from the W >= V violation check. Entry to
    # KLayout's .drc() predicate DSL; composes with selections via _check_expr.
    A = {"name": "A", "edges": _edges("a", 0, 0, 300, 5000, {1, 3})}
    rule = {"name": "narrow", "desc": "narrow metal1",
            "shapes": [A], "measures": [{"name": "W", "from": "a3", "to": "a1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                            {"kind": "check", "expr": "W <= 0.34"}]}
    drc = get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(rule))
    line = next(ln for ln in drc.splitlines() if ".drc(" in ln).split("=", 1)[1].strip()
    assert line == "metal1.drc(width <= 0.34.um)"
    assert ".width(" not in line and ".polygons(" not in line   # drc returns a Region


def test_min_width_still_a_violation_check():
    # The MIN bound is unchanged: W >= V -> .width(V).polygons(0.001).
    A = {"name": "A", "edges": _edges("a", 0, 0, 300, 5000, {1, 3})}
    rule = {"name": "w", "desc": "w",
            "shapes": [A], "measures": [{"name": "W", "from": "a3", "to": "a1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                            {"kind": "check", "expr": "W >= 0.34"}]}
    drc = get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(rule))
    line = next(ln for ln in drc.splitlines() if ".width(" in ln)
    assert ".width(0.34.um, euclidian).polygons(0.001)" in line and ".drc(" not in line


def test_drc_space_and_separation_selectors():
    # MAX bound between two shapes -> drc(space<=V) (same layer) or
    # drc(separation(X)<=V) (different layers) -- narrow-gap selectors.
    A = {"name": "A", "edges": _edges("a", 0, 0, 1000, 1000, {1})}
    B = {"name": "B", "edges": _edges("b", 1200, 0, 2200, 1000, {3})}
    sp = {"name": "x", "desc": "x", "shapes": [A, B],
          "measures": [{"name": "S", "from": "a1", "to": "b3"}],
          "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                          {"kind": "match", "expr": "B.layer == metal1"},
                          {"kind": "check", "expr": "S <= 0.3"}]}
    line = next(ln for ln in get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(sp)).splitlines()
                if ".drc(" in ln).split("=", 1)[1].strip()
    assert line == "metal1.drc(space <= 0.3.um)"

    C = {"name": "C", "edges": _edges("c", 1200, 0, 2200, 1000, {3})}
    sep = {"name": "x", "desc": "x", "shapes": [A, C],
           "measures": [{"name": "S", "from": "a1", "to": "c3"}],
           "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                           {"kind": "match", "expr": "C.layer == comp"},
                           {"kind": "check", "expr": "S <= 0.2"}]}
    line = next(ln for ln in get("klayout", layermap={"metal1": (34, 0), "comp": (22, 0)}).compile(Rule(sep)).splitlines()
                if ".drc(" in ln).split("=", 1)[1].strip()
    assert line == "metal1.drc(separation(comp) <= 0.2.um)"


def test_drc_enclosed_and_range_predicates():
    # MAX bound across an inside relation -> drc(enclosed(outer) <op> V); a
    # two-sided range (min and max on one measure) -> drc(lo <= pred < hi).
    # This is the V1.3d via-enclosure pattern.
    inner = {"name": "A", "edges": _edges("a", 100, 100, 400, 400, {0, 1, 2, 3})}
    outer = {"name": "B", "edges": _edges("b", 0, 0, 500, 500, {0, 1, 2, 3})}
    lm = {"via1": (35, 0), "metal1": (34, 0)}

    def compile_(expr):
        rule = {"name": "x", "desc": "x", "shapes": [inner, outer],
                "measures": [{"name": "S", "from": "a0", "to": "b0"}],
                "constraints": [{"kind": "match", "expr": "A.layer == via1"},
                                {"kind": "match", "expr": "B.layer == metal1"},
                                {"kind": "check", "expr": expr}]}
        return next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                    if ".drc(" in ln).split("=", 1)[1].strip()

    assert compile_("S <= 0.06") == "via1.drc(enclosed(metal1) <= 0.06.um)"
    assert compile_("S >= 0.04 and S < 0.06") == \
        "via1.drc(0.04.um <= enclosed(metal1) < 0.06.um)"


def test_drc_width_range():
    A = {"name": "A", "edges": _edges("a", 0, 0, 300, 5000, {1, 3})}
    rule = {"name": "x", "desc": "x", "shapes": [A],
            "measures": [{"name": "W", "from": "a3", "to": "a1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                            {"kind": "check", "expr": "W >= 0.1 and W <= 0.5"}]}
    line = next(ln for ln in get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(rule)).splitlines()
                if ".drc(" in ln).split("=", 1)[1].strip()
    assert line == "metal1.drc(0.1.um <= width <= 0.5.um)"
