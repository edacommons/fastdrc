// Region accumulator over the Manhattan sweep: union / cut / polygon compose,
// area correctness, and that the merged tiling stays compact (extend-merge).

#include <cassert>
#include <cstdio>

#include "fastdrc/region.hpp"

using namespace fastdrc;

static long long area2_ll(const Region& r) {
  return static_cast<long long>(r.area2());
}

int main() {
  // Overlapping rectangles union (no double count).
  {
    Region g;
    g.add(Rect{0, 0, 10, 10});
    g.add(Rect{5, 5, 15, 15});
    assert(area2_ll(g) == 175 * 2);
  }

  // Cut a hole -> donut.
  {
    Region g;
    g.add(Rect{0, 0, 10, 10});
    g.cut(Rect{3, 3, 7, 7});
    assert(area2_ll(g) == 84 * 2);
  }

  // Rectilinear polygon (L-shape) area 64.
  {
    Region g;
    g.add_polygon({{0, 0}, {10, 0}, {10, 4}, {4, 4}, {4, 10}, {0, 10}});
    assert(area2_ll(g) == 64 * 2);
  }

  // Polygon with a polygon cut-out -> donut 84.
  {
    Region g;
    g.add_polygon({{0, 0}, {10, 0}, {10, 10}, {0, 10}});
    g.cut_polygon({{3, 3}, {7, 3}, {7, 7}, {3, 7}});
    assert(area2_ll(g) == 84 * 2);
  }

  // Extend-merge keeps the tiling compact: a tall solid column built from many
  // abutting rectangles is a single tile, not many slivers.
  {
    Region g;
    for (int i = 0; i < 100; ++i) g.add(Rect{0, i, 2, i + 1});
    assert(g.rect_count() == 1);
    assert(area2_ll(g) == 200 * 2);  // 2 wide x 100 tall
  }

  // A plain filled square is one tile (duplicate input collapses).
  {
    Region g;
    g.add(Rect{0, 0, 10, 10});
    g.add(Rect{0, 0, 10, 10});
    assert(g.rect_count() == 1);
    assert(area2_ll(g) == 100 * 2);
  }

  std::puts("all region tests passed");
  return 0;
}
