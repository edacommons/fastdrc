"""Regression tests for the `as_drawn` corpus generator (tools/validate_klayout.py).

as_drawn turns a parsed deck rule into the geometry-by-example IR a user would
draw. These guard the drawing GEOMETRY (not the backend) -- specifically that
subtracted (`.not`) layers are drawn as cutouts that always fit INSIDE the host
shape, regardless of how many there are.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_klayout  # noqa: E402,F401  (registers "klayout")
from fastdrc.backend import get  # noqa: E402
from fastdrc.rule import Rule    # noqa: E402
from validate_klayout import as_drawn, our_line, _decompose_sel, parse_subject  # noqa: E402

LM = {"poly2": (30, 0), "comp": (22, 0),
      **{f"m{i}": (100 + i, 0) for i in range(1, 9)}}


def _sep_rule(n):
    hs = [f"m{i}" for i in range(1, n + 1)]
    return {"id": "T", "desc": "t", "op": "separation", "base": "poly2",
            "covers": [], "holes": hs, "arg": ["comp"], "arg_covers": [],
            "arg_holes": hs, "value": "0.1", "mode": "euclidian", "scope": [],
            "selections": [], "arg_selections": [], "area_op": ">="}


def _op(r):
    return our_line(get("klayout", layermap=LM).compile(Rule(as_drawn(r)))).split("=", 1)[1].strip()


def test_many_subtractions_dont_leak_to_scope():
    # the narrow (4-wide) separation subject once overflowed with >= 4 cutouts at
    # fixed +1 offsets -- the overflowing one wasn't `inside`, so it leaked into a
    # scope op instead of a `.not`. Cutouts now distribute along the longer axis.
    for n in (2, 3, 4, 6, 8):
        op = _op(_sep_rule(n))
        nots = "".join(f".not(m{i})" for i in range(1, n + 1))
        assert op == f"poly2{nots}.separation(comp{nots}, 0.1.um, euclidian).polygons(0.001)", \
            f"n={n}: {op}"
        assert ".interacting(" not in op and ".overlapping(" not in op  # no scope leak


def test_gate_width_edge_rule():
    # PL.2-class gate width: poly width over the gate. The structure is RECOVERED from
    # the drawn geometry -- the GATE active layer crosses the subject (drawn as an
    # overlap -> relation 'overlap'), a mask FILTER contains it (a covering -> relation
    # 'inside' -> .and(M)), a cutout is removed (-> .not(M)). Nothing is stashed.
    r = {"id": "PL.2", "desc": "d", "op": "edge_width", "base": "poly2", "gate": ["comp"],
         "covers": ["m1"], "holes": ["m2"], "arg": [], "value": "0.28", "mode": "euclidian",
         "scope": [], "selections": [], "arg_selections": [], "arg_covers": [], "arg_holes": [],
         "area_op": ">="}
    assert _op(r) == ("poly2.edges.and(poly2.and(comp).edges).not(m2).and(m1)"
                      ".width(0.28.um, euclidian).polygons(0.001)")


def _edge_rule(kind, lo, hi):
    return {"id": "CO.1", "desc": "d", "op": "edge_length", "base": "poly2",
            "kind": kind, "lo": lo, "hi": hi, "extended": "0, 0, 0.001, 0.001",
            "covers": [], "holes": [], "arg": [], "value": "", "mode": "", "scope": [],
            "selections": [], "arg_selections": [], "arg_covers": [], "arg_holes": [],
            "area_op": ">="}


def test_edge_size_rule_round_trips():
    # CO.1-class: LAYER.edges.(with|without)_length(L).extended(...) -- min/max
    # contact/via size; the flagged EDGES (rendered as thin polygons) are the
    # violation. The validator's first .edges round-trip.
    assert _op(_edge_rule("without", "0.22", "0.22")) == \
        "poly2.edges.without_length(0.22.um).extended(0, 0, 0.001, 0.001)"
    assert _op(_edge_rule("with", None, "0.34")) == \
        "poly2.edges.with_length(nil, 0.34.um).extended(0, 0, 0.001, 0.001)"


def test_inside_and_marker_decomposes():
    # inside(A&B&C) == inside(A).inside(B).inside(C) -- containment is intersective.
    assert sorted(_decompose_sel("inside", "dnwell.and(dualgate).and(ldmos_xtor)")) == \
        [("inside", "dnwell"), ("inside", "dualgate"), ("inside", "ldmos_xtor")]
    # other ops DON'T decompose (outside(A&B) != outside(A).outside(B)); single stays.
    assert _decompose_sel("outside", "a.and(b)") == [("outside", "a.and(b)")]
    assert _decompose_sel("inside", "dnwell") == [("inside", "dnwell")]


def test_parse_subject_ignores_nested_marker_ands():
    # a .and()/.not() buried in a selection marker's parens must NOT be pulled into
    # the subject's AND/NOT set (the MDP.9a / PL.9 latent canon bug).
    ands, nots = parse_subject("poly2.inside(dnwell.and(dualgate).and(ldmos_xtor))")
    assert ands == frozenset({"poly2"}) and nots == frozenset()
    ands, nots = parse_subject("poly2.interacting(poly2.not(v5_xtor).not(dualgate))")
    assert ands == frozenset({"poly2"}) and nots == frozenset()
    # top-level AND/NOT still captured
    ands, nots = parse_subject("comp.and(nplus).not(otp_mk)")
    assert ands == frozenset({"comp", "nplus"}) and nots == frozenset({"otp_mk"})


def test_or_union_roundtrip_via_synthetic_deck():
    # OR-of-variants: a deck `final = caseA.or(caseB)` whose operands are separately
    # DEFINED vars. parse_rules resolves each operand to its recognized sub-rule,
    # builds an `or` rule; as_drawn -> a {or:[...]} scene; the backend unions them.
    # We feed a tiny synthetic deck through the REAL parse/as_drawn/compile/canon
    # path and assert canon(ours) == canon(the deck union line).
    from validate_klayout import parse_layermap, parse_rules, canon
    deck = "\n".join([
        "layer_a = polygons(100, 0)",   # (layermap lines are parsed by parse_layermap)
        "wa = poly2.width(0.23.um, euclidian).polygons(0.001)",
        "wb = comp.width(0.3.um, euclidian).polygons(0.001)",
        "u_l1 = wa.or(wb)",
        'u_l1.output("U.1", "poly2 OR comp too narrow")',
    ])
    lm = {"poly2": (30, 0), "comp": (22, 0)}
    # parse_layermap needs the real header form; inject lm directly via parse_rules
    import validate_klayout as vk
    rules, _ = vk.parse_rules(deck, lm)
    u = next(r for r in rules if r["id"] == "U.1")
    assert u["op"] == "or" and [v["op"] for v in u["variants"]] == ["width", "width"]
    drc = get("klayout", layermap=lm).compile(Rule(as_drawn(u)))
    ours = our_line(drc)
    assert ".or(" in ours
    # the union round-trips against the deck line, order-insensitively
    assert canon(ours) == canon(u["line"])
    swapped = "z = comp.width(0.3.um, euclidian).polygons(0.001).or(poly2.width(0.23.um, euclidian).polygons(0.001))"
    assert canon(ours) == canon(swapped)


def test_or_defers_when_an_operand_is_unsupported():
    # if ANY operand can't be recognized, the whole union stays deferred (no partial
    # / wrong emission) -- the no-shortcuts discipline.
    from validate_klayout import parse_rules
    deck = "\n".join([
        "wa = poly2.width(0.23.um, euclidian).polygons(0.001)",
        "wb = comp.some_unhandled_op(0.3.um).polygons(0.001)",
        "u_l1 = wa.or(wb)",
        'u_l1.output("U.2", "deferred")',
    ])
    rules, skip = parse_rules(deck, {"poly2": (30, 0), "comp": (22, 0)})
    assert not any(r["id"] == "U.2" for r in rules)
    assert skip["or_unresolved"] >= 1
