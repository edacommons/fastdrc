"""The worked example of docs/from-a-manual.md must keep compiling to what the guide shows."""
import json
import os
import zipfile

import pytest

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
    for line in ["nwell_nets = nwell.nets",                         # the nets are read once, with their connections
                 "nw_3_l1 = nwell_nets.space(2.um, euclidian, props_ne).polygons(0.001)",
                 "lu_1_l1 = pdiff.and(nwell).not(ndiff_and_nwell.sized(20.um).and(nwell).interacting(ndiff_and_nwell))",
                 "v1_2_l1 = met1.sized(-0.999.um).sized(0.999.um).not_interacting(via1, 2)",
                 "an_1_l1 = antenna_check(gate, met1, 400.0)",
                 "dn_2_l1 = out_of_density.call(met1, 0.0 .. 0.9, chip, 100.0, 50.0)"]:
        assert line in deck, line
    assert open(text_path).read().startswith("# Demo — design rules")


def test_the_example_is_one_rule_one_file_one_drawing():
    files = process.rule_files(DEMO)
    assert len(files) == 17 and [os.path.relpath(f, os.path.join(DEMO, "rules")) for f in files][:6] == [
        "1-wells/LU.1.json", "1-wells/NW.1.json", "1-wells/NW.2.json", "1-wells/NW.3.json", "1-wells/NW.4.json",
        "2-devices/CO.1.json"]
    for f in files:
        rule = json.load(open(f))
        assert "rules" not in rule and rule["shapes"] and rule["name"] == os.path.basename(f)[:-5]


def test_what_rules_share_is_said_once_in_the_deck():
    # legend, columns and connections come from process.json, not from each rule file
    _, files = process.load(DEMO)
    nw3 = next(d for p, d in files if p.endswith("NW.3.json"))
    assert ["ndiff", "nwell"] in nw3["connect"] and "pdiff" in nw3["layers"] and "thin" in nw3["domains"]
    raw = json.load(open(next(p for p, _ in files if p.endswith("NW.3.json"))))
    assert not ({"connect", "layers", "domains"} & set(raw))


def test_a_rule_file_with_a_value_per_column_is_one_rule_per_column():
    from fastdrc.sheet import rules_of
    _, files = process.load(DEMO)
    nw4 = next(d for p, d in files if p.endswith("NW.4.json"))
    assert [r["name"] for r in rules_of(nw4)] == ["NW.4_thin", "NW.4_thick"]


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
            "every part of pdiff combined with nwell must be within 20 µm of ndiff combined with nwell — one in the same piece of nwell.",
            "met1 must enclose via1 by at least 0.1 µm on at least one of any two adjacent sides.",
            "met1 must cover at most 90 % of every 100 × 100 µm window (stepped by 50 µm) of chip.",
            "the area of met1 connected to gate must be at most 400 times the area of that gate"]:
        assert sentence in text, sentence
    assert "(no checkable assertion found)" not in text
    # a legend entry is shown only under a rule that uses it
    nw1 = text.split("NW.1 —")[1].split("NW.2 —")[0]
    assert "where" not in nw1


# --- a deck in one zip file --------------------------------------------------------------

def test_a_zipped_deck_is_the_same_deck(tmp_path):
    z = process.pack(DEMO, str(tmp_path / "demo.zip"))
    assert process.is_zip(z) and not process.is_zip(DEMO)
    with zipfile.ZipFile(z) as f:
        names = f.namelist()
    assert names[0] == "process.json" and not any(n.startswith("build/") for n in names)
    deck, text, n, notes = process.build(z)
    ref_deck, ref_text, ref_n, _ = process.build(DEMO, str(tmp_path / "ref"))
    assert n == ref_n and notes == []
    assert open(deck).read() == open(ref_deck).read() and open(text).read() == open(ref_text).read()
    # what is generated lands beside the zip, never in it
    assert os.path.dirname(deck) == str(tmp_path / "demo.build")
    with zipfile.ZipFile(z) as f:
        assert f.namelist() == names
    _, files = process.load(z)
    assert all(p.startswith(z + os.sep + "rules") for p, _ in files)


def test_a_zip_of_the_folder_itself_and_back(tmp_path):
    z = str(tmp_path / "top.zip")
    with zipfile.ZipFile(z, "w") as f:                       # as "zip -r demo.zip demo" makes it
        for path in [os.path.join(DEMO, "process.json")] + process.rule_files(DEMO):
            f.write(path, "demo/" + os.path.relpath(path, DEMO))
    assert process.build(z)[2] == process.build(DEMO, str(tmp_path / "ref"))[2]
    out = process.unpack(z, str(tmp_path / "out"))
    assert process.is_process(out) and len(process.rule_files(out)) == len(process.rule_files(DEMO))


def test_a_zip_entry_outside_the_deck_is_refused(tmp_path):
    z = str(tmp_path / "bad.zip")
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("process.json", "{}")
        f.writestr("../escaped.json", "{}")
    with pytest.raises(ValueError, match="outside the deck"):
        process.unpack(z, str(tmp_path / "out"))
    assert not (tmp_path / "escaped.json").exists()


def test_the_command_line_takes_a_zip(capsys, tmp_path):
    from fastdrc.cli import main
    z = str(tmp_path / "fresh.zip")
    main(["init", z])
    assert process.is_zip(z) and process.load(z)[0]["name"] == "fresh"
    main(["pack", DEMO, "-o", str(tmp_path / "demo.zip")])
    main(["build", str(tmp_path / "demo.zip")])
    assert (tmp_path / "demo.build" / "demo.drc").exists()
    main(["unpack", str(tmp_path / "demo.zip")])
    assert process.is_process(str(tmp_path / "demo"))
    with pytest.raises(SystemExit):                          # never over a folder that has something in it
        main(["unpack", str(tmp_path / "demo.zip")])
    main(["rules", str(tmp_path / "demo.zip"), "--to", "docs"])
    assert "NW.1" in capsys.readouterr().out
