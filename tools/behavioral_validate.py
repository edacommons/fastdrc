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

from validate_klayout import parse_layermap, parse_rules, as_drawn, _OP  # noqa: E402
from behavioral_diff import gen_layout, KLAYOUT                           # noqa: E402
from fastdrc.backend import get                                          # noqa: E402
from fastdrc.rule import Rule                                            # noqa: E402

DECK = os.path.join(os.path.dirname(__file__), "..", "docs", "gf180mcu", "gf180mcu_full.drc")


def run_exprs(exprs, gds, lm):
    """exprs: [(id, full_check_expression)]. Returns {id: violation_count}."""
    rep = gds + ".lyrdb"
    body = [f'source("{gds}")', f'report("d", "{rep}")']
    body += [f"{n} = polygons({l}, {d})" for n, (l, d) in lm.items()]
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
    bl = lm[base]
    v = float(r["value"])
    vnm = int(round(v * 1000))
    s = []
    if op == "width":                                   # too-narrow rect
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
    elif op == "with_area":                             # area below threshold
        side = max(1, int(math.sqrt(v) * 1000 * 0.5))
        s.append((*bl, 0, 0, side, side))
    for cl in r["covers"]:                              # AND-ed layer -> must cover
        s.append((*lm[cl], -2000, -2000, 8000, 8000))
    for hl in r["holes"]:                               # NOT-ed layer -> must NOT cover
        s.append((*lm[hl], 90000, 90000, 91000, 91000))
    for so, mk in r["scope"]:                           # in-domain so the rule fires
        far = (*lm[mk], 90000, 90000, 91000, 91000)
        near = (*lm[mk], -2000, -2000, 8000, 8000)
        s.append(far if so == "not_interacting" else near)
    if r.get("selection"):                              # X.inside/outside/interacting(Y)
        sel_op, y = r["selection"]
        box = {"inside": (-5000, -5000, 30000, 30000),    # S0 contains the violating X
               "outside": (100000, 100000, 101000, 101000),  # S0 far (disjoint)
               "interacting": (-3000, -3000, 5000, 5000)}[sel_op]  # S0 overlaps X
        s.append((*lm[y], *box))
        # NOTE: for separation, outside(Y) before separation(Y) is a behavioral
        # no-op -- the only subject polys it drops are those area-overlapping Y, and
        # plain separation never flags overlapping polys anyway (verified across
        # touch/overlap/inside configs). We still emit it for op-text fidelity.
    return s


def main():
    text = open(DECK, encoding="utf-8").read()
    lm = parse_layermap(text)
    rules, _ = parse_rules(text, lm)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(rules)
    rules = rules[:limit]

    matched = fired = 0
    fails = []
    for r in rules:
        gds = tempfile.mktemp(suffix=".gds")
        try:
            gen_layout(layout_for(r, lm), gds)
            official = r["line"].split("=", 1)[1].strip()
            drc = get("klayout", layermap=lm, metric=r["mode"] or "euclidian").compile(Rule(as_drawn(r)))
            ours = next(ln for ln in drc.splitlines() if _OP.search(ln)).split("=", 1)[1].strip()
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
