"""Generalized scope: the scope region can be any layer -- a drawn marker, a
computed region, or a deck-level (incremental) window applied to every rule.

This is the lever for incremental DRC: scope every rule to the changed region
inflated by the rule's own halo, so only edits' neighbourhoods are re-checked.
Maps to `.interacting(region)` in KLayout and to the engine's FDCZ zones.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_klayout       # noqa: E402,F401  (registers "klayout")
from fastdrc.backend import get      # noqa: E402
from fastdrc.rule import Rule         # noqa: E402

LM = {"metal1": (34, 0)}


def _rect(name, x0, y0, x1, y1, p):
    v = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    e = [{"id": f"{p}{k}", "start": list(v[k]), "end": list(v[(k + 1) % 4]),
          "edge": True} for k in range(4)]
    return {"name": name, "edges": e}


def width_rule():
    return {"name": "M1.1", "desc": "Min. Metal1 width : 0.23um",
            "shapes": [_rect("A", 0, 0, 10, 4, "a")],
            "measures": [{"name": "W", "from": "a1", "to": "a3"}],
            "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                            {"kind": "check", "expr": "W >= 0.23"}]}


def test_incremental_scope():
    # Restrict every rule to violations near a change, inflated by the rule halo.
    drc = get("klayout", layermap=LM,
              scope_all=("interacting", "changed.sized({value}.um)")).compile(Rule(width_rule()))
    assert "metal1.width(0.23.um, euclidian).polygons(0.001)" in drc
    assert ".interacting(changed.sized(0.23.um))" in drc       # per-rule halo = threshold


def test_custom_region_roi():
    # A computed scope region defined in the preamble, used as an ROI scope.
    drc = get("klayout", layermap=LM, regions={"roi": "polygons(100, 0)"},
              scope_all=("inside", "roi")).compile(Rule(width_rule()))
    assert "roi = polygons(100, 0)" in drc
    assert ".inside(roi)" in drc


def test_plain_unchanged():
    # No scope_all -> output identical to before (no extra scoping).
    drc = get("klayout", layermap=LM).compile(Rule(width_rule()))
    assert ".interacting(" not in drc and ".inside(" not in drc
    assert "metal1.width(0.23.um, euclidian)" in drc


if __name__ == "__main__":
    test_incremental_scope()
    test_custom_region_roi()
    test_plain_unchanged()
    print("OK — incremental per-rule-halo scope, custom-region (ROI) scope, plain unchanged")
    print("\n--- incremental .drc (every rule scoped to changed region) ---")
    print(get("klayout", layermap=LM,
              scope_all=("interacting", "changed.sized({value}.um)")).compile(Rule(width_rule())), end="")
