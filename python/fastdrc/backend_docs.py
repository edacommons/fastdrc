"""A DOCS backend: compile a `Rule` into a full English description.

Same contract as every backend (`compile(rule) -> str`), reading the SAME
geometry-recovery surface the KLayout backend uses (relation / covering /
removed / layer_of / relation_selections / bounds / measures). It emits prose
instead of `.drc`, so a human can read a rule back and confirm it says exactly
what the rule should do -- and, because it consumes the same analyzed `Rule`,
it doubles as a cross-check that the representation is faithful (no shortcuts):
if the English is right, the structure was recovered correctly, independently
of the KLayout codegen.
"""

from __future__ import annotations

from .backend import Backend, register
from .rule import Rule


def _rel_to_sel(rel):                       # mirror of the KLayout backend's mapping
    return {"inside": "inside", "touch": "interacting",
            "overlap": "overlapping"}.get(rel, "outside")


# a CROSS on a relation forbids it: how the REQUIREMENT reads, per the selection op
# the crossed relation compiles to (the op selects the violating shapes, so the
# sentence states its opposite).
_CROSS = {"straddling": "must not straddle the edge of",
          "overlapping": "must not overlap", "inside": "must not lie inside",
          "interacting": "must not touch or overlap", "outside": "must overlap",
          "not_interacting": "must touch or overlap", "not_inside": "must lie inside"}
# how a SELECTION op reads as a restricting clause (singular, plural)
_SEL = {"straddling": ("straddles the edge of", "straddle the edge of"),
        "inside": ("lies inside", "lie inside"), "outside": ("lies outside", "lie outside"),
        "interacting": ("touches or overlaps", "touch or overlap"),
        "overlapping": ("overlaps", "overlap"),
        "not_interacting": ("is clear of", "are clear of"),
        "not_inside": ("does not lie inside", "do not lie inside")}


def _fmt(v):
    return None if v is None else f"{float(v):g}"


def _join(items):
    items = list(items)
    if len(items) <= 1:
        return items[0] if items else ""
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


@register
class DocsBackend(Backend):
    name = "docs"

    def __init__(self, glossary=None, **_):
        # OPTIONAL, PROCESS-SPECIFIC layer glossary {layer_name: human description},
        # supplied WITH the PDK. Use TERSE noun phrases without leading articles so they
        # read in any slot (e.g. {"tgate": "transistor gate", "v5_xtor": "5V region",
        # "ncomp": "N+ diffusion"}) -> "no contact that overlaps transistor gate". The
        # backend stays process-agnostic: raw layer names by default, humanized only
        # when a glossary is given. PDK knowledge never lives in this backend.
        self.glossary = glossary or {}

    def _name(self, layer):
        if layer and "|" in layer:            # a layer SET reads as "a or b"
            return " or ".join(self._name(l) for l in layer.split("|"))
        return self.glossary.get(layer, layer)

    def compile_all(self, rules) -> str:
        return "".join(self.compile(r if isinstance(r, Rule) else Rule(r)) for r in rules)

    def compile(self, rule: Rule) -> str:
        head = rule.name or "(rule)"
        if rule.desc and rule.desc != head:
            head = f"{head} — {rule.desc}"
        lines = [head]
        if rule.data.get("or"):             # OR-of-variants: one bullet per alternative
            lines.append("  A violation is ANY of these configurations:")
            for i, sub in enumerate(rule.data["or"], 1):
                for s in self.compile(Rule(sub)).splitlines()[1:]:
                    lines.append(("    " + s.strip()) if i == 1 or "•" in s else "    " + s.strip())
            return "\n".join(lines) + "\n"
        for sent in self._sentences(rule):
            lines.append(f"  • {sent}")
        for name, expr in (rule.data.get("layers") or {}).items():   # the legend, as drawn
            lines.append(f"    where {name} = {expr}")
        if len(lines) == 1:
            lines.append("  • (no checkable assertion found)")
        return "\n".join(lines) + "\n"

    # --- one English sentence per check the rule carries ---
    def _sentences(self, rule):
        out = []
        eol = rule.eol_measures()               # an end-of-line is ONE rule: its measure and
        eol_vars = {v for v, _, _ in eol}       # the two qualifying edge lengths are not
        eol_edges = rule.eol_edges()            # separate assertions
        for var, lo, hi, select in rule.distance_rules():
            if var not in eol_vars:
                out.append(self._dim(rule, var, lo, hi, select))
        for shape, op, value, on_holes in rule.area_bounds():
            kind = "at least" if op in (">", ">=") else "at most"
            d, _, consumed = rule.check_derivations(shape, shape)
            subj = self._fmt_deriv_en(d)
            if on_holes:
                core = (f"every enclosed void (hole) in {subj} must be {kind} "
                        f"{_fmt(value)} µm² in area.")
            else:
                core = f"{subj} must be {kind} {_fmt(value)} µm² in area."
            out.append(self._with_scope(core, self._scope(rule, shape, consumed)))
        for edge, lo, hi in rule.angle_bounds() + rule.corner_ranges():
            subj = self._deriv_en(rule, rule.shape_of_edge(edge))
            if float(lo) == 0:
                out.append(f"every {subj} corner must be at least {_fmt(hi)}° "
                           f"(a sharper, acute corner is flagged).")
            elif float(hi) == 180:
                out.append(f"every {subj} corner must be at most {_fmt(lo)}°.")
            else:
                out.append(f"{subj} must have no corner between {_fmt(lo)}° and {_fmt(hi)}°.")
        for edge, angle, others in rule.corner_bounds():
            subj = self._deriv_en(rule, rule.shape_of_edge(edge))
            out.append(f"every {subj} corner must be {_fmt(angle)}°." if others
                       else f"{subj} must have no {_fmt(angle)}° corner.")
        for edge, kind, form, lo, hi in rule.length_facts():
            if edge not in eol_edges and edge not in rule.array_edges():
                out.append(self._length(rule, edge, kind, form, lo, hi))
        for a, b, shape in rule.either_bounds():
            m = rule.measures[a.var]
            sf, st = rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"])
            ds, dt, consumed = rule.measure_derivations(a.var)
            other = self._fmt_deriv_en(dt if shape == sf else ds)
            me, v = self._fmt_deriv_en(ds if shape == sf else dt), _fmt(a.value)
            if rule.relation(sf, st) in ("inside", "contains"):
                core = (f"{other} must enclose {me} by at least {v} µm on at least one of any two "
                        f"adjacent sides")
            else:
                core = (f"{me} must be at least {v} µm from {other} on at least one of any two "
                        f"adjacent sides (in one direction)")
            out.append(self._with_scope(core + ".", self._scope(rule, sf, consumed)))
        for shape, form in rule.form_checks():
            subj = self._deriv_en(rule, shape)
            out.append(f"every {subj} shape must be a square." if form == "square"
                       else f"no {subj} shape may be a square.")
        for var in [v for v, mm in rule.measures.items() if mm.get("crossed")]:
            out.append(self._presence(rule, var))
        for var, cap, side in eol:
            out.append(self._eol(rule, var, cap, side))
        for inner, outer in rule.inside_checks():
            d_in, d_out, used = rule.check_derivations(inner, outer)
            out.append(self._with_scope(
                f"every {self._fmt_deriv_en(d_in)} must lie wholly inside "
                f"{self._fmt_deriv_en(d_out)}.", self._scope(rule, inner, used)))
        return out

    def _length(self, rule, edge, kind, form, lo, hi):
        """One edge-length constraint. A CHECK states what every edge must satisfy (the
        ones that fail are flagged); a MATCH only says which edges are picked out."""
        subj = self._deriv_en(rule, rule.shape_of_edge(edge))
        role = rule.side_roles().get(edge)
        if role:                              # the long / short side: one answer per shape
            side = "longer" if role == "long" else "shorter"
            rng = (f"exactly {_fmt(lo)} µm" if form == "exact" else f"other than {_fmt(lo)} µm" if form == "not"
                   else f"between {_fmt(lo)} and {_fmt(hi)} µm" if lo is not None and hi is not None
                   else f"at least {_fmt(lo)} µm" if lo is not None else f"at most {_fmt(hi)} µm")
            if kind == "check":
                return f"the {side} side of every {subj} must be {rng}."
            return f"{subj} whose {side} side is {rng} are flagged."
        if form == "range":
            rng = (f"between {_fmt(lo)} and {_fmt(hi)} µm" if lo is not None and hi is not None
                   else f"at least {_fmt(lo)} µm" if lo is not None else f"at most {_fmt(hi)} µm")
        if kind == "check":
            if form == "exact":
                return (f"every {subj} edge must be exactly {_fmt(lo)} µm long "
                        f"(any other length is flagged — a min/max size check).")
            if form == "not":
                return f"no {subj} edge may be exactly {_fmt(lo)} µm long."
            return f"every {subj} edge must be {rng} long."
        if form == "exact":
            return f"{subj} edges exactly {_fmt(lo)} µm long are flagged."
        if form == "not":
            return f"{subj} edges that are not {_fmt(lo)} µm long are flagged."
        return f"{subj} edges {rng} long are flagged."

    def _eol(self, rule, var, cap, side):
        m = rule.measures[var]
        sf, st = rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"])
        L, arg = self._name(rule.layer_of(sf)), self._name(rule.layer_of(st))
        lb = rule.length_spans()
        v = _fmt(next(b.value for b in rule.bounds() if b.var == var and b.is_min))
        verb = ("enclose" if rule.relation(sf, st) in ("contains", "inside")
                else "be at least {} µm from".format(v))
        tail = f"{arg} by at least {v} µm" if verb == "enclose" else f"{arg}"
        return (f"the end-of-line edge of a narrow {L} wire (width ≤ {_fmt(lb[cap][1])} µm, "
                f"length ≥ {_fmt(lb[side][0])} µm) must {verb} {tail}.")

    # --- the SAME analyzed view the KLayout backend formats, rendered in English ---
    def _deriv_en(self, rule, shape):       # single-shape derivation (area / angle / length)
        d, _, _ = rule.check_derivations(shape, shape)
        return self._fmt_deriv_en(d)

    def _dim(self, rule, var, lo, hi, select=False):
        m = rule.measures[var]
        sf, st = rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"])
        mt = m.get("metric") or "euclidian"
        if m.get("edges"):                  # gate-edge (directional) width -- the edge_dl form
            scope = self._scope(rule, sf, {sf} | set(rule.covering(sf)) | set(rule.removed(sf)))
            v = _fmt(lo.value)
            core = (f"{self._gate_en(rule, sf)} must be at least {v} µm wide (a directional "
                    f"edge-to-edge width, not the polygon width)" if sf == st
                    else f"{self._gate_en(rule, sf)} must be at least {v} µm from "
                         f"{self._gate_en(rule, st)}")
            return self._with_scope(core + f" ({mt}).", scope)
        ds, dt, consumed = rule.measure_derivations(var)    # the shared analysis
        subj, arg = self._fmt_deriv_en(ds), self._fmt_deriv_en(dt)
        scope = self._scope(rule, sf, consumed)
        if not select and hi is not None:               # MAX check -> must not exceed it
            v = _fmt(hi.value)
            if sf == st:
                core = f"{subj} must be at most {v} µm wide"
            elif rule.relation(sf, st) in ("inside", "contains"):
                outer, inner = (arg, subj) if rule.relation(sf, st) == "inside" else (subj, arg)
                core = f"no part of {outer} may reach more than {v} µm beyond {inner}"
                return self._with_scope(core + ".", scope)
            elif subj == arg:
                core = f"any two shapes of {subj} must be at most {v} µm apart"
            else:
                core = f"every part of {subj} must be within {v} µm of {arg}"
                return self._with_scope(core + ".", scope)
            return self._with_scope(core + f" ({mt}).", scope)
        over = None if select or hi is not None else rule.overlap_reading(var)
        if over:                                        # partly overlapping shapes
            v = _fmt(lo.value)
            a, b = self._fmt_deriv_en(over["outer"]), self._fmt_deriv_en(over["inner"])
            core = {"extension": f"{a} must extend at least {v} µm beyond {b}",
                    "overlap": f"{a} must overlap {b} by at least {v} µm"}[over["kind"]]
            if lo.zero_ok:
                core += " — or exactly 0 (coinciding edges are allowed)"
            return self._with_scope(core + f" ({mt}).",
                                    self._scope(rule, over["outer_shape"], over["consumed"]))
        if not select:                                  # MIN check -> a violation check
            v = _fmt(lo.value)
            if sf == st:
                core = (f"each of {subj} must be at least {v} µm wide" if "|" in (ds["base"] or "")
                        else f"{subj} must be at least {v} µm wide")
            else:
                rel = rule.relation(sf, st)
                if rel in ("inside", "contains"):
                    outer, inner = (arg, subj) if rel == "inside" else (subj, arg)
                    core = f"{outer} must enclose {inner} by at least {v} µm"
                    if m.get("within"):
                        core += f", and no {inner} may lie across its edge"
                elif subj == arg:                   # the SAME derived layer on both sides
                    core = f"any two shapes of {subj} must be at least {v} µm apart"
                else:
                    core = f"{subj} must be at least {v} µm from {arg}"
                    if rule.cut_apart(ds, dt):     # they share the edge of the cut itself
                        core += " (where the two abut is not a spacing)"
            run = rule.run_bounds().get(var)
            if run:
                core += f", where the edges run side by side for {run[0]} {_fmt(run[1])} µm"
            if lo.zero_ok:
                core += " — or exactly 0 (coinciding edges are allowed)"
            return self._with_scope(core + f" ({mt}).", scope)
        pred = "width" if sf == st else "space" if subj == arg else "separation"
        cond = (f"{pred} ≥ {_fmt(lo.value)} µm" if hi is None
                else f"{pred} ≤ {_fmt(hi.value)} µm" if lo is None
                else f"{_fmt(lo.value)} µm ≤ {pred} < {_fmt(hi.value)} µm")
        return self._with_scope(f"selects {subj} regions where {cond}.", scope)

    def _presence(self, rule, var):
        """A CROSS: a forbidden configuration. Drawn on a relation between two shapes
        it forbids THAT relation -- everything else about the subject is the filter --
        so the sentence states the requirement on it; drawn across the subject alone,
        the subject's region itself must not exist."""
        m = rule.measures[var]
        sf = rule.shape_of_edge(m["from"])
        d, _, consumed = rule.check_derivations(sf, sf)
        scope = self._scope(rule, sf, consumed)
        pair = rule.crossed_relation(var)
        hit = [(op, mk) for op, mk in d["selections"] if pair and mk.get("shape") == pair[1]]
        pre = [(op, mk) for op, mk in d.get("pre_selections", [])
               if pair and mk.get("shape") == pair[1]]
        if pre:
            # the crossed relation is to a layer that is also cut out of the subject: the
            # violation is the part of the subject left outside it -- the subject
            # STRADDLES that layer's edge.
            op, marker = pre[0]
            rest = dict(d, nots=[n for n in d["nots"] if n != marker["base"]],
                        pre_selections=[x for x in d["pre_selections"] if x[1] is not marker])
            return self._with_scope(
                f"{self._fmt_deriv_en(rest)} that {_SEL.get(op, ('relates to',))[0]} "
                f"{self._fmt_deriv_en(marker)} must not extend beyond it.", scope)
        if not hit:
            return self._with_scope(f"no {self._fmt_deriv_en(d)} may exist.", scope)
        op, marker = hit[0]
        rest = dict(d, selections=[x for x in d["selections"] if x[1] is not marker])
        return self._with_scope(f"{self._fmt_deriv_en(rest)} {_CROSS.get(op, 'must not relate to')} "
                                f"{self._fmt_deriv_en(marker)}.", scope)

    def _layer(self, rule, shape):
        return rule.layer_of(shape) or "?"

    def _fmt_deriv_en(self, d):
        """Render a Rule derivation {base, ands, nots, selections} in English -- the
        exact counterpart of the KLayout backend's `_fmt_deriv`, over the SAME facts."""
        from collections import defaultdict
        phrase = self._name(d["base"])
        if d.get("form"):                    # the shape class a filter states
            phrase = ("square " if d["form"] == "square" else "non-square ") + phrase
        arr = d.get("array")
        if arr:                              # members of a large enough array
            dims = f"{arr['rows']} rows and {arr['columns']} columns"
            phrase += (f" in an array of at least {dims} (shapes within {_fmt(arr['gap'])} µm of each other"
                       + (f", each {_fmt(arr['size'])} µm" if arr["size"] is not None else "") + ")")
        if d["ands"]:
            phrase += " combined with " + _join(sorted(self._name(a) for a in d["ands"]))

        def clauses(selections):
            by_op, out = defaultdict(list), ""
            for op, marker in selections:
                by_op[op].append(self._fmt_deriv_en(marker))
            for op in sorted(by_op):
                ctxs = sorted(set(by_op[op]))
                # bracket only to delimit a MULTI-context list with compound parts; a single
                # context (even a compound one) reads fine without brackets
                multi = len(ctxs) > 1 and any(" " in c for c in ctxs)
                wrap = (lambda p: f"[{p}]") if multi else (lambda p: p)
                out += f" that {_SEL.get(op, ('relates to',))[0]} {_join([wrap(c) for c in ctxs])}"
            return out

        phrase += clauses(d.get("pre_selections", []))   # read before the cutouts, as compiled
        if d["nots"]:
            phrase += " (excluding " + _join(sorted(self._name(n) for n in d["nots"])) + ")"
        phrase += clauses(d["selections"])
        if d.get("wide"):
            phrase += f" at least {_fmt(d['wide'])} µm wide"
        h = d.get("halo")
        if h:                                # the signed halo / margin (rounded reserved)
            phrase = (f"{phrase} grown by {_fmt(h)} µm" if h > 0
                      else f"{phrase} shrunk by {_fmt(-h)} µm")
        return phrase

    def _gate_en(self, rule, shape):        # gate-edge subject (mirrors the backend's edge_dl)
        base = self._layer(rule, shape)
        gate, filt = [], []
        for c in rule.covering(shape):
            cl = rule.layer_of(c)
            if cl and cl != base:
                (filt if rule.relation(shape, c) == "inside" else gate).append(cl)
        cuts = sorted({self._name(l) for c in rule.removed(shape)
                       for l in (rule.layer_of(c) or "?").split("|")}
                      | {self._name(l) for l in rule.excepted()})
        phrase = (f"{self._name(base)} edges over the gate ({self._name(base)}"
                  + "".join(f" & {self._name(g)}" for g in sorted(set(gate))) + ")") if gate \
            else f"{self._name(base)} edges"
        if cuts:
            phrase += " (excluding " + _join(cuts) + ")"
        for f in sorted(set(filt)):
            phrase += f" within {self._name(f)}"
        return phrase

    def _scope(self, rule, shape, consumed):
        out = []
        for name in rule.shapes:
            if name in consumed or not rule.layer_of(name):
                continue
            # the same three readings the deck gets (KLayoutBackend._scope): apart,
            # touching, or -- any way of sharing area -- overlapping
            phrase = {"disjoint": "away from", "outside": "away from",
                      "touch": "touching"}.get(rule.relation(shape, name), "overlapping")
            out.append((phrase, self._name(rule.layer_of(name))))
        words = {"not_interacting": "away from", "interacting": "touching", "inside": "inside",
                 "outside": "outside"}
        out += [(words.get(op, "overlapping"), self._name(layer))
                for op, layer in rule.data.get("scope") or []]
        return out

    def _with_scope(self, core, scope):
        if not scope:
            return core
        from collections import defaultdict
        by = defaultdict(list)               # group same-relation markers: "away from A and B"
        for phrase, layer in scope:
            by[phrase].append(layer)
        clauses = [f"{p} {_join(sorted(by[p]))}" for p in sorted(by)]
        return core[:-1] + " — only where the geometry is " + ", ".join(clauses) + "."
