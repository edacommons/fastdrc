#!/usr/bin/env python3
"""Differential validation of the KLayout backend against the GF180 deck.

For every deck rule: run OUR complete generated deck and the OFFICIAL deck
statement(s) through the KLayout binary on many layouts (the firing layout of
behavioral_validate, mutations of it around the rule's threshold, and random
ones), and compare the violation MARKERS by XOR -- geometry, not counts.

Stronger than behavioral_validate.py in four ways: it runs the whole generated deck
(its own layer declarations included), not one expression with the layers predefined;
it compares marker geometry; it uses many layouts per rule, clean ones as well as
violating ones; and it reports rules the official deck never fired on as unverified
instead of counting them.

Run:  python tools/differential_validate.py [--corpus | --dir DIR] [--tiles N] [rule-id ...]
  --corpus  compile docs/gf180mcu/drawn_rules/<id>.json instead of as_drawn(deck rule)
  --dir DIR compile the drawings in DIR (matched to deck rules by their "name").
            DIR/validate.json may say how to check rules the deck parser can't read
            and give realistic layouts for any rule (see spec_rule)
  SEEDS=file.json  extra hand-made firing layouts: {rule-id: [[[layer,x0,y0,x1,y1],..],..]}
Exit status 1 if any rule differs or errors.
"""
import glob
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path[:0] = [os.path.join(REPO, "python"), os.path.join(REPO, "tools")]
_argv, sys.argv = sys.argv, sys.argv[:1]
import validate_klayout as vk                      # noqa: E402
sys.argv = _argv
from behavioral_diff import KLAYOUT                # noqa: E402
from behavioral_validate import layout_for         # noqa: E402
from fastdrc.backend import get                    # noqa: E402
from fastdrc.rule import Rule                      # noqa: E402

PITCH = 1_000_000
TEXT = open(vk.DECK, encoding="utf-8").read()
LINES = TEXT.splitlines()
LM = vk.parse_layermap(TEXT)
RULES, _ = vk.parse_rules(TEXT, LM)
CORPUS = os.path.join(REPO, "docs", "gf180mcu", "drawn_rules")


def official_block(r):
    """(statements, result_var) exactly as the deck has them."""
    line = r["line"]
    pre = list(r.get("_pre") or [])                 # definitions the statement relies on
    if ".output(" not in line:
        return pre + [line], line.split("=", 1)[0].strip()
    var = line.split(".output", 1)[0].strip()
    i = next(k for k, l in enumerate(LINES) if l.strip() == line.strip())
    blk = []
    k = i - 1
    while k >= 0 and LINES[k].strip():
        s = LINES[k].strip()
        if not s.startswith("#") and not s.startswith("logger"):
            blk.append(s)
        k -= 1
    return pre + blk[::-1], var


def spec_rule(name, sp):
    """A rule record for an official statement the deck parser does not read, from a
    validation spec entry: {"official": output variable, "tag": text its output line
    contains (to pick one of several), "pre": [definitions], "like": {op, base, arg,
    value} (what kind of layout exercises it), "layouts": [[[layer, x0, y0, x1, y1]..]..]}."""
    var, tag = sp["official"], sp.get("tag")
    line = next(l for l in LINES if l.strip().startswith(var + ".output(") and (not tag or tag in l))
    like = sp.get("like") or {}
    return {"id": name, "op": like.get("op", "width"), "base": like.get("base", "comp"),
            "arg": [like["arg"]] if like.get("arg") else [], "value": str(like.get("value", "")),
            "mode": like.get("mode", "euclidian"), "covers": [], "holes": [], "scope": [],
            "selections": [], "arg_selections": [], "arg_covers": [], "arg_holes": [],
            "area_op": ">=", "void": False, "line": line,
            "_pre": sp.get("pre"), "_layouts": sp.get("layouts")}


def our_block(deck):
    """Our complete deck minus the report plumbing; returns (statements, var)."""
    out = ["__outs = []"]
    for l in deck.splitlines():
        s = l.strip()
        if not s or s.startswith("#") or s.endswith(".forget"):
            continue
        if ".output(" in s:
            # a rule may report several checks (even reusing a variable name): collect
            # each result where it is output, and compare their union
            out.append(f"__outs << __norm({s.split('.output', 1)[0].strip()})")
            continue
        out.append(s)
    return out, "__outs.inject { |a, b| a | b }"


def layers_of(*texts):
    names = set()
    for t in texts:
        for w in re.findall(r"[A-Za-z_]\w*", t):
            if w in LM:
                names.add(w)
            elif w in vk._VARDEFS:
                a, n = vk._VARDEFS[w]
                names |= {x for x in a | n if x in LM}
    return sorted(names)


def dims(r):
    v = 0.0
    for k in ("value", "lo", "hi", "weol", "slen"):
        try:
            v = max(v, float(r.get(k) or 0))
        except (TypeError, ValueError):
            pass
    if r["op"] == "or":
        v = max([v] + [float(x["value"] or 0) for x in r["variants"]])
    if r["op"] == "with_area" or (r["op"] == "or" and any(x["op"] == "with_area" for x in r["variants"])):
        v = math.sqrt(v) if v else 0
    vn = int(round(v * 1000)) or 300
    return vn, sorted({max(1, d) for d in (vn // 2, vn - 1, vn, vn + 1, vn + vn // 2, 2 * vn, 4 * vn)})


def seed_layout(r):
    try:
        s = layout_for(r, LM)
    except Exception:
        s = []
    return [list(x) for x in s]


def extra_seeds(r, vn):
    """Firing seeds for ops layout_for has no branch for."""
    op = r["op"]
    b = LM.get(r["base"])
    s = []
    if not b:
        return s
    if op == "edge_length":
        s.append([[*b, 0, 0, vn + 20, vn], [*b, 3000, 0, 3000 + vn, vn]])
    if op == "edge_width":
        g = LM[r["gate"][0]]
        w = max(1, vn // 2)
        s.append([[*b, 1000, -1000, 1000 + w, 3000], [*g, 0, 0, 3000, 2000]])
        s.append([[*b, 1000, -1000, 1000 + vn, 3000], [*g, 0, 0, 3000, 2000]])
    if op == "edge_eol":
        a = LM[r["arg"][0]]
        w = int(float(r["weol"]) * 1000) - 40
        e = max(1, int(float(r["value"]) * 1000) // 2)
        for ee in (e, 2 * e, 2 * e + 1, 4 * e):
            s.append([[*b, 0, 0, w, 3000], [*a, 60, 3000 - ee - 200, w - 60, 3000 - ee]])
    return s


def mutate(shapes, D, rng):
    s = [list(x) for x in shapes]
    if not s:
        return s
    if len(s) > 1 and rng.random() < 0.25:
        s.pop(rng.randrange(len(s)))
    for _ in range(rng.choice((1, 1, 2, 3))):
        x = rng.choice(s)
        d = rng.choice(D) * rng.choice((1, -1))
        how = rng.randrange(4)
        if how == 0:                       # translate
            ax = rng.randrange(2)
            x[2 + ax] += d
            x[4 + ax] += d
        elif how == 1:                     # move one edge
            x[2 + rng.randrange(4)] += d
        elif how == 2:                     # set one extent to an exact probe dim
            ax = rng.randrange(2)
            x[4 + ax] = x[2 + ax] + rng.choice(D)
        else:                              # duplicate at an exact probe offset
            ax = rng.randrange(2)
            y = list(x)
            off = (x[4 + ax] - x[2 + ax]) + rng.choice(D)
            y[2 + ax] += off
            y[4 + ax] += off
            s.append(y)
    return [x for x in s if x[4] > x[2] and x[5] > x[3]]


def rand_layout(layers, vn, D, rng):
    s = []
    W = 6 * vn
    for name in layers:
        if rng.random() < 0.2:
            continue
        for _ in range(rng.choice((1, 1, 2, 3))):
            if rng.random() < 0.35:        # big context box
                s.append([*LM[name], -W, -W, 2 * W, 2 * W])
                continue
            x0, y0 = rng.randrange(0, W), rng.randrange(0, W)
            s.append([*LM[name], x0, y0, x0 + rng.choice(D), y0 + rng.choice(D)])
    return s


def build_tiles(r, layers, ntiles, rng):
    vn, D = dims(r)
    seeds = [x for x in [seed_layout(r)] + extra_seeds(r, vn) if x]
    extra = list(r.get("_layouts") or [])           # realistic layouts given with the rule
    if os.environ.get("SEEDS"):
        extra += json.load(open(os.environ["SEEDS"])).get(r["id"], [])
    for lay in extra:
        seeds.insert(0, [[*LM[n], *c] for n, *c in lay])
    tiles = list(seeds)
    while len(tiles) < ntiles:
        if seeds and rng.random() < 0.65:
            tiles.append(mutate(rng.choice(seeds), D, rng))
        else:
            tiles.append(rand_layout(layers, vn, D, rng))
    return tiles


RUBY_NORM = r'''
def __norm(l)
  return l if l.polygons?
  return l.polygons(0.001) if l.edge_pairs?
  l.extended(0, 0, 0.001, 0.001)
end
'''

RUBY_TAIL = r'''
__a = __norm(__off).data.merged
__b = __norm(__our).data.merged
__x = __a ^ __b
tile = lambda { |reg| h = {}; reg.each { |p| c = p.bbox.center; h[[(c.x + %(half)d) / %(pitch)d, 0].max] = 1 }; h.keys.sort }
File.write("%(res)s", [tile.call(__a).join(","), tile.call(__b).join(","), tile.call(__x).join(","), __x.area.to_s].join("|"))
'''


def run_rule(r, drawn, ntiles):
    rid = r["id"]
    rng = random.Random(rid)
    try:
        be = get("klayout", layermap=LM, metric=r["mode"] or "euclidian")
        # a rule may be drawn in several parts under one name: they are one rule
        deck = be.compile_all(drawn) if isinstance(drawn, list) else be.compile(Rule(drawn))
    except Exception as e:                                           # noqa: BLE001
        return rid, "COMPILE-ERR", str(e)[:100]
    ours, ovar = our_block(deck)
    off, fvar = official_block(r)
    layers = layers_of("\n".join(off), "\n".join(ours))
    tiles = build_tiles(r, layers, ntiles, rng)
    tmp = tempfile.mkdtemp(prefix="fz_")
    gds, res, rb = (os.path.join(tmp, n) for n in ("t.gds", "res.txt", "s.drc"))
    L = ["ly = RBA::Layout.new", "ly.dbu = 0.001", 'top = ly.create_cell("TOP")']
    for t, shapes in enumerate(tiles):
        for (l, d, x0, y0, x1, y1) in shapes:
            ox = t * PITCH
            L.append(f"top.shapes(ly.layer({l}, {d})).insert(RBA::Box.new({x0 + ox}, {y0}, {x1 + ox}, {y1}))")
    for n in layers:                       # every referenced layer exists
        L.append(f"ly.layer({LM[n][0]}, {LM[n][1]})")
    L.append(f'ly.write("{gds}")')
    L.append(f'source("{gds}")')
    L += [f"{n} = polygons({l}, {d})" for n, (l, d) in LM.items()]
    L += [f"{v} = {vk._var_expansion(*ad)}" for v, ad in vk._VARDEFS.items()]
    L.append(RUBY_NORM)
    L += off + [f"__off = {fvar}"]
    L += ours + [f"__our = {ovar}"]
    L.append(RUBY_TAIL % {"half": PITCH // 2, "pitch": PITCH, "res": res})
    open(rb, "w").write("\n".join(L) + "\n")
    p = subprocess.run([KLAYOUT, "-b", "-r", rb], capture_output=True, text=True)
    if p.returncode or not os.path.exists(res):
        return rid, "RUN-ERR", (p.stderr or p.stdout).strip().splitlines()[-1][:200] if (p.stderr or p.stdout).strip() else "?"
    a, b, x, area = open(res).read().split("|")
    f = lambda s: [int(v) for v in s.split(",") if v]
    a, b, x = f(a), f(b), f(x)
    return rid, "OK" if not x else "DIFF", {
        "tiles": len(tiles), "off_fired": len(a), "our_fired": len(b),
        # layouts where one deck reports a violation and the other none -- the measure
        # that matters when the two formulate the same rule with different markers
        "verdict_differs": sorted(set(a) ^ set(b)),
        "diff_tiles": x, "xor_area": area, "dir": tmp,
        "off": off, "ours": ours}


def main():
    args = sys.argv[1:]
    corpus = "--corpus" in args
    ntiles = 60
    if "--tiles" in args:
        ntiles = int(args[args.index("--tiles") + 1])
    ids = [a for i, a in enumerate(args)
           if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--tiles", "--dir"))]
    jobs = []
    cj = {}
    cdir = CORPUS
    if "--dir" in args:                    # another folder of drawings, e.g. hand-drawn ones
        cdir, corpus = args[args.index("--dir") + 1], True
    spec = {}
    if corpus:
        from fastdrc.sheet import rules_of
        for f in sorted(glob.glob(os.path.join(cdir, "*.json"))):
            if os.path.basename(f) == "validate.json":   # how to check them, not a drawing
                spec = json.load(open(f)).get("rules", {})
                continue
            for j in rules_of(json.load(open(f))):      # a sheet holds many rules
                cj.setdefault(j.get("name", os.path.basename(f)), []).append(j)
    parsed = {r["id"]: r for r in RULES}
    names = list(parsed) if not corpus else [n for n in cj if n in parsed or n in spec]
    for name in names:
        if ids and name not in ids:
            continue
        if name in spec and spec[name].get("official"):  # a statement the parser can't read
            r = spec_rule(name, spec[name])
        elif name in spec:                               # just realistic layouts for it
            r = dict(parsed[name], _layouts=spec[name].get("layouts"))
        else:
            r = parsed[name]
        if corpus:
            jobs.append((r, cj[name] if len(cj[name]) > 1 else cj[name][0]))
        else:
            jobs.append((r, vk.as_drawn(r)))
    unchecked = sorted(n for n in cj if n not in parsed and n not in spec) if corpus else []
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda j: run_rule(j[0], j[1], ntiles), jobs))
    ok = [x for x in results if x[1] == "OK"]
    never = [x[0] for x in ok if x[2]["off_fired"] == 0]
    always = [x[0] for x in ok if x[2]["off_fired"] == x[2]["tiles"]]
    print(f"rules {len(results)} | identical markers {len(ok)} | "
          f"DIFF {sum(x[1] == 'DIFF' for x in results)} | "
          f"errors {sum(x[1].endswith('ERR') for x in results)}")
    print(f"of the identical: official never fired on {len(never)}: {never}")
    print(f"                  official fired on every tile for {len(always)}: {always}")
    if ok:
        print("fired tiles per rule (official): min %d  median %d  of %d" % (
            min(x[2]["off_fired"] for x in ok),
            sorted(x[2]["off_fired"] for x in ok)[len(ok) // 2], ntiles))
    for rid, st, d in results:
        if st == "OK":
            continue
        print(f"\n[{st}] {rid}")
        if isinstance(d, dict):
            print(f"   off fired {d['off_fired']}  ours fired {d['our_fired']}  differing tiles {d['diff_tiles'][:12]}  xor area {d['xor_area']}")
            vd = d["verdict_differs"]
            print(f"   same verdict (violation / clean) on {d['tiles'] - len(vd)} of {d['tiles']} layouts"
                  + (f"; differs on {vd[:12]}" if vd else " -- only the marker shapes differ"))
            print("   OFF :", " ; ".join(d["off"]))
            print("   OURS:", " ; ".join(d["ours"]))
            print("   dir :", d["dir"])
        else:
            print("  ", d)
    print("\nper rule:")
    for rid, st, d in results:
        if not isinstance(d, dict):
            print(f"  {rid:14} {st}")
            continue
        vd, n = d["verdict_differs"], d["tiles"]
        note = "  (official never fired: unverified)" if d["off_fired"] == 0 else ""
        print(f"  {rid:14} " + ("identical markers" if st == "OK" else
                                f"same verdict on {n - len(vd)} of {n} layouts") + note)
    if unchecked:
        print("not checked (no official statement known for them):", ", ".join(unchecked))
    return 1 if len(ok) != len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
