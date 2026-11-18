"""The GF180MCU deck (the package fastdrc-gf180mcu): every rule file compiles, and says something."""
import glob
import json
import os

import pytest

from fastdrc import process

DECK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fastdrc-gf180mcu", "fastdrc_gf180mcu")


def test_every_rule_compiles(tmp_path):
    deck, text, n, notes = process.build(DECK, str(tmp_path))
    files = glob.glob(os.path.join(DECK, "rules", "*", "*.json"))
    grid = len(process.load(DECK)[0]["layers"])              # one grid check per layer
    assert len(files) == 775 and n == 835 + 2 * grid == 1045  # the default; the grid and the angle, per layer
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
    assert "built for metal_stack = 3LM, top_metal = 30K, mim = A" in a   # option B needs four metals or more
    assert "top_via = via2" in a and "under_top_metal = metal2" in a
    assert '.output("MT30.1a"' in a and '.output("MT.1"' not in a
    assert "top_metal_layer = metal3" in a and "top_metal_layer.width(1.8.um" in a   # the top metal of three
    assert not any(f'.output("{x}' in a for x in ("M3.1", "M4.1", "M5.1", "V3.1", "V4.1", "V5.1"))
    thin = read(params=["top_metal=6K", "mim=A"])
    assert "top_metal_layer.width(0.36.um" in thin and '.output("M5.1"' in thin and '.output("V5.1"' in thin and '.output("MIM.1"' in thin and '.output("MIMTM.1"' not in thin
    assert '.output("MIM' not in read(params=["mim=none"])
    for name in process.load(DECK)[0]["profiles"]:        # every profile builds, with nothing to remark
        assert process.build(DECK, str(tmp_path), profile=name)[3] == []
    wedge = read()                                        # the bond pad: wedge by default, no circuit under it
    assert '.output("PAD.17"' in wedge and '.output("CUP.5"' not in wedge and '.output("TM.6"' not in wedge
    ball = read(params=["bond_pad=ball"])
    assert '.output("CUP.5"' in ball and '.output("PAD.17"' not in ball
    bump = read(params=["bond_pad=solder_bump"])
    assert '.output("TM.6"' in bump and '.output("GR.5' in bump and '.output("PAD.1"' not in bump
    assert "centres_closer.call(probe_pad, nil, 80)" in bump and '.output("TM.4"' in bump      # the pitch, centre to centre
    assert ", nil, 70, 1.3, " in bump and '.output("TM.5"' in bump                              # ... growing with the UBM opening
    assert "side_sums.call(comp, lv_esd_gate, true, 160, nil)" in wedge                        # the total finger width


def test_the_antenna_rules_count_the_side_wall_and_the_diode(tmp_path):
    deck = open(process.build(DECK, str(tmp_path), only=["ANT.2,ANT.9,ANT.14"], optimise=False)[0]).read()
    assert "perimeter(x) * 0.54 / (area + 2 * area(d1) + 2 * area(d2))" in deck      # metal1: side wall, gate + 2 x COMP
    assert "area(x) / (area + 2 * area(d1) + 2 * area(d2))" in deck                  # via1: its area
    assert 'evaluate_nets(fusetop, { "x" => top_metal_layer, "d1" => ncomp, "d2" => pcomp }' in deck    # the MIM top plate as the gate
    assert "perimeter(x) * 0.99 / (area + 15 * area(d1) + 15 * area(d2))" in deck    # ... helped by 15 x the COMP below it
    before = deck.split("ant_14_l1 =")[0]                                             # the stack below the plate stands by then
    assert "connect(contact, ncomp)" in before and "connect(via5, metaltop)" in before and "clear_connections" not in before


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_part_of_it_behaves_as_drawn(tmp_path):
    res = process.selftest(DECK, str(tmp_path), only=["Dualgate,Mcell,BJT,SRAM"])
    assert res["wrong"] == [] and res["stray"] == {}
    assert sum(v == "ok" for v in res["rules"].values()) == len(res["rules"]) == 30


@pytest.mark.skipif(not process.klayout_binary(), reason="KLayout is not installed")
def test_its_layouts_are_read_as_they_should_be(tmp_path):
    # whole devices drawn by hand: every rule of the deck gets its say on each
    assert len(process.deck_layouts(DECK)) == 150
    res = process.selftest(DECK, str(tmp_path), only=["LDNMOS,LDPMOS,HRES,PRES,LRES,Poly2,Pplus,Nplus,Dualgate,OTP,"
                                                      "DummyExclude,MIM-B,Grid,COMP,Wells,SAB,Contact"])
    assert [line for line in res["wrong"] if line.startswith("layout ")] == []
    assert set(res["layouts"].values()) == {"ok"}
