#pragma once

#include <vector>

#include "fastdrc/rect.hpp"
#include "fastdrc/tile.hpp"
#include "fastdrc/types.hpp"

namespace fastdrc {

/// Whether an input shape contributes to the union (Add) or is subtracted as a
/// hole (Cut).
enum class Tag { Add, Cut };

struct TaggedRect {
  Rect rect;
  Tag tag;
};

/// A closed polygon ring (straight edges, any angle), tagged Add or Cut. Input
/// to the polygon sweep.
struct TaggedRing {
  std::vector<Coord2D> verts;
  Tag tag;
};

/// Phase A — the Manhattan trapezoidal sweep. Computes the merged, disjoint
/// rectangle tiling of `union(Add) \ union(Cut)` from a bag of axis-aligned
/// input rectangles.
///
/// Two anti-fragmentation properties (see docs/trapezoid-sweep-v0.md):
///   * per-axis selection — the sweep extends primitives along the layer's
///     dominant edge direction (total vertical vs horizontal edge length), so a
///     tall layer is not sliced horizontally (and vice versa);
///   * extend-while-unchanged — a primitive stays open and grows while its span
///     is unchanged, closing only when an edge actually starts/ends. Output
///     scales with geometric feature count, not with the number of sweep events
///     (N abutting collinear rectangles merge to 1, not N).
///
/// Output rectangles are normalized and disjoint, maximal along the chosen
/// extend axis.
std::vector<Rect> sweep_manhattan(const std::vector<TaggedRect>& input);

/// The trapezoidal polygon sweep. Computes the merged, disjoint tiling of
/// `union(Add) \ union(Cut)` from polygon rings of ANY straight angle, emitting
/// `Tile`s (rect / half-rect / trapezoid / parallelogram) whose corner cuts
/// carry a signed extent, so the angle is free (45 deg is not special). A
/// horizontal sweep slabs at vertex Ys; within a slab each active edge is a
/// straight segment, nonzero winding gives the solid spans, and
/// extend-while-unchanged grows each tile in y so an angled wire is ONE tile.
/// Off-grid edge positions (from non-45 slopes) are snapped to the DBU grid.
///
/// Edges that CROSS inside a slab split it at the crossing (snapped to the DBU
/// grid; a crossing that falls between grid lines is exact to within one unit).
/// Collinear edges count as one boundary, so abutting shapes merge into one tile.
std::vector<Tile> sweep_polygons(const std::vector<TaggedRing>& rings);

/// The polygon ring (CCW, 4 vertices) of a tile, reconstructed from its bbox +
/// signed cut extents (the inverse of the sweep's emit).
std::vector<Coord2D> tile_ring(const Tile& t);

/// Layer-algebra boolean between two sets of tiles. `A op B`:
///   Or  = A ∪ B (union)    And = A ∩ B (intersection)
///   Sub = A − B            (for B − A, just swap the operands)
///   Xor = (A − B) ∪ (B − A) (symmetric difference)
/// One sweep, parameterized by a per-operand winding predicate (same sweep, and
/// the same crossing handling, as sweep_polygons).
enum class BoolOp { Or, And, Sub, Xor };
std::vector<Tile> boolean(const std::vector<Tile>& a, const std::vector<Tile>& b,
                          BoolOp op);

/// Grow (d > 0) or shrink (d < 0) a region by `d` on all sides, with square
/// corners: the dilation / erosion by the square [-|d|, |d|]^2. Angled sides
/// move out (or in) keeping their slope. Use d > 0 to add a halo around a
/// scope/changed region; d < 0 is a true erosion of the merged region, not a
/// per-tile shrink.
std::vector<Tile> inflate(const std::vector<Tile>& tiles, Coord1D d);

}  // namespace fastdrc
