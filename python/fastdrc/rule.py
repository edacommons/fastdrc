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
            b = _bound(ast, names)
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


def _bound(ast, varnames):
    """If ast is ``var <cmp> num`` (either order), return a normalized Bound."""
    if isinstance(ast, dict) and ast.get("op") in _CMP:
        a, b = ast["args"]
        if a.get("ref") in varnames and "num" in b:
            return Bound(a["ref"], ast["op"], b["num"])
        if b.get("ref") in varnames and "num" in a:
            return Bound(b["ref"], _FLIP[ast["op"]], a["num"])
    return None
