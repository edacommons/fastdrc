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


_VARDEFS = {}   # derived-var name -> frozenset(base layers), set by parse_rules


def parse_and_vars(text, lm):
    """Resolve derived vars that are a PURE AND of base layers (KLayout infix
    `var = A & B`, recursively: ncomp=comp&nplus, ngate=nplus&tgate) into
    {var: frozenset(base layers)}. Anything with - | parens . or a selection is
    left out (those rules stay deferred)."""
    raw = {}
    for m in re.finditer(r"^(\w+)\s*=\s*([^=].*)$", text, re.M):
        var, expr = m.group(1), m.group(2).strip()
        if var not in lm and ".output(" not in expr and ".forget" not in expr:
            raw[var] = expr
    out = {}

    def resolve(var, seen):
        if var in lm:
            return frozenset({var})
        if var in out:
            return out[var]
        if var not in raw or var in seen:
            return None
        e = raw[var]
        if not re.fullmatch(r"[\w\s&]+", e):
            return None
        layers = set()
        for name in re.findall(r"\w+", e):
            r = resolve(name, seen | {var})
            if r is None:
                return None
            layers |= r
        out[var] = frozenset(layers)
        return out[var]

    for v in list(raw):
        resolve(v, set())
    return out


def expand_base(s, vardefs):
    """Expand ONLY the leading identifier of an expression if it's a derived var
    (`ncomp.outside(x)` -> `comp.and(nplus).outside(x)`). Base position only, so a
    derived var inside a selection's parens is left intact (and stays deferred)."""
    m = re.match(r"\s*(\w+)", s)
    if m and m.group(1) in vardefs:
        ls = sorted(vardefs[m.group(1)])
        return ls[0] + "".join(f".and({l})" for l in ls[1:]) + s[m.end():]
    return s


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
    subj_str = expand_base(rhs[:m.start()], _VARDEFS)      # derived-var base -> AND chain
    s_and, s_not = parse_subject(subj_str)
    s_sel = parse_selections(subj_str)                    # inside/outside/interacting on subject
    tail = rhs[m.end():]
    args_str = _balanced(tail)            # args up to the op's MATCHING close paren
    a_sel = frozenset()
    if op == "with_area":
        val = re.search(r"([0-9.]+)\.um", args_str)
        a_and, a_not, value, mode = frozenset(), frozenset(), val.group(1) if val else "", ""
    elif op in ("width", "space"):
        parts = [p.strip() for p in args_str.split(",")]
        a_and, a_not, value, mode = frozenset(), frozenset(), parts[0].replace(".um", ""), _get(parts, 1)
    else:
        parts = [p.strip() for p in args_str.split(",")]
        arg0 = expand_base(parts[0], _VARDEFS)            # derived-var arg base -> AND chain
        a_and, a_not = parse_subject(arg0)
        a_sel = parse_selections(arg0)                    # selections on a derived/selected arg
        value, mode = _get(parts, 1).replace(".um", ""), _get(parts, 2)
    after = tail[len(args_str) + 1:]                       # result chain after the op
    return (s_and, s_not, s_sel, op, a_and, a_not, a_sel, value, mode,
            frozenset(_SCOPE.findall(after)))


def parse_selections(s):
    """Whole-polygon selections on a (sub)expression: inside/outside/interacting.
    Empty for plain/AND/NOT subjects, so non-selection rules are unaffected."""
    return frozenset(re.findall(r"\.(inside|outside|interacting)\((\w+)\)", s))


def parse_complex_subject(subject):
    """Parse a subject of ONLY ``base`` + a chain of
    ``.and/.not/.inside/.outside/.interacting(layer)`` into
    ``(base, ands, nots, selections)``. Returns None if it contains any other op
    (or / holes / edges / not_inside / nested derived) or doesn't fully reduce --
    those are deferred. Subsumes plain, AND/NOT-derived, and (multi-)selection
    subjects in one parse."""
    bm = re.match(r"\s*(\w+)", subject)
    if not bm:
        return None
    base, rest = bm.group(1), subject[bm.end():].strip()
    toks = re.findall(r"\.(and|not|inside|outside|interacting)\((\w+)\)", rest)
    if "".join(f".{op}({l})" for op, l in toks) != rest:
        return None                          # an unhandled op / nesting remains
    ands = [l for op, l in toks if op == "and"]
    nots = [l for op, l in toks if op == "not"]
    sels = [(op, l) for op, l in toks if op in ("inside", "outside", "interacting")]
    return base, ands, nots, sels


def _balanced(s):
    """Substring of s up to the close paren matching the op's open paren (s begins
    just inside it). Unlike split(')'), this handles NESTED parens -- e.g. a
    derived arg `contact.and(otp_mk), 0.03, euclidian`."""
    depth, out = 1, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return "".join(out)


def _get(seq, i):
    return seq[i] if i < len(seq) else ""


def parse_rules(text, lm):
    global _VARDEFS
    _VARDEFS = parse_and_vars(text, lm)         # derived-var (pure-AND) resolution
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
        subject = expand_base(rhs[:rhs.index("." + op + "(")].strip(), _VARDEFS)
        # one parse for plain / AND-NOT-derived / (multi-)selection subjects; None
        # if it has an unhandled op (or / holes / edges / not_inside) -> deferred.
        parsed = parse_complex_subject(subject)
        if parsed is None:
            skip["complex_subj"] += 1
            continue
        base, ands, nots, selections = parsed
        s_and = frozenset({base}) | frozenset(ands)
        s_not = frozenset(nots)
        derived = bool(ands or nots)
        behavioral_only = False
        # derived separation now round-trips (op-aware covering covers only the
        # subject, + balanced-paren canon). Derived ENCLOSING stays deferred: its
        # arg is INSIDE the subject, so any covering of the subject also covers it.
        if derived and op not in ("width", "space", "with_area", "separation"):
            skip["derived_encsep"] += 1
            continue
        tail = rhs[rhs.index("." + op + "(") + len("." + op + "("):]
        args = _balanced(tail)               # MATCHING close paren (derived args nest)
        arg_selections = []
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
                # arg may be a SELECTED layer (e.g. nwell.outside(dnwell)); parse it
                # like a subject. AND/NOT-derived args are still deferred.
                argp = parse_complex_subject(expand_base(parts[0], _VARDEFS))
                if argp is None or argp[1] or argp[2]:    # unparseable or has and/not
                    skip["derived_arg"] += 1
                    continue
                arg_layers, arg_selections = [argp[0]], argp[3]
                value, mode = parts[1].replace(".um", ""), parts[2]
        sel_layers = [y for _, y in selections] + [y for _, y in arg_selections]
        if any(l not in lm for l in list(s_and | s_not) + arg_layers + sel_layers):
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
                      "scope": scope, "line": line, "selections": selections,
                      "arg_selections": arg_selections, "behavioral_only": behavioral_only})
    return rules, skip


def _find_output(lines, i, var):
    for j in range(i, min(i + 4, len(lines))):
        m = re.search(rf'{re.escape(var)}\.output\("([^"]*)",\s*"([^"]*)"\)', lines[j])
        if m:
            return m.group(1), m.group(2)
    return None, None


# --- "as-drawn" IR (what the editor would produce) ---

def _rect(name, x0, y0, x1, y1, p):
    # edges default to WAVY (edge=False = arbitrary continuation); as_drawn marks
    # the measured ones STRAIGHT (edge=True = a real, oriented edge).
    v = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    e = [{"id": f"{p}{k}", "start": list(v[k]), "end": list(v[(k + 1) % 4]),
          "edge": False} for k in range(4)]   # 0=bottom 1=right 2=top 3=left
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
    # checked geometry sits within ~[0,10]^2; lay markers AROUND it (right / top /
    # left / bottom) with a gap so each is disjoint (-> not_interacting) yet close
    # and visible. Extra markers move to an outer ring.
    if scope_op == "not_interacting":
        d = 8 * (i // 4)
        return {0: (13 + d, 3, 19 + d, 7),       # right
                1: (3, 13 + d, 9, 19 + d),        # top
                2: (-9 - d, 3, -3 - d, 7),        # left
                3: (3, -9 - d, 9, -3 - d)}[i % 4]  # bottom
    if scope_op == "interacting":
        return (-3, -3, 0, 0)                    # touch at the origin corner
    return (-2, -2, 5 + i, 5)                    # overlap near origin


def as_drawn(r):
    op, v, base, holes = r["op"], r["value"], r["base"], r["holes"]
    # `match` constraints identify WHICH geometry the rule is about (layers,
    # markers); the single `check` is the assertion verified (a violation is a
    # matched configuration where the check fails).
    if op == "with_area":
        shapes = [_rect("A", 0, 0, 10, 4, "a")]
        meas = []
        cons = [_match(f"A.layer == {base}"), _check(f"A.area >= {v}")]
    elif op == "width":
        shapes = [_rect("A", 0, 0, 10, 4, "a")]
        meas = [{"name": "W", "from": "a1", "to": "a3"}]
        cons = [_match(f"A.layer == {base}"), _check(f"W >= {v}")]
    elif op == "space":
        shapes = [_rect("A", 0, 0, 4, 10, "a"), _rect("B", 6, 0, 10, 10, "b")]
        meas = [{"name": "S", "from": "a1", "to": "b3"}]
        cons = [_match(f"A.layer == {base}"), _match(f"B.layer == {base}"), _check(f"S >= {v}")]
    elif op == "enclosing":
        shapes = [_rect("A", 0, 0, 10, 10, "a"), _rect("B", 3, 3, 7, 7, "b")]
        meas = [{"name": "E", "from": "a3", "to": "b3"}]
        cons = [_match(f"A.layer == {base}"), _match(f"B.layer == {r['arg'][0]}"), _check(f"E >= {v}")]
    else:  # separation
        shapes = [_rect("A", 0, 0, 4, 10, "a"), _rect("B", 6, 0, 10, 10, "b")]
        meas = [{"name": "S", "from": "a1", "to": "b3"}]
        cons = [_match(f"A.layer == {base}"), _match(f"B.layer == {r['arg'][0]}"), _check(f"S >= {v}")]
    sel_meas = []                       # wavy selection measures (A->S0), kept off straight
    # covering rects (AND-derived layers) must cover only the SUBJECT shapes -- A,
    # plus B for space (same layer) -- NOT a separation arg, else the arg becomes
    # spuriously derived.
    subj = shapes[:2] if op == "space" else shapes[:1]
    bx0 = min(e["start"][0] for s in subj for e in s["edges"])
    by0 = min(e["start"][1] for s in subj for e in s["edges"])
    bx1 = max(e["start"][0] for s in subj for e in s["edges"])
    by1 = max(e["start"][1] for s in subj for e in s["edges"])
    # REMOVED (subtracted) layers -> a real cutout shape INSIDE each subject shape,
    # away from the device point, with a straight cutting edge (a device layer, not
    # an all-wavy domain marker). The compiler derives comp.not(otp_mk) from where
    # the cutout sits relative to the measurement. (Replaces the old tagged hole.)
    cut_straight = set()
    for k, hl in enumerate(holes):
        for si, ss in enumerate(subj):
            sx = min(e["start"][0] for e in ss["edges"])
            sy = min(e["start"][1] for e in ss["edges"])
            nm = f"X{k}{si}"
            cut = _rect(nm, sx + 0.5 + k, sy + 0.5, sx + 1.5 + k, sy + 1.5, nm.lower())
            cut["edges"][0]["edge"] = True          # the cutting boundary is real
            cut_straight.add(cut["edges"][0]["id"])
            shapes.append(cut)
            cons.append(_match(f"{nm}.layer == {hl}"))
    for k, cl in enumerate(r["covers"]):
        shapes.append(_rect(f"C{k}", bx0 - 1 - k, by0 - 1 - k, bx1 + 1 + k, by1 + 1 + k, f"c{k}"))
        cons.append(_match(f"C{k}.layer == {cl}"))
    for k, (so, mk) in enumerate(r["scope"]):
        shapes.append(_rect(f"M{k}", *_marker_pos(so, k), f"m{k}"))
        cons.append(_match(f"M{k}.layer == {mk}"))
    # selection CONTEXTS S{k}: the subject is selected by how it relates to each
    # (inside/outside/interacting). A WAVY measure subject->S{k} (>= 0) makes it a
    # topological SELECTION, not a covering or scope marker -- so ours emits the
    # deck's selection op. Applied to EACH subject shape (A, plus B for space, so
    # both same-layer operands get the selection -> stays space, not separation).
    # Layers may coincide with an operand -- that signal is meaningful, not a dup.
    for k, (sel_op, y) in enumerate(r.get("selections", [])):
        box = {"inside": (-5 - 3 * k, -5 - 3 * k, 30 + 3 * k, 30 + 3 * k),  # contains A
               "outside": (14 + 8 * k, 0, 20 + 8 * k, 4),                  # beside, disjoint
               "interacting": (-3 - 2 * k, -3 - 2 * k, 5, 5)}[sel_op]      # overlaps A
        shapes.append(_rect(f"S{k}", *box, f"s{k}"))
        cons.append(_match(f"S{k}.layer == {y}"))
        for j, pfx in enumerate(["a", "b"] if op == "space" else ["a"]):
            sel_meas.append({"name": f"GS{k}_{j}", "from": f"{pfx}0", "to": f"s{k}0"})
            cons.append(_match(f"GS{k}_{j} >= 0"))
    # arg selections (separation/enclosing): the ARG shape B is selected by how it
    # relates to its context T{k} -> ours emits separation(arg.outside(Y)) etc.
    # Contexts sit to the RIGHT/ABOVE B (6,0,10,10), clear of subject A (x in [0,4]).
    for k, (sel_op, y) in enumerate(r.get("arg_selections", [])):
        # boxes contain/avoid B in BOTH layouts (separation B=(6,0,10,10),
        # enclosing B=(3,3,7,7)); B's selection is read via the wavy measure to T.
        box = {"inside": (2 - 2 * k, -2 - 2 * k, 12 + 2 * k, 12 + 2 * k),  # contains B
               "outside": (3, 14 + 8 * k, 10, 18 + 8 * k),                # above B, disjoint
               "interacting": (4, 4, 12 + 2 * k, 12)}[sel_op]             # overlaps B
        shapes.append(_rect(f"T{k}", *box, f"t{k}"))
        cons.append(_match(f"T{k}.layer == {y}"))
        sel_meas.append({"name": f"GT{k}", "from": "b2", "to": f"t{k}0"})
        cons.append(_match(f"GT{k} >= 0"))
    # only the MEASURED (dimensional) edges are straight (real, oriented); every
    # other edge is wavy = a don't-care boundary the rule doesn't constrain. A min-
    # area shape has NO measured edge (only the total area matters), so all its
    # edges are wavy -- the rect stands for "any polygon", and area is still defined.
    straight = {mz["from"] for mz in meas} | {mz["to"] for mz in meas} | cut_straight
    for s in shapes:
        for e in s["edges"]:
            e["edge"] = e["id"] in straight
    return {"name": r["id"], "desc": r["desc"], "shapes": shapes,
            "measures": meas + sel_meas, "constraints": cons}


def _match(expr):
    return {"kind": "match", "expr": expr}


def _check(expr):
    return {"kind": "check", "expr": expr}


def our_line(drc):
    for line in drc.splitlines():
        if _OP.search(line):
            return line
    return ""


def _angle_sig(s):
    m = re.search(r"(\w+)\.with_angle\(\s*([\d.]+)\s*\.\.\s*([\d.]+)\s*\)", s)
    return (m.group(1), float(m.group(2)), float(m.group(3))) if m else None


def validate_angle_rules(text, lm):
    """Round-trip the deck's `layer.with_angle(lo .. hi)` ACUTE rules: parse the
    deck line, rebuild it as a drawn angle rule (a shape with an oriented edge +
    `e.angle > lo and e.angle < hi`), recompile, and confirm the with_angle op
    matches. Returns (matched, total, fails). Acute rules are inline one-liners
    (`LAYER.with_angle(0 .. 45).output(...)`); the `.edges.with_angle(45)` single-
    value sub-expressions don't match (need `..` and a real base layer)."""
    lines = text.splitlines()
    ok, tot, fails = 0, 0, []
    for i, line in enumerate(lines):
        m = re.search(r"(?:^|[=\s(])(\w+)\.with_angle\(\s*([\d.]+)\s*\.\.\s*([\d.]+)\s*\)", line)
        if not m or m.group(1) not in lm:
            continue
        layer, lo, hi = m.group(1), float(m.group(2)), float(m.group(3))
        mo = re.search(r'\.output\("([^"]+)",\s*"([^"]*)"\)', line)
        if mo:
            rid, desc = mo.group(1), mo.group(2)
        else:
            rid, desc = _find_output(lines, i, line.split("=", 1)[0].strip())
        if rid is None:
            continue
        tot += 1
        fl = lambda x: int(x) if float(x).is_integer() else x
        tri = {"name": "A", "edges": [
            # only a2 (the angle-constrained edge) is STRAIGHT/real -- it has a real
            # orientation; a0/a1 just close the shape (don't-care boundary -> wavy).
            {"id": "a0", "start": [0, 0], "end": [2000, 0], "edge": False},
            {"id": "a1", "start": [2000, 0], "end": [2000, 1000], "edge": False},
            {"id": "a2", "start": [2000, 1000], "end": [0, 0], "edge": True}]}
        rule = {"name": rid, "desc": desc, "shapes": [tri], "measures": [],
                "constraints": [_match(f"A.layer == {layer}"),
                                _check(f"a2.angle > {fl(lo)} and a2.angle < {fl(hi)}")]}
        drc = get("klayout", layermap=lm).compile(Rule(rule))
        ours = next((ln for ln in drc.splitlines() if "with_angle" in ln), "")
        if _angle_sig(ours) == _angle_sig(line):
            ok += 1
        else:
            fails.append((rid, line.split("=", 1)[1].strip(), ours))
    return ok, tot, fails


def main():
    text = open(DECK, encoding="utf-8").read()
    lm = parse_layermap(text)
    rules, skip = parse_rules(text, lm)

    by, fails = {}, []
    for r in rules:
        if r.get("behavioral_only"):     # op text diverges (selection); behavioral-only
            continue
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

    tot = sum(v[1] for v in by.values())   # rules actually compared (behavioral-only skipped)
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

    # validation-scaling: round-trip the with_angle ACUTE rules (exact-match form)
    a_ok, a_tot, a_fails = validate_angle_rules(text, lm)
    print(f"\nwith_angle ACUTE rules: {a_ok}/{a_tot} round-trip MATCHED")
    for rid, off, ours in a_fails[:10]:
        print(f"  MISMATCH [{rid}] official={off!r} ours={ours!r}")
    return 0 if (tot == ok and a_ok == a_tot) else 1


if __name__ == "__main__":
    raise SystemExit(main())
