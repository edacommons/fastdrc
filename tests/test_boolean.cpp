// Layer-algebra booleans between two tile sets: OR / AND / SUB / RSUB / XOR,
// plus an angled operand (tiles with cuts) to confirm any-angle.

#include <cassert>
#include <cstdio>
#include <vector>

#include "fastdrc/sweep.hpp"

using namespace fastdrc;

static long long area2(const std::vector<Tile>& ts) {
  long long a = 0;
  for (const Tile& t : ts) a += static_cast<long long>(t.area2());
  return a;
}

int main() {
  // A = [0,10]x[0,10], B = [5,15]x[0,10]; overlap [5,10]x[0,10] (area 50).
  const std::vector<Tile> a = {{0, 0, 10, 10, 0, 0}};
  const std::vector<Tile> b = {{5, 0, 15, 10, 0, 0}};

  assert(area2(boolean(a, b, BoolOp::Or)) == 150 * 2);   // union
  assert(area2(boolean(a, b, BoolOp::And)) == 50 * 2);   // intersection
  assert(area2(boolean(a, b, BoolOp::Sub)) == 50 * 2);   // A - B = [0,5]
  assert(area2(boolean(b, a, BoolOp::Sub)) == 50 * 2);   // B - A = swap operands
  assert(area2(boolean(a, b, BoolOp::Xor)) == 100 * 2);  // both differences

  // AND of disjoint -> empty; OR of disjoint -> sum.
  {
    const std::vector<Tile> c = {{0, 0, 10, 10, 0, 0}};
    const std::vector<Tile> d = {{20, 0, 30, 10, 0, 0}};
    assert(boolean(c, d, BoolOp::And).empty());
    assert(area2(boolean(c, d, BoolOp::Or)) == 200 * 2);
  }

  // SUB exact region: A - B should be exactly [0,5]x[0,10].
  {
    const auto r = boolean(a, b, BoolOp::Sub);
    assert(r.size() == 1);
    assert(r[0].x0 == 0 && r[0].x1 == 5 && r[0].y0 == 0 && r[0].y1 == 10);
    assert(r[0].cut_left == 0 && r[0].cut_right == 0);
  }

  // Angled operand: a 45-degree wire AND its bbox -> the wire itself (area 100).
  {
    const std::vector<Tile> wire = {{0, 0, 20, 10, 10, 10}};  // parallelogram
    const std::vector<Tile> bbox = {{0, 0, 20, 10, 0, 0}};
    assert(area2(boolean(wire, bbox, BoolOp::And)) == 100 * 2);
    assert(boolean(wire, bbox, BoolOp::Sub).empty());  // wire inside its bbox
  }

  // inflate (halo grow): a rect grown by 2 on all sides -> [-2,-2,12,12].
  {
    const std::vector<Tile> t = {{0, 0, 10, 10, 0, 0}};
    const auto g = inflate(t, 2);
    assert(area2(g) == 14 * 14 * 2);
  }

  // inflate closes a gap: two rects 4 apart grown by 2 each meet at x=12 ->
  // connected region, area 2 * 14*14 (abutting, no double-count).
  {
    const std::vector<Tile> t = {{0, 0, 10, 10, 0, 0}, {14, 0, 24, 10, 0, 0}};
    const auto g = inflate(t, 2);
    assert(area2(g) == 2 * 14 * 14 * 2);
  }

  // Edges that CROSS between vertex heights: two right triangles whose
  // hypotenuses cross at (10,10). Each has area 200, they share 100.
  {
    const std::vector<Tile> t1 = {{0, 0, 20, 20, 0, -20}};  // (0,0)(20,0)(0,20)
    const std::vector<Tile> t2 = {{0, 0, 20, 20, 0, 20}};   // (0,0)(20,20)(0,20)
    assert(area2(t1) == 200 * 2 && area2(t2) == 200 * 2);
    assert(area2(boolean(t1, t2, BoolOp::And)) == 100 * 2);
    assert(area2(boolean(t1, t2, BoolOp::Or)) == 300 * 2);
    assert(area2(boolean(t1, t2, BoolOp::Xor)) == 200 * 2);
    assert(area2(boolean(t1, t2, BoolOp::Sub)) == 100 * 2);
    assert(area2(boolean(t2, t1, BoolOp::Sub)) == 100 * 2);
    // the union is a disjoint tiling: A or B == (A xor B) + (A and B) by area,
    // and re-unioning it with itself changes nothing
    const auto u = boolean(t1, t2, BoolOp::Or);
    assert(area2(boolean(u, u, BoolOp::Or)) == 300 * 2);
    assert(boolean(u, u, BoolOp::Xor).empty());
  }

  // A diagonal crossing a rectangle's vertical side: triangle (0,0)(20,0)(0,20)
  // against the box [5,15]x[0,20]; the hypotenuse x+y=20 enters the box at
  // y=5 and leaves at y=15. Intersection area = 5*10 + 10*10/2 = 100.
  {
    const std::vector<Tile> tri = {{0, 0, 20, 20, 0, -20}};
    const std::vector<Tile> box = {{5, 0, 15, 20, 0, 0}};
    assert(area2(boolean(tri, box, BoolOp::And)) == 100 * 2);
    assert(area2(boolean(tri, box, BoolOp::Or)) == 300 * 2);
    assert(area2(boolean(box, tri, BoolOp::Sub)) == 100 * 2);
  }

  // inflate grows an ANGLED tile by moving its sides out, not by its bbox: the
  // triangle (0,0)(20,0)(0,20) dilated by the square of half-side 2 is the
  // hexagon (-2,-2)(22,-2)(22,2)(2,22)(-2,22) -> 24*24 - 20*20/2 = 376.
  {
    const std::vector<Tile> tri = {{0, 0, 20, 20, 0, -20}};
    assert(area2(inflate(tri, 2)) == 376 * 2);
  }

  // inflate with d < 0 is a true erosion of the MERGED region. An L made of two
  // tiles shrunk by 2 stays one connected L: [2,8]x[2,8] + [2,18]x[7,8] = 46
  // (shrinking each tile on its own would leave two slivers, area 22).
  {
    const std::vector<Tile> ell = {{0, 0, 10, 5, 0, 0}, {0, 5, 20, 10, 0, 0}};
    const auto s = inflate(ell, -2);
    assert(area2(s) == 46 * 2);
    // grow then shrink a rectangle returns it; shrinking past half-width empties
    const std::vector<Tile> sq = {{0, 0, 10, 10, 0, 0}};
    assert(area2(inflate(inflate(sq, 3), -3)) == 100 * 2);
    assert(inflate(sq, -5).empty());
    assert(area2(inflate(sq, -2)) == 36 * 2);
  }

  std::puts("all boolean tests passed");
  return 0;
}

