"""Regression tests for the `as_drawn` corpus generator (tools/validate_klayout.py).

as_drawn turns a parsed deck rule into the geometry-by-example IR a user would
draw. These guard the drawing GEOMETRY (not the backend) -- specifically that
subtracted (`.not`) layers are drawn as cutouts that always fit INSIDE the host
shape, regardless of how many there are.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_klayout  # noqa: E402,F401  (registers "klayout")
from fastdrc.backend import get  # noqa: E402
from fastdrc.rule import Rule    # noqa: E402
from validate_klayout import as_drawn, our_line  # noqa: E402

LM = {"poly2": (30, 0), "comp": (22, 0),
      **{f"m{i}": (100 + i, 0) for i in range(1, 9)}}


def _sep_rule(n):
    hs = [f"m{i}" for i in range(1, n + 1)]
    return {"id": "T", "desc": "t", "op": "separation", "base": "poly2",
            "covers": [], "holes": hs, "arg": ["comp"], "arg_covers": [],
            "arg_holes": hs, "value": "0.1", "mode": "euclidian", "scope": [],
            "selections": [], "arg_selections": [], "area_op": ">="}


def _op(r):
    return our_line(get("klayout", layermap=LM).compile(Rule(as_drawn(r)))).split("=", 1)[1].strip()


def test_many_subtractions_dont_leak_to_scope():
    # the narrow (4-wide) separation subject once overflowed with >= 4 cutouts at
    # fixed +1 offsets -- the overflowing one wasn't `inside`, so it leaked into a
    # scope op instead of a `.not`. Cutouts now distribute along the longer axis.
    for n in (2, 3, 4, 6, 8):
        op = _op(_sep_rule(n))
        nots = "".join(f".not(m{i})" for i in range(1, n + 1))
        assert op == f"poly2{nots}.separation(comp{nots}, 0.1.um, euclidian).polygons(0.001)", \
            f"n={n}: {op}"
        assert ".interacting(" not in op and ".overlapping(" not in op  # no scope leak
