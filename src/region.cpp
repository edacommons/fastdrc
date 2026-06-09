#include "fastdrc/region.hpp"

namespace fastdrc {

const std::vector<Tile>& Region::tiles() const {
  if (dirty_) {
    tiles_ = sweep_polygons(rings_);
    dirty_ = false;
  }
  return tiles_;
}

Area Region::area2() const {
  Area a = 0;
  for (const Tile& t : tiles()) a += t.area2();
  return a;
}

}  // namespace fastdrc
