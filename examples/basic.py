"""Minimal FastDRC producer demo: build a Design, compose, serialize.

The CLI (`fastdrc compose config.toml`) does this from a GDS via rawast; here we
add a couple of shapes by hand and write both output forms.
"""

import fastdrc

d = fastdrc.Design(dbu_per_micron=1000)

m1 = d.add_layer(gds_layer=10, gds_datatype=0, name="metal1")
m1.add_rect(0, 0, 10, 10)
m1.add_rect(5, 5, 15, 15)        # overlaps -> merged into a disjoint union
m1.cut_rect(2, 2, 4, 4)          # punch a hole

via1 = d.add_layer(gds_layer=11, gds_datatype=0, name="via1")
via1.add_polygon([fastdrc.Coord2D(0, 0), fastdrc.Coord2D(8, 0),
                  fastdrc.Coord2D(8, 4), fastdrc.Coord2D(0, 4)])

# One pure single-layer .tiles per layer, plus index.json (the id -> file +
# source mapping). A .tiles is a general geometry container — boolean-op results
# between layers store the same way.
fastdrc.write_split(d, "layout_dir")

print("wrote layout_dir/ (metal1.tiles, via1.tiles, index.json)")
