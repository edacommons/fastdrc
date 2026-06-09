"""FastDRC — fast design-rule-check (DRC) engine for IC/PCB layout.

A C++ core exposed to Python through nanobind. The native extension lives in
:mod:`fastdrc._fastdrc`; this package re-exports its public API.

Producer side: build a :class:`Design` from geometry (per layer), then serialize
it to the compiled-geometry (FDCG) format. The :mod:`fastdrc.cli` front-end
loads a GDS via ``rawast`` and feeds shapes in:

    import fastdrc

    d = fastdrc.Design(dbu_per_micron=1000)
    m1 = d.add_layer(gds_layer=10, gds_datatype=0, name="metal1")
    m1.add_rect(0, 0, 10, 10)
    m1.add_polygon([fastdrc.Coord2D(0, 0), fastdrc.Coord2D(10, 0),
                    fastdrc.Coord2D(10, 4), fastdrc.Coord2D(0, 4)])

    fastdrc.write_split(d, "layout_dir")     # one pure .tiles per layer + index.json
"""

from __future__ import annotations

from ._fastdrc import Coord2D, Design, Region, boolean, inflate, write_split

__all__ = ["Coord2D", "Design", "Region", "boolean", "inflate", "write_split"]
__version__ = "0.0.1"
