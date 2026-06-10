#!/usr/bin/env python3
"""Behavioral diff: run KLayout DRC ops on a generated layout, compare violations.

The form-tolerant validation: two rules that are behaviorally equal (e.g.
`X.and(Y)` vs `X.inside(Y)` when X is contained in Y, or scope ops in a different
order) flag the SAME markers even when their op TEXT differs -- which op-level
diffing (tools/validate_klayout.py) can't see. This drives both the test layout
and both decks through the KLayout binary and compares per-category counts.

Run:  python tools/behavioral_diff.py
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

KLAYOUT = os.environ.get("KLAYOUT_BIN", "klayout")
DECK = os.path.join(os.path.dirname(__file__), "..", "docs", "gf180mcu", "gf180mcu_full.drc")


def layermap():
    text = open(DECK, encoding="utf-8").read()
    return {m[0]: (int(m[1]), int(m[2])) for m in
            re.findall(r"^(\w+)\s*=\s*polygons\(\s*(\d+)\s*,\s*(\d+)\s*\)", text, re.M)}


def _klayout(script_path):
    subprocess.run([KLAYOUT, "-b", "-r", script_path], check=True,
                   capture_output=True, text=True)


def gen_layout(shapes, gds_path):
    """shapes: list of (gds_layer, datatype, x0, y0, x1, y1) in DBU (nm)."""
    lines = ["ly = RBA::Layout.new", "ly.dbu = 0.001", 'top = ly.create_cell("TOP")']
    for i, (l, d, x0, y0, x1, y1) in enumerate(shapes):
        lines.append(f"li{i} = ly.layer({l}, {d})")
        lines.append(f"top.shapes(li{i}).insert(RBA::Box.new({x0}, {y0}, {x1}, {y1}))")
    lines.append(f'ly.write("{gds_path}")')
    with tempfile.NamedTemporaryFile("w", suffix=".rb", delete=False) as f:
        f.write("\n".join(lines))
        rb = f.name
    _klayout(rb)
    os.unlink(rb)


def run_ops(ops, gds_path, lm):
    """ops: list of (rule_id, check_expr). Returns {rule_id: violation_count}."""
    rep = gds_path + ".lyrdb"
    body = [f'source("{gds_path}")', f'report("d", "{rep}")']
    body += [f"{n} = polygons({l}, {d})" for n, (l, d) in lm.items()]
    for i, (rid, expr) in enumerate(ops):
        body.append(f"r{i} = {expr}.polygons(0.001)")
        body.append(f'r{i}.output("{rid}", "{rid}")')
    with tempfile.NamedTemporaryFile("w", suffix=".drc", delete=False) as f:
        f.write("\n".join(body) + "\n")
        drc = f.name
    _klayout(drc)
    os.unlink(drc)
    xml = open(rep).read()
    os.unlink(rep)
    items = re.findall(r"<item>.*?<category>'?([^<']*)'?</category>.*?</item>", xml, re.S)
    counts = {}
    for c in items:
        counts[c] = counts.get(c, 0) + 1
    return counts


def behavioral_eq(label, expr_a, expr_b, shapes, lm):
    gds = tempfile.mktemp(suffix=".gds")
    gen_layout(shapes, gds)
    ca = run_ops([("A", expr_a)], gds, lm).get("A", 0)
    cb = run_ops([("B", expr_b)], gds, lm).get("B", 0)
    os.unlink(gds)
    ok = ca == cb
    print(f"[{'OK ' if ok else 'DIFF'}] {label}: official={ca} ours={cb}")
    return ok


def main():
    lm = layermap()
    # layout: a too-close metal1 pair (space) + a thin metal1 square (width).
    metal1 = lm["metal1"]
    shapes = [(metal1[0], metal1[1], 0, 0, 1000, 1000),
              (metal1[0], metal1[1], 1100, 0, 2100, 1000),   # 0.1um gap
              (metal1[0], metal1[1], 0, 3000, 100, 3100)]    # 0.1um wide
    ok = True
    # 1) our generated op == official op -> identical violations (sanity).
    ok &= behavioral_eq("metal1 width (ours==official)",
                        "metal1.width(0.23.um, euclidian)",
                        "metal1.width(0.23.um, euclidian)", shapes, lm)
    # 2) FORM-VARIANT, behaviorally identical: and(self) is identity, so
    #    op text differs but violations are the same -- what op-diff can't see.
    ok &= behavioral_eq("metal1 space: L.space vs L.and(L).space (form-variant)",
                        "metal1.space(0.23.um, euclidian)",
                        "metal1.and(metal1).space(0.23.um, euclidian)", shapes, lm)
    print("ALL BEHAVIORALLY EQUAL" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
