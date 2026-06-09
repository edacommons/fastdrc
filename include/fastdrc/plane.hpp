#pragma once

#include <cstddef>
#include <list>
#include <map>
#include <vector>

#include "fastdrc/types.hpp"

namespace fastdrc {

// A corner-stitched tile plane (Ousterhout). The whole universe is tiled by
// solid and space tiles (no gaps, no overlaps); each tile carries 4 corner
// stitches to its neighbors, so point-location and neighbor queries are pointer
// walks. This first version keeps things correct rather than optimal: the
// stitches are recomputed globally after each change, and space tiles are not
// merged (valid tiling, just not canonical). Local updates + the line-index
// start hint come next.

/// A tile in the plane: an axis-aligned rectangle, solid or space, with 4
/// corner stitches to neighboring tiles (null at the universe boundary).
struct CTile {
  Coord1D x0{}, y0{}, x1{}, y1{};
  bool solid{};
  CTile* tr{};  // right neighbor at the top-right corner
  CTile* rt{};  // top neighbor at the top-right corner
  CTile* bl{};  // left neighbor at the bottom-left corner
  CTile* lb{};  // bottom neighbor at the bottom-left corner

  [[nodiscard]] Area area() const {
    return (static_cast<Area>(x1) - x0) * (static_cast<Area>(y1) - y0);
  }
};

class Plane {
 public:
  Plane();  // one space tile covering the universe

  /// Paint a solid rectangle whose area is currently all space. Precondition:
  /// it overlaps no existing solid tile. Returns the new solid tile.
  CTile* insert(Coord1D x0, Coord1D y0, Coord1D x1, Coord1D y1);

  /// The tile containing (x, y) — a pointer walk from the last-accessed tile.
  CTile* find(Coord1D x, Coord1D y);

  [[nodiscard]] std::size_t tile_count() const { return tiles_.size(); }
  [[nodiscard]] std::size_t solid_count() const;
  /// Total area of all tiles (== the universe area for a valid tiling).
  [[nodiscard]] Area total_area() const;

 private:
  void restitch();  // recompute all corner stitches (global, O(n^2))
  void reindex();   // rebuild the by-left-edge start-hint index

  // A tile near (x, y) to seed the point-location walk, found via the index.
  CTile* start_hint(Coord1D x, Coord1D y);

  std::list<CTile> tiles_;  // stable addresses for the stitch pointers
  CTile* hint_;             // last-accessed tile (kept for locality)

  // Start-hint index: left-edge x -> tiles starting there, sorted by y0.
  std::map<Coord1D, std::vector<CTile*>> by_left_;
};

}  // namespace fastdrc
