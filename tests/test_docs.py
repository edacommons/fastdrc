"""The docs backend turns a Rule into an English description from the same
recovery surface the KLayout backend uses. These pin the phrasing for the main
rule shapes so the description stays a faithful read-back, not a re-statement."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

import fastdrc.backend_docs   # noqa: E402,F401  (registers "docs")
from fastdrc.backend import get  # noqa: E402
from fastdrc.rule import Rule    # noqa: E402


def _edges(pfx, x0, y0, x1, y1, straight):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [{"id": f"{pfx}{i}", "start": pts[i], "end": pts[(i + 1) % 4], "edge": i in straight}
            for i in range(4)]


def _desc(rule):
    return get("docs").compile(Rule(rule))


def test_width_description():
    r = {"name": "M.1", "desc": "min width", "shapes": [{"name": "A", "edges": _edges("a", 0, 0, 10, 4, {1, 3})}],
         "measures": [{"name": "W", "from": "a1", "to": "a3"}],
         "constraints": [{"kind": "match", "expr": "A.layer == metal1"}, {"kind": "check", "expr": "W >= 0.28"}]}
    assert "metal1 must be at least 0.28 µm wide" in _desc(r)


def test_separation_description():
    A = {"name": "A", "edges": _edges("a", 0, 0, 4, 10, {1})}
    B = {"name": "B", "edges": _edges("b", 6, 0, 10, 10, {3})}
    r = {"name": "S.1", "desc": "sep", "shapes": [A, B],
         "measures": [{"name": "S", "from": "a1", "to": "b3"}],
         "constraints": [{"kind": "match", "expr": "A.layer == comp"}, {"kind": "match", "expr": "B.layer == poly2"},
                         {"kind": "check", "expr": "S >= 0.3"}]}
    assert "comp must be at least 0.3 µm from poly2" in _desc(r)


def test_presence_must_interact():
    # disjoint + cross -> "must interact" (the relation is the rule)
    A = {"name": "A", "edges": _edges("a", 0, 0, 6, 6, set())}
    B = {"name": "B", "edges": _edges("b", 20, 20, 26, 26, set())}        # disjoint from A
    r = {"name": "X.1", "desc": "must overlap", "shapes": [A, B],
         "measures": [{"name": "X", "from": "a1", "to": "b3", "crossed": True}],
         "constraints": [{"kind": "match", "expr": "A.layer == contact"}, {"kind": "match", "expr": "B.layer == metal1"}]}
    assert "contact must interact with (touch or overlap) metal1" in _desc(r)


def test_both_backends_render_one_shared_analysis():
    # The KLayout and docs backends are now thin FORMATTERS over the same
    # Rule.check_derivations -- so they can't semantically diverge. This pins that:
    # given one derivation, both renderers reference the SAME layers (catches the
    # MDN.5aii-class divergence where the docs invented an arg the op never had).
    import re
    import fastdrc.backend_klayout  # noqa: F401
    d = {"base": "comp", "ands": ["pplus"], "nots": ["nplus"], "selections": [
        ("inside", {"base": "dualgate", "ands": [], "nots": [], "selections": []}),
        ("interacting", {"base": "ncomp", "ands": [], "nots": [], "selections": []})]}
    lm = {k: (i, 0) for i, k in enumerate(["comp", "pplus", "nplus", "dualgate", "ncomp"])}
    klb = get("klayout", layermap=lm)
    klb._layers, klb._decls = {}, []          # _fmt_deriv interns layer vars (compile() does this)
    kl = klb._fmt_deriv(d)
    en = get("docs")._fmt_deriv_en(d)
    known = set(lm)
    layers = lambda s: sorted(w for w in re.findall(r"[a-z0-9_]+", s) if w in known)
    assert layers(kl) == layers(en) == ["comp", "dualgate", "ncomp", "nplus", "pplus"]
    assert kl == "comp.and(pplus).not(nplus).inside(dualgate).interacting(ncomp)"
    assert "combined with pplus" in en and "excluding nplus" in en
    assert "lies inside dualgate" in en and "touches ncomp" in en


def test_presence_must_not_overlap_with_selection():
    # LPW.12-class: lvpwell crossed while overlapping nwell -> "no lvpwell that overlaps nwell may exist"
    A = {"name": "A", "edges": _edges("a", 0, 0, 6, 6, set())}
    B = {"name": "B", "edges": _edges("b", 3, 3, 9, 9, set())}            # overlaps A
    r = {"name": "LPW.12", "desc": "no overlap", "shapes": [A, B],
         "measures": [{"name": "G", "from": "a1", "to": "b3"},            # wavy selection (overlap)
                      {"name": "X", "from": "a0", "to": "a2", "crossed": True}],
         "constraints": [{"kind": "match", "expr": "A.layer == lvpwell"}, {"kind": "match", "expr": "B.layer == nwell"},
                         {"kind": "match", "expr": "G >= 0"}]}
    d = _desc(r)
    assert "lvpwell that overlaps nwell" in d and "may exist" in d
