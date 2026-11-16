"""FastDRC command line: design rules drawn by example, compiled to a working deck.

    fastdrc init mydeck             a deck is a folder: process.json + rules/<section>/<rule>.json
    fastdrc gui mydeck              draw the rules: one rule, one file, one drawing
    fastdrc build mydeck            -> the deck (a KLayout script) and the rules as sentences
    fastdrc selftest mydeck         does every constraint of every rule behave as drawn?
    fastdrc check chip.gds mydeck   run the deck on a layout, list the violations per rule
"""

from __future__ import annotations

import argparse
import os
import sys


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
    return {"only": args.only, "skip": args.skip, "optimise": not args.no_optimise, "backend": args.backend,
            "deep": args.deep, "profile": args.profile, "params": args.set}


def _set_options(p):
    p.add_argument("--only", action="append", metavar="RULES",
                   help="build these rules alone: names or sections, comma-separated, * and ? allowed "
                        "(NW.*, 3-metal, 2-devices/CO.1)")
    p.add_argument("--skip", action="append", metavar="RULES", help="leave these rules out (same form)")
    p.add_argument("--no-optimise", action="store_true",
                   help="write every rule out in full instead of computing once what several rules need")
    p.add_argument("--deep", action="store_true",
                   help="work on the layout's hierarchy (KLayout's deep mode): a cell is checked once however "
                        "often it is placed, and a violation is reported once, in the cell it lies in")
    p.add_argument("--profile", metavar="NAME",
                   help="build for this profile of the deck's parameters (see `fastdrc parameters DECK`)")
    p.add_argument("--set", action="append", metavar="NAME=VALUE",
                   help="choose one parameter of the deck (top_metal=30K); comma-separated or repeated")
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
    if res.get("layouts"):
        good = sum(1 for v in res["layouts"].values() if v == "ok")
        print(f"{good} of the deck's {len(res['layouts'])} layouts are read as they should be. "
              f"Layout: {res['files'].get('layouts')}")
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
    if args.deep and hit:
        print("(--deep: each violation is counted once, in the cell it lies in, however often that cell is placed)")
    return 1 if hit else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="fastdrc",
        description="FastDRC — design rules drawn by example, compiled to a working DRC deck.")
    sub = parser.add_subparsers(dest="command", required=True)
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

    par_p = sub.add_parser(
        "parameters", help="List a deck's parameters (what is chosen once for a chip), their choices and its profiles.")
    par_p.add_argument("process", nargs="?", default=".", help="deck folder or .zip (default: here)")

    args = parser.parse_args(argv)
    from .params import ParameterError
    try:
        return _run(args)
    except ParameterError as ex:
        raise SystemExit(f"fastdrc: {ex}")


def _parameters(args):
    """`fastdrc parameters`: what can be chosen for this deck."""
    from . import params, process
    proc, _ = process.load(args.process)
    defs = proc.get("parameters") or {}
    if not defs:
        print("this deck has no parameters")
        return 0
    for name, d in defs.items():
        if "choices" in d:
            now = params._key(proc["_values"][name])
            print(f"  {name:14} " + "  ".join(f"[{params._key(c)}]" if params._key(c) == now else params._key(c) for c in d["choices"]))
        else:
            print(f"  {name:14} = {params._key(proc['_values'][name])}   (from {d.get('switch')})")
    for name, picks in (proc.get("profiles") or {}).items():
        print(f"  profile {name}: " + ", ".join(f"{k} = {params._key(v)}" for k, v in picks.items()))
    print("choose with --profile NAME or --set name=value; [the default]")
    return 0


def _run(args):
    if args.command == "rules":
        return _compile_rules(args)
    elif args.command == "parameters":
        return _parameters(args)
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
