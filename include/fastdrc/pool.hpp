#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <unordered_map>
#include <vector>

#include "fastdrc/edge.hpp"
#include "fastdrc/handles.hpp"
#include "fastdrc/line.hpp"
#include "fastdrc/point.hpp"
#include "fastdrc/types.hpp"

namespace fastdrc {

/// Interning factory for geometry (the flyweight pattern), plus the line index
/// that DRC queries hit constantly.
///
/// Same coords -> same Point; same normalized (dx,dy,ofs) -> same Line; same
/// ordered endpoint pair -> same Edge. Returned handles are shared_ptr<const>;
/// the pool holds strong references.
///
/// Lines are indexed by orientation (dx,dy) -> a vector sorted by ofs. Within a
/// bucket every line is parallel and ofs is unique, so:
///   - interning a line is a binary-search find, insert at lower_bound,
///   - "nearest parallel line above/below/closest to an offset" is a
///     lower_bound/upper_bound — the core spacing/width query.
/// This favors fast queries (the DRC hot path) at the cost of O(n) insert
/// during load, which is the cheaper phase.
class Pool {
 public:
  PointPtr point(Coord1D x, Coord1D y);
  PointPtr point(Coord2D p) { return point(p.x, p.y); }

  /// The line through two points (must be octilinear), interned by its
  /// normalized (dx, dy, ofs).
  LinePtr line(Coord2D p0, Coord2D p1);
  LinePtr line(const PointPtr& p0, const PointPtr& p1) {
    return line(p0->xy, p1->xy);
  }

  /// The directed edge p0 -> p1, interned by its ordered endpoint pair.
  EdgePtr edge(Coord2D p0, Coord2D p1) { return edge(point(p0), point(p1)); }
  EdgePtr edge(const PointPtr& p0, const PointPtr& p1);

  /// Nearest line of orientation (dx, dy) relative to a target offset. (dx, dy)
  /// must be a canonical direction (as produced by Line). Null if none.
  ///   - nearest_above: smallest ofs strictly greater than `ofs`
  ///   - nearest_below: largest ofs strictly less than `ofs`
  ///   - nearest: closest by |Δofs| (exact match wins)
  [[nodiscard]] LinePtr nearest_above(Coord1D dx, Coord1D dy,
                                      LineValue ofs) const;
  [[nodiscard]] LinePtr nearest_below(Coord1D dx, Coord1D dy,
                                      LineValue ofs) const;
  [[nodiscard]] LinePtr nearest(Coord1D dx, Coord1D dy, LineValue ofs) const;

  [[nodiscard]] std::size_t point_count() const { return points_.size(); }
  [[nodiscard]] std::size_t line_count() const { return line_count_; }
  [[nodiscard]] std::size_t edge_count() const { return edges_.size(); }

 private:
  // (x, y) packs losslessly into 64 bits, so distinct points never collide.
  static std::uint64_t point_key(Coord1D x, Coord1D y) {
    return (static_cast<std::uint64_t>(static_cast<std::uint32_t>(x)) << 32) |
           static_cast<std::uint32_t>(y);
  }

  // Orientation key: the canonical direction of a family of parallel lines.
  struct DirKey {
    Coord1D dx;
    Coord1D dy;
    bool operator==(const DirKey&) const = default;
  };

  struct DirKeyHash {
    std::size_t operator()(const DirKey& k) const noexcept {
      std::uint64_t h = 1469598103934665603ull;  // FNV-1a offset basis
      h = (h ^ static_cast<std::uint32_t>(k.dx)) * 1099511628211ull;
      h = (h ^ static_cast<std::uint32_t>(k.dy)) * 1099511628211ull;
      return static_cast<std::size_t>(h);
    }
  };

  // One line within an orientation bucket. Keeping ofs inline lets the binary
  // search compare offsets without dereferencing the LinePtr.
  struct LineSlot {
    LineValue ofs;
    LinePtr line;
  };
  // A bucket is kept sorted ascending by ofs, with unique ofs.
  using Bucket = std::vector<LineSlot>;

  // Edges are directed: keyed by the ordered pair of (interned) endpoints, by
  // pointer identity — same coords give the same PointPtr, so this is exact.
  struct EdgeKey {
    const Point* a;
    const Point* b;
    bool operator==(const EdgeKey&) const = default;
  };

  struct EdgeKeyHash {
    std::size_t operator()(const EdgeKey& k) const noexcept {
      std::uint64_t h = 1469598103934665603ull;  // FNV-1a offset basis
      h = (h ^ reinterpret_cast<std::uintptr_t>(k.a)) * 1099511628211ull;
      h = (h ^ reinterpret_cast<std::uintptr_t>(k.b)) * 1099511628211ull;
      return static_cast<std::size_t>(h);
    }
  };

  // Declared so that on destruction edges_ (which strong-ref lines) is torn
  // down before lines_, which before points_.
  std::unordered_map<std::uint64_t, PointPtr> points_;
  std::unordered_map<DirKey, Bucket, DirKeyHash> lines_;
  std::unordered_map<EdgeKey, EdgePtr, EdgeKeyHash> edges_;
  std::size_t line_count_ = 0;
};

}  // namespace fastdrc
