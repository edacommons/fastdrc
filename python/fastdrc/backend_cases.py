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
import re

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
                    # "and not x": simply do not draw x -- unless x, or what it is taken from, is a
                    # layer grown or shrunk: a zone along an edge ("within 0.43 of the well edge")
                    # is a place in the layout, not something a box on one layer can be
                    cut = walk(a["args"][0])
                    if cut is None or any(isinstance(x, tuple) for x in cut):
                        return None
                    if any(isinstance(x, tuple) for b in ast["args"] if not (isinstance(b, dict) and b.get("op") == "not")
                           for x in (walk(b) or [])):
                        return None
                    continue
                got = walk(a)
                if got is None:
                    return None
                out += got
            return out
        if op == "or":
            return walk(ast["args"][0])
        if op in ("+", "-") and "num" in ast["args"][1]:   # "nwell + 0.43": draw nwell, that much smaller
            if ast["args"][0].get("ref") in legend:        # ... but a zone round a DERIVED thing (a gate) is
                return None                                # made by the picture's own shapes, not by a box
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
        # an edge that must go on exactly so far BESIDE a shape sitting on it does so on both
        # sides: the arrow drawn on one side has a twin on the other, set with it
        self.twins = {}
        for name, m in list(self.measures.items()):
            form = rule.exact_form(name)
            if form and form["kind"] == "beside" and all(self.rectangle(n) for n in (form["pad"], form["link"])):
                opp = lambda eid: (lambda s, i: s["edges"][(i + 2) % 4]["id"])(*self.where[eid])   # noqa: E731
                self.measures[name + "~"] = {"name": name + "~", "from": opp(m["from"]), "to": opp(m["to"])}
                self.twins[name] = [name + "~"]
        for b in rule.bounds():                        # outlines that must COINCIDE do so on every side
            m = self.measures.get(b.var)
            if not (m and b.kind == "check" and b.exact and b.value == 0 and not b.is_min and b.var not in self.twins):
                continue
            sf, st = rule.shape_of_edge(m.get("from")), rule.shape_of_edge(m.get("to"))
            if sf == st or rule.relation(sf, st) not in ("inside", "contains") or not all(self.rectangle(n) for n in (sf, st)):
                continue

            def side(shape, ax, far):                  # the edge of a box on that side
                es = [e for e in self.rectangle(shape) if self._axis(e) == ax]
                return (max if far else min)(es, key=lambda e: e["start"][ax])["id"]
            for ax in (0, 1):
                for far in (False, True):
                    pair = (side(sf, ax, far), side(st, ax, far))
                    if set(pair) != {m["from"], m["to"]}:
                        twin = f"{b.var}~{ax}{int(far)}"
                        self.measures[twin] = {"name": twin, "from": pair[0], "to": pair[1]}
                        self.twins.setdefault(b.var, []).append(twin)
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
        ok = self._set(q, target)
        for twin in (self.twins.get(q[1], ()) if ok and q[0] == "arrow" else ()):
            ok = ok and self._set(("arrow", twin), target)
        return ok

    def _set(self, q, target):
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
        if eid in self.rule.side_roles() or eid in self.rule.eol_edges():
            return [("side", eid)]                     # a long / short side, a line end's cap or side
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
            # a shape that abuts another along a vertical side keeps that side: the copies are
            # stacked along it, so that every copy still touches what it is counted in
            others = [[e["start"] for e in o["edges"]] for o in self.data["shapes"] if o is not s]
            along_y = any(any(p[0] in (x0, x1) for p in op) and min(p[1] for p in op) < y1 and max(p[1] for p in op) > y0
                          and (min(p[0] for p in op) == x1 or max(p[0] for p in op) == x0) for op in others)
            if along_y:
                h = (y1 - y0) // (2 * n - 1) if n else 0
                for i in range(n):
                    b = y0 + 2 * i * h
                    out.append((s["name"], [(x0, b), (x1, b), (x1, b + h), (x0, b + h)]))
                continue
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
            made = self._unbutted_count_cases(r, rule_dict)       # (it draws the grown layer's source itself)
            if made is not None:
                return made, None
            for name in r.shapes:                          # a zone drawn beside the very shape it is grown from
                zone = r.layer_defs.get(r.layer_of(name) or "")   # ("within 1.06 of the top plate", with the plate)
                if isinstance(zone, dict) and zone.get("op") in ("+", "-") and any(
                        r.layer_of(o) == zone["args"][0].get("ref") for o in r.shapes if o != name):
                    raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            if r.on_extent():
                raise _CannotBuild("a rule about the whole die (its field): a tile cannot show it")
            for special in (self._centre_cases, self._sum_cases, self._band_cases, self._no_straddle_cases, self._faced_edge_cases, self._count_chain_cases, self._shielded_cases, self._slanted_cases, self._net_cross_cases, self._exact_cases, self._orientation_cases, self._ring_count_cases, self._grid_cases, self._acute_cases, self._total_cases, self._form_cases, self._presence_cases, self._corner_cases, self._hole_cases, self._held_array_cases, self._array_cases, self._share_cases,
                            self._density_cases, self._antenna_cases):
                made = special(r, rule_dict)
                if made is not None:
                    return made, None
            self._needs_more_than_its_picture(r)
            self._marks_it_cannot_build(r, rule_dict)
            constraints = [(c.get("kind", "check"), c["expr"], parse(c["expr"])) for c in rule_dict.get("constraints", [])]
            if rule_dict.get("touching") == "allowed":     # every minimum distance: "... or exactly 0"
                constraints = [(k, e, self._or_zero(r, a) if k == "check" else a) for k, e, a in constraints]
            numbers = [abs(n) for _, _, ast in constraints for n in self._lengths(ast) if n]
            unit_nm = ROOM * round(max(numbers) * 1000) if numbers else 1000
            # one very large value (a line "longer than 1000 um") must not blow the whole
            # picture up: the quantity it is about is stretched to it on its own
            extent = max((abs(c) for sh in rule_dict["shapes"] for e in sh["edges"] for c in e["start"]), default=1) or 1
            unit_nm = max(1000, min(unit_nm, 5_000_000 // int(extent + 1)))
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
                    elif (other is not None and op in (">", ">=", "<", "<=") and q[0] == other[0] == "side"
                          and other not in pinned and not self._satisfied(pic, q, op, v, other) is None):
                        # two sides put in order (the long side and the short one): the partner
                        # follows, half or twice as long, so the side a value is set on keeps its role
                        q, longer = other, op in ("<", "<=")
                        target = pic.value(key[1]) * 2 if longer else max(pic.value(key[1]) // 2, 1)
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
            for key, want in forced.items():               # an AREA filter stepped outside, on a shape that is
                if key[1][0] == "aside" and key[0] not in checks:
                    shape = r.shape_of_edge(key[1][1])     # also "the wide one": its neighbour must not be one
                    for arrow, sides in either_wide.items():
                        if r.shape_of_edge(r.measures[arrow]["from"]) == shape:
                            narrow = round(r.wide_filters()[shape] * 1000) // 2
                            for side in sides:
                                if not pic.set(side, narrow):
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
            layers = self._real_layers(r, pic.outlines() + self._trunks(r, pic))
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

    @classmethod
    def _lengths(cls, ast):
        """The numbers of a constraint as lengths (µm): an area (µm²) counts by its side."""
        if isinstance(ast, dict) and ast.get("op") in ("==", "!=", "<", "<=", ">", ">="):
            refs = [a.get("ref", "") for a in ast["args"] if isinstance(a, dict)]
            if any(x.endswith(".area") for x in refs):
                yield from (abs(n) ** 0.5 for n in cls._numbers(ast))
                return
        if isinstance(ast, dict) and ast.get("op") in ("and", "or", "not"):
            for a in ast["args"]:
                yield from cls._lengths(a)
            return
        yield from cls._numbers(ast)

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
        rings = {outer for inner, outer in inside if r.in_holes(inner, outer)}
        for name, s in r.shapes.items():
            if (s.get("hole_loops") and name not in rings) or s.get("halo"):
                raise _CannotBuild(f"shape {name} carries a mark this generator does not build yet")
        pic = _Picture(r, rule_dict, 1000)
        outlines, bars = [], {}
        for n, pts in pic.outlines():
            if n not in rings:
                outlines.append((n, pts))
                continue
            # a ring: four bars around the drawn void (the last one closes it)
            hole = [p for e in r.shapes[n]["hole_loops"][0] for p in (e["start"],)]
            x0, x1, y0, y1 = (min(p[0] for p in pts), max(p[0] for p in pts),
                              min(p[1] for p in pts), max(p[1] for p in pts))
            a0, a1, b0, b1 = (round(min(p[0] for p in hole) * 1000), round(max(p[0] for p in hole) * 1000),
                              round(min(p[1] for p in hole) * 1000), round(max(p[1] for p in hole) * 1000))
            box = lambda u0, v0, u1, v1: [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]   # noqa: E731
            bars[n] = [box(x0, y0, x1, b0), box(x0, b1, x1, y1), box(x0, b0, a0, b1), box(a1, b0, x1, b1)]
        ring_open = [(n, b) for n, bs in bars.items() for b in bs[:-1]]
        ring_closed = [(n, b) for n, bs in bars.items() for b in bs]
        outlines += ring_closed
        markers = [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]

        def case(what, expect, without=(), shapes=None):
            layers = self._real_layers(r, [(n, p) for n, p in (shapes or outlines) if n not in without])
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
                if outer in rings:
                    e = case("the ring left open on one side", True,
                             shapes=[x for x in outlines if x[0] not in rings] + ring_open)
                    e["testing"] = tag
                    out.append(e)
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

        def case(what, expect, without=(), shapes=None):
            layers = self._real_layers(r, [(n, p) for n, p in (shapes or outlines) if n not in without])
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": f"check {check}", "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        out = [case("the corner as drawn", True)]
        if around:
            out.append(case(f"the same without {', '.join(around)}", False, without=around))
        return out

    def _acute_cases(self, r, rule_dict):
        """"No corner sharper than so many degrees": the shape as drawn, with its sharp
        corner (reported), and the box around it (quiet)."""
        ranges = r.corner_ranges()
        if not ranges or r.corner_bounds():
            return None
        if len(ranges) != 1 or len(r.shapes) != 1 or len(rule_dict.get("constraints", [])) != 2:
            raise _CannotBuild("an angle rule with further conditions is not built yet")
        edge, lo, hi = ranges[0]
        drawn = abs(r.corner_angle(edge) or 0)
        if not (lo < drawn < hi or lo < 180 - drawn < hi):   # read as the angle between the edges, or the turn
            raise _CannotBuild("the corner drawn is not one the rule forbids")
        shape = r.shape_of_edge(edge)
        pts = _Picture(r, rule_dict, 1000).outlines()[0][1]
        xs, ys = [q[0] for q in pts], [q[1] for q in pts]
        tag = "check " + next(c["expr"] for c in rule_dict["constraints"] if ".corner" in c["expr"])

        def case(what, expect, outline):
            layers = self._real_layers(r, [(shape, outline)])
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": tag, "case": what, "expect": expect, "shapes": layers, "markers": []}
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        if lo < 90 < hi:                                     # a right angle is too sharp as well: an octagon is quiet
            c = (min(x1 - x0, y1 - y0) // 3 // 10) * 10
            return [case("the corner as drawn", True, pts),
                    case("an octagon, every corner 135 degrees", lo < 135 < hi,
                         [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)])]
        return [case("the corner as drawn", True, pts),
                case("a box, every corner a right angle", False, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])]

    def _grid_cases(self, r, rule_dict):
        """"On the grid": a box on it (quiet) and one with a corner 1 nm off it (reported)."""
        if not rule_dict.get("grid"):
            return None
        g = round(rule_dict["grid"] * 1000)
        if g < 2:
            raise _CannotBuild("a grid of 1 nm cannot be missed in the test layout")
        shape = next(iter(r.shapes))
        out = [self._case(r, rule_dict, "on the grid", "every corner on the grid", False, [(shape, 0, 0, 400 * g, 200 * g)]),
               self._case(r, rule_dict, "on the grid", "one side 1 nm off the grid", True, [(shape, 0, 0, 400 * g + 1, 200 * g)])]
        for c in out:
            c["snap"] = g
        return out

    def _form_cases(self, r, rule_dict):
        """"Must be rectangular": a plain box (quiet) and an L made of two boxes (reported)."""
        forms = [(sh, f) for sh, f in r.form_checks() if f in ("rectangle", "other")]
        if not forms:
            return None
        if len(forms) != 1 or len(r.shapes) != 1 or len(rule_dict.get("constraints", [])) != 2:
            raise _CannotBuild("a rectangle rule with further conditions is not built yet")
        shape, form = forms[0]
        tag = "check " + next(c["expr"] for c in rule_dict["constraints"] if ".corners" in c["expr"])
        box, ell = [(shape, 0, 0, 4000, 2000)], [(shape, 0, 0, 4000, 2000), (shape, 0, 0, 2000, 4000)]
        return [self._case(r, rule_dict, tag, "a rectangle", form != "rectangle", box),
                self._case(r, rule_dict, tag, "an L shape", form == "rectangle", ell)]

    def _no_straddle_cases(self, r, rule_dict):
        """"Enclose by at least 0" with the inner one not to lie across the edge: the
        picture as drawn (quiet), the outer one's measured edge moved into the inner one
        so the inner one lies across it (reported), the outer one taken away (quiet)."""
        dist = [d for d in r.distance_rules() if not d[3] and d[1] is not None and d[1].value == 0]
        if len(dist) != 1 or not r.measures[dist[0][0]].get("within"):
            return None
        var = dist[0][0]
        m = r.measures[var]
        sf, st = r.shape_of_edge(m["from"]), r.shape_of_edge(m["to"])
        outer_edge = m["to"] if r.relation(sf, st) == "inside" else m["from"]
        inner_edge = m["from"] if outer_edge == m["to"] else m["to"]
        outer = r.shape_of_edge(outer_edge)
        check = "check " + next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
        pic = _Picture(r, rule_dict, 1000)
        eo, ei = pic._edge(outer_edge), pic._edge(inner_edge)
        ax = 1 - pic._axis(eo) if pic._axis(eo) is not None else None
        if ax is None or pic._axis(ei) != pic._axis(eo):
            raise _CannotBuild("a no-straddle rule on edges this generator does not arrange yet")
        ax = pic._axis(eo)
        co, ci = eo["start"][ax], ei["start"][ax]
        drawn = pic.outlines()
        pic._move(outer_edge, ax, ci - (co - ci) // 2 if co != ci else ci + 1000)
        across = pic.outlines()

        def case(what, expect, shapes):
            layers = self._real_layers(r, shapes)
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": check, "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        return [case("wholly inside, as drawn", False, drawn),
                case("lying across the edge", True, across),
                case(f"the same without {outer}", False, [(n, q) for n, q in drawn if n != outer])]

    def _faced_edge_cases(self, r, rule_dict):
        """"Its long sides must each have the other facing them within v": the shape
        between two of the other at the distance (quiet), one of them a step too far and
        one of them gone (reported), one of them shorter than the shape (reported only
        if ALL of the edge must be faced), and a shape with no edge that long (quiet)."""
        filters = r.faced_edge_filters()
        if not filters:
            return None
        if len(filters) != 1 or len([c for c in rule_dict["constraints"] if c.get("kind", "check") == "check"]) != 1:
            raise _CannotBuild("a faced-edge rule with further conditions is not built yet")
        edge, (lo, hi) = next(iter(filters.items()))
        var = next(k for k, m in r.measures.items() if m.get("from") == edge and m.get("edges"))
        bound = next((d[2] for d in r.distance_rules() if d[0] == var and d[2] is not None and not d[3]), None)
        if lo is None or hi is not None or bound is None:
            raise _CannotBuild("a faced-edge rule this generator does not arrange yet")
        m = r.measures[var]
        a, b = r.shape_of_edge(m["from"]), r.shape_of_edge(m["to"])
        every_part = m.get("metric") == "projection"
        v, w = round(bound.value * 1000), round(lo * 1000) - 1      # (the filter's own value: not such an edge)
        long_, bw = 4 * w, 1000
        check = "check " + next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
        which = "match " + next(c["expr"] for c in rule_dict["constraints"] if ".length" in c["expr"])

        def row(left, right, right_len=None, a_len=long_):
            boxes = [(a, 0, 0, w, a_len)]
            if left is not None:
                boxes.append((b, -left - bw, 0, -left, long_))
            if right is not None:
                boxes.append((b, w + right, 0, w + right + bw, right_len or long_))
            return boxes
        return [self._case(r, rule_dict, check, "one on each side at the distance", False, row(v, v)),
                self._case(r, rule_dict, check, "one of them a step too far", True, row(v, v + STEP_NM)),
                self._case(r, rule_dict, check, "one on one side only", True, row(v, None)),
                self._case(r, rule_dict, check, "one of them only half as long as the shape", every_part, row(v, v, long_ // 2)),
                self._case(r, rule_dict, which, "a shape with no edge that long, alone", False, row(None, None, a_len=w))]

    def _unbutted_count_cases(self, r, rule_dict):
        """"A ring's hole may hold so many -- not counting those whose neighbour butts
        a tap": the counted shape is linked to a neighbour, and that to a shape whose
        layer is another layer GROWN a little (what butts it overlaps the grown one).
        Rows of neighbour | counted in a ring: the allowed number (quiet), one more
        (reported), one more with every neighbour butted (quiet), and one more with
        just one of them butted (quiet: it is not counted)."""
        counts = r.count_bounds()
        if len(counts) != 1:
            return None
        holder, counted, lo, hi = counts[0]
        if not r.in_hole(counted, holder) or hi is None or lo not in (None, 0):
            return None
        link = lambda a: [r.shape_of_edge(m["to"]) for m in r.measures.values()                 # noqa: E731
                          if not m.get("crossed") and m.get("from") and r.shape_of_edge(m["from"]) == a]
        near = link(counted)
        far = link(near[0]) if len(near) == 1 else []
        zone = r.layer_defs.get(r.layer_of(far[0])) if len(far) == 1 else None
        if not (isinstance(zone, dict) and zone.get("op") == "+" and "ref" in zone["args"][0]):
            return None
        tap = _expand(zone["args"][0]["ref"], r.layer_defs)
        if not tap or any(not isinstance(x, str) for x in tap):
            raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
        tag = "check " + next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
        w, h, pitch = 2000, 6000, 8000

        def case(what, expect, n, butted):
            boxes, raw = [], []
            for i in range(n):
                x = i * pitch
                boxes += [(near[0], x, 0, x + w, h), (counted, x + w, 0, x + w + 1000, h)]
                if i in butted:
                    raw += [{"layer": lay, "points": [[x - 600, 0], [x, 0], [x, h], [x - 600, h]]} for lay in tap]
            x1 = n * pitch
            boxes += [(holder, -4000, -4000, x1 + 2000, -3000), (holder, -4000, h + 3000, x1 + 2000, h + 4000),
                      (holder, -4000, -3000, -3000, h + 3000), (holder, x1 + 1000, -3000, x1 + 2000, h + 3000)]
            c = self._case(r, rule_dict, tag, what, expect, boxes)
            c["shapes"] += raw
            return c
        return [case(f"{hi} in the ring", False, hi, ()),
                case(f"{hi + 1} in the ring", True, hi + 1, ()),
                case(f"{hi + 1} in the ring, each with its neighbour butted", False, hi + 1, tuple(range(hi + 1))),
                case(f"{hi + 1} in the ring, one of them with its neighbour butted", False, hi + 1, (0,))]

    def _count_chain_cases(self, r, rule_dict):
        """A count whose holders are named through what is counted (a drain whose gate's
        source is shared by two gates must lie between two gates): rows of the three
        kinds of shape, side by side, in the orders that matter -- every holder between
        two of the counted (quiet), a single holder | counted | neighbour (quiet: its
        neighbour is not shared), and rows ending in a holder next to a shared neighbour
        (reported)."""
        found = [(h, k, lo, hi, r.count_chain(h, k)) for h, k, lo, hi in r.count_bounds() if r.count_chain(h, k)]
        if not found:
            return None
        if len(found) != 1 or len(found[0][4]) != 1 or (found[0][2], found[0][3]) != (2, 2) \
                or found[0][4][0][2][1:] != (2, 2):
            raise _CannotBuild("a chained count this generator does not arrange yet")
        holder, counted, _, _, [(_, far, _)] = found[0]
        tag = "check " + next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
        name = {"H": holder, "C": counted, "F": far}
        w, h = 2000, 6000

        def row(order):
            return [(name[ch], i * w, 0, (i + 1) * w, h) for i, ch in enumerate(order)]
        said = lambda o: " | ".join({"H": r.layer_of(holder), "C": r.layer_of(counted), "F": r.layer_of(far)}[ch] for ch in o)   # noqa: E731
        return [self._case(r, rule_dict, tag, said(o), bad, row(o))
                for o, bad in (("FCHCF", False), ("FCH", False), ("FCHCFCHCF", False), ("HCFCH", True), ("FCHCFCH", True))]

    def _shielded_cases(self, r, rule_dict):
        """A distance that a third shape lying between lifts: the rule is tested as a
        plain distance on the picture WITHOUT that shape, and then with it -- the two
        too close with the shape right across between them (quiet), and with the shape
        too short to stand between all of the one and all of the other (reported)."""
        found = [(v, r.measure_shield(v)) for v in r.measures if r.measure_shield(v)]
        if not found:
            return None
        if len(found) != 1:
            raise _CannotBuild("several shielded distances in one picture are not built yet")
        var, shields = found[0]
        lo = next((d[1] for d in r.distance_rules() if d[0] == var and d[1] is not None and not d[3]), None)
        if lo is None:
            raise _CannotBuild("a shielded distance that is not a minimum is not built yet")
        gone = set(shields)
        plain = json.loads(json.dumps(rule_dict))
        plain["shapes"] = [sh for sh in plain["shapes"] if sh["name"] not in gone]
        ids = {e["id"] for sh in rule_dict["shapes"] if sh["name"] in gone for e in sh["edges"]}
        plain["measures"] = [m for m in plain.get("measures", []) if m.get("from") not in ids and m.get("to") not in ids]
        plain["constraints"] = [c for c in plain["constraints"]
                                if not any(re.match(rf"\s*{re.escape(g)}\.", c["expr"]) for g in gone)]
        cases, why = self.cases_of(plain)
        if why:
            raise _CannotBuild(why)
        check = next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
        scaled = self._scaled_to(r, rule_dict, r.measures[var], round(lo.value * 1000) - STEP_NM)
        if scaled is None:
            raise _CannotBuild("the picture cannot be scaled to the value")

        def case(what, expect, shapes):
            layers = self._real_layers(r, shapes)
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": f"check {check}", "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        drawn = scaled[0]
        m = r.measures[var]
        (a0, a1) = r._edge_ends(m["from"])
        along = 1 if a0[0] == a1[0] else 0                 # the coordinate the two measured edges run along
        for end in (m["from"], m["to"]):                   # "... if it is wider than so much": made that wide, away from the arrow
            n, w = r.shape_of_edge(end), r.wide_filters().get(r.shape_of_edge(end))
            if not w:
                continue
            need = round(w * 1000) + 2000
            pts = drawn[n]
            lo_a, hi_a = min(q[along] for q in pts), max(q[along] for q in pts)
            lo_c, hi_c = min(q[1 - along] for q in pts), max(q[1 - along] for q in pts)
            other = [q[1 - along] for k in (m["from"], m["to"]) if r.shape_of_edge(k) != n for q in drawn[r.shape_of_edge(k)]]
            if other and min(other) >= hi_c:               # the other shape lies beyond its high side: grow the low side
                lo_c = min(lo_c, hi_c - need)
            else:
                hi_c = max(hi_c, lo_c + need)
            c = (lo_a + hi_a) // 2
            lo_a, hi_a = min(lo_a, c - need // 2), max(hi_a, c + need // 2)
            box = [(lo_c, lo_a), (hi_c, lo_a), (hi_c, hi_a), (lo_c, hi_a)]
            drawn[n] = box if along == 1 else [(y, x) for x, y in box]
        runs = [[q[along] for q in drawn[n]] for n in (r.shape_of_edge(m["from"]), r.shape_of_edge(m["to"]))]
        f0, f1 = max(min(x) for x in runs), min(max(x) for x in runs)      # the stretch where the two face each other
        if f1 <= f0:
            f0, f1 = min(min(x) for x in runs), max(max(x) for x in runs)
        mid, span = (f0 + f1) // 2, f1 - f0

        def short(pts):                                    # squeezed to a quarter of the facing stretch, in its middle
            lo_, hi_ = min(q[along] for q in pts), max(q[along] for q in pts)
            f = lambda c: mid + round((c - (lo_ + hi_) / 2) * (span / 4) / max(hi_ - lo_, 1))   # noqa: E731
            return [((q[0], f(q[1])) if along == 1 else (f(q[0]), q[1])) for q in pts]
        held = {k for g in gone for k in r.shapes if k != g and r.relation(k, g) == "inside"}
        return cases + [
            case("too close, with the shape right across between them", False, drawn),
            case("too close, the shape between them too short to part them", True,
                 {n: (short(q) if n in gone | held else q) for n, q in drawn.items()})]

    def _slanted_cases(self, r, rule_dict):
        """A width between edges that run at an ANGLE the rule names ("45 degree bent
        gate"): a slanted strip clearly narrower than the value (reported) and clearly
        wider (quiet), each way the filter names -- and a straight strip that narrow
        (quiet: not an edge of that direction)."""
        dist = [d for d in r.distance_rules() if not d[3] and d[1] is not None]
        if len(dist) != 1:
            return None
        var, lo = dist[0][0], dist[0][1]
        m = r.measures[var]
        shape = r.shape_of_edge(m["from"])
        angles = r.edge_angle_filter(shape)
        if not angles or shape != r.shape_of_edge(m["to"]):
            return None
        others = [n for n in r.shapes if n != shape]
        if any(a not in (45, 135) for a in angles) or len(rule_dict.get("constraints", [])) != len(r.shapes) + 2:
            raise _CannotBuild("an angle rule this generator does not arrange yet")
        check = next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
        which = next(c["expr"] for c in rule_dict["constraints"] if ".angle" in c["expr"])
        v, off = round(lo.value * 1000), 10                       # clearly on either side: 10 nm
        L = 8 * v

        def case(tag, what, expect, across, way):
            # a strip `across` wide measured along x; slanted, its width is across / sqrt(2)
            pts = {45: [(0, 0), (across, 0), (across + L, L), (L, L)],
                   135: [(L, 0), (L + across, 0), (across, L), (0, L)],
                   0: [(0, 0), (across, 0), (across, L), (0, L)]}[way]
            shapes = [(shape, pts)] + [(o, [(-L, L // 4), (3 * L, L // 4), (3 * L, 3 * L // 4), (-L, 3 * L // 4)]) for o in others]
            layers = self._real_layers(r, shapes)
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": tag, "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        narrow, wide = int((v - off) * 2 ** 0.5), int((v + off) * 2 ** 0.5) + 1
        out = []
        for a in angles:
            out += [case(f"check {check}", f"a strip at {a} degrees, clearly narrower", True, narrow, a),
                    case(f"check {check}", f"a strip at {a} degrees, clearly wider", False, wide, a)]
        out.append(case(f"match {which}", "a straight strip that narrow", False, v - off, 0))
        return out

    def _net_cross_cases(self, r, rule_dict):
        """"Two at different potentials must not be in one holder": the picture as drawn
        (two, not tied: reported); the two tied together by a strap of the layers that
        connect them (quiet); only one of them (quiet); each in a holder of its own (quiet)."""
        found = [(v, r.net_cross(v)) for v in r.measures if r.net_cross(v)]
        if not found:
            return None
        if len(found) != 1 or len(rule_dict.get("constraints", [])) != len(r.shapes) + 1:
            raise _CannotBuild("a rule on nets with further conditions is not built yet")
        (holder, a, b) = found[0][1]
        layer, pairs = r.layer_of(a), [tuple(p) for p in r.connections()]
        via = next((x for p in pairs if layer in p for x in p if x != layer
                    and any(x in q and q != p and layer not in q for q in pairs)), None)
        strap = next((y for q in pairs if via in q and layer not in q for y in q if y != via), None) if via else None
        if not strap:
            raise _CannotBuild("nothing in the process's connections can tie the two together")
        tag = "match " + next(c["expr"] for c in rule_dict["constraints"] if ".net" in c["expr"])
        A, B, H, gap = (4000, 4000, 6000, 8000), (10000, 4000, 12000, 8000), (0, 0, 16000, 12000), 4000

        def case(what, expect, boxes, tie=()):
            c = self._case(r, rule_dict, tag, what, expect, boxes)
            for lay, x0, y0, x1, y1 in tie:
                c["shapes"].append({"layer": lay, "points": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]})
            return c
        cut = lambda q: (via, q[0] + 800, q[1] + 1800, q[0] + 1200, q[1] + 2200)   # noqa: E731
        both = [(holder, *H), (a, *A), (b, *B)]
        apart = [(holder, 0, 0, 8000, 12000), (holder, 8000 + gap, 0, 16000 + gap, 12000), (a, *A),
                 (b, B[0] + gap, B[1], B[2] + gap, B[3])]
        return [case("two, not connected to each other", True, both),
                case("the two tied together", False, both, [cut(A), cut(B), (strap, A[0] + 600, A[1] + 1600, B[0] + 1400, B[1] + 2400)]),
                case("only one", False, [(holder, *H), (a, *A)]),
                case("each in one of its own", False, apart)]

    def _band_cases(self, r, rule_dict):
        """One outline a set distance outside another, within a tolerance (``E >= lo``
        and ``E <= hi`` on one arrow, the inner shape wholly inside): the outer one is put
        that far out on every side. At either end of the band: quiet. A step past it: reported."""
        dist = [d for d in r.distance_rules() if not d[3]]
        if len(dist) != 2 or dist[0][0] != dist[1][0] or len(r.shapes) != 2:
            return None
        lo = next((d[1] for d in dist if d[1] is not None), None)
        hi = next((d[2] for d in dist if d[2] is not None), None)
        if lo is None or hi is None or lo.exact or hi.exact or not lo.value < hi.value:
            return None
        var = dist[0][0]
        m = r.measures[var]
        sf, st = r.shape_of_edge(m["from"]), r.shape_of_edge(m["to"])
        if sf == st or r.relation(sf, st) not in ("inside", "contains"):
            return None
        inner, outer = (sf, st) if r.relation(sf, st) == "inside" else (st, sf)
        pic = _Picture(r, rule_dict, 1000)
        if not (pic.rectangle(inner) and pic.rectangle(outer)):
            raise _CannotBuild("a distance kept within a tolerance is tested on boxes only")
        side = max(4 * round(hi.value * 1000), 4000)
        box = lambda d: [(-d, -d), (side + d, -d), (side + d, side + d), (-d, side + d)]   # noqa: E731

        def case(tag, what, expect, d):
            layers = self._real_layers(r, [(outer, box(d)), (inner, box(0))])
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": tag, "case": what, "expect": expect, "shapes": layers, "markers": []}
        a, b = round(lo.value * 1000), round(hi.value * 1000)
        tl, th = f"check {var} >= {lo.value:g}", f"check {var} <= {hi.value:g}"
        return [case(tl, f"{var} exactly {lo.value:g} on every side", False, a), case(tl, f"{var} just under it", True, a - STEP_NM),
                case(th, f"{var} exactly {hi.value:g} on every side", False, b), case(th, f"{var} just over it", True, b + STEP_NM)]

    def _exact_cases(self, r, rule_dict):
        """One exact value that no single edge can be moved to: a shape's length FROM END
        TO END (the whole picture is scaled until the arrow has it), and two outlines that
        must COINCIDE (drawn one inside the other; the outer one is put onto the inner).
        At the value: quiet. A step off it, either way: reported."""
        checks = [c["expr"] for c in rule_dict.get("constraints", []) if c.get("kind", "check") == "check"]
        dist = [d for d in r.distance_rules() if not d[3]]
        if len(checks) != 1 or len(dist) != 1 or dist[0][2] is None or not dist[0][2].exact:
            return None
        var, hi, tag = dist[0][0], dist[0][2], "check " + checks[0]
        m = r.measures[var]
        sf, st = r.shape_of_edge(m["from"]), r.shape_of_edge(m["to"])
        form = r.exact_form(var)

        def case(what, expect, shapes):
            layers = self._real_layers(r, shapes)
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": tag, "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        if form and form["kind"] == "span":
            v, out = round(hi.value * 1000), []
            for what, target, expect in ((f"{var} exactly {hi.value:g}", v, False), (f"{var} just longer", v + STEP_NM, True),
                                         (f"{var} just shorter", v - STEP_NM, True)):
                scaled = self._scaled_to(r, rule_dict, m, target)
                if scaled is None:
                    raise _CannotBuild("the picture cannot be scaled to the value")
                out.append(case(what, expect, scaled[0]))
            return out
        if hi.value == 0 and sf != st and r.relation(sf, st) in ("inside", "contains"):
            pic = _Picture(r, rule_dict, 1000)
            inner, outer = (sf, st) if r.relation(sf, st) == "inside" else (st, sf)
            if not (pic.rectangle(inner) and pic.rectangle(outer)):
                raise _CannotBuild("outlines that must coincide are tested on boxes only")
            drawn = dict(pic.outlines())
            xs, ys = [q[0] for q in drawn[inner]], [q[1] for q in drawn[inner]]
            x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
            box = lambda d0, d1: [(x0 - d0, y0), (x1 + d1, y0), (x1 + d1, y1), (x0 - d0, y1)]   # noqa: E731
            # the inner one is "this layer where that one is NOT" (poly without implant), drawn
            # on a longer shape of the layer: the rest of that shape carries what is not there
            rest, zone = [], r.layer_defs.get(r.layer_of(inner))
            if (isinstance(zone, dict) and zone.get("op") == "and" and len(zone["args"]) == 2 and "ref" in zone["args"][0]
                    and isinstance(zone["args"][1], dict) and zone["args"][1].get("op") == "not"
                    and "ref" in zone["args"][1]["args"][0]):
                base, gone = zone["args"][0]["ref"], zone["args"][1]["args"][0]["ref"]
                for n, q in drawn.items():
                    if n != inner and r.layer_of(n) == base:
                        bx, by = [p[0] for p in q], [p[1] for p in q]
                        for a, b in ((min(bx) - 1000, x0), (x1, max(bx) + 1000)):
                            if b > a:
                                rest.append({"layer": gone, "points": [[a, min(by) - 1000], [b, min(by) - 1000],
                                                                       [b, max(by) + 1000], [a, max(by) + 1000]]})
            _case = case

            def case(what, expect, shapes):                # noqa: F811
                c = _case(what, expect, shapes)
                c["shapes"] += [dict(x) for x in rest]
                return c
            put = lambda pts: [(n, pts if n == outer else q) for n, q in drawn.items()]       # noqa: E731
            return [case("the two outlines coincide", False, put(box(0, 0))),
                    case(f"{outer} a step beyond {inner} on one side", True, put(box(STEP_NM, 0))),
                    case(f"{outer} a step short of {inner} on one side", True, put(box(0, -STEP_NM))),
                    case(f"the same without {outer}", False, [(n, q) for n, q in drawn.items() if n != outer])]
        return None

    def _orientation_cases(self, r, rule_dict):
        """"Must run along x": the picture as drawn, and the same picture turned a quarter
        round (every x and y swapped) -- the edge then runs the other way."""
        orient = r.orientation_checks()
        if not orient:
            return None
        if len(orient) != 1 or len(rule_dict.get("constraints", [])) != len(r.shapes) + 1:
            raise _CannotBuild("an orientation rule with further conditions is not built yet")
        edge, angle, must = orient[0]
        pic = _Picture(r, rule_dict, 1000)
        drawn = pic.outlines()
        turned = [(n, [(y, x) for x, y in pts]) for n, pts in drawn]
        e = pic._edge(edge)
        runs_x = e["start"][1] == e["end"][1]
        if angle not in (0, 90) or runs_x != (angle == 0):
            raise _CannotBuild("the edge is not drawn the way the rule names")
        tag = "check " + next(c["expr"] for c in rule_dict["constraints"] if ".angle" in c["expr"])

        def case(what, expect, shapes):
            layers = self._real_layers(r, shapes)
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return {"rule": r.name, "testing": tag, "case": what, "expect": expect, "shapes": layers,
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        return [case("as drawn", not must, drawn), case("turned a quarter round", must, turned)]

    @staticmethod
    def _trunks(r, pic):
        """A line END is the end of a branch: its far (wavy) end continues. Built alone, a
        short wire would have a second cap there; so the wire stands on a wide trunk of
        its own layer -- as the manual's figures draw branches on a wider line."""
        out = []
        for _, cap, _ in r.eol_measures():
            sh = r.shape_of_edge(cap)
            es = [e["id"] for e in r.shapes[sh]["edges"]]
            far, c = pic._edge(es[(es.index(cap) + 2) % 4]), pic._edge(cap)
            (ax, ay), (bx, by) = far["start"], far["end"]
            w = max(abs(bx - ax), abs(by - ay))
            if ay == by:                                   # a horizontal far end: the trunk above / below
                out_dir = 1 if ay > c["start"][1] else -1
                x0, x1 = min(ax, bx) - 3 * w, max(ax, bx) + 3 * w
                y0, y1 = sorted((ay, ay + out_dir * 3 * w))
            else:                                          # a vertical far end: left / right
                out_dir = 1 if ax > c["start"][0] else -1
                y0, y1 = min(ay, by) - 3 * w, max(ay, by) + 3 * w
                x0, x1 = sorted((ax, ax + out_dir * 3 * w))
            out.append((sh, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
        return out

    def _ring_count_cases(self, r, rule_dict):
        """"What a ring's hole holds": a ring (four bars around its drawn void) with what
        the rule counts in it. Built: every count satisfied (quiet); each count one over
        its maximum or one under its minimum (reported, unless a filter then no longer
        holds); the ring left open (reported if the rule asks that something be in it)."""
        rings = [h for h in r.shapes if r.shapes[h].get("hole_loops")]
        if len(rings) != 1:
            return None
        ring = rings[0]
        checks = [(k, lo, hi) for h, k, lo, hi in r.count_bounds() if h == ring and r.in_hole(k, ring)]
        filters = [(k, lo, hi) for h, (k, lo, hi) in r.count_filters().items() if h == ring and r.in_hole(k, ring)]
        if not checks:
            return None
        inside = [(i, o) for i, o in r.inside_checks() if o == ring and r.in_holes(i, ring)]
        for kind, ast in r.constraints:
            for leaf in _plain_leaves(ast):
                refs = [x.get("ref", "") for x in leaf.get("args", []) if isinstance(x, dict)]
                if not (any(x.endswith(".layer") for x in refs) or leaf.get("op") == "in"
                        or any(isinstance(x, dict) and x.get("op") == "count" for x in leaf.get("args", []))):
                    raise _CannotBuild("a ring rule with further conditions is not built yet")
        need = {}                                        # shape -> how many make every count hold
        for k, lo, hi in filters + checks:
            want = max(lo or 0, need.get(k, 0))
            need[k] = min(want, hi) if hi is not None else want
        pic = _Picture(r, rule_dict, 1000)
        tag = "check " + " and ".join(c["expr"] for c in rule_dict["constraints"]
                                      if c.get("kind") == "check" and "Count(" in c["expr"])

        def layout(counts, ring_open=False):
            pic.copies = dict(counts)
            out = []
            for n, pts in pic.outlines():
                if n != ring:
                    out.append((n, pts))
                    continue
                hole = [e["start"] for e in r.shapes[ring]["hole_loops"][0]]
                x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
                y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
                a0, a1 = round(min(p[0] for p in hole) * 1000), round(max(p[0] for p in hole) * 1000)
                b0, b1 = round(min(p[1] for p in hole) * 1000), round(max(p[1] for p in hole) * 1000)
                box = lambda u0, v0, u1, v1: [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]   # noqa: E731
                bars = [box(x0, y0, x1, b0), box(x0, b1, x1, y1), box(x0, b0, a0, b1), box(a1, b0, x1, b1)]
                out += [(ring, b) for b in bars[: 3 if ring_open else 4]]
            layers = self._real_layers(r, out)
            if layers is None:
                raise _CannotBuild("a layer of the picture is defined in a way that cannot be drawn directly")
            return layers

        def case(what, expect, counts, ring_open=False):
            return {"rule": r.name, "testing": tag, "case": what, "expect": expect,
                    "shapes": layout(counts, ring_open),
                    "markers": [lay for op, lay in (rule_dict.get("scope") or []) if op in _INSIDE_OPS]}
        out = [case("every count as it must be", False, need)]
        for k, lo, hi in checks:
            if hi is not None:
                out.append(case(f"one {k} more than allowed", True, dict(need, **{k: hi + 1})))
            if lo:
                short = dict(need, **{k: lo - 1})
                held = all(short.get(fk, 0) >= (flo or 0) and (fhi is None or short.get(fk, 0) <= fhi)
                           for fk, flo, fhi in filters)
                out.append(case(f"one {k} fewer than asked", held, short))
        if inside:
            out.append(case("the ring left open on one side", True, need, ring_open=True))
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
        whole = not window or not window["layer"]          # over the layout's own extent (the die)
        side = round(window["size"] * 1000) if window and window["size"] else 100_000   # one window, or a 100 um area
        check = next(c["expr"] for c in rule_dict["constraints"] if ".density" in c["expr"])
        more, less = r.density_strict(shape)
        out = []

        def case(what, expect, strip):
            if whole:                                      # the extent: two specks at opposite corners
                c = self._case(r, rule_dict, f"check {check}", what, expect,
                               [(shape, 0, 0, strip, side)] if strip else [])
                c["shapes"] += [{"layer": "_extent", "points": [[x, y], [x + 10, y], [x + 10, y + 10], [x, y + 10]]}
                                for x, y in ((0, 0), (side - 10, side - 10))]
            else:
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
            case(f"coverage exactly at the {'minimum' if is_min else 'maximum'}", more if is_min else less, at)
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
        g_layer = r.plain_layer(r.layer_of(gate))
        collectors = sorted(collectors, key=lambda c: len(r.connections_up_to(g_layer, r.layer_of(c))))
        chains, prev = [], g_layer
        for c in collectors:
            chain = self._chain(pairs, prev, r.plain_layer(r.layer_of(c)), legend)
            if chain is None:
                raise _CannotBuild("no chain of connections leads from the gate to the collector")
            # a step that is the next layer over again, less something that is not there (a via "not on
            # a capacitor plate" before the via itself): the next one stands for both
            reals = [set(x for x in (_expand(c_, legend) or []) if isinstance(x, str)) for c_ in chain]
            chain = [c_ for k, c_ in enumerate(chain)
                     if not (0 < k < len(chain) - 1 and reals[k] and reals[k] <= reals[k + 1])]
            chains.append(chain)
            prev = r.plain_layer(r.layer_of(c))
        side, height = 1000, 10_000                        # the gate: 1 um x 1 um; collectors 10 um tall
        weights, extra = r.antenna_extras(ants[0][0], gate)
        if weights and (len(collectors) != 1 or filters):
            raise _CannotBuild("a side-wall antenna rule with several collectors is not built yet")
        wall = round(weights[collectors[0]] * 1000) if weights else None      # nm: the collector's thickness
        g_h = 2 * wall if wall else side                   # (the gate as tall as makes the lengths whole nanometres)
        same = bool(set(_expand(g_layer, legend) or []) & set(_expand(r.plain_layer(r.layer_of(collectors[-1])), legend) or []))

        def length_for(helped=0):
            """how long the last collector is when it holds exactly what the ratio allows"""
            if wall:                                       # perimeter x thickness <= ratio x (gate + help)
                half = ratio * (side * g_h + helped) / wall / 2
                return half - height - (side if same else 0) if not same or height >= g_h else None
            return ratio * (side * g_h + helped) / height
        allowed = ratio * side * side                      # nm2 that may be collected
        parts = len(collectors)
        fixed = allowed / (2 * (parts - 1)) / height if parts > 1 else 0     # length of each earlier collector
        last = (allowed / 2 if parts > 1 else allowed) / height if not wall else length_for()
        if last is None or abs(fixed - round(fixed)) > 1e-9 or abs(last - round(last)) > 1e-9:
            raise _CannotBuild("a ratio that does not come out in whole nanometres")
        fixed, last = round(fixed), round(last)
        check = next(c["expr"] for c in rule_dict["constraints"]
                     if c.get("kind", "check") == "check" and (f"{collectors[-1]}.area" in c["expr"]
                                                               or f"{collectors[-1]}.perimeter" in c["expr"]))
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
            boxes, box = [(lay, 0, 0, side, g_h) for lay in (_expand(g_layer, legend) or [])], (0, 0, side, g_h)
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
            out = [case(tag, "the collected area just above the ratio", True, last + STEP_NM),
                   case(tag, "the collected area exactly at the ratio", False, last),
                   case(tag, "the collected area far above the ratio", True, last * 2),
                   case(tag, "the collected area far below the ratio", False, max(side, last // 2))]
            # "... the gate's area plus f times the diode on the net": a diode tied to the collector
            # lets it hold that much more -- where this process's stack lets one be tied to it directly
            on = r.plain_layer(r.layer_of(collectors[0]))
            for d_shape, f in extra[:1]:
                d_layer = r.layer_of(d_shape)
                link = next((y if x == d_layer else x for x, y in pairs if d_layer in (x, y)
                             and any({(y if x == d_layer else x), on} == {u, v} for u, v in pairs)), None)
                d_h = 2 * wall if wall else side
                more = length_for(f * side * d_h)
                if link is None or more is None or abs(more - round(more)) > 1e-9:
                    continue
                diode = (d_layer, link, None, side, d_h, None)
                more = round(more)
                out += [case(tag, "with a diode on the net, as much as the ratio allowed without it", False, last + STEP_NM, d_h),
                        case(tag, "with a diode on the net, exactly what it allows now", False, more, d_h),
                        case(tag, "with a diode on the net, just above what it allows now", True, more + STEP_NM, d_h)]
            return out
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
        boxes, small = [], None
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
            elif small:                                    # further up the stack: on the same small square
                box = small
            else:                                          # a contact / via: a small square on the previous box
                m = min(x1 - x0, y1 - y0, 2000) // 4
                box = small = (x0 + m, y0 + m, x0 + 3 * m, y0 + 3 * m)
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

    def _held_array_cases(self, r, rule_dict):
        """"Must hold an array of R x C": the holder with such an array (quiet), with one
        row or one column fewer, with a single one, and with the array pulled apart
        (reported) -- and with an array AND a single one elsewhere on it (quiet). With
        nothing on it at all it is quiet if the rule is about holders that hold one."""
        arrays, counts = r.array_filters(), r.count_bounds()
        if not arrays or len(counts) != 1 or counts[0][1] not in arrays or counts[0][2] != 1 or counts[0][3] is not None:
            return None
        holder, member = counts[0][0], counts[0][1]
        spec = arrays[member]
        if spec.get("gap") is None or r.distance_rules() and any(not d[3] for d in r.distance_rules()):
            raise _CannotBuild("an array rule this generator does not arrange yet")
        gap = round(spec["gap"] * 1000)
        size = round(spec["size"] * 1000) if spec["size"] else gap
        rows, cols = spec["rows"], spec["columns"]
        only_with_one = holder in r.count_filters()
        tag = "check " + next(c["expr"] for c in rule_dict["constraints"]
                              if c.get("kind", "check") == "check" and "Count(" in c["expr"])
        room = (max(rows, cols) + 6) * (size + gap)

        def grid(n_rows, n_cols, g, lone=False):
            boxes = [(member, i * (size + g), j * (size + g), i * (size + g) + size, j * (size + g) + size)
                     for i in range(n_cols) for j in range(n_rows)]
            if lone:
                boxes.append((member, room - 2 * size, 0, room - size, size))
            return boxes + [(holder, -size, -size, room, room)]
        near = gap - STEP_NM
        return [self._case(r, rule_dict, tag, f"an array of {rows} x {cols}", False, grid(rows, cols, near)),
                self._case(r, rule_dict, tag, f"an array of {rows} x {cols} and a single one elsewhere", False, grid(rows, cols, near, True)),
                self._case(r, rule_dict, tag, "a single one", True, grid(1, 1, near)),
                self._case(r, rule_dict, tag, f"one row fewer ({rows - 1} x {cols})", True, grid(rows - 1, cols, near)),
                self._case(r, rule_dict, tag, f"one column fewer ({rows} x {cols - 1})", True, grid(rows, cols - 1, near)),
                self._case(r, rule_dict, tag, f"{rows} x {cols}, too far apart to be an array", True, grid(rows, cols, gap)),
                self._case(r, rule_dict, tag, "nothing on it at all", not only_with_one, grid(0, 0, near))]

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

    def _centre_cases(self, r, rule_dict):
        """"From the CENTRE of a shape": two squares with their centres just closer than
        the value, at it and far apart -- side by side and on a slant (3 : 4 : 5, where
        the distance comes out whole); or a square whose centre is just too near an
        edge of the other shape, at the value from it, and far from it."""
        found = r.centre_bounds()
        if not found:
            return None
        if len(found) != 1 or len(rule_dict.get("constraints", [])) != len(r.shapes) + 1:
            raise _CannotBuild("a distance from a centre with further conditions is not built yet")
        var, shape, other, edge, lo, per = found[0]
        v = round(lo * 1000)
        side = max(v // 4 // (2 * STEP_NM) * 2 * STEP_NM, 2 * STEP_NM)      # an even side: the centre on the grid
        tag = f"check {var} >= {lo:g}"
        sq = lambda n, cx, cy: (n, cx - side // 2, cy - side // 2, cx + side // 2, cy + side // 2)   # noqa: E731
        out = []

        def case(what, expect, boxes):
            out.append(self._case(r, rule_dict, tag, what, expect, boxes))
        if per:
            # the limit grows with a size: two of one size, then a small one beside a large
            # one (the larger counts), each just closer than its own limit and exactly at it
            f, _, sized = per
            tag = "check " + next(c["expr"] for c in rule_dict["constraints"] if c.get("kind", "check") == "check")
            small = max(v, 20_000) // (20 * STEP_NM) * 20 * STEP_NM or 20 * STEP_NM
            large = 2 * small
            lim = {s: v + f * s for s in (small, large)}
            if any(abs(x - round(x)) > 1e-6 or round(x) % STEP_NM or round(x) <= s for s, x in lim.items()):
                raise _CannotBuild("a limit that does not come out in whole steps")
            lim = {s: round(x) for s, x in lim.items()}

            def one(n, cx, s):                               # a square of that size; or one a holder of that size holds
                if sized in (shape, other):
                    return [(n, cx - s // 2, -s // 2, cx + s // 2, s // 2)]
                return [(sized, cx - s // 2, -s // 2, cx + s // 2, s // 2), (n, cx - s // 4, -s // 4, cx + s // 4, s // 4)]
            for (s1, s2), what in (((small, small), "two of one size"), ((small, large), "a small one beside a large one")):
                need = lim[max(s1, s2)]
                case(f"{what}, the centres just closer than their limit", True, one(shape, 0, s1) + one(other, need - STEP_NM, s2))
                case(f"{what}, the centres exactly at their limit", False, one(shape, 0, s1) + one(other, need, s2))
            case("a small one beside a large one, as far as two small ones need", True,
                 one(shape, 0, small) + one(other, lim[small], large))
            return out
        if edge is None:
            case("side by side, the centres just closer than the value", True, [sq(shape, 0, 0), sq(other, v - STEP_NM, 0)])
            case("side by side, the centres exactly the value apart", False, [sq(shape, 0, 0), sq(other, v, 0)])
            case("one above the other, the centres just closer", True, [sq(shape, 0, 0), sq(other, 0, v - STEP_NM)])
            if v % (5 * STEP_NM) == 0:
                dx, dy = 3 * v // 5, 4 * v // 5
                case("on a slant, the centres exactly the value apart", False, [sq(shape, 0, 0), sq(other, dx, dy)])
                case("on a slant, the centres just closer", True, [sq(shape, 0, 0), sq(other, dx, dy - STEP_NM)])
            case("far apart", False, [sq(shape, 0, 0), sq(other, 3 * v, 0)])
            return out
        size = 4 * v
        if r.holes(other):                                   # a ring: four bars; the square in its hole
            t_ = v // 4
            ring = [(other, 0, 0, size, t_), (other, 0, size - t_, size, size), (other, 0, t_, t_, size - t_),
                    (other, size - t_, t_, size, size - t_)]
            outline = any(e.get("id") == edge for e in r.shapes[other]["edges"])
            off = 0 if outline else t_                       # measured from the outline, or from the edge of the hole
        elif r.relation(shape, other) == "inside":
            ring, off = [(other, 0, 0, size, size)], 0
        else:                                                # beside it
            block = (other, -size, 0, 0, size)
            case("its centre just too near", True, [block, sq(shape, v - STEP_NM, size // 2)])
            case("its centre exactly the value away", False, [block, sq(shape, v, size // 2)])
            case("far away", False, [block, sq(shape, 3 * v, size // 2)])
            return out
        case("its centre just too near one edge", True, ring + [sq(shape, off + v - STEP_NM, size // 2)])
        case("its centre exactly the value from that edge", False, ring + [sq(shape, off + v, size // 2)])
        case("its centre just too near another edge", True, ring + [sq(shape, size // 2, size - off - v + STEP_NM)])
        case("in the middle", False, ring + [sq(shape, size // 2, size // 2)])
        return out

    def _sum_cases(self, r, rule_dict):
        """"One side of all it holds, added up": a holder with two bars in it whose long
        (or short) sides together are just outside the value, at it and well inside it;
        and one bar alone that has the whole value."""
        found = r.sum_bounds()
        if not found:
            return None
        holder, shape, edge, lo, hi = found[0]
        plain = [c for c in rule_dict.get("constraints", []) if "um(" not in c.get("expr", "") and ".layer" not in c.get("expr", "")]
        if len(found) != 1 or len(plain) != 1:               # (the one that says which side is the long one)
            raise _CannotBuild("a sum of sides with further conditions is not built yet")
        long = r.side_roles()[edge] == "long"
        check = next(c["expr"] for c in rule_dict["constraints"] if "um(" in c["expr"])
        out = []
        for limit, is_min in ((lo, True), (hi, False)):
            if limit is None:
                continue
            total = round(limit * 1000)
            half = total // 2 // STEP_NM * STEP_NM
            if half * 2 != total or half <= 8 * STEP_NM:
                raise _CannotBuild("a sum that does not split into two whole steps")
            sign = -1 if is_min else 1

            def boxes(sides):
                # the summed side runs upwards; the other one is far shorter (long) or far longer (short)
                far = max(sides) * 3 if not long else max(max(sides) // 20 // STEP_NM * STEP_NM, 2 * STEP_NM)
                bars, x = [], far
                for s in sides:
                    w, h = (far, s) if long else (far, s)
                    bars.append((shape, x, far, x + w, far + h) if long else (shape, far, x, far + h, x + w))
                    x += 2 * far
                reach = far + max(sides) + far
                return [(holder, 0, 0, x, reach) if long else (holder, 0, 0, reach, x)] + bars
            tag = f"check {check}"
            out += [self._case(r, rule_dict, tag, "two of them, together just outside the value", True, boxes([half, half + sign * STEP_NM])),
                    self._case(r, rule_dict, tag, "two of them, together exactly the value", False, boxes([half, half])),
                    self._case(r, rule_dict, tag, "two of them, together well inside the value", False,
                               boxes([half, half * 2] if is_min else [half // 2, half // 2])),
                    self._case(r, rule_dict, tag, "one alone, exactly the value", False, boxes([total])),
                    self._case(r, rule_dict, tag, "one alone, just outside the value", True, boxes([total + sign * STEP_NM]))]
        return out

    def _total_cases(self, r, rule_dict):
        """"What it holds must be so much area in all": a holder with TWO pieces in it
        whose areas together are just outside the value, at it and well inside it."""
        totals = r.held_area_bounds()
        if not totals:
            return None
        if len(totals) != 1 or len(rule_dict.get("constraints", [])) != len(r.shapes) + 1:
            raise _CannotBuild("a total area with further conditions is not built yet")
        holder, held, lo, hi = totals[0]
        check = next(c["expr"] for c in rule_dict["constraints"] if "rea(" in c["expr"])
        out = []
        for limit, is_min in ((lo, True), (hi, False)):
            if limit is None:
                continue
            total = round(limit * 1e6)                     # nm2, as two strips of one height
            h = next((d for d in range(int((total // 2) ** 0.5), 0, -1) if total % (2 * d) == 0), None)
            if not h or h <= 4 * STEP_NM:
                raise _CannotBuild("an area that does not split into whole nanometres")
            w = total // (2 * h)
            sign = -1 if is_min else 1

            def boxes(w2):
                return [(holder, 0, 0, 2 * w + w2 + 4 * h, 3 * h), (held, h, h, h + w, 2 * h),
                        (held, 2 * h + w, h, 2 * h + w + w2, 2 * h)]
            tag = f"check {check}"
            out += [self._case(r, rule_dict, tag, "two pieces, together just outside the value", True, boxes(w + sign * STEP_NM)),
                    self._case(r, rule_dict, tag, "two pieces, together exactly the value", False, boxes(w)),
                    self._case(r, rule_dict, tag, "two pieces, together well inside the value", False,
                               boxes(w * 2 if is_min else w // 2))]
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
            if any(abs(v) > 20_000_000 for c in got for sh in c["shapes"] for pt in sh["points"] for v in pt):
                got, why = [], "its test layout would be larger than 20 mm"
            if why:
                skipped.append((rd.get("name") or "(unnamed)", why))
            cases += got
        # tiles stand apart by more than the farthest distance a rule of the set looks
        # (a 20 um spacing between dummy-exclude marks reaches into the next tile otherwise)
        reach = 0
        for rd in rules:
            r = rd if isinstance(rd, Rule) else Rule(rd.data if isinstance(rd, Rule) else rd)
            for b in r.bounds():
                if r.measures.get(b.var) and isinstance(b.value, (int, float)):
                    reach = max(reach, min(float(b.value), 50.0))
        reach_nm = round(reach * 1000)
        x, alone = 0, 0
        row_y, row_h, row_gap = 0, 0, 0                # tiles in rows: one row of everything runs out of coordinates
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
            pad = (max(max(xs) - min(xs), max(ys) - min(ys)) // 4 + 1000 + reach_nm // 2) // 10 * 10
            if x > self.ROW_NM:                        # the next row, clear of this one by more than any rule reaches
                row_y = -(-(row_y + row_h + row_gap) // 10) * 10
                x, row_h, row_gap = 0, 0, 0
            dx, dy = x - min(xs) + pad, row_y - min(ys) + pad
            if c.get("snap"):                          # a case about the grid stays on it
                dx, dy = dx - dx % c["snap"], dy - dy % c["snap"]
            for s in c["shapes"]:
                s["points"] = [[p[0] + dx, p[1] + dy] for p in s["points"]]
            w, h = max(xs) - min(xs) + 2 * pad, max(ys) - min(ys) + 2 * pad
            w, h = -(-w // 10) * 10, -(-h // 10) * 10     # tiles (and the markers drawn over them) on a 10 nm grid
            c["tile"] = [x, row_y, x + w, row_y + h]   # nm; a marker inside it belongs to this case
            row_h, row_gap = max(row_h, h), max(row_gap, 4 * pad)
            x += w + 4 * pad
            x = -(-x // 10) * 10
        return cases, skipped

    ROW_NM = 200_000_000    # 200 mm: how long a row of tiles may grow

    def files(self, rules, stem):
        cases, skipped = self.plan(rules)
        used = sorted({s["layer"] for c in cases for s in c["shapes"]} | {m for c in cases for m in c["markers"]})
        missing = [lay for lay in used if lay not in self.layermap and not lay.startswith("_")]
        numbers = dict(self.layermap)
        for i, lay in enumerate(missing + [x for x in used if x.startswith("_") and x not in self.layermap]):
            numbers[lay] = (900 + i, 0)                # no number given: take free ones (and say so, but
                                                       # for the test layout's own helpers, "_extent")
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
