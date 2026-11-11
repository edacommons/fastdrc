#include "fastdrc/layer_ops.hpp"

#include <cstddef>
#include <vector>

#include "fastdrc/geom_reader.hpp"
#include "fastdrc/geom_writer.hpp"
#include "fastdrc/tile.hpp"

namespace fastdrc {

namespace {

std::vector<Tile> read_tiles(const geom::Reader& r) {
  const auto s = r.spans();
  std::vector<Tile> tiles;
  tiles.reserve(s.count);
  for (std::size_t i = 0; i < s.count; ++i) {
    tiles.push_back(
        {s.x0[i], s.y0[i], s.x1[i], s.y1[i], s.cut_left[i], s.cut_right[i]});
  }
  return tiles;
}

}  // namespace

long boolean_files(const std::string& a_path, const std::string& b_path,
                   BoolOp op, const std::string& out_path) {
  geom::Reader ra(a_path), rb(b_path);
  if (!ra.valid() || !rb.valid()) return -1;

  const std::vector<Tile> result = boolean(read_tiles(ra), read_tiles(rb), op);

  geom::Writer w(ra.dbu_per_micron());
  for (const Tile& t : result) {
    w.tile(t.x0, t.y0, t.x1, t.y1, t.cut_left, t.cut_right);
  }
  if (!w.write(out_path)) return -2;
  return static_cast<long>(result.size());
}

long inflate_file(const std::string& in_path, Coord1D d,
                  const std::string& out_path) {
  geom::Reader r(in_path);
  if (!r.valid()) return -1;

  const std::vector<Tile> result = inflate(read_tiles(r), d);

  geom::Writer w(r.dbu_per_micron());
  for (const Tile& t : result) {
    w.tile(t.x0, t.y0, t.x1, t.y1, t.cut_left, t.cut_right);
  }
  if (!w.write(out_path)) return -2;
  return static_cast<long>(result.size());
}

}  // namespace fastdrc
