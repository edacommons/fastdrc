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
> backend** (the bootstrap target, validated rule-by-rule against a production
> foundry deck) and is structured so the C++ engine becomes a second, swappable
> backend.

## Authoring rules by drawing

You describe a rule by **drawing real shapes of real layers** in a spatial
relationship and adding a few constraints — *not* by writing `.and` / `.not` /
`.inside` / `.outside`. The compiler reads the topology off the drawing and
*derives* the operations needed to implement the check.

> **Start with [docs/from-a-manual.md](docs/from-a-manual.md)** — one invented process
> taken from the manual's page to a deck that has been run, with every kind of rule
> ([`examples/demo/`](examples/demo)): `fastdrc build examples/demo`.
>
> **📐 See [docs/drawing-model.md](docs/drawing-model.md)** — the reference for how
> each rule type is drawn: wavy vs straight edges, measures (a real distance vs a
> topological assertion), `match` vs `check` constraints, and a recipe per rule
> family (width / space / separation / enclosing / area, layer overlap & removal,
> inside/outside/interacting selections, domain markers, angle, corner, edge
> length, narrow-region/range selectors, OR-of-variants) — each with what the
> compiler derives from it.

The pieces (Python):

```
python/fastdrc/topology.py        spatial relations off geometry (inside/overlap/…)
python/fastdrc/constraint.py      the constraint DSL (a Python-expression subset, parsed with `ast`)
python/fastdrc/rule.py            the analyzed-rule API the backends work against
python/fastdrc/backend.py         backend registry (swappable targets)
python/fastdrc/backend_klayout.py compile a drawn rule -> a KLayout .drc deck
tools/validate_klayout.py         round-trip each deck rule (draw -> recompile -> diff)
tools/behavioral_validate.py      run both decks in KLayout, compare violations
tools/audit_corpus.py             load the drawn-rule corpus and validate it
```

The `tools/` scripts read a reference deck and drawn-rule corpus from
`docs/gf180mcu/`, which is not part of this repository; place them there to run
the validation.

`fastdrc gui [rule.json]` opens the visual rule editor (Tkinter); its bottom pane
shows the drawn rule compiled live by the selected backend.

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
import fastdrc.backend_klayout                 # registers the "klayout" backend
from fastdrc.backend import get
from fastdrc.rule import Rule

# a drawn rule = shapes (edges) + measures + constraints; see docs/drawing-model.md
deck = get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(my_drawn_rule))
```
