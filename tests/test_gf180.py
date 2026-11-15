"""The GF180MCU deck in examples/: every rule file compiles, and says something."""
import glob
import json
import os

import pytest

from fastdrc import process

DECK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "gf180mcu")


def test_every_rule_compiles(tmp_path):
    deck, text, n, notes = process.build(DECK, str(tmp_path))
    files = glob.glob(os.path.join(DECK, "rules", "*", "*.json"))
    assert len(files) == 420 and n == 449                 # the default: six metals, 9K top metal, MIM option B
    assert notes == []                                    # no rule that checks nothing, no layer without a number
    said = open(text, encoding="utf-8").read()
    assert "(no checkable assertion found)" not in said
    script = open(deck, encoding="utf-8").read()
    there = {os.path.basename(p)[:-5] for p, _ in process.load(DECK)[1]}
    for name in there:                                    # every rule reports under its own name
        assert f'.output("{name}' in script, name
    assert not any(f'.output("{x}' in script for x in ("MT30.1a", "MIM.1"))


def test_one_file_is_one_rule():
    for f in glob.glob(os.path.join(DECK, "rules", "*", "*.json")):
        d = json.load(open(f))
        assert d["name"] == os.path.basename(f)[:-5] and d["desc"], f


def test_the_options_of_the_process_are_parameters(tmp_path):
    read = lambda **kw: open(process.build(DECK, str(tmp_path), optimise=False, **kw)[0]).read()   # noqa: E731
    a = read(profile="gf180mcuA")                         # three metals, the 3 um top metal
    assert "built for metal_stack = 3LM, top_metal = 30K, mim = B" in a
    assert "top_via = via2" in a and "under_top_metal = metal2" in a
    assert '.output("MT30.1a"' in a and '.output("MT.1"' not in a
    thin = read(params=["top_metal=6K", "mim=A"])
    assert "metaltop.width(0.36.um" in thin and '.output("MIM.1"' in thin and '.output("MIMTM.1"' not in thin
    assert '.output("MIM' not in read(params=["mim=none"])
    for name in process.load(DECK)[0]["profiles"]:        # every profile builds, with nothing to remark
        assert process.build(DECK, str(tmp_path), profile=name)[3] == []


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_part_of_it_behaves_as_drawn(tmp_path):
    res = process.selftest(DECK, str(tmp_path), only=["Dualgate,Mcell,BJT,SRAM"])
    assert res["wrong"] == [] and res["stray"] == {}
    assert sum(v == "ok" for v in res["rules"].values()) == len(res["rules"]) == 29
