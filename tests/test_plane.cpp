// Tests for the corner-stitched Plane: insert + point-location, and the
// full-coverage tiling invariant.

#include <cassert>
#include <cstdio>

#include "fastdrc/plane.hpp"

using namespace fastdrc;

int main() {
  Plane p;  // one space tile covering the universe
  assert(p.tile_count() == 1 && p.solid_count() == 0);
  const Area universe = p.total_area();

  // Locating anything in the empty plane returns the space universe tile.
  assert(!p.find(5, 5)->solid);

  p.insert(0, 0, 10, 10);    // solid A
  p.insert(20, 0, 30, 10);   // solid B (disjoint, same row)
  assert(p.solid_count() == 2);

  // The plane is still a full, exact tiling.
  assert(p.total_area() == universe);

  // Point-location lands in the right tiles.
  CTile* a = p.find(5, 5);
  assert(a->solid && a->x0 == 0 && a->y0 == 0 && a->x1 == 10 && a->y1 == 10);

  CTile* b = p.find(25, 5);
  assert(b->solid && b->x0 == 20 && b->x1 == 30);

  assert(!p.find(15, 5)->solid);  // gap between A and B
  assert(!p.find(5, 50)->solid);  // space above A
  assert(!p.find(-5, 5)->solid);  // space left of A

  // A point on a solid corner/edge resolves consistently (interior of A).
  assert(p.find(0, 0)->solid);

  // Insert a far tile (so the last-tile hint is far away), then locate back
  // near A: the index seeds the walk near the target.
  p.insert(1'000, 1'000, 1'010, 1'010);  // C, far
  CTile* a2 = p.find(5, 5);
  assert(a2->solid && a2->x0 == 0 && a2->x1 == 10);
  assert(p.find(1'005, 1'005)->solid);   // and C itself
  assert(p.total_area() == universe);

  std::puts("all Plane tests passed");
  return 0;
}
