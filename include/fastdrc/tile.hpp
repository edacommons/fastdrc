#pragma once

#include "fastdrc/types.hpp"

namespace fastdrc {

/// A composed primitive: an axis-aligned bbox with up to one straight cut per
/// side, taking off a corner triangle. Each cut is described by its **signed
/// horizontal extent** over the full tile height H = y1 - y0:
///
///   cut_left  == 0 : vertical left edge at x0.
///   cut_left  >  0 : top-left cut;     left edge (x0, y0) -> (x0+cut_left, y1).
///   cut_left  <  0 : bottom-left cut;  left edge (x0-cut_left, y0) -> (x0, y1).
///   cut_right == 0 : vertical right edge at x1.
///   cut_right >  0 : bottom-right cut; right edge (x1-cut_right, y0) -> (x1, y1).
///   cut_right <  0 : top-right cut;    right edge (x1, y0) -> (x1+cut_right, y1).
///
/// (The cut is the signed delta x_top - x_bottom of that side's edge: a positive
/// left cut leans the top inward; a positive right cut leans the bottom inward.)
///
/// The cut's slope is H / extent, so ANY straight angle is representable — 45 deg
/// is just |extent| == H, nothing special. This one primitive is rect (no cuts),
/// half-rect/triangle, trapezoid, and parallelogram, at any angle.
struct Tile {
  Coord1D x0{};
  Coord1D y0{};
  Coord1D x1{};
  Coord1D y1{};
  Coord1D cut_left = 0;   // signed horizontal extent of the left-side cut
  Coord1D cut_right = 0;  // signed horizontal extent of the right-side cut

  /// Twice the area: bbox minus the two corner triangles (legs |cut| and H).
  [[nodiscard]] Area area2() const {
    const Area w = x1 - x0;
    const Area h = y1 - y0;
    const Area cl = cut_left < 0 ? -cut_left : cut_left;
    const Area cr = cut_right < 0 ? -cut_right : cut_right;
    return 2 * w * h - (cl + cr) * h;
  }
};

}  // namespace fastdrc
