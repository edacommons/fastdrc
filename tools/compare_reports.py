#!/usr/bin/env python3
"""Compare two KLayout DRC runs of ONE layout, rule by rule, by WHERE they flag it.

    tools/compare_reports.py OURS.lyrdb REFERENCE.lyrdb [MORE_REFERENCE.lyrdb ...]
                             [--tol 0.25] [--rename FROM=TO ...] [--ref-rename REGEX=TO ...]

The use: run the deck generated from your drawings and the foundry's own deck on the
same real layout (ideally one with errors in it), then see for every rule whether the
two flag the same places. Marker shapes differ between decks (edge pairs, polygons,
whole shapes), so a marker agrees when a marker of the same rule in the other report
lies within `--tol` µm of it.

`--rename _thin= --rename _thick=1` maps our rule names onto the reference's (a rule
drawn once with a value per column carries the column in its name).

A rule the reference deck does not have is listed as such, with what ours flags: on a
clean layout that should be nothing.

Exit status 1 if any rule differs.
"""
import argparse
import collections
import re
import sys

_ITEM = re.compile(r"<item>.*?<category>'?([^<']*)'?</category>.*?<values>(.*?)</values>", re.S)
_XY = re.compile(r"(-?[\d.]+(?:e-?\d+)?),(-?[\d.]+(?:e-?\d+)?)")


def load(path):
    """({rule: [bbox, ...]}, [declared rule names]) of a .lyrdb report."""
    text = open(path).read()
    boxes = collections.defaultdict(list)
    for cat, vals in _ITEM.findall(text):
        pts = [(float(a), float(b)) for a, b in _XY.findall(vals)]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            boxes[cat].append((min(xs), min(ys), max(xs), max(ys)))
    head = text.split("<cells>")[0]
    return boxes, re.findall(r"<category>\s*<name>([^<]*)</name>", head)


def near(b, others, tol):
    return any(b[0] <= c[2] + tol and c[0] <= b[2] + tol and b[1] <= c[3] + tol and c[1] <= b[3] + tol
               for c in others)


def compare(ours, ref, rules, tol):
    """[(rule, status, n_ref, n_ours, ref_only, ours_only)] -- status one of
    ``quiet`` (neither flags anything), ``same`` or ``differs``."""
    out = []
    for r in rules:
        o, u = ref.get(r, []), ours.get(r, [])
        only_o = [b for b in o if not near(b, u, tol)]
        only_u = [b for b in u if not near(b, o, tol)]
        status = "quiet" if not o and not u else "differs" if only_o or only_u else "same"
        out.append((r, status, len(o), len(u), only_o, only_u))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ours")
    ap.add_argument("reference", nargs="+")
    ap.add_argument("--tol", type=float, default=0.25, help="how close two markers must be, in µm")
    ap.add_argument("--rename", action="append", default=[], metavar="FROM=TO",
                    help="replace FROM by TO in our rule names")
    ap.add_argument("--ref-rename", action="append", default=[], metavar="REGEX=TO",
                    help="rewrite the reference's rule names (a regular expression), e.g. to "
                         "fold its sub-rules into one: '^(x\\.1)_[abc]$=\\1'")
    a = ap.parse_args(argv)

    def renamed(name):
        for pair in a.rename:
            frm, _, to = pair.partition("=")
            name = name.replace(frm, to)
        return name

    raw, names = load(a.ours)
    ours = collections.defaultdict(list)
    for k, v in raw.items():
        ours[renamed(k)] += v
    def ref_renamed(name):
        for pair in a.ref_rename:
            frm, _, to = pair.partition("=")
            name = re.sub(frm, to, name)
        return name

    ref, ref_names = collections.defaultdict(list), set()
    for path in a.reference:
        boxes, declared = load(path)
        ref_names |= {ref_renamed(n) for n in declared} | {ref_renamed(k) for k in boxes}
        for k, v in boxes.items():
            ref[ref_renamed(k)] += v
    rules = sorted({renamed(n) for n in names} | set(ours))
    tally = collections.Counter()
    for r, status, n_ref, n_ours, only_ref, only_ours in compare(ours, ref, rules, a.tol):
        if r not in ref_names:                 # the reference has no such rule: nothing to
            tally["unchecked"] += 1            # compare with -- but say what ours does
            print(f"{r:14} no such rule in the reference; ours flags {n_ours}")
            continue
        tally[status] += 1
        if status == "quiet":
            continue
        print(f"{r:14} {status:8} reference {n_ref:5}  ours {n_ours:5}"
              + (f"  reference only {len(only_ref)}, ours only {len(only_ours)}" if status == "differs" else ""))
        for label, bs in (("reference only", only_ref), ("ours only", only_ours)):
            for b in bs[:3]:
                print(f"{'':16}{label} at ({b[0]:.3f}, {b[1]:.3f}) .. ({b[2]:.3f}, {b[3]:.3f})")
    print(f"{len(rules)} rules: {tally['same']} flag the same places, {tally['differs']} differ, "
          f"{tally['quiet']} flag nothing in either run, {tally['unchecked']} are not in the reference")
    return 1 if tally["differs"] else 0


if __name__ == "__main__":
    sys.exit(main())
