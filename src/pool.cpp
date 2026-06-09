#include "fastdrc/pool.hpp"

#include <algorithm>
#include <iterator>

namespace fastdrc {

PointPtr Pool::point(Coord1D x, Coord1D y) {
  const std::uint64_t key = point_key(x, y);
  if (auto it = points_.find(key); it != points_.end()) {
    return it->second;
  }
  auto p = std::make_shared<const Point>(Coord2D{x, y});
  points_.emplace(key, p);
  return p;
}

LinePtr Pool::line(Coord2D p0, Coord2D p1) {
  const Line ln{p0, p1};  // normalizes to (dx, dy, ofs)
  Bucket& bucket = lines_[DirKey{ln.dx, ln.dy}];

  // Binary search the orientation bucket for this offset.
  auto it = std::lower_bound(
      bucket.begin(), bucket.end(), ln.ofs,
      [](const LineSlot& s, LineValue v) { return s.ofs < v; });
  if (it != bucket.end() && it->ofs == ln.ofs) {
    return it->line;  // already interned
  }

  auto p = std::make_shared<const Line>(ln);
  bucket.insert(it, LineSlot{ln.ofs, p});  // keep the bucket sorted by ofs
  ++line_count_;
  return p;
}

EdgePtr Pool::edge(const PointPtr& p0, const PointPtr& p1) {
  const EdgeKey key{p0.get(), p1.get()};
  if (auto it = edges_.find(key); it != edges_.end()) {
    return it->second;
  }
  const LinePtr ln = line(p0->xy, p1->xy);  // interned supporting line
  auto e = std::make_shared<const Edge>(Edge{p0, p1, ln});
  ln->attach(e.get());  // register this edge on its line (non-owning)
  p0->attach(e.get());  // and on each of its endpoints
  p1->attach(e.get());
  edges_.emplace(key, e);
  return e;
}

LinePtr Pool::nearest_above(Coord1D dx, Coord1D dy, LineValue ofs) const {
  const auto bit = lines_.find(DirKey{dx, dy});
  if (bit == lines_.end()) return nullptr;
  const Bucket& bucket = bit->second;
  const auto it = std::upper_bound(
      bucket.begin(), bucket.end(), ofs,
      [](LineValue v, const LineSlot& s) { return v < s.ofs; });
  return it == bucket.end() ? nullptr : it->line;
}

LinePtr Pool::nearest_below(Coord1D dx, Coord1D dy, LineValue ofs) const {
  const auto bit = lines_.find(DirKey{dx, dy});
  if (bit == lines_.end()) return nullptr;
  const Bucket& bucket = bit->second;
  const auto it = std::lower_bound(
      bucket.begin(), bucket.end(), ofs,
      [](const LineSlot& s, LineValue v) { return s.ofs < v; });
  if (it == bucket.begin()) return nullptr;
  return std::prev(it)->line;
}

LinePtr Pool::nearest(Coord1D dx, Coord1D dy, LineValue ofs) const {
  const auto bit = lines_.find(DirKey{dx, dy});
  if (bit == lines_.end()) return nullptr;
  const Bucket& bucket = bit->second;
  if (bucket.empty()) return nullptr;

  const auto it = std::lower_bound(
      bucket.begin(), bucket.end(), ofs,
      [](const LineSlot& s, LineValue v) { return s.ofs < v; });
  if (it == bucket.end()) return bucket.back().line;  // all below target
  if (it->ofs == ofs) return it->line;                // exact match
  if (it == bucket.begin()) return it->line;          // nothing below

  const auto lo = std::prev(it);
  return (ofs - lo->ofs <= it->ofs - ofs) ? lo->line : it->line;
}

}  // namespace fastdrc
