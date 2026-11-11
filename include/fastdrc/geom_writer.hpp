#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "fastdrc/geom_format.hpp"

namespace fastdrc::geom {

/// Writes a v0 compiled-geometry file — ONE layer of pure geometry. Accumulate
/// primitives, then stream the binary (header -> span array -> manifest ->
/// footer). v0 writes the primitives (spans) only; edges / stripe are
/// left absent. The file carries no source/semantic metadata (no GDS
/// layer/datatype, no name) — layer identity / names / derivation live in an
/// external index.
class Writer {
 public:
  explicit Writer(std::int32_t dbu_per_micron = 1000)
      : dbu_(dbu_per_micron) {}

  /// Add a filled rectangle (no cuts).
  void rect(std::int32_t x0, std::int32_t y0, std::int32_t x1, std::int32_t y1);

  /// Add a tile: bbox + signed cut extents (see tile.hpp / geom_format.hpp).
  void tile(std::int32_t x0, std::int32_t y0, std::int32_t x1, std::int32_t y1,
            std::int32_t cut_left, std::int32_t cut_right);

  /// Serialize to `path`. False if the file could not be created or fully
  /// written (missing directory, no permission, disk full).
  [[nodiscard]] bool write(const std::string& path) const;

 private:
  std::vector<std::int32_t> x0_, y0_, x1_, y1_, cl_, cr_;
  std::int32_t dbu_;
};

}  // namespace fastdrc::geom
