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


def _compile_rules(args):
    """`fastdrc rules`: drawn rule files (or a process folder) -> one deck / one text."""
    import json

    from . import process
    from .backend import get

    kw, sheets = {}, []
    for path in args.files:
        if os.path.isdir(path) or process.is_zip(path):   # a deck (folder or zip): its rules, its layer table
            proc, found = process.load(path)
            sheets += found
            kw.setdefault("layermap", {}).update({k: tuple(v) for k, v in (proc.get("layers") or {}).items()})
        else:
            with open(path) as f:
                sheets.append((path, json.load(f)))
    if args.layermap:
        with open(args.layermap) as f:
            kw.setdefault("layermap", {}).update({k: tuple(v) for k, v in json.load(f).items()})
    try:
        rules, notes = process.rules_and_warnings(sheets)
    except Exception as ex:                          # noqa: BLE001
        raise SystemExit(f"fastdrc: {ex}")
    for note in notes:
        print(f"fastdrc: warning: {note}", file=sys.stderr)
    if args.to == "klayout":
        kw.update(standalone=args.standalone, title=args.title)
    backend = get(args.to, **(kw if args.to == "klayout" else {}))
    text = backend.compile_all(rules)
    if args.to == "klayout" and (kw.get("layermap") or args.standalone) and backend.unmapped:
        # such a layer is read by NAME, which a GDS file does not have: every rule on
        # it would silently see nothing
        print("fastdrc: warning: no layer number for " + ", ".join(sorted(backend.unmapped))
              + " -- add to the layer map", file=sys.stderr)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


def _set(args):
    """Which rules, and how the script is fitted to them (`--only`, `--skip`, `--no-optimise`)."""
    from .backend import runner
    try:
        runner(args.backend)
    except KeyError as ex:
        raise SystemExit(f"fastdrc: {ex.args[0]}")
    return {"only": args.only, "skip": args.skip, "optimise": not args.no_optimise, "backend": args.backend}


def _set_options(p):
    p.add_argument("--only", action="append", metavar="RULES",
                   help="build these rules alone: names or sections, comma-separated, * and ? allowed "
                        "(NW.*, 3-metal, 2-devices/CO.1)")
    p.add_argument("--skip", action="append", metavar="RULES", help="leave these rules out (same form)")
    p.add_argument("--no-optimise", action="store_true",
                   help="write every rule out in full instead of computing once what several rules need")
    p.add_argument("--backend", default="klayout", metavar="NAME",
                   help="the backend that compiles the deck and runs it (default: klayout; see `fastdrc backends`)")


def _build(args):
    """`fastdrc build`: a process folder -> its deck and its rules as text."""
    from . import process
    extra = [x for x in (args.also or "").split(",") if x]
    deck, text, n, notes = process.build(args.process, args.out, extra=extra, **_set(args))
    for note in notes:
        print(f"fastdrc: warning: {note}", file=sys.stderr)
    print(f"{n} rules -> {deck}\n{' ' * len(str(n))}          {text}"
          + (f"\n{len(notes)} warning(s)" if notes else ""))
    return 0


def _selftest(args):
    """`fastdrc selftest`: do the rules behave as drawn?"""
    from . import process
    try:
        res = process.selftest(args.process, args.out, **_set(args))
    except RuntimeError as ex:
        raise SystemExit(f"fastdrc: {ex}")
    for note in res["notes"]:
        print(f"fastdrc: warning: {note}", file=sys.stderr)
    tally = {k: sum(1 for v in res["rules"].values() if v == k) for k in ("ok", "wrong", "not generated")}
    for line in res["wrong"]:
        print(f"  WRONG  {line}")
    if args.verbose or not res["wrong"]:
        for rule, why in res["not_generated"]:
            print(f"  not tested  {rule}: {why}")
    if args.verbose:
        for (rule, testing), state in res["constraints"].items():
            print(f"  {'ok   ' if state == 'ok' else 'WRONG'}  {rule}  [{testing}]")
    tested = [t for (_, t) in res["constraints"] if t.startswith(("check ", "match "))]
    checks, filters = (sum(1 for t in tested if t.startswith(k)) for k in ("check ", "match "))
    print(f"{tally['ok']} rule(s) behave as drawn, {tally['wrong']} do not, "
          f"{tally['not generated']} have no generated test yet "
          f"({checks} checks and {filters} filters tested one by one). "
          f"Cases: {res['files'].get('layout', res['files']['cases'])}")
    return 1 if res["wrong"] else 0


def _check(args):
    """`fastdrc check`: build the process, run its deck on a layout, say what it found."""
    from . import process
    from .backend import RunError, runner
    deck, _, n, notes = process.build(args.process, **_set(args))
    for note in notes:
        print(f"fastdrc: warning: {note}", file=sys.stderr)
    eng = runner(args.backend)
    if eng.ready():
        by_hand = eng.by_hand(deck, args.layout)
        raise SystemExit(f"fastdrc: {eng.ready()}." + (f"\n         The deck is ready: {by_hand}" if by_hand else ""))
    try:
        res = eng.run(deck, args.layout, top=args.top, report=args.report)
    except RunError as ex:
        raise SystemExit(f"fastdrc: {ex}")
    hit = {r: v for r, v in res["rules"].items() if v[1]}
    width = max((len(r) for r in hit), default=0)
    for r, (desc, boxes) in hit.items():
        print(f"  {r:{width}}  {len(boxes):5}  {desc}")
    print(f"{sum(len(b) for _, b in hit.values())} violation(s) of {len(hit)} rule(s); {len(res['rules']) - len(hit)} of "
          f"{len(res['rules'])} rules clean." + (f" Report: {res['report']}" if res.get("report") else ""))
    return 1 if hit else 0


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

    rules_p = sub.add_parser(
        "rules", help="Compile drawn rule files (single rules or sheets) into a "
                      "KLayout DRC deck, or into plain-language rule text.")
    rules_p.add_argument("files", nargs="+", help="rule .json files, or decks (folders or .zip)")
    rules_p.add_argument("--to", default="klayout",
                         help="what to produce (default: a KLayout .drc deck)")
    rules_p.add_argument("--layermap", help="JSON file {layer name: [gds layer, datatype]}")
    rules_p.add_argument("-o", "--out", help="write here instead of standard output")
    rules_p.add_argument("--standalone", action="store_true",
                         help="a complete KLayout script (reads $input, writes $report): "
                              "klayout -b -r deck.drc -rd input=layout.gds -rd report=out.lyrdb")
    rules_p.add_argument("--title", default="FastDRC", help="report title (with --standalone)")

    init_p = sub.add_parser("init", help="Create an empty deck: a folder (process.json, rules/), or NAME.zip.")
    init_p.add_argument("process", help="folder to create (or a .zip file)")
    init_p.add_argument("--name", help="process name (default: the folder's name)")

    build_p = sub.add_parser(
        "build", help="Compile a process folder: the KLayout deck and the rules as text.")
    build_p.add_argument("process", nargs="?", default=".", help="deck folder or .zip (default: here)")
    build_p.add_argument("-o", "--out", help="output folder (default: <deck>/build; for a zip, <name>.build beside it)")

    _set_options(build_p)
    build_p.add_argument("--with", dest="also", metavar="BACKEND[,BACKEND]",
                         help="also write the files of these backends (see `fastdrc backends`)")

    self_p = sub.add_parser(
        "selftest", help="Generate test cases from the drawings and report which rules behave as drawn "
                         "(needs KLayout).")
    self_p.add_argument("process", nargs="?", default=".", help="deck folder or .zip (default: here)")
    self_p.add_argument("-o", "--out", help="output folder (default: <deck>/build; for a zip, <name>.build beside it)")
    _set_options(self_p)
    self_p.add_argument("-v", "--verbose", action="store_true", help="also list the rules with no generated test")

    pack_p = sub.add_parser("pack", help="Put a deck folder into one zip file (build/ is left out).")
    pack_p.add_argument("process", nargs="?", default=".", help="deck folder (default: here)")
    pack_p.add_argument("-o", "--out", help="zip file to write (default: <folder>.zip beside it)")

    unpack_p = sub.add_parser("unpack", help="Extract a zipped deck into a folder.")
    unpack_p.add_argument("zip", help="deck .zip")
    unpack_p.add_argument("folder", nargs="?", help="folder to extract into (default: the zip's name, beside it)")

    sub.add_parser("backends", help="List the available backends (the shipped ones and installed plugins).")

    split_p = sub.add_parser(
        "split", help="Rewrite a deck's sheets (several rules on one drawing) as one file per rule.")
    split_p.add_argument("process", nargs="?", default=".", help="deck folder or .zip (default: here)")

    check_p = sub.add_parser(
        "check", help="Run a process's rules on a layout (needs KLayout) and list the violations.")
    check_p.add_argument("layout", help="layout file (GDS / OASIS)")
    check_p.add_argument("process", nargs="?", default=".", help="deck folder or .zip (default: here)")
    _set_options(check_p)
    check_p.add_argument("--top", help="top cell (default: the layout's)")
    check_p.add_argument("--report", help="report database to write (default: beside the layout)")

    gui_p = sub.add_parser(
        "gui", help="Launch the visual rule editor (Tkinter).")
    gui_p.add_argument("file", nargs="?", help="optional rule .json, or a deck (folder or .zip), to open")

    args = parser.parse_args(argv)
    if args.command in ("compose", "run", "boolean", "inflate") and not fastdrc.has_core():
        raise SystemExit(f"fastdrc {args.command}: {fastdrc.NO_CORE}.")
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
    elif args.command == "rules":
        return _compile_rules(args)
    elif args.command == "init":
        from fastdrc import process
        made = process.init(args.process, args.name)
        if args.process.lower().endswith(".zip"):
            print(f"{'created' if made else 'already there:'} {args.process} -- draw rules into it: fastdrc gui {args.process}")
        else:
            print(f"{'created' if made else 'already a process:'} {args.process}/{process.PROCESS_FILE}"
                  f" -- draw rules into {args.process}/rules/ (fastdrc gui {args.process})")
    elif args.command == "build":
        return _build(args)
    elif args.command == "check":
        return _check(args)
    elif args.command == "selftest":
        return _selftest(args)
    elif args.command == "split":
        from fastdrc import process
        n = process.split(args.process)
        print(f"{n} rule file(s) written: one rule, one file, one drawing" if n
              else "nothing to do: every rule already has its own file")
    elif args.command == "pack":
        from fastdrc import process
        if not process.is_process(args.process):
            raise SystemExit(f"fastdrc: {args.process} is not a deck folder (no {process.PROCESS_FILE})")
        print(process.pack(args.process, args.out))
    elif args.command == "unpack":
        from fastdrc import process
        if not process.is_zip(args.zip):
            raise SystemExit(f"fastdrc: {args.zip} is not a zip file")
        target = args.folder or os.path.splitext(os.path.abspath(args.zip))[0]
        if os.path.isdir(target) and os.listdir(target):
            raise SystemExit(f"fastdrc: {target} exists and is not empty")
        print(process.unpack(args.zip, target))
    elif args.command == "backends":
        from fastdrc import backend
        for name in backend.available():
            cls = backend._REGISTRY[name]
            print(f"  {name:10} {cls.about}{'; runs it on a layout' if cls.runs else ''}  ({cls.__module__})")
        for problem in backend.problems:
            print(f"fastdrc: warning: {problem}", file=sys.stderr)
    elif args.command == "gui":
        from fastdrc import editor
        editor.run(args.file)


if __name__ == "__main__":
    main()
