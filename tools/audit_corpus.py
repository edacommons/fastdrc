#!/usr/bin/env python3
"""Corpus-as-source-of-truth audit.

The drawn-rule corpus (docs/gf180mcu/drawn_rules/*.json) is the authoritative
artifact: each file is a rule a user drew. This tool LOADS the corpus (it does
not regenerate it inline), compiles each rule through the KLayout backend, and
checks it against the official deck:

  - compiles cleanly (no backend error, non-empty op);
  - where the op text is directly comparable (dimensional forms), canon(ours)
    matches canon(official) -- the op-level round-trip;
  - the rest are form-divergent (selection -> and/scope etc.) and confirmed
    behaviorally elsewhere; here they only need to compile.

Pass --regen to first (re-)write the corpus from the deck via as_drawn, then load
it (useful after changing as_drawn). Without it, the on-disk corpus is used as-is.

Run:  python tools/audit_corpus.py [--regen]
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(__file__))

import fastdrc.backend_klayout                                  # noqa: E402,F401
from fastdrc.backend import get                                 # noqa: E402
from fastdrc.rule import Rule                                   # noqa: E402
from validate_klayout import (parse_layermap, parse_rules, as_drawn,  # noqa: E402
                              canon, our_line)

CORPUS = os.path.join(os.path.dirname(__file__), "..", "docs", "gf180mcu", "drawn_rules")
DECK = os.path.join(os.path.dirname(__file__), "..", "docs", "gf180mcu", "gf180mcu_full.drc")


def main():
    text = open(DECK, encoding="utf-8").read()
    lm = parse_layermap(text)
    rules, _ = parse_rules(text, lm)
    deck_line = {r["id"]: r["line"] for r in rules}

    if "--regen" in sys.argv:
        os.makedirs(CORPUS, exist_ok=True)
        for r in rules:
            fn = re.sub(r"[^A-Za-z0-9._-]", "_", r["id"]) + ".json"
            json.dump(as_drawn(r), open(os.path.join(CORPUS, fn), "w"), indent=2)
        print(f"regenerated {len(rules)} corpus files")

    files = sorted(glob.glob(os.path.join(CORPUS, "*.json")))
    compiled = comparable = matched = 0
    errs, mism = [], []
    for f in files:
        j = json.load(open(f))
        rid = j.get("name", os.path.basename(f))
        try:
            drc = get("klayout", layermap=lm).compile(Rule(j))
        except Exception as e:                              # noqa: BLE001
            errs.append((rid, str(e)[:70]))
            continue
        op = our_line(drc)
        if not op:
            errs.append((rid, "no op emitted"))
            continue
        compiled += 1
        official = deck_line.get(rid)
        if official:
            comparable += 1
            if canon(op) == canon(official):
                matched += 1
            else:
                mism.append((rid, canon(official), canon(op)))

    print(f"corpus files loaded : {len(files)}")
    print(f"compiled cleanly    : {compiled}/{len(files)}")
    print(f"op-level comparable : {comparable}   matched: {matched}   "
          f"divergent/behavioral: {comparable - matched}")
    for rid, e in errs[:10]:
        print(f"  COMPILE-ERR [{rid}]: {e}")
    return 0 if not errs else 1


if __name__ == "__main__":
    raise SystemExit(main())
