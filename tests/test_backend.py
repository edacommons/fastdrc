"""Tests for the rule-driven backends (no data IR; backends use our functions).

A backend's compile(rule) reads layers from constraints, the threshold from a
measure's bound, and the geometry relation from topology -- then emits its
target. KLayout backend is checked here; the same Rule drives the facts backend.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_klayout       # noqa: E402,F401  (registers "klayout")
from fastdrc.backend import available, get  # noqa: E402
from fastdrc.rule import Rule         # noqa: E402

HERE = os.path.dirname(__file__)


def rect_shape(name, x0, y0, x1, y1, ids):
    verts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    edges = [{"id": ids[i], "start": list(verts[i]),
              "end": list(verts[(i + 1) % 4]), "edge": True} for i in range(4)]
    return {"name": name, "edges": edges}
    # edge order: 0=bottom, 1=right, 2=top, 3=left


def width_rule():
    return {
        "shapes": [rect_shape("A", 0, 0, 10, 4, ["a0", "a1", "a2", "a3"])],
        "measures": [{"name": "W", "from": "a1", "to": "a3"}],   # right<->left = width
        "constraints": [{"kind": "match", "expr": "A.layer == metal5"},
                        {"kind": "check", "expr": "W > 2"}],
    }


def space_rule():
    return {
        "shapes": [rect_shape("A", 0, 0, 4, 10, ["a0", "a1", "a2", "a3"]),
                   rect_shape("B", 6, 0, 10, 10, ["b0", "b1", "b2", "b3"])],
        "measures": [{"name": "S", "from": "a1", "to": "b3"}],   # A right <-> B left (the gap)
        "constraints": [{"kind": "match", "expr": "A.layer == metal5"},
                        {"kind": "match", "expr": "B.layer == metal5"},
                        {"kind": "check", "expr": "S >= 0.28"}],
    }


def enclosure_rule():
    return {
        "shapes": [rect_shape("A", 0, 0, 10, 10, ["a0", "a1", "a2", "a3"]),     # outer
                   rect_shape("B", 3, 3, 7, 7, ["b0", "b1", "b2", "b3"])],      # inner
        "measures": [{"name": "E", "from": "a3", "to": "b3"}],   # outer edge <-> inner edge
        "constraints": [{"kind": "match", "expr": "A.layer == metal2"},
                        {"kind": "match", "expr": "B.layer == via2"},
                        {"kind": "check", "expr": "E > 0.06"}],
    }


def test_width():
    drc = get("klayout").compile(Rule(width_rule()))
    assert 'metal5 = input("metal5")' in drc
    assert "metal5.width(2.um, euclidian).polygons(0.001)" in drc
    assert '.output("W > 2"' in drc


def test_space_same_layer():
    drc = get("klayout").compile(Rule(space_rule()))
    assert "metal5.space(0.28.um, euclidian)" in drc
    assert '.output("S >= 0.28"' in drc


def test_enclosure():
    drc = get("klayout").compile(Rule(enclosure_rule()))
    # A contains B; A=metal2 outer encloses B=via2 inner
    assert "metal2.enclosing(via2, 0.06.um, euclidian)" in drc
    assert '.output("E > 0.06"' in drc


def test_layermap():
    drc = get("klayout", layermap={"metal5": (81, 0)}).compile(Rule(width_rule()))
    assert "metal5 = polygons(81, 0)" in drc


def test_same_rule_two_backends():
    rule = Rule(enclosure_rule())
    assert "klayout" in available() and "facts" in available()
    assert "enclosing" in get("klayout").compile(rule)
    facts = get("facts").compile(rule)
    assert "A contains B" in facts and "via2" in facts


def test_real_rule1():
    # the actual editor test file: shape A (met5), measure X between e3 (right)
    # and e5 (left) -> same shape -> a width check; constraint X > 2.
    rule = Rule(json.load(open(os.path.join(HERE, "..", "rule1.json"))))
    drc = get("klayout").compile(rule)
    assert 'met5 = input("met5")' in drc
    assert "met5.width(2.um, euclidian)" in drc
    assert '.output("X > 2"' in drc


if __name__ == "__main__":
    for fn in (test_width, test_space_same_layer, test_enclosure, test_layermap,
               test_same_rule_two_backends, test_real_rule1):
        fn()
    print("OK — width / space / enclosure recognition, layermap, two-backend swap, rule1.json")
    print("\n--- KLayout .drc from rule1.json (width) ---")
    print(get("klayout").compile(Rule(json.load(open(os.path.join(HERE, "..", "rule1.json"))))), end="")
    print("--- facts backend on the enclosure rule ---")
    print(get("facts").compile(Rule(enclosure_rule())), end="")


# ---- a shape drawn around its context; a selected shape against its own layer ----

def _box(name, x0, y0, x1, y1, straight=""):
    """y grows downwards: sides t(op) r(ight) b(ottom) l(eft) are <name>_0 .. _3."""
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"name": name, "edges": [{"id": f"{name.lower()}_{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
                                     "edge": "trbl"[i] in straight} for i in range(4)]}


def _drawn(shapes, measures, constraints):
    layers = [{"kind": "match", "expr": f"{s['name']}.layer == {layer}"} for s, layer in shapes]
    return Rule({"name": "R", "desc": "R", "shapes": [s for s, _ in shapes], "measures": measures,
                 "constraints": layers + constraints})


def test_a_shape_that_contains_its_context_is_the_one_covering_it():
    # the bottom plate is the metal that holds a top plate: a link to a shape drawn inside it
    r = _drawn([(_box("PLATE", 0, 0, 14, 14, "r"), "metal2"), (_box("FT", 3, 3, 11, 11), "fusetop"),
                (_box("B", 19, 0, 25, 14, "l"), "metal2")],
               [{"name": "LP", "from": "plate_0", "to": "ft_0"}, {"name": "S", "from": "plate_1", "to": "b_3"}],
               [{"kind": "check", "expr": "S >= 1.2"}])
    deck = get("klayout").compile(r)
    # ... and its spacing to its own layer is every too-small space with such a plate on one side
    assert "metal2.space(1.2.um, euclidian).polygons(0.001).interacting(metal2.covering(fusetop))" in deck
    assert "metal2 that contains fusetop" in get("docs").compile(r)


def test_a_cross_on_what_a_shape_contains():
    r = _drawn([(_box("PLATE", 0, 0, 20, 14), "metal2"), (_box("V", 13, 5, 16, 8), "via1")],
               [{"name": "X", "from": "plate_3", "to": "v_3", "crossed": True}], [])
    assert "metal2.covering(via1)" in get("klayout").compile(r)
    assert "metal2 must not contain via1" in get("docs").compile(r)


def test_an_area_exactly_at_its_maximum_is_not_reported():
    r = _drawn([(_box("A", 0, 0, 8, 8), "fusetop")], [], [{"kind": "check", "expr": "A.area <= 10000"}])
    assert "fusetop.with_area(10000.um, nil).not_in(fusetop.with_area(10000.um))" in get("klayout").compile(r)
    r = _drawn([(_box("A", 0, 0, 8, 8), "fusetop")], [], [{"kind": "check", "expr": "A.area >= 25"}])
    assert "fusetop.with_area(nil, 25.um)" in get("klayout").compile(r)


def test_edges_of_an_enclosing_shape_around_those_of_the_enclosed_one():
    # measured on edges (the rule holds in a zone only): the outer one's edges enclose the inner one's
    r = _drawn([(_box("ZONE", -4, -4, 22, 22), "zone"), (_box("IMP", 0, 0, 16, 16, "l"), "nplus"),
                (_box("CX", 5, 5, 11, 11, "l"), "comp")],
               [{"name": "E", "from": "imp_3", "to": "cx_3", "edges": True}], [{"kind": "check", "expr": "E >= 0.16"}])
    deck = get("klayout").compile(r)
    assert ".enclosing(" in deck and ".separation(" not in deck and deck.index("nplus") < deck.index("comp")
    assert "must enclose" in get("docs").compile(r)


def test_a_maximum_on_a_width_measured_on_gate_edges_is_said():
    r = _drawn([(_box("COMP", 4, 0, 10, 14, "lr"), "comp"), (_box("G", 0, 6, 14, 10, "tb"), "poly2")],
               [{"name": "W", "from": "comp_3", "to": "comp_1", "edges": True, "at": 8.0}], [{"kind": "check", "expr": "W <= 50"}])
    assert "at most 50 µm wide" in get("docs").compile(r)
