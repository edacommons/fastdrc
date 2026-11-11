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
