# The `fastdrc` command

Every command, with every option. `fastdrc COMMAND -h` prints the same from the tool.

Where a command asks for a **deck** it takes any of: a deck folder, a deck zipped into
one file, or the name of a deck installed as a package (`fastdrc decks`). Left out, it
is the current folder.

`build`, `rules` and the editor need only Python. `check`, `selftest` and the editor's
**Test** button run [KLayout](https://www.klayout.de): `klayout` on the PATH, or the
program named in `KLAYOUT_BIN`.

| | |
|---|---|
| [`init`](#init) | start an empty deck |
| [`gui`](#gui) | draw the rules |
| [`build`](#build) | deck → the script and the rules as sentences |
| [`selftest`](#selftest) | do the rules behave as drawn? |
| [`check`](#check) | run the deck on a layout |
| [`parameters`](#parameters) | the options of a process |
| [`rules`](#rules) | compile single rule files; print with any backend |
| [`pack`, `unpack`](#pack-unpack) | a deck as one zip file |
| [`decks`, `backends`](#decks-backends) | what is installed |
| [`split`](#split) | older multi-rule sheets → one file per rule |

## init

    fastdrc init mydeck                 a folder with process.json and rules/
    fastdrc init mydeck.zip             the same as one zip file
    fastdrc init mydeck --name "My process 0.18"

`--name` is the process name written into `process.json` (default: the folder's name).

## gui

    fastdrc gui                         an empty drawing
    fastdrc gui mydeck                  the deck: its rules in a list, the first one open
    fastdrc gui mydeck/rules/metal/M1.1.json

The editor: one rule, one file, one drawing. How to use it is in
[the reference](drawing-model.md#in-the-editor-fastdrc-gui).

## build

    fastdrc build mydeck                ->  mydeck/build/<name>.drc   the KLayout script
                                            mydeck/build/<name>.md    the rules as sentences

| option | |
|---|---|
| `-o DIR` | where to write. Default: `build/` in the deck folder; for a zip, `<name>.build/` beside it; for an installed deck, `<name>.build/` in the current folder |
| `--only RULES` | build these rules alone: names or sections, comma-separated, `*` and `?` as in file names — `--only "NW.*,CO.1"`, `--only 3-metal`, `--only 2-devices/CO.1`. May be repeated |
| `--skip RULES` | leave these out (same form) |
| `--profile NAME` | a named set of the deck's parameters |
| `--set NAME=VALUE` | one parameter: `--set top_metal=30K,mim=none`. May be repeated |
| `--deep` | a script that works on the layout's hierarchy (see `check`) |
| `--no-optimise` | write every rule out in full, instead of computing once what several rules need. Slower to run; each rule can be read by itself |
| `--backend NAME` | compile with this backend instead of `klayout` |
| `--with NAMES` | also write the files of these backends: `--with svg` (a picture per rule), `--with cases,table` |

The script is fitted to the set of rules it holds: it reads only the layers they need
and computes once what several of them share. Its first lines say which set it is.

## selftest

    fastdrc selftest mydeck

For each constraint of each rule, test layouts are made from the rule's own drawing —
a check just outside its value (must be reported) and exactly at it (must not), a
filter with a violation just outside it (must not be reported) — and the deck is run
on them. Then every layout in the deck's `layouts/` is run and compared with the rules
it names.

    1015 rule(s) behave as drawn, 0 do not, 30 have no generated test yet (...)
    146 of the deck's 146 layouts are read as they should be.

Takes the options of `build` except `--with`, and:

| option | |
|---|---|
| `-v`, `--verbose` | list every constraint tested, one by one. (The rules with no generated test are named, with the reason, whenever nothing is wrong; with `-v` always) |

The exit status is 1 if a rule does not behave as drawn, else 0.

## check

    fastdrc check chip.gds mydeck
    fastdrc check chip.gds gf180mcu --profile gf180mcuC

Builds the deck, runs it on the layout (GDS or OASIS) and lists the violations per
rule:

      NW.1       3  nwell width
      CO.4      12  diff enclosure of contact
    15 violation(s) of 2 rule(s); 16 of 18 rules clean. Report: chip.lyrdb

Takes `--only`, `--skip`, `--profile`, `--set`, `--no-optimise` and `--backend` as
`build` does, and:

| option | |
|---|---|
| `--deep` | work cell by cell: a cell placed many times is checked once. The same places are found; a violation is reported once, in the cell it lies in, so the counts are per cell and not per placement |
| `--top CELL` | the cell to check (default: the layout's top cell) |
| `--report FILE` | where the report database goes (default: beside the layout, `chip.lyrdb`; open it in KLayout's marker browser) |

The exit status is 1 if anything was reported, else 0. Without KLayout the deck is
still built, and the command to run it by hand is printed.

## parameters

    fastdrc parameters gf180mcu

      metal_stack    3LM  4LM  5LM  [6LM]
      top_metal      6K  [9K]  11K  30K
      profile gf180mcuC: metal_stack = 5LM, top_metal = 9K, mim = B

What is chosen once for a chip, the choices (the default in brackets) and the deck's
profiles. Chosen with `--profile` and `--set` on `build`, `check` and `selftest`.

## rules

    fastdrc rules rules/metal/M1.1.json                    one rule as KLayout statements
    fastdrc rules mydeck --to docs                         the deck as sentences, printed
    fastdrc rules M1.1.json --to facts                     what was read from the drawing

Compiles rule files, or whole decks, with one backend and prints the result. For
looking at a single rule; `build` is the command for a deck.

| option | |
|---|---|
| `--to NAME` | the backend (default `klayout`; see `fastdrc backends`) |
| `-o FILE` | write here instead of printing |
| `--layermap FILE` | a JSON file `{"layer name": [gds layer, datatype]}`, for rule files outside a deck |
| `--standalone` | a complete KLayout script that reads `$input` and writes `$report`: `klayout -b -r deck.drc -rd input=chip.gds -rd report=out.lyrdb` |
| `--title TEXT` | the report's title (with `--standalone`) |

## pack, unpack

    fastdrc pack mydeck                 ->  mydeck.zip   (build/ is left out)
    fastdrc pack mydeck -o /tmp/d.zip
    fastdrc unpack mydeck.zip           ->  mydeck/
    fastdrc unpack mydeck.zip elsewhere

The zip is the same deck: every command and the editor take it where they take the
folder, and the editor saves straight into it.

## decks, backends

    fastdrc decks                       the decks installed as packages, and where they lie
    fastdrc backends                    the backends: what each writes, whether it runs a deck

Both kinds are plugins: [plugins.md](plugins.md).

## split

    fastdrc split mydeck

Rewrites a deck's older sheets — several rules on one drawing — as one file per rule.
A deck drawn with the current editor has nothing to split.

## From Python

```python
from fastdrc import process

deck, text, n_rules, notes = process.build("mydeck")              # as `fastdrc build`
deck, *_ = process.build("mydeck", "out", only=["NW.*"], profile="thick", params=["mim=none"])
result = process.selftest("mydeck")                               # as `fastdrc selftest`
folder, out = process.find("gf180mcu")                            # an installed deck, by name

from fastdrc.backend import get, runner
from fastdrc.rule import Rule
import json

rule = Rule(json.load(open("mydeck/rules/metal/M1.1.json")))
print(get("docs").compile(rule))                                  # one rule as a sentence
found = runner("klayout").run(deck, "chip.gds")                   # {"rules": {name: (text, boxes)}, "report": path}
```
