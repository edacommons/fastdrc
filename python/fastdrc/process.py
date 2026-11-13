"""A PROCESS: one folder holding everything that turns a design manual into a deck.

    mydeck/
      process.json            what is said once for the whole deck
      rules/<section>/*.json  ONE rule per file, one drawing each; a section of the
                              manual is a folder (rule files directly in rules/ work too)
      build/                  generated: <name>.drc (the KLayout script), <name>.md (the rules as text)

The same folder zipped -- ``mydeck.zip`` -- is the same deck: every function here that
takes a deck takes either (`pack` / `unpack` convert). What is generated from a zip is
written beside it, in ``mydeck.build/``; the zip itself holds only what was written by
hand.

``process.json``::

    {"name": "Demo",
     "layers":  {"nwell": [1, 0], "diff": [2, 0], ...},        layer name -> GDS layer, datatype
     "legend":  {"pdiff": "diff and pimp", ...},                names for combinations
     "columns": {"thin": [["not_interacting", "thick"]], ...},  the table columns (domains)
     "each":    {"metalint": ["metal2", "metal3"]},             families a rule is repeated for
     "touching": "allowed",                                     abutting shapes never violate a minimum distance
     "connect": [["nwell", "ndiff"], ...]}                      optional; or draw the stack

A sheet may still carry its own legend / columns / connections; the process supplies
what it does not. How layers connect is collected from EVERY sheet (a stack drawn on
one sheet makes the nets for all of them) plus the process's own list.
"""

from __future__ import annotations

import contextlib
import glob
import json
import os
import shutil
import tempfile
import zipfile

from .rule import Rule
from .sheet import rules_of, sheet_warnings

PROCESS_FILE = "process.json"


def _natural(text):
    """Sort key putting NW.2 before NW.10."""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def section_of(path, folder):
    """The section a rule file belongs to: the folder it lies in under rules/ ("" for a
    file directly in rules/)."""
    root = os.path.join(folder, "rules")
    rel = os.path.relpath(os.path.dirname(os.path.abspath(path)), os.path.abspath(root if os.path.isdir(root) else folder))
    return "" if rel == "." else rel


def is_process(path) -> bool:
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, PROCESS_FILE))


def is_zip(path) -> bool:
    """Is `path` a deck in one file (a zipped deck folder)?"""
    return os.path.isfile(path) and zipfile.is_zipfile(path)


def deck_name(path) -> str:
    """What a deck is called when its process.json gives no name: the folder's name, or
    the zip file's without ``.zip``."""
    base = os.path.basename(os.path.abspath(path))
    return os.path.splitext(base)[0] if is_zip(path) else base


def build_dir(path) -> str:
    """Where a deck's generated files go by default: ``build/`` in the folder; for
    ``mydeck.zip``, ``mydeck.build/`` beside it."""
    if is_zip(path):
        return os.path.splitext(os.path.abspath(path))[0] + ".build"
    return os.path.join(path, "build")


def _kept(rel) -> bool:
    """Does the file `rel` (relative to the deck) belong in the zip?  Everything written
    by hand does; what is generated (build/) and hidden files do not."""
    parts = rel.replace("\\", "/").split("/")
    return parts[0] != "build" and not any(p.startswith(".") or p == "__MACOSX" for p in parts)


def pack(folder, zip_path=None) -> str:
    """Write the deck `folder` as one zip file (default: ``<folder>.zip`` beside it);
    returns its path. The zip is replaced in one step, never left half-written."""
    folder = os.path.abspath(folder)
    zip_path = os.path.abspath(zip_path or folder.rstrip(os.sep) + ".zip")
    names = []
    for here, dirs, files in os.walk(folder):
        dirs.sort(key=_natural)
        for f in sorted(files, key=_natural):
            full = os.path.join(here, f)
            rel = os.path.relpath(full, folder).replace(os.sep, "/")
            if _kept(rel) and os.path.abspath(full) != zip_path:
                names.append((rel, full))
    names.sort(key=lambda n: (n[0] != PROCESS_FILE,))          # process.json first; the rest as walked
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=os.path.dirname(zip_path))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for rel, full in names:
                z.write(full, rel)
        os.replace(tmp, zip_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return zip_path


def unpack(zip_path, folder) -> str:
    """Extract the deck `zip_path` into `folder` (made if missing); returns `folder`.
    A zip made by zipping the folder itself (everything under one top folder) gives
    the same deck. Entries that would land outside `folder` are refused."""
    folder = os.path.abspath(folder)
    with zipfile.ZipFile(zip_path) as z:
        for i in z.infolist():                              # before anything is written
            parts = i.filename.replace("\\", "/").split("/")
            if ".." in parts or i.filename.startswith(("/", "\\")) or os.path.isabs(i.filename):
                raise ValueError(f"{zip_path}: refusing entry {i.filename!r} (outside the deck)")
        entries = [i for i in z.infolist() if not i.is_dir() and _kept(i.filename)]
        tops = {i.filename.split("/")[0] for i in entries}
        strip = ""
        if PROCESS_FILE not in tops and len(tops) == 1 and all("/" in i.filename for i in entries):
            strip = next(iter(tops)) + "/"                  # mydeck.zip holding mydeck/...
        for i in entries:
            rel = i.filename[len(strip):]
            if not _kept(rel):
                continue
            target = os.path.abspath(os.path.join(folder, *rel.split("/")))
            if os.path.isabs(rel) or os.path.commonpath([folder, target]) != folder:
                raise ValueError(f"{zip_path}: refusing entry {i.filename!r} (outside the deck)")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(i) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    os.makedirs(folder, exist_ok=True)
    return folder


@contextlib.contextmanager
def opened(path, save=False):
    """The deck `path` as a folder: the folder itself, or a zip unpacked for as long as
    the block runs. With `save`, what the block changed is written back into the zip."""
    if not is_zip(path):
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="fastdrc-deck-") as tmp:
        folder = unpack(path, os.path.join(tmp, deck_name(path)))
        yield folder
        if save:
            pack(folder, path)


def rule_files(folder) -> list:
    """The rule / sheet files of a process folder (``rules/*.json``, else ``*.json`` in
    the folder itself), in name order. Files that are not drawings are left out."""
    sub = os.path.join(folder, "rules")
    root = sub if os.path.isdir(sub) else folder
    paths = sorted(glob.glob(os.path.join(root, "*.json")) + glob.glob(os.path.join(root, "*", "*.json")),
                   key=lambda p: (_natural(os.path.relpath(os.path.dirname(p), root)), _natural(os.path.basename(p))))
    paths = [p for p in paths if os.path.basename(os.path.dirname(p)) != "build"]
    out = []
    for p in paths:
        if os.path.basename(p) == PROCESS_FILE:
            continue
        try:
            with open(p) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and ("shapes" in data or "or" in data or isinstance(data.get("rules"), list)):
            out.append(p)
    return out


def load(folder):
    """``(process, [(path, sheet), ...])`` with the process's legend, columns and
    connections merged into every sheet (a sheet's own entries win). For a zipped
    deck the paths read ``mydeck.zip/rules/...``."""
    if is_zip(folder):
        with opened(folder) as tmp:
            proc, merged = load(tmp)
        return proc, [(os.path.join(folder, os.path.relpath(p, tmp)), d) for p, d in merged]
    proc = {}
    pf = os.path.join(folder, PROCESS_FILE)
    if os.path.isfile(pf):
        with open(pf) as f:
            proc = json.load(f)
    sheets = []
    for p in rule_files(folder):
        with open(p) as f:
            sheets.append((p, json.load(f)))
    # A legend name means one thing in a process: names defined on any sheet are known
    # on all of them (the process's own legend first, a sheet's own definition last).
    shared = {}
    for _, data in sheets:
        shared.update(data.get("layers") or {})
    shared.update(proc.get("legend") or {})
    proc = dict(proc, legend=shared)
    joined = [tuple(x) for x in proc.get("connect") or []]
    from .sheet import is_section
    for _, data in sheets:                       # a stack drawn anywhere serves every file
        legend = {**(proc.get("legend") or {}), **(data.get("layers") or {})}
        drawings = (data["rules"] if is_section(data) else [dict(data, rules=[])])
        for pair in [tuple(x) for x in data.get("connect") or []] + [
                p for d in drawings for p in Rule({"shapes": d.get("shapes", []), "measures": d.get("measures", []),
                                                    "constraints": [c for c in d.get("constraints", [])
                                                                    if ".layer" in c.get("expr", "") or ".net" in c.get("expr", "")],
                                                    "layers": legend}).connections()]:
            if pair not in joined and pair[::-1] not in joined:
                joined.append(pair)
    merged = []
    for p, data in sheets:
        d = dict(data)
        legend = {**(proc.get("legend") or {}), **(data.get("layers") or {})}
        columns = {**(proc.get("columns") or {}), **(data.get("domains") or {})}
        if legend:
            d["layers"] = legend
        if columns:
            d["domains"] = columns
        if proc.get("touching") and "touching" not in d:
            d["touching"] = proc["touching"]
        each = {**(proc.get("each") or {}), **(data.get("each") or {})}
        if each:                                 # families of layers a rule is repeated for
            d["each"] = each
        if joined:
            d["connect"] = [list(x) for x in joined]
        merged.append((p, d))
    return proc, merged


def split(folder) -> int:
    """Rewrite a deck so that every rule is ONE file with ONE drawing.

    A sheet (one drawn scene shared by several rules) becomes a folder named after it,
    holding a file per rule with the shapes and arrows that rule was read from. What
    the sheets said for themselves -- legend, columns, families -- and how layers
    connect (until now read from a stack drawn on some sheet) moves to process.json.
    Returns the number of rule files written; rule files are left as they are."""
    from .sheet import is_section, is_sheet, to_section
    if is_zip(folder):
        with opened(folder, save=True) as tmp:
            own = os.path.join(tmp, PROCESS_FILE)
            named = os.path.isfile(own) and "name" in json.load(open(own))
            n = split(tmp)
            if n and not named:                      # the temporary folder's name is not the deck's
                data = json.load(open(own))
                data["name"] = deck_name(folder)
                with open(own, "w") as f:
                    json.dump(data, f, indent=1)
                    f.write("\n")
        return n
    proc, merged = load(folder)
    files = rule_files(folder)                       # before rules/ is made: sheets may lie beside it
    root = os.path.join(folder, "rules")
    os.makedirs(root, exist_ok=True)
    written, shared = 0, {"legend": dict(proc.get("legend") or {}), "columns": dict(proc.get("columns") or {}),
                          "each": dict(proc.get("each") or {})}
    connect = next((d["connect"] for _, d in merged if d.get("connect")), None)
    for path in files:
        with open(path) as f:
            raw = json.load(f)
        if not is_sheet(raw):
            if os.path.dirname(os.path.abspath(path)) == os.path.abspath(folder):   # a rule file beside rules/
                os.replace(path, os.path.join(root, os.path.basename(path)))
            continue
        shared["columns"].update(raw.get("domains") or {})
        shared["each"].update(raw.get("each") or {})
        section = raw if is_section(raw) else to_section(raw)
        stem = os.path.basename(path)
        for ext in (".sheet.json", ".json"):
            if stem.endswith(ext):
                stem = stem[:-len(ext)]
                break
        target = os.path.join(root, stem)
        os.makedirs(target, exist_ok=True)
        notes = [n for n in (raw.get("except") or [])]
        for rule in section["rules"]:
            one = dict(rule)
            if notes:                                # the section's note applies to each of its rules
                one["except"] = sorted(set(notes) | set(one.get("except") or []))
            name = "".join(c if c.isalnum() or c in "-_." else "_" for c in one.get("name") or "rule")
            with open(os.path.join(target, name + ".json"), "w") as f:
                json.dump(one, f, indent=1)
                f.write("\n")
            written += 1
        os.remove(path)
    if written:
        pf = os.path.join(folder, PROCESS_FILE)
        try:
            with open(pf) as f:
                own = json.load(f)
        except (OSError, ValueError):
            own = {}
        own.setdefault("name", os.path.basename(os.path.abspath(folder)))
        for key in ("legend", "columns", "each"):
            if shared[key]:
                own[key] = shared[key]
        if connect:
            own["connect"] = connect
        with open(pf, "w") as f:
            json.dump(own, f, indent=1)
            f.write("\n")
    return written


def rules_and_warnings(sheets):
    """Every rule of the given ``[(path, sheet)]`` and what should be said about them."""
    from .backend import get
    rules, notes = [], []
    silent = get("docs")
    for path, data in sheets:
        notes += [f"{os.path.basename(path)}: {n}" for n in sheet_warnings(data)]
        for r in rules_of(data):
            name = r.get("name") or "(unnamed)"
            rules.append(r)
            if not Rule(r).connections() and any(".net" in c.get("expr", "") for c in r.get("constraints", [])):
                notes.append(f"rule {name} is about nets but nothing says how layers connect -- "
                             f"draw the stack or add \"connect\": [[layer, layer], ...]")
            notes += [f"rule {name}: {n}" for n in r.get("warnings", [])]
            if "(no checkable assertion found)" in silent.compile(Rule(r)):
                notes.append(f"rule {name} checks nothing -- no bound, cross or 'in' applies to what is drawn")
    return rules, notes


def build(folder, out_dir=None, extra=()):
    """Compile a process: ``(deck path, text path, number of rules, warnings)``. `extra`
    names further backends (plugins) whose files are written beside the deck."""
    from .backend import get
    proc, sheets = load(folder)
    name = proc.get("name") or deck_name(folder)
    rules, notes = rules_and_warnings(sheets)
    layermap = {k: tuple(v) for k, v in (proc.get("layers") or {}).items()}
    kl = get("klayout", layermap=layermap, standalone=True, title=name)
    deck = kl.compile_all(rules)
    if kl.unmapped:
        notes.append("no layer number for " + ", ".join(sorted(kl.unmapped))
                     + f" -- add to \"layers\" in {PROCESS_FILE}")
    text = f"# {name} — design rules\n\n" + get("docs").compile_all(rules)
    # absolute: KLayout resolves a report path against the deck's folder, not the caller's
    out_dir = os.path.abspath(out_dir or build_dir(folder))
    os.makedirs(out_dir, exist_ok=True)
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).lower() or "deck"
    deck_path, text_path = os.path.join(out_dir, stem + ".drc"), os.path.join(out_dir, stem + ".md")
    with open(deck_path, "w") as f:
        f.write(deck)
    with open(text_path, "w") as f:
        f.write(text)
    for name_ in extra:                              # other backends: whatever files they make
        for fname, body in get(name_, layermap=layermap, title=name).files(rules, stem).items():
            with open(os.path.join(out_dir, fname), "w") as f:
                f.write(body)
    return deck_path, text_path, len(rules), notes


def read_markers(path) -> dict:
    """``{rule: [(x0, y0, x1, y1), ...]}`` -- the bounding box (µm) of every marker in
    a KLayout report database."""
    import re
    out = {}
    text = open(path, encoding="utf-8").read()
    for cat, vals in re.findall(r"<item>.*?<category>'?([^<']*)'?</category>.*?<values>(.*?)</values>", text, re.S):
        pts = [(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+(?:e-?\d+)?),(-?[\d.]+(?:e-?\d+)?)", vals)]
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.setdefault(cat, []).append((min(xs), min(ys), max(xs), max(ys)))
    return out


def selftest(folder, out_dir=None):
    """Do the rules behave as drawn?  Builds the deck, generates test cases from the
    drawings (the ``cases`` backend), runs the deck on them in KLayout, and compares.

    Returns ``{"rules": {rule: "ok" | "wrong" | "not generated"},
    "constraints": {(rule, constraint under test): "ok" | "wrong"}, "wrong": [text, ...],
    "not_generated": [(rule, why), ...], "notes": [...], "files": {...}}``."""
    deck, _, n, notes = build(folder, out_dir, extra=("cases",))
    return _run_selftest(deck, notes)


def selftest_rules(rules, layers, out_dir, name="rules"):
    """`selftest` for rules already at hand (the sheet open in the editor, with its
    process's legend / columns / connections merged in): same result."""
    from .backend import get
    layermap = {k: tuple(v) for k, v in (layers or {}).items()}
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    deck = os.path.join(out_dir, name + ".drc")
    with open(deck, "w") as f:
        f.write(get("klayout", layermap=layermap, standalone=True, title=name).compile_all(rules))
    for fname, body in get("cases", layermap=layermap).files(rules, name).items():
        with open(os.path.join(out_dir, fname), "w") as f:
            f.write(body)
    return _run_selftest(deck, [])


def _run_selftest(deck, notes):
    import subprocess
    out_dir = os.path.dirname(deck)
    stem = os.path.splitext(os.path.basename(deck))[0]
    script, expect_path = (os.path.join(out_dir, stem + ext) for ext in (".cases.rb", ".cases.json"))
    with open(expect_path) as f:
        expect = json.load(f)
    result = {"rules": {}, "constraints": {}, "wrong": [], "not_generated": [(x["rule"], x["why"]) for x in expect["not_generated"]],
              "notes": list(notes), "files": {"deck": deck, "cases": script, "expect": expect_path}}
    if expect["layers_without_a_number"]:
        result["notes"].append("no layer number for " + ", ".join(expect["layers_without_a_number"])
                               + " -- cases on these layers cannot be seen by the deck")
    for rule, _why in result["not_generated"]:
        result["rules"][rule] = "not generated"
    if not expect["cases"]:
        return result
    klayout = klayout_binary()
    if not klayout:
        raise RuntimeError("KLayout not found -- put `klayout` on the PATH or set KLAYOUT_BIN")
    layout, report = os.path.join(out_dir, stem + ".cases.gds"), os.path.join(out_dir, stem + ".cases.lyrdb")
    cells = sorted({c.get("cell", "CASES") for c in expect["cases"]})       # most cases share one cell;
    runs = [[klayout, "-b", "-r", script, "-rd", f"out={layout}"]]           # a density has its own
    reports = {cell: (report if cell == "CASES" else os.path.join(out_dir, f"{stem}.cases.{cell}.lyrdb"))
               for cell in cells}
    runs += [[klayout, "-b", "-r", deck, "-rd", f"input={layout}", "-rd", f"topcell={cell}",
              "-rd", f"report={reports[cell]}"] for cell in cells]
    for cmd in runs:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError("KLayout failed:\n" + (p.stderr or p.stdout)[-2000:])
    markers = {cell: read_markers(path) for cell, path in reports.items()}
    result["files"].update(layout=layout, report=report)
    for c in expect["cases"]:
        x0, y0, x1, y1 = (v / 1000.0 for v in c["tile"])
        flagged = any(x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1
                      for b in markers[c.get("cell", "CASES")].get(c["rule"], []))
        good = flagged == c["expect"]
        per = result["constraints"].setdefault((c["rule"], c["testing"]), "ok")
        if not good:
            result["constraints"][(c["rule"], c["testing"])] = "wrong"
            result["wrong"].append(
                f"{c['rule']}  [{c['testing']}]  {c['case']}: the rule "
                + ("did not report it" if c["expect"] else "reported it, and should not"))
        if result["rules"].get(c["rule"]) != "wrong":
            result["rules"][c["rule"]] = "ok" if good else "wrong"
    return result


def init(folder, name=None):
    """Create an empty process folder -- or, for a name ending in ``.zip``, an empty
    deck in one file. Existing files are left alone."""
    if folder.lower().endswith(".zip"):
        if os.path.exists(folder):
            return False
        with tempfile.TemporaryDirectory(prefix="fastdrc-deck-") as tmp:
            init(tmp, name or os.path.splitext(os.path.basename(folder))[0])
            os.makedirs(os.path.dirname(os.path.abspath(folder)), exist_ok=True)
            with zipfile.ZipFile(folder, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(os.path.join(tmp, PROCESS_FILE), PROCESS_FILE)
                z.writestr("rules/", "")
        return True
    os.makedirs(os.path.join(folder, "rules"), exist_ok=True)
    pf = os.path.join(folder, PROCESS_FILE)
    if os.path.exists(pf):
        return False
    with open(pf, "w") as f:
        json.dump({"name": name or os.path.basename(os.path.abspath(folder)),
                   "layers": {}, "legend": {}, "columns": {}}, f, indent=1)
        f.write("\n")
    return True


def klayout_binary():
    """The KLayout executable: ``$KLAYOUT_BIN``, else ``klayout`` on the PATH, else None."""
    import shutil
    return os.environ.get("KLAYOUT_BIN") or shutil.which("klayout")


def read_report(path) -> dict:
    """``{rule: (number of markers, description)}`` of a KLayout report database
    (.lyrdb), in the order the rules were declared; clean rules are included with 0."""
    import re
    text = open(path, encoding="utf-8").read()
    head = text.split("<cells>")[0]
    out = {n: [0, d] for n, d in re.findall(
        r"<category>\s*<name>([^<]*)</name>\s*<description>([^<]*)</description>", head)}
    for cat in re.findall(r"<item>.*?<category>'?([^<']*)'?</category>", text, re.S):
        out.setdefault(cat, [0, ""])[0] += 1
    return {k: tuple(v) for k, v in out.items()}
