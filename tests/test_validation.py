"""Generated-Ruby escaping, and the geometry reads the compiler rests on (overlap
classification, a concave shape's interior point)."""

from __future__ import annotations

import fastdrc.backend_docs      # noqa: F401  (registers "docs")
import fastdrc.backend_klayout   # noqa: F401  (registers "klayout")
from fastdrc import topology
from fastdrc.backend import get
from fastdrc.rule import Rule


def _rect(name, x0, y0, x1, y1, straight=(1, 3)):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"name": name, "edges": [
        {"id": f"{name.lower()}{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
         "edge": i in straight} for i in range(4)]}


def _width(*constraints, **extra):
    return {"shapes": [_rect("A", 0, 0, 10, 4)],
            "measures": [{"name": "W", "from": "a1", "to": "a3"}],
            "constraints": [{"kind": "match", "expr": "A.layer == metal5"}, *constraints],
            **extra}


def _check(expr):
    return {"kind": "check", "expr": expr}


def _kl(rule):
    return get("klayout").compile(Rule(rule))


def test_generated_ruby_is_escaped():
    drc = _kl(_width(_check("W >= 2"), name="3V.1", desc='min "m5" width #{exit} \\ #1'))
    assert "\n_3v_1_l1 = " in drc                    # a Ruby local can't start with a digit
    assert '.output("3V.1", "min \\"m5\\" width \\#{exit} \\\\ #1")' in drc


def _r(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def test_overlap_without_a_crossing_or_inside_vertex():
    # equal-height rectangles overlapping sideways: no vertex strictly inside the
    # other and no edges properly cross, yet they share area
    assert topology.relate(_r(0, 0, 10, 10), _r(5, 0, 15, 10)) == "overlap"
    assert topology.relate(_r(5, 0, 15, 10), _r(0, 0, 10, 10)) == "overlap"
    # the neighbours of that case keep their reading
    assert topology.relate(_r(0, 0, 10, 10), _r(10, 0, 20, 10)) == "touch"
    assert topology.relate(_r(0, 0, 10, 10), _r(10, 10, 20, 20)) == "touch"
    assert topology.relate(_r(0, 0, 10, 10), _r(11, 0, 20, 10)) == "disjoint"
    assert topology.relate(_r(0, 0, 10, 10), _r(2, 0, 8, 10)) == "contains"
    assert topology.relate(_r(2, 0, 8, 10), _r(0, 0, 10, 10)) == "inside"
    assert topology.relate(_r(0, 0, 10, 10), _r(0, 0, 10, 10)) == "equal"
    assert topology.relate(_r(0, 4, 10, 6), _r(4, 0, 6, 10)) == "overlap"
    # a U: a bar across its mouth touches both arms but is not inside it
    u = [(0, 0), (10, 0), (10, 10), (7, 10), (7, 3), (3, 3), (3, 10), (0, 10)]
    assert topology.relate(_r(3, 5, 7, 8), u) == "touch"


def test_interior_point_of_a_concave_shape_is_inside_it():
    pts = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)]     # an L; vertex mean (4, 4)
    L = {"name": "A", "edges": [{"id": f"a{i}", "start": list(pts[i]),
                                 "end": list(pts[(i + 1) % 6]), "edge": False}
                                for i in range(6)]}
    notch = _rect("N", 3, 3, 9, 9, straight=())                    # sits in the L's notch
    r = Rule({"shapes": [L, notch], "measures": [], "constraints": []})
    assert topology.contains(r.polygon("A"), r.interior_point("A"))
    assert r.covering("A") == []                                   # the notch shape covers none of A
