"""The parameters of a process, and the SWITCH that reads them.

A process has options that are chosen once for a whole chip -- the metal stack, the
thickness of the top metal, which capacitor is offered. ``process.json`` names them::

    "parameters": {
      "top_metal":   {"choices": ["6K", "9K", "30K"], "default": "9K"},      chosen
      "metal_stack": {"choices": ["5LM", "6LM"], "default": "6LM"},
      "top_via":     {"switch": "metal_stack", "cases": {"5LM": "via4", "6LM": "via5"}}   derived
    },
    "profiles": {"5LM-30K": {"metal_stack": "5LM", "top_metal": "30K"}}

A parameter is defined ONCE: it is either chosen (it lists its choices) or derived (a
switch on another parameter). Nothing sets or clears a parameter anywhere else, so what
a parameter is can be read in one place. A profile is a named set of choices.

Wherever a value stands in the deck -- a rule's value, a legend entry, whether a rule
is there at all -- a switch may stand instead::

    {"switch": "top_metal", "cases": {"6K": 0.36, "9K": 0.44}, "default": 0.44}

It is the case of the parameter's value, else the default, else nothing (``null``).
A case may itself be a switch. Everything is settled when the deck is loaded: a built
deck is the deck of one choice of parameters.
"""

from __future__ import annotations


class ParameterError(ValueError):
    """Something about a deck's parameters that cannot be right; the message says what."""


def is_switch(x) -> bool:
    return isinstance(x, dict) and "switch" in x and "cases" in x


def _key(value) -> str:
    """How a parameter's value is spelled as a case name (JSON keys are text)."""
    if value is True or value is False:
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def chosen(proc) -> list:
    """The names of the parameters a user chooses, in the order the process lists them."""
    return [n for n, d in (proc.get("parameters") or {}).items() if isinstance(d, dict) and "choices" in d]


def resolve(proc, profile=None, given=None) -> dict:
    """``{parameter: value}`` for every parameter of the process: the given choices
    (`given`, ``{name: value}`` or ``["name=value", ...]``) over those of the named
    `profile` over the defaults; then what is derived from them."""
    defs = proc.get("parameters") or {}
    profiles = proc.get("profiles") or {}
    if isinstance(given, (list, tuple)):
        pairs = {}
        for item in given:
            for part in str(item).split(","):
                if not part.strip():
                    continue
                if "=" not in part:
                    raise ParameterError(f"a parameter is set as name=value, not {part.strip()!r}")
                k, v = part.split("=", 1)
                pairs[k.strip()] = v.strip()
        given = pairs
    picked = {}
    if profile:
        if profile not in profiles:
            known = ", ".join(profiles) or "none"
            raise ParameterError(f"no profile is called {profile!r} (the deck has: {known})")
        picked.update(profiles[profile])
    picked.update(given or {})
    for name in picked:
        if name not in defs:
            raise ParameterError(f"no parameter is called {name!r} (the deck has: {', '.join(defs) or 'none'})")
        if "choices" not in (defs[name] if isinstance(defs[name], dict) else {}):
            raise ParameterError(f"{name} is not chosen: it is derived from "
                                 f"{defs[name].get('switch') if isinstance(defs[name], dict) else 'nothing'}")
    values, busy = {}, []

    def value_of(name):
        if name in values:
            return values[name]
        if name not in defs:
            raise ParameterError(f"a switch reads {name!r}, which is not a parameter of the deck")
        if name in busy:
            raise ParameterError("parameters defined by each other: " + " -> ".join(busy[busy.index(name):] + [name]))
        d = defs[name]
        if isinstance(d, dict) and "choices" in d:
            choices = list(d["choices"])
            if not choices:
                raise ParameterError(f"{name} has no choices")
            v = picked.get(name, d.get("default", choices[0]))
            match = next((c for c in choices if _key(c) == _key(v)), None)
            if match is None:
                raise ParameterError(f"{name} cannot be {v!r} (its choices: {', '.join(_key(c) for c in choices)})")
            values[name] = match
        elif is_switch(d):
            busy.append(name)
            values[name] = settle(d, value_of)
            busy.pop()
        else:
            raise ParameterError(f"{name} must list its choices or be a switch on another parameter")
        return values[name]

    for name in defs:
        value_of(name)
    return values


def settle(obj, values):
    """`obj` with every switch replaced by the case it selects. `values` is the dict of
    `resolve` (or a function giving a parameter's value)."""
    get = values if callable(values) else values.__getitem__
    if is_switch(obj):
        try:
            v = get(obj["switch"])
        except KeyError:
            raise ParameterError(f"a switch reads {obj['switch']!r}, which is not a parameter of the deck") from None
        cases = obj["cases"]
        if not isinstance(cases, dict):
            raise ParameterError(f"the cases of a switch on {obj['switch']} must be {{value: what it gives}}")
        picked = cases[_key(v)] if _key(v) in cases else obj.get("default")
        return settle(picked, values)
    if isinstance(obj, dict):
        return {k: settle(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [settle(v, values) for v in obj]
    return obj


def has_switch(obj) -> bool:
    if is_switch(obj):
        return True
    if isinstance(obj, dict):
        return any(has_switch(v) for v in obj.values())
    return isinstance(obj, list) and any(has_switch(v) for v in obj)


def said(values, proc) -> str:
    """The chosen parameters as one line: ``top_metal = 9K, metal_stack = 6LM``."""
    return ", ".join(f"{n} = {_key(values[n])}" for n in chosen(proc) if n in values)
