"""Parse FastDRC constraint formulas into a uniform AST.

A constraint is a strict subset of Python expression syntax, so it is parsed with
Python's own ``ast`` module (no third-party parser) and mapped to a tree in which
**every operation is a node** ``{"op": str, "args": [node, ...]}`` -- ``or``/``and``
are flat n-ary, comparisons and ``+ - * /`` are binary, ``not`` is unary -- and the
leaves are::

    {"ref": "A.layer"}      # measure var / entity attribute / layer literal
    {"num": -45}            # numeric literal (signed)
    {"set": [node, ...]}    # set literal, only as RHS of `in`

The one function is ``Count(K)`` -> ``{"op": "count", "args": [{"ref": "K"}]}``: how
many of K's layer a shape holds (which shape: the one K is drawn in).

Anything outside the subset (other calls, strings, subscripts, chained comparisons,
``**``, ...) is rejected with `ConstraintError`. The compiler walks one node
shape: read ``op``, recurse ``args``.

``grammars/constraint.rawast`` is the same language as a rawast PEG grammar; it is
kept as the written definition of the subset and as a differential check in the
tests (when rawast is installed), but nothing here depends on it.
"""

from __future__ import annotations

import ast

_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
        ast.Gt: ">", ast.GtE: ">=", ast.In: "in"}
_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


class ConstraintError(ValueError):
    """The text is not a valid constraint formula."""


def _ref(node):
    """A dotted name (``X`` / ``A.layer`` / ``A.holes.area``) as a string, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _ref(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _num(node):
    """A numeric literal's value (bool is not a number here), or None."""
    if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)):
        return node.value
    return None


def _lower(node, text):
    def bad(what):
        return ConstraintError(f"{what} is not allowed in a constraint: {text!r}")

    if isinstance(node, ast.BoolOp):                       # flat n-ary and / or
        op = "and" if isinstance(node.op, ast.And) else "or"
        return {"op": op, "args": [_lower(v, text) for v in node.values]}
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return {"op": "not", "args": [_lower(node.operand, text)]}
        if isinstance(node.op, ast.USub) and _num(node.operand) is not None:
            return {"num": -_num(node.operand)}            # -45 is a signed literal
        raise bad("this unary operator")
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _BIN:
            raise bad("this arithmetic operator")
        return {"op": _BIN[type(node.op)],
                "args": [_lower(node.left, text), _lower(node.right, text)]}
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1:
            raise bad("a chained comparison")
        if type(node.ops[0]) not in _CMP:
            raise bad("this comparison operator")
        op, rhs = _CMP[type(node.ops[0])], node.comparators[0]
        if isinstance(rhs, ast.Set):                       # a set only after `in`
            if op != "in":
                raise bad("a set outside `in`")
            elems = [_ref(e) for e in rhs.elts]
            if any(e is None for e in elems):
                raise bad("a set element that is not a name")
            right = {"set": [{"ref": e} for e in elems]}
        else:
            right = _lower(rhs, text)
        return {"op": op, "args": [_lower(node.left, text), right]}
    if isinstance(node, ast.Call):                         # the one function: Count(shape)
        name = node.func.id.lower() if isinstance(node.func, ast.Name) else None
        arg = _ref(node.args[0]) if len(node.args) == 1 and not node.keywords else None
        if name != "count" or arg is None:
            raise bad("this function call")
        return {"op": "count", "args": [{"ref": arg}]}
    if _num(node) is not None:
        return {"num": _num(node)}
    if _ref(node) is not None:
        return {"ref": _ref(node)}
    raise bad(type(node).__name__)


def parse(text: str) -> dict:
    """Parse a constraint formula into the uniform ``{op, args[]}`` AST."""
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError as ex:
        raise ConstraintError(f"not a valid constraint: {text!r} ({ex.msg})") from None
    return _lower(tree.body, text)
