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


def test_touching_example_is_interacting():
    # plfuse abutting the wire's top edge (coinciding edges) -> relation "touch".
    # The DRAWN relation is the signal: a touch -> `interacting` (touch counts),
    # NOT `outside`. `outside` is drawn APART (see test_apart_example_is_outside).
    line = _sel_op(_outside_rule(0, 3000, 150, 4000))
    assert ".interacting(plfuse)" in line and ".outside(" not in line


def test_apart_example_is_outside():
    # drawn disjoint (a gap) with a wavy >= 0 measure -> `outside` (touch allowed,
    # but drawn apart). Disjoint, not touch, is what makes it `outside`.
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


def test_lookup_sel_truth_table():
    # the selection op is read off the (kept, dropped) relations of labeled
    # instances; a negation is a row whose excluded set is a single relation.
    from fastdrc.rule import lookup_sel as _lookup_sel   # now lives on the Rule surface
    # not_inside: inside dropped, disjoint+overlap kept (the discriminating case)
    assert _lookup_sel({"disjoint", "overlap"}, {"inside"}) == "not_inside"
    # inside dropped + disjoint kept ALONE is still ambiguous (outside vs not_inside);
    # the kept overlap is what pins not_inside over outside
    assert _lookup_sel({"disjoint"}, {"inside"}) in ("outside", "not_inside")
    assert _lookup_sel({"disjoint", "overlap"}, set()) is None or True  # no carve-out


def test_not_inside_from_labeled_instances():
    # comp width, but NOT the comp wholly inside mvsd (comp.not_inside(mvsd)). Drawn
    # as three same-layer comp instances vs the mvsd context: A outside (flagged,
    # carries the width check), B3 straddling (flagged), B1 inside (NOT flagged --
    # the carve-out). The truth table reads inside-dropped + disjoint/overlap-kept.
    lm = {"comp": (22, 0), "mvsd": (210, 0)}
    A = {"name": "A", "edges": _edges("a", 0, 0, 150, 3000, {1, 3})}
    Y = {"name": "Y", "edges": _edges("y", 1000, 0, 3000, 2000, set())}
    B3 = {"name": "B3", "edges": _edges("b3", 900, 500, 1200, 700, set())}      # straddle
    B1 = {"name": "B1", "edges": _edges("b1", 1400, 500, 1600, 700, set()),
          "flagged": False}                                                     # inside, carve-out
    rule = {"name": "DF.like", "desc": "comp width not inside mvsd",
            "shapes": [A, Y, B3, B1],
            "measures": [{"name": "W", "from": "a3", "to": "a1"},
                         {"name": "GA", "from": "a2", "to": "y0"},
                         {"name": "GB3", "from": "b30", "to": "y1"},
                         {"name": "GB1", "from": "b10", "to": "y2"}],
            "constraints": [{"kind": "match", "expr": "A.layer == comp"},
                            {"kind": "match", "expr": "Y.layer == mvsd"},
                            {"kind": "match", "expr": "B3.layer == comp"},
                            {"kind": "match", "expr": "B1.layer == comp"},
                            {"kind": "check", "expr": "W >= 0.3"},
                            {"kind": "match", "expr": "GA >= 0"},
                            {"kind": "match", "expr": "GB3 >= 0"},
                            {"kind": "match", "expr": "GB1 >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if ".width(" in ln).split("=", 1)[1].strip()
    assert line == "comp.not_inside(mvsd).width(0.3.um, euclidian).polygons(0.001)"


def test_arg_not_interacting_from_labeled_instances():
    # esd.separation(comp.not_interacting(mvsd)): a SELECT-FIRST restriction on the
    # ARG (the comp diffusion that is NOT near mvsd), drawn as two arrangements of
    # the arg vs the mvsd marker -- B2 disjoint (measured, flagged), B1 TOUCHING
    # (not flagged). The touch is the discriminator: a dropped touch -> not_interacting
    # (strict), not outside. The labeled-instances reading already runs on the arg.
    lm = {"esd": (216, 0), "comp": (22, 0), "mvsd": (210, 0)}
    A = {"name": "A", "edges": _edges("a", 0, 0, 4, 10, {1})}
    B2 = {"name": "B2", "edges": _edges("b", 10, 0, 14, 10, {3})}              # arg, measured
    M = {"name": "M", "edges": _edges("m", 10, 14, 14, 18, set())}            # mvsd marker
    B1 = {"name": "B1", "edges": _edges("n", 14, 14, 18, 18, set()),          # left edge = M right edge
          "flagged": False}                                                   # TOUCH, the carve-out
    rule = {"name": "x", "desc": "esd sep to non-mvsd comp", "shapes": [A, B2, M, B1],
            "measures": [{"name": "S", "from": "a1", "to": "b3"},
                         {"name": "GB2", "from": "b2", "to": "m0"},
                         {"name": "GB1", "from": "n0", "to": "m1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == esd"},
                            {"kind": "match", "expr": "B2.layer == comp"},
                            {"kind": "match", "expr": "M.layer == mvsd"},
                            {"kind": "match", "expr": "B1.layer == comp"},
                            {"kind": "check", "expr": "S >= 0.6"},
                            {"kind": "match", "expr": "GB2 >= 0"},
                            {"kind": "match", "expr": "GB1 >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if ".separation(" in ln).split("=", 1)[1].strip()
    assert line == "esd.separation(comp.not_interacting(mvsd), 0.6.um, euclidian).polygons(0.001)"


def test_derived_marker_from_overlapping_layers():
    # esd.separation(comp.not_interacting(tgate)) where tgate = poly2 & comp (a GATE,
    # no mask of its own). The gate is drawn as two OVERLAPPING shapes P(poly2)+Q(comp);
    # the compiler ANDs them into the marker -> not_interacting(poly2.and(comp)) = tgate.
    # Only triggers with counter-examples present (B1 touches the gate, not flagged).
    lm = {"esd": (216, 0), "comp": (22, 0), "poly2": (30, 0)}
    A = {"name": "A", "edges": _edges("a", 0, 0, 4, 10, {1})}
    B2 = {"name": "B2", "edges": _edges("b", 10, 0, 14, 10, {3})}             # arg, measured, gate-free
    P = {"name": "P", "edges": _edges("p", 20, 2, 24, 8, set())}             # poly2
    Q = {"name": "Q", "edges": _edges("q", 22, 0, 26, 10, set())}            # comp -> P&Q = tgate
    B1 = {"name": "B1", "edges": _edges("n", 24, 2, 28, 8, set()),           # left edge = P right (TOUCH)
          "flagged": False}
    rule = {"name": "x", "desc": "esd sep to non-gate comp", "shapes": [A, B2, P, Q, B1],
            "measures": [{"name": "S", "from": "a1", "to": "b3"},
                         {"name": "GB2", "from": "b2", "to": "p0"},
                         {"name": "GB1", "from": "n0", "to": "p1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == esd"},
                            {"kind": "match", "expr": "B2.layer == comp"},
                            {"kind": "match", "expr": "P.layer == poly2"},
                            {"kind": "match", "expr": "Q.layer == comp"},
                            {"kind": "match", "expr": "B1.layer == comp"},
                            {"kind": "check", "expr": "S >= 0.6"},
                            {"kind": "match", "expr": "GB2 >= 0"},
                            {"kind": "match", "expr": "GB1 >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if ".separation(" in ln).split("=", 1)[1].strip()
    assert line == "esd.separation(comp.not_interacting(poly2.and(comp)), 0.6.um, euclidian).polygons(0.001)"


def test_df4b_full_derived_arg_and_derived_marker():
    # The complete DF.4b: dnwell.enclosing(ncomp.not_interacting(tgate)), where the
    # ARG is derived (ncomp = comp & nplus) AND the marker is derived (tgate = poly2
    # & comp). Two arrangements of the ncomp arg vs a gate: B2 gate-free (measured),
    # B1 touching the gate (contrast). The contrast's doping (C1) must be consumed
    # with the contrast so it doesn't leak as a scope.
    lm = {"dnwell": (12, 0), "comp": (22, 0), "nplus": (32, 0), "poly2": (30, 0)}
    A = {"name": "A", "edges": _edges("a", 0, 0, 40, 20, {3})}
    B2 = {"name": "B2", "edges": _edges("b", 2, 2, 6, 6, {3})}
    C2 = {"name": "C2", "edges": _edges("c", 1, 1, 7, 7, set())}             # nplus -> ncomp
    P = {"name": "P", "edges": _edges("p", 20, 2, 24, 8, set())}            # poly2
    Q = {"name": "Q", "edges": _edges("q", 22, 0, 26, 10, set())}           # comp -> P&Q = tgate
    B1 = {"name": "B1", "edges": _edges("d", 24, 2, 28, 8, set()), "flagged": False}
    C1 = {"name": "C1", "edges": _edges("e", 24, 1, 29, 9, set())}          # contrast's nplus doping
    rule = {"name": "DF.4b", "desc": "dnwell enclose ncomp not gate",
            "shapes": [A, B2, C2, P, Q, B1, C1],
            "measures": [{"name": "E", "from": "a3", "to": "b3"},
                         {"name": "GB2", "from": "b2", "to": "p0"},
                         {"name": "GB1", "from": "d0", "to": "p1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == dnwell"},
                            {"kind": "match", "expr": "B2.layer == comp"},
                            {"kind": "match", "expr": "C2.layer == nplus"},
                            {"kind": "match", "expr": "P.layer == poly2"},
                            {"kind": "match", "expr": "Q.layer == comp"},
                            {"kind": "match", "expr": "B1.layer == comp"},
                            {"kind": "match", "expr": "C1.layer == nplus"},
                            {"kind": "check", "expr": "E >= 0.62"},
                            {"kind": "match", "expr": "GB2 >= 0"},
                            {"kind": "match", "expr": "GB1 >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if ".enclosing(" in ln).split("=", 1)[1].strip()
    assert line == ("dnwell.enclosing(comp.and(nplus).not_interacting(poly2.and(comp)), "
                    "0.62.um, euclidian).polygons(0.001)")


def _cross_line(shapes, measures, lm):
    rule = {"name": "x", "desc": "d", "shapes": shapes, "measures": measures,
            "constraints": [{"kind": "match", "expr": "A.layer == lvpwell"},
                            {"kind": "match", "expr": "B.layer == nwell"}]}
    return next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if "lvpwell" in ln and "polygons(" not in ln).split("=", 1)[1].strip()


def test_selected_marker():
    # DF.3b-class: a selection whose MARKER is itself a derived+selected device --
    # not_outside(pcomp.inside(nwell)) = "overlaps a P+ diffusion that's in the well".
    # The marker P (comp) is doped by CP (pplus) -> pcomp, and sits inside well W.
    # Doping = a different-layer shape WHOLLY containing the marker that isn't a
    # measure endpoint; the marker's own context (the shared well) adds .inside(nwell).
    lm = {"comp": (22, 0), "nplus": (32, 0), "pplus": (31, 0), "nwell": (21, 0)}
    A = {"name": "A", "edges": _edges("a", 2, 2, 8, 6, set())}              # N+ subject
    CA = {"name": "CA", "edges": _edges("ca", 1, 1, 9, 7, set())}           # nplus -> ncomp
    P = {"name": "P", "edges": _edges("p", 6, 2, 12, 6, set())}             # P+ marker (overlaps A)
    CP = {"name": "CP", "edges": _edges("cp", 5, 1, 13, 7, set())}          # pplus -> pcomp
    W = {"name": "W", "edges": _edges("w", 0, 0, 16, 8, set())}             # nwell (contains both)
    rule = {"name": "DF.3b", "desc": "d", "shapes": [A, CA, P, CP, W],
            "measures": [{"name": "GAW", "from": "a3", "to": "w0"},          # A inside W
                         {"name": "GPW", "from": "p1", "to": "w2"},          # P inside W
                         {"name": "GAP", "from": "a1", "to": "p3"},          # A overlaps P
                         {"name": "X", "from": "a0", "to": "a2", "crossed": True}],
            "constraints": [{"kind": "match", "expr": "A.layer == comp"},
                            {"kind": "match", "expr": "CA.layer == nplus"},
                            {"kind": "match", "expr": "P.layer == comp"},
                            {"kind": "match", "expr": "CP.layer == pplus"},
                            {"kind": "match", "expr": "W.layer == nwell"},
                            {"kind": "match", "expr": "GAW >= 0"},
                            {"kind": "match", "expr": "GPW >= 0"},
                            {"kind": "match", "expr": "GAP >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if "comp" in ln and "polygons(" not in ln.split("=")[1][:14]).split("=", 1)[1].strip()
    # subject ncomp.inside(nwell); marker is the SELECTED device pcomp.inside(nwell)
    assert "comp.and(nplus).inside(nwell)" in line
    assert "comp.and(pplus).inside(nwell)" in line   # the selected marker


def test_nested_selection_marker():
    # MIM.10-class: a SELECTION whose marker carries its OWN selection --
    # via1.interacting(metal2.interacting(fusetop)) = "a via1 touching a metal2 that
    # itself touches fusetop". The marker S (metal2) gets its own context F (fusetop)
    # via a wavy measure S->F; the backend reads the nested op back onto the marker.
    lm = {"via1": (51, 0), "metal2": (36, 0), "fusetop": (75, 0)}
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 4, set())}             # via1 subject
    S = {"name": "S", "edges": _edges("s", 10, 0, 16, 4, set())}            # metal2 marker (touches A)
    F = {"name": "F", "edges": _edges("f", 16, 0, 20, 4, set())}            # fusetop (touches S)
    rule = {"name": "MIM.10", "desc": "d", "shapes": [A, S, F],
            "measures": [{"name": "GAS", "from": "a0", "to": "s0"},          # A interacting S
                         {"name": "GSF", "from": "s0", "to": "f0"},          # S interacting F
                         {"name": "X", "from": "a0", "to": "a1", "crossed": True}],
            "constraints": [{"kind": "match", "expr": "A.layer == via1"},
                            {"kind": "match", "expr": "S.layer == metal2"},
                            {"kind": "match", "expr": "F.layer == fusetop"},
                            {"kind": "match", "expr": "GAS >= 0"},
                            {"kind": "match", "expr": "GSF >= 0"}]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if "via1" in ln and "polygons(" not in ln.split("=")[1][:14]).split("=", 1)[1].strip()
    assert "via1.interacting(metal2.interacting(fusetop))" in line


def test_cross_presence_violation():
    # A CROSS = a measure whose bound is "never": the derived REGION is itself the
    # violation (presence), output raw -- no dimensional op. It's a selection whose
    # selected set is OUTPUT, so the crossed config's RELATION picks the op, exactly
    # like a normal selection.
    lm = {"lvpwell": (204, 0), "nwell": (21, 0)}
    A = {"name": "A", "edges": _edges("a", 0, 0, 6, 6, set())}

    # "LVPWELL cannot overlap Nwell" (LPW.12): cross an OVERLAP -> overlapping
    # (== deck's not_outside). Flag the lvpwell that overlaps.
    Bover = {"name": "B", "edges": _edges("b", 3, 3, 9, 9, set())}          # overlaps A
    line = _cross_line([A, Bover], [{"name": "X", "from": "a1", "to": "b3", "crossed": True}], lm)
    assert line == "lvpwell.overlapping(nwell)"

    # "every A must overlap B": cross a DISJOINT config -> outside. Flag the lvpwell
    # that fails to overlap. The relation you draw on the cross is the whole signal.
    Bfar = {"name": "B", "edges": _edges("b", 20, 20, 26, 26, set())}       # disjoint from A
    line = _cross_line([A, Bfar], [{"name": "X", "from": "a1", "to": "b3", "crossed": True}], lm)
    assert line == "lvpwell.outside(nwell)"

    # "A must NOT touch B": slide B until its left edge (b3) COINCIDES with A's right
    # edge (a1) -> they touch -> interacting. The cross now sits ON the shared edge.
    Btouch = {"name": "B", "edges": _edges("b", 6, 0, 12, 6, set())}        # b3 (x=6) == a1 (x=6)
    line = _cross_line([A, Btouch], [{"name": "X", "from": "a1", "to": "b3", "crossed": True}], lm)
    assert line == "lvpwell.interacting(nwell)"


def test_or_of_crossed_variants():
    # OR-of-variants where each variant is a CROSSED (presence) case: a rule with
    # several forbidden configurations (e.g. DF.3b butted comp = overlap OR touch),
    # each a cross, unioned. _emit_union must gather crossed measures, not just bounds.
    lm = {"comp": (22, 0), "nplus": (32, 0), "pplus": (31, 0)}

    def crossed(layer2, box, pfx):
        A = {"name": pfx + "A", "edges": _edges(pfx + "a", 0, 0, 6, 6, set())}
        B = {"name": pfx + "B", "edges": _edges(pfx + "b", *box, set())}
        return {"shapes": [A, B],
                "measures": [{"name": "GS", "from": pfx + "a2", "to": pfx + "b0"},
                             {"name": "X", "from": pfx + "a0", "to": pfx + "a1", "crossed": True}],
                "constraints": [{"kind": "match", "expr": f"{pfx}A.layer == comp"},
                                {"kind": "match", "expr": f"{pfx}B.layer == {layer2}"},
                                {"kind": "match", "expr": "GS >= 0"}]}
    rule = {"name": "DF.3b", "desc": "d",
            "or": [crossed("pplus", (3, 3, 9, 9), "u"), crossed("nplus", (6, 0, 12, 6), "v")]}
    line = next(ln for ln in get("klayout", layermap=lm).compile(Rule(rule)).splitlines()
                if ".or(" in ln).split("=", 1)[1].strip()
    assert line == "comp.overlapping(pplus).or(comp.interacting(nplus))"


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
    # A width bound written as a FILTER (match W <= V) is a narrow-region SELECTOR ->
    # layer.drc(width <= V), distinct from the check W >= V. Entry to
    # KLayout's .drc() predicate DSL; composes with selections via _check_expr.
    A = {"name": "A", "edges": _edges("a", 0, 0, 300, 5000, {1, 3})}
    rule = {"name": "narrow", "desc": "narrow metal1",
            "shapes": [A], "measures": [{"name": "W", "from": "a3", "to": "a1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                            {"kind": "match", "expr": "W <= 0.34"}]}
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
                          {"kind": "match", "expr": "S <= 0.3"}]}
    line = next(ln for ln in get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(sp)).splitlines()
                if ".drc(" in ln).split("=", 1)[1].strip()
    assert line == "metal1.drc(space <= 0.3.um)"

    C = {"name": "C", "edges": _edges("c", 1200, 0, 2200, 1000, {3})}
    sep = {"name": "x", "desc": "x", "shapes": [A, C],
           "measures": [{"name": "S", "from": "a1", "to": "c3"}],
           "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                           {"kind": "match", "expr": "C.layer == comp"},
                           {"kind": "match", "expr": "S <= 0.2"}]}
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
                                {"kind": "match", "expr": expr}]}
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
                            {"kind": "match", "expr": "W >= 0.1 and W <= 0.5"}]}
    line = next(ln for ln in get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(rule)).splitlines()
                if ".drc(" in ln).split("=", 1)[1].strip()
    assert line == "metal1.drc(0.1.um <= width <= 0.5.um)"


def test_min_and_max_area():
    def rect(name, layer, expr):
        pts = [(0, 0), (10, 0), (10, 4), (0, 4)]
        e = [{"id": f"a{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
              "edge": False} for i in range(4)]
        return Rule({"name": "x", "desc": "x", "shapes": [{"name": "A", "edges": e}],
                     "measures": [], "constraints": [{"kind": "match", "expr": f"A.layer == {layer}"},
                                                     {"kind": "check", "expr": expr}]})
    g = get("klayout", layermap={"fusetop": (75, 0)})
    # MIN area (area >= v) flags the too-SMALL polys -> with_area(nil, v)
    line = next(ln for ln in g.compile(rect("A", "fusetop", "A.area >= 0.2")).splitlines()
                if "with_area" in ln)
    assert "with_area(nil, 0.2.um)" in line
    # MAX area (area <= v) flags the too-LARGE polys -> with_area(v, nil)
    line = next(ln for ln in g.compile(rect("A", "fusetop", "A.area <= 0.2")).splitlines()
                if "with_area" in ln)
    assert "with_area(0.2.um, nil)" in line


def test_relation_link_needs_no_bound_and_layer_set_removes_each_member():
    # A link drawn to a wavy edge states the relation as drawn -- no `G >= 0` needed.
    # One cutout whose layer is a SET removes every member layer.
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 4, {1, 3})}
    X = {"name": "X", "edges": _edges("x", 4.7, 0.7, 5.3, 1.3, {0})}     # cutout off the measure
    P = {"name": "P", "edges": _edges("p", 14, 0, 20, 4, set())}         # drawn apart
    rule = {"name": "PL.1", "desc": "d", "shapes": [A, X, P],
            "measures": [{"name": "W", "from": "a1", "to": "a3"},
                         {"name": "G", "from": "a0", "to": "p0"}],        # the link: no constraint
            "constraints": [{"kind": "match", "expr": "A.layer == poly2"},
                            {"kind": "match", "expr": "X.layer in {ymtp_mk, otp_mk}"},
                            {"kind": "match", "expr": "P.layer == plfuse"},
                            {"kind": "check", "expr": "W >= 0.18"}]}
    lm = {"poly2": (30, 0), "ymtp_mk": (86, 17), "otp_mk": (173, 5), "plfuse": (125, 5)}
    drc = get("klayout", layermap=lm).compile(Rule(rule))
    assert ("poly2.not(otp_mk).not(ymtp_mk).outside(plfuse).width(0.18.um, euclidian)"
            ".polygons(0.001)") in drc
    en = get("docs").compile(Rule(rule))
    assert "poly2 (excluding otp_mk and ymtp_mk) that lies outside plfuse must be at least 0.18" in en


def test_layer_set_as_a_context_is_the_union():
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 4, {1, 3})}
    M = {"name": "M", "edges": _edges("m", 13, 3, 19, 7, set())}         # unlinked, apart: scope
    rule = {"name": "R", "desc": "d", "shapes": [A, M],
            "measures": [{"name": "W", "from": "a1", "to": "a3"}],
            "constraints": [{"kind": "match", "expr": "A.layer == comp"},
                            {"kind": "match", "expr": "M.layer in {dualgate, v5_xtor}"},
                            {"kind": "check", "expr": "W >= 0.22"}]}
    lm = {"comp": (22, 0), "dualgate": (55, 0), "v5_xtor": (112, 1)}
    drc = get("klayout", layermap=lm).compile(Rule(rule))
    assert "comp.width(0.22.um, euclidian).polygons(0.001).not_interacting(dualgate.or(v5_xtor))" in drc
    assert "away from dualgate or v5_xtor" in get("docs").compile(Rule(rule))


def test_distance_bound_kind_decides_the_reading():
    # check = what must hold (flag where it fails); match = a filter (select what fits)
    A = {"name": "A", "edges": _edges("a", 0, 0, 300, 5000, {1, 3})}

    def line(kind, expr):
        rule = {"name": "x", "desc": "x", "shapes": [A],
                "measures": [{"name": "W", "from": "a3", "to": "a1"}],
                "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                                {"kind": kind, "expr": expr}]}
        r = Rule(rule)
        drc = get("klayout", layermap={"metal1": (34, 0)}).compile(r)
        ops = [ln.split("=", 1)[1].strip() for ln in drc.splitlines() if "_l1 =" in ln]
        import fastdrc.backend_docs  # noqa: F401
        return ops, get("docs").compile(r)

    ops, en = line("check", "W >= 0.34")                 # min width
    assert ops == ["metal1.width(0.34.um, euclidian).polygons(0.001)"]
    ops, en = line("check", "W <= 5")                    # MAX width: the wider ones fail
    assert ops == ["metal1.drc(width > 5.um)"] and "metal1 must be at most 5 µm wide" in en
    ops, en = line("check", "W >= 0.34 and W <= 5")      # both must hold: two checks
    assert ops == ["metal1.width(0.34.um, euclidian).polygons(0.001)", "metal1.drc(width > 5.um)"]
    ops, en = line("match", "W <= 0.34")                 # filter: the narrow regions
    assert ops == ["metal1.drc(width <= 0.34.um)"] and "selects metal1 regions where width ≤ 0.34" in en
    ops, en = line("match", "W >= 2")                    # filter: the wide regions
    assert ops == ["metal1.drc(width >= 2.um)"]
