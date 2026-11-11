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
    # disjoint + cross -> "must overlap" (the crossed relation is the rule's check)
    A = {"name": "A", "edges": _edges("a", 0, 0, 6, 6, set())}
    B = {"name": "B", "edges": _edges("b", 20, 20, 26, 26, set())}        # disjoint from A
    r = {"name": "X.1", "desc": "must overlap", "shapes": [A, B],
         "measures": [{"name": "X", "from": "a1", "to": "b3", "crossed": True}],
         "constraints": [{"kind": "match", "expr": "A.layer == contact"}, {"kind": "match", "expr": "B.layer == metal1"}]}
    # the violation is contact.outside(metal1), which still keeps a contact that only
    # touches -- so the requirement is a real overlap
    assert "contact must overlap metal1" in _desc(r)


def test_eol_construction():
    # END-OF-LINE: a drawn wire-end (narrow run, a straight cap opposite a wavy
    # 'continues' end) + the eol rule -> KLayout's cond/eol idiom (CO.6a shape);
    # docs describes it as an end-of-line edge.
    import fastdrc.backend_klayout  # noqa: F401
    # NO flag, NO explicit thresholds: a3 is the cap (straight, opposite the WAVY a1
    # 'continues' end); Weol/Slen are ordinary edge-length constraints recovered from
    # the drawn scene.
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 3, {0, 2, 3})}   # a1 wavy; a3 cap; a0/a2 sides
    B = {"name": "B", "edges": _edges("b", 1, 1, 2, 2, set())}        # contact inside the end
    r = {"name": "CO.6a", "desc": "EOL enclosure", "shapes": [A, B],
         "measures": [{"name": "R", "from": "a3", "to": "b3", "metric": "projection"}],
         "constraints": [{"kind": "match", "expr": "A.layer == metal1"},
                         {"kind": "match", "expr": "B.layer == contact"},
                         {"kind": "match", "expr": "a3.length <= 0.34"},   # = EOL width Weol (a filter)
                         {"kind": "match", "expr": "a0.length >= 0.24"},   # = min run length Slen (a filter)
                         {"kind": "check", "expr": "R >= 0.06"}]}
    drc = get("klayout", layermap={"metal1": (34, 0), "contact": (33, 0)}).compile(Rule(r))
    assert "metal1.drc(width <= 0.34.um).with_length(0.24.um, nil, both)" in drc
    assert (".edges.with_length(nil, 0.34.um).interacting(eol0_cond.first_edges)"
            ".interacting(eol0_cond.second_edges).not(eol0_cond.first_edges)"
            ".not(eol0_cond.second_edges)") in drc
    assert "eol0.enclosing(contact.edges, 0.06.um, projection).polygons(0.001)" in drc
    d = get("docs").compile(Rule(r))
    assert "end-of-line edge of a narrow metal1 wire" in d and "must enclose contact" in d
    # ONE rule, one sentence: the qualifying lengths and the measure are not restated
    # as rules of their own
    assert d.count("•") == 1


def test_halo_primitive():
    # a shape's signed HALO (a 'within X' margin, drawn as a dashed offset line) ->
    # .sized(halo.um); both backends render it from the same derivation.
    import fastdrc.backend_klayout  # noqa: F401
    A = {"name": "A", "edges": _edges("a", 0, 0, 4, 10, {1}), "halo": 0.429}
    B = {"name": "B", "edges": _edges("b", 6, 0, 10, 10, {3})}
    r = {"name": "H.1", "desc": "haloed sep", "shapes": [A, B],
         "measures": [{"name": "S", "from": "a1", "to": "b3"}],
         "constraints": [{"kind": "match", "expr": "A.layer == nwell"},
                         {"kind": "match", "expr": "B.layer == comp"},
                         {"kind": "check", "expr": "S >= 0.5"}]}
    drc = get("klayout", layermap={"nwell": (21, 0), "comp": (22, 0)}).compile(Rule(r))
    assert "nwell.sized(0.429.um).separation(comp" in drc
    assert "nwell grown by 0.429 µm must be at least 0.5 µm from comp" in get("docs").compile(Rule(r))
    A["halo"] = -0.18                          # negative halo = shrink
    assert "nwell.sized(-0.18.um)" in get("klayout", layermap={"nwell": (21, 0), "comp": (22, 0)}).compile(Rule(r))
    assert "nwell shrunk by 0.18 µm" in get("docs").compile(Rule(r))


def test_void_area_primitive():
    # min-VOID-area (NP.8b shape): a DONUT of `base` (outer loop + an inner
    # hole_loops void) measured by `A.holes.area` -> L.holes.with_area(...). The
    # `.holes` is recovered from the drawn void, the threshold from the constraint.
    import fastdrc.backend_klayout  # noqa: F401
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 10, set()),
         "hole_loops": [_edges("h", 3, 3, 7, 7, set())]}
    r = {"name": "NP.8b", "desc": "min void area", "shapes": [A], "measures": [],
         "constraints": [{"kind": "match", "expr": "A.layer == nplus"},
                         {"kind": "check", "expr": "A.holes.area >= 0.35"}]}
    drc = get("klayout", layermap={"nplus": (32, 0)}).compile(Rule(r))
    assert "nplus.holes.with_area(nil, 0.35.um)" in drc
    assert "every enclosed void (hole) in nplus must be at least 0.35 µm²" in _desc(r)
    # NO drawn void (no hole_loops) -> NOT a void rule: `.holes` is never invented.
    del A["hole_loops"]
    assert ".holes" not in get("klayout", layermap={"nplus": (32, 0)}).compile(Rule(r))


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
    assert "lies inside dualgate" in en and "touches or overlaps ncomp" in en


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


def test_cross_on_a_relation_is_the_check_and_the_rest_are_filters():
    # NW.6-class: "an nwell resistor (nwell inside res_mk) must not touch dnwell". Two
    # relations are drawn from the subject: a wavy `>= 0` one to res_mk (a FILTER: which
    # nwell) and a CROSSED one to dnwell (the CHECK: the forbidden relation). The cross
    # needs no `>= 0` constraint and no measure on the subject itself.
    import fastdrc.backend_klayout  # noqa: F401
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 4, set())}
    R = {"name": "R", "edges": _edges("r", -5, -5, 30, 30, set())}       # contains A
    D = {"name": "D", "edges": _edges("d", 0, 4, 10, 10, set())}         # touches A's top edge
    r = {"name": "NW.6", "desc": "nwell resistor outside dnwell", "shapes": [A, R, D],
         "measures": [{"name": "G", "from": "a0", "to": "r0"},
                      {"name": "X", "from": "a2", "to": "d0", "crossed": True}],
         "constraints": [{"kind": "match", "expr": "A.layer == nwell"},
                         {"kind": "match", "expr": "R.layer == res_mk"},
                         {"kind": "match", "expr": "D.layer == dnwell"},
                         {"kind": "match", "expr": "G >= 0"}]}
    lm = {"nwell": (21, 0), "res_mk": (110, 5), "dnwell": (12, 0)}
    drc = get("klayout", layermap=lm).compile(Rule(r))
    assert "nw_6_l1 = nwell.inside(res_mk).interacting(dnwell)" in drc
    assert "nwell that lies inside res_mk must not touch or overlap dnwell." in _desc(r)


def test_area_rule_states_its_scope():
    # DF.9_5V-class: a marker drawn over the subject with no measure scopes the rule;
    # the text must say so, as the KLayout line does.
    import fastdrc.backend_klayout  # noqa: F401
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 4, set())}
    M = {"name": "M", "edges": _edges("m", -2, -2, 4, 5, set())}         # overlaps A, off its centre
    r = {"name": "DF.9", "desc": "min area", "shapes": [A, M], "measures": [],
         "constraints": [{"kind": "match", "expr": "A.layer == comp"},
                         {"kind": "match", "expr": "M.layer == dualgate"},
                         {"kind": "check", "expr": "A.area >= 0.2025"}]}
    drc = get("klayout", layermap={"comp": (22, 0), "dualgate": (55, 0)}).compile(Rule(r))
    assert "comp.with_area(nil, 0.2025.um).overlapping(dualgate)" in drc
    assert "only where the geometry is overlapping dualgate" in _desc(r)


def test_spacing_between_differently_derived_same_base_layers_names_both():
    # SB.14a-class: both operands are poly2, but one is under nplus and the other under
    # pplus -- a separation of two DIFFERENT derived layers, not "two shapes of" one.
    import fastdrc.backend_klayout  # noqa: F401
    A = {"name": "A", "edges": _edges("a", 0, 0, 4, 10, {1})}
    B = {"name": "B", "edges": _edges("b", 6, 0, 10, 10, {3})}
    CA = {"name": "CA", "edges": _edges("ca", -1, -1, 5, 11, set())}
    CB = {"name": "CB", "edges": _edges("cb", 5.5, -1, 11, 11, set())}
    r = {"name": "SB.14a", "desc": "n-poly to p-poly", "shapes": [A, B, CA, CB],
         "measures": [{"name": "S", "from": "a1", "to": "b3"}],
         "constraints": [{"kind": "match", "expr": "A.layer == poly2"},
                         {"kind": "match", "expr": "B.layer == poly2"},
                         {"kind": "match", "expr": "CA.layer == nplus"},
                         {"kind": "match", "expr": "CB.layer == pplus"},
                         {"kind": "check", "expr": "S >= 0.56"}]}
    d = _desc(r)
    assert "poly2 combined with nplus must be at least 0.56 µm from poly2 combined with pplus" in d
