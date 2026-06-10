"""Tests for fastdrc.topology: spatial relation classification from geometry."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from fastdrc.topology import _point_in_poly, inverse, relate, relations, shape_polygon  # noqa: E402

# A concave (notched) outline: the rectangle 5..20 x 3..16 with a rectangular
# notch (13..20 x 6..12) cut out of its right side. Exercises the winding
# number on a non-convex shape -- a convex same-sign test would misclassify the
# notch interior as inside.
NOTCH = [(20, 3), (20, 6), (13, 6), (13, 12), (20, 12), (20, 16), (5, 16), (5, 3)]


def rect(x0, y0, x1, y1):
    """Axis-aligned rectangle as a CCW-ish vertex loop."""
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def tri(a, b, c):
    return [a, b, c]


CASES = [
    # (label, A, B, expected relate(A, B))
    ("disjoint",            rect(0, 0, 2, 2),   rect(5, 5, 7, 7),    "disjoint"),
    ("nested -> inside",    rect(2, 2, 4, 4),   rect(0, 0, 10, 10),  "inside"),
    ("nested -> contains",  rect(0, 0, 10, 10), rect(2, 2, 4, 4),    "contains"),
    ("partial overlap",     rect(0, 0, 4, 4),   rect(2, 2, 6, 6),    "overlap"),
    ("shared edge -> touch", rect(0, 0, 2, 2),  rect(2, 0, 4, 2),    "touch"),
    ("corner -> touch",     rect(0, 0, 2, 2),   rect(2, 2, 4, 4),    "touch"),
    ("identical -> equal",  rect(0, 0, 2, 2),   rect(0, 0, 2, 2),    "equal"),
    # thin slice flush against the left edge: all vertices land ON B's boundary,
    # yet A is genuinely inside B -- the vertex-outside test must still say inside.
    ("flush slice -> inside", rect(0, 0, 2, 5), rect(0, 0, 5, 5),    "inside"),
    # any-angle: a triangle fully within a box
    ("triangle in box",     tri((2, 2), (5, 2), (3, 5)), rect(0, 0, 8, 8), "inside"),
    # diagonal cut crossing a box edge
    ("triangle overlaps box", tri((-2, 1), (4, 1), (1, 4)), rect(0, 0, 8, 8), "overlap"),
    # concave: a box sitting in the notch is OUTSIDE the shape (disjoint),
    # while one in the solid part is inside -- winding number must get both.
    ("box in notch -> disjoint",  rect(15, 8, 18, 10), NOTCH, "disjoint"),
    ("box in solid -> inside",    rect(7, 8, 9, 10),   NOTCH, "inside"),
]


def test_concave_point_classification():
    # Winding number on the notched outline: the notch interior is OUTSIDE.
    assert _point_in_poly((17, 9), NOTCH) == "out"   # inside the notch
    assert _point_in_poly((8, 9), NOTCH) == "in"     # solid left part
    assert _point_in_poly((13, 9), NOTCH) == "on"    # on the notch wall (x=13)


def test_relate_cases():
    for label, A, B, expected in CASES:
        got = relate(A, B)
        assert got == expected, f"{label}: relate(A,B)={got!r}, expected {expected!r}"


def test_inverse_consistency():
    # relate(B, A) must equal inverse(relate(A, B)) for every case.
    for label, A, B, expected in CASES:
        assert relate(B, A) == inverse(relate(A, B)), f"{label}: inverse mismatch"


def test_relations_over_rule():
    rule = {
        "shapes": [
            {"name": "A", "edges": _edges_of(rect(0, 0, 10, 10))},
            {"name": "B", "edges": _edges_of(rect(2, 2, 4, 4))},
            {"name": "C", "edges": _edges_of(rect(20, 20, 22, 22))},
        ]
    }
    rel = relations(rule)
    assert rel[("A", "B")] == "contains"   # B sits inside A
    assert rel[("A", "C")] == "disjoint"
    assert rel[("B", "C")] == "disjoint"
    # polygon extraction round-trips the loop
    assert shape_polygon(rule["shapes"][1]) == [(2, 2), (4, 2), (4, 4), (2, 4)]


def _edges_of(poly):
    n = len(poly)
    return [{"id": f"e{i}", "start": list(poly[i]), "end": list(poly[(i + 1) % n]),
             "edge": True} for i in range(n)]


if __name__ == "__main__":
    test_relate_cases()
    test_inverse_consistency()
    test_relations_over_rule()
    test_concave_point_classification()
    print(f"OK — {len(CASES)} relation cases + inverse + rule relations + concave winding")
