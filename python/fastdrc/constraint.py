"""Parse FastDRC constraint formulas into a uniform AST.

A constraint is a strict subset of Python expression syntax (see
``grammars/constraint.rawast``). ``parse(text)`` returns an AST in which **every
operation is a node** ``{"op": str, "args": [node, ...]}`` -- ``or``/``and`` are
flat n-ary, comparisons and ``+ - * /`` are binary, ``not`` is unary -- and the
leaves are::

    {"ref": "A.layer"}      # measure var / entity attribute / layer literal
    {"num": -45}            # numeric literal (signed)
    {"set": [node, ...]}    # set literal, only as RHS of `in`

The grammar emits this shape directly (via PEG ordered-choice fallback at each
precedence tier), so there is no separate lowering pass -- ``parse`` is just a
grammar parse. The compiler walks one node shape: read ``op``, recurse ``args``.
"""

from __future__ import annotations

import os

import rawast

_GRAMMAR_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "grammars", "constraint.rawast"
)
_grammar = None


def _load() -> "rawast.Grammar":
    global _grammar
    if _grammar is None:
        meta = rawast.Grammar("rawast")
        _grammar = rawast.Grammar.from_dict(meta.parse_file(_GRAMMAR_PATH))
    return _grammar


def parse(text: str) -> dict:
    """Parse a constraint formula into the uniform ``{op, args[]}`` AST."""
    return _load().parse_string(text)
