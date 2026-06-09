"""End-to-end CLI test: GDS (minted via rawast's save) -> compose -> FDCG.

Skipped if rawast is not importable, so the C++/binding tests still run in a
minimal environment.
"""

import json
import os

import pytest

rawast = pytest.importorskip("rawast")

from fastdrc.cli import _chdir, compose  # noqa: E402


def _write_gds(path):
    """Mint a tiny 2-layer GDS using rawast's bidirectional save."""
    base = os.path.dirname(rawast.GRAMMAR_DIR)
    with _chdir(base):
        g = rawast.Grammar("gdsii")
    tree = {
        "version": 5,
        "timestamp": [0] * 12,
        "name": "TESTLIB",
        "units": [0.001, 1e-9],  # 1e-9 m/dbu -> 1000 dbu/micron
        "structures": [{
            "timestamp": [0] * 12,
            "name": "TOP",
            "elements": [
                # layer 10: two overlapping squares -> union
                {"type": "boundary", "layer": 10, "datatype": 0,
                 "xy": [0, 0, 100, 0, 100, 100, 0, 100, 0, 0]},
                {"type": "boundary", "layer": 10, "datatype": 0,
                 "xy": [50, 50, 150, 50, 150, 150, 50, 150, 50, 50]},
                # layer 11: one square
                {"type": "boundary", "layer": 11, "datatype": 0,
                 "xy": [0, 0, 40, 0, 40, 40, 0, 40, 0, 0]},
            ],
        }],
    }
    with open(path, "wb") as f:
        f.write(g.save(tree))


def test_compose_split_from_gds(tmp_path):
    gds = tmp_path / "in.gds"
    _write_gds(str(gds))

    config = tmp_path / "compose.toml"
    config.write_text(
        "[input]\n"
        f'gds = "{gds.name}"\n'
        "[output]\n"
        f'path = "{tmp_path / "out"}"\n'
        "[[layer]]\n"
        'name = "metal1"\n'
        "gds_layer = 10\n"
        "gds_datatype = 0\n"
        "[[layer]]\n"
        'name = "via1"\n'
        "gds_layer = 11\n"
        "gds_datatype = 0\n"
    )

    compose(str(config))

    outdir = tmp_path / "out"
    idx = json.loads((outdir / "index.json").read_text())
    assert idx["dbu_per_micron"] == 1000  # derived from GDS units
    files = {layer["file"] for layer in idx["layers"]}
    assert files == {"metal1.tiles", "via1.tiles"}
    assert (outdir / "metal1.tiles").read_bytes()[:4] == b"FDCG"


def test_compose_autodiscovers_layers(tmp_path):
    gds = tmp_path / "in.gds"
    _write_gds(str(gds))

    config = tmp_path / "compose.toml"
    config.write_text(
        "[input]\n"
        f'gds = "{gds.name}"\n'
        "[output]\n"
        f'path = "{tmp_path / "out"}"\n'
    )

    compose(str(config))  # no [[layer]] -> import all (layer,datatype) found

    outdir = tmp_path / "out"
    idx = json.loads((outdir / "index.json").read_text())
    # layers 10 and 11 auto-named L<l>D<d>
    files = {layer["file"] for layer in idx["layers"]}
    assert files == {"L10D0.tiles", "L11D0.tiles"}
    data = (outdir / "L10D0.tiles").read_bytes()
    assert data[:4] == b"FDCG"
    assert data[-8:] == b"FDCGEND\x00"
