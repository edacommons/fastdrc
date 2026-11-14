// Tests for interned Edges and the Line <-> Edge back-reference.

#include <cassert>
#include <cstdio>

#include "fastdrc/pool.hpp"

using namespace fastdrc;

int main() {
  Pool pool;

  // Two collinear edges share one supporting Line; the Line lists both.
  const auto e1 = pool.edge({0, 0}, {4, 0});
  const auto e2 = pool.edge({4, 0}, {8, 0});
  assert(e1->line == e2->line);
  assert(pool.edge_count() == 2);
  assert(pool.line_count() == 1);
  assert(e1->line->edges().size() == 2);

  // Endpoints are interned points, shared between adjacent edges.
  assert(e1->p1 == e2->p0);        // both are the point (4, 0)
  assert(pool.point_count() == 3);  // (0,0), (4,0), (8,0)

  // Points also know their incident edges: (4,0) is on e1 and e2.
  const auto shared_pt = e1->p1;
  assert((shared_pt->xy == Coord2D{4, 0}));
  assert(shared_pt->edges().size() == 2);

  // The same directed edge is one shared object.
  const auto e1b = pool.edge({0, 0}, {4, 0});
  assert(e1b == e1);
  assert(pool.edge_count() == 2);

  // Reversed edge: a different directed Edge, but the same (undirected) Line.
  const auto e3 = pool.edge({4, 0}, {0, 0});
  assert(e3 != e1);
  assert(e3->line == e1->line);
  assert(pool.edge_count() == 3);
  assert(e1->line->edges().size() == 3);
  assert(shared_pt->edges().size() == 3);  // (4,0) now also on e3

  // A perpendicular edge lies on a different line.
  const auto e4 = pool.edge(Coord2D{0, 0}, Coord2D{0, 5});  // spelled out: {0, n} also reads as a null pointer
  assert(e4->line != e1->line);
  assert(pool.line_count() == 2);

  std::puts("all Edge tests passed");
  return 0;
}
