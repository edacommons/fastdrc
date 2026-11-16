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

435 rule files in 28 sections (a rule with a value per column — 3.3 V and 5 V/6 V — is
one file and one check per column). The section `Grid` holds what the manual says once
for every layer: the manufacturing grid, 0.005 µm (`"grid"` in `process.json`, one check
per layer), and "no acute angle" (one drawing, `ANGLE`, on the family `every_layer`).
Built as it is by default — six metals, 9K top metal, MIM option B — that is 673 checks,
210 of them those two per layer.

| sections | the manual's chapter |
|---|---|
| `Wells`, `COMP`, `Dualgate`, `Poly2`, `Nplus`, `Pplus`, `SAB`, `ESD`, `Contact`, `Metal`, `Via`, `MetalTop30K`, `Mcell` | 7, layout rules |
| `PRES`, `LRES`, `HRES`, `MIM-A`, `MIM-B`, `Native`, `BJT`, `DummyExclude`, `OTP`, `eFuse`, `LDNMOS`, `LDPMOS`, `MTP` | 10, devices |
| `SRAM` | 11, SRAM core cells |

`process.json` holds the layer numbers, the names for combinations of layers (`ncomp`,
`tgate`, the zones within 0.43 µm of a well edge, the parts of an eFuse), the two
columns, and which layers connect.

## The options of the process

They are parameters of the deck (`fastdrc parameters examples/gf180mcu`):

| parameter | choices | what it decides |
|---|---|---|
| `metal_stack` | 3LM, 4LM, 5LM, **6LM** | which layers are the top metal, the one below it and the vias under them (`top_metal_layer`, `top_via`, `under_top_metal`, `under_top_via`) |
| `top_metal` | 6K, **9K**, 11K, 30K | the values of `MT.1` and `MT.2a` (0.36 / 0.38 at 6K, 0.44 / 0.46 at 9K and 11K); with 30K the rules of the 3 µm metal, `MT30.*`, in their place |
| `mim` | none, A, **B** | `MIM.*` (between Metal2 and Metal3), or `MIMTM.*` (under the top metal), or neither |

The profiles are the variants of the open PDK: `gf180mcuA` (3LM, 30K), `gf180mcuB`
(4LM, 11K), `gf180mcuC` (5LM, 9K), `gf180mcuD` (5LM, 11K), each with MIM option B.

    fastdrc check chip.gds examples/gf180mcu --profile gf180mcuC
    fastdrc build examples/gf180mcu --set top_metal=30K,mim=none

The stack does not yet take the rules of the metals a shorter stack lacks out of the
deck; with no shapes on those layers they report nothing.

## How it was checked

- **Against its own drawings.** `fastdrc selftest examples/gf180mcu` builds test layouts
  from every rule's picture and runs the deck on them: of the default deck's 673 checks,
  648 behave as drawn; `DE.3` and `O.PL.ORT` have no generated layout yet. The 23
  that do not are limits of the generated layouts, not of the rules: the end-of-line
  enclosures (`CO.6a`, `Vn.3c`, `Vn.4b`, and `MT30.6` with the 3 µm metal — the generated line end is shorter than
  it is wide), the rules that hold within 0.43 µm of a well edge (`NP.3bi`, `NP.5ci`,
  `NP.5di`, `PP.5cii`, `PP.5dii`, `NP.11`, `PP.11`), three exact eFuse lengths (`EF.03`, `EF.06`,
  `EF.09`), the count of drains at a gate (`MDN.13b`, `MDP.13b` — the generated second
  drain does not reach the gate).
- **Against the process's own KLayout deck**, on the same layouts and on the 41 standard
  cells of the 7-track library:
  - the cells as they are: all 355 front-end rules are silent in both decks;
  - the cells with about 1400 random small errors put in: the rules that fire flag the
    same places in both decks, but for `NP.5b` and one marker of `NP.3cii`;
  - the rules near a well edge and the end-of-line enclosures agree with it on layouts
    made by hand.
- **On its own layouts.** `layouts/` holds 35 pieces of layout drawn by hand, each naming
  the rules that must report on it; `fastdrc selftest` runs the whole deck on them, and a
  rule that reports where it is not named is a rule drawn too broadly. All 35 are read as
  they should be. A layout names the rules its deliberate error breaks by the manual; the
  other rules it names are those both decks report on it, which have not each been read
  against the manual.
- **On those layouts against the process's deck**, each with deliberate errors (an LDNMOS
  and an LDPMOS finger with and without its guard ring, with the ring open or on one side
  only, with the drift overlap, the gate-to-drain space and the gate end off; a
  high-resistance, a P+ and an N+ poly resistor; MIM capacitors on one plate;
  dummy-exclude areas; OTP gates either way; a well with two kinds of PMOS; a shape off
  the grid and one with an acute corner). Where the two decks differed, the manual decided — its sentence and its figure (10.12.1
  LDNMOS, 10.12.2 LDPMOS, 7.6 Dualgate, 7.10 SAB) — and where the manual was not clear, what the
  rule is for in the process:
  - *this deck was wrong, and is corrected:* `DV.1` and 14 more "X must enclose Y" rules
    missed a Y lying across X's edge (the figures draw Y wholly inside X; a well, a
    diffusion or a contact half outside is a different structure); `MDP.11`, `MDN.10b`,
    `MDP.9b` measured the side the manual leaves out; `MDN.4a`, `MDP.2`, `PRES.5`,
    `LRES.5`, `HRES.4`, `MDP.9ei`, `MDN.10ei` reported devices they are not about;
    6 V transistors were checked at the 5 V gate length (`PL.2`);
  - *read by what the rule is for:* `PL.4` is the gate's **end cap**, so it is measured
    past the diffusion edges that run out from under the gate — the LDMOS poly edge
    towards the drain is not an end cap, and no exemption is needed. `PP.9`, `NP.9` ask
    the implant to cover the sides of unsalicided poly by 0.18; they do not say such poly
    may not leave the implant, as it does by design on the high-resistance resistor.
    `MDN.5c`, `MDP.3c` are latch-up rules: every part of the device must be within 15 µm
    of a tab;
  - *the two decks still differ:* with no tab at all `MDN.5c` / `MDP.3c` report the
    device here and the process's deck is silent; with a tab on one side of a small
    LDPMOS the process's deck reports `MDP.3c` (it asks for the tab on four sides) and
    this deck does not. `SB.7` reports salicide-block and diffusion edges that coincide;
    the process's deck lets them pass there (both report them by `SB.6`);
  - *not judged:* the two layouts with a drift region on both sides of the gate are not
    devices, and the decks disagree on several rules there.
  The end-of-line rules read "narrower than" as the manual writes it (`<`), and `MT30.6`
  looks at line ends of the thick metal narrower than 2.5 µm; the process's deck uses 0.34 there.
  On the real standard cells with 700 random edits `PL.4` still flags the same 141 places in
  both decks.
- **Not shown yet:** the LDMOS, eFuse, resistor and MIM rules behave as their own drawings
  say, and the ones named above agree with the process's deck on a device made by hand;
  the rest have not been compared with it on a device of that kind, and none on a device
  from a real design. The process's deck
  asks more of a guard ring than is drawn here (one well per ring, nothing else in it);
  `NP.11` and `PP.11` have been tried on no layout that makes them fire.

## Checked against the manual

[`MANUAL_CHECK.md`](MANUAL_CHECK.md) lists every rule file with its row in the manual, whether its values are the
manual's (compared by script for all 435), and whether the manual's figure for it has been read (32 so far). A
rule counts as checked only after that read.

## Not drawn

20 of the manual's rules that its KLayout deck checks:

- guard rings with more to them than "surrounded by": `MDP.4`, `MDN.13d`, `MDP.17a`,
  `MDP.17c`;
- an exact length along one direction, or two edges that must coincide: `EF.05`, `EF.21`,
  `EF.22a`, `EF.22b`, `DF.3b`, `PRES.9a`, `LRES.9a`, `HRES.12a` (of `MDN.10c` the minimum
  half is drawn);
- the order of things along a device: `MDN.13c`, `MDP.13c`, `MDP.3d`;
- the rest: `DF.11`, `PL.7`, `NP.4b`, `PP.4b`, `SB.15b`, `NAT.6`.

Also not here: the manual's chapters its own
deck does not check either (antenna ratios are in `examples/demo` as a kind of rule;
bond pads, dummy fill, latch-up, metal slots, the guard ring).
