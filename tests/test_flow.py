"""Flow runner: a deck of boolean steps over layer files, run via doit.

No rawast / GDS needed — uses pre-made layer files, so it exercises the DAG
engine, ref resolution, and incremental (content-hash) up-to-date behaviour.
"""

import pytest

import fastdrc

pytestmark = pytest.mark.skipif(not fastdrc.has_core(), reason="the C++ core is not built (cmake -DFASTDRC_BUILD_PYTHON=ON)")
from fastdrc import flow


def _make_layers(dirpath):
    d = fastdrc.Design(1000)
    d.add_layer(10, 0, "A").add_rect(0, 0, 10, 10)
    d.add_layer(11, 0, "B").add_rect(5, 0, 15, 10)
    fastdrc.write_split(d, str(dirpath))


def test_flow_runs_dag(tmp_path):
    _make_layers(tmp_path / "src")
    deck = tmp_path / "deck.toml"
    deck.write_text(
        '[deck]\n'
        'workdir = "out"\n'
        '[[step]]\n'
        'name = "gate"\n'
        'op = "boolean"\n'
        'kind = "and"\n'
        'inputs = ["src/A.tiles", "src/B.tiles"]\n'   # external files
        '[[step]]\n'
        'name = "diff"\n'
        'op = "boolean"\n'
        'kind = "xor"\n'
        'inputs = ["gate", "src/A.tiles"]\n'         # "gate" -> prior step's output
    )

    rc = flow.run(str(deck))
    assert rc == 0

    out = tmp_path / "out"
    assert (out / "gate.tiles").read_bytes()[:4] == b"FDCG"   # A AND B
    assert (out / "diff.tiles").read_bytes()[:4] == b"FDCG"   # gate XOR A

    # Second run: everything up to date -> still succeeds, no error.
    assert flow.run(str(deck)) == 0


def test_flow_builds_edges(tmp_path):
    # "gate" depends on the two source files; "diff" depends on gate -> task_dep.
    _make_layers(tmp_path / "src")
    deck = tmp_path / "deck.toml"
    deck.write_text(
        '[deck]\nworkdir = "out"\n'
        '[[step]]\nname = "gate"\nop = "boolean"\nkind = "and"\n'
        'inputs = ["src/A.tiles", "src/B.tiles"]\n'
        '[[step]]\nname = "diff"\nop = "boolean"\nkind = "sub"\n'
        'inputs = ["gate", "src/B.tiles"]\n'
    )
    tasks, workdir = flow._build_tasks(str(deck))
    by_name = {t["name"]: t for t in tasks}
    assert by_name["diff"]["task_dep"] == ["gate"]   # edge gate -> diff
    assert by_name["gate"]["task_dep"] == []          # leaves are external files
