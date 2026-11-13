# FastDRC

A fast **design-rule-check (DRC)** toolkit for IC/PCB layout, in two parts:

1. a **C++ checking core** (delivered as a C++ library and as a Python module via
   [nanobind](https://nanobind.readthedocs.io)); and
2. a **rule compiler** that lets you author DRC rules by **drawing an example** of
   the geometry — *geometry-by-example* — instead of writing layer algebra by hand.

> Status: pre-alpha. The C++ core today is the **geometry layer**: it merges a
> layer's polygons (any straight angle) into a disjoint tiling, stores it in the
> compiled `.tiles` format, and does layer algebra on it (`or` / `and` / `sub` /
> `xor`, grow / shrink). The checks themselves are not in the core yet
> (`Layout::check` is a stub). The rule compiler has a working **KLayout `.drc`
> backend** (the bootstrap target, compared rule by rule with the public decks of
> several open processes) and is structured so the C++ engine becomes a second,
> swappable backend.

## Authoring rules by drawing

You describe a rule the way a design manual does: **draw the shapes** of the layers in
the situation the rule is about, put an **arrow** where a distance matters, and write
the one or two things the manual says about it (`W >= 0.8`, `A.net != B.net`,
`Count(V) >= 2`). You do *not* write `.and` / `.not` / `.inside`: the compiler reads
what lies inside, across or beside what off the drawing and derives the operations.

```
fastdrc init myprocess             # a process is a folder: process.json + rules/
fastdrc gui myprocess              # draw the sheets (one per section of the manual)
fastdrc build myprocess            # -> build/<name>.drc (KLayout deck), build/<name>.md (the rules as sentences)
fastdrc selftest myprocess         # does every constraint of every rule behave as drawn?
fastdrc check chip.gds myprocess   # run the deck on a layout, list the violations per rule
```

`build` and `rules` need nothing but Python; `selftest`, `check` and the editor's
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

The pieces (Python):

```
python/fastdrc/topology.py        spatial relations off geometry (inside / overlap / ...)
python/fastdrc/constraint.py      the constraint language (a Python-expression subset, parsed with `ast`)
python/fastdrc/rule.py            the analyzed-rule API the backends work against
python/fastdrc/sheet.py           a sheet: one drawn scene, many rules; table columns; layer families
python/fastdrc/process.py         a process folder: build, check, self-test
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
bindings/          nanobind module          -> fastdrc._fastdrc
python/fastdrc/    Python package: the checking wrapper + the rule compiler
tools/             rule-compiler validation against a reference deck
docs/              docs (see docs/drawing-model.md)
tests/             C++ (CTest) + Python (pytest) tests
examples/          usage examples
```

## Build the C++ library + run C++ tests

```bash
cmake -S . -B build              # configure (tests on by default)
cmake --build build              # build fastdrc_core + the test executables
ctest --test-dir build --output-on-failure
```

To consume the library from another CMake project, link `fastdrc::core` and
include the headers you need from `<fastdrc/...>` (e.g. `design.hpp`,
`sweep.hpp`, `layer_ops.hpp`).

## Build the Python module

Requires Python 3.11+ and network access on first build (the build fetches
`nanobind` and `scikit-build-core` in an isolated environment).

```bash
pip install -e '.[gds]' pytest   # editable install; [gds] adds the GDS reader (rawast)
pytest                           # run the Python tests
python examples/basic.py
```

With [uv](https://docs.astral.sh/uv/): `uv sync` then `uv run pytest`.

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
steps).

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
