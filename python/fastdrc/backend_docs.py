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


# how a CROSSED relation (a forbidden configuration) reads in English
_CROSS = {"overlap": "must not overlap", "inside": "must not lie inside",
          "contains": "must not contain", "touch": "must not touch",
          "equal": "must not coincide with", "disjoint": "must interact with (touch or overlap)"}
# how a SELECTION op reads as a restricting clause
_SEL = {"inside": "lies inside", "outside": "lies outside",
        "interacting": "touches", "overlapping": "overlaps",
        "not_interacting": "is clear of", "not_inside": "does not lie inside"}


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
        return self.glossary.get(layer, layer)

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
        if len(lines) == 1:
            lines.append("  • (no checkable assertion found)")
        return "\n".join(lines) + "\n"

    # --- one English sentence per check the rule carries ---
    def _sentences(self, rule):
        out = []
        for var, (lo, hi) in self._gb(rule):
            out.append(self._dim(rule, var, lo, hi))
        for shape, op, value in rule.area_bounds():
            kind = "at least" if op == ">=" else "at most"
            out.append(f"{self._deriv_en(rule, shape)} must be {kind} {_fmt(value)} µm² in area.")
        for edge, lo, hi in rule.angle_bounds():
            sh = rule.shape_of_edge(edge)
            out.append(f"{self._deriv_en(rule, sh)} must have no edge angled between "
                       f"{_fmt(lo)}° and {_fmt(hi)}° (an acute/illegal angle).")
        for edge, angle in rule.corner_bounds():
            out.append(f"{self._deriv_en(rule, rule.shape_of_edge(edge))} must have no "
                       f"{_fmt(angle)}° corner.")
        for edge, kind, lo, hi in rule.length_bounds():
            subj = self._deriv_en(rule, rule.shape_of_edge(edge))
            if kind == "without":
                out.append(f"every {subj} edge must be exactly {_fmt(lo)} µm long "
                           f"(any other length is flagged — a min/max size check).")
            elif lo == hi:
                out.append(f"{subj} edges {_fmt(lo)} µm long are flagged.")
            else:
                rng = (f"between {_fmt(lo)} and {_fmt(hi)}" if lo and hi else
                       f"at most {_fmt(hi)}" if lo is None else f"at least {_fmt(lo)}")
                out.append(f"{subj} edges {rng} µm long are flagged.")
        for var in [v for v, mm in rule.measures.items() if mm.get("crossed")]:
            out.append(self._presence(rule, var))
        return out

    def _gb(self, rule):                    # group straight-measure bounds (min, max) per var
        from collections import defaultdict
        g = defaultdict(lambda: [None, None])
        for b in rule.bounds():
            if rule.measure_is_straight(b.var):
                g[b.var][1 if b.is_max else 0] = b
        return list(g.items())

    # --- the SAME analyzed view the KLayout backend formats, rendered in English ---
    def _deriv_en(self, rule, shape):       # single-shape derivation (area / angle / length)
        d, _, _ = rule.check_derivations(shape, shape)
        return self._fmt_deriv_en(d)

    def _dim(self, rule, var, lo, hi):
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
        ds, dt, consumed = rule.check_derivations(sf, st)   # the shared analysis
        subj, arg = self._fmt_deriv_en(ds), self._fmt_deriv_en(dt)
        scope = self._scope(rule, sf, consumed)
        if lo is not None and hi is None:               # MIN bound -> a violation check
            v = _fmt(lo.value)
            if sf == st:
                core = f"{subj} must be at least {v} µm wide"
            else:
                rel = rule.relation(sf, st)
                if rel in ("inside", "contains"):
                    outer, inner = (arg, subj) if rel == "inside" else (subj, arg)
                    core = f"{outer} must enclose {inner} by at least {v} µm"
                elif self._layer(rule, sf) == self._layer(rule, st):
                    core = f"two {subj} shapes must be at least {v} µm apart"
                else:
                    core = f"{subj} must be at least {v} µm from {arg}"
            return self._with_scope(core + f" ({mt}).", scope)
        pred = ("width" if sf == st else
                "space" if self._layer(rule, sf) == self._layer(rule, st) else "separation")
        cond = (f"{pred} ≤ {_fmt(hi.value)} µm" if lo is None
                else f"{_fmt(lo.value)} µm ≤ {pred} < {_fmt(hi.value)} µm")
        return self._with_scope(f"selects {subj} regions where {cond}.", scope)

    def _presence(self, rule, var):
        m = rule.measures[var]
        sf, st = rule.shape_of_edge(m["from"]), rule.shape_of_edge(m["to"])
        ds, dt, consumed = rule.check_derivations(sf, st)
        scope = self._scope(rule, sf, consumed)
        if sf == st:
            return self._with_scope(f"no {self._fmt_deriv_en(ds)} may exist.", scope)
        rel = rule.relation(sf, st)
        return self._with_scope(f"{self._fmt_deriv_en(ds)} {_CROSS.get(rel, 'must not relate to')} "
                                f"{self._fmt_deriv_en(dt)}.", scope)

    def _layer(self, rule, shape):
        return rule.layer_of(shape) or "?"

    def _fmt_deriv_en(self, d):
        """Render a Rule derivation {base, ands, nots, selections} in English -- the
        exact counterpart of the KLayout backend's `_fmt_deriv`, over the SAME facts."""
        from collections import defaultdict
        phrase = self._name(d["base"])
        if d["ands"]:
            phrase += " combined with " + _join(sorted(self._name(a) for a in d["ands"]))
        if d["nots"]:
            phrase += " (excluding " + _join(sorted(self._name(n) for n in d["nots"])) + ")"
        by_op = defaultdict(list)
        for op, marker in d["selections"]:
            by_op[op].append(self._fmt_deriv_en(marker))
        for op in sorted(by_op):
            ctxs = sorted(by_op[op])
            # bracket only to delimit a MULTI-context list with compound parts; a single
            # context (even a compound one) reads fine without brackets
            multi = len(ctxs) > 1 and any(" " in c for c in ctxs)
            wrap = (lambda p: f"[{p}]") if multi else (lambda p: p)
            phrase += f" that {_SEL.get(op, 'relates to')} {_join([wrap(c) for c in ctxs])}"
        return phrase

    def _gate_en(self, rule, shape):        # gate-edge subject (mirrors the backend's edge_dl)
        base = self._layer(rule, shape)
        gate, filt = [], []
        for c in rule.covering(shape):
            cl = rule.layer_of(c)
            if cl and cl != base:
                (filt if rule.relation(shape, c) == "inside" else gate).append(cl)
        cuts = sorted({self._name(rule.layer_of(c)) for c in rule.removed(shape)})
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
            phrase = {"disjoint": "away from", "overlap": "overlapping",
                      "touch": "touching"}.get(rule.relation(shape, name))
            if phrase:
                out.append((phrase, self._name(rule.layer_of(name))))
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
