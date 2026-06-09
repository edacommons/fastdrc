// Phase A Manhattan sweep: correctness (union / cut areas, disjointness) and the
// anti-fragmentation properties (extend-merge: N abutting rects -> 1).

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <vector>

#include "fastdrc/sweep.hpp"

using namespace fastdrc;

static std::int64_t total_area(const std::vector<Rect>& rs) {
  std::int64_t a = 0;
  for (const Rect& r : rs) {
    assert(r.x0 < r.x1 && r.y0 < r.y1);  // normalized, non-degenerate
    a += static_cast<std::int64_t>(r.x1 - r.x0) * (r.y1 - r.y0);
  }
  return a;
}

// Brute-force disjointness + coverage check against a small reference grid.
static void check_against_grid(const std::vector<TaggedRect>& in,
                               const std::vector<Rect>& out, int lo, int hi) {
  for (int x = lo; x < hi; ++x) {
    for (int y = lo; y < hi; ++y) {
      int add = 0, cut = 0;
      for (const auto& t : in) {
        const bool inside = x >= std::min(t.rect.x0, t.rect.x1) &&
                            x < std::max(t.rect.x0, t.rect.x1) &&
                            y >= std::min(t.rect.y0, t.rect.y1) &&
                            y < std::max(t.rect.y0, t.rect.y1);
        if (inside) (t.tag == Tag::Add ? add : cut)++;
      }
      const bool want = add > 0 && cut == 0;
      int cover = 0;
      for (const Rect& r : out) {
        if (x >= r.x0 && x < r.x1 && y >= r.y0 && y < r.y1) cover++;
      }
      assert(cover == (want ? 1 : 0));  // exact coverage AND disjoint
    }
  }
}

int main() {
  // Two overlapping squares -> union area 175, no double count.
  {
    std::vector<TaggedRect> in = {
        {{0, 0, 10, 10}, Tag::Add}, {{5, 5, 15, 15}, Tag::Add}};
    const auto out = sweep_manhattan(in);
    assert(total_area(out) == 175);
    check_against_grid(in, out, -2, 18);
  }

  // Square with a hole cut -> 100 - 16 = 84.
  {
    std::vector<TaggedRect> in = {
        {{0, 0, 10, 10}, Tag::Add}, {{3, 3, 7, 7}, Tag::Cut}};
    const auto out = sweep_manhattan(in);
    assert(total_area(out) == 84);
    check_against_grid(in, out, -2, 12);
  }

  // Extend-merge: 50 abutting unit-tall rectangles stacked in y collapse to a
  // SINGLE rectangle (not 50). This is the anti-explosion property.
  {
    std::vector<TaggedRect> in;
    for (int i = 0; i < 50; ++i) in.push_back({{0, i, 1, i + 1}, Tag::Add});
    const auto out = sweep_manhattan(in);
    assert(out.size() == 1);
    assert(out[0].x0 == 0 && out[0].y0 == 0 && out[0].x1 == 1 &&
           out[0].y1 == 50);
  }

  // The same merge across the other axis: 50 abutting unit-wide rectangles in a
  // row -> 1 (per-axis selection picks the x-extend sweep).
  {
    std::vector<TaggedRect> in;
    for (int i = 0; i < 50; ++i) in.push_back({{i, 0, i + 1, 1}, Tag::Add});
    const auto out = sweep_manhattan(in);
    assert(out.size() == 1);
    assert(out[0].x0 == 0 && out[0].x1 == 50 && out[0].y0 == 0 &&
           out[0].y1 == 1);
  }

  // Disjoint inputs stay separate.
  {
    std::vector<TaggedRect> in = {
        {{0, 0, 10, 10}, Tag::Add}, {{20, 0, 30, 10}, Tag::Add}};
    const auto out = sweep_manhattan(in);
    assert(total_area(out) == 200);
    check_against_grid(in, out, -2, 32);
  }

  // Cut that fully splits a bar into two pieces.
  {
    std::vector<TaggedRect> in = {
        {{0, 0, 30, 10}, Tag::Add}, {{10, -1, 20, 11}, Tag::Cut}};
    const auto out = sweep_manhattan(in);
    assert(total_area(out) == 200);  // two 10x10 pieces
    check_against_grid(in, out, -2, 32);
  }

  // ---- Polygon sweep (any straight angle) ----
  auto shoelace2 = [](const std::vector<Coord2D>& v) {
    long long s = 0;
    const std::size_t n = v.size();
    for (std::size_t i = 0; i < n; ++i)
      s += static_cast<long long>(v[i].x) * v[(i + 1) % n].y -
           static_cast<long long>(v[(i + 1) % n].x) * v[i].y;
    return s < 0 ? -s : s;  // doubled area
  };
  auto tiles_area2 = [](const std::vector<Tile>& ts) {
    long long a = 0;
    for (const Tile& t : ts) a += static_cast<long long>(t.area2());
    return a;
  };

  // A plain rectangle ring -> one rect tile (no cuts).
  {
    std::vector<Coord2D> sq = {{0, 0}, {10, 0}, {10, 10}, {0, 10}};
    const auto out = sweep_polygons({{sq, Tag::Add}});
    assert(out.size() == 1 && out[0].cut_left == 0 && out[0].cut_right == 0);
    assert(tiles_area2(out) == shoelace2(sq));  // 200
  }

  // A 45-degree right triangle -> one tile with a single cut.
  {
    std::vector<Coord2D> tri = {{0, 0}, {10, 0}, {10, 10}};
    const auto out = sweep_polygons({{tri, Tag::Add}});
    assert(out.size() == 1);
    assert((out[0].cut_left != 0) != (out[0].cut_right != 0));  // exactly one cut
    assert(tiles_area2(out) == shoelace2(tri));  // 100 (area 50)
  }

  // A 45-degree wire (parallelogram) -> ONE tile, not a staircase.
  {
    std::vector<Coord2D> wire = {{0, 0}, {10, 0}, {20, 10}, {10, 10}};
    const auto out = sweep_polygons({{wire, Tag::Add}});
    assert(out.size() == 1);                      // no fragmentation
    assert(out[0].cut_left != 0 && out[0].cut_right != 0);  // two cuts
    assert(tiles_area2(out) == shoelace2(wire));  // 200 (area 100)
  }

  // An arbitrary (non-45) angle wire -> still ONE tile (the point of Phase C).
  // Slope 2 parallelogram: width 10, height 20, shift 10 in x.
  {
    std::vector<Coord2D> wire = {{0, 0}, {10, 0}, {20, 20}, {10, 20}};
    const auto out = sweep_polygons({{wire, Tag::Add}});
    assert(out.size() == 1);
    assert(out[0].cut_left == 10 && out[0].cut_right == 10);  // extent != height
    assert(tiles_area2(out) == shoelace2(wire));  // 400 (area 200)
  }

  // An octagon (chamfered square) -> a few tiles, exact area.
  {
    std::vector<Coord2D> oct = {{3, 0},  {7, 0},  {10, 3}, {10, 7},
                                {7, 10}, {3, 10}, {0, 7},  {0, 3}};
    const auto out = sweep_polygons({{oct, Tag::Add}});
    assert(tiles_area2(out) == shoelace2(oct));
  }

  // Overlapping rectangle rings union (winding, not XOR) -> 175.
  {
    std::vector<Coord2D> a = {{0, 0}, {10, 0}, {10, 10}, {0, 10}};
    std::vector<Coord2D> b = {{5, 5}, {15, 5}, {15, 15}, {5, 15}};
    const auto out = sweep_polygons({{a, Tag::Add}, {b, Tag::Add}});
    assert(tiles_area2(out) == 175 * 2);
  }

  // Duplicate ring must NOT cancel (winding, not even-odd).
  {
    std::vector<Coord2D> a = {{0, 0}, {10, 0}, {10, 10}, {0, 10}};
    const auto out = sweep_polygons({{a, Tag::Add}, {a, Tag::Add}});
    assert(tiles_area2(out) == 100 * 2);
  }

  // Square with a 45-degree corner cut out (Cut ring).
  {
    std::vector<Coord2D> sq = {{0, 0}, {10, 0}, {10, 10}, {0, 10}};
    std::vector<Coord2D> notch = {{6, 10}, {10, 6}, {10, 10}};  // TR triangle, area 8
    const auto out = sweep_polygons({{sq, Tag::Add}, {notch, Tag::Cut}});
    assert(tiles_area2(out) == (100 - 8) * 2);
  }

  std::puts("all sweep tests passed");
  return 0;
}
