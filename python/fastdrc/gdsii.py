"""GDSII value tree -> flat per-layer polygons.

Consumes the rawast GDSII value tree and produces flat geometry in the top
cell's coordinate space:

  * hierarchy flattened — SREF (instances) and AREF (arrays) resolved
    recursively with their transform (magnify, reflect-about-x, rotate,
    translate), composed across nesting levels;
  * boundary / box elements -> polygon rings;
  * path elements -> stroked polygons (width, end caps);
  * text / node elements ignored (not geometry).

Transformed coordinates are snapped to the integer DBU grid (rounding), which is
how every EDA tool handles off-grid results from rotation/magnification.

`flatten()` returns a list of ``(gds_layer, gds_datatype, [(x, y), ...])`` tuples
— closed rings with the duplicate closing point dropped.
"""

from __future__ import annotations

import math

# Affine transform as (a, b, c, d, e, f): (x, y) -> (a*x + b*y + e, c*x + d*y + f).
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# STRANS bit flags (16-bit record; GDS numbers bit 0 as the MSB).
_REFLX = 0x8000     # reflect about the x-axis before rotation
_ABS_MAG = 0x0004
_ABS_ANGLE = 0x0002

_MAX_DEPTH = 200  # hierarchy-depth guard


def _apply(t, x, y):
    a, b, c, d, e, f = t
    return (a * x + b * y + e, c * x + d * y + f)


def _compose(t1, t2):
    """Transform that applies t2 then t1: (t1 ∘ t2)(p) = t1(t2(p))."""
    a1, b1, c1, d1, e1, f1 = t1
    a2, b2, c2, d2, e2, f2 = t2
    return (
        a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
        a1 * e2 + b1 * f2 + e1, c1 * e2 + d1 * f2 + f1,
    )


def _instance_transform(strans, mag, angle, tx, ty):
    """GDS instance transform: translate ∘ rotate ∘ reflect ∘ magnify."""
    m = 1.0 if mag is None else float(mag)
    deg = 0.0 if angle is None else float(angle)
    t = (m, 0.0, 0.0, m, 0.0, 0.0)                      # magnify
    if int(strans or 0) & _REFLX:
        t = _compose((1.0, 0.0, 0.0, -1.0, 0.0, 0.0), t)  # reflect about x
    th = math.radians(deg)
    cos, sin = math.cos(th), math.sin(th)
    t = _compose((cos, -sin, sin, cos, 0.0, 0.0), t)    # rotate
    t = _compose((1.0, 0.0, 0.0, 1.0, float(tx), float(ty)), t)  # translate
    return t


def _ring(xy):
    """Flat ``[x0, y0, ...]`` -> ``[(x, y), ...]`` with closing duplicate dropped."""
    pts = [(xy[i], xy[i + 1]) for i in range(0, len(xy) - 1, 2)]
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts.pop()
    return pts


def _xform(t, pts):
    out = []
    for (x, y) in pts:
        ax, ay = _apply(t, x, y)
        out.append((int(round(ax)), int(round(ay))))
    return out


def _stroke_path(el):
    """A PATH element -> list of polygon rings (centerline coords, untransformed).

    Each segment becomes an oriented rectangle offset by half the width;
    overlapping segment rectangles let the later union fill the joints. End caps:
    pathtype 0 = flush, 1 = round (semicircle, approximated), 2 = square (extend
    by half width), 4 = custom (begin_ext / end_ext).
    """
    xy = el.get("xy") or []
    pts = [(xy[i], xy[i + 1]) for i in range(0, len(xy) - 1, 2)]
    width = float(el.get("width") or 0)
    if width <= 0 or len(pts) < 2:
        return []
    half = width / 2.0
    ptype = int(el.get("pathtype") or 0)
    bext = float(el.get("begin_ext") or 0)
    eext = float(el.get("end_ext") or 0)

    polys = []
    n = len(pts)
    for k in range(n - 1):
        (x0, y0), (x1, y1) = pts[k], pts[k + 1]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        ux, uy = dx / length, dy / length      # unit direction
        px, py = -uy, ux                        # unit perpendicular
        s_ext = e_ext = 0.0
        if k == 0:
            s_ext = half if ptype == 2 else (bext if ptype == 4 else 0.0)
        if k == n - 2:
            e_ext = half if ptype == 2 else (eext if ptype == 4 else 0.0)
        sx, sy = x0 - ux * s_ext, y0 - uy * s_ext
        ex, ey = x1 + ux * e_ext, y1 + uy * e_ext
        polys.append([
            (sx + px * half, sy + py * half),
            (ex + px * half, ey + py * half),
            (ex - px * half, ey - py * half),
            (sx - px * half, sy - py * half),
        ])

    if ptype == 1:  # round caps: approximate a semicircle at each true end
        polys += _round_cap(pts[0], pts[1], half, outward=True)
        polys += _round_cap(pts[-1], pts[-2], half, outward=True)
    return polys


def _round_cap(end, neighbor, half, outward, segments=16):
    ex, ey = end
    nx, ny = neighbor
    dx, dy = ex - nx, ey - ny
    length = math.hypot(dx, dy)
    if length == 0 or half <= 0:
        return []
    base = math.atan2(dy, dx)  # direction pointing outward from the path
    fan = [(ex, ey)]
    for i in range(segments + 1):
        a = base - math.pi / 2 + math.pi * i / segments
        fan.append((ex + half * math.cos(a), ey + half * math.sin(a)))
    return [fan]


def flatten(tree, top=None, on_warn=None):
    """Flatten a GDSII tree into ``[(layer, datatype, [(x, y), ...]), ...]``."""
    warn = on_warn if on_warn is not None else (lambda _m: None)
    structs = {s["name"]: s for s in (tree.get("structures") or [])}
    if not structs:
        return []

    if top is not None:
        tops = [top]
    else:
        referenced = set()
        for s in structs.values():
            for el in s.get("elements") or []:
                if el.get("type") in ("sref", "aref"):
                    referenced.add(el.get("name"))
        tops = [n for n in structs if n not in referenced] or list(structs)

    out = []
    abs_warned = [False]

    def walk(name, t, stack):
        s = structs.get(name)
        if s is None:
            warn(f"reference to undefined cell {name!r}; skipped")
            return
        if name in stack:
            warn(f"cyclic reference to cell {name!r}; skipped")
            return
        if len(stack) >= _MAX_DEPTH:
            warn(f"hierarchy deeper than {_MAX_DEPTH} at {name!r}; truncated")
            return
        stack = stack | {name}

        for el in s.get("elements") or []:
            kind = el.get("type")
            if kind in ("boundary", "box"):
                out.append((el["layer"], el["datatype"], _xform(t, _ring(el["xy"]))))
            elif kind == "path":
                for poly in _stroke_path(el):
                    out.append((el["layer"], el["datatype"], _xform(t, poly)))
            elif kind in ("sref", "aref"):
                if int(el.get("strans") or 0) & (_ABS_MAG | _ABS_ANGLE) and not abs_warned[0]:
                    warn("absolute magnification/angle flag ignored "
                         "(treated as relative)")
                    abs_warned[0] = True
                if kind == "sref":
                    ox, oy = el["xy"][0], el["xy"][1]
                    inst = _instance_transform(
                        el.get("strans"), el.get("mag"), el.get("angle"), ox, oy)
                    walk(el["name"], _compose(t, inst), stack)
                else:  # aref
                    cols, rows = el["colrow"]
                    xy = el["xy"]
                    ox, oy = xy[0], xy[1]
                    cstep = (((xy[2] - ox) / cols, (xy[3] - oy) / cols)
                             if cols else (0.0, 0.0))
                    rstep = (((xy[4] - ox) / rows, (xy[5] - oy) / rows)
                             if rows else (0.0, 0.0))
                    for j in range(int(rows)):
                        for i in range(int(cols)):
                            px = ox + i * cstep[0] + j * rstep[0]
                            py = oy + i * cstep[1] + j * rstep[1]
                            inst = _instance_transform(
                                el.get("strans"), el.get("mag"), el.get("angle"),
                                px, py)
                            walk(el["name"], _compose(t, inst), stack)
            # text / node: not geometry — ignored

    for name in tops:
        walk(name, _IDENTITY, frozenset())
    return out
