"""The ``svg`` backend: a rule's drawing as a picture.

``compile(rule)`` gives the drawing the way a design manual prints a figure -- the
shapes named by their layer, a straight edge as a line and a free one as a wavy line,
a hole dashed, the arrows with their names, a cross where the rule says "never". It is
vector text (SVG), so it goes into a web page or a document as it is.

Other backends use it for their figures (``get("svg").compile(rule)``); `files` writes
one ``<stem>.<rule>.svg`` per rule.
"""

from __future__ import annotations

import math
import zlib
from xml.sax.saxutils import escape

from .backend import Backend, register
from .rule import Rule

UNIT = 14.0                # px per grid step of the drawing
PAD = 34.0
INK, ARROW, NEVER, HOLE = "#1f2430", "#b3541e", "#c62828", "#6b7280"
PALETTE = ["#2f7fd1", "#d1495b", "#2e9e6b", "#8e5bd1", "#c98a12", "#1f9aa8", "#b05a8c", "#6c7a1e"]


def colour_of(layer) -> str:
    """One colour per layer name, the same in every figure of a deck."""
    return PALETTE[zlib.crc32(str(layer).encode()) % len(PALETTE)]


def _wavy(a, b, centre, end=False):
    """Points along a->b bulging away from the shape's centre, meeting both corners."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln, dx / ln
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    if (mx + nx - centre[0]) ** 2 + (my + ny - centre[1]) ** 2 < (mx - nx - centre[0]) ** 2 + (my - ny - centre[1]) ** 2:
        nx, ny = -nx, -ny
    bumps = max(1, round(ln / (2 * UNIT)))
    steps = max(12, bumps * 8)
    amp = min(0.5 * UNIT, 0.09 * ln)                    # a short side waves gently: a small shape keeps its form
    out = []
    for k in range(steps + 1 if end else steps):
        t = k / steps
        off = amp * (1 - math.cos(t * 2 * math.pi * bumps)) / 2
        out.append((a[0] + dx * t + nx * off, a[1] + dy * t + ny * off))
    return out


def _place(r1, r2, lo_all, hi_all):
    """Where a dimension line sits along the edges it measures: in the middle of the
    stretch both cover, else just outside the shapes on the nearer side."""
    lo1, hi1 = sorted(r1)
    lo2, hi2 = sorted(r2)
    lo, hi = max(lo1, lo2), min(hi1, hi2)
    if hi - lo > 4:
        s = (lo + hi) / 2
    else:
        s = lo_all - 0.7 * UNIT if (min(lo1, lo2) - lo_all) <= (hi_all - max(hi1, hi2)) else hi_all + 0.7 * UNIT
    return s, min(max(s, lo1), hi1), min(max(s, lo2), hi2)


def _pts(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


@register
class SvgBackend(Backend):
    name = "svg"
    extension = ".svg"
    about = "each rule's drawing as a picture (SVG), for documents and other backends"

    def __init__(self, labels="layer"):
        # labels: what a shape is called in the figure -- its "layer" (as a manual does) or its "name"
        self.labels = labels

    def files(self, rules, stem):
        out = {}
        for r in rules:
            r = r if isinstance(r, Rule) else Rule(r)
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in r.name or "rule")
            out[f"{stem}.{safe}.svg"] = self.compile(r)
        return out

    def compile(self, rule: Rule) -> str:
        data = rule.data
        if data.get("or") and not data.get("shapes"):       # alternatives: the first one's drawing
            return self.compile(Rule({**data["or"][0], "name": rule.name, "desc": rule.desc}))
        px = lambda p: (p[0] * UNIT, p[1] * UNIT)            # noqa: E731
        shapes, sides = [], {}
        for sh in data.get("shapes", []):
            edges = sh.get("edges", [])
            if len(edges) < 2:
                continue
            verts = [px(e["start"]) for e in edges]
            centre = (sum(v[0] for v in verts) / len(verts), sum(v[1] for v in verts) / len(verts))
            try:
                layer = rule.layer_of(sh["name"])
            except Exception:                                # noqa: BLE001  (a shape with no layer said)
                layer = None
            shapes.append((sh, verts, centre, layer))
            for i, e in enumerate(edges):
                sides[e.get("id")] = (verts[i], verts[(i + 1) % len(verts)], sh["name"])
        if not shapes:
            return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>\n'
        allx = [v[0] for _, vs, _, _ in shapes for v in vs]
        ally = [v[1] for _, vs, _, _ in shapes for v in vs]
        body, marks = [], []                                 # marks: every point drawn, for the frame

        def outline(sh, verts, centre):
            out = []
            for i, e in enumerate(sh["edges"]):
                a, b = verts[i], verts[(i + 1) % len(verts)]
                out += [a] if e.get("edge") else _wavy(a, b, centre)
            return out

        holes = [outline(sh, v, c) for sh, v, c, _ in shapes if sh.get("cut")]
        for sh, verts, centre, layer in shapes:
            if sh.get("cut"):
                continue
            col = sh.get("color") or colour_of(layer or sh["name"])
            ring = outline(sh, verts, centre)
            marks += ring
            d = "M" + _pts(ring) + "Z" + "".join(" M" + _pts(h) + "Z" for h in holes)
            body.append(f'<path d="{d}" fill="{col}" fill-opacity="0.16" fill-rule="evenodd" stroke="none"/>')
            for i, e in enumerate(sh["edges"]):
                a, b = verts[i], verts[(i + 1) % len(verts)]
                if e.get("edge"):                            # a real edge: what a distance is measured to
                    body.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
                                f'stroke="{INK}" stroke-width="2.2" stroke-linecap="round"/>')
                else:                                        # the shape goes on: any form
                    body.append(f'<polyline points="{_pts(_wavy(a, b, centre, end=True))}" fill="none" '
                                f'stroke="{col}" stroke-width="1.3"/>')
        for sh, verts, centre, layer in shapes:              # holes on top, dashed
            if sh.get("cut"):
                ring = outline(sh, verts, centre)
                marks += ring
                body.append(f'<path d="M{_pts(ring)}Z" fill="#ffffff" fill-opacity="0.001" stroke="{HOLE}" '
                            f'stroke-width="1.4" stroke-dasharray="4 3"/>')
        for sh, verts, centre, layer in shapes:              # names, and the cross of a forbidden shape
            xs, ys = [v[0] for v in verts], [v[1] for v in verts]
            col = HOLE if sh.get("cut") else sh.get("color") or colour_of(layer or sh["name"])
            if sh.get("crossed"):
                body.append(f'<path d="M{min(xs):.1f},{min(ys):.1f} L{max(xs):.1f},{max(ys):.1f} '
                            f'M{min(xs):.1f},{max(ys):.1f} L{max(xs):.1f},{min(ys):.1f}" stroke="{NEVER}" stroke-width="3"/>')
            text = (layer if self.labels == "layer" and layer else sh["name"])
            text = ("hole in " if sh.get("cut") and self.labels == "layer" else "") + str(text)
            if sh.get("halo"):
                text += f"  ±{abs(sh['halo']):g}"
            tx, ty = verts[0][0] + 5, verts[0][1] + 15       # where the editor writes it: at the first corner
            body.append(f'<text x="{tx:.1f}" y="{ty:.1f}" fill="{col}" font-weight="600">{escape(text)}</text>')
            marks += [(tx, ty - 12), (tx + 7.2 * len(text), ty)]

        for m in data.get("measures", []):
            a, b = sides.get(m.get("from")), sides.get(m.get("to"))
            if not a or not b:
                continue
            (a1, b1, _), (a2, b2, _) = a, b
            vert = abs(a1[0] - b1[0]) < 1e-6 and abs(a2[0] - b2[0]) < 1e-6
            horiz = abs(a1[1] - b1[1]) < 1e-6 and abs(a2[1] - b2[1]) < 1e-6
            col = NEVER if m.get("crossed") else ARROW
            if vert:                                         # vertical edges: a horizontal dimension line
                s, c1, c2 = _place((a1[1], b1[1]), (a2[1], b2[1]), min(ally), max(ally))
                if m.get("at") is not None:
                    s = m["at"] * UNIT
                    c1 = min(max(s, min(a1[1], b1[1])), max(a1[1], b1[1]))
                    c2 = min(max(s, min(a2[1], b2[1])), max(a2[1], b2[1]))
                p1, p2, e1, e2 = (a1[0], s), (a2[0], s), (a1[0], c1), (a2[0], c2)
            elif horiz:
                s, c1, c2 = _place((a1[0], b1[0]), (a2[0], b2[0]), min(allx), max(allx))
                if m.get("at") is not None:
                    s = m["at"] * UNIT
                    c1 = min(max(s, min(a1[0], b1[0])), max(a1[0], b1[0]))
                    c2 = min(max(s, min(a2[0], b2[0])), max(a2[0], b2[0]))
                p1, p2, e1, e2 = (s, a1[1]), (s, a2[1]), (c1, a1[1]), (c2, a2[1])
            else:                                            # not parallel: from middle to middle
                p1 = ((a1[0] + b1[0]) / 2, (a1[1] + b1[1]) / 2)
                p2 = ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2)
                e1 = e2 = None
            for pe, pp in ((e1, p1), (e2, p2)):              # witness lines, from the edge out to the arrow
                if pe and (abs(pe[0] - pp[0]) > 0.5 or abs(pe[1] - pp[1]) > 0.5):
                    body.append(f'<line x1="{pe[0]:.1f}" y1="{pe[1]:.1f}" x2="{pp[0]:.1f}" y2="{pp[1]:.1f}" '
                                f'stroke="{col}" stroke-width="0.9" stroke-dasharray="2 2"/>')
            body.append(f'<line x1="{p1[0]:.1f}" y1="{p1[1]:.1f}" x2="{p2[0]:.1f}" y2="{p2[1]:.1f}" stroke="{col}" '
                        f'stroke-width="1.6" marker-start="url(#head-{col[1:]})" marker-end="url(#head-{col[1:]})"/>')
            mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
            if m.get("crossed"):
                body.append(f'<path d="M{mx - 7:.1f},{my - 7:.1f} L{mx + 7:.1f},{my + 7:.1f} M{mx - 7:.1f},{my + 7:.1f} '
                            f'L{mx + 7:.1f},{my - 7:.1f}" stroke="{col}" stroke-width="3"/>')
            along, side = m.get("label_offset", 0) * UNIT, m.get("label_side", -1)
            if abs(p1[1] - p2[1]) < 0.5:                     # horizontal line: the name above or below it
                lx, ly, anchor = mx + along, (my - 6 if side < 0 else my + 16), "middle"
            else:
                lx, ly, anchor = (mx - 6 if side < 0 else mx + 6), my + along + 4, ("end" if side < 0 else "start")
            body.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="{col}" text-anchor="{anchor}" '
                        f'font-weight="600">{escape(str(m.get("name", "")))}</text>')
            marks += [p1, p2, (lx - 18, ly - 12), (lx + 18, ly + 4)]

        x0, y0 = min(p[0] for p in marks) - PAD, min(p[1] for p in marks) - PAD
        x1, y1 = max(p[0] for p in marks) + PAD, max(p[1] for p in marks) + PAD
        heads = "".join(
            f'<marker id="head-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" fill="{c}"/></marker>' for c in (ARROW, NEVER))
        title = escape(" — ".join(x for x in (rule.name, rule.desc) if x))
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {x1 - x0:.1f} {y1 - y0:.1f}" '
                f'width="{x1 - x0:.0f}" height="{y1 - y0:.0f}" font-family="Helvetica, Arial, sans-serif" font-size="12">\n'
                f'<title>{title}</title>\n<defs>{heads}</defs>\n'
                f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" fill="#ffffff"/>\n'
                + "\n".join(body) + "\n</svg>\n")
