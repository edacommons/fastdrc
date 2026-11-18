"""The deck as a graph, so that it can be optimised the way a compiler optimises code.

A deck is, per rule, one layer expression and an output. Read as text the rules are
independent; read as a GRAPH -- a node per operation, its inputs as edges -- they
overlap: `pdiff.and(nwell)` in two rules is ONE node with two users. `Deck` holds that
graph for the rules being built:

  * a NODE is a value: a layer read from the layout, an operation on nodes, a number.
    Nodes are unique by structure, so the same expression met twice IS the same node
    (common subexpressions are shared by construction);
  * an OUTPUT (`rule name, description, node`) and a CONNECTION are what the script
    DOES, in the order the rules give them;
  * PASSES rewrite the graph (`canonical`, `nested`), and `lines` writes it back as a
    script: every node computed once, just before it is first needed, and released
    after its last use.

The graph is built from the statements the KLayout backend writes for each rule (a
small language: method chains over layers), so its operations are KLayout's. Nets are
the one thing that is not a pure function of its inputs: `connect` / `clear_connections`
change what `.nets`, `antenna_check` and `evaluate_nets` mean. Such a node carries the
number of the SET of connections it was written under: it is never mistaken for the same
text under other connections, and it IS the same node as that text under the same
connections declared again for another rule. `lines` then declares each set once (a
larger one on top of the smaller) and reads everything that needs it there -- the nets
are extracted once per set instead of once per rule.
"""

from __future__ import annotations

import re

# --- the language -------------------------------------------------------------------------

_TOKEN = re.compile(r"""
    (?P<num>\d+(?:\.\d+)?(?:e-?\d+)?(?:\.(?:um|dbu|mm|micron|degree)\b)?)
  | (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<id>\$?[A-Za-z_]\w*\??)
  | (?P<op>\.\.|=>|>=|<=|==|!=|&&|\|\||[-+*/<>&|^(){},.\[\]=?:])
  | (?P<ws>\s+)
""", re.X)

# binary operators, loosest first (Ruby's order, as far as a deck uses it)
_LEVELS = [("||",), ("&&",), ("..",), ("==", "!="), ("<", ">", "<=", ">="), ("|", "^"), ("&",), ("+", "-"), ("*", "/")]
# reading these depends on the connections declared so far
STATEFUL = {"nets", "antenna_check", "evaluate_nets"}
# a number or a box comes out, not a layer: nothing to name, nothing to release
SCALAR = {"area", "perimeter", "bbox", "count", "hier_count", "length", "size", "width", "height",
          "left", "right", "top", "bottom", "to_s", "to_f", "to_i"}
# the result is the same whichever of the two layers is asked
COMMUTATIVE = {"and", "or", "xor"}
# polygons in, polygons out -- whatever the argument is
_POLYGON_KEEPS = {"sized", "inside", "not_inside", "outside", "not_outside", "interacting", "not_interacting",
                  "overlapping", "not_overlapping", "covering", "not_covering", "squares", "non_squares",
                  "rectangles", "non_rectangles", "rectilinear", "non_rectilinear", "holes", "hulls", "merged",
                  "with_area", "without_area", "with_holes", "without_holes", "dup", "extents"}
_LEVEL_OF = {op: n for n, ops in enumerate(_LEVELS) for op in ops}


class ParseError(ValueError):
    pass


def _tokens(text):
    out, i = [], 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m:
            raise ParseError(f"cannot read {text[i:i + 20]!r} in {text!r}")
        i = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group()))
    return out


class Deck:
    """The graph of one deck. Nodes are tuples, unique by structure:

        ("lit", text)                       a number, a keyword, a string
        ("in", function, args)              a layer of the layout: polygons(1, 0), input("m1")
        ("call", receiver | None, name, args, state | None)
        ("bin", op, left, right)   ("neg", x)   ("hash", ((key, value), ...))   ("list", items)
        ("if", condition, this, that)
    """

    def __init__(self):
        self.names = {}        # variable -> node (what the rules call it)
        self.given = {}        # node -> the name it was first given
        self.top = []          # variables of the preamble (layers and legend), in order
        self.steps = []        # ("out", node, name, desc) | ("do", name, args) | ("at", node), in order
        self.connected = []    # the connections declared at this point, in the order written
        self.states = {}       # set of connections -> its number (equal sets are ONE state)
        self.stacks = []       # number -> those connections, in the order first written
        self._epoch = self._read_at = 0
        self.stats = {}

    # ---- building ----
    def add(self, lines, top=False):
        """Read statements into the graph. `top`: these are the deck's layers and legend."""
        for line in lines:
            self._statement(line, top)

    def _statement(self, line, top):
        toks = _tokens(line)
        self._t, self._i = toks, 0
        if len(toks) >= 3 and toks[0][0] == "id" and toks[1] == ("op", "="):
            name = toks[0][1]
            self._i = 2
            node = self._expr()
            self._end(line)
            if name in self.names:
                raise ParseError(f"{name} is assigned twice")
            self.names[name] = node
            self.given.setdefault(node, name)
            if top:
                self.top.append(name)
            elif self.stateful(node):
                self.steps.append(("at", node))        # computed where the rule put it
            return
        if len(toks) == 3 and toks[1] == ("op", ".") and toks[2][1] == "forget":
            return                                     # lifetimes are worked out again from the graph
        if (len(toks) > 3 and toks[0][0] == "id" and toks[1] == ("op", ".") and toks[2][1] == "output"
                and toks[0][1] in self.names):
            self._i = 3
            args = self._args()
            self._end(line)
            self.steps.append(("out", self.names[toks[0][1]], args))
            return
        if toks and toks[0][0] == "id" and toks[0][1] in ("connect", "connect_global", "connect_implicit",
                                                           "clear_connections"):
            self._i = 1
            args = self._args() if self._peek() == "(" else ()
            self._end(line)
            self._epoch += 1
            if toks[0][1] == "clear_connections":
                self.connected = []
            elif (toks[0][1], args) not in self.connected:
                self.connected = self.connected + [(toks[0][1], args)]
            self.steps.append(("do", toks[0][1], args))
            return
        raise ParseError(f"not a statement this graph knows: {line!r}")

    @property
    def state(self):
        """The number of the connections declared here. What counts is WHICH are declared,
        not when: the same stack declared again for another rule is the same state, so
        what two rules read from it is one node."""
        key = frozenset(self.connected)
        if key not in self.states:
            self.states[key] = len(self.stacks)
            self.stacks.append(list(self.connected))
        if self._read_at != self._epoch or "nets_written" not in self.stats:
            self.stats["nets_written"] = self.stats.get("nets_written", 0) + 1
            self._read_at = self._epoch
        return self.states[key]

    def _peek(self):
        return self._t[self._i][1] if self._i < len(self._t) else None

    def _end(self, line):
        if self._i != len(self._t):
            raise ParseError(f"cannot read {self._peek()!r} in {line!r}")

    def _take(self, text):
        if self._peek() != text:
            raise ParseError(f"expected {text!r}, found {self._peek()!r}")
        self._i += 1

    def _args(self):
        self._take("(")
        out = []
        while self._peek() != ")":
            out.append(self._expr())
            if self._peek() == ",":
                self._i += 1
        self._take(")")
        return tuple(out)

    def _expr(self, level=0):
        if level == 0:                                 # condition ? this : that
            cond = self._binary(0)
            if self._peek() == "?" and self._t[self._i][0] == "op":
                self._i += 1
                yes = self._expr()
                self._take(":")
                return ("if", cond, yes, self._expr())
            return cond
        return self._binary(level)

    def _binary(self, level):
        if level == len(_LEVELS):
            return self._unary()
        left = self._binary(level + 1)
        while self._peek() in _LEVELS[level] and self._t[self._i][0] == "op":
            op = self._peek()
            self._i += 1
            left = ("bin", op, left, self._binary(level + 1))
        return left

    def _unary(self):
        if self._peek() == "-":
            self._i += 1
            x = self._unary()
            return ("lit", "-" + x[1]) if x[0] == "lit" and x[1][:1].isdigit() else ("neg", x)
        return self._postfix()

    def _postfix(self):
        kind, text = self._t[self._i] if self._i < len(self._t) else (None, None)
        self._i += 1
        if kind in ("num", "str"):
            node = ("lit", text)
        elif kind == "id":
            if self._peek() == "(":
                args = self._args()
                node = (("in", text, args) if text in ("polygons", "input", "labels", "edges", "extent")
                        and all(a[0] == "lit" for a in args)
                        else ("call", None, text, args, self.state if text in STATEFUL else None))
            elif text in self.names:
                node = self.names[text]
            else:
                node = ("lit", text)                   # a keyword: euclidian, projection, nil ...
        elif text == "(":
            node = self._expr()
            self._take(")")
        elif text == "{":
            pairs = []
            while self._peek() != "}":
                key = self._expr()
                self._take("=>")
                pairs.append((key, self._expr()))
                if self._peek() == ",":
                    self._i += 1
            self._take("}")
            node = ("hash", tuple(pairs))
        elif text == "[":
            items = []
            while self._peek() != "]":
                items.append(self._expr())
                if self._peek() == ",":
                    self._i += 1
            self._take("]")
            node = ("list", tuple(items))
        else:
            raise ParseError(f"cannot read {text!r}")
        while self._peek() == "." and self._i + 1 < len(self._t) and self._t[self._i + 1][0] == "id":
            name = self._t[self._i + 1][1]
            self._i += 2
            args = self._args() if self._peek() == "(" else None
            node = ("call", node, name, args, self.state if name in STATEFUL else None)
        return node

    # ---- reading the graph ----
    @staticmethod
    def children(node):
        kind = node[0]
        if kind == "call":
            return ((node[1],) if node[1] is not None else ()) + tuple(node[3] or ())
        if kind == "bin":
            return (node[2], node[3])
        if kind == "neg":
            return (node[1],)
        if kind == "if":
            return node[1:]
        if kind == "hash":
            return tuple(x for pair in node[1] for x in pair)
        if kind == "list":
            return node[1]
        return ()

    def _below(self, node):
        seen, todo = set(), list(self.children(node))
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo += self.children(n)
        return seen

    def stateful(self, node, _seen=None):
        """Does the value depend on the connections declared when it is computed?"""
        memo = self.__dict__.setdefault("_stateful", {})
        if node not in memo:
            memo[node] = ((node[0] == "call" and node[4] is not None)
                          or any(self.stateful(c) for c in self.children(node)))
        return memo[node]

    def is_layer(self, node):
        """Is the value a layer (something worth computing once, and releasable)?"""
        memo = self.__dict__.setdefault("_layer", {})
        if node not in memo:
            kind = node[0]
            if kind == "in":
                ans = True
            elif kind == "call":
                name = node[2]
                if node[1] is None:
                    ans = name in ("antenna_check", "evaluate_nets")
                else:
                    ans = self.is_layer(node[1]) and name not in SCALAR and not name.endswith("?")
            elif kind == "bin":
                ans = node[1] in "+-&|^" and self.is_layer(node[2]) and self.is_layer(node[3])
            else:
                ans = False
            memo[node] = ans
        return memo[node]

    def is_polygons(self, node):
        """Is the value certainly a layer of POLYGONS (not edges, not pairs)?  Only for
        those is the order of an `and` / `not` chain free."""
        memo = self.__dict__.setdefault("_polygons", {})
        if node not in memo:
            if node[0] == "in":
                ans = node[1] == "polygons"
            elif node[0] == "call" and node[1] is not None and node[4] is None and self.is_polygons(node[1]):
                if node[2] in COMMUTATIVE or node[2] == "not":
                    ans = node[3] is not None and len(node[3]) == 1 and self.is_polygons(node[3][0])
                else:
                    ans = node[2] in _POLYGON_KEEPS
            else:
                ans = False
            memo[node] = ans
        return memo[node]

    def label(self, node):
        """The node as the deck's reader knows it: its name if it has one."""
        return self.given.get(node) or self.text(node, self.given)

    def roots(self):
        """The nodes the script needs, in the order it needs them."""
        out = []
        for step in self.steps:
            out += [step[1]] if step[0] in ("out", "at") else list(step[2])
            if step[0] == "out":
                out += list(step[2])
        return out

    def rewrite(self, change):
        """Replace every node by `change(node)` (children first); names follow."""
        memo = {}

        def go(node):
            if node in memo:
                return memo[node]
            kind = node[0]
            if kind == "call":
                new = ("call", go(node[1]) if node[1] is not None else None, node[2],
                       tuple(go(a) for a in node[3]) if node[3] is not None else None, node[4])
            elif kind == "bin":
                new = ("bin", node[1], go(node[2]), go(node[3]))
            elif kind == "neg":
                new = ("neg", go(node[1]))
            elif kind == "if":
                new = ("if", go(node[1]), go(node[2]), go(node[3]))
            elif kind == "hash":
                new = ("hash", tuple((go(k), go(v)) for k, v in node[1]))
            elif kind == "list":
                new = ("list", tuple(go(x) for x in node[1]))
            else:
                new = node
            memo[node] = change(new)
            return memo[node]

        self.names = {name: go(node) for name, node in self.names.items()}
        given = {}
        for node, name in self.given.items():
            given.setdefault(go(node), name)
        self.given = given
        self.steps = [(s[0], go(s[1]), tuple(go(a) for a in s[2])) if s[0] == "out"
                      else (s[0], go(s[1])) if s[0] == "at"
                      else (s[0], s[1], tuple(go(a) for a in s[2])) for s in self.steps]
        for memo in ("_stateful", "_layer", "_polygons"):
            self.__dict__.pop(memo, None)

    # ---- passes ----
    def _chain(self, node, op):
        """``a.op(b).op(c)`` -> ``(a, [b, c])`` for plain layer operands."""
        operands = []
        while (node[0] == "call" and node[2] == op and node[1] is not None and node[3] is not None
               and len(node[3]) == 1 and node[4] is None and self.is_polygons(node[1])
               and self.is_polygons(node[3][0]) and node not in self.given):
            operands.append(node[3][0])
            node = node[1]
        return node, operands[::-1]

    def canonical(self):
        """One spelling for one meaning: ``b.and(a)`` is ``a.and(b)``, and the layers cut
        out of a shape may be cut in any order -- so equal things become one node. The
        spelling kept is the one the deck uses first (the rule's subject stays in front)."""
        first = {}

        def change(node):
            if node[0] != "call" or node[1] is None or node[4] is not None or self.stateful(node):
                return node
            for op in (*COMMUTATIVE, "not"):
                if node[2] != op:
                    continue
                base, ops = self._chain(node, op)
                if not ops or (op == "not" and len(ops) < 2):
                    return node
                key = (op, None, frozenset([base] + ops)) if op != "not" else (op, base, frozenset(ops))
                return first.setdefault(key, node)
            return node
        before = self.count()
        self.rewrite(change)
        self.stats["canonical"] = before - self.count()

    def nested(self):
        """Build a longer chain on a shorter one that is needed anyway: with
        ``comp.not(otp)`` computed, ``comp.not(mvsd).not(otp)`` is ``comp.not(otp).not(mvsd)``
        -- one operation on a smaller layer instead of two on the whole one. (The order
        of such a chain does not change its result; which order shares most depends on
        the rules being built.)"""
        saved = 0
        for op in ("not", "and"):
            # a chain counts where it is USED as a whole -- not where it is only the
            # first part of a longer one
            part = set()
            needed = self.nodes()
            for node in needed:
                if (node[0] == "call" and node[2] == op and node[1] is not None and node[4] is None
                        and node[1][0] == "call" and node[1][2] == op and node[1] not in self.given):
                    part.add(node[1])
            other = set(self.roots())
            for node in needed:
                same = node[0] == "call" and node[2] == op and node[4] is None
                for i, c in enumerate(self.children(node)):
                    if not (same and i == 0 and node[1] is not None):
                        other.add(c)
            chains = {}                                # base -> {frozenset(operands): node}
            for node in needed:
                if node in part and node not in other:
                    continue
                base, ops = self._chain(node, op)
                if not ops:
                    continue
                if op == "and":                        # the base is an operand like the others
                    base, ops = None, [base] + ops
                chains.setdefault(base, {})[frozenset(ops)] = node
            plan = {}
            for base, sets in chains.items():
                often = {}                             # the operand most chains need goes first
                for group in sets:
                    for x in group:
                        often[x] = often.get(x, 0) + 1
                key = lambda x: (-often[x], self.label(x))                 # noqa: E731
                built = {}                             # operand set -> the node that computes it
                for group in sorted(sets, key=lambda g: (len(g), sorted(map(self.label, g)))):
                    inner = max((g for g in built if g < group), key=len, default=None)
                    if inner is None:                  # nothing shorter to build on: as written
                        built[group] = sets[group]
                        continue
                    node = built[inner]
                    for x in sorted(group - inner, key=key):
                        node = ("call", node, op, (x,), None)
                    built[group] = node
                    if node != sets[group]:
                        plan[sets[group]] = node
            if plan:
                before = self.count()
                self.rewrite(lambda n: plan.get(n, n))
                saved += before - self.count()
        self.stats["nested"] = saved

    def written(self):
        """How many operations the rules run when each is written out in full."""
        size = {}

        def go(node):
            if node not in size:
                size[node] = (node[0] in ("call", "bin")) + sum(go(c) for c in self.children(node))
            return size[node]
        tops = {self.names[n] for n in self.top}
        seen, total = set(), 0

        def walk(node):                                # a legend layer is computed once as it is
            nonlocal total
            if node in tops:
                if node not in seen:
                    seen.add(node)
                    total += node[0] in ("call", "bin")
                    for c in self.children(node):
                        walk(c)
                return
            total += node[0] in ("call", "bin")
            for c in self.children(node):
                walk(c)
        for r in self.roots():
            walk(r)
        return total

    def optimise(self):
        """Run the passes. `stats`: operations as written -> as one graph -> after the passes."""
        self.stats["written"] = self.written()
        self.stats["graph"] = self.count()
        self.canonical()
        self.nested()
        self.stats["after"] = self.count()
        return self

    def nodes(self):
        """Every node the script needs, each once, inputs before their users."""
        seen, out = set(), []

        def go(node):
            if node in seen:
                return
            seen.add(node)
            for c in self.children(node):
                go(c)
            out.append(node)
        for r in self.roots():
            go(r)
        return out

    def count(self):
        """How many operations the script runs."""
        return sum(1 for n in self.nodes() if n[0] in ("call", "bin"))

    # ---- writing ----
    def text(self, node, named=None, top=True):
        """The node as script text; a node in `named` is written by its name."""
        if named and not top and node in named:
            return named[node]
        kind = node[0]
        sub = lambda n: self.text(n, named, False)             # noqa: E731
        if kind == "lit":
            return node[1]
        if kind == "in":
            return f"{node[1]}({', '.join(a[1] for a in node[2])})"
        if kind == "call":
            args = "" if node[3] is None else "(" + ", ".join(sub(a) for a in node[3]) + ")"
            if node[1] is None:
                return node[2] + args
            recv = sub(node[1])
            if node[1][0] in ("bin", "neg", "if") and not (named and node[1] in named):
                recv = f"({recv})"
            return f"{recv}.{node[2]}{args}"
        if kind == "bin":
            def side(n, right):
                t = sub(n)
                written = n[0] in ("bin", "if") and not (named and n in named)
                loose = written and (n[0] == "if" or _LEVEL_OF[n[1]] < _LEVEL_OF[node[1]]
                                     or (right and _LEVEL_OF[n[1]] == _LEVEL_OF[node[1]]))
                return f"({t})" if loose else t
            return f"{side(node[2], False)} {node[1]} {side(node[3], True)}"
        if kind == "neg":
            return "-" + sub(node[1])
        if kind == "if":
            return f"({sub(node[1])}) ? {sub(node[2])} : {sub(node[3])}"
        if kind == "hash":
            return "{ " + ", ".join(f"{sub(k)} => {sub(v)}" for k, v in node[1]) + " }"
        return "[" + ", ".join(sub(x) for x in node[1]) + "]"

    def _fresh(self, node, taken, named):
        text = re.sub(r"(?<![\w.])-(?=\d)", "m", self.text(node, named))
        text = re.sub(r"\.(um|dbu)\b", "", text)
        text = re.sub(r"(?<=\d)\.(?=\d)", "p", text)
        name = re.sub(r"_+", "_", re.sub(r"\W", "_", text)).strip("_")
        if len(name) > 48 or not name or name[0].isdigit():
            name = "shared"
        base, n = name, 1
        while name in taken:
            n += 1
            name = f"{base}_{n}"
        taken.add(name)
        return name

    def lines(self):
        """The script: ``(preamble lines, rule lines, {name: text of what is shared})``.
        Every named node is computed once, just before it is first needed; a layer is
        released after its last use (never one a connection or a net operation holds)."""
        needed = self.nodes()
        alive = set(needed)
        users = {}
        for node in needed:
            for c in self.children(node):
                users[c] = users.get(c, 0) + 1
        for r in self.roots():
            users[r] = users.get(r, 0) + 1
        named, taken, shared = {}, set(self.names), {}
        for name in self.top:                                  # the deck's layers keep their names
            if self.names[name] in alive:
                named.setdefault(self.names[name], name)
        top_nodes = set(named)
        for node in needed:                                    # what a rule named, and what several need
            if node in named or node[0] not in ("call", "bin", "if"):
                continue
            if node in self.given and (self.is_layer(node) or node in {s[1] for s in self.steps if s[0] != "do"}):
                named[node] = self.given[node]
            elif node[0] == "call" and node[4] is not None:        # read off the nets: computed with its state
                named[node] = self.given.get(node) or self._fresh(node, taken, named)
            elif (users.get(node, 0) > 1 and self.is_layer(node) and not self.stateful(node)
                  and not (node[0] == "call" and node[2] == "dup")):     # a copy is made to be one's own
                named[node] = self._fresh(node, taken, named)
                shared[named[node]] = self.text(node, named)
        held = set()                                           # layers a connection or a net operation holds
        for node in needed:
            if node[0] == "call" and (node[4] is not None or node[2] in STATEFUL):
                held.update(c for c in self.children(node))
        for step in self.steps:
            if step[0] == "do":
                held.update(step[2])

        pre, body, done = [], [], set()
        into = pre

        def ensure(node):
            if node in done:
                return
            for c in self.children(node):
                ensure(c)
            done.add(node)
            if node in named:
                into.append(f"{named[node]} = {self.text(node, named)}")

        for name in self.top:                                  # with what they are built from
            node = self.names[name]
            if node in alive and named.get(node) == name:
                ensure(node)
        early = {n for n in done if n in named} - top_nodes     # shared, and a legend layer needs it
        into = body

        def nets():
            """Everything read off the nets, state by state: each stack of connections is
            declared ONCE (a stack that contains the one before it is built on it), and
            the nets are extracted once per stack instead of once per rule."""
            reads = {}
            for node in needed:
                if node[0] == "call" and node[4] is not None:
                    if any(c[0] == "call" and c[4] not in (None, node[4]) for c in self._below(node)):
                        raise ParseError("a net operation on the nets of another set of connections")
                    reads.setdefault(node[4], []).append(node)
            todo, now, seen, kept = sorted(reads), [], set(), set()
            # will the connections be started over somewhere? (the same walk as below, dry)
            dry, cur, restarts = sorted(reads), [], False
            while dry:
                grows = [s for s in dry if set(cur) <= set(self.stacks[s])]
                nxt = min(grows or dry, key=lambda s: (len(self.stacks[s]), s))
                dry.remove(nxt)
                if not grows and cur:
                    restarts, cur = True, []
                cur = cur + [x for x in self.stacks[nxt] if x not in cur]
            while todo:
                grows = [s for s in todo if set(now) <= set(self.stacks[s])]
                nxt = min(grows or todo, key=lambda s: (len(self.stacks[s]), s))
                todo.remove(nxt)
                if not grows and now:
                    body.append("clear_connections")
                    now = []
                for name, args in self.stacks[nxt]:
                    if (name, args) not in now:
                        for a in args:
                            ensure(a)
                        body.append(f"{name}({', '.join(self.text(a, named, False) for a in args)})")
                        now.append((name, args))
                for node in reads[nxt]:
                    ensure(node)
                # ... and what is made FROM those readings: the nets they stand on are gone as
                # soon as a later stack starts the connections over
                seen.add(nxt)
                for node in needed:
                    if node[0] == "call" and node[4] is None and node not in done:
                        on = {c[4] for c in self._below(node) if c[0] == "call" and c[4] is not None}
                        if on and on <= seen and nxt in on:
                            ensure(node)
                if restarts:                               # what a rule reports is then kept as plain shapes
                    for step in self.steps:
                        node = step[1] if step[0] == "out" else None
                        if node is None or node not in named or node in kept:
                            continue
                        on = {c[4] for c in [node, *self._below(node)] if c[0] == "call" and c[4] is not None}
                        if on and on <= seen and nxt in on:
                            ensure(node)
                            body.append(f"{named[node]} = off_the_nets.call({named[node]})")
                            kept.add(node)
            self.stats["nets"] = len(reads)

        nets_done = False
        for step in self.steps:
            if step[0] != "out" and not nets_done:
                nets()
                nets_done = True
            if step[0] == "at":
                ensure(step[1])
            elif step[0] == "do":
                pass                                           # declared in `nets`, once per stack
            elif step[0] == "out":
                ensure(step[1])
                for a in step[2]:
                    ensure(a)
                body.append(f"{self.text(step[1], named, False)}.output({', '.join(self.text(a, named, False) for a in step[2])})")
        # release: after the last line that mentions the name
        outputs = {s[1] for s in self.steps if s[0] == "out"}
        for node, name in named.items():
            if node in top_nodes or node in held or not (self.is_layer(node) or node in outputs):
                continue
            word = re.compile(r"(?<![\w.$])" + re.escape(name) + r"(?!\w)")
            last = max((i for i, ln in enumerate(body) if word.search(ln)), default=None)
            if last is not None:
                body.insert(last + 1, f"{name}.forget")
            elif node in early:                                # needed for the legend only: done with
                pre.append(f"{name}.forget")
        self.stats["shared"] = len(shared)
        return pre, body, shared
