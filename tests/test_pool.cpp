// Tests for the interning Pool: same inputs -> same shared object.

#include <cassert>
#include <cstdio>

#include "fastdrc/pool.hpp"

using namespace fastdrc;

int main() {
  Pool pool;

  // Same coordinates -> identical shared_ptr (one stored instance).
  const auto a = pool.point(3, 4);
  const auto b = pool.point(3, 4);
  assert(a == b);              // same control block / pointer
  assert(a.get() == b.get());
  assert(a->xy.x == 3 && a->xy.y == 4);
  assert(pool.point_count() == 1);

  // Different coordinates -> different object.
  const auto c = pool.point(5, 6);
  assert(c != a);
  assert(pool.point_count() == 2);

  // Any pair of collinear points on the same line shares one Line object,
  // INCLUDING the opposite direction (lines are canonical / undirected).
  const auto l1 = pool.line({0, 0}, {4, 0});  // y = 0
  const auto l2 = pool.line({1, 0}, {9, 0});  // other collinear points
  const auto l3 = pool.line({4, 0}, {0, 0});  // reversed direction
  assert(l1 == l2);
  assert(l3 == l1);
  assert(pool.line_count() == 1);

  // A genuinely different line increments the count.
  const auto diag = pool.line(pool.point(0, 0), pool.point(2, 2));
  assert(diag->dx == 1 && diag->dy == -1);  // canonical form for y = x
  assert(pool.line_count() == 2);

  // --- nearest-line queries within an orientation bucket ---
  Pool q;
  // Horizontal lines (dx=1, dy=0): y=k gives ofs = -k. Created out of order.
  const auto h20 = q.line({0, 20}, {1, 20});  // ofs -20
  const auto h0 = q.line({0, 0}, {1, 0});     // ofs  0
  const auto h30 = q.line({0, 30}, {1, 30});  // ofs -30
  const auto h10 = q.line({0, 10}, {1, 10});  // ofs -10
  assert(q.line_count() == 4);                // sorted bucket: -30,-20,-10,0
  // Collinear points return the same interned line (binary-search find).
  assert(q.line({0, 10}, {5, 10}) == h10);
  assert(q.line_count() == 4);

  assert(q.nearest_above(1, 0, -15) == h10);    // first ofs > -15
  assert(q.nearest_below(1, 0, -15) == h20);    // last ofs < -15
  assert(q.nearest(1, 0, -12) == h10);          // -10 closer than -20
  assert(q.nearest(1, 0, -10) == h10);          // exact
  assert(q.nearest(1, 0, -100) == h30);         // below all -> smallest ofs
  assert(q.nearest_above(1, 0, 0) == nullptr);  // nothing above the top (h0)
  assert(q.nearest_below(1, 0, -30) == nullptr);
  assert(q.nearest(0, 1, 0) == nullptr);        // no lines on this orientation

  std::puts("all Pool tests passed");
  return 0;
}
