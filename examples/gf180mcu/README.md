# GF180MCU

The design rules of the open GlobalFoundries 180 nm MCU process, drawn: one rule, one
file, one drawing. The wording and the values are those of the process's public design
manual:

<https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/Design_Manual.html>

(part of the GF180MCU PDK, Apache License 2.0, GlobalFoundries PDK Authors).

    fastdrc build examples/gf180mcu            ->  build/gf180mcu.drc, build/gf180mcu.md
    fastdrc gui examples/gf180mcu              the drawings
    fastdrc check chip.gds examples/gf180mcu --profile gf180mcuC

## What is in it

766 rule files in 37 sections — the rules of the whole manual that can be checked on a
layout, chapters 7 to 14 (a rule with a value per column — 3.3 V and 5 V/6 V — is one
file and one check per column; what is left out, and why, is under *Not drawn*). The section `Grid` holds what the manual says once for every layer: the
manufacturing grid, 0.005 µm (`"grid"` in `process.json`, one check per layer), and "no
acute angle" (one drawing, `ANGLE`, on the family `every_layer`). Built as it is by
default — six metals, 9K top metal, MIM option B, wedge bond pads — that is 1039 checks,
210 of them those two per layer.

| sections | the manual's chapter |
|---|---|
| `Wells`, `COMP`, `Dualgate`, `Poly2`, `Nplus`, `Pplus`, `SAB`, `ESD`, `Contact`, `Metal`, `Via`, `MetalTop30K`, `Mcell` | 7, layout rules |
| `PRES`, `LRES`, `HRES`, `MIM-A`, `MIM-B`, `Native`, `BJT`, `DummyExclude`, `OTP`, `eFuse`, `LDNMOS`, `LDPMOS`, `MTP`, `Schottky`, `NeoEE` | 10, devices |
| `SRAM` | 11, SRAM core cells |
| `Antenna` | 8, antenna ratios |
| `BondPad` | 9, bond pads, circuit under pad, solder bump and probe pads |
| `GuardRing` | 12, the scribe line guard ring |
| `DummyFill` | 13, dummy COMP, Poly2 and metal |
| `LatchUp`, `ESDdevice`, `MetalSlot` | 14, latch-up, ESD protection devices, metal slots |

`process.json` holds the layer numbers, the names for combinations of layers (`ncomp`,
`tgate`, the zones within 0.43 µm of a well edge, the parts of an eFuse and of a Schottky
diode, of an ESD device, the rim of a pad opening), the columns, and which layers connect:
gate, poly, contacts and the whole metal stack — a via that lands on a MIM top plate ends
on it.

A device page that gives its own value for a rule (OTP, SRAM, MTP, NEO_EE) takes the
cells of its marker out of the general rule, so each place is checked once, at the
value the manual gives for it.

## The options of the process

They are parameters of the deck (`fastdrc parameters examples/gf180mcu`):

| parameter | choices | what it decides |
|---|---|---|
| `metal_stack` | 3LM, 4LM, 5LM, **6LM** | which metal is the top metal ("for 4 metal layers process, Top Metal is Metal4"): the `MT.*` / `MT30.*` rules are on it, the metal and via rules (`Mn.*`, `Vn.*`) are there for the metals and vias below it only (`top_metal_layer`, `top_via`, `under_top_metal`, `under_top_via`) |
| `top_metal` | 6K, **9K**, 11K, 30K | the values of `MT.1` and `MT.2a` (0.36 / 0.38 at 6K, 0.44 / 0.46 at 9K and 11K); with 30K the rules of the 3 µm metal, `MT30.*`, in their place |
| `mim` | none, A, **B** | `MIM.*` (between Metal2 and Metal3), or `MIMTM.*` (under the top metal), or neither |
| `bond_pad` | **wedge**, ball, gold_bump, solder_bump | the column of chapter 9: the values of `PAD.*`; no circuit under a wedge-bond pad (`PAD.17`-`PAD.19a`, `CUP.1a`), the `CUP.*` rules under a ball-bond one; `TM.*` / `PM.*` for solder bump pads; and the two cases of the guard ring (`GR.5`, `GR.11`) |

The profiles are the variants of the open PDK: `gf180mcuA` (3LM, 30K, MIM option A — the
manual's option B "only supports 4 or more metal layers"), `gf180mcuB` (4LM, 11K),
`gf180mcuC` (5LM, 9K), `gf180mcuD` (5LM, 11K), these three with MIM option B.

    fastdrc check chip.gds examples/gf180mcu --profile gf180mcuC
    fastdrc build examples/gf180mcu --set top_metal=30K,mim=none
    fastdrc check chip.gds examples/gf180mcu --set bond_pad=ball

## How it was checked

- **Against the manual.** Every section's rules were read beside the manual's sentence and
  its figure; what was found and changed is in [`MANUAL_CHECK.md`](MANUAL_CHECK.md). Where
  the manual was not clear, a rule is read by what it is for in the process, and that is
  written there too.
- **Against its own drawings.** `fastdrc selftest examples/gf180mcu` builds test layouts
  from every rule's picture and runs the deck on them: of the default deck's 1039 checks,
  all 1009 that are tested behave as drawn, and so do those of every profile and every
  kind of bond pad. 30 have no generated test. 18 are the rules that hold in a zone — along a well edge (`NP.3bi`, `NP.3bii`,
  `NP.3ci`, `NP.5ci`, `NP.5cii`, `NP.5di`, `PP.3bii`, `PP.3cii`, `PP.5cii`, `PP.5dii`,
  `NP.11`, `PP.11`), near a gate's poly extension (`NP.4b`, `PP.4b`, `NP.12`, `PP.12`),
  within 1.06 µm of a MIM top plate (`MIMTM.1`, `MIMTM.2`). A zone is a place in a layout,
  not a box on a layer, so the generator does not draw it; every one of them is tested on
  the layouts below, just inside and just outside its value. The other 12 are from the
  later chapters: the via stack in the rim of a pad opening (`PAD.5`-`PAD.14`) and the
  native device near an Nwell (`LU.5`), each on a layout below, and `DCF.1a`, which
  speaks of the whole die and was run on a layout of its own (COMP 18 µm apart: quiet;
  30 µm apart: reported).
- **On its own layouts.** `layouts/` holds 147 pieces of layout drawn by hand, each naming
  the rules that must report on it; `fastdrc selftest` runs the whole deck on them, and a
  rule that reports where it is not named is a rule drawn too broadly. All of them are read as
  they should be, in every build they belong to. Among them: LDNMOS and LDPMOS fingers with each of their values off,
  rings open or missing, fingers in a row, the guard between an LDPMOS and an outside
  NCOMP; resistors with the marker too long and too short; eFuses with each size off;
  Schottky diodes; native devices on one and on two nets; MIM plates; OTP gates and poly;
  near-edge implants just inside and just outside their values.
- **Against the process's own KLayout deck**, on those layouts and on the 7-track
  standard-cell library (one cell of each of its 69 types):
  - the cells as they are: the rules are silent in both decks, but for `DF.13` and
    `DF.14` here — the tap distance, read for the device: a cell with no tap of its own is
    reported until it stands beside its tap cells (the process's deck reads these two from
    the tap's side and is silent);
  - the cells with about 2800 random small errors put in: the rules that fire flag the
    same places in both decks, but for `DF.13`, `DF.14` (above) and one marker of `NP.5b`,
    where the process's deck takes a whole Nplus shape that touches an LVPWELL anywhere
    and reports a tap of it that lies in an Nwell — `NP.5b` is for COMP inside LVPWELL or
    outside Nwell and DNWELL; that tap is `NP.5d`'s, which both decks report alike;
  - on the hand-made layouts, where the two decks read the manual differently, this deck
    follows the manual's wording and the layout that shows it says so in its
    description: `MDN.5c`, `MDP.3c` (every part of the device within 15 µm of a tab);
    `DF.13`, `DF.14` (the device's tap, in its own well); `NP.11`, `PP.11` (the band on
    both sides of the well edge); `NP.12`, `PP.12` (the implant over the gate's poly
    extension); `SB.7` (coinciding edges); `MT30.6` (lines narrower than 2.5 µm);
    `MDP.4` (an LDPMOS's DNWELL with no P+ ring at all); `MDP.17a` (no tabbed DNWELL
    between the drift and an NCOMP 40 µm away); `PL.3a` (field poly in an OTP cell keeps
    0.24); `EF.07` (a strip of the anode left out of LVS_Source is cathode).
  - Schottky diodes (10.14), NEO_EE cells (10.15) and the whole of chapters 8, 9, 12, 13
    and 14 are not in the process's deck; they were drawn and checked from the manual
    alone.

## Not drawn

What the manual itself says is not a check on a layout: rules with the value 0 under its
note "Rules allowed minimum overlap and spacing are 0, so DRC deck will not check those
rules" (`O.PL.5a`, `O.PL.5b`, `O.SB.5a`, `O.SB.12`, `O.SB.15b`, `EE.DF.16_LV`), rules
whose value is "NA" (they take the general rule out, which is done), guidelines (`DE.1`,
`PL.3b`), the potential a ring must be tied to (`DN.3`, `MDN.17`, `MDP.3` — a fact about
the circuit, checked by LVS), and the pages that send the rest back to section 7
(`MDN.16`, `MDP.14`).

From chapters 8 to 14 the same holds for what the manual lists in its appendix B, "Rules
not coded", and for what it calls a guideline or a recommended rule (the names beginning
`G.` / `G_`, the matched-pair and DFM pages, current densities, the ESD diode and die
corner pages); [`MANUAL_CHECK.md`](MANUAL_CHECK.md) names them one by one.

Seven rules are layout checks and are not drawn yet, because the drawing has no way to
say them:

- `TM.4`, `TM.5`, `PM.3` — distances from the **centre** of a pad (bump pitch, probe pad
  pitch, pad centre to the die edge);
- `LVESD.6`, `HVNESD.6`, `HVPESD.6` — the **total** finger width of an ESD device, the
  fingers' widths added up;
- the diode allowance of `ANT.16` for the MIM case (`ANT.14`, `ANT.15` are checked at the
  plain ratio).
