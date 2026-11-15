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
Built as it is by default — six metals, 9K top metal, MIM option B — that is 671 checks,
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
  from every rule's picture and runs the deck on them: of the default deck's 671 checks,
  646 behave as drawn; `DE.3` and `O.PL.ORT` have no generated layout yet. The 23
  that do not are limits of the generated layouts, not of the rules: the end-of-line
  enclosures (`CO.6a`, `Vn.3c`, `Vn.4b`, and `MT30.6` with the 3 µm metal — the generated line end is shorter than
  it is wide), the rules that hold within 0.43 µm of a well edge (`NP.3bi`, `NP.5ci`,
  `NP.5di`, `PP.5cii`, `PP.5dii`, `NP.11`, `PP.11`), three exact eFuse lengths (`EF.03`, `EF.06`,
  `EF.09`), and the count of drains at a gate (`MDN.13b`, `MDP.13b` — the generated second
  drain does not reach the gate).
- **Against the process's own KLayout deck**, on the same layouts and on the 41 standard
  cells of the 7-track library:
  - the cells as they are: all 355 front-end rules are silent in both decks;
  - the cells with about 1400 random small errors put in: the rules that fire flag the
    same places in both decks, but for `NP.5b` and one marker of `NP.3cii`;
  - the rules near a well edge and the end-of-line enclosures agree with it on layouts
    made by hand.
- **On devices made by hand**, each with deliberate errors (an LDNMOS and an LDPMOS finger
  with and without its guard ring, with the drift overlap and the gate-to-drain space off
  either way; a high-resistance, a P+ and an N+ poly resistor; MIM capacitors on one
  plate; dummy-exclude areas; OTP gates either way; a well with two kinds of PMOS; a
  shape off the grid and one with an acute corner): the 34 things asked of `MDN.10d`,
  `MDN.11`, `MDN.13b`, `MDN.17`, `MDP.3`, `MDP.13b`, `HRES.10`, `MIMTM.11`, `DE.3`,
  `O.PL.ORT`, `DV.9`, the grid and the angle come out the same in both decks.
  On the same devices this deck still reports what the process's deck does not:
  `MDN.10b` and `MDP.9b` on every LDMOS (the poly extension "in the width direction" is
  measured in every direction here), and `MDN.5c` on an LDNMOS with no substrate tab at
  all. The LDPMOS made by hand is not known to be a legal one, so what the two decks say
  of it beyond the rules above (`MDP.11`, `MDP.3c`, `DV.1`, `MDP.3d`) is not settled.
- **Not shown yet:** the LDMOS, eFuse, resistor and MIM rules behave as their own drawings
  say, and the ones named above agree with the process's deck on a device made by hand;
  the rest have not been compared with it on a device of that kind, and none on a device
  from a real design. The process's deck
  asks more of a guard ring than is drawn here (one well per ring, nothing else in it);
  `NP.11` and `PP.11` have been tried on no layout that makes them fire.

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
