#pragma once

#include <cstddef>
#include <vector>

#include "fastdrc/pool.hpp"
#include "fastdrc/types.hpp"

namespace fastdrc {

/// One layout layer (a.k.a. plane): holds the geometry on it, interned through
/// its own Pool. Geometry is added without a layer argument — the Layer is the
/// layer. A Layout owns several of these.
class Layer {
 public:
  /// Add an axis-aligned rectangle from four coordinates (corners normalized).
  void add_rect(Coord1D x0, Coord1D y0, Coord1D x1, Coord1D y1);

  /// Add an axis-aligned rectangle from two opposite corners.
  void add_rect(Coord2D p0, Coord2D p1);

  /// Add a polygon. Vertices and edges are interned through this layer's pool.
  void add_polygon(const std::vector<Coord2D>& points);

  [[nodiscard]] std::size_t point_count() const { return pool_.point_count(); }
  [[nodiscard]] std::size_t line_count() const { return pool_.line_count(); }
  [[nodiscard]] std::size_t edge_count() const { return pool_.edge_count(); }

  [[nodiscard]] const Pool& pool() const { return pool_; }

 private:
  Pool pool_;
};

}  // namespace fastdrc
