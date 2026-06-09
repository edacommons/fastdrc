#pragma once

#include <vector>

#include "fastdrc/types.hpp"

namespace fastdrc {

struct Edge;  // back-referenced by Line (the edges lying on it)

/// A canonical, undirected octilinear line.
///
/// Coordinates are y-up (GDS convention). The line is stored by its direction
/// reduced to lowest terms, plus an offset:
///
///     dx  = (x1 - x0) / g
///     dy  = (y0 - y1) / g          // y-up sign: the normal is (dy, dx)
///     ofs = -(dy*x0 + dx*y0)
///
/// with g = gcd(|x1-x0|, |y0-y1|). The implicit value is
///
///     f(P) = dx*Py + dy*Px + ofs
///
/// whose sign gives the side and which is 0 exactly on the line.
///
/// Uniqueness: gcd reduction removes the magnitude freedom, and canonicalize()
/// fixes the overall sign (first non-zero of (dx, dy) positive). So every pair
/// of collinear points — either direction — yields identical (dx, dy, ofs),
/// and the Pool returns the same Line object.
///
/// For octilinear geometry dx, dy ∈ {-1, 0, +1}, so f never forms a
/// coordinate*coordinate product and stays exact in LineValue (int64).
///
/// Undirected: f's sign is relative to the canonical orientation, not a p0->p1
/// travel direction. Per-edge "inside" is tracked by the Edge.
struct Line {
  Coord1D   dx{};   ///< (x1 - x0) / g
  Coord1D   dy{};   ///< (y0 - y1) / g   (y-up convention; normal is (dy, dx))
  LineValue ofs{};  ///< -(dy*x0 + dx*y0)

  Line() = default;

  /// Construct from coefficients (gcd-reduced and sign-canonicalized).
  Line(Coord1D dx, Coord1D dy, LineValue ofs);

  /// The line through p0 and p1 (canonicalized; direction is discarded).
  /// Precondition: the segment is octilinear (0/45/90 degrees) and p0 != p1.
  Line(Coord2D p0, Coord2D p1);

  /// Evaluate f(p) = dx*Py + dy*Px + ofs. Sign gives the side; 0 = on the line.
  [[nodiscard]] LineValue eval(Coord2D p) const;

  /// Sign of eval(p): +1, 0, or -1 (relative to the canonical normal).
  [[nodiscard]] int side(Coord2D p) const;

  /// True iff p lies exactly on the line.
  [[nodiscard]] bool contains(Coord2D p) const { return eval(p) == 0; }

  /// The edges that lie on this line. Non-owning back-references: the Pool
  /// owns the edges, and a Line outlives the edges that point to it. Marked
  /// mutable so the line's geometry stays const while its edge list grows.
  void attach(const Edge* e) const { edges_.push_back(e); }
  [[nodiscard]] const std::vector<const Edge*>& edges() const { return edges_; }

 private:
  /// Reduce (dx, dy, ofs) by gcd(dx, dy, ofs) — the gcd of all three, so ofs is
  /// never truncated — then sign-normalize so the first non-zero of (dx, dy) is
  /// positive.
  void canonicalize();

  mutable std::vector<const Edge*> edges_;
};

}  // namespace fastdrc
