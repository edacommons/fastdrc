"""FastDRC command-line front-end.

`fastdrc compose CONFIG.toml` reads a GDS file via ``rawast``, flattens it into
a :class:`fastdrc.Design` (one layer per selected GDS layer/datatype), and
writes the compiled-geometry (FDCG) form: one pure single-layer ``.tiles`` file
per layer plus a JSON index.

The TOML config (forward-compatible with a future pipeline definition)::

    [input]
    gds = "layout.gds"            # path (relative to the config file)
    dbu_per_micron = 1000         # optional; default: derived from GDS units
    top = "TOP"                   # optional; default: auto-detected top cell(s)

    [output]
    path = "out"                  # output directory (per-layer files + index)
    index = "index.json"          # optional; default index.json

    [[layer]]                     # omit all -> import every (layer,datatype)
    name = "metal1"
    gds_layer = 10
    gds_datatype = 0
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tomllib

import fastdrc
from fastdrc import gdsii


@contextlib.contextmanager
def _chdir(path):
    prev = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def _load_gdsii_grammar():
    """Load rawast's bundled GDSII grammar.

    rawast resolves its meta-grammar (``grammars/rawast.json``) by a
    cwd-relative path, so we load while chdir'd to the directory that contains
    ``grammars/``.
    """
    try:
        import rawast
    except ImportError:
        raise SystemExit("fastdrc compose reads GDS through rawast, which is not "
                         "installed: pip install 'fastdrc[gds]'") from None

    base = os.path.dirname(rawast.GRAMMAR_DIR)  # the dir that holds grammars/
    with _chdir(base):
        return rawast.Grammar("gdsii")


def _dbu_from_units(tree):
    """dbu/micron from the GDS ``units`` record (units[1] = meters per dbu)."""
    units = tree.get("units")
    if units and len(units) >= 2 and units[1]:
        return int(round(1e-6 / float(units[1])))
    return 1000


def compose(config_path, out_override=None):
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)

    inp = cfg.get("input", {})
    out = cfg.get("output", {})
    if "gds" not in inp:
        raise SystemExit("config: [input] gds is required")
    gds_path = inp["gds"]
    if not os.path.isabs(gds_path):
        gds_path = os.path.join(
            os.path.dirname(os.path.abspath(config_path)), gds_path)

    grammar = _load_gdsii_grammar()
    tree = grammar.parse_file(gds_path)

    dbu = int(inp.get("dbu_per_micron") or _dbu_from_units(tree))
    design = fastdrc.Design(dbu)

    # Flatten hierarchy (SREF/AREF + transforms), stroke paths, convert boxes:
    # -> [(gds_layer, gds_datatype, points)] in the top cell's coordinate space.
    warnings = []
    flat = gdsii.flatten(tree, top=inp.get("top"), on_warn=warnings.append)

    # Layer selection. [[layer]] entries map a GDS (layer, datatype) -> name and
    # their order sets the auto-assigned fastdrc layer ids. With no entries,
    # import every (layer, datatype) encountered (sorted), auto-named L<l>D<d>.
    wanted = cfg.get("layer") or []
    regions = {}  # (gds_layer, gds_datatype) -> Region
    if wanted:
        for spec in wanted:
            key = (int(spec["gds_layer"]), int(spec["gds_datatype"]))
            name = spec.get("name", f"L{key[0]}D{key[1]}")
            regions[key] = design.add_layer(key[0], key[1], name)
    else:
        keys = sorted({(layer, dt) for (layer, dt, _pts) in flat})
        for (layer, dt) in keys:
            regions[(layer, dt)] = design.add_layer(layer, dt, f"L{layer}D{dt}")

    added = skipped = 0
    for (layer, dt, pts) in flat:
        region = regions.get((layer, dt))
        if region is None:
            skipped += 1
            continue
        if len(pts) < 3:
            continue
        region.add_polygon([fastdrc.Coord2D(x, y) for x, y in pts])
        added += 1

    path = out_override or out.get("path")
    if not path:
        raise SystemExit("config: [output] path (a directory) is required")
    fastdrc.write_split(design, path, out.get("index", "index.json"))

    print(f"composed {added} shape(s) into {len(regions)} layer(s) "
          f"@ {dbu} dbu/micron -> {path}/ (per-layer .tiles + index)")
    if skipped:
        print(f"  note: {skipped} shape(s) on unselected layers ignored",
              file=sys.stderr)
    for w in dict.fromkeys(warnings):  # de-duplicated, order-preserving
        print(f"  warning: {w}", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fastdrc",
        description="FastDRC — compile GDS geometry to the FDCG format.")
    sub = parser.add_subparsers(dest="command", required=True)
    compose_p = sub.add_parser(
        "compose",
        help="Read a GDS (via rawast) and write compiled geometry per a TOML config.")
    compose_p.add_argument("config", help="Path to the TOML compose config.")
    compose_p.add_argument("--out", help="Override [output] path (deck use).")

    run_p = sub.add_parser(
        "run", help="Run a rule-deck flow (a DAG of steps) via doit.")
    run_p.add_argument("deck", help="Path to the deck TOML.")
    run_p.add_argument("-j", "--jobs", type=int, default=1,
                       help="number of parallel jobs")

    bool_p = sub.add_parser(
        "boolean",
        help="Layer algebra: A op B between two .tiles files -> a new .tiles.")
    bool_p.add_argument("--a", required=True, help="layer A (.tiles)")
    bool_p.add_argument("--b", required=True, help="layer B (.tiles)")
    bool_p.add_argument("--op", required=True, choices=["or", "and", "sub", "xor"],
                        help="A op B  (for B - A, swap --a/--b)")
    bool_p.add_argument("--out", required=True, help="output layer (.tiles)")

    inf_p = sub.add_parser(
        "inflate", help="Grow (d > 0) or shrink (d < 0) a layer by d on all sides "
                        "-> a new .tiles.")
    inf_p.add_argument("--in", dest="infile", required=True, help="input (.tiles)")
    inf_p.add_argument("--d", type=int, required=True, help="distance in DBU: > 0 grows (a halo), < 0 shrinks")
    inf_p.add_argument("--out", required=True, help="output layer (.tiles)")

    gui_p = sub.add_parser(
        "gui", help="Launch the visual rule editor (Tkinter).")
    gui_p.add_argument("file", nargs="?", help="optional rule .json to open")

    args = parser.parse_args(argv)
    if args.command == "compose":
        compose(args.config, args.out)
    elif args.command == "run":
        from fastdrc import flow
        raise SystemExit(flow.run(args.deck, args.jobs))
    elif args.command == "boolean":
        n = fastdrc.boolean(args.a, args.b, args.op, args.out)
        print(f"{args.op}: {args.a} {args.op} {args.b} -> {args.out} ({n} tiles)")
    elif args.command == "inflate":
        n = fastdrc.inflate(args.infile, args.d, args.out)
        print(f"inflate {args.infile} by {args.d} -> {args.out} ({n} tiles)")
    elif args.command == "gui":
        from fastdrc import editor
        editor.run(args.file)


if __name__ == "__main__":
    main()
