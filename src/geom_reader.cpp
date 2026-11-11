#include "fastdrc/geom_reader.hpp"

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cassert>
#include <cstring>

namespace fastdrc::geom {

Reader::Reader(const std::string& path) {
  const int fd = ::open(path.c_str(), O_RDONLY);
  if (fd < 0) return;

  struct stat st {};
  if (::fstat(fd, &st) != 0 ||
      static_cast<std::size_t>(st.st_size) < sizeof(Header) + sizeof(Footer)) {
    ::close(fd);
    return;
  }
  const std::size_t sz = static_cast<std::size_t>(st.st_size);

  void* p = ::mmap(nullptr, sz, PROT_READ, MAP_PRIVATE, fd, 0);
  ::close(fd);
  if (p == MAP_FAILED) return;

  const char* b = static_cast<const char*>(p);
  const auto* h = reinterpret_cast<const Header*>(b);
  const auto* f = reinterpret_cast<const Footer*>(b + sz - sizeof(Footer));
  bool ok = std::memcmp(h->magic, kMagic, 4) == 0 &&
            std::memcmp(f->end_magic, kEndMagic, 8) == 0 && f->file_size == sz;
  // The section table is file content, not a promise: every offset/count the
  // accessors follow must land inside the body (between header and footer).
  const std::uint64_t body_end = sz - sizeof(Footer);
  if (ok && f->span_count != 0) {
    const std::uint64_t per = 6 * sizeof(std::int32_t);
    ok = f->span_off >= sizeof(Header) && f->span_off <= body_end &&
         f->span_off % alignof(std::int32_t) == 0 &&
         f->span_count <= (body_end - f->span_off) / per;
  }
  if (ok && f->manifest_off != 0) {
    ok = f->manifest_off >= sizeof(Header) &&
         f->manifest_off <= body_end - sizeof(std::uint64_t);
    if (ok) {
      std::uint64_t len = 0;
      std::memcpy(&len, b + f->manifest_off, sizeof(len));
      ok = len <= body_end - f->manifest_off - sizeof(len);
    }
  }
  if (!ok) {
    ::munmap(p, sz);
    return;
  }

  base_ = b;
  size_ = sz;
}

Reader::~Reader() {
  if (base_) ::munmap(const_cast<char*>(base_), size_);
}

Reader::Reader(Reader&& other) noexcept
    : base_(other.base_), size_(other.size_) {
  other.base_ = nullptr;
  other.size_ = 0;
}

Reader& Reader::operator=(Reader&& other) noexcept {
  if (this != &other) {
    if (base_) ::munmap(const_cast<char*>(base_), size_);
    base_ = other.base_;
    size_ = other.size_;
    other.base_ = nullptr;
    other.size_ = 0;
  }
  return *this;
}

const Footer& Reader::footer() const {
  assert(valid());
  return *reinterpret_cast<const Footer*>(base_ + size_ - sizeof(Footer));
}

Reader::SpanView Reader::spans() const {
  const Footer& f = footer();
  SpanView v;
  v.count = f.span_count;
  if (v.count == 0) return v;
  const auto* x0 = reinterpret_cast<const std::int32_t*>(base_ + f.span_off);
  const std::size_t m = v.count;
  v.x0 = x0;
  v.y0 = x0 + m;
  v.x1 = x0 + 2 * m;
  v.y1 = x0 + 3 * m;
  v.cut_left = x0 + 4 * m;
  v.cut_right = x0 + 5 * m;
  return v;
}

const std::int32_t* Reader::bbox() const { return footer().bbox; }

std::int32_t Reader::dbu_per_micron() const {
  return footer().dbu_per_micron;
}

std::uint64_t Reader::content_hash() const {
  return reinterpret_cast<const Header*>(base_)->content_hash;
}

std::string_view Reader::manifest() const {
  const std::uint64_t off = footer().manifest_off;
  if (off == 0) return {};
  std::uint64_t len = 0;
  std::memcpy(&len, base_ + off, sizeof(len));
  return std::string_view(base_ + off + sizeof(len), len);
}

}  // namespace fastdrc::geom
