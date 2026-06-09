#pragma once

#include <cstdint>
#include <deque>
#include <string>

#include "fastdrc/region.hpp"

namespace fastdrc {

/// The thing that holds everything: composed geometry per layer plus the
/// higher-level metadata (database units, GDS layer/datatype, name) needed to
/// describe and serialize it. The geometry merge lives entirely in Region; this
/// object just owns the layers and their metadata. Serialization is external
/// (see write() below) — Design knows nothing about the file format.
class Design {
 public:
  struct Layer {
    std::uint32_t gds_layer = 0;
    std::uint32_t gds_datatype = 0;
    std::string name;
    Region region;
  };

  explicit Design(std::int32_t dbu_per_micron = 1000) : dbu_(dbu_per_micron) {}

  /// Add a layer and return its geometry merger. The reference stays valid as
  /// more layers are added (deque: stable element addresses, and Region holds
  /// self-referential iterators).
  Region& add_layer(std::uint32_t gds_layer, std::uint32_t gds_datatype,
                    std::string name = {});

  [[nodiscard]] std::int32_t dbu() const { return dbu_; }
  [[nodiscard]] const std::deque<Layer>& layers() const { return layers_; }

 private:
  std::deque<Layer> layers_;
  std::int32_t dbu_;
};

/// Serialize a Design: one pure single-layer FDCG file per layer in `dir`
/// (named after the layer, or layer<id>), plus a small JSON index that ties
/// them into one design and holds the id -> file + source (GDS layer/datatype,
/// name) mapping and design-wide dbu/bbox. A `.tiles` is one layer of pure
/// geometry; the index is where all naming/source/derivation lives. This is the
/// single serializer — it enables independent per-layer production, incremental
/// recompute, explicit per-file DAG dependencies, and boolean ops between layer
/// files. `dir` is created if needed. External to Design: the holder carries no
/// knowledge of the byte format.
void write_split(const Design& design, const std::string& dir,
                 const std::string& index_name = "index.json");

}  // namespace fastdrc
