"""A deck installed as a package is found by its name (the ``fastdrc.decks`` entry points)."""
import os
import types

import pytest

from fastdrc import cli, process

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "..", "examples", "demo")


class _Entry:
    def __init__(self, name, module):
        self.name, self._module = name, module

    def load(self):
        if self._module is None:
            raise ImportError("broken package")
        return self._module


@pytest.fixture
def offered(monkeypatch):
    """The demo deck offered under the name `demo-installed`, beside a package that does not load."""
    mod = types.ModuleType("deck_package")
    mod.PATH = os.path.abspath(DEMO)
    import importlib.metadata as md
    monkeypatch.setattr(md, "entry_points", lambda group=None: (
        [_Entry("broken", None), _Entry("demo-installed", mod)] if group == "fastdrc.decks" else []))
    return mod.PATH


def test_an_installed_deck_is_found_by_its_name(offered, tmp_path, monkeypatch):
    assert process.installed() == {"demo-installed": offered}
    monkeypatch.chdir(tmp_path)
    assert process.find("demo-installed") == (offered, str(tmp_path / "demo-installed.build"))


def test_a_folder_of_that_name_comes_first(offered, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.mkdir("demo-installed")
    assert process.find("demo-installed") == ("demo-installed", None)
    assert process.find(DEMO) == (DEMO, None)


def test_nothing_is_written_into_the_installed_package(offered, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(offered))
    assert cli.main(["build", "demo-installed"]) == 0
    assert os.path.isfile(tmp_path / "demo-installed.build" / "demo.drc")
    assert set(os.listdir(offered)) == before
    assert cli.main(["decks"]) is None and "demo-installed" in capsys.readouterr().out


def test_an_unknown_name_is_said_so(offered):
    with pytest.raises(SystemExit) as ex:
        cli.main(["build", "no-such-deck"])
    assert "no installed deck of that name" in str(ex.value) and "demo-installed" in str(ex.value)


def test_the_gf180mcu_package_offers_its_deck():
    pytest.importorskip("fastdrc_gf180mcu")
    folder = process.installed().get("gf180mcu")
    assert folder and process.is_process(folder) and os.path.isdir(os.path.join(folder, "rules"))
