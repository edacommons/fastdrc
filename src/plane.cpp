#include "fastdrc/plane.hpp"

#include <algorithm>
#include <cassert>
#include <vector>

namespace fastdrc {

namespace {

// The universe bounds. Span (2e9) stays within int32 so coordinate
// differences don't overflow.
constexpr Coord1D kUMin = -1'000'000'000;
constexpr Coord1D kUMax = 1'000'000'000;

struct Box {
  Coord1D x0, y0, x1, y1;
};

bool overlaps(const Box& a, const Box& b) {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}

// Append the disjoint pieces of a \ b to `out` (up to 4 rectangles).
void subtract(const Box& a, const Box& b, std::vector<Box>& out) {
  if (!overlaps(a, b)) {
    out.push_back(a);
    return;
  }
  const Coord1D ix0 = a.x0 > b.x0 ? a.x0 : b.x0;
  const Coord1D iy0 = a.y0 > b.y0 ? a.y0 : b.y0;
  const Coord1D ix1 = a.x1 < b.x1 ? a.x1 : b.x1;
  const Coord1D iy1 = a.y1 < b.y1 ? a.y1 : b.y1;
  if (a.y0 < iy0) out.push_back({a.x0, a.y0, a.x1, iy0});
  if (iy1 < a.y1) out.push_back({a.x0, iy1, a.x1, a.y1});
  if (a.x0 < ix0) out.push_back({a.x0, iy0, ix0, iy1});
  if (ix1 < a.x1) out.push_back({ix1, iy0, a.x1, iy1});
}

}  // namespace

Plane::Plane() {
  tiles_.push_back(CTile{kUMin, kUMin, kUMax, kUMax, false, nullptr, nullptr,
                         nullptr, nullptr});
  hint_ = &tiles_.front();
}

void Plane::restitch() {
  for (CTile& t : tiles_) {
    t.tr = t.rt = t.bl = t.lb = nullptr;
    for (CTile& o : tiles_) {
      if (&o == &t) continue;
      // tr: tile to the right covering the top of t's right edge.
      if (o.x0 == t.x1 && o.y0 <= t.y1 - 1 && o.y1 > t.y1 - 1) t.tr = &o;
      // rt: tile above covering the right of t's top edge.
      if (o.y0 == t.y1 && o.x0 <= t.x1 - 1 && o.x1 > t.x1 - 1) t.rt = &o;
      // bl: tile to the left covering the bottom of t's left edge.
      if (o.x1 == t.x0 && o.y0 <= t.y0 && o.y1 > t.y0) t.bl = &o;
      // lb: tile below covering the left of t's bottom edge.
      if (o.y1 == t.y0 && o.x0 <= t.x0 && o.x1 > t.x0) t.lb = &o;
    }
  }
}

CTile* Plane::insert(Coord1D x0, Coord1D y0, Coord1D x1, Coord1D y1) {
  const Box R{x0, y0, x1, y1};
  assert(x0 < x1 && y0 < y1 && "degenerate insert");

  // Replace the space tiles R overlaps with their R-complements; R becomes one
  // solid tile (its area was all space, so the complements + R re-tile exactly
  // the removed area).
  std::vector<Box> space_pieces;
  for (auto it = tiles_.begin(); it != tiles_.end();) {
    const Box T{it->x0, it->y0, it->x1, it->y1};
    if (overlaps(T, R)) {
      assert(!it->solid && "insert precondition: area must be space");
      subtract(T, R, space_pieces);
      it = tiles_.erase(it);
    } else {
      ++it;
    }
  }

  tiles_.push_back(CTile{x0, y0, x1, y1, true, nullptr, nullptr, nullptr,
                         nullptr});
  CTile* solid = &tiles_.back();
  for (const Box& p : space_pieces) {
    tiles_.push_back(CTile{p.x0, p.y0, p.x1, p.y1, false, nullptr, nullptr,
                           nullptr, nullptr});
  }

  restitch();
  reindex();
  hint_ = solid;
  return solid;
}

void Plane::reindex() {
  by_left_.clear();
  for (CTile& t : tiles_) by_left_[t.x0].push_back(&t);
  for (auto& [k, v] : by_left_) {
    std::sort(v.begin(), v.end(),
              [](const CTile* a, const CTile* b) { return a->y0 < b->y0; });
  }
}

CTile* Plane::start_hint(Coord1D x, Coord1D y) {
  if (by_left_.empty()) return &tiles_.front();

  // Column with the largest left-edge x <= x (else the leftmost column).
  auto col = by_left_.upper_bound(x);  // first key > x
  if (col != by_left_.begin()) --col;  // largest key <= x

  const auto& v = col->second;  // tiles sorted by y0
  // Tile with the largest y0 <= y (else the lowest).
  const auto pos = std::upper_bound(
      v.begin(), v.end(), y,
      [](Coord1D yy, const CTile* t) { return yy < t->y0; });
  return (pos == v.begin()) ? v.front() : *std::prev(pos);
}

CTile* Plane::find(Coord1D x, Coord1D y) {
  CTile* t = start_hint(x, y);
  // Walk vertically into the right strip, then horizontally; a horizontal move
  // can change the strip, so re-check vertically after each.
  while (y < t->y0 || y >= t->y1) {
    t = (y >= t->y1) ? t->rt : t->lb;
  }
  while (x < t->x0 || x >= t->x1) {
    t = (x >= t->x1) ? t->tr : t->bl;
    while (y < t->y0 || y >= t->y1) {
      t = (y >= t->y1) ? t->rt : t->lb;
    }
  }
  hint_ = t;
  return t;
}

std::size_t Plane::solid_count() const {
  std::size_t n = 0;
  for (const CTile& t : tiles_) {
    if (t.solid) ++n;
  }
  return n;
}

Area Plane::total_area() const {
  Area a = 0;
  for (const CTile& t : tiles_) a += t.area();
  return a;
}

}  // namespace fastdrc
