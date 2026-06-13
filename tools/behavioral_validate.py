#!/usr/bin/env python3
"""Per-rule BEHAVIORAL validation against the GF180 deck.

For each parsed deck rule: generate a layout that VIOLATES it (a base shape sized
to fail, plus covering / hole / scope geometry positioned so the rule fires),
then run BOTH the official op (from the deck) and OUR generated op through the
KLayout binary and confirm they flag the same number of violations.

Unlike op-text diffing (tools/validate_klayout.py), this is form-tolerant: a
rule whose op text differs from ours (e.g. X.inside(Y) vs X.and(Y)) still passes
if the violations match. Slow (2 KLayout runs per rule); optional first arg
limits how many rules to check.

Run:  python tools/behavioral_validate.py [N]
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(__file__))

from validate_klayout import parse_layermap, parse_rules, as_drawn, _OP, our_line  # noqa: E402
from behavioral_diff import gen_layout, KLAYOUT                           # noqa: E402
from fastdrc.backend import get                                          # noqa: E402
from fastdrc.rule import Rule                                            # noqa: E402

DECK = os.path.join(os.path.dirname(__file__), "..", "docs", "gf180mcu", "gf180mcu_full.drc")


def run_exprs(exprs, gds, lm):
    """exprs: [(id, full_check_expression)]. Returns {id: violation_count}."""
    rep = gds + ".lyrdb"
    body = [f'source("{gds}")', f'report("d", "{rep}")']
    body += [f"{n} = polygons({l}, {d})" for n, (l, d) in lm.items()]
    # define pure-AND derived vars (ncomp = comp & nplus ...) so OFFICIAL ops that
    # reference them by name run; each resolves to base layers, so order is free.
    import validate_klayout as _vk
    body += [f"{v} = {_vk._var_expansion(*ad)}" for v, ad in _vk._VARDEFS.items()]
    for i, (rid, expr) in enumerate(exprs):
        body.append(f"x{i} = {expr}")
        body.append(f'x{i}.output("{rid}", "{rid}")')
    with tempfile.NamedTemporaryFile("w", suffix=".drc", delete=False) as f:
        f.write("\n".join(body) + "\n")
        drc = f.name
    subprocess.run([KLAYOUT, "-b", "-r", drc], check=True, capture_output=True, text=True)
    os.unlink(drc)
    xml = open(rep).read()
    os.unlink(rep)
    items = re.findall(r"<item>.*?<category>'?([^<']*)'?</category>.*?</item>", xml, re.S)
    c = {}
    for it in items:
        c[it] = c.get(it, 0) + 1
    return c


def layout_for(r, lm):
    """Geometry (gds_layer, dt, x0,y0,x1,y1 in nm) that VIOLATES rule r, plus the
    covering/hole/scope geometry positioned so the rule fires on it."""
    op, base = r["op"], r["base"]
    if op == "or":          # OR-of-variants: a layout that violates the FIRST variant
        return layout_for(r["variants"][0], lm)   # fires that case -> the union fires too
    bl = lm[base]
    v = float(r["value"]) if r["value"] else 0.0
    vnm = int(round(v * 1000))
    s = []
    if op == "presence":                                # the subject region IS the
        s.append((*bl, 0, 0, 1000, 1000))               # violation; place it, contexts below
    elif op == "width":                                 # too-narrow rect
        s.append((*bl, 0, 0, max(1, vnm // 2), 3000))
    elif op == "space":                                 # too-close pair
        g = max(1, vnm // 2)
        s += [(*bl, 0, 0, 1000, 1000), (*bl, 1000 + g, 0, 2000 + g, 1000)]
    elif op == "enclosing":                             # inner too close to outer edge
        mg = max(1, vnm // 2)
        s += [(*bl, 0, 0, 5000, 5000), (*lm[r["arg"][0]], mg, mg, 4000, 4000)]
    elif op == "separation":
        g = max(1, vnm // 2)
        s += [(*bl, 0, 0, 1000, 1000), (*lm[r["arg"][0]], 1000 + g, 0, 2000 + g, 1000)]
    elif op == "with_area" and r.get("void"):           # min-VOID-area: a DONUT whose
        side = max(2, int(math.sqrt(v) * 1000 * 0.5))   # hole is smaller than threshold
        h0, h1 = 4000, 4000 + side                      # the under-area void
        s += [(*bl, 0, 0, h0, 10000), (*bl, h1, 0, 10000, 10000),   # left, right
              (*bl, h0, 0, h1, h0), (*bl, h0, h1, h1, 10000)]       # bottom, top (frame)
    elif op == "with_area":                             # area below threshold
        side = max(1, int(math.sqrt(v) * 1000 * 0.5))
        s.append((*bl, 0, 0, side, side))
    for cl in r["covers"] + r.get("arg_covers", []):    # AND-ed layer (subject or arg) -> covers
        s.append((*lm[cl], -2000, -2000, 8000, 8000))
    for hl in r["holes"] + r.get("arg_holes", []):      # NOT-ed layer -> must NOT cover
        s.append((*lm[hl], 90000, 90000, 91000, 91000))
    for so, mk in r["scope"]:                           # in-domain so the rule fires
        far = (*lm[mk], 90000, 90000, 91000, 91000)
        near = (*lm[mk], -2000, -2000, 8000, 8000)
        s.append(far if so == "not_interacting" else near)
    for sel_op, y in r.get("selections", []):           # X.inside/outside/interacting/overlapping(Y)
        box = {"inside": (-5000, -5000, 30000, 30000),    # context contains the violating X
               "outside": (100000, 100000, 101000, 101000),  # context far (disjoint)
               "interacting": (-3000, -3000, 5000, 5000),     # context overlaps X
               # overlapping / not_outside need area overlap; a containing box gives it
               "overlapping": (-5000, -5000, 30000, 30000),
               "not_outside": (-5000, -5000, 30000, 30000),
               "not_interacting": (200000, 200000, 201000, 201000)}[sel_op]  # far -> X kept
        for bl in _marker_layers(y, lm):                  # derived marker -> its base layers
            s.append((*lm[bl], *box))
        # NOTE: for separation, outside(Y) before separation(Y) is a behavioral
        # no-op -- the only subject polys it drops are those area-overlapping Y, and
        # plain separation never flags overlapping polys anyway (verified across
        # touch/overlap/inside configs). We still emit it for op-text fidelity.
    for sel_op, y in r.get("arg_selections", []):       # arg = via2.interacting(metal2) etc.
        # the context must NOT empty the arg: inside/interacting -> a big box that
        # contains/overlaps the arg; outside/not_interacting -> far (arg stays selected).
        box = {"inside": (-5000, -5000, 30000, 30000),
               "interacting": (-5000, -5000, 30000, 30000),
               "outside": (200000, 200000, 201000, 201000),
               "not_interacting": (200000, 200000, 201000, 201000)}[sel_op]
        for bl in _marker_layers(y, lm):
            s.append((*lm[bl], *box))
    return s


def _marker_layers(y, lm):
    """The gds layers for a selection marker: the layer itself, or -- for a DERIVED
    var (tgate = poly2 & comp) -- its base layers, placed at the same box so they
    overlap into the AND. Skips anything not in the layermap."""
    if y in lm:
        return [y]
    import validate_klayout as _vk
    return [l for l in _vk._VARDEFS.get(y, (frozenset(),))[0] if l in lm]


def main():
    text = open(DECK, encoding="utf-8").read()
    lm = parse_layermap(text)
    rules, _ = parse_rules(text, lm)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(rules)
    rules = rules[:limit]

    matched = fired = 0
    fails = []
    for r in rules:
        if r["op"] == "edge_eol":         # multi-statement cond/eol construction -- op-level
            continue                      # only (a single-expression run can't reference its vars)
        gds = tempfile.mktemp(suffix=".gds")
        try:
            gen_layout(layout_for(r, lm), gds)
            official = r["line"].split("=", 1)[1].strip()
            drc = get("klayout", layermap=lm, metric=r["mode"] or "euclidian").compile(Rule(as_drawn(r)))
            ours = our_line(drc).split("=", 1)[1].strip()   # _OP line, or presence assignment
            c = run_exprs([("OFF", official), ("OUR", ours)], gds, lm)
            off, our = c.get("OFF", 0), c.get("OUR", 0)
            fired += off > 0 and our > 0
            if off == our and off > 0:
                matched += 1
            else:
                fails.append((r["id"], off, our))
        except Exception as e:
            fails.append((r["id"], "ERR", str(e)[:60]))
        finally:
            if os.path.exists(gds):
                os.unlink(gds)

    print(f"behavioral: {len(rules)} rules | both-fired {fired} | matched {matched} | issues {len(fails)}")
    for rid, o, u in fails[:20]:
        print(f"  [{rid}] official={o} ours={u}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
