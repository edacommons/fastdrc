#include "fastdrc/layer.hpp"

#include <algorithm>

namespace fastdrc {

void Layer::add_rect(Coord1D x0, Coord1D y0, Coord1D x1, Coord1D y1) {
  const Coord1D xmin = std::min(x0, x1);
  const Coord1D xmax = std::max(x0, x1);
  const Coord1D ymin = std::min(y0, y1);
  const Coord1D ymax = std::max(y0, y1);
  if (xmin == xmax || ymin == ymax) return;  // degenerate (zero area)

  // A rectangle is a fixed, simple shape: intern its four sides directly
  // rather than routing through add_polygon, whose general path will grow logic
  // for holes / multiple contours that a rect never needs. Corners are
  // counter-clockwise.
  const Coord2D ll{xmin, ymin};
  const Coord2D lr{xmax, ymin};
  const Coord2D ur{xmax, ymax};
  const Coord2D ul{xmin, ymax};
  pool_.edge(ll, lr);
  pool_.edge(lr, ur);
  pool_.edge(ur, ul);
  pool_.edge(ul, ll);
}

void Layer::add_rect(Coord2D p0, Coord2D p1) {
  add_rect(p0.x, p0.y, p1.x, p1.y);
}

void Layer::add_polygon(const std::vector<Coord2D>& points) {
  for (const Coord2D& p : points) {
    pool_.point(p);
  }

  const std::size_t n = points.size();
  if (n >= 2) {
    for (std::size_t i = 0; i < n; ++i) {
      const Coord2D& a = points[i];
      const Coord2D& b = points[(i + 1) % n];  // closing edge wraps around
      if (!(a == b)) {                          // skip degenerate edges
        pool_.edge(a, b);                       // interns edge + line + points
      }
    }
  }
}

}  // namespace fastdrc
