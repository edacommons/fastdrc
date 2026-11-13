"""Writes the demo process: process.json and one sheet per section under rules/.

An invented process -- the values are made up -- with every kind of rule. The sheets
are ordinary files; this script only saves typing them. Run it to regenerate them.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
M = lambda e: {"kind": "match", "expr": e}     # noqa: E731  a filter / a fact
C = lambda e: {"kind": "check", "expr": e}     # noqa: E731  what must hold


def rect(n, x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]      # edges: _0 bottom, _1 right, _2 top, _3 left
    return {"name": n, "edges": [{"id": f"{n.lower()}_{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % 4]),
                                  "edge": False} for i in range(4)]}


class Sheet:
    def __init__(self, name, desc):
        self.data = {"name": name, "desc": desc, "shapes": [], "measures": [], "constraints": [], "rules": []}

    def shape(self, name, layer, *box):
        self.data["shapes"].append(rect(name, *box))
        self.data["constraints"].append(M(f"{name}.layer == {layer}"))

    def arrow(self, name, frm, to, **kw):
        self.data["measures"].append(dict(name=name, **{"from": frm, "to": to}, **kw))

    def fact(self, *exprs):
        self.data["constraints"] += [M(e) for e in exprs]

    def rule(self, name, desc, *constraints, **more):
        self.data["rules"].append(dict(name=name, desc=desc, constraints=list(constraints), **more))

    def save(self, filename):
        with open(os.path.join(HERE, "rules", filename), "w") as f:
            json.dump(self.data, f, indent=1)
            f.write("\n")


# ---- what is said once for the whole process
process = {
    "name": "Demo",
    "layers": {"nwell": [1, 0], "diff": [2, 0], "pimp": [3, 0], "poly": [4, 0], "cont": [5, 0], "met1": [6, 0],
               "via1": [7, 0], "met2": [8, 0], "thick": [9, 0], "chip": [10, 0]},
    "legend": {"pdiff": "diff and pimp", "ndiff": "diff and not pimp", "gate": "poly and diff",
               "field_poly": "poly and not diff"},
    "columns": {"thin": [["not_interacting", "thick"]], "thick": [["overlapping", "thick"]]},
}
with open(os.path.join(HERE, "process.json"), "w") as f:
    json.dump(process, f, indent=1)
    f.write("\n")

# ---- section 1: wells
s = Sheet("Wells", "N-well and what sits in it")
s.shape("NA", "nwell", 0, 0, 8, 10); s.shape("NB", "nwell", 12, 0, 20, 10)
s.arrow("Wn", "na_3", "na_1"); s.arrow("Sn", "na_1", "nb_3")
s.shape("NW", "nwell", 30, 0, 56, 20); s.shape("PD", "pdiff", 34, 6, 40, 14); s.shape("NT", "ndiff", 44, 6, 50, 14)
s.arrow("Ep", "nw_3", "pd_3")
s.arrow("Sl", "nt_3", "pd_3")        # from the tap to the diffusion's FAR side: every part of it
s.rule("NW.1", "N-well width", C("Wn >= 0.8"))
s.rule("NW.2", "N-well space", C("Sn >= 1.0"))
s.rule("NW.3", "N-well space, wells on different nets", M("NA.net != NB.net"), C("Sn >= 2.0"))
s.rule("NW.4", "N-well enclosure of P+ diffusion", C("Ep >= ?"), values={"thin": 0.3, "thick": 0.6})
s.rule("LU.1", "Any part of a P+ diffusion to the nearest well tap", C("Sl <= 20"), **{"with": ["NW"]})
s.save("1-wells.sheet.json")

# ---- section 2: poly and contacts
s = Sheet("Devices", "Poly, diffusion and contacts")
s.shape("DT", "diff", 0, 4, 20, 12); s.shape("GT", "poly", 8, 0, 11, 16)
s.arrow("Xp", "dt_2", "gt_2"); s.arrow("Xd", "gt_1", "dt_1", at=8)
s.shape("PW", "poly", 26, 0, 28, 16); s.arrow("Wp", "pw_3", "pw_1")
s.shape("DC", "diff", 40, 0, 60, 12); s.shape("KA", "cont", 44, 3, 45, 4); s.shape("KB", "cont", 44, 7, 45, 8)
s.shape("KC", "cont", 48, 7, 49, 8)
s.fact("ka_0.length == ka_1.length", "kb_0.length == kb_1.length", "kc_0.length == kc_1.length")   # contacts are squares
s.arrow("Sk", "ka_2", "kb_0"); s.arrow("Sk2", "kb_1", "kc_3"); s.arrow("Ek", "dc_3", "ka_3")
s.rule("PO.1", "Poly width", C("Wp >= 0.15"))
s.rule("PO.2", "Poly extension beyond diffusion (end cap)", C("Xp >= 0.2"))
s.rule("PO.3", "Diffusion extension beyond the gate", C("Xd >= 0.25"))
s.rule("CO.1", "Contact size", C("ka_0.length == 0.2"), extended=True)
s.rule("CO.2", "Contact space", C("Sk >= 0.25"))
s.rule("CO.3", "Contact space in an array of 4 x 4 or more (one direction is enough)",
       M("KA.rows >= 4 and KA.columns >= 4"), M("KB.rows >= 4 and KB.columns >= 4"), M("KC.rows >= 4 and KC.columns >= 4"),
       M("ka_0.length == 0.2"), M("kb_0.length == 0.2"), M("kc_0.length == 0.2"), C("Sk >= 0.3 or Sk2 >= 0.3"))
s.rule("CO.4", "Diffusion enclosure of contact", C("Ek >= 0.1"))
s.save("2-devices.sheet.json")

# ---- section 3: metal, and the stack that makes the nets
s = Sheet("Metal", "Metal-1, via-1, antenna and density; and how the layers connect")
s.shape("MV", "met1", 0, 0, 10, 10); s.shape("VA", "via1", 2, 2, 4, 4)
s.arrow("Ea", "mv_3", "va_3"); s.arrow("Eb", "mv_0", "va_0")
s.shape("MW", "met1", 20, 0, 32, 12); s.shape("VW", "via1", 25, 5, 27, 7); s.arrow("Ww", "mw_3", "mw_1", at=10)
# the stack: a tap in the well, a contact on it, metal over the contact, a via, metal-2
s.shape("WS", "nwell", 40, 0, 60, 20); s.shape("TS", "ndiff", 44, 4, 56, 16); s.shape("KS", "cont", 48, 8, 52, 12)
s.shape("MS", "met1", 46, 6, 54, 14); s.shape("VS", "via1", 49, 9, 51, 11); s.shape("M2", "met2", 45, 5, 55, 15)
s.fact("TS.net == WS.net", "KS.net == TS.net", "KS.net == MS.net", "VS.net == MS.net", "VS.net == M2.net")
# a gate, the poly it continues as, a contact on that, and metal over the contact
s.shape("GA", "gate", 70, 4, 72, 8); s.shape("PA", "field_poly", 70, 8, 72, 20); s.shape("KP", "cont", 70.5, 12, 71.5, 13)
s.shape("MP", "met1", 69, 11, 73, 14)
s.fact("PA.net == GA.net", "KP.net == PA.net", "KP.net == MP.net")
s.shape("CHIP", "chip", 90, 0, 120, 20); s.shape("MD", "met1", 96, 6, 104, 14)
s.shape("WIN", "chip", 130, 0, 150, 20); s.shape("MN", "met1", 136, 6, 144, 14)
s.fact("win_0.length == 100", "WIN.step == 50")
s.rule("V1.1", "Metal-1 enclosure of via-1 on one of two adjacent sides", C("Ea >= 0.1 or Eb >= 0.1"))
s.rule("V1.2", "Vias on metal-1 wider than 2", M("Ww >= 2"), C("Count(VW) >= 2"))
s.rule("AN.1", "Metal-1 area per connected gate area", C("MP.area <= 400 * GA.area"))
s.rule("DN.1", "Metal-1 density over the chip", C("MD.density >= 0.2 and MD.density <= 0.8"), **{"with": ["CHIP"]})
s.rule("DN.2", "Metal-1 density in any 100 x 100 window", C("MN.density <= 0.9"), **{"with": ["WIN"]})
s.save("3-metal.sheet.json")
