#include "fastdrc/design.hpp"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <limits>

#include "fastdrc/geom_writer.hpp"

namespace fastdrc {

namespace {

std::string json_escape(const std::string& s) {
  std::string out;
  out.reserve(s.size() + 2);
  for (char c : s) {
    if (c == '"' || c == '\\') out.push_back('\\');
    out.push_back(c);
  }
  return out;
}

// File-safe name for a layer's split file: the layer name with non-portable
// characters replaced by '_', or layer<id> if it has no name.
std::string layer_filename(const Design::Layer& L, std::size_t id) {
  std::string base;
  for (char c : L.name) {
    const auto u = static_cast<unsigned char>(c);
    base.push_back((std::isalnum(u) || c == '_' || c == '-') ? c : '_');
  }
  if (base.empty()) base = "layer" + std::to_string(id);
  return base + ".tiles";
}

}  // namespace

Region& Design::add_layer(std::uint32_t gds_layer, std::uint32_t gds_datatype,
                          std::string name) {
  Layer& L = layers_.emplace_back();  // Region constructed in place (no move)
  L.gds_layer = gds_layer;
  L.gds_datatype = gds_datatype;
  L.name = std::move(name);
  return L.region;
}

void write_split(const Design& design, const std::string& dir,
                 const std::string& index_name) {
  namespace fs = std::filesystem;
  fs::create_directories(dir);

  // Union bbox across all layers (the design-wide extent for the index).
  std::int32_t bx0 = std::numeric_limits<std::int32_t>::max();
  std::int32_t by0 = std::numeric_limits<std::int32_t>::max();
  std::int32_t bx1 = std::numeric_limits<std::int32_t>::min();
  std::int32_t by1 = std::numeric_limits<std::int32_t>::min();
  for (const Design::Layer& L : design.layers()) {
    for (const Tile& t : L.region.tiles()) {
      bx0 = std::min(bx0, t.x0);
      by0 = std::min(by0, t.y0);
      bx1 = std::max(bx1, t.x1);
      by1 = std::max(by1, t.y1);
    }
  }
  if (bx0 > bx1) bx0 = by0 = bx1 = by1 = 0;  // empty design

  std::string idx =
      "{\"format\":\"FDCG-index\",\"version\":0,\"generator\":\"fastdrc\"";
  idx += ",\"dbu_per_micron\":" + std::to_string(design.dbu());
  idx += ",\"bbox\":[" + std::to_string(bx0) + "," + std::to_string(by0) + "," +
         std::to_string(bx1) + "," + std::to_string(by1) + "]";
  idx += ",\"layers\":[";

  std::size_t id = 0;
  for (const Design::Layer& L : design.layers()) {
    const std::string fname = layer_filename(L, id);

    // Each layer file is a pure single-layer FDCG (no source metadata — that
    // lives in the index below, keyed by id/file).
    geom::Writer w(design.dbu());
    for (const Tile& t : L.region.tiles()) {
      w.tile(t.x0, t.y0, t.x1, t.y1, t.cut_left, t.cut_right);
    }
    w.write((fs::path(dir) / fname).string());

    if (id) idx += ",";
    idx += "{\"id\":" + std::to_string(id);
    idx += ",\"file\":\"" + json_escape(fname) + "\"";
    idx += ",\"gds_layer\":" + std::to_string(L.gds_layer);
    idx += ",\"gds_datatype\":" + std::to_string(L.gds_datatype);
    idx += ",\"name\":\"" + json_escape(L.name) + "\"}";
    ++id;
  }
  idx += "]}";

  std::ofstream f((fs::path(dir) / index_name).string(),
                  std::ios::binary | std::ios::trunc);
  f.write(idx.data(), static_cast<std::streamsize>(idx.size()));
}

}  // namespace fastdrc
