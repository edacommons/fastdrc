"""The analyzed-rule API a backend works against.

This is the "Python API with our functions" that backends use instead of a
separate data IR: a thin `Rule` over the editor's rule JSON that answers
questions by calling our topology (`topology.relate`) and constraint
(`constraint.parse`) functions.

A rule is ``{shapes, measures, constraints}``:
  - shapes carry geometry (a closed edge loop);
  - measures are named edge-to-edge distances (``{name, from, to}``);
  - constraints are Python-subset formulas (parsed to the ``{op, args[]}`` AST).

What the drawing can't show lives in constraints, so the accessors mine the
parsed ASTs for facts:
  - ``layer_of(shape)`` / ``net_of(shape)`` from ``Shape.layer == X`` etc.
  - ``bounds()`` from numeric relations on measure vars (``W > 2`` -> min 2).
Topology (relation between shapes) and edge ownership come from geometry.
"""

from __future__ import annotations

from .constraint import parse
from . import topology

_CMP = {">", ">=", "<", "<="}
_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}


class Bound:
    """A numeric bound on a measure variable, e.g. ``W >= 2``."""

    def __init__(self, var, op, value):
        self.var, self.op, self.value = var, op, value

    @property
    def is_min(self):
        return self.op in (">", ">=")

    @property
    def is_max(self):
        return self.op in ("<", "<=")

    def __repr__(self):
        return f"Bound({self.var} {self.op} {self.value})"


class Rule:
    def __init__(self, data: dict):
        self.data = data
        self.name = data.get("name")          # rule id, e.g. "DN.1"
        self.desc = data.get("desc", "")      # human description for .output
        self.shapes = {s["name"]: s for s in data.get("shapes", [])}
        self.measures = {m["name"]: m for m in data.get("measures", [])}
        # constraints: list of (kind, parsed-AST)
        self.constraints = [(c.get("kind", "check"), parse(c["expr"]))
                            for c in data.get("constraints", [])]
        self._edge_owner = {e["id"]: name
                            for name, s in self.shapes.items() for e in s["edges"]}

    # --- geometry / topology (via our functions) ---
    def polygon(self, shape):
        return topology.shape_polygon(self.shapes[shape])

    def relation(self, a, b) -> str:
        return topology.relate(self.polygon(a), self.polygon(b))

    def relations(self) -> dict:
        return topology.relations(self.data)

    def shape_of_edge(self, edge_id):
        return self._edge_owner.get(edge_id)

    def edge_angle(self, edge_id):
        """The DRAWN orientation of a straight edge (undirected, [0,180)), via our
        topology -- what our engine evaluates an angle rule against natively."""
        name = self._edge_owner.get(edge_id)
        if name is None:
            return None
        for e in self.shapes[name]["edges"]:
            if e["id"] == edge_id:
                return topology.edge_angle(e)
        return None

    def _edge_straight(self, edge_id):
        name = self._edge_owner.get(edge_id)
        if name is None:
            return False
        for e in self.shapes[name]["edges"]:
            if e["id"] == edge_id:
                return bool(e.get("edge", False))
        return False

    def measure_is_straight(self, mname):
        """True if both endpoints of the measure are STRAIGHT edges -- a real,
        oriented distance (a dimensional check). A measure across WAVY edges is
        not a distance but a topological assertion (see relation_selections)."""
        m = self.measures.get(mname)
        return bool(m and self._edge_straight(m["from"]) and self._edge_straight(m["to"]))

    def relation_selections(self):
        """A measure drawn across WAVY edges asserts a topological relation, not a
        distance: a ``>=`` bound means the two shapes don't overlap but the gap
        MAY be zero (they may touch) -> a SELECTION on the subject (KLayout
        ``outside`` keeps touching polys), as opposed to a fully-separate
        no-measure shape, which is a ``not_interacting`` scope MARKER (drops
        touching). Returns [(shapeA, shapeB)] of such wavy-measure pairs; the
        backend reads the relation from the subject's side."""
        out = []
        minvars = {b.var for b in self.bounds() if b.is_min}
        for mname, m in self.measures.items():
            if mname not in minvars or self.measure_is_straight(mname):
                continue
            sa, sb = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
            if sa is not None and sb is not None and sa != sb:
                out.append((sa, sb))
        return out

    def edge_length(self, edge_id):
        """Drawn Euclidean length of an edge (native eval; informational -- the
        rule threshold is symbolic, in the constraint)."""
        name = self._edge_owner.get(edge_id)
        if name is None:
            return None
        for e in self.shapes[name]["edges"]:
            if e["id"] == edge_id:
                return topology.edge_length(e)
        return None

    def length_bounds(self) -> list:
        """Edge-LENGTH facts from ``<edge>.length <cmp> n``, as
        ``(edge, kind, lo, hi)``: kind ``"with"`` -> ``with_length(lo, hi)``
        (``<=`` gives lo=None=nil; ``==`` gives lo==hi exact); kind ``"without"``
        from ``!=`` -> ``without_length(n)`` (edges NOT of that length)."""
        out = []
        spans = {}
        for _, ast in self.constraints:
            for edge, op, num in _attr_cmps(ast, ".length"):
                if op == "!=":
                    out.append((edge, "without", num, num))
                    continue
                lo, hi = spans.get(edge, [None, None])
                if op in (">", ">="):
                    lo = num
                elif op in ("<", "<="):
                    hi = num
                elif op == "==":
                    lo = hi = num
                spans[edge] = [lo, hi]
        out += [(e, "with", lo, hi) for e, (lo, hi) in spans.items()]
        return out

    def corner_angle(self, edge_id):
        """The DRAWN interior turn at the vertex where this edge STARTS (between
        the previous edge and this one), via our topology -- what an engine
        evaluates a corner rule against. Sign depends on draw winding; rules use
        the magnitude."""
        name = self._edge_owner.get(edge_id)
        if name is None:
            return None
        edges = self.shapes[name]["edges"]
        for i, e in enumerate(edges):
            if e["id"] == edge_id:
                return topology.corner_angle(edges[(i - 1) % len(edges)], e)
        return None

    def interior_point(self, shape):
        poly = self.polygon(shape)
        n = len(poly)
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)

    def covering(self, shape):
        """Other shapes whose polygon contains this shape's interior point --
        i.e. layers that AND into this shape's (derived) layer."""
        pt = self.interior_point(shape)
        return [s for s in self.shapes
                if s != shape and topology.contains(self.polygon(s), pt)]

    def hole_layers(self, shape):
        """Layers tagged on this shape's holes -- each is SUBTRACTED from the
        shape's (derived) layer (a hole = NOT that layer)."""
        return [h["layer"] for h in self.shapes[shape].get("holes", []) if h.get("layer")]

    # --- constraint facts (mined from the parsed ASTs) ---
    def layer_of(self, shape):
        return self._attr_eq(shape, "layer")

    def net_of(self, shape):
        return self._attr_eq(shape, "net")

    def _attr_eq(self, shape, attr):
        target = f"{shape}.{attr}"
        for _, ast in self.constraints:
            v = _eq_value(ast, target)
            if v is not None:
                return v
        return None

    def bounds(self) -> list:
        """Numeric bounds on measure variables, normalized so the var is on the
        left (so ``2 < W`` reads as ``W > 2``)."""
        out = []
        names = set(self.measures)
        for _, ast in self.constraints:
            for leaf in _and_leaves(ast):       # a two-sided range is one `and`
                b = _bound(leaf, names)
                if b is not None:
                    out.append(b)
        return out

    def area_bounds(self) -> list:
        """Bounds on a shape's area: ``A.area >= v`` -> (shape, op, value)."""
        out = []
        for _, ast in self.constraints:
            if isinstance(ast, dict) and ast.get("op") in _CMP:
                a, b = ast["args"]
                ref = a.get("ref", "") if isinstance(a, dict) else ""
                if ref.endswith(".area") and isinstance(b, dict) and "num" in b:
                    out.append((ref[:-len(".area")], ast["op"], b["num"]))
        return out

    def angle_bounds(self) -> list:
        """Forbidden edge-ORIENTATION ranges, from constraints on an edge's angle
        (``<edge>.angle <cmp> n``, optionally and-combined): ``e.angle > 0 and
        e.angle < 45`` -> ``(edge, 0, 45)``. The compiler maps each to
        ``layer.with_angle(lo .. hi)`` (an acute / non-45 angle check). An open
        side defaults to the full [0,180) range."""
        ranges = {}
        for _, ast in self.constraints:
            for edge, op, num in _attr_cmps(ast, ".angle"):
                if op not in ("<", "<=", ">", ">="):    # ranges only (not ==/!=)
                    continue
                lo, hi = ranges.get(edge, [0.0, 180.0])
                if op in (">", ">="):
                    lo = num
                else:
                    hi = num
                ranges[edge] = [lo, hi]
        return [(e, lo, hi) for e, (lo, hi) in ranges.items()]

    def corner_bounds(self) -> list:
        """Corner-angle facts: ``<edge>.corner == n`` -> (edge, |n|). The vertex
        where the edge starts must turn by magnitude n. Maps to
        ``corners(n).or(corners(-n))`` -- a |angle|=n corner rule (convex OR
        concave), e.g. PL.6's ``corners(90).or(corners(-90))``. Magnitude is
        winding-independent, so the drawn corner's sign is incidental."""
        out = []
        for _, ast in self.constraints:
            for edge, op, num in _attr_cmps(ast, ".corner"):
                if op == "==":
                    out.append((edge, abs(num)))
        return out


# --- small AST queries over the {op, args[]} constraint form ---

def _eq_value(ast, target):
    """If ast is ``X == target`` (either order), return the other side's
    value (a ref name or a number); else None."""
    if isinstance(ast, dict) and ast.get("op") == "==":
        a, b = ast["args"]
        if a.get("ref") == target:
            return b.get("ref", b.get("num"))
        if b.get("ref") == target:
            return a.get("ref", a.get("num"))
    return None


def _attr_cmps(ast, suffix):
    """Collect ``(ref, cmp, num)`` from every ``<ref><suffix> <cmp> num`` leaf
    (e.g. suffix ``.angle`` or ``.corner``), recursing through ``and`` and ``or``
    so a two-sided range or either branch yields its bounds."""
    out = []
    if not isinstance(ast, dict):
        return out
    op = ast.get("op")
    if op in ("and", "or"):
        for a in ast["args"]:
            out += _attr_cmps(a, suffix)
    elif op in _CMP or op in ("==", "!="):
        a, b = ast["args"]
        ar = a.get("ref", "") if isinstance(a, dict) else ""
        br = b.get("ref", "") if isinstance(b, dict) else ""
        if ar.endswith(suffix) and isinstance(b, dict) and "num" in b:
            out.append((ar[:-len(suffix)], op, b["num"]))
        elif br.endswith(suffix) and isinstance(a, dict) and "num" in a:
            out.append((br[:-len(suffix)], _FLIP.get(op, op), a["num"]))
    return out


def _and_leaves(ast):
    """Yield the conjuncts of an ``and`` (recursively), or the ast itself -- so a
    two-sided range ``v1 <= W and W < v2`` surfaces both comparisons."""
    if isinstance(ast, dict) and ast.get("op") == "and":
        for a in ast["args"]:
            yield from _and_leaves(a)
    else:
        yield ast


def _bound(ast, varnames):
    """If ast is ``var <cmp> num`` (either order), return a normalized Bound."""
    if isinstance(ast, dict) and ast.get("op") in _CMP:
        a, b = ast["args"]
        if a.get("ref") in varnames and "num" in b:
            return Bound(a["ref"], ast["op"], b["num"])
        if b.get("ref") in varnames and "num" in a:
            return Bound(b["ref"], _FLIP[ast["op"]], a["num"])
    return None
