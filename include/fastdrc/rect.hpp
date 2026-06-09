#pragma once

#include "fastdrc/types.hpp"

namespace fastdrc {

/// Axis-aligned rectangle. Sweep outputs are normalized (x0 < x1, y0 < y1).
struct Rect {
  Coord1D x0{};
  Coord1D y0{};
  Coord1D x1{};
  Coord1D y1{};

  /// Twice the area (exact integer; valid for normalized rects).
  [[nodiscard]] Area area2() const {
    return static_cast<Area>(x1 - x0) * (y1 - y0) * 2;
  }
};

}  // namespace fastdrc
