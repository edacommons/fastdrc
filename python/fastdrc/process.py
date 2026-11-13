"""A PROCESS: one folder holding everything that turns a design manual into a deck.

    myprocess/
      process.json     what is said once for the whole process
      rules/*.json     one sheet (or single rule) per section of the manual
      build/           generated: <name>.drc (the KLayout deck), <name>.md (the rules as text)

``process.json``::

    {"name": "Demo",
     "layers":  {"nwell": [1, 0], "diff": [2, 0], ...},        layer name -> GDS layer, datatype
     "legend":  {"pdiff": "diff and pimp", ...},                names for combinations
     "columns": {"thin": [["not_interacting", "thick"]], ...},  the table columns (domains)
     "connect": [["nwell", "ndiff"], ...]}                      optional; or draw the stack

A sheet may still carry its own legend / columns / connections; the process supplies
what it does not. How layers connect is collected from EVERY sheet (a stack drawn on
one sheet makes the nets for all of them) plus the process's own list.
"""

from __future__ import annotations

import glob
import json
import os

from .rule import Rule
from .sheet import rules_of, sheet_warnings

PROCESS_FILE = "process.json"


def is_process(path) -> bool:
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, PROCESS_FILE))


def rule_files(folder) -> list:
    """The rule / sheet files of a process folder (``rules/*.json``, else ``*.json`` in
    the folder itself), in name order. Files that are not drawings are left out."""
    sub = os.path.join(folder, "rules")
    paths = sorted(glob.glob(os.path.join(sub if os.path.isdir(sub) else folder, "*.json")))
    out = []
    for p in paths:
        if os.path.basename(p) == PROCESS_FILE:
            continue
        try:
            with open(p) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and ("shapes" in data or "or" in data):
            out.append(p)
    return out


def load(folder):
    """``(process, [(path, sheet), ...])`` with the process's legend, columns and
    connections merged into every sheet (a sheet's own entries win)."""
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
    for _, data in sheets:                       # a stack drawn anywhere serves every sheet
        legend = {**(proc.get("legend") or {}), **(data.get("layers") or {})}
        for pair in Rule(dict(data, layers=legend, rules=[])).connections():
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
        if joined:
            d["connect"] = [list(x) for x in joined]
        merged.append((p, d))
    return proc, merged


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
    name = proc.get("name") or os.path.basename(os.path.abspath(folder))
    rules, notes = rules_and_warnings(sheets)
    layermap = {k: tuple(v) for k, v in (proc.get("layers") or {}).items()}
    kl = get("klayout", layermap=layermap, standalone=True, title=name)
    deck = kl.compile_all(rules)
    if kl.unmapped:
        notes.append("no layer number for " + ", ".join(sorted(kl.unmapped))
                     + f" -- add to \"layers\" in {PROCESS_FILE}")
    text = f"# {name} — design rules\n\n" + get("docs").compile_all(rules)
    out_dir = out_dir or os.path.join(folder, "build")
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
    import subprocess
    deck, _, n, notes = build(folder, out_dir, extra=("cases",))
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
    """Create an empty process folder. Existing files are left alone."""
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
