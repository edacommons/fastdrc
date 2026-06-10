"""Pluggable DRC backends.

A backend turns an analyzed `Rule` into a target artifact. There is no data IR:
a backend's ``compile(rule)`` works directly against our functions -- topology
relations, parsed constraints, measures -- via the `Rule` API, and emits its
target (KLayout `.drc` now, a native-engine driver later). The rule->target
interpretation lives in each backend; the shared substrate is `topology` +
`constraint` + `Rule`.

Backends register by name and are selected at compile time. Because every
backend consumes the same `Rule`, two can compile the same rule and their
results compared as a cross-check.
"""

from __future__ import annotations

from .rule import Rule


class Backend:
    name = "abstract"

    def compile(self, rule: Rule) -> str:
        raise NotImplementedError


_REGISTRY: dict[str, type] = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def get(name, **kw) -> Backend:
    if name not in _REGISTRY:
        raise KeyError(f"no backend {name!r}; registered: {sorted(_REGISTRY)}")
    return _REGISTRY[name](**kw)


def available() -> list[str]:
    return sorted(_REGISTRY)


@register
class FactsBackend(Backend):
    """Reference backend: report the facts the `Rule` API exposes. Demonstrates
    a backend built purely from our topology + constraint functions, and acts
    as a swappability check against the real codegen backends."""

    name = "facts"

    def compile(self, rule: Rule) -> str:
        lines = []
        for name in rule.shapes:
            lines.append(f"shape {name}: layer={rule.layer_of(name)} "
                         f"net={rule.net_of(name)}")
        for (a, b), rel in rule.relations().items():
            lines.append(f"topology: {a} {rel} {b}")
        for bnd in rule.bounds():
            kind = "min" if bnd.is_min else "max"
            lines.append(f"bound: {bnd.var} {bnd.op} {bnd.value} ({kind})")
        return "\n".join(lines) + "\n"
