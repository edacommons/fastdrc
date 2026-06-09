#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

#include "fastdrc/zones_format.hpp"

namespace fastdrc::zones {

/// Memory-maps a v0 compiled-zones file and exposes its sets zero-copy. A
/// downstream filter mmaps the geometry AND a zones file, then refines only
/// within the zones. All returned pointers/views point into the mmap and are
/// valid for the Reader's lifetime. Read-only; pages are shared across procs.
class Reader {
 public:
  explicit Reader(const std::string& path);
  ~Reader();

  Reader(const Reader&) = delete;
  Reader& operator=(const Reader&) = delete;
  Reader(Reader&& other) noexcept;
  Reader& operator=(Reader&& other) noexcept;

  /// True if the file mapped and validated (magic, end sentinel, size).
  [[nodiscard]] bool valid() const { return base_ != nullptr; }

  [[nodiscard]] std::size_t set_count() const;
  [[nodiscard]] const ZoneSetDesc& set(std::size_t i) const;

  /// Zero-copy SoA view of a set's zones.
  struct ZoneView {
    std::size_t count = 0;
    const std::int32_t* x0 = nullptr;
    const std::int32_t* y0 = nullptr;
    const std::int32_t* x1 = nullptr;
    const std::int32_t* y1 = nullptr;
    const std::int64_t* value = nullptr;
    const std::uint8_t* kind = nullptr;
  };
  [[nodiscard]] ZoneView zones(std::size_t set_index) const;

  [[nodiscard]] const std::int32_t* bbox() const;  // [x0, y0, x1, y1]
  [[nodiscard]] std::int32_t dbu_per_micron() const;
  [[nodiscard]] std::string_view manifest() const;  // raw JSON; empty if none

 private:
  [[nodiscard]] const Footer& footer() const;

  const char* base_ = nullptr;
  std::size_t size_ = 0;
};

}  // namespace fastdrc::zones
