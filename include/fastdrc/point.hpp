#pragma once

#include <vector>

#include "fastdrc/types.hpp"

namespace fastdrc {

struct Edge;  // back-referenced by Point (the edges incident to it)

/// An interned point: a coordinate plus the edges incident to it.
///
/// Coord2D stays the lightweight value type used throughout the API; Point is
/// the shared interned node, so the incidence list doesn't burden every
/// coordinate passed by value.
struct Point {
  Coord2D xy;

  explicit Point(Coord2D p) : xy(p) {}

  /// Edges incident to this point. Non-owning back-references (the Pool owns
  /// the edges); marked mutable so the point's coordinate stays const while its
  /// edge list grows.
  void attach(const Edge* e) const { edges_.push_back(e); }
  [[nodiscard]] const std::vector<const Edge*>& edges() const { return edges_; }

 private:
  mutable std::vector<const Edge*> edges_;
};

}  // namespace fastdrc
