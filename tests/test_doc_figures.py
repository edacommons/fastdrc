"""The figures in the docs are what the rule files give, and every one the docs show exists."""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_figures_are_current():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "doc_figures.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_figure_shown_exists():
    for doc in ("README.md", "docs/drawing-model.md", "docs/from-a-manual.md", "docs/own-and-known.md"):
        text = open(os.path.join(ROOT, doc), encoding="utf-8").read()
        shown = re.findall(r"!\[[^\]]*\]\(([^)]+\.svg)\)", text)
        assert shown, doc
        for path in shown:
            assert os.path.exists(os.path.join(ROOT, os.path.dirname(doc), path)), f"{doc}: {path}"
