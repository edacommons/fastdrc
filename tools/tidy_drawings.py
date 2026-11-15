#!/usr/bin/env python3
"""Redraw a deck's rule files neatly, without changing what any rule means.

    tools/tidy_drawings.py DECK [DECK ...]

For every rule file of the deck:
  1. the edges the rule uses (an arrow ends on them, or a constraint names them) become
     straight, as the reference asks; the others stay wavy;
  2. the distinct x and y positions of the drawing are moved, keeping their order, so
     that no step is smaller than LO grid units or larger than HI -- a contact one step
     wide grows to hold its name, a well thirty steps wide shrinks.

A drawing's size means nothing to the compiler; the order of its positions does, and that
is kept. A file is rewritten only if the KLayout deck, the rule's sentence and the
relations read off the drawing are all exactly what they were; otherwise it is tried with
the positions alone, and failing that left as it is, and named. Drawings with a slanted
edge keep their positions. Run `fastdrc selftest DECK` afterwards.
"""
import copy, glob, json, os, re, sys
from fastdrc import process
from fastdrc.backend import get
from fastdrc.rule import Rule
from fastdrc.sheet import rules_of

LO, HI = 4, 6

def reading(data, ctx):
    proc, layermap = ctx
    d, _ = process.settled_rule(copy.deepcopy(data), proc.get("_values") or {})
    d.setdefault("layers", {}); d["layers"] = {**(proc.get("legend") or {}), **d["layers"]}
    if proc.get("columns") and "domains" not in d: d["domains"] = proc["columns"]
    if proc.get("connect") and "connect" not in d: d["connect"] = proc["connect"]
    if proc.get("each") and "each" not in d: d["each"] = proc["each"]
    if proc.get("touching") and "touching" not in d: d["touching"] = proc["touching"]
    rs = rules_of(d)
    return (get("klayout", layermap=layermap).compile_all(rs), get("docs").compile_all(rs),
            [sorted(map(str, Rule(r).relations().items())) for r in rs])

def remap(values):
    vs = sorted(set(values)); out = {vs[0]: 0}
    for a, b in zip(vs, vs[1:]):
        out[b] = out[a] + min(HI, max(LO, b - a))
    return out

def interp(v, m):
    ks = sorted(m)
    if v in m: return m[v]
    if v <= ks[0]: return m[ks[0]] - (ks[0] - v)
    if v >= ks[-1]: return m[ks[-1]] + (v - ks[-1])
    for a, b in zip(ks, ks[1:]):
        if a < v < b: return round(m[a] + (v - a) * (m[b] - m[a]) / (b - a), 2)

def tidy(data, straighten=True):
    d = copy.deepcopy(data)
    used = {m.get(k) for m in d.get("measures", []) for k in ("from", "to")}
    text = " ".join(c.get("expr", "") for c in d.get("constraints", []))
    edges = [e for sh in d.get("shapes", []) for e in sh.get("edges", [])]
    for e in edges:
        if straighten and (e.get("id") in used) or straighten and re.search(rf"(?<![\w.]){re.escape(str(e.get('id')))}\.", text):
            e["edge"] = True
    straight = copy.deepcopy(d)
    if any(e["start"][0] != e["end"][0] and e["start"][1] != e["end"][1] for e in edges):
        return straight, straight                       # slanted edges: positions left alone
    mx = remap([p[0] for e in edges for p in (e["start"], e["end"])])
    my = remap([p[1] for e in edges for p in (e["start"], e["end"])])
    vert = {e.get("id"): e["start"][0] == e["end"][0] for e in edges}
    for m in d.get("measures", []):
        if m.get("at") is not None:                     # along the measured edges: y if they are vertical
            m["at"] = interp(m["at"], my if vert.get(m.get("from")) else mx)
    for e in edges:
        for k in ("start", "end"):
            e[k] = [mx[e[k][0]], my[e[k][1]]]
    return d, straight

for deck in sys.argv[1:]:
    proc, _ = process.load(deck)
    ctx = (proc, {k: tuple(v) for k, v in (proc.get("layers") or {}).items()})
    full = part = same = 0
    for f in sorted(glob.glob(os.path.join(deck, "rules", "*", "*.json"))):
        data = json.load(open(f))
        if isinstance(data.get("rules"), list) or data.get("or"):
            print("  left alone (several drawings):", os.path.basename(f)); continue
        if any(sh.get("hole_loops") for sh in data.get("shapes", [])):
            same += 1                                    # a ring: its hole is drawn against its outline
            continue
        before = reading(data, ctx)
        both, straight = tidy(data)
        moved = tidy(data, straighten=False)[0]          # the positions alone, the edges as drawn
        new = next((c for c in (both, straight, moved) if reading(c, ctx) == before), None)
        if new is None or new == data:
            same += 1
            if new is None: print("  left alone (its reading would change):", os.path.basename(f))
            continue
        full += new is both; part += new is not both
        if new is not both: print('  positions only (straight edges would change its reading):' if new is moved else '  edges only:', os.path.basename(f))
        with open(f, "w") as out:
            json.dump(new, out, indent=1); out.write("\n")
    print(f"{deck}: {full} redrawn, {part} in part, {same} unchanged")
