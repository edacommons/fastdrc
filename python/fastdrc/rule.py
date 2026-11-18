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

import math

from .constraint import parse
from . import topology

_CMP = {">", ">=", "<", "<="}
_FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
_OPPOSITE = {">": "<=", ">=": "<", "<": ">=", "<=": ">"}   # the assertion a filter's matches fail


def split_layers(layer):
    """The member layers of a layer value: one name, or a SET (``a|b|c`` = the union
    of those layers, from ``A.layer in {a, b, c}``)."""
    return layer.split("|") if layer else []


def rel_to_sel(rel):
    """Map a drawn relation to the whole-polygon SELECTION op. The drawn relation
    IS the signal: TOUCH (coinciding edges, gap 0) -> `interacting`; genuine area
    OVERLAP -> `overlapping` (== `not_outside`); containment -> `inside`; the other
    shape drawn wholly within this one -> `covering`; else (disjoint) -> `outside`."""
    return ("inside" if rel == "inside" else "interacting" if rel == "touch"
            else "overlapping" if rel == "overlap" else "covering" if rel == "contains" else "outside")


# truth table: each selection op -> the relations it KEEPS. Every row is distinct,
# so labeled example instances (some flagged, some not) identify the op uniquely;
# a NEGATION (not_inside) is a row whose excluded set is a single relation.
SEL_ROWS = {
    # not a KLayout op of its own: over the other shape's edge -- partly in, partly out
    "straddling":      frozenset({"overlap"}),
    "inside":          frozenset({"inside"}),
    "not_interacting": frozenset({"disjoint"}),
    "outside":         frozenset({"disjoint", "touch"}),
    "overlapping":     frozenset({"inside", "overlap"}),
    "interacting":     frozenset({"inside", "touch", "overlap"}),
    "not_inside":      frozenset({"disjoint", "touch", "overlap"}),
}


def lookup_sel(kept, dropped):
    """The selection op whose row keeps every `kept` relation and none of `dropped`;
    among consistent ops the MOST permissive. None if nothing fits."""
    cands = [op for op, keep in SEL_ROWS.items() if kept <= keep and not (dropped & keep)]
    return max(cands, key=lambda op: len(SEL_ROWS[op])) if cands else None


class Bound:
    """A numeric bound on a measure variable, e.g. ``W >= 2``."""

    def __init__(self, var, op, value, kind="check"):
        self.var, self.op, self.value = var, op, value
        self.kind = kind                      # the constraint's kind: "match" / "check"
        self.zero_ok = False                  # written `W >= v or W == 0`: coinciding edges pass
        self.exact = False                    # one half of `W == v`

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
        # A cross drawn OVER a shape ("this must not exist", as a datasheet marks it):
        # the same check as a cross on a measure across that shape -- carried as one.
        filtered = self._filtered_shapes(data)
        for name, s in self.shapes.items():
            # ... unless a filter on the shape says WHICH ones are crossed out ("area <
            # 2", an X over it): then that filter's matches are the violation already
            if s.get("crossed") and s.get("edges") and name not in filtered:
                e0 = s["edges"][0]["id"]
                self.measures[f"cross_{name}"] = {"name": f"cross_{name}", "from": e0, "to": e0,
                                                  "crossed": True, "of_shape": True}
        # the LEGEND: names for derived layers, as a datasheet's legend has NCOMP or
        # "gate" -- {"pcomp": "comp and pplus", "field_poly": "poly2 and not comp"}
        self.layer_defs = {k: parse(v) for k, v in (data.get("layers") or {}).items()}
        # how layers CONNECT (contact joins diffusion and metal1 ...): what makes a net.
        # Process knowledge given once, like the legend: [["nwell", "ntap"], ...]
        self.connect = [tuple(p) for p in (data.get("connect") or [])]
        # constraints: list of (kind, parsed-AST)
        self.constraints = [(c.get("kind", "check"), parse(c["expr"]))
                            for c in data.get("constraints", [])]
        self._edge_owner = {e["id"]: name
                            for name, s in self.shapes.items() for e in s["edges"]}
        self._wide_strict = {}   # shape -> its width filter is "wider than" (>), not "at least" (>=)
        self._anchors = {}    # shape -> the point it is checked at, while reading a measure
        self._length_ops = {}  # (edge, kind, is a minimum) -> the operator written
        self._partners = set()   # shapes of the partner arrow of a "one of two sides" check

    @staticmethod
    def _filtered_shapes(data):
        """Shapes that carry a `match` on their own area."""
        out = set()
        for c in data.get("constraints", []):
            if c.get("kind") == "match":
                for ref in _all_refs(parse(c["expr"])):
                    if ref.endswith(".area"):
                        out.add(ref.split(".")[0])
        return out

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
        """True if the measure is a DISTANCE (a dimensional check): both its edges are
        straight, or a constraint bounds it by a number. Otherwise it is a link -- a
        topological assertion (see relation_selections)."""
        m = self.measures.get(mname)
        if not m:
            return False
        if self._edge_straight(m["from"]) and self._edge_straight(m["to"]):
            return True
        # A measure that a constraint gives a real number to IS a distance, however its
        # edges were drawn: `E >= 0.18` cannot mean a relation. (Only a bare measure,
        # or the older `G >= 0`, is a link.) So forgetting to make a measured edge
        # straight cannot silently turn a rule into something else.
        return mname in self._bounded_measures()

    def _bounded_measures(self):
        if getattr(self, "_bounded", None) is None:
            self._bounded = set()              # (set first: _either is asked while it fills)
            either = {x.var for kind, ast in self.constraints if kind == "check"
                      for x in (self._either(ast) or ())[:2]}
            self._bounded = ({b.var for b in self.bounds() if b.value != 0} | either
                             | set(self.run_bounds()))
        return self._bounded

    def relation_selections(self):
        """A measure drawn to a WAVY edge is a LINK that asserts a topological relation,
        not a distance: the two shapes relate as drawn -> a SELECTION on the subject
        (drawn apart it is KLayout ``outside``, which keeps touching polys), as opposed
        to a fully-separate UNLINKED shape, which is a ``not_interacting`` scope MARKER
        (drops touching). Returns [(shapeA, shapeB)] of such linked pairs; the backend
        reads the relation from the subject's side."""
        out = []
        maxvars = {b.var for b in self.bounds() if b.is_max}
        for mname, m in self.measures.items():
            # A link drawn to a WAVY edge is not a distance, so it needs no bound: by
            # itself it says "this relation, as drawn" (a `>= 0` on it is accepted and
            # adds nothing). A CROSS between two shapes is the same statement, only it
            # is the rule's CHECK ("never") instead of a filter, and may sit on any edges.
            if not m.get("crossed") and (mname in maxvars or self.measure_is_straight(mname)):
                continue
            sa, sb = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
            if sa is not None and sb is not None and sa != sb:
                out.append((sa, sb))
        return out

    def _straddle_cross(self, subj, other):
        """Is the relation subj-other crossed as "must not STRADDLE" (``"crossed":
        "straddle"``)? Drawn overlapping, a plain cross forbids any shared area; this
        one forbids only lying across the other's edge -- wholly inside is fine."""
        for m in self.measures.values():
            if m.get("crossed") == "straddle" and {self.shape_of_edge(m["from"]),
                                                   self.shape_of_edge(m["to"])} == {subj, other}:
                return True
        return False

    def crossed_relation(self, mname):
        """``(subject, context)`` when measure `mname` is a CROSS drawn between two
        different shapes -- the forbidden RELATION (the rule's check); else None. The
        subject is the shape the measure starts from."""
        m = self.measures.get(mname)
        if not (m and m.get("crossed")):
            return None
        sa, sb = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
        return (sa, sb) if sa is not None and sb is not None and sa != sb else None

    def net_cross(self, mname):
        """``(holder, a, b)`` when a CROSS is drawn between two shapes of one layer that a
        constraint puts on DIFFERENT nets (``A.net != B.net``), both drawn inside one
        other shape: "two of these at different potentials must not be in one holder"."""
        rel = self.crossed_relation(mname)
        if not rel or self.layer_of(rel[0]) != self.layer_of(rel[1]) or self.net_relation(*rel) != "ne":
            return None
        a, b = rel
        holder = next((h for h in self.shapes if h not in rel and self.relation(a, h) == "inside"
                       and self.relation(b, h) == "inside"), None)
        return (holder, a, b) if holder else None

    def is_flagged(self, shape):
        """A drawn instance is FLAGGED (a positive example -- the violation, the
        default) or a not-flagged COUNTER-EXAMPLE: a same-layer instance drawn in
        a particular relation to a context to show that that relation is NOT
        flagged. Counter-examples turn a selection into a labeled truth table, so
        a NEGATION (e.g. not_inside) -- which has no single positive picture -- is
        drawn as `flagged outside/overlap instances + a not-flagged inside one`."""
        return bool(self.shapes.get(shape, {}).get("flagged", True))

    def instances_relating_to(self, layer, context):
        """Every drawn instance OF `layer` that asserts a wavy-measure relation to
        the `context` shape, as [(shape, flagged)] -- the labeled examples the
        selection op for `context` is read off (see is_flagged)."""
        out = []
        for sa, sb in self.relation_selections():
            for inst, ctx in ((sa, sb), (sb, sa)):
                if ctx == context and self.layer_of(inst) == layer:
                    out.append((inst, self.is_flagged(inst)))
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

    def length_facts(self) -> list:
        """Edge-LENGTH constraints as WRITTEN: ``(edge, kind, form, lo, hi)`` where kind
        is the constraint's kind (``match`` / ``check``) and form is ``"range"``
        (``>=`` / ``<=`` bounds, either side may be None), ``"exact"`` (``==``, lo == hi)
        or ``"not"`` (``!=``, lo == hi). Bounds on one edge within one kind merge into
        a single range."""
        out, spans = [], {}
        for kind, ast in self.constraints:
            for edge, op, num in _attr_cmps(ast, ".length"):
                if op == "!=":
                    out.append((edge, kind, "not", num, num))
                elif op == "==":
                    out.append((edge, kind, "exact", num, num))
                else:
                    lh = spans.setdefault((edge, kind), [None, None])
                    lh[0 if op in (">", ">=") else 1] = num
                    self._length_ops[(edge, kind, op in (">", ">="))] = op
        out += [(e, k, "range", lo, hi) for (e, k), (lo, hi) in spans.items()]
        return out

    def side_relations(self) -> list:
        """Constraints that compare two SIDES of one shape -- ``a.length == b.length``,
        ``a.length > b.length`` -- as ``(kind, shape, a, op, b)``. They say what KIND of
        shape it is (a square, a bar), not how big."""
        out = []
        for kind, ast in self.constraints:
            for leaf in _and_leaves(ast):
                if not (isinstance(leaf, dict) and leaf.get("op") in ("==", "!=", "<", ">", "<=", ">=")):
                    continue
                a, b = (x.get("ref", "") if isinstance(x, dict) else "" for x in leaf["args"])
                if a.endswith(".length") and b.endswith(".length"):
                    ea, eb = a[:-7], b[:-7]
                    sa = self.shape_of_edge(ea)
                    if sa and sa == self.shape_of_edge(eb) and ea != eb:
                        out.append((kind, sa, ea, leaf["op"], eb))
        return out

    def _corner_counts(self):
        """``(kind, shape, is_rectangle)`` from ``F.corners == 4`` / ``!= 4`` (also
        ``> 4``): a shape with four corners is a rectangle."""
        out = []
        for kind, ast in self.constraints:
            for leaf in _and_leaves(ast):
                if not (isinstance(leaf, dict) and leaf.get("op") in ("==", "!=", ">")):
                    continue
                a, b = leaf["args"]
                ref = a.get("ref", "") if isinstance(a, dict) else ""
                if ref.endswith(".corners") and ref[:-8] in self.shapes and isinstance(b, dict) and b.get("num") == 4:
                    out.append((kind, ref[:-8], leaf["op"] == "=="))
        return out

    def shape_forms(self) -> dict:
        """``{shape: "square" | "oblong"}`` -- the shape class a FILTER states: a `match`
        that two sides are equal picks the squares of the layer, one that they differ
        (``!=``, ``<``, ``>``) the others (a bar)."""
        forms = {shape: "rectangle" if rect else "other"
                 for kind, shape, rect in self._corner_counts() if kind != "check"}
        forms.update({shape: "square" if op == "==" else "oblong"       # `>=` only orders the sides
                      for kind, shape, _, op, _ in self.side_relations() if kind != "check" and op not in (">=", "<=")})
        return forms

    def form_checks(self) -> list:
        """``(shape, form)``: a CHECK comparing two sides asserts the shape class --
        ``a.length == b.length`` "must be square" (the others are flagged)."""
        return ([(shape, "square" if op == "==" else "oblong")
                 for kind, shape, _, op, _ in self.side_relations() if kind == "check" and op not in (">=", "<=")]
                + [(shape, "rectangle" if rect else "other")
                   for kind, shape, rect in self._corner_counts() if kind == "check"])

    def side_roles(self) -> dict:
        """``{edge: "long" | "short"}`` for sides a constraint orders by length
        (``a.length > b.length``: a is the long side of its shape, b the short one). A
        length constraint on such a side is about the shape's longer / shorter extent,
        not about every edge."""
        out = {}
        for _, _, a, op, b in self.side_relations():
            if op in (">", ">="):
                out[a], out[b] = "long", "short"
            elif op in ("<", "<="):
                out[a], out[b] = "short", "long"
        return out

    def length_spans(self) -> dict:
        """``{edge: (lo, hi)}`` of the range/exact length constraints as written, of
        ANY kind -- the qualifiers a structural recognition (end-of-line) reads."""
        return {e: (lo, hi) for e, _, form, lo, hi in self.length_facts() if form != "not"}

    LENGTH_STEP = 0.001   # µm: one database unit, the smallest step a length can differ by

    def near_filters(self) -> dict:
        """``{filter arrow: (narrowed shape, other shape, Bound)}``: a FILTER "closer than
        v" on an arrow from a shape that a CHECK measures to a third shape the check does
        not involve -- it says WHICH of those shapes the check is about ("an NCOMP less
        than 1 um from an Nwell must have a tap within 20"). Not a rule of its own."""
        ends = lambda k: (self.shape_of_edge(self.measures[k].get("from")), self.shape_of_edge(self.measures[k].get("to")))   # noqa: E731
        straight = [k for k, m in self.measures.items() if m.get("from") and not m.get("crossed") and self.measure_is_straight(k)]
        checked = {}
        for b in self.bounds():
            if b.kind == "check" and b.var in straight:
                for sh in ends(b.var):
                    checked.setdefault(sh, set()).update(ends(b.var))
        out = {}
        for b in self.bounds():
            if b.kind == "check" or b.var not in straight or b.is_min or any(
                    x.var == b.var and x.kind == "check" for x in self.bounds()):
                continue
            a, c = ends(b.var)
            for mine, other in ((a, c), (c, a)):
                if mine in checked and other not in checked[mine] and mine != other \
                        and self.relation(mine, other) in ("disjoint", "outside"):
                    out[b.var] = (mine, other, b)
            if b.var in out:
                continue
            # ... or from what HOLDS a measured shape (its well) to a third shape outside it:
            # "the devices in a well that is closer than v to ..."
            for mine, other in ((a, c), (c, a)):
                held = [s for s in checked if s != mine and self.relation(mine, s) == "contains"]
                if mine not in checked and other not in checked and held and mine != other \
                        and self.relation(mine, other) in ("disjoint", "outside"):
                    out[b.var] = (mine, other, b)
        return out

    def faced_edge_filters(self) -> dict:
        """``{edge: (lo, hi)}``: a length FILTER on the edge a maximum "measured on
        edges" starts at -- which edges of the shape must have the other one facing them
        ("the long sides of the anode": the edges longer than its width). Such a filter
        is part of that rule, not a rule of its own."""
        starts = {m["from"] for k, m in self.measures.items()
                  if m.get("edges") and m.get("from") and any(b.var == k and b.is_max and b.kind == "check" for b in self.bounds())}
        out = {}
        for edge, kind, form, lo, hi in self.length_facts():
            if kind != "check" and form == "range" and edge in starts:
                # as KLayout takes a range (from lo, up to but not hi): `> lo` starts a step above
                # lo, `<= hi` ends a step above hi
                if lo is not None and self._length_ops.get((edge, kind, True)) == ">":
                    lo = round(lo + self.LENGTH_STEP, 6)
                if hi is not None and self._length_ops.get((edge, kind, False)) == "<=":
                    hi = round(hi + self.LENGTH_STEP, 6)
                out[edge] = (lo, hi)
        return out

    def length_bounds(self) -> list:
        """The edges an edge-length constraint FLAGS, as ``(edge, sel, lo, hi)`` with
        sel ``"with"`` -> ``with_length(lo, hi)`` (None = unbounded, lo == hi exact) or
        ``"without"`` -> ``without_length(lo[, hi])``.

        The constraint's KIND decides the reading. A ``match`` is a FILTER: it selects
        the edges that satisfy it. A ``check`` is an ASSERTION: it flags the edges that
        FAIL it -- ``e.length == 0.22`` flags every edge that is not 0.22 long,
        ``e.length >= 0.1`` flags the shorter ones, and an edge of exactly 0.1 passes;
        ``e.length <= 80`` flags the longer ones, not one of exactly 80."""
        out = []
        for edge, kind, form, lo, hi in self.length_facts():
            if kind != "check":                       # filter: the edges that satisfy it
                out.append((edge, "without" if form == "not" else "with", lo, hi))
            elif form == "exact":
                out.append((edge, "without", lo, hi))
            elif form == "not":
                out.append((edge, "with", lo, hi))
            elif lo is not None and hi is not None:   # must lie in [lo, hi]: `<= hi` keeps hi itself
                more = self.LENGTH_STEP if self._length_ops.get((edge, kind, False)) == "<=" else 0
                out.append((edge, "without", lo, round(hi + more, 6)))
            elif lo is not None:                      # must be >= lo -> the shorter fail
                # the flagged range leaves its upper end out: exactly lo passes `>=`;
                # for `>` it fails too -- one database unit more
                more = self.LENGTH_STEP if self._length_ops.get((edge, kind, True)) == ">" else 0
                out.append((edge, "with", None, round(lo + more, 6)))
            else:                                     # must be <= hi -> the longer fail
                # ... and includes its lower end: exactly hi passes `<=`, so start above it
                more = self.LENGTH_STEP if self._length_ops.get((edge, kind, False)) == "<=" else 0
                out.append((edge, "with", round(hi + more, 6), None))
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
        """A point strictly INSIDE the shape. The vertex average when that is inside
        (every convex shape); for a concave outline (L / U) whose average falls in
        the notch, the middle of the widest interior run on a scanline instead."""
        poly = self.polygon(shape)
        n = len(poly)
        c = (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
        if topology.contains(poly, c):
            return c
        ys = sorted({p[1] for p in poly})
        best = None                              # widest interior run over the scanlines
        for y0, y1 in zip(ys, ys[1:]):
            y = (y0 + y1) / 2
            xs = sorted(a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1])
                        for a, b in zip(poly, poly[1:] + poly[:1])
                        if (a[1] <= y) != (b[1] <= y))
            for xa, xb in zip(xs, xs[1:]):
                p = ((xa + xb) / 2, y)
                if xb > xa and topology.contains(poly, p) and (best is None or xb - xa > best[0]):
                    best = (xb - xa, p)
        return best[1] if best else c

    def _edge_ends(self, edge_id):
        for e in self.shapes[self._edge_owner[edge_id]]["edges"]:
            if e["id"] == edge_id:
                return (tuple(map(float, e["start"])), tuple(map(float, e["end"])))

    def measure_spots(self, mname):
        """Where a measure sits: one point on each of its two edges. By default the
        middle of the stretch where the edges face each other; a measure may say
        ``"at": c`` -- the coordinate ALONG its (axis-parallel) edges where the arrow
        is drawn, as in a manual's figure the arrow is put on a chosen part of an edge."""
        m = self.measures[mname]
        a, b = self._facing_points(m["from"], m["to"])
        c = m.get("at")
        if c is None:
            return a, b
        A, B = self._edge_ends(m["from"]), self._edge_ends(m["to"])
        vert = lambda E: E[0][0] == E[1][0]         # noqa: E731  (x constant)
        horz = lambda E: E[0][1] == E[1][1]         # noqa: E731
        clamp = lambda v, lo, hi: max(min(lo, hi), min(max(lo, hi), v))   # noqa: E731
        if vert(A) and vert(B):
            return ((A[0][0], clamp(c, A[0][1], A[1][1])), (B[0][0], clamp(c, B[0][1], B[1][1])))
        if horz(A) and horz(B):
            return ((clamp(c, A[0][0], A[1][0]), A[0][1]), (clamp(c, B[0][0], B[1][0]), B[0][1]))
        return a, b

    def _just_inside(self, shape, p):
        """A point of `shape` right next to the point `p` on its boundary."""
        poly = self.polygon(shape)
        xs, ys = [q[0] for q in poly], [q[1] for q in poly]
        eps = 1e-4 * (math.hypot(max(xs) - min(xs), max(ys) - min(ys)) or 1.0)
        for dx, dy in ((eps, 0), (-eps, 0), (0, eps), (0, -eps),
                       (eps, eps), (-eps, eps), (eps, -eps), (-eps, -eps)):
            q = (p[0] + dx, p[1] + dy)
            if topology.contains(poly, q):
                return q
        return p

    def measure_derivations(self, mname):
        """`check_derivations` for the two shapes of a distance measure, read AT THE
        MEASURE: what combines with a shape is what is drawn over the spot where the
        arrow touches it (for a width: over the middle of the arrow) -- not whatever
        happens to cover the shape's centre."""
        m = self.measures[mname]
        sf, st = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
        if not self.measure_is_straight(mname) or m.get("crossed"):
            return self.check_derivations(sf, st)
        # the other arrow of a "one of two sides" pair goes to a neighbour that is part
        # of the same statement, not a context of this one
        for ba, bb, _ in self.either_bounds():
            if mname in (ba.var, bb.var):
                pm = self.measures[bb.var if mname == ba.var else ba.var]
                self._partners = {self.shape_of_edge(pm["from"]), self.shape_of_edge(pm["to"])} - {sf, st}
        a, b = self.measure_spots(mname)
        if sf == st:
            self._anchors = {sf: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)}
        else:
            self._anchors = {sf: self._just_inside(sf, a), st: self._just_inside(st, b)}
        try:
            df, dt, used = self.check_derivations(sf, st)
        finally:
            self._anchors, self._partners = {}, set()
        # a LINK (a wavy arrow) drawn between the two measured shapes, beside the distance:
        # the shape it starts at is one that lies OUTSIDE every shape of the other's layer
        # ("NCOMP outside the drift": not the source that the drift itself reaches under)
        if sf != st and self.relation(sf, st) in ("disjoint", "outside"):
            for k, lm in self.measures.items():
                if k == mname or lm.get("crossed") or self.measure_is_straight(k) or not lm.get("from"):
                    continue
                ends = (self.shape_of_edge(lm["from"]), self.shape_of_edge(lm["to"]))
                if set(ends) != {sf, st}:
                    continue
                other = dict(df, base=self.layer_of(ends[1]), ands=[], nots=[], wide=None, form=None, area=None,
                             array=None, nets=False, count=None, holes=False, pre_selections=[], selections=[], halo=0)
                if ends[0] == sf:
                    df = dict(df, selections=list(df["selections"]) + [("outside", other)])
                else:
                    dt = dict(dt, selections=list(dt["selections"]) + [("outside", other)])
        # a FILTER "closer than v" from one of the two to a third shape: only those of it
        # that have such a one that near (it overlaps the third one grown by v)
        for k, (mine, third, b) in self.near_filters().items():
            if k == mname:
                continue
            grown = dict(df, base=self.layer_of(third), ands=[], nots=[], wide=None, form=None, area=None, array=None,
                         nets=False, count=None, holes=False, pre_selections=[], selections=[],
                         halo=round(b.value + (self.LENGTH_STEP if b.op == "<=" else 0), 6))
            if mine not in (sf, st):                 # the filter is on what holds them: those lying in such a one
                if not any(self.relation(mine, s) == "contains" for s in (sf, st)):
                    continue
                holder = dict(grown, base=self.layer_of(mine), halo=0, selections=[("overlapping", grown)])
                if self.relation(mine, sf) == "contains":
                    df = dict(df, selections=list(df["selections"]) + [("inside", holder)])
                if st != sf and self.relation(mine, st) == "contains":
                    dt = dict(dt, selections=list(dt["selections"]) + [("inside", holder)])
                used = used | {third}
                continue
            if mine == sf:
                df = dict(df, selections=list(df["selections"]) + [("overlapping", grown)])
            if mine == st:                           # (a width: the one shape is both ends)
                dt = dict(dt, selections=list(dt["selections"]) + [("overlapping", grown)])
            used = used | {third}
        return df, dt, used

    def measure_shield(self, mname) -> list:
        """Shapes a minimum-distance arrow passes right THROUGH on its way between two
        shapes that are apart -- a third shape lying between them, apart from both: where
        it lies across the whole way from one to the other, the rule is lifted (a guard
        between a device and what it must keep away from)."""
        m = self.measures.get(mname)
        if not m or m.get("crossed") or not self.measure_is_straight(mname):
            return []
        sf, st = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
        if sf is None or st is None or sf == st or self.relation(sf, st) not in ("disjoint", "outside"):
            return []
        a, b = self.measure_spots(mname)
        out = []
        for name in self.shapes:
            if name in (sf, st) or not self.layer_of(name):
                continue
            if any(self.relation(name, x) not in ("disjoint", "outside") for x in (sf, st)):
                continue
            poly = self.polygon(name)
            if topology.contains(poly, a) or topology.contains(poly, b):
                continue
            if any(topology.contains(poly, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                   for t in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)):
                out.append(name)
        return out

    def measure_through(self, mname):
        """The shape an arrow runs THROUGH on its way from one edge to the other -- it
        ends on that shape's far side -- or None when the two edges simply face each
        other. For a maximum distance this is the difference between "every part of it
        within reach" (measured to the far side) and "it has one within reach"."""
        m = self.measures[mname]
        sf, st = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
        if sf == st:
            return None
        a, b = self.measure_spots(mname)
        for t in (0.25, 0.5, 0.75):
            p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            for shape in (sf, st):
                if topology.contains(self.polygon(shape), p):
                    return shape
        return None

    def _facing_points(self, ea, eb):
        """The pair of points, one on each edge, where a measure between the two edges
        sits: the points of the edges closest to each other, centred on the stretch
        where they face one another."""
        def clamp_onto(p, seg):
            (x0, y0), (x1, y1) = seg
            dx, dy = x1 - x0, y1 - y0
            L2 = dx * dx + dy * dy or 1.0
            t = max(0.0, min(1.0, ((p[0] - x0) * dx + (p[1] - y0) * dy) / L2))
            return (x0 + t * dx, y0 + t * dy)
        A, B = self._edge_ends(ea), self._edge_ends(eb)
        # the stretch of A that B projects onto, and its middle
        pa = [clamp_onto(q, A) for q in B]
        a = ((pa[0][0] + pa[1][0]) / 2, (pa[0][1] + pa[1][1]) / 2)
        return a, clamp_onto(a, B)

    def _edge_mid(self, edge_id):
        name = self._edge_owner.get(edge_id)
        for e in self.shapes[name]["edges"]:
            if e["id"] == edge_id:
                return ((e["start"][0] + e["end"][0]) / 2, (e["start"][1] + e["end"][1]) / 2)
        return None

    def device_point(self, shape):
        """The point that represents WHERE this shape is checked: the midpoint of an
        intra-shape (width) measure, else the centroid. Whether another layer
        covers THIS point is what decides combine (.and) vs remove (.not) -- you
        position the measurement in the overlap or in the layer-free part."""
        if shape in self._anchors:               # a measure being read says where
            return self._anchors[shape]
        for m in self.measures.values():
            if m.get("of_shape"):
                continue                         # a cross over the shape: no position of its own
            if self.shape_of_edge(m["from"]) == shape == self.shape_of_edge(m["to"]):
                if m.get("at") is not None:          # the arrow is put on a chosen part of the edges
                    fm, tm = self.measure_spots(m["name"]) if "name" in m else (self._edge_mid(m["from"]), self._edge_mid(m["to"]))
                else:
                    fm, tm = self._edge_mid(m["from"]), self._edge_mid(m["to"])
                return ((fm[0] + tm[0]) / 2, (fm[1] + tm[1]) / 2)
        return self.interior_point(shape)

    def covering(self, shape):
        """Other shapes whose polygon contains this shape's DEVICE point -- the
        measured region sits inside them, so they COMBINE into its layer (.and).

        A shape marked ``"whole": true`` stands for its layer wherever it is, whatever
        happens to lie under it in the picture ("space to poly", drawn at a gate)."""
        if self.shapes[shape].get("whole"):
            return []
        pt = self.device_point(shape)
        return [s for s in self.shapes
                if s != shape and topology.contains(self.polygon(s), pt)]

    def _has_straight_edge(self, shape):
        return any(e.get("edge") for e in self.shapes[shape]["edges"])

    def removed(self, shape):
        """Other shapes that sit INSIDE this shape (cutouts) but do NOT cover its
        device point, AND are a real DEVICE layer (>=1 straight edge -- its boundary
        cuts the result). The measured region is in the layer-free part, so they are
        SUBTRACTED (.not). A real cutout shape, not a tagged hole. An all-wavy inside
        shape is a domain marker (a filter), NOT a removal."""
        pt = self.device_point(shape)
        out = []
        for s in self.shapes:
            if s == shape or topology.contains(self.polygon(s), pt):
                continue                         # that's a combine, not a remove
            if self.relation(shape, s) == "contains" and self._has_straight_edge(s):
                out.append(s)
        return out

    def hole_layers(self, shape):
        """Layers tagged on this shape's holes -- each is SUBTRACTED from the
        shape's (derived) layer (a hole = NOT that layer). Legacy encoding; the
        natural form is a real cutout shape (see `removed`)."""
        return [h["layer"] for h in self.shapes[shape].get("holes", []) if h.get("layer")]

    # --- constraint facts (mined from the parsed ASTs) ---
    def layer_of(self, shape):
        """The shape's layer: a name from ``A.layer == metal1``, or -- for a layer SET,
        ``A.layer in {otp_mk, ymtp_mk}`` or ``A.layer == otp_mk or A.layer == ymtp_mk``
        ("any of these") -- the names joined with
        ``|`` in sorted order (see `split_layers`). None if the rule doesn't say."""
        v = self._attr_eq(shape, "layer")
        if v is not None:
            return v
        target = f"{shape}.layer"
        for _, ast in self.constraints:
            if isinstance(ast, dict) and ast.get("op") == "in":
                a, b = ast["args"]
                if a.get("ref") == target and "set" in b:
                    return "|".join(sorted(e["ref"] for e in b["set"]))
            # the same set spelled out: ``A.layer == diff or A.layer == tap``
            if isinstance(ast, dict) and ast.get("op") == "or":
                names = [_eq_value(x, target) for x in ast["args"]]
                if all(isinstance(n, str) for n in names):
                    return "|".join(sorted(names))
        return None

    def overlapping_markers(self, context, exclude):
        """Layers of shapes that OVERLAP the `context` marker shape (and aren't an
        operand/instance in `exclude`) -- i.e. a DERIVED marker drawn as overlapping
        real layers, e.g. a gate = poly2 over comp => the marker is poly2 & comp =
        tgate. The compiler ANDs these into the marker layer (the combine recipe,
        applied to a context). Returns distinct helper layers, sorted."""
        base = self.layer_of(context)
        out = set()
        for name in self.shapes:
            if name == context or name in exclude:
                continue
            if self.relation(context, name) in ("overlap", "inside", "contains"):
                ml = self.layer_of(name)
                if ml and ml != base:
                    out.add(ml)
        return sorted(out)

    # --- the analyzed "derived layer" view: the single source both backends FORMAT ---
    def check_derivations(self, sf, st):
        """For a measured check between shapes `sf` and `st`, the OBJECTIVE derived-
        layer analysis of each, plus the set of shapes CONSUMED (so they're neither
        AND-ed nor treated as scope). Returns ``(deriv(sf), deriv(st), consumed)``. A
        derivation is ``{base, ands, nots, pre_selections, selections}`` (layer NAMES),
        where each selection is ``(op, marker)`` and the marker is itself a derivation;
        ``pre_selections`` are the ones that apply before the cutouts (see `deriv`). Both
        backends format this and never re-derive it, so the .drc and the English
        describe the same facts. Encapsulates cover->and, cutout->not, wavy-measure->
        selection (op via the labeled-instances table), and selected-marker recovery."""
        measured = {sf, st}
        lying = set(self.edges_lying_on(sf, measured)) | set(self.edges_lying_on(st, measured))
        counted = set() if getattr(self, "_reading_counted", False) else (
            {k for k, _, _ in self.count_filters().values()}             # what a filter counts,
            | {k for _, k, _, _ in self._count_checks_cached()})           # ... or a check
        consumed = set(self._partners) | (counted - measured) | lying   # is not a context
        sel = {sf: [], st: []}
        for sa, sb in self.relation_selections():
            for subj in (sf, st):
                other = sb if sa == subj else sa if sb == subj else None
                if (other is not None and other not in measured and other not in self._partners
                        and other not in counted):
                    sel[subj].append(self._marker(subj, other, measured, consumed))
                    consumed.add(other)
                    break
        scope_consumed = set(consumed)
        for ms in measured:
            scope_consumed |= {s for s in (self.covering(ms) + self.removed(ms))
                               if s not in measured and s not in consumed}
        # a leftover shape whose layer is ALSO an operand layer can't be a scope marker
        # (it would drop everything) -> read it as a selection on the subject.
        operand_layers = {self.layer_of(sf), self.layer_of(st)}
        for name in self.shapes:
            if name in measured or name in scope_consumed:
                continue
            ml = self.layer_of(name)
            if ml and ml in operand_layers:
                sel[sf].append(self._marker(sf, name, measured, consumed))
                consumed.add(name)
                scope_consumed.add(name)

        def deriv(shape):
            ex = measured | consumed
            ands = sorted({self.layer_of(c) for c in self.covering(shape)
                           if c not in ex and self.layer_of(c) not in (None, self.layer_of(shape))})
            # removing a SET of layers is removing each of them
            nots = sorted({l for c in self.removed(shape) if c not in ex
                           for l in split_layers(self.layer_of(c))}
                          | set(self.hole_layers(shape)) | set(self.excepted()))
            # A relation to layer Y is drawn against the shape AS DRAWN. If Y is also cut
            # out of that shape, the relation can only be read BEFORE the cut: what is
            # left after removing Y can no longer overlap or sit inside Y. So a plain
            # selection against a cut-out layer is applied first ("the part of A outside
            # Y, for an A that overlaps Y" -- a shape straddling Y's edge).
            pre = [x for x in sel[shape] if x[1]["base"] in nots and not (
                x[1]["ands"] or x[1]["nots"] or x[1]["selections"])]
            return {"base": self.layer_of(shape), "ands": ands, "nots": nots,
                    "wide": self.wide_filters().get(shape),
                    "wide_strict": self._wide_strict.get(shape, False),
                    "form": self.shape_forms().get(shape),
                    "area": self.area_filters().get(shape),
                    "array": self.array_filters().get(shape),
                    "nets": self.net_relation(sf, st) is not None,
                    # a filter "holding so many K" where K is the other shape measured says nothing more
                    "count": (lambda c: c and c[0] not in measured and (self.layer_of(c[0]), c[1], c[2]))(
                        self.count_filters().get(shape)),
                    "holes": (lambda c: bool(c) and c[0] not in measured and self.in_hole(c[0], shape))(
                        self.count_filters().get(shape)),
                    "pre_selections": pre,
                    "selections": [x for x in sel[shape] if x not in pre],
                    "halo": self.halo(shape), "rounded": self.rounded(shape)}
        return deriv(sf), deriv(st), measured | scope_consumed

    def _cut_layers(self, layer, seen=()):
        """Layers the LEGEND removes to make `layer` ("n_activ = activ and not psd")."""
        out = set()

        def walk(ast, neg):
            if not isinstance(ast, dict):
                return
            if "ref" in ast:
                if neg:
                    out.add(ast["ref"])
                elif ast["ref"] in self.layer_defs and ast["ref"] not in seen:
                    out.update(self._cut_layers(ast["ref"], (*seen, layer)))
            elif ast.get("op") == "not":
                walk(ast["args"][0], not neg)
            elif ast.get("op") in ("and", "or"):
                for a in ast["args"]:
                    walk(a, neg)
        for l in split_layers(layer or ""):
            if l in self.layer_defs:
                walk(self.layer_defs[l], False)
        return out

    def cut_apart(self, da, db) -> bool:
        """Is one of two derived layers what is LEFT of something after removing the
        other ("activ outside psd" against psd)?  Then the two meet edge to edge
        wherever the cut runs -- by construction, not by a designer's choice -- and
        that zero distance is not a spacing between them."""
        def cut(d, other):
            return other["base"] in (set(d["nots"]) | self._cut_layers(d["base"]))
        return bool(da["base"] and db["base"]) and (cut(da, db) or cut(db, da))

    def overlap_reading(self, mname):
        """How to read a distance measured between two shapes that PARTLY OVERLAP --
        the picture a person draws for an extension / overhang / butted-region rule:
        one layer crossing or partly covering another, and a measure from the edge of
        one to an edge of the other. Plain space/separation is meaningless there, so
        the two things the drawing shows decide instead:

        * WHAT COVERS EACH MEASURED EDGE. An edge of X drawn inside C is an edge of the
          region X-and-C (poly over diffusion is the gate; pplus over comp is pcomp);
          any other layer drawn over a measured edge combines the same way.
        * WHERE THE MEASURE LIES. Inside C but outside X, it is how far C reaches past
          X -- C ENCLOSES the part of X inside it (``extension``). A further layer over
          that stretch which X does not reach (nplus butted against pplus) scopes the
          rule to where it is. Inside both shapes, the measure is the depth of the
          OVERLAP (``overlap``).

        Returns ``{"kind", "outer", "inner", "consumed"}`` (derivations as in
        `check_derivations`; for ``overlap`` outer/inner are just from/to), or None
        when the measure is not of this form."""
        m = self.measures.get(mname)
        if not m or m.get("crossed") or not self.measure_is_straight(mname):
            return None
        sf, st = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
        if sf is None or st is None or sf == st or self.relation(sf, st) != "overlap":
            return None
        # where on each edge the measure sits: the two points facing each other (an
        # edge's own midpoint may lie far from the other shape)
        af, at = self.measure_spots(mname)
        mid = ((af[0] + at[0]) / 2, (af[1] + at[1]) / 2)
        span = math.hypot(at[0] - af[0], at[1] - af[1]) or 1.0
        in_f = topology.contains(self.polygon(sf), mid)
        in_t = topology.contains(self.polygon(st), mid)
        if not (in_f or in_t):
            return None                              # the measure lies in neither shape

        def anchor(a, inside):
            # a point just off the measured edge, on the shape's own side of it
            k = (1 if inside else -1) * 1e-3 * span
            return (a[0] + (mid[0] - a[0]) / span * 2 * k, a[1] + (mid[1] - a[1]) / span * 2 * k)

        self._anchors = {sf: anchor(af, in_f), st: anchor(at, in_t)}
        try:
            d_f, d_t, consumed = self.check_derivations(sf, st)
            cov = {sh: [c for c in self.covering(sh) if c not in (sf, st)] for sh in (sf, st)}
        finally:
            self._anchors = {}
        if in_f and in_t:                            # the depth two layers overlap by
            return {"kind": "overlap", "outer": d_f, "inner": d_t, "consumed": consumed,
                    "outer_shape": sf, "inner_shape": st}
        outer, inner, d_out, d_in = (st, sf, d_t, d_f) if in_t else (sf, st, d_f, d_t)
        # A layer over the outer's measured edge that stops short of the inner shape
        # (nplus butted against pplus) is not part of the outer region -- combined with
        # it, the outer could no longer surround the inner. It says WHERE the extension
        # is looked at, so it is left as a scope on the violations.
        apart = [c for c in cov[outer] if self.relation(inner, c) in ("touch", "disjoint")]
        if apart:
            gone = {self.layer_of(c) for c in apart}
            d_out = dict(d_out, ands=[a for a in d_out["ands"] if a not in gone])
            consumed = consumed - set(apart)
        # C reaches past X: an enclosure, which only ever looks at the edges of X that
        # lie inside C -- so X needs no clipping
        return {"kind": "extension", "outer": d_out, "inner": d_in, "consumed": consumed,
                "outer_shape": outer, "inner_shape": inner}

    def edge_crosses(self, edge_id, shape):
        """How the straight edge `edge_id` lies against another `shape` AS DRAWN:
        ``"across"`` (it crosses the shape's boundary: one end inside, one outside),
        ``"inside"`` (wholly inside it) or ``"outside"``. This is how a drawing says WHICH
        edge of a shape is meant -- "the extension in the width direction" is past the
        edges of the diffusion that run out from under the gate; "towards the drain" is
        past the one that lies under it."""
        (ax, ay), (bx, by) = self._edge_ends(edge_id)
        poly = self.polygon(shape)
        # sampled along its length: an edge the other shape runs across (a gate over a
        # diffusion) is "across" as much as one that ends under it
        ends = [topology.contains(poly, (ax + (bx - ax) * t / 50, ay + (by - ay) * t / 50)) for t in range(1, 50)]
        return "inside" if all(ends) else "across" if any(ends) else "outside"

    def edge_cut_by(self, edge_id, shape) -> bool:
        """Does the straight edge `edge_id` cross a STRAIGHT edge of another `shape` as
        drawn?  (A wavy edge is an arbitrary boundary: crossing it says nothing.)"""
        (ax, ay), (bx, by) = self._edge_ends(edge_id)

        def side(px, py, qx, qy, rx, ry):
            return (qx - px) * (ry - py) - (qy - py) * (rx - px)
        for e in self.shapes[shape]["edges"]:
            if not e.get("edge"):
                continue
            (cx, cy), (dx, dy) = e["start"], e["end"]
            if (side(ax, ay, bx, by, cx, cy) * side(ax, ay, bx, by, dx, dy) < 0
                    and side(cx, cy, dx, dy, ax, ay) * side(cx, cy, dx, dy, bx, by) < 0):
                return True
        return False

    def edges_lying_on(self, shape, measured=()) -> list:
        """Shapes an edge measured on `shape` LIES ALONG (a straight edge of the arrow's
        shape drawn on an edge of another shape: an implant edge butting a diffusion).
        That edge is then the shared one: "at a butting edge"."""
        mine = [m["from"] for m in self.measures.values()     # the edge the arrow starts at
                if m.get("edges") and self.shape_of_edge(m["from"]) == shape]
        out = []
        for other in self.shapes:
            if other == shape or other in measured:
                continue
            for eid in mine:
                (ax, ay), (bx, by) = self._edge_ends(eid)
                for e in self.shapes[other]["edges"]:
                    (cx, cy), (dx, dy) = e["start"], e["end"]
                    same_line = ((ax == bx == cx == dx) or (ay == by == cy == dy))
                    if same_line and max(min(ax, bx), min(cx, dx)) <= min(max(ax, bx), max(cx, dx)) \
                            and max(min(ay, by), min(cy, dy)) <= min(max(ay, by), max(cy, dy)) \
                            and (ax, ay) != (bx, by):
                        overlap = (min(max(ax, bx), max(cx, dx)) - max(min(ax, bx), min(cx, dx))
                                   + min(max(ay, by), max(cy, dy)) - max(min(ay, by), min(cy, dy)))
                        if overlap > 0 and other not in out:
                            out.append(other)
        return out

    def marker_parts(self, consumed, primary) -> dict:
        """What a scope MARKER (a shape that is neither measured nor combined into the
        checked layers) is made of, beyond its layer: ``{marker: {"holds": [shapes],
        "another": layer | None}}``.

        ``holds`` -- other such shapes drawn INSIDE it with a link from it: the marker is
        "one that holds these" (a DNWELL with an NCOMP tab in it), and they are not
        markers of their own.  ``another`` -- the marker is drawn apart from the checked
        shape but is of the layer of a shape that covers it (a second DNWELL beside the
        one the device is in): it is ANOTHER one, one that does not touch the checked
        layer; the name of that layer."""
        free = [n for n in self.shapes if n not in consumed and self.layer_of(n)]
        linked = {frozenset((self.shape_of_edge(m.get("from")), self.shape_of_edge(m.get("to"))))
                  for m in self.measures.values() if m.get("from") and m.get("to") and not m.get("crossed")}
        out, held = {}, set()
        for n in free:
            holds = [k for k in free if k != n and frozenset((n, k)) in linked and self.relation(k, n) == "inside"]
            held |= set(holds)
            own = any(c in consumed and c != primary and self.layer_of(c) == self.layer_of(n)
                      and self.relation(primary, c) in ("inside", "overlap") for c in self.shapes)
            another = self.layer_of(primary) if own and self.relation(primary, n) in ("disjoint", "outside") else None
            out[n] = {"holds": holds, "another": another}
        return {n: v for n, v in out.items() if n not in held}

    def edge_angle_filter(self, shape) -> list:
        """The directions a FILTER names for the straight edges of `shape`
        (``match g.angle == 45 or g.angle == 135``: "the 45-degree edges"), as angles in
        [0, 180); empty when no filter says so."""
        out = []
        for kind, ast in self.constraints:
            if kind == "check":
                continue
            for ref, op, num in _attr_cmps(ast, ".angle"):
                if op == "==" and self.shape_of_edge(ref) == shape and num % 180 not in out:
                    out.append(num % 180)
        return sorted(out)

    def edge_shared_with(self, edge_id):
        """The other shape (of another layer) whose edge lies ALONG this straight edge
        -- two shapes drawn butting: a length on that edge is the length of the stretch
        they share. None if the edge is not shared."""
        mine = self.shape_of_edge(edge_id)
        if mine is None or not self._edge_straight(edge_id):
            return None
        (ax, ay), (bx, by) = self._edge_ends(edge_id)
        for other, sh in self.shapes.items():
            if other == mine or self.layer_of(other) == self.layer_of(mine) or self.relation(mine, other) != "touch":
                continue
            for e in sh["edges"]:
                (cx, cy), (dx, dy) = e["start"], e["end"]
                if ax == bx == cx == dx and min(max(ay, by), max(cy, dy)) > max(min(ay, by), min(cy, dy)):
                    return other
                if ay == by == cy == dy and min(max(ax, bx), max(cx, dx)) > max(min(ax, bx), min(cx, dx)):
                    return other
        return None

    def edge_kinds(self, edge_id, but=()) -> list:
        """``[(other shape, crosses it)]`` for every other shape that tells the edges of
        this edge's shape APART: a straight edge of it crosses some of them and not
        others. The measured edge is then one of those that do (True) or do not."""
        mine = self.shape_of_edge(edge_id)
        out = []
        for other in self.shapes:
            if other == mine or other in but:
                continue
            cuts = {self.edge_cut_by(e["id"], other) for e in self.shapes[mine]["edges"] if e.get("edge")}
            if self.edge_cut_by(edge_id, other):         # drawn across the measured edge itself
                out.append((other, True))
            elif cuts == {True, False}:                  # ... across others of its edges, not this one
                out.append((other, False))
        return out

    def _marker(self, subj, other, measured, consumed):
        """``(op, marker-derivation)`` for a wavy-measured CONTEXT `other` of `subj`:
        the selection op (from the labeled-instances table when counter-examples are
        present, else the drawn relation) and the context's own derivation -- doping
        that WHOLLY covers it (relation inside), its own cutouts (excluding the
        subject's), and its own contexts. Mutates `consumed` with what it claims."""
        ml = self.layer_of(other)
        insts = self.instances_relating_to(self.layer_of(subj), other)
        dropped = {self.relation(s, other) for s, f in insts if not f}
        ands, nots, sels = [], [], []
        if dropped:
            kept = {self.relation(s, other) for s, f in insts if f}
            op = lookup_sel(kept, dropped) or rel_to_sel(self.relation(subj, other))
            consumed.update(s for s, _ in insts)
            for s, f in insts:
                if not f:
                    consumed.update(self.covering(s))
            helpers = self.overlapping_markers(other, measured | {s for s, _ in insts})
            ands += helpers
            for h in helpers:
                consumed.update(s for s in self.shapes if self.layer_of(s) == h
                                and self.relation(other, s) in ("overlap", "inside", "contains"))
        else:
            op = rel_to_sel(self.relation(subj, other))
        if self._straddle_cross(subj, other):
            op = "straddling"
        measure_pts = set()
        for mm in self.measures.values():
            measure_pts.add(self.shape_of_edge(mm["from"]))
            measure_pts.add(self.shape_of_edge(mm["to"]))
        subj_covers = {c for s in measured for c in self.covering(s)}
        subj_removed = {c for s in measured for c in self.removed(s)}
        own_ctx = {c for sa, sb in self.relation_selections()
                   for c in (sb if sa == other else sa if sb == other else None,) if c}
        for d in self.covering(other):
            dl_ = self.layer_of(d)
            if (d not in measure_pts and d not in subj_covers and d != other
                    and d not in consumed and dl_ and dl_ != ml
                    and self.relation(other, d) == "inside"):
                ands.append(dl_)
                consumed.add(d)
        for d in self.removed(other):
            dl_ = self.layer_of(d)
            if (dl_ and dl_ != ml and d not in measure_pts and d not in consumed
                    and d not in subj_removed):
                nots += split_layers(dl_)
                consumed.add(d)
        for c in sorted(own_ctx - measured - {s for s, _ in insts}):
            if self.relation(other, c) not in ("contains", "equal"):
                sels.append((rel_to_sel(self.relation(other, c)),
                             {"base": self.layer_of(c), "ands": [], "nots": [], "selections": [],
                              "shape": c}))
                consumed.add(c)
        consumed.add(other)
        return op, {"base": ml, "ands": ands, "nots": nots, "selections": sels,
                    "halo": self.halo(other), "rounded": self.rounded(other),
                    "shape": other}

    def eol_measures(self):
        """End-of-line measures, recovered from GEOMETRY (no flag): a measure whose
        FROM edge is a wire CAP -- a straight edge whose opposite edge in the loop is
        WAVY (the 'continues' end) -- carrying a MAX edge-length bound (the EOL width
        Weol), with a side edge carrying a MIN edge-length bound (the min run length
        Slen). Returns [(var, cap_edge, side_edge)]. Both thresholds are ordinary
        edge-length constraints; nothing about EOL is stashed or flagged."""
        lb = self.length_spans()
        out = []
        for v, mm in self.measures.items():
            cap = mm.get("from")
            if mm.get("crossed") or cap is None or lb.get(cap, (None, None))[1] is None:
                continue
            es = [e["id"] for e in self.shapes[self.shape_of_edge(cap)]["edges"]]
            if cap not in es or len(es) != 4 or not self._edge_straight(cap):
                continue
            i = es.index(cap)
            if self._edge_straight(es[(i + 2) % 4]):      # opposite edge must be WAVY
                continue
            side = next((s for s in (es[(i + 1) % 4], es[(i - 1) % 4])
                         if lb.get(s, (None, None))[0] is not None), None)
            out.append((v, cap, side))                    # no length given for the sides: any line end
        return out

    def eol_strict(self, cap) -> bool:
        """Is the end-of-line width written ``<`` ("narrower than"), not ``<=``?"""
        self.length_facts()
        return any(op == "<" for (e, _, is_min), op in self._length_ops.items() if e == cap and not is_min)

    def checked_pairs(self):
        """``[(subject, operand, measure)]`` for everything the rule checks or flags: each
        bounded distance, each cross, each area / length / corner constraint (operand
        == subject when only one shape is involved)."""
        out = []
        for name, m in self.measures.items():
            sf, st = self.shape_of_edge(m.get("from")), self.shape_of_edge(m.get("to"))
            if sf is None or st is None:
                continue
            if m.get("crossed"):
                out.append((sf, sf, None))
            elif self.measure_is_straight(name) and any(
                    b.var == name and (b.kind == "check" or sf != st or not b.is_min)
                    for b in self.bounds()):
                out.append((sf, st, name))
        out += [(sh, sh, None) for sh, *_ in self.area_bounds()]
        out += [(a, b, None) for a, b in self.inside_checks()]
        for facts in (self.length_facts(), self.corner_facts(), self.angle_bounds()):
            out += [(self.shape_of_edge(f[0]),) * 2 + (None,)
                    for f in facts if self.shape_of_edge(f[0])]
        seen = []
        return [p for p in out if not (p[:2] in seen or seen.append(p[:2]))]

    def shape_roles(self) -> dict:
        """How the compiler READS each drawn shape: ``{shape: role sentence}``. The
        same picture can mean different things depending on a link or a straight edge,
        so an editor shows this next to every shape instead of leaving it implicit."""
        roles = {}
        scope_word = {"disjoint": "away from", "touch": "touching"}
        sel_word = {"inside": "inside", "outside": "outside", "interacting": "touching or overlapping",
                    "overlapping": "overlapping", "not_interacting": "clear of",
                    "not_inside": "not inside"}
        crossed = {pair[1]: pair[0] for pair in map(self.crossed_relation, self.measures) if pair}

        def markers(owner, deriv):
            for op, mk in deriv.get("pre_selections", []) + deriv["selections"]:
                ctx = mk.get("shape")
                if ctx and ctx not in roles:
                    roles[ctx] = (f"forbidden: {owner} must not be {sel_word.get(op, op)} this"
                                  if crossed.get(ctx) == owner else
                                  f"filter: only {owner} {sel_word.get(op, op)} this")
                    markers(ctx, mk)

        pairs = self.checked_pairs()
        over = {var: self.overlap_reading(var) for _, _, var in pairs if var}
        for sf, st, var in pairs:
            o = over.get(var)
            if o:                                    # partly overlapping: who reaches past whom
                a, b = o["outer_shape"], o["inner_shape"]
                roles.setdefault(a, f"subject: must overlap {b} deeply enough" if o["kind"] == "overlap"
                                 else f"subject: must reach far enough past {b}")
                roles.setdefault(b, "operand: what the subject is checked against")
                continue
            roles.setdefault(sf, "subject: what the rule checks")
            if st != sf:
                roles.setdefault(st, "operand: what the subject is checked against")
        for sf, st, var in pairs:
            o = over.get(var)
            if o:
                d_sf, d_st, consumed = o["outer"], o["inner"], o["consumed"]
                sf, st = o["outer_shape"], o["inner_shape"]
            else:
                d_sf, d_st, consumed = self.check_derivations(sf, st)
            markers(sf, d_sf)
            if st != sf:
                markers(st, d_st)
            if o:
                for name in self.shapes:             # layers combined into either region
                    if name not in roles and name in consumed:
                        roles[name] = "combined: the rule is on the region under this (and)"
            for ms in ({sf, st} if not o else ()):
                for c in self.covering(ms):
                    roles.setdefault(c, f"combined: the rule is on {ms} under this (and)")
                for c in self.removed(ms):
                    roles.setdefault(c, f"cut out: the rule is on {ms} outside this (not)")
            for name in self.shapes:
                if name in roles:
                    continue
                if not self.is_flagged(name):
                    roles[name] = "allowed example: this arrangement is not a violation"
                elif name in consumed:
                    roles[name] = "part of a context"
                elif self.layer_of(name):
                    word = scope_word.get(self.relation(sf, name), "overlapping")
                    roles[name] = f"scope: violations only {word} this"
        for name in self.shapes:
            roles.setdefault(name, "not used by any check yet")
        return roles

    def excepted(self):
        """Layers the whole rule does not apply under -- a manual's section note ("not
        applicable inside OTP_MK"), stated once in words (``"except": [...]``) instead
        of a cutout drawn into every shape. Removed from every checked layer."""
        return list(self.data.get("except") or [])

    def eol_edges(self):
        """The cap and side edges of every end-of-line construction: their length
        constraints QUALIFY the wire end (filters), they are not rules of their own."""
        return {e for _, cap, side in self.eol_measures() for e in (cap, side) if e}

    def array_filters(self) -> dict:
        """``{shape: {"rows", "columns", "size", "gap"}}`` -- shapes a FILTER says sit in
        an ARRAY: ``match K.rows >= 4 and K.columns >= 4`` ("in a 4 x 4 or larger
        array"). The array is the group of same-layer shapes each closer to a
        neighbour than ``gap`` -- a filter on the spacing arrow says how close makes an
        array (``match S < 0.36``); without one it is the spacing the rule checks;
        ``size`` is the element's side when the rule states it (an exact length on one
        of the shape's edges, e.g. ``match k0.length == 0.22``), else None."""
        want = {}
        for kind, ast in self.constraints:
            if kind == "check":
                continue
            for key in ("rows", "columns"):
                for shape, op, num in _attr_cmps(ast, "." + key):
                    if shape in self.shapes and op in (">", ">=", "=="):
                        want.setdefault(shape, {"rows": 1, "columns": 1})[key] = int(num) + (op == ">")
        for shape, w in want.items():
            edges = {e["id"] for e in self.shapes[shape]["edges"]}
            w["size"] = next((lo for e, _, form, lo, _ in self.length_facts()
                              if e in edges and form == "exact"), None)
            w["gap"] = w["var"] = None
            w["gap_incl"] = True           # by the checked spacing: at it or closer
            near = None                    # a filter on the same arrow: "closer than this"
            either = [x for e in self.either_bounds() for x in e[:2]]
            for b in self.bounds() + either:
                m = self.measures.get(b.var)
                if not (m and self.measure_is_straight(b.var)):
                    continue
                ends = (self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"]))
                if shape not in ends or ends[0] == ends[1]:
                    continue
                if b.kind == "check" and b.is_min:
                    w["gap"], w["var"] = b.value, b.var
                elif b.kind != "check" and b.is_max:
                    near, near_var = b.value, b.var
                    w["gap_incl"] = b.op == "<="
            if near is not None and w["gap"] is not None:
                w["gap"] = near
            elif near is not None:         # no spacing is checked (a count of arrays): the filter alone
                w["gap"], w["var"] = near, near_var
        return {s: w for s, w in want.items() if w["gap"] is not None}

    def array_edges(self):
        """Edges whose exact length is the element SIZE of an array filter -- a
        qualifier of the array, not a length rule of its own."""
        arr = self.array_filters()
        return {e["id"] for s in arr for e in self.shapes[s]["edges"]
                if any(x == e["id"] and form == "exact" for x, _, form, _, _ in self.length_facts())}

    def density_bounds(self) -> list:
        """Density rules -> ``(shape, lo, hi, window)``: ``M.density >= 0.35`` and/or
        ``M.density <= 0.6`` as CHECKS (fractions; a value above 1 is a percentage).

        `window` says where the density is taken, read from the picture: a shape drawn
        AROUND the material is the window. Its exact side length (``w0.length == 800``)
        makes it a sliding window of that size, ``W.step == 400`` its step (default: the
        size); without a size the density is that of the whole area. If the window
        shape has a layer, that layer bounds the area (a chip boundary); else the
        layout's extent does. ``{"shape", "size", "step", "layer"}``, or None when no
        window is drawn (the whole layout)."""
        spans = {}
        for kind, ast in self.constraints:
            if kind != "check":
                continue
            for shape, op, num in _attr_cmps(ast, ".density"):
                if shape in self.shapes and op in (">", ">=", "<", "<="):
                    v = num / 100.0 if num > 1 else num
                    spans.setdefault(shape, [None, None])[0 if op in (">", ">=") else 1] = v
        out = []
        for shape, (lo, hi) in spans.items():
            win = next((w for w in self.shapes
                        if w != shape and self.relation(shape, w) == "inside"), None)
            window = None
            if win:
                edges = {e["id"] for e in self.shapes[win]["edges"]}
                size = next((a for e, _, form, a, _ in self.length_facts()
                             if e in edges and form == "exact"), None)
                step = self._attr_eq(win, "step")
                window = {"shape": win, "size": size, "layer": self.layer_of(win),
                          "step": step if isinstance(step, (int, float)) else None}
            out.append((shape, lo, hi, window))
        return out

    def density_strict(self, shape) -> tuple:
        """``(more than, less than)``: is the density bound written ``>`` / ``<`` rather
        than ``>=`` / ``<=``?  "Coverage shall be >30%" leaves exactly 30 % out."""
        ops = {op for kind, ast in self.constraints if kind == "check"
               for sh, op, _ in _attr_cmps(ast, ".density") if sh == shape}
        return ">" in ops, "<" in ops

    def density_edges(self):
        """Edges of a density window whose exact length is the window SIZE."""
        wins = {w["shape"] for _, _, _, w in self.density_bounds() if w}
        return {e["id"] for s in wins for e in self.shapes[s]["edges"]
                if any(x == e["id"] and form == "exact" for x, _, form, _, _ in self.length_facts())}

    def halo(self, shape):
        """A shape's HALO: a signed margin (µm) its region is grown (>0) or shrunk
        (<0) by -- the formal form of a DRM 'within X' / offset, drawn as a dashed
        offset outline. Default 0 (no halo). Compiles to `.sized(halo.um)`."""
        return self.shapes.get(shape, {}).get("halo", 0) or 0

    def rounded(self, shape):
        """Whether the halo grows with ROUNDED corners (vs square). A drawn tick."""
        return bool(self.shapes.get(shape, {}).get("rounded"))

    def on_extent(self) -> bool:
        """Is a shape of this rule drawn on the layout's own EXTENT (the die: the name
        ``extent``, or a legend name built on it)?  Such a rule speaks of the whole die --
        "no open field wider than ..." -- and says nothing of one device."""
        def refs(node, seen):
            if isinstance(node, str):
                node = {"ref": node}
            if not isinstance(node, dict):
                return False
            if "ref" in node:
                name = node["ref"]
                if name == "extent":
                    return True
                if name in seen:
                    return False
                return refs(self.layer_defs.get(name), seen | {name}) if name in self.layer_defs else False
            return any(refs(a, seen) for a in node.get("args", []))
        return any(refs(self.layer_of(s) or "", set()) for s in self.shapes)

    def plain_layer(self, name):
        """The layer a legend name merely STANDS for (``top_metal_layer`` -> ``metaltop``
        for this stack), or the name itself."""
        seen = set()
        while name not in seen:
            seen.add(name)
            d = self.layer_defs.get(name)
            if not (isinstance(d, dict) and set(d) == {"ref"}):
                break
            name = d["ref"]
        return name

    def connections(self) -> list:
        """``(layer, layer)`` pairs that CONNECT -- what makes a net. Either listed
        (``"connect"``) or DRAWN: two shapes of different layers drawn touching or
        overlapping, with the fact ``K.net == D.net`` ("where a contact sits on
        diffusion they are one net"). A net equality between shapes that are apart
        is a filter on a rule instead (`net_relation`)."""
        out = []
        for a, b in self.connect:              # a name that only stands for a layer (the top metal of this stack) IS that layer
            pair = (self.plain_layer(a), self.plain_layer(b))
            if pair not in out and pair[::-1] not in out:
                out.append(pair)
        for _, ast in self.constraints:
            for leaf in _and_leaves(ast):
                if not (isinstance(leaf, dict) and leaf.get("op") == "=="):
                    continue
                refs = [x.get("ref", "") if isinstance(x, dict) else "" for x in leaf["args"]]
                if not all(r.endswith(".net") for r in refs):
                    continue
                a, b = (r[:-4] for r in refs)
                if a in self.shapes and b in self.shapes and self._drawn_joined(a, b):
                    pair = (self.layer_of(a), self.layer_of(b))
                    if all(pair) and pair[0] != pair[1] and pair not in out and pair[::-1] not in out:
                        out.append(pair)
        return out

    def _drawn_joined(self, a, b):
        return self.relation(a, b) not in ("disjoint", "outside", None)

    def held_area_bounds(self) -> list:
        """``(holder, held, lo, hi)``: the CHECK ``Area(K) <= v`` / ``>= v`` -- the area
        of K's layer a shape holds, all of it together, in µm². The holder is read from
        the picture, as for ``Count``."""
        return [(h, k, lo, hi) for k, (h, lo, hi) in self._counts(("check",), "area").items()]

    def _counts(self, kinds, fn="count"):
        """``{counted shape: [container, lo, hi]}`` from ``Count(K) <cmp> n`` constraints
        of the given kinds. WHAT is counted in WHAT is read from the picture: K's
        layer, in the shape K is drawn inside (or, failing that, overlapping)."""
        out = {}
        for kind, ast in self.constraints:
            if kind not in kinds:
                continue
            for leaf in _and_leaves(ast):
                if not (isinstance(leaf, dict) and leaf.get("op") in ("==", "<", "<=", ">", ">=")):
                    continue
                a, b = leaf["args"]
                op = leaf["op"]
                if isinstance(b, dict) and b.get("op") == fn:
                    a, b, op = b, a, _FLIP.get(op, op)
                if not (isinstance(a, dict) and a.get("op") == fn and isinstance(b, dict) and "num" in b):
                    continue
                k = a["args"][0].get("ref")
                if k not in self.shapes:
                    continue
                holders = ([s for s in self.shapes if s != k and self.relation(k, s) == "inside"]
                           or [s for s in self.shapes if s != k and self._drawn_joined(k, s)])
                if not holders:
                    continue
                n = int(b["num"]) if fn == "count" else b["num"]
                span = out.setdefault(k, [holders[0], None, None])
                step = 1 if fn == "count" else 0     # an area is not stepped: > reads as >=
                if op in ("==", ">=", ">"):
                    span[1] = n + step * (op == ">")
                if op in ("==", "<=", "<"):
                    span[2] = n - step * (op == "<")
        return out

    def _count_checks_cached(self):
        if not hasattr(self, "_cc"):
            self._cc = None                          # guard: _counts does not come back here
            self._cc = self.count_bounds()
        return self._cc or []

    def count_bounds(self) -> list:
        """``(holder, counted, lo, hi)``: the CHECK ``Count(K) == 2`` / ``>= 2`` /
        ``<= 4`` -- every shape like the holder must hold that many of K's layer."""
        return [(h, k, lo, hi) for k, (h, lo, hi) in self._counts(("check",)).items()]

    def count_filters(self) -> dict:
        """``{holder: (counted, lo, hi)}``: the same as a FILTER (`match`) -- the
        holder stands for the shapes of its layer that hold that many."""
        return {h: (k, lo, hi) for k, (h, lo, hi) in self._counts(("match",)).items()}

    def _area_ratios(self, full=False):
        """Every CHECK comparing areas of shapes: ``(terms, base, op, value)`` from
        ``T.area <op> v * B.area`` or ``T.area / B.area <op> v``, the left side a single
        area or a sum of areas.

        Two things a manual's antenna rule may add, both read here (with `full` they are
        returned too, as ``weights`` and ``extra``): the collector counted by its SIDE
        WALL -- ``T.perimeter * t`` (t its thickness) in place of ``T.area`` -- and the
        gate helped by what else is on the net -- ``v * (B.area + f * D.area + ...)``,
        "gate area plus f times the diode area connected to it"."""
        def attr_of(node, what):
            ref = node.get("ref", "") if isinstance(node, dict) else ""
            n = len(what) + 1
            return ref[:-n] if ref.endswith("." + what) and ref[:-n] in self.shapes else None

        def area_of(node):
            return attr_of(node, "area")

        def term(node):                        # X.area -> (X, None); X.perimeter * t -> (X, t)
            if area_of(node):
                return (area_of(node), None)
            if isinstance(node, dict) and node.get("op") == "*":
                x, y = node["args"]
                num, ref = (x, y) if "num" in x else (y, x)
                if "num" in num and attr_of(ref, "perimeter"):
                    return (attr_of(ref, "perimeter"), num["num"])
            return None

        def summed(node):                      # A.area + B.area + ... -> [(A, None), (B, None), ...] or None
            if term(node):
                return [term(node)]
            if isinstance(node, dict) and node.get("op") == "+":
                parts = [summed(x) for x in node["args"]]
                return None if any(p is None for p in parts) else [x for p in parts for x in p]
            return None

        def helped(node):                      # B.area + f * D.area + ... -> (B, [(D, f), ...]) or None
            if area_of(node):
                return area_of(node), []
            if not (isinstance(node, dict) and node.get("op") == "+"):
                return None
            base, extra = None, []
            for x in node["args"]:
                if area_of(x) and base is None:
                    base = area_of(x)
                elif isinstance(x, dict) and x.get("op") == "*":
                    u, v = x["args"]
                    num, ref = (u, v) if "num" in u else (v, u)
                    if "num" not in num or not area_of(ref):
                        return None
                    extra.append((area_of(ref), num["num"]))
                elif isinstance(x, dict) and x.get("op") == "+":
                    got = helped(x)
                    if got is None or (got[0] and base):
                        return None
                    base, extra = got[0] or base, extra + got[1]
                else:
                    return None
            return (base, extra) if base else None
        out = []
        for kind, ast in self.constraints:
            if kind != "check" or not (isinstance(ast, dict) and ast.get("op") in ("<=", "<", ">=", ">")):
                continue
            left, right = ast["args"]
            got = None
            if summed(left) and isinstance(right, dict) and right.get("op") == "*":
                x, y = right["args"]
                num, base = (x, y) if "num" in x else (y, x)
                if "num" in num and helped(base):
                    got = (summed(left), helped(base), ast["op"], num["num"])
            elif isinstance(left, dict) and left.get("op") == "/" and "num" in right:
                top, bottom = left["args"]
                if summed(top) and helped(bottom):
                    got = (summed(top), helped(bottom), ast["op"], right["num"])
            if got:
                terms, (base, extra), op, value = got
                row = ([s for s, _ in terms], base, op, value)
                out.append(row + ({s: w for s, w in terms if w is not None}, extra) if full else row)
        return out

    def antenna_extras(self, collectors, gate):
        """``(weights, extra)`` of the antenna check on these collectors and this gate:
        ``weights`` ``{collector: thickness}`` for a collector counted by its side wall
        (perimeter x thickness), ``extra`` ``[(shape, factor)]`` for what is added to the
        gate's area -- the diode area connected to the net, times a factor."""
        for terms, base, _, _, weights, extra in self._area_ratios(full=True):
            if terms == list(collectors) and base == gate:
                return weights, extra
        return {}, []

    def _connected(self, terms, base):
        return all(self.connections_up_to(self.layer_of(base), self.layer_of(t)) for t in terms)

    def antenna_bounds(self) -> list:
        """Antenna rules -> ``(collectors, gate, ratio, filters)``: the CHECK that the
        area of one shape -- or of several together -- is at most `ratio` times the
        area of another it is CONNECTED to (through the drawn or listed connections):
        ``M.area <= 200 * G.area``, ``(M1.area + M2.area) / G.area <= 200`` ("max.
        ratio of cumulative metal area to connected gate area"). Charge collected
        during processing has only the gate to go through.

        `filters` are the nets the rule is about, by what else is connected to them:
        a `match` on the area of another connected shape -- ``D.area >= 0.16`` "with a
        protection diode", ``D.area < 0.16`` "without" -- as ``(shape, op, value)``."""
        out = []
        for terms, base, op, value in self._area_ratios():
            if op in ("<=", "<") and self._connected(terms, base):
                used = set(terms) | {base} | {s for s, _ in self.antenna_extras(terms, base)[1]}
                filters = [(sh, o, num) for k, a in self.constraints if k != "check"
                           for sh, o, num in _attr_cmps(a, ".area") if sh in self.shapes and sh not in used]
                out.append((terms, base, value, filters))
        return out

    def holder_derivation(self, holder, contents):
        """The derived layer of a shape judged by what it HOLDS (a count, an area
        share): its own filters and contexts apply, but its contents are neither cut
        out of it nor combined with it. Returns ``(derivation, consumed)``."""
        d, _, consumed = self.check_derivations(holder, holder)
        if any(self.in_hole(c, holder) for c in contents):
            d = dict(d, holes=True)                  # held in the ring's hole
        inner = {self.layer_of(c) for c in contents}
        d = dict(d, nots=[n for n in d["nots"] if n not in inner],
                 ands=[a for a in d["ands"] if a not in inner],
                 selections=[x for x in d["selections"] if x[1]["base"] not in inner],
                 pre_selections=[x for x in d.get("pre_selections", []) if x[1]["base"] not in inner])
        return d, consumed | set(contents)

    def count_chain(self, holder, counted) -> list:
        """Which holders a count is about, said THROUGH what is counted: the counted
        shape (drawn touching the holder) is linked on to a further shape that carries
        a count FILTER of its own -- "a drain whose gate's source is shared by two
        gates". ``[(counted shape, further shape, (what it counts, lo, hi))]``."""
        filters, out = self.count_filters(), []
        for m in self.measures.values():
            if m.get("crossed") or not m.get("from") or not m.get("to"):
                continue
            ends = (self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"]))
            if counted not in ends:
                continue
            far = ends[1] if ends[0] == counted else ends[0]
            if far != holder and far in filters:
                out.append((counted, far, filters[far]))
        return out

    def held_ratio_bounds(self) -> list:
        """``(held, holder, lo, hi)``: how much of a shape's area what it HOLDS takes up
        -- ``S.area >= 0.06 * P.area`` with S drawn inside P ("min. slit density of a
        plate"), a sum on the left for several layers together. By containment, read
        from the drawing; shapes joined by connections are an antenna rule instead."""
        spans = {}
        for terms, base, op, value in self._area_ratios():
            if self._connected(terms, base) or not all(self._drawn_joined(t, base) for t in terms):
                continue
            span = spans.setdefault((tuple(terms), base), [None, None])
            span[0 if op in (">=", ">") else 1] = value
        return [(list(t), b, lo, hi) for (t, b), (lo, hi) in spans.items()]

    def antenna_shapes(self):
        """Every shape an antenna rule speaks of (its areas are not area rules)."""
        return {s for cs, g, _, fs in self.antenna_bounds() for s in [*cs, g, *(f[0] for f in fs)]}

    def connections_up_to(self, start, stop) -> list:
        """The connections that exist while layer `stop` is the last one made. The
        stack's order is read from the connections themselves: a layer's level is how
        many connection steps it is from `start` (the gate) -- poly and the contacts
        on it 1, metal-1 2, via-1 3 ... -- and the stage of `stop` is every connection
        between layers no further out than `stop` itself."""
        start, stop = self.plain_layer(start), self.plain_layer(stop)
        pairs, level, todo = self.connections(), {start: 0}, [start]
        while todo:
            cur = todo.pop(0)
            for a, b in pairs:
                if cur in (a, b):
                    other = b if cur == a else a
                    if other not in level:
                        level[other] = level[cur] + 1
                        todo.append(other)
        top = level.get(stop)
        if top is None:
            return []
        return [(a, b) for a, b in pairs if level.get(a, top + 1) <= top and level.get(b, top + 1) <= top]

    def net_relation(self, a, b):
        """``"ne"`` / ``"eq"`` if a constraint says shapes `a` and `b` are on DIFFERENT
        nets (``A.net != B.net``) or on the SAME net (``A.net == B.net``) -- a filter on
        which pairs a spacing rule is about ("different potential"); else None."""
        want = {f"{a}.net", f"{b}.net"}
        for _, ast in self.constraints:
            for leaf in _and_leaves(ast):
                if isinstance(leaf, dict) and leaf.get("op") in ("==", "!="):
                    refs = {x.get("ref") for x in leaf["args"] if isinstance(x, dict)}
                    if refs == want and len(want) == 2:
                        if leaf["op"] == "==" and self._drawn_joined(a, b) and (
                                self.layer_of(a) != self.layer_of(b)):
                            continue              # drawn joined: a connection, not a filter
                        return "ne" if leaf["op"] == "!=" else "eq"
        return None

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
        # "touching": "allowed" on the rule (or, through its sheet, on the process): shapes
        # that exactly abut never violate a minimum distance -- as if every such check
        # said "... or exactly 0"
        everywhere = self.data.get("touching") == "allowed"
        for kind, ast in self.constraints:
            zero_ok = everywhere and kind == "check"
            if isinstance(ast, dict) and ast.get("op") == "or":
                # `E >= v or E == 0`: the distance must reach v -- or be exactly zero
                # (the two edges coincide, which a manual calls "coinciding permitted")
                keep = [a for a in ast["args"] if not _is_zero(a, names)]
                if len(keep) == 1 and len(keep) < len(ast["args"]):
                    ast, zero_ok = keep[0], True
            if kind == "check" and self._either(ast) is not None:
                continue                        # "one of two sides": see either_bounds
            for leaf in _and_leaves(ast):       # a two-sided range is one `and`
                exact = _exact(leaf, names)     # `E == v`: at least v and at most v
                for b in ([_bound(leaf, names)] if exact is None else
                          [Bound(exact[0], ">=", exact[1]), Bound(exact[0], "<=", exact[1])]):
                    if b is not None:
                        b.kind, b.zero_ok, b.exact = kind, zero_ok, exact is not None
                        out.append(b)
        return out

    def _either(self, ast):
        """If `ast` is ``Ea >= v or Eb >= v`` over two arrows on ADJACENT sides of one
        shape (the measured edges of that shape are perpendicular), the two bounds. The
        two values may differ (``Ea >= 0.04 or Eb >= 0.06``): "where one side has less
        than 0.04, the sides next to it need 0.06"."""
        if not (isinstance(ast, dict) and ast.get("op") == "or" and len(ast["args"]) == 2):
            return None
        a, b = (_bound(x, set(self.measures)) for x in ast["args"])
        if not (a and b and a.var != b.var and a.is_min and b.is_min and a.value != 0 and b.value != 0):
            return None
        ma, mb = self.measures[a.var], self.measures[b.var]
        found = []
        for ea in (ma["from"], ma["to"]):
            for eb in (mb["from"], mb["to"]):
                if ea != eb and self.shape_of_edge(ea) == self.shape_of_edge(eb):
                    (x0, y0), (x1, y1) = self._edge_ends(ea)
                    (u0, v0), (u1, v1) = self._edge_ends(eb)
                    dot = (x1 - x0) * (u1 - u0) + (y1 - y0) * (v1 - v0)
                    la = math.hypot(x1 - x0, y1 - y0) * math.hypot(u1 - u0, v1 - v0)
                    if la and abs(dot) < 0.2 * la:
                        found.append(self.shape_of_edge(ea))
        if not found:
            return None
        # an enclosure is about the sides of the INNER shape
        inner = [x for x in found if any(y != x and self.relation(x, y) == "inside" for y in found)]
        return a, b, (inner or found)[0]

    def either_bounds(self) -> list:
        """``(bound_a, bound_b, shape)``: a CHECK that at least ONE of two arrows on
        adjacent sides of `shape` reaches the value -- "enclosed by 0.06 on one of two
        adjacent sides", "spaced 0.20 in at least one direction". The shape fails where
        two of its sides that meet at a corner both fall short."""
        out = []
        for kind, ast in self.constraints:
            e = self._either(ast) if kind == "check" else None
            if e:
                e[0].kind = e[1].kind = "check"
                out.append(e)
        return out

    def run_bounds(self) -> dict:
        """``{measure: (op, value)}`` from ``S.run > 0.5``: the rule applies only where
        the two measured edges run side by side for that long (a parallel run length)
        -- how a manual words "space to a wide line" or "... with run length > x"."""
        out = {}
        for _, ast in self.constraints:
            for var, op, num in _attr_cmps(ast, ".run"):
                if var in self.measures:
                    out[var] = (op, num)
        return out

    def inside_checks(self) -> list:
        """``[(inner, outer)]`` from the check ``K in M``: every K must lie wholly
        inside M -- "must be within", for which an enclosure distance alone is not
        enough (it says nothing about a K that sticks out or lies elsewhere)."""
        out = []
        for kind, ast in self.constraints:
            if kind == "check" and isinstance(ast, dict) and ast.get("op") == "in":
                a, b = ast["args"]
                outer = b.get("ref") or ""
                if outer.endswith(".holes") and self.holes(outer[:-6]):
                    outer = outer[:-6]           # inside the drawn void of a ring
                if a.get("ref") in self.shapes and outer in self.shapes:
                    out.append((a["ref"], outer))
        return out

    def in_holes(self, inner, outer) -> bool:
        """Is the check ``inner in outer.holes`` -- "must be surrounded by a ring of"?"""
        return any(kind == "check" and isinstance(ast, dict) and ast.get("op") == "in"
                   and ast["args"][0].get("ref") == inner and ast["args"][1].get("ref") == outer + ".holes"
                   for kind, ast in self.constraints)

    def wide_filters(self) -> dict:
        """``{shape: w}``: shapes that a FILTER says are wide -- a `match` minimum on a
        width measured across the shape (``Ww > 0.3``) while the rule checks something
        else about it ("space to a line wider than 0.3"). The shape then stands for
        the parts of its layer at least that wide."""
        pairs = [p for p in self.checked_pairs() if p[2]]
        maxed = {b.var for b in self.bounds() if b.is_max}       # a band is a selector itself
        out = {}
        for b in self.bounds():
            m = self.measures.get(b.var)
            if not (m and b.kind != "check" and b.is_min and b.var not in maxed
                    and self.measure_is_straight(b.var)):
                continue
            sf, st = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
            # ... and some OTHER measure checks the shape
            held = ({h for _, h, _, _ in self.held_ratio_bounds()}
                    | {h for h, _, _, _ in self.count_bounds()})
            if sf == st and (sf in held or any(sf in (a, c) for a, c, var in pairs if var != b.var)):
                out[sf] = b.value
                self._wide_strict[sf] = b.op == ">"
        return out

    def distance_rules(self) -> list:
        """What each STRAIGHT (distance) measure's bounds ask for, as
        ``(var, lo, hi, select)`` with `lo` / `hi` the min / max `Bound` (or None).

        The constraint's KIND decides, as everywhere. A ``check`` is the assertion
        that must hold -- one entry per bound, flagging where it FAILS: ``W >= v``
        the too-narrow (``select`` False, lo set), ``W <= v`` the too-wide (hi set).
        A ``match`` is a filter -- one entry (``select`` True) picking out what
        satisfies it: ``W <= v`` the narrow regions, ``v1 <= W < v2`` a band."""
        sel, out = {}, []
        wide = self.wide_filters()
        arrayed = {w["var"] for w in self.array_filters().values()}
        near = self.near_filters()
        for b in self.bounds():
            if not self.measure_is_straight(b.var):
                continue
            if b.kind != "check" and b.var in near:
                continue                                 # says which shapes another arrow's check is about
            if b.kind != "check" and b.var in arrayed:
                continue                                 # how close makes an array: a filter
            m = self.measures[b.var]
            if (b.kind != "check" and b.is_min and self.shape_of_edge(m["from"]) in wide
                    and self.shape_of_edge(m["from"]) == self.shape_of_edge(m["to"])):
                continue                                 # a filter on an operand, not a rule
            if b.kind == "check" and b.exact and b.is_min and (b.value == 0 or self.exact_form(b.var)):
                continue                                 # `S == 0`: only "at most 0" says anything;
                                                         # an exact form is one check, not two
            if b.kind == "check":
                out.append((b.var, b, None, False) if b.is_min else (b.var, None, b, False))
            else:
                sel.setdefault(b.var, [None, None])[0 if b.is_min else 1] = b
        return out + [(v, lo, hi, True) for v, (lo, hi) in sel.items()]

    def part_of(self, layer, whole) -> bool:
        """Is the named combination `layer` a PART of the layer `whole` by its definition
        (`cathode = efuse_poly and not ...`, `efuse_poly = poly2 and efuse_mk`: a part of
        poly2)?"""
        seen = set()
        while layer in self.layer_defs and layer not in seen:
            seen.add(layer)
            d = self.layer_defs[layer]
            if not (isinstance(d, dict) and d.get("op") == "and" and "ref" in d["args"][0]):
                return False
            layer = d["args"][0]["ref"]
            if layer == whole:
                return True
        return False

    def exact_form(self, var):
        """What an EXACT value (`L == v`, a check) says when the arrow is not a plain
        width, space or enclosure -- read from where the arrow is drawn:

        ``{"kind": "span", "shape", "role"}`` -- from one end of a shape that is not a
        box to its other end (the two outermost edges): the shape's whole extent, its
        "long" or "short" one as drawn.

        ``{"kind": "beside", "pad", "link"}`` -- two shapes that ABUT, the arrow between
        their sides that run away from the shared edge: how far the wider one's edge
        goes on beside the narrower one (the fuse link in the middle of its pad).

        None for everything else."""
        m = self.measures.get(var)
        if not m or m.get("crossed") or not m.get("from") or not m.get("to"):
            return None
        if not any(b.var == var and b.exact and b.kind == "check" for b in self.bounds()):
            return None
        sf, st = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
        geo = {e["id"]: e for n in (sf, st) if n in self.shapes for e in self.shapes[n]["edges"]}
        ea, eb = geo.get(m["from"]), geo.get(m["to"])
        if ea is None or eb is None:
            return None
        axis = lambda e: (0 if e["start"][0] == e["end"][0] else 1 if e["start"][1] == e["end"][1] else None)  # noqa: E731
        if axis(ea) is None or axis(ea) != axis(eb):
            return None                                  # parallel, along x or y
        ax = axis(ea)                                    # the coordinate the arrow runs along
        box = lambda n: [f([q[i] for e in self.shapes[n]["edges"] for q in (e["start"], e["end"])])  # noqa: E731
                         for f in (min, max) for i in (0, 1)]          # x0, y0, x1, y1
        if sf == st:
            x0, y0, x1, y1 = box(sf)
            if len(self.shapes[sf]["edges"]) <= 4:
                return None                              # a box: its width, as ever
            ends = sorted((ea["start"][ax], eb["start"][ax]))
            if ends != [(x0, y0)[ax], (x1, y1)[ax]]:
                return None
            along, across = ((x1 - x0), (y1 - y0)) if ax == 0 else ((y1 - y0), (x1 - x0))
            return {"kind": "span", "shape": sf, "role": "long" if along >= across else "short"}
        if self.relation(sf, st) != "touch":
            return None
        bf, bt = box(sf), box(st)
        other = 1 - ax                                   # the shared edge lies across the arrow
        if not (bf[other] == bt[other + 2] or bt[other] == bf[other + 2]):
            return None
        wider = sf if (bf[ax + 2] - bf[ax]) > (bt[ax + 2] - bt[ax]) else st
        return {"kind": "beside", "pad": wider, "link": st if wider == sf else sf}

    def from_outer_edge(self, var) -> bool:
        """Is this arrow drawn from the OUTER edge of a ring (a shape with a drawn void)
        to a shape lying on the ring's material?  Then the distance is to that outer edge
        alone -- "space to the die edge, the outer edge of the marking" -- and the edge of
        the void, which the inner shape may well touch, is no part of it."""
        m = self.measures.get(var) or {}
        for end, other in ((m.get("from"), m.get("to")), (m.get("to"), m.get("from"))):
            shape, inner = self.shape_of_edge(end), self.shape_of_edge(other)
            if not shape or not inner or shape == inner or not self.holes(shape):
                continue
            loops = [[(float(e["start"][0]), float(e["start"][1])) for e in loop] for loop in self.shapes[shape]["hole_loops"]]
            first = self.shapes[inner]["edges"][0]["start"]      # (a corner of it: the inner one may be a ring itself)
            on_material = not any(topology.contains(loop, (float(first[0]), float(first[1]))) for loop in loops)
            if any(e.get("id") == end for e in self.shapes[shape].get("edges", [])) and on_material \
                    and self.relation(shape, inner) == "contains":
                return True
        return False

    def in_hole(self, k, holder) -> bool:
        """Is shape `k` drawn in a drawn VOID of `holder` (in the hole of a ring, not on
        its material)?  Then what holds `k` is the ring's hole."""
        loops = self.shapes.get(holder, {}).get("hole_loops") or []
        pt = self.interior_point(k)
        return any(topology.contains([(float(e["start"][0]), float(e["start"][1])) for e in loop], pt)
                   for loop in loops)

    def counted_derivation(self, k, holder):
        """The derivation of a COUNTED shape: its layer, narrowed by how it is drawn
        against the other shapes the holder holds ("an Nwell drawn apart from the
        DNWELL in the same ring" -- an Nwell outside DNWELL). Relations to anything
        else are not part of what is counted."""
        # only what the counted shape is linked TO (an arrow drawn from it) narrows it
        sibs = {s for s in self.shapes if s not in (k, holder)
                and (self.relation(s, holder) == "inside" or self.in_hole(s, holder))
                and any(self.shape_of_edge(m.get("from")) == k and self.shape_of_edge(m.get("to")) == s
                        for m in self.measures.values())}
        if not sibs:
            return None
        self._partners = set(self.shapes) - sibs - {k}
        self._reading_counted = True
        try:
            d, _, _ = self.check_derivations(k, k)
        finally:
            self._partners, self._reading_counted = set(), False
        keep = {self.layer_of(s) for s in sibs}
        d = dict(d, ands=[], nots=[], pre_selections=[],
                 selections=[x for x in d["selections"] if x[1]["base"] in keep])
        return d if d["selections"] else None

    def holes(self, shape: str) -> bool:
        """Does this shape carry a drawn VOID (a donut: outer loop + an inner
        ``hole_loops`` boundary)?  The void is a genuine sub-feature of the drawn
        shape -- like an edge's length -- so ``<shape>.holes.area`` measures it and
        a relation can sit inside it (``X.inside(L.holes)``)."""
        return bool(self.shapes.get(shape, {}).get("hole_loops"))

    def area_filters(self) -> dict:
        """``{shape: (lo, lo_strict, hi, hi_strict)}``: a FILTER on a shape's area beside
        a check of the rule -- "if larger than 15000 µm², then ..." -- narrows the shape
        to those of its layer with such an area."""
        out = {}
        if not any(kind == "check" for kind, _ in self.constraints):
            return out                           # filters alone: what they pick is flagged
        for kind, ast in self.constraints:
            if kind == "check":
                continue
            for leaf in _and_leaves(ast):
                if not (isinstance(leaf, dict) and leaf.get("op") in _CMP):
                    continue
                a, b = leaf["args"]
                ref = a.get("ref", "") if isinstance(a, dict) else ""
                if ref.endswith(".area") and not ref.endswith(".holes.area") and ref[:-5] in self.shapes \
                        and isinstance(b, dict) and "num" in b and ref[:-5] not in self.antenna_shapes():
                    span = out.setdefault(ref[:-5], [None, False, None, False])
                    if leaf["op"] in (">", ">="):
                        span[0], span[1] = b["num"], leaf["op"] == ">"
                    else:
                        span[2], span[3] = b["num"], leaf["op"] == "<"
        return {k: tuple(v) for k, v in out.items()}

    def area_bounds(self) -> list:
        """Area rules -> (shape, op, value, on_holes), `op value` being what must HOLD
        (its failures are flagged). ``A.area >= v`` is the whole region;
        ``A.holes.area >= v`` measures the drawn VOID (on_holes True -> the layer's
        ``.holes``). Written as a filter (match ``A.area < v``: "the small ones") it
        flags the same shapes."""
        out = []
        antenna = self.antenna_shapes()
        for kind, ast in self.constraints:
            if isinstance(ast, dict) and ast.get("op") in _CMP:
                a, b = ast["args"]
                ref = a.get("ref", "") if isinstance(a, dict) else ""
                if not (isinstance(b, dict) and "num" in b):
                    continue
                if ref.split(".")[0] in antenna:
                    continue                     # "with a diode of at least ...": a net filter
                if kind != "check" and ref.split(".")[0] in self.area_filters():
                    continue                     # a filter beside a check: it narrows the shape
                # the op returned is the ASSERTION whose failures are flagged. A check
                # is one already; a match is a filter ("the ones with area < 2", crossed
                # out on a datasheet) -- flagging those is asserting the opposite.
                op = ast["op"] if kind == "check" else _OPPOSITE[ast["op"]]
                if ref.endswith(".holes.area"):
                    shape = ref[:-len(".holes.area")]
                    if self.holes(shape):            # only if a void is genuinely drawn
                        out.append((shape, op, b["num"], True))
                elif ref.endswith(".area"):
                    out.append((ref[:-len(".area")], op, b["num"], False))
        return out

    def angle_bounds(self) -> list:
        """The OLDER spelling of an acute-angle rule: a forbidden range written on an
        edge's angle (``e.angle > 0 and e.angle < 45`` -> ``(edge, 0, 45)``), mapped to
        ``layer.with_angle(lo .. hi)`` whatever the constraint's kind. What that op
        selects is CORNERS whose two edges enclose an angle in the range, so the
        accurate way to draw the rule is a corner with a `.corner` constraint (see
        `corner_ranges`); this form is kept so existing rules still compile."""
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

    def orientation_checks(self) -> list:
        """``(edge, angle, must)`` from the CHECK ``e.angle == 0`` ("must run along x";
        90: along y) or ``e.angle != 0`` ("must not") on a straight edge."""
        out = []
        for kind, ast in self.constraints:
            if kind != "check":
                continue
            for edge, op, num in _attr_cmps(ast, ".angle"):
                if op in ("==", "!=") and self.shape_of_edge(edge):
                    out.append((edge, num, op == "=="))
        return out

    def corner_facts(self) -> list:
        """Corner constraints as WRITTEN: ``(edge, kind, form, lo, hi)`` on
        ``<edge>.corner`` -- the ANGLE BETWEEN the two edges meeting where `edge`
        starts (a right angle is 90, an acute corner < 90, whichever way the shape
        turns). form is ``"range"`` / ``"exact"`` / ``"not"`` as in `length_facts`."""
        out, spans = [], {}
        for kind, ast in self.constraints:
            for edge, op, num in _attr_cmps(ast, ".corner"):
                if op == "!=":
                    out.append((edge, kind, "not", abs(num), abs(num)))
                elif op == "==":
                    out.append((edge, kind, "exact", abs(num), abs(num)))
                else:
                    lh = spans.setdefault((edge, kind), [None, None])
                    lh[0 if op in (">", ">=") else 1] = num
        out += [(e, k, "range", lo, hi) for (e, k), (lo, hi) in spans.items()]
        return out

    def corner_bounds(self) -> list:
        """The exact-angle corners a rule FLAGS: ``(edge, angle, others)``. `others`
        False -> the corners OF that angle; True -> every corner that is NOT that
        angle. As with every constraint the KIND decides: a ``match`` (filter) selects
        what satisfies it (``== 90`` -> the right-angle corners); a ``check``
        (assertion) flags what fails it (``== 90`` -> all the others; ``!= 90`` -> the
        right-angle ones). Winding-independent: both turn directions count."""
        out = []
        for edge, kind, form, lo, _ in self.corner_facts():
            if form == "range":
                continue
            satisfied_is_angle = form == "exact"          # what the constraint describes
            flag_angle = satisfied_is_angle if kind != "check" else not satisfied_is_angle
            out.append((edge, lo, not flag_angle))
        return out

    def corner_ranges(self) -> list:
        """The corner-angle RANGES a rule flags: ``(edge, lo, hi)`` (degrees between the
        two edges). A ``match`` flags the corners inside its range; a ``check`` the
        ones outside it -- so the acute-angle rule is simply the check
        ``e.corner >= 45`` -> ``(e, 0, 45)``."""
        out = []
        for edge, kind, form, lo, hi in self.corner_facts():
            if form != "range":
                continue
            if kind != "check":
                out.append((edge, 0.0 if lo is None else lo, 180.0 if hi is None else hi))
                continue
            if lo is not None:
                out.append((edge, 0.0, lo))
            if hi is not None:
                # a range includes its lower end, and a corner of exactly `hi` degrees
                # satisfies the check -- start just above it
                out.append((edge, hi + self.ANGLE_EPS, 180.0))
        return out

    ANGLE_EPS = 0.001   # degrees: "strictly more than" for a corner-angle range


# --- small AST queries over the {op, args[]} constraint form ---

def _all_refs(ast):
    if isinstance(ast, dict):
        if "ref" in ast:
            yield ast["ref"]
        for a in ast.get("args", []) or []:
            yield from _all_refs(a)


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


def _is_zero(ast, varnames):
    """Is ast ``var == 0`` (either order)?"""
    if isinstance(ast, dict) and ast.get("op") == "==":
        a, b = ast["args"]
        return ((a.get("ref") in varnames and b.get("num") == 0)
                or (b.get("ref") in varnames and a.get("num") == 0))
    return False


def _exact(ast, varnames):
    """If ast is ``var == num`` (either order), ``(var, num)``."""
    if isinstance(ast, dict) and ast.get("op") == "==":
        a, b = ast["args"]
        if isinstance(a, dict) and isinstance(b, dict):
            if a.get("ref") in varnames and "num" in b:
                return a["ref"], b["num"]
            if b.get("ref") in varnames and "num" in a:
                return b["ref"], a["num"]
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
