#!/usr/bin/env python3
"""Describe deck rules in plain English via the `docs` backend.

For each rule the validator can parse, build the as-drawn IR and run the docs
backend -- so you can read back what each rule *means* and confirm it's exactly
what it should do. Because the docs backend reads the same analyzed `Rule` as the
KLayout backend, the description and the `.drc` come from one analysis (a faithful
cross-check, not a re-statement of the deck text).

Run:  python tools/describe_rules.py [id-substring]   # filter, e.g. PL.2
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
sys.path.insert(0, os.path.dirname(__file__))

import fastdrc.backend_docs        # noqa: F401  (registers "docs")
import fastdrc.backend_klayout     # noqa: F401  (registers "klayout")
import validate_klayout as vk
from validate_klayout import parse_layermap, parse_rules, as_drawn, our_line
from fastdrc.backend import get
from fastdrc.rule import Rule

DECK = os.path.join(os.path.dirname(__file__), "..", "docs", "gf180mcu", "gf180mcu_full.drc")


def main():
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    text = open(DECK, encoding="utf-8").read()
    lm = parse_layermap(text)
    vk._VARDEFS = vk.parse_and_vars(text, lm)
    rules, _ = parse_rules(text, lm)
    docs = get("docs")
    n = 0
    for r in rules:
        if flt and flt not in r["id"]:
            continue
        rr = Rule(as_drawn(r))
        print(docs.compile(rr).rstrip())
        if flt:                              # when zoomed in, also show the KLayout op
            kl = our_line(get("klayout", layermap=lm, metric=r["mode"]).compile(rr))
            print(f"      .drc:  {kl.split('=', 1)[1].strip()}")
        print()
        n += 1
    print(f"# {n} rules described")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
