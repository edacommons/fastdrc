# Compiled Zones format — v0 (`FDCZ`)

A filter/checker's **output** sidecar. A *zone* is an axis-aligned area of
interest or a violation marker; zones are grouped into **sets** (one set per
rule or stage). The file is chainable: it can be a checker's final result, or an
extra input handed to a downstream filter that refines only inside these zones
(the flow-of-filters DAG).

It deliberately mirrors the geometry format (`FDCG`,
[geometry-format-v0.md](geometry-format-v0.md)): little-endian, packed structs
written verbatim, section arrays 8-byte aligned, a length-prefixed JSON
manifest, and a directory + footer at the end so it can be **streamed** (no
seek-back) and **mmapped** zero-copy.

## File layout

```
+--------------------------------------------------+ offset 0
| Header (16 B)                                    |
+--------------------------------------------------+
| Set 0 zone arrays (SoA, 8-byte aligned):         |
|   x0[count] y0[count] x1[count] y1[count]  (i32)  |  box_off
|   value[count]                             (i64)  |  value_off
|   kind[count]                              (u8)   |  kind_off
| Set 1 zone arrays ...                            |
| ...                                              |
+--------------------------------------------------+
| Manifest: u64 len + JSON bytes                   |  manifest_off
+--------------------------------------------------+
| Set directory: ZoneSetDesc[set_count] (32 B ea.) |  set_dir_off
+--------------------------------------------------+
| Footer (72 B) — last 8 bytes are end_magic       |
+--------------------------------------------------+ EOF
```

## Structures (`include/fastdrc/zones_format.hpp`)

- **Header** (16 B): `magic="FDCZ"`, `version=0`, `flags` (bit0 = little-endian).
- **ZoneSetDesc** (32 B): purely structural — `box_off`, `value_off`,
  `kind_off`, `count`. The set's **id is its index** in the directory
  (auto-assigned); all semantics live in the manifest, keyed by that id.
- **Footer** (72 B): `set_dir_off`, `manifest_off`, `set_count`, `file_size`,
  `bbox[4]` (union of all zone boxes), `dbu_per_micron`, `version`, `flags`,
  `magic`, `end_magic="FDCZEND\0"`.

## Per-zone data

| array      | type | meaning                                             |
|------------|------|-----------------------------------------------------|
| x0,y0,x1,y1| i32  | the zone's axis-aligned box (DBU)                   |
| value      | i64  | measured value (e.g. the gap found); 0 if N/A       |
| kind       | u8   | `0` interest, `1` violation; 2..15 reserved         |

Within a set, zones are sorted by `(y0, x0)` for a deterministic,
query-friendly order.

## Manifest (JSON)

Read once (not the hot path). Carries `format`, `version`, `generator`,
`dbu_per_micron`, `bbox`, and `sets[]` — each `{id, rule, name, severity,
count}`. Extend with more fields (source layers, value units, waiver refs, …)
without touching the binary.

## Validation

A reader checks `Header.magic`, `Footer.end_magic`, and `Footer.file_size ==
actual size` before trusting any offset.
