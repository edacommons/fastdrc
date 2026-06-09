// Dependency-free C++ smoke tests for the FastDRC core API surface.
// The methods are stubs; these tests just assert the surface compiles, links,
// and is callable. Run via CTest (`ctest`) or directly (`./test_drc`).

#include <cassert>
#include <cstdio>

#include "fastdrc/drc.hpp"

using namespace fastdrc;

int main() {
  Layout layout;
  Layer& m1 = layout.layer(1);
  m1.add_rect(0, 0, 10, 10);                    // four coordinates
  m1.add_rect(Coord2D{0, 0}, Coord2D{10, 10});  // same rect, two corners
  m1.add_polygon({Coord2D{0, 0}, Coord2D{10, 0}, Coord2D{10, 10}});
  // Interning dedups across shapes. Unique points: (0,0),(10,0),(10,10),(0,10).
  assert(m1.point_count() == 4);
  // 4 rect edges + 1 new polygon diagonal (10,10)->(0,0) = 5.
  assert(m1.line_count() == 5);
  // Directed edges: 4 rect edges + 1 new triangle edge (the other two triangle
  // edges coincide with rect edges in the same direction) = 5.
  assert(m1.edge_count() == 5);

  // Layers are independent: each owns its own pool.
  Layer& m2 = layout.layer(2);
  m2.add_rect(0, 0, 10, 10);
  assert(m2.point_count() == 4 && m2.edge_count() == 4);
  assert(layout.layer_count() == 2);
  assert(layout.has_layer(1) && layout.has_layer(2) && !layout.has_layer(3));
  // layer(1) again returns the same layer (geometry preserved).
  assert(layout.layer(1).point_count() == 4);

  // A check takes a single config (layout description + measurements + checks).
  layout.check(CheckConfig{});  // stub: no-op

  std::puts("all C++ tests passed");
  return 0;
}
