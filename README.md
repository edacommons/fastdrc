# FastDRC

A fast **design-rule-check (DRC)** toolkit for IC/PCB layout, in two parts:

1. a **C++ checking core** (delivered as a C++ library and as a Python module via
   [nanobind](https://nanobind.readthedocs.io)); and
2. a **rule compiler** that lets you author DRC rules by **drawing an example** of
   the geometry — *geometry-by-example* — instead of writing layer algebra by hand.

> Status: pre-alpha. The C++ core is a minimum-spacing check with an O(n²)
> reference implementation (the spatial-index fast path is the next milestone).
> The rule compiler has a working **KLayout `.drc` backend** (the bootstrap
> target, validated rule-by-rule against a production foundry deck) and is
> structured so the C++ engine becomes a second, swappable backend.

## Authoring rules by drawing

You describe a rule by **drawing real shapes of real layers** in a spatial
relationship and adding a few constraints — *not* by writing `.and` / `.not` /
`.inside` / `.outside`. The compiler reads the topology off the drawing and
*derives* the operations needed to implement the check.

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
python/fastdrc/constraint.py      the constraint DSL (a Python-expression subset)
python/fastdrc/rule.py            the analyzed-rule API the backends work against
python/fastdrc/backend.py         backend registry (swappable targets)
python/fastdrc/backend_klayout.py compile a drawn rule -> a KLayout .drc deck
tools/validate_klayout.py         round-trip each deck rule (draw -> recompile -> diff)
tools/behavioral_validate.py      run both decks in KLayout, compare violations
tools/audit_corpus.py             load the drawn-rule corpus and validate it
```

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
cmake --build build              # build fastdrc_core + test_drc
ctest --test-dir build --output-on-failure
```

To consume the library from another CMake project, link `fastdrc::core` and
include `<fastdrc/drc.hpp>`.

## Build the Python module

Requires network access on first build (the build fetches `nanobind` and
`scikit-build-core` in an isolated environment).

```bash
pip install -e .                 # editable install of the `fastdrc` package
pytest                           # run the Python tests
python examples/basic.py
```

```python
import fastdrc

shapes = [fastdrc.Rect(0, 0, 10, 10), fastdrc.Rect(12, 0, 22, 10)]
for v in fastdrc.check_min_spacing(shapes, min_spacing=3.0):
    print(v)
```

## Compiling a drawn rule

```python
import fastdrc.backend_klayout                 # registers the "klayout" backend
from fastdrc.backend import get
from fastdrc.rule import Rule

# a drawn rule = shapes (edges) + measures + constraints; see docs/drawing-model.md
deck = get("klayout", layermap={"metal1": (34, 0)}).compile(Rule(my_drawn_rule))
```
