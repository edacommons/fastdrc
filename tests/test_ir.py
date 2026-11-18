"""The deck as a graph (python/fastdrc/ir.py): the rules being built are read into one
graph of operations, optimised, and written back with everything computed once."""
import os

import pytest

from fastdrc import process
from fastdrc.ir import Deck, ParseError

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "..", "examples", "demo")
TOP = ["diff = polygons(2, 0)", "pimp = polygons(3, 0)", "nwell = polygons(1, 0)", "cont = polygons(5, 0)",
       "met1 = polygons(6, 0)", "otp = polygons(7, 0)", "mvsd = polygons(8, 0)",
       "pdiff = diff.and(pimp)", "ndiff = diff.not(pimp)"]


def deck(rules, top=TOP, optimise=True):
    d = Deck()
    d.add(top, top=True)
    d.add(rules)
    if optimise:
        d.optimise()
    return d


def test_what_is_read_is_written_back():
    rules = ["a = nwell.width(0.8.um, euclidian).polygons(0.001)", 'a.output("NW.1", "N-well width")', "a.forget",
             "b = (met1.area < 0.2 * nwell.area || met1.area > 0.8 * nwell.area) ? nwell.dup : polygon_layer",
             'b.output("DN.1", "density")', "b.forget",
             "c = met1.drc(width <= 0.5.um).with_length(1.um, nil, both)", 'c.output("X", "")', "c.forget",
             "d = out_of_density.call(met1, 0.0 .. 0.65, nwell, 800.0, 400.0)", 'd.output("D", "")', "d.forget",
             "e = met1.sized(-1.um).sized(1.um).not_interacting(cont, 2)", 'e.output("E", "")', "e.forget"]
    pre, body, shared = deck(rules, optimise=False).lines()
    assert body == rules and shared == {}
    assert pre == [t for t in TOP if t.split(" = ")[0] in ("nwell", "met1", "cont")]      # only what is needed


def test_the_same_expression_is_one_node_computed_once_and_released_after_its_last_use():
    rules = ["a = pdiff.and(nwell).not(ndiff.and(nwell).sized(20.um))", 'a.output("A", "")', "a.forget",
             "b = pdiff.and(nwell).width(0.3.um)", 'b.output("B", "")', "b.forget",
             "c = met1.width(1.um)", 'c.output("C", "")', "c.forget"]
    d = deck(rules)
    pre, body, shared = d.lines()
    assert shared == {"pdiff_and_nwell": "pdiff.and(nwell)"}
    assert body == ["pdiff_and_nwell = pdiff.and(nwell)",
                    "a = pdiff_and_nwell.not(ndiff.and(nwell).sized(20.um))", 'a.output("A", "")', "a.forget",
                    "b = pdiff_and_nwell.width(0.3.um)", "pdiff_and_nwell.forget", 'b.output("B", "")', "b.forget",
                    "c = met1.width(1.um)", 'c.output("C", "")', "c.forget"]
    assert d.stats["written"] == 9 and d.stats["after"] == 8


def test_one_spelling_for_one_meaning():
    # b.and(a) is a.and(b): met in two rules in two orders, it is still one node
    d = deck(["a = nwell.and(cont).width(1.um)", 'a.output("A", "")', "b = cont.and(nwell).space(1.um)", 'b.output("B", "")'])
    assert list(d.lines()[2].values()) == ["nwell.and(cont)"]          # the spelling met first
    # ... but only where both are certainly polygons: edges.and(polygons) is not polygons.and(edges)
    d = deck(["a = nwell.edges.and(cont).length", "b = cont.and(nwell.edges).length"])
    assert d.names["a"] != d.names["b"]


def test_a_longer_chain_is_built_on_a_shorter_one_that_is_needed_anyway():
    rules = ["a = diff.not(otp).width(1.um)", 'a.output("A", "")',
             "b = diff.not(mvsd).not(otp).space(1.um)", 'b.output("B", "")',
             "c = diff.not(mvsd).not(otp).not(pimp).area", 'c.output("C", "")']
    plain = deck(rules, optimise=False)
    d = deck(rules)
    body = d.lines()[1]
    assert "diff_not_otp = diff.not(otp)" in body
    assert "diff_not_otp_not_mvsd = diff_not_otp.not(mvsd)" in body
    assert any(line.startswith("c = diff_not_otp_not_mvsd.not(pimp)") for line in body)
    assert plain.count() == 7 and d.count() == 6               # one subtraction less, each on a smaller layer


def test_a_name_that_holds_another_is_never_mistaken_for_it():
    d = deck(["a = diff.and(nwell).width(1.um)", 'a.output("A", "")', "b = ndiff.and(nwell).width(1.um)", 'b.output("B", "")'])
    assert d.lines()[2] == {}


def test_nets_are_read_once_for_each_set_of_connections():
    # the same text under OTHER connections is another value ...
    rules = ["connect(cont, met1)", "a = met1.nets.space(1.um, euclidian, props_ne)", 'a.output("A", "")', "a.forget",
             "clear_connections", "connect(cont, diff)",
             "b = met1.nets.space(1.um, euclidian, props_ne)", 'b.output("B", "")', "b.forget"]
    d = deck(rules)
    pre, body, shared = d.lines()
    assert d.names["a"] != d.names["b"] and shared == {}
    # ... and where the connections are started over, what was read off the nets is computed while
    # they stand and kept as plain shapes: by the time it is reported, those nets are gone
    assert body == ["connect(cont, met1)", "met1_nets = met1.nets",
                    "a = met1_nets.space(1.um, euclidian, props_ne)", "met1_nets.forget", "a = off_the_nets.call(a)",
                    "clear_connections", "connect(cont, diff)", "met1_nets_2 = met1.nets",
                    "b = met1_nets_2.space(1.um, euclidian, props_ne)", "met1_nets_2.forget", "b = off_the_nets.call(b)",
                    'a.output("A", "")', "a.forget", 'b.output("B", "")', "b.forget"]
    assert (d.stats["nets_written"], d.stats["nets"]) == (2, 2)
    # ... and under the SAME connections, declared again for another rule, the same one:
    # the stack is declared once, a longer stack is built on the shorter, and the nets
    # are extracted once per stack, not once per rule
    one = ["connect(cont, gate)", "connect(cont, met1)"]
    two = one + ["connect(via, met1)", "connect(via, met2)"]
    rules = (one + ['a1 = evaluate_nets(gate, { "x" => met1 }, "put(\'t\', area(x))")']
             + two[2:] + ['a2 = evaluate_nets(gate, { "x" => met2 }, "put(\'t\', area(x))")',
                          "a = (a1 + a2).merged_props", 'a.output("A", "")', "a.forget", "clear_connections"]
             + one + ['b = evaluate_nets(gate, { "x" => met1 }, "put(\'t\', area(x))").selected_if("value(\'t\') > 9")',
                      'b.output("B", "")', "b.forget", "clear_connections"]
             + two + ['c = evaluate_nets(gate, { "x" => met2 }, "put(\'t\', area(x))").selected_if("value(\'t\') > 5")',
                      'c.output("C", "")', "c.forget"])
    d = deck(rules)
    body = d.lines()[1]
    # (what is made from a reading is computed while its connections stand, so it may sit between them)
    nets = [ln for ln in body if ln.startswith(("connect", "clear_connections")) or "evaluate_nets" in ln]
    assert nets == one + ['a1 = evaluate_nets(gate, { "x" => met1 }, "put(\'t\', area(x))")'] + two[2:] + [
        'a2 = evaluate_nets(gate, { "x" => met2 }, "put(\'t\', area(x))")']
    assert (d.stats["nets_written"], d.stats["nets"]) == (4, 2)
    # a layer that a connection holds is never released
    rules = ["g = diff.and(met1)", "connect(g, cont)", "x = antenna_check(g, met1, 400.0)", 'x.output("AN", "")', "x.forget"]
    assert deck(rules).lines()[1] == rules


def test_a_copy_and_a_number_are_never_shared():
    rules = ["a = (met1.area < 0.2 * nwell.area) ? nwell.dup : polygon_layer", 'a.output("A", "")', "a.forget",
             "b = (met1.area > 0.8 * nwell.area) ? nwell.dup : polygon_layer", 'b.output("B", "")', "b.forget"]
    pre, body, shared = deck(rules).lines()
    assert body == rules and shared == {}


def test_a_rule_that_is_the_same_as_another_does_not_release_its_layer_early():
    rules = ["a = cont.squares.space(1.um)", 'a.output("A", "")', "a.forget",
             "b = cont.squares.space(1.um)", 'b.output("B", "")', "b.forget"]
    assert deck(rules).lines()[1] == ["a = cont.squares.space(1.um)", 'a.output("A", "")', 'a.output("B", "")', "a.forget"]


def test_what_the_graph_cannot_read_is_said_not_guessed():
    with pytest.raises(ParseError):
        deck(["a = met1.each { |p| p.area }"])
    with pytest.raises(ParseError):
        deck(["puts 1"])


# --- the set of rules being built --------------------------------------------------------

def test_only_and_skip_choose_the_rules_by_name_or_section():
    _, sheets = process.load(DEMO)
    every, _ = process.rules_and_warnings(sheets)
    names = lambda **kw: [r["name"] for r in process.rules_and_warnings(sheets, **kw)[0]]   # noqa: E731
    assert names(only="NW.*") == [r["name"] for r in every if r["name"].startswith("NW.")]
    assert names(only="1-wells") == [r["name"] for r in every[:len(names(only="1-wells"))]]
    assert set(names(only=["3-metal", "NW.1"], skip="DN.*")) == {"NW.1", "AN.1", "V1.1", "V1.2"}
    assert names(only="2-devices/CO.1") == ["CO.1"]
    assert len(names(skip="2-devices, 3-metal")) == len(names(only="1-wells"))
    rules, notes = process.rules_and_warnings(sheets, only="NW.1, ZZ.*")
    assert [r["name"] for r in rules] == ["NW.1"] and any("ZZ.*" in n for n in notes)


def test_a_chosen_set_reads_only_its_layers_and_says_what_it_is(tmp_path):
    deck_path, text, n, notes = process.build(DEMO, str(tmp_path), only="NW.1, NW.2")
    body = open(deck_path).read()
    assert n == 2 and notes == []
    assert "# 2 of the deck's 18 rules: only NW.1, NW.2" in body
    assert [line for line in body.splitlines() if " = polygons(" in line] == ["nwell = polygons(1, 0)"]
    assert open(text).read().count("\n  • ") == 2


def test_the_built_deck_is_fitted_to_its_set_and_the_plain_one_is_still_available(tmp_path):
    fitted = open(process.build(DEMO, str(tmp_path / "a"))[0]).read()
    plain = open(process.build(DEMO, str(tmp_path / "b"), optimise=False)[0]).read()
    assert "cont_squares = cont.squares" in fitted and fitted.count("cont.squares") == 1
    assert "cont_squares = " not in plain and plain.count("cont.squares") > 1
    assert "layer operations as the rules are written" in fitted and "layer operations" not in plain
    outputs = lambda t: [line.split(".output(")[1] for line in t.splitlines() if ".output(" in line]   # noqa: E731
    assert outputs(fitted) == outputs(plain)                   # the same rules, in the same order
    few = open(process.build(DEMO, str(tmp_path / "c"), only="CO.1, NW.*")[0]).read()
    assert "cont_squares = " not in few                        # another set shares other things
