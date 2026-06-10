#!/usr/bin/env python3
"""Validate FastDRC's KLayout backend against the real GF180MCU deck, rule by rule.

Scope handled now: dimensional checks (width / space / enclosing / separation)
on a base layer or an AND/NOT-derived layer (width/space), optionally voltage/
marker scoped (`.not_interacting`/`.overlapping`/`.interacting`). For each rule:
  1. parse the official op (subject AND/NOT layers, op, arg layer, value, metric,
     scope),
  2. build the "as-drawn" rule IR a user would draw: checked rect(s) + measure +
     constraints, with COVERING rects for AND-ed layers, TAGGED HOLES in the
     primary for NOT-ed (subtracted) layers, and marker rects positioned to
     reproduce the scope -- proving the geometry-by-example model captures it,
  3. regenerate our .drc and diff CANONICALLY: subject (AND-set, NOT-set) + op +
     arg (AND/NOT-set) + value + metric + scope-set (AND and scope commute).

Deferred (reported): OR-derived, AND/NOT-derived enclosing/separation, derived
op-args, area scoping (inside/outside), no-metric checks, multi-term/OR, net-aware.

Run:  python tools/validate_klayout.py [path/to/gf180mcu_full.drc]
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_klayout  # noqa: F401  (registers "klayout")
from fastdrc.backend import get
from fastdrc.rule import Rule

DECK = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(__file__), "..", "docs", "gf180mcu", "gf180mcu_full.drc")

OPS = ("width", "space", "enclosing", "separation", "with_area")
_OP = re.compile(r"\.(%s)\(" % "|".join(OPS))
_SCOPE = re.compile(r"\.(not_interacting|overlapping|interacting|inside|outside|"
                    r"not_inside|not_outside)\((\w+)\)")
_HANDLED_SCOPE = {"not_interacting", "overlapping", "interacting"}
# subject methods we don't handle yet (OR / selection); .and and .not ARE handled
_COMPLEX_SUBJ = re.compile(r"\.(or|inside|outside|interacting|overlapping|"
                           r"not_inside|not_outside)\(")


def parse_layermap(text):
    return {m[0]: (int(m[1]), int(m[2])) for m in
            re.findall(r"^(\w+)\s*=\s*polygons\(\s*(\d+)\s*,\s*(\d+)\s*\)", text, re.M)}


def parse_subject(s):
    """Split a layer expression into (AND-set, NOT-set): base + .and(X) -> AND,
    .not(Y) -> NOT."""
    base = re.match(r"\s*(\w+)", s)
    ands = {base.group(1)} if base else set()
    nots = set()
    for op, l in re.findall(r"\.(and|not)\((\w+)\)", s):
        (ands if op == "and" else nots).add(l)
    return frozenset(ands), frozenset(nots)


def canon(line):
    """Canonical, order-insensitive signature of a KLayout check line."""
    rhs = line.split("=", 1)[1] if "=" in line else line
    m = _OP.search(rhs)
    op = m.group(1)
    s_and, s_not = parse_subject(rhs[:m.start()])
    tail = rhs[m.end():]
    args_str = tail.split(")", 1)[0]
    if op == "with_area":
        val = re.search(r"([0-9.]+)\.um", args_str)
        a_and, a_not, value, mode = frozenset(), frozenset(), val.group(1) if val else "", ""
    elif op in ("width", "space"):
        parts = [p.strip() for p in args_str.split(",")]
        a_and, a_not, value, mode = frozenset(), frozenset(), parts[0].replace(".um", ""), _get(parts, 1)
    else:
        parts = [p.strip() for p in args_str.split(",")]
        a_and, a_not = parse_subject(parts[0])
        value, mode = _get(parts, 1).replace(".um", ""), _get(parts, 2)
    after = tail.split(")", 1)[1] if ")" in tail else ""   # result chain after the op
    return (s_and, s_not, op, a_and, a_not, value, mode, frozenset(_SCOPE.findall(after)))


def _get(seq, i):
    return seq[i] if i < len(seq) else ""


def parse_rules(text, lm):
    lines = text.splitlines()
    rules = []
    skip = dict(complex_subj=0, derived_encsep=0, derived_arg=0, argshape=0,
                area_scope=0, no_output=0, nonbase=0, area_max=0)
    for i, line in enumerate(lines):
        m = _OP.search(line)
        if not m or "=" not in line:
            continue
        op = m.group(1)
        rhs = line.split("=", 1)[1]
        subject = rhs[:rhs.index("." + op + "(")].strip()
        if _COMPLEX_SUBJ.search(subject) or ".holes" in subject or ".edges" in subject:
            skip["complex_subj"] += 1
            continue
        s_and, s_not = parse_subject(subject)
        base = re.match(r"\s*(\w+)", subject).group(1)
        derived = ".and(" in subject or ".not(" in subject
        if derived and op not in ("width", "space", "with_area"):
            skip["derived_encsep"] += 1
            continue
        tail = rhs[rhs.index("." + op + "(") + len("." + op + "("):]
        args = tail.split(")", 1)[0]
        if op == "with_area":
            # we handle MIN area only: with_area(nil, v). with_area(v, nil) is a
            # MAX-area rule (opposite direction) -- deferred.
            if not args.strip().startswith("nil") or ".not_in" in tail:
                skip["area_max"] += 1
                continue
            val = re.search(r"([0-9.]+)\.um", args)
            if not val:
                skip["argshape"] += 1
                continue
            arg_layers, value, mode = [], val.group(1), ""
        else:
            parts = [p.strip() for p in args.split(",")]
            need = 2 if op in ("width", "space") else 3
            if len(parts) < need:
                skip["argshape"] += 1
                continue
            if op in ("width", "space"):
                arg_layers, value, mode = [], parts[0].replace(".um", ""), parts[1]
            else:
                if _COMPLEX_SUBJ.search(parts[0]) or ".and(" in parts[0] or ".not(" in parts[0]:
                    skip["derived_arg"] += 1
                    continue
                arg_layers, value, mode = [parts[0]], parts[1].replace(".um", ""), parts[2]
        if any(l not in lm for l in list(s_and | s_not) + arg_layers):
            skip["nonbase"] += 1
            continue
        after = tail.split(")", 1)[1] if ")" in tail else ""   # result chain after the op
        scope = _SCOPE.findall(after)
        if any(mk not in lm for _, mk in scope):
            skip["nonbase"] += 1
            continue
        if any(so not in _HANDLED_SCOPE for so, _ in scope):
            skip["area_scope"] += 1
            continue
        rid, desc = _find_output(lines, i, line.split("=", 1)[0].strip())
        if rid is None:
            skip["no_output"] += 1
            continue
        rules.append({"id": rid, "desc": desc, "op": op, "base": base,
                      "covers": sorted(s_and - {base}), "holes": sorted(s_not),
                      "arg": arg_layers, "value": value, "mode": mode,
                      "scope": scope, "line": line})
    return rules, skip


def _find_output(lines, i, var):
    for j in range(i, min(i + 4, len(lines))):
        m = re.search(rf'{re.escape(var)}\.output\("([^"]*)",\s*"([^"]*)"\)', lines[j])
        if m:
            return m.group(1), m.group(2)
    return None, None


# --- "as-drawn" IR (what the editor would produce) ---

def _rect(name, x0, y0, x1, y1, p):
    v = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    e = [{"id": f"{p}{k}", "start": list(v[k]), "end": list(v[(k + 1) % 4]),
          "edge": True} for k in range(4)]   # 0=bottom 1=right 2=top 3=left
    return {"name": name, "edges": e}


def _holed(name, x0, y0, x1, y1, p, hole_layers):
    """A rect with a small tagged hole per subtracted layer (strictly inside,
    away from the centroid so covering tests are unaffected)."""
    sh = _rect(name, x0, y0, x1, y1, p)
    holes = []
    for k, hl in enumerate(hole_layers):
        hx, hy = x0 + 1 + 2 * k, y0 + 1
        loop = _rect(f"{name}h{k}", hx, hy, hx + 1, hy + 1, f"{p}h{k}")["edges"]
        holes.append({"layer": hl, "edges": loop})
    if holes:
        sh["holes"] = holes
    return sh


def _marker_pos(scope_op, i):
    if scope_op == "not_interacting":
        return (100 + 20 * i, 100, 110 + 20 * i, 110)
    if scope_op == "interacting":
        return (-3, -3, 0, 0)
    return (-2, -2, 5 + i, 5)


def as_drawn(r):
    op, v, base, holes = r["op"], r["value"], r["base"], r["holes"]
    if op == "with_area":
        shapes = [_holed("A", 0, 0, 10, 4, "a", holes)]
        meas = []
        cons = [_c(f"A.layer == {base}"), _c(f"A.area >= {v}")]
    elif op == "width":
        shapes = [_holed("A", 0, 0, 10, 4, "a", holes)]
        meas = [{"name": "W", "from": "a1", "to": "a3"}]
        cons = [_c(f"A.layer == {base}"), _c(f"W >= {v}")]
    elif op == "space":
        shapes = [_holed("A", 0, 0, 4, 10, "a", holes), _holed("B", 6, 0, 10, 10, "b", holes)]
        meas = [{"name": "S", "from": "a1", "to": "b3"}]
        cons = [_c(f"A.layer == {base}"), _c(f"B.layer == {base}"), _c(f"S >= {v}")]
    elif op == "enclosing":
        shapes = [_rect("A", 0, 0, 10, 10, "a"), _rect("B", 3, 3, 7, 7, "b")]
        meas = [{"name": "E", "from": "a3", "to": "b3"}]
        cons = [_c(f"A.layer == {base}"), _c(f"B.layer == {r['arg'][0]}"), _c(f"E >= {v}")]
    else:  # separation
        shapes = [_rect("A", 0, 0, 4, 10, "a"), _rect("B", 6, 0, 10, 10, "b")]
        meas = [{"name": "S", "from": "a1", "to": "b3"}]
        cons = [_c(f"A.layer == {base}"), _c(f"B.layer == {r['arg'][0]}"), _c(f"S >= {v}")]
    for k, cl in enumerate(r["covers"]):
        shapes.append(_rect(f"C{k}", -2 - k, -2 - k, 30, 30, f"c{k}"))
        cons.append(_c(f"C{k}.layer == {cl}"))
    for k, (so, mk) in enumerate(r["scope"]):
        shapes.append(_rect(f"M{k}", *_marker_pos(so, k), f"m{k}"))
        cons.append(_c(f"M{k}.layer == {mk}"))
    return {"name": r["id"], "desc": r["desc"], "shapes": shapes,
            "measures": meas, "constraints": cons}


def _c(expr):
    return {"kind": "check", "expr": expr}


def our_line(drc):
    for line in drc.splitlines():
        if _OP.search(line):
            return line
    return ""


def main():
    text = open(DECK, encoding="utf-8").read()
    lm = parse_layermap(text)
    rules, skip = parse_rules(text, lm)

    by, fails = {}, []
    for r in rules:
        drc = get("klayout", layermap=lm, metric=r["mode"]).compile(Rule(as_drawn(r)))
        ok = canon(our_line(drc)) == canon(r["line"])
        kind = ("derived" if r["covers"] or r["holes"]
                else "scoped" if r["scope"] else "clean")
        key = (r["op"], kind)
        by.setdefault(key, [0, 0])
        by[key][1] += 1
        by[key][0] += ok
        if not ok:
            fails.append((r["id"], canon(r["line"]), canon(our_line(drc))))

    tot = len(rules)
    ok = sum(v[0] for v in by.values())
    print(f"deck: {os.path.relpath(DECK)}   layers: {len(lm)}")
    print(f"rules compared: {tot}    MATCHED {ok}    MISMATCHED {tot - ok}")
    print("by (op, kind) matched/total:")
    for k in sorted(by):
        m, t = by[k]
        print(f"  {k[0]:11} {k[1]:8} {m}/{t}")
    print(f"deferred (next frontiers): {skip}")
    for rid, off, ours in fails[:15]:
        print(f"  MISMATCH [{rid}]\n    official: {off}\n    ours    : {ours}")
    return 0 if tot == ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
