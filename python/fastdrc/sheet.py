"""A SHEET: one drawn scene carrying many rules -- the way a design manual does it.

A datasheet figure is not one rule. It draws a situation once (a diffusion, two poly
bars across it, a contact) and hangs many rules on it, each a labelled dimension or
a cross. A sheet is that figure::

    {"name": "Poly2",
     "layers": {...},                       # optional legend of derived layers
     "shapes": [...], "measures": [...],    # the scene
     "constraints": [...],                  # facts about the scene (which layer is what)
     "rules": [{"name": "PL.4", "desc": "...",
                "constraints": [{"kind": "check", "expr": "E >= 0.22"}]},
               ...]}

Each rule sees only the part of the scene it is about, read from where it measures
(a small shape that merely sits inside a measured one -- a contact on a diffusion --
is not taken for a cutout: say ``"except"`` for what a rule does not apply under):

  * the shapes its measures / constraints touch;
  * any shape drawn over the spot a measure sits on (it combines with the layer there)
    -- unless it holds the whole measured shape, like the diffusion under a contact:
    such a background takes part only in a rule that names it (``"with"``);
  * for a crossed-out shape, everything it is drawn on;
  * the shapes those are LINKED to (a link runs from a shape to its context), and
    so on along the links.

Everything else in the figure belongs to other rules and is ignored. A rule can
override that with ``"shapes": [names]`` (exactly these), ``"with": [names]`` (also
these -- e.g. a marker the rule is scoped to) or ``"without": [names]``, and can put
the datasheet marks on shapes for itself: ``"cross": [names]``, ``"allowed": [names]``.

A manual gives most rules as a TABLE: one arrow, a value per column (3.3 V, 5 V ...).
A sheet does the same. ``"domains"`` names each column once by where it applies --
``{"3.3V": [["not_interacting", "dualgate"]], "5V": [["overlapping", "dualgate"]]}``
-- and a rule gives ``"values": {"3.3V": 0.22, "5V": 0.3}`` with ``?`` standing for
the value in its constraint (``"E >= ?"``). It becomes one rule per column, named
``<rule>_<column>`` and scoped to that column.

A section note such as "these rules do not apply inside OTP_MK" is ``"except":
[layers]`` on the sheet (every rule) or on one rule.

`rules_of(data)` turns a sheet into ordinary rule dicts (a plain rule comes back as
itself), so every backend compiles a sheet with no special case.
"""

from __future__ import annotations

from . import topology
from .constraint import parse
from .rule import Rule

_RULE_KEYS = ("shapes", "with", "without", "cross", "allowed", "whole", "constraints",
              "measures", "values", "columns")


# Pairs of domain tests that between them leave nothing out. A column is applied to the
# MARKER of a violation, so "outside X" and "inside X" as the two columns of a table
# would both drop a marker that lies across X's edge.
_COVERING = [{"not_interacting", "interacting"}, {"not_interacting", "overlapping"},
             {"outside", "not_outside"}, {"inside", "not_inside"},
             {"not_overlapping", "overlapping"}]


def sheet_warnings(data) -> list:
    """What a sheet's table columns leave out: two columns that split by the same layer
    with tests that do not cover every case (``outside`` / ``inside``)."""
    by_layer = {}
    for column, tests in (data.get("domains") or {}).items():
        for op, layer in tests:
            by_layer.setdefault(layer, {})[op] = column
    out = []
    for layer, ops in by_layer.items():
        if len(ops) > 1 and not any(set(ops) <= pair for pair in _COVERING):
            cols = " / ".join(f"{c} ({op})" for op, c in ops.items())
            out.append(f"columns {cols} leave out a violation that lies across the edge of "
                       f"{layer}; split with not_interacting / overlapping instead")
    return out


def is_sheet(data) -> bool:
    return isinstance(data.get("rules"), list)


def _refs(ast, out):
    if isinstance(ast, dict):
        if "ref" in ast:
            out.add(ast["ref"])
        for a in ast.get("args", []) or []:
            _refs(a, out)
        for a in ast.get("set", []) or []:
            _refs(a, out)
    return out


def _per_column(data):
    """The sheet's rules with every table rule spread into one rule per column."""
    domains = data.get("domains") or {}
    for r in data["rules"]:
        if any(not c.get("expr", "").strip() for c in r.get("constraints", [])):
            # a rule still being written: ignore its empty constraint lines
            r = dict(r, constraints=[c for c in r["constraints"] if c.get("expr", "").strip()])
        # "columns": [...] is the same without a number -- a rule stated once that holds
        # in each of those columns
        table = r.get("values") or {c: None for c in r.get("columns") or []}
        if not table:
            yield r
            continue
        for column, value in table.items():
            one = {k: v for k, v in r.items() if k not in ("values", "columns")}
            one["name"] = f"{r.get('name', '')}_{column}"
            one["constraints"] = [dict(c, expr=c["expr"].replace("?", repr(value)))
                                  for c in r.get("constraints", [])]
            one["scope"] = list(r.get("scope", [])) + [list(x) for x in domains.get(column, [])]
            yield one


def rules_of(data) -> list:
    """The rules a file holds, each as a complete rule dict."""
    if not is_sheet(data):
        return [data]
    # the scene, knowing every rule's bounds: an arrow that ANY rule gives a number to is
    # a distance for all of them, never a link another rule would follow
    everything = list(data.get("constraints", [])) + [
        dict(c, expr=c["expr"].replace("?", "1")) for r in data["rules"]
        for c in r.get("constraints", []) if c.get("expr", "").strip()]
    scene = Rule({"shapes": data.get("shapes", []), "measures": data.get("measures", []),
                  "constraints": everything})
    joined = [list(p) for p in Rule(dict(data, rules=[])).connections()]
    shapes = {s["name"]: s for s in data.get("shapes", [])}
    measures = {m["name"]: m for m in data.get("measures", [])}
    edge_owner = {e["id"]: n for n, s in shapes.items() for e in s["edges"]}
    out = []
    for r in _per_column(data):
        refs = set()
        for c in r.get("constraints", []):
            _refs(parse(c["expr"]), refs)
        used = [m for m in measures if m in refs or m in r.get("measures", [])]
        notes = []
        if "shapes" in r:
            keep = list(r["shapes"])
        else:
            seeds = set(r.get("cross", [])) | set(r.get("allowed", []))
            spots = []                               # (point, the shapes measured there)
            for name in r.get("cross", []):          # an X on a shape: "this, HERE" -- all
                spots.append((scene.interior_point(name), ()))   # that lies under it counts
            for mn in used:
                m = measures[mn]
                sf, st = edge_owner.get(m["from"]), edge_owner.get(m["to"])
                seeds |= {s for s in (sf, st) if s}
                # a DISTANCE sits at a place; a link or a cross on a relation does not
                if sf and st and scene.measure_is_straight(mn) and not m.get("crossed"):
                    a, b = scene.measure_spots(mn)
                    spots += [(a, (sf,)), (b, (st,)),
                              (((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), (sf, st))]
            for ref in refs:                         # A.area, a0.length, a0.corner ...
                head = ref.split(".")[0]
                seeds |= {s for s in (head if head in shapes else edge_owner.get(head),) if s}
            keep = set(seeds)
            # Drawn over a measured spot -> it takes part. Except a BACKGROUND: a shape that
            # holds the whole measured shape (the diffusion a contact sits on, a well).
            # Moving the arrow cannot say anything about such a layer, so it is only
            # part of a rule that names it ("with").
            def background(name, measured):
                return bool(measured) and all(
                    scene.relation(m, name) in ("inside", "equal") for m in measured)
            for name in shapes:
                if name not in keep and any(
                        topology.contains(scene.polygon(name), p) and not background(name, ms)
                        for p, ms in spots):
                    keep.add(name)
                    # a shape of the SAME layer as a measured one, lying on the arrow, is
                    # almost surely another rule's example drawn too close -- say so
                    if name not in r.get("without", []) and scene.layer_of(name) in {
                            scene.layer_of(x) for x in seeds} - {None}:
                        notes.append(f"shape {name} lies on an arrow of this rule and takes part in "
                                     f"it; if it belongs to another rule, move it or list it "
                                     f"under \"without\"")
            skip = set(r.get("without", []))
            grown = True
            while grown:                             # follow the links, from a kept shape
                grown = False                        # to the context it is linked to
                for mn, m in measures.items():
                    sf, st = edge_owner.get(m["from"]), edge_owner.get(m["to"])
                    if (sf and st and sf != st and not m.get("crossed")
                            and not scene.measure_is_straight(mn)
                            and sf in keep and st not in keep and st not in skip):
                        keep.add(st)
                        grown = True
            keep |= set(r.get("with", []))
            keep -= skip
        keep = [n for n in shapes if n in set(keep)]          # scene order
        kept_edges = {e["id"] for n in keep for e in shapes[n]["edges"]}
        rs = []
        for n in keep:
            s = dict(shapes[n])
            if n in r.get("cross", []):
                s["crossed"] = True
            if n in r.get("allowed", []):
                s["flagged"] = False
            if n in r.get("whole", []):          # its layer anywhere, not only under a cover
                s["whole"] = True
            rs.append(s)
        # its own measures, and the plain links among the shapes it keeps (a cross is a
        # rule of its own: only the rule that names it has it)
        rm = [m for mn, m in measures.items()
              if (mn in used or not (m.get("crossed") or scene.measure_is_straight(mn)))
              and m["from"] in kept_edges and m["to"] in kept_edges]
        # the scene's facts that are about the kept shapes, then the rule's own
        cons = []
        for c in data.get("constraints", []):
            heads = {x.split(".")[0] for x in _refs(parse(c["expr"]), set())}
            if heads & set(keep) or heads & kept_edges:
                cons.append(c)
        rule = {k: v for k, v in r.items() if k not in _RULE_KEYS}
        rule.update(shapes=rs, measures=rm, constraints=cons + list(r.get("constraints", [])))
        if data.get("layers"):
            rule["layers"] = data["layers"]
        if joined:                                   # listed, or drawn in the scene
            rule["connect"] = joined
        skip_under = list(data.get("except") or []) + list(r.get("except") or [])
        if skip_under:                               # the section note + the rule's own
            rule["except"] = sorted(set(skip_under))
        if notes:
            rule["warnings"] = notes
        out.append(rule)
    return out
