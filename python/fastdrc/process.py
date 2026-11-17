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
     "grid": 0.005,                                             every vertex of every layer on this grid (µm)
     "parameters": {"top_metal": {"choices": ["9K", "30K"]}},   what is chosen once for a chip (see `params`)
     "profiles": {"thick": {"top_metal": "30K"}},               named sets of choices
     "connect": [["nwell", "ndiff"], ...]}                      optional; or draw the stack

Beside the rules a deck may carry ``layouts/*.json``: whole devices drawn by hand, each
naming the rules that must report on it (`deck_layouts`); the self-test runs them too.

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

from . import params as _params
from .rule import Rule
from .sheet import rules_of, sheet_warnings

PROCESS_FILE = "process.json"
LAYOUTS_DIR = "layouts"


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


def settled_rule(data, values, gone=()):
    """One rule file with its switches settled for the parameter `values`:
    ``(rule, is it in the deck)``. A rule is not in the deck when its ``"when"`` is
    false, when its value comes out as nothing, or when it is drawn on a legend name
    the process does not have with these parameters (`gone`)."""
    import re
    d = _params.settle(data, values)
    present = True
    if "when" in d:
        present = bool(d.pop("when"))
    single = [d.pop(k) for k in ("value",) if k in d]
    if "values" in d and not isinstance(d["values"], dict):   # a switch that gave one number
        single.append(d.pop("values"))
    for v in single:
        if v is None:
            present = False
        else:
            d["constraints"] = [dict(c, expr=c.get("expr", "").replace("?", repr(v))) for c in d.get("constraints", [])]
    if isinstance(d.get("values"), dict):
        stated = {k: v for k, v in d["values"].items() if v is not None}
        if d["values"] and not stated:
            present = False
        if stated:
            d["values"] = stated
        else:
            d.pop("values")
    if gone:
        if d.get("except"):
            d["except"] = [x for x in d["except"] if x not in gone]
        said = " ".join(c.get("expr", "") for c in d.get("constraints", []))
        if set(re.findall(r"[A-Za-z_]\w*", said)) & set(gone):
            present = False
    return d, present


def load(folder, profile=None, params=None, absent=False):
    """``(process, [(path, sheet), ...])`` with the process's legend, columns and
    connections merged into every sheet (a sheet's own entries win). For a zipped
    deck the paths read ``mydeck.zip/rules/...``.

    A deck with parameters is loaded for ONE choice of them -- `profile` (a name from
    its ``"profiles"``) and `params` (``{name: value}`` or ``["name=value"]``) over
    the defaults: every switch is settled, and a rule that is not in the deck for that
    choice is left out (`absent` keeps it in the list, marked ``"_absent"``). The
    choice is ``process["_values"]``."""
    if is_zip(folder):
        with opened(folder) as tmp:
            proc, merged = load(tmp, profile, params, absent)
        return proc, [(os.path.join(folder, os.path.relpath(p, tmp)), d) for p, d in merged]
    proc = {}
    pf = os.path.join(folder, PROCESS_FILE)
    if os.path.isfile(pf):
        with open(pf) as f:
            proc = json.load(f)
    values = _params.resolve(proc, profile, params)
    kept = {k: proc[k] for k in ("parameters", "profiles") if k in proc}
    proc = _params.settle({k: v for k, v in proc.items() if k not in kept}, values)
    gone = {k for k, v in (proc.get("legend") or {}).items() if v is None}
    if gone:                                     # names the process does not have with these parameters
        proc["legend"] = {k: v for k, v in proc["legend"].items() if v is not None}
    proc.update(kept, _values=values)
    sheets = []
    for p in rule_files(folder):
        with open(p) as f:
            data = json.load(f)
        if isinstance(data.get("rules"), list):  # an older sheet of several rules: its switches only
            sheets.append((p, _params.settle(data, values)))
            continue
        data, present = settled_rule(data, values, gone)
        if present or absent:
            sheets.append((p, data if present else dict(data, _absent=True)))
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
    if proc.get("grid") and not absent:          # "grid": every vertex of every layer on it
        merged += grid_rules(folder, proc)
    return proc, merged


def grid_rules(folder, proc) -> list:
    """``[(path, rule)]``: one check per layer of the process -- every vertex on the
    process's ``"grid"`` (µm). They are the section ``Grid``, named ``GRID.<layer>``."""
    v = proc["grid"]
    box = [[0, 0], [8, 0], [8, 6], [0, 6]]
    out = []
    for layer in proc.get("layers") or {}:
        name = f"GRID.{layer}"
        out.append((os.path.join(folder, "rules", "Grid", name + ".json"), {
            "name": name, "desc": f"{name} : every vertex of {layer} must be on the {v:g} µm grid.", "grid": v,
            "shapes": [{"name": "A", "edges": [{"id": f"a_{i}", "start": box[i], "end": box[(i + 1) % 4], "edge": False}
                                               for i in range(4)]}],
            "measures": [], "constraints": [{"kind": "match", "expr": f"A.layer == {layer}"}]}))
    return out


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


def _patterns(text):
    """``"NW.*, 3-metal"`` (or a list of such) -> ``["NW.*", "3-metal"]``."""
    if not text:
        return []
    items = [text] if isinstance(text, str) else list(text)
    return [p.strip() for item in items for p in item.split(",") if p.strip()]


def _section_in_path(path):
    parts = os.path.normpath(path).split(os.sep)
    if "rules" in parts[:-1]:
        at = len(parts) - 2 - parts[-2::-1].index("rules")
        return "/".join(parts[at + 1:-1])
    return ""


def chosen(name, section, only=None, skip=None, hit=None) -> bool:
    """Is the rule `name` of `section` in the set being built?  `only` / `skip` are
    patterns (``*`` and ``?`` as in file names) matched against the rule's name, its
    section, or ``section/name``; `only` empty means every rule. `hit` collects the
    patterns that matched something."""
    import fnmatch

    def matching(patterns):
        found = [p for p in patterns
                 if any(fnmatch.fnmatchcase(x, p) for x in (name, section, f"{section}/{name}") if x)]
        if hit is not None:
            hit.update(found)
        return found
    only, skip = _patterns(only), _patterns(skip)
    wanted = bool(matching(only)) if only else True          # both are asked, so both are recorded
    return not matching(skip) and wanted


def rules_and_warnings(sheets, only=None, skip=None):
    """Every rule of the given ``[(path, sheet)]`` and what should be said about them.
    With `only` / `skip` (see `chosen`): the rules of that set."""
    from .backend import get
    rules, notes = [], []
    silent = get("docs")
    hit, total = set(), 0
    for path, data in sheets:
        section = _section_in_path(path)
        picked = [r for r in rules_of(data)]
        total += len(picked)
        picked = [r for r in picked if chosen(r.get("name") or "", section, only, skip, hit)]
        if not picked:
            continue
        notes += [f"{os.path.basename(path)}: {n}" for n in sheet_warnings(data)]
        for r in picked:
            name = r.get("name") or "(unnamed)"
            rules.append(r)
            if not Rule(r).connections() and any(".net" in c.get("expr", "") for c in r.get("constraints", [])):
                notes.append(f"rule {name} is about nets but nothing says how layers connect -- "
                             f"draw the stack or add \"connect\": [[layer, layer], ...]")
            notes += [f"rule {name}: {n}" for n in r.get("warnings", [])]
            if "(no checkable assertion found)" in silent.compile(Rule(r)):
                notes.append(f"rule {name} checks nothing -- no bound, cross or 'in' applies to what is drawn")
    for p in _patterns(only) + _patterns(skip):
        if p not in hit:
            notes.append(f"no rule or section is called {p!r}")
    return rules, notes


def build(folder, out_dir=None, extra=(), only=None, skip=None, optimise=True, backend="klayout",
          deep=False, profile=None, params=None):
    """Compile a process: ``(deck path, text path, number of rules, warnings)``. `extra`
    names further backends (plugins) whose files are written beside the deck.

    `only` / `skip` choose the rules (see `chosen`): the deck and the text hold that set
    alone, and only the layers it needs are read. With `optimise`, the chosen rules
    are one graph (ir.py): what several of them need is computed once, so the script is
    fitted to the set.

    `backend` names the backend that compiles the deck and will run it (a plugin, see
    `fastdrc.backend`); KLayout unless said otherwise.

    `deep`: the deck works on the layout's hierarchy where its backend can (KLayout's
    deep mode): a cell is checked once however often it is placed, and a violation is
    reported once, in the cell it lies in. The same places are found as without it.

    `profile`, `params`: the choice of the deck's parameters it is built for (see `load`)."""
    from .backend import get, runner
    proc, sheets = load(folder, profile, params)
    name = proc.get("name") or deck_name(folder)
    rules, notes = rules_and_warnings(sheets, only, skip)
    layermap = {k: tuple(v) for k, v in (proc.get("layers") or {}).items()}
    kl = runner(backend, layermap=layermap, standalone=True, title=name, optimise=optimise, deep=deep)
    deck = kl.compile_all(rules)
    notes += getattr(kl, "problems", [])
    about = []                                       # what this deck is, for its first lines
    if _params.chosen(proc):
        about.append("built for " + _params.said(proc["_values"], proc))
    if _patterns(only) or _patterns(skip):
        every = sum(len(rules_of(d)) for _, d in sheets)
        said = "; ".join(x for x in ("only " + ", ".join(_patterns(only)) if _patterns(only) else "",
                                     "without " + ", ".join(_patterns(skip)) if _patterns(skip) else "") if x)
        about.append(f"{len(rules)} of the deck's {every} rules: {said}")
    deck = kl.headed(deck, about)
    if getattr(kl, "unmapped", None):
        notes.append("no layer number for " + ", ".join(sorted(kl.unmapped))
                     + f" -- add to \"layers\" in {PROCESS_FILE}")
    text = f"# {name} — design rules\n\n" + get("docs").compile_all(rules)
    # absolute: a backend may resolve a path against the deck's folder, not the caller's
    out_dir = os.path.abspath(out_dir or build_dir(folder))
    os.makedirs(out_dir, exist_ok=True)
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).lower() or "deck"
    deck_path, text_path = os.path.join(out_dir, stem + kl.extension), os.path.join(out_dir, stem + ".md")
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
    from .backend import get
    return {rule: [b for b in boxes if b] for rule, (_, boxes) in get("klayout").read(path).items()
            if any(boxes)}


def selftest(folder, out_dir=None, only=None, skip=None, optimise=True, backend="klayout", deep=False,
             profile=None, params=None):
    """Do the rules behave as drawn?  Builds the deck, generates test cases from the
    drawings (the ``cases`` backend), runs the deck on them with its backend, and compares.

    Returns ``{"rules": {rule: "ok" | "wrong" | "not generated"},
    "constraints": {(rule, constraint under test): "ok" | "wrong"}, "wrong": [text, ...],
    "not_generated": [(rule, why), ...], "stray": {rule: markers outside every test case},
    "notes": [...], "files": {...}}``."""
    deck, _, n, notes = build(folder, out_dir, extra=("cases",), only=only, skip=skip, optimise=optimise,
                              backend=backend, deep=deep, profile=profile, params=params)
    proc, sheets = load(folder, profile, params)
    whole = [r for _, data in sheets for r in rules_of(data)
             if any(w is None for _, _, _, w in Rule(r).density_bounds())]
    result = _run_selftest(deck, notes, backend, whole)
    _run_layouts(folder, deck, backend, result, {r.get("name") for r in whole}, proc.get("_values") or {})
    return result


def deck_layouts(folder) -> list:
    """``[(name, layout)]``: the layouts a deck carries, ``layouts/*.json`` -- devices and
    other pieces of layout drawn by hand, each saying what the deck must find on it::

        {"desc": "an LDNMOS finger, its guard ring left open",
         "shapes": [{"layer": "comp", "box": [0, 0, 3, 10]},          µm: x0, y0, x1, y1
                    {"layer": "metal1", "points": [[0, 0], [8, 0], [8, 2]]}],
         "reports": ["MDN.17"],        the rules that must report it; every other rule must be silent
         "when": {"switch": "mim", "cases": {"B": true}, "default": false},   optional: for which choice of the deck's parameters
         "not_judged": ["MDP.3c"]}     rules whose answer on this layout is not known

    A rule's own drawing shows the rule; a layout shows a whole device, where EVERY rule
    of the deck gets its say -- a rule drawn too broadly reports here."""
    if is_zip(folder):
        with opened(folder) as tmp:
            return deck_layouts(tmp)
    out = []
    for path in sorted(glob.glob(os.path.join(folder, LAYOUTS_DIR, "*.json")), key=lambda q: _natural(os.path.basename(q))):
        with open(path) as f:
            out.append((os.path.basename(path)[:-5], json.load(f)))
    return out


def _run_layouts(folder, deck, backend, result, whole=(), values=None):
    """Run the built deck on the deck's own layouts (`deck_layouts`) and add what it finds
    to a self-test's `result`: ``result["layouts"] = {name: "ok" | "wrong"}``, and a line
    in ``result["wrong"]`` for each rule that reports where it should not or does not
    where it should."""
    import re
    import subprocess

    from .backend import runner
    # a layout may say `"when"` it applies, as a rule does (a capacitor of one MIM option)
    layouts = [(n, lay) for n, lay in deck_layouts(folder)
               if "when" not in lay or _params.settle(lay["when"], values or {})]
    result["layouts"] = {}
    if not layouts:
        return
    klayout = klayout_binary()
    if not klayout:
        raise RuntimeError("KLayout not found -- put `klayout` on the PATH or set KLAYOUT_BIN "
                           "(it writes the test layout)")
    with (opened(folder) if is_zip(folder) else contextlib.nullcontext(folder)) as base:
        with open(os.path.join(base, PROCESS_FILE)) as f:
            numbers = json.load(f).get("layers") or {}
    in_deck = set(re.findall(r'\.output\("([^"]+)"', open(deck, encoding="utf-8").read()))
    # `whole`: rules about the whole die (a coverage over the layout's extent) say nothing of one device
    out_dir, stem = os.path.dirname(deck), os.path.splitext(os.path.basename(deck))[0]
    rb = ["# Generated by FastDRC: the deck's own layouts, side by side.", "ly = RBA::Layout.new", "ly.dbu = 0.001",
          "top = ly.create_cell('LAYOUTS')",
          "put = lambda { |l, d, pts| top.shapes(ly.layer(l, d)).insert(RBA::Polygon.new(pts.map { |x, y| RBA::Point.new(x, y) })) }"]
    x, tiles = 0, []
    for name, lay in layouts:
        polys = []
        for sh in lay.get("shapes", []):
            if sh["layer"] not in numbers:
                result["notes"].append(f"layout {name}: no layer number for {sh['layer']}")
                continue
            if "box" in sh:
                x0, y0, x1, y1 = sh["box"]
                pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            else:
                pts = sh["points"]
            polys.append((numbers[sh["layer"]], [(round(a * 1000), round(b * 1000)) for a, b in pts]))
        if not polys:
            continue
        xs, ys = [q[0] for _, pts in polys for q in pts], [q[1] for _, pts in polys for q in pts]
        pad = 50_000                                   # further than any rule of a process reaches
        dx = x - min(xs) + pad
        dx -= dx % 1000                                # on every grid a process is likely to have
        for (l, d), pts in polys:
            rb.append(f"put.call({l}, {d}, {json.dumps([[q[0] + dx, q[1]] for q in pts])})   # {name}")
        tiles.append((name, lay, (min(xs) + dx - pad / 2) / 1000.0, (max(xs) + dx + pad / 2) / 1000.0))
        x = max(xs) + dx + pad
    rb.append("ly.write($out)")
    script, gds, report = (os.path.join(out_dir, stem + ext) for ext in (".layouts.rb", ".layouts.gds", ".layouts.lyrdb"))
    with open(script, "w") as f:
        f.write("\n".join(rb) + "\n")
    p = subprocess.run([klayout, "-b", "-r", script, "-rd", f"out={gds}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("KLayout failed:\n" + (p.stderr or p.stdout)[-2000:])
    found = {rule: [b for b in boxes if b] for rule, (_, boxes) in
             runner(backend).run(deck, gds, top="LAYOUTS", report=report)["rules"].items()}
    result["files"].update(layouts=gds, layouts_report=report)
    named = lambda rule, listed: any(rule == n or rule.startswith(n + "_") for n in listed)   # noqa: E731  (a column's rule)
    for name, lay, x0, x1 in tiles:
        here = {rule for rule, boxes in found.items() if any(x0 <= (b[0] + b[2]) / 2 <= x1 for b in boxes)
                and not any(rule == w or rule.startswith(w + "_") for w in whole)}
        must, skip = lay.get("reports") or [], lay.get("not_judged") or []
        lines = [f"layout {name}: {n} did not report it" for n in must
                 if any(named(r, [n]) for r in in_deck) and not any(named(r, [n]) for r in here)]
        lines += [f"layout {name}: {r} reported it, and should not" for r in sorted(here)
                  if not named(r, must) and not named(r, skip)]
        result["layouts"][name] = "wrong" if lines else "ok"
        result["wrong"] += lines


def selftest_rules(rules, layers, out_dir, name="rules", backend="klayout"):
    """`selftest` for rules already at hand (the sheet open in the editor, with its
    process's legend / columns / connections merged in): same result."""
    from .backend import get, runner
    layermap = {k: tuple(v) for k, v in (layers or {}).items()}
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    eng = runner(backend, layermap=layermap, standalone=True, title=name)
    deck = os.path.join(out_dir, name + eng.extension)
    with open(deck, "w") as f:
        f.write(eng.compile_all(rules))
    for fname, body in get("cases", layermap=layermap).files(rules, name).items():
        with open(os.path.join(out_dir, fname), "w") as f:
            f.write(body)
    return _run_selftest(deck, [], backend)


def _run_selftest(deck, notes, backend="klayout", rules_whole_die=()):
    import subprocess

    from .backend import RunError, runner
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
    eng = runner(backend)
    if eng.ready():
        raise RunError(eng.ready())
    # the case layout is WRITTEN by a KLayout script (the `cases` backend), whichever backend reads it
    klayout = klayout_binary()
    if not klayout:
        raise RuntimeError("KLayout not found -- put `klayout` on the PATH or set KLAYOUT_BIN "
                           "(it writes the test layout)")
    layout, report = os.path.join(out_dir, stem + ".cases.gds"), os.path.join(out_dir, stem + ".cases.lyrdb")
    p = subprocess.run([klayout, "-b", "-r", script, "-rd", f"out={layout}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("KLayout failed:\n" + (p.stderr or p.stdout)[-2000:])
    cells = sorted({c.get("cell", "CASES") for c in expect["cases"]})       # most cases share one cell;
    reports = {cell: (report if cell == "CASES" else os.path.join(out_dir, f"{stem}.cases.{cell}.lyrdb"))
               for cell in cells}                                           # a density has its own
    markers = {cell: {rule: [b for b in boxes if b] for rule, (_, boxes) in
                      eng.run(deck, layout, top=cell, report=path)["rules"].items()}
               for cell, path in reports.items()}
    result["files"].update(layout=layout, report=report)
    # a marker in no case's tile is a rule reporting where nothing was drawn for it
    tiles = {}
    for c in expect["cases"]:
        tiles.setdefault(c.get("cell", "CASES"), []).append([v / 1000.0 for v in c["tile"]])
    stray = {}
    whole = {r.get("name") for r in rules_whole_die}
    for cell, found in markers.items():
        for rule, boxes in found.items():
            if any(rule == w or rule.startswith(w + "_") for w in whole):
                continue                                # about the whole die: its marker is the layout's extent
            n = sum(1 for b in boxes if not any(x0 <= (b[0] + b[2]) / 2 <= x1 and y0 <= (b[1] + b[3]) / 2 <= y1
                                                for x0, y0, x1, y1 in tiles.get(cell, [])))
            if n:
                stray[rule] = stray.get(rule, 0) + n
    result["stray"] = stray
    if stray:
        result["notes"].append("reported outside every test case: "
                               + ", ".join(f"{r} ({n})" for r, n in sorted(stray.items())))
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
    from .backend import get
    return get("klayout").binary()


def read_report(path) -> dict:
    """``{rule: (number of markers, description)}`` of a KLayout report database
    (.lyrdb), in the order the rules were declared; clean rules are included with 0."""
    from .backend import get
    return {rule: (len(boxes), desc) for rule, (desc, boxes) in get("klayout").read(path).items()}
