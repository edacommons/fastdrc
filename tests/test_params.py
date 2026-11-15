"""The parameters of a deck: chosen once, read by switches, settled when the deck is loaded."""
import json
import os

import pytest

from fastdrc import params, process
from fastdrc.editor import format_values, parse_values

PROC = {"parameters": {"top_metal": {"choices": ["6K", "9K", "30K"], "default": "9K"},
                       "stack": {"choices": [5, 6]},
                       "top_via": {"switch": "stack", "cases": {"5": "via4", "6": "via5"}},
                       "thick": {"switch": "top_metal", "cases": {"30K": True}, "default": False}},
        "profiles": {"big": {"stack": 6, "top_metal": "30K"}}}


def test_defaults_then_the_profile_then_what_is_given():
    assert params.resolve(PROC) == {"top_metal": "9K", "stack": 5, "top_via": "via4", "thick": False}
    assert params.resolve(PROC, "big")["top_via"] == "via5" and params.resolve(PROC, "big")["thick"] is True
    assert params.resolve(PROC, "big", ["top_metal=6K"])["top_metal"] == "6K"
    assert params.resolve(PROC, None, {"stack": "6"})["stack"] == 6          # typed on a command line: text
    assert params.said(params.resolve(PROC), PROC) == "top_metal = 9K, stack = 5"


@pytest.mark.parametrize("profile, given, says", [
    (None, ["top_metal=12K"], "top_metal cannot be '12K' (its choices: 6K, 9K, 30K)"),
    ("small", None, "no profile is called 'small'"),
    (None, ["metal=6K"], "no parameter is called 'metal'"),
    (None, ["top_via=via1"], "top_via is not chosen: it is derived from stack"),
    (None, ["top_metal"], "name=value"),
])
def test_what_cannot_be_chosen_is_said(profile, given, says):
    with pytest.raises(params.ParameterError, match=says.replace("(", r"\(").replace(")", r"\)")):
        params.resolve(PROC, profile, given)


def test_a_parameter_is_defined_once_and_never_by_itself():
    loop = {"parameters": {"a": {"switch": "b", "cases": {}}, "b": {"switch": "a", "cases": {}}}}
    with pytest.raises(params.ParameterError, match="defined by each other: a -> b -> a"):
        params.resolve(loop)
    with pytest.raises(params.ParameterError, match="'nothing', which is not a parameter"):
        params.resolve({"parameters": {"a": {"switch": "nothing", "cases": {}}}})
    with pytest.raises(params.ParameterError, match="must list its choices or be a switch"):
        params.resolve({"parameters": {"a": 3}})


def test_a_switch_stands_wherever_a_value_stands():
    v = params.resolve(PROC, "big")
    sw = {"switch": "top_metal", "cases": {"6K": 0.36, "9K": 0.44}}
    assert params.settle({"x": [1, sw], "y": dict(sw, default=1.8)}, v) == {"x": [1, None], "y": 1.8}
    nested = {"switch": "thick", "cases": {"true": {"switch": "stack", "cases": {"6": "deep"}}}}
    assert params.settle(nested, v) == "deep"


def _deck(tmp_path, rule_extra, legend=None):
    os.makedirs(tmp_path / "rules")
    proc = dict(PROC, name="P", layers={"metaltop": [53, 0], "via4": [41, 0], "via5": [82, 0]}, legend=legend or {})
    json.dump(proc, open(tmp_path / "process.json", "w"))
    box = lambda n, x0, y0, x1, y1, st: {"name": n, "edges": [  # noqa: E731
        {"id": f"{n.lower()}_{i}", "start": list(p), "end": list(q), "edge": "trbl"[i] in st}
        for i, (p, q) in enumerate(zip([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [(x1, y0), (x1, y1), (x0, y1), (x0, y0)]))]}
    rule = {"name": "MT.1", "desc": "MT.1 : width", "shapes": [box("A", 0, 0, 5, 16, "lr")],
            "measures": [{"name": "W", "from": "a_3", "to": "a_1"}],
            "constraints": [{"kind": "match", "expr": "A.layer == metaltop"}, {"kind": "check", "expr": "W >= ?"}]}
    rule.update(rule_extra)
    json.dump(rule, open(tmp_path / "rules" / "MT.1.json", "w"))
    return str(tmp_path)


def test_a_rules_value_by_parameter_and_no_value_no_rule(tmp_path):
    deck = _deck(tmp_path, {"value": {"switch": "top_metal", "cases": {"6K": 0.36, "9K": 0.44}}})
    path, _, n, notes = process.build(deck)
    text = open(path).read()
    assert n == 1 and notes == [] and "metaltop.width(0.44.um" in text
    assert "built for top_metal = 9K, stack = 5" in text.split("\n\n")[0]
    assert "metaltop.width(0.36.um" in open(process.build(deck, params=["top_metal=6K"])[0]).read()
    assert process.build(deck, profile="big")[2] == 0                 # 30K: the rule gives no value -- not in the deck
    # ... but the deck's files are all still there for whoever lists them
    assert [d.get("_absent") for _, d in process.load(deck, "big", absent=True)[1]] == [True]


def test_when_says_whether_a_rule_is_there(tmp_path):
    deck = _deck(tmp_path, {"value": 1.8, "when": {"switch": "thick", "cases": {"true": True}, "default": False}})
    assert process.build(deck)[2] == 0
    assert "metaltop.width(1.8.um" in open(process.build(deck, profile="big")[0]).read()


def test_a_legend_name_by_parameter(tmp_path):
    deck = _deck(tmp_path, {"value": 0.3, "constraints": [{"kind": "match", "expr": "A.layer == top_via"},
                                                           {"kind": "check", "expr": "W >= ?"}]},
                 legend={"top_via": {"switch": "top_via", "cases": {"via4": "via4", "via5": "via5"}},
                         "gone": {"switch": "thick", "cases": {"true": "via5"}}})
    assert "via4.width(0.3.um" in open(process.build(deck)[0]).read()
    assert "via5.width(0.3.um" in open(process.build(deck, profile="big")[0]).read()


def test_a_rule_on_a_name_the_process_does_not_have_is_not_there(tmp_path):
    deck = _deck(tmp_path, {"value": 0.3, "constraints": [{"kind": "match", "expr": "A.layer == deep"},
                                                           {"kind": "check", "expr": "W >= ?"}]},
                 legend={"deep": {"switch": "thick", "cases": {"true": "via5"}}})
    assert process.build(deck)[2] == 0 and process.build(deck, profile="big")[2] == 1


def test_a_value_per_column_may_depend_on_a_parameter_too():
    rule = {"constraints": [{"kind": "check", "expr": "W >= ?"}],
            "values": {"3.3V": {"switch": "top_metal", "cases": {"9K": 0.2}}, "5V": 0.3}}
    d, present = process.settled_rule(rule, params.resolve(PROC))
    assert present and d["values"] == {"3.3V": 0.2, "5V": 0.3}
    d, present = process.settled_rule(rule, params.resolve(PROC, "big"))
    assert present and d["values"] == {"5V": 0.3}                      # the column it has no value for is left out


def test_the_editor_shows_a_switch_as_it_is_written_and_keeps_it():
    sw = {"switch": "top_metal", "cases": {"6K": 0.36, "9K": 0.44}}
    assert parse_values(format_values(sw)) == sw
    assert format_values({"3.3V": 0.22, "5V": 0.3}) == "3.3V=0.22, 5V=0.3"
    assert parse_values("3.3V=0.22, 5V=0.3") == {"3.3V": 0.22, "5V": 0.3}


def test_the_editors_process_window_keeps_what_parameters_decide():
    from fastdrc.editor import Editor
    old = dict(PROC, legend={"pdiff": "diff and pimp", "top_via": {"switch": "stack", "cases": {"5": "via4"}}},
               connect=[["a", "b"]])
    new = Editor.process_from_text("P", "diff 1/0", "pdiff = diff and pimp\nndiff = diff and nimp", "", old)
    assert new["legend"] == {"pdiff": "diff and pimp", "ndiff": "diff and nimp", "top_via": old["legend"]["top_via"]}
    assert new["parameters"] == PROC["parameters"] and new["profiles"] == PROC["profiles"] and new["connect"] == [["a", "b"]]
