# FastDRC

A fast **design-rule-check (DRC)** engine for IC/PCB layout. A C++ core,
delivered both as a C++ library and as a Python module (via
[nanobind](https://nanobind.readthedocs.io)).

> Status: pre-alpha scaffold. The current rule set is a minimum-spacing check
> with an O(n²) reference implementation; the spatial-index "fast path" is the
> next milestone.

## Layout

```
include/fastdrc/   public C++ headers
src/               C++ core implementation  -> fastdrc::core
bindings/          nanobind module          -> fastdrc._fastdrc
python/fastdrc/    Python package wrapper
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
