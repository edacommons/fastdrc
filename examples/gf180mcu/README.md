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

420 rule files in 27 sections (a rule with a value per column — 3.3 V and 5 V/6 V — is
one file and one check per column). Built as it is by default — six metals, 9K top
metal, MIM option B — that is 449 checks.

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
  from every rule's picture and runs the deck on them: of the default deck's 449 checks,
  428 behave as drawn. The 21
  that do not are limits of the generated layouts, not of the rules: the end-of-line
  enclosures (`CO.6a`, `Vn.3c`, `Vn.4b`, and `MT30.6` with the 3 µm metal — the generated line end is shorter than
  it is wide), the rules that hold within 0.43 µm of a well edge (`NP.3bi`, `NP.5ci`,
  `NP.5di`, `PP.5cii`, `PP.5dii`, `NP.11`, `PP.11`), and three exact eFuse lengths (`EF.03`, `EF.06`,
  `EF.09`).
- **Against the process's own KLayout deck**, on the same layouts and on the 41 standard
  cells of the 7-track library:
  - the cells as they are: all 355 front-end rules are silent in both decks;
  - the cells with about 1400 random small errors put in: the rules that fire flag the
    same places in both decks, but for `NP.5b` and one marker of `NP.3cii`;
  - the rules near a well edge and the end-of-line enclosures agree with it on layouts
    made by hand.
- **Not shown yet:** the LDMOS, eFuse, resistor and MIM rules behave as their own drawings
  say, but have not been compared with the process's deck on a real device of that kind;
  `NP.11` and `PP.11` have been tried on no layout that makes them fire.

## Not drawn

35 of the manual's rules that its KLayout deck checks, each for want of something the
drawing cannot say yet:

- a ring that must surround something: `DN.3`, `MDP.3`, `MDP.4`, `MDN.13d`, `MDN.17`,
  `MDP.17a`, `MDP.17c`;
- "exactly", as a minimum and a maximum at once, or "must be rectangular": `EF.04b`,
  `EF.04c`, `EF.04d`, `EF.05`, `EF.21`, `EF.22a`, `EF.22b`, `DF.3b` (the minimum half of
  `HRES.10`, `MDN.10c`, `MDN.10d`, `MDN.11` and their LDPMOS twins is drawn);
- the order of things along a device: `MDN.13b`, `MDN.13c`, `MDP.13b`, `MDP.13c`,
  `MDP.3d`;
- the rest: `DF.11`, `DV.9`, `PL.7`, `NP.4b`, `PP.4b`, `SB.15b`, `MT30.8`, `PRES.9a`,
  `LRES.9a`, `HRES.12a`, `MIM.11`, `MIMTM.11`, `NAT.6`, `DE.3`, `O.PL.ORT`.

Also not here: the per-layer grid and angle checks, and the manual's chapters its own
deck does not check either (antenna ratios are in `examples/demo` as a kind of rule;
bond pads, dummy fill, latch-up, metal slots, the guard ring).
