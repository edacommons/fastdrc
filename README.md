# FastDRC

**Design rules drawn, not programmed.** You describe a DRC rule the way a design manual
does — draw an example of the geometry, put an arrow where a distance matters, write
what must hold — and FastDRC compiles the rules into a working
[KLayout](https://www.klayout.de) DRC script, with the same rules written out as plain
sentences to read back. *Fast*: the script is fitted to the set of rules being run —
the whole deck, or part of it — so what several rules need is computed once.

    pip install .                # from a checkout (not on PyPI yet); pure Python, no compiler needed

> Status: pre-alpha. This release is the **rule compiler** with its **KLayout
> backend** (compared rule by rule with the public decks of several open processes),
> the self-test and the editor. Backends are plugins. A **C++ checking core** is in
> development in this repository as a second backend; it is not part of the Python
> package and nothing here needs it. Today it is a geometry layer only (merge, layer
> algebra, grow / shrink on a compiled `.tiles` format; `Layout::check` is a stub) —
> see [Build the C++ core](#build-the-c-core).

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

**Backends are plugins.** `fastdrc backends` lists them (`klayout`, `docs`, `facts`,
`cases`); another package adds one through the entry-point group `fastdrc.backends`.
A backend may also *run* the deck it writes on a layout: `build`, `check` and
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
python/fastdrc/backend_cases.py   -> test layouts made from the drawings, with what to expect
python/fastdrc/editor.py          the visual editor (Tkinter)
tools/compare_reports.py          two DRC runs of one layout, compared rule by rule, by place
tools/cells_in_a_row.rb           a cell library laid out flat, to try a deck on
tools/perturb_layout.rb           random small errors put into a layout
```

The other `tools/` scripts validate the compiler against a reference deck and a
drawn-rule corpus read from `docs/gf180mcu/`, which is not part of this repository.

## Layout

```
include/fastdrc/   public C++ headers
src/               C++ core implementation  -> fastdrc::core
bindings/          nanobind module          -> fastdrc._fastdrc (optional)
python/fastdrc/    Python package: the rule compiler, its backends, the editor
tools/             rule-compiler validation against a reference deck
docs/              docs (see docs/drawing-model.md)
tests/             C++ (CTest) + Python (pytest) tests
examples/          usage examples
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

Requires Python 3.11+.

```bash
pip install -e . pytest          # editable install, pure Python
pytest                           # the tests of the C++ core are skipped until it is built
```

With [uv](https://docs.astral.sh/uv/): `uv sync` then `uv run pytest`.

## Build the C++ core

The core is in development and optional. It is a C++ library (`fastdrc::core`) with a
[nanobind](https://nanobind.readthedocs.io) Python module, built with CMake:

```bash
cmake -S . -B build              # the library + the C++ tests
cmake --build build
ctest --test-dir build --output-on-failure
```

To use it from Python, build the module too; it is written into `python/fastdrc/`,
where an editable install finds it (`fastdrc.has_core()` then says `True`):

```bash
pip install nanobind 'rawast>=0.1.1'     # rawast: the GDS reader, for `fastdrc compose`
cmake -S . -B build -DFASTDRC_BUILD_PYTHON=ON
cmake --build build
python examples/basic.py
```

```python
import fastdrc

d = fastdrc.Design(dbu_per_micron=1000)
m1 = d.add_layer(gds_layer=10, gds_datatype=0, name="metal1")
m1.add_rect(0, 0, 10, 10)
m1.add_rect(5, 5, 15, 15)                  # overlaps -> merged into one region
via = d.add_layer(gds_layer=11, gds_datatype=0, name="via1")
via.add_rect(2, 2, 8, 8)

fastdrc.write_split(d, "out")              # out/metal1.tiles, out/via1.tiles, out/index.json
fastdrc.boolean("out/metal1.tiles", "out/via1.tiles", "sub", "out/m1_not_via.tiles")
fastdrc.inflate("out/via1.tiles", 2, "out/via_halo.tiles")   # d < 0 shrinks
```

The same from the command line: `fastdrc compose config.toml` (GDS -> `.tiles`),
`fastdrc boolean`, `fastdrc inflate`, and `fastdrc run deck.toml` (a DAG of such
steps). Without the core these four say so; every other command works.

To consume the library from another CMake project, link `fastdrc::core` and include the
headers you need from `<fastdrc/...>` (e.g. `design.hpp`, `sweep.hpp`, `layer_ops.hpp`).
