# Trapezoidal Sweep — compose engine spec (v0)

The batch boolean/decomposition engine that turns a layer's input polygons
(any straight angle, with holes, overlapping) into the **merged, disjoint set of
`rect` + `half-rect` primitives** written to a `.tiles`. Replaces the incremental
clip-and-insert `Region` for the compose step.

It is designed up front to **not** fragment-explode on adversely oriented
layers. Three properties are load-bearing:

1. **Per-layer sweep axis** — extend primitives along the layer's dominant
   feature direction, so tall layers don't get sliced horizontally (and vice
   versa).
2. **Extend-while-unchanged** — primitives are *maximal*; output scales with
   geometric feature count, not with the number of sweep events.
3. **Banded streaming** — peak RAM is bounded by the sweep front, independent of
   layer size; closed primitives flush to disk.

## Contract

**Input:** per layer, a set of closed polygon rings, each tagged `ADD` or `CUT`,
each with an orientation. Vertices are int32 DBU. Any straight angle; rings may
self-overlap; holes allowed (as opposite-wound rings or explicit `CUT`).

**Output:** a stream of primitives — `(x0,y0,x1,y1, shape)` with `shape ∈
{rect, halfBL, halfBR, halfTL, halfTR}` — forming a **disjoint** tiling whose
union equals `union(ADD) \ union(CUT)`, sorted in sweep order, written directly
to the `.tiles` SpanArray.

## 1. Primary-axis selection

In the edge-loading pass, accumulate `Hlen = Σ|dx|` over horizontal edges and
`Vlen = Σ|dy|` over vertical edges (diagonal length split onto both axes by its
components). Then:

    events on Y  (horizontal sweep, primitives extend in +Y)  iff  Vlen >= Hlen
    events on X  (vertical   sweep, primitives extend in +X)  otherwise

Rationale: the edges **parallel to the sweep direction** are the ones that
persist in the active set and let a primitive extend. Picking the sweep so the
*dominant* edges persist keeps tall features whole on tall layers and wide
features whole on wide layers. This only changes a constant factor — property 2
bounds the worst case regardless — so a cheap heuristic is enough. (Option: pick
per partition-band rather than per layer; or, for pathological mixes, decompose a
small sample both ways and keep the cheaper.)

The rest of this spec describes the **horizontal** sweep (events on Y, spans on
X). The vertical sweep is identical with x/y swapped.

## 2. Core algorithm

A Bentley–Ottmann-style ordered sweep with an active edge table (AET) and an
*open-primitive* table layered on top.

### 2.1 Events

Event coordinates on Y are: every vertex Y, plus every edge–edge **intersection**
Y. Intersections are discovered incrementally (only adjacent edges in the AET can
cross), so the full O(n²) crossing set is never materialized. An intersection
point off the DBU grid is **snapped** to the nearest grid point, *once*, and
reused everywhere it appears — this keeps the tiling watertight (shared corners
snap identically). Octilinear (slope ±1) crossings at integer Y are already
integer; no snap.

### 2.2 Active edge table + boolean by winding

The AET holds all non-horizontal edges crossing the current sweep line, **sorted
by X** at the sweep position (re-sorted only across an intersection). Each edge
carries its source tag (`ADD`/`CUT`) and a winding contribution `±1` from its
orientation.

Two running winding counters are maintained while scanning the AET left→right:
`w_add` and `w_cut`. A point is **solid** iff

    w_add != 0  AND  w_cut == 0          (nonzero rule)

This one rule handles union of overlapping ADDs, holes (opposite-wound rings
cancel), and explicit CUTs uniformly.

A **slab** is the strip between two consecutive events. Within a slab the AET
membership and order are constant and every active edge is a single straight
segment. Scanning the AET yields the slab's **solid spans**: maximal X-intervals
where the predicate holds, each bounded by a `left_edge` and a `right_edge`.

### 2.3 Open-primitive extend-merge (the core)

Each solid span has an **identity** `(left_edge_id, right_edge_id)`. We keep a
table of *open* primitives keyed by that identity. At each new slab, reconcile
the new solid spans against the open table:

- **same identity present** → the primitive continues; do nothing (it extends).
- **open identity no longer solid** → **close** it: emit its geometry from its
  open-Y to the current event-Y, then drop it.
- **new identity** → **open** a primitive starting at the current event-Y.

Because a bounding edge persists until a vertex ends it or an intersection splits
the span, identity-by-edge makes every primitive **maximal**: a tall plain
column is one primitive no matter how many unrelated Y-events occur elsewhere. An
event only touches the spans whose bounding edges it changes — closing/opening is
**local**, never a global flush.

### 2.4 Closing a primitive → rect + half-rect

A closed primitive is a **trapezoid**: horizontal top/bottom (`y = y_open`,
`y = y_close`), sides = the two straight bounding edges. Let the left edge's X be
`xl0` at `y_open`, `xl1` at `y_close`; right edge `xr0`, `xr1` (each snapped to
DBU).

    both sides vertical (xl0==xl1, xr0==xr1)     -> one rect
    left side slanted                            -> + one half-rect (the left
                                                    triangle; bbox aspect encodes
                                                    the slope, corner by sign)
    right side slanted                           -> + one half-rect (right triangle)

So **≤ 3 primitives per trapezoid, independent of its height** — a long slanted
side is one half-rect, never a stack of slivers. Decomposition: left triangle
bbox `[min(xl0,xl1),max(xl0,xl1)] × [y_open,y_close]`; middle rect
`[max(xl0,xl1),min(xr0,xr1)] × [y_open,y_close]`; right triangle symmetric. This
is the half-rect = `bbox − complementary corner triangle` model.

## 3. Banded streaming & partitioning

Process the sweep in **bands** along the sweep axis. Within a band, run §2 and
flush each closed primitive to the output as soon as it closes. At a band
boundary, only the currently-open primitives and the AET cross over — carry them
into the next band; nothing else is retained.

Two modes share this core:

- **Single-pass streaming** (one process): bands are just flush points; a
  primitive spanning a boundary stays open and is emitted later, uncut. Minimal
  RAM, minimal fragments.
- **Parallel per-band** (the stripe/halo partition): force-close at the band
  boundary (+halo) so each band is independent and a worker emits only its core;
  duplicates in the halo are dropped on merge. Trades a few boundary cuts for
  parallelism. The format's stripe index already supports this.

## 4. Bounds (the reason this is safe)

Let `n` = total input vertices on the layer, `k` = edge–edge intersections
(post-snap).

- **Output primitives:** `O(n + k)`. Each closes when a bounding edge ends or a
  span splits — both charge to a vertex or an intersection. With extend-merge
  there is **no per-slab and no per-event term**, so unrelated Y-events cost
  nothing. For real layout `k` is small/local; the dense-plaid worst case
  (`k = O(n²)`) is genuine geometric complexity, not a sweep artifact, and is the
  same for any rectangular tiling.
- **Peak RAM:** `O(active edges + open primitives)` = the **sweep-front width**,
  not the layer total. With banding it never grows with layer height. Output is
  streamed to disk (out-of-core); a huge layer makes a big *file*, not a big
  heap.

Contrast the naive version this avoids: slab-at-every-Y + emit-per-slab is
`O(#Y-events × #spans)` primitives and holds them all — that is the explosion.

## 5. Degenerate / robustness cases

- **Horizontal edges** (in a horizontal sweep): not in the AET; they are pure
  span-boundary events at their Y.
- **Coincident / collinear edges** (abutting shapes sharing a border): winding
  sums make the shared border interior — no double primitives, no zero-width gap.
- **Zero-area spikes / repeated vertices:** dropped during edge loading.
- **Touching at a point:** a span of width 0 is not emitted.
- **Snap-induced coincidence:** after snapping an intersection, re-check local
  ordering and merge degenerate (now-equal) coordinates.

## 6. Integration

- `Composer`/`Design` accumulate raw input polygons per layer (tagged ADD/CUT)
  instead of merging eagerly. The sweep runs **once per layer at serialize
  time**, producing the primitive tiling.
- It emits `rect` and `half_rect` straight into `geom::Writer` (which already
  supports both shapes), then `write_split`.
- The existing octilinear `edges` view, when wanted, is derived from the same
  sweep's boundary edges in the same pass.

## 7. Phasing (de-risking the hard part)

The robust intersection handling (§2.1) is the only delicate piece; isolate it:

- **Phase A — Manhattan only.** Vertical/horizontal edges; no general
  intersections (crossings are at endpoints). Gives per-axis + extend-merge +
  banding immediately — this alone fixes the fragment-explosion concern for the
  common case and replaces today's rectilinear decompose.
- **Phase B — octilinear (45°).** Slope ±1; intersections at integer Y → exact,
  no snapping. Introduces half-rect output and trapezoid closing.
- **Phase C — arbitrary angle.** Full Bentley–Ottmann adjacency intersections +
  DBU snapping. Completes "any GDS polygon."

Each phase is shippable and testable against area/coverage invariants
(`Σ primitive area == swept solid area`) before the next.
