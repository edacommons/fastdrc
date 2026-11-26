"""A FastDRC backend for gdscheck (https://github.com/aesc-silicon/gdscheck).

gdscheck reads a PDK: a ``pdk.yml`` (the layers, the derived "virtual" layers, each a
sentence such as ``comp and nplus not_inside nwell``) and decks of rules, each one of
its named checks on such layers (``min_width``, ``min_space``, ``min_enclosure``,
``forbidden`` ...). This backend writes that PDK from the drawn rules.

How a rule gets there. gdscheck's words for layers are KLayout's, on purpose, so the
operations a drawn rule comes to are taken from the `klayout` backend (a backend may use
another) as the graph `fastdrc.ir` reads them, and written again in gdscheck's terms:

  * a layer expression                  -> a virtual layer, its sentence
  * ``a.width(v)``                      -> ``min_width``
  * ``a.space(v)``                      -> ``min_space`` and ``min_notch`` (KLayout's space is both)
  * ``a.separation(b, v)``              -> ``min_space`` on two layers
  * ``a.enclosing(b, v)``               -> ``min_enclosure``, read as KLayout reads it
  * ``a.overlap(b, v)``                 -> ``min_overlap``
  * ``a.ongrid(g)``                     -> ``offgrid``
  * what must not exist (a layer left over by the rule's own operations) -> ``forbidden``

A rule with a part that has no form here yet is NOT written in part: it is left out
whole, and named with the reason -- at the head of the deck, and in `problems`, which
`fastdrc build` prints.

One file is compiled (``<name>.gdscheck``): the PDK's files one after another, each under
a line ``#### file: <path>``. `run` unfolds it into ``<name>.gdscheck.pdk/`` and starts
gdscheck on it; ``fastdrc build DECK --with gdscheck`` writes that folder directly.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess

from fastdrc.backend import Backend, RunError, get
from fastdrc.ir import Deck, ParseError
from fastdrc.rule import Rule

__version__ = "0.0.1"

FILE = "#### file: "


class NoForm(Exception):
    """This part of a rule has no gdscheck form (yet); the message says which."""


# KLayout's name -> gdscheck's word, where the meaning is the same
_SELECT = {"inside", "not_inside", "outside", "not_outside", "interacting", "not_interacting",
           "overlapping", "not_overlapping", "covering", "not_covering"}
_COUNTED = {"interacting", "overlapping", "covering"}           # these take "how many of the other"
_UNARY = {"holes": "holes", "extents": "extents", "with_holes": "with_holes", "rectangles": "rectangle",
          "non_rectangles": "not_rectangle", "squares": "square", "non_squares": "not_square"}
_SAME = {"merged", "dup"}                                       # no change to what the layer is
_RANGED = {"with_area": "region", "with_bbox_min": "region", "with_bbox_max": "region",
           "with_length": "edges", "without_length": "edges"}   # word(min, max): half-open, as KLayout's
# a selection made on a check's markers, as the selection of shapes it stands for
_NARROW = {"overlapping": "overlapping", "not_outside": "overlapping", "interacting": "interacting",
           "not_overlapping": "not_overlapping", "outside": "not_overlapping", "not_interacting": "not_interacting"}
_ONE = {"width": "min_width", "notch": "min_notch", "isolated": "min_space"}
_TWO = {"separation": "min_space", "enclosing": "min_enclosure", "overlap": "min_overlap"}
# a check's markers read as a layer: gdscheck's word for the same quadrangles
_KEPT = {"separation": "separation_below", "enclosing": "enclosure_below", "enclosed": "enclosure_below"}


def _is(node, name=None, nargs=None):
    """Is the node an operation on a layer (of that name, with that many values)?"""
    return (node[0] == "call" and node[1] is not None and (name is None or node[2] == name)
            and (nargs is None or len(node[3] or ()) == nargs))


def _lit(node, text):
    return node == ("lit", text)


def _number(node):
    """A KLayout number (``0.22.um``, ``-0.5``) as a float; None for ``nil``."""
    if node[0] == "lit" and node[1] == "nil":
        return None
    m = node[0] == "lit" and re.fullmatch(r"(-?\d+(?:\.\d+)?(?:e-?\d+)?)(?:\.(um|um2|micron|degree))?", node[1])
    if not m:
        raise NoForm("a value that is not a plain number")
    return float(m.group(1))


def _fmt(v):
    return f"{v:.6f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "rule"


def _text(s):
    """A YAML double-quoted string."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Translation:
    """The rules of one deck in gdscheck's terms."""

    def __init__(self, layermap=None):
        self.layermap = dict(layermap or {})
        self.by_number = {}
        for name, number in self.layermap.items():
            self.by_number.setdefault(tuple(number), name)
        self.drawn = {}        # drawn layers the rules read: name -> (layer, datatype)
        self.virtual = {}      # sentence -> the name of its virtual layer
        self.entries = []      # (rule id, description, check, [layers], value, {params})
        self.left_out = []     # (rule name, why)
        self.connections = []     # the deck's connections, in order: [(layer, layer)] (see `finish`)
        self.ids = {}             # rule name -> the ids it is entered under
        self.net_rules = []       # (rule name, [((layer, layer), joins no nets)], antenna: (params, conductor) | None)
        self.tied = []            # (a layer a net rule reads, the connected layer it is a part of)
        self.halves = {}          # of the rule's connections: a half of a layer -> the layer (see `connect`)
        self.rule = None       # the rule being translated: what an idiom needs to know of the drawing

    # ---- a layer expression as a sentence ----
    def sentence(self, node):
        """``(text, "region" | "edges")`` of a layer node of the graph."""
        kind = node[0]
        if kind == "text":                                         # a sentence already written
            return node[1], "region"
        if kind == "in":
            if node[1] != "polygons":
                raise NoForm("the layout's extent" if node[1] == "extent" else
                             f"a layer with no GDS number ({node[2][0][1].strip(chr(34))})" if node[1] == "input"
                             else f"KLayout's {node[1]}")
            number = tuple(int(_number(a)) for a in node[2])
            name = self.by_number.get(number)
            if name is None or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
                raise NoForm(f"layer {number} has no name gdscheck can read")
            self.drawn[name] = number
            return name, "region"
        if kind != "call" or node[1] is None:
            raise NoForm("the layout's extent as a layer" if node == ("lit", "extent")
                         else "an expression that is not an operation on a layer")
        _, receiver, name, args, state = node
        if receiver[0] == "lit" and name == "call":
            raise NoForm({"side_sums": "a sum of the sides of the shapes in one (gdscheck sums areas only)",
                          "centres_closer": "a distance between the centres of shapes (gdscheck measures between edges)",
                          }.get(receiver[1], f"written with a Ruby helper ({receiver[1]})"))
        if state is not None:
            raise NoForm("nets")
        args = args or ()
        if name == "polygons" and receiver[0] == "call" and receiver[2] in _KEPT and receiver[4] is None:
            # a measurement kept as geometry: the quadrangles between the walls closer than the value
            a, (b, value, *how) = receiver[1], receiver[3]
            if any(h != ("lit", "euclidian") for h in how):
                raise NoForm(f"'{receiver[2]}' kept as a layer, measured otherwise than between the nearest points")
            first, fk = self.operand(a)
            second, sk = self.operand(b)
            if fk != "region" or sk != "region":
                raise NoForm(f"'{receiver[2]}' of edges kept as a layer")
            if receiver[2] == "enclosed":                          # gdscheck names the outer layer first
                first, second = second, first
            return f"{first} {_KEPT[receiver[2]]} {_fmt(_number(value))} {second}", "region"
        if name == "middle":
            raise NoForm("the centre of a shape (gdscheck has no word for it)")
        left, lk = self.sentence(receiver)
        if name in _SAME and not args:
            return left, lk
        if name == "edges" and not args and lk == "region":
            return f"{left} edges", "edges"
        if name in _UNARY and not args and lk == "region":
            return f"{left} {_UNARY[name]}", "region"
        if (name in ("not", "not_in") and len(args) == 1 and lk == "edges" and _is(args[0], "first_edges", 0)
                and _is(args[0][1], "separation") and args[0][1][4] is None):
            # edges without those that face the other layer closer than the value: the gaps, kept as
            # geometry, lie on the walls that face it
            a, (b, value, *how) = args[0][1][1], args[0][1][3]
            first, second = self.operand(a)[0], self.operand(b)[0]
            if not left.startswith(self.sentence(a)[0]) or set(how) - {("lit", "projection"), ("lit", "whole_edges")}:
                raise NoForm("edges not among those a check finds")
            gaps = f"({first} separation_below {_fmt(_number(value))} {second})"
            if ("lit", "whole_edges") in how:                      # whole edges: those no gap lies on
                return f"{left} centers 99% not_interacting {gaps}", "edges"
            return f"{left} outside_part {gaps}", "edges"         # or the parts of them no gap lies on
        if name in ("and", "or", "join", "not") and len(args) == 1:
            right, rk = self.operand(args[0])
            if lk == rk:
                return f"{left} {'or' if name == 'join' else name} {right}", lk
            if lk == "edges" and name in ("and", "not"):           # edges cut at a region's boundary
                return f"{left} {'inside_part' if name == 'and' else 'outside_part'} {right}", "edges"
            raise NoForm(f"'{name}' of polygons with edges")
        if name == "not_in" and len(args) == 1 and lk == "region" and self.sentence(args[0])[1] == "region":
            return f"{left} not {self.operand(args[0])[0]}", "region"   # the shapes that are not shapes of the other
        if name in _SELECT and 1 <= len(args) <= 3:
            right, rk = self.operand(args[0])
            count = ""
            if len(args) > 1:
                if name.removeprefix("not_") not in _COUNTED or lk != "region" or rk != "region":
                    raise NoForm(f"a count on '{name}'")
                lo, hi = _number(args[1]), (_number(args[2]) if len(args) > 2 else None)
                count = "".join(f" {w} {_fmt(v)}" for w, v in (("min", lo), ("max", hi)) if v is not None)
            if count and name.startswith("not_"):                  # the count is said of the positive word:
                keep = f"{left} {name[4:]}{count} {right}"            # all of it but those with that many
                return f"{left} not ({keep})", "region"
            if lk == "region" and rk == "region":
                return f"{left} {name}{count} {right}", "region"
            if name in ("interacting", "not_interacting"):
                if lk == "region":                                 # the polygons an edge touches
                    return f"{left} {name}{count} {right}", "region"
                return f"{left} {name}{'_edges' if rk == 'edges' else ''} {right}", "edges"
            raise NoForm(f"'{name}' on edges")
        if name == "sized" and lk == "region" and len(args) == 1:
            d = _number(args[0])
            return f"{left} {'grow' if d >= 0 else 'shrink'} {_fmt(abs(d))}", "region"
        if name in _RANGED and lk == _RANGED[name] and len(args) in (1, 2):
            values = [_number(a) for a in args]
            if len(values) == 1 and name.startswith("with_bbox"):     # exactly: the one step of the grid, [v, v + 1 nm)
                return f"{left} {name} min {_fmt(values[0])} max {_fmt(values[0] + 0.001)}", lk
            if len(values) == 1 and name == "with_area":
                return f"{left} {name} min {_fmt(values[0])} max {_fmt(values[0] + 0.000001)}", lk
            if len(values) == 1:
                return f"{left} {name} {_fmt(values[0])}", lk          # exactly
            said = "".join(f" {w} {_fmt(v)}" for w, v in zip(("min", "max"), values) if v is not None)
            if not said:
                raise NoForm(f"'{name}' with no bound")
            return f"{left} {name}{said}", lk
        if name.startswith("without_") and "with_" + name[8:] in _RANGED and lk == "region" and len(args) in (1, 2):
            kept, _ = self.sentence(("call", receiver, "with_" + name[8:], args, None))
            return f"{left} not ({kept})", "region"                # whole shapes: all but those it keeps
        if name in ("with_angle", "without_angle") and lk == "edges" and len(args) == 1:
            return f"{left} {name} {_fmt(_number(args[0]))}", "edges"
        if name == "polygons" or name in _ONE or name in _TWO or name == "space":
            raise NoForm("a check whose markers are narrowed afterwards by another layer")
        raise NoForm(f"KLayout's '{name}'" + (f" with {len(args)} values" if args else "") + f" on {lk}")

    def operand(self, node):
        text, kind = self.sentence(node)
        return (text if re.fullmatch(r"[\w.]+", text) else f"({text})"), kind

    def layer(self, node, stem, want="region"):
        """The name a check reads this layer by: the drawn layer's, or a virtual layer's."""
        text, kind = self.sentence(node)
        if kind != want and want is not None:
            raise NoForm(f"a check on {kind}")
        if re.fullmatch(r"[\w.]+", text):
            return text
        if text not in self.virtual:
            name, k = stem, 2
            while name in self.virtual.values() or name in self.layermap:
                name, k = f"{stem}_{k}", k + 1
            self.virtual[text] = name
        return self.virtual[text]

    # ---- what KLayout says in several operations and gdscheck has one check for ----
    def idiom(self, node):
        """``(check, layer, other layer | None, value, {params}, how it is narrowed)`` if the
        node is one of the forms below, else None."""
        inner = node[1] if _is(node, "polygons") else node
        for form in (self.gate, self.line_end, self.array, self.block, self.adjacent, self.opposite, self.wide, self.density, self.bend, self.several_nets):
            found = form(inner) or (form(node) if inner is not node else None)
            if found:
                return found
        return None

    @staticmethod
    def gate(node):
        """``x.edges.and(g.edges).width(v)``, g being x under another layer: the width of x
        between the walls it shares with g -- a gate's length (x the poly) or the
        transistor's width (x the active). Edges kept inside / outside a layer narrow g."""
        if not (_is(node, "width", 2) and _lit(node[3][1], "euclidian")):
            return None
        edges, params = node[1], {}
        if edges[0] == "bin" and edges[1] == "+":                  # the 45 degree walls of both directions
            a, b = edges[2], edges[3]
            if not (_is(a, "with_angle", 1) and _is(b, "with_angle", 1) and a[1] == b[1]
                    and {abs(_number(a[3][0])), abs(_number(b[3][0]))} == {45.0}):
                return None
            edges, params = a[1], {"angle": "bent"}
        cut = []
        while not (_is(edges, "and", 1) and _is(edges[1], "edges", 0) and _is(edges[3][0], "edges", 0)):
            if not ((_is(edges, "and", 1) or _is(edges, "not", 1)) and not _is(edges[3][0], "edges")):
                return None
            cut.insert(0, (edges[2], edges[3][0]))
            edges = edges[1]
        x, g = edges[1][1], edges[3][0][1]
        parts, todo = [], [g]
        while todo:
            n = todo.pop()
            if _is(n, "and", 1):
                todo += [n[1], n[3][0]]
            else:
                parts.append(n)
        if x not in parts or len(parts) < 2:
            return None
        return "min_gate_length", x, Translation.wrap(g, cut), _number(node[3][0]), params, "reference"

    @staticmethod
    def line_end(node):
        """The enclosure of a via at the END of a narrow line: KLayout finds the cap edges
        of the lines under a width that run a length, and measures from those."""
        if not (_is(node, "enclosing", 3) and _lit(node[3][2], "projection") and _is(node[3][0], "edges", 0)):
            return None
        seen, todo = set(), [node[1]]
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            if (_is(n, "with_length", 3) and _is(n[1], "drc", 1) and n[1][3][0][0] == "bin"
                    and n[1][3][0][1] in ("<", "<=") and _lit(n[1][3][0][2], "width")):
                # gdscheck's track is NARROWER than max_width: one at the width itself is a step of the grid under
                widest = _number(n[1][3][0][3]) + (0.001 if n[1][3][0][1] == "<=" else 0)
                return ("min_enclosure", n[1][1], node[3][0][1], _number(node[3][1]),
                        {"interacting_only": 1, "sides": "line_end", "max_width": _fmt(widest),
                         "min_length": _fmt(_number(n[3][0]))}, "pair")
            todo += Deck.children(n)
        return None

    @staticmethod
    def adjacent(node):
        """A via enclosed by less than t on one side must have v on the sides next to it."""
        if not (_is(node, "interacting", 1) and _is(node[3][0], "polygons") and _is(node[3][0][1], "overlap", 3)):
            return None
        via, both = node[1], node[3][0][1]
        sides = []
        for side in (both[1], both[3][0]):
            if not (_is(side, "second_edges", 0) and _is(side[1], "enclosing", 4) and side[1][3][0] == via
                    and _lit(side[1][3][2], "projection")):
                return None
            sides.append((side[1][1], _number(side[1][3][1])))
        if sides[0][0] != sides[1][0]:
            return None
        return ("min_enclosure", sides[0][0], via, max(sides[0][1], sides[1][1]),
                {"interacting_only": 1, "sides": "adjacent", "trigger": _fmt(min(sides[0][1], sides[1][1]))}, "pair")

    @staticmethod
    def opposite(node):
        """A via whose enclosure falls short on two sides that meet at a corner: it must have
        the value on one pair of opposite sides at least."""
        if not (_is(node, "interacting", 1) and _is(node[3][0], "polygons") and _is(node[3][0][1], "width", 2)):
            return None
        via, sides = node[1], node[3][0][1][1]
        if not (_is(sides, "second_edges", 0) and _is(sides[1], "enclosing", 4) and sides[1][3][0] == via
                and _lit(sides[1][3][2], "projection") and _lit(sides[1][3][3], "whole_edges")):
            return None
        return ("min_enclosure", sides[1][1], via, _number(sides[1][3][1]),
                {"interacting_only": 1, "sides": "opposite"}, "pair")

    @staticmethod
    def wide(node):
        """The space of a wide line running alongside another for a length:
        ``x.space(v, projection, projecting > l)``, of the pairs with a part of x wider than w."""
        if not (_is(node, "interacting", 1) and _is(node[1], "polygons") and _is(node[1][1], "space", 3)):
            return None
        space, opened = node[1][1], node[3][0]
        run = space[3][2]
        if not (_lit(space[3][1], "projection") and run[0] == "bin" and run[1] == ">" and _lit(run[2], "projecting")
                and _is(opened, "sized", 1) and _is(opened[1], "sized", 1) and opened[1][1] == space[1]
                and _number(opened[3][0]) == -_number(opened[1][3][0]) > 0):
            return None
        return ("min_space", space[1], None, _number(space[3][0]),
                {"width": _fmt(2 * _number(opened[3][0])), "length": _fmt(_number(run[3]))}, "alone")

    def block(self, node):
        """The space of the vias of a block of so many rows and columns, tight both ways
        (one direction at the value is enough): KLayout closes the layer and wears the
        blocks down to find them; gdscheck counts the rows and columns."""
        if not (_is(node, "interacting", 1) and _is(node[3][0], "polygons") and _is(node[3][0][1], "width", 2)):
            return None
        vias, tight = node[1], node[3][0][1][1]
        if not (_is(tight, "and", 1) and _is(tight[1], "edges", 0) and _is(tight[1][1], "space", 3)
                and tight[1][1][1] == vias and tight[3][0] == ("call", vias, "edges", None, None)
                and _lit(tight[1][1][3][1], "projection") and _lit(tight[1][1][3][2], "whole_edges")
                and _is(vias, "inside", 1) and vias[3][0][0] == "call"):
            return None
        worn = vias[3][0]
        for _ in range(4):
            if not _is(worn, "sized", 1):
                return None
            worn = worn[1]
        if worn != vias[1]:
            return None
        said = next(iter(self.rule.array_filters().values()), None) if self.rule is not None else None
        if not said:
            raise NoForm("an array whose rows and columns the rule does not say")
        return ("min_space", vias[1], None, _number(tight[1][1][3][0]),      # gdscheck: MORE than so many rows
                {"rows": said["rows"] - 1, "cols": said["columns"] - 1}, "alone")

    def shares(self, node, stem):
        """A layer's share of an area, between two bounds: of the chip (or of the layer that
        is the die), of every window laid over it, or of each wide plate of another layer."""
        def bound(low, layer, value, params):
            return ("min_density" if low else "max_density", [self.layer(layer, stem)], value * 100, params)

        if node[0] == "if" and node[1][0] == "bin":                # (a.area < f * die.area || ...) ? die : nothing
            out = []
            for test in ((node[1][2], node[1][3]) if node[1][1] == "||" else (node[1],)):
                if not (test[0] == "bin" and test[1] in ("<", ">") and _is(test[2], "area")):
                    return None
                share = test[3]
                while share[0] == "bin" and share[1] == "*" and not _is(share[3], "area"):
                    share = share[2]                               # a hair of slack on the value: not said here
                if not (share[0] == "bin" and share[1] == "*" and _is(share[3], "area") and share[2][0] == "lit"):
                    return None
                die = share[3][1]
                params = {} if _lit(die, "extent") else {"layer:boundary": self.layer(die, stem + "_die")}
                out.append(bound(test[1] == "<", test[2][1], _number(share[2]), params))
            return out
        if node[0] == "call" and node[1] == ("lit", "out_of_density") and node[2] == "call" and len(node[3] or ()) == 5:
            layer, span, die, window, step = node[3]
            if not (span[0] == "bin" and span[1] == ".."):
                return None
            params = {"scope": "window", "window": _fmt(_number(window)), "step": _fmt(_number(step))}
            if not _lit(die, "extent") and not _lit(die, "nil"):
                params["layer:boundary"] = self.layer(die, stem + "_die")
            lo, hi = _number(span[2]), _number(span[3])
            return [bound(low, layer, v, dict(params)) for low, v in ((True, lo), (False, hi)) if 0 < v < 1]
        if (_is(node, "selected_if", 1) and node[1][0] == "call" and node[1][1] is None and node[1][2] == "evaluate_nets"
                and len(node[1][3]) == 3 and node[1][3][1][0] == "hash" and len(node[1][3][1][1]) == 1):
            plate, ((key, held),), formula = node[1][3][0], node[1][3][1][1], node[1][3][2]
            name = key[1].strip('"')
            test = re.fullmatch(r"value\('(\w+)'\) (<|>) ([\d.]+)", node[3][0][1].strip('"'))
            if not (test and formula[1].strip('"') == f"put('{test.group(1)}', (area({name})) / area)"
                    and _is(plate, "sized", 1) and _is(plate[1], "sized", 1) and _is(held, "and", 1) and held[3][0] == plate
                    and _number(plate[3][0]) == -_number(plate[1][3][0]) > 0):
                return None
            check = "min_density" if test.group(2) == "<" else "max_density"
            return [(check, [self.layer(plate[1][1], stem), self.layer(held[1], stem + "_in")], float(test.group(3)) * 100,
                     {"scope": "region", "min_size": _fmt(2 * _number(plate[3][0]))})]
        return None

    def array(self, node):
        """The space of vias in an array: those in a group of so many, each close to a
        neighbour. How many rows and columns make an array is in the drawn rule."""
        if not (_is(node, "space", 2) and _lit(node[3][1], "euclidian") and _is(node[1], "inside", 1)):
            return None
        via, group = node[1][1], node[1][3][0]
        if not (_is(group, "interacting", 2) and group[3][0] == via and _is(group[1], "or", 1) and group[1][1] == via):
            return None
        near = group[1][3][0]
        if not (_is(near, "polygons") and _is(near[1], "space", 2) and near[1][1] == via):
            return None
        said = next(iter(self.rule.array_filters().values()), None) if self.rule is not None else None
        if not said:
            raise NoForm("an array whose rows and columns the rule does not say")
        return ("min_space", via, None, _number(node[3][0]),         # gdscheck: MORE than so many rows
                {"rows": said["rows"] - 1, "cols": said["columns"] - 1, "axes": 2,
                 "pitch": _fmt(_number(near[1][3][0])),         # a gap AT the drawn one is still the array's
                 "count": _fmt(_number(group[3][1]))}, "alone")

    @staticmethod
    def bend(node):
        """``x.corners(90).sized(0.1).or(x.corners(-90).sized(0.1)).inside(layer)``: no corner
        of that turn, either way, on the layer (a dot at the corner must lie inside it)."""
        if not (_is(node, "inside", 1) and _is(node[1], "or", 1)):
            return None
        dots = (node[1][1], node[1][3][0])
        if not all(_is(d, "sized", 1) and _is(d[1], "corners", 1) for d in dots) or dots[0][1][1] != dots[1][1][1]:
            return None
        turn, other = (_number(d[1][3][0]) for d in dots)
        if turn != -other or any(abs(_number(d[3][0]) - 0.1) > 1e-9 for d in dots):
            return None
        return ("no_corner", dots[0][1][1], node[3][0], abs(turn), {}, "reference")

    def several_nets(self, node):
        """The helper that reports the shapes under one marker that are of several nets."""
        if not (node[0] == "call" and node[1] == ("lit", "on_several_nets") and node[2] == "call" and len(node[3] or ()) == 2):
            return None
        bases = []
        held = self.of_nets(node[3][1], bases)
        self.connect()
        return ("max_nets_under", node[3][0], held, 1, {}, "reference")

    @staticmethod
    def density(node):
        """``(layer.area < f * extent.area) ? extent : nothing``: the layer's share of the chip."""
        if not (node[0] == "if" and node[1][0] == "bin" and node[1][1] in ("<", "<=", ">", ">=")
                and _is(node[1][2], "area")):
            return None
        share = node[1][3]
        while share[0] == "bin":
            share = share[2]
        low = node[1][1][0] == "<"                                 # gdscheck's bounds are allowed values; one
        edge = (1e-6 if low else -1e-6) if node[1][1].endswith("=") else 0   # that is itself a violation: a step past
        return ("min_density" if low else "max_density", node[1][2][1], None,
                _number(share) * 100 + edge, {}, "alone")

    def said(self, found, stem, narrowed):
        check, a, b, value, params, how = found
        if how == "pair":
            return self.pair(check, a, b, value, params, narrowed, stem)
        if how == "reference":                                     # the whole layer, between the walls of the
            return [(check, [self.layer(a, stem), self.layer(self.wrap(b, narrowed), stem)], value, params)]   # ones chosen
        if narrowed:
            raise NoForm(f"'{check}' narrowed afterwards by another layer")
        return [(check, [self.layer(a, stem)], value, params)]

    def pair(self, check, a, b, value, params, narrowed, stem):
        """A check of two layers, on the shapes a narrowing selects.

        Shapes that must NOT touch the other layer: both layers are taken without those
        that do. Shapes that MUST: each pair with one of them in it is read, once."""
        if "edges" in (self.sentence(a)[1], self.sentence(b)[1]):  # between walls: an edge is in a marker's
            if check == "min_overlap":                             # layer if it touches it
                raise NoForm("an overlap of edges")
            narrowed = [(w.replace("overlapping", "interacting"), x) for w, x in narrowed]
        without = [(w, x) for w, x in narrowed if w.startswith("not_")]
        must = [(w, x) for w, x in narrowed if not w.startswith("not_")]
        a, b = self.wrap(a, without), self.wrap(b, without)
        chosen = self.wrap(a, must)
        out = [(check, [self.layer(chosen, stem, None), self.layer(b, stem, None)], value, params)]
        if must:                                                   # whole shapes: a selection keeps or drops them whole
            rest = ("call", a, "not", (chosen,), None)
            out += [(check, [self.layer(rest, stem, None), self.layer(self.wrap(b, must), stem, None)], value, params)]
        return out

    # ---- what a rule reports ----
    def has_check(self, node):
        """Is this the markers of a check (or several, or those narrowed by a layer)?"""
        if self.idiom(node):
            return True
        if node[0] != "call" or node[1] is None:
            return False
        _, receiver, name, args, _ = node
        if name in ("or", "join") and args and len(args) == 1:
            return self.has_check(receiver) or self.has_check(args[0])
        if name in _SELECT:
            return self.has_check(receiver)
        if name == "extended":
            return True
        return (name == "polygons" and receiver[0] == "call"
                and (receiver[2] in _ONE or receiver[2] in _TWO or receiver[2] in ("space", "ongrid", "with_angle")))

    @staticmethod
    def wrap(node, selections):
        for name, other in selections:
            node = ("call", node, name, (other,), None)
        return node

    def reports(self, node, stem, narrowed=()):
        """One reported layer -> ``[(check, [layers], value, {params})]``.

        `narrowed`: selections by another layer made on the result afterwards -- the
        column of a manual's table, a marker the checked shape is drawn in or apart from.
        A drawing says that of the SHAPE, and here it is said of the shape: the check
        reads the shapes so selected (see `measured`)."""
        found = self.shares(node, stem)
        if found:
            if narrowed:
                raise NoForm("a share of an area narrowed afterwards by another layer")
            return found
        found = self.antenna(node)
        if found:
            if narrowed:
                raise NoForm("an antenna rule narrowed afterwards by another layer")
            return found
        found = self.idiom(node)
        if found:
            return self.said(found, stem, narrowed)
        if node[0] == "call" and node[1] is not None:
            _, receiver, name, args, _ = node
            args = args or ()
            if name in ("or", "join") and len(args) == 1:          # reports a, and reports b: a check each
                return self.reports(receiver, stem, narrowed) + self.reports(args[0], stem, narrowed)
            if (name == "not_covering" and len(args) == 1 and _is(receiver, "polygons") and _is(receiver[1], "overlap", 3)
                    and receiver[1][3][0] == args[0]):
                # an overlap's marker lies in both layers; it covers a shape of the second only if that
                a, (b, *rest) = receiver[1][1], receiver[1][3]     # shape lies wholly in the first
                inner = ("call", a, "overlap", (("call", b, "not_inside", (a,), None), *rest), None)
                return self.reports(("call", inner, "polygons", receiver[3], None), stem, narrowed)
            if name in _SELECT and self.has_check(receiver):
                if name in _NARROW and len(args) == 1:
                    return self.reports(receiver, stem, ((_NARROW[name], args[0]),) + tuple(narrowed))
                # otherwise the markers themselves are the layer selected from (see `sentence`)
            if name == "polygons" and receiver[0] == "call" and receiver[1] is not None:
                found = self.measured(receiver, stem, narrowed, bool(args))   # the markers of a check, as polygons
                if found is not None:
                    return found
            if (name == "and" and len(args) == 1 and _is(receiver, "not", 1) and _is(receiver[1], "extended_out", 1)
                    and _is(receiver[3][0], "extended_out", 1) and receiver[1][1] == receiver[3][0][1] and not narrowed):
                # the layer in the hair of a band just past a distance from these edges: it reaches further
                far, near = _number(receiver[1][3][0]), _number(receiver[3][0][3][0])
                if abs(far - near - 0.001) > 1e-9:
                    raise NoForm("a band at a distance from edges")
                edges, _ = self.operand(receiver[1][1])
                base = receiver[1][1]
                while base[0] == "call" and base[2] != "edges":    # the layer those edges are of
                    base = base[1]
                if base[0] != "call":
                    raise NoForm("a band at a distance from edges of no one layer")
                outer, inner = self.operand(args[0])[0], self.operand(base[1])[0]
                text = f"{outer} enclosure_above {_fmt(near)} {inner} inside {outer} interacting ({edges} centers 99%)"
                return [("forbidden", [self.layer(("text", text), stem)], 0.0, {})]
            if name == "with_angle" and len(args) == 1 and args[0][0] == "bin" and not narrowed:
                return self.corners(receiver, args, stem)
            if name == "extended" and len(args) == 4:              # edges drawn as slivers, to be seen: the
                edges = self.wrap(receiver, [("interacting" if "not" not in w else "not_interacting", x)
                                             for w, x in narrowed])      # edges are the report
                return [("forbidden", [self.layer(edges, stem, "edges")], 0.0, {})]
        return [("forbidden", [self.layer(self.wrap(node, narrowed), stem, None)], 0.0, {})]

    def corners(self, receiver, args, stem):
        """``layer.with_angle(lo .. hi)``: no corner whose two edges enclose such an angle.
        gdscheck names a corner by its turn, which is 180 less the angle, both ends counted;
        KLayout's range leaves its upper end out, hence the hair added."""
        if len(args) != 1 or args[0][0] != "bin" or args[0][1] != "..":
            raise NoForm("corners of one exact angle")
        lo, hi = _number(args[0][2]), _number(args[0][3])
        return [("no_corner", [self.layer(receiver, stem)], 0.0,
                 {"min": _fmt(180 - hi + 0.005), "max": _fmt(180 - lo)})]

    def measured(self, node, stem, narrowed=(), touching=False):
        """A check, on the layers as drawn or on the shapes a narrowing selects.

        Shapes that must NOT touch the other layer: every layer of the check is taken
        without those that do. Shapes that MUST: a one-layer check reads those that do
        (a space also between one of them and one of the rest); a two-layer check reads
        each pair with one of them in it, once.

        `touching`: the markers are given a width, so the one between two shapes drawn edge
        to edge -- a space of nothing, with no area -- is seen: touching is reported."""
        _, receiver, name, args, _ = node
        args = args or ()
        if name in ("with_angle", "ongrid") and narrowed:
            raise NoForm(f"'{name}' narrowed afterwards by another layer")
        if name == "with_angle":                                   # on polygons: the corners of that angle
            return self.corners(receiver, args, stem)
        if name == "ongrid" and len(args) == 1:
            return [("offgrid", [self.layer(receiver, stem)], _number(args[0]), {})]
        if name not in _ONE and name != "space" and name not in _TWO:
            return None
        other = 1 if name in _TWO else 0
        nets = len(args) == other + 3 and _lit(args[-1], "props_ne")       # between shapes of different nets
        if nets:
            if name not in ("space", "separation"):
                raise NoForm(f"'{name}' between different nets")
            if name == "space" and receiver[0] == "bin" and receiver[1] == "+" and len(args) == 3 and not narrowed \
                    and _lit(args[1], "euclidian"):
                # the shapes of two layers together: each layer's own spaces, and those from one to the other
                first, second = [], []
                a, b = self.of_nets(receiver[2], first), self.of_nets(receiver[3], second)
                self.connect()
                la, lb, params = self.layer(a, stem), self.layer(b, stem + "_2"), {"net": "different"}
                across = {**params, "abutting": "report"} if touching else params
                return (self.tie([("min_space", [la], _number(args[0]), params)], first)
                        + self.tie([("min_space", [lb], _number(args[0]), params)], second)
                        + self.tie([("min_space", [la, lb], _number(args[0]), across)], first + second))
            bases = []
            receiver = self.of_nets(receiver, bases)
            args = tuple(self.of_nets(a, bases) if i < other else a for i, a in enumerate(args[:-1]))
            self.connect()
        if len(args) != other + 2 or args[-1][0] != "lit":
            raise NoForm(f"'{name}' with conditions on the edges it pairs")
        value, metric = _number(args[other]), args[-1][1]
        if metric not in ("euclidian", "square", "projection") or (metric == "projection" and name not in ("enclosing", "separation")):
            raise NoForm(f"'{name}' in the {metric} metric")
        params = {} if metric == "euclidian" else {"metric": metric}
        if name in _TWO:
            if name == "enclosing":       # KLayout measures what the outer layer holds or crosses, and
                params = {"interacting_only": 1, "metric": metric}      # says nothing of a shape apart from it
            if name == "separation" and touching:
                params = {**params, "abutting": "report"}
            if name == "separation" and not touching and self.sentence(receiver)[1] == self.sentence(args[0])[1] == "edges":
                receiver = ("call", receiver, "not", (args[0],), None)    # walls lying on each other are no space
            if name == "separation" and metric == "projection":     # walls facing each other: across, and upwards
                params = {k: v for k, v in params.items() if k != "metric"}
                return self.pair("min_space", receiver, args[0], value, params, narrowed, stem)
            if nets:
                return self.tie(self.pair("min_space", receiver, args[0], value, {**params, "net": "different"},
                                          narrowed, stem), bases)
            return self.pair(_TWO[name], receiver, args[0], value, params, narrowed, stem)
        without = [(w, x) for w, x in narrowed if w.startswith("not_")]
        must = [(w, x) for w, x in narrowed if not w.startswith("not_")]
        a = self.wrap(receiver, without)
        chosen = self.wrap(a, must)
        if name == "space" and len(must) == 1 and must[0][0] == "interacting" and self.part(must[0][1], receiver):
            chosen = must[0][1]           # the markers at a PART of the layer: that part's own spaces, and to the rest
        layers = [self.layer(chosen, stem)]
        if nets:                          # a notch is of one shape, which is one net
            params = {**params, "net": "different"}
            rest = [self.layer(("call", a, "not", (chosen,), None), stem)] if must else []
            return self.tie([("min_space", layers, value, params)]
                            + [("min_space", layers + rest, value, params) for _ in rest], bases * 2)
        if name != "space":
            return [(_ONE[name], layers, value, params)]
        out = [("min_space", layers, value, params), ("min_notch", layers, value, {})]    # between two shapes, and
        if must:                                                                          # across a notch of one
            rest = ("call", a, "not", (chosen,), None)
            out += [("min_space", layers + [self.layer(rest, stem)], value, params)]
        return out

    # ---- nets ----
    def of_nets(self, node, bases):
        """``layer.nets``, and parts of it that keep their nets, as the plain layer; the
        connected layer is added to `bases`."""
        if node[0] == "call" and node[1] is not None:
            _, receiver, name, args, state = node
            if name == "nets" and not args:
                bases.append(receiver)
                self.state = state
                return receiver
            if name in ("and", "not") and args and len(args) == 2 and _lit(args[1], "props_copy"):
                return ("call", self.of_nets(receiver, bases), name, (args[0],), None)
        raise NoForm("a rule on nets in a form not known here")

    def connect(self, antenna=None):
        """Note the connections the rule was written under (`finish` makes the deck's of them).
        A part of a layer connected to that layer joins no nets: it only gives the part its net."""
        pairs, steps, halves = [], [], {}
        for kind, args in self.graph.stacks[self.state]:
            if kind != "connect" or len(args) != 2:
                raise NoForm(f"KLayout's {kind}")
            if any(_lit(a, "extent") for a in args):               # an anchor for the whole layout: no layer
                continue
            steps.append(tuple(args))
            for rest, cut in (args, args[::-1]):
                # ``x.not(y)`` connected to ``x.and(y)``: the two halves of x, which only touch. KLayout joins
                # what touches and gdscheck what overlaps, so the first half is read as x whole -- the same
                # nets, since the halves are joined -- and the second is a part of x
                if (_is(rest, "not", 1) and _is(cut, "and", 1) and rest[1] == cut[1] and rest[3] == cut[3]):
                    halves[rest] = rest[1]
        self.halves = halves
        for args in steps:
            args = tuple(halves.get(a, a) for a in args)
            names = tuple(self.layer(a, "net_" + _slug(self.sentence(a)[0])[:40]) for a in args)
            pairs.append((names, self.cut_of(args[0], args[1]) or self.cut_of(args[1], args[0])))
        self.net_rules.append((self.rule.name, pairs, antenna))
        return pairs

    @staticmethod
    def cut_of(node, layer):
        """Is `node` the layer cut down: ``layer.and(x).not(y)...``?"""
        while node != layer and node[0] == "call" and node[1] is not None and node[2] in ("and", "not"):
            node = node[1]
        return node == layer

    def antenna(self, node):
        """A ratio summed over a net -- ``antenna_check(gate, metal, ratio)``, or the sum
        written out with `evaluate_nets` -- as gdscheck's antenna_ratio."""
        params, diodes = {}, []
        found = self.shared(node)
        if found:
            return found
        found = self.summed(node)
        if found:
            return found
        if node[0] == "call" and node[1] is None and node[2] == "antenna_check" and len(node[3] or ()) == 3:
            (gate, metal, limit), self.state = node[3], node[4]
            limit = _number(limit)
        elif (_is(node, "selected_if", 1) and node[1][0] == "call" and node[1][1] is None and node[1][2] == "evaluate_nets"):
            if len(node[1][3]) != 3 or node[1][3][1][0] != "hash":
                raise NoForm("a sum over a net in a form not known here")
            (gate, named, formula), self.state = node[1][3], node[1][4]
            named = {k[1].strip('"'): v for k, v in named[1]}
            said = re.fullmatch(r"put\('(\w+)', (perimeter|area)\(x\)(?: \* ([\d.]+))? / "
                                r"(?:area|\(area((?: \+ [\d.]+ \* area\(\w+\))+)\))\)", formula[1].strip('"'))
            test = re.fullmatch(r"value\('(\w+)'\) > ([\d.]+)", node[3][0][1].strip('"'))
            if not said or not test or said.group(1) != test.group(1) or "x" not in named \
                    or (said.group(2) == "perimeter") != bool(said.group(3)):
                raise NoForm("a sum over a net in a form not known here")
            metal, limit = named["x"], float(test.group(2))
            if said.group(2) == "perimeter":                       # the side wall: the perimeter by the thickness
                params.update(metric="sidewall", thickness=said.group(3))
            diodes = re.findall(r"([\d.]+) \* area\((\w+)\)", said.group(4) or "")
            if len({f for f, _ in diodes}) > 1 or len(diodes) > 3 or any(d not in named for _, d in diodes):
                raise NoForm("an antenna rule with diodes counted differently")
            if diodes:
                params.update(diode_factor=diodes[0][0], diode_area=0)
        else:
            return None
        stem = _slug(self.rule.name)
        params["layer:gate"] = self.layer(gate, stem + "_gate")
        for i, (_, d) in enumerate(diodes, 1):
            params[f"layer:diode_{i}"] = self.layer(named[d], stem + "_diode")
        conductor = self.layer(metal, stem)
        for (a, b), joins_none in self.connect((params, conductor)):
            if metal in self.halves:                               # a half of a layer: its net is the layer's
                params["layer:antenna_net_of"] = self.layer(self.halves[metal], stem)
            if joins_none and a == params["layer:gate"]:           # a gate is a part of the poly its net is on
                params["layer:gate_net_of"] = b
        return [("antenna_ratio", [conductor], limit, params)]

    def summed(self, node):
        """The ratio summed level by level -- each metal's area over the gate area of the net
        as it is when that metal is made -- for the nets with, or without, a diode of a size:
        gdscheck's antenna_ratio over several layers, with ``diode``."""
        if not (_is(node, "selected_if", 1) and _is(node[1], "merged_props", 0)):
            return None
        terms, todo = [], [node[1][1]]
        while todo:
            n = todo.pop()
            if n[0] == "bin" and n[1] == "+":
                todo += [n[3], n[2]]
            elif n[0] == "call" and n[1] is None and n[2] == "evaluate_nets" and len(n[3]) == 3 and n[3][1][0] == "hash" \
                    and len(n[3][1][1]) == 1 and n[3][1][1][0][0][1] == '"x"':
                terms.append((n[3][0], n[3][1][1][0][1], n[3][2][1].strip('"'), n[4]))
            else:
                raise NoForm("a sum over a net in a form not known here")
        test = re.fullmatch(r"value\('(\w+)'\) (<|>=) ([\d.]+) && ((?:value\('\w+'\)(?: \+ )?)+) > ([\d.]+)",
                            node[3][0][1].strip('"'))
        if not test or len({t[0] for t in terms}) != 1:
            raise NoForm("a sum over a net in a form not known here")
        levels = {k: (layer, state) for _, layer, said, state in terms
                  for k in re.findall(r"^put\('(\w+)', area\(x\) / area\)$", said)}
        diodes = [(layer, state) for _, layer, said, state in terms if said == f"put('{test.group(1)}', area(x))"]
        asked = re.findall(r"value\('(\w+)'\)", test.group(4))
        if len(diodes) != 1 or sorted(asked) != sorted(levels) or len(levels) + 1 != len(terms):
            raise NoForm("a sum over a net in a form not known here")
        stem = _slug(self.rule.name)
        params = {"diode": "without" if test.group(2) == "<" else "with", "diode_area": test.group(3),
                  "layer:gate": self.layer(terms[0][0], stem + "_gate"),
                  "layer:diode_1": self.layer(diodes[0][0], stem + "_diode")}
        self.state = max([diodes[0][1]] + [state for _, state in levels.values()], key=lambda x: len(self.graph.stacks[x]))
        self.connect()
        metals = [self.layer(levels[k][0], stem) for k in sorted(levels, key=lambda k: len(self.graph.stacks[levels[k][1]]))]
        return [("antenna_ratio", metals, float(test.group(5)), params)]

    def shared(self, node):
        """The area of one layer summed over each shape of another it lies on (capacitors
        sharing a plate), with a maximum: gdscheck's max_area, ``scope: contained``."""
        chosen = []
        while _is(node, "overlapping", 1) or _is(node, "interacting", 1):
            chosen.insert(0, (node[2], node[3][0]))
            node = node[1]
        if not (_is(node, "selected_if", 1) and node[1][0] == "call" and node[1][1] is None
                and node[1][2] == "evaluate_nets" and len(node[1][3]) == 3 and node[1][3][1][0] == "hash"):
            return None
        (plate, named, formula), state = node[1][3], node[1][4]
        said = re.fullmatch(r"put\('(\w+)', \(area\((\w+)\)\)\)", formula[1].strip('"'))
        test = re.fullmatch(r"value\('(\w+)'\) > ([\d.]+)", node[3][0][1].strip('"'))
        named = {k[1].strip('"'): v for k, v in named[1]}
        if not said or not test or said.group(1) != test.group(1) or list(named) != [said.group(2)]:
            return None
        held = named[said.group(2)]
        if [tuple(args) for _, args in self.graph.stacks[state] if not any(_lit(a, "extent") for a in args)] != [(held, plate)]:
            raise NoForm("an area summed over a net of several layers")
        stem = _slug(self.rule.name)
        return [("max_area", [self.layer(self.wrap(plate, chosen), stem), self.layer(held, stem)],
                 float(test.group(2)), {"scope": "contained"})]

    def finish(self):
        """The deck's connections, of those of its rules on nets.

        KLayout connects for each rule what that rule says; gdscheck has one ordered list
        for a deck, and an antenna rule reads a beginning of it (`level`). So the rules'
        sets must lie one inside the next -- then the list is written in that order, each
        antenna rule's own conductor last of what it adds. A rule whose set does not fit
        is left out."""
        joins = lambda pairs: {p for p, none in pairs if not none}          # noqa: E731
        order, real, out = [], {}, []
        for name, pairs, antenna in sorted(self.net_rules, key=lambda r: len(joins(r[1]))):
            if not joins(order_pairs := [(p, real[p]) for p in order]) <= joins(pairs):
                out.append((name, "a rule on nets whose connections do not lie within the next rule's"))
                continue
            new = [(p, none) for p, none in pairs if p not in real]
            if antenna:
                new.sort(key=lambda x: antenna[1] in x[0])
            for p, none in new:
                real[p] = none
                order.append(p)
        dropped = {name for name, _ in out}
        for name, pairs, antenna in self.net_rules:
            if name in dropped or not antenna:
                continue
            params, conductor = antenna
            upto = lambda n: {p for p in order[:n] if not real[p]}             # noqa: E731
            first = next((i for i, p in enumerate(order) if conductor in p), None)
            if first is not None and upto(first + 1) == joins(pairs):
                params["layer:level"] = conductor                  # through the first connection of the conductor
                continue
            seen = set()
            for i, p in enumerate(order):
                fresh = [x for x in p if x not in seen]
                if fresh and upto(i) == joins(pairs) and i:
                    params["layer:level_before"] = fresh[0]        # short of the first connection of that layer
                    break
                seen.update(p)
            else:
                seen = set()
                for i, p in enumerate(order):                      # or through the first connection of a layer
                    fresh = [x for x in p if x not in seen]
                    if fresh and upto(i + 1) == joins(pairs) and not real[p]:
                        params["layer:level"] = fresh[0]
                        break
                    seen.update(p)
                else:
                    out.append((name, "an antenna rule whose connections are no beginning of the deck's"))
        dropped = {name for name, _ in out}
        gone = {rid for name in dropped for rid in self.ids.get(name, ())}
        self.entries = [e for e in self.entries if e[0] not in gone]
        self.left_out += out
        self.connections = order

    def tie(self, checks, bases):
        """The layers a net rule reads are parts of connected layers: each is tied to its own."""
        for _, layers, _, _ in checks:
            for name, base in zip(layers, bases):
                whole_ = self.layer(base, "net")
                if name != whole_ and (name, whole_) not in self.tied:
                    self.tied.append((name, whole_))
        return checks

    @staticmethod
    def part(node, layer):
        """Is `node` a part of `layer`: ``layer.and(...)``, or whole shapes chosen of that?"""
        while node[0] == "call" and node[1] is not None and (node[2] in _SELECT or node[2] in _COUNTED):
            node = node[1]
        return node[0] == "call" and node[2] == "and" and layer in (node[1], node[3][0])

    def add(self, rule: Rule):
        """Translate one rule; it is entered whole, or left out with the reason."""
        graph = Deck()
        try:
            text = get("klayout", layermap=self.layermap).compile(rule)
            lines, inside = [], False
            for line in text.splitlines():
                if re.fullmatch(r"\w+ = lambda do \|.*\|", line.strip()):   # a helper written out in Ruby: its
                    inside = True                                          # calls are read, its text is not
                elif inside:
                    inside = line.rstrip() != "end"                        # the helper's own end: not indented
                elif line.strip() and not line.startswith("#"):
                    lines.append(line)
            graph.add(lines)
        except ParseError:
            return self.left_out.append((rule.name, "written with a Ruby helper"))
        kept = (dict(self.drawn), dict(self.virtual), list(self.net_rules), list(self.tied))
        entries, self.rule, self.graph = [], rule, graph
        try:
            for step in graph.steps:
                if step[0] != "out":
                    continue                                       # connections: only a rule on nets needs
                rid, desc = (a[1][1:-1] for a in step[2][:2])      # them, and it has no form here
                entries += [(rid, desc, *c) for c in self.reports(step[1], _slug(rid))]
            if not entries:
                raise NoForm("nothing to report")
        except NoForm as why:
            self.drawn, self.virtual, self.net_rules, self.tied = kept    # nothing of a rule left out stays behind
            return self.left_out.append((rule.name, str(why)))
        self.entries += entries
        self.ids[rule.name] = {e[0] for e in entries}

    # ---- the PDK ----
    def files(self, title) -> dict:
        """``{path in the PDK: text}``."""
        head = ["# Generated by FastDRC -> gdscheck"]
        if self.left_out:
            head += [f"# {len(self.left_out)} rule(s) have no gdscheck form yet and are NOT in this deck:"]
            head += [f"#   {name}: {why}" for name, why in self.left_out]
        pdk = head + ["", f"name: {_text(title or 'FastDRC')}", 'version: "1"', "",
                      "suites:", "  - name: main", "    path: suites/main.yml", "    description: every rule",
                      "decks:", "  - name: rules", "    path: decks/rules.yml",
                      "    description: the rules as drawn", ""]
        drawn, virtual, entries = dict(self.drawn), [(name, text) for text, name in self.virtual.items()], list(self.entries)
        if any(check in DENSITY for _, _, check, _, _, _ in entries):
            # a share of the chip: gdscheck takes the chip as the extent of the layers its rules read,
            taken = set(drawn) | set(self.virtual.values())        # so one entry reads every layer of the process
            drawn.update({name: tuple(number) for name, number in self.layermap.items() if name not in taken
                          and tuple(number) not in drawn.values() and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name)})
            virtual.append((WHOLE, whole(sorted(drawn))))
            entries.append((EXTENT, "not a rule: it reads every layer, so that the chip is the whole layout's extent",
                            "forbidden", [WHOLE], 0, {}))
        if virtual:
            width = max(len(name) for name, _ in virtual) + 1
            pdk += ["virtual_layers:"] + [f"  {name + ':':<{width}} {text}" for name, text in virtual] + [""]
        if self.connections:              # gdscheck: a connector joins the layers it lies on. In order: an
            pdk += ["connectivity:"]      # antenna rule reads a beginning of this list (`finish`)
            pairs = list(dict.fromkeys(p for p in self.connections + self.tied if p[0] != p[1]))
            joined = {b for _, b in pairs}
            for a, b in pairs:            # a layer has a net of its own only as one that is joined: where
                pdk += [f"  - connector: {a}", f"    layers: [{b}]"]       # it is nowhere, the connection is
                if a not in joined:                                        # said the other way round as well
                    pdk += [f"  - connector: {b}", f"    layers: [{a}]"]
            pdk += [""]
        pdk += ["layers:"]
        for name, (layer, datatype) in sorted(drawn.items(), key=lambda x: (x[1], x[0])):
            pdk += [f"  - name: {name}", f"    gds_layer: {layer}", f"    gds_datatype: {datatype}"]
        deck = ["# Generated by FastDRC -> gdscheck", "", "rules:" if entries else "rules: []"]
        for rid, desc, check, layers, value, params in entries:
            said = " ".join(str(desc).split())
            deck += [f"  - id: {_text(rid)}" + (f"   # {said}" if said else ""), f"    check: {check}",
                     f"    layers: [{', '.join(layers)}]", f"    value: {_fmt(value)}"]
            for head_, of in (("params", False), ("layer_params", True)):
                said_ = [f"      {k.removeprefix('layer:')}: {v}" for k, v in params.items() if k.startswith("layer:") == of]
                deck += [f"    {head_}:"] + said_ if said_ else []
        suite = ["# Generated by FastDRC -> gdscheck", "", "include:", "  - deck: rules"]
        return {"pdk.yml": "\n".join(pdk) + "\n", "decks/rules.yml": "\n".join(deck) + "\n",
                "suites/main.yml": "\n".join(suite) + "\n"}


DENSITY = ("min_density", "max_density")
WHOLE, EXTENT = "fastdrc_no_shape", "fastdrc.extent"    # the entry that reads every layer, and the layer it is of


def whole(names) -> str:
    """A layer with no shape that reads every layer named."""
    return f"({' or '.join(names)}) not ({' or '.join(names)})"


def layers_of(path) -> set:
    """``{(layer, datatype)}`` of every shape in a GDS file."""
    out, layer = set(), None
    with open(path, "rb") as f:
        while True:
            head = f.read(4)
            if len(head) < 4:
                break
            size, kind = struct.unpack(">HH", head)
            body = f.read(max(size - 4, 0))
            if kind == 0x0D02:                                     # LAYER
                layer = struct.unpack(">h", body[:2])[0]
            elif kind in (0x0E02, 0x2E02) and layer is not None:   # DATATYPE, BOXTYPE
                out.add((layer, struct.unpack(">h", body[:2])[0]))
    return out


def unfold(text) -> dict:
    """The compiled file's parts: ``{path: text}``."""
    out, path = {}, None
    for line in text.splitlines(keepends=True):
        if line.startswith(FILE):
            path = line[len(FILE):].strip()
            out[path] = ""
        elif path:
            out[path] += line
    return out


def top_cell(path):
    """The cell of a GDS file that no other cell places (the first, if several)."""
    names, placed = [], set()
    with open(path, "rb") as f:
        while True:
            head = f.read(4)
            if len(head) < 4:
                break
            size, kind = struct.unpack(">HH", head)
            body = f.read(max(size - 4, 0))
            if kind in (0x0606, 0x1206):                           # STRNAME, SNAME
                (names.append if kind == 0x0606 else placed.add)(body.rstrip(b"\0").decode("ascii", "replace"))
    return next((n for n in names if n not in placed), None)


class GdsCheck(Backend):
    """The rules as a gdscheck PDK; it also runs gdscheck on a layout."""
    name = "gdscheck"
    extension = ".gdscheck"
    about = "a PDK (pdk.yml, a deck, a suite) for gdscheck; runs it on a GDS layout"
    runs = True

    def __init__(self, layermap=None, title=None):
        self.layermap, self.title = dict(layermap or {}), title
        self.problems = []     # the rules left out, with the reason: `fastdrc build` prints them

    def translate(self, rules) -> Translation:
        t = Translation(self.layermap)
        for r in rules:
            t.add(r if isinstance(r, Rule) else Rule(r))
        t.finish()
        self.problems = [f"{name} is not in the gdscheck deck: {why}" for name, why in t.left_out]
        return t

    def compile(self, rule):
        return self.compile_all([rule])

    def compile_all(self, rules):
        return "".join(f"{FILE}{path}\n{text}" for path, text in self.translate(rules).files(self.title).items())

    def files(self, rules, stem):
        return {f"{stem}{self.extension}.pdk/{path}": text
                for path, text in self.translate(rules).files(self.title).items()}

    # ---- running ----
    @staticmethod
    def binary():
        return os.environ.get("GDSCHECK_BIN") or shutil.which("gdscheck")

    def ready(self):
        return None if self.binary() else ("gdscheck not found -- put `gdscheck` on the PATH or set GDSCHECK_BIN "
                                           "(cargo install gdscheck)")

    def by_hand(self, deck, layout):
        return f"gdscheck run --process {deck}.pdk/pdk.yml --suite main --input {layout} --topcell <cell>"

    def run(self, deck, layout, top=None, report=None):
        if not self.binary():
            raise RunError(self.ready())
        with open(deck, encoding="utf-8") as f:
            parts = unfold(f.read())
        if "pdk.yml" not in parts:
            raise RunError(f"{deck} is not a deck this backend compiled")
        if re.search(rf"^    check: ({'|'.join(DENSITY)})$", parts.get("decks/rules.yml", ""), re.M):
            # a share of the chip is of the WHOLE layout's extent: its other layers are named too
            known = {(int(a), int(b)) for a, b in re.findall(r"gds_layer: (-?\d+)\n    gds_datatype: (-?\d+)", parts["pdk.yml"])}
            try:
                more = sorted(layers_of(layout) - known)
            except OSError as ex:
                raise RunError(f"cannot read {layout}: {ex}") from ex
            head, layers = parts["pdk.yml"].rsplit("\nlayers:\n", 1)
            layers += "".join(f"  - name: layout_{a}_{b}\n    gds_layer: {a}\n    gds_datatype: {b}\n" for a, b in more)
            head = re.sub(rf"^(  {WHOLE}:\s+).*$",
                          lambda m: m.group(1) + whole(re.findall(r"^  - name: (\S+)$", layers, re.M)), head, flags=re.M)
            parts["pdk.yml"] = head + "\nlayers:\n" + layers
        folder = os.path.abspath(deck) + ".pdk"
        for path, text in parts.items():
            os.makedirs(os.path.dirname(os.path.join(folder, path)), exist_ok=True)
            with open(os.path.join(folder, path), "w", encoding="utf-8") as f:
                f.write(text)
        rules = {rid: (desc or "", []) for rid, desc in re.findall(
            r'^  - id: "((?:[^"\\]|\\.)*)"(?:   # (.*))?$', parts.get("decks/rules.yml", ""), re.M)}
        rules.pop(EXTENT, None)                                    # not a rule
        if not rules:                                              # gdscheck has nothing to read
            return {"rules": {}, "report": None}
        try:
            top = top or top_cell(layout)
        except OSError as ex:
            raise RunError(f"cannot read {layout}: {ex}") from ex
        if not top:
            raise RunError(f"no top cell found in {layout} (gdscheck reads GDS; name the cell with --top)")
        report = os.path.abspath(report or os.path.splitext(layout)[0] + ".lyrdb")
        if os.path.exists(report):
            os.remove(report)
        p = subprocess.run([self.binary(), "run", "--process", os.path.join(folder, "pdk.yml"), "--suite", "main",
                            "--input", os.path.abspath(layout), "--topcell", top, "--report", report]
                           + (["--threads", os.environ["GDSCHECK_THREADS"]] if os.environ.get("GDSCHECK_THREADS") else []),
                           capture_output=True, text=True)
        if p.returncode not in (0, 2):                             # 0 clean, 2 violations found
            raise RunError("gdscheck failed:\n" + (p.stderr or p.stdout)[-2000:])
        if os.path.exists(report):
            for rid, boxes in self.read(report).items():
                rules.setdefault(rid, ("", []))[1].extend(boxes)
        return {"rules": rules, "report": report if os.path.exists(report) else None}

    @staticmethod
    def read(path) -> dict:
        """``{rule id: [(x0, y0, x1, y1) | None, ...]}`` of the report database gdscheck
        wrote: the bounding box (µm) of each marker, None for one with no place."""
        out = {}
        text = open(path, encoding="utf-8").read()
        for cat, vals in re.findall(r"<item>.*?<category>([^<]*)</category>.*?<values>(.*?)</values>", text, re.S):
            vals = re.sub(r"<value>\s*text:.*?</value>", "", vals, flags=re.S)       # the message may quote numbers
            pts = [(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+(?:e-?\d+)?),(-?[\d.]+(?:e-?\d+)?)", vals)]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.setdefault(cat.replace("'", "").strip(), []).append(
                (min(xs), min(ys), max(xs), max(ys)) if pts else None)
        return out
