"""The README's relative links are made full addresses for the package index (hatch_build.py)."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_relative_link_is_left_and_the_file_keeps_its_own():
    sys.path.insert(0, ROOT)
    try:
        from hatch_build import RAW, REPO, for_the_index
    finally:
        sys.path.remove(ROOT)
    text = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
    assert "](docs/img/" in text and "](docs/from-a-manual.md)" in text      # relative in the repository
    out = for_the_index(text)
    assert all(re.match(r"https?:|#", t) for t in re.findall(r"\]\(([^)\s]+)\)", out))
    assert f"]({RAW}docs/img/demo/PO.2.svg)" in out
    assert f"]({REPO}/blob/main/docs/from-a-manual.md)" in out
    assert f"]({REPO}/tree/main/examples/demo)" in out and f"]({REPO}/blob/main/LICENSE)" in out
