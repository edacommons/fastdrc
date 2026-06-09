# Compiled Layout Geometry — format v0 (final)

A flat, mmap-able, zero-copy binary holding composed layout geometry, produced
once and consumed by independent, specialized tools (DRC, metal fill, PEX). It
is a *compiled* format (the "object code" of layout) — not an archival one like
GDS/OASIS.

**One file = one layer of pure geometry.** A `.tiles` carries no source/semantic
metadata (no GDS layer/datatype, no name) — just geometry plus structural facts
(dbu, bbox). This keeps it a *general* geometry container: the result of a
boolean op between two layers stores as just another `.tiles`. A design is a set
of such files tied together by an external **index** (`index.json`) that holds
the `id -> file + source (gds_layer/datatype, name) + derivation` mapping and
the design-wide dbu/bbox. See the producer's `write_split`.

Design rules:
- **Zero-copy / mmap:** all structures are read in place; **offsets and indices,
  never pointers**; positions are absolute byte offsets from file start.
- **SoA:** parallel typed arrays (SIMD/GPU friendly), not arrays of structs.
- **Streaming-writable:** data is written forward; the navigation directory is a
  **footer** at the end, so the writer never seeks back.
- **Partial read:** each tool reads (via the footer) only the sections it needs;
  mmap demand-paging faults in only touched pages.
- **Extensible:** versioned; sections are optional (offset 0 == absent), so new
  sections (edges, stripe) don't break existing readers.

Conventions: little-endian; coordinates are `int32` in DBU; all section offsets
are `uint64` and **8-byte aligned** (pad with zeros).

## The geometry primitive

A primitive (a `Tile`) is its **bounding rect with up to one straight cut per
side**, taking a corner triangle off. Each cut is a **signed horizontal extent**
over the full tile height `H = y1 - y0`:

    cut_left  == 0 : vertical left edge at x0.
    cut_left  >  0 : top-left cut;     left edge (x0, y0) -> (x0+cut_left, y1).
    cut_left  <  0 : bottom-left cut;  left edge (x0-cut_left, y0) -> (x0, y1).
    cut_right == 0 : vertical right edge at x1.
    cut_right >  0 : bottom-right cut; right edge (x1-cut_right, y0) -> (x1, y1).
    cut_right <  0 : top-right cut;    right edge (x1, y0) -> (x1+cut_right, y1).

The cut's slope is `H / extent`, so **any straight angle is representable** — 45°
is just `|extent| == H`, nothing special. This one family subsumes rect (no
cuts), half-rect/triangle (one cut), trapezoid (two adjacent cuts), and
**parallelogram** (two opposite cuts), at any angle — so an angled wire's slice
is a *single* tile, not a staircase. There are no "positive half-rect" objects: a
triangle is just `rect − a corner cut` (with `|cut| == width`).

    area = bbox − Σ |cut|·H / 2.   (bbox is the spatial handle; the cuts carry
                                    the exact straight shape — no approximation.)

Off-grid cut positions (from non-45° slopes, e.g. a stroked angled path) are
snapped to the DBU grid when the geometry is produced. **Curves** (exact bends,
e.g. photonics) are a reserved future section carrying Bezier control points;
they don't touch the straight hot path. The bbox is always the spatial handle —
indexing / partitioning / quick-reject work on bboxes uniformly.

The **`edges` section is an optional, precomputed octilinear *view*** of this
geometry, for the DRC spacing/width fast path. It is present only when the
geometry is octilinear; for general-angle or curved geometry it is absent and an
engine derives what it needs from the primitives (e.g. a BVH over bboxes).

v0 scope: rect + straight-cut tiles (any angle); curves, the `edges` view, the
diagonal edge views (D1, D2), and the `stripe` section are reserved and
may be absent (`*_off == 0`).

## File layout

    [ Header        ]   16 B, at offset 0
    [ data blocks   ]   span array, optional edge / stripe arrays, manifest
                        — streamed, any order
    [ Footer        ]   152 B, at file end; last 8 bytes are the end sentinel

The footer holds the layer's section table inline — there is no separate
directory, because a file is a single layer.

### Header (16 B, offset 0)

    char   magic[4]     = "FDCG"    // FastDRC Compiled Geometry
    uint16 version      = 0
    uint16 flags                    // bit0: little-endian (1 in v0)
    uint64 content_hash             // FNV-1a-64 of all bytes after this header

(The header lets a tool sniff the file type up front; the footer is the
authority for navigation. `content_hash` fingerprints the file: a 16-byte read
tells layers apart — deterministic geometry yields identical bytes, hence an
identical hash, so it's a fast change-detect / dedup key. 0 means unset.)

### Footer (152 B, at file end)

The single layer's section offsets/counts, inlined, plus file-level facts:

    uint64 span_off                 // 0 if none
    uint64 span_count
    uint64 edge_off[4]              // H=0, V=1, D1=2, D2=3; 0 = none (reserved)
    uint64 edge_count[4]            // reserved
    uint64 stripe_off               // 0 = none (reserved)
    uint64 stripe_count
    uint64 manifest_off             // 0 if none; -> length-prefixed JSON blob
    uint64 file_size
    int32  bbox[4]                  // x0, y0, x1, y1 in DBU
    int32  dbu_per_micron           // e.g. 1000
    uint16 version                  // == header version
    uint16 flags
    uint32 reserved   = 0
    char   magic[4]   = "FDCG"
    char   end_magic[8] = "FDCGEND\0"   // the last 8 bytes of the file

Reader: read the last 8 bytes, check `end_magic`; the footer is the preceding
160 bytes; then read sections via the inlined offsets.

## Sections

### SpanArray — the primitives (`span_off`, `m = span_count`)

The solid geometry as a stream of primitives, **sorted ascending by `y0` then
`x0`** (area / windowed density / fill / PEX overlap, and the source the `edges`
view is derived from). SoA:

    int32 x0[m], y0[m], x1[m], y1[m]       // bounding rect
    int32 cut_left[m], cut_right[m]        // signed cut extents (see above)

    x0 at span_off, y0 +4*m, x1 +8*m, y1 +12*m,
    cut_left +16*m, cut_right +20*m        // then pad to 8

A plain rectangle has `cut_left == cut_right == 0` (the Manhattan common case);
non-zero cuts carry any straight angle. Curves (reserved) would carry an exact
Bezier whose control points live in a future variable section.

### EdgeArray — optional octilinear view (`edge_off[k]`, `n = edge_count[k]`)

A precomputed boundary-edge view for the octilinear DRC fast path, present only
for octilinear geometry (else `edge_off[k] == 0`). Edges of one orientation,
**sorted ascending by `pos`**. SoA, the four arrays laid out contiguously
(offsets derived from `edge_off` and `n`):

    int32 pos[n]    // line coordinate: y for H edges, x for V edges
    int32 lo[n]     // extent start along the edge: x for H, y for V
    int32 hi[n]     // extent end
    uint8 flag[n]   // bit0: solid is on the larger-coordinate side
                    //   (above for H, right for V)

    pos  at edge_off
    lo   at edge_off + 4*n
    hi   at edge_off + 8*n
    flag at edge_off + 12*n         // then pad to 8-byte alignment

Facing pairs for spacing: a `+side` edge of one shape faces a `-side` edge of
another across the gap.

### StripeIndex (`stripe_off`, `s = stripe_count`) — optional

Spatial partition for parallel workers. v0 partitions on x via the V-edge array:

    int32  cut[s]          // x boundaries, ascending
    uint64 v_edge_start[s] // index into the V EdgeArray where pos >= cut[i] begins

A worker for stripe `[cut[i], cut[i+1])` reads V edges
`[v_edge_start[i], v_edge_start[i+1])`, extended by the halo (max rule distance).

### Manifest (`manifest_off`) — optional

A **length-prefixed JSON blob**: `uint64 len; char json[len];`. Read once at
startup (never in the hot loop), so the binary arrays stay pure. It is
**structural only** — format identity and geometric facts:
`{format, version, generator, dbu_per_micron, bbox}`. It deliberately carries
**no** source/semantic info (no names, no GDS layer/datatype) — that lives in the
external index. New structural fields may be added at any time; readers ignore
what they don't know.

## The index (external, `index.json`)

A design is a directory of per-layer `.tiles` files plus an index that ties them
together. The index is plain JSON (read once; parsing is the consumer's choice):

    {
      "format": "FDCG-index", "version": 0, "generator": "fastdrc",
      "dbu_per_micron": 1000,
      "bbox": [x0, y0, x1, y1],
      "layers": [
        {"id": 0, "file": "metal1.tiles", "gds_layer": 10, "gds_datatype": 0,
         "name": "metal1"},
        ...
      ]
    }

The index is the *only* place naming / source / derivation lives. A derived
layer (e.g. `metal1 AND poly`) is just another `.tiles` with its own index entry
(name, and later a derivation record) — the geometry file itself is unchanged in
kind.

## Versioning

`version = 0`. Readers must reject a higher major version they don't understand,
and may ignore unknown optional sections. New sections are added via new footer
fields in later versions; existing fields never change meaning.
