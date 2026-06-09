#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

#include "fastdrc/geom_format.hpp"

namespace fastdrc::geom {

/// Memory-maps a v0 compiled-geometry file and exposes its single layer of
/// geometry zero-copy. All returned pointers/views point into the mmap and are
/// valid for the Reader's lifetime. Read-only; the OS shares the pages across
/// processes.
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

  /// Zero-copy SoA view of the layer's primitives.
  struct SpanView {
    std::size_t count = 0;
    const std::int32_t* x0 = nullptr;
    const std::int32_t* y0 = nullptr;
    const std::int32_t* x1 = nullptr;
    const std::int32_t* y1 = nullptr;
    const std::int32_t* cut_left = nullptr;
    const std::int32_t* cut_right = nullptr;
  };
  [[nodiscard]] SpanView spans() const;

  [[nodiscard]] const std::int32_t* bbox() const;  // [x0, y0, x1, y1]
  [[nodiscard]] std::int32_t dbu_per_micron() const;
  [[nodiscard]] std::string_view manifest() const;  // raw JSON; empty if none

  /// Content fingerprint from the header (FNV-1a-64). A 16-byte read; two files
  /// with identical geometry share it. 0 if unset.
  [[nodiscard]] std::uint64_t content_hash() const;

 private:
  [[nodiscard]] const Footer& footer() const;

  const char* base_ = nullptr;
  std::size_t size_ = 0;
};

}  // namespace fastdrc::geom
