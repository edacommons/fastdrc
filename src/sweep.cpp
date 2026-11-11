#include "fastdrc/sweep.hpp"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <functional>
#include <map>
#include <numeric>
#include <set>
#include <utility>
#include <vector>

namespace fastdrc {

namespace {

// One horizontal sweep edge of a rectangle: at y, the x-interval [x0,x1)
// turns on (+) or off (-) for its tag.
struct Event {
  Coord1D y;
  Coord1D x0, x1;
  int add_d;
  int cut_d;
};

// Horizontal sweep: events on y, spans on x, primitives extend in +y, producing
// y-maximal disjoint rectangles. (The public entry transposes the input first
// when x is the better extend axis.)
std::vector<Rect> horizontal_sweep(const std::vector<TaggedRect>& in) {
  std::vector<Rect> out;

  std::vector<Coord1D> xs;
  std::vector<Event> ev;
  xs.reserve(in.size() * 2);
  ev.reserve(in.size() * 2);
  for (const auto& t : in) {
    const Coord1D x0 = std::min(t.rect.x0, t.rect.x1);
    const Coord1D x1 = std::max(t.rect.x0, t.rect.x1);
    const Coord1D y0 = std::min(t.rect.y0, t.rect.y1);
    const Coord1D y1 = std::max(t.rect.y0, t.rect.y1);
    if (x0 == x1 || y0 == y1) continue;  // degenerate
    const int ad = (t.tag == Tag::Add) ? 1 : 0;
    const int cd = (t.tag == Tag::Cut) ? 1 : 0;
    ev.push_back({y0, x0, x1, +ad, +cd});
    ev.push_back({y1, x0, x1, -ad, -cd});
    xs.push_back(x0);
    xs.push_back(x1);
  }
  if (ev.empty()) return out;

  // Compress x into elementary intervals [xs[k], xs[k+1]).
  std::sort(xs.begin(), xs.end());
  xs.erase(std::unique(xs.begin(), xs.end()), xs.end());
  const std::size_t m = xs.size() - 1;

  std::sort(ev.begin(), ev.end(),
            [](const Event& a, const Event& b) { return a.y < b.y; });

  // Winding counts per elementary interval (the sweep front; O(x-complexity)).
  std::vector<int> add_cnt(m, 0), cut_cnt(m, 0);
  // Open primitives: (xl, xr) -> y where it opened. Extend-while-unchanged.
  std::map<std::pair<Coord1D, Coord1D>, Coord1D> open;
  std::vector<std::pair<Coord1D, Coord1D>> spans;

  std::size_t i = 0;
  const std::size_t n = ev.size();
  while (i < n) {
    const Coord1D y = ev[i].y;
    // Apply every event at this y (order within a y is irrelevant: additive).
    while (i < n && ev[i].y == y) {
      const Event& e = ev[i];
      const std::size_t a =
          static_cast<std::size_t>(
              std::lower_bound(xs.begin(), xs.end(), e.x0) - xs.begin());
      const std::size_t b =
          static_cast<std::size_t>(
              std::lower_bound(xs.begin(), xs.end(), e.x1) - xs.begin());
      for (std::size_t k = a; k < b; ++k) {
        add_cnt[k] += e.add_d;
        cut_cnt[k] += e.cut_d;
      }
      ++i;
    }

    // Maximal solid spans: add > 0 AND cut == 0 (nonzero rule).
    spans.clear();
    std::size_t k = 0;
    while (k < m) {
      if (add_cnt[k] > 0 && cut_cnt[k] == 0) {
        std::size_t j = k;
        while (j < m && add_cnt[j] > 0 && cut_cnt[j] == 0) ++j;
        spans.emplace_back(xs[k], xs[j]);
        k = j;
      } else {
        ++k;
      }
    }

    // Reconcile open primitives with the new spans (spans are sorted by xl):
    //   span still present  -> keep open (extend);
    //   open no longer solid -> close, emit [xl, y_open, xr, y];
    //   new span            -> open at y.
    std::map<std::pair<Coord1D, Coord1D>, Coord1D> next;
    for (const auto& kv : open) {
      if (std::binary_search(spans.begin(), spans.end(), kv.first)) {
        next.insert(kv);  // continue with the same y_open
      } else {
        out.push_back(
            Rect{kv.first.first, kv.second, kv.first.second, y});
      }
    }
    for (const auto& s : spans) {
      if (next.find(s) == next.end()) next.emplace(s, y);
    }
    open.swap(next);
  }
  // At the final y the geometry is empty, so all primitives have closed.
  return out;
}

}  // namespace

std::vector<Rect> sweep_manhattan(const std::vector<TaggedRect>& input) {
  if (input.empty()) return {};

  // Per-axis selection: extend along the dominant edge direction so tall layers
  // stay tall and wide layers stay wide.
  long long hlen = 0, vlen = 0;
  for (const auto& t : input) {
    hlen += (t.rect.x1 > t.rect.x0) ? (t.rect.x1 - t.rect.x0)
                                    : (t.rect.x0 - t.rect.x1);
    vlen += (t.rect.y1 > t.rect.y0) ? (t.rect.y1 - t.rect.y0)
                                    : (t.rect.y0 - t.rect.y1);
  }

  if (vlen >= hlen) return horizontal_sweep(input);  // extend in y

  // Extend in x: transpose (swap x<->y), sweep, transpose the result back.
  std::vector<TaggedRect> tr;
  tr.reserve(input.size());
  for (const auto& t : input) {
    tr.push_back({Rect{t.rect.y0, t.rect.x0, t.rect.y1, t.rect.x1}, t.tag});
  }
  std::vector<Rect> r = horizontal_sweep(tr);
  for (auto& o : r) o = Rect{o.y0, o.x0, o.y1, o.x1};
  return r;
}

namespace {

// A non-horizontal edge as a function of y: x(y) = xlo + (dxn/dyn)*(y-ylo),
// any slope (dxn==0 = vertical). x_at snaps the (rational) position to the DBU
// grid, deterministically, so tiles sharing an edge stay watertight. `wind` is
// the directed crossing contribution (+/-1), normalized so every ring acts CCW.
// `group` selects which operand's winding the edge contributes to (0 or 1).
// `line` identifies the infinite line the edge lies on (collinear edges share
// it), so a span bounded by the continuation of the same two lines extends.
struct OEdge {
  Coord1D ylo, yhi, xlo;
  long long dxn, dyn;  // slope = dxn/dyn, dyn > 0
  int group;
  int wind;
  int line = 0;
  double x_d(double y) const {
    return xlo + static_cast<double>(dxn) * (y - ylo) / dyn;
  }
  // Round half UP (floor(v + 1/2)), on the exact rational: the result depends
  // only on the line, not on which collinear edge (base point) evaluates it.
  Coord1D x_at(Coord1D y) const {
    const __int128 num = static_cast<__int128>(dxn) * (y - ylo);
    const __int128 a = 2 * num + dyn, b = 2 * static_cast<__int128>(dyn);
    __int128 q = a / b;
    if ((a % b != 0) && (a < 0)) --q;  // floor division
    return xlo + static_cast<Coord1D>(q);
  }
};

// Append a ring's non-horizontal edges (as `group`) and its vertex Ys. Orient by
// signed area so every ring contributes winding as if CCW.
void add_ring(const std::vector<Coord2D>& v, int group,
              std::vector<OEdge>& edges, std::vector<Coord1D>& ys) {
  const std::size_t n = v.size();
  if (n < 3) return;
  Area sa = 0;
  for (std::size_t i = 0; i < n; ++i) {
    sa += static_cast<Area>(v[i].x) * v[(i + 1) % n].y -
          static_cast<Area>(v[(i + 1) % n].x) * v[i].y;
  }
  const int orient = (sa >= 0) ? 1 : -1;
  for (std::size_t i = 0; i < n; ++i) {
    const Coord2D a = v[i];
    const Coord2D b = v[(i + 1) % n];
    ys.push_back(a.y);
    if (a.y == b.y) continue;  // horizontal: not an active edge
    const Coord1D ylo = std::min(a.y, b.y);
    const Coord1D yhi = std::max(a.y, b.y);
    const Coord1D xlo = (a.y < b.y) ? a.x : b.x;
    const Coord1D xhi = (a.y < b.y) ? b.x : a.x;
    const int wind = ((a.y < b.y) ? 1 : -1) * orient;
    edges.push_back({ylo, yhi, xlo, static_cast<long long>(xhi) - xlo,
                     static_cast<long long>(yhi) - ylo, group, wind});
  }
}

// Give collinear edges a shared line id: the line is dy*x - dx*y = c with
// (dx, dy) the slope reduced to lowest terms.
void assign_lines(std::vector<OEdge>& edges) {
  struct Key {
    long long dx, dy;
    __int128 c;
    bool operator<(const Key& o) const {
      if (dx != o.dx) return dx < o.dx;
      if (dy != o.dy) return dy < o.dy;
      return c < o.c;
    }
  };
  std::map<Key, int> ids;
  for (OEdge& e : edges) {
    const long long g = std::gcd(e.dxn < 0 ? -e.dxn : e.dxn, e.dyn);
    const long long dx = e.dxn / g, dy = e.dyn / g;
    const Key k{dx, dy,
                static_cast<__int128>(dy) * e.xlo -
                    static_cast<__int128>(dx) * e.ylo};
    e.line = ids.emplace(k, static_cast<int>(ids.size())).first->second;
  }
}

// The shared horizontal trapezoidal sweep over grouped edges. `solid(w0, w1)`
// decides whether the span between two consecutive active edges is solid, given
// the running winding of each operand. Compose and all booleans differ only in
// this predicate.
//
// Slabs break at every vertex Y, and additionally wherever two active edges
// CROSS inside a slab (snapped to the DBU grid), so the left-to-right edge order
// is constant within each slab processed. The active set is maintained
// incrementally (edges enter at ylo, leave at yhi), so the cost follows the
// sweep-front width, not the total edge count.
std::vector<Tile> sweep_grouped(std::vector<OEdge>& edges,
                                std::vector<Coord1D>& ys,
                                const std::function<bool(int, int)>& solid) {
  std::vector<Tile> out;
  if (edges.empty()) return out;
  std::sort(ys.begin(), ys.end());
  ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
  if (ys.size() < 2) return out;
  assign_lines(edges);

  // One representative edge per line, to evaluate the line at any y.
  std::vector<int> rep;
  for (int e = 0; e < static_cast<int>(edges.size()); ++e) {
    if (edges[e].line >= static_cast<int>(rep.size()))
      rep.resize(edges[e].line + 1, -1);
    if (rep[edges[e].line] < 0) rep[edges[e].line] = e;
  }

  auto emit = [&](int lline, int rline, Coord1D y0, Coord1D y1) {
    if (y0 >= y1) return;
    const OEdge& L = edges[rep[lline]];
    const OEdge& R = edges[rep[rline]];
    const Coord1D xbl = L.x_at(y0), xtl = L.x_at(y1);
    // Two edges that cross within half a grid step of a slab end can land a
    // grid unit apart the wrong way round after snapping; pinch, never invert.
    const Coord1D xbr = std::max(R.x_at(y0), xbl);
    const Coord1D xtr = std::max(R.x_at(y1), xtl);
    if (xbr == xbl && xtr == xtl) return;  // zero area
    out.push_back(Tile{std::min(xbl, xtl), y0, std::max(xbr, xtr), y1,
                       xtl - xbl, xtr - xbr});
  };

  std::vector<int> by_ylo(edges.size());
  for (int e = 0; e < static_cast<int>(edges.size()); ++e) by_ylo[e] = e;
  std::sort(by_ylo.begin(), by_ylo.end(),
            [&](int p, int q) { return edges[p].ylo < edges[q].ylo; });
  std::size_t next_edge = 0;

  std::map<std::pair<int, int>, Coord1D> open;  // (left line, right line) -> y
  std::vector<int> active;
  std::vector<std::pair<int, int>> spans;
  const double kEps = 1e-9;

  // Y (on the grid, strictly inside the slab) where edges p and q cross, if any.
  auto crossing = [&](int p, int q, Coord1D ya, Coord1D yb, Coord1D& ysplit) {
    const OEdge& P = edges[p];
    const OEdge& Q = edges[q];
    const double sp = static_cast<double>(P.dxn) / P.dyn;
    const double sq = static_cast<double>(Q.dxn) / Q.dyn;
    if (sp == sq) return false;
    const double yc =
        ((Q.xlo - sq * Q.ylo) - (P.xlo - sp * P.ylo)) / (sp - sq);
    const long long r = std::llround(yc);
    if (r <= ya || r >= yb) return false;
    ysplit = static_cast<Coord1D>(r);
    return true;
  };

  // Process one slab whose active set is fixed; splits itself at crossings.
  const std::function<void(Coord1D, Coord1D)> slab = [&](Coord1D ya,
                                                         Coord1D yb) {
    const double ym = (static_cast<double>(ya) + yb) / 2;
    std::sort(active.begin(), active.end(), [&](int p, int q) {
      const double pm = edges[p].x_d(ym), qm = edges[q].x_d(ym);
      if (pm != qm) return pm < qm;
      const double pa = edges[p].x_d(ya), qa = edges[q].x_d(ya);
      if (pa != qa) return pa < qa;
      return p < q;
    });

    // The order at the slab middle must also hold at both ends; where it does
    // not, two edges cross inside the slab -> split there and redo each part.
    auto inverted = [&](int p, int q) {
      return edges[p].x_d(ya) > edges[q].x_d(ya) + kEps ||
             edges[p].x_d(yb) > edges[q].x_d(yb) + kEps;
    };
    bool any = false;
    Coord1D ysplit = 0;
    for (std::size_t e = 0; e + 1 < active.size(); ++e) {
      if (!inverted(active[e], active[e + 1])) continue;
      any = true;
      if (crossing(active[e], active[e + 1], ya, yb, ysplit)) {
        slab(ya, ysplit);
        slab(ysplit, yb);
        return;
      }
    }
    if (any) {  // adjacent crossings all sit at a slab end; look further apart
      for (std::size_t e = 0; e < active.size(); ++e) {
        for (std::size_t f = e + 2; f < active.size(); ++f) {
          if (inverted(active[e], active[f]) &&
              crossing(active[e], active[f], ya, yb, ysplit)) {
            slab(ya, ysplit);
            slab(ysplit, yb);
            return;
          }
        }
      }
    }

    spans.clear();
    int w0 = 0, w1 = 0;
    for (std::size_t e = 0; e < active.size(); ++e) {
      if (edges[active[e]].group == 0)
        w0 += edges[active[e]].wind;
      else
        w1 += edges[active[e]].wind;
      if (solid(w0, w1) && e + 1 < active.size()) {
        const int l = edges[active[e]].line, r = edges[active[e + 1]].line;
        if (l != r) spans.emplace_back(l, r);  // coincident edges: zero width
      }
    }

    const std::set<std::pair<int, int>> span_set(spans.begin(), spans.end());
    std::map<std::pair<int, int>, Coord1D> next;
    for (const auto& kv : open) {
      if (span_set.count(kv.first))
        next.insert(kv);  // extend
      else
        emit(kv.first.first, kv.first.second, kv.second, ya);  // close
    }
    for (const auto& s : spans) {
      if (next.find(s) == next.end()) next.emplace(s, ya);  // open
    }
    open.swap(next);
  };

  for (std::size_t k = 0; k + 1 < ys.size(); ++k) {
    const Coord1D ya = ys[k];
    const Coord1D yb = ys[k + 1];
    active.erase(std::remove_if(active.begin(), active.end(),
                                [&](int e) { return edges[e].yhi <= ya; }),
                 active.end());
    while (next_edge < by_ylo.size() && edges[by_ylo[next_edge]].ylo <= ya)
      active.push_back(by_ylo[next_edge++]);
    slab(ya, yb);
  }
  const Coord1D ytop = ys.back();
  for (const auto& kv : open)
    emit(kv.first.first, kv.first.second, kv.second, ytop);
  return out;
}

}  // namespace

std::vector<Tile> sweep_polygons(const std::vector<TaggedRing>& rings) {
  std::vector<OEdge> edges;
  std::vector<Coord1D> ys;
  for (const auto& r : rings) {
    add_ring(r.verts, (r.tag == Tag::Add) ? 0 : 1, edges, ys);
  }
  // Compose: solid where inside the Add union and outside the Cut union.
  return sweep_grouped(edges, ys,
                       [](int w0, int w1) { return w0 != 0 && w1 == 0; });
}

std::vector<Coord2D> tile_ring(const Tile& t) {
  const Coord1D xbl = (t.cut_left >= 0) ? t.x0 : t.x0 - t.cut_left;
  const Coord1D xtl = (t.cut_left >= 0) ? t.x0 + t.cut_left : t.x0;
  const Coord1D xbr = (t.cut_right >= 0) ? t.x1 - t.cut_right : t.x1;
  const Coord1D xtr = (t.cut_right >= 0) ? t.x1 : t.x1 + t.cut_right;
  return {{xbl, t.y0}, {xbr, t.y0}, {xtr, t.y1}, {xtl, t.y1}};  // CCW
}

std::vector<Tile> boolean(const std::vector<Tile>& a, const std::vector<Tile>& b,
                          BoolOp op) {
  std::vector<OEdge> edges;
  std::vector<Coord1D> ys;
  for (const Tile& t : a) add_ring(tile_ring(t), 0, edges, ys);
  for (const Tile& t : b) add_ring(tile_ring(t), 1, edges, ys);

  std::function<bool(int, int)> solid;
  switch (op) {
    case BoolOp::Or:
      solid = [](int w0, int w1) { return w0 != 0 || w1 != 0; };
      break;
    case BoolOp::And:
      solid = [](int w0, int w1) { return w0 != 0 && w1 != 0; };
      break;
    case BoolOp::Sub:
      solid = [](int w0, int w1) { return w0 != 0 && w1 == 0; };
      break;
    case BoolOp::Xor:
      solid = [](int w0, int w1) { return (w0 != 0) != (w1 != 0); };
      break;
  }
  return sweep_grouped(edges, ys, solid);
}

namespace {

// Convex hull (CCW, no collinear points) by Andrew's monotone chain.
std::vector<Coord2D> convex_hull(std::vector<Coord2D> p) {
  std::sort(p.begin(), p.end(), [](const Coord2D& a, const Coord2D& b) {
    return a.x != b.x ? a.x < b.x : a.y < b.y;
  });
  p.erase(std::unique(p.begin(), p.end()), p.end());
  if (p.size() < 3) return p;
  auto cross = [](const Coord2D& o, const Coord2D& a, const Coord2D& b) {
    return (static_cast<long long>(a.x) - o.x) * (static_cast<long long>(b.y) - o.y) -
           (static_cast<long long>(a.y) - o.y) * (static_cast<long long>(b.x) - o.x);
  };
  std::vector<Coord2D> h(2 * p.size());
  std::size_t k = 0;
  for (std::size_t i = 0; i < p.size(); ++i) {
    while (k >= 2 && cross(h[k - 2], h[k - 1], p[i]) <= 0) --k;
    h[k++] = p[i];
  }
  for (std::size_t i = p.size() - 1, t = k + 1; i > 0; --i) {
    while (k >= t && cross(h[k - 2], h[k - 1], p[i - 1]) <= 0) --k;
    h[k++] = p[i - 1];
  }
  h.resize(k - 1);
  return h;
}

// Dilation by the square [-d, d]^2 (d > 0). A tile is convex, so its dilation
// is the hull of its four copies shifted to the square's corners: a rectangle
// grows to the bigger rectangle, an angled side moves out and keeps its slope.
std::vector<Tile> grow(const std::vector<Tile>& tiles, Coord1D d) {
  std::vector<TaggedRing> rings;
  rings.reserve(tiles.size());
  for (const Tile& t : tiles) {
    std::vector<Coord2D> pts;
    pts.reserve(16);
    for (const Coord2D& v : tile_ring(t)) {
      pts.push_back({v.x - d, v.y - d});
      pts.push_back({v.x + d, v.y - d});
      pts.push_back({v.x + d, v.y + d});
      pts.push_back({v.x - d, v.y + d});
    }
    rings.push_back({convex_hull(std::move(pts)), Tag::Add});
  }
  return sweep_polygons(rings);  // Add-only -> union of the grown tiles
}

}  // namespace

std::vector<Tile> inflate(const std::vector<Tile>& tiles, Coord1D d) {
  if (tiles.empty() || d == 0) return tiles;
  if (d > 0) return grow(tiles, d);

  // Erosion: what survives is the region minus the dilation of its complement.
  // Only the complement within |d| of the region matters, so a frame around the
  // bbox stands in for the whole outside.
  const Coord1D e = -d;
  Tile frame = tiles.front();
  frame.cut_left = frame.cut_right = 0;
  for (const Tile& t : tiles) {
    frame.x0 = std::min(frame.x0, t.x0);
    frame.y0 = std::min(frame.y0, t.y0);
    frame.x1 = std::max(frame.x1, t.x1);
    frame.y1 = std::max(frame.y1, t.y1);
  }
  frame.x0 -= e + 1;
  frame.y0 -= e + 1;
  frame.x1 += e + 1;
  frame.y1 += e + 1;
  const std::vector<Tile> outside = boolean({frame}, tiles, BoolOp::Sub);
  return boolean(tiles, grow(outside, e), BoolOp::Sub);
}

}  // namespace fastdrc
