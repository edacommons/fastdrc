"""The gdscheck backend (the package fastdrc-gdscheck): the drawn rules as a gdscheck PDK."""
import os
import re
import stat
import struct
import sys

import pytest

fastdrc_gdscheck = pytest.importorskip("fastdrc_gdscheck")

from fastdrc import process                                    # noqa: E402
from fastdrc.backend import get, runner                        # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "..", "examples", "demo")
GF180 = os.path.join(HERE, "..", "fastdrc-gf180mcu", "fastdrc_gf180mcu")


def _pdk(folder, **chosen):
    proc, sheets = process.load(folder)
    rules, _ = process.rules_and_warnings(sheets, **chosen)
    eng = get("gdscheck", layermap={k: tuple(v) for k, v in proc["layers"].items()}, title=proc.get("name"))
    return eng, fastdrc_gdscheck.unfold(eng.compile_all(rules))


def _rules(deck_text):
    """``[(id, check, layers, value, {params})]`` of a deck this backend wrote."""
    out = []
    for block in deck_text.split("  - id: ")[1:]:
        lines = block.splitlines()
        fields = dict(line.strip().split(": ", 1) for line in lines[1:] if ": " in line)
        params = {k: v for k, v in fields.items() if k not in ("check", "layers", "value")}
        out.append((lines[0].split('"')[1], fields["check"], fields["layers"].strip("[]").split(", "),
                    float(fields["value"]), params))
    return out


def test_the_backend_is_found_and_runs_its_deck():
    assert runner("gdscheck").name == "gdscheck"


def test_the_demo_deck_is_a_whole_pdk():
    eng, parts = _pdk(DEMO)
    assert set(parts) == {"pdk.yml", "decks/rules.yml", "suites/main.yml"}
    assert "path: decks/rules.yml" in parts["pdk.yml"] and "- deck: rules" in parts["suites/main.yml"]
    rules = _rules(parts["decks/rules.yml"])
    assert rules, eng.problems
    drawn = set(re.findall(r"- name: (\S+)\n    gds_layer", parts["pdk.yml"]))
    derived = parts["pdk.yml"].split("virtual_layers:")[-1].split("layers:")[0] if "virtual_layers:" in parts["pdk.yml"] else ""
    virtual = set(re.findall(r"^  (\w+): +\S", derived, re.M))
    for rid, check, layers, value, params in rules:            # every layer a rule reads is declared
        assert set(layers) <= drawn | virtual, (rid, layers)


def test_checks_are_written_as_gdscheck_names_them():
    _, parts = _pdk(GF180, only=["PL.1_3.3V", "CO.2a", "CO.4", "V1.1", "comp_OFFGRID", "ANGLE_comp", "EF.16a"])
    by_id = {}
    for rid, check, layers, value, params in _rules(parts["decks/rules.yml"]):
        by_id.setdefault(rid, []).append((check, layers, value, params))
    # a space on one layer is between two shapes and across a notch of one: both, under one id
    assert [c for c, *_ in by_id["CO.2a"]] == ["min_space", "min_notch"]
    assert by_id["CO.2a"][0][2] == 0.25
    # an enclosure as KLayout reads it, and the shape crossing the edge as a forbidden of its own
    (check, layers, value, params), (also, *_rest) = by_id["CO.4"]
    assert (check, value, also) == ("min_enclosure", 0.07, "forbidden")
    assert params == {"interacting_only": "1", "metric": "euclidian"}
    pdk = parts["pdk.yml"]
    assert f"{layers[0]}:" in pdk and "comp not mvpsd not mvsd" in pdk       # the layers it reads are sentences
    # exactly four contacts: the count is said of the positive word
    assert "interacting min 4 max 4 contact)" in pdk


def test_a_rule_with_no_form_is_left_out_whole_and_named():
    eng, parts = _pdk(GF180, only=["DCF.1a", "CO.1"])
    assert [p.split(" ")[0] for p in eng.problems] == ["DCF.1a"]
    assert "DCF.1a" in parts["pdk.yml"].split("name:")[0]        # said at the head of the PDK
    assert {r[0] for r in _rules(parts["decks/rules.yml"])} == {"CO.1"}
    assert "comp_dummy" not in parts["pdk.yml"]                  # and nothing of it stays behind


def test_build_with_writes_the_pdk_folder(tmp_path):
    process.build(DEMO, str(tmp_path), extra=("gdscheck",))
    (folder,) = [p for p in tmp_path.iterdir() if p.name.endswith(".gdscheck.pdk")]
    assert {str(p.relative_to(folder)) for p in folder.rglob("*.yml")} == {
        "pdk.yml", os.path.join("decks", "rules.yml"), os.path.join("suites", "main.yml")}


def _gds(path, cells):
    """A GDS file of empty cells; `cells` is ``{name: [names it places]}``."""
    def rec(kind, body=b""):
        body += b"\0" * (len(body) % 2)
        return struct.pack(">HH", len(body) + 4, kind) + body
    out = rec(0x0002, struct.pack(">h", 600)) + rec(0x0102, b"\0" * 24) + rec(0x0206, b"LIB")
    out += rec(0x0305, b"\0" * 16)
    for name, placed in cells.items():
        out += rec(0x0502, b"\0" * 24) + rec(0x0606, name.encode())
        for other in placed:
            out += rec(0x0A00) + rec(0x1206, other.encode()) + rec(0x1003, struct.pack(">ii", 0, 0)) + rec(0x1100)
        out += rec(0x0700)
    path.write_bytes(out + rec(0x0400))


def test_the_top_cell_is_the_one_nothing_places(tmp_path):
    _gds(tmp_path / "a.gds", {"LEAF": [], "TOP": ["LEAF", "MID"], "MID": ["LEAF"]})
    assert fastdrc_gdscheck.top_cell(str(tmp_path / "a.gds")) == "TOP"


def test_run_unfolds_the_pdk_starts_gdscheck_and_reads_its_report(tmp_path, monkeypatch):
    deck, *_ = process.build(DEMO, str(tmp_path), backend="gdscheck")
    ids = [r[0] for r in _rules(fastdrc_gdscheck.unfold(open(deck).read())["decks/rules.yml"])
           if r[0] != fastdrc_gdscheck.EXTENT]                    # the entry that reads every layer is not a rule
    _gds(tmp_path / "chip.gds", {"CHIP": []})
    stand_in = tmp_path / "gdscheck"                              # says what it was asked, reports one marker
    stand_in.write_text(f"""#!{sys.executable}
import sys
a = dict(zip(sys.argv[2::2], sys.argv[3::2]))
assert sys.argv[1] == "run" and a["--suite"] == "main" and a["--topcell"] == "CHIP", sys.argv
assert open(a["--process"]).read().startswith("# Generated by FastDRC"), a
open(a["--report"], "w").write('''<report-database><items><item><category>'{ids[0]}'</category>
<cell>CHIP</cell><values><value>edge: (1.5,2;1.5,3.25)</value><value>text: 'gap 0.1 &lt; 0.2 at (9,9)'</value></values>
</item></items></report-database>''')
sys.exit(2)
""")
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GDSCHECK_BIN", str(stand_in))
    found = runner("gdscheck").run(deck, str(tmp_path / "chip.gds"))
    assert os.path.exists(deck + ".pdk/decks/rules.yml")
    assert set(found["rules"]) == set(ids)                         # every rule of the deck, the clean ones too
    assert found["rules"][ids[0]][1] == [(1.5, 2.0, 1.5, 3.25)]
    assert all(boxes == [] for rid, (_, boxes) in found["rules"].items() if rid != ids[0])


def _by_id(only):
    _, parts = _pdk(GF180, only=only)
    out = {}
    for rid, check, layers, value, params in _rules(parts["decks/rules.yml"]):
        out.setdefault(rid, []).append((check, layers, value, params))
    return out, parts["pdk.yml"]


def test_a_rule_of_one_column_reads_the_shapes_of_that_column():
    by_id, pdk = _by_id(["DF.3a_3.3V", "DF.3a_5V", "DF.4a_5V"])
    sentence = dict(re.findall(r"^  (\w+): +(.+)$", pdk, re.M))
    # not under the marker: the layer without the shapes that touch it
    (space, layers, value, _), (notch, *_rest) = by_id["DF.3a_3.3V"]
    assert (space, notch, value) == ("min_space", "min_notch", 0.28)
    assert sentence[layers[0]].endswith("not_interacting dualgate not_interacting v5_xtor")
    # under it: those shapes, and the space from one of them to one of the rest
    checks = by_id["DF.3a_5V"]
    assert [c for c, *_ in checks] == ["min_space", "min_notch", "min_space"]
    chosen, rest = checks[2][1]
    assert sentence[chosen].endswith("overlapping dualgate") and sentence[rest].endswith(f"not ({sentence[chosen]})")
    # two layers: every pair with one of them in it, once
    (_, (a, b), *_), (_, (a_rest, b_chosen), *_) = by_id["DF.4a_5V"]
    assert sentence[a].endswith("overlapping dualgate") and "overlapping dualgate" not in sentence[b]
    assert sentence[a_rest].endswith(f"not ({sentence[a]})") and sentence[b_chosen] == sentence[b] + " overlapping dualgate"


def test_what_klayout_says_in_several_operations_is_one_gdscheck_check():
    by_id, pdk = _by_id(["PL.2_3.3V", "PL.7_5V", "DF.2a_5V", "CO.6a", "V1.3d", "V1.2b", "M1.4", "DCF.1d"])
    sentence = dict(re.findall(r"^  (\w+): +(.+)$", pdk, re.M))
    (check, (poly, gate), value, params), = by_id["PL.2_3.3V"]         # the poly between the walls it shares with the gate
    assert (check, poly, value, params) == ("min_gate_length", "poly2", 0.28, {})
    assert sentence[gate].startswith("poly2 and comp ") and sentence[gate].endswith("not_interacting v5_xtor")
    assert by_id["PL.7_5V"][0][3] == {"angle": "bent"}
    assert by_id["DF.2a_5V"][0][:2] == ("min_gate_length", ["comp", by_id["DF.2a_5V"][0][1][1]])       # a channel's width
    assert by_id["CO.6a"] == [("min_enclosure", ["metal1", "contact"], 0.06, {
        "interacting_only": "1", "sides": "line_end", "max_width": "0.34", "min_length": "0.24"})]
    assert by_id["V1.3d"] == [("min_enclosure", ["metal1", "via1"], 0.06, {
        "interacting_only": "1", "sides": "adjacent", "trigger": "0.04"})]
    assert by_id["V1.2b"] == [("min_space", ["via1"], 0.36, {                # 4 x 4 or larger: MORE than 3 rows
        "rows": "3", "cols": "3", "axes": "2", "pitch": "0.361", "count": "16"})]   # a gap AT 0.36 is the array's still
    assert by_id["M1.4"] == [("min_density", ["metal1"], 30.000001, {})]            # "> 30": 30 itself is reported
    assert by_id["DCF.1d"][0][0] == "max_density" and by_id["DCF.1d"][0][2] == 70.0


def test_a_share_of_the_chip_is_of_the_whole_layouts_extent(tmp_path, monkeypatch):
    """gdscheck takes the chip as the extent of the layers its rules read: one entry
    reads every layer of the process, and at a run every other layer of the layout."""
    by_id, pdk = _by_id(["PL.8"])
    sentence = dict(re.findall(r"^  (\w+): +(.+)$", pdk, re.M))
    (check, (nothing,), *_), = by_id[fastdrc_gdscheck.EXTENT]
    assert check == "forbidden" and " or metal5 " in sentence[nothing] and sentence[nothing].count(" not (") == 1
    assert "- name: metal5\n" in pdk                                # declared, though PL.8 reads poly2 alone
    assert "PL.1" not in by_id and by_id["PL.8"] == [("min_density", ["poly2"], 14.0, {})]
    # at a run: a layer of the layout the process does not know is part of the extent too
    deck, *_ = process.build(GF180, str(tmp_path), backend="gdscheck", only=["PL.8"])
    gds = tmp_path / "chip.gds"
    rec = lambda kind, body=b"": struct.pack(">HH", 4 + len(body), kind) + body          # noqa: E731
    gds.write_bytes(rec(0x0502) + rec(0x0606, b"CHIP") + rec(0x0800) + rec(0x0D02, struct.pack(">h", 900))
                    + rec(0x0E02, struct.pack(">h", 0)) + rec(0x1100) + rec(0x0700) + rec(0x0400))
    assert fastdrc_gdscheck.layers_of(str(gds)) == {(900, 0)}
    stand_in = tmp_path / "gdscheck"
    stand_in.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n")
    stand_in.chmod(stand_in.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("GDSCHECK_BIN", str(stand_in))
    found = runner("gdscheck").run(deck, str(gds))
    assert set(found["rules"]) == {"PL.8"}                          # the entry that reads the layers is not a rule
    ran = open(deck + ".pdk/pdk.yml").read()
    assert "  - name: layout_900_0\n    gds_layer: 900\n    gds_datatype: 0\n" in ran
    assert re.search(r"^  fastdrc_no_shape: +\(.* or layout_900_0\) not \(.* or layout_900_0\)$", ran, re.M)


def test_forms_the_selftest_through_gdscheck_settled():
    by_id, pdk = _by_id(["EF.02", "NP.3a", "PP.3bi", "MIMTM.1"])
    sentence = dict(re.findall(r"^  (\w+): +(.+)$", pdk, re.M))
    # an exact size: the one step of the grid
    assert "with_bbox_min min 0.18 max 0.181)" in sentence[by_id["EF.02"][0][1][0]]
    # shapes drawn edge to edge: reported where the drawn rule's markers have a width, not otherwise
    assert by_id["NP.3a"][0][3] == {"abutting": "report"} and by_id["PP.3bi"][0][3] == {}
    # a space at a PART of a layer: that part's own spaces and those to the rest of the layer
    (_, (part,), *_), _notch, (_, (part_, rest), *_) = by_id["MIMTM.1"]
    assert part == part_ and sentence[part].startswith("metal5 and (fusetop grow 1.06)")
    assert sentence[rest] == f"metal5 not ({sentence[part]})"


def test_most_of_the_gf180mcu_deck_is_written():
    eng, parts = _pdk(GF180)
    written = {r[0] for r in _rules(parts["decks/rules.yml"])}
    assert len(written) >= 1033 and len(eng.problems) <= 7
    assert not written & {p.split(" ")[0] for p in eng.problems}     # whole or not at all


def test_a_check_between_walls_reads_edge_layers():
    by_id, pdk = _by_id(["NP.5b", "NP.3bi", "NP.4a", "PL.4_5V"])
    sentence = dict(re.findall(r"^  (\w+): +(.+)$", pdk, re.M))
    (check, (outer, inner), value, _), = by_id["NP.5b"]
    assert (check, value) == ("min_enclosure", 0.16) and sentence[outer].startswith("nplus edges ")
    assert " edges " in sentence[inner] + " "
    # walls lying on each other are no space, where the drawn rule does not report touching
    (check, (a, b), *_), = by_id["NP.3bi"]
    assert check == "min_space" and sentence[a].endswith(f"not ({sentence[b]})")
    assert by_id["NP.4a"][0][0] == "min_space"                    # KLayout's projection metric: walls facing
    chosen = sentence[by_id["PL.4_5V"][0][1][0]]                  # an edge is of a marker's column if it touches it
    assert chosen.endswith("interacting dualgate")


def test_rules_on_nets_share_one_ordered_list_of_connections():
    by_id, pdk = _by_id(["DN.2b", "NW.2b_3.3V", "NAT.6", "ANT.1", "ANT.2", "ANT.8", "ANT.13", "ANT.15", "ANT.7", "MIMTM.11"])
    assert by_id["DN.2b"] == [("min_space", ["dnwell"], 5.42, {"net": "different"})]      # no notch: one shape, one net
    assert by_id["NAT.6"][0][0] == "max_nets_under" and by_id["NAT.6"][0][1][0] == "nat"
    (check, layers, value, params), = by_id["ANT.2"]
    assert (check, layers, value) == ("antenna_ratio", ["metal1"], 400.0)
    assert {k: params[k] for k in ("metric", "thickness", "diode_factor", "gate_net_of", "level")} == {
        "metric": "sidewall", "thickness": "0.54", "diode_factor": "2", "gate_net_of": "poly2", "level": "metal1"}
    assert by_id["ANT.8"][0][3]["level"] == "contact" and "metric" not in by_id["ANT.8"][0][3]
    assert by_id["ANT.15"][0][3]["level_before"] == "metaltop"   # all but the last connection: short of it
    assert by_id["ANT.7"][0][3]["level"] == "metaltop"
    assert by_id["MIMTM.11"][0][0] == "max_area" and by_id["MIMTM.11"][0][3] == {"scope": "contained"}
    # the connections: each antenna rule's own set is a beginning of the list
    said = re.findall(r"  - connector: (\S+)\n    layers: \[(\S+)\]", pdk.split("connectivity:")[1].split("\nlayers:")[0])
    first = lambda name: next(i for i, pair in enumerate(said) if name in pair)          # noqa: E731
    assert first("contact") < first("metal1") < first("via1") < first("via5") < first("metaltop")
    assert ("fusetop", "via5") in said and ("via5", "fusetop") in said     # a layer that only connects: both ways
    # a part of a connected layer that a net rule reads is tied to it
    part = by_id["NW.2b_3.3V"][0][1][0]
    assert (part, "nwell") in said


def test_single_forms_the_selftest_through_gdscheck_settled():
    by_id, pdk = _by_id(["PL.6", "MIMTM.8b", "HVNESD.7a"])
    sentence = dict(re.findall(r"^  (\w+): +(.+)$", pdk, re.M))
    (check, (layer, on), value, _), = by_id["PL.6"]
    assert (check, layer, value) == ("no_corner", "poly2", 90.0) and sentence[on].startswith("comp not ")
    assert "with_area min 10000 not (fusetop with_area min 10000 max 10000.000001)" in sentence[by_id["MIMTM.8b"][0][1][0]]
    overlap = next(c for c in by_id["HVNESD.7a"] if c[0] == "min_overlap")
    assert " not_inside " in sentence[overlap[1][1]]              # a gate wholly under the SAB is not measured


def test_a_measurement_read_as_a_layer_is_gdschecks_word_for_it():
    """The markers of a check, used as a layer by the rule, are the gaps gdscheck keeps as geometry."""
    _, parts = _pdk(GF180, only=["DCF.8b", "MDN.10c", "SK.14", "SK.17"])
    pdk = parts["pdk.yml"]
    assert "separation_below 20 (res_mk shrink 40 grow 40) not (comp_dummy or" in pdk     # no row of fill in the gap
    assert "enclosure_above 0.2" in pdk and "centers 99%)" in pdk     # reaching further than exactly 0.2, at those edges
    assert "outside_part (" in pdk                                    # the parts of edges no gap lies on
    assert "centers 99% not_interacting (" in pdk                     # the whole edges no gap lies on
    assert {r[0] for r in _rules(parts["decks/rules.yml"])} == {"DCF.8b", "MDN.10c", "SK.14", "SK.17"}


# ---- decks the backend was not written against: nothing here may hold for one process only ----
DOCS = os.path.join(HERE, "..", "docs")
OTHERS = {"demo": DEMO, "sky130": os.path.join(DOCS, "sky130", "after"), "ihp": os.path.join(DOCS, "ihp", "after"),
          "fp45": os.path.join(DOCS, "fp45", "after"), "fp45-blind": os.path.join(DOCS, "fp45", "blind"),
          "figures": os.path.join(DOCS, "figures")}
NO_FORM = {"figures": {"5-pitch", "6-centre-to-edge", "7-total"}}     # centres, and a sum of sides: not in gdscheck


def _there(*decks):
    """Some of these decks are kept beside the repository, not in it."""
    for deck in decks:
        if not os.path.isdir(OTHERS[deck]):
            pytest.skip(f"the {deck} deck is not here")


@pytest.mark.parametrize("deck", sorted(OTHERS))
def test_the_other_decks_are_written_whole(deck):
    _there(deck)
    eng, parts = _pdk(OTHERS[deck])
    assert {p.split(" ")[0] for p in eng.problems} == NO_FORM.get(deck, set())
    assert "rules:" in parts["decks/rules.yml"]


@pytest.mark.skipif(runner("gdscheck").ready() is not None, reason="gdscheck is not installed")
@pytest.mark.parametrize("deck", sorted(OTHERS))
def test_the_other_decks_behave_as_drawn_in_gdscheck(deck, tmp_path, monkeypatch):
    _there(deck)
    monkeypatch.setenv("GDSCHECK_TILE_UM", "500")                # the cases lie in one long, sparse row
    res = process.selftest(OTHERS[deck], str(tmp_path), backend="gdscheck")
    assert res["wrong"] == []
    assert {r for r, state in res["rules"].items() if state == "not in the deck"} == NO_FORM.get(deck, set())
    assert all(state == "ok" for r, state in res["rules"].items() if r not in NO_FORM.get(deck, set()))


def test_forms_the_other_decks_brought():
    by_id = {}
    eng, parts = _pdk(DEMO)
    for rid, check, layers, value, params in _rules(parts["decks/rules.yml"]):
        by_id.setdefault(rid, []).append((check, layers, value, params))
    pdk = parts["pdk.yml"]
    # two halves of a layer that only touch are joined by KLayout: the gate is connected through the poly whole
    assert "- connector: poly\n    layers: [net_poly_and_diff]" in pdk and "net_poly_not_diff" not in pdk
    assert by_id["V1.1"][0][3]["sides"] == "opposite"            # short on two sides that meet: one opposite pair must hold
    assert by_id["CO.3"][0][3] == {"rows": "3", "cols": "3"}     # a 4 x 4 block, tight both ways
    assert [(c, v, p["boundary"]) for c, _, v, p in by_id["DN.1"]] == [("min_density", 20, "chip"), ("max_density", 80, "chip")]
    assert by_id["DN.2"][0][3] == {"scope": "window", "window": "100", "step": "50", "boundary": "chip"}
    _there("ihp", "fp45", "sky130")
    ihp = {}
    for rid, check, layers, value, params in _rules(_pdk(OTHERS["ihp"])[1]["decks/rules.yml"]):
        ihp.setdefault(rid, []).append((check, layers, value, params))
    assert ihp["M1.e"][0][3] == {"width": "0.3", "length": "1"}  # a wide line running alongside for a length
    assert ihp["Slt.i_M1"][0] == ("min_density", ["Metal1", "Metal1_slit"], 6, {"scope": "region", "min_size": "35"})
    check, layers, value, params = ihp["Ant.b_Metal2"][0]        # summed level by level, for the nets without a diode
    assert (check, layers, value, params["diode"], params["diode_area"]) == ("antenna_ratio", ["Metal1", "Metal2"], 200, "without", "0.16")
    assert ihp["Ant.a"][0][3]["antenna_net_of"] == "GatPoly"     # poly over field is a half of the poly: its net is the poly's
    well = [(layers, p) for _, layers, _, p in
            (r[1:] for r in _rules(_pdk(OTHERS["fp45"])[1]["decks/rules.yml"]) if r[0] == "WELL.2")]
    assert [l for l, _ in well] == [["nwell"], ["pwell"], ["nwell", "pwell"]] and all(p["net"] == "different" for _, p in well)
    sky = _pdk(OTHERS["sky130"])[1]["pdk.yml"]
    assert "separation_below 0.05 (poly and (diff or tap)) inside areaid_sc" in sky     # markers selected by a word
