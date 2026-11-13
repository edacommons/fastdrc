"""A sheet: one drawn scene, many rules -- the way a design-manual figure works."""

import fastdrc.backend_docs      # noqa: F401
import fastdrc.backend_klayout   # noqa: F401
from fastdrc.backend import get
from fastdrc.sheet import is_sheet, rules_of


def _rect(name, x0, y0, x1, y1, straight=()):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"name": name, "edges": [
        {"id": f"{name.lower()}{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
         "edge": i in straight} for i in range(4)]}


M = lambda e: {"kind": "match", "expr": e}     # noqa: E731
C = lambda e: {"kind": "check", "expr": e}     # noqa: E731

SHEET = {
    "name": "SAB", "desc": "salicide block, one figure",
    "shapes": [_rect("COMP", 0, 0, 16, 6, {0, 1}), _rect("POLY", 0, 9, 16, 11, {1}),
               _rect("SAB", 5, -3, 11, 13, {0, 1}),          # a band across both bars
               _rect("K", 7, 11.5, 9, 12.5),                 # a contact inside the band
               _rect("SAB2", 20, 0, 24, 6, {1, 3}), _rect("SAB3", 27, 0, 31, 6, {3}),
               _rect("OTP", 36, 0, 40, 6)],
    "measures": [{"name": "W1", "from": "sab23", "to": "sab21"},
                 {"name": "S2", "from": "sab21", "to": "sab33"},
                 {"name": "E6", "from": "comp0", "to": "sab0"},
                 {"name": "E7", "from": "sab1", "to": "comp1"},
                 {"name": "E10", "from": "sab1", "to": "poly1"},
                 {"name": "X8", "from": "k0", "to": "sab0", "crossed": True},
                 {"name": "L2", "from": "sab22", "to": "otp2"},      # links: these blocks
                 {"name": "L3", "from": "sab32", "to": "otp2"}],     # lie outside OTP
    "constraints": [M("COMP.layer == comp"), M("POLY.layer == poly2"), M("SAB.layer == sab"),
                    M("K.layer == contact"), M("SAB2.layer == sab"), M("SAB3.layer == sab"),
                    M("OTP.layer == otp_mk")],
    "rules": [
        {"name": "SB.1", "desc": "min width", "constraints": [C("W1 >= 0.42")], "without": ["OTP"]},
        {"name": "SB.2", "desc": "min spacing", "constraints": [C("S2 >= 0.42")]},
        {"name": "SB.6", "desc": "sab beyond comp", "constraints": [C("E6 >= 0.22")]},
        {"name": "SB.7", "desc": "comp beyond sab", "constraints": [C("E7 >= 0.22")]},
        {"name": "SB.8", "desc": "no contact in sab", "measures": ["X8"]},
        {"name": "SB.10", "desc": "poly beyond sab", "constraints": [C("E10 >= 0.22")]}]}


def _ops(deck):
    return {ln.split("=", 1)[0].strip(): ln.split("=", 1)[1].strip()
            for ln in deck.splitlines() if "_l1 =" in ln}


def test_each_rule_reads_only_its_part_of_the_scene():
    assert is_sheet(SHEET)
    rules = {r["name"]: r for r in rules_of(SHEET)}
    names = lambda n: [s["name"] for s in rules[n]["shapes"]]       # noqa: E731
    assert names("SB.1") == ["SAB2"]                 # `without` drops the linked marker
    assert names("SB.2") == ["SAB2", "SAB3", "OTP"]  # the links bring the marker in
    assert names("SB.6") == ["COMP", "SAB"]          # the contact and the poly are not its business
    assert names("SB.8") == ["SAB", "K"]             # a cross on a relation: just the two
    assert names("SB.10") == ["POLY", "SAB"]


def test_a_sheet_compiles_to_one_deck_and_one_text():
    deck = get("klayout").compile_data(SHEET)
    assert deck.count('sab = input("sab")') == 1          # declarations once
    assert _ops(deck) == {
        "sb_1_l1": "sab.width(0.42.um, euclidian).polygons(0.001)",
        "sb_2_l1": "sab.outside(otp_mk).space(0.42.um, euclidian).polygons(0.001)",
        "sb_6_l1": "sab.enclosing(comp, 0.22.um, euclidian).polygons(0.001)",
        "sb_7_l1": "comp.enclosing(sab, 0.22.um, euclidian).polygons(0.001)",
        "sb_8_l1": "contact.inside(sab)",
        "sb_10_l1": "poly2.enclosing(sab, 0.22.um, euclidian).polygons(0.001)"}
    en = get("docs").compile_data(SHEET)
    assert "comp must extend at least 0.22 µm beyond sab" in en
    assert "contact must not lie inside sab." in en


def test_something_drawn_under_an_arrow_takes_part():
    # move the contact under the SB.6 arrow: it now scopes that rule
    sheet = dict(SHEET, shapes=[_rect("K", 7, -2.5, 9, -0.5) if s["name"] == "K" else s
                                for s in SHEET["shapes"]])
    assert _ops(get("klayout").compile_data(sheet))["sb_6_l1"].endswith(".overlapping(contact)")


def test_a_plain_rule_is_its_own_single_rule():
    rule = {"name": "R", "shapes": [], "measures": [], "constraints": []}
    assert not is_sheet(rule) and rules_of(rule) == [rule]


POLY = {
    "name": "Poly2",
    # the columns of the manual's table, each named once by where it applies
    "domains": {"3.3V": [["not_interacting", "v5_xtor"], ["not_interacting", "dualgate"]],
                "5V": [["overlapping", "dualgate"]]},
    "shapes": [_rect("COMP", 0, 0, 16, 10, {1, 3}),
               _rect("G1", -3, 6, 19, 7.5, {0, 2, 3}),     # a gate across the diffusion
               _rect("G2", -3, 2.5, 19, 4, {2}),           # a second gate
               _rect("LINE", 22, 0, 23.5, 10, {1, 3})],    # a poly line on field
    "measures": [{"name": "W2", "from": "g10", "to": "g12", "edges": True, "at": 8},
                 {"name": "S3", "from": "g22", "to": "g10", "at": -2},
                 {"name": "S3c", "from": "g22", "to": "g10", "at": 8},
                 {"name": "E4", "from": "comp3", "to": "g13"},
                 {"name": "S5", "from": "line3", "to": "comp1", "at": 5}],
    "constraints": [M("COMP.layer == comp"), M("G1.layer == poly2"), M("G2.layer == poly2"),
                    M("LINE.layer == poly2")],
    "rules": [
        {"name": "PL.2", "desc": "gate width", "constraints": [C("W2 >= ?")],
         "values": {"3.3V": 0.28}, "except": ["otp_mk", "ymtp_mk"]},
        {"name": "PL.3a", "desc": "space", "constraints": [C("S3 >= ?")],
         "values": {"3.3V": 0.24, "5V": 0.24}},
        {"name": "PL.3c", "desc": "gate space on comp", "constraints": [C("S3c >= 0.24")]},
        {"name": "PL.4", "desc": "end cap", "constraints": [C("E4 >= ?")],
         "values": {"3.3V": 0.22, "5V": 0.22}},
        {"name": "PL.5a", "desc": "field poly to comp", "constraints": [C("S5 >= ?")],
         "values": {"3.3V": 0.1, "5V": 0.3}}]}


def test_a_table_rule_becomes_one_rule_per_column_scoped_to_it():
    ops = _ops(get("klayout").compile_data(POLY))
    assert ops["pl_4_3_3v_l1"] == ("poly2.enclosing(comp, 0.22.um, euclidian).polygons(0.001)"
                                   ".not_interacting(dualgate).not_interacting(v5_xtor)")
    assert ops["pl_4_5v_l1"] == ("poly2.enclosing(comp, 0.22.um, euclidian).polygons(0.001)"
                                 ".overlapping(dualgate)")
    assert ops["pl_5a_5v_l1"].startswith("poly2.separation(comp, 0.3.um, euclidian)")
    assert [r["name"] for r in rules_of(POLY)][:3] == ["PL.2_3.3V", "PL.3a_3.3V", "PL.3a_5V"]
    en = get("docs").compile_data(POLY)
    assert "only where the geometry is away from dualgate and v5_xtor" in en


def test_where_the_arrow_is_put_decides_what_it_measures():
    ops = _ops(get("klayout").compile_data(POLY))
    # the same two edges: an arrow on field measures poly spacing; an arrow put over
    # the diffusion measures the spacing of the gates
    assert ops["pl_3a_5v_l1"].startswith("poly2.space(0.24.um, euclidian)")
    assert ops["pl_3c_l1"] == "poly2.and(comp).space(0.24.um, euclidian).polygons(0.001)"


def test_a_section_note_excepts_layers_without_drawing_cutouts():
    ops = _ops(get("klayout").compile_data(POLY))
    assert ops["pl_2_3_3v_l1"] == (
        "poly2.edges.and(poly2.and(comp).edges).not(otp_mk).not(ymtp_mk).width(0.28.um, euclidian)"
        ".polygons(0.001).not_interacting(dualgate).not_interacting(v5_xtor)")
    whole = dict(POLY, **{"except": ["otp_mk"]})          # on the sheet: every rule
    assert _ops(get("klayout").compile_data(whole))["pl_4_5v_l1"].startswith(
        "poly2.not(otp_mk).enclosing(comp.not(otp_mk), 0.22.um")


def _named(name, x0, y0, x1, y1, straight=()):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"name": name, "edges": [
        {"id": f"{name.lower()}_{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
         "edge": i in straight} for i in range(4)]}


CONTACT = {
    "name": "Contact",
    "shapes": [_named("COMP", 0, 0, 20, 12, {1, 3}), _named("GATE", 9, -4, 11, 16, {3}),
               _named("KC", 4, 5, 5, 6, {0, 1, 2, 3}), _named("KD", 4, 8, 5, 9, {0}),
               _named("M1", 3, 4, 6, 7, {3}),
               _named("LINE", 24, 0, 26, 12, {1}), _named("KP", 24.5, 5, 25.5, 6, {1, 3}),
               _named("KS", 25.5, 9, 26.5, 10),            # wrong: across the poly line's edge
               _named("KG", 9.5, 5, 10.5, 6),              # wrong: on the gate
               _named("ON", 36, 0, 44, 6), _named("KO", 39, 2, 40, 3)],
    "measures": [{"name": "S2", "from": "kc_2", "to": "kd_0"},
                 {"name": "E3", "from": "kp_1", "to": "line_1"},
                 {"name": "E6", "from": "m1_3", "to": "kc_3"},
                 {"name": "S7", "from": "kc_1", "to": "gate_3", "at": 5.5},
                 {"name": "S8", "from": "kp_3", "to": "comp_1", "at": 5.5},
                 {"name": "XS3", "from": "ks_0", "to": "line_0", "crossed": "straddle"}],
    "constraints": [M("COMP.layer == comp"), M("GATE.layer == poly2"), M("LINE.layer == poly2"),
                    M("M1.layer == metal1"), M("ON.layer in {comp, poly2}")]
    + [M(f"{k}.layer == contact") for k in ("KC", "KD", "KP", "KS", "KG", "KO")],
    "rules": [
        {"name": "CO.2a", "constraints": [C("S2 >= 0.25")]},
        {"name": "CO.3", "constraints": [C("E3 >= 0.07")]},
        {"name": "CO.3", "measures": ["XS3"]},              # same rule: the forbidden example
        {"name": "CO.6", "constraints": [C("E6 >= 0.005"), C("KC in M1")]},
        {"name": "CO.7", "constraints": [C("S7 >= 0.15")]},
        {"name": "CO.8", "constraints": [C("S8 >= 0.17")], "with": ["LINE"]},
        {"name": "CO.10", "cross": ["KG"]},
        {"name": "CO.11", "constraints": [C("KO in ON")]}]}


def _all_ops(deck):
    return [ln.split("=", 1)[1].strip() for ln in deck.splitlines() if "_l1 =" in ln]


def test_the_contact_figure_as_one_sheet():
    assert _all_ops(get("klayout").compile_data(CONTACT)) == [
        # the diffusion and the metal hold the whole contact: backgrounds, not part of a spacing
        "contact.space(0.25.um, euclidian).polygons(0.001)",
        "poly2.enclosing(contact, 0.07.um, euclidian).polygons(0.001)",
        # the crossed-out example: a contact across the line's edge
        "contact.overlapping(poly2).not_inside(poly2)",
        "metal1.enclosing(contact, 0.005.um, euclidian).polygons(0.001)",
        "contact.not_inside(metal1)",
        # the gate is only partly on the diffusion -> the diffusion takes part, for both ends
        "contact.and(comp).separation(poly2.and(comp), 0.15.um, euclidian).polygons(0.001)",
        # a background named by the rule
        "contact.and(poly2).separation(comp, 0.17.um, euclidian).polygons(0.001)",
        # an X on a shape: everything it is drawn on counts
        "contact.and(comp).and(poly2)",
        # "must be on diffusion or poly": inside a shape whose layer is a set
        "contact.not_inside(comp.or(poly2))"]
    en = get("docs").compile_data(CONTACT)
    assert "contact must not straddle the edge of poly2." in en
    assert "every contact must lie wholly inside comp or poly2." in en


def test_a_rule_without_a_number_can_still_hold_per_column():
    sheet = {"name": "S", "domains": {"3.3V": [["not_interacting", "dualgate"]],
                                      "5V": [["overlapping", "dualgate"]]},
             "shapes": [_rect("IMP", 0, 0, 14, 14), _rect("CI", 4, 4, 10, 10)],
             "measures": [],
             "constraints": [M("IMP.layer in {nplus, pplus}"), M("CI.layer == comp")],
             "rules": [{"name": "DF.12", "constraints": [C("CI in IMP")], "columns": ["3.3V", "5V"]}]}
    assert _ops(get("klayout").compile_data(sheet)) == {
        "df_12_3_3v_l1": "comp.not_inside(nplus.or(pplus)).not_interacting(dualgate)",
        "df_12_5v_l1": "comp.not_inside(nplus.or(pplus)).overlapping(dualgate)"}


def test_an_arrow_one_rule_bounds_is_not_a_link_for_the_others():
    # E8 has wavy ends, but a rule gives it a number: it is a distance. It must not be
    # followed as a link that drags the tap into the well's other rules.
    sheet = {"name": "S",
             "shapes": [_rect("NW", 0, 0, 20, 14), _rect("PD", 4, 4, 9, 10), _rect("ND", 24, 4, 29, 10)],
             "measures": [{"name": "E8", "from": "nw3", "to": "pd3"},
                          {"name": "S9", "from": "nw1", "to": "nd3"}],
             "constraints": [M("NW.layer == nwell"), M("PD.layer == pdiff"), M("ND.layer == ndiff")],
             "rules": [{"name": "d.8", "constraints": [C("E8 >= 0.18")]},
                       {"name": "d.9", "constraints": [C("S9 >= 0.34")]}]}
    assert _ops(get("klayout").compile_data(sheet)) == {
        "d_8_l1": "nwell.enclosing(pdiff, 0.18.um, euclidian).polygons(0.001)",
        "d_9_l1": "nwell.separation(ndiff, 0.34.um, euclidian).polygons(0.001)"}


def test_cli_warns_when_a_rule_checks_nothing(tmp_path, capsys):
    import json
    from fastdrc.cli import main
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"name": "EMPTY.1", "shapes": [_rect("A", 0, 0, 4, 4)], "measures": [],
                             "constraints": [M("A.layer == met1")]}))
    main(["rules", str(f)])
    assert "rule EMPTY.1 checks nothing" in capsys.readouterr().err


def test_cli_warns_about_a_layer_the_layer_map_does_not_give(tmp_path, capsys):
    # found on real cells: a rule on a layer read by name sees nothing and flags it all
    import json
    from fastdrc.cli import main
    f, lm = tmp_path / "r.json", tmp_path / "lm.json"
    f.write_text(json.dumps({"name": "E.1", "shapes": [_rect("M", 0, 0, 10, 10), _rect("V", 4, 4, 6, 6)],
                             "measures": [{"name": "E", "from": "m3", "to": "v3"}],
                             "constraints": [M("M.layer == met1"), M("V.layer == via"), C("E >= 0.03")]}))
    lm.write_text(json.dumps({"via": [67, 44]}))
    main(["rules", str(f), "--layermap", str(lm)])
    err = capsys.readouterr().err
    assert "no layer number for met1" in err and "via" not in err


def test_a_same_layer_shape_on_another_rules_arrow_is_reported():
    # met while extending a real sheet: a contact added for one rule sat on the arrow of
    # another and silently became part of it
    sheet = {"name": "S", "shapes": [_rect("D", 0, 0, 20, 12), _rect("KA", 8, 5, 9, 6), _rect("KX", 3.5, 5.2, 4.5, 5.8)],
             "measures": [{"name": "E", "from": "d3", "to": "ka3"}],
             "constraints": [M("D.layer == diff"), M("KA.layer == cont"), M("KX.layer == cont")],
             "rules": [{"name": "c.1", "constraints": [C("E >= 0.07")]}]}
    (rule,) = rules_of(sheet)
    assert [s["name"] for s in rule["shapes"]] == ["D", "KA", "KX"]
    assert "shape KX lies on an arrow of this rule" in rule["warnings"][0]
    sheet["rules"][0]["without"] = ["KX"]
    (rule,) = rules_of(sheet)
    assert [s["name"] for s in rule["shapes"]] == ["D", "KA"] and "warnings" not in rule


def test_columns_that_leave_a_gap_between_them_are_reported():
    # found on real I/O cells: "outside X" / "inside X" as the two columns of a table both
    # drop a violation whose marker lies across X's edge
    from fastdrc.sheet import sheet_warnings
    gap = {"domains": {"thin": [["outside", "tgo"]], "thick": [["inside", "tgo"]]}}
    assert "lies across the edge of tgo" in sheet_warnings(gap)[0]
    ok = {"domains": {"thin": [["not_interacting", "tgo"]], "thick": [["overlapping", "tgo"]]}}
    assert sheet_warnings(ok) == [] and sheet_warnings({}) == []


def test_cli_warns_when_a_net_rule_has_no_connectivity(tmp_path, capsys):
    import json
    from fastdrc.cli import main
    f = tmp_path / "r.json"
    rule = {"name": "N.1", "shapes": [_rect("A", 0, 0, 4, 4), _rect("B", 8, 0, 12, 4)],
            "measures": [{"name": "S", "from": "a1", "to": "b3"}],
            "constraints": [M("A.layer == nwell"), M("B.layer == nwell"), M("A.net != B.net"), C("S >= 1.4")]}
    f.write_text(json.dumps(rule))
    main(["rules", str(f)])
    assert "is about nets but nothing says how layers connect" in capsys.readouterr().err
    f.write_text(json.dumps(dict(rule, connect=[["nwell", "ntap"]])))
    main(["rules", str(f)])
    assert "about nets" not in capsys.readouterr().err


def test_connectivity_is_read_from_a_drawn_stack():
    # a tap in the well, a contact on the tap, metal over the contact -- each pair stated
    # to be one net where they touch; two wells apart, on different nets, is the rule
    sheet = {"name": "W",
             "shapes": [_rect("A", 0, 0, 8, 10), _rect("B", 12, 0, 20, 10),
                        _rect("W", 30, 0, 50, 20), _rect("T", 34, 4, 46, 16), _rect("K", 38, 8, 42, 12),
                        _rect("MT", 36, 6, 44, 14)],
             "measures": [{"name": "S", "from": "a1", "to": "b3"}],
             "constraints": [M("A.layer == nwell"), M("B.layer == nwell"), M("W.layer == nwell"),
                             M("T.layer == ntap"), M("K.layer == cont"), M("MT.layer == met1"),
                             M("T.net == W.net"), M("K.net == T.net"), M("K.net == MT.net")],
             "rules": [{"name": "w.2", "constraints": [M("A.net != B.net"), C("S >= 1.4")]}]}
    (rule,) = rules_of(sheet)
    assert rule["connect"] == [["ntap", "nwell"], ["cont", "ntap"], ["cont", "met1"]]
    assert [s["name"] for s in rule["shapes"]] == ["A", "B"]        # the stack is not part of the rule
    deck = get("klayout").compile_data(sheet)
    assert "connect(ntap, nwell)" in deck and "connect(cont, met1)" in deck
    assert "w_2_l1 = nwell.nets.space(1.4.um, euclidian, props_ne).polygons(0.001)" in deck


def test_a_net_equality_between_shapes_apart_is_a_filter_not_a_connection():
    from fastdrc.rule import Rule
    r = Rule({"shapes": [_rect("A", 0, 0, 8, 10), _rect("B", 12, 0, 20, 10)], "measures": [],
              "constraints": [M("A.layer == nwell"), M("B.layer == pwell"), M("A.net == B.net")]})
    assert r.connections() == [] and r.net_relation("A", "B") == "eq"


ANTENNA = {"name": "A", "layers": {"gate": "poly and diff", "field_poly": "poly and not diff"},
           "shapes": [_rect("G", 10, 4, 12, 8), _rect("P", 10, 8, 12, 20), _rect("K", 10.5, 12, 11.5, 13),
                      _rect("MT", 9, 11, 13, 14), _rect("V", 10.6, 12.1, 11.4, 12.9), _rect("M2", 8, 10, 14, 15)],
           "measures": [],
           "constraints": [M("G.layer == gate"), M("P.layer == field_poly"), M("K.layer == cont"),
                           M("MT.layer == met1"), M("V.layer == via1"), M("M2.layer == met2"),
                           M("P.net == G.net"), M("K.net == P.net"), M("K.net == MT.net"),
                           M("V.net == MT.net"), M("V.net == M2.net")],
           "rules": [{"name": "ant.1", "constraints": [C("MT.area <= 400 * G.area")]},
                     {"name": "ant.p", "constraints": [C("P.area / G.area <= 200")]}]}


def test_antenna_ratio_sees_the_stack_only_up_to_its_own_layer():
    deck = get("klayout").compile_data(ANTENNA).splitlines()
    i = deck.index("ant_1_l1 = antenna_check(gate, met1, 400.0)")
    # metal1 is the last layer made: via1 and metal2 are not connected yet
    assert deck[i - 3:i] == ["connect(field_poly, gate)", "connect(cont, field_poly)", "connect(cont, met1)"]
    j = deck.index("ant_p_l1 = antenna_check(gate, field_poly, 200.0)")
    # a different set for the poly rule (contacts come after poly): the deck starts over
    assert deck[j - 2:j] == ["clear_connections", "connect(field_poly, gate)"]
    en = get("docs").compile_data(ANTENNA)
    assert "the area of met1 connected to gate must be at most 400 times the area of that gate" in en


def _antenna_sheet(rules, extra_shapes=(), extra_facts=()):
    s = dict(ANTENNA, rules=rules)
    s["shapes"] = ANTENNA["shapes"] + list(extra_shapes)
    s["constraints"] = ANTENNA["constraints"] + list(extra_facts)
    return s


def test_a_contact_on_the_gate_does_not_pull_the_metals_into_the_poly_stage():
    # the contact touches the gate directly; the stage is still "poly and what is as
    # close to the gate as poly", not everything reachable through the contact
    sheet = _antenna_sheet([{"name": "ant.p", "constraints": [C("P.area <= 200 * G.area")]}],
                           [_rect("KG", 10.5, 5, 11.5, 6)], [M("KG.layer == cont"), M("KG.net == G.net")])
    deck = get("klayout").compile_data(sheet)
    assert "connect(cont, gate)" in deck and "connect(cont, met1)" not in deck


def test_cumulative_antenna_ratio_with_and_without_a_diode():
    diode = ([_rect("D", 30, 0, 40, 10), _rect("KD", 34, 4, 36, 6)],
             [M("D.layer == ndiff"), M("KD.layer == cont"), M("KD.net == D.net")])
    sheet = _antenna_sheet(
        [{"name": "ant.b", "constraints": [M("D.area < 0.16"), C("(MT.area + M2.area) <= 200 * G.area")]},
         {"name": "ant.e", "constraints": [M("D.area >= 0.16"), C("(MT.area + M2.area) / G.area <= 20000")]}], *diode)
    deck = get("klayout").compile_data(sheet).splitlines()
    i = next(n for n, ln in enumerate(deck) if ln.startswith("ant_b_l1_t1 = "))
    j = next(n for n, ln in enumerate(deck) if ln.startswith("ant_b_l1_t2 = "))
    # metal-1's share is taken before via-1 and metal-2 are connected, metal-2's after
    assert deck[i] == 'ant_b_l1_t1 = evaluate_nets(gate, { "x" => met1 }, "put(\'t1\', area(x) / area)")'
    assert deck[i + 1:j] == ["connect(via1, met1)", "connect(via1, met2)"]
    assert "clear_connections" not in deck[i:j + 3]            # never started over within one rule
    assert ("""ant_b_l1 = ant_b_l1_nets.selected_if("value('f1') < 0.16 && value('t1') + value('t2') > 200")"""
            in deck)
    assert any("value('f1') >= 0.16 && value('t1') + value('t2') > 20000" in ln for ln in deck)
    assert not any(".with_area(" in ln for ln in deck)          # the diode's area is not an area rule
    en = get("docs").compile_data(sheet)
    assert ("the area of met1 and met2 together connected to gate must be at most 200 times the area of "
            "that gate, on nets with less than 0.16 µm² of ndiff (antenna ratio).") in en


def test_a_rule_drawn_on_a_family_of_layers_is_repeated_for_each_member():
    # "intermediate metal (metal2, metal3)": one drawing, the rules of each layer on its own
    sheet = {"name": "MI", "each": {"metalint": ["metal2", "metal3"]},
             "shapes": [_rect("A", 0, 0, 3, 20), _rect("B", 6, 0, 9, 20), _rect("P", 20, 0, 23, 20)],
             "measures": [{"name": "W", "from": "a3", "to": "a1"}, {"name": "S", "from": "a1", "to": "b3"},
                          {"name": "Wp", "from": "p3", "to": "p1"}],
             "constraints": [M("A.layer == metalint"), M("B.layer == metalint"), M("P.layer == poly")],
             "rules": [{"name": "MI.1", "constraints": [C("W >= 0.07")]},
                       {"name": "MI.2", "constraints": [C("S >= 0.07")]},
                       {"name": "PO.1", "constraints": [C("Wp >= 0.05")]}]}
    assert [r["name"] for r in rules_of(sheet)] == ["MI.1_metal2", "MI.1_metal3", "MI.2_metal2", "MI.2_metal3", "PO.1"]
    ops = _ops(get("klayout").compile_data(sheet))
    assert ops["mi_2_metal2_l1"] == "metal2.space(0.07.um, euclidian).polygons(0.001)"     # never metal2 to metal3
    assert ops["mi_2_metal3_l1"] == "metal3.space(0.07.um, euclidian).polygons(0.001)"
    assert ops["po_1_l1"] == "poly.width(0.05.um, euclidian).polygons(0.001)"
