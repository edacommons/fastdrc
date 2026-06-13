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


_VARDEFS = {}   # derived-var name -> (frozenset ands, frozenset nots), set by parse_rules


def parse_and_vars(text, lm):
    """Resolve derived vars that are a pure AND/NOT of base layers into
    {var: (frozenset ands, frozenset nots)}. Handles KLayout infix (`ncomp=comp &
    nplus`, `x = A & B - C`) AND method chains (`poly_pl = poly2.not(otp_mk).not(
    ymtp_mk)`), recursively. A `&` term may itself be derived (its ands/nots merge
    in); a `-` term must be a SINGLE base layer (subtracting a multi-layer region
    isn't a flat not-set). A var REDEFINED with a different RHS (per voltage block)
    is dropped -- it can't be resolved globally. Anything with | parens or a
    selection (.inside/.outside/...) is left out -- those rules stay deferred."""
    raw, conflict = {}, set()
    for m in re.finditer(r"^(\w+)\s*=\s*([^=].*)$", text, re.M):
        var, expr = m.group(1), m.group(2).strip()
        if var not in lm and ".output(" not in expr and ".forget" not in expr:
            if var in raw and raw[var] != expr:
                conflict.add(var)        # redefined per voltage block -> not resolvable
            raw[var] = expr
    out = {}

    def resolve(var, seen):
        if var in lm:
            return frozenset({var}), frozenset()
        if var in out:
            return out[var]
        if var not in raw or var in seen or var in conflict:
            return None
        e = raw[var].replace(" ", "")
        # ONLY .and()/.not() methods and & / - infix over plain identifiers; any
        # other method (a selection) or grouping disqualifies the whole var.
        if not re.fullmatch(r"[\w&\-]+(\.(and|not)\(\w+\))*", e):
            return None
        ands, nots = set(), set()
        head = re.match(r"[\w&\-]+", e).group(0)
        for sign, name in re.findall(r"([&\-]?)(\w+)", head):
            r = resolve(name, seen | {var})
            if r is None:
                return None
            ta, tn = r
            if sign == "-":                       # subtract: only a single base layer
                if len(ta) != 1 or tn:
                    return None
                nots |= ta
            else:                                 # base or & term: merge ands and nots
                ands |= ta
                nots |= tn
        for mop, ml in re.findall(r"\.(and|not)\((\w+)\)", e[len(head):]):
            r = resolve(ml, seen | {var})
            if r is None:
                return None
            ta, tn = r
            if mop == "and":
                ands |= ta
                nots |= tn
            elif len(ta) != 1 or tn:
                return None
            else:
                nots |= ta
        out[var] = (frozenset(ands), frozenset(nots))
        return out[var]

    for v in list(raw):
        resolve(v, set())
    return out


def _var_expansion(ands, nots):
    """Canonical `base.and(a).not(n)...` string for a resolved (ands, nots)."""
    a = sorted(ands)
    return a[0] + "".join(f".and({l})" for l in a[1:]) + "".join(f".not({l})" for l in sorted(nots))


def expand_base(s, vardefs):
    """Expand ONLY the leading identifier of an expression if it's a derived var
    (`ncomp.outside(x)` -> `comp.and(nplus).outside(x)`; `poly_pl.width` ->
    `poly2.not(otp_mk)....width`). Base position only, so a derived var inside a
    selection's parens is left intact (and stays deferred)."""
    m = re.match(r"\s*(\w+)", s)
    if m and m.group(1) in vardefs:
        return _var_expansion(*vardefs[m.group(1)]) + s[m.end():]
    return s


def parse_subject(s):
    """Split a layer expression into (AND-set, NOT-set): base + .and(X) -> AND,
    .not(Y) -> NOT. Walks TOP-LEVEL ops only (balanced args) so a `.and()`/`.not()`
    buried inside a selection marker's parens isn't pulled into the subject set."""
    bm = re.match(r"\s*(\w+)", s)
    if not bm:
        return frozenset(), frozenset()
    ands, nots = {bm.group(1)}, set()
    rest, i = s[bm.end():], 0
    while i < len(rest):
        m = re.match(r"\.(\w+)\(", rest[i:])
        if not m:
            break
        op, arg = m.group(1), _balanced(rest[i + m.end():])
        if op == "and":
            ands.add(arg)
        elif op == "not":
            nots.add(arg)
        i += m.end() + len(arg) + 1
    return frozenset(ands), frozenset(nots)


_EDGE_SIZE = re.compile(r"\s*(\w+)\.edges\.(with_length|without_length)\(([^)]*)\)"
                        r"\.extended\(([^)]*)\)(.*)$")


def _walk_edge_chain(chain):
    """Walk the balanced op-chain after `BASE.edges`: `.and(X.edges)` -> a GATE
    (X's base-layer set), `.not(M)` -> an edge cutout, `.and(M)` (M a region) -> an
    edge region-AND. Returns (gates, enots, eands) as sets, or None if an unhandled
    op (interacting / selection) is hit -- those edge rules stay deferred."""
    gates, enots, eands = set(), set(), set()
    i = 0
    while i < len(chain):
        cm = re.match(r"\.(\w+)\(", chain[i:])
        if not cm:
            return None
        cop, arg = cm.group(1), _balanced(chain[i + cm.end():])
        if arg.endswith(".edges") and cop == "and":
            gates.add(_norm_marker(arg[:-len(".edges")]))
        elif cop == "not":
            enots.add(_norm_marker(arg))
        elif cop == "and":
            eands.add(_norm_marker(arg))
        else:
            return None
        i += cm.end() + len(arg) + 1
    return gates, enots, eands


def _parse_edge_dim(region):
    """Parse `BASE.edges.and(GATE.edges).not(M)...width/space/separation(args)<after>`
    into (base, gates_raw, enots, eands, op, value, mode, after). gates_raw are the
    raw `X` of each `.and(X.edges)`. None if the chain has an unhandled op."""
    bm = re.match(r"(\w+)\.edges", region)
    if not bm:
        return None
    base, rest = bm.group(1), region[bm.end():]
    gates_raw, enots, eands = [], [], []
    op = value = mode = None
    after, i = "", 0
    while i < len(rest):
        cm = re.match(r"\.(\w+)\(", rest[i:])
        if not cm:
            return None
        cop, arg = cm.group(1), _balanced(rest[i + cm.end():])
        nxt = i + cm.end() + len(arg) + 1
        if cop in ("width", "space", "separation"):
            op = cop
            parts = [p.strip() for p in arg.split(",")]
            if cop == "separation":
                value, mode = _get(parts, 1).replace(".um", ""), _get(parts, 2)
            else:
                value, mode = parts[0].replace(".um", ""), _get(parts, 1)
            after = rest[nxt:]
            break
        if cop == "and" and arg.endswith(".edges"):
            gates_raw.append(arg[:-len(".edges")])
        elif cop == "not":
            enots.append(arg)
        elif cop == "and":
            eands.append(arg)
        else:
            return None
        i = nxt
    return None if op is None else (base, gates_raw, enots, eands, op, value, mode, after)


def _canon_edge_dim(op, subj_str, rhs, m):
    """Canon signature for an EDGE-dimensional rule (gate width):
    `BASE.edges.and(GATE.edges).not(M)...OP(value)`. tgate.edges and
    (poly2&comp).edges normalize to the same gate set."""
    bm = re.match(r"\s*(\w+)\.edges", subj_str)
    walk = _walk_edge_chain(subj_str[bm.end():])
    if walk is None:
        return None
    gates, enots, eands = walk
    tail = rhs[m.end():]
    args_str = _balanced(tail)
    parts = [p.strip() for p in args_str.split(",")]
    value, mode = parts[0].replace(".um", ""), _get(parts, 1)
    after = tail[len(args_str) + 1:]
    return (frozenset({bm.group(1)}), frozenset(enots), frozenset(eands), "edge_" + op,
            frozenset(gates), frozenset(), frozenset(), value, mode,
            frozenset(_SCOPE.findall(after)))


def _norm_lenargs(kind, largs):
    """Canonical length-arg key: `without_length(0.22.um)` -> 'without:0.22';
    `with_length(nil, 0.34.um)` -> 'with:nil:0.34'."""
    vals = []
    for p in (x.strip() for x in largs.split(",")):
        mm = re.match(r"([0-9.]+)\.um", p)
        vals.append(mm.group(1) if mm else p)              # 'nil' stays
    return ("without" if kind == "without_length" else "with") + ":" + ":".join(vals)


def canon(line):
    """Canonical, order-insensitive signature of a KLayout check line."""
    rhs = line.split("=", 1)[1] if "=" in line else line
    m = _OP.search(rhs)
    if m is None:                          # no dimensional op -> a PRESENCE region (the
        # region itself is the violation). Signature = subject layers + selections.
        region = expand_base(re.split(r"\.(?:output|forget)\(", rhs)[0].strip(), _VARDEFS)
        em = _EDGE_SIZE.match(region)      # edge-SIZE: LAYER.edges.(with|without)_length(...).extended(...)
        if em:
            base, kind, largs, ext, after = em.groups()
            return (frozenset({base}), frozenset(), frozenset(), "edge_length",
                    frozenset(), frozenset(), frozenset({re.sub(r'\s+', '', ext)}),
                    _norm_lenargs(kind, largs), "", frozenset(_SCOPE.findall(after)))
        s_and, s_not = parse_subject(region)
        s_sel = parse_selections(region)
        return (s_and, s_not, s_sel, "presence",
                frozenset(), frozenset(), frozenset(), "", "", frozenset())
    op = m.group(1)
    subj_str = expand_base(rhs[:m.start()], _VARDEFS)      # derived-var base -> AND chain
    if ".edges" in subj_str:                              # EDGE-dimensional (gate width)
        sig = _canon_edge_dim(op, subj_str, rhs, m)
        if sig is not None:
            return sig
    s_and, s_not = parse_subject(subj_str)
    s_sel = parse_selections(subj_str)                    # inside/outside/interacting on subject
    tail = rhs[m.end():]
    args_str = _balanced(tail)            # args up to the op's MATCHING close paren
    a_sel = frozenset()
    if op == "with_area":
        val = re.search(r"([0-9.]+)\.um", args_str)
        a_and, a_not, value = frozenset(), frozenset(), val.group(1) if val else ""
        mode = "minarea" if args_str.strip().startswith("nil") else "maxarea"  # nil,v vs v,nil
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


_SELOPS = ("inside", "outside", "interacting", "overlapping", "not_outside", "not_interacting")


def _norm_marker(arg):
    """Canonical key for a selection's marker: a derived var (tgate) or an .and
    chain (poly2.and(comp)) or a single layer -> the sorted base-layer SET, so
    `not_interacting(tgate)` matches `not_interacting(poly2.and(comp))`. A NESTED
    marker (metal2.interacting(fusetop)) recurses: base SET + each nested selection,
    so a selected marker keeps its own context in the signature."""
    p = parse_complex_subject(arg)
    if p is None:                                        # not a clean base+chain: fall back
        bm = re.match(r"\s*(\w+)", arg)
        if not bm:
            return arg
        name = bm.group(1)
        layers = set(_VARDEFS[name][0]) if name in _VARDEFS else {name}
        layers |= set(re.findall(r"\.and\((\w+)\)", arg))
        return ".".join(sorted(layers))
    base, ands, nots, sels = p
    layers = set(_VARDEFS[base][0]) if base in _VARDEFS else {base}
    for a in ands:
        layers |= set(_VARDEFS[a][0]) if a in _VARDEFS else {a}
    sig = ".".join(sorted(layers))
    for op, m in sorted(("overlapping" if o == "not_outside" else o, _norm_marker(mm))
                        for o, mm in sels):
        sig += f".{op}({m})"
    for n in sorted(nots):
        sig += f".not({n})"
    return sig


def parse_selections(s):
    """Whole-polygon selections on a (sub)expression: inside/outside/interacting/
    overlapping/not_outside/not_interacting. The marker is normalized (see
    _norm_marker), and `not_outside` -> `overlapping` (same op). Walks TOP-LEVEL ops
    only (balanced args) so a NESTED selection inside a marker isn't mistaken for a
    selection on the subject. Empty for plain/AND/NOT subjects."""
    out = set()
    bm = re.match(r"\s*\w+", s)
    if not bm:
        return frozenset(out)
    rest, i = s[bm.end():], 0
    while i < len(rest):
        m = re.match(r"\.(\w+)\(", rest[i:])
        if not m:
            break
        op, arg = m.group(1), _balanced(rest[i + m.end():])
        if op in _SELOPS:
            o = "overlapping" if op == "not_outside" else op
            for oo, mm in _decompose_sel(o, arg):     # inside(A&B) -> inside(A).inside(B)
                out.add((oo, _norm_marker(mm)))
        i += m.end() + len(arg) + 1
    return frozenset(out)


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
    _SELOPS = ("inside", "outside", "interacting", "overlapping", "not_outside", "not_interacting")
    toks, i = [], 0                          # (op, arg) with BALANCED args -> markers may NEST
    while i < len(rest):
        m = re.match(r"\.(\w+)\(", rest[i:])
        if not m:
            return None
        op, arg = m.group(1), _balanced(rest[i + m.end():])
        toks.append((op, arg))
        i += m.end() + len(arg) + 1          # past `.op(arg)`
    if i != len(rest) or any(op not in ("and", "not") + _SELOPS for op, _ in toks):
        return None                          # unhandled op (or / holes / edges / ...)
    if any(op in ("and", "not") and not re.fullmatch(r"\w+", a) for op, a in toks):
        return None                          # AND/NOT args must be a single layer
    ands = [a for op, a in toks if op == "and"]
    nots = [a for op, a in toks if op == "not"]
    sels = [(op, a) for op, a in toks if op in _SELOPS]   # `a` may be a NESTED marker
    return base, ands, nots, sels


def _marker_deep_layers(y):
    """All base layers a (possibly NESTED) selection marker resolves to -- the
    marker's own base/AND/NOT layers plus, recursively, those of any nested
    selection context. Used to confirm a nested marker is drawable (all in lm)."""
    p = parse_complex_subject(y)
    if p is None:
        return list(_VARDEFS[y][0]) if y in _VARDEFS else [y]
    b, a, n, sl = p
    out = (list(_VARDEFS[b][0]) if b in _VARDEFS else [b]) + a + n
    for _, m in sl:
        out += _marker_deep_layers(m)
    return out


def _drawable_marker(op, y):
    """Whether a (possibly NESTED) selection marker is one we can currently draw as
    a device. A regular-selection marker must be a base + nested single-layer
    SELECTIONS only: an AND inside the marker (its doping) overlaps the subject for
    `inside` contexts, and marker NOTs (cutouts) aren't drawn yet -- both deferred.
    A `not_interacting` marker goes through the derived-marker (sni) path, which
    already resolves ANDs to overlapping shapes, so it's always drawable."""
    if op == "not_interacting":
        return True
    p = parse_complex_subject(y)
    if p is None:                              # plain layer or var -- the original path
        return True
    _b, ands, nots, sl = p
    if ands and op == "inside":
        return False              # doping on a CONTAINING box also covers the subject;
        #   pure inside(A&B) is split by _decompose_sel before we get here, so a
        #   leftover inside-AND is a non-decomposable mix -- defer it
    return all(_drawable_marker(so, m) for so, m in sl)


def _decompose_sel(op, marker):
    """`inside(A.and(B).and(C))` == `inside(A).inside(B).inside(C)` -- a polygon is
    inside the intersection iff inside each operand (containment is intersective).
    So a pure base+AND marker under `inside` splits into one selection per layer.
    Other ops DON'T decompose (outside(A&B) != outside(A).outside(B) -- that's A|B;
    interacting/overlapping likewise). A single layer / var / nested marker is left
    as-is."""
    if op != "inside":
        return [(op, marker)]
    p = parse_complex_subject(marker)
    if p is None:
        return [(op, marker)]
    b, ands, nots, sl = p
    if nots or sl or not ands:          # only a pure base+AND of >=2 base layers
        return [(op, marker)]
    return [(op, b)] + [(op, a) for a in ands]


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
        selections = [d for o, y in selections for d in _decompose_sel(o, y)]
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
        arg_selections, arg_covers, arg_holes = [], [], []
        area_op = ">="                       # min-area by default (with_area direction)
        if op == "with_area":
            # with_area(nil, v) = MIN area (A.area >= v); with_area(v, nil) = MAX
            # area (A.area <= v). `.not_in` (area between two values) is deferred.
            mn = re.match(r"\s*nil\s*,\s*([0-9.]+)\.um", args)
            mx = re.match(r"\s*([0-9.]+)\.um\s*,\s*nil", args)
            if ".not_in" in tail or not (mn or mx):
                skip["area_max"] += 1
                continue
            arg_layers, mode = [], ""
            value, area_op = (mn.group(1), ">=") if mn else (mx.group(1), "<=")
        else:
            parts = [p.strip() for p in args.split(",")]
            need = 2 if op in ("width", "space") else 3
            if len(parts) < need:
                skip["argshape"] += 1
                continue
            if op in ("width", "space"):
                arg_layers, value, mode = [], parts[0].replace(".um", ""), parts[1]
            else:
                # arg may be a SELECTED or AND/NOT-DERIVED layer (e.g.
                # nwell.outside(dnwell), poly2.and(sab)); parse it like a subject.
                argp = parse_complex_subject(expand_base(parts[0], _VARDEFS))
                if argp is None:
                    skip["derived_arg"] += 1
                    continue
                arg_base, arg_ands, arg_nots, arg_selections = argp
                arg_selections = [d for o, y in arg_selections for d in _decompose_sel(o, y)]
                # AND/NOT-derived arg now works for enclosing too: drawn match-first
                # (the inner device near the subject's EDGE), the doping covers only
                # the device, not the subject's interior -- no entanglement.
                if (arg_ands or arg_nots) and op not in ("separation", "enclosing"):
                    skip["derived_arg"] += 1
                    continue
                # a self-referential arg selection on ENCLOSING (context layer ==
                # subject base, e.g. nat.enclosing(ncomp.interacting(nat))) is
                # ambiguous to draw -- the inner context and the subject share a
                # layer. Separation handles it (subject clear of the arg); enclosing
                # doesn't (arg sits inside the subject). Defer just the enclosing case.
                if op == "enclosing" and any(y == base for _, y in arg_selections):
                    skip["derived_arg"] += 1
                    continue
                arg_layers, arg_covers, arg_holes = [arg_base], arg_ands, arg_nots
                value, mode = parts[1].replace(".um", ""), parts[2]
        # a selection marker may be a DERIVED var (not_interacting(tgate)); expand it
        # to its base layers for the in-layermap check.
        if any(not _drawable_marker(o, y) for o, y in selections + arg_selections):
            skip["complex_subj"] += 1            # AND/NOT inside a regular-selection marker
            continue
        sel_layers = []
        for _, y in selections + arg_selections:
            sel_layers += _marker_deep_layers(y)
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
                      "arg_selections": arg_selections, "arg_covers": arg_covers,
                      "arg_holes": arg_holes, "area_op": area_op,
                      "behavioral_only": behavioral_only})
    # EDGE-DIMENSIONAL (gate WIDTH): `BASE.edges.and(GATE.edges).not(M)...width(L)` --
    # poly width over the gate (tgate = poly2 & comp). The covering GATE layer(s) form
    # the gate edge-marker; .not(M) cutouts become edge .not. First slice: WIDTH only,
    # no region-AND filters / subject selections (those stay deferred).
    done = {r["id"] for r in rules}
    skip["edge_dim"] = 0
    for i, line in enumerate(lines):
        mo = re.match(r'\s*(\w+)\.output\("([^"]*)",\s*"([^"]*)"\)', line)
        if not mo:
            continue
        var, rid, desc = mo.group(1), mo.group(2), mo.group(3)
        if rid in done:
            continue
        defn = defline = None
        for j in range(i - 1, -1, -1):
            dm = re.match(rf"\s*{re.escape(var)}\s*=\s*([^=].*)", lines[j])
            if dm:
                defn, defline = dm.group(1).strip(), lines[j]
                break
        if defn is None or ".edges" not in defn:
            continue
        region = expand_base(re.split(r"\.(?:output|forget)\(", defn)[0].strip(), _VARDEFS)
        pe = _parse_edge_dim(region)
        if pe is None:
            continue
        base, gates_raw, enots, eands, eop, value, mode, after = pe
        if eop != "width" or not gates_raw:                 # first slice: gate WIDTH
            continue
        # gate = the active region (drawn CROSSING the subject); filters = mask regions
        # (drawn CONTAINING it); cutouts = removed. The backend recovers all three from
        # the drawn relations -- nothing about the structure is stashed.
        gate = sorted({l for g in gates_raw for l in _marker_deep_layers(g)} - {base})
        filters = sorted({l for e in eands for l in _marker_deep_layers(e)})
        cuts = sorted({l for e in enots for l in _marker_deep_layers(e)})
        if base not in lm or any(l not in lm for l in gate + filters + cuts):
            continue
        scope = [(o, mk) for o, mk in _SCOPE.findall(after) if o in _HANDLED_SCOPE and mk in lm]
        done.add(rid)
        rules.append({"id": rid, "desc": desc, "op": "edge_width", "base": base,
                      "gate": gate, "covers": filters, "holes": cuts,
                      "arg": [], "value": value, "mode": mode, "scope": scope, "line": defline,
                      "selections": [], "arg_selections": [], "arg_covers": [], "arg_holes": [],
                      "area_op": ">=", "behavioral_only": False})
    # EDGE-SIZE rules: `var = LAYER.edges.(with|without)_length(...).extended(...)` --
    # min/max contact/via size. The flagged EDGES are the violation, rendered as thin
    # polygons via .extended. The validator's first round-trip of an .edges rule.
    done = {r["id"] for r in rules}
    skip["edge_size"] = 0
    for i, line in enumerate(lines):
        mo = re.match(r'\s*(\w+)\.output\("([^"]*)",\s*"([^"]*)"\)', line)
        if not mo:
            continue
        var, rid, desc = mo.group(1), mo.group(2), mo.group(3)
        if rid in done:
            continue
        defn = defline = None
        for j in range(i - 1, -1, -1):
            dm = re.match(rf"\s*{re.escape(var)}\s*=\s*([^=].*)", lines[j])
            if dm:
                defn, defline = dm.group(1).strip(), lines[j]
                break
        if defn is None:
            continue
        region = expand_base(re.split(r"\.(?:output|forget)\(", defn)[0].strip(), _VARDEFS)
        em = _EDGE_SIZE.match(region)
        if not em:
            continue
        base, kind, largs, ext, after = em.groups()
        if base not in lm:
            continue
        nums = [None if p.strip() == "nil" else
                (re.match(r"([0-9.]+)\.um", p.strip()) or re.match(r"([0-9.]+)", p.strip())).group(1).rstrip(".")
                for p in largs.split(",")]
        if kind == "without_length":
            k, lo, hi = "without", nums[0], nums[0]
        else:
            k, lo, hi = "with", nums[0], (nums[1] if len(nums) > 1 else nums[0])
        scope = [(o, mk) for o, mk in _SCOPE.findall(after) if o in _HANDLED_SCOPE and mk in lm]
        done.add(rid)
        rules.append({"id": rid, "desc": desc, "op": "edge_length", "base": base,
                      "kind": k, "lo": lo, "hi": hi, "extended": True,
                      "covers": [], "holes": [], "arg": [], "value": "", "mode": "",
                      "scope": scope, "line": defline, "selections": [],
                      "arg_selections": [], "arg_covers": [], "arg_holes": [],
                      "area_op": ">=", "behavioral_only": False})
    # PRESENCE rules: `var = <region with NO dimensional op>; var.output(...)`. The
    # region itself is the violation (a cross). Subject base + AND/NOT + selections.
    done = {r["id"] for r in rules}
    skip["presence"] = 0
    for i, line in enumerate(lines):
        mo = re.match(r'\s*(\w+)\.output\("([^"]*)",\s*"([^"]*)"\)', line)
        if not mo:
            continue
        var, rid, desc = mo.group(1), mo.group(2), mo.group(3)
        if rid in done:
            continue
        defn = defline = None
        for j in range(i - 1, -1, -1):                  # nearest assignment above
            dm = re.match(rf"\s*{re.escape(var)}\s*=\s*([^=].*)", lines[j])
            if dm:
                defn, defline = dm.group(1).strip(), lines[j]
                break
        if defn is None or _OP.search(defn) or ".drc(" in defn:
            continue                                    # dimensional -> handled above
        region = expand_base(re.split(r"\.(?:output|forget|polygons)\(", defn)[0].strip(), _VARDEFS)
        p = parse_complex_subject(region)
        if p is None:
            skip["complex_subj"] += 1
            continue
        pbase, pands, pnots, psels = p
        psels = [d for o, y in psels for d in _decompose_sel(o, y)]
        if any(not _drawable_marker(o, y) for o, y in psels):
            skip["complex_subj"] += 1
            continue
        sl = []
        for _, y in psels:
            sl += _marker_deep_layers(y)
        if any(l not in lm for l in [pbase] + pands + pnots + sl):
            skip["nonbase"] += 1
            continue
        done.add(rid)
        rules.append({"id": rid, "desc": desc, "op": "presence", "base": pbase,
                      "covers": sorted(pands), "holes": sorted(pnots), "arg": [],
                      "value": "", "mode": "", "scope": [], "line": defline,
                      "selections": psels, "arg_selections": [], "arg_covers": [],
                      "arg_holes": [], "area_op": ">=", "behavioral_only": False})
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


def _cut_box(host_edges, idx, n):
    """Box for cutout `idx` of `n` placed INSIDE a host shape's bbox, distributed
    along the host's LONGER axis in an off-centre band (so it's a removal off the
    device point) and small (0.6) so any count fits -- a fixed +1-per-cutout offset
    overflowed the narrow (4-wide) separation/space subject once n >= 4."""
    xs = [e["start"][0] for e in host_edges]
    ys = [e["start"][1] for e in host_edges]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if w >= h:                                   # wide host -> spread along x, low band
        cx, cy = x0 + w * (idx + 1) / (n + 1), y0 + h * 0.25
    else:                                        # tall host -> spread along y, left band
        cx, cy = x0 + w * 0.25, y0 + h * (idx + 1) / (n + 1)
    return (cx - 0.3, cy - 0.3, cx + 0.3, cy + 0.3)


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
    if op == "presence":
        # the region IS the violation (a cross). Subject A + an intra-A CROSSED
        # measure -> the rule outputs dl(A) (A plus its selections/cutouts). The
        # selection/cover/hole loops below draw the contexts as usual.
        shapes = [_rect("A", 0, 0, 10, 4, "a")]
        meas = [{"name": "X", "from": "a0", "to": "a1", "crossed": True}]
        cons = [_match(f"A.layer == {base}")]
    elif op == "edge_width":
        # gate WIDTH: poly A. The GATE active layer(s) are drawn CROSSING A (a partial
        # overlap) so the backend recovers them as the gate; mask FILTERS are drawn
        # CONTAINING A (shared cover loop -> relation inside); cutouts are removed
        # (shared hole loop). edge_dl reads the three apart from the geometry. The width
        # measure carries edges:True; a1/a3 are straight (a real measured distance).
        shapes = [_rect("A", 0, 0, 10, 4, "a")]
        for e in shapes[0]["edges"]:
            if e["id"] in ("a1", "a3"):
                e["edge"] = True
        meas = [{"name": "W", "from": "a1", "to": "a3", "edges": True}]
        cons = [_match(f"A.layer == {base}"), _check(f"W >= {v}")]
        for gi, gl in enumerate(r.get("gate", [])):        # active region CROSSING A (overlap)
            shapes.append(_rect(f"G{gi}", 3 - gi, -2 - gi, 7 + gi, 6 + gi, f"g{gi}"))
            cons.append(_match(f"G{gi}.layer == {gl}"))     # crosses A's centre -> covers its
            #   device point with relation 'overlap' (not 'inside') -> recovered as the gate
    elif op == "edge_length":
        # min/max SIZE: the EDGE a0's length is the violation. A length constraint
        # (!= for without_length, == / range for with_length) -> the backend emits
        # base.edges.(with|without)_length(...). .extended renders edges as polygons.
        shapes = [_rect("A", 0, 0, 10, 4, "a")]
        meas = []
        lo, hi, k = r["lo"], r["hi"], r["kind"]
        if k == "without":
            cexpr = f"a0.length != {lo}"
        elif lo == hi:
            cexpr = f"a0.length == {lo}"
        else:
            cexpr = " and ".join(c for c in (f"a0.length >= {lo}" if lo else None,
                                             f"a0.length <= {hi}" if hi else None) if c)
        cons = [_match(f"A.layer == {base}"), _check(cexpr)]
    elif op == "with_area":
        shapes = [_rect("A", 0, 0, 10, 4, "a")]
        meas = []
        cons = [_match(f"A.layer == {base}"), _check(f"A.area {r.get('area_op', '>=')} {v}")]
    elif op == "width":
        shapes = [_rect("A", 0, 0, 10, 4, "a")]
        meas = [{"name": "W", "from": "a1", "to": "a3"}]
        cons = [_match(f"A.layer == {base}"), _check(f"W >= {v}")]
    elif op == "space":
        shapes = [_rect("A", 0, 0, 4, 10, "a"), _rect("B", 6, 0, 10, 10, "b")]
        meas = [{"name": "S", "from": "a1", "to": "b3"}]
        cons = [_match(f"A.layer == {base}"), _match(f"B.layer == {base}"), _check(f"S >= {v}")]
    elif op == "enclosing":
        # inner arg B near A's TOP-RIGHT corner (not centred): a derived-arg covering
        # over B never reaches A's interior point (no entanglement), and B stays clear
        # of the scope/cover shapes that sit near the origin.
        shapes = [_rect("A", 0, 0, 20, 12, "a"), _rect("B", 14, 7, 18, 11, "b")]
        meas = [{"name": "E", "from": "a1", "to": "b1"}]
        cons = [_match(f"A.layer == {base}"), _match(f"B.layer == {r['arg'][0]}"), _check(f"E >= {v}")]
    else:  # separation
        shapes = [_rect("A", 0, 0, 4, 10, "a"), _rect("B", 6, 0, 10, 10, "b")]
        meas = [{"name": "S", "from": "a1", "to": "b3"}]
        cons = [_match(f"A.layer == {base}"), _match(f"B.layer == {r['arg'][0]}"), _check(f"S >= {v}")]
    for mz in meas:                     # the metric (euclidian / square) is part of the measure
        mz["metric"] = r.get("mode") or "euclidian"
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
    for si, ss in enumerate(subj):
        for k, hl in enumerate(holes):
            nm = f"X{k}{si}"
            cut = _rect(nm, *_cut_box(ss["edges"], k, len(holes)), nm.lower())
            cut["edges"][0]["edge"] = True          # the cutting boundary is real
            cut_straight.add(cut["edges"][0]["id"])
            shapes.append(cut)
            cons.append(_match(f"{nm}.layer == {hl}"))
    for k, cl in enumerate(r["covers"]):
        shapes.append(_rect(f"C{k}", bx0 - 1 - k, by0 - 1 - k, bx1 + 1 + k, by1 + 1 + k, f"c{k}"))
        cons.append(_match(f"C{k}.layer == {cl}"))
    # AND/NOT-derived separation ARG: covering / cutout over B (= shapes[1]), placed
    # to cover/cut B but stay clear of the subject A (which is at x in [0,4]).
    if r.get("arg_covers") or r.get("arg_holes"):
        bb = shapes[1]["edges"]
        ax0 = min(e["start"][0] for e in bb); ay0 = min(e["start"][1] for e in bb)
        ax1 = max(e["start"][0] for e in bb); ay1 = max(e["start"][1] for e in bb)
        for k, cl in enumerate(r["arg_covers"]):
            shapes.append(_rect(f"AC{k}", ax0 - 1 - k, ay0 - 1 - k, ax1 + 1 + k, ay1 + 1 + k, f"ac{k}"))
            cons.append(_match(f"AC{k}.layer == {cl}"))
        for k, hl in enumerate(r["arg_holes"]):
            cut = _rect(f"AX{k}", *_cut_box(bb, k, len(r["arg_holes"])), f"ax{k}")
            cut["edges"][0]["edge"] = True
            cut_straight.add(cut["edges"][0]["id"])
            shapes.append(cut)
            cons.append(_match(f"AX{k}.layer == {hl}"))
    for k, (so, mk) in enumerate(r["scope"]):
        shapes.append(_rect(f"M{k}", *_marker_pos(so, k), f"m{k}"))
        cons.append(_match(f"M{k}.layer == {mk}"))
    # selection CONTEXTS S{k}: the subject is selected by how it relates to each
    # (inside/outside/interacting). A WAVY measure subject->S{k} (>= 0) makes it a
    # topological SELECTION, not a covering or scope marker -- so ours emits the
    # deck's selection op. Applied to EACH subject shape (A, plus B for space, so
    # both same-layer operands get the selection -> stays space, not separation).
    # Layers may coincide with an operand -- that signal is meaningful, not a dup.
    sreg = [(o, y) for o, y in r.get("selections", []) if o != "not_interacting"]
    sni = [(o, y) for o, y in r.get("selections", []) if o == "not_interacting"]
    for k, (sel_op, y) in enumerate(sreg):
        # the EXACT drawn relation picks the op: touch (coinciding edge at A's right
        # edge bx1) -> interacting; overlap -> overlapping (== not_outside); contains
        # -> inside; beside/disjoint -> outside.
        # interacting markers TOUCH a different edge of A per k (right/top/left/bottom)
        # so several SAME-LAYER interacting markers don't merge into one polygon.
        touch = [(bx1, by0, bx1 + 6, by1), (bx0, by1, bx1, by1 + 6),
                 (bx0 - 6, by0, bx0, by1), (bx0, by0 - 6, bx1, by0)][k % 4]
        box = {"inside": (-5 - 3 * k, -5 - 3 * k, 30 + 3 * k, 30 + 3 * k),  # contains A
               "outside": (14 + 8 * k, 0, 20 + 8 * k, 4),                  # beside, disjoint
               "interacting": touch,                                       # touch a perimeter edge
               "overlapping": (-3 - 2 * k, -3 - 2 * k, 5, 5),              # overlaps A
               "not_outside": (-3 - 2 * k, -3 - 2 * k, 5, 5)}[sel_op]      # == overlapping
        skshape = _rect(f"S{k}", *box, f"s{k}")
        shapes.append(skshape)
        # a NESTED marker (metal2.interacting(fusetop)) is drawn as its own device:
        # base layer S{k}, with its OWN doping (wholly-covering AND layers), OWN
        # cutouts (NOT layers), and OWN selection contexts FS{k}_j (wavy measure
        # S{k}->FS{k}_j). The backend reads `mbase.and(dope).not(cut).subop(ctx)`.
        mp = parse_complex_subject(y)
        mbase, mands, mnots, msels = mp if mp else (y, [], [], [])
        cons.append(_match(f"S{k}.layer == {mbase}"))
        for j, pfx in enumerate(["a", "b"] if op == "space" else ["a"]):
            sel_meas.append({"name": f"GS{k}_{j}", "from": f"{pfx}0", "to": f"s{k}0"})
            cons.append(_match(f"GS{k}_{j} >= 0"))
        sx0, sy0, sx1, sy1 = box
        for j, cl in enumerate(mands):                   # marker's OWN doping wholly covers S{k}
            shapes.append(_rect(f"SD{k}_{j}", sx0 - 1 - j, sy0 - 1 - j, sx1 + 1 + j, sy1 + 1 + j, f"sd{k}_{j}"))
            cons.append(_match(f"SD{k}_{j}.layer == {cl}"))
        for j, nl in enumerate(mnots):                   # marker's OWN cutout -> .not(layer)
            cut = _rect(f"SX{k}_{j}", *_cut_box(skshape["edges"], j, len(mnots)), f"sx{k}_{j}")
            cut["edges"][0]["edge"] = True
            cut_straight.add(cut["edges"][0]["id"])
            shapes.append(cut)
            cons.append(_match(f"SX{k}_{j}.layer == {nl}"))
        for j, (subop, subm) in enumerate(msels):        # marker's OWN selection context FS{k}_{j}
            cbox = {"inside": (sx0 - 2 - j, sy0 - 2 - j, sx1 + 2 + j, sy1 + 2 + j),  # contains S{k}
                    "outside": (sx1 + 6 + 5 * j, sy0, sx1 + 10 + 5 * j, sy1),       # beside S{k}
                    "interacting": (sx1, sy0, sx1 + 4 + 2 * j, sy1),                # touch S{k}'s right
                    "overlapping": (sx0 + 1, sy0 + 1, sx1 + 2 + j, sy1 + 2 + j),    # overlaps S{k}
                    "not_outside": (sx0 + 1, sy0 + 1, sx1 + 2 + j, sy1 + 2 + j)}[subop]
            sp = parse_complex_subject(subm)
            cons.append(_match(f"FS{k}_{j}.layer == {sp[0] if sp else subm}"))
            shapes.append(_rect(f"FS{k}_{j}", *cbox, f"fs{k}_{j}"))
            sel_meas.append({"name": f"GF{k}_{j}", "from": f"s{k}0", "to": f"fs{k}_{j}0"})
            cons.append(_match(f"GF{k}_{j} >= 0"))
    # subject NOT_INTERACTING: two arrangements of the subject vs a marker, FAR away
    # (clear of the subject's regular contexts) -- measured A disjoint (flagged), a
    # CONTRAST (same layer + doping) TOUCHING the marker (not flagged). Derived marker
    # drawn as overlapping shapes. Mirrors the arg not_interacting path.
    sbjlayers = {base} | set(r.get("covers", []))
    for gi, (o, marker) in enumerate(sni):
        gx, gy = 60, 40 + gi * 14
        ml = _marker_deep_layers(marker)   # base layers of a (derived/var) marker
        ctx = next((m for m in ml if m not in sbjlayers), ml[0])
        helpers = [m for m in ml if m != ctx]
        shapes.append(_rect(f"SP{gi}", gx, gy + 3, gx + 4, gy + 7, f"sp{gi}"))
        cons.append(_match(f"SP{gi}.layer == {ctx}"))
        for j, h in enumerate(helpers):
            shapes.append(_rect(f"SH{gi}{j}", gx + 1, gy, gx + 3, gy + 10, f"sh{gi}{j}"))
            cons.append(_match(f"SH{gi}{j}.layer == {h}"))
        bc = _rect(f"SB{gi}", gx + 4, gy + 3, gx + 8, gy + 7, f"sbn{gi}")
        bc["flagged"] = False
        shapes.append(bc); cons.append(_match(f"SB{gi}.layer == {base}"))
        for j, cl in enumerate(r.get("covers", [])):
            shapes.append(_rect(f"SC{gi}{j}", gx + 4, gy + 2, gx + 9, gy + 8, f"scn{gi}{j}"))
            cons.append(_match(f"SC{gi}{j}.layer == {cl}"))
        sel_meas.append({"name": f"SNa{gi}", "from": "a0", "to": f"sp{gi}0"})
        cons.append(_match(f"SNa{gi} >= 0"))
        sel_meas.append({"name": f"SNb{gi}", "from": f"sbn{gi}0", "to": f"sp{gi}1"})
        cons.append(_match(f"SNb{gi} >= 0"))
    # arg selections (separation/enclosing): the ARG shape B is selected by how it
    # relates to its context T{k} -> ours emits separation(arg.outside(Y)) etc.
    # Contexts sit to the RIGHT/ABOVE B (6,0,10,10), clear of subject A (x in [0,4]).
    reg = [(o, y) for o, y in r.get("arg_selections", []) if o != "not_interacting"]
    ni = [(o, y) for o, y in r.get("arg_selections", []) if o == "not_interacting"]
    if reg:                                              # contexts are RELATIVE to B's bbox
        eb = shapes[1]["edges"]
        ebx0 = min(e["start"][0] for e in eb); eby0 = min(e["start"][1] for e in eb)
        ebx1 = max(e["start"][0] for e in eb); eby1 = max(e["start"][1] for e in eb)
    for k, (sel_op, y) in enumerate(reg):
        # positions RELATIVE to B's bbox so they hold for separation (B clear of A)
        # AND enclosing (B in A's corner); B's selection is read via the wavy measure
        # to T. touch (coinciding edge at B's right edge) -> interacting; overlap ->
        # overlapping; disjoint-beside -> outside; containing box -> inside.
        box = {"inside": (ebx0 - 2 - k, eby0 - 2 - k, ebx1 + 2 + k, eby1 + 2 + k),  # contains B
               "outside": (ebx1 + 4 + 5 * k, eby0, ebx1 + 8 + 5 * k, eby1),        # beside B, disjoint
               "interacting": (ebx1, eby0, ebx1 + 4 + 2 * k, eby1),               # touch at B's right
               "overlapping": (ebx0 + 2, eby0 + 2, ebx1 + 3 + k, eby1 + 3 + k),   # overlaps B
               "not_outside": (ebx0 + 2, eby0 + 2, ebx1 + 3 + k, eby1 + 3 + k)}[sel_op]
        shapes.append(_rect(f"T{k}", *box, f"t{k}"))
        cons.append(_match(f"T{k}.layer == {y}"))
        sel_meas.append({"name": f"GT{k}", "from": "b2", "to": f"t{k}0"})
        cons.append(_match(f"GT{k} >= 0"))
    # arg NOT_INTERACTING (select-first): two arrangements of the arg vs a marker,
    # FAR right (clear of subject/measured-arg). The measured arg B (shapes[1]) is
    # DISJOINT (flagged); a CONTRAST arrangement (same layer + same doping) TOUCHES
    # the marker (not-flagged). A DERIVED marker (tgate = poly2 & comp) is drawn as
    # overlapping shapes: context P = a marker layer NOT shared with the arg (poly2);
    # the shared one (comp) is an overlapping helper the compiler ANDs back in.
    arglayers = ({r["arg"][0]} | set(r.get("arg_covers", []))) if ni else set()
    for gi, (o, marker) in enumerate(ni):
        gx, gy = 60, gi * 14                             # FAR right: clear of the subject's
        #   inside-context boxes (which reach ~30+3k) so they don't merge into the marker
        ml = _marker_deep_layers(marker)   # base layers of a (derived/var) marker
        ctx = next((m for m in ml if m not in arglayers), ml[0])
        helpers = [m for m in ml if m != ctx]
        shapes.append(_rect(f"GP{gi}", gx, gy + 3, gx + 4, gy + 7, f"gp{gi}"))   # context (poly2)
        cons.append(_match(f"GP{gi}.layer == {ctx}"))
        for j, h in enumerate(helpers):                  # overlapping helper(s) -> derived marker
            shapes.append(_rect(f"GH{gi}{j}", gx + 1, gy, gx + 3, gy + 10, f"gh{gi}{j}"))
            cons.append(_match(f"GH{gi}{j}.layer == {h}"))
        bc = _rect(f"GB{gi}", gx + 4, gy + 3, gx + 8, gy + 7, f"gb{gi}")         # contrast, TOUCH P
        bc["flagged"] = False
        shapes.append(bc); cons.append(_match(f"GB{gi}.layer == {r['arg'][0]}"))
        for j, cl in enumerate(r.get("arg_covers", [])):  # the contrast's doping (-> ncomp)
            shapes.append(_rect(f"GC{gi}{j}", gx + 4, gy + 2, gx + 9, gy + 8, f"gc{gi}{j}"))
            cons.append(_match(f"GC{gi}{j}.layer == {cl}"))
        sel_meas.append({"name": f"GNa{gi}", "from": "b0", "to": f"gp{gi}0"})    # measured arg -> marker
        cons.append(_match(f"GNa{gi} >= 0"))
        sel_meas.append({"name": f"GNb{gi}", "from": f"gb{gi}0", "to": f"gp{gi}1"})  # contrast -> marker
        cons.append(_match(f"GNb{gi} >= 0"))
    # only the MEASURED (dimensional) edges are straight (real, oriented); every
    # other edge is wavy = a don't-care boundary the rule doesn't constrain. A min-
    # area shape has NO measured edge (only the total area matters), so all its
    # edges are wavy -- the rect stands for "any polygon", and area is still defined.
    straight = {mz["from"] for mz in meas} | {mz["to"] for mz in meas} | cut_straight
    for s in shapes:
        for e in s["edges"]:
            e["edge"] = e["id"] in straight
    out = {"name": r["id"], "desc": r["desc"], "shapes": shapes,
           "measures": meas + sel_meas, "constraints": cons}
    if r.get("extended"):                  # edge-size: render flagged edges as thin polygons
        out["extended"] = r["extended"]
    return out


def _match(expr):
    return {"kind": "match", "expr": expr}


def _check(expr):
    return {"kind": "check", "expr": expr}


def our_line(drc):
    lines = drc.splitlines()
    for line in lines:
        if _OP.search(line):
            return line
    # PRESENCE rule: no dimensional op -> the result-var assignment that's output
    out = next((l for l in lines if ".output(" in l), "")
    var = out.split(".output")[0].strip()
    return next((l for l in lines if re.match(rf"\s*{re.escape(var)}\s*=", l)), "")


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
