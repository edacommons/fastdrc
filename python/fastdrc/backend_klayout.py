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

import re

import os

from .backend import register, Backend, RunError
from .rule import Rule, rel_to_sel as _rel_to_sel   # selection classification lives on the Rule

# flip a comparison so the value can move to the left of a .drc() range predicate
# (e.g. `S >= 0.04` -> `0.04 <= S`): used to build `lo <= pred < hi`.
_FLIPOP = {">": "<", ">=": "<=", "<": ">", "<=": ">="}
# what FAILS a max check: `W <= v` is violated by `W > v`
_BEYOND = {"<=": ">", "<": ">="}


@register
class KLayoutBackend(Backend):
    name = "klayout"
    extension = ".drc"
    about = "a KLayout DRC deck"
    runs = True

    def headed(self, text, about):
        about = list(about)
        if self.graph:
            st = self.graph.stats
            about.append(f"{st['written']} layer operations as the rules are written, "
                         f"{st['after']} run: what several rules need is computed once"
                         + (f"; nets extracted {st['nets']} time(s) instead of {st['nets_written']}"
                            if st.get("nets_written", 0) > st.get("nets", 0) else ""))
        return text.replace("\n", "\n" + "".join(f"# {line}\n" for line in about), 1) if about else text

    # ---- running the deck: KLayout in batch mode, its report database read back ----
    @staticmethod
    def binary():
        """The KLayout executable: ``$KLAYOUT_BIN``, else ``klayout`` on the PATH, else None."""
        import shutil
        return os.environ.get("KLAYOUT_BIN") or shutil.which("klayout")

    def ready(self):
        return None if self.binary() else "KLayout not found -- put `klayout` on the PATH or set KLAYOUT_BIN"

    def by_hand(self, deck, layout):
        return f"klayout -b -r {deck} -rd input={layout}"

    def run(self, deck, layout, top=None, report=None):
        import subprocess
        if not self.binary():
            raise RunError(self.ready())
        # absolute: KLayout resolves a report path against the deck's folder, not the caller's
        report = os.path.abspath(report or os.path.splitext(layout)[0] + ".lyrdb")
        cmd = [self.binary(), "-b", "-r", deck, "-rd", f"input={os.path.abspath(layout)}", "-rd", f"report={report}"]
        if top:
            cmd += ["-rd", f"topcell={top}"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(report):
            raise RunError("KLayout failed:\n" + (p.stderr or p.stdout)[-2000:])
        return {"rules": self.read(report), "report": report}

    @staticmethod
    def read(path) -> dict:
        """``{rule: (description, [(x0, y0, x1, y1), ...])}`` of a KLayout report database
        (.lyrdb): every rule the deck declared, in that order, with the bounding box (µm)
        of each of its markers (None for a marker that is a value, not a place)."""
        text = open(path, encoding="utf-8").read()
        head = text.split("<cells>")[0]
        out = {n: (d, []) for n, d in re.findall(
            r"<category>\s*<name>([^<]*)</name>\s*<description>([^<]*)</description>", head)}
        for cat, vals in re.findall(r"<item>.*?<category>'?([^<']*)'?</category>.*?<values>(.*?)</values>", text, re.S):
            pts = [(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+(?:e-?\d+)?),(-?[\d.]+(?:e-?\d+)?)", vals)]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.setdefault(cat, ("", []))[1].append((min(xs), min(ys), max(xs), max(ys)) if pts else None)
        return out

    def __init__(self, layermap=None, metric="euclidian", regions=None, scope_all=None,
                 standalone=False, title="FastDRC", optimise=False):
        # optimise: the rules as one graph -- what several need is computed once (ir.py)
        self.optimise, self.shared, self.graph, self.problems = optimise, {}, None, []
        self.layermap = dict(layermap or {})
        self.unmapped = []    # layers used by the rules that the layer map does not give
        self.metric = metric
        # regions: {name: definition_expr} -- computed scope layers emitted in the
        #   preamble, e.g. {"incr": "(layout ^ layout_prev)"}.
        self.regions = dict(regions or {})
        # scope_all: (op, region_template) appended to EVERY check (a deck-level
        #   scope). region_template may contain "{value}" -> the rule's threshold,
        #   so incremental DRC is scope_all=("interacting", "changed.sized({value}.um)")
        #   -- restrict each rule to violations near a change, inflated by its halo.
        self.scope_all = scope_all
        self._used = {}       # ruby var -> layer name (reset per compile)
        # standalone: a complete script, run as
        #   klayout -b -r deck.drc -rd input=layout.gds -rd report=out.lyrdb
        self.standalone, self.title = standalone, title

    def _reset(self):
        self._layers = {}     # layer name -> ruby var (insertion-ordered)
        self._decls = []      # `layer = polygons(...)` lines
        self._checks = []     # check + output + forget lines
        self._used = {}       # ruby var -> layer name (keeps two layers off one var)
        self._defs = {}       # legend: derived layer names -> definition
        self._named = {}      # long derived expression -> the name it is given once
        self._helpers = {}    # name -> a small Ruby helper the deck needs, emitted once
        self._connect = None  # the connections currently declared in the deck

    def compile(self, rule: Rule) -> str:
        self._reset()
        self._compile_into(rule)
        return self._assemble()

    def compile_all(self, rules) -> str:
        """Several rules as ONE deck: the layer declarations once, then each rule."""
        self._reset()
        for r in rules:
            self._compile_into(r if isinstance(r, Rule) else Rule(r))
        return self._assemble()

    def _compile_into(self, rule: Rule):
        self._defs.update(getattr(rule, "layer_defs", {}))
        if rule.data.get("or"):           # OR-of-variants: union alternative sub-rules
            self._emit_union(rule, rule.data["or"])
            return
        # Straight-edge measures are distances. A CHECK bound flags where it fails
        # (min -> width/space/sep/enclosing; max -> .drc(pred > v)); a MATCH bound or
        # band is a .drc(...) SELECTOR of what satisfies it (rule.distance_rules).
        # Links to wavy edges are topological selections (in _check_expr).
        eols = rule.eol_measures()                       # [(var, cap, side)] from GEOMETRY
        eol_vars = {v for v, _, _ in eols}
        eol_edges = rule.eol_edges()                     # length bounds the EOL claims
        checks = [c for c in rule.distance_rules() if c[0] not in eol_vars]
        areas = rule.area_bounds()                       # both min (>=) and max (<=)
        angles = rule.angle_bounds() + rule.corner_ranges()   # both -> with_angle(lo .. hi)
        corners = rule.corner_bounds()
        claimed = eol_edges | rule.array_edges() | rule.density_edges()   # ... sizes, not rules
        lengths = [lb for lb in rule.length_bounds() if lb[0] not in claimed]
        crosses = [v for v, mm in rule.measures.items() if mm.get("crossed")]  # presence
        insides = rule.inside_checks()                   # `K in M`: must lie inside
        forms = rule.form_checks()                       # "must be square"
        eithers = rule.either_bounds()                   # "on one of two adjacent sides"
        densities = rule.density_bounds()                # coverage of an area / a window
        antennas = rule.antenna_bounds()                 # area collected per connected gate area
        counts = rule.count_bounds()                     # how many of a layer a shape holds
        helds = rule.held_ratio_bounds()                 # how much of a shape what it holds takes up
        if any(m.get("from") and rule.net_relation(rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"]))
               for m in rule.measures.values()):
            self._use_connections(rule.connections())
        total = (len(checks) + len(areas) + len(angles) + len(corners) + len(lengths)
                 + len(crosses) + len(eols) + len(insides) + len(forms) + len(eithers) + len(densities) + len(antennas) + len(counts) + len(helds))
        off = 0
        for i, (var, lo, hi, select) in enumerate(checks):
            self._emit_check(rule, var, lo, hi, i, total, select)
        off += len(checks)
        for j, (shape, op, value, on_holes) in enumerate(areas):
            self._emit_area(rule, shape, op, value, off + j, total, on_holes)
        off += len(areas)
        for k, (edge, lo, hi) in enumerate(angles):
            self._emit_angle(rule, edge, lo, hi, off + k, total)
        off += len(angles)
        for k, (edge, angle, others) in enumerate(corners):
            self._emit_corner(rule, edge, angle, off + k, total, others)
        off += len(corners)
        for k, (edge, kind, lo, hi) in enumerate(lengths):
            self._emit_length(rule, edge, kind, lo, hi, off + k, total)
        off += len(lengths)
        for k, var in enumerate(crosses):                # CROSS -> presence violation
            self._emit_check(rule, var, None, None, off + k, total)
        off += len(crosses)
        for k, (var, cap, side) in enumerate(eols):      # END-OF-LINE construction
            self._emit_eol(rule, var, cap, side, off + k, total)
        off += len(eols)
        for k, (inner, outer) in enumerate(insides):     # MUST BE WITHIN: flag what is not
            d_in, d_out, used = rule.check_derivations(inner, outer)
            expr = f"{self._fmt_deriv(d_in)}.not_inside({self._fmt_deriv(d_out)})"
            expr += "".join(f".{op}({mk})" for op, mk in self._scope(rule, used, inner))
            self._emit(rule, expr, off + k, total, rule.name or f"{inner} in {outer}")
        off += len(insides)
        for k, (shape, form) in enumerate(forms):        # SHAPE CLASS: flag the others
            other = self._FORM["oblong" if form == "square" else "square"]
            expr = self._derived(rule, shape, {shape}) + other
            expr += "".join(f".{op}({mk})" for op, mk in self._scope(rule, {shape}, shape))
            self._emit(rule, expr, off + k, total, rule.name or f"{shape} {form}")
        off += len(forms)
        for k, (a, b, shape) in enumerate(eithers):
            expr = self._check_expr(rule, a.var, a, None, corner_of=shape)
            if expr:
                self._emit(rule, expr, off + k, total, rule.name or f"{a.var} or {b.var}")
        off += len(eithers)
        for k, (shape, lo, hi, window) in enumerate(densities):
            self._emit(rule, self._density(rule, shape, lo, hi, window), off + k, total,
                       rule.name or f"{shape}.density")
        off += len(densities)
        for k, (collectors, gate, ratio, filters) in enumerate(antennas):
            rid = rule.name or f"{'+'.join(collectors)} / {gate}"
            stem = self._result_var(rid, off + k, total)
            self._emit(rule, self._antenna(rule, collectors, gate, ratio, filters, stem),
                       off + k, total, rid)
        off += len(antennas)
        for k, (holder, counted, lo, hi) in enumerate(counts):
            d, consumed = rule.holder_derivation(holder, [counted])
            expr = self._fmt_deriv(d) + self._holding(rule.layer_of(counted), lo, hi, select=False)
            expr += "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, holder))
            self._emit(rule, expr, off + k, total, rule.name or f"Count({counted})")
        off += len(counts)
        for k, (held, holder, lo, hi) in enumerate(helds):
            rid = rule.name or f"{'+'.join(held)} in {holder}"
            stem = self._result_var(rid, off + k, total)
            self._emit(rule, self._held_ratio(rule, held, holder, lo, hi, stem), off + k, total, rid)

    def _assemble(self):
        out = ["# Generated by FastDRC -> KLayout DRC", ""]
        if self.standalone:
            out += ["# run:  klayout -b -r <this file> -rd input=<layout.gds> [-rd report=<out.lyrdb>]"
                    " [-rd topcell=<name>]",
                    "if $topcell", "  source($input, $topcell)", "else", "  source($input)", "end",
                    f'report("{self._q(self.title)}", $report || "fastdrc_report.lyrdb")', ""]
        named = [f"{name} = {expr}" for expr, name in self._named.items()]
        top = self._decls + [f"{name} = {defn}" for name, defn in self.regions.items()] + named
        checks = self._checks
        self.shared, self.graph = {}, None
        if self.optimise:                         # the rules as ONE graph, optimised, written back (ir.py)
            from .ir import Deck, ParseError
            try:
                graph = Deck()
                graph.add(top, top=True)
                graph.add(checks)
                graph.optimise()
                top, checks, self.shared = graph.lines()
                self.graph = graph
            except ParseError as ex:           # a statement the graph cannot read: the deck as written
                self.problems.append(f"not optimised ({ex})")
                top = self._decls + [f"{name} = {defn}" for name, defn in self.regions.items()] + named
                checks = self._checks
        out += top
        for text in self._helpers.values():
            out += ["", *text.splitlines()]
        if top:
            out.append("")
        out += checks
        return "\n".join(out) + "\n"

    def _check_expr(self, rule, var, lo, hi, select=False, corner_of=None):
        """Full KLayout expression for one entry of `rule.distance_rules` (derived
        layer + selections + op + scope), WITHOUT output/forget. Not `select`: a CHECK
        -- a MIN bound is the violation check (width/space/sep/enclosing), a MAX bound
        flags what exceeds it (.drc(pred > v)). `select`: a FILTER -- a .drc(...)
        selector of what satisfies the bound(s). None if unsupported."""
        m = rule.measures.get(var)
        if m is None:
            return None
        sf, st = rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"])
        if rule.crossed_relation(var):        # a cross BETWEEN two shapes: the forbidden
            st = sf                           # relation is one of the subject's selections
        mt = m.get("metric") or self.metric   # the metric is a property of the measure
        if corner_of:                         # a side is judged straight across
            mt = "projection"
        net = rule.net_relation(sf, st) if sf != st else None
        if net:                               # only pairs on different nets / on one net
            mt += ", props_ne" if net == "ne" else ", props_eq"
        measured = {sf, st}
        # The DERIVED-LAYER analysis (cover->and, cutout->not, wavy-measure->selection
        # via the labeled-instances table, selected-marker recovery) is the Rule's
        # analyzed view -- the SINGLE source of truth both backends format, never
        # re-derive. We only RENDER it to KLayout syntax here (`_fmt_deriv`).
        deriv_sf, deriv_st, consumed = (rule.check_derivations(sf, st) if sf == st and m.get("crossed")
                                        else rule.measure_derivations(var))
        scope_from = sf                       # the shape scope markers are read against
        derivs = {sf: deriv_sf, st: deriv_st}

        def dl(shape):                          # derived layer string, from the shared analysis
            return self._fmt_deriv(derivs[shape])

        def edge_dl(shape):                     # EDGE-dimensional subject: gate width/space.
            # base.edges.and((base & active).edges) -- recovered from the GEOMETRY: a
            # covering layer that PARTIALLY CROSSES the subject (overlap) is the active
            # region forming the GATE (poly over comp = tgate); a covering layer that
            # CONTAINS the subject (inside) is a mask FILTER -> a plain edge .and(M);
            # cutouts (removed) become edge .not(M). No stashed structure.
            base = self._layer_var(rule.layer_of(shape))
            gate_covers, filters = [], []
            for c in rule.covering(shape):
                if c in measured:
                    continue
                (filters if rule.relation(shape, c) == "inside" else gate_covers).append(
                    self._layer_var(rule.layer_of(c)))
            gate = base + "".join(f".and({c})" for c in sorted(gate_covers))
            core = f"{base}.edges.and({gate}.edges)"
            for r in sorted({self._layer_var(l) for c in rule.removed(shape)
                             for l in rule.layer_of(c).split("|")}
                            | {self._layer_var(l) for l in rule.excepted()}):
                core += f".not({r})"
            for f in sorted(filters):           # mask region the gate edges sit inside
                core += f".and({f})"
            return core

        if m.get("crossed"):                               # a CROSS: the derived REGION is
            # itself the violation (presence) -- output it, no measure/threshold. The
            # crossed config's RELATION picks the op, exactly like a selection (a cross
            # is a selection whose selected set is output, not measured): overlap ->
            # overlapping ("must not overlap"), disjoint -> outside ("must overlap"),
            # inside -> inside, touch -> interacting. The crossed relation is carried
            # by the subject's derivation like every other relation (rule.
            # relation_selections), so the subject's derived region IS the violation.
            body, v_scope = dl(sf), "0"
        elif not select and hi is None:                    # MIN check -> violation check
            v = self._fmt(lo.value)
            within, each_more, touching = None, [], ""
            run = rule.run_bounds().get(var)               # only where the edges run side by
            if run:                                        # side this long
                mt = f"{mt}, projecting {run[0]} {self._fmt(run[1])}.um"
            over = None if m.get("edges") else rule.overlap_reading(var)
            if over:                                       # partly overlapping shapes
                consumed, scope_from = over["consumed"], over["outer_shape"]
                a, b = self._fmt_deriv(over["outer"]), self._fmt_deriv(over["inner"])
                core = {"extension": f"{a}.enclosing({b}, {v}.um, {mt})",
                        "overlap": f"{a}.overlap({b}, {v}.um, {mt})"}[over["kind"]]
            elif m.get("edges"):                           # gate-EDGE width/space/separation
                if sf == st:
                    core = f"{edge_dl(sf)}.width({v}.um, {mt})"
                elif edge_dl(sf) == edge_dl(st):
                    core = f"{edge_dl(sf)}.space({v}.um, {mt})"
                else:
                    core = f"{edge_dl(sf)}.separation({edge_dl(st)}, {v}.um, {mt})"
            elif sf == st and "|" in (derivs[sf]["base"] or ""):
                # "width of A or B": each of them, not their union -- two abutting
                # narrow shapes of different layers are each too narrow
                each = [self._fmt_deriv(dict(derivs[sf], base=one))
                        for one in derivs[sf]["base"].split("|")]
                core = f"{each[0]}.width({v}.um, {mt})"
                each_more = [f"{e}.width({v}.um, {mt})" for e in each[1:]]
            elif sf == st:                                 # intra-shape -> width
                core = f"{dl(sf)}.width({v}.um, {mt})"
            else:
                rel = rule.relation(sf, st)
                if rel in ("inside", "contains"):          # one encloses the other
                    o, i = (dl(st), dl(sf)) if rel == "inside" else (dl(sf), dl(st))
                    core = f"{o}.enclosing({i}, {v}.um, {mt})"
                    if m.get("within"):                    # ... and the inner must not stick out
                        within = f"{i}.overlapping({o}).not_inside({o})"
                elif dl(sf) == dl(st):                     # same layer -> space
                    core = f"{dl(sf)}.space({v}.um, {mt})"
                else:                                      # diff layers -> separation
                    a, b = dl(sf), dl(st)
                    wide_one = (sf if derivs[sf].get("wide") else st if derivs[st].get("wide") else None)
                    whole = lambda sh: self._fmt_deriv(dict(derivs[sh], wide=None))   # noqa: E731
                    if wide_one and bool(derivs[sf].get("wide")) != bool(derivs[st].get("wide")) \
                            and whole(sf) == whole(st):
                        # "space to a WIDE part of the same layer": every too-small space
                        # of the layer that has a wide part on one side. (Measuring the
                        # layer against its own wide parts would hide a wide neighbour
                        # behind the wide part's own edge -- two wide lines side by side
                        # went unreported.)
                        core = f"{whole(sf)}.space({v}.um, {mt})"
                        touching = f".interacting({dl(wide_one)})"
                    else:
                        if derivs[sf].get("wide") and not derivs[st].get("wide"):
                            a, b = b, a
                        core = f"{a}.separation({b}, {v}.um, {mt})"
            # Edge pairs become markers 1 nm fat, so a violation of zero width still
            # shows. The exception: an enclosed region that was CLIPPED by the enclosing
            # layer (`sab.enclosing(poly2.and(sab))`) shares its clip edges with it --
            # zero enclosure there is the clip itself, not a violation, so those
            # zero-width pairs are dropped by converting without the 1 nm. The same when
            # the rule says so itself (`E >= v or E == 0`: coinciding edges permitted).
            if corner_of:
                # ONE OF TWO ADJACENT SIDES must reach v: the sides of the shape that
                # fall short are edges; two of them meeting at a corner (a "width" of
                # zero between edges at a right angle) is the violation, and the shape
                # at that corner is flagged
                # (a side falls short if any part of it does: whole edges)
                core = core[:-1] + ", whole_edges)"
                short = ".second_edges" if ".enclosing(" in core else ".edges"
                who = self._fmt_deriv(derivs[corner_of])
                mine = "" if short == ".second_edges" else f".and({who}.edges)"
                body = (f"{who}.interacting({core}{short}{mine}"
                        f".width(angle_limit(100.0), 1.dbu).polygons(0.001))")
                suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, scope_from))
                return body + suffix
            bare = self._clipped_inner(core) or lo.zero_ok   # zero distance is not a violation
            if ".separation(" in core and not over and rule.cut_apart(derivs[sf], derivs[st]):
                bare = True      # one is the rest of a layer after cutting the other out
            body, v_scope = f"{core}.polygons{'' if bare else '(0.001)'}{touching}", v
            for more in each_more:
                body += f".or({more}.polygons(0.001))"
            if within:
                # "G within H by v": the margin, AND a G lying across H's edge has none
                body += f".or({within})"
        elif not select:                                   # MAX check -> flag what exceeds it
            subject, pred = self._drc_predicate(rule, sf, st, dl)
            if subject is None:
                return None
            hv = self._fmt(hi.value)
            body, v_scope = f"{subject}.drc({pred} {_BEYOND[hi.op]} {hv}.um)", hv
            if sf != st and dl(sf) != dl(st):
                # A maximum between two shapes is about B grown by v. (A pairwise
                # "distance > v" is not that: it holds for some pair of edges of any two
                # shapes.) WHERE the arrow ends says what is asked: on the far side of a
                # shape -- every part of it must be inside the grown B; on the edge
                # facing B -- the shape must reach the grown B at all. For an enclosure
                # the one reaching out is the outer shape.
                rel = rule.relation(sf, st)
                through = rule.measure_through(var)
                if rel in ("inside", "contains"):
                    far, near = (st, sf) if rel == "inside" else (sf, st)
                    body = f"{dl(far)}.not({dl(near)}.sized({hv}.um))"
                elif through:
                    # the arrow ends on the FAR side of a shape: every part of it
                    far, near = through, (st if through == sf else sf)
                    body = f"{dl(far)}.not({dl(near)}.sized({hv}.um))"
                else:
                    # facing edges: the shape (the one the arrow starts at) needs one
                    # within reach -- the others are those with none at all
                    body = f"{dl(sf)}.not_interacting({dl(st)}.sized({hv}.um))"
        else:                                              # a filter -> .drc() selector
            subject, pred = self._drc_predicate(rule, sf, st, dl)
            if subject is None:
                return None
            if hi is None:                                 # one-sided min: pred >= lo
                lv = self._fmt(lo.value)
                suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, sf))
                if self.scope_all:
                    sop, region = self.scope_all
                    suffix += f".{sop}({region.format(value=lv)})"
                return f"{subject}.drc({pred} {lo.op} {lv}.um){suffix}"
            hv = self._fmt(hi.value)
            if lo is None:                                 # one-sided: pred <op> hi
                inner = f"{pred} {hi.op} {hv}.um"
            else:                                          # range: lo <= pred < hi
                inner = f"{self._fmt(lo.value)}.um {_FLIPOP[lo.op]} {pred} {hi.op} {hv}.um"
            body, v_scope = f"{subject}.drc({inner})", hv

        suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, scope_from))
        if self.scope_all:                                # deck-level / computed scope
            sop, region = self.scope_all
            suffix += f".{sop}({region.format(value=v_scope)})"
        return f"{body}{suffix}"

    @staticmethod
    def _clipped_inner(core):
        """Is `core` an `OUTER.enclosing(INNER, ...)` whose INNER is and-ed with OUTER's
        own base layer (so INNER's boundary partly IS OUTER's)?"""
        m = re.match(r"(\w+)((?:\.\w+\([^()]*\))*)\.enclosing\((\w+)((?:\.\w+\([^()]*\))*)", core)
        if not m:
            return False
        outer, inner_base, inner_chain = m.group(1), m.group(3), m.group(4)
        return outer in [inner_base] + re.findall(r"\.and\((\w+)\)", inner_chain) and (
            ".and(" in inner_chain)

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

    def _emit_check(self, rule, var, lo, hi, idx, total, select=False):
        expr = self._check_expr(rule, var, lo, hi, select)
        if expr is not None:
            b = lo or hi
            rid = rule.name or (f"{var} {b.op} {self._fmt(b.value)}" if b else f"cross {var}")
            self._emit(rule, expr, idx, total, rid)

    def _emit(self, rule, expr, idx, total, rid):
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {expr}")
        self._checks.append(self._output(var, rid, desc))
        self._checks.append(f"{var}.forget")

    def _emit_union(self, rule, alts):
        """OR-of-variants: each alternative is a sub-rule {shapes, measures,
        constraints}; union their check expressions into one output
        (`expr1.or(expr2)...`). Sub-rules share this backend so their layers are
        declared once. Matches the deck's `varA.or(varB)` rules."""
        exprs = []
        for sub in alts:
            sr = Rule(sub)
            self._defs.update(sr.layer_defs)
            for var, lo, hi, select in sr.distance_rules():
                e = self._check_expr(sr, var, lo, hi, select)
                if e:
                    exprs.append(e)
            for var in [v for v, mm in sr.measures.items() if mm.get("crossed")]:
                e = self._check_expr(sr, var, None, None)   # a CROSSED (presence) variant
                if e:
                    exprs.append(e)
        if exprs:
            union = exprs[0] + "".join(f".or({e})" for e in exprs[1:])
            self._emit(rule, union, 0, 1, rule.name or "or")

    def _emit_area(self, rule, shape, op, value, idx, total, on_holes=False):
        """Area check on a (possibly derived/selected) layer. MIN (`area >= v`) ->
        with_area(nil, v) (flags the too-small polys); MAX (`area <= v`) ->
        with_area(v, nil) (flags the too-large polys).

        on_holes (a drawn donut + `A.holes.area`): the layer's VOIDS are the subject
        (`L.holes.with_area(...)`), so a min-void-area rule flags slivers/pinholes."""
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
        holes = ".holes" if on_holes else ""
        core = f"{self._derived(rule, shape, measured | sel_consumed)}{holes}{selsuf}.with_area({args})"
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
        self._checks.append(self._output(var, rid, desc))
        self._checks.append(f"{var}.forget")

    def _emit_angle(self, rule, edge, lo, hi, idx, total):
        """Corner-angle RANGE on a layer -> with_angle(lo .. hi): on a polygon layer it
        selects the corners whose two edges enclose an angle in the range (the acute-
        angle rule). The range comes from the constraint; the drawn corner is an
        example. The result is edge pairs output as they are, like the deck."""
        shape = rule.shape_of_edge(edge)
        layer = self._layer_var(rule.layer_of(shape) or f"_{shape}")
        core = f"{layer}.with_angle({self._fmt(lo)} .. {self._fmt(hi)})"
        suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, {shape}, shape))
        rid = rule.name or f"{edge}.angle in ({lo}..{hi})"
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {core}{suffix}")
        self._checks.append(self._output(var, rid, desc))
        self._checks.append(f"{var}.forget")

    def _emit_length(self, rule, edge, kind, lo, hi, idx, total):
        """Edge-length check. kind "with" -> layer.edges.with_length(min, max)
        (nil = unbounded; single arg when exact); kind "without" -> edges NOT of
        that length (without_length). Returns edges, so no .polygons."""
        shape = rule.shape_of_edge(edge)
        layer = self._shape_layer(rule, shape)
        nm = lambda x: "nil" if x is None else f"{self._fmt(x)}.um"
        role = rule.side_roles().get(edge)
        if role:
            # the LONG / SHORT side of a shape: its longer / shorter extent, one answer
            # per shape (an edge-length test would also catch the other sides)
            fn = ("without" if kind == "without" else "with") + ("_bbox_max" if role == "long" else "_bbox_min")
            args = nm(lo) if lo is not None and lo == hi else f"{nm(lo)}, {nm(hi)}"
            suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, {shape}, shape))
            self._emit(rule, f"{layer}.{fn}({args}){suffix}", idx, total, rule.name or f"{edge}.length")
            return
        if kind == "without":
            args = nm(lo) if lo == hi else f"{nm(lo)}, {nm(hi)}"
            core = f"{layer}.edges.without_length({args})"
        elif lo is not None and lo == hi:
            core = f"{layer}.edges.with_length({nm(lo)})"      # exact length
        else:
            core = f"{layer}.edges.with_length({nm(lo)}, {nm(hi)})"
        if rule.data.get("extended"):          # edge-SIZE: render the flagged edges as thin
            core += f".extended({self.EDGE_EXTEND})"   # polygons (a fixed output convention)
        suffix = "".join(f".{op}({mk})" for op, mk in self._scope(rule, {shape}, shape))
        rid = rule.name or f"{edge}.length"
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {core}{suffix}")
        self._checks.append(self._output(var, rid, desc))
        self._checks.append(f"{var}.forget")

    # corners() returns degenerate points; the deck sizes them to a small box so
    # they carry area and can be output. 0.1um matches the GF180 corner rules.
    CORNER_SIZE = "0.1"
    # edge-SIZE rules render flagged edges as 1nm-thick polygons so they're outputtable;
    # a fixed convention (the edge analogue of CORNER_SIZE), matching the GF180 deck.
    EDGE_EXTEND = "0, 0, 0.001, 0.001"

    def _emit_corner(self, rule, edge, angle, idx, total, others=False):
        """Exact corner-angle rule -> corners(t).sized(S).or(corners(-t).sized(S)).
        `angle` is the angle BETWEEN the two edges; KLayout's corners() takes the TURN
        (180 - angle), and both signs are emitted because the rule is on the magnitude
        (convex OR concave), matching the deck's `corners(90).or(corners(-90))`.
        `others` flags every corner that is NOT of that angle instead."""
        shape = rule.shape_of_edge(edge)
        layer = self._layer_var(rule.layer_of(shape) or f"_{shape}")
        a, na, sz = self._fmt(180 - angle), self._fmt(angle - 180), self.CORNER_SIZE
        if others:
            core = (f"{layer}.corners.sized({sz}).not({layer}.corners({a}).sized({sz}))"
                    f".not({layer}.corners({na}).sized({sz}))")
        else:
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
        rid = rule.name or f"{edge}.corner {'!=' if others else '=='} {angle}"
        desc = rule.desc or rid
        var = self._result_var(rid, idx, total)
        self._checks.append(f"{var} = {core}{suffix}")
        self._checks.append(self._output(var, rid, desc))
        self._checks.append(f"{var}.forget")

    _FORM = {"square": ".squares", "oblong": ".non_squares"}

    def _array(self, layer, arr):
        """The shapes of `layer` that sit in an array of at least rows x columns.

        The array: shapes closer to each other than the checked spacing V merge into
        one group (a closing by V/2). A group at least N elements across is wider than
        N-1 elements could ever span inside a group, (N-1)*size + (N-2)*V -- so the
        parts of the group at least that wide both ways (an opening) are the large
        array, and the shapes inside it are its members. Without a stated element size
        the group is taken by COUNT instead (at least rows*columns shapes)."""
        half = self._fmt(round(arr["gap"] / 2, 6))
        group = f"{layer}.sized({half}.um).sized(-{half}.um)"
        stem = re.sub(r"\W+", "_", layer).strip("_") + f"_array_{arr['rows']}x{arr['columns']}"
        if arr["size"] is None:
            n = arr["rows"] * arr["columns"]
            return self._name_it(stem, f"{layer}.inside({group}.interacting({layer}, {n}))")
        span = lambda n: max(0.0, (n - 1) * arr["size"] + (n - 2) * arr["gap"]) if n > 1 else 0.0
        tx, ty = (self._fmt(round(span(arr[k]) / 2, 6)) for k in ("columns", "rows"))
        by = f"{tx}.um" if tx == ty else f"{tx}.um, {ty}.um"
        neg = f"-{tx}.um" if tx == ty else f"-{tx}.um, -{ty}.um"
        return self._name_it(stem, f"{layer}.inside({group}.sized({neg}).sized({by}))")

    # Density windows: stepped across the area from its lower-left corner, none
    # reaching outside it; where the last step stops short of the far edge, one more
    # row / column of windows is laid against that edge so all of the area is seen.
    # (KLayout places its tiles by their centre, hence the origin; the windows at the
    # far edges -- and all of them when the area is only one window wide or tall --
    # are measured directly.)
    _WINDOWS = """out_of_density = lambda do |layer, range, area, size, step|
  b = area.bbox
  nx = [1 + ((b.width - size) / step).floor, 1].max
  ny = [1 + ((b.height - size) / step).floor, 1].max
  xs = (0...nx).map { |i| b.left + i * step }
  ys = (0...ny).map { |j| b.bottom + j * step }
  far_x = b.width > size + (nx - 1) * step ? [b.right - size] : []
  far_y = b.height > size + (ny - 1) * step ? [b.top - size] : []
  # KLayout's tiling needs at least two windows each way (a single row or column
  # comes back empty): a narrow area is measured window by window instead
  tiled = nx >= 2 && ny >= 2
  found = polygon_layer
  if tiled
    found = layer.without_density(range, tile_size(size), tile_step(step), tile_count(nx, ny),
                                  tile_boundary(area),
                                  tile_origin(b.left + (size - step) / 2.0, b.bottom + (size - step) / 2.0))
  end
  direct = far_x.product(ys + far_y) + xs.product(far_y)
  direct += xs.product(ys) unless tiled
  direct.each do |x, y|
    window = polygon_layer
    window.insert(box(x, y, x + size, y + size))
    inside = window & area
    next if inside.is_empty?
    found += window unless range.include?((layer & inside).area / inside.area)
  end
  found
end"""

    def _density(self, rule, shape, lo, hi, window):
        """Coverage of a layer. In a sliding WINDOW: the windows whose density is out of
        range (`without_density`, the range inclusive). Over the WHOLE area: the area
        itself is flagged when the layer covers too little / too much of it. The area
        is the window shape's layer when it has one (a chip boundary), else the
        layout's extent."""
        skip = {shape} | ({window["shape"]} if window else set())
        layer = self._derived(rule, shape, skip)
        area = self._layer_var(window["layer"]) if window and window["layer"] else "extent"
        f = self._fmt
        if window and window["size"] is not None:
            rng = f"{f(lo) if lo is not None else '0.0'} .. {f(hi) if hi is not None else '1.0'}"
            self._helpers["out_of_density"] = self._WINDOWS
            size = float(window["size"])
            return (f"out_of_density.call({layer}, {rng}, {area}, {size!r}, "
                    f"{float(window['step'] or size)!r})")
        tests = ([f"{layer}.area < {f(lo)} * {area}.area"] if lo is not None else []) + (
            [f"{layer}.area > {f(hi)} * {area}.area"] if hi is not None else [])
        return f"({' || '.join(tests)}) ? {area}.dup : polygon_layer"

    def _holding(self, layer, lo, hi, select):
        """Count what a shape holds of `layer` (everything that touches or overlaps
        it). `select`: the shapes whose count is in the range; else those whose count
        is NOT (a shape holding none included) -- the violations of a count check."""
        k = self._layer_var(layer)
        if lo is None:                               # at most hi
            return f".not_interacting({k}, {hi + 1})" if select else f".interacting({k}, {hi + 1})"
        rng = f"{lo}" if hi is None else f"{lo}, {hi}"
        return f".{'interacting' if select else 'not_interacting'}({k}, {rng})"

    def _held_ratio(self, rule, held, holder, lo, hi, stem):
        """How much of each holder its contents take up. Each holder with what lies in
        it is made one group (a private connection between the two), the areas are
        taken per group, and the holders whose ratio is out of range are the result --
        a holder holding nothing included."""
        d, consumed = rule.holder_derivation(holder, held)
        self._checks.append(f"{stem}_in = {self._fmt_deriv(d)}")
        names = {}
        for i, h in enumerate(held):
            names[chr(ord("a") + i)] = f"{stem}_h{i + 1}"
            self._checks.append(f"{stem}_h{i + 1} = {self._layer_var(rule.layer_of(h))}.and({stem}_in)")
            self._checks.append(f"connect({stem}_h{i + 1}, {stem}_in)")
        args = ", ".join(f'"{k}" => {v}' for k, v in names.items())
        total = " + ".join(f"area({k})" for k in names)
        f = self._fmt
        bad = " || ".join(([f"value('r') < {f(lo)}"] if lo is not None else [])
                          + ([f"value('r') > {f(hi)}"] if hi is not None else []))
        expr = (f"evaluate_nets({stem}_in, {{ {args} }}, \"put('r', ({total}) / area)\")"
                f".selected_if(\"{bad}\")")
        return expr + "".join(f".{op}({mk})" for op, mk in self._scope(rule, consumed, holder))

    def _use_connections(self, pairs):
        """Declare how layers connect, before a rule that needs nets. Connections are
        only ever ADDED while the declared ones are part of what is needed; a rule
        that needs fewer (an antenna rule sees the stack only up to its own layer)
        starts them over."""
        pairs = [tuple(p) for p in pairs]
        have = self._connect or []
        if not pairs or (self._connect is not None and set(pairs) == set(have)):
            return
        if not set(have) <= set(pairs):
            self._checks.append("clear_connections")
            have = []
        self._checks += [f"connect({self._layer_var(a)}, {self._layer_var(b)})"
                         for a, b in pairs if (a, b) not in have]
        self._connect = have + [p for p in pairs if p not in have]

    def _antenna(self, rule, collectors, gate, ratio, filters, stem):
        """One collector, every net: KLayout's antenna_check. Several collectors summed,
        or only the nets with / without something else on them (a diode): each layer's
        share is taken at ITS stage of the stack (connections are added layer by
        layer, never started over in between -- that would lose the earlier stages),
        the conditions on the finished stack, then the gates whose net fails."""
        g = self._derived(rule, gate, set(collectors) | {gate})
        lay = lambda sh: self._derived(rule, sh, set(collectors) | {gate})
        glayer = rule.layer_of(gate)
        stage = lambda sh: rule.connections_up_to(glayer, rule.layer_of(sh))
        if len(collectors) == 1 and not filters:
            self._use_connections(stage(collectors[0]))
            return f"antenna_check({g}, {lay(collectors[0])}, {float(ratio)!r})"
        parts = []
        for i, sh in enumerate(sorted(collectors, key=lambda x: len(stage(x))), 1):
            self._use_connections(stage(sh))
            self._checks.append(f"{stem}_t{i} = evaluate_nets({g}, {{ \"x\" => {lay(sh)} }}, "
                                f"\"put('t{i}', area(x) / area)\")")
            parts.append(f"t{i}")
        conds = []
        if filters:
            self._use_connections(rule.connections())
        for j, (sh, op, value) in enumerate(filters, 1):
            self._checks.append(f"{stem}_f{j} = evaluate_nets({g}, {{ \"x\" => {lay(sh)} }}, "
                                f"\"put('f{j}', area(x))\")")
            conds.append(f"value('f{j}') {op} {self._fmt(value)}")
        names = [f"{stem}_{n}" for n in parts] + [f"{stem}_f{j}" for j in range(1, len(filters) + 1)]
        total = " + ".join(f"value('{n}')" for n in parts)
        self._checks.append(f"{stem}_nets = ({' + '.join(names)}).merged_props")
        cond = " && ".join(conds + [f"{total} > {self._fmt(ratio)}"])
        return f"{stem}_nets.selected_if(\"{cond}\")"

    def _name_it(self, stem, expr):
        """Give a long derived layer a name, declared once at the top of the deck, so
        the rules that use it stay readable."""
        if expr not in self._named:
            name, k = stem, 2
            while name in self._named.values() or name in self._used or name in self.regions:
                name, k = f"{stem}_{k}", k + 1
            self._named[expr] = name
        return self._named[expr]

    def _shape_layer(self, rule, shape):
        """A shape's own layer, narrowed to its shape class when a filter states one."""
        return (self._layer_var(rule.layer_of(shape) or f"_{shape}")
                + self._FORM.get(rule.shape_forms().get(shape), ""))

    def _fmt_deriv(self, d):
        """Render a Rule derivation {base, ands, nots, selections} to KLayout syntax.
        AND-operands (base + covers) sorted (commute); then the selections that must
        precede the cutouts; then .not(cutouts); then each remaining selection
        .op(marker), the marker itself a rendered derivation. This is the
        ONLY place the KLayout backend turns the shared analysis into op text."""
        own = self._layer_var(d["base"]) + self._FORM.get(d.get("form"), "")
        if d.get("array"):
            own = self._array(own, d["array"])
        if d.get("count"):                           # only those holding so many of a layer
            own += self._holding(*d["count"], select=True)
        keep = ""
        if d.get("nets"):                            # shapes carrying the net they belong to
            keep = ", props_copy"                    # ... through the layer algebra
            members = (d["base"] or "").split("|")
            if len(members) > 1 and not (d.get("form") or d.get("array") or d.get("count")):
                # "any of these layers": nets can only be taken from each layer itself
                # (not from their union), so take them one by one and put them together
                own = "(" + " + ".join(f"{self._layer_var(m)}.nets" for m in sorted(members)) + ")"
            else:
                own += ".nets"
        expr = own + "".join(                        # base, then sorted ANDs
            f".and({v}{keep})" for v in sorted(self._layer_var(a) for a in d["ands"]))
        for op, marker in sorted((o, self._fmt_deriv(m)) for o, m in d.get("pre_selections", [])):
            expr += f".{op}({marker})"      # a relation to a layer that is then cut out
        for n in sorted(self._layer_var(x) for x in d["nots"]):
            expr += f".not({n}{keep})"
        for op, marker in sorted({(o, self._fmt_deriv(m)) for o, m in d["selections"]}):
            # straddling = shares area with it, yet is not wholly inside it
            expr += (f".overlapping({marker}).not_inside({marker})" if op == "straddling"
                     else f".{op}({marker})")
        if d.get("wide"):                   # only the parts at least this wide (an opening)
            # An opening by half the width keeps what is WIDER than w -- a part exactly
            # w wide shrinks to nothing. "At least w" therefore opens by one database
            # unit (1 nm) less: on a grid coarser than 2 nm that is exactly ">= w".
            less = 0.0 if d.get("wide_strict") else 0.001
            h = self._fmt(round(float(d["wide"]) / 2 - less, 6))
            expr += f".sized(-{h}.um).sized({h}.um)"
        if d.get("halo"):                   # a signed margin: grow (>0) / shrink (<0) the region
            expr += f".sized({self._fmt(d['halo'])}.um)"
            # NOTE: `rounded` is carried on the Rule but not rendered yet -- a rounded grow
            # isn't a `.sized` mode (those handle acute corners); it needs rounded_corners.
        return expr

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
        form = self._FORM.get(rule.shape_forms().get(shape), "")
        own = self._layer_var(layers[0])
        vs = sorted({self._layer_var(l) for l in layers})
        if form:                                 # the class is of the shape's OWN layer
            vs = [own + form] + [v for v in vs if v != own]
        expr = vs[0] if len(vs) == 1 else vs[0] + "".join(f".and({v})" for v in vs[1:])
        # NOT each removed (cutout) layer; + any legacy tagged-hole layers
        nots = {self._layer_var(l) for s in rule.removed(shape) if s not in measured
                for l in (rule.layer_of(s) or f"_{s}").split("|")}
        nots |= {self._layer_var(h) for h in rule.hole_layers(shape)}
        nots |= {self._layer_var(l) for l in rule.excepted()}
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
        # a scope stated in words -- the column of a manual's table (3.3 V / 5 V)
        out += [(op, self._layer_var(layer)) for op, layer in rule.data.get("scope") or []]
        return sorted(out, key=lambda x: (x[1], x[0]))

    def _layer_var(self, layer):
        if layer and "|" in layer:            # a layer SET: the union of its members
            first, *more = [self._layer_var(l) for l in layer.split("|")]
            return first + "".join(f".or({v})" for v in more)
        if layer not in self._layers:
            base = var = self._var(layer)
            k = 2
            while var in self._used:              # "a.b" and "a_b" must not share a var
                var, k = f"{base}_{k}", k + 1
            self._used[var] = layer
            if layer in getattr(self, "_defs", {}):          # a legend name: built from its layers
                self._decls.append(f"{var} = {self._layer_expr(self._defs[layer])}")
            elif layer in self.layermap:
                gl, dt = self.layermap[layer]
                self._decls.append(f"{var} = polygons({gl}, {dt})")
            else:
                self._decls.append(f'{var} = input("{self._q(layer)}")')
                if layer not in self.unmapped:
                    self.unmapped.append(layer)      # no GDS number known for it
            self._layers[layer] = var
        return self._layers[layer]

    def _layer_expr(self, ast):
        """A legend definition (`comp and pplus`, `poly2 and not comp`, `a or b`) as a
        KLayout layer expression."""
        if "ref" in ast:
            return self._layer_var(ast["ref"])
        op, args = ast["op"], ast["args"]
        if op in ("+", "-") and "num" in args[1]:         # a layer grown / shrunk: the zone
            d = self._fmt(args[1]["num"])                 # "within x of" it, as a manual says
            return f"{self._layer_expr(args[0])}.sized({'-' if op == '-' else ''}{d}.um)"
        if op == "or":
            first, *rest = [self._layer_expr(a) for a in args]
            return first + "".join(f".or({r})" for r in rest)
        if op == "and":                                   # positives first, then the nots
            pos = [a for a in args if a.get("op") != "not"]
            neg = [a["args"][0] for a in args if a.get("op") == "not"]
            expr = self._layer_expr(pos[0]) + "".join(f".and({self._layer_expr(a)})" for a in pos[1:])
            return expr + "".join(f".not({self._layer_expr(a)})" for a in neg)
        raise ValueError(f"a layer definition cannot use '{op}'")

    @staticmethod
    def _q(text):
        """Escape text for a Ruby double-quoted string: backslash, the quote, and the
        `#` of an interpolation (`#{..}`, `#$x`, `#@x`), so a rule's name/description
        is always data, never code."""
        s = str(text).replace("\\", "\\\\").replace('"', '\\"')
        return re.sub(r"#(?=[{$@])", r"\\#", s)

    def _output(self, var, rid, desc):
        return f'{var}.output("{self._q(rid)}", "{self._q(desc)}")'

    def _emit_eol(self, rule, var, cap, side, idx, total):
        """END-OF-LINE. The CAP edge of a narrow wire (recovered geometrically) carries
        a spacing/enclosing rule. Emit KLayout's edge-of-line idiom: select the narrow
        runs (drc(width <= Weol).with_length(Slen, nil, both)), take their cap edges
        (short edges bridging BOTH sides, minus the sides), apply the rule. Weol/Slen
        come from the cap's / a side's edge-length bounds; op + arg from the geometry."""
        m = rule.measures[var]
        sf, st = rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"])
        L = self._layer_var(rule.layer_of(sf))
        mt = m.get("metric") or self.metric
        lb = rule.length_spans()
        weol, slen = f"{self._fmt(lb[cap][1])}.um", f"{self._fmt(lb[side][0])}.um"
        v = f"{self._fmt(next(b.value for b in rule.bounds() if b.var == var and b.is_min))}.um"
        rid = rule.name or f"{var}.eol"
        desc = rule.desc or rid
        cond, eol = f"eol{idx}_cond", f"eol{idx}"
        self._checks.append(f"{cond} = {L}.drc(width <= {weol}).with_length({slen}, nil, both)")
        self._checks.append(
            f"{eol} = {L}.edges.with_length(nil, {weol})"
            f".interacting({cond}.first_edges).interacting({cond}.second_edges)"
            f".not({cond}.first_edges).not({cond}.second_edges)")
        op = "enclosing" if rule.relation(sf, st) in ("contains", "inside") else "separation"
        arg = f"{self._layer_var(rule.layer_of(st))}.edges"
        res = self._result_var(rid, idx, total)
        self._checks.append(f"{res} = {eol}.{op}({arg}, {v}, {mt}).polygons(0.001)")
        self._checks.append(self._output(res, rid, desc))
        for fv in (res, eol, cond):
            self._checks.append(f"{fv}.forget")

    def _result_var(self, rid, idx, total):
        base = self._var(rid).lower()
        return f"{base}_l1" if total == 1 else f"{base}_{idx + 1}_l1"

    @staticmethod
    def _var(layer):
        v = "".join(c if (c.isascii() and c.isalnum() or c == "_") else "_" for c in str(layer))
        return v if v and not v[0].isdigit() else "_" + v      # a Ruby local can't start with a digit

    @staticmethod
    def _fmt(v):
        # KLayout deck writes integer thresholds without a trailing .0 (e.g. 1.um)
        f = float(v)
        return str(int(f)) if f.is_integer() else str(v)
