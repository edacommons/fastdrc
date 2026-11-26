# fastdrc-gdscheck

A [FastDRC](https://github.com/edacommons/FastDRC) backend for
[gdscheck](https://github.com/aesc-silicon/gdscheck): design rules drawn by example,
written as a gdscheck PDK.

    pip install fastdrc-gdscheck

    fastdrc build mydeck --with gdscheck        ->  build/<name>.gdscheck.pdk/pdk.yml
                                                                          decks/rules.yml
                                                                          suites/main.yml
    gdscheck run --process build/<name>.gdscheck.pdk/pdk.yml --suite main \
                 --input chip.gds --topcell TOP --report chip.lyrdb

With `gdscheck` on the PATH (or named in `GDSCHECK_BIN`) FastDRC runs it itself:

    fastdrc check chip.gds mydeck --backend gdscheck
    fastdrc selftest mydeck --backend gdscheck      do the rules behave as drawn, in gdscheck?

## What is written

| a drawn rule comes to | in the gdscheck deck |
|---|---|
| a layer made from others (`comp and nplus`, grown, selected by what it touches ...) | a virtual layer, its sentence |
| a width | `min_width` |
| a space on one layer | `min_space` and `min_notch`, under one id |
| a space between two layers | `min_space` on both; shapes drawn edge to edge are reported (`abutting: report`) where the drawn rule reports them |
| an enclosure | `min_enclosure`, measured as KLayout measures it; a shape crossing the edge is a `forbidden` of its own |
| an enclosure at the end of a narrow line | `min_enclosure` with `sides: line_end` |
| an enclosure on the sides next to a short one | `min_enclosure` with `sides: adjacent` |
| an overlap | `min_overlap` |
| a gate's length, a transistor's width | `min_gate_length`: the layer between the walls it shares with the gate |
| the space of vias in an array | `min_space` with `rows`, `cols`, `pitch`, `count` |
| a layer's share of the chip | `min_density`, `max_density`, of the whole layout's extent (see below) |
| vertices on the grid | `offgrid` |
| no corner sharper than an angle | `no_corner` |
| a shape that must not exist, an exact size, a required cover, a count | `forbidden` on the layer the rule leaves over |
| a space or an enclosure between walls (edges cut out by other layers) | the same check on edge layers |
| a space between shapes of different nets | `min_space` with `net: different` |
| shapes of several nets under one marker | `max_nets_under` |
| an antenna ratio (area or side wall, diodes counted) | `antenna_ratio` |
| no bend of a line on a layer | `no_corner` with the layer |
| the area of capacitors sharing a plate | `max_area`, `scope: contained` |
| the gaps a space or an enclosure finds, read as a layer by the rule | a virtual layer: `separation_below`, `enclosure_below` |
| a layer reaching further than an exact distance past chosen edges | `enclosure_above`, at those edges |
| edges that do not face another layer within a distance | the edges, or their parts, no `separation_below` gap lies on |
| an enclosure that may fall short on two opposite sides, not on two that meet | `min_enclosure` with `sides: opposite` |
| the space of a wide line running alongside another for a length | `min_space` with `width`, `length` |
| the space of vias in a block of so many rows and columns, tight both ways | `min_space` with `rows`, `cols` |
| a layer's share of every window, or of the layer that is the die | `min_density`, `max_density` with `scope: window`, `boundary` |
| a layer's share of each wide plate of another | `min_density` with `scope: region` |
| an antenna ratio summed level by level, for nets with or without a diode | `antenna_ratio` over the levels, with `diode` |
| a space between different nets over two layers together | `min_space` with `net: different` on each layer and across |

**A rule of one column of a manual's table** (3.3 V / 5 V), or one drawn inside or apart
from a marker: the check reads the shapes of that column. Not under the marker: every
layer of the check without the shapes that touch it. Under it: those shapes, and for a
space or a check of two layers also each pair with one of them in it. (The KLayout
backend selects the violations by the marker instead; the two agree wherever a shape
lies wholly in or out of the marker, which is how such markers are drawn.)

**The chip, for a share of it.** gdscheck takes the chip as the extent of the layers
its rules read. A deck with such a rule therefore has one more entry, `fastdrc.extent`,
which reads every layer of the process and can report nothing; run by FastDRC it also
reads every other layer found in the layout. It is not a rule and is not listed in
FastDRC's results.

**Nets.** KLayout connects, for each rule, what that rule says; gdscheck has one
ordered list of connections for a deck, and an antenna rule reads a beginning of it. The
rules' own sets lie one inside the next (metal by metal), so the list is written in that
order and each antenna rule names where its own ends (`level`, `level_before`). A rule
whose set would not fit is left out and named. KLayout also joins two shapes that
only touch and gdscheck those that overlap: where a rule connects the two halves of a
layer (`poly not diff` with `poly and diff`), the connection is written through the
layer whole, which gives the same nets.

**Where gdscheck measures otherwise.** A check narrowed by a marker reads the shapes
(above). A space in KLayout's projection metric between two edge layers is written as
gdscheck's plain space between them.

## What is not, yet

A rule is written whole or not at all. One with a part that has no form here is left
out and named, with the reason, at the head of `pdk.yml` and by `fastdrc build`:

    DCF.1a is not in the gdscheck deck: the layout's extent as a layer

What has no form in gdscheck 0.2.2, in any deck tried: a sum of the sides of the shapes
in one (gdscheck sums areas), the layout's extent as a layer, the centre of a shape and
a distance between centres.

## Status

A first version, written to gdscheck's documented format and run through gdscheck
0.2.2 with `fastdrc selftest DECK --backend gdscheck` -- the test layouts and what each
rule must report come from the drawings, not from any backend. The forms were first
found on the GF180MCU deck; the other decks were run afterwards, to see that nothing
here holds for one process only, and the forms they brought were added:

| deck | rules | written | behave as drawn | do not | no test yet |
|---|---|---|---|---|---|
| GF180MCU (`fastdrc-gf180mcu`) | 1045 | 1038 | 1009 | 0 | 30 |
| IHP SG13G2 (a part) | 63 | 63 | 63 | 0 | 0 |
| FreePDK45 (a part) | 37 | 37 | 37 | 0 | 0 |
| SKY130 (a part) | 30 | 30 | 30 | 0 | 0 |
| the demo deck | 18 | 18 | 18 | 0 | 0 |
| the figures of the drawing guide | 7 | 4 | 4 | 0 | 0 |

The IHP, FreePDK45 and SKY130 rows are small test decks that are not shipped with
FastDRC. A deck with forms not met yet will have rules left out, each named with the
reason.

The limits of gdscheck apply to a run: `GDSCHECK_THREADS` is passed on as `--threads`;
gdscheck itself reads `GDSCHECK_MEMORY` and `GDSCHECK_TILE_UM`. The self-test's layout
is one long, sparse row of cases, for which gdscheck's 20 µm tile is slow and takes
gigabytes; `GDSCHECK_TILE_UM=500` runs it in seconds.

gdscheck is a separate program under its own licence (AGPL-3.0); this package only
writes files for it and starts it.
