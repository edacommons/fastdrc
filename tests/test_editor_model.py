"""The editor's model <-> rule-JSON conversion (no window needed).

Opening a rule and saving it must not change what it means: every key the editor
has no control for is carried through untouched.
"""

import glob
import json
import os

import pytest

pytest.importorskip("tkinter")

import fastdrc.backend_docs      # noqa: E402,F401
import fastdrc.backend_klayout   # noqa: E402,F401
from fastdrc.backend import get  # noqa: E402
from fastdrc.editor import model_from_json, model_to_json  # noqa: E402
from fastdrc.rule import Rule    # noqa: E402

HERE = os.path.dirname(__file__)
CORPUS = os.path.join(HERE, "..", "docs", "gf180mcu", "drawn_rules")


def _roundtrip(rule):
    ids = iter(range(10 ** 6))
    nid = lambda p: f"{p}_new{next(ids)}"
    return model_to_json(*model_from_json(rule, nid, lambda shapes: "Z"))


def _edges(pfx, x0, y0, x1, y1, straight=()):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [{"id": f"{pfx}{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
             "edge": i in straight} for i in range(4)]


def test_everything_the_editor_does_not_model_survives_open_and_save():
    A = {"name": "A", "edges": _edges("a", 0, 0, 10, 4), "halo": 0.43,
         "hole_loops": [_edges("h", 3, 1, 7, 3)]}
    B = {"name": "B", "edges": _edges("b", 0, 4, 10, 10), "flagged": False}
    rule = {"name": "R.1", "desc": "d", "extended": True,
            "shapes": [A, B],
            "measures": [{"name": "X", "from": "a2", "to": "b0", "crossed": True},
                         {"name": "W", "from": "a1", "to": "a3", "metric": "projection",
                          "edges": True, "label_offset": 1.5, "label_side": 1}],
            "constraints": [{"kind": "match", "expr": "A.layer == nwell"},
                            {"kind": "check", "expr": "W >= 0.2"}]}
    assert _roundtrip(rule) == rule


def test_plain_rule_gains_no_keys():
    rule = json.load(open(os.path.join(HERE, "..", "rule1.json")))
    assert _roundtrip(rule) == rule


@pytest.mark.skipif(not os.path.isdir(CORPUS), reason="GF180 drawn-rule corpus not present")
def test_corpus_rules_compile_the_same_after_open_and_save():
    files = sorted(glob.glob(os.path.join(CORPUS, "*.json")))
    assert files
    for f in files:
        rule = json.load(open(f))
        if "or" in rule:                       # alternatives live in `meta`, kept verbatim
            assert _roundtrip(rule)["or"] == rule["or"]
            continue
        again = _roundtrip(rule)
        for backend in ("klayout", "docs"):
            assert get(backend).compile(Rule(again)) == get(backend).compile(Rule(rule)), f


def test_a_hole_shape_is_written_as_the_void_of_the_shape_it_lies_in():
    from fastdrc.editor import void_owner
    rule = {"shapes": [{"name": "A", "edges": _edges("a", 0, 0, 10, 10),
                        "hole_loops": [_edges("h", 3, 3, 7, 7)]}],
            "measures": [],
            "constraints": [{"kind": "match", "expr": "A.layer == nplus"},
                            {"kind": "check", "expr": "A.holes.area >= 0.35"}]}
    ids = iter(range(100))
    meta, shapes, measures, cons = model_from_json(rule, lambda p: f"{p}{next(ids)}", lambda s: "Z")
    ring, void = shapes
    assert void["cut"] and void_owner(void, shapes) is ring       # edited as its own shape
    assert model_to_json(meta, shapes, measures, cons) == rule
    # dragged out of the ring it is no longer anyone's void
    void["verts"] = [[x + 20, y] for x, y in void["verts"]]
    out = model_to_json(meta, shapes, measures, cons)
    assert "hole_loops" not in out["shapes"][0] and out["shapes"][1]["cut"] is True
    assert void_owner(void, shapes) is None


def test_sheet_fields_are_typed_as_one_line_each():
    from fastdrc.editor import (format_columns, format_legend, format_values, parse_columns,
                                parse_legend, parse_values)
    legend = {"pcomp": "comp and pplus", "gate": "poly2 and comp"}
    assert parse_legend("pcomp = comp and pplus; gate = poly2 and comp") == legend
    assert parse_legend(format_legend(legend)) == legend
    cols = {"3.3V": [["not_interacting", "v5_xtor"], ["not_interacting", "dualgate"]],
            "5V": [["overlapping", "dualgate"]]}
    assert parse_columns(format_columns(cols)) == cols
    assert parse_values("3.3V=0.22, 5V=0.3") == {"3.3V": 0.22, "5V": 0.3}
    assert parse_values(format_values({"3.3V": 0.22})) == {"3.3V": 0.22}


def test_a_sheet_survives_open_and_save():
    sheet = {"name": "S", "domains": {"5V": [["overlapping", "dualgate"]]}, "except": ["otp_mk"],
             "layers": {"gate": "poly2 and comp"},
             "shapes": [{"name": "A", "edges": _edges("a", 0, 0, 10, 4, {1, 3})}],
             "measures": [{"name": "W", "from": "a1", "to": "a3", "at": 2.0}],
             "constraints": [{"kind": "match", "expr": "A.layer == poly2"}],
             "rules": [{"name": "PL.1", "constraints": [{"kind": "check", "expr": "W >= ?"}],
                        "values": {"5V": 0.2}}]}
    assert _roundtrip(sheet) == sheet


def test_the_whole_layer_mark_survives_open_and_save():
    G = {"name": "G", "edges": _edges("g", 4, -2, 6, 8, {3}), "whole": True}
    D = {"name": "D", "edges": _edges("d", 0, 0, 10, 6)}
    rule = {"name": "R.2", "desc": "d", "shapes": [D, G], "measures": [],
            "constraints": [{"kind": "match", "expr": "G.layer == poly"}]}
    assert _roundtrip(rule) == rule
