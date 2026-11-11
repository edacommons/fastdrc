"""Derive spatial topology between drawn shapes.

The drawing carries the topology that constraints deliberately omit -- whether a
shape is inside / overlapping / abutting / disjoint from another. The compiler
reads these relations off the geometry rather than from constraint text.

Input shapes are the editor's rule IR: each shape is a closed loop of edges
(``{"start": [x, y], "end": [x, y], ...}``), so its polygon is the ordered list
of edge start points.

``relate(A, B)`` classifies the ordered pair (A relative to B) as one of:

    "disjoint"   no contact
    "touch"      boundaries meet (shared vertex / colinear edge), interiors don't
    "overlap"    interiors partially intersect (boundaries cross)
    "inside"     A lies within B (boundary-touching allowed)
    "contains"   B lies within A
    "equal"      same polygon

Classification: a proper (interior) segment crossing => "overlap"; otherwise the
relation is decided by whether either polygon has a vertex strictly outside the
other (no such vertex and no crossing => one encloses the other), plus whether
the boundaries make contact. Point-in-polygon uses the signed-crossing winding
number (orientation-sign only, no division, exact on integer coordinates), so
concave and self-intersecting outlines classify correctly.

TODO: the ``edge: bool`` flag distinguishes real material edges from abstract /
open boundaries; topology currently treats every shape as a closed loop. Open
sides (a shape that notionally extends past an edge) will need special handling.
"""

from __future__ import annotations

import math

EPS = 1e-9


def shape_polygon(shape) -> list:
    """Vertex loop of a shape = the ordered edge start points."""
    return [(float(e["start"][0]), float(e["start"][1])) for e in shape["edges"]]


def edge_angle(edge) -> float:
    """Orientation of a (straight) edge as an UNDIRECTED line, in [0, 180)
    degrees. A horizontal edge is 0, vertical is 90, a "/" diagonal 45, a "\\"
    diagonal 135. This is the quantity KLayout's ``with_angle`` filters on, so an
    acute (non-0/45/90/135) edge reads as a non-axis, non-45 orientation.
    Only meaningful for ``edge: true`` (straight/real) edges -- a wavy edge is an
    arbitrary continuation with no fixed orientation."""
    (x0, y0), (x1, y1) = edge["start"], edge["end"]
    a = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
    return 0.0 if abs(a) < EPS or abs(a - 180.0) < EPS else a


def edge_length(edge) -> float:
    """Euclidean length of an edge. The drawn value is an EXAMPLE; an edge-length
    rule's threshold lives in the constraint (``<edge>.length <= 0.34``) and maps
    to KLayout ``with_length``. Foundation for the edge-based rule family."""
    (x0, y0), (x1, y1) = edge["start"], edge["end"]
    return math.hypot(x1 - x0, y1 - y0)


def corner_angle(prev_edge, edge) -> float:
    """Interior turn at the vertex where ``prev_edge`` meets ``edge`` (they share
    a vertex), in degrees: the signed change of direction (+ = left/convex on a
    CCW loop, - = right/concave), in (-180, 180]. A right-angle convex corner is
    +90; a 135-degree chamfer corner is +45. Foundation for corner rules
    (``corners(a)``); the sign mirrors a CCW traversal."""
    (ax0, ay0), (ax1, ay1) = prev_edge["start"], prev_edge["end"]
    (bx0, by0), (bx1, by1) = edge["start"], edge["end"]
    d1 = math.atan2(ay1 - ay0, ax1 - ax0)
    d2 = math.atan2(by1 - by0, bx1 - bx0)
    return math.degrees((d2 - d1 + math.pi) % (2 * math.pi) - math.pi)


def _orient(a, b, c) -> float:
    """>0 if c is left of a->b, <0 if right, ~0 if colinear (twice signed area)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(p, a, b) -> bool:
    if abs(_orient(a, b, p)) > EPS:
        return False
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS and
            min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def _proper_cross(p1, p2, p3, p4) -> bool:
    """True iff segments p1p2 and p3p4 cross at interior points of both
    (shared endpoints / colinear touching do NOT count)."""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    return d1 * d2 < -EPS and d3 * d4 < -EPS


def _point_in_poly(pt, poly) -> str:
    """Return 'in', 'on', or 'out' for pt vs the closed polygon.

    In/out uses the signed-crossing winding number (nonzero-winding rule): walk
    the loop and, for each edge that spans the test height, add +1 for an upward
    crossing with pt to its left and -1 for a downward crossing with pt to its
    right -- decided by orientation sign only, no division, exact on integer
    coordinates. This handles concave (and self-intersecting / holed) outlines,
    unlike a convex same-sign / signed-area test.
    """
    n = len(poly)
    for i in range(n):
        if _on_segment(pt, poly[i], poly[(i + 1) % n]):
            return "on"
    py = pt[1]
    wn = 0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if a[1] <= py:
            if b[1] > py and _orient(a, b, pt) > EPS:      # upward crossing, pt left
                wn += 1
        elif b[1] <= py and _orient(a, b, pt) < -EPS:      # downward crossing, pt right
            wn -= 1
    return "in" if wn != 0 else "out"


def contains(poly, pt) -> bool:
    """True if pt is strictly inside the polygon."""
    return _point_in_poly(pt, poly) == "in"


def _edges(poly):
    n = len(poly)
    return [(poly[i], poly[(i + 1) % n]) for i in range(n)]


def _any_proper_cross(A, B) -> bool:
    return any(_proper_cross(a1, a2, b1, b2)
               for a1, a2 in _edges(A) for b1, b2 in _edges(B))


def _boundary_contact(A, B) -> bool:
    # A colinear overlap always puts one polygon's vertex on the other's edge,
    # so a vertex-on-boundary test covers shared-vertex and shared-edge touches.
    return (any(_point_in_poly(v, B) == "on" for v in A) or
            any(_point_in_poly(v, A) == "on" for v in B))


def _seg_param(p, a, b):
    """Parameter t of point p along segment a->b (p assumed on the segment)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    return ((p[0] - a[0]) / dx) if abs(dx) >= abs(dy) else ((p[1] - a[1]) / dy)


def _boundary_samples(A, B) -> set:
    """Where A's boundary lies relative to B: the set of 'in'/'on'/'out' over A's
    vertices AND the midpoints of A's edges after splitting each edge wherever
    B's boundary meets it. Vertices alone miss an edge that runs through B's
    interior between two vertices that sit ON B's boundary (e.g. two equal-height
    rectangles overlapping sideways -- no vertex is inside, no edges cross)."""
    seen = {_point_in_poly(v, B) for v in A}
    for a, b in _edges(A):
        ts = {0.0, 1.0}
        for c, d in _edges(B):
            for v in (c, d):                        # B vertex on this edge
                if _on_segment(v, a, b):
                    ts.add(_seg_param(v, a, b))
            den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
            if abs(den) > EPS:                      # non-parallel: the single meeting point
                t = ((c[0] - a[0]) * (d[1] - c[1]) - (c[1] - a[1]) * (d[0] - c[0])) / den
                u = ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])) / den
                if -EPS <= t <= 1 + EPS and -EPS <= u <= 1 + EPS:
                    ts.add(min(1.0, max(0.0, t)))
        ts = sorted(ts)
        for t0, t1 in zip(ts, ts[1:]):
            if t1 - t0 > EPS:
                tm = (t0 + t1) / 2
                seen.add(_point_in_poly((a[0] + (b[0] - a[0]) * tm,
                                         a[1] + (b[1] - a[1]) * tm), B))
    return seen


def relate(A, B) -> str:
    """Classify the ordered pair of polygons A, B (A relative to B)."""
    if _any_proper_cross(A, B):
        return "overlap"
    a_in_b, b_in_a = _boundary_samples(A, B), _boundary_samples(B, A)
    a_out, b_out = "out" in a_in_b, "out" in b_in_a
    if not a_out and not b_out:
        return "equal"
    if not a_out:           # all of A's boundary is in/on B, no crossing => A within B
        return "inside"
    if not b_out:
        return "contains"
    if "in" in a_in_b or "in" in b_in_a:    # a boundary runs through the other's interior
        return "overlap"
    return "touch" if _boundary_contact(A, B) else "disjoint"


_INVERSE = {"inside": "contains", "contains": "inside"}


def inverse(rel: str) -> str:
    """The relation seen from the other shape's perspective."""
    return _INVERSE.get(rel, rel)


def relations(rule) -> dict:
    """All pairwise relations in a rule, keyed by (nameA, nameB) in shape order.

    The value is ``relate(A, B)``; the reverse pair's relation is ``inverse(...)``.
    """
    shapes = rule["shapes"]
    polys = {s["name"]: shape_polygon(s) for s in shapes}
    names = [s["name"] for s in shapes]
    out = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            out[(a, b)] = relate(polys[a], polys[b])
    return out
