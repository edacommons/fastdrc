#!/usr/bin/env python3
"""The figures in the docs, drawn by the svg backend from the rule files themselves.

    python tools/doc_figures.py            write docs/img/<set>/<rule>.svg
    python tools/doc_figures.py --check    exit 1 if a figure is not what the rule files give

Sets: `demo` -- every rule of examples/demo; `edges` -- docs/figures, the small
drawings that show what straight and wavy edges mean, a centre mark and a sum. A figure is never edited by hand:
change the rule file and run this again.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))
from fastdrc import process  # noqa: E402

SETS = {"demo": "examples/demo", "edges": "docs/figures"}
IMG = os.path.join(ROOT, "docs", "img")


def figures(deck) -> dict:
    """{file name: svg text} for every rule of a deck."""
    with tempfile.TemporaryDirectory() as tmp:
        process.build(os.path.join(ROOT, deck), tmp, extra=("svg",))
        stem = process.deck_name(os.path.join(ROOT, deck)) + "."
        return {n[len(stem):]: open(os.path.join(tmp, n), encoding="utf-8").read()
                for n in sorted(os.listdir(tmp)) if n.endswith(".svg")}


def main(argv):
    check, stale = "--check" in argv, []
    for name, deck in SETS.items():
        folder, want = os.path.join(IMG, name), figures(deck)
        have = set(os.listdir(folder)) if os.path.isdir(folder) else set()
        for extra in sorted(have - set(want)):
            stale.append(os.path.join(name, extra))
            if not check:
                os.remove(os.path.join(folder, extra))
        for n, text in want.items():
            path = os.path.join(folder, n)
            if os.path.exists(path) and open(path, encoding="utf-8").read() == text:
                continue
            stale.append(os.path.join(name, n))
            if not check:
                os.makedirs(folder, exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
    if check and stale:
        print("docs/img is not what the rule files give (run tools/doc_figures.py):", *stale, sep="\n  ")
        return 1
    print(f"{len(stale)} figure(s) {'differ' if check else 'written'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
