# Plugins: your own backend, your own deck

FastDRC takes two kinds of plugin, and each is an ordinary Python package that names
what it offers in an entry-point group:

| | what it adds | entry-point group |
|---|---|---|
| a **backend** | another thing made from the drawn rules: a deck for another checking tool, a report, a table | `fastdrc.backends` |
| a **deck** | the rules of a process, found by name | `fastdrc.decks` |

Nothing in FastDRC itself is changed for either. `fastdrc backends` and `fastdrc decks`
list what is installed.

## The backends that come with FastDRC

| name | what it writes | used by |
|---|---|---|
| `klayout` | a KLayout DRC script; it also runs it on a layout | `build`, `check`, `selftest` (the default) |
| `docs` | every rule as a sentence | `build` (always written beside the deck), the editor's text pane |
| `svg` | every rule's drawing as a picture | `build --with svg`; the figures of these documents |
| `cases` | test layouts made from the drawings, with what each rule must report | `selftest` |
| `facts` | what was read from a drawing: shapes, layers, relations, bounds | `rules FILE --to facts`, to see why a drawing is read as it is |

    fastdrc rules rules/2-devices/PO.2.json --to facts

    rule PO.2
    shape DT: layer=diff net=None
    shape GT: layer=poly net=None
    topology: DT overlap GT
    bound: Xp >= 0.2 (min)

## A backend

A backend is a class that turns one analysed rule into text. It is given a
`fastdrc.rule.Rule` — the drawing already read: which shape lies inside, across or
beside which, what each arrow measures, what the constraints bound — and returns what
its target needs. It never parses the drawing itself.

The smallest useful one: every distance a deck checks, as a table.

```python
from fastdrc.backend import Backend
from fastdrc.rule import Rule


class Table(Backend):
    """Every distance a deck checks, as one line of a table."""
    name = "table"                 # the name it is asked for by
    extension = ".csv"             # of the file `fastdrc build` writes
    about = "the distances a deck checks, as a table"      # for `fastdrc backends`

    def compile(self, rule):
        layers = "+".join(sorted({str(rule.layer_of(s)) for s in rule.shapes}))
        return "".join(f"{rule.name},{layers},{b.var},{b.op},{b.value}\n" for b in rule.bounds())

    def compile_all(self, rules):  # the whole deck: the heading once
        return "rule,layers,measure,op,value\n" + "".join(
            self.compile(r if isinstance(r, Rule) else Rule(r)) for r in rules)
```

Its package names the class:

```toml
[project]
name = "fastdrc-table"
dependencies = ["fastdrc"]

[project.entry-points."fastdrc.backends"]
table = "fastdrc_table:Table"
```

Once installed it works wherever a backend is named:

    fastdrc backends                          table  the distances a deck checks, as a table
    fastdrc build mydeck --with table         ->  build/mydeck.csv beside the deck
    fastdrc rules mydeck --to table           the same, printed

    rule,layers,measure,op,value
    NW.1,nwell,Wn,>=,0.8
    CO.4,cont+diff,Ek,>=,0.1
    PO.2,diff+poly,Xp,>=,0.2

What a backend may define:

| | |
|---|---|
| `name`, `extension`, `about` | as above |
| `compile(rule)` | one rule → text. The only method that must be written |
| `compile_all(rules)` | several rules → one text; override it for a heading or a preamble written once. The rules arrive as `Rule`s or as rule files' contents, so wrap them as the example does |
| `files(rules, stem)` | `{file name: text}`, for a backend that writes more than one file (`svg` writes a picture per rule); a name may lie in a folder (`mydeck.mytool/rules.yml`), which is made |
| `headed(text, about)` | the text with lines about the set of rules put at its head, in the target's comment form |
| `__init__(self, layermap=None, title=None, ...)` | optional. A backend is offered the deck's layer numbers and its name; it receives those it names as parameters and no others |

A backend may use another: `get("svg").compile(rule)` gives a rule's picture,
`get("docs").compile(rule)` its sentence (`from fastdrc.backend import get`).

### What a rule tells a backend

The `Rule` is the whole interface. The `facts` backend (in `fastdrc/backend.py`, thirty
lines) reads the most general part of it and is the place to start; `backend_docs.py`
shows every kind of rule being put into words, `backend_klayout.py` into operations.

| | |
|---|---|
| `rule.name`, `rule.desc` | the rule's name and the manual's sentence |
| `rule.shapes`, `rule.measures` | the drawn shapes and arrows, by name |
| `rule.layer_of(shape)`, `rule.net_of(shape)` | what a shape stands for |
| `rule.relations()` | `{(a, b): "inside" / "overlap" / "touching" / "apart" ...}`, read off the drawing |
| `rule.bounds()` | the numeric limits on the arrows: `.var`, `.op`, `.value`, `.is_min` |
| `rule.constraints` | every constraint, parsed: `("match" or "check", expression)` |
| `rule.shape_forms()`, `rule.length_facts()` | squares and bars; limits on the length of a side |
| `rule.count_bounds()`, `rule.density_bounds()`, `rule.antenna_bounds()`, `rule.centre_bounds()`, `rule.sum_bounds()` … | one reader per kind of rule in [the reference](drawing-model.md) |

A rule a backend has no form for should be said so — raise, or write a comment naming
the rule — and never be left out silently.

### A backend that also runs its deck

`build`, `check` and `selftest` take `--backend NAME`: the deck is compiled by that
backend and run by it, and nothing else in the tool knows how. This is how another
checking tool is put under FastDRC. Such a backend sets `runs = True` and adds:

```python
class MyTool(Backend):
    name, extension, about, runs = "mytool", ".rules", "a deck for MyTool; runs it", True

    def compile(self, rule): ...

    def ready(self):
        """None when the tool can be run here; otherwise what is missing, as a sentence."""
        return None if shutil.which("mytool") else "mytool is not on the PATH"

    def run(self, deck, layout, top=None, report=None):
        """Run the file this backend compiled on a GDS / OASIS layout."""
        ...                                    # start the tool, read its result
        return {"rules": {"NW.1": ("nwell width", [(0.0, 0.0, 1.2, 0.4)]),   # a box in µm per violation
                          "NW.2": ("nwell space", [])},                       # every rule of the deck, clean ones too
                "report": None}                                             # or the path of the tool's own report

    def by_hand(self, deck, layout):           # optional: shown when the tool is not `ready`
        return f"mytool -rules {deck} {layout}"
```

    fastdrc check chip.gds mydeck --backend mytool
    fastdrc selftest mydeck --backend mytool        do the rules behave as drawn — in this tool?

`selftest` is then the proof of the new backend: the test layouts and what each rule
must report on them come from the drawings, not from any backend, so a backend that
reads a rule wrongly is shown rule by rule. (The layout of the test cases is itself
written by a KLayout script, so KLayout is needed for `selftest` whichever backend
reads it.)

A backend need not write every rule. One it leaves out of its file it names in its
`problems` (a list of sentences, printed by `build`), and does not name in what `run`
returns; `selftest` then counts that rule as not in the deck — not tested, which is
not the same as wrong. A violation with no place (a share of the whole chip) is given
as `None` in place of a box.

Raise `fastdrc.backend.RunError` when the tool cannot be run or fails.

A backend of this kind in full is [`fastdrc-gdscheck/`](../fastdrc-gdscheck): a package
of its own (`pip install fastdrc-gdscheck`) that writes the rules as a PDK for
[gdscheck](https://github.com/aesc-silicon/gdscheck), runs it, and leaves out, by name,
the rules gdscheck has no form for.

## A deck

A deck is a folder — `process.json`, `rules/<section>/<rule>.json`, optionally
`layouts/` — and is used as it is (`fastdrc build path/to/deck`) or zipped. To let
others install it and name it, put the folder in a package as a module and name that
module:

```
fastdrc-myprocess/
  pyproject.toml
  fastdrc_myprocess/
    __init__.py            (may be empty)
    process.json
    rules/
    layouts/
```

```toml
[project]
name = "fastdrc-myprocess"
dependencies = ["fastdrc"]

[project.entry-points."fastdrc.decks"]
myprocess = "fastdrc_myprocess"          # a module; the deck is the folder it lies in

[tool.hatch.build.targets.wheel]
packages = ["fastdrc_myprocess"]
```

    pip install fastdrc-myprocess
    fastdrc decks                             myprocess   .../site-packages/fastdrc_myprocess
    fastdrc check chip.gds myprocess

Every command takes the name where it takes a folder. What is generated goes to
`myprocess.build/` in the current folder; nothing is written into the installed
package. A folder or file of that name in the current folder comes first.

[`fastdrc-gf180mcu`](../fastdrc-gf180mcu) in this repository is such a package.
