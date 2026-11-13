"""Test cases generated from the drawings themselves -- one set per CONSTRAINT.

A drawn rule already holds an example and its numbers. Every constraint of it has a
job, and each job can be shown to be done:

  * a ``check`` is what must hold: where it fails the rule must REPORT, where it holds
    (exactly at the value) it must not;
  * a ``match`` is a filter: a violation OUTSIDE the filter must NOT be reported --
    the filter really filters.

So for each constraint this backend makes small layouts from the rule's own picture,
with the quantity that constraint speaks of put just on one side of its value, just on
the other, and exactly on it -- while everything else stays as it should be -- and
writes down what the rule must do. The expected outcome is not taken from the compiled
deck: it is computed from the constraints themselves (reported = every ``match`` holds
and some ``check`` does not), evaluated on the layout actually built.

How a case is built: the drawing is taken at a scale at which everything in it is
several times larger than the rule's values; then each quantity -- an arrow's length,
the length of a side -- is given its size by squeezing or stretching the stretch of the
axis between its two ends, for the whole picture at once. The order of all edges is
kept, so what the picture says (inside, across, apart) cannot change; a case is kept
only if its shapes' relations and its compiled statement are indeed the same.

What can be stepped today: distances (arrows) and side lengths, with any mix of
``and`` / ``or``, columns, legend names and "different nets". A rule that also speaks
of areas, counts, arrays, density, corners or run lengths is listed as not generated,
with the reason; it is never reported as passing.

Output (`files`): ``<stem>.cases.rb`` -- a KLayout script writing the layout
(``klayout -b -r <stem>.cases.rb -rd out=cases.gds``) -- and ``<stem>.cases.json`` --
per case: the rule, the constraint under test, where it lies, and whether the rule
must flag it.
"""

from __future__ import annotations

import json

from .backend import Backend, register
from .constraint import parse
from .rule import Rule

STEP_NM = 5            # how far "just on the other side" is
ROOM = 4               # everything in a case is at least this many times the rule's values
_INSIDE_OPS = ("overlapping", "inside", "interacting", "not_outside", "covering")
_CMP = ("==", "!=", "<", "<=", ">", ">=")
_FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "==": "==", "!=": "!="}


class _CannotBuild(Exception):
    """This rule (or this case of it) is beyond what the generator arranges."""


def _mentions(ast, suffix):
    if isinstance(ast, dict):
        if ast.get("ref", "").endswith(suffix):
            return True
        return any(_mentions(a, suffix) for a in ast.get("args", []) or [])
    return False


def _step(q):
    """How far "just on the other side" is: one more or one fewer for a count."""
    return 1 if q[0] == "count" else STEP_NM


def _plain_leaves(ast):
    if isinstance(ast, dict) and ast.get("op") in ("and", "or", "not"):
        for a in ast["args"]:
            yield from _plain_leaves(a)
    elif isinstance(ast, dict):
        yield ast


def _expand(layer, legend, seen=()):
    """The real layers a shape of legend name `layer` must be drawn on (the positive
    part of its definition), or None if the definition cannot be drawn directly."""
    if "|" in layer:                                   # "any of these": the first will do
        layer = layer.split("|")[0]
    if layer not in legend or layer in seen:
        return [layer]

    def walk(ast):
        if "ref" in ast:
            return _expand(ast["ref"], legend, (*seen, layer))
        op = ast.get("op")
        if op == "and":
            out = []
            for a in ast["args"]:
                if isinstance(a, dict) and a.get("op") == "not":
                    continue                           # "and not x": simply do not draw x
                got = walk(a)
                if got is None:
                    return None
                out += got
            return out
        if op == "or":
            return walk(ast["args"][0])
        if op in ("+", "-") and "num" in ast["args"][1]:   # "nwell + 0.43": draw nwell, that much smaller
            inner = walk(ast["args"][0])
            grow = ast["args"][1]["num"] * (1 if op == "+" else -1)
            if inner is None or any(isinstance(x, tuple) for x in inner):
                return None
            return [(lay, grow) for lay in inner]
        return None
    return walk(legend[layer])


class _Picture:
    """A rule's drawing in nanometres, whose arrows and sides can be set to a size."""

    def __init__(self, rule, rule_dict, unit_nm, far_end="squeeze"):
        self.rule = rule
        self.far_end = far_end                         # "squeeze" the axis, or move one end: "to" / "from"
        self.data = json.loads(json.dumps(rule_dict))
        nm = lambda v: round(v * unit_nm)              # noqa: E731
        for s in self.data["shapes"]:
            for e in s["edges"]:
                e["start"], e["end"] = [nm(e["start"][0]), nm(e["start"][1])], [nm(e["end"][0]), nm(e["end"][1])]
        for m in self.data.get("measures", []):        # arrow positions are in drawing units
            if "at" in m:
                m["at"] = nm(m["at"])
        self.where = {e["id"]: (s, i) for s in self.data["shapes"] for i, e in enumerate(s["edges"])}
        self.measures = {m["name"]: m for m in self.data.get("measures", [])}
        self.sign = {}                                 # quantity -> the side its far edge started on
        self.copies = {}                               # shape -> how many of it are drawn (default: one)

    # -- edges
    def _edge(self, eid):
        s, i = self.where[eid]
        return s["edges"][i]

    @staticmethod
    def _axis(e):
        """0 for a vertical edge (x constant), 1 for a horizontal one, None otherwise."""
        return 0 if e["start"][0] == e["end"][0] else 1 if e["start"][1] == e["end"][1] else None

    def _move(self, eid, axis, coord):
        s, i = self.where[eid]
        edges = s["edges"]
        e = edges[i]
        e["start"][axis] = e["end"][axis] = coord
        edges[i - 1]["end"] = list(e["start"])         # the neighbours follow: the outline stays closed
        edges[(i + 1) % len(edges)]["start"] = list(e["end"])

    # -- quantities: ("arrow", measure name) or ("side", edge id)
    def quantity(self, ref):
        if ref in self.measures:
            m = self.measures[ref]
            if m["from"] not in self.where or m["to"] not in self.where:
                return None
            a, b = self._axis(self._edge(m["from"])), self._axis(self._edge(m["to"]))
            return ("arrow", ref) if a is not None and a == b else None
        if ref.endswith(".run") and ref[:-4] in self.measures and self.quantity(ref[:-4]):
            return ("run", ref[:-4])                   # how long the two edges run side by side
        if ref.endswith(".length") and ref[:-7] in self.where:
            eid = ref[:-7]
            s, i = self.where[eid]
            e, nxt = s["edges"][i], s["edges"][(i + 1) % len(s["edges"])]
            ax = self._axis(e)
            return ("side", eid) if ax is not None and self._axis(nxt) == 1 - ax else None
        return None

    def rectangle(self, shape):
        """The four edges of `shape` if it is drawn as an axis-aligned rectangle."""
        s = next((x for x in self.data["shapes"] if x["name"] == shape), None)
        if not s or len(s["edges"]) != 4 or any(self._axis(e) is None for e in s["edges"]):
            return None
        return s["edges"]

    def area(self, shape):
        e = self.rectangle(shape)
        return None if e is None else self.value(("side", e[0]["id"])) * self.value(("side", e[1]["id"]))

    def _run(self, name):
        """(axis, lo, hi) of the stretch along which an arrow's two edges face each other."""
        m = self.measures[name]
        ea, eb = self._edge(m["from"]), self._edge(m["to"])
        ax = 1 - self._axis(ea)
        a, b = sorted((ea["start"][ax], ea["end"][ax])), sorted((eb["start"][ax], eb["end"][ax]))
        return ax, max(a[0], b[0]), min(a[1], b[1])

    def value(self, q):
        kind, name = q
        if kind == "count":
            return self.copies.get(name, 1)
        if kind == "run":
            _, lo, hi = self._run(name)
            return max(0, hi - lo)
        if kind == "arrow":
            m = self.measures[name]
            ax = self._axis(self._edge(m["from"]))
            return abs(self._edge(m["to"])["start"][ax] - self._edge(m["from"])["start"][ax])
        e = self._edge(name)                           # "side", or "aside": a side standing for an area
        ax = 1 - self._axis(e)
        return abs(e["end"][ax] - e["start"][ax])

    def set(self, q, target):
        """Give an arrow or a side the length `target` by squeezing (or stretching) the
        stretch of the axis between its two ends -- for the WHOLE picture: whatever
        lies beyond moves along, whatever lies in between is squeezed in proportion.
        The order of all edges along the axis is kept, so nothing that the picture
        says (inside, across, apart) can change. False if the squeeze would make two
        different edges coincide."""
        if q[0] == "count":                            # so many copies, side by side in its place
            self.copies[q[1]] = target
            return True
        if q[0] == "run":                              # always by squeezing the shared stretch
            ax, lo0, hi0 = self._run(q[1])
            ends = (lo0, hi0)
        elif self.far_end != "squeeze":
            return self._set_by_one_edge(q, target)
        kind, name = q
        if kind == "run":
            pass
        elif kind == "arrow":
            m = self.measures[name]
            ax = self._axis(self._edge(m["from"]))
            ends = (self._edge(m["from"])["start"][ax], self._edge(m["to"])["start"][ax])
        else:                                          # "side" / "aside"
            e = self._edge(name)
            ax = 1 - self._axis(e)
            ends = (e["start"][ax], e["end"][ax])
        lo, hi = min(ends), max(ends)
        if hi == lo:
            return False
        shift = target - (hi - lo)

        def at(c):
            if c <= lo:
                return c
            if c >= hi:
                return c + shift
            return lo + round((c - lo) * target / (hi - lo))
        before = sorted({e[k][ax] for s in self.data["shapes"] for e in s["edges"] for k in ("start", "end")})
        after = [at(c) for c in before]
        if target > 0 and any(y <= x for x, y in zip(after, after[1:])):
            return False                               # two edges would fall onto each other
        for s in self.data["shapes"]:
            for e in s["edges"]:
                e["start"][ax], e["end"][ax] = at(e["start"][ax]), at(e["end"][ax])
        for m in self.data.get("measures", []):        # an arrow's position along its edges
            if "at" in m and m["from"] in self.where and self._axis(self._edge(m["from"])) == 1 - ax:
                m["at"] = at(m["at"])
        return True

    def _set_by_one_edge(self, q, target):
        """The other way to give a quantity its size: move ONE edge (an end of the
        arrow, or the edge that ends the side). It leaves the rest of the picture
        alone -- needed when two shapes are drawn in line and only one is to change --
        but can run an edge into a neighbour; the caller checks the result."""
        kind, name = q
        if kind == "arrow":
            m = self.measures[name]
            fixed, moving = (m["from"], m["to"]) if self.far_end == "to" else (m["to"], m["from"])
            ax = self._axis(self._edge(fixed))
            base, far = self._edge(fixed)["start"][ax], self._edge(moving)["start"][ax]
            sign = self.sign.setdefault(q, 1 if far >= base else -1)
            self._move(moving, ax, base + sign * target)
        else:
            e = self._edge(name)
            ax = 1 - self._axis(e)
            sign = self.sign.setdefault(q, 1 if e["end"][ax] >= e["start"][ax] else -1)
            s, i = self.where[name]
            self._move(s["edges"][(i + 1) % len(s["edges"])]["id"], ax, e["start"][ax] + sign * target)
        return True

    def sides_like(self, eid):
        """An edge-length constraint is about EVERY edge of the shape's layer ("every
        contact edge must be 0.2 long"), unless the rule tells the long side from the
        short one. So: the sides it speaks of -- all of the shape's, or just this one."""
        if eid in self.rule.side_roles():
            return [("side", eid)]
        s, _ = self.where[eid]
        return [q for q in (self.quantity(e["id"] + ".length") for e in s["edges"]) if q]

    def outlines(self):
        """``[(shape, [(x, y), ...]), ...]`` -- a shape drawn several times (a count)
        appears as that many smaller copies side by side where it was drawn; none at
        all if the count is zero."""
        out = []
        for s in self.data["shapes"]:
            pts = [tuple(e["start"]) for e in s["edges"]]
            n = self.copies.get(s["name"], 1)
            if n == 1:
                out.append((s["name"], pts))
                continue
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            w = (x1 - x0) // (2 * n - 1) if n else 0   # copies and the gaps between them, equal
            for i in range(n):
                a = x0 + 2 * i * w
                out.append((s["name"], [(a, y0), (a + w, y0), (a + w, y1), (a, y1)]))
        return out


@register
class CasesBackend(Backend):
    name = "cases"
    extension = ".cases.rb"
    about = "test layouts generated from the drawings, with what each rule must report"

    def __init__(self, layermap=None):
        self.layermap = {k: tuple(v) for k, v in (layermap or {}).items()}

    # ---- one rule -> its cases -------------------------------------------------
    def cases_of(self, rule_dict):
        """``(cases, reason)``: the cases for one rule, or ``([], why not)``."""
        from .backend import get
        r = Rule(rule_dict)
        try:
            for special in (self._presence_cases, self._corner_cases, self._hole_cases, self._array_cases, self._share_cases,
                            self._density_cases, self._antenna_cases):
                made = special(r, rule_dict)
                if made is not None:
                    return made, None
            self._needs_more_than_its_picture(r)
            self._marks_it_cannot_build(r, rule_dict)
            constraints = [(c.get("kind", "check"), c["expr"], parse(c["expr"])) for c in rule_dict.get("constraints", [])]
            if rule_dict.get("touching") == "allowed":     # every minimum distance: "... or exactly 0"
                constraints = [(k, e, self._or_zero(r, a) if k == "check" else a) for k, e, a in constraints]
            numbers = [abs(n) for _, _, ast in constraints for n in self._numbers(ast) if n]
            unit_nm = ROOM * round(max(numbers) * 1000) if numbers else 1000
            self._presets = []                             # sizes fixed before anything is stepped
            self._optional = set()                         # alternatives of an "or" beyond its first
            probe = _Picture(r, rule_dict, unit_nm)
            leaves = []                                    # (constraint index, quantity, op, value nm, other quantity)
            for ci, (kind, expr, ast) in enumerate(constraints):
                leaves += [(ci, *leaf) for leaf in self._leaves(r, probe, ast, expr)]
            stepped = [l for l in leaves if l[1] is not None]
            if not stepped:
                raise _CannotBuild("it has no value to step around" if not numbers else
                                   "none of its conditions is a distance, a side length or an area")
            if min([l[3] for l in stepped if l[4] is None and l[3] > 0 and l[1][0] != "count"]
                   or [2]) <= 1:
                raise _CannotBuild("a value is too small to step around")
            presets, optional = list(self._presets), set(self._optional)
            # "space to a WIDE one": the rule is about a wide part on EITHER side. The
            # neighbour drawn beside it must be made narrow too, or the filter still holds.
            self._either_wide = {}
            for shape in r.wide_filters():
                arrow = next((n for n, m in r.measures.items() if r.shape_of_edge(m["from"]) == shape
                              == r.shape_of_edge(m["to"])), None)
                if arrow is None or probe.quantity(arrow) is None:
                    continue
                ax = probe._axis(probe._edge(r.measures[arrow]["from"]))        # the arrow runs along 1 - ax
                for a, c, var in r.checked_pairs():
                    other = c if a == shape else a if c == shape else None
                    if other and other != shape and r.layer_of(other) == r.layer_of(shape) and probe.rectangle(other):
                        side = next((("side", e["id"]) for e in probe.rectangle(other) if probe._axis(e) == 1 - ax), None)
                        if side:
                            self._either_wide.setdefault(arrow, []).append(side)
            either_wide = dict(self._either_wide)
        except _CannotBuild as ex:
            return [], str(ex)
        reference, relations = get("klayout").compile(r), r.relations()
        checks = [ci for ci, (kind, _, _) in enumerate(constraints) if kind == "check"]

        def build(forced, testing, what):
            # squeezing the axis keeps the whole picture's order; moving one edge leaves
            # the rest alone. Whichever arranges the case without changing its meaning.
            for how in ("squeeze", "to", "from"):
                case = build_with(forced, testing, what, how)
                if case is not None:
                    return case
            return None

        def build_with(forced, testing, what, far_end):
            pic = _Picture(r, rule_dict, unit_nm, far_end)
            for q, size in presets:
                if pic.value(q) != size and not pic.set(q, size):
                    return None
            pinned = {key[1] for key in forced} | {q for q, _ in presets}   # sizes set on purpose
            for _ in range(3):                             # moves can disturb one another: settle
                for ci, q, op, v, other in stepped:
                    key = (ci, q, op, v, other)
                    if key not in forced and key[1:] in optional:
                        continue                           # "... or this": not needed for the rule to hold
                    if key not in forced and other is None and q in pinned:
                        continue                           # this case sets that quantity on purpose
                    if key in forced:
                        target = forced[key](pic)
                    elif other is not None and q in pinned and other not in pinned:
                        # "this side equals that side", and this side is being set on
                        # purpose: let the other one follow
                        q, other = other, q
                        target = self._satisfied(pic, q, _FLIP[op], v, other)
                    else:
                        target = self._satisfied(pic, q, op, v, other)
                        if q[0] == "side" and other is None:       # ... and so for its other sides
                            for sib in pic.sides_like(q[1]):
                                if sib != q and sib not in pinned:
                                    t = self._satisfied(pic, sib, op, v, None)
                                    if t is not None and t >= 0 and not pic.set(sib, t):
                                        return None
                    if target is not None and target != pic.value(q):
                        if target < 0 or not pic.set(q, target):
                            return None
                for key, want in forced.items():           # a width filter stepped outside: its
                    if key[1][0] == "arrow" and key[1][1] in either_wide and key[0] not in checks:
                        for side in either_wide[key[1][1]]:        # neighbour follows it
                            if pic.value(side) != want(pic) and not pic.set(side, want(pic)):
                                return None
            for key, want in forced.items():               # each forced size must have been reached
                if pic.value(key[1]) != want(pic):
                    return None
            now = Rule(pic.data)
            if now.relations() != relations or get("klayout").compile(now) != reference:
                return None                                # the moves changed what the picture says
            truth = [self._holds(pic, ast) for _, _, ast in constraints]
            filters = all(t for (kind, _, _), t in zip(constraints, truth) if kind != "check")
            failed = any(not truth[ci] for ci in checks) if checks else True
            layers = self._real_layers(r, pic.outlines())
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": testing, "case": what, "expect": bool(filters and failed),
                    "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}

        try:
            base = build({}, "the picture as drawn, every condition met", "all conditions met")
            if base is None:
                return [], "the picture cannot be brought to its values without changing what it says"
            cases = [base]
            first_check = next((l for l in stepped if l[0] in checks), None)
            for leaf in sorted(stepped, key=lambda l: l[0] not in checks):     # the checks first
                ci, q, op, v, other = leaf
                kind, expr, _ = constraints[ci]
                name = (q[1] if q[0] == "arrow" else f"{q[1]}.length" if q[0] == "side"
                        else f"the number of {q[1]}" if q[0] == "count"
                        else f"the run of {q[1]}" if q[0] == "run" else "the area")
                tag = f"{kind} {expr}"
                outside = self._outside(q, op, v, other)
                if kind == "check" or not checks:
                    cases.append(build({leaf: outside}, tag, f"{name} just outside"))
                else:                                      # a filter shows only when something is violated
                    cases.append(build({leaf: outside, first_check: self._outside(*first_check[1:])}, tag,
                                       f"{name} just outside the filter, and a check violated"))
                    cases.append(build({first_check: self._outside(*first_check[1:])}, tag,
                                       f"{name} inside the filter, and a check violated"))
                if q[0] == "count" and op in (">=", ">") and other is None:     # ... and none at all
                    none = {leaf: (lambda pic: 0)}
                    if kind != "check" and checks:
                        none[first_check] = self._outside(*first_check[1:])
                    cases.append(build(none, tag, f"no {q[1]} at all"))
                if other is None and q[0] != "count" and op in (">=", ">", "<=", "<") and v > 4 * STEP_NM:
                    # ... and well away from the value, on either side
                    wide = {leaf: (lambda pic, v=v, op=op: v // 2 if op in (">=", ">") else v + v // 2)}
                    safe = {leaf: (lambda pic, v=v, op=op: v + v // 2 if op in (">=", ">") else v // 2)}
                    if kind != "check" and checks:
                        wide[first_check] = safe[first_check] = self._outside(*first_check[1:])
                    cases.append(build(wide, tag, f"{name} far outside"))
                    cases.append(build(safe, tag, f"{name} well inside"))
                if op == "==" and other is not None:                 # two sides equal: also shorter, and far off
                    for what, size in (("just shorter", lambda pic, o=other: pic.value(o) - STEP_NM),
                                       ("far from equal", lambda pic, o=other: pic.value(o) * 2)):
                        extra = {leaf: size}
                        if kind != "check" and checks:
                            extra[first_check] = self._outside(*first_check[1:])
                        cases.append(build(extra, tag, f"{name} {what}"))
                if op == "==" and other is None and v > _step(q):     # "exactly": too small is wrong as well
                    below = {leaf: (lambda pic, v=v, q=q: v - _step(q))}
                    if kind != "check" and checks:
                        below[first_check] = self._outside(*first_check[1:])
                    cases.append(build(below, tag, f"{name} just below"))
                if op in ("==", "<=", ">=") and other is None:
                    extra = {leaf: (lambda pic, v=v: v)}
                    at = f"{name} exactly {v / 1000:g}" if q[0] in ("arrow", "side") else f"{name} exactly at the value"
                    if kind != "check" and checks:
                        extra[first_check] = self._outside(*first_check[1:])
                    cases.append(build(extra, tag, at if len([l for l in stepped if l[1] == q]) > 1
                                       else f"{name} exactly at the value"))
            for ci in checks:                              # "either is enough": all of them outside
                mine = [l for l in stepped if l[0] == ci]
                if len(mine) > 1:
                    cases.append(build({l: self._outside(*l[1:]) for l in mine}, f"check {constraints[ci][1]}",
                                       "every part of it just outside"))
        except _CannotBuild as ex:
            return [], str(ex)
        seen, out = set(), []
        for c in cases:
            if c is None:
                continue
            key = json.dumps(c["shapes"], sort_keys=True)
            if key not in seen:
                seen.add(key)
                out.append(c)
        tested = {c["testing"] for c in out}
        if len(tested) < 2:                                # only the picture as drawn: nothing was stepped
            return [], "moving the measured edges changes what the picture says"
        return out, None

    # -- what a constraint is made of
    @staticmethod
    def _or_zero(r, ast):
        """`ast` with every minimum on an arrow between two different shapes made
        "... or exactly 0" (what "touching": "allowed" says for the whole process)."""
        if isinstance(ast, dict) and ast.get("op") == "and":
            return dict(ast, args=[CasesBackend._or_zero(r, a) for a in ast["args"]])
        if isinstance(ast, dict) and ast.get("op") in (">=", ">"):
            ref = ast["args"][0].get("ref") if isinstance(ast["args"][0], dict) else None
            m = r.measures.get(ref)
            if m and "num" in ast["args"][1] and r.shape_of_edge(m["from"]) != r.shape_of_edge(m["to"]):
                return {"op": "or", "args": [ast, {"op": "==", "args": [{"ref": ref}, {"num": 0}]}]}
        return ast

    @staticmethod
    def _numbers(ast):
        if isinstance(ast, dict):
            if "num" in ast:
                yield ast["num"]
            for a in ast.get("args", []) or []:
                yield from CasesBackend._numbers(a)

    def _leaves(self, r, pic, ast, expr):
        """The comparisons a constraint is built from, as ``(quantity, op, value nm,
        other quantity)``; quantity None for a fact that needs no stepping (a layer, a
        net). Raises for anything the generator cannot arrange."""
        if not isinstance(ast, dict):
            raise _CannotBuild(f"condition not built yet: {expr}")
        op = ast.get("op")
        if op == "and":
            return [leaf for a in ast["args"] for leaf in self._leaves(r, pic, a, expr)]
        if op == "or":                                     # one alternative holding is enough:
            out = []                                       # the others are left as drawn
            for i, a in enumerate(ast["args"]):
                got = self._leaves(r, pic, a, expr)
                if i:
                    self._optional |= {leaf for leaf in got if leaf[0] is not None}
                out += got
            return out
        if op == "in":
            return [(None, "fact", 0, None)]
        if op in _CMP:
            a, b = ast["args"]
            ra, rb = (x.get("ref", "") if isinstance(x, dict) else "" for x in (a, b))
            if ra.endswith(".layer") or rb.endswith(".layer"):
                return [(None, "fact", 0, None)]
            if ra.endswith(".net") and rb.endswith(".net"):
                sa, sb = ra[:-4], rb[:-4]
                if sa not in r.shapes or sb not in r.shapes:
                    return [(None, "fact", 0, None)]       # about a shape drawn for another rule
                joined = r._drawn_joined(sa, sb) and r.layer_of(sa) != r.layer_of(sb)
                if op == "==" and not joined:
                    raise _CannotBuild("a same-net rule needs the two shapes tied together")
                return [(None, "fact", 0, None)]
            for node, num, o in ((a, b, op), (b, a, _FLIP[op])):
                if isinstance(node, dict) and node.get("op") == "count" and isinstance(num, dict) and "num" in num:
                    counted = node["args"][0].get("ref")
                    if counted not in r.shapes or pic.rectangle(counted) is None:
                        raise _CannotBuild(f"condition not built yet: {expr}")
                    return [(("count", counted), o, int(num["num"]), None)]
            for ref, num, o in ((ra, b, op), (rb, a, _FLIP[op])):
                if ref.endswith(".area") and isinstance(num, dict) and "num" in num:
                    # a rectangle's area: fix one side, and the other side carries the value
                    edges = pic.rectangle(ref[:-5])
                    if edges is None or ref.endswith(".holes.area"):
                        raise _CannotBuild(f"condition not built yet: {expr}")
                    total = round(num["num"] * 1e6)            # nm2
                    width = next((d for d in range(int(total ** 0.5), 0, -1) if total % d == 0), None)
                    if not width or total // width > 50 * width:
                        raise _CannotBuild(f"an area that does not split into whole nanometres: {expr}")
                    self._presets.append((("side", edges[0]["id"]), width))
                    return [(("aside", edges[1]["id"]), o, total // width, None)]
            if "num" in b and pic.quantity(ra):
                return [(pic.quantity(ra), op, round(b["num"] * 1000), None)]
            if "num" in a and pic.quantity(rb):
                return [(pic.quantity(rb), _FLIP[op], round(a["num"] * 1000), None)]
            if pic.quantity(ra) and pic.quantity(rb):
                return [(pic.quantity(ra), op, 0, pic.quantity(rb))]
        raise _CannotBuild(f"condition not built yet: {expr}")

    @staticmethod
    def _satisfied(pic, q, op, v, other):
        """The size that makes a comparison hold (None: it already does)."""
        cur, step = pic.value(q), _step(q)
        if other is not None:
            v = pic.value(other)
        if op == ">=":
            return None if cur >= v else v
        if op == ">":
            return None if cur > v else v + step
        if op == "<=":
            return None if cur <= v else v
        if op == "<":
            return None if cur < v else v - step
        if op == "==":
            return None if cur == v else v
        return None if cur != v else v + step              # !=

    @staticmethod
    def _outside(q, op, v, other):
        """A function giving the size that makes a comparison just fail."""
        def size(pic):
            limit, step = (pic.value(other) if other is not None else v), _step(q)
            if q[0] != "count" and other is None and 1 < limit <= 2 * step:
                step = limit // 2                          # a value of a few nanometres
            return {">=": limit - step, ">": limit, "<=": limit + step, "<": limit,
                    "==": limit + step, "!=": limit}[op]
        return size

    def _holds(self, pic, ast):
        """Is a constraint true of the picture as it now stands?"""
        op = ast.get("op")
        if op == "and":
            return all(self._holds(pic, a) for a in ast["args"])
        if op == "or":
            return any(self._holds(pic, a) for a in ast["args"])
        if op == "not":
            return not self._holds(pic, ast["args"][0])
        if op in _CMP:
            cmp = lambda a, b, op=op: {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b,   # noqa: E731
                                       ">": a > b, ">=": a >= b}[op]
            x, y = ast["args"]
            qx, qy = (pic.quantity(t.get("ref", "")) if "ref" in t else None for t in (x, y))
            alts = getattr(self, "_either_wide", {}).get(x.get("ref", "")) if "num" in y else None
            if alts:                                       # "a wide one": this line, or its neighbour
                limit = round(y["num"] * 1000)
                return cmp(pic.value(qx), limit) or any(cmp(pic.value(q), limit) for q in alts)
            if "num" in y and qx and qx[0] == "side":       # a side against a number: every such side
                return all(cmp(pic.value(q), round(y["num"] * 1000)) for q in pic.sides_like(qx[1]))
            if "num" in x and qy and qy[0] == "side":
                return all(cmp(round(x["num"] * 1000), pic.value(q)) for q in pic.sides_like(qy[1]))

            for t, u, flip in ((x, y, False), (y, x, True)):   # an area against a number, in nm2
                if t.get("ref", "").endswith(".area") and "num" in u and pic.area(t["ref"][:-5]) is not None:
                    a, b = pic.area(t["ref"][:-5]), round(u["num"] * 1e6)
                    return cmp(b, a) if flip else cmp(a, b)

            for t, u, flip in ((x, y, False), (y, x, True)):   # a count against a number
                if t.get("op") == "count" and "num" in u:
                    a, b = pic.copies.get(t["args"][0].get("ref"), 1), int(u["num"])
                    return cmp(b, a) if flip else cmp(a, b)

            def val(t, q):
                return round(t["num"] * 1000) if "num" in t else pic.value(q) if q else None
            a, b = val(x, qx), val(y, qy)
            if a is None or b is None:
                return True                                # a fact about layers or nets
            return cmp(a, b)
        return True

    def _presence_cases(self, r, rule_dict):
        """A rule that is a CROSS -- "this must not exist" on a shape, "these must not
        relate like this" on an arrow -- has no value to step. The picture itself is
        the violation: as drawn it must be reported; with the crossed-out shape (or the
        arrow's other shape) taken away it must not. None if the rule is not of this
        kind; raises if it is but carries more than the generator arranges."""
        # a crossed-out shape with a filter on its own area ("the small ones") is an
        # area rule: the cross only says the filter's pick is the violation
        by_area = Rule._filtered_shapes(rule_dict)
        shapes_x = [n for n, s in r.shapes.items() if s.get("crossed") and n not in by_area]
        arrows_x = [n for n, m in r.measures.items() if m.get("crossed") and not m.get("of_shape")]
        inside = r.inside_checks()
        if not shapes_x and not arrows_x and not inside:
            return None
        if inside and (shapes_x or arrows_x or any(self._numbers(a) and list(self._numbers(a)) for _, a in r.constraints)):
            return None                                # mixed with values: the general path decides
        for kind, ast in r.constraints:
            for leaf in _plain_leaves(ast):
                refs = [x.get("ref", "") for x in leaf.get("args", []) if isinstance(x, dict)]
                sides = len(refs) == 2 and all(x.endswith(".length") for x in refs)   # "a square": as drawn
                if leaf.get("op") != "in" and not sides and not any(x.endswith((".layer", ".net")) for x in refs):
                    raise _CannotBuild("a cross together with further conditions is not built yet")
        for name, s in r.shapes.items():
            if s.get("hole_loops") or s.get("halo"):
                raise _CannotBuild(f"shape {name} carries a mark this generator does not build yet")
        pic = _Picture(r, rule_dict, 1000)
        outlines = pic.outlines()
        markers = [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]

        def case(what, expect, without=()):
            layers = self._real_layers(r, [(n, p) for n, p in outlines if n not in without])
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": "the cross: this must not exist", "case": what,
                    "expect": expect, "shapes": layers, "markers": markers}
        if inside:                                     # "K in M": must lie inside
            out = []
            for inner, outer in inside:
                tag = f"check {inner} in {outer}"
                c = case("the picture as drawn", False)
                c["testing"] = tag
                d = case(f"the same without {outer}", True, without=[outer])
                d["testing"] = tag
                out += [c, d]
            return out
        allowed = [n for n, s in r.shapes.items() if s.get("flagged") is False]
        out = [case("the picture as drawn", True, without=allowed)]
        for n in shapes_x:
            out.append(case(f"the same without {n}", False, without=[n, *allowed]))
        for a in arrows_x:
            m = r.measures[a]
            sf, st = r.shape_of_edge(m["from"]), r.shape_of_edge(m["to"])
            # taking the other shape away ends a relation that needs it to be there
            if sf != st and r.relation(sf, st) in ("overlap", "inside", "contains", "touch", "equal"):
                out.append(case(f"the same without {st}", False, without=[st, *allowed]))
        if allowed:                                    # the ticked example: allowed, on its own
            out.append(case("only what is marked as allowed", False,
                            without=[n for n in r.shapes if n in shapes_x]))
        return out

    @staticmethod
    def _needs_more_than_its_picture(r):
        if r.density_bounds():
            raise _CannotBuild("a density rule this generator does not arrange yet")
        if r.antenna_bounds():
            raise _CannotBuild("an antenna rule this generator does not arrange yet")

    def _corner_cases(self, r, rule_dict):
        """"No corner of this angle here" (no 90-degree turn of poly on diffusion): the
        picture shows such a corner, so as drawn it must be reported; with the shape it
        lies in taken away the corner is no longer "here" and must not be."""
        corners = [(e, angle) for e, angle, others in r.corner_bounds() if not others]
        if not corners:
            return None
        if len(corners) != 1 or any(list(self._numbers(a)) != [corners[0][1]] and list(self._numbers(a))
                                    for _, a in r.constraints):
            raise _CannotBuild("a corner rule with further conditions is not built yet")
        edge, angle = corners[0]
        shape = r.shape_of_edge(edge)
        if abs(abs(r.corner_angle(edge) or 0) - angle) > 1e-6:      # an inner or an outer corner
            raise _CannotBuild("the corner drawn is not the angle the rule speaks of")
        pic = _Picture(r, rule_dict, 1000)
        outlines = pic.outlines()
        check = next(c["expr"] for c in rule_dict["constraints"] if ".corner" in c["expr"])
        around = [n for n in r.shapes if n != shape and r.relation(shape, n) in ("inside", "overlap")]

        def case(what, expect, without=()):
            layers = self._real_layers(r, [(n, p) for n, p in outlines if n not in without])
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": f"check {check}", "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        out = [case("the corner as drawn", True)]
        if around:
            out.append(case(f"the same without {', '.join(around)}", False, without=around))
        return out

    def _hole_cases(self, r, rule_dict):
        """"Every enclosed hole must be at least so large": a ring of the layer -- four
        bars around a rectangular hole -- with the hole just too small (reported),
        exactly the value, well above it and with no hole at all (quiet)."""
        holes = [(sh, op, v) for sh, op, v, on_holes in r.area_bounds() if on_holes]
        if not holes:
            return None
        if len(holes) != 1 or len([1 for k, a in r.constraints if _mentions(a, ".area")]) != 1:
            raise _CannotBuild("a hole rule with further conditions is not built yet")
        shape, op, value = holes[0]
        total = round(value * 1e6)                         # nm2
        w = next((d for d in range(int(total ** 0.5), 0, -1) if total % d == 0), None)
        if not w or total // w > 50 * w:
            raise _CannotBuild("an area that does not split into whole nanometres")
        h, bar = total // w, 4 * max(w, total // w)
        check = next(c["expr"] for c in rule_dict["constraints"] if ".holes.area" in c["expr"])
        is_min = op in (">", ">=")

        def ring(hole_h):
            if not hole_h:                                 # a plate with no hole in it
                return [(shape, 0, 0, w + 2 * bar, h + 2 * bar)]
            return [(shape, 0, 0, w + 2 * bar, bar), (shape, 0, bar + hole_h, w + 2 * bar, 2 * bar + hole_h),
                    (shape, 0, bar, bar, bar + hole_h), (shape, bar + w, bar, w + 2 * bar, bar + hole_h)]
        sign = -1 if is_min else 1
        tag = f"check {check}"
        return [self._case(r, rule_dict, tag, "the hole just outside the value", True, ring(h + sign * STEP_NM)),
                self._case(r, rule_dict, tag, "the hole exactly at the value", False, ring(h)),
                self._case(r, rule_dict, tag, "the hole far outside the value", True, ring(h // 2 if is_min else h * 2)),
                self._case(r, rule_dict, tag, "the hole well inside the value", False, ring(h * 2 if is_min else h // 2)),
                self._case(r, rule_dict, tag, "no hole at all", False, ring(0))]

    def _density_cases(self, r, rule_dict):
        """A density is taken over a whole area, so every case is a LAYOUT OF ITS OWN
        (a cell the deck is run on separately): the area, and a strip of the material
        whose width sets the coverage exactly -- just outside each limit, at it, far
        from it, and well inside."""
        dens = r.density_bounds()
        if not dens:
            return None
        if len(dens) != 1 or len(r.constraints) and any(
                k == "check" and not _mentions(a, ".density") for k, a in r.constraints):
            raise _CannotBuild("a density rule with further conditions is not built yet")
        shape, lo, hi, window = dens[0]
        if not window or not window["layer"]:
            raise _CannotBuild("a density over the layout's own extent cannot be tested: draw the area "
                               "(a shape on a boundary layer) around the material")
        side = round(window["size"] * 1000) if window["size"] else 100_000      # one window, or a 100 um area
        check = next(c["expr"] for c in rule_dict["constraints"] if ".density" in c["expr"])
        out = []

        def case(what, expect, strip):
            c = self._case(r, rule_dict, f"check {check}", what, expect,
                           [(window["shape"], 0, 0, side, side)] + ([(shape, 0, 0, strip, side)] if strip else []))
            c["alone"] = True
            out.append(c)
        for limit, is_min in ((lo, True), (hi, False)):
            if limit is None:
                continue
            at = round(limit * side)
            if at <= STEP_NM or abs(at / side - limit) > 1e-12:
                raise _CannotBuild("a density that does not come out in whole nanometres")
            sign = -1 if is_min else 1
            other = hi if is_min else lo                   # stay on the right side of the other limit
            far = at // 2 if is_min else (at + side) // 2
            inside = round(((lo if lo is not None else limit / 2) + (hi if hi is not None else (1 + limit) / 2)) / 2 * side)
            case(f"coverage just {'below the minimum' if is_min else 'above the maximum'}", True, at + sign * STEP_NM)
            case(f"coverage exactly at the {'minimum' if is_min else 'maximum'}", False, at)
            case(f"coverage far {'below the minimum' if is_min else 'above the maximum'}", True, far)
            if other is None or (is_min and inside < round(other * side)) or (not is_min and inside > round(other * side)):
                case("coverage well inside", False, inside)
        return out

    def _antenna_cases(self, r, rule_dict):
        """An antenna ratio needs the gate, the collector(s) and what CONNECTS them. The
        connection is built from the process's own connections: the shortest chain of
        layers from the gate to the first collector, and on from each collector to the
        next, each layer drawn so that it meets the one before.

        The collected area is put just above the ratio (reported), exactly at it and
        far below (quiet), and far above (reported); with several collectors the
        earlier ones hold half of the allowance and the last one is stepped. A
        condition on something else on the net (a diode of at least / less than so
        much) is tested as the filter it is: the same too-large area with the diode
        just on the other side of its value must NOT be reported."""
        ants = r.antenna_bounds()
        if not ants:
            return None
        if len(ants) != 1 or len(ants[0][3]) > 1:
            raise _CannotBuild("an antenna rule with several conditions on the net is not built yet")
        collectors, gate, ratio, filters = ants[0]
        legend, pairs = dict(r.layer_defs), r.connections()
        g_layer = r.layer_of(gate)
        collectors = sorted(collectors, key=lambda c: len(r.connections_up_to(g_layer, r.layer_of(c))))
        chains, prev = [], g_layer
        for c in collectors:
            chain = self._chain(pairs, prev, r.layer_of(c), legend)
            if chain is None:
                raise _CannotBuild("no chain of connections leads from the gate to the collector")
            chains.append(chain)
            prev = r.layer_of(c)
        side, height = 1000, 10_000                        # the gate: 1 um x 1 um; collectors 10 um tall
        allowed = ratio * side * side                      # nm2 that may be collected
        parts = len(collectors)
        fixed = allowed / (2 * (parts - 1)) / height if parts > 1 else 0     # length of each earlier collector
        last = (allowed / 2 if parts > 1 else allowed) / height
        if abs(fixed - round(fixed)) > 1e-9 or abs(last - round(last)) > 1e-9:
            raise _CannotBuild("a ratio that does not come out in whole nanometres")
        fixed, last = round(fixed), round(last)
        check = next(c["expr"] for c in rule_dict["constraints"]
                     if c.get("kind", "check") == "check" and f"{collectors[-1]}.area" in c["expr"])
        diode = None
        if filters:
            d_shape, d_op, d_value = filters[0]
            d_layer = r.layer_of(d_shape)
            link = next((b if a == d_layer else a for a, b in pairs if d_layer in (a, b)), None)
            on = r.layer_of(collectors[0])
            total = round(d_value * 1e6)
            w = next((d for d in range(int(total ** 0.5), 0, -1) if total % d == 0), None)
            if link is None or not any({link, on} == {a, b} for a, b in pairs) or not w or total // w > 4 * w:
                raise _CannotBuild("the diode of this rule cannot be attached automatically")
            d_expr = next(c["expr"] for c in rule_dict["constraints"] if f"{d_shape}.area" in c["expr"] and c.get("kind") == "match")
            diode = (d_layer, link, d_op, w, total // w, d_expr)

        def case(testing, what, expect, length, diode_h=None):
            boxes, box = [(lay, 0, 0, side, side) for lay in (_expand(g_layer, legend) or [])], (0, 0, side, side)
            real = _expand(g_layer, legend)
            first = None
            for i, chain in enumerate(chains):
                got = self._stack(chain, legend, box, real, height, length if i == parts - 1 else fixed)
                if got is None:
                    raise _CannotBuild("the connecting stack of this process cannot be laid out automatically")
                more, box, real = got
                boxes += more
                first = first or box
            if diode and diode_h:                          # under the first collector, tied to it
                d_layer, link, _, w, _, _ = diode
                x, y = first[0] + 3 * side, first[1] + 5 * side
                if x + w + side > first[2]:
                    raise _CannotBuild("the first collector is too short to hold the diode")
                for lay in (_expand(d_layer, legend) or []):
                    boxes.append((lay, x, y, x + w, y + diode_h))
                for lay in (_expand(link, legend) or []):
                    boxes.append((lay, x + w // 4, y + diode_h // 4, x + w - w // 4, y + diode_h - diode_h // 4))
            layers = [{"layer": lay, "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]} for lay, x0, y0, x1, y1 in boxes]
            return {"rule": r.name, "testing": testing, "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        tag = f"check {check}"
        if not diode:
            return [case(tag, "the collected area just above the ratio", True, last + STEP_NM),
                    case(tag, "the collected area exactly at the ratio", False, last),
                    case(tag, "the collected area far above the ratio", True, last * 2),
                    case(tag, "the collected area far below the ratio", False, max(side, last // 2))]
        _, _, d_op, w, h, d_expr = diode
        # diode heights that meet / miss the condition (None: no diode at all)
        if d_op in ("<", "<="):
            meets = [("no diode", None), ("a diode just below its value", h - STEP_NM)]
            misses = [("a diode exactly at its value", h)] if d_op == "<" else [("a diode just above its value", h + STEP_NM)]
        else:
            meets = [("a diode exactly at its value", h)] if d_op == ">=" else [("a diode just above its value", h + STEP_NM)]
            meets.append(("a diode well above its value", h * 2))
            misses = [("a diode just below its value", h - STEP_NM), ("no diode", None)]
        out = [case(tag, f"{meets[0][0]}, the collected area just above the ratio", True, last + STEP_NM, meets[0][1]),
               case(tag, f"{meets[0][0]}, the collected area exactly at the ratio", False, last, meets[0][1]),
               case(tag, f"{meets[0][0]}, the collected area far below the ratio", False, max(side, last // 2), meets[0][1])]
        for what, dh in meets[1:]:
            out.append(case(f"match {d_expr}", f"{what}, the collected area just above the ratio", True, last + STEP_NM, dh))
        for what, dh in misses:
            out.append(case(f"match {d_expr}", f"{what}, the collected area just above the ratio", False, last + STEP_NM, dh))
        return out

    @staticmethod
    def _chain(pairs, start, goal, legend):
        """The shortest chain of connected layers from `start` to `goal` in which no
        layer is reached straight from the gate unless it shares a real layer with it
        (a contact is put on the poly beside the gate, not on the gate itself)."""
        def real(layer):
            return {x for x in (_expand(layer, legend) or []) if isinstance(x, str)}
        best = None
        todo = [[start]]
        while todo:
            path = todo.pop(0)
            if path[-1] == goal:
                best = path
                break
            for a, b in pairs:
                if path[-1] in (a, b):
                    nxt = b if path[-1] == a else a
                    if nxt in path:
                        continue
                    if len(path) == 1 and not (real(start) & real(nxt)) and any(
                            real(start) & real(x) for p in pairs for x in p if x not in (start, nxt) and start in p):
                        continue                           # leave the gate along its own layer first
                    todo.append(path + [nxt])
        return best

    @staticmethod
    def _stack(chain, legend, start_box, start_real, height, length):
        """Boxes ``(real layer, x0, y0, x1, y1)`` for a chain of connected layers going
        on from `start_box` (which is not repeated): each next layer either continues
        the previous one (they share a real layer: drawn beside it) or sits on it
        (drawn within / over it). The LAST layer is a collector: `length` x `height`.
        Returns ``(boxes, the collector's box, its real layers)``."""
        def real(layer):
            got = _expand(layer, legend)
            return None if got is None or any(not isinstance(x, str) for x in got) else got
        boxes = []
        prev_box, prev_real = start_box, start_real
        if prev_real is None or len(chain) < 2:
            return None
        for i, layer in enumerate(chain[1:], 1):
            now = real(layer)
            if now is None:
                return None
            last = i == len(chain) - 1
            x0, y0, x1, y1 = prev_box
            if set(now) & set(prev_real):                  # the same material going on: beside it
                box = (x1, y0, x1 + (length if last else 2 * (x1 - x0)), y0 + (height if last else y1 - y0))
            elif last:                                     # the collector lies over what it connects to
                box = (x0, y0, x0 + length, y0 + height)
            else:                                          # a contact / via: a small square on the previous box
                m = min(x1 - x0, y1 - y0, 2000) // 4
                box = (x0 + m, y0 + m, x0 + 3 * m, y0 + 3 * m)
            boxes += [(lay, *box) for lay in now]
            prev_box, prev_real = box, now
        return boxes, prev_box, prev_real

    def _case(self, r, rule_dict, testing, what, expect, boxes):
        """A case from plain boxes ``[(shape name, x0, y0, x1, y1)]`` in nanometres."""
        layers = self._real_layers(r, [(n, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]) for n, x0, y0, x1, y1 in boxes])
        if layers is None:
            raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
        return {"rule": r.name, "testing": testing, "case": what, "expect": expect, "shapes": layers,
                "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}

    @staticmethod
    def _holders(r, shape):
        """The shapes `shape` is drawn inside (the diffusion a contact sits on ...)."""
        return [n for n in r.shapes if n != shape and r.relation(shape, n) == "inside"]

    def _array_cases(self, r, rule_dict):
        """"In an array of R x C or more": the two neighbours of the picture stand for
        a whole array, so the cases ARE arrays -- R x C with the gaps just too small
        (reported) and exactly right (quiet), and one row or one column fewer with the
        same too-small gaps (quiet: no longer an array -- the filter filters)."""
        arrays = r.array_filters()
        if not arrays:
            return None
        members = list(arrays)
        spec = arrays[members[0]]
        if any(arrays[m]["rows"] != spec["rows"] or arrays[m]["columns"] != spec["columns"]
               or r.layer_of(m) != r.layer_of(members[0]) for m in members):
            raise _CannotBuild("an array whose members are stated differently")
        either = r.either_bounds()
        plain = [d for d in r.distance_rules() if not d[3] and d[1] is not None]
        if either:
            value, both_needed = either[0][0].value, True
            check = next(c["expr"] for c in rule_dict["constraints"] if " or " in c["expr"] and c.get("kind", "check") == "check")
        elif len(plain) == 1:
            value, both_needed = plain[0][1].value, False
            check = next(c["expr"] for c in rule_dict["constraints"]
                         if c.get("kind", "check") == "check" and plain[0][0] in c["expr"])
        else:
            raise _CannotBuild("an array rule this generator does not arrange yet")
        if r.area_bounds() or r.count_bounds() or r.held_ratio_bounds() or r.length_bounds() and not r.array_edges():
            raise _CannotBuild("an array rule with further conditions is not built yet")
        v = round(value * 1000)
        size = round(spec["size"] * 1000) if spec["size"] else v
        rows, cols = spec["rows"], spec["columns"]
        holders = self._holders(r, members[0])
        which = next((c["expr"] for c in rule_dict["constraints"] if ".rows" in c["expr"] or ".columns" in c["expr"]), "")

        def grid(n_rows, n_cols, gx, gy):
            boxes = [(members[0], i * (size + gx), j * (size + gy), i * (size + gx) + size, j * (size + gy) + size)
                     for i in range(n_cols) for j in range(n_rows)]
            w, h, pad = n_cols * (size + gx), n_rows * (size + gy), 4 * max(size, v)
            return boxes + [(hd, -pad, -pad, w + pad, h + pad) for hd in holders]
        under = v - STEP_NM
        out = [self._case(r, rule_dict, f"check {check}", f"{rows} x {cols}, gaps just too small", True,
                          grid(rows, cols, under, under)),
               self._case(r, rule_dict, f"check {check}", f"{rows} x {cols}, gaps exactly at the value", False,
                          grid(rows, cols, v, v)),
               self._case(r, rule_dict, f"match {which}", f"one row fewer ({rows - 1} x {cols}), gaps just too small", False,
                          grid(rows - 1, cols, under, under)),
               self._case(r, rule_dict, f"match {which}", f"one column fewer ({rows} x {cols - 1}), gaps just too small", False,
                          grid(rows, cols - 1, under, under)),
               self._case(r, rule_dict, f"match {which}", f"one more of each ({rows + 1} x {cols + 1}), gaps just too small", True,
                          grid(rows + 1, cols + 1, under, under))]
        for what, gx, gy in (("too small across only", under, v), ("too small upwards only", v, under)):
            out.append(self._case(r, rule_dict, f"check {check}", f"{rows} x {cols}, gaps {what}",
                                  not both_needed, grid(rows, cols, gx, gy)))
        return out

    def _share_cases(self, r, rule_dict):
        """"What it holds must take up so much of its area": a square holder with a
        strip in it whose width sets the share exactly -- just outside the value, at
        it, far from it, and nothing in it at all; and, if the holder is filtered by
        width, the same too-small share in a holder just outside the filter."""
        shares = r.held_ratio_bounds()
        if not shares:
            return None
        if len(shares) != 1 or len(shares[0][0]) != 1:
            raise _CannotBuild("an area share of several layers together is not built yet")
        (held,), holder, lo, hi = shares[0][0], shares[0][1], shares[0][2], shares[0][3]
        wide = r.wide_filters().get(holder)
        strict = r._wide_strict.get(holder, False)
        side = 100_000 if not wide else max(100_000, round(wide * 1000) * 2 // 1000 * 1000)   # nm, a round number
        check = next(c["expr"] for c in rule_dict["constraints"]
                     if c.get("kind", "check") == "check" and f"{held}.area" in c["expr"])
        out = []

        def case(testing, what, expect, share_nm, width=None):
            w = width or side
            boxes = [(holder, 0, 0, w, side)]
            if share_nm:
                boxes.append((held, 0, 0, share_nm, side) if not width else (held, 0, 0, w, share_nm))
            out.append(self._case(r, rule_dict, testing, what, expect, boxes))
        tag = f"check {check}"
        for limit, is_min in ((lo, True), (hi, False)):
            if limit is None:
                continue
            at = round(limit * side)                       # strip width giving exactly this share
            if at <= STEP_NM or abs(at / side - limit) > 1e-12:
                raise _CannotBuild("a share that does not come out in whole nanometres")
            sign = -1 if is_min else 1
            case(tag, "the share just outside", True, at + sign * STEP_NM)
            case(tag, "the share exactly at the value", False, at)
            case(tag, "the share far outside", True, at // 2 if is_min else min(side, at * 2))
            case(tag, "the share well inside", False, min(side, at * 2) if is_min else at // 2)
            if is_min:
                case(tag, "nothing in it at all", True, 0)
            if wide:                                       # the same too-small share, holder outside the filter
                expr = next((c["expr"] for c in rule_dict["constraints"] if c.get("kind") == "match"
                             and any(mn in c["expr"] for mn in r.measures)), "the width filter")
                narrow = round(wide * 1000) - (0 if strict else STEP_NM)
                share_h = round(limit * side) + sign * STEP_NM
                case(f"match {expr}", "a holder just outside the filter, its share just outside", False, share_h, width=narrow)
        return out

    @staticmethod
    def _marks_it_cannot_build(r, rule_dict):
        if rule_dict.get("or"):
            raise _CannotBuild("a rule with alternative cases")
        by_area = Rule._filtered_shapes(rule_dict)
        for name, m in r.measures.items():
            if m.get("crossed") and not m.get("of_shape"):
                raise _CannotBuild("an arrow carries a mark this generator does not build yet")
        for name, s in r.shapes.items():
            if (s.get("crossed") and name not in by_area) or s.get("flagged") is False or s.get("hole_loops") or s.get("halo"):
                raise _CannotBuild(f"shape {name} carries a mark this generator does not build yet")

    def _single_maximum(self, r, rule_dict, reference):
        """A maximum measured across a whole shape (to its far side): no single edge can
        be moved that far, but the arrow is the largest distance that matters, so the
        whole picture can be scaled until the arrow has the wanted length."""
        from .backend import get
        dist = r.distance_rules()
        if len(dist) != 1 or dist[0][3] or dist[0][1] is not None or len(r.constraints) != len(
                [1 for k, a in r.constraints if k != "check" or True]):
            return [], "moving the measured edges changes what the picture says"
        var, _, hi, _ = dist[0]
        if sum(1 for k, _ in r.constraints if k == "check") != 1:
            return [], "moving the measured edges changes what the picture says"
        m, value_nm, out = r.measures[var], round(hi.value * 1000), []
        expr = next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
        for what, target, expect in ((f"{var} just outside", value_nm + STEP_NM, True),
                                     (f"{var} exactly at the value", value_nm, False)):
            scaled = self._scaled_to(r, rule_dict, m, target)
            if scaled is None or get("klayout").compile(Rule(scaled[1])) != reference:
                return [], "moving the measured edges changes what the picture says"
            layers = self._real_layers(r, scaled[0])
            if layers is None:
                return [], "a layer of the picture is defined in a way that cannot be drawn directly"
            out.append({"rule": r.name, "testing": f"check {expr}", "case": what, "expect": expect, "shapes": layers,
                        "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]})
        return out, None

    @staticmethod
    def _scaled_to(r, rule_dict, m, target_nm):
        """The whole drawing scaled so that the arrow is `target_nm` long."""
        (a0, a1), (b0, b1) = r._edge_ends(m["from"]), r._edge_ends(m["to"])
        axis = 0 if a0[0] == a1[0] and b0[0] == b1[0] else 1 if a0[1] == a1[1] and b0[1] == b1[1] else None
        if axis is None or a0[axis] == b0[axis]:
            return None
        k = target_nm / abs(b0[axis] - a0[axis])
        nm = lambda v: round(v * k)                    # noqa: E731
        # rounding must not change the arrow: place its two edges exactly
        base, far = nm(a0[axis]), nm(a0[axis]) + (1 if b0[axis] > a0[axis] else -1) * target_nm
        moved, shapes = json.loads(json.dumps(rule_dict)), {}
        for s in moved["shapes"]:
            for e in s["edges"]:
                for key in ("start", "end"):
                    was = e[key]
                    p = [nm(was[0]), nm(was[1])]
                    if was[axis] == a0[axis]:
                        p[axis] = base
                    elif was[axis] == b0[axis]:
                        p[axis] = far
                    e[key] = p
            shapes[s["name"]] = [tuple(e["start"]) for e in s["edges"]]
        for mm in moved.get("measures", []):
            if "at" in mm:
                mm["at"] = nm(mm["at"])
        return shapes, moved

    def _real_layers(self, r, shapes):
        out = []
        for name, pts in (shapes.items() if isinstance(shapes, dict) else shapes):
            layer = r.layer_of(name)
            if not layer:
                continue
            real = _expand(layer, {k: v for k, v in r.layer_defs.items()})
            if real is None:
                return None
            for lay in real:
                if isinstance(lay, tuple):                 # a grown layer: its source, inset by the growth
                    lay, grow = lay
                    xs, ys = sorted({p[0] for p in pts}), sorted({p[1] for p in pts})
                    g = round(grow * 1000)
                    if len(pts) != 4 or len(xs) != 2 or len(ys) != 2 or xs[1] - xs[0] <= 2 * g or ys[1] - ys[0] <= 2 * g:
                        return None
                    box = [(xs[0] + g, ys[0] + g), (xs[1] - g, ys[0] + g), (xs[1] - g, ys[1] - g), (xs[0] + g, ys[1] - g)]
                    out.append({"layer": lay, "points": [list(p) for p in box]})
                else:
                    out.append({"layer": lay, "points": [list(p) for p in pts]})
        return out

    # ---- all rules -> a layout script + expectations ---------------------------
    def plan(self, rules):
        """``(cases placed in tiles, [(rule, why not generated)])``."""
        cases, skipped = [], []
        for rd in rules:
            rd = rd.data if isinstance(rd, Rule) else rd
            try:
                got, why = self.cases_of(rd)
            except Exception as ex:                    # noqa: BLE001  never let one rule stop the rest
                got, why = [], f"could not be generated ({type(ex).__name__}: {ex})"
            if why:
                skipped.append((rd.get("name") or "(unnamed)", why))
            cases += got
        x, alone = 0, 0
        for c in cases:
            if c.get("alone"):                         # a layout of its own (a density)
                alone += 1
                c["cell"] = f"ALONE_{alone}"
                xs = [p[0] for s in c["shapes"] for p in s["points"]]
                ys = [p[1] for s in c["shapes"] for p in s["points"]]
                c["tile"] = [min(xs) - 1000, min(ys) - 1000, max(xs) + 1000, max(ys) + 1000]
                continue
            c["cell"] = "CASES"
            xs = [p[0] for s in c["shapes"] for p in s["points"]]
            ys = [p[1] for s in c["shapes"] for p in s["points"]]
            pad = max(max(xs) - min(xs), max(ys) - min(ys)) // 4 + 1000
            dx, dy = x - min(xs) + pad, -min(ys) + pad
            for s in c["shapes"]:
                s["points"] = [[p[0] + dx, p[1] + dy] for p in s["points"]]
            w, h = max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad
            c["tile"] = [x, 0, x + w, h]               # nm; a marker inside it belongs to this case
            x += w + 4 * pad
        return cases, skipped

    def files(self, rules, stem):
        cases, skipped = self.plan(rules)
        used = sorted({s["layer"] for c in cases for s in c["shapes"]} | {m for c in cases for m in c["markers"]})
        missing = [lay for lay in used if lay not in self.layermap]
        numbers = dict(self.layermap)
        for i, lay in enumerate(missing):              # no number given: take free ones, and say so
            numbers[lay] = (900 + i, 0)
        rb = ["# Generated by FastDRC: test cases made from the drawings.",
              "#   klayout -b -r <this file> -rd out=cases.gds",
              "ly = RBA::Layout.new", "ly.dbu = 0.001", "cells = {}",
              "put = lambda { |cell, l, d, pts| (cells[cell] ||= ly.create_cell(cell)).shapes(ly.layer(l, d)).insert("
              "RBA::Polygon.new(pts.map { |x, y| RBA::Point.new(x, y) })) }"]
        for c in cases:
            rb.append(f"# {c['rule']} | {c['testing']} | {c['case']} -- must {'' if c['expect'] else 'not '}be reported")
            for s in c["shapes"]:
                l, d = numbers[s["layer"]]
                rb.append(f"put.call(\"{c['cell']}\", {l}, {d}, {json.dumps(s['points'])})")
            t = c["tile"]
            for lay in c["markers"]:                   # the column this case belongs to
                l, d = numbers[lay]
                rb.append(f"put.call(\"{c['cell']}\", {l}, {d}, [[{t[0]}, {t[1]}], [{t[2]}, {t[1]}], [{t[2]}, {t[3]}], [{t[0]}, {t[3]}]])")
        rb.append("ly.write($out)")
        expect = {"cases": [{k: c[k] for k in ("rule", "testing", "case", "expect", "tile", "cell")} for c in cases],
                  "not_generated": [{"rule": n, "why": w} for n, w in skipped],
                  "layers_without_a_number": missing}
        return {stem + ".cases.rb": "\n".join(rb) + "\n",
                stem + ".cases.json": json.dumps(expect, indent=1) + "\n"}

    def compile(self, rule):
        return self.files([rule], "x")["x.cases.rb"]

    def compile_all(self, rules):
        return self.files(rules, "x")["x.cases.rb"]
