#include "fastdrc/sweep.hpp"

#include <algorithm>
#include <cstddef>
#include <functional>
#include <map>
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
struct OEdge {
  Coord1D ylo, yhi, xlo;
  long long dxn, dyn;  // slope = dxn/dyn, dyn > 0
  int group;
  int wind;
  double x_d(Coord1D y) const {
    return xlo + static_cast<double>(dxn) * (y - ylo) / dyn;
  }
  Coord1D x_at(Coord1D y) const {
    const long long num = dxn * static_cast<long long>(y - ylo);
    const long long q = num >= 0 ? (num + dyn / 2) / dyn
                                 : -((-num + dyn / 2) / dyn);
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
    edges.push_back({ylo, yhi, xlo, static_cast<long long>(xhi - xlo),
                     static_cast<long long>(yhi - ylo), group, wind});
  }
}

// The shared horizontal trapezoidal sweep over grouped edges. `solid(w0, w1)`
// decides whether the span between two consecutive active edges is solid, given
// the running winding of each operand. Compose and all booleans differ only in
// this predicate.
std::vector<Tile> sweep_grouped(std::vector<OEdge>& edges,
                                std::vector<Coord1D>& ys,
                                const std::function<bool(int, int)>& solid) {
  std::vector<Tile> out;
  if (edges.empty()) return out;
  std::sort(ys.begin(), ys.end());
  ys.erase(std::unique(ys.begin(), ys.end()), ys.end());
  if (ys.size() < 2) return out;

  auto emit = [&](int lid, int rid, Coord1D y0, Coord1D y1) {
    const OEdge& L = edges[lid];
    const OEdge& R = edges[rid];
    const Coord1D xbl = L.x_at(y0), xtl = L.x_at(y1);
    const Coord1D xbr = R.x_at(y0), xtr = R.x_at(y1);
    const Coord1D x0 = std::min(xbl, xtl);
    const Coord1D x1 = std::max(xbr, xtr);
    if (x0 >= x1 || y0 >= y1) return;  // degenerate (incl. unsupported crossing)
    out.push_back(Tile{x0, y0, x1, y1, xtl - xbl, xtr - xbr});
  };

  std::map<std::pair<int, int>, Coord1D> open;
  std::vector<int> active;
  std::vector<std::pair<int, int>> spans;

  for (std::size_t k = 0; k + 1 < ys.size(); ++k) {
    const Coord1D ya = ys[k];
    const Coord1D yb = ys[k + 1];

    active.clear();
    for (int e = 0; e < static_cast<int>(edges.size()); ++e) {
      if (edges[e].ylo <= ya && edges[e].yhi >= yb) active.push_back(e);
    }
    std::sort(active.begin(), active.end(), [&](int p, int q) {
      const double pa = edges[p].x_d(ya), qa = edges[q].x_d(ya);
      if (pa != qa) return pa < qa;
      return edges[p].x_d(yb) < edges[q].x_d(yb);
    });

    spans.clear();
    int w0 = 0, w1 = 0;
    for (std::size_t e = 0; e < active.size(); ++e) {
      if (edges[active[e]].group == 0)
        w0 += edges[active[e]].wind;
      else
        w1 += edges[active[e]].wind;
      if (solid(w0, w1) && e + 1 < active.size())
        spans.emplace_back(active[e], active[e + 1]);
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

std::vector<Tile> inflate(const std::vector<Tile>& tiles, Coord1D d) {
  std::vector<TaggedRing> rings;
  rings.reserve(tiles.size());
  for (const Tile& t : tiles) {
    const Coord1D x0 = t.x0 - d, y0 = t.y0 - d, x1 = t.x1 + d, y1 = t.y1 + d;
    if (x0 >= x1 || y0 >= y1) continue;  // shrank away
    rings.push_back({{{x0, y0}, {x1, y0}, {x1, y1}, {x0, y1}}, Tag::Add});
  }
  return sweep_polygons(rings);  // Add-only -> union of the grown boxes
}

}  // namespace fastdrc
