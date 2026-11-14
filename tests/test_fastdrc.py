"""Python-side tests for the nanobind module. Requires `pip install -e .`.

Exercise the producer API: build a Design, compose geometry per layer, and
serialize to the FDCG format (single + split forms).
"""

import json

import pytest

import fastdrc

pytestmark = pytest.mark.skipif(not fastdrc.has_core(), reason="the C++ core is not built (cmake -DFASTDRC_BUILD_PYTHON=ON)")


def test_coord2d():
    p = fastdrc.Coord2D(3, 4)
    assert (p.x, p.y) == (3, 4)
    assert p == fastdrc.Coord2D(3, 4)
    assert p != fastdrc.Coord2D(3, 5)


def test_region_merges_geometry():
    d = fastdrc.Design(1000)
    m = d.add_layer(10, 0, "metal1")
    m.add_rect(0, 0, 10, 10)
    m.add_rect(5, 5, 15, 15)  # overlaps -> union, no double tiles
    m.cut_rect(2, 2, 4, 4)    # punch a hole
    assert m.rect_count() >= 1


def test_write_split_index(tmp_path):
    d = fastdrc.Design(1000)
    d.add_layer(10, 0, "metal1").add_rect(0, 0, 10, 10)
    d.add_layer(11, 0, "via1").add_polygon(
        [fastdrc.Coord2D(0, 0), fastdrc.Coord2D(4, 0),
         fastdrc.Coord2D(4, 4), fastdrc.Coord2D(0, 4)])

    outdir = tmp_path / "split"
    fastdrc.write_split(d, str(outdir))

    idx = json.loads((outdir / "index.json").read_text())
    assert idx["format"] == "FDCG-index"
    assert idx["dbu_per_micron"] == 1000
    files = {layer["file"] for layer in idx["layers"]}
    assert files == {"metal1.tiles", "via1.tiles"}
    for f in files:
        # each per-layer file is a pure single-layer FDCG
        data = (outdir / f).read_bytes()
        assert data[:4] == b"FDCG"
        assert data[-8:] == b"FDCGEND\x00"
