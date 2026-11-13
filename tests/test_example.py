"""The worked example of docs/from-a-manual.md must keep compiling to what the guide shows."""
import os

import fastdrc.backend_docs      # noqa: F401
import fastdrc.backend_klayout   # noqa: F401
from fastdrc.backend import get
from fastdrc.cli import main

from fastdrc import process

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "..", "examples", "demo")


def test_the_example_builds_without_a_warning(tmp_path):
    deck_path, text_path, n, notes = process.build(DEMO, str(tmp_path))
    assert notes == [] and n == 18                                  # NW.4 is two columns
    deck = open(deck_path).read()
    assert deck.count(".output(") == 18 and "nwell = polygons(1, 0)" in deck
    for line in ["nw_3_l1 = nwell.nets.space(2.um, euclidian, props_ne).polygons(0.001)",
                 "lu_1_l1 = pdiff.and(nwell).not(ndiff.and(nwell).sized(20.um))",
                 "v1_2_l1 = met1.sized(-0.999.um).sized(0.999.um).not_interacting(via1, 2)",
                 "an_1_l1 = antenna_check(gate, met1, 400.0)",
                 "dn_2_l1 = out_of_density.call(met1, 0.0 .. 0.9, chip, 100.0, 50.0)"]:
        assert line in deck, line
    assert open(text_path).read().startswith("# Demo — design rules")


def test_the_stack_drawn_on_one_sheet_makes_the_nets_for_the_others():
    # NW.3 is on the wells sheet; the stack is drawn on the metal sheet
    _, sheets = process.load(DEMO)
    wells = next(d for p, d in sheets if p.endswith("1-wells.sheet.json"))
    assert ["ndiff", "nwell"] in wells["connect"] and "pdiff" in wells["layers"]
    assert "thin" in wells["domains"]                               # the columns come from process.json


def test_the_command_line_takes_the_folder(capsys, tmp_path):
    main(["rules", DEMO, "--to", "docs"])
    out = capsys.readouterr()
    assert out.err == "" and "NW.1 — N-well width" in out.out
    main(["build", DEMO, "-o", str(tmp_path)])
    assert "18 rules ->" in capsys.readouterr().out
    new = tmp_path / "fresh"
    main(["init", str(new)])
    assert (new / "process.json").exists() and (new / "rules").is_dir()
    assert process.rule_files(str(new)) == [] and process.is_process(str(new))


def _demo_text():
    _, sheets = process.load(DEMO)
    rules, _ = process.rules_and_warnings(sheets)
    return get("docs").compile_all(rules)


def test_the_example_reads_back_as_the_guide_says():
    text = _demo_text()
    for sentence in [
            "any two shapes of nwell must be at least 2 µm apart where the two are on different nets",
            "every met1 at least 2 µm wide must hold at least 2 via1.",
            "every part of pdiff combined with nwell must be within 20 µm of ndiff combined with nwell.",
            "met1 must enclose via1 by at least 0.1 µm on at least one of any two adjacent sides.",
            "met1 must cover at most 90 % of every 100 × 100 µm window (stepped by 50 µm) of chip.",
            "the area of met1 connected to gate must be at most 400 times the area of that gate"]:
        assert sentence in text, sentence
    assert "(no checkable assertion found)" not in text
    # a legend entry is shown only under a rule that uses it
    nw1 = text.split("NW.2")[0]
    assert "where" not in nw1
