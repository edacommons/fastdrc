#pragma once

#include "fastdrc/handles.hpp"

namespace fastdrc {

/// A directed, bounded segment between two interned endpoints, lying on a
/// canonical (undirected) Line. Created and interned by the Pool.
///
/// The direction p0 -> p1 is meaningful (e.g. a polygon edge's winding); the
/// `line` it references is undirected, so opposite-direction edges share one
/// Line. `line` holds the supporting line; the Line holds a back-reference to
/// every Edge on it.
struct Edge {
  PointPtr p0;
  PointPtr p1;
  LinePtr line;
};

}  // namespace fastdrc
