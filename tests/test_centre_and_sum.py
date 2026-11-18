"""An arrow from the CENTRE of a shape, and a side ADDED UP over what a shape holds."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from fastdrc.backend import get  # noqa: E402
from fastdrc.editor import model_from_json, model_to_json  # noqa: E402
from fastdrc.rule import Rule  # noqa: E402


def box(name, x0, y0, x1, y1, straight=""):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"name": name, "edges": [{"id": f"{name.lower()}_{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
                                     "edge": "trbl"[i] in straight} for i in range(4)]}


def rule(shapes, measures, *constraints):
    cons = [{"kind": "match", "expr": c[2:]} if c.startswith("m ") else {"kind": "check", "expr": c} for c in constraints]
    return {"name": "R.1", "desc": "R.1", "shapes": shapes, "measures": measures, "constraints": cons}


PITCH = rule([box("A", 0, 0, 8, 8), box("B", 16, 0, 24, 8)], [{"name": "P", "from": "A.centre", "to": "B.centre"}],
             "m A.layer == pad", "m B.layer == pad", "P >= 80")


def ring():
    r = box("R", 0, 0, 40, 30, straight="l")
    hole = [(4, 4), (36, 4), (36, 26), (4, 26)]
    r["hole_loops"] = [[{"id": f"hr_{i}", "start": list(hole[i]), "end": list(hole[(i + 1) % 4]), "edge": False} for i in range(4)]]
    return r


EDGE = rule([ring(), box("A", 16, 11, 24, 19)], [{"name": "D", "from": "A.centre", "to": "r_3"}],
            "m R.layer == seal", "m A.layer == pad", "D >= 90")
TOTAL = rule([box("D", 0, 0, 30, 20), box("G", 8, 2, 11, 18, "lr"), box("G2", 19, 2, 22, 18, "lr")], [],
             "m D.layer == comp", "m G.layer == gate", "m G2.layer == gate", "m g_1.length >= g_0.length",
             "Sum(g_1.length) >= 160 and Sum(g_1.length) <= 720")


def test_a_pitch_is_measured_from_centre_to_centre():
    r = Rule(PITCH)
    assert r.measures == {} and r.centre_bounds() == [("P", "A", "B", None, 80, None)]
    deck = get("klayout").compile(r)
    assert "r_1_l1 = centres_closer.call(pad, nil, 80)" in deck and "centres_closer = lambda do |a, b, least, per = 0, by = nil|" in deck
    assert "the centres of two pad must be at least 80 µm apart" in get("docs").compile(r)
    assert get("svg").compile(r).count("<circle") == 2


def test_from_a_centre_to_the_outer_edge_of_the_ring_around_it():
    r = Rule(EDGE)
    assert r.centre_bounds() == [("D", "A", "R", "r_3", 90, None)]
    assert "pad.interacting(pad.middle.not_inside(seal.hulls.sized(-89.999)))" in get("klayout").compile(r)
    said = get("docs").compile(r)
    assert "the centre of every pad must be at least 90 µm in from the outer edge of the seal around it" in said


def test_from_a_centre_to_a_shape_beside_it():
    one = rule([box("A", 0, 0, 8, 8), box("B", 16, 0, 24, 8, "l")], [{"name": "D", "from": "b_3", "to": "A.centre"}],
               "m A.layer == pad", "m B.layer == metal", "D >= 12")
    assert "pad.interacting(pad.middle.overlapping(metal.sized(11.999)))" in get("klayout").compile(Rule(one))


def test_a_pitch_that_grows_with_the_size_of_what_holds_each_one():
    one = rule([box("UA", 0, 0, 12, 12, "lr"), box("A", 3, 3, 9, 9), box("UB", 20, 0, 32, 12), box("B", 23, 3, 29, 9)],
               [{"name": "P", "from": "A.centre", "to": "B.centre"}, {"name": "U", "from": "ua_3", "to": "ua_1"}],
               "m UA.layer == ubm", "m A.layer == pad", "m UB.layer == ubm", "m B.layer == pad", "P >= 70 + 1.3 * U")
    r = Rule(one)
    assert r.centre_bounds() == [("P", "A", "B", None, 70, (1.3, "U", "UA"))]
    deck = get("klayout").compile(r)
    assert "centres_closer.call(pad, nil, 70, 1.3, ubm)" in deck and deck.count(".output(") == 1
    assert ("the pitch of two pad, centre to centre, must be at least 70 µm plus 1.3 times the size of the ubm it lies in"
            in get("docs").compile(r))
    made, why = get("cases").cases_of(one)
    assert why is None and [c["expect"] for c in made] == [True, False, True, False, True]
    own = rule([box("A", 0, 0, 8, 8, "lr"), box("B", 16, 0, 24, 8)],
               [{"name": "P", "from": "A.centre", "to": "B.centre"}, {"name": "W", "from": "a_3", "to": "a_1"}],
               "m A.layer == pad", "m B.layer == pad", "P >= 2 * W")
    assert "centres_closer.call(pad, nil, 0, 2)" in get("klayout").compile(Rule(own))      # by its own size


def test_a_side_added_up_over_what_a_shape_holds():
    r = Rule(TOTAL)
    assert r.sum_bounds() == [("D", "G", "g_1", 160, 720)]
    deck = get("klayout").compile(r)
    assert "side_sums.call(comp, gate, true, 160, 720)" in deck and "side_sums = lambda do" in deck
    assert ("the longer sides of all the gate in one comp must add up to at least 160 µm and at most 720 µm"
            in get("docs").compile(r))
    assert "Σ" in get("svg").compile(r)


def test_the_self_test_builds_cases_for_both():
    cases = get("cases")
    for data, flagged in ((PITCH, 3), (EDGE, 2), (TOTAL, 4)):
        made, why = cases.cases_of(data)
        assert why is None and sum(c["expect"] for c in made) == flagged


def test_a_centre_arrow_survives_the_editor():
    ids = iter(range(10 ** 6))
    back = model_to_json(*model_from_json(PITCH, lambda p: f"{p}_new{next(ids)}", lambda shapes: "Z"))
    assert back["measures"] == PITCH["measures"]
