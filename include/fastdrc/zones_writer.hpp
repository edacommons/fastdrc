#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "fastdrc/zones_format.hpp"

namespace fastdrc::zones {

/// Writes a v0 compiled-zones file. A checker/filter accumulates zones per set
/// (one set per rule/stage), then streams the binary (header -> per-set arrays
/// -> manifest -> set directory -> footer). Same write-once, no-seek-back shape
/// as the geometry Writer.
class Writer {
 public:
  explicit Writer(std::int32_t dbu_per_micron = 1000) : dbu_(dbu_per_micron) {}

  /// Start a new zone set (one rule/stage's output). The strings are recorded
  /// in the manifest, keyed by the set's index.
  void begin_set(std::string rule, std::string name = {},
                 std::string severity = {});

  /// Add a zone to the current set: an axis-aligned box, an optional measured
  /// value (e.g. the spacing found, in DBU), and a kind (interest/violation).
  void zone(std::int32_t x0, std::int32_t y0, std::int32_t x1, std::int32_t y1,
            std::int64_t value = 0, std::uint8_t kind = kViolation);

  /// Serialize to `path`.
  void write(const std::string& path) const;

 private:
  struct Set {
    std::string rule, name, severity;
    std::vector<std::int32_t> x0, y0, x1, y1;
    std::vector<std::int64_t> value;
    std::vector<std::uint8_t> kind;
  };

  std::vector<Set> sets_;
  std::int32_t dbu_;
};

}  // namespace fastdrc::zones
