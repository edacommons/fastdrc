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
