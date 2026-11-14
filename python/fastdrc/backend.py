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

A backend is a PLUGIN. The ones shipped here (``klayout``, ``docs``, ``facts``,
``cases``) load on first use; any installed package can add one by declaring an entry
point in the group ``fastdrc.backends``::

    [project.entry-points."fastdrc.backends"]
    mytarget = "mypackage.mymodule:MyBackend"

where ``MyBackend`` subclasses `Backend`, sets ``name`` and implements ``compile``. It
then works everywhere a backend is named: ``fastdrc rules ... --to mytarget``,
``fastdrc build DIR --with mytarget``, the editor's text pane.

A backend may also RUN what it compiles on a layout (``runs = True``, with `ready` and
`run`). ``fastdrc build | check | selftest --backend NAME`` then compile the deck with
it and run it with it; KLayout is the one shipped here that does, and nothing else in
the tool names it.
"""

from __future__ import annotations

from .rule import Rule


class RunError(RuntimeError):
    """A backend could not run a deck (not installed, or it failed)."""


class Backend:
    name = "abstract"
    extension = ".txt"        # of the file `fastdrc build` writes for this backend
    about = ""                # one line for `fastdrc backends`
    runs = False              # can it also RUN the deck it compiles on a layout?

    # ---- running a deck: what `fastdrc check` and `fastdrc selftest` ask of a backend that `runs` ----
    def ready(self):
        """None if the backend can run its deck here; otherwise what is missing, as a sentence."""
        return f"the {self.name} backend writes a file; it does not run one"

    def by_hand(self, deck, layout) -> str:
        """How to run the deck oneself (shown when the backend is not `ready`); may be ''."""
        return ""

    def run(self, deck, layout, top=None, report=None) -> dict:
        """Run the compiled `deck` (a file this backend wrote) on `layout`, a GDS / OASIS
        file; `top` names the cell to check (default: the layout's), `report` where to
        leave the backend's own report, if it writes one. Returns::

            {"rules": {rule name: (description, [(x0, y0, x1, y1) | None, ...])},   # µm, one box per violation (None: no place);
             "report": path or None}                                         # every rule of the deck, clean ones with []

        Raises `RunError` if it cannot run or fails."""
        raise RunError(self.ready() or f"the {self.name} backend has no run step")

    def files(self, rules, stem) -> dict:
        """What this backend writes for a set of rules, as ``{file name: text}`` --
        one file by default; a backend producing several (a layout generator and what
        to expect of it) overrides this."""
        return {stem + self.extension: self.compile_all(rules)}

    def compile(self, rule: Rule) -> str:
        raise NotImplementedError

    def headed(self, text, about) -> str:
        """The compiled deck with lines `about` it (which rules of the process it holds)
        put at its head, in this target's comment form. Default: left as it is."""
        return text

    def compile_all(self, rules) -> str:
        """Compile several rules (rule dicts or `Rule`s) into one output. A backend
        with a shared preamble (a deck) overrides this to emit it once."""
        return "\n".join(self.compile(r if isinstance(r, Rule) else Rule(r)) for r in rules)

    def compile_data(self, data: dict) -> str:
        """Compile what a rule FILE holds: one rule, or a sheet of many (one drawn
        scene, see `fastdrc.sheet`)."""
        from .sheet import is_sheet, rules_of
        if not is_sheet(data):
            return self.compile(Rule(data))
        return self.compile_all(rules_of(data))


_REGISTRY: dict[str, type] = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


_BUILTIN = ("backend_klayout", "backend_docs", "backend_cases")
_loaded = False


def _discover():
    """Load the backends shipped with fastdrc and those other packages offer through
    the ``fastdrc.backends`` entry-point group. A plugin that fails to load is skipped
    (and named in `problems`), never fatal."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    import importlib
    for mod in _BUILTIN:
        importlib.import_module(f"{__package__}.{mod}")
    try:
        from importlib.metadata import entry_points
        found = entry_points(group="fastdrc.backends")
    except Exception:                                   # noqa: BLE001
        found = []
    for ep in found:
        try:
            cls = ep.load()
            if not getattr(cls, "name", None) or cls.name == "abstract":
                cls.name = ep.name
            _REGISTRY.setdefault(cls.name, cls)
        except Exception as ex:                         # noqa: BLE001
            problems.append(f"backend plugin {ep.name!r} could not be loaded: {ex}")


problems: list[str] = []


def get(name, **kw) -> Backend:
    """A backend by name. Keyword arguments it does not take are left out, so a caller
    can offer what it has (a layer map, a title) to any backend."""
    _discover()
    if name not in _REGISTRY:
        raise KeyError(f"no backend {name!r}; available: {sorted(_REGISTRY)}")
    cls = _REGISTRY[name]
    import inspect
    params = inspect.signature(cls.__init__).parameters
    if not any(p.kind is p.VAR_KEYWORD for p in params.values()):
        kw = {k: v for k, v in kw.items() if k in params}
    return cls(**kw)


def available() -> list[str]:
    _discover()
    return sorted(_REGISTRY)


def runner(name, **kw) -> Backend:
    """A backend that runs what it compiles, by name."""
    b = get(name, **kw)
    if not b.runs:
        raise KeyError(f"the {name!r} backend does not run decks; these do: "
                       + ", ".join(n for n in available() if _REGISTRY[n].runs))
    return b


@register
class FactsBackend(Backend):
    """Reference backend: report the facts the `Rule` API exposes. Demonstrates
    a backend built purely from our topology + constraint functions, and acts
    as a swappability check against the real codegen backends."""

    name = "facts"
    extension = ".facts.txt"
    about = "the facts read from each drawing (for debugging a drawing)"

    def compile(self, rule: Rule) -> str:
        lines = [f"rule {rule.name}"] if rule.name else []
        forms = rule.shape_forms()
        for name in rule.shapes:
            lines.append(f"shape {name}: layer={rule.layer_of(name)} "
                         f"net={rule.net_of(name)}"
                         + (f" class={forms[name]}" if name in forms else ""))
        roles = rule.side_roles()
        for edge, kind, form, lo, hi in rule.length_facts():
            side = f"{roles[edge]} side" if edge in roles else "edge"
            lines.append(f"length: {kind} {side} {edge} {form} {lo}..{hi}")
        for shape, form in rule.form_checks():
            lines.append(f"class: check {shape} must be {form}")
        for (a, b), rel in rule.relations().items():
            lines.append(f"topology: {a} {rel} {b}")
        for bnd in rule.bounds():
            kind = "min" if bnd.is_min else "max"
            lines.append(f"bound: {bnd.var} {bnd.op} {bnd.value} ({kind})")
        return "\n".join(lines) + "\n"
