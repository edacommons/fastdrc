#!/usr/bin/env python3
"""FastDRC rule editor — all-Python (Tkinter, stdlib only).

A rule is ONE primitive family: polygons whose every side is STRAIGHT or WAVY.
  * The polygon interior is the layer's material (stippled).
  * STRAIGHT side = a real edge the rule is about (matched / measured).
  * WAVY side     = "material continues past here" — open / don't-care boundary.

You build any orthogonal shape from a rect by editing it:
  * Select : click a side to select it (toggle straight/wavy), or drag a side to
             move it (resize; auto-inserts risers -> a rectangular extension when
             the neighbour is collinear). Drag the interior to move the shape.
  * Rect   : drag a box = a material polygon (all sides wavy).
  * Split  : click a side to drop a vertex on it (divide into two sub-sides).
  * Measure: shift-click two sides. Two STRAIGHT sides = a distance (a variable you
             bound in a constraint). With a WAVY side = a RELATION between the two
             shapes (inside / overlapping / touching / apart, read off the drawing):
             a filter as it stands, or -- with "cross" set -- the rule's check: that
             relation must never exist.

Sides have stable ids, so measures (which reference sides) survive edits.

Run:  fastdrc gui [rule.json]      (or:  python -m fastdrc.editor [rule.json])
"""

from __future__ import annotations

import json
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, colorchooser

from . import topology

CELL = 26
COLORS = ["#3aa0ff", "#ff6b6b", "#5cd6a0", "#c792ea", "#ffd166", "#f78c6c"]
CMPS = ["<", "<=", "=", ">=", ">"]
INK, BG, PANEL, ACCENT = "#e6e6e6", "#0f1115", "#181b22", "#ffd166"
MATCH, CHECK = "#ffd633", "#ff6b6b"   # ? = match (yellow), ! = check (red)
HATCH_ANGLES = [45, 135, 18, 108, 72, 162]  # per-layer hatch direction (see-through)
SPLITCOL = "#c792ea"                         # split-dot color
HOLECOL = "#9aa3b2"                          # hole (cutout) boundary color
FADED = "#39404f"                             # what belongs to other rules of the sheet
MEASURECOL = "#e8b84b"                        # measure (declared variable) color
RELCOL = "#7fb6c9"                            # relation (a measure with a wavy end) color


def seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def point_in_poly(px, py, poly):
    inside, n, j = False, len(poly), len(poly) - 1
    for i in range(n):
        xi, yi = poly[i]; xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


# Keys of the rule JSON the editor itself models. Everything else on a shape or a
# measure (crossed / metric / edges, flagged / halo / hole_loops, ...) is carried in
# an "extra" dict and written back unchanged, so opening and saving a rule never
# loses part of it -- including keys this editor has no control for yet.
_SHAPE_KEYS = ("name", "color", "cut", "edges")
_MEASURE_KEYS = ("name", "from", "to", "label_offset", "label_side")
METRICS = ["(default)", "euclidian", "projection", "square"]


def model_from_json(o, nid, next_name):
    """Rule JSON -> the editor's model ``(meta, shapes, measures, constraints)``."""
    meta = {k: v for k, v in o.items() if k not in ("shapes", "measures", "constraints")}
    shapes, measures = [], []

    def loop(edges):
        verts = [[e["start"][0], e["start"][1]] for e in edges]
        sides = [{"id": e.get("id") or nid("e"),
                  "kind": "straight" if e.get("edge") else "wavy"} for e in edges]
        return verts, sides

    for sh in o.get("shapes", []):
        verts, sides = loop(sh.get("edges", []))
        owner = {"id": nid("s"), "name": sh.get("name") or next_name(shapes),
                 "color": sh.get("color", ""),
                 "verts": verts, "sides": sides, "cut": bool(sh.get("cut")),
                 "extra": {k: v for k, v in sh.items() if k not in _SHAPE_KEYS}}
        shapes.append(owner)
        # a drawn VOID (the hole of a donut) is edited as a hole shape of its own that
        # sits inside its owner; it is written back as the owner's `hole_loops`
        for k, hl in enumerate(owner["extra"].pop("hole_loops", None) or []):
            hv, hs = loop(hl)
            shapes.append({"id": nid("s"), "name": f"{owner['name']}.void{k + 1}", "color": "",
                           "verts": hv, "sides": hs, "cut": True, "extra": {}})
    for m in o.get("measures", []):
        measures.append({"id": nid("m"), "var": m.get("name", "X"),
                         "from": m.get("from"), "to": m.get("to"),
                         "loff": m.get("label_offset", 0),
                         "lside": m.get("label_side", -1),
                         "extra": {k: v for k, v in m.items() if k not in _MEASURE_KEYS}})
    constraints = [dict(c) if isinstance(c, dict) else {"kind": "check", "expr": c}
                   for c in o.get("constraints", []) if c]
    for c in constraints:
        c.setdefault("kind", "check"); c.setdefault("expr", "")
    return meta, shapes, measures, constraints


def parse_legend(text):
    """`pcomp = comp and pplus; gate = poly2 and comp` -> {"pcomp": "comp and pplus", ...}"""
    out = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


def format_legend(d):
    return "; ".join(f"{k} = {v}" for k, v in (d or {}).items())


def parse_columns(text):
    """`3.3V: not_interacting v5_xtor, not_interacting dualgate | 5V: overlapping dualgate`
    -> the sheet's "domains" (a table's columns, each named by where it applies)."""
    out = {}
    for part in text.split("|"):
        if ":" not in part:
            continue
        name, conds = part.split(":", 1)
        pairs = [c.split() for c in conds.split(",")]
        if name.strip():
            out[name.strip()] = [[c[0], c[1]] for c in pairs if len(c) == 2]
    return out


def format_columns(d):
    return " | ".join(f"{k}: " + ", ".join(f"{op} {layer}" for op, layer in v)
                      for k, v in (d or {}).items())


def format_rule_constraints(cons):
    """A rule's constraints on one line: `match K.rows >= 4; S >= 0.3` -- a filter is
    marked `match`, a check needs no mark."""
    return "; ".join(("match " if c.get("kind") == "match" else "") + c.get("expr", "") for c in cons or [])


def parse_rule_constraints(text):
    """The reverse: parts separated by `;`, each a check unless it starts with `match`
    (or, spelled out, `check`)."""
    out = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        kind = "check"
        for word in ("match", "check"):
            if part.startswith(word + " "):
                kind, part = word, part[len(word):].strip()
        out.append({"kind": kind, "expr": part})
    return out


def parse_layers(text):
    """One layer per line (or `;`): `nwell 1/0` -> {"nwell": [1, 0]}. A missing datatype
    is 0; lines that do not read as a name and a number are left out."""
    out = {}
    for part in text.replace(";", "\n").splitlines():
        words = part.replace("=", " ").replace(",", " ").split()
        if len(words) < 2:
            continue
        nums = words[1].split("/") if "/" in words[1] else words[1:3]
        try:
            out[words[0]] = [int(nums[0]), int(nums[1]) if len(nums) > 1 else 0]
        except ValueError:
            pass
    return out


def format_layers(d):
    return "\n".join(f"{k} {v[0]}/{v[1]}" for k, v in (d or {}).items())


def parse_values(text):
    """`3.3V=0.22, 5V=0.3` -> {"3.3V": 0.22, "5V": 0.3}; a switch on a parameter is written
    as it is in the file: {"switch": ..., "cases": {...}}"""
    if text.strip().startswith("{"):
        import json
        try:
            return json.loads(text)
        except ValueError:
            return {}
    out = {}
    for part in text.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = float(v)
            except ValueError:
                pass
    return out


def format_values(d):
    from .params import has_switch
    if d is not None and (not isinstance(d, dict) or has_switch(d)):   # depends on a parameter of the deck
        import json
        return json.dumps(d)
    return ", ".join(f"{k}={v:g}" for k, v in (d or {}).items())


def parse_names(text):
    return [w for w in text.replace(",", " ").split() if w]


def void_owner(hole, shapes):
    """The material shape a hole shape is the VOID of: the smallest one it lies
    inside (None if it lies in none)."""
    best = None
    for s in shapes:
        if s.get("cut") or s is hole or len(s["verts"]) < 3 or len(hole["verts"]) < 3:
            continue
        if topology.relate(hole["verts"], s["verts"]) == "inside":
            xs, ys = [v[0] for v in s["verts"]], [v[1] for v in s["verts"]]
            size = (max(xs) - min(xs)) * (max(ys) - min(ys))
            if best is None or size < best[0]:
                best = (size, s)
    return best[1] if best else None


def model_to_json(meta, shapes, measures, constraints):
    """The editor's model -> rule JSON. A shape is a LOOP OF EDGES, each
    {id,start,end,edge:bool}; a measure references an edge by its stable id."""
    def edges(s):
        v, n = s["verts"], len(s["verts"])
        return [{"id": sd["id"], "start": list(v[ei]), "end": list(v[(ei + 1) % n]),
                 "edge": sd["kind"] == "straight"} for ei, sd in enumerate(s["sides"])]

    out_shapes, by_id = [], {}
    for s in shapes:
        if s.get("cut") and void_owner(s, shapes):
            continue                         # written below, as its owner's void
        sh = {"name": s.get("name", "")}     # name only; layer/net go in constraints (A.layer, A.net)
        if s.get("color"):
            sh["color"] = s["color"]         # optional per-shape display color
        if s.get("cut"):
            sh["cut"] = True                 # a hole lying in no shape: kept as it is
        sh["edges"] = edges(s)
        sh.update(s.get("extra") or {})
        out_shapes.append(sh)
        by_id[s["id"]] = sh
    for s in shapes:
        owner = s.get("cut") and void_owner(s, shapes)
        if owner:
            by_id[owner["id"]].setdefault("hole_loops", []).append(edges(s))
    out_measures = []
    for m in measures:
        md = {"name": m["var"], "from": m["from"], "to": m["to"]}
        if m.get("loff"):
            md["label_offset"] = round(m["loff"], 3)    # label offset along its line (grid units)
        if m.get("lside", -1) == 1:                     # default side is -1 (above/left)
            md["label_side"] = 1                        # flipped to below/right
        md.update(m.get("extra") or {})
        out_measures.append(md)
    out = {**meta, "shapes": out_shapes, "measures": out_measures}
    if constraints:
        out["constraints"] = [{"kind": c["kind"], "expr": c["expr"]} for c in constraints]
    return out


def _backends():
    """Names of the text backends, importing the modules that register them."""
    from . import backend, backend_docs, backend_klayout   # noqa: F401
    return backend.available()


class Editor:
    def __init__(self, root):
        self.root = root
        root.title("FastDRC rule editor")
        self.shapes, self.measures, self.constraints = [], [], []
        # view: pixel = grid * cell + (ox, oy). Default is CELL px per grid unit at the
        # origin; opening a rule (or Fit) rescales/centres it in the canvas.
        self.cell, self.ox, self.oy, self._fit_pending = CELL, 0, 0, False
        self.meta = {}                         # non-drawing rule keys (name, desc, ...) kept across load/save
        # a rule with several alternative CASES ("or": each a complete drawing): the
        # canvas shows one case at a time; `cases` holds them all as rule JSON.
        self.cases, self.case, self.top_meta = None, 0, {}
        self._roles = {}                       # shape name -> how the compiler reads it
        self._focus = None                     # (shape names, measure names) of the selected rule
        self.sel, self._newk = None, "check"   # _newk = kind for the next new constraint
        self._dir = os.getcwd()                # Save/Open start in the launch dir, then last-used
        self._path = None                      # the file being edited (Save writes here)
        self._process = None                   # the process folder it belongs to, if any
        self._zip = None                       # the deck's zip file, when it is edited from one
        self._ctx = {}                         # what the process says once: legend, columns, connect, layers
        self.pending, self.drag, self.moving, self.ids = None, None, None, 1
        self._suppress_tree = False
        self._label_meta = {}                  # measure id -> (label bbox, axis) for label drag

        # (tree | canvas | side panel) over a rule-text pane, separated by draggable sashes
        outer = tk.PanedWindow(root, orient="vertical", sashwidth=6,
                               sashrelief="raised", bg="#2b313f", bd=0, opaqueresize=True)
        outer.pack(fill="both", expand=True)
        paned = tk.PanedWindow(outer, orient="horizontal", sashwidth=6,
                               sashrelief="raised", bg="#2b313f", bd=0, opaqueresize=True)
        outer.add(paned, minsize=240, stretch="always")
        text_frame = tk.Frame(outer, bg=PANEL)
        outer.add(text_frame, minsize=60, height=150, stretch="never")
        self._build_text(text_frame)

        tree_frame = tk.Frame(paned, bg=PANEL)
        style = ttk.Style()
        try:
            style.theme_use("clam")             # aqua ignores Treeview colors; clam honors them
        except tk.TclError:
            pass
        style.configure("FD.Treeview", background="#0b0d12", fieldbackground="#0b0d12",
                        foreground=INK, rowheight=20, borderwidth=0)
        style.map("FD.Treeview",                # readable selection: white text on a blue row
                  background=[("selected", "#2d5a88")], foreground=[("selected", "#ffffff")])
        # the PROCESS this file belongs to: its sheets, one click apart
        self.proc_frame = tk.Frame(tree_frame, bg=PANEL)
        self.proc_title = tk.Label(self.proc_frame, text="", bg=PANEL, fg="#8b93a7", anchor="w",
                                   font=("TkDefaultFont", 9, "bold"))
        self.proc_title.pack(fill="x", padx=6, pady=(6, 2))
        # one dropdown for the whole deck: its sections (folders), and under each its rules (files)
        self.rule_pick = tk.Menubutton(self.proc_frame, text="", relief="raised", anchor="w", bg="#0b0d12", fg=INK,
                                       activebackground="#2d5a88", activeforeground="#ffffff", padx=6, pady=3)
        self.rule_pick.pack(fill="x", padx=4)
        self.rule_menu = tk.Menu(self.rule_pick, tearoff=0)
        self.rule_pick.config(menu=self.rule_menu)
        self._proc_rules = {}                          # sheet path -> [(rule name, description)]
        bar = tk.Frame(self.proc_frame, bg=PANEL)
        bar.pack(fill="x", padx=4, pady=4)
        tk.Button(bar, text="+ rule", command=self.new_rule).pack(side="left")
        tk.Button(bar, text="Process…", command=self.edit_process).pack(side="left", padx=4)
        self._proc_files = []
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse", style="FD.Treeview")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self._drag_item = None                 # drag-to-reorder within a category
        self.tree.bind("<ButtonPress-1>", self.on_tree_press, add="+")
        self.tree.bind("<B1-Motion>", self.on_tree_drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self.on_tree_release, add="+")
        paned.add(tree_frame, minsize=140, width=190, stretch="never")

        self.canvas = tk.Canvas(paned, bg=BG, highlightthickness=0, width=560, height=640)
        paned.add(self.canvas, minsize=240, stretch="always")

        side = tk.Frame(paned, bg=PANEL, width=300)
        paned.add(side, minsize=240, width=300, stretch="never")
        self._build_side(side)

        # one direct-manipulation mode (no tools): plain mouse edits geometry,
        # right-click splits, shift-click measures.
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        self.canvas.bind("<Shift-ButtonPress-1>", self.on_measure)
        self.canvas.bind("<ButtonPress-3>", self.on_split)
        self.canvas.bind("<Control-ButtonPress-1>", self.on_split)   # mac trackpad ctrl-click
        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Leave>", lambda e: self.canvas.config(cursor=""))
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        # pick cursor names that this platform accepts (portable X names first)
        self.cur_move = self._pick_cursor("fleur", "hand2")
        self.cur_h = self._pick_cursor("sb_h_double_arrow", "size_we")
        self.cur_v = self._pick_cursor("sb_v_double_arrow", "size_ns")
        self.canvas.config(cursor="")
        root.bind("<Delete>", self.on_del)
        root.bind("<BackSpace>", self.on_del)
        root.bind("h", self.toggle_hole)
        root.bind("H", self.toggle_hole)
        self.redraw()

    # ---- side panel: a toolbar + one context-sensitive Properties table ----
    def _build_side(self, side):
        tb = tk.Frame(side, bg=PANEL)
        tb.pack(fill="x", padx=8, pady=6)
        for txt, cmd in (("Open", self.open), ("Save", self.save), ("Save as", self.save_as),
                         ("New", self.clear), ("Fit", self.fit)):
            tk.Button(tb, text=txt, command=cmd).pack(side="left", padx=2)
        self.case_bar = tk.Frame(side, bg=PANEL)
        self.case_bar.pack(fill="x", padx=8, pady=(0, 4))
        self._refresh_cases()
        tk.Label(side, text="PROPERTIES", bg=PANEL, fg="#8b93a7", anchor="w",
                 font=("TkDefaultFont", 9, "bold")).pack(fill="x", padx=10, pady=(6, 2))
        self.props = tk.Frame(side, bg=PANEL)
        self.props.pack(fill="both", expand=True, padx=10)
        self.props.columnconfigure(1, weight=1)

    # ---- bottom pane: the drawn rule compiled to text by a backend, kept live ----
    def _build_text(self, frame):
        self._text_job, self._text_shown = None, None
        bar = tk.Frame(frame, bg=PANEL)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        tk.Label(bar, text="RULE TEXT", bg=PANEL, fg="#8b93a7",
                 font=("TkDefaultFont", 9, "bold")).pack(side="left")
        self._backend = tk.StringVar(value="docs")
        try:
            names = _backends()
        except Exception:                      # reported in the pane by _update_text
            names = ["docs"]
        tk.OptionMenu(bar, self._backend, *names,
                      command=lambda _: self._update_text()).pack(side="right")
        tk.Label(bar, text="backend", bg=PANEL, fg="#8b93a7").pack(side="right", padx=(0, 4))
        tk.Button(bar, text="Test", command=self.run_selftest).pack(side="left", padx=10)
        sb = tk.Scrollbar(frame, orient="vertical")
        sb.pack(side="right", fill="y")
        self.text = tk.Text(frame, height=6, wrap="word", bg="#0b0d12", fg=INK, bd=0,
                            highlightthickness=0, padx=8, pady=4, font="TkFixedFont",
                            state="disabled", yscrollcommand=sb.set)
        self.text.pack(fill="both", expand=True)
        sb.config(command=self.text.yview)
        self.text.tag_configure("err", foreground=CHECK)
        self.text.tag_configure("ok", foreground="#7fd68a")
        self.text.bind("<Button-1>", lambda e: self.text.focus_set())   # select + copy

    def selftest_report(self):
        """Do the rules of this sheet behave as drawn?  Lines ``(text, tag)`` for the
        text pane: every constraint tested, what went wrong, what could not be tested."""
        import tempfile

        from . import process
        from .sheet import rules_of
        data = self.in_process(self.serialize())
        if not data.get("shapes") and not data.get("or"):
            return [("(nothing drawn yet)", "")]
        rules = rules_of(data)
        with tempfile.TemporaryDirectory() as tmp:
            res = process.selftest_rules(rules, self._ctx.get("layers"), tmp)
        lines = []
        for (rule, testing), state in res["constraints"].items():
            lines.append((f"{'ok   ' if state == 'ok' else 'WRONG'}  {rule}   {testing}", "ok" if state == "ok" else "err"))
        for text in res["wrong"]:
            lines.append((f"   -> {text}", "err"))
        for rule, why in res["not_generated"]:
            lines.append((f"no test  {rule}: {why}", ""))
        for note in res["notes"]:
            lines.append((f"note: {note}", ""))
        good = sum(1 for v in res["rules"].values() if v == "ok")
        bad = sum(1 for v in res["rules"].values() if v == "wrong")
        lines.insert(0, (f"{good} rule(s) behave as drawn, {bad} do not, "
                         f"{len(res['not_generated'])} have no generated test.", "err" if bad else "ok"))
        return lines

    def run_selftest(self):
        """The Test button: run the self-test on what is drawn and show it in the pane."""
        try:
            lines = self.selftest_report()
        except Exception as ex:                # KLayout missing, a half-typed constraint ...
            lines = [(f"{type(ex).__name__}: {ex}", "err")]
        if self._text_job:
            self.root.after_cancel(self._text_job)
            self._text_job = None
        self._text_shown = ("selftest", "")     # the next edit brings the rule text back
        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")
        for text, tag in lines:
            t.insert("end", text + "\n", tag)
        t.config(state="disabled")

    def _schedule_text(self):
        """Debounced: every model change funnels through draw_canvas / _refresh_tree,
        which call this; drags and keystrokes collapse into one recompile."""
        if getattr(self, "text", None) is None:
            return
        if self._text_job:
            self.root.after_cancel(self._text_job)
        self._text_job = self.root.after(150, self._update_text)

    def _update_text(self):
        self._text_job = None
        try:
            from .backend import get
            from .rule import Rule
            _backends()
            data = self.in_process(self.serialize())
            if not data.get("shapes") and not data.get("or"):
                out, tag = "(nothing drawn yet)", ""
            else:
                kw = ({"layermap": self._ctx["layers"]} if self._backend.get() == "klayout"
                      and self._ctx.get("layers") else {})
                out, tag = get(self._backend.get(), **kw).compile_data(data).rstrip(), ""
        except Exception as ex:                # e.g. a half-typed constraint
            out, tag = f"{type(ex).__name__}: {ex}", "err"
        if (out, tag) == self._text_shown:     # unchanged -> keep scroll position / selection
            return
        self._text_shown = (out, tag)
        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", out, tag)
        t.config(state="disabled")

    # ---- model helpers ----
    def nid(self, p):
        self.ids += 1
        return f"{p}{self.ids - 1}"

    def find_shape(self, i):
        return next((s for s in self.shapes if s["id"] == i), None)

    def find_measure(self, i):
        return next((m for m in self.measures if m["id"] == i), None)

    def find_side(self, sid):
        for s in self.shapes:
            for i, sd in enumerate(s["sides"]):
                if sd["id"] == sid:
                    return s, i
        return None, None

    def next_name(self):
        used = {s.get("name", "") for s in self.shapes}
        return next((chr(65 + i) for i in range(26) if chr(65 + i) not in used), "A")

    def next_var(self):
        used = {m["var"] for m in self.measures}
        k = 0
        while f"X{k}" in used:
            k += 1
        return next((c for c in "XYZWUV" if c not in used), f"X{k}")

    def _compute_roles(self):
        """How the compiler reads each shape of the drawing on the canvas."""
        try:
            from .rule import Rule
            from .sheet import rules_of
            cur = model_to_json(self.meta, self.shapes, self.measures, self.constraints)
            i = self.sel_rule()
            self._focus = None
            if i is None:
                self._roles = Rule(cur).shape_roles()
            else:                              # a sheet rule is selected: ITS reading
                cur = {k: v for k, v in cur.items() if k != "each"}     # one rule, not one per family member
                part = Rule(rules_of(cur)[self._expanded_index(cur, i)])
                self._roles = {s.get("name", ""): "not part of this rule" for s in self.shapes}
                self._roles.update(part.shape_roles())
                # what the canvas shows in full while this rule is selected
                self._focus = (set(part.shapes), set(part.measures))
        except Exception:                      # e.g. a half-typed constraint
            self._roles, self._focus = {}, None

    # ---- sheet rules (one drawn scene, many rules) ----
    def sheet_rules(self):
        return self.meta.get("rules") if isinstance(self.meta.get("rules"), list) else None

    def sel_rule(self):
        """Index of the selected sheet rule, or None."""
        if isinstance(self.sel, str) and self.sel.startswith("rule:") and self.sheet_rules():
            i = int(self.sel[5:])
            return i if 0 <= i < len(self.sheet_rules()) else None
        return None

    @staticmethod
    def _expanded_index(cur, i):
        """Where sheet rule `i` starts among the expanded rules (a table rule expands
        into one rule per column)."""
        return sum(max(1, len(r.get("values") or {})) for r in cur["rules"][:i])

    def add_rule(self):
        """Add a rule to the scene. The first one turns a single drawing into a sheet:
        its check constraints become that rule; the layer facts stay with the scene."""
        rules = self.sheet_rules()
        if rules is None:
            mine = [c for c in self.constraints if c["kind"] == "check"]
            self.constraints = [c for c in self.constraints if c["kind"] != "check"]
            first = {"name": self.meta.pop("name", None) or "R1", "desc": self.meta.pop("desc", ""),
                     "constraints": [dict(c) for c in mine]}
            rules = self.meta["rules"] = [first] if mine else []
        used = {r.get("name") for r in rules}
        k = len(rules) + 1
        while f"R{k}" in used:
            k += 1
        rules.append({"name": f"R{k}", "desc": "", "constraints": [{"kind": "check", "expr": ""}]})
        self.sel = f"rule:{len(rules) - 1}"
        self.redraw()

    def del_rule(self, i):
        rules = self.sheet_rules()
        if rules is not None and 0 <= i < len(rules):
            del rules[i]
            self.sel = None
            self.redraw()

    def role_of(self, s):
        if s.get("cut"):
            owner = void_owner(s, self.shapes)
            return (f"void: a hole in {owner.get('name', '')}" if owner
                    else "hole lying in no shape (ignored)")
        return self._roles.get(s.get("name", ""), "")

    def _side_kind(self, sid):
        s, i = self.find_side(sid)
        return s["sides"][i]["kind"] if s else None

    def is_relation(self, m):
        """A measure with a WAVY end that no constraint gives a number is not a distance
        but a relation between shapes. Once a constraint names it (``Sl <= 20``) it is a
        distance, whatever its ends look like -- that is how the rule is compiled."""
        import re
        var = m.get("var")
        if var and any(re.search(rf"(?<![\w.]){re.escape(var)}(?![\w.(])", c.get("expr", ""))
                       for c in self.constraints):
            return False
        sa, sb = self.find_side(m["from"])[0], self.find_side(m["to"])[0]
        if sa is not None and sa is sb:                # across ONE shape: its width, never a relation
            return False
        return not (self._side_kind(m["from"]) == "straight" == self._side_kind(m["to"]))

    # ---- view transform (grid units <-> canvas pixels) ----
    def px(self, v):
        return (v[0] * self.cell + self.ox, v[1] * self.cell + self.oy)

    def grid(self, e):
        return (round((e.x - self.ox) / self.cell), round((e.y - self.oy) / self.cell))

    def fit(self):
        """Ask for the drawing to be scaled (down only) and centred in the canvas;
        applied at the next draw, once the canvas has a real size."""
        self._fit_pending = True
        self.redraw()

    def _apply_fit(self, W, H):
        pts = [v for s in self.shapes for v in s["verts"]]
        if not pts:
            self.cell, self.ox, self.oy = CELL, 0, 0
            return
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        pad = 48                               # room for wavy bumps, dimension lines, labels
        self.cell = max(2.0, min(CELL, (W - 2 * pad) / max(x1 - x0, 1e-9),
                                 (H - 2 * pad) / max(y1 - y0, 1e-9)))
        self.ox = round(W / 2 - (x0 + x1) / 2 * self.cell)
        self.oy = round(H / 2 - (y0 + y1) / 2 * self.cell)

    def side_pts_px(self, shape, i):
        v = shape["verts"]; n = len(v)
        return self.px(v[i]), self.px(v[(i + 1) % n])

    def ref_point(self, sid):
        s, i = self.find_side(sid)
        if not s:
            return None
        a, b = self.side_pts_px(s, i)
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    def _measure_geom(self, m):
        """Endpoints (p1,p2) of a measure's dimension line + on-edge witness
        anchors (e1,e2). Two parallel sides -> a true H/V dimension line: placed
        INSIDE their shared span when they overlap there, else OUTSIDE the shape
        (nearest side, with witness lines extending each edge to it) so it never
        lands on a perpendicular edge. A non-parallel pair -> slanted midpoint link."""
        sa, ia = self.find_side(m["from"])
        sb, ib = self.find_side(m["to"])
        if not sa or not sb:
            return None
        a1, b1 = self.side_pts_px(sa, ia)
        a2, b2 = self.side_pts_px(sb, ib)
        v1, v2 = a1[0] == b1[0], a2[0] == b2[0]   # vertical sides
        h1, h2 = a1[1] == b1[1], a2[1] == b2[1]   # horizontal sides
        if not ((v1 and v2) or (h1 and h2)):
            return self.ref_point(m["from"]), self.ref_point(m["to"]), None, None
        xs, ys, seen = [], [], set()
        for sh in (sa, sb):
            if sh["id"] in seen:
                continue
            seen.add(sh["id"])
            for v in sh["verts"]:
                x, y = self.px(v)
                xs.append(x); ys.append(y)
        at = (m.get("extra") or {}).get("at")       # where the arrow was put, if anywhere
        if v1 and v2:                              # vertical edges -> horizontal dim line
            s, c1, c2 = self._place((a1[1], b1[1]), (a2[1], b2[1]), min(ys), max(ys))
            if at is not None:
                s = self.px((0, at))[1]
                c1 = min(max(s, min(a1[1], b1[1])), max(a1[1], b1[1]))
                c2 = min(max(s, min(a2[1], b2[1])), max(a2[1], b2[1]))
            return (a1[0], s), (a2[0], s), (a1[0], c1), (a2[0], c2)
        s, c1, c2 = self._place((a1[0], b1[0]), (a2[0], b2[0]), min(xs), max(xs))
        if at is not None:
            s = self.px((at, 0))[0]
            c1 = min(max(s, min(a1[0], b1[0])), max(a1[0], b1[0]))
            c2 = min(max(s, min(a2[0], b2[0])), max(a2[0], b2[0]))
        return (s, a1[1]), (s, a2[1]), (c1, a1[1]), (c2, a2[1])

    @staticmethod
    def _place(r1, r2, bbox_lo, bbox_hi):
        """Pick the dimension line's position s along the shared axis: midpoint of a
        real overlap (inside), else just outside the shape bbox on the nearer side."""
        lo1, hi1 = sorted(r1); lo2, hi2 = sorted(r2)
        lo, hi = max(lo1, lo2), min(hi1, hi2)
        if hi - lo > 4:                                  # genuine overlap -> inside
            s = (lo + hi) / 2
        else:                                            # outside, nearest bbox side
            ext_lo, ext_hi, margin = min(lo1, lo2), max(hi1, hi2), 0.7 * CELL
            s = (bbox_lo - margin if (ext_lo - bbox_lo) <= (bbox_hi - ext_hi)
                 else bbox_hi + margin)
        return s, min(max(s, lo1), hi1), min(max(s, lo2), hi2)

    # ---- hit-testing ----
    def hit_side(self, px, py):
        for s in reversed(self.shapes):
            for i in range(len(s["verts"])):
                a, b = self.side_pts_px(s, i)
                if seg_dist(px, py, *a, *b) < 6:
                    return s, i
        return None, None

    def hit_vertex(self, px, py):
        for s in reversed(self.shapes):
            for i, v in enumerate(s["verts"]):
                vx, vy = self.px(v)
                if abs(vx - px) <= 7 and abs(vy - py) <= 7:
                    return s, i
        return None, None

    def _collinear_vertex(self, s, i):
        """True if vertex i is redundant (its two edges are collinear) — i.e. a
        split point whose removal doesn't change the shape."""
        v, n = s["verts"], len(s["verts"])
        if n <= 3:
            return False
        a, b, c = v[(i - 1) % n], v[i], v[(i + 1) % n]
        return (b[0] - a[0]) * (c[1] - b[1]) == (b[1] - a[1]) * (c[0] - b[0])

    def hit(self, px, py):
        for m in reversed(self.measures):
            g = self._measure_geom(m)
            if g and g[0] and g[1] and seg_dist(px, py, *g[0], *g[1]) < 6:
                return ("measure", m["id"])
        s, i = self.hit_side(px, py)
        if s:
            return ("side", s, i)
        for s in reversed(self.shapes):
            if point_in_poly(px, py, [self.px(v) for v in s["verts"]]):
                return ("shape", s["id"])
        return None

    # ---- geometry ops ----
    def split_side(self, shape, i, m):
        """Insert vertex m=(gx,gy) on side i, dividing it into two sub-sides."""
        v, sides, n = shape["verts"], shape["sides"], len(shape["verts"])
        a, b = v[i], v[(i + 1) % n]
        if list(m) in (list(a), list(b)):
            return
        v.insert(i + 1, [m[0], m[1]])
        sides.insert(i + 1, {"id": self.nid("e"), "kind": sides[i]["kind"]})

    def merge_vertex(self, s, i):
        """Reverse of split: drop vertex i, merging its two edges into one (the
        edge into i survives and extends). Re-points measures off the dropped edge."""
        n = len(s["verts"])
        if n <= 3:
            return
        keep = s["sides"][(i - 1) % n]["id"]   # edge into vertex i (survives, extends)
        gone = s["sides"][i]["id"]             # edge out of vertex i (removed)
        del s["verts"][i]
        del s["sides"][i]
        for m in self.measures:
            if m["from"] == gone:
                m["from"] = keep
            if m["to"] == gone:
                m["to"] = keep
        self.sel = keep

    def move_side(self, snap, sid, ddx, ddy):
        """Return a new shape with side `sid` moved perpendicular by (ddx,ddy),
        inserting risers where a neighbour is collinear (keeps it orthogonal)."""
        verts = [list(p) for p in snap["verts"]]
        sides = [dict(s) for s in snap["sides"]]
        n = len(verts)
        k = next((j for j, s in enumerate(sides) if s["id"] == sid), None)
        if k is None or n < 4:
            return dict(snap)
        A, B = verts[k], verts[(k + 1) % n]
        horiz, vert = (A[1] == B[1]), (A[0] == B[0])
        if not (horiz or vert):
            return dict(snap)
        d = (0, ddy) if horiz else (ddx, 0)
        if d == (0, 0):
            return dict(snap)
        start = (k - 1) % n
        rv = verts[start:] + verts[:start]
        rs = sides[start:] + sides[:start]
        P, Aa, Bb, Q = rv[0], rv[1], rv[2], rv[3]
        newA = [Aa[0] + d[0], Aa[1] + d[1]]
        newB = [Bb[0] + d[0], Bb[1] + d[1]]
        prev_coll = (horiz and P[1] == Aa[1]) or (vert and P[0] == Aa[0])
        next_coll = (horiz and Q[1] == Bb[1]) or (vert and Q[0] == Bb[0])

        out_v, out_s = [P], []
        if prev_coll:
            out_s.append(rs[0]); out_v.append(list(Aa))           # P -> A(old)
            out_s.append({"id": self.nid("e"), "kind": "straight"})  # riser A(old)->newA
            out_v.append(newA)
        else:
            out_s.append(rs[0]); out_v.append(newA)               # P -> newA
        out_s.append(rs[1]); out_v.append(newB)                   # newA -> newB (moved)
        if next_coll:
            out_s.append({"id": self.nid("e"), "kind": "straight"})  # riser newB->B(old)
            out_v.append(list(Bb))
            out_s.append(rs[2]); out_v.append(list(Q))            # B(old) -> Q
        else:
            out_s.append(rs[2]); out_v.append(list(Q))            # newB -> Q
        out_v += [list(p) for p in rv[4:]]
        out_s += [dict(s) for s in rs[3:]]
        return {"id": snap["id"], "name": snap.get("name", ""), "color": snap.get("color", ""),
                "verts": out_v, "sides": out_s, "cut": snap.get("cut", False),
                "extra": snap.get("extra", {})}

    # ---- mouse (single direct-manipulation mode) ----
    def on_down(self, e):
        g = self.grid(e)
        self.moving = None
        for m in self.measures:                            # grab a measure label -> drag along its line
            box, axis = self._label_meta.get(m["id"], (None, None))
            if box and box[0] <= e.x <= box[2] and box[1] <= e.y <= box[3]:
                g = self._measure_geom(m)
                mid = (((g[0][0] + g[1][0]) / 2, (g[0][1] + g[1][1]) / 2)
                       if g and g[0] and g[1] else (e.x, e.y))
                self.sel = m["id"]
                self.moving = {"kind": "label", "mid_id": m["id"], "axis": axis,
                               "e0": (e.x, e.y), "o0a": m.get("loff", 0), "mid": mid}
                self.redraw()
                return
        vs, vi = self.hit_vertex(e.x, e.y)
        if vs:
            if self._collinear_vertex(vs, vi):             # split dot -> un-split
                self.merge_vertex(vs, vi)
                self.redraw()
                return
            self.sel = vs["id"]                            # a real corner -> drag it (free angle)
            self.moving = {"kind": "vertex", "sid": vs["id"], "vi": vi}
            self.redraw()
            return
        r = self.hit(e.x, e.y)
        if r and r[0] == "side":
            s, i = r[1], r[2]
            self.sel = s["sides"][i]["id"]
            self.moving = {"kind": "side", "sid": self.sel,
                           "snap": json.loads(json.dumps(s)),
                           "horiz": s["verts"][i][1] == s["verts"][(i + 1) % len(s["verts"])][1],
                           "g0": g}
        elif r and r[0] == "measure":
            self.sel = r[1]
            m = self.find_measure(r[1])
            gg = self._measure_geom(m)
            if gg and gg[2] is not None:
                # drag an arrow along its edges (a new one lands mid-way, often on another):
                # where it sits is what it measures
                self.moving = {"kind": "arrow", "mid_id": m["id"],
                               "axis": "y" if abs(gg[0][1] - gg[1][1]) < 0.5 else "x"}
        elif r and r[0] == "shape":
            # cycle through overlapping shapes: repeated clicks step down the stack
            stack = [s for s in self.shapes
                     if point_in_poly(e.x, e.y, [self.px(v) for v in s["verts"]])]
            stack.reverse()                                # topmost first
            cur = next((k for k, s in enumerate(stack) if s["id"] == self.sel), -1)
            pick = stack[(cur + 1) % len(stack)] if cur >= 0 else stack[0]
            self.sel = pick["id"]
            self.moving = {"kind": "shape", "id": pick["id"], "g0": g,
                           "orig": [list(v) for v in pick["verts"]],
                           # its voids travel with it
                           "voids": [(h["id"], [list(v) for v in h["verts"]]) for h in self.shapes
                                     if h.get("cut") and void_owner(h, self.shapes) is pick]}
        else:                                              # empty -> deselect + start a rect drag
            self.sel = None
            self.drag = [g[0], g[1], g[0], g[1]]
        self.redraw()

    def on_move(self, e):
        g = self.grid(e)
        if self.drag:
            self.drag[2], self.drag[3] = g
            self.redraw()
        elif self.moving:
            mv = self.moving
            if mv["kind"] == "shape":
                ddx, ddy = g[0] - mv["g0"][0], g[1] - mv["g0"][1]
                sh = self.find_shape(mv["id"])
                if sh:
                    sh["verts"] = [[o[0] + ddx, o[1] + ddy] for o in mv["orig"]]
                for hid, horig in mv.get("voids", []):
                    h = self.find_shape(hid)
                    if h:
                        h["verts"] = [[o[0] + ddx, o[1] + ddy] for o in horig]
            elif mv["kind"] == "vertex":                   # drag a corner -> free angle (snapped)
                sh = self.find_shape(mv["sid"])
                if sh:
                    sh["verts"][mv["vi"]] = [g[0], g[1]]
            elif mv["kind"] == "arrow":                    # slide the arrow along its two edges
                m = self.find_measure(mv["mid_id"])
                if m:
                    m.setdefault("extra", {})["at"] = g[1] if mv["axis"] == "y" else g[0]
            elif mv["kind"] == "label":                    # free along the line; side flips across it
                m = self.find_measure(mv["mid_id"])
                if m:
                    if mv["axis"] == "x":                  # horizontal line
                        m["loff"] = mv["o0a"] + (e.x - mv["e0"][0]) / self.cell
                        m["lside"] = -1 if e.y < mv["mid"][1] else 1   # above / below
                    else:                                  # vertical line
                        m["loff"] = mv["o0a"] + (e.y - mv["e0"][1]) / self.cell
                        m["lside"] = -1 if e.x < mv["mid"][0] else 1   # left / right
            else:  # side
                ddx = 0 if mv["horiz"] else g[0] - mv["g0"][0]
                ddy = (g[1] - mv["g0"][1]) if mv["horiz"] else 0
                new = self.move_side(mv["snap"], mv["sid"], ddx, ddy)
                idx = next(j for j, s in enumerate(self.shapes) if s["id"] == new["id"])
                self.shapes[idx] = new
            self.refresh()

    def on_up(self, e):
        self.moving = None
        if self.drag:
            x, y = min(self.drag[0], self.drag[2]), min(self.drag[1], self.drag[3])
            w, h = abs(self.drag[2] - self.drag[0]), abs(self.drag[3] - self.drag[1])
            if w and h:                                    # a real drag -> new rect
                s = {"id": self.nid("s"), "name": self.next_name(),
                     "verts": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                     "sides": [{"id": self.nid("e"), "kind": "wavy"} for _ in range(4)],
                     "extra": {}}
                self.shapes.append(s)
                self.sel = s["id"]
            self.drag = None
            self.redraw()

    def on_double(self, e):
        # double-click an edge -> flip straight <-> wavy
        if e.state & 0x0001:                           # two quick shift-clicks are two picks for a
            return self.on_measure(e)                  # measure, never a flip of the edge
        s, i = self.hit_side(e.x, e.y)
        if s:
            self.moving = self.drag = None
            sd = s["sides"][i]
            sd["kind"] = "straight" if sd["kind"] == "wavy" else "wavy"
            self.sel = sd["id"]
            self.redraw()

    def on_split(self, e):
        # right-click / ctrl-click an edge -> split it at the click point
        s, i = self.hit_side(e.x, e.y)
        if s:
            g = self.grid(e)
            a, b = s["verts"][i], s["verts"][(i + 1) % len(s["verts"])]
            m = ([min(max(g[0], min(a[0], b[0])), max(a[0], b[0])), a[1]]
                 if a[1] == b[1] else
                 [a[0], min(max(g[1], min(a[1], b[1])), max(a[1], b[1]))])
            self.split_side(s, i, m)
            self.redraw()
        return "break"

    def on_measure(self, e):
        # shift-click two edges -> declare a measure between them. Between STRAIGHT edges
        # it is a real distance (a dimensional variable); with a WAVY end it is a
        # RELATION between the two shapes (a filter as it stands, or the check when crossed).
        s, i = self.hit_side(e.x, e.y)
        if not s:
            self.pending = None
        else:
            ref = s["sides"][i]["id"]
            if not self.pending:
                self.pending = {"side": ref}
            elif self.pending["side"] != ref:
                m = {"id": self.nid("m"), "var": self.next_var(),
                     "from": self.pending["side"], "to": ref, "extra": {}}
                self.measures.append(m)
                self.pending = None
                self.sel = m["id"]                 # show its properties (cross, metric)
        self.redraw()
        return "break"

    def _pick_cursor(self, *names):
        """Return the first cursor name this Tk/platform accepts (else default)."""
        for nm in names:
            try:
                self.canvas.config(cursor=nm)
                return nm
            except tk.TclError:
                continue
        return ""

    def on_hover(self, e):
        # hint that edges resize, interiors move, split dots/labels are draggable
        cur = ""
        over_label = any(b and b[0] <= e.x <= b[2] and b[1] <= e.y <= b[3]
                         for b, _ in self._label_meta.values())
        vs, vi = self.hit_vertex(e.x, e.y)
        if over_label or vs:                          # labels + corner vertices are draggable
            cur = self.cur_move
        else:
            r = self.hit(e.x, e.y)
            if r and r[0] == "side":
                a, b = self.side_pts_px(r[1], r[2])
                cur = self.cur_v if a[1] == b[1] else self.cur_h  # horiz edge -> up/down
            elif r and r[0] == "shape":
                cur = self.cur_move
        if self.canvas["cursor"] != cur:
            self.canvas.config(cursor=cur)

    def toggle_hole(self, e=None):
        # 'h': turn the selected shape into a hole (cutout) / back into material.
        if isinstance(self.root.focus_get(), (tk.Entry, tk.Text)):
            return                                   # don't fire while typing
        s = self.find_shape(self.sel) if self.sel else None
        if not s:
            return
        s["cut"] = not s.get("cut", False)           # hole punches whatever material it overlaps
        self.redraw()

    def on_del(self, e):
        if isinstance(self.root.focus_get(), (tk.Entry, tk.Text)):
            return                                   # typing in a field -> let it handle Del/BackSpace
        if not self.sel:
            return
        if self.find_measure(self.sel):
            self.measures = [m for m in self.measures if m["id"] != self.sel]
        elif self.find_shape(self.sel):
            sid = self.sel
            dead = {sd["id"] for sd in self.find_shape(sid)["sides"]}
            self.shapes = [s for s in self.shapes if s["id"] != sid]
            self.measures = [m for m in self.measures
                             if m["from"] not in dead and m["to"] not in dead]
        self.sel = None
        self.redraw()

    @staticmethod
    def _orient(a, b):
        if a[1] == b[1]: return "H"
        if a[0] == b[0]: return "V"
        return "D"

    # ---- render ----
    def draw_canvas(self):
        self._schedule_text()
        c = self.canvas
        c.delete("all")
        W, H = c.winfo_width(), c.winfo_height()
        if self._fit_pending and W > 1 and H > 1:      # canvas has a real size now
            self._fit_pending = False
            self._apply_fit(W, H)
        cell, ox, oy = self.cell, self.ox, self.oy
        step = max(1, math.ceil(12 / cell))            # thin the dot grid when zoomed out
        for i in range(math.ceil(-ox / cell / step) * step, math.floor((W - ox) / cell) + 1, step):
            for j in range(math.ceil(-oy / cell / step) * step, math.floor((H - oy) / cell) + 1, step):
                x, y = i * cell + ox, j * cell + oy
                c.create_oval(x - 1, y - 1, x + 1, y + 1, fill="#2b313f", outline="")

        # A sheet holds several rules in one drawing. With a rule selected, only what
        # that rule is about is drawn in full; the rest of the sheet is a faint outline.
        if self.sel_rule() is not None or self._focus is not None:
            self._compute_roles()
        focus = self._focus if self.sel_rule() is not None else None
        faded = (lambda s: s.get("name", "").split(".void")[0] not in focus[0]) if focus else (lambda s: False)

        # precompute geometry + scalloped outline for every shape
        geo = []
        for s in self.shapes:
            vpx = [self.px(v) for v in s["verts"]]
            n = len(vpx)
            cx = sum(p[0] for p in vpx) / n
            cy = sum(p[1] for p in vpx) / n
            outline = []
            for i in range(n):
                a, b = vpx[i], vpx[(i + 1) % n]
                outline += self._scallop(a, b, cx, cy) if s["sides"][i]["kind"] == "wavy" else [a]
            geo.append((s, vpx, cx, cy, outline))

        # material shapes first; same-layer cut shapes punch holes via even-odd
        for idx, (s, vpx, cx, cy, outline) in enumerate(geo):
            if s.get("cut"):
                continue
            if faded(s):                               # belongs to other rules of the sheet
                c.create_polygon([q for pt in outline for q in pt], fill="", outline=FADED, width=1)
                c.create_text(vpx[0][0] + 5, vpx[0][1] + 4, text=s.get("name", ""), fill=FADED,
                              anchor="nw", font=("TkDefaultFont", 10))
                continue
            col = s.get("color") or COLORS[idx % len(COLORS)]   # per-shape color, else auto
            holes = [o for (s2, _, _, _, o) in geo if s2.get("cut")]   # punch by the hole's outline
            self._hatch(c, [outline] + holes, col, HATCH_ANGLES[idx % len(HATCH_ANGLES)])
            if self.sel == s["id"]:
                flat = [coord for p in outline for coord in p]
                c.create_polygon(flat, fill="", outline=ACCENT, width=2, dash=(4, 3))
            self._draw_edges(c, s, vpx, cx, cy, INK, col, ())
            ex = s.get("extra") or {}
            tag = s.get("name", "")
            if ex.get("crossed"):                              # forbidden: an X over the shape
                xs, ys = [q[0] for q in vpx], [q[1] for q in vpx]
                c.create_line(min(xs), min(ys), max(xs), max(ys), fill=CHECK, width=3)
                c.create_line(min(xs), max(ys), max(xs), min(ys), fill=CHECK, width=3)
            if ex.get("flagged") is False:                     # a counter-example: NOT a violation
                tag += "  ✓"
            if ex.get("halo"):
                tag += f"  halo {ex['halo']:+g}"
            if ex.get("whole"):
                tag += "  (anywhere)"
            c.create_text(vpx[0][0] + 5, vpx[0][1] + 4, text=tag, fill=col,
                          anchor="nw", font=("TkDefaultFont", 11, "bold"))
            self._draw_verts(c, s, vpx)

        # cut shapes (holes) on top: dashed boundary in hole color, no fill
        for s, vpx, cx, cy, outline in geo:
            if not s.get("cut") or faded(s):
                continue
            self._draw_edges(c, s, vpx, cx, cy, HOLECOL, HOLECOL, (3, 2))
            c.create_text(vpx[0][0] + 5, vpx[0][1] + 4, text="∅" + s.get("name", ""),
                          fill=HOLECOL, anchor="nw", font=("TkDefaultFont", 11, "bold"))
            self._draw_verts(c, s, vpx)

        self._label_meta = {}
        for m in self.measures:
            if focus and m["var"] not in focus[1]:     # another rule's arrow
                continue
            g = self._measure_geom(m)
            if not g or not g[0] or not g[1]:
                continue
            p1, p2, e1, e2 = g
            sel = self.sel == m["id"]
            crossed = bool(m.get("extra", {}).get("crossed"))
            relation = self.is_relation(m)
            # a distance is a solid arrowed dimension; a relation is a thin dashed link;
            # a CROSS (the rule's check: "never") is red with an X on it
            col = ACCENT if sel else CHECK if crossed else RELCOL if relation else MEASURECOL
            for pe, pp in ((e1, p1), (e2, p2)):          # witness/extension lines
                if pe and (abs(pe[0] - pp[0]) > 0.5 or abs(pe[1] - pp[1]) > 0.5):
                    c.create_line(*pe, *pp, fill=col, width=1, dash=(2, 2))
            if relation:
                c.create_line(*p1, *p2, fill=col, width=2 if sel else 1.4, dash=(5, 3))
            else:
                c.create_line(*p1, *p2, fill=col, width=3 if sel else 2,
                              arrow=tk.BOTH, arrowshape=(9, 11, 4))
            mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
            if crossed:
                r = 7
                c.create_line(mx - r, my - r, mx + r, my + r, fill=col, width=3)
                c.create_line(mx - r, my + r, mx + r, my - r, fill=col, width=3)
            axis = "x" if abs(p1[1] - p2[1]) < 0.5 else "y"   # dim-line direction
            along = m.get("loff", 0) * self.cell                  # free offset along the line
            side = m.get("lside", -1)                         # which side, at a fixed gap
            GAP = 6
            if axis == "x":                                   # horizontal: slide x, side = above/below
                lx = mx + along
                anchor, ly = ("s", my - GAP) if side < 0 else ("n", my + GAP)
            else:                                             # vertical: slide y, side = left/right
                ly = my + along
                anchor, lx = ("e", mx - GAP) if side < 0 else ("w", mx + GAP)
            lab = c.create_text(lx, ly, text=m["var"], fill=col, anchor=anchor,
                                font=("TkDefaultFont", 10, "bold"))
            self._label_meta[m["id"]] = (c.bbox(lab), axis)

        if self.pending and "side" in self.pending:
            s, i = self.find_side(self.pending["side"])
            if s:                                      # the edge picked first, in its own form
                a, b = self.side_pts_px(s, i)
                if s["sides"][i]["kind"] == "wavy":
                    vpx = [self.px(v) for v in s["verts"]]
                    cx, cy = sum(q[0] for q in vpx) / len(vpx), sum(q[1] for q in vpx) / len(vpx)
                    pts = self._scallop(a, b, cx, cy, end=True)
                    c.create_line([co for q in pts for co in q], fill=ACCENT, width=4)
                else:
                    c.create_line(*a, *b, fill=ACCENT, width=4)
        if self.drag:
            x0, y0 = self.px((min(self.drag[0], self.drag[2]), min(self.drag[1], self.drag[3])))
            x1, y1 = self.px((max(self.drag[0], self.drag[2]), max(self.drag[1], self.drag[3])))
            c.create_rectangle(x0, y0, x1, y1, outline=ACCENT, dash=(4, 3))

    def _draw_edges(self, c, s, vpx, cx, cy, edgecol, wavycol, dash):
        n = len(vpx)
        for i in range(n):
            a, b = vpx[i], vpx[(i + 1) % n]
            sel = self.sel == s["sides"][i]["id"]
            hl = ACCENT if sel else None
            if s["sides"][i]["kind"] == "straight":
                c.create_line(*a, *b, fill=hl or edgecol, width=4 if sel else 2.6, dash=dash)
            else:
                pts = self._scallop(a, b, cx, cy, end=True)
                c.create_line([co for p in pts for co in p],
                              fill=hl or wavycol, width=3 if sel else 1.6, dash=dash)

    def _draw_verts(self, c, s, vpx):
        for i, p in enumerate(vpx):
            if self._collinear_vertex(s, i):     # a split point -> big, noticeable dot
                c.create_oval(p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5,
                              fill=SPLITCOL, outline=INK, width=1.5)
            else:                                 # a real corner -> grab handle (drag to reshape)
                c.create_oval(p[0] - 3.5, p[1] - 3.5, p[0] + 3.5, p[1] + 3.5,
                              fill=BG, outline=INK, width=1.5)

    def _hatch(self, c, loops, color, angle, spacing=7, width=1):
        """Fill the region bounded by `loops` (a list of point-loops; the outer loop
        plus any hole loops) with parallel lines at `angle`. Even-odd across all loops
        leaves holes unfilled. Real lines = works on macOS (stipple doesn't)."""
        th = math.radians(angle)
        dx, dy = math.cos(th), math.sin(th)   # line direction
        nx, ny = -dy, dx                       # spacing axis (unit normal)
        pts = [p for loop in loops for p in loop]
        if not pts:
            return
        ds = [p[0] * nx + p[1] * ny for p in pts]
        d, dmax = min(ds), max(ds)
        while d <= dmax:
            ts = []
            for loop in loops:
                ln = len(loop)
                for i in range(ln):
                    x1, y1 = loop[i]; x2, y2 = loop[(i + 1) % ln]
                    s1 = x1 * nx + y1 * ny - d
                    s2 = x2 * nx + y2 * ny - d
                    if (s1 > 0) != (s2 > 0):
                        f = s1 / (s1 - s2)
                        ix, iy = x1 + f * (x2 - x1), y1 + f * (y2 - y1)
                        ts.append(ix * dx + iy * dy)
            ts.sort()
            for k in range(0, len(ts) - 1, 2):
                t0, t1 = ts[k], ts[k + 1]
                c.create_line(d * nx + t0 * dx, d * ny + t0 * dy,
                              d * nx + t1 * dx, d * ny + t1 * dy, fill=color, width=width)
            d += spacing

    def _scallop(self, a, b, cx, cy, end=False):
        """Scallop samples along side a->b, bumping OUTWARD (away from centroid),
        meeting both corners exactly (offset 0 at the ends). end=True includes b."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1
        nx, ny = -dy / ln, dx / ln
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if (mx + nx - cx) ** 2 + (my + ny - cy) ** 2 < (mx - nx - cx) ** 2 + (my - ny - cy) ** 2:
            nx, ny = -nx, -ny
        amp = 8.0 * self.cell / CELL                # bumps scale with the view
        bumps = max(1, round(ln / (2 * self.cell)))
        steps = max(12, bumps * 8)
        pts = []
        for k in range(steps + 1 if end else steps):
            t = k / steps
            off = amp * (1 - math.cos(t * 2 * math.pi * bumps)) / 2
            pts.append((a[0] + dx * t + nx * off, a[1] + dy * t + ny * off))
        return pts

    def redraw(self):
        self.draw_canvas(); self.show_props(); self._refresh_tree()

    def refresh(self):
        self.draw_canvas()

    # ---- outline tree ----
    def _refresh_tree(self):
        self._schedule_text()
        t = self.tree
        self._suppress_tree = True
        keep = t.selection()           # preserve the tree selection across the rebuild
        opened = {s["id"] for s in self.shapes if t.exists(s["id"]) and t.item(s["id"], "open")}
        t.delete(*t.get_children())
        self._compute_roles()
        cs = t.insert("", "end", "cat_shapes", text="Shapes", open=True)
        for s in self.shapes:
            role = self.role_of(s).split(":")[0] or ("hole" if s.get("cut") else "material")
            n = t.insert(cs, "end", s["id"], open=(s["id"] in opened),
                         text=f"{'∅' if s.get('cut') else ''}{s.get('name', '')}  ({role})")
            for sd in s["sides"]:
                t.insert(n, "end", sd["id"], text=f"{sd['id']}  {sd['kind']}")
        cm = t.insert("", "end", "cat_measures", text="Measures", open=True)
        for m in self.measures:
            kind = ("never" if m.get("extra", {}).get("crossed") else
                    "rel" if self.is_relation(m) else "dist")
            t.insert(cm, "end", m["id"], text=f"{m['var']} = {kind}({m['from']}, {m['to']})")
        cc = t.insert("", "end", "cat_constraints", text="Constraints", open=True)
        for i, c in enumerate(self.constraints):
            t.insert(cc, "end", f"con{i}", text=f"[{c['kind']}] {c['expr']}")
        if self.sheet_rules() is not None:             # a sheet: the rules on this scene
            cr = t.insert("", "end", "cat_rules", text="Rules", open=True)
            for i, r in enumerate(self.sheet_rules()):
                what = "; ".join(c.get("expr", "") for c in r.get("constraints", [])) or \
                    " ".join(["✗"] + list(r.get("cross", []) + r.get("measures", [])))
                t.insert(cr, "end", f"rule:{i}", text=f"{r.get('name', '')}   {what}")
        # reflect self.sel; for @constraints keep the specific constraint node if one was
        # selected (avoids the build-time-trace jump), else the group header.
        if self.sel == "@constraints":
            want = next((i for i in keep if i.startswith("con")), "cat_constraints")
        else:
            want = self.sel
        if want and t.exists(want):
            t.selection_set(want); t.see(want)
        self._suppress_tree = False

    def on_tree_select(self, e):
        if self._suppress_tree:
            return
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("rule:"):                             # a rule of the sheet
            new = iid
        elif iid == "cat_constraints" or iid.startswith("con"):   # the constraints table
            new = "@constraints"
        elif iid.startswith("cat_"):                            # Shapes / Measures group nodes
            return
        else:
            new = iid                             # shape / edge / measure id
        if new == self.sel:                       # unchanged (e.g. tree rebuilt) -> don't clobber edits
            return
        self.sel = new
        if self._process:
            self._mark_current()                 # the dropdown names the rule in hand
        self.draw_canvas(); self.show_props()

    # ---- drag a tree item to reorder within its category ----
    _REORDER = ("cat_shapes", "cat_measures", "cat_constraints")

    def on_tree_press(self, e):
        item = self.tree.identify_row(e.y)
        self._drag_item = item if self.tree.parent(item) in self._REORDER else None
        self._dragged = False

    def on_tree_drag(self, e):
        if not self._drag_item:
            return
        target = self.tree.identify_row(e.y)
        parent = self.tree.parent(self._drag_item)
        if target and target != self._drag_item and self.tree.parent(target) == parent:
            self.tree.move(self._drag_item, parent, self.tree.index(target))
            self._dragged = True

    def on_tree_release(self, e):
        if not self._drag_item or not self._dragged:   # plain click (no drag) -> leave selection be
            self._drag_item = None
            return
        parent = self.tree.parent(self._drag_item)
        self._drag_item = None
        kids = self.tree.get_children(parent)
        if parent == "cat_constraints":               # iids are con<original index>
            self.constraints = [self.constraints[int(i[3:])] for i in kids]
        elif parent == "cat_shapes":                  # stable ids -> reorder by id (changes z-order)
            rank = {iid: k for k, iid in enumerate(kids)}
            self.shapes.sort(key=lambda s: rank.get(s["id"], 0))
        elif parent == "cat_measures":
            rank = {iid: k for k, iid in enumerate(kids)}
            self.measures.sort(key=lambda m: rank.get(m["id"], 0))
        self.redraw()

    # ---- properties table (Name | Value), context-sensitive to the selection ----
    def show_props(self):
        p = self.props
        for w in p.winfo_children():
            w.destroy()

        def name(r, txt):
            tk.Label(p, text=txt, bg=PANEL, fg="#8b93a7", anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 8), pady=2)

        def ro(r, val):                                # read-only value
            tk.Label(p, text=str(val), bg=PANEL, fg=INK, anchor="w").grid(
                row=r, column=1, sticky="w", pady=2)

        def ent(r, val, setter):                       # editable text value
            v = tk.StringVar(value=str(val))
            tk.Entry(p, textvariable=v, bg="#11141b", fg=INK, insertbackground=INK).grid(
                row=r, column=1, sticky="ew", pady=2)
            # update model + canvas + tree, but NOT show_props (would kill this entry's focus)
            v.trace_add("write", lambda *_: (setter(v.get()), self.refresh(), self._refresh_tree()))

        def opt(r, val, opts, setter):                 # dropdown value
            v = tk.StringVar(value=str(val))
            tk.OptionMenu(p, v, *opts,
                          command=lambda x: (setter(x), self.refresh(), self._refresh_tree())).grid(
                row=r, column=1, sticky="w", pady=2)

        if self.sel == "@constraints":
            self._build_constraints_table(p)
            return
        ri = self.sel_rule()
        if ri is not None:                                     # a rule of the sheet
            r = self.sheet_rules()[ri]

            def set_expr(txt):                                 # its constraints, in one line
                cons = parse_rule_constraints(txt)
                if cons:
                    r["constraints"] = cons
                else:
                    r.pop("constraints", None)

            def set_list(key):
                def f(txt):
                    v = parse_names(txt)
                    r.__setitem__(key, v) if v else r.pop(key, None)
                return f

            def set_values(txt):
                v = parse_values(txt)
                r.__setitem__("values", v) if v else r.pop("values", None)
            name(0, "rule"); ent(0, r.get("name", ""), lambda x: r.__setitem__("name", x))
            name(1, "description"); ent(1, r.get("desc", ""), lambda x: r.__setitem__("desc", x))
            name(2, "check")
            ent(2, format_rule_constraints(r.get("constraints")), set_expr)
            name(3, "values"); ent(3, format_values(r.get("values")), set_values)
            def set_cross(txt):                                # shapes, or crossed measures
                words = parse_names(txt)
                picked = (("cross", [w for w in words if any(x.get("name") == w for x in self.shapes)]),
                          ("measures", [w for w in words if any(m["var"] == w for m in self.measures)]))
                for key, v in picked:
                    r.__setitem__(key, v) if v else r.pop(key, None)
            name(4, "cross (✗)"); ent(4, " ".join(r.get("cross", []) + r.get("measures", [])), set_cross)
            name(5, "except"); ent(5, " ".join(r.get("except", [])), set_list("except"))
            name(6, "also with"); ent(6, " ".join(r.get("with", [])), set_list("with"))
            name(7, "without"); ent(7, " ".join(r.get("without", [])), set_list("without"))
            tk.Label(p, text="check: e.g.  E4 >= 0.22   (or  E4 >= ?  with values  3.3V=0.22, 5V=0.3)\n"
                             "cross: shapes or measures that are forbidden in this rule\n"
                             "the tree shows how THIS rule reads each shape",
                     bg=PANEL, fg="#8b93a7", wraplength=260, justify="left").grid(
                row=8, column=0, columnspan=2, sticky="w", pady=(6, 2))
            tk.Button(p, text="delete rule", command=lambda: self.del_rule(ri)).grid(
                row=9, column=1, sticky="w")
            return
        s, i = self.find_side(self.sel) if self.sel else (None, None)
        if s:                                                  # an edge
            a, b = s["verts"][i], s["verts"][(i + 1) % len(s["verts"])]
            name(0, "kind"); opt(0, s["sides"][i]["kind"], ["wavy", "straight"],
                                 lambda x: s["sides"][i].__setitem__("kind", x))
            name(1, "orient"); ro(1, self._orient(a, b))
            name(2, "of shape"); ro(2, s.get("name", ""))
            name(3, "id"); ro(3, self.sel)
            return
        m = self.find_measure(self.sel) if self.sel else None
        if m:                                                  # a measure (= a variable)
            ex = m.setdefault("extra", {})
            rel = self.is_relation(m)

            def flag(key, on):                                 # set / drop a boolean key
                if on:
                    ex[key] = True
                else:
                    ex.pop(key, None)

            name(0, "name"); ent(0, m["var"], lambda x: m.__setitem__("var", x))
            same = self.find_side(m["from"])[0] is self.find_side(m["to"])[0]
            name(1, "relation" if rel else "width" if same else "distance"); ro(1, f"{m['from']} → {m['to']}")
            # the CROSS: this measure is the rule's check with the bound "never" -- the
            # drawn relation (or, across one shape, its region) must not exist
            name(2, "cross")
            CROSS = {"no": None, "never (forbidden)": True,
                     "never straddle (inside is fine)": "straddle"}

            def set_cross_kind(x):
                ex.pop("crossed", None)
                if CROSS[x]:
                    ex["crossed"] = CROSS[x]
                self.show_props()
            opt(2, next(k for k, v in CROSS.items() if v == (ex.get("crossed") or None)),
                list(CROSS) if rel else list(CROSS)[:2], set_cross_kind)
            if not rel:
                name(3, "metric")
                opt(3, ex.get("metric") or METRICS[0], METRICS,
                    lambda x: ex.pop("metric", None) if x == METRICS[0] else ex.__setitem__("metric", x))
                name(4, "between")
                opt(4, "edges" if ex.get("edges") else "shapes", ["shapes", "edges"],
                    lambda x: flag("edges", x == "edges"))
                # an enclosure margin: may the inner shape also lie ACROSS the outer's
                # edge? "within" (a manual's "overlap of ...") says no.
                name(5, "enclosure")
                opt(5, "within: no straddling" if ex.get("within") else "margin only",
                    ["margin only", "within: no straddling"],
                    lambda x: flag("within", x.startswith("within")))
            if not rel:
                def set_at(txt):
                    try:
                        ex["at"] = float(txt)
                    except ValueError:
                        ex.pop("at", None)             # blank = where the edges face each other
                name(6, "arrow at")
                ent(6, "" if ex.get("at") is None else f"{ex['at']:g}", set_at)
            hint = ("cross = the check; no constraint needed" if ex.get("crossed") else
                    "a link: this relation, as drawn, is a filter (no constraint needed)" if rel else
                    f"check: add  ! {m['var']} >= value")
            tk.Label(p, text=hint, bg=PANEL, fg="#8b93a7", anchor="w", wraplength=260,
                     justify="left").grid(row=8, column=0, columnspan=2, sticky="w", pady=(6, 0))
            return
        sh = self.find_shape(self.sel) if self.sel else None
        if sh:                                                 # a shape
            name(0, "name"); ent(0, sh.get("name", ""), lambda x: sh.__setitem__("name", x))
            name(1, "drawn as")
            tk.Button(p, text="material" if not sh.get("cut") else "hole (a void)",
                      command=self.toggle_hole).grid(row=1, column=1, sticky="w", pady=2)
            # how the compiler READS this shape -- derived from the drawing, not set here
            name(2, "reads as")
            tk.Label(p, text=self.role_of(sh) or "—", bg=PANEL, fg=ACCENT, anchor="w",
                     wraplength=200, justify="left").grid(row=2, column=1, sticky="w", pady=2)
            nr = 3
            if sh.get("cut"):
                return
            if not sh.get("cut"):                              # color only matters for material
                name(nr, "color")
                self._color_swatch(p, sh).grid(row=nr, column=1, sticky="w", pady=2)
                nr += 1
            st = sum(1 for k in sh["sides"] if k["kind"] == "straight")
            name(nr, "sides"); ro(nr, f"{len(sh['sides'])}  ({st} straight)")
            ex = sh.setdefault("extra", {})
            # a counter-example is the same layer drawn in a relation that is ALLOWED,
            # next to the flagged one -- how a negated selection is drawn
            # the two marks a datasheet puts on a shape: X = this must not exist (the
            # rule's check), a tick = this arrangement is allowed (a counter-example)
            name(nr + 1, "mark")
            cur = ("✗ forbidden" if ex.get("crossed") else
                   "✓ allowed" if ex.get("flagged") is False else "none")

            def set_mark(x):
                ex.pop("crossed", None); ex.pop("flagged", None)
                if x.startswith("✗"):
                    ex["crossed"] = True
                elif x.startswith("✓"):
                    ex["flagged"] = False
            opt(nr + 1, cur, ["none", "✗ forbidden", "✓ allowed"], set_mark)

            def set_halo(txt):
                try:
                    v = float(txt)
                except ValueError:
                    return
                if v:
                    ex["halo"] = v
                else:
                    ex.pop("halo", None)
            name(nr + 2, "halo (µm)"); ent(nr + 2, ex.get("halo", 0), set_halo)

            # "here" (the default): what is drawn under the arrow combines with this
            # shape -- poly over diffusion is the gate. "anywhere": its whole layer,
            # for a rule about poly that is merely drawn at a gate.
            def set_whole(x):
                if x.startswith("anywhere"):
                    ex["whole"] = True
                else:
                    ex.pop("whole", None)
            name(nr + 3, "stands for")
            opt(nr + 3, "anywhere on its layer" if ex.get("whole") else "its layer here",
                ["its layer here", "anywhere on its layer"], set_whole)
            return
        meta = self.rule_meta()                                # nothing selected: the rule itself
        name(0, "rule"); ent(0, meta.get("name") or "", lambda x: meta.__setitem__("name", x))
        name(1, "description"); ent(1, meta.get("desc") or "", lambda x: meta.__setitem__("desc", x))
        def set_json(key, parse):
            def f(txt):
                v = parse(txt)
                self.meta.__setitem__(key, v) if v else self.meta.pop(key, None)
            return f
        # what a manual puts around its figures: the legend, the table's columns, notes
        name(2, "legend"); ent(2, format_legend(self.meta.get("layers")), set_json("layers", parse_legend))
        name(3, "columns"); ent(3, format_columns(self.meta.get("domains")), set_json("domains", parse_columns))
        name(4, "except"); ent(4, " ".join(self.meta.get("except", [])), set_json("except", parse_names))
        tk.Label(p, text="legend:  pcomp = comp and pplus; gate = poly2 and comp\n"
                         "columns:  3.3V: not_interacting dualgate | 5V: overlapping dualgate\n"
                         "except:  layers the rules do not apply under\n\n"
                         "pick a shape / edge / measure, or Constraints in the tree;\n"
                         "'+ rule' adds another rule to this drawing",
                 bg=PANEL, fg="#8b93a7", wraplength=260, justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _color_swatch(self, p, sh):
        """A color chip showing the shape's current color; click -> palette picker."""
        def eff():
            idx = self.shapes.index(sh) if sh in self.shapes else 0
            return sh.get("color") or COLORS[idx % len(COLORS)]
        cv = tk.Canvas(p, width=26, height=16, bd=0, highlightthickness=1,
                       highlightbackground="#3a4150", bg=eff())

        def pick(e=None):
            chosen = colorchooser.askcolor(color=eff(), parent=self.root, title="Shape color")[1]
            if chosen:
                sh["color"] = chosen
                cv.config(bg=chosen)
                self.refresh()
        cv.bind("<Button-1>", pick)
        return cv

    def _kind_toggle(self, p, get, flip):
        """A square click-to-flip swatch: '?' = match (where it applies), '!' = check (the rule).
        A fixed-size Canvas (tk.Button has a wide native min-size on macOS)."""
        cv = tk.Canvas(p, width=20, height=20, bg="#11141b", highlightthickness=1,
                       highlightbackground="#3a4150", bd=0)
        t = cv.create_text(11, 11, font=("TkDefaultFont", 13, "bold"))

        def paint():
            k = get()
            cv.itemconfigure(t, text="?" if k == "match" else "!",
                             fill=MATCH if k == "match" else CHECK)
        cv.bind("<Button-1>", lambda e: (flip(), paint()))   # flip in place, no table rebuild
        paint()
        return cv

    def _build_constraints_table(self, p):
        tk.Label(p, text="?=match (where it applies) · !=check (the rule)\n"
                 "clear the text and press Enter to remove a constraint", bg=PANEL,
                 fg="#8b93a7", wraplength=260, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        for r, c in enumerate(self.constraints, start=1):
            def flip(c=c):
                c["kind"] = "check" if c["kind"] == "match" else "match"
                self._refresh_tree()
            self._kind_toggle(p, (lambda c=c: c["kind"]), flip).grid(row=r, column=0, sticky="w", pady=1)
            ev = tk.StringVar(value=c["expr"])
            en = tk.Entry(p, textvariable=ev, bg="#11141b", fg=INK, insertbackground=INK)
            en.grid(row=r, column=1, sticky="ew", padx=4, pady=1)
            ev.trace_add("write", lambda *a, c=c, ev=ev: (c.__setitem__("expr", ev.get()),
                                                          self._refresh_tree()))
            # delete by clearing the text and pressing Enter (no easy-to-misclick button)
            en.bind("<Return>", lambda e, c=c: self._del_constraint(c) if not c["expr"].strip() else None)
        # always-empty last row, ready for input: type + Enter -> commit, new empty row appears
        r = len(self.constraints) + 1

        def newflip():
            self._newk = "check" if self._newk == "match" else "match"
        self._kind_toggle(p, (lambda: self._newk), newflip).grid(row=r, column=0, sticky="w", pady=1)
        ne = tk.Entry(p, bg="#11141b", fg=INK, insertbackground=INK)
        ne.grid(row=r, column=1, sticky="ew", padx=4, pady=1)
        self._con_new_entry = ne

        def add():
            txt = ne.get().strip()
            if txt:
                self.constraints.append({"kind": self._newk, "expr": txt})
                self.show_props(); self._refresh_tree()
                self._con_new_entry.focus_set()
        ne.bind("<Return>", lambda e: add())

    def _del_constraint(self, c):
        if c in self.constraints:
            self.constraints.remove(c)
        self.show_props(); self._refresh_tree()

    # ---- json ----
    # External form: a shape is a LOOP OF EDGES, each {id,start,end,edge:bool}; a measure
    # references an edge by its stable id. Ids are persisted so measure links survive both
    # in-session edits AND save/reload (positional refs would shift on split/insert).
    def serialize(self):
        cur = model_to_json(self.meta, self.shapes, self.measures, self.constraints)
        if self.cases is None:
            return cur
        self.cases[self.case] = cur
        return {**self.top_meta, "or": list(self.cases)}

    def _load_case(self, o):
        # seed the id counter above any edge id in the file so fresh ids never collide
        existing = [int(str(e.get("id", ""))[1:]) for sh in o.get("shapes", [])
                    for e in sh.get("edges", []) if str(e.get("id", ""))[1:].isdigit()]
        self.ids = max(self.ids, 1 + (max(existing) if existing else 0))

        def next_name(shapes):
            used = {x.get("name", "") for x in shapes}
            return next((chr(65 + k) for k in range(26) if chr(65 + k) not in used), "A")
        self.meta, self.shapes, self.measures, self.constraints = model_from_json(
            o, self.nid, next_name)
        self.sel = self.pending = self.drag = self.moving = None

    def load(self, o):
        if isinstance(o.get("or"), list) and o["or"]:      # alternatives: show the first
            self.top_meta = {k: v for k, v in o.items() if k != "or"}
            self.cases, self.case = [dict(c) for c in o["or"]], 0
            self._load_case(self.cases[0])
        else:
            self.cases, self.case, self.top_meta = None, 0, {}
            self._load_case(o)
        self._refresh_cases()
        self.fit()

    # ---- alternative cases (a rule that is violated by ANY of several drawings) ----
    def rule_meta(self):
        """The dict holding the rule's own name / description."""
        return self.top_meta if self.cases is not None else self.meta

    def switch_case(self, i):
        if self.cases is None or not 0 <= i < len(self.cases) or i == self.case:
            return
        self.serialize()                                   # keep the edits of the current case
        self.case = i
        self._load_case(self.cases[i])
        self._refresh_cases()
        self.fit()

    def add_case(self):
        cur = model_to_json(self.meta, self.shapes, self.measures, self.constraints)
        if self.cases is None:                             # the single drawing becomes case 1
            self.top_meta = {k: cur.pop(k) for k in list(cur)
                             if k not in ("shapes", "measures", "constraints")}
            self.cases = [cur]
        else:
            self.cases[self.case] = cur
        self.cases.append({"shapes": [], "measures": [], "constraints": []})
        self.case = len(self.cases) - 1
        self._load_case(self.cases[self.case])
        self._refresh_cases()
        self.fit()

    def remove_case(self):
        if self.cases is None:
            return
        del self.cases[self.case]
        self.case = min(self.case, len(self.cases) - 1)
        if len(self.cases) == 1:                           # back to an ordinary single rule
            only, self.cases = {**self.cases[0], **self.top_meta}, None   # the rule's own name wins
            self.top_meta, self.case = {}, 0
            self._load_case(only)
        else:
            self._load_case(self.cases[self.case])
        self._refresh_cases()
        self.fit()

    def _refresh_cases(self):
        bar = self.case_bar
        for w in bar.winfo_children():
            w.destroy()
        if self.cases is None:
            tk.Label(bar, text="one drawing", bg=PANEL, fg="#8b93a7").pack(side="left")
            tk.Button(bar, text="+ alternative case", command=self.add_case).pack(side="left", padx=6)
            return
        tk.Label(bar, text="violated by ANY of:", bg=PANEL, fg="#8b93a7").pack(side="left")
        for i in range(len(self.cases)):
            tk.Button(bar, text=f"case {i + 1}", relief="sunken" if i == self.case else "raised",
                      command=lambda i=i: self.switch_case(i)).pack(side="left", padx=1)
        tk.Button(bar, text="+", command=self.add_case).pack(side="left", padx=(6, 1))
        tk.Button(bar, text="−", command=self.remove_case).pack(side="left", padx=1)

    # ---- files and the process they belong to ----
    def in_process(self, data):
        """`data` with what the process says once (legend, columns, how layers connect)
        added where the sheet does not say it itself -- what the deck is built from."""
        c = self._ctx
        if not c:
            return data
        d = dict(data)
        if c.get("values") is not None:                    # as the deck's default parameters make it
            from . import process
            d, _ = process.settled_rule(d, c["values"], c.get("gone", ()))
        if c.get("legend") or d.get("layers"):
            d["layers"] = {**c.get("legend", {}), **(d.get("layers") or {})}
        if c.get("columns") or d.get("domains"):
            d["domains"] = {**c.get("columns", {}), **(d.get("domains") or {})}
        if c.get("connect") and not d.get("connect"):
            d["connect"] = c["connect"]
        return d

    def set_process(self, folder):
        """Show `folder` as the process: list its sheets and take its legend, columns,
        layer numbers and connections for the live text."""
        from . import process
        self._process = folder
        self._ctx = {}
        if folder:
            try:
                proc, sheets = process.load(folder, absent=True)
                self._ctx = {"values": proc.get("_values") or {},
                             "legend": proc.get("legend") or {}, "columns": proc.get("columns") or {},
                             "layers": {k: tuple(v) for k, v in (proc.get("layers") or {}).items()},
                             "connect": next((d["connect"] for _, d in sheets if d.get("connect")), [])}
                self._proc_files = [p for p, _ in sheets]
                # the deck as it is browsed: sections (folders), and in each its rule files
                self._proc_rules = {}
                for p, d in sheets:
                    label = d.get("name") or self._sheet_label(p)
                    if isinstance(d.get("rules"), list):       # an old sheet: several rules on one drawing
                        label += f"   ({len(d['rules'])} rules on one drawing)"
                    self._proc_rules.setdefault(process.section_of(p, folder), []).append((p, label, d.get("desc", "")))
                self.proc_title.config(text=(proc.get("name") or process.deck_name(self._zip or folder)).upper())
            except Exception as ex:                    # noqa: BLE001
                messagebox.showerror("Process", str(ex))
                self._process, self._proc_files = None, []
        if not self._process:
            self.proc_frame.pack_forget()
            return
        self.proc_frame.pack(fill="x", before=self.tree)
        self._fill_rule_menu()
        self._mark_current()

    @staticmethod
    def _sheet_label(path):
        return os.path.basename(path).replace(".sheet.json", "").replace(".json", "")

    def deck_tree(self):
        """The deck as it is browsed: ``[(section, [(rule file, rule name, description)])]``
        -- a section is a folder under rules/, a rule is ONE file with ONE drawing."""
        return [(section or "(rules)", rules) for section, rules in self._proc_rules.items()]

    def _fill_rule_menu(self):
        """The dropdown: a section per entry, opening onto that section's rules."""
        menu = self.rule_menu
        menu.delete(0, "end")
        for section, rules in self.deck_tree():
            sub = tk.Menu(menu, tearoff=0)
            for path, name, desc in rules:
                sub.add_command(label=f"{name}    {desc}" if desc else name,
                                command=lambda p=path: self.goto_rule(p))
            menu.add_cascade(label=f"{section}    ({len(rules)})", menu=sub)

    def goto_rule(self, path, _index=None):
        """Open the rule file `path` (offering to save what changed in the one open)."""
        if self._path and os.path.abspath(path) == os.path.abspath(self._path):
            return True
        if not self.may_leave():
            return False
        try:
            self.open_path(path)
        except Exception as ex:                        # noqa: BLE001
            messagebox.showerror("Open failed", str(ex))
            return False
        return True

    def _mark_current(self):
        name = os.path.basename(self._path) if self._path else "untitled"
        if self._zip:
            name += f"  in  {os.path.basename(self._zip)}"
        self.root.title(f"{name} — FastDRC rule editor")
        if not self._path:
            self.rule_pick.config(text="choose a rule  ▾")
            return
        from . import process
        section = process.section_of(self._path, self._process) if self._process else ""
        rule = self.rule_meta().get("name") or self._sheet_label(self._path)
        self.rule_pick.config(text=(f"{section}  ›  " if section else "") + f"{rule}  ▾")

    def changed(self):
        """Does what is drawn differ from the file on disk?"""
        cur = self.serialize()
        if not self._path:
            return bool(cur.get("shapes") or cur.get("or"))
        try:
            with open(self._path) as f:
                return json.load(f) != cur
        except (OSError, ValueError):
            return True

    def may_leave(self):
        """Before another file replaces this one: offer to save what changed."""
        if not self.changed():
            return True
        name = os.path.basename(self._path) if self._path else "this drawing"
        ans = messagebox.askyesnocancel("Unsaved changes", f"Save changes to {name}?")
        if ans is None:
            return False
        return self.save() if ans else True

    def open_zip(self, path):
        """Open a deck kept in one zip file. It is unpacked to a folder of its own for
        the session; every save writes the whole deck back into the zip."""
        import atexit
        import shutil
        import tempfile

        from . import process
        tmp = tempfile.mkdtemp(prefix="fastdrc-deck-")
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        folder = process.unpack(path, os.path.join(tmp, process.deck_name(path)))
        self._zip = os.path.abspath(path)
        self._path = None
        self.set_process(folder)
        if self._proc_files:
            self.open_path(self._proc_files[0])
        else:
            self.clear_drawing()
        self._dir = os.path.dirname(self._zip)         # Open / Save as start beside the zip
        self._mark_current()

    def store(self):
        """After anything was written to the deck's folder: put it back into the zip
        the deck was opened from (nothing to do for a deck that is a folder)."""
        if self._zip and self._process:
            from . import process
            process.pack(self._process, self._zip)

    def open_path(self, path):
        from . import process
        if process.is_zip(path):
            return self.open_zip(path)
        with open(path) as f:
            data = json.load(f)
        self._path, self._dir = path, os.path.dirname(os.path.abspath(path))
        if self._zip:
            root = os.path.abspath(self._process or os.sep)
            if os.path.commonpath([root, os.path.abspath(path)]) == root:
                self._dir = os.path.dirname(self._zip)     # dialogs start beside the zip, not in its unpacked copy
            else:
                self._zip = None                           # a file outside the zipped deck
        folder, here = None, os.path.dirname(os.path.abspath(path))
        for _ in range(4):                             # the deck: the folder holding process.json
            if process.is_process(here):
                folder = here
                break
            here = os.path.dirname(here)
        self.load(data)
        if folder != self._process or folder:
            self.set_process(folder)
        self._mark_current()

    def new_rule(self):
        """A new rule: ONE file with ONE drawing, in the section of the rule that is
        open. `section/name` starts (or picks) another section."""
        from tkinter import simpledialog

        from . import process
        if not self._process or not self.may_leave():
            return
        here = process.section_of(self._path, self._process) if self._path else ""
        name = simpledialog.askstring("New rule", "Name of the rule (section/name for another section):",
                                      initialvalue=f"{here}/" if here else "", parent=self.root)
        if not name or not name.strip().strip("/"):
            return
        section, _, rule = name.strip().rpartition("/")
        clean = lambda t: "".join(c if c.isalnum() or c in "-_." else "_" for c in t.strip())   # noqa: E731
        folder = os.path.join(self._process, "rules", *[clean(x) for x in section.split("/") if x.strip()])
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, clean(rule) + ".json")
        if os.path.exists(path):
            messagebox.showerror("New rule", f"{os.path.basename(path)} already exists")
            return
        with open(path, "w") as f:
            json.dump({"name": rule.strip(), "desc": "", "shapes": [], "measures": [], "constraints": []}, f, indent=1)
        self.store()
        self.open_path(path)

    def edit_process(self):
        """What the process says once -- its name, the layers' GDS numbers, the legend
        and the table columns -- in one window; saved to process.json."""
        from . import process
        if not self._process:
            return
        path = os.path.join(self._process, process.PROCESS_FILE)
        try:
            with open(path) as f:
                proc = json.load(f)
        except (OSError, ValueError):
            proc = {}
        win = tk.Toplevel(self.root)
        win.title("Process")
        win.configure(bg=PANEL)
        fields = {}

        def row(label, hint, text, height):
            tk.Label(win, text=label, bg=PANEL, fg=INK, anchor="w",
                     font=("TkDefaultFont", 10, "bold")).pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(win, text=hint, bg=PANEL, fg="#8b93a7", anchor="w", justify="left").pack(fill="x", padx=10)
            box = tk.Text(win, height=height, width=64, bg="#0b0d12", fg=INK, insertbackground=INK,
                          highlightthickness=0, borderwidth=0)
            box.insert("1.0", text)
            box.pack(fill="both", expand=height > 2, padx=10, pady=2)
            fields[label] = box
        row("Name", "the process", proc.get("name", ""), 1)
        row("Layers", "one per line:  name  gds-layer/datatype      e.g.  nwell 1/0",
            format_layers(proc.get("layers")), 10)
        by_parameter = [k for k, v in (proc.get("legend") or {}).items() if not isinstance(v, str)]
        row("Legend", "names for combinations, one per line:  pdiff = diff and pimp"
            + (f"\n(kept as they are, set by a parameter of the deck: {', '.join(by_parameter)})" if by_parameter else ""),
            "\n".join(f"{k} = {v}" for k, v in (proc.get("legend") or {}).items() if isinstance(v, str)), 5)
        row("Columns", "a table's columns, one per line:  thin: not_interacting thick",
            "\n".join(f"{k}: " + ", ".join(f"{op} {layer}" for op, layer in v)
                      for k, v in (proc.get("columns") or {}).items()), 3)

        def text_of(label):
            return fields[label].get("1.0", "end").strip()

        def done():
            new = self.process_from_text(text_of("Name"), text_of("Layers"), text_of("Legend"), text_of("Columns"),
                                         proc)
            with open(path, "w") as f:
                json.dump(new, f, indent=1)
                f.write("\n")
            self.store()
            win.destroy()
            self.set_process(self._process)
            self.redraw()
        buttons = tk.Frame(win, bg=PANEL)
        buttons.pack(fill="x", padx=10, pady=8)
        tk.Button(buttons, text="Save", command=done).pack(side="right")
        tk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=6)
        return win, fields, done

    @staticmethod
    def process_from_text(name, layers, legend, columns, old=None):
        """The process.json content from the settings window's four texts (anything
        else already in the file -- a connect list -- is kept)."""
        new = dict(old or {})
        new["name"] = name
        new["layers"] = parse_layers(layers)
        new["legend"] = parse_legend(legend.replace("\n", ";"))
        # a name that a parameter of the deck decides is not text in the window: it stays
        new["legend"].update({k: v for k, v in ((old or {}).get("legend") or {}).items()
                              if not isinstance(v, str) and k not in new["legend"]})
        new["columns"] = parse_columns(columns.replace("\n", "|"))
        return new

    def save(self):
        """Write to the file being edited (asks where the first time). True if saved."""
        if not self._path:
            return self.save_as()
        with open(self._path, "w") as f:
            json.dump(self.serialize(), f, indent=2)
        self.store()
        if self._process:
            self.set_process(self._process)            # a drawn stack / legend may have changed
        return True

    def save_as(self):
        path = filedialog.asksaveasfilename(initialdir=self._dir, defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if not path:
            return False
        self._path, self._dir = path, os.path.dirname(path)
        self.save()
        self.open_path(path)                           # it may now belong to a process
        return True

    def open(self):
        if not self.may_leave():
            return
        path = filedialog.askopenfilename(initialdir=self._dir,
                                          filetypes=[("Rule or zipped deck", "*.json *.zip"), ("All files", "*")])
        if path:
            try:
                self.open_path(path)
            except Exception as ex:                    # noqa: BLE001
                messagebox.showerror("Open failed", str(ex))

    def clear(self):
        """A new, untitled drawing (the file that was open is left as it is on disk)."""
        if not self.may_leave():
            return
        self.clear_drawing()

    def clear_drawing(self):
        self._path = None
        self._mark_current()
        self.shapes, self.measures, self.constraints, self.meta = [], [], [], {}
        self.cases, self.case, self.top_meta = None, 0, {}
        self.sel = self.pending = self.drag = self.moving = None
        self._refresh_cases()
        self.fit()


def run(path=None):
    """Open the editor on a rule / sheet file, or on a process folder (its first sheet)."""
    from . import process
    root = tk.Tk()
    ed = Editor(root)
    if path:
        try:
            if process.is_zip(path):
                ed.open_zip(path)
            elif os.path.isdir(path):
                ed._dir = os.path.abspath(path)
                ed.set_process(path)
                if ed._proc_files:
                    ed.open_path(ed._proc_files[0])
                elif not process.is_process(path):
                    messagebox.showinfo("Process", f"{path} has no {process.PROCESS_FILE} yet -- "
                                        f"run `fastdrc init {path}` to make it a process.")
            else:
                ed.open_path(path)
        except Exception as ex:
            messagebox.showerror("Open failed", str(ex))
    root.protocol("WM_DELETE_WINDOW", lambda: ed.may_leave() and root.destroy())
    root.mainloop()


def main():
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)


if __name__ == "__main__":
    main()
