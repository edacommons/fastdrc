"""KLayout DRC backend: compile an analyzed `Rule` to a Ruby ``.drc`` script.

No data IR -- `compile(rule)` recognizes each dimensional check directly from
our functions and emits the GF180-deck idiom so the output is comparable to the
reference deck:

    <layer> = polygons(<gds>, <dt>)              # one per input layer (layermap)
    <var> = <layer>.<op>(<v>.um, <metric>).polygons(0.001)
    <var>.output("<id>", "<desc>")
    <var>.forget

Recognition, per measure with a MIN bound (e.g. ``W >= 2``):
    - both edges on the same shape            -> width    : L.width(v)
    - different shapes, one inside the other  -> enclosing : outer.enclosing(inner, v)
    - different shapes, otherwise             -> space (same layer) / separation (diff)

Layers come from `rule.layer_of`; the threshold from the measure's bound; the
geometry relation from `rule.relation`. `input` uses a layermap (name ->
(gds, datatype)) when given (`polygons(gds, dt)`), else `input("name")`.
"""

from __future__ import annotations

from .backend import register, Backend
from .rule import Rule

# flip a comparison so the value can move to the left of a .drc() range predicate
# (e.g. `S >= 0.04` -> `0.04 <= S`): used to build `lo <= pred < hi`.
_FLIPOP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}


def _rel_to_sel(rel):
    """Map a drawn relation to the whole-polygon SELECTION op. The drawn relation
    IS the signal: TOUCH (coinciding edges, gap 0) -> `interacting` (touch counts);
    genuine area OVERLAP -> `overlapping` (== `not_outside`, touch excluded);
    containment -> `inside`; else (disjoint, gap may be 0) -> `outside`."""
    return ("inside" if rel == "inside"
            else "interacting" if rel == "touch"
            else "overlapping" if rel == "overlap"
            else "outside")


# truth table: each selection op -> the set of subject/context RELATIONS it keeps
# (flags). Every row is distinct, so labeled example instances (some flagged, some
# not) identify the op uniquely -- and a NEGATION (not_inside) is just a row whose
# excluded set is a single relation.
_SEL_ROWS = {
    "inside":          frozenset({"inside"}),
    "not_interacting": frozenset({"disjoint"}),
    "outside":         frozenset({"disjoint", "touch"}),
    "overlapping":     frozenset({"inside", "overlap"}),
    "interacting":     frozenset({"inside", "touch", "overlap"}),
    "not_inside":      frozenset({"disjoint", "touch", "overlap"}),
}


def _lookup_sel(kept, dropped):
    """The selection op whose truth-table row keeps every `kept` relation and none
    of the `dropped` ones. Among consistent ops prefer the MOST permissive (the
    negation the counter-examples were drawn to express). None if nothing fits."""
    cands = [op for op, keep in _SEL_ROWS.items()
             if kept <= keep and not (dropped & keep)]
    return max(cands, key=lambda op: len(_SEL_ROWS[op])) if cands else None


@register
class KLayoutBackend(Backend):
    name = "klayout"

    def __init__(self, layermap=None, metric="euclidian", regions=None, scope_all=None):
        self.layermap = dict(layermap or {})
        self.metric = metric
        # regions: {name: definition_expr} -- computed scope layers emitted in the
        #   preamble, e.g. {"incr": "(layout ^ layout_prev)"}.
        self.regions = dict(regions or {})
        # scope_all: (op, region_template) appended to EVERY check (a deck-level
        #   scope). region_template may contain "{value}" -> the rule's threshold,
        #   so incremental DRC is scope_all=("interacting", "changed.sized({value}.um)")
        #   -- restrict each rule to violations near a change, inflated by its halo.
        self.scope_all = scope_all

    def compile(self, rule: Rule) -> str:
        self._layers = {}     # layer name -> ruby var (insertion-ordered)
        self._decls = []      # `layer = polygons(...)` lines
        self._checks = []     # check + output + forget lines
        if rule.data.get("or"):           # OR-of-variants: union alternative sub-rules
            self._emit_union(rule, rule.data["or"])
            return self._assemble()
        # Straight-edge measures are dimensional, grouped per measure: a MIN bound
        # -> violation check (width/space/sep/enclosing); a MAX bound (optionally
        # WITH a min = a two-sided range) -> a .drc(...) narrow-gap / range
        # SELECTOR. Wavy measures are topological selections (in _check_expr).
        checks = self._group_bounds(rule)
        areas = rule.area_bounds()                       # both min (>=) and max (<=)
        angles = rule.angle_bounds()
        corners = rule.corner_bounds()
        lengths = rule.length_bounds()
        total = len(checks) + len(areas) + len(angles) + len(corners) + len(lengths)
        off = 0
        for i, (var, (lo, hi)) in enumerate(checks):
            self._emit_check(rule, var, lo, hi, i, total)
        off += len(checks)
        for j, (shape, op, value) in enumerate(areas):
            self._emit_area(rule, shape, op, value, off + j, total)
        off += len(areas)
        for k, (edge, lo, hi) in enumerate(angles):
            self._emit_angle(rule, edge, lo, hi, off + k, total)
        off += len(angles)
        for k, (edge, angle) in enumerate(corners):
            self._emit_corner(rule, edge, angle, off + k, total)
        off += len(corners)
        for k, (edge, kind, lo, hi) in enumerate(lengths):
            self._emit_length(rule, edge, kind, lo, hi, off + k, total)
        return self._assemble()

    def _assemble(self):
        out = ["# Generated by FastDRC -> KLayout DRC", ""]
        out += self._decls
        out += [f"{name} = {defn}" for name, defn in self.regions.items()]
        if self._decls or self.regions:
            out.append("")
        out += self._checks
        return "\n".join(out) + "\n"

    def _check_expr(self, rule, var, lo, hi):
        """Full KLayout check expression for a measure's bounds (derived layer +
        selections + op + scope), WITHOUT output/forget. A MIN bound -> violation
        check (width/space/sep/enclosing); a MAX bound (optionally WITH a min) ->
        a .drc(...) narrow-gap / range SELECTOR. None if unsupported."""
        m = rule.measures.get(var)
        if m is None:
            return None
        sf, st = rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"])
        mt = m.get("metric") or self.metric   # the metric is a property of the measure
        measured = {sf, st}

        # Topological SELECTIONS (outside/inside/interacting) on the measured
        # shapes -- sel_for[shape] is a list of (op, layer). They CLAIM their
        # context shape (sel_consumed) so it is neither AND-ed as a covering nor
        # treated as a scope marker, and `inside` stays a selection (not `and`).
        sel_for = {sf: [], st: []}
        sel_consumed = set()

        def _add_sel(subj, other):
            ml = rule.layer_of(other)
            if not ml:
                return False
            # labeled same-layer instances relating to `other` (the measured subj
            # plus any counter-examples). With counter-examples, read the op off the
            # truth table; otherwise the single drawn relation (back-compatible).
            insts = rule.instances_relating_to(rule.layer_of(subj), other)
            dropped = {rule.relation(s, other) for s, f in insts if not f}
            mvar = self._layer_var(ml)
            if dropped:
                kept = {rule.relation(s, other) for s, f in insts if f}
                so = _lookup_sel(kept, dropped) or _rel_to_sel(rule.relation(subj, other))
                sel_consumed.update(s for s, _ in insts)   # consume the label instances
                for s, f in insts:                         # ...and the CONTRAST's doping/covers
                    if not f:                              # (not the measured arg's -- it needs them)
                        sel_consumed.update(rule.covering(s))
                # ONLY when counter-examples are present (a negation drawing) do we
                # read a DERIVED marker: shapes overlapping `other` (a gate = poly2
                # over comp) AND into the marker -> not_interacting(poly2.and(comp))
                # = tgate. Gating on `dropped` keeps simple selections untouched
                # (their incidental layout overlaps aren't derived markers).
                helpers = rule.overlapping_markers(other, measured | {s for s, _ in insts})
                mvar += "".join(f".and({self._layer_var(h)})" for h in helpers)
                for h in helpers:
                    sel_consumed.update(s for s in rule.shapes if rule.layer_of(s) == h
                                        and rule.relation(other, s) in ("overlap", "inside", "contains"))
            else:
                so = _rel_to_sel(rule.relation(subj, other))
            sel_for[subj].append((so, mvar))
            sel_consumed.add(other)
            return True

        # wavy-edge measures = topological assertions; attach each to whichever
        # measured shape it touches (subject or separation arg). A context may be
        # selected by BOTH (e.g. subject AND arg inside the same region), so we
        # don't gate on sel_consumed here -- only on which measured shape the
        # measure connects.
        for sa, sb in rule.relation_selections():
            for subj in (sf, st):
                other = sb if sa == subj else sa if sb == subj else None
                if other is not None and other not in measured:
                    _add_sel(subj, other)
                    break

        # shapes that combine (cover the device point) or remove (cutout) into a
        # measured shape's derived layer -- EXCEPT those already inside-selections.
        derived = set()
        for ms in measured:
            derived |= {s for s in (rule.covering(ms) + rule.removed(ms))
                        if s not in measured and s not in sel_consumed}
        consumed = measured | derived | sel_consumed

        # A leftover marker whose layer is ALSO a check operand can't be a result
        # scope (it would drop everything); it's a selection on the subject.
        operand_layers = {rule.layer_of(sf), rule.layer_of(st)}
        for name in rule.shapes:
            if name in consumed:
                continue
            ml = rule.layer_of(name)
            if ml and ml in operand_layers:
                _add_sel(sf, name)
                consumed.add(name)

        def selsuf(shape):
            return "".join(f".{so}({l})" for so, l in sorted(set(sel_for[shape])))

        def dl(shape):                          # derived layer, minus selected contexts
            return self._derived(rule, shape, measured | sel_consumed) + selsuf(shape)

        if hi is None:                                     # MIN bound -> violation check
            v = self._fmt(lo.value)
            if sf == st:                                   # intra-shape -> width
                core = f"{dl(sf)}.width({v}.um, {mt})"
            else:
                rel = rule.relation(sf, st)
                if rel == "inside":                        # sf inside st -> st encloses sf
                    core = f"{dl(st)}.enclosing({dl(sf)}, {v}.um, {mt})"
                elif rel == "contains":                    # sf contains st
                    core = f"{dl(sf)}.enclosing({dl(st)}, {v}.um, {mt})"
                elif dl(sf) == dl(st):                     # same layer -> space
                    core = f"{dl(sf)}.space({v}.um, {mt})"
                else:                                      # diff layers -> separation
                    core = f"{dl(sf)}.separation({dl(st)}, {v}.um, {mt})"
            body, v_scope = f"{core}.polygons(0.001)", v
        else:                                              # MAX present -> .drc() selector
            subject, pred = self._drc_predicate(rule, sf, st, dl)
            if subject is None:
                return None
            hv = self._fmt(hi.value)
            if lo is None:                                 # one-sided: pred <op> hi
                inner = f"{pred} {hi.op} {hv}.um"
            else:                                          # range: lo <= pred < hi
                inner = f"{self._fmt(lo.value)}.um {_FLIPOP[lo.op]} {pred} {hi.op} {hv}.um"
            body, v_scope = f"{subject}.drc({inner})", hv

        suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, sf))
        if self.scope_all:                                # deck-level / computed scope
            sop, region = self.scope_all
            suffix += f".{sop}({region.format(value=v_scope)})"
        return f"{body}{suffix}"

    def _drc_predicate(self, rule, sf, st, dl):
        """(subject_expr, predicate) for a .drc() selector: width / space /
        separation(other) / enclosed(outer). enclosed is on the INNER shape."""
        if sf == st:
            return dl(sf), "width"
        rel = rule.relation(sf, st)
        if rel == "inside":                                # sf enclosed by st
            return dl(sf), f"enclosed({dl(st)})"
        if rel == "contains":                              # st enclosed by sf
            return dl(st), f"enclosed({dl(sf)})"
        if dl(sf) == dl(st):
            return dl(sf), "space"
        return dl(sf), f"separation({dl(st)})"

    def _emit_check(self, rule, var, lo, hi, idx, total):
        expr = self._check_expr(rule, var, lo, hi)
        if expr is not None:
            b = lo or hi
            rid = rule.name or f"{var} {b.op} {self._fmt(b.value)}"
            self._emit(rule, expr, idx, total, rid)

    @staticmethod
    def _group_bounds(rule):
        """Straight-edge measure -> [min_bound, max_bound] (either may be None).
        A measure with both is a two-sided range (-> .drc range selector)."""
        groups = {}
        for b in rule.bounds():
            if rule.measure_is_straight(b.var):
                lh = groups.setdefault(b.var, [None, None])
                lh[0 if b.is_min else 1] = b
        return list(groups.items())

    def _emit(self, rule, expr, idx, total, rid):
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {expr}")
        self._checks.append(f'{var}.output("{rid}", "{desc}")')
        self._checks.append(f"{var}.forget")

    def _emit_union(self, rule, alts):
        """OR-of-variants: each alternative is a sub-rule {shapes, measures,
        constraints}; union their check expressions into one output
        (`expr1.or(expr2)...`). Sub-rules share this backend so their layers are
        declared once. Matches the deck's `varA.or(varB)` rules."""
        exprs = []
        for sub in alts:
            sr = Rule(sub)
            for var, (lo, hi) in self._group_bounds(sr):
                e = self._check_expr(sr, var, lo, hi)
                if e:
                    exprs.append(e)
        if exprs:
            union = exprs[0] + "".join(f".or({e})" for e in exprs[1:])
            self._emit(rule, union, 0, 1, rule.name or "or")

    def _emit_area(self, rule, shape, op, value, idx, total):
        """Area check on a (possibly derived/selected) layer. MIN (`area >= v`) ->
        with_area(nil, v) (flags the too-small polys); MAX (`area <= v`) ->
        with_area(v, nil) (flags the too-large polys)."""
        measured = {shape}
        # wavy-measure selections on the area shape (e.g. sab.outside(otp_mk))
        sels, sel_consumed = [], set()
        for sa, sb in rule.relation_selections():
            other = sb if sa == shape else sa if sb == shape else None
            if other is None or other in measured:
                continue
            ml = rule.layer_of(other)
            if ml:
                rel = rule.relation(shape, other)
                so = _rel_to_sel(rel)
                sels.append((so, self._layer_var(ml)))
                sel_consumed.add(other)
        derived = {s for s in (rule.covering(shape) + rule.removed(shape))
                   if s not in measured and s not in sel_consumed}
        consumed = measured | derived | sel_consumed
        v = self._fmt(value)
        selsuf = "".join(f".{so}({l})" for so, l in sorted(set(sels)))
        args = f"nil, {v}.um" if op in (">", ">=") else f"{v}.um, nil"   # min vs max area
        core = f"{self._derived(rule, shape, measured | sel_consumed)}{selsuf}.with_area({args})"
        suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, shape))
        if self.scope_all:
            sop, region = self.scope_all
            suffix += f".{sop}({region.format(value=v)})"
        rid = rule.name or f"{shape}.area >= {v}"
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        # with_area returns polygons directly -- no .polygons(0.001) (that needs
        # an edge-pair layer; appending it errors in KLayout).
        self._checks.append(f"{var} = {core}{suffix}")
        self._checks.append(f'{var}.output("{rid}", "{desc}")')
        self._checks.append(f"{var}.forget")

    def _emit_angle(self, rule, edge, lo, hi, idx, total):
        """Edge-orientation check on a layer -> with_angle(lo .. hi). The drawn
        edge's angle is an EXAMPLE in the forbidden range; the range itself comes
        from the constraint. with_angle returns edges, so no .polygons(0.001)."""
        shape = rule.shape_of_edge(edge)
        layer = self._layer_var(rule.layer_of(shape) or f"_{shape}")
        core = f"{layer}.with_angle({self._fmt(lo)} .. {self._fmt(hi)})"
        suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, {shape}, shape))
        rid = rule.name or f"{edge}.angle in ({lo}..{hi})"
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {core}{suffix}")
        self._checks.append(f'{var}.output("{rid}", "{desc}")')
        self._checks.append(f"{var}.forget")

    def _emit_length(self, rule, edge, kind, lo, hi, idx, total):
        """Edge-length check. kind "with" -> layer.edges.with_length(min, max)
        (nil = unbounded; single arg when exact); kind "without" -> edges NOT of
        that length (without_length). Returns edges, so no .polygons."""
        shape = rule.shape_of_edge(edge)
        layer = self._layer_var(rule.layer_of(shape) or f"_{shape}")
        nm = lambda x: "nil" if x is None else f"{self._fmt(x)}.um"
        if kind == "without":
            core = f"{layer}.edges.without_length({nm(lo)})"
        elif lo is not None and lo == hi:
            core = f"{layer}.edges.with_length({nm(lo)})"      # exact length
        else:
            core = f"{layer}.edges.with_length({nm(lo)}, {nm(hi)})"
        suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, {shape}, shape))
        rid = rule.name or f"{edge}.length"
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {core}{suffix}")
        self._checks.append(f'{var}.output("{rid}", "{desc}")')
        self._checks.append(f"{var}.forget")

    # corners() returns degenerate points; the deck sizes them to a small box so
    # they carry area and can be output. 0.1um matches the GF180 corner rules.
    CORNER_SIZE = "0.1"

    def _emit_corner(self, rule, edge, angle, idx, total):
        """Corner-angle check -> corners(a).sized(S).or(corners(-a).sized(S)).
        Both signs because the rule is on the MAGNITUDE |angle| (convex OR
        concave), matching the deck's `corners(90).or(corners(-90))`. KLayout's
        sign is the negative of ours, but emitting both makes that moot."""
        shape = rule.shape_of_edge(edge)
        layer = self._layer_var(rule.layer_of(shape) or f"_{shape}")
        a, na, sz = self._fmt(angle), self._fmt(-angle), self.CORNER_SIZE
        core = (f"{layer}.corners({a}).sized({sz})"
                f".or({layer}.corners({na}).sized({sz}))")
        # CONTAINMENT scope on the result: a shape that contains the subject means
        # the corner is only checked INSIDE it (.inside(ctx)); each hole layer of
        # such a context is also excluded from the result first (.not(H)) -- this
        # is PL.6's `.not(ymtp_mk).inside(comp.not(ymtp_mk))`.
        contexts = [n for n in rule.shapes
                    if n != shape and rule.relation(shape, n) == "inside"]
        consumed = {shape} | set(contexts)
        excl = sorted({self._layer_var(h) for c in contexts for h in rule.hole_layers(c)})
        insides = sorted(self._derived(rule, c, {shape}) for c in contexts)
        rscope = ("".join(f".not({h})" for h in excl)
                  + "".join(f".inside({d})" for d in insides))
        suffix = rscope + "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, shape))
        rid = rule.name or f"{edge}.corner == {angle}"
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {core}{suffix}")
        self._checks.append(f'{var}.output("{rid}", "{desc}")')
        self._checks.append(f"{var}.forget")

    def _derived(self, rule, shape, measured):
        """The layer a measured shape checks on: its own layer AND every
        non-measured shape that COVERS its interior, then NOT each layer tagged
        on a HOLE (an excluded region). AND-combined layers are sorted (commute);
        NOT terms follow. So a covering poly2 -> comp.and(poly2); an otp_mk hole
        -> comp.not(otp_mk)."""
        layers = [rule.layer_of(shape) or f"_{shape}"]
        for s in rule.covering(shape):
            if s in measured:
                continue
            layers.append(rule.layer_of(s) or f"_{s}")
        vs = sorted({self._layer_var(l) for l in layers})
        expr = vs[0] if len(vs) == 1 else vs[0] + "".join(f".and({v})" for v in vs[1:])
        # NOT each removed (cutout) layer; + any legacy tagged-hole layers
        nots = {self._layer_var(rule.layer_of(s) or f"_{s}") for s in rule.removed(shape)
                if s not in measured}
        nots |= {self._layer_var(h) for h in rule.hole_layers(shape)}
        for hl in sorted(nots):
            expr += f".not({hl})"
        return expr

    def _scope(self, rule, consumed, primary):
        """Marker scoping: shapes NOT consumed by the check (measured or AND-ed
        into a derived layer) are markers. Their topology to the checked geometry
        (`primary`) picks the error-selection op. Sorted (the ops commute)."""
        out = []
        for name in rule.shapes:
            if name in consumed:
                continue
            mk = self._layer_var(rule.layer_of(name) or f"_{name}")
            rel = rule.relation(primary, name)
            if rel in ("disjoint", "outside"):
                op = "not_interacting"
            elif rel == "touch":
                op = "interacting"
            else:                                    # overlap / inside / contains
                op = "overlapping"
            out.append((op, mk))
        return sorted(out, key=lambda x: (x[1], x[0]))

    def _layer_var(self, layer):
        if layer not in self._layers:
            var = self._var(layer)
            if layer in self.layermap:
                gl, dt = self.layermap[layer]
                self._decls.append(f"{var} = polygons({gl}, {dt})")
            else:
                self._decls.append(f'{var} = input("{layer}")')
            self._layers[layer] = var
        return self._layers[layer]

    def _result_var(self, rid, idx, total):
        base = self._var(rid).lower()
        return f"{base}_l1" if total == 1 else f"{base}_{idx + 1}_l1"

    @staticmethod
    def _var(layer):
        return "".join(c if (c.isalnum() or c == "_") else "_" for c in str(layer))

    @staticmethod
    def _fmt(v):
        # KLayout deck writes integer thresholds without a trailing .0 (e.g. 1.um)
        f = float(v)
        return str(int(f)) if f.is_integer() else str(v)
