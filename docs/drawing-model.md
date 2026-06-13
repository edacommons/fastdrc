# Drawing DRC Rules — the geometry-by-example model

How a DRC rule is **drawn** so the compiler can turn it into a real check
(KLayout `.drc`, and eventually the FastDRC engine). This is the reference for the
rule editor and for `tools/validate_klayout.py` (`as_drawn`), which builds these
shapes for every deck rule it validates.

## The core idea

A rule is **`shapes` + `measures` + `constraints`**.

- The **drawing carries the topology** — how shapes relate (inside / overlapping /
  abutting / disjoint). The compiler reads relations off the geometry, it does not
  take them from text.
- **Constraints carry only the non-visual facts** — which layer a shape is, a net,
  a numeric threshold. Anything you *can* see, you draw; anything you can't, you
  write as a constraint.

So you draw an **example** of the situation the rule is about, and annotate it.

> **You draw real shapes of real layers — never layer algebra.** You never draw or
> write `.and`, `.not`, `.inside`, `.outside`, `.not_interacting`, a "derived
> layer", a selection, or a scope. You draw `comp` and `mos_cap_mk` as two real
> shapes that overlap; the **compiler** looks at how they relate and *heuristically
> derives* that it must build `comp.and(mos_cap_mk)` to implement the rule. Every
> "**→ compiles to**" line below is the **compiler's output** — what it produces to
> run the check — not something you draw or type. The whole point is that you think
> in geometry and the compiler thinks in operations.

---

## Edges: wavy vs straight

Every shape is a closed loop of edges. Each edge is one of:

- **straight** (`edge: true`) — a **real, oriented** edge that the rule actually
  uses (it is measured, or its orientation/corner matters). It has a direction, so
  two straight edges can be "the same orientation".
- **wavy** (`edge: false`) — a **don't-care boundary**: the rule places no
  constraint on it. It is *arbitrary*. For a wire's open ends that reads as "any
  length" (the shape continues); for a closed shape whose outline is irrelevant it
  reads as "any polygon". A wavy edge does **not** mean the shape is unbounded — it
  means *this edge's geometry is unspecified*.

> A min-width rule is **not** a fixed box — it's a *wire*: two parallel **straight**
> edges the width `W` apart, **wavy** in the other direction (any length).

Rule of thumb: **straight = the edges the rule uses; wavy = everything else.** A
min-**area** shape, for instance, has *no* measured edge — only its total area
matters — so **all of its edges are wavy**: the drawn rect stands for "any
polygon", and that polygon's area is perfectly well-defined regardless of the
(arbitrary) outline.

---

## Measures: a real distance vs a topological assertion

A measure is a named distance between two edges. **The edges' kind decides what it
means:**

- between **straight** edges → a **real oriented distance** → a *dimensional
  check* (width / space / separation / enclosing).
- between **wavy** edges → **not a distance at all**, but a **topological
  assertion** about how two shapes relate. It picks an `inside` / `outside` /
  `interacting` / `overlapping` selection from the *relation you drew* (see
  *Restrict the subject…*): inside → `inside`, touch → `interacting`, overlap →
  `overlapping`, disjoint → `outside`. For the disjoint case the operator carries
  the touch semantics — `X >= 0` → "they don't overlap, but the gap may be **zero**
  (may touch)" → an **`outside`** selection (KLayout `outside` keeps polygons that
  merely touch). A *drawn* touch (coinciding edges), by contrast, is `interacting`.

This is the single most important convention: **straight measure = check, wavy
measure = relation.**

---

## Constraints: `match` vs `check`

- **`match`** — identifies *which* geometry the rule is about: `A.layer == metal2`,
  `M0.layer == dualgate`, a wavy `GS >= 0`. These are preconditions.
- **`check`** — the *single* assertion that is verified. A violation is a matched
  configuration where the `check` fails (e.g. `W >= 0.28`).

Rule of thumb: layer/marker/relation facts are `match`; the one dimensional bound
being enforced is `check`.

---

## Recipes — how to draw each rule

Each entry: **what you draw** → **what the compiler derives** (the `→` is the
compiler's output, not anything you draw — see the box above).

### Min width
One rect (a wire). The two parallel edges that define the width are **straight**;
the other two are **wavy**. A measure `W` between the straight edges.
`A.layer == metal1` (match), `W >= 0.28` (check).
→ `metal1.width(0.28)`

### Min space (same layer)
Two rects of the **same** layer, side by side. The two **facing** edges are
straight; a measure `S` across the gap.
`A.layer == metal1`, `B.layer == metal1`, `S >= 0.28`.
→ `metal1.space(0.28)`

### Min separation (different layers)
Same as space, but `B` is a **different** layer.
→ `metal1.separation(comp, 0.28)`

### Enclosure
An **inner** rect inside an **outer** rect. A measure `E` from the outer edge to
the matching inner edge (the margin).
→ `outer.enclosing(inner, 0.22)`

### Min area
One closed rect with **all edges wavy** — it stands for "any polygon"; only the
total area is constrained, no edge is measured. `A.area >= 0.2025`.
→ `comp.with_area(nil, 0.2025)`

### A check on the overlap of two layers (a device)
Draw **both real layers**, with the second one **covering the measured region**
(the part you measure sits inside it). You draw `comp` and `mos_cap_mk` — two
shapes. The compiler sees the second layer covers what you measure and *derives*
the combined layer for you.
→ compiler produces `comp.and(mos_cap_mk)`, then runs width/space/area on it.

### A check on a layer with a region removed
The mirror of the overlap case. Draw `otp_mk` as a **real shape inside** `comp`
(the part to remove) **with a real (straight) edge** — its boundary cuts `comp`, so
it's a device layer, not an all-wavy domain — and put the measured geometry on the
`comp` that is **not** under it. The compiler sees that what you measure sits
*outside* `otp_mk` and derives the subtraction. (No tagged hole — a real cutout.)
→ compiler produces `comp.not(otp_mk)`

> **Combine vs remove vs filter — read off the drawing, no special markup.** It
> comes down to *where `B` sits relative to your measurement*, and — when it's off
> the measurement — whether `B` is a device (a real edge) or a domain (all wavy):
>
> | `B` relative to the measurement | meaning | compiler derives |
> |---|---|---|
> | **covers** it | combine | `comp.and(B)` |
> | **off** it — a cutout inside the subject, with a real (**straight**) edge | remove | `comp.not(B)` |
> | **off** it — all wavy (a domain), inside or around the subject | filter | a scope (`.overlapping` / `.not_interacting` / `.interacting`) |
>
> A layer that **covers the measurement is always `.and(B)`** — the deck writes
> `.and` whether `B` is a true device or just a domain marker that happens to cover
> (they're equivalent there). The device-vs-domain question (real edge vs all wavy)
> only matters when `B` is **off** the measurement: a real edge = a device being
> cut out (remove); all wavy = a domain marker (filter).

### Restrict the subject by a relationship to another layer
Draw a **context shape** of that other layer and a **wavy measure `>= 0`** from the
subject to it. The *relation* you draw is what the compiler reads — it derives the
selection op:

| what you draw (subject vs context) | compiler derives |
|---|---|
| subject **inside** the context | `X.inside(Y)` |
| **disjoint** (drawn apart; the wavy `>= 0` allows a zero gap) | `X.outside(Y)` |
| **touching** (coinciding edges — one shape's edge *is* the other's) | `X.interacting(Y)` |
| **overlapping** (interiors cross) | `X.overlapping(Y)` ≡ `X.not_outside(Y)` |

The **exact relation you draw is the signal** — `touch` and `overlap` are different
pictures with different meanings. A *touch* (coinciding edges) counts as
`interacting`; a genuine *area overlap* is `overlapping` (equivalently
`not_outside` — "not wholly outside" = has overlap). To get `outside` you draw the
shapes **apart** (disjoint); the wavy `>= 0` measure just says the gap *may* reach
zero. This matches the scope table below exactly (touch→`interacting`,
overlap→`overlapping`) — selection and scope read relations the same way.

The **wavy measure is the signal** that this is a *selection*: drawing one to a layer makes it a *selection*
(`X.inside(Y)`, keeping whole polygons). The *same* containing layer drawn with **no
measure** — just covering — would instead be a *combine* (`X.and(Y)`, the
overlap-of-two-layers recipe above). They're behaviourally close when the subject
is inside `Y`, but the measure is what tells them apart. Apply the wavy measure to
**each** subject shape (both rects of a space rule), and you can chain several
(`X.inside(A).inside(B)`). The separation **argument** can be selected the same
way: a wavy measure from `B` to its context → `X.separation(Y.outside(Z))`.

### A selected marker (the context has its own selection)
The context shape itself can be a *selected* thing —
`via1.interacting(metal2.interacting(fusetop))` = "a via1 touching a **metal2 that
itself touches fusetop**". You draw it by giving the **context its own context**:
draw the marker `metal2` in its relation to the subject (a wavy `>= 0`), then draw
`fusetop` in its relation to the *marker* with **another wavy `>= 0` from the marker
to it**. The compiler reads the inner relation back onto the marker, producing the
nested `metal2.interacting(fusetop)`. The marker can also be a **device** — give it
a wholly-covering doping layer and the marker becomes `comp.and(pplus)` (a P+
diffusion), so `pcomp.inside(nwell)` is "draw a comp, dope it pplus, put it in a
well." A marker can also carry a **cutout** (`poly2.not(v5_xtor)`) — draw a hole of
that layer inside the marker, same as a NOT on the subject. And `inside(A&B&C)` is
the same as `inside(A).inside(B).inside(C)` (a polygon inside the intersection is
inside each operand), so a multi-layer `inside` context is just **several `inside`
selections** — draw a containing context per layer.

### Negated selections (`not_inside`): label several instances
The positive selections above are each a *single* relation — one picture. A
**negation** like `not_inside(Y)` ("keep the X polygons **not** wholly inside Y")
has no single positive picture: it keeps a whole *family* of relations (outside,
touching, straddling) and drops only one (wholly inside). So you draw the subject
layer **more than once** and **label** each instance flagged (a violation) or not:

- the **flagged** instance carries the check/measurement (the violation), drawn in
  a kept relation — e.g. the subject **outside** Y, and a second copy **straddling**
  Y's edge;
- a **not-flagged counter-example** — the same layer drawn **wholly inside** Y —
  shows the relation that is *carved out*.

A selection is really a **partition of relations into flagged / dropped**, and
every op is a distinct row of this table (✓ = that relation is kept):

| op | inside | disjoint | touch | overlap |
|---|---|---|---|---|
| `inside` | ✓ | | | |
| `not_interacting` | | ✓ | | |
| `outside` | | ✓ | ✓ | |
| `overlapping` ≡ `not_outside` | ✓ | | | ✓ |
| `interacting` | ✓ | | ✓ | ✓ |
| `not_inside` | | ✓ | ✓ | ✓ |

The compiler reads the relations of the labeled instances (inside → dropped,
disjoint + overlap → kept) and **looks up the matching row** → `X.not_inside(Y)`.
You only draw the extra counter-example(s) for a **negation**; a positive
selection stays a single flagged instance (everything else dropped by default).
Nothing here is derived — just the subject layer drawn a few times in its natural
relations, each labeled.

### Restrict a rule to a domain / region (a marker layer)
Draw a marker-layer rect **around** the subject, with **no measure** (fully
separate). The compiler derives the scope op from the relation:

| what you draw (no measure) | compiler derives |
|---|---|
| disjoint | `.not_interacting(marker)`  (e.g. 3.3 V domain) |
| overlapping | `.overlapping(marker)`  (e.g. 5 V domain) |
| touching | `.interacting(marker)` |

> **Disjoint-with-no-measure vs disjoint-with-a-wavy-`>=0`-measure** is the
> deliberate distinction between a strict **scope marker** (`not_interacting`,
> drops touching) and an **`outside` selection** (keeps touching). Same picture,
> different meaning — the measure is the signal.

### Acute / non-45 angle
A shape with a **straight** edge at an acute orientation. Constrain the edge's
angle: `a2.angle > 0 and a2.angle < 45`.
→ `comp.with_angle(0 .. 45)`

### Corner
A shape with a corner (two adjacent straight edges). Constrain the corner's
magnitude: `a1.corner == 90`.
→ `poly2.corners(90).sized(0.1).or(poly2.corners(-90).sized(0.1))`  (both convex
and concave 90° corners).

`corners()` returns the corners as zero-area **points**, so the `.sized(0.1)`
inflates each into a small 0.1 µm marker box so it can be output (the corner
analogue of `.extended` for edges). That `0.1` is **not drawn** — it's a fixed
backend constant (a marker-box size, matching the deck); it doesn't change *which*
corners are flagged, only that they're outputtable.

### Edge length
A shape with a **straight** edge; constrain its length:

| constraint | compiles to |
|---|---|
| `e.length <= 0.34` | `layer.edges.with_length(nil, 0.34)` |
| `e.length == 0.22` | `layer.edges.with_length(0.22)` |
| `e.length != 0.22` | `layer.edges.without_length(0.22)` |
| `e.length >= 0.1 and e.length <= 0.5` | `layer.edges.with_length(0.1, 0.5)` |

### Min/max size (contact/via) — edges as the output
A contact/via must be *exactly* the right size, so any edge of the **wrong length**
is a violation. Draw the layer with a straight edge and `e.length != 0.22`; the
flagged edges are rendered as thin polygons via `.extended` so they're outputtable:
→ `contact.edges.without_length(0.22).extended(0, 0, 0.001, 0.001)`

### Gate width (poly over the gate) — a directional edge width
A transistor gate is a **poly line crossing a comp region**; the channel width is
the poly width *at the crossing*. Draw the poly `A` (its two measured edges
**straight**), and a `comp` rect that **crosses** it (a partial overlap — comp runs
*through* the poly, as a real gate does). The compiler reads that crossing relation
and derives the gate `tgate = poly2 & comp` as the edge-marker. Mark the width as an
**edge** measure (a directional, edge-to-edge distance, not a polygon width). Two
neighbours are told apart **by how they sit on the subject**, exactly like combine
vs filter elsewhere:

| what you draw, relative to the poly | compiler derives |
|---|---|
| a layer **crossing** it (partial overlap) | the **gate** — `.and((poly2 & it).edges)` |
| a mask layer **containing** it (full cover) | a **region filter** — `.and(mask)` |
| a cutout **inside** it (real edge) | **remove** — `.not(cut)` |

→ `poly2.edges.and((poly2 & comp).edges).not(otp_mk).and(ymtp_mk).width(0.13)`

### Narrow-region / range selector (`.drc`)
A width/space/separation/enclosure measure with a **MAX** bound (or a two-sided
range) — this **selects** the narrow region instead of **checking** for a
violation:

| constraint | compiles to |
|---|---|
| `W <= 0.34` | `metal1.drc(width <= 0.34)` |
| `S <= 0.3` (same layer) | `metal1.drc(space <= 0.3)` |
| `S <= 0.2` (diff layers) | `metal1.drc(separation(comp) <= 0.2)` |
| `E <= V` (inner inside outer) | `via.drc(enclosed(metal) <= V)` |
| `0.04 <= E < 0.06` | `via1.drc(0.04 <= enclosed(metal1) < 0.06)` |

(`W >= V` checks for a violation; `W <= V` selects what is narrower than `V`.)

### A configuration that must never exist (the cross)
Most rules check a *quantity* ("this distance is too small"). The other half of DRC
forbids a *configuration outright* ("these two must never overlap"). You draw the
forbidden picture and put a **cross** on the measure — a measure whose bound is
**"never"** (not `>= V`). There's no threshold: the drawn **relation itself is the
rule**, and the compiler reads it exactly like a selection (`_rel_to_sel`):

| what you draw (A vs B) | how you mark it | compiler derives | reads as |
|---|---|---|---|
| **disjoint** (a gap) | cross **in the gap** between the two edges | `A.outside(B)` is the violation | A must **interact** with B |
| **touch** (edges coincide) | cross drawn **over the shared edge** (an X on it) | `A.interacting(B)` is the violation | A must **not touch** B |
| **overlap** (interiors cross) | cross over the overlap | `A.overlapping(B)` is the violation | A must **not overlap** B (touching OK) |
| **inside** | cross, A drawn inside B | `A.inside(B)` is the violation | A must **not be inside** B |

The flagged region *is* the output (no `width`/`space` op, no threshold) — `outside(B)`
keeps the A's with no contact, `interacting(B)` keeps the A's that touch-or-overlap,
etc. So the violation is "every A in *this relation* to B."

> **Why the X sits on the edge for the touch case.** Start from the disjoint picture
> (cross *in the gap* between A's edge and B's edge). Now slide B until those two
> edges **coincide** — A and B now touch. The cross was *between* the two edges, but
> they're now at the same place, so it has nowhere to live — you draw it **across the
> coinciding edge** (an X over the contact). That X-on-the-edge is the natural mark
> for "this contact is forbidden": touch → `interacting` → must not touch.

The gap *size* never matters (close vs far are both "disjoint" → same rule); a cross
has no distance. For a distance bound, use a normal `>= V` measure instead.

> **Future extension — a directional cross.** Today the cross reads the *shape*
> relation, so which specific edges you pick doesn't change the result. If a rule ever
> needs "*this particular* edge of A must abut B" (a directional contact), letting the
> cross read the **edge pairing** (not just the shapes) is the clean way to express
> it — the same way straight edges already carry orientation for angle/corner rules.

### A rule with two alternative cases (OR)
Some rules count more than one configuration as the same violation. E.g. **NP.11**
forbids butting nplus/pcomp *"within 0.43 µm of the Nwell edge (outside DNWELL) and
of the LVPWELL edge (inside DNWELL)"* — two different cases (different reference
layer, different region), one named rule.

So you **don't draw one picture** here — you draw **each case as its own complete
rule** (its own shapes + measures + constraints), group them under the one rule
name, and the compiler compiles each and **unions their violations**. A violation
in *either* case is a violation of the rule.
→ `case1.or(case2)`

This is the only form where a "rule" is genuinely **several drawings** rather than
one; everything else above is a single drawing.

---

## The subtle disambiguations (read these twice)

| Two pictures that look alike | Distinguish by | Means |
|---|---|---|
| straight measure vs wavy measure | the edges it spans | a real distance (a *check*) vs a topological relation |
| **touch** (coinciding edges) vs **overlap** (interiors cross) | the drawn relation | `interacting(Y)` (touch counts) vs `overlapping(Y)` ≡ `not_outside(Y)` (area overlap only) |
| **one flagged instance** vs flagged + a **not-flagged counter-example** | is there a carve-out? | a positive selection vs a **negation** (`not_inside`) — the dropped relation labels which |
| wavy `>= 0` vs no measure (both disjoint) | is there a measure? | `outside` selection (touch OK) vs `not_interacting` scope (strict) |
| a 2nd layer **covering** the measurement vs a **cutout off** it | where the measurement sits | combine (`.and`) vs remove (`.not`) |
| a 2nd layer off the measurement: **real edge** vs **all wavy** | the layer's edges | a device cut out (remove, `.not`) vs a domain marker (filter/scope) |

---

## Status / scope

This model is validated against the GF180MCU deck by `tools/validate_klayout.py`
(op-level round-trip: parse the deck rule → draw it via `as_drawn` → recompile →
confirm the op matches) and `tools/behavioral_validate.py` (run both ops in
KLayout, compare violations). The recipes above cover the declarative rule
families. **Not** geometry-by-example (separate, parameterized/universal forms):
off-grid, density, antenna; and a small tail of **procedural** rules (multi-stage
imperative layer algebra, e.g. via-3D enclosure) that are hand-coded algorithms
rather than a single drawn example.

_Keep this in sync as new drawing conventions are added._
