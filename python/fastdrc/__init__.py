"""FastDRC — design rules drawn by example, compiled to a working DRC deck.

The rule compiler (drawings -> a KLayout deck, the rules as sentences, test cases) is
pure Python: :mod:`fastdrc.process`, :mod:`fastdrc.backend`, :mod:`fastdrc.editor`.

The C++ checking core is in development and is NOT part of the Python package: it is
built separately with CMake (see the README) and loaded only when it is asked for.
Today it is the geometry layer -- build a :class:`Design` per layer, write the compiled
``.tiles`` form, do layer algebra on it:

    import fastdrc

    d = fastdrc.Design(dbu_per_micron=1000)
    m1 = d.add_layer(gds_layer=10, gds_datatype=0, name="metal1")
    m1.add_rect(0, 0, 10, 10)
    fastdrc.write_split(d, "layout_dir")     # one pure .tiles per layer + index.json
"""

from __future__ import annotations

__all__ = ["Coord2D", "Design", "Region", "boolean", "inflate", "write_split", "has_core"]
__version__ = "0.0.1"

_CORE = ("Coord2D", "Design", "Region", "boolean", "inflate", "write_split")
NO_CORE = ("the C++ core is not built here -- it is in development and not part of this release. "
           "Drawing rules, building decks, `check` and `selftest` do not need it; `compose`, "
           "`boolean`, `inflate` and `run` do (to build it: see \"Build the C++ core\" in the README)")


def has_core() -> bool:
    """Is the C++ core (the compiled module ``fastdrc._fastdrc``) built and importable?"""
    try:
        from . import _fastdrc  # noqa: F401
    except ImportError:
        return False
    return True


def __getattr__(name):
    # the core's names stay importable from `fastdrc`, and load it on first use
    if name in _CORE:
        try:
            from . import _fastdrc
        except ImportError as ex:
            raise ImportError(NO_CORE) from ex
        return getattr(_fastdrc, name)
    raise AttributeError(f"module 'fastdrc' has no attribute {name!r}")
