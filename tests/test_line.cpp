// Tests for the canonical, undirected octilinear Line. Stored as a gcd-reduced
// direction (dx, dy) plus offset ofs; the same geometric line — from any pair
// of collinear points, either direction — must produce identical fields.

#include <cassert>
#include <cstdio>

#include "fastdrc/line.hpp"

using namespace fastdrc;

int main() {
  // Horizontal y = 0: dx=1, dy=0, ofs=0. f = dx*Py + dy*Px + ofs = Py.
  const Line ax{Coord2D{0, 0}, Coord2D{4, 0}};
  assert(ax.dx == 1 && ax.dy == 0 && ax.ofs == 0);
  assert(ax.eval({0, 5}) == 5);
  assert(ax.side({0, 5}) == 1);
  assert(ax.side({0, -5}) == -1);
  assert(ax.contains({2, 0}) && ax.contains({0, 0}) && ax.contains({4, 0}));

  // Same line from reversed direction and from other collinear points ->
  // identical canonical fields (the interning guarantee).
  const Line rev{Coord2D{4, 0}, Coord2D{0, 0}};
  const Line other{Coord2D{1, 0}, Coord2D{9, 0}};
  assert(rev.dx == ax.dx && rev.dy == ax.dy && rev.ofs == ax.ofs);
  assert(other.dx == ax.dx && other.dy == ax.dy && other.ofs == ax.ofs);

  // Vertical x = 0: dx=0, dy=1, ofs=0. f = Px.
  const Line vert{Coord2D{0, 0}, Coord2D{0, 100}};
  assert(vert.dx == 0 && vert.dy == 1 && vert.ofs == 0);
  assert(vert.side({3, 50}) == 1);
  assert(vert.side({-3, 50}) == -1);

  // 45-degree y = x: dx=1, dy=-1, ofs=0. f = Py - Px.
  const Line diag{Coord2D{0, 0}, Coord2D{2, 2}};
  assert(diag.dx == 1 && diag.dy == -1 && diag.ofs == 0);
  assert(diag.side({1, 0}) == -1);
  assert(diag.side({0, 1}) == 1);
  assert(diag.contains({5, 5}));
  const Line diag_rev{Coord2D{2, 2}, Coord2D{0, 0}};  // reversed -> same line
  assert(diag_rev.dx == diag.dx && diag_rev.dy == diag.dy &&
         diag_rev.ofs == diag.ofs);

  // Direct ctor: sign canonicalization, (0,-1,5) -> (0,1,-5).
  const Line lit{0, -1, 5};
  assert(lit.dx == 0 && lit.dy == 1 && lit.ofs == -5);

  // Direct ctor: gcd reduction, (2,-2,4) -> (1,-1,2).
  const Line reduced{2, -2, 4};
  assert(reduced.dx == 1 && reduced.dy == -1 && reduced.ofs == 2);

  // Axis-aligned lines reduce correctly even with a zero component:
  // std::gcd(0,n) == n, and gcd(dx,dy) always divides ofs for integer-point
  // lines, so no separate gcd with ofs is needed.
  const Line hscaled{2, 0, -10};  // 2*Py - 10 = 0  ->  Py = 5
  assert(hscaled.dx == 1 && hscaled.dy == 0 && hscaled.ofs == -5);
  const Line vscaled{0, 3, 6};  // 3*Px + 6 = 0  ->  Px = -2
  assert(vscaled.dx == 0 && vscaled.dy == 1 && vscaled.ofs == 2);

  // ofs must be reduced by gcd(dx,dy,ofs), never gcd(dx,dy) alone. This line
  // (2*Py + 5 = 0 -> Py = -2.5) has no integer point and is already primitive;
  // it must be preserved exactly, not truncated to (1,0,2).
  const Line halfgrid{2, 0, 5};
  assert(halfgrid.dx == 2 && halfgrid.dy == 0 && halfgrid.ofs == 5);

  // int64 needed (45-degree line far from origin); int128 never.
  const Coord1D q = 2'000'000'000;  // ~2e9, within int32
  const Line antiln{Coord2D{q, q}, Coord2D{q + 1, q - 1}};  // dx=1, dy=1
  assert(antiln.dx == 1 && antiln.dy == 1);
  assert(antiln.ofs == -(static_cast<LineValue>(q) + q));
  assert(antiln.side({0, 0}) == -1);

  std::puts("all Line tests passed");
  return 0;
}
