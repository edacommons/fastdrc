"""Rules drawn the way a person draws them from the design manual.

These are not transliterations of a deck expression (one shape per operator): each is
the picture in the manual -- a layer crossing or partly covering another, and one
measure. The compiler reads what covers each measured edge and where the measure lies
(`Rule.overlap_reading`).
"""

import fastdrc.backend_docs      # noqa: F401
import fastdrc.backend_klayout   # noqa: F401
from fastdrc.backend import get
from fastdrc.rule import Rule


def _rect(name, x0, y0, x1, y1, straight=()):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]      # edges: 0 bottom, 1 right, 2 top, 3 left
    return {"name": name, "edges": [
        {"id": f"{name.lower()}{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
         "edge": i in straight} for i in range(4)]}


def _compile(shapes, measure, layers, bound="E >= 0.22", extra=()):
    rule = {"name": "R", "desc": "r", "shapes": shapes, "measures": [dict(measure, name="E"), *extra],
            "constraints": [{"kind": "match", "expr": f"{s}.layer == {l}"} for s, l in layers.items()]
            + [{"kind": "check", "expr": bound}]}
    r = Rule(rule)
    op = next(ln.split("=", 1)[1].strip() for ln in get("klayout").compile(r).splitlines()
              if "_l1 =" in ln)
    return op, get("docs").compile(r).splitlines()[1].strip(), r.shape_roles()


def test_a_bar_must_reach_past_the_band_that_crosses_it():
    # "COMP extension beyond salicide block": a comp bar, a sab band across it, and a
    # measure from the band's edge (inside the bar) to the bar's end.
    bar, band = _rect("L", 0, 0, 10, 4, {1}), _rect("X", 3, -2, 7, 6, {1})
    op, en, roles = _compile([bar, band], {"from": "x1", "to": "l1"}, {"L": "comp", "X": "sab"})
    assert op == "comp.enclosing(sab, 0.22.um, euclidian).polygons(0.001)"
    assert en == "• comp must extend at least 0.22 µm beyond sab (euclidian)."
    assert roles["L"].startswith("subject: must reach far enough past X")


def test_the_band_must_reach_past_the_bar_it_crosses():
    # "Poly2 end cap" / "salicide block extension beyond COMP": the same picture, the
    # measure taken the other way -- from the bar's edge (inside the band) to the band's end.
    bar, band = _rect("L", 0, 0, 10, 4, {2}), _rect("X", 3, -2, 7, 6, {2})
    op, en, _ = _compile([bar, band], {"from": "l2", "to": "x2"}, {"L": "comp", "X": "poly2"})
    assert op == "poly2.enclosing(comp, 0.22.um, euclidian).polygons(0.001)"
    assert en == "• poly2 must extend at least 0.22 µm beyond comp (euclidian)."


def test_the_measure_is_read_where_the_two_edges_face_each_other():
    # the bar's top edge is long and its midpoint lies outside the band; the measure
    # still belongs to the stretch under the band
    bar, band = _rect("L", 0, 0, 30, 4, {2}), _rect("X", 3, -2, 7, 6, {2})
    op, _, _ = _compile([bar, band], {"from": "l2", "to": "x2"}, {"L": "comp", "X": "poly2"})
    assert op == "poly2.enclosing(comp, 0.22.um, euclidian).polygons(0.001)"


def test_butted_implants_scope_the_extension_to_the_neighbour():
    # NP.6 "overlap with NCOMP butted to PCOMP": ONE diffusion, pplus over its left
    # part, nplus butted over the rest, and a measure from the butting edge to the end
    # of the diffusion. nplus does not reach the pplus part, so it is not combined into
    # the diffusion: it says where the extension is looked at.
    comp = _rect("C", 0, 0, 10, 4, {1})
    pplus, nplus = _rect("P", -1, -1, 4, 5, {1}), _rect("N", 4, -1, 11, 5)
    op, en, roles = _compile([comp, pplus, nplus], {"from": "p1", "to": "c1", "metric": "projection"},
                             {"C": "comp", "P": "pplus", "N": "nplus"})
    assert op == "comp.enclosing(pplus, 0.22.um, projection).polygons(0.001).overlapping(nplus)"
    assert en == ("• comp must extend at least 0.22 µm beyond pplus (projection) — only where "
                  "the geometry is overlapping nplus.")
    assert roles["N"] == "scope: violations only overlapping this"


def test_a_linked_context_still_filters_the_reaching_shape():
    # SB.9: the band (sab) must be one that lies outside otp_mk -- a link from it
    bar, band, far = _rect("L", 0, 0, 10, 4, {2}), _rect("X", 3, -2, 7, 6, {2}), _rect("O", 14, 0, 18, 4)
    op, _, roles = _compile([bar, band, far], {"from": "l2", "to": "x2"},
                            {"L": "poly2", "X": "sab", "O": "otp_mk"},
                            extra=[{"name": "G", "from": "x1", "to": "o3"}])
    assert op == "sab.outside(otp_mk).enclosing(poly2, 0.22.um, euclidian).polygons(0.001)"
    assert roles["O"] == "filter: only X outside this"


def test_measure_inside_both_shapes_is_the_overlap_depth():
    # an implant must overlap the diffusion by at least 0.16: the measure runs from the
    # implant's edge (inside the diffusion) to the diffusion's edge (inside the implant)
    comp, nplus = _rect("C", 0, 0, 10, 4, {1}), _rect("N", 6, -1, 14, 5, {3})
    op, en, _ = _compile([comp, nplus], {"from": "n3", "to": "c1"}, {"C": "comp", "N": "nplus"},
                         bound="E >= 0.16")
    assert op == "nplus.overlap(comp, 0.16.um, euclidian).polygons(0.001)"
    assert en == "• nplus must overlap comp by at least 0.16 µm (euclidian)."


def test_an_enclosed_region_clipped_by_its_encloser_drops_the_clip_edges():
    # deck-mirroring form `sab.enclosing(poly2.and(sab))`: the clipped region shares its
    # clip edges with sab; zero enclosure there is the clip, not a violation
    outer, inner, cover = _rect("A", 0, 0, 20, 12, {1}), _rect("B", 14, 7, 18, 11, {1}), _rect("K", 13, 6, 19, 12)
    op, _, _ = _compile([outer, inner, cover], {"from": "a1", "to": "b1"},
                        {"A": "sab", "B": "poly2", "K": "sab"})
    assert op == "sab.enclosing(poly2.and(sab), 0.22.um, euclidian).polygons"


def _rule(shapes, constraints, **more):
    r = Rule({"name": "R", "desc": "r", "shapes": shapes, "measures": [],
              "constraints": constraints, **more})
    ops = [ln.split("=", 1)[1].strip() for ln in get("klayout").compile(r).splitlines()
           if "_l1 =" in ln]
    return ops, get("klayout").compile(r), get("docs").compile(r)


def test_a_cross_over_a_shape_forbids_it():
    # CO.9-style datasheet mark: the contact drawn where it must not be, with an X on it
    contact = dict(_rect("K", 4, 1, 6, 3), crossed=True)
    sab = _rect("S", 0, 0, 10, 4)                     # covers the contact
    ops, _, en = _rule([contact, sab], [{"kind": "match", "expr": "K.layer == contact"},
                                        {"kind": "match", "expr": "S.layer == sab"}])
    assert ops == ["contact.and(sab)"]
    assert "no contact combined with sab may exist" in en


def test_small_area_crossed_out_is_the_min_area_rule():
    # datasheet: a small shape, "Area < 2", crossed out. Same rule as the check A.area >= 2.
    small = dict(_rect("A", 0, 0, 2, 2), crossed=False)
    as_filter, _, _ = _rule([small], [{"kind": "match", "expr": "A.layer == sab"},
                                      {"kind": "match", "expr": "A.area < 2"}])
    as_check, _, en = _rule([small], [{"kind": "match", "expr": "A.layer == sab"},
                                      {"kind": "check", "expr": "A.area >= 2"}])
    assert as_filter == as_check == ["sab.with_area(nil, 2.um)"]
    assert "sab must be at least 2 µm² in area" in en


def test_legend_names_a_derived_layer():
    # the datasheet legend has NCOMP / PCOMP as things you draw; define them once
    esd, tap = _rect("E", 0, 0, 10, 4), _rect("T", 6, 1, 14, 3)
    rule = Rule({"name": "ESD.7", "desc": "no ESD implant over PCOMP",
                 "layers": {"pcomp": "comp and pplus"},
                 "shapes": [esd, tap],
                 "measures": [{"name": "X", "from": "e1", "to": "t3", "crossed": True}],
                 "constraints": [{"kind": "match", "expr": "E.layer == esd"},
                                 {"kind": "match", "expr": "T.layer == pcomp"}]})
    deck = get("klayout", layermap={"esd": (24, 0), "comp": (22, 0), "pplus": (31, 0)}).compile(rule)
    assert "pcomp = comp.and(pplus)" in deck and 'input("pcomp")' not in deck
    assert "esd_7_l1 = esd.overlapping(pcomp)" in deck
    en = get("docs").compile(rule)
    assert "esd must not overlap pcomp." in en and "where pcomp = comp and pplus" in en


def test_legend_with_a_subtraction():
    bar = _rect("A", 0, 0, 10, 4, {1, 3})
    rule = Rule({"name": "PL.1", "desc": "field poly width",
                 "layers": {"field_poly": "poly2 and not comp"},
                 "shapes": [bar], "measures": [{"name": "W", "from": "a1", "to": "a3"}],
                 "constraints": [{"kind": "match", "expr": "A.layer == field_poly"},
                                 {"kind": "check", "expr": "W >= 0.18"}]})
    deck = get("klayout", layermap={"poly2": (30, 0), "comp": (22, 0)}).compile(rule)
    assert "field_poly = poly2.not(comp)" in deck


def test_coinciding_edges_permitted_is_said_in_the_constraint():
    # "must extend at least 0.22 -- or not at all": zero distance is not a violation
    bar, band = _rect("L", 0, 0, 10, 4, {1}), _rect("X", 3, -2, 7, 6, {1})
    op, en, _ = _compile([bar, band], {"from": "x1", "to": "l1"}, {"L": "comp", "X": "sab"},
                         bound="E >= 0.22 or E == 0")
    assert op == "comp.enclosing(sab, 0.22.um, euclidian).polygons"      # zero-width pairs dropped
    assert "or exactly 0 (coinciding edges are allowed)" in en


def test_run_length_condition_on_a_spacing():
    # "space to a neighbour that runs alongside for more than 1 um"
    a, b = _rect("A", 0, 0, 4, 10, {1}), _rect("B", 6, 0, 10, 10, {3})
    r = Rule({"name": "R", "desc": "r", "shapes": [a, b],
              "measures": [{"name": "S", "from": "a1", "to": "b3", "metric": "projection"}],
              "constraints": [{"kind": "match", "expr": "A.layer == metal2"},
                              {"kind": "match", "expr": "B.layer == metal2"},
                              {"kind": "match", "expr": "S.run > 1"},
                              {"kind": "check", "expr": "S >= 0.3"}]})
    deck = get("klayout").compile(r)
    assert "metal2.space(0.3.um, projection, projecting > 1.um).polygons(0.001)" in deck
    assert "where the edges run side by side for > 1 µm" in get("docs").compile(r)


def test_must_be_within_is_an_enclosure_plus_lying_inside():
    # CO.6 "metal1 overlap of contact": the margin, AND the contact must be inside at all
    metal, contact = _rect("M", 0, 0, 10, 8, {1}), _rect("K", 6, 3, 8, 5, {1})
    r = Rule({"name": "CO.6", "desc": "metal over contact", "shapes": [metal, contact],
              "measures": [{"name": "E", "from": "m1", "to": "k1"}],
              "constraints": [{"kind": "match", "expr": "M.layer == metal1"},
                              {"kind": "match", "expr": "K.layer == contact"},
                              {"kind": "check", "expr": "E >= 0.005"},
                              {"kind": "check", "expr": "K in M"}]})
    ops = [ln.split("=", 1)[1].strip() for ln in get("klayout").compile(r).splitlines() if "_l1 =" in ln]
    assert ops == ["metal1.enclosing(contact, 0.005.um, euclidian).polygons(0.001)",
                   "contact.not_inside(metal1)"]
    assert "every contact must lie wholly inside metal1." in get("docs").compile(r)


def test_within_is_the_margin_and_no_straddling_in_one_arrow():
    # a manual's "DNWELL overlap of PCOMP": the margin, and a PCOMP across the well's
    # edge has none. One arrow with "within" set -- not a second drawing.
    well, tap = _rect("W", 0, 0, 20, 12, {1}), _rect("T", 12, 4, 16, 8, {1})
    r = Rule({"name": "DF.4e", "desc": "d", "layers": {"pcomp": "comp and pplus"},
              "shapes": [well, tap],
              "measures": [{"name": "E", "from": "w1", "to": "t1", "within": True}],
              "constraints": [{"kind": "match", "expr": "W.layer == dnwell"},
                              {"kind": "match", "expr": "T.layer == pcomp"},
                              {"kind": "check", "expr": "E >= 0.93"}],
              "scope": [["overlapping", "dualgate"]]})
    op = next(ln.split("=", 1)[1].strip() for ln in get("klayout").compile(r).splitlines()
              if "_l1 =" in ln)
    assert op == ("dnwell.enclosing(pcomp, 0.93.um, euclidian).polygons(0.001)"
                  ".or(pcomp.overlapping(dnwell).not_inside(dnwell)).overlapping(dualgate)")
    en = get("docs").compile(r)
    assert "dnwell must enclose pcomp by at least 0.93 µm, and no pcomp may lie across its edge" in en


def test_crossed_out_shape_with_a_condition_is_one_rule():
    # "Area < 0.2025" with an X over the small shape: the filter says which ones
    small = dict(_rect("A", 0, 0, 3, 3), crossed=True)
    ops, _, _ = _rule([small], [{"kind": "match", "expr": "A.layer == comp"},
                                {"kind": "match", "expr": "A.area < 0.2025"}])
    assert ops == ["comp.with_area(nil, 0.2025.um)"]


def test_a_measure_given_a_number_is_a_distance_whatever_its_edges():
    # forgetting to make a measured edge straight must not silently turn the rule into
    # a relation: E >= 0.18 can only be a distance
    well, tap = _rect("W", 0, 0, 20, 12), _rect("T", 12, 4, 16, 8)      # ALL edges left wavy
    op, _, _ = _compile([well, tap], {"from": "w1", "to": "t1"}, {"W": "nwell", "T": "tap"},
                        bound="E >= 0.18")
    assert op == "nwell.enclosing(tap, 0.18.um, euclidian).polygons(0.001)"


def test_width_of_a_or_b_is_each_of_them_and_spacing_is_between_any():
    bar = _rect("A", 0, 0, 3, 10, {1, 3})
    r = Rule({"name": "difftap.1", "desc": "d", "shapes": [bar],
              "measures": [{"name": "W", "from": "a3", "to": "a1"}],
              "constraints": [{"kind": "match", "expr": "A.layer in {diff, tap}"},
                              {"kind": "check", "expr": "W >= 0.15"}]})
    deck = get("klayout").compile(r)
    assert ("diff.width(0.15.um, euclidian).polygons(0.001)"
            ".or(tap.width(0.15.um, euclidian).polygons(0.001))") in deck
    assert "each of diff or tap must be at least 0.15 µm wide" in get("docs").compile(r)
    a, b = _rect("A", 0, 0, 3, 10, {1}), _rect("B", 5, 0, 8, 10, {3})
    s = Rule({"name": "difftap.3", "desc": "d", "shapes": [a, b],
              "measures": [{"name": "S", "from": "a1", "to": "b3"}],
              "constraints": [{"kind": "match", "expr": "A.layer in {diff, tap}"},
                              {"kind": "match", "expr": "B.layer in {diff, tap}"},
                              {"kind": "check", "expr": "S >= 0.27"}]})
    assert "diff.or(tap).space(0.27.um, euclidian).polygons(0.001)" in get("klayout").compile(s)


def test_standalone_deck_reads_its_input_and_writes_a_report():
    bar = _rect("A", 0, 0, 3, 10, {1, 3})
    r = Rule({"name": "m1.1", "desc": 'width "min"', "shapes": [bar],
              "measures": [{"name": "W", "from": "a3", "to": "a1"}],
              "constraints": [{"kind": "match", "expr": "A.layer == met1"},
                              {"kind": "check", "expr": "W >= 0.14"}]})
    deck = get("klayout", layermap={"met1": (68, 20)}, standalone=True, title="demo").compile(r)
    lines = deck.splitlines()
    assert "  source($input)" in lines and 'report("demo", $report || "fastdrc_report.lyrdb")' in lines
    assert lines.index("met1 = polygons(68, 20)") > lines.index("  source($input)")


def test_spacing_to_a_wide_line_with_a_long_parallel_run():
    # "Min. space of lines if at least one is wider than 0.3 and the parallel run is more
    # than 1.0": the wide line carries a width FILTER, the spacing arrow a run-length
    # filter, and the spacing bound is the one check.
    wide, near = _rect("A", 0, 0, 8, 20, {1, 3}), _rect("B", 11, 0, 14, 20, {3})
    r = Rule({"name": "M1.e", "desc": "wide metal spacing", "shapes": [wide, near],
              "measures": [{"name": "Ww", "from": "a3", "to": "a1"},
                           {"name": "S", "from": "a1", "to": "b3", "metric": "projection"}],
              "constraints": [{"kind": "match", "expr": "A.layer == Metal1"},
                              {"kind": "match", "expr": "B.layer == Metal1"},
                              {"kind": "match", "expr": "Ww > 0.3"},
                              {"kind": "match", "expr": "S.run > 1.0"},
                              {"kind": "check", "expr": "S >= 0.22"}]})
    ops = [ln.split("=", 1)[1].strip() for ln in get("klayout").compile(r).splitlines() if "_l1 =" in ln]
    # one rule; the whole layer first, so its own edge does not hide the neighbour
    assert ops == ["Metal1.separation(Metal1.sized(-0.15.um).sized(0.15.um), 0.22.um, projection, "
                   "projecting > 1.um).polygons(0.001)"]
    en = get("docs").compile(r)
    assert "Metal1 at least 0.3 µm wide must be at least 0.22 µm from Metal1" in en
    assert "where the edges run side by side for > 1 µm" in en


# --- shape class: comparing two SIDES of a shape says what kind of shape it is ---

def _sized(constraints, shapes=None):
    shapes = shapes or [_rect("K", 0, 0, 2, 2)]
    rule = {"name": "R", "desc": "r", "shapes": shapes, "measures": [],
            "constraints": [{"kind": "match", "expr": f"{s['name']}.layer == cont"} for s in shapes]
            + [{"kind": k, "expr": e} for k, e in constraints]}
    r = Rule(rule)
    ops = [ln.split("=", 1)[1].strip() for ln in get("klayout").compile(r).splitlines() if "_l" in ln and " = " in ln]
    return ops, [ln.strip() for ln in get("docs").compile(r).splitlines()[1:]]


def test_equal_sides_as_a_filter_pick_the_squares():
    # "contact size" in a manual that treats bars separately: the rule is about squares
    ops, en = _sized([("match", "k0.length == k1.length"), ("check", "k0.length == 0.16")])
    assert ops == ["cont.squares.edges.without_length(0.16.um)"]
    assert en == ["• every square cont edge must be exactly 0.16 µm long "
                  "(any other length is flagged — a min/max size check)."]


def test_a_longer_side_makes_a_bar_and_names_its_two_extents():
    bar = [_rect("K", 0, 0, 8, 2)]
    longer = ("match", "k0.length > k1.length")
    ops, en = _sized([longer, ("check", "k1.length == 0.16")], bar)
    assert ops == ["cont.non_squares.without_bbox_min(0.16.um)"]
    assert en == ["• the shorter side of every non-square cont must be exactly 0.16 µm."]
    ops, en = _sized([longer, ("check", "k0.length >= 0.34")], bar)
    assert ops == ["cont.non_squares.with_bbox_max(nil, 0.34.um)"]
    assert en == ["• the longer side of every non-square cont must be at least 0.34 µm."]


def test_equal_sides_as_a_check_mean_must_be_square():
    ops, en = _sized([("check", "k0.length == k1.length")])
    assert ops == ["cont.non_squares"]
    assert en == ["• every cont shape must be a square."]


def test_the_shape_class_follows_the_shape_into_a_spacing_rule():
    a, b = _rect("A", 0, 0, 8, 2, {2}), _rect("B", 0, 5, 8, 7, {0})
    rule = {"name": "R", "desc": "r", "shapes": [a, b], "measures": [{"name": "S", "from": "a2", "to": "b0"}],
            "constraints": [{"kind": "match", "expr": "A.layer == cont"}, {"kind": "match", "expr": "B.layer == cont"},
                            {"kind": "match", "expr": "a0.length > a1.length"},
                            {"kind": "match", "expr": "b0.length > b1.length"},
                            {"kind": "check", "expr": "S >= 0.28"}]}
    out = get("klayout").compile(Rule(rule))
    assert "cont.non_squares.space(0.28.um, euclidian).polygons(0.001)" in out


# --- found by running a generated deck on real standard cells ---

def test_a_layer_cut_out_of_another_abuts_it_without_violating_their_spacing():
    # "P+ implant space to N+ diffusion", N+ diffusion being diffusion outside the
    # implant: a butted tap puts the two edge to edge by construction
    imp, nd = _rect("P", 0, 0, 4, 4, {1}), _rect("N", 6, 0, 10, 4, {3})
    rule = {"name": "R", "desc": "r", "shapes": [imp, nd], "layers": {"ndiff": "diff and not pimp"},
            "measures": [{"name": "S", "from": "p1", "to": "n3"}],
            "constraints": [{"kind": "match", "expr": "P.layer == pimp"}, {"kind": "match", "expr": "N.layer == ndiff"},
                            {"kind": "check", "expr": "S >= 0.18"}]}
    r = Rule(rule)
    assert "pimp.separation(ndiff, 0.18.um, euclidian).polygons\n" in get("klayout").compile(r)
    assert "(where the two abut is not a spacing)" in get("docs").compile(r)
    rule["layers"] = {"ndiff": "diff and nimp"}            # no cut: abutting is a zero spacing
    assert ".polygons(0.001)" in get("klayout").compile(Rule(rule))


def test_a_shape_marked_whole_stands_for_its_layer_anywhere():
    # "contact on diffusion, space to poly" drawn at a gate: the poly is over diffusion in
    # the picture, but the rule is about poly wherever it is
    diff, gate, k = _rect("D", 0, 0, 20, 12), _rect("G", 9, -4, 11, 16, {3}), _rect("K", 4, 5, 5, 6, {1})
    args = ([diff, gate, k], {"from": "k1", "to": "g3"}, {"D": "diff", "G": "poly", "K": "cont"})
    assert _compile(*args)[0] == "cont.and(diff).separation(poly.and(diff), 0.22.um, euclidian).polygons(0.001)"
    gate["whole"] = True
    assert _compile(*args)[0] == "cont.and(diff).separation(poly, 0.22.um, euclidian).polygons(0.001)"


# --- arrays: "spacing in an N x N or larger array" ---

def _array_rule(extra):
    a, b = _rect("A", 0, 0, 4, 4, {1}), _rect("B", 6, 0, 10, 4, {3})
    rule = {"name": "R", "desc": "r", "shapes": [a, b], "measures": [{"name": "S", "from": "a1", "to": "b3"}],
            "constraints": [{"kind": "match", "expr": "A.layer == cont"}, {"kind": "match", "expr": "B.layer == cont"},
                            *({"kind": "match", "expr": e} for e in extra), {"kind": "check", "expr": "S >= 0.28"}]}
    r = Rule(rule)
    deck = get("klayout").compile(r)
    ops = [ln.split("=", 1)[1].strip() for ln in deck.splitlines() if ln.startswith("r_l") and " = " in ln]
    return ops, get("docs").compile(r).splitlines()[1].strip(), deck


def test_spacing_inside_a_large_array():
    both = ["A.rows >= 4 and A.columns >= 4", "B.rows >= 4 and B.columns >= 4"]
    # the element size and how close makes an array are filters, not rules of their own
    ops, en, deck = _array_rule(both + ["a0.length == 0.22", "b0.length == 0.22", "S < 0.36"])
    assert ops == ["cont_array_4x4.space(0.28.um, euclidian).polygons(0.001)"]
    assert ("cont_array_4x4 = cont.inside(cont.sized(0.18.um).sized(-0.18.um).sized(-0.69.um).sized(0.69.um))"
            in deck)
    assert en == ("• any two shapes of cont in an array of at least 4 rows and 4 columns (shapes within "
                  "0.36 µm of each other, each 0.22 µm) must be at least 0.28 µm apart (euclidian).")


def test_an_array_without_a_stated_size_is_taken_by_count_and_the_checked_spacing():
    ops, _, deck = _array_rule(["A.rows > 4 and A.columns > 4", "B.rows > 4 and B.columns > 4"])
    group = "cont.sized(0.14.um).sized(-0.14.um)"
    assert ops == ["cont_array_5x5.space(0.28.um, euclidian).polygons(0.001)"]
    assert f"cont_array_5x5 = cont.inside({group}.interacting(cont, 25))" in deck


def test_rows_and_columns_may_differ():
    _, _, deck = _array_rule(["A.rows >= 2 and A.columns >= 5", "B.rows >= 2 and B.columns >= 5",
                              "a0.length == 0.2", "b0.length == 0.2"])
    assert ".sized(-0.82.um, -0.1.um).sized(0.82.um, 0.1.um)" in deck


def test_a_layer_set_can_be_spelled_with_in_or_with_or():
    def op(expr):
        big, k = _rect("D", 0, 0, 10, 10, {3}), _rect("L", 4, 4, 6, 6, {3})
        rule = {"name": "R", "desc": "r", "shapes": [big, k], "measures": [{"name": "E", "from": "d3", "to": "l3"}],
                "constraints": [{"kind": "match", "expr": expr}, {"kind": "match", "expr": "L.layer == licon"},
                                {"kind": "check", "expr": "E >= 0.04"}]}
        return next(ln for ln in get("klayout").compile(Rule(rule)).splitlines() if "_l1 =" in ln)
    assert "diff.or(tap).enclosing(licon" in op("D.layer in {diff, tap}")
    assert op("D.layer == diff or D.layer == tap") == op("D.layer in {diff, tap}")


# --- "on one of two adjacent sides": a check that either of two arrows reaches the value ---

def test_enclosure_on_one_of_two_adjacent_sides():
    m, k = _rect("M", 0, 0, 10, 10), _rect("K", 3, 3, 5, 5)
    rule = {"name": "R", "desc": "r", "shapes": [m, k],
            "measures": [{"name": "Ea", "from": "m3", "to": "k3"}, {"name": "Eb", "from": "m0", "to": "k0"}],
            "constraints": [{"kind": "match", "expr": "M.layer == met"}, {"kind": "match", "expr": "K.layer == via"},
                            {"kind": "check", "expr": "Ea >= 0.06 or Eb >= 0.06"}]}
    r = Rule(rule)
    out = get("klayout").compile(r)
    assert ("r_l1 = via.interacting(met.enclosing(via, 0.06.um, projection, whole_edges).second_edges"
            ".width(angle_limit(100.0), 1.dbu).polygons(0.001))") in out
    assert out.count(".output(") == 1                       # one rule, not one per arrow
    assert get("docs").compile(r).splitlines()[1].strip() == (
        "• met must enclose via by at least 0.06 µm on at least one of any two adjacent sides.")


def test_spacing_in_at_least_one_direction():
    a, right, up = _rect("A", 0, 0, 2, 2), _rect("B", 4, 0, 6, 2), _rect("C", 0, 4, 2, 6)
    rule = {"name": "R", "desc": "r", "shapes": [a, right, up],
            "measures": [{"name": "Sx", "from": "a1", "to": "b3"}, {"name": "Sy", "from": "a2", "to": "c0"}],
            "constraints": [*({"kind": "match", "expr": f"{n}.layer == cont"} for n in "ABC"),
                            {"kind": "check", "expr": "Sx >= 0.2 or Sy >= 0.2"}]}
    out = get("klayout").compile(Rule(rule))
    assert ("r_l1 = cont.interacting(cont.space(0.2.um, projection, whole_edges).edges.and(cont.edges)"
            ".width(angle_limit(100.0), 1.dbu).polygons(0.001))") in out
    assert out.count(".output(") == 1


def test_or_with_zero_is_still_coinciding_allowed():
    big, k = _rect("M", 0, 0, 10, 10, {3}), _rect("K", 3, 3, 5, 5, {3})
    op, en, _ = _compile([big, k], {"from": "m3", "to": "k3"}, {"M": "met", "K": "via"}, bound="E >= 0.06 or E == 0")
    assert op == "met.enclosing(via, 0.06.um, euclidian).polygons"


# --- a MAXIMUM between two shapes: every part of the first within reach of the second ---

def test_every_part_must_be_within_a_distance_of_a_neighbour():
    # latch-up: "max. distance from any diffusion to a well tap"
    a, t = _rect("A", 0, 0, 4, 4, {1}), _rect("T", 8, 0, 10, 4, {3})
    op, en, _ = _compile([a, t], {"from": "a1", "to": "t3"}, {"A": "pdiff", "T": "ntap"}, bound="E <= 20")
    assert op == "pdiff.not(ntap.sized(20.um))"
    assert en == "• every part of pdiff must be within 20 µm of ntap."


def test_a_maximum_enclosure_is_about_how_far_the_outer_reaches():
    tie, k = _rect("T", 0, 0, 10, 4, {1}), _rect("K", 1, 1, 3, 3, {1})
    op, en, _ = _compile([tie, k], {"from": "k1", "to": "t1"}, {"T": "tie", "K": "cont"}, bound="E <= 6")
    assert op == "tie.not(cont.sized(6.um))"
    assert en == "• no part of tie may reach more than 6 µm beyond cont."
