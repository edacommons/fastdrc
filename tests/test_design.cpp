// End-to-end: build a Design (geometry per layer + metadata) -> write_split() ->
// read back, and verify the saved geometry is the correct merged region.
// Separation under test: Region merges (no metadata), Design holds everything,
// write_split() is the external serializer. A `.tiles` is one pure layer; the
// id -> file + source mapping lives in the JSON index.

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

#include "fastdrc/design.hpp"
#include "fastdrc/geom_reader.hpp"

using namespace fastdrc;

static std::int64_t total_span_area(const geom::Reader::SpanView& s) {
  std::int64_t a = 0;
  for (std::size_t i = 0; i < s.count; ++i) {
    a += static_cast<std::int64_t>(s.x1[i] - s.x0[i]) * (s.y1[i] - s.y0[i]);
  }
  return a;
}

static std::string read_text(const std::string& path) {
  std::ifstream f(path, std::ios::binary);
  return std::string(std::istreambuf_iterator<char>(f), {});
}

int main() {
  namespace fs = std::filesystem;
  const std::string dir = "test_design_split";

  {
    Design d(1000);

    Region& m1 = d.add_layer(10, 0, "metal1");
    m1.add(Rect{0, 0, 10, 10});
    m1.add(Rect{5, 5, 15, 15});  // overlaps -> union area 175 (no double count)

    Region& via1 = d.add_layer(11, 0, "via1");
    via1.add(Rect{0, 0, 10, 10});
    via1.cut(Rect{3, 3, 7, 7});  // hole -> area 100 - 16 = 84

    Region& poly = d.add_layer(12, 0, "poly");
    // L-shape (rectilinear polygon), area 64.
    poly.add_polygon({{0, 0}, {10, 0}, {10, 4}, {4, 4}, {4, 10}, {0, 10}});

    Region& diff = d.add_layer(13, 0, "diff");
    // square with a polygon hole cut out -> donut, area 100 - 16 = 84.
    diff.add_polygon({{0, 0}, {10, 0}, {10, 10}, {0, 10}});
    diff.cut_polygon({{3, 3}, {7, 3}, {7, 7}, {3, 7}});

    write_split(d, dir);
  }

  // Each layer is its own pure single-layer FDCG file.
  geom::Reader m1((fs::path(dir) / "metal1.tiles").string());
  geom::Reader via1((fs::path(dir) / "via1.tiles").string());
  geom::Reader poly((fs::path(dir) / "poly.tiles").string());
  geom::Reader diff((fs::path(dir) / "diff.tiles").string());
  assert(m1.valid() && via1.valid() && poly.valid() && diff.valid());

  assert(total_span_area(m1.spans()) == 175);   // overlapping rects unioned
  assert(total_span_area(via1.spans()) == 84);  // rect donut
  assert(total_span_area(poly.spans()) == 64);  // L-shape polygon
  assert(total_span_area(diff.spans()) == 84);  // polygon with a polygon hole

  // Each file is PURE geometry: no source names/ids embedded.
  assert(m1.manifest().find("metal1") == std::string_view::npos);
  assert(m1.manifest().find("gds_layer") == std::string_view::npos);

  // The index maps ids -> files + source, and holds design-wide dbu/bbox.
  const std::string idx = read_text((fs::path(dir) / "index.json").string());
  assert(idx.find("\"format\":\"FDCG-index\"") != std::string::npos);
  assert(idx.find("\"dbu_per_micron\":1000") != std::string::npos);
  assert(idx.find("\"file\":\"metal1.tiles\"") != std::string::npos);
  assert(idx.find("\"name\":\"via1\"") != std::string::npos);
  assert(idx.find("\"gds_layer\":11") != std::string::npos);
  assert(idx.find("\"bbox\":[0,0,15,15]") != std::string::npos);

  fs::remove_all(dir);
  std::puts("all design tests passed");
  return 0;
}
