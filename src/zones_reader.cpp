#include "fastdrc/zones_reader.hpp"

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cassert>
#include <cstring>

namespace fastdrc::zones {

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
  if (std::memcmp(h->magic, kMagic, 4) != 0 ||
      std::memcmp(f->end_magic, kEndMagic, 8) != 0 || f->file_size != sz) {
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

std::size_t Reader::set_count() const { return footer().set_count; }

const ZoneSetDesc& Reader::set(std::size_t i) const {
  assert(i < set_count());
  const auto* dir =
      reinterpret_cast<const ZoneSetDesc*>(base_ + footer().set_dir_off);
  return dir[i];
}

Reader::ZoneView Reader::zones(std::size_t set_index) const {
  const ZoneSetDesc& d = set(set_index);
  ZoneView v;
  v.count = d.count;
  if (v.count == 0) return v;
  const std::size_t m = v.count;
  const auto* x0 = reinterpret_cast<const std::int32_t*>(base_ + d.box_off);
  v.x0 = x0;
  v.y0 = x0 + m;
  v.x1 = x0 + 2 * m;
  v.y1 = x0 + 3 * m;
  v.value = reinterpret_cast<const std::int64_t*>(base_ + d.value_off);
  v.kind = reinterpret_cast<const std::uint8_t*>(base_ + d.kind_off);
  return v;
}

const std::int32_t* Reader::bbox() const { return footer().bbox; }

std::int32_t Reader::dbu_per_micron() const { return footer().dbu_per_micron; }

std::string_view Reader::manifest() const {
  const std::uint64_t off = footer().manifest_off;
  if (off == 0) return {};
  std::uint64_t len = 0;
  std::memcpy(&len, base_ + off, sizeof(len));
  return std::string_view(base_ + off + sizeof(len), len);
}

}  // namespace fastdrc::zones
