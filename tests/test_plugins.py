"""Backends are plugins; the `cases` backend makes test cases from the drawings."""
import json
import os

import pytest

from fastdrc import backend, process
from fastdrc.backend import Backend, get
from fastdrc.rule import Rule

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "..", "examples", "demo")
M = lambda e: {"kind": "match", "expr": e}     # noqa: E731
C = lambda e: {"kind": "check", "expr": e}     # noqa: E731


def _rect(name, x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"name": name, "edges": [{"id": f"{name.lower()}{i}", "start": list(pts[i]),
                                     "end": list(pts[(i + 1) % 4]), "edge": False} for i in range(4)]}


def test_the_shipped_backends_load_by_themselves():
    assert {"klayout", "docs", "facts", "cases", "svg"} <= set(backend.available())
    assert get("docs", layermap={"a": (1, 0)}, title="x").name == "docs"     # offers it does not take are dropped


def test_another_package_can_add_a_backend(monkeypatch):
    class Shout(Backend):
        name, extension, about = "shout", ".shout.txt", "rule names, loudly"

        def compile(self, rule):
            return rule.name.upper()

    class EntryPoint:
        name = "shout"

        @staticmethod
        def load():
            return Shout

    import importlib.metadata
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group=None: [EntryPoint] if group == "fastdrc.backends" else [])
    monkeypatch.setattr(backend, "_loaded", False)
    monkeypatch.setitem(backend._REGISTRY, "shout", Shout)      # (restored afterwards)
    backend._REGISTRY.pop("shout")
    assert "shout" in backend.available()
    rule = {"name": "w.1", "shapes": [_rect("A", 0, 0, 4, 10)], "measures": [{"name": "W", "from": "a3", "to": "a1"}],
            "constraints": [M("A.layer == met"), C("W >= 0.2")]}
    assert get("shout").files([rule], "deck") == {"deck.shout.txt": "W.1"}


def test_another_package_can_add_a_backend_that_runs_its_deck(monkeypatch, tmp_path, capsys):
    """A backend may also RUN the deck it compiles: build, check and selftest go through
    the backend chosen, and nothing but that backend is asked how."""
    ran = []

    class Toy(Backend):
        name, extension, about, runs = "toy", ".toy", "rule names, one per line", True

        def __init__(self, title="deck"):
            self.title = title

        def compile(self, rule):
            return rule.name

        def ready(self):
            return None

        def run(self, deck, layout, top=None, report=None):
            ran.append((os.path.basename(deck), os.path.basename(layout), top))
            names = open(deck).read().split()
            return {"rules": {n: ("", [(0.0, 0.0, 1.0, 1.0)] if n == "NW.1" else []) for n in names}, "report": None}

    monkeypatch.setitem(backend._REGISTRY, "toy", Toy)
    assert backend.runner("toy").name == "toy" and backend.runner("klayout").runs
    with pytest.raises(KeyError, match="does not run decks; these do: klayout, toy"):
        backend.runner("docs")
    assert "does not run" in get("docs").ready()
    with pytest.raises(backend.RunError):
        get("docs").run("x", "y")

    deck, text, n, notes = process.build(DEMO, str(tmp_path), backend="toy")
    assert deck.endswith("demo.toy") and n == 18 and "NW.1" in open(deck).read().split()
    assert os.path.exists(text)                                  # the rules as sentences, whatever the backend

    from fastdrc import cli
    layout = tmp_path / "chip.gds"
    layout.write_bytes(b"")
    import shutil
    mine = str(tmp_path / "demo")                                # `check` builds beside the deck
    shutil.copytree(DEMO, mine, ignore=shutil.ignore_patterns("build"))
    assert cli.main(["check", str(layout), mine, "--backend", "toy", "--only", "NW.*"]) == 1
    out = capsys.readouterr().out
    assert "NW.1" in out and "1 violation(s) of 1 rule(s); 4 of 5 rules clean." in out and "Report" not in out
    assert ran[-1][1:] == ("chip.gds", None)
    with pytest.raises(SystemExit, match="the 'docs' backend does not run decks"):
        cli.main(["check", str(layout), DEMO, "--backend", "docs"])


def _plan(rule):
    cases, skipped = get("cases").plan([rule])
    return [(c["testing"], c["case"], c["expect"]) for c in cases], skipped, cases


def test_a_check_is_tested_just_outside_and_exactly_at_its_value():
    rule = {"name": "w.1", "shapes": [_rect("A", 0, 0, 4, 10)], "measures": [{"name": "W", "from": "a3", "to": "a1"}],
            "constraints": [M("A.layer == met"), C("W >= 0.2")]}
    got, skipped, cases = _plan(rule)
    assert skipped == [] and got == [
        ("the picture as drawn, every condition met", "all conditions met", False),
        ("check W >= 0.2", "W just outside", True),
        ("check W >= 0.2", "W far outside", True),
        ("check W >= 0.2", "W well inside", False),
        ("check W >= 0.2", "W exactly at the value", False)]
    widths = []
    for c in cases:
        xs = sorted({p[0] for p in c["shapes"][0]["points"]})
        ys = sorted({p[1] for p in c["shapes"][0]["points"]})
        widths.append(xs[1] - xs[0])
        assert ys[1] - ys[0] >= 4 * 200                              # the rest stays far from the limit
    assert widths[1:] == [195, 100, 300, 200]                        # nanometres


def test_an_edge_lying_between_the_measured_ones_is_squeezed_along():
    # a tap in a well, measured to the neighbouring well: the tap's own well edge lies
    # between the two measured edges, so no single edge can be moved far enough
    well, tap, other = _rect("W", 0, 30, 60, 60), _rect("T", 40, 40, 56, 48), _rect("P", 0, -10, 60, 28)
    rule = {"name": "d.4", "shapes": [well, tap, other], "measures": [{"name": "S", "from": "t0", "to": "p2"}],
            "constraints": [M("W.layer == nwell"), M("T.layer == ntap"), M("P.layer == pwell"), C("S >= 0.12")]}
    got, skipped, cases = _plan(rule)
    assert skipped == [] and ("check S >= 0.12", "S just outside", True) in got
    case = next(c for c in cases if c["case"] == "S just outside")
    ys = {s["layer"]: sorted({p[1] for p in s["points"]}) for s in case["shapes"]}
    assert ys["ntap"][0] - ys["pwell"][1] == 115                     # the arrow, in nanometres
    assert ys["pwell"][1] < ys["nwell"][0] < ys["ntap"][0]           # the well edge is still in between


def test_a_filter_is_tested_by_a_violation_outside_it():
    # "space between lines at least 2 wide": the same too-small space next to a line
    # that is NOT that wide must not be reported -- the filter filters
    a, b = _rect("A", 0, 0, 4, 10), _rect("B", 6, 0, 10, 10)
    rule = {"name": "s.2", "shapes": [a, b],
            "measures": [{"name": "W", "from": "a3", "to": "a1"}, {"name": "S", "from": "a1", "to": "b3"}],
            "constraints": [M("A.layer == met"), M("B.layer == met"), M("W >= 2"), C("S >= 0.5")]}
    got, skipped, _ = _plan(rule)
    assert skipped == []
    assert ("check S >= 0.5", "S just outside", True) in got
    assert ("match W >= 2", "W just outside the filter, and a check violated", False) in got
    assert ("match W >= 2", "W exactly at the value", True) in got            # still inside the filter


def test_either_of_two_is_enough_is_tested_both_ways():
    m, v = _rect("M", 0, 0, 10, 10), _rect("V", 3, 3, 5, 5)
    rule = {"name": "v.1", "shapes": [m, v],
            "measures": [{"name": "Ea", "from": "m3", "to": "v3"}, {"name": "Eb", "from": "m0", "to": "v0"}],
            "constraints": [M("M.layer == met"), M("V.layer == via"), C("Ea >= 0.1 or Eb >= 0.1")]}
    got, _, _ = _plan(rule)
    tag = "check Ea >= 0.1 or Eb >= 0.1"
    assert (tag, "Ea just outside", False) in got and (tag, "Eb just outside", False) in got
    assert (tag, "every part of it just outside", True) in got


def test_an_exact_size_is_about_every_side():
    # "contact size 0.2": the constraint names one side, the rule means all of them
    rule = {"name": "c.1", "shapes": [_rect("K", 0, 0, 1, 3)], "measures": [],
            "constraints": [M("K.layer == cont"), C("k0.length == 0.2")]}
    got, _, cases = _plan(rule)
    assert got[0][2] is False                                        # as drawn, made 0.2 x 0.2: quiet
    box = cases[0]["shapes"][0]["points"]
    assert {abs(box[1][0] - box[0][0]), abs(box[2][1] - box[1][1])} == {200}
    assert ("check k0.length == 0.2", "k0.length just outside", True) in got


def test_legend_names_and_columns_become_real_layers():
    rule = {"name": "e.1_thick", "layers": {"pdiff": "diff and pimp"}, "scope": [["overlapping", "thick"]],
            "shapes": [_rect("W", 0, 0, 20, 20), _rect("P", 4, 4, 10, 10)],
            "measures": [{"name": "E", "from": "w3", "to": "p3"}],
            "constraints": [M("W.layer == nwell"), M("P.layer == pdiff"), C("E >= 0.6")]}
    cases, skipped = get("cases").plan([rule])
    assert skipped == []
    assert sorted(s["layer"] for s in cases[0]["shapes"]) == ["diff", "nwell", "pimp"]
    assert cases[0]["markers"] == ["thick"]                          # the column: drawn over the whole case
    files = get("cases", layermap={"nwell": (1, 0), "diff": (2, 0), "pimp": (3, 0), "thick": (9, 0)}).files([rule], "p")
    assert set(files) == {"p.cases.rb", "p.cases.json"} and 'put.call("CASES", 9, 0,' in files["p.cases.rb"]
    assert json.loads(files["p.cases.json"])["layers_without_a_number"] == []


def test_a_count_is_tested_with_one_fewer_copy_and_with_none():
    count = {"name": "c.1", "shapes": [_rect("P", 0, 0, 10, 10), _rect("V", 2, 2, 8, 3)], "measures": [],
             "constraints": [M("P.layer == pad"), M("V.layer == via"), C("Count(V) >= 2")]}
    got, skipped, cases = _plan(count)
    tag = "check Count(V) >= 2"
    assert skipped == [] and (tag, "the number of V just outside", True) in got
    assert (tag, "no V at all", True) in got
    vias = {c["case"]: sum(s["layer"] == "via" for s in c["shapes"]) for c in cases}
    # as drawn there is one via; "every condition met" makes it the two the rule asks for
    assert vias == {"all conditions met": 2, "the number of V just outside": 1, "no V at all": 0}


def test_a_rule_it_cannot_build_is_said_so_and_never_passed():
    # a hole rule together with another area condition: beyond what is arranged today
    ring = _rect("R", 0, 0, 10, 10)
    ring["hole_loops"] = [_rect("H", 3, 3, 7, 7)["edges"]]
    rule = {"name": "h.2", "shapes": [ring], "measures": [],
            "constraints": [M("R.layer == met"), M("R.area >= 4"), C("R.holes.area >= 0.25")]}
    cases, skipped = get("cases").plan([rule])
    assert cases == [] and skipped == [("h.2", "a hole rule with further conditions is not built yet")]


def test_spacing_to_a_wide_line_is_tested_for_width_run_and_space():
    # the rule is about a wide line on EITHER side: to step outside the width filter, the
    # neighbour is made narrow too
    wide, near = _rect("W", 20, 0, 28, 20), _rect("N", 31, 0, 34, 20)
    rule = {"name": "m.e", "shapes": [wide, near],
            "measures": [{"name": "Ww", "from": "w3", "to": "w1"}, {"name": "S", "from": "w1", "to": "n3", "metric": "projection"}],
            "constraints": [M("W.layer == met"), M("N.layer == met"), M("Ww > 0.3"), M("S.run > 1.0"), C("S >= 0.22")]}
    got, skipped, cases = _plan(rule)
    assert skipped == []
    assert ("check S >= 0.22", "S just outside", True) in got
    assert ("match S.run > 1.0", "the run of S just outside the filter, and a check violated", False) in got
    assert ("match Ww > 0.3", "Ww just outside the filter, and a check violated", False) in got
    narrow = next(c for c in cases if c["case"].startswith("Ww just outside"))
    widths = sorted(max(p[0] for p in s["points"]) - min(p[0] for p in s["points"]) for s in narrow["shapes"])
    assert widths == [300, 300]                                      # both lines, in nanometres


def test_the_example_process_gets_cases_for_its_distance_rules():
    proc, sheets = process.load(DEMO)
    rules, _ = process.rules_and_warnings(sheets)
    cases, skipped = get("cases", layermap=proc["layers"]).plan(rules)
    tested = {c["rule"] for c in cases}
    assert {"NW.1", "NW.2", "NW.3", "NW.4_thin", "NW.4_thick", "LU.1", "PO.1", "PO.2", "PO.3",
            "CO.1", "CO.2", "CO.3", "CO.4", "V1.1", "V1.2", "AN.1", "DN.1", "DN.2"} == tested
    assert skipped == []
    filters = {(c["rule"], c["testing"]) for c in cases if c["testing"].startswith("match ")}
    assert ("CO.2", "match ka_0.length == ka_1.length") in filters   # contacts are squares: tested


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_the_example_process_behaves_as_drawn(tmp_path):
    res = process.selftest(DEMO, str(tmp_path))
    assert res["wrong"] == [] and sum(v == "ok" for v in res["rules"].values()) == 18
    assert sum(1 for (_, t) in res["constraints"] if t.startswith("match ")) == 5
    assert res["stray"] == {}                           # nothing is reported where nothing was drawn


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_the_self_test_names_a_rule_that_reports_outside_every_case(tmp_path):
    import json
    deck, _, _, notes = process.build(DEMO, str(tmp_path), extra=("cases",))
    expect_path = os.path.splitext(deck)[0] + ".cases.json"
    expect = json.load(open(expect_path))
    # forget the cases drawn for one rule: what it reports there now belongs to no case
    expect["cases"] = [c for c in expect["cases"] if c["rule"] != "NW.1"]
    json.dump(expect, open(expect_path, "w"))
    res = process._run_selftest(deck, notes)
    assert set(res["stray"]) == {"NW.1"} and res["stray"]["NW.1"] >= 1
    assert any("outside every test case: NW.1" in n for n in res["notes"])


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_the_self_test_sees_a_filter_that_does_not_filter(tmp_path, monkeypatch):
    from fastdrc import backend_klayout
    monkeypatch.setattr(backend_klayout.KLayoutBackend, "_FORM", {})      # "square" compiles to nothing
    res = process.selftest(DEMO, str(tmp_path))
    # every case of the three "contacts are squares" filters that lies outside the filter
    assert res["wrong"] and all("[match " in w and "should not" in w for w in res["wrong"])
    assert {w.split("]")[0].split("[")[1] for w in res["wrong"]} == {
        "match ka_0.length == ka_1.length", "match kb_0.length == kb_1.length"}


def test_a_crossed_out_shape_is_tested_by_showing_it_and_by_taking_it_away():
    # "no contact on the gate": the picture IS the violation
    diff, gate, k = _rect("D", 0, 0, 20, 10), _rect("G", 8, -4, 12, 14), _rect("K", 9, 4, 11, 6)
    k["crossed"] = True
    rule = {"name": "c.j", "shapes": [diff, gate, k], "measures": [],
            "constraints": [M("D.layer == diff"), M("G.layer == poly"), M("K.layer == cont")]}
    got, skipped, cases = _plan(rule)
    assert skipped == [] and got == [
        ("the cross: this must not exist", "the picture as drawn", True),
        ("the cross: this must not exist", "the same without K", False)]
    assert sorted(s["layer"] for s in cases[0]["shapes"]) == ["cont", "diff", "poly"]
    assert sorted(s["layer"] for s in cases[1]["shapes"]) == ["diff", "poly"]


def test_an_area_is_stepped_by_one_side_of_the_rectangle():
    rule = {"name": "a.1", "shapes": [_rect("A", 0, 0, 4, 6)], "measures": [],
            "constraints": [M("A.layer == met"), C("A.area >= 0.25")]}
    got, skipped, cases = _plan(rule)
    assert skipped == []
    assert ("check A.area >= 0.25", "the area just outside", True) in got
    assert ("check A.area >= 0.25", "the area exactly at the value", False) in got

    def area(case):
        xs = sorted({p[0] for p in case["shapes"][0]["points"]})
        ys = sorted({p[1] for p in case["shapes"][0]["points"]})
        return (xs[1] - xs[0]) * (ys[1] - ys[0])
    exact = next(c for c in cases if c["case"] == "the area exactly at the value")
    under = next(c for c in cases if c["case"] == "the area just outside")
    assert area(exact) == 250_000 and 0 < 250_000 - area(under) <= 5 * 500          # nm2


def test_must_lie_inside_is_tested_with_and_without_the_outer_shape():
    rule = {"name": "c.h", "shapes": [_rect("MT", 0, 0, 10, 10), _rect("K", 4, 4, 6, 6)], "measures": [],
            "constraints": [M("MT.layer == met"), M("K.layer == cont"), C("K in MT")]}
    got, skipped, _ = _plan(rule)
    assert skipped == [] and got == [("check K in MT", "the picture as drawn", False),
                                     ("check K in MT", "the same without MT", True)]


def test_an_array_rule_is_tested_with_whole_arrays():
    # "contact space in a 4 x 4 or larger array": two neighbours stand for the array
    a, b = _rect("KA", 4, 3, 5, 4), _rect("KB", 4, 7, 5, 8)
    rule = {"name": "c.3", "shapes": [_rect("D", 0, 0, 20, 12), a, b],
            "measures": [{"name": "S", "from": "ka2", "to": "kb0"}],
            "constraints": [M("D.layer == diff"), M("KA.layer == cont"), M("KB.layer == cont"),
                            M("KA.rows >= 4 and KA.columns >= 4"), M("KB.rows >= 4 and KB.columns >= 4"),
                            M("ka0.length == 0.2"), M("kb0.length == 0.2"), C("S >= 0.3")]}
    got, skipped, cases = _plan(rule)
    assert skipped == []
    by = {c["case"]: (c["expect"], sum(s["layer"] == "cont" for s in c["shapes"])) for c in cases}
    assert by["4 x 4, gaps just too small"] == (True, 16)
    assert by["4 x 4, gaps exactly at the value"] == (False, 16)
    assert by["one row fewer (3 x 4), gaps just too small"] == (False, 12)       # no longer an array
    assert by["one column fewer (4 x 3), gaps just too small"] == (False, 12)
    assert by["4 x 4, gaps too small across only"] == (True, 16)                  # a plain spacing: either way
    assert all(any(s["layer"] == "diff" for s in c["shapes"]) for c in cases)    # still on its diffusion


def test_an_area_share_is_tested_at_the_value_around_it_and_with_nothing():
    plate, slit = _rect("P", 0, 0, 40, 40), _rect("S", 6, 10, 9, 30)
    rule = {"name": "s.i", "shapes": [plate, slit], "measures": [],
            "constraints": [M("P.layer == met"), M("S.layer == slit"), C("S.area >= 0.06 * P.area")]}
    got, skipped, cases = _plan(rule)
    tag = "check S.area >= 0.06 * P.area"
    assert skipped == [] and {(t, w): e for t, w, e in got} == {
        (tag, "the share just outside"): True, (tag, "the share exactly at the value"): False,
        (tag, "the share far outside"): True, (tag, "the share well inside"): False,
        (tag, "nothing in it at all"): True}
    exact = next(c for c in cases if c["case"] == "the share exactly at the value")
    area = {s["layer"]: (max(p[0] for p in s["points"]) - min(p[0] for p in s["points"]))
            * (max(p[1] for p in s["points"]) - min(p[1] for p in s["points"])) for s in exact["shapes"]}
    assert area["slit"] * 100 == area["met"] * 6


def test_a_density_case_is_a_layout_of_its_own():
    # a density is taken over the whole area: cases cannot share a layout
    rule = {"name": "d.1", "shapes": [_rect("CHIP", 0, 0, 30, 30), _rect("MT", 5, 5, 15, 15)], "measures": [],
            "constraints": [M("CHIP.layer == chip"), M("MT.layer == met"), C("MT.density >= 0.2 and MT.density <= 0.8")]}
    cases, skipped = get("cases").plan([rule])
    assert skipped == [] and len({c["cell"] for c in cases}) == len(cases) and all(c["cell"].startswith("ALONE_") for c in cases)
    by = {c["case"]: c for c in cases}
    assert by["coverage just below the minimum"]["expect"] and not by["coverage exactly at the minimum"]["expect"]
    assert by["coverage just above the maximum"]["expect"] and not by["coverage exactly at the maximum"]["expect"]
    assert not by["coverage well inside"]["expect"]

    def share(c):
        w = {s["layer"]: max(p[0] for p in s["points"]) - min(p[0] for p in s["points"]) for s in c["shapes"]}
        return w["met"] / w["chip"]
    assert share(by["coverage exactly at the minimum"]) == 0.2 and share(by["coverage exactly at the maximum"]) == 0.8
    # without a drawn area the density is the layout's own: two specks set its extent
    bare = dict(rule, shapes=[_rect("MT", 5, 5, 15, 15)],
                constraints=[M("MT.layer == met"), C("MT.density >= 0.2")])
    cases, skipped = get("cases").plan([bare])
    assert skipped == [] and all(any(s["layer"] == "_extent" for s in c["shapes"]) for c in cases)


def test_an_antenna_case_gets_its_connecting_stack_from_the_process():
    # the rule's own picture has only the gate and the metal; what joins them comes from
    # the process's connections: gate - poly beside it - contact on that - metal over it
    rule = {"name": "an.1", "layers": {"gate": "poly and diff", "field_poly": "poly and not diff"},
            "connect": [["field_poly", "gate"], ["cont", "field_poly"], ["cont", "met1"], ["via1", "met1"]],
            "shapes": [_rect("G", 0, 4, 2, 8), _rect("MP", -1, 11, 3, 14)], "measures": [],
            "constraints": [M("G.layer == gate"), M("MP.layer == met1"), C("MP.area <= 400 * G.area")]}
    cases, skipped = get("cases").plan([rule])
    assert skipped == []
    by = {c["case"]: c for c in cases}

    def area(c, layer):
        return sum((max(p[0] for p in s["points"]) - min(p[0] for p in s["points"]))
                   * (max(p[1] for p in s["points"]) - min(p[1] for p in s["points"])) for s in c["shapes"] if s["layer"] == layer)
    exact = by["the collected area exactly at the ratio"]
    assert not exact["expect"] and area(exact, "met1") == 400 * area(exact, "diff")     # diff = the gate
    assert by["the collected area just above the ratio"]["expect"]
    assert {s["layer"] for s in exact["shapes"]} == {"poly", "diff", "cont", "met1"}    # no via1: not on the way


def test_a_hole_rule_is_tested_with_rings():
    ring = _rect("R", 0, 0, 10, 10)
    ring["hole_loops"] = [_rect("H", 3, 3, 7, 7)["edges"]]
    rule = {"name": "h.1", "shapes": [ring], "measures": [],
            "constraints": [M("R.layer == met"), C("R.holes.area >= 0.25")]}
    got, skipped, cases = _plan(rule)
    tag = "check R.holes.area >= 0.25"
    assert skipped == [] and {w: e for _, w, e in got} == {
        "the hole just outside the value": True, "the hole exactly at the value": False,
        "the hole far outside the value": True, "the hole well inside the value": False, "no hole at all": False}
    exact = next(c for c in cases if c["case"] == "the hole exactly at the value")
    assert len(exact["shapes"]) == 4 and all(t == tag for t, _, _ in got)            # four bars around the hole
    xs = sorted({p[0] for s in exact["shapes"] for p in s["points"]})
    ys = sorted({p[1] for s in exact["shapes"] for p in s["points"]})
    assert (xs[2] - xs[1]) * (ys[2] - ys[1]) == 250_000                              # the hole, in nm2


def test_a_value_of_a_few_nanometres_is_stepped_by_less():
    rule = {"name": "e.6", "shapes": [_rect("M", 0, 0, 10, 10), _rect("K", 3, 3, 5, 5)],
            "measures": [{"name": "E", "from": "m3", "to": "k3"}],
            "constraints": [M("M.layer == met"), M("K.layer == cont"), C("E >= 0.005")]}
    got, skipped, cases = _plan(rule)
    assert skipped == [] and ("check E >= 0.005", "E just outside", True) in got
    under = next(c for c in cases if c["case"] == "E just outside")
    left = {s["layer"]: min(p[0] for p in s["points"]) for s in under["shapes"]}
    assert 0 < left["cont"] - left["met"] < 5                                        # nanometres


def test_or_exactly_zero_is_arranged_by_its_first_alternative():
    rule = {"name": "s.7", "shapes": [_rect("M", 0, 0, 10, 10), _rect("K", 3, 3, 5, 5)],
            "measures": [{"name": "E", "from": "m3", "to": "k3"}],
            "constraints": [M("M.layer == met"), M("K.layer == cont"), C("E >= 0.22 or E == 0")]}
    got, skipped, _ = _plan(rule)
    tag = "check E >= 0.22 or E == 0"
    assert skipped == [] and got[0] == ("the picture as drawn, every condition met", "all conditions met", False)
    assert (tag, "E exactly 0.22", False) in got and (tag, "E far outside", True) in got
    assert (tag, "E exactly 0", False) in got                        # coinciding edges are allowed


def test_antenna_over_two_metals_with_a_diode_condition():
    # cumulative: metal-1 holds half of what is allowed, metal-2 is stepped; the diode
    # condition is tested as a filter -- the same too-large area with a diode must be quiet
    rule = {"name": "an.b", "layers": {"gate": "poly and diff", "field_poly": "poly and not diff", "diode": "diff and not poly"},
            "connect": [["field_poly", "gate"], ["cont", "field_poly"], ["cont", "met1"], ["via1", "met1"],
                        ["via1", "met2"], ["cont", "diode"]],
            "shapes": [_rect("G", 0, 4, 2, 8), _rect("MA", -1, 11, 3, 14), _rect("MB", -2, 10, 4, 15), _rect("D", 20, 0, 30, 10)],
            "measures": [],
            "constraints": [M("G.layer == gate"), M("MA.layer == met1"), M("MB.layer == met2"), M("D.layer == diode"),
                            M("D.area < 0.16"), C("(MA.area + MB.area) <= 200 * G.area")]}
    cases, skipped = get("cases").plan([rule])
    assert skipped == []
    by = {c["case"]: c for c in cases}

    def area(c, layer):
        return sum((max(p[0] for p in s["points"]) - min(p[0] for p in s["points"]))
                   * (max(p[1] for p in s["points"]) - min(p[1] for p in s["points"])) for s in c["shapes"] if s["layer"] == layer)
    exact = by["no diode, the collected area exactly at the ratio"]
    assert not exact["expect"] and area(exact, "met1") + area(exact, "met2") == 200 * 1_000_000
    assert by["no diode, the collected area just above the ratio"]["expect"]
    with_diode = by["a diode exactly at its value, the collected area just above the ratio"]
    assert with_diode["testing"] == "match D.area < 0.16" and not with_diode["expect"]
    assert by["a diode just below its value, the collected area just above the ratio"]["expect"]


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_the_self_test_runs_on_rules_at_hand(tmp_path):
    # what the editor's Test button does: the open sheet, as part of its process
    proc, files = process.load(DEMO)
    co2 = next(d for p, d in files if p.endswith("CO.2.json"))
    from fastdrc.sheet import rules_of
    res = process.selftest_rules(rules_of(co2), proc["layers"], str(tmp_path))
    assert res["wrong"] == [] and set(res["rules"]) == {"CO.2"}
    assert ("CO.2", "match ka_0.length == ka_1.length") in res["constraints"]


def test_a_forbidden_corner_is_tested_where_it_is_and_where_it_is_not():
    # "no 90-degree turn of poly on diffusion"
    pts = [(4, 0), (6, 0), (6, 6), (12, 6), (12, 8), (4, 8)]                 # an L of poly
    poly = {"name": "LP", "edges": [{"id": f"lp{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 6]), "edge": False}
                                    for i in range(6)]}
    rule = {"name": "p.11", "shapes": [_rect("D", 0, -2, 16, 12), poly], "measures": [],
            "constraints": [M("D.layer == diff"), M("LP.layer == poly"), C("lp1.corner != 90")]}
    got, skipped, cases = _plan(rule)
    assert skipped == [] and got == [("check lp1.corner != 90", "the corner as drawn", True),
                                     ("check lp1.corner != 90", "the same without D", False)]
    assert [sorted(s["layer"] for s in c["shapes"]) for c in cases] == [["diff", "poly"], ["poly"]]


def test_the_drawing_as_a_picture_is_a_backend_others_can_use(tmp_path):
    """`svg` turns a rule's drawing into a figure; any backend gets it by name."""
    import xml.etree.ElementTree as ET
    rule = {"name": "w.1", "desc": "width & <more>",
            "shapes": [dict(_rect("A", 0, 0, 4, 10), crossed=True)],
            "measures": [{"name": "W", "from": "a3", "to": "a1"}],
            "constraints": [M("A.layer == met"), C("W >= 0.2")]}
    rule["shapes"][0]["edges"][1]["edge"] = rule["shapes"][0]["edges"][3]["edge"] = True    # the measured sides
    svg = get("svg").compile(Rule(rule))
    root = ET.fromstring(svg)                                    # well-formed, whatever the text holds
    ns = "{http://www.w3.org/2000/svg}"
    texts = [t.text for t in root.iter(ns + "text")]
    assert texts == ["met", "W"] and root.find(ns + "title").text == "w.1 — width & <more>"
    assert len(list(root.iter(ns + "line"))) == 3                # two straight sides and the arrow
    assert len(list(root.iter(ns + "polyline"))) == 2            # two wavy sides
    assert get("svg", labels="name").compile(Rule(rule)).count(">A<") == 1
    from fastdrc.backend_svg import colour_of
    assert colour_of("met") == colour_of("met") and colour_of("met") in svg      # one colour per layer, deck-wide

    class Page(Backend):                                         # a backend made of two others
        name, extension = "page", ".html"

        def compile(self, rule):
            return get("svg").compile(rule) + "<p>" + get("docs").compile(rule).splitlines()[1].strip() + "</p>"

    page = Page().compile(Rule(rule))
    assert page.startswith("<svg") and "<p>•" in page
    deck, _, n, _ = process.build(DEMO, str(tmp_path), extra=("svg",))
    figures = sorted(f for f in os.listdir(tmp_path) if f.endswith(".svg"))
    assert len(figures) == n == 18 and "demo.NW.1.svg" in figures
    for f in figures:
        ET.parse(os.path.join(tmp_path, f))


def test_deep_asks_the_deck_to_work_on_the_hierarchy(tmp_path):
    flat = open(process.build(DEMO, str(tmp_path / "flat"))[0]).read()
    deep = open(process.build(DEMO, str(tmp_path / "deep"), deep=True)[0]).read()
    assert "\ndeep\n" not in flat and "\ndeep\n" in deep
    assert deep.index("report(") < deep.index("\ndeep\n") < deep.index("polygons(")   # before any layer is read
    assert "hierarchical (KLayout's deep mode)" in deep.split("\n\n")[0]              # said at the head
    body = lambda text: text[text.index("polygons("):]                                # noqa: E731
    assert body(flat) == body(deep)                                                   # the rules are the same


def test_a_backend_that_knows_no_hierarchy_is_built_as_before(tmp_path):
    # `deep` is offered to the backend; one that does not take it is not disturbed
    assert get("docs", deep=True).compile_all([]) == get("docs").compile_all([])


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_the_rules_behave_as_drawn_on_the_hierarchy_too(tmp_path):
    res = process.selftest(DEMO, str(tmp_path), only=["NW.*,CO.*,AN.1,DN.*"], deep=True)
    assert res["wrong"] == [] and res["stray"] == {}
    assert sum(v == "ok" for v in res["rules"].values()) == 12
