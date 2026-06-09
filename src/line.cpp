#include "fastdrc/line.hpp"

#include <cassert>
#include <numeric>

namespace fastdrc {

void Line::canonicalize() {
  // Reduce by gcd of ALL THREE (std::gcd works on absolute values). Using
  // gcd(dx,dy,ofs) — not gcd(dx,dy) — keeps ofs exact: gcd(dx,dy) need not
  // divide ofs (e.g. a line at a half-grid position has no integer point and
  // is already primitive), and dividing by it would truncate.
  if (const LineValue g = std::gcd(std::gcd(dx, dy), ofs); g > 1) {
    dx = static_cast<Coord1D>(dx / g);
    dy = static_cast<Coord1D>(dy / g);
    ofs /= g;
  }
  // Sign: make the first non-zero of (dx, dy) positive, so the two directions
  // of one line collapse to a single representation.
  if (dx < 0 || (dx == 0 && dy < 0)) {
    dx = -dx;
    dy = -dy;
    ofs = -ofs;
  }
}

Line::Line(Coord1D dx_, Coord1D dy_, LineValue ofs_)
    : dx(dx_), dy(dy_), ofs(ofs_) {
  canonicalize();
}

Line::Line(Coord2D p0, Coord2D p1) {
  // Differences of int32 coordinates need a wider type to avoid overflow.
  // y-up convention: dy stores y0 - y1 so the normal is (dy, dx).
  const std::int64_t rdx = static_cast<std::int64_t>(p1.x) - p0.x;
  const std::int64_t rdy = static_cast<std::int64_t>(p0.y) - p1.y;
  const std::int64_t g = std::gcd(rdx, rdy);

  assert(g > 0 && "degenerate line: p0 == p1");

  dx = static_cast<Coord1D>(rdx / g);
  dy = static_cast<Coord1D>(rdy / g);

  assert(dx >= -1 && dx <= 1 && dy >= -1 && dy <= 1 &&
         "non-octilinear edge: only 0/45/90 degrees are supported");

  ofs = -(static_cast<LineValue>(dy) * p0.x + static_cast<LineValue>(dx) * p0.y);
  canonicalize();  // direction is already reduced; this fixes the sign
}

LineValue Line::eval(Coord2D p) const {
  return static_cast<LineValue>(dx) * p.y + static_cast<LineValue>(dy) * p.x +
         ofs;
}

int Line::side(Coord2D p) const {
  const LineValue v = eval(p);
  return (v > 0) - (v < 0);
}

}  // namespace fastdrc
