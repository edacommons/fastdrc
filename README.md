# FastDRC

**Design rules drawn, not programmed.** You describe a DRC rule the way a design manual
does — draw an example of the geometry, put an arrow where a distance matters, write
what must hold — and FastDRC compiles the rules into a working
[KLayout](https://www.klayout.de) DRC script, with the same rules written out as plain
sentences to read back. *Fast*: the script is fitted to the set of rules being run —
the whole deck, or part of it — so what several rules need is computed once.

    pip install .                # from a checkout (not on PyPI yet); pure Python, no compiler needed

> Status: pre-alpha. This is the **rule compiler** with its **KLayout backend**
> (compared rule by rule with the public decks of several open processes), the
> self-test and the editor. Backends are plugins. A C++ checking core is being
> developed separately, on the `cpp-core` branch, to become a second backend; it is
> not part of `main` and nothing here needs it.

## Authoring rules by drawing

You describe a rule the way a design manual does: **draw the shapes** of the layers in
the situation the rule is about, put an **arrow** where a distance matters, and write
the one or two things the manual says about it (`W >= 0.8`, `A.net != B.net`,
`Count(V) >= 2`). You do *not* write `.and` / `.not` / `.inside`: the compiler reads
what lies inside, across or beside what off the drawing and derives the operations.

```
fastdrc init mydeck             # a deck is a folder: process.json + rules/<section>/<rule>.json
fastdrc gui mydeck              # draw the rules: one rule, one file, one drawing
fastdrc build mydeck            # -> build/<name>.drc (KLayout script), build/<name>.md (the rules as sentences)
fastdrc selftest mydeck         # does every constraint of every rule behave as drawn?
fastdrc check chip.gds mydeck   # run the deck on a layout, list the violations per rule
fastdrc check chip.gds mydeck --only "3-metal,NW.*"   # part of the deck: one script fitted to that set
fastdrc pack mydeck             # -> mydeck.zip: the same deck in one file; every command and the editor take it
```

`build`, `rules` and the editor need nothing but Python; `selftest`, `check` and the editor's
**Test** button run [KLayout](https://www.klayout.de) (`klayout` on the PATH, or
`KLAYOUT_BIN`).

> **Start with [docs/from-a-manual.md](docs/from-a-manual.md)** — one invented process
> taken from the manual's page to a deck that has been run, with every kind of rule
> ([`examples/demo/`](examples/demo)): `fastdrc build examples/demo`.
>
> **📐 [docs/drawing-model.md](docs/drawing-model.md)** is the reference: how each kind
> of rule is drawn and what the compiler derives from it — widths, spacings,
> enclosures and extensions; a value per table column; exact sizes, squares and bars;
> arrays and counts; "on one of two sides"; maximum distances; density; nets and
> antenna ratios; rules for a family of layers; and the cross ("must not exist").

**The self-test.** For each constraint of each rule, `selftest` builds small layouts
from the rule's own picture — a `check` with its quantity just outside the value (must
be reported) and exactly at it (must not), a `match` with a violation just outside the
filter (must *not* be reported) — and runs the deck on them. What the rule must do is
computed from the constraints, not from the deck. Rules it cannot build a case for are
listed, never counted as passing.

**Backends are plugins.** `fastdrc backends` lists them (`klayout`, `docs`, `svg`,
`facts`, `cases`); another package adds one through the entry-point group `fastdrc.backends`.
A backend may use another (`get("svg")` for a rule's figure, `get("docs")` for its
sentence). A backend may also *run* the deck it writes on a layout: `build`, `check` and
`selftest` take `--backend NAME` and go through that one for both. KLayout is the one
shipped here that does, and the default; the C++ core will be another.

The pieces (Python):

```
python/fastdrc/topology.py        spatial relations off geometry (inside / overlap / ...)
python/fastdrc/constraint.py      the constraint language (a Python-expression subset, parsed with `ast`)
python/fastdrc/rule.py            the analyzed-rule API the backends work against
python/fastdrc/sheet.py           rule files: a value per table column, layer families (and older multi-rule sheets)
python/fastdrc/process.py         a deck folder: build, check, self-test
python/fastdrc/backend.py         the backend registry and plugin discovery
python/fastdrc/backend_klayout.py -> a KLayout DRC deck
python/fastdrc/backend_docs.py    -> the rules as plain sentences
python/fastdrc/backend_svg.py     -> each rule's drawing as a picture
python/fastdrc/backend_cases.py   -> test layouts made from the drawings, with what to expect
python/fastdrc/editor.py          the visual editor (Tkinter)
tools/compare_reports.py          two DRC runs of one layout, compared rule by rule, by place
tools/cells_in_a_row.rb           a cell library laid out flat, to try a deck on
tools/perturb_layout.rb           random small errors put into a layout
tools/tidy_drawings.py            a deck's drawings redrawn neatly, their meaning unchanged
```

The other `tools/` scripts validate the compiler against a reference deck and a
drawn-rule corpus read from `docs/gf180mcu/`, which is not part of this repository.

## Layout

```
python/fastdrc/    the Python package: the rule compiler, its backends, the editor
examples/demo/     a complete deck for an invented process, with every kind of rule
docs/              the tutorial (from-a-manual.md) and the reference (drawing-model.md)
tools/             validation of the compiler against a reference deck
tests/             pytest
```

## Compiling a drawn rule

```python
from fastdrc.backend import get                # backends load on first use
from fastdrc.rule import Rule

# a drawn rule = shapes (edges) + measures + constraints; see docs/drawing-model.md
deck = get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(my_drawn_rule))
text = get("docs").compile(Rule(my_drawn_rule))     # the same rule as a sentence

from fastdrc import process                    # a whole process folder
deck_path, text_path, n_rules, warnings = process.build("examples/demo")
```

## Install for development

Requires Python 3.11+; pure Python, no compiler.

```bash
pip install -e . pytest
pytest
```

With [uv](https://docs.astral.sh/uv/): `uv sync` then `uv run pytest`. Tests that run a
deck need [KLayout](https://www.klayout.de) and are skipped without it.
