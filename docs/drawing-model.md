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
  check* (width / space / separation / enclosing). A measure that a constraint
  gives a number to (`E >= 0.18`) is a distance too, however its edges were drawn —
  a number cannot mean a relation.
- to a **wavy** edge → **not a distance at all**, but a **link**: a topological
  assertion about how two shapes relate. It picks an `inside` / `outside` /
  `interacting` / `overlapping` selection from the *relation you drew* (see
  *Restrict the subject…*): inside → `inside`, touch → `interacting`, overlap →
  `overlapping`, disjoint → `outside`. A link needs no bound. Drawn apart it means
  "they don't overlap, but the gap may be **zero** (may touch)" → an **`outside`**
  selection (KLayout `outside` keeps polygons that merely touch). A *drawn* touch
  (coinciding edges), by contrast, is `interacting`.

This is the single most important convention: **straight measure = distance, a link to
a wavy edge = relation.**

---

## Constraints: `match` vs `check`

- **`match`** — the **filter**: *which* geometry the rule is about: `A.layer == metal2`,
  `M0.layer == dualgate`, a qualifying edge length (and every link, which needs no
  constraint at all). A filter
  **selects what satisfies it**.
- **`check`** — the **check**: the *single* assertion that must hold on what was
  matched. A violation is a matched configuration where the `check` **fails**
  (`W >= 0.28` flags the narrower wires; `e.length == 0.22` flags every edge that is
  not 0.22 long).

A rule is therefore always *filters + one check*. The check is either a bound
(`W >= 0.28`, `A.area >= 0.2`, `e.length == 0.22`) or a **cross** — the check whose
bound is "never" (see *A configuration that must never exist*).

Rule of thumb: layer/marker/relation facts and "which ones" qualifiers are `match`;
the one thing being enforced is `check`.

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

(When the inner one only *crosses* the outer, see *Extension / overhang / end cap*.)

### Extension / overhang / end cap (one layer crossing another)
"Poly2 must extend 0.22 beyond COMP", "COMP must extend 0.24 beyond the gate",
"salicide block must extend beyond unsalicided poly": draw the two layers the way
they sit — a **bar** and a **band crossing it** — and **one measure** from the edge
of one (where it lies inside the other) to the edge of the other. Nothing else.

The compiler reads two things off that picture:

- **where the measure lies** — inside `C` but outside `X` means "how far `C` reaches
  past `X`";
- **what covers each measured edge** — any other layer drawn over the stretch.

| the measure runs | means | compiler derives |
|---|---|---|
| from the band's edge (inside the bar) to the bar's end | the bar reaches past the band | `bar.enclosing(band, v)` |
| from the bar's edge (inside the band) to the band's end | the band reaches past the bar (an end cap) | `band.enclosing(bar, v)` |
| from one layer's edge to the other's, **inside both** | how deep they overlap | `a.overlap(b, v)` |

→ `comp.enclosing(sab, 0.22)` — "comp must extend at least 0.22 µm beyond sab".

**Butted regions.** For "N+ part of a diffusion butted to its P+ part must be at
least 0.22 long", draw **one** `comp`, `pplus` over one part, `nplus` butted against
it over the rest, and measure from the butting edge to the end of the `comp`. The
`nplus` covers the measured stretch but does not reach the `pplus` part, so it is
not combined into the diffusion — it says *where* the extension is looked at:
→ `comp.enclosing(pplus, 0.22).overlapping(nplus)` — "comp must extend at least
0.22 µm beyond pplus — only where the geometry is overlapping nplus".

A context **linked** to either shape still filters that shape
(`sab.outside(otp_mk).enclosing(poly2, 0.22)`).

### Min area
One closed rect with **all edges wavy** — it stands for "any polygon"; only the
total area is constrained, no edge is measured. `A.area >= 0.2025`.
→ `comp.with_area(nil, 0.2025)`

### Min void area (a hole / ring)
Draw the shape as a **donut** — an outer outline with an inner **hole loop**
(`hole_loops`) cut out — and measure *the void*, not the ring: `A.holes.area >= 0.35`.
`A.holes` is a genuine sub-feature of the drawn shape (like an edge's length), so the
compiler recovers "the layer's enclosed voids" from the drawn hole and the threshold
from the constraint. A min-void-area rule flags pinholes/slivers in a fill.
→ `nplus.holes.with_area(nil, 0.35)`
(The same drawn void is what lets `X.inside(G.holes)` express "X surrounded by ring G".)

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

**The part of a shape that sticks out of a layer (straddling).** When the layer you
cut out is *also* one the shape has a relation to — `nat` drawn overlapping
`dualgate`, with the `dualgate` part cut out of it — the relation is read on the
shape **as drawn, before the cut** (what is left after removing `dualgate` can no
longer overlap it). So the picture means "for a `nat` that overlaps `dualgate`, the
part left outside it":
→ `nat.overlapping(dualgate).not(dualgate)` — with a cross: "nat that overlaps
dualgate must not extend beyond it".

**Several layers at once (a layer set).** When the same shape stands for *any of*
several layers, give it a set: `X.layer in {mvsd, otp_mk, ymtp_mk}`. One cutout with
a set removes each member (`.not(mvsd).not(otp_mk).not(ymtp_mk)`); anywhere else the
set is the union of its layers (`.not_interacting(dualgate.or(v5_xtor))`).

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
Draw a **context shape** of that other layer and a **link** from the subject to it:
a measure drawn to a wavy edge. A link is not a distance, so it needs **no
constraint** (an old-style `G >= 0` on it is accepted and adds nothing). The *relation* you draw is what the compiler reads — it derives the
selection op:

| what you draw (subject vs context) | compiler derives |
|---|---|
| subject **inside** the context | `X.inside(Y)` |
| **disjoint** (drawn apart; the gap may be zero) | `X.outside(Y)` |
| **touching** (coinciding edges — one shape's edge *is* the other's) | `X.interacting(Y)` |
| **overlapping** (interiors cross) | `X.overlapping(Y)` ≡ `X.not_outside(Y)` |

The **exact relation you draw is the signal** — `touch` and `overlap` are different
pictures with different meanings. A *touch* (coinciding edges) counts as
`interacting`; a genuine *area overlap* is `overlapping` (equivalently
`not_outside` — "not wholly outside" = has overlap). To get `outside` you draw the
shapes **apart** (disjoint); a linked "apart" still allows the gap to reach zero. This matches the scope table below exactly (touch→`interacting`,
overlap→`overlapping`) — selection and scope read relations the same way.

The **link is the signal** that this is a *selection*: drawing one to a layer makes it a *selection*
(`X.inside(Y)`, keeping whole polygons). The *same* containing layer drawn with **no
measure** — just covering — would instead be a *combine* (`X.and(Y)`, the
overlap-of-two-layers recipe above). They're behaviourally close when the subject
is inside `Y`, but the link is what tells them apart. Apply the link to
**each** subject shape (both rects of a space rule), and you can chain several
(`X.inside(A).inside(B)`). The separation **argument** can be selected the same
way: a link from `B` to its context → `X.separation(Y.outside(Z))`.

### A selected marker (the context has its own selection)
The context shape itself can be a *selected* thing —
`via1.interacting(metal2.interacting(fusetop))` = "a via1 touching a **metal2 that
itself touches fusetop**". You draw it by giving the **context its own context**:
draw the marker `metal2` in its relation to the subject (a link), then draw
`fusetop` in its relation to the *marker* with **another link from the marker
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

> **Unlinked vs linked** is the deliberate distinction between a **scope marker**
> (it restricts where *violations* are reported: `not_interacting` drops touching)
> and a **selection** (it restricts which *shapes* are checked: `outside` keeps
> touching). The link is the signal, and the editor shows each shape's role.

### Acute angle
"No acute angles" is a rule about **corners**: two **straight** edges meeting at a
sharp corner. Draw such a corner and write the check on it — what must hold:
`a1.corner >= 45` (`<edge>.corner` is the angle **between** the two edges meeting
where that edge starts: a right angle is 90, an acute corner less). The corners that
fail it are flagged.
→ `comp.with_angle(0 .. 45)`

(A forbidden *range* that has no single assertion — "no corner between 30° and 60°" —
is written as a filter: match `a1.corner >= 30 and a1.corner <= 60`. The older
spelling on an edge's direction, `a2.angle > 0 and a2.angle < 45`, still compiles to
the same op.)

### Corner
A shape with a corner (two adjacent straight edges) and a constraint on its angle.
As everywhere, the kind decides: the check `a1.corner != 90` ("no right-angle
corners here") flags the 90° corners; the check `a1.corner == 90` flags every corner
that is *not* 90°; as a filter (match), `a1.corner == 90` selects the 90° ones.
→ `poly2.corners(90).sized(0.1).or(poly2.corners(-90).sized(0.1))`  (both convex
and concave 90° corners).

`corners()` returns the corners as zero-area **points**, so the `.sized(0.1)`
inflates each into a small 0.1 µm marker box so it can be output (the corner
analogue of `.extended` for edges). That `0.1` is **not drawn** — it's a fixed
backend constant (a marker-box size, matching the deck); it doesn't change *which*
corners are flagged, only that they're outputtable.

### Edge length
A shape with a **straight** edge; constrain its length. The constraint's **kind**
decides the reading, exactly as for every other constraint:

As a **`check`** (what every edge must satisfy — the edges that *fail* are flagged):

| check | flags | compiles to |
|---|---|---|
| `e.length == 0.22` | edges that are not 0.22 | `layer.edges.without_length(0.22)` |
| `e.length != 0.22` | edges that are exactly 0.22 | `layer.edges.with_length(0.22)` |
| `e.length >= 0.34` | the shorter edges | `layer.edges.with_length(nil, 0.34)` |
| `e.length <= 0.5` | the longer edges | `layer.edges.with_length(0.5, nil)` |
| `e.length >= 0.1 and e.length <= 0.5` | edges outside the range | `layer.edges.without_length(0.1, 0.5)` |

As a **`match`** (a filter — the edges that *satisfy* it are selected):

| match | selects | compiles to |
|---|---|---|
| `e.length <= 0.34` | edges up to 0.34 | `layer.edges.with_length(nil, 0.34)` |
| `e.length == 0.22` | edges of exactly 0.22 | `layer.edges.with_length(0.22)` |
| `e.length >= 0.1 and e.length <= 0.5` | edges in the range | `layer.edges.with_length(0.1, 0.5)` |

A length filter usually qualifies another rule (an end-of-line, below). A rule that
has only filters and no check flags what the filters select.

### End-of-line (a wire end)
A min-width wire is *two straight sides + wavy ends*. An **end-of-line** rule is that
same wire with **one end made real**: draw the **cap** (the short end edge) **straight**
while the *other* end stays **wavy** ("the wire continues"), keep the two sides straight,
and add two edge-length **filters** (`match`) — `cap.length <= Weol` (the EOL width) and
`side.length >= Slen` (the min run length): they say *which* wire ends the rule is
about. Put the rule measure on the cap (e.g. an inner contact it must enclose) — its
bound (`R >= 0.06`) is the one **check**. No new primitive, no to-scale drawing: the cap-opposite-
a-wavy-end geometry *is* the end-of-line, and the compiler emits KLayout's idiom:
→ `cond = metal1.drc(width <= Weol).with_length(Slen, nil, both)`
→ `eol = metal1.edges.with_length(nil, Weol).interacting(cond.first_edges).interacting(cond.second_edges).not(cond.first_edges).not(cond.second_edges)`
→ `eol.enclosing(contact.edges, 0.06)`

### Halo (a "within X" margin on a shape)
A shape carries an optional **halo** — a signed margin (default 0) its region is
**grown** (halo > 0) or **shrunk** (halo < 0) by, the formal form of a DRM figure's
dashed offset line ("within X of"). Draw it as a **dashed/dotted offset outline**
(outside the shape for +, inside for −). A `rounded` tick selects rounded vs square
corners on the grown region (reserved — not yet rendered to KLayout). The compiler
appends it to that shape's derived region:
→ `nwell.outside(dnwell).sized(0.429.um)` (a nwell-outside-dnwell region grown 0.429)

### Min/max size (contact/via) — edges as the output
A contact/via must be *exactly* the right size, so any edge of the **wrong length**
is a violation. Draw the layer with a straight edge and the check `e.length == 0.22`
(what must hold); the edges that fail it are flagged, rendered as thin polygons via
`.extended` so they're outputtable:
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

### Maximum bounds and narrow-region selectors (`.drc`)
A bound on a distance reads by its **kind**, like every constraint.

As a **`check`** (what must hold — where it fails is flagged):

| check | flags | compiles to |
|---|---|---|
| `W >= 0.28` | the narrower | `metal1.width(0.28)` |
| `W <= 5` | the wider (a *maximum* width) | `metal1.drc(width > 5)` |
| `W >= 0.28 and W <= 5` | both | the two checks above |

As a **`match`** (a filter — it *selects* the region that satisfies it; a rule with
only filters flags what they select):

| match | compiles to |
|---|---|
| `W <= 0.34` | `metal1.drc(width <= 0.34)` |
| `S <= 0.3` (same layer) | `metal1.drc(space <= 0.3)` |
| `S <= 0.2` (diff layers) | `metal1.drc(separation(comp) <= 0.2)` |
| `E <= V` (inner inside outer) | `via.drc(enclosed(metal) <= V)` |
| `0.04 <= E < 0.06` | `via1.drc(0.04 <= enclosed(metal1) < 0.06)` |
| `W >= 2` | `metal1.drc(width >= 2)` |

(A "greater than" predicate is KLayout's: it returns the edges that have no partner
within the distance.)

### A configuration that must never exist (the cross)
Most rules check a *quantity* ("this distance is too small"). The other half of DRC
forbids a *configuration outright* ("these two must never overlap"). You draw the
forbidden picture and put a **cross** on it. The cross **is the rule's check**: a
measure whose bound is **"never"** (not `>= V`), so it needs no constraint of its own.

**A cross on a relation** — drawn between an edge of the subject `A` and an edge of
another shape `B` — forbids *that relation*. The compiler reads the drawn relation
exactly like a selection (`rel_to_sel`); the shapes selected are the violation:

| what you draw (A vs B) | how you mark it | the violation | reads as |
|---|---|---|---|
| **disjoint** (a gap) | cross **in the gap** between the two edges | `A.outside(B)` | A must **overlap** B |
| **touch** (edges coincide) | cross drawn **over the shared edge** (an X on it) | `A.interacting(B)` | A must **not touch or overlap** B |
| **overlap** (interiors cross) | cross over the overlap | `A.overlapping(B)` | A must **not overlap** B (touching OK) |
| **inside** | cross, A drawn inside B | `A.inside(B)` | A must **not be inside** B |

Every *other* relation of the subject stays an ordinary filter (a plain link), so a
rule reads straight off the picture: **NW.6** "an Nwell resistor must stay out of
DNWELL" is an `nwell` drawn inside `res_mk` (a link — *which* nwell) touching a
`dnwell` with the **cross on that contact** (the check):
→ `nwell.inside(res_mk).interacting(dnwell)` — "nwell that lies inside res_mk must not
touch or overlap dnwell".

**A cross on the subject alone** — across its own two parallel edges, like a width
whose bound is "never" — forbids the subject's *region* itself: the layer as combined
by its covers and cutouts, in whatever relations are drawn as filters. Use it when
the forbidden thing is an overlap *region* rather than a relation: "salicide block
over a contact" is `sab` drawn under a covering `contact`, crossed.
→ `sab.and(contact)`

The flagged region *is* the output (no `width`/`space` op, no threshold).

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

The cases need not be the same *kind*: **SB.4** is "salicide block too close to a
contact **OR** overlapping one" — a *separation* case unioned with a *presence*
(overlap) case. Each variant is recognized independently, so the union is only as
complete as its parts (an unrecognized operand keeps the whole rule deferred —
never partially emitted).

This is the only form where a "rule" is genuinely **several drawings** rather than
one; everything else above is a single drawing.

---

## The way design manuals draw rules — and say them

The conventions above were checked against how foundry design manuals draw and word
their rules (the open GF180MCU and SKY130 manuals, among others). The figures share
one visual language, and the model follows it:

| in a manual's figure | here |
|---|---|
| real layers drawn as they sit: a bar, a band across it, an implant over part of it | the same shapes; relations are read off the drawing |
| a named derived region in the legend (NCOMP, tap, gate, poly resistor) | a **legend**: `"layers": {"pcomp": "comp and pplus"}`, then draw `pcomp` |
| a dimension arrow between two edges | a measure with a `check` bound |
| an arrow labelled with an inequality that is a *condition* ("< 0.34 wide") | a measure / length with a `match` bound |
| a big **X** over a shape or a configuration | a **cross**: on the shape, or on the relation that is forbidden |
| a tick next to an arrangement that is fine | an **allowed example** (the shape's mark) |
| break lines: "the shape continues" | wavy edges |
| ONE figure carrying many rules | a **sheet**: one scene, a list of rules (below) |

And the phrases manuals use map onto drawings like this:

| a manual says | you draw | compiles to |
|---|---|---|
| *A width* / *A space* (notch included) | one shape / two shapes, an arrow | `A.width(v)` / `A.space(v)` |
| *... with run length > x* | the same, plus the filter `S.run > x` | `A.space(v, projection, projecting > x)` |
| *space to a line wider than w* | a width arrow on that line with the filter `Ww > w`, the spacing arrow with its check | `A.separation(A.sized(-w/2).sized(w/2), v)` |
| *P space to Q, square check* / *projection check* | the measure's metric | `P.separation(Q, v, square)` |
| *(P space to Q) checked over R* | R drawn over the measured stretch | `P.and(R).separation(Q.and(R), v)` |
| *J overlap past K* / *J extension beyond K* | J and K crossing, an arrow from K's edge to J's end | `J.enclosing(K, v)` |
| *L overlap of M* | the arrow inside both | `L.overlap(M, v)` |
| *G within H by x* (enclosure) | G inside H, an arrow across the margin | `H.enclosing(G, v)` |
| *G within H by x* / *H overlap of G*, straddling prohibited | the same arrow, set to **within** | `H.enclosing(G, v).or(G.overlapping(H).not_inside(H))` |
| *J must be within K* (every J) | the same, plus the check `J in K` | `... ` and `J.not_inside(K)` |
| *... with coinciding permitted* | the bound `E >= v or E == 0` | zero-width violations dropped |
| *C prohibited over D* / *must not touch* / *must not be inside* | the two shapes in that relation, crossed | `C.overlapping(D)` / `.interacting` / `.inside` |
| *C must not straddle D* (inside is fine) | C drawn across D's edge, crossed as "never straddle" | `C.overlapping(D).not_inside(D)` |
| *K must be on A or B* | a shape whose layer is the set `{A, B}`, K inside it, the check `K in X` | `K.not_inside(A.or(B))` |
| *A maximum width* / *maximum within* | the check `W <= v` | `A.drc(width > v)` |
| *gate length*: poly edges over diffusion | poly crossing diffusion, an edge-to-edge width | `poly.edges.and((poly & diff).edges).width(v)` |
| *minimum area* / *enclosed area* | `A.area >= v` / `A.holes.area >= v` | `A.with_area(nil, v)` / `A.holes.with_area(nil, v)` |

A layer **set** distributes the way the phrase does: "width of diff or tap" is the
width of *each* (`diff.width(v)` or `tap.width(v)`), "spacing of diff or tap" is
between *any* of them (`diff.or(tap).space(v)`). A zone "within x of" a layer is a
legend entry: `"near_nwell": "nwell + 0.43"` (grown; `-` shrinks).

Not drawn by example (yet): density, summed areas, counts ("must contain exactly n",
contact arrays), shape-class filters ("square contacts only"), and a required
neighbour within a maximum distance.

### Sheets — one figure, many rules

A manual's figure draws a situation once and hangs many rules on it. A **sheet** is
that figure: `shapes` + `measures` + the layer facts, and `rules` — each a name with
its constraint (or its cross) on the scene:

```json
{"name": "SAB", "shapes": [...], "measures": [...], "constraints": [...],
 "rules": [{"name": "SB.6", "desc": "...", "constraints": [{"kind": "check", "expr": "E6 >= 0.22"}]},
           {"name": "SB.8", "desc": "...", "measures": ["X8"]}]}
```

Each rule reads only its part of the scene:

- the shapes it measures;
- anything drawn **over the spot the arrow sits on** — but not a *background*, a
  shape that holds the whole measured shape (the diffusion under a contact, a well):
  moving the arrow can say nothing about such a layer, so it takes part only when the
  rule names it (`"with": ["COMP"]`);
- for a crossed-out shape, everything it is drawn on;
- the contexts those shapes are linked to.

The rest of the figure belongs to other rules. `"with"` / `"without"` / `"shapes"`
on a rule override this; `"cross"` / `"allowed"` put the marks on shapes for that
rule only. Two entries may share a name — a rule and its crossed-out example.
`fastdrc rules sheet.json` compiles a sheet to one deck.

**Where the arrow is put matters**, as in a manual. A measure is read *at its spot*:
by default the middle of the stretch where its two edges face each other, or where
you put it — `"at": c`, the coordinate along the edges. The same two poly edges give
"poly spacing" with the arrow on field and "gate spacing" with the arrow over the
diffusion, because the diffusion is then drawn over the measured spot.

**A table of values.** Manuals give most rules as a table: one arrow, a value per
column (3.3 V, 5 V …). Name each column once by where it applies, and give the rule
its values with `?` in the constraint:

```json
"domains": {"3.3V": [["not_interacting", "v5_xtor"], ["not_interacting", "dualgate"]],
            "5V":   [["overlapping", "dualgate"]]},
"rules": [{"name": "PL.4", "constraints": [{"kind": "check", "expr": "E4 >= ?"}],
           "values": {"3.3V": 0.22, "5V": 0.22}}]
```

→ the rules `PL.4_3.3V` and `PL.4_5V`, each scoped to its column. A rule with no
number that still holds per column lists them: `"columns": ["3.3V", "5V"]`.

**A section note** — "these rules do not apply inside OTP_MK" — is `"except":
["otp_mk"]` on the sheet (every rule) or on one rule, instead of a cutout drawn into
every shape.

---

## From drawings to a deck

```
fastdrc rules poly.sheet.json contact.sheet.json --layermap layers.json --standalone -o process.drc
klayout -b -r process.drc -rd input=chip.gds -rd report=chip.lyrdb
fastdrc rules poly.sheet.json --to docs          # the same rules as plain sentences
```

`layers.json` maps the layer names used in the drawings to GDS numbers:
`{"poly": [66, 20], "diff": [65, 20]}`. Nothing in the compiler knows any process:
layer names, values, columns and exemptions all come from the drawings. A rule that
ends up checking nothing is reported as a warning rather than silently dropped.

---

## In the editor (`fastdrc gui`)

- **Drag** on empty canvas: a new shape (all edges wavy). **Double-click** an edge:
  straight ↔ wavy. **Right-click** an edge: split it.
- **Shift-click two edges**: a measure. Two straight edges give a distance (bound it
  with a constraint); a wavy end gives a **link** (a relation, no constraint). Select
  the measure to set a **cross**, the metric, or edge-to-edge.
- **`h`** on a shape: a hole. Lying inside a shape it is that shape's **void**
  (`A.holes`) and moves with it.
- Each shape shows how it **reads** — subject, operand, combined, cut out, filter,
  forbidden, scope — in the tree and in its properties. This is derived from the
  drawing; if it is not what you meant, the drawing says something else.
- **Alternative cases**: "+ alternative case" turns the rule into several drawings,
  any of which is a violation; switch between them with the case buttons.
- With nothing selected the panel edits the rule's **name and description**. The
  pane at the bottom shows the rule as text (or as the KLayout deck), live.

---

## The subtle disambiguations (read these twice)

| Two pictures that look alike | Distinguish by | Means |
|---|---|---|
| a measure between straight edges vs a link to a wavy edge | the edges it spans | a real distance (bounded by a constraint) vs a topological relation (no constraint) |
| a plain link vs the same link **crossed** | the cross | a filter (*which* shapes) vs the check (this relation is forbidden) |
| a length / corner constraint as `match` vs as `check` | the constraint's kind | selects what satisfies it vs flags what fails it |
| a context **linked** to a shape vs an **unlinked** all-wavy one | is there a link (a measure to a wavy edge)? | filters that *shape* by the drawn relation vs scopes the *violations* to a region |
| **touch** (coinciding edges) vs **overlap** (interiors cross) | the drawn relation | `interacting(Y)` (touch counts) vs `overlapping(Y)` ≡ `not_outside(Y)` (area overlap only) |
| **one flagged instance** vs flagged + a **not-flagged counter-example** | is there a carve-out? | a positive selection vs a **negation** (`not_inside`) — the dropped relation labels which |
| linked vs unlinked (both disjoint) | is there a link? | `outside` selection (touch OK) vs `not_interacting` scope (strict) |
| a 2nd layer **covering** the measurement vs a **cutout off** it | where the measurement sits | combine (`.and`) vs remove (`.not`) |
| a 2nd layer off the measurement: **real edge** vs **all wavy** | the layer's edges | a device cut out (remove, `.not`) vs a domain marker (filter/scope) |

---

## Status / scope

This model is validated against the GF180MCU deck by `tools/validate_klayout.py`
(op-level round-trip: parse the deck rule → draw it via `as_drawn` → recompile →
confirm the op matches) and `tools/differential_validate.py` (run our complete
generated deck and the official statement in KLayout on many layouts per rule and
compare the violation markers by XOR; `tools/behavioral_validate.py` is the older,
weaker count-on-one-layout check). The recipes above cover the declarative rule
families. **Not** geometry-by-example (separate, parameterized/universal forms):
off-grid, density, antenna; and a small tail of **procedural** rules (multi-stage
imperative layer algebra, e.g. via-3D enclosure) that are hand-coded algorithms
rather than a single drawn example.

_Keep this in sync as new drawing conventions are added._
