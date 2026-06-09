#pragma once

#include <cstddef>
#include <vector>

#include "fastdrc/rect.hpp"
#include "fastdrc/sweep.hpp"  // TaggedRing, Tile, sweep_octilinear
#include "fastdrc/tile.hpp"
#include "fastdrc/types.hpp"

namespace fastdrc {

// Engine 1 — the compose accumulator. Collects tagged rings (rectangles and
// polygons, any straight angle), then computes the merged, disjoint tiling on
// demand via sweep_polygons, emitting Tiles (rect / half-rect / trapezoid /
// parallelogram) whose corner cuts carry a signed extent. add = union, cut =
// subtract.
//
// The merge is BATCH (compose once): add/cut just append a ring; the sweep runs
// lazily when the tiling is read.
class Region {
 public:
  /// Union a rectangle.
  void add(const Rect& r) { push(r, Tag::Add); }

  /// Subtract a rectangle (cut a hole).
  void cut(const Rect& r) { push(r, Tag::Cut); }

  /// Union an octilinear polygon (closed vertex ring).
  void add_polygon(const std::vector<Coord2D>& vertices) {
    rings_.push_back({vertices, Tag::Add});
    dirty_ = true;
  }

  /// Cut an octilinear polygon (a hole).
  void cut_polygon(const std::vector<Coord2D>& vertices) {
    rings_.push_back({vertices, Tag::Cut});
    dirty_ = true;
  }

  /// The merged disjoint tiling (computed and cached lazily).
  [[nodiscard]] const std::vector<Tile>& tiles() const;

  /// Twice the total area of the merged tiling.
  [[nodiscard]] Area area2() const;

  /// Number of primitives in the merged tiling.
  [[nodiscard]] std::size_t rect_count() const { return tiles().size(); }

 private:
  void push(const Rect& r, Tag tag) {
    rings_.push_back({{{r.x0, r.y0}, {r.x1, r.y0}, {r.x1, r.y1}, {r.x0, r.y1}},
                      tag});
    dirty_ = true;
  }

  std::vector<TaggedRing> rings_;
  mutable std::vector<Tile> tiles_;
  mutable bool dirty_ = true;
};

}  // namespace fastdrc
