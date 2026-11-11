"""Tests for fastdrc.constraint: parse to the uniform {op, args[]} AST.

The strong check is a differential oracle against Python's own `ast`: since the
constraint DSL is a strict subset of Python expression syntax, parsing a formula
with our grammar and with `ast.parse(..., mode="eval")` must yield structurally
identical trees (after mapping Python's ast into the same {op, args[]} shape).
"""

from __future__ import annotations

import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from fastdrc.constraint import parse  # noqa: E402

_CMP = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=",
        ast.Gt: ">", ast.GtE: ">=", ast.In: "in"}
_BIN = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


def _dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    raise AssertionError(f"not a dotted name: {ast.dump(node)}")


def from_py(node):
    """Map a Python ast expression into our {op, args[]} shape."""
    if isinstance(node, ast.Expression):
        return from_py(node.body)
    if isinstance(node, ast.BoolOp):                      # flat n-ary
        op = "and" if isinstance(node.op, ast.And) else "or"
        return {"op": op, "args": [from_py(v) for v in node.values]}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return {"op": "not", "args": [from_py(node.operand)]}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = from_py(node.operand)                   # we fold -<lit> to a signed num
        assert "num" in operand, "unary minus only on numeric literals"
        return {"num": -operand["num"]}
    if isinstance(node, ast.BinOp):
        return {"op": _BIN[type(node.op)],
                "args": [from_py(node.left), from_py(node.right)]}
    if isinstance(node, ast.Compare):
        assert len(node.ops) == 1, "chained comparison unsupported"
        return {"op": _CMP[type(node.ops[0])],
                "args": [from_py(node.left), from_py(node.comparators[0])]}
    if isinstance(node, ast.Set):
        return {"set": [from_py(e) for e in node.elts]}
    if isinstance(node, (ast.Name, ast.Attribute)):
        return {"ref": _dotted(node)}
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return {"num": node.value}
    raise AssertionError(f"unhandled py ast: {ast.dump(node)}")


FORMULAS = [
    "A.layer == metal5",
    "A.net != B.net",
    "e2.angle - e3.angle == 45",
    "X >= 1.4",
    "X == -45",
    "A.layer in {metal1, metal2, metal3}",
    "A.width > 10 and A.length > 5",
    "X == 1 or Y == 2 or Z == 3",
    "(A.w + B.w) * 2 <= X",
    "a * b + c == d * e",
    "not (A.fast) or X >= 1.4",
    "not (A.x == 5) and B.y != 0",
    "notch_width > 5",          # strict-keyword regression: `not` must not bite
    "indium == 1",              # strict-keyword regression: `in` must not bite
]


def test_matches_python_ast():
    for f in FORMULAS:
        ours = parse(f)
        theirs = from_py(ast.parse(f, mode="eval"))
        assert ours == theirs, f"\n  formula: {f}\n  ours:   {ours}\n  python: {theirs}"


def test_expected_trees():
    # Pinned independently of any parser, so the lowering itself is checked.
    assert parse("A.layer == metal5") == {
        "op": "==", "args": [{"ref": "A.layer"}, {"ref": "metal5"}]}
    assert parse("X == -45") == {"op": "==", "args": [{"ref": "X"}, {"num": -45}]}
    assert parse("0.28 <= W") == {"op": "<=", "args": [{"num": 0.28}, {"ref": "W"}]}
    assert parse("A.holes.area >= 0.35") == {
        "op": ">=", "args": [{"ref": "A.holes.area"}, {"num": 0.35}]}
    assert parse("A.layer in {m1, m2}") == {
        "op": "in", "args": [{"ref": "A.layer"}, {"set": [{"ref": "m1"}, {"ref": "m2"}]}]}
    assert parse("X == 1 or Y == 2 or Z == 3")["op"] == "or"
    assert len(parse("X == 1 or Y == 2 or Z == 3")["args"]) == 3
    assert parse("not (A.x == 5) and B.y != 0") == {"op": "and", "args": [
        {"op": "not", "args": [{"op": "==", "args": [{"ref": "A.x"}, {"num": 5}]}]},
        {"op": "!=", "args": [{"ref": "B.y"}, {"num": 0}]}]}


def test_outside_the_subset_is_rejected():
    import pytest
    from fastdrc.constraint import ConstraintError
    for bad in ["W >", "", "f(X) > 2", "X > 'a'", "1 < X < 3", "X ** 2 > 1",
                "A.layer == {m1}", "A.layer in {1, 2}", "X is Y", "X not in {a}",
                "X[0] > 1", "X == True", "~X", "x = 1", "W >= 2; Y >= 1"]:
        with pytest.raises(ConstraintError):
            parse(bad)


def test_rawast_grammar_agrees():
    # grammars/constraint.rawast defines the same language as a PEG grammar. When
    # rawast is installed, the two parsers must build identical trees.
    import pytest
    rawast = pytest.importorskip("rawast")
    path = os.path.join(os.path.dirname(__file__), "..", "python", "fastdrc",
                        "grammars", "constraint.rawast")
    grammar = rawast.Grammar.from_dict(rawast.Grammar("rawast").parse_file(path))
    for f in FORMULAS:
        assert grammar.parse_string(f) == parse(f), f


def test_uniform_shape():
    # Every non-leaf node is {op, args[list]}; leaves are ref/num/set.
    def check(n):
        assert isinstance(n, dict)
        if {"ref", "num", "set"} & n.keys():
            if "set" in n:
                for e in n["set"]:
                    check(e)
            return
        assert set(n) == {"op", "args"}, n
        assert isinstance(n["args"], list) and n["args"], n
        if n["op"] == "not":
            assert len(n["args"]) == 1, n
        elif n["op"] in ("and", "or"):
            assert len(n["args"]) >= 2, n          # flat n-ary
        else:
            assert len(n["args"]) == 2, n          # comparison / arithmetic
        for a in n["args"]:
            check(a)

    for f in FORMULAS:
        check(parse(f))


if __name__ == "__main__":
    test_matches_python_ast()
    test_uniform_shape()
    print(f"OK — {len(FORMULAS)} formulas match Python ast and are uniform {{op, args[]}}")
