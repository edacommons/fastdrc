"""Rule-deck flow runner.

A *deck* (TOML) is a DAG of *steps*; each step has a name, an op, inputs (refs to
other steps' outputs, or files), and produces output file(s). The deck compiles
to ``doit`` tasks and runs incrementally (content-hash up-to-date) and in
parallel. The flow engine is op-agnostic — it just wires nodes and tracks file
dependencies; it bakes in no check policy.

Refs in ``inputs``:
  * ``step``        -> that step's single output file,
  * ``step:layer``  -> a named layer file produced by a compose step,
  * anything else   -> a file path (relative to the deck file).

Ops:
  * ``compose``  -> ``fastdrc compose <config> --out workdir/<step>`` (named layers)
  * ``boolean``  -> ``fastdrc boolean --a A --b B --op <kind> --out ...``
  * ``command``  -> a raw shell command template; ``{ref}`` -> input file,
                    ``{out}`` -> this step's output.

Example::

    [deck]
    workdir = "out"

    [[step]]
    name = "layers"
    op   = "compose"
    config = "compose.toml"

    [[step]]
    name = "gate"
    op   = "boolean"
    kind = "and"
    inputs = ["layers:poly", "layers:active"]
"""

from __future__ import annotations

import os
import sys
import tomllib


def _resolve(path, base):
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(base, path))


def _load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def _build_tasks(deck_path):
    deck_dir = os.path.dirname(os.path.abspath(deck_path))
    deck = _load(deck_path)
    workdir = _resolve(deck.get("deck", {}).get("workdir", "out"), deck_dir)
    steps = deck.get("step", [])

    # First pass: each step's output file(s).
    outputs = {}  # name -> {targets, file (single, or None), layers, ...}
    for s in steps:
        name = s["name"]
        if ":" in name:
            raise SystemExit(f"step name must not contain ':': {name!r}")
        if name in outputs:
            raise SystemExit(f"duplicate step name: {name!r}")
        op = s["op"]
        if op == "compose":
            cfg_path = _resolve(s["config"], deck_dir)
            cfg = _load(cfg_path)
            specs = cfg.get("layer") or []
            if not specs:
                raise SystemExit(
                    f"compose step {name!r}: its config must list [[layer]] with "
                    "names so the deck knows the layer files")
            outdir = os.path.join(workdir, name)
            layers, targets = {}, []
            for ls in specs:
                lname = ls.get("name", f"L{ls['gds_layer']}D{ls['gds_datatype']}")
                f = os.path.join(outdir, lname + ".tiles")
                layers[lname] = f
                targets.append(f)
            targets.append(os.path.join(outdir, cfg.get("output", {})
                                        .get("index", "index.json")))
            outputs[name] = {"targets": targets, "file": None, "layers": layers,
                             "cfg": cfg, "cfg_path": cfg_path, "outdir": outdir}
        else:
            out = s.get("out")
            f = _resolve(out, deck_dir) if out else os.path.join(workdir, name + ".tiles")
            outputs[name] = {"targets": [f], "file": f, "layers": {}}

    def resolve_ref(ref):
        """Return (file, producer_step_or_None)."""
        if ":" in ref:
            sn, layer = ref.split(":", 1)
            if sn not in outputs or layer not in outputs[sn]["layers"]:
                raise SystemExit(f"unknown layer ref: {ref!r}")
            return outputs[sn]["layers"][layer], sn
        if ref in outputs:
            f = outputs[ref]["file"]
            if f is None:
                raise SystemExit(
                    f"ref {ref!r} is a compose step (many layers); use 'step:layer'")
            return f, ref
        return _resolve(ref, deck_dir), None  # external file

    # Second pass: actions + dependency edges.
    tasks = []
    for s in steps:
        name, op = s["name"], s["op"]
        o = outputs[name]
        file_dep, task_dep, actions = [], [], []

        if op == "compose":
            gds = _resolve(o["cfg"].get("input", {}).get("gds", ""),
                           os.path.dirname(o["cfg_path"]))
            file_dep = [o["cfg_path"]] + ([gds] if gds else [])
            actions = [[sys.executable, "-m", "fastdrc", "compose",
                        o["cfg_path"], "--out", o["outdir"]]]

        elif op == "boolean":
            inputs = s.get("inputs", [])
            if len(inputs) != 2:
                raise SystemExit(f"boolean step {name!r} needs exactly 2 inputs")
            fa, pa = resolve_ref(inputs[0])
            fb, pb = resolve_ref(inputs[1])
            kind = s.get("kind") or (s.get("params") or {}).get("op")
            if kind not in ("or", "and", "sub", "xor"):
                raise SystemExit(f"boolean step {name!r}: kind must be or/and/sub/xor")
            file_dep = [fa, fb]
            task_dep = [p for p in (pa, pb) if p]
            actions = [[sys.executable, "-m", "fastdrc", "boolean",
                        "--a", fa, "--b", fb, "--op", kind, "--out", o["file"]]]

        elif op == "inflate":
            inputs = s.get("inputs", [])
            if len(inputs) != 1:
                raise SystemExit(f"inflate step {name!r} needs exactly 1 input")
            d = s.get("d")
            if d is None:
                raise SystemExit(f"inflate step {name!r} needs a 'd' (distance)")
            f, p = resolve_ref(inputs[0])
            file_dep = [f]
            task_dep = [p] if p else []
            actions = [[sys.executable, "-m", "fastdrc", "inflate",
                        "--in", f, "--d", str(int(d)), "--out", o["file"]]]

        elif op == "command":
            subs = {}
            for ref in s.get("inputs", []):
                f, p = resolve_ref(ref)
                file_dep.append(f)
                if p:
                    task_dep.append(p)
                subs[ref.split(":")[-1] if ":" in ref else ref] = f
            subs["out"] = o["file"]
            actions = [s["command"].format(**subs)]  # shell string

        else:
            raise SystemExit(f"step {name!r}: unknown op {op!r}")

        tasks.append({
            "name": name,
            "actions": actions,
            "file_dep": file_dep,
            "targets": o["targets"],
            "task_dep": list(dict.fromkeys(task_dep)),
        })
    return tasks, workdir


def run(deck_path, jobs=1):
    """Run the deck via doit. Returns the doit exit code (0 = success)."""
    from doit.cmd_base import TaskLoader2
    from doit.doit_cmd import DoitMain
    from doit.task import dict_to_task

    tasks, workdir = _build_tasks(deck_path)
    os.makedirs(workdir, exist_ok=True)

    class _Loader(TaskLoader2):
        def load_doit_config(self):
            return {"verbosity": 2}

        def load_tasks(self, *args, **kwargs):
            return [dict_to_task(t) for t in tasks]

    args = ["-n", str(jobs)] if jobs and jobs > 1 else []
    return DoitMain(_Loader()).run(args)
