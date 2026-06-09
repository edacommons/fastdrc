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

  std::puts("all boolean tests passed");
  return 0;
}

