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
    OVERLAP -> `overlapping` (== `not_outside`); containment -> `inside`; else
    (disjoint) -> `outside`."""
    return ("inside" if rel == "inside" else "interacting" if rel == "touch"
            else "overlapping" if rel == "overlap" else "outside")


# truth table: each selection op -> the relations it KEEPS. Every row is distinct,
# so labeled example instances (some flagged, some not) identify the op uniquely;
# a NEGATION (not_inside) is a row whose excluded set is a single relation.
SEL_ROWS = {
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
        for name, s in self.shapes.items():
            if s.get("crossed") and s.get("edges"):
                e0 = s["edges"][0]["id"]
                self.measures[f"cross_{name}"] = {"name": f"cross_{name}", "from": e0, "to": e0,
                                                  "crossed": True, "of_shape": True}
        # the LEGEND: names for derived layers, as a datasheet's legend has NCOMP or
        # "gate" -- {"pcomp": "comp and pplus", "field_poly": "poly2 and not comp"}
        self.layer_defs = {k: parse(v) for k, v in (data.get("layers") or {}).items()}
        # constraints: list of (kind, parsed-AST)
        self.constraints = [(c.get("kind", "check"), parse(c["expr"]))
                            for c in data.get("constraints", [])]
        self._edge_owner = {e["id"]: name
                            for name, s in self.shapes.items() for e in s["edges"]}
        self._anchors = {}    # shape -> the point it is checked at, while reading a measure

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

    def crossed_relation(self, mname):
        """``(subject, context)`` when measure `mname` is a CROSS drawn between two
        different shapes -- the forbidden RELATION (the rule's check); else None. The
        subject is the shape the measure starts from."""
        m = self.measures.get(mname)
        if not (m and m.get("crossed")):
            return None
        sa, sb = self.shape_of_edge(m["from"]), self.shape_of_edge(m["to"])
        return (sa, sb) if sa is not None and sb is not None and sa != sb else None

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
        out += [(e, k, "range", lo, hi) for (e, k), (lo, hi) in spans.items()]
        return out

    def length_spans(self) -> dict:
        """``{edge: (lo, hi)}`` of the range/exact length constraints as written, of
        ANY kind -- the qualifiers a structural recognition (end-of-line) reads."""
        return {e: (lo, hi) for e, _, form, lo, hi in self.length_facts() if form != "not"}

    def length_bounds(self) -> list:
        """The edges an edge-length constraint FLAGS, as ``(edge, sel, lo, hi)`` with
        sel ``"with"`` -> ``with_length(lo, hi)`` (None = unbounded, lo == hi exact) or
        ``"without"`` -> ``without_length(lo[, hi])``.

        The constraint's KIND decides the reading. A ``match`` is a FILTER: it selects
        the edges that satisfy it. A ``check`` is an ASSERTION: it flags the edges that
        FAIL it -- ``e.length == 0.22`` flags every edge that is not 0.22 long,
        ``e.length >= 0.1`` flags the shorter ones. (As everywhere, a bound's
        strictness is not distinguished.)"""
        out = []
        for edge, kind, form, lo, hi in self.length_facts():
            if kind != "check":                       # filter: the edges that satisfy it
                out.append((edge, "without" if form == "not" else "with", lo, hi))
            elif form == "exact":
                out.append((edge, "without", lo, hi))
            elif form == "not":
                out.append((edge, "with", lo, hi))
            elif lo is not None and hi is not None:   # must lie in [lo, hi]
                out.append((edge, "without", lo, hi))
            elif lo is not None:                      # must be >= lo -> the shorter fail
                out.append((edge, "with", None, lo))
            else:                                     # must be <= hi -> the longer fail
                out.append((edge, "with", hi, None))
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
                fm, tm = self._edge_mid(m["from"]), self._edge_mid(m["to"])
                return ((fm[0] + tm[0]) / 2, (fm[1] + tm[1]) / 2)
        return self.interior_point(shape)

    def covering(self, shape):
        """Other shapes whose polygon contains this shape's DEVICE point -- the
        measured region sits inside them, so they COMBINE into its layer (.and)."""
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
        ``A.layer in {otp_mk, ymtp_mk}`` ("any of these") -- the names joined with
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
        consumed = set()
        sel = {sf: [], st: []}
        for sa, sb in self.relation_selections():
            for subj in (sf, st):
                other = sb if sa == subj else sa if sb == subj else None
                if other is not None and other not in measured:
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
                          | set(self.hole_layers(shape)))
            # A relation to layer Y is drawn against the shape AS DRAWN. If Y is also cut
            # out of that shape, the relation can only be read BEFORE the cut: what is
            # left after removing Y can no longer overlap or sit inside Y. So a plain
            # selection against a cut-out layer is applied first ("the part of A outside
            # Y, for an A that overlaps Y" -- a shape straddling Y's edge).
            pre = [x for x in sel[shape] if x[1]["base"] in nots and not (
                x[1]["ands"] or x[1]["nots"] or x[1]["selections"])]
            return {"base": self.layer_of(shape), "ands": ands, "nots": nots,
                    "pre_selections": pre,
                    "selections": [x for x in sel[shape] if x not in pre],
                    "halo": self.halo(shape), "rounded": self.rounded(shape)}
        return deriv(sf), deriv(st), measured | scope_consumed

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
        af, at = self._facing_points(m["from"], m["to"])
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
            if side:
                out.append((v, cap, side))
        return out

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
            elif self.measure_is_straight(name) and any(b.var == name for b in self.bounds()):
                out.append((sf, st, name))
        out += [(sh, sh, None) for sh, *_ in self.area_bounds()]
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

    def eol_edges(self):
        """The cap and side edges of every end-of-line construction: their length
        constraints QUALIFY the wire end (filters), they are not rules of their own."""
        return {e for _, cap, side in self.eol_measures() for e in (cap, side)}

    def halo(self, shape):
        """A shape's HALO: a signed margin (µm) its region is grown (>0) or shrunk
        (<0) by -- the formal form of a DRM 'within X' / offset, drawn as a dashed
        offset outline. Default 0 (no halo). Compiles to `.sized(halo.um)`."""
        return self.shapes.get(shape, {}).get("halo", 0) or 0

    def rounded(self, shape):
        """Whether the halo grows with ROUNDED corners (vs square). A drawn tick."""
        return bool(self.shapes.get(shape, {}).get("rounded"))

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
        for kind, ast in self.constraints:
            for leaf in _and_leaves(ast):       # a two-sided range is one `and`
                b = _bound(leaf, names)
                if b is not None:
                    b.kind = kind
                    out.append(b)
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
        for b in self.bounds():
            if not self.measure_is_straight(b.var):
                continue
            if b.kind == "check":
                out.append((b.var, b, None, False) if b.is_min else (b.var, None, b, False))
            else:
                sel.setdefault(b.var, [None, None])[0 if b.is_min else 1] = b
        return out + [(v, lo, hi, True) for v, (lo, hi) in sel.items()]

    def holes(self, shape: str) -> bool:
        """Does this shape carry a drawn VOID (a donut: outer loop + an inner
        ``hole_loops`` boundary)?  The void is a genuine sub-feature of the drawn
        shape -- like an edge's length -- so ``<shape>.holes.area`` measures it and
        a relation can sit inside it (``X.inside(L.holes)``)."""
        return bool(self.shapes.get(shape, {}).get("hole_loops"))

    def area_bounds(self) -> list:
        """Area rules -> (shape, op, value, on_holes), `op value` being what must HOLD
        (its failures are flagged). ``A.area >= v`` is the whole region;
        ``A.holes.area >= v`` measures the drawn VOID (on_holes True -> the layer's
        ``.holes``). Written as a filter (match ``A.area < v``: "the small ones") it
        flags the same shapes."""
        out = []
        for kind, ast in self.constraints:
            if isinstance(ast, dict) and ast.get("op") in _CMP:
                a, b = ast["args"]
                ref = a.get("ref", "") if isinstance(a, dict) else ""
                if not (isinstance(b, dict) and "num" in b):
                    continue
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
