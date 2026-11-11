#include "fastdrc/geom_writer.hpp"

#include <algorithm>
#include <cstring>
#include <fstream>
#include <limits>
#include <numeric>
#include <string>

namespace fastdrc::geom {

namespace {

// A growable byte buffer that tracks size and 8-byte alignment.
struct Buf {
  std::vector<char> d;

  std::uint64_t size() const { return d.size(); }
  void put(const void* p, std::size_t n) {
    const char* c = static_cast<const char*>(p);
    d.insert(d.end(), c, c + n);
  }
  template <class T>
  void put_array(const std::vector<T>& v) {
    put(v.data(), v.size() * sizeof(T));
  }
  void align8() {
    while (d.size() % 8 != 0) d.push_back(0);
  }
};

// FNV-1a-64: a fast, non-cryptographic content hash for change-detection.
std::uint64_t fnv1a64(const char* p, std::size_t n) {
  std::uint64_t h = 1469598103934665603ull;
  for (std::size_t i = 0; i < n; ++i) {
    h ^= static_cast<unsigned char>(p[i]);
    h *= 1099511628211ull;
  }
  return h;
}

}  // namespace

void Writer::rect(std::int32_t x0, std::int32_t y0, std::int32_t x1,
                  std::int32_t y1) {
  tile(x0, y0, x1, y1, 0, 0);
}

void Writer::tile(std::int32_t x0, std::int32_t y0, std::int32_t x1,
                  std::int32_t y1, std::int32_t cut_left,
                  std::int32_t cut_right) {
  x0_.push_back(x0);
  y0_.push_back(y0);
  x1_.push_back(x1);
  y1_.push_back(y1);
  cl_.push_back(cut_left);
  cr_.push_back(cut_right);
}

bool Writer::write(const std::string& path) const {
  Buf b;

  Header h{};
  std::memcpy(h.magic, kMagic, 4);
  h.version = kVersion;
  h.flags = 1;  // little-endian
  h.content_hash = 0;  // patched in after the buffer is complete
  b.put(&h, sizeof(h));

  const std::size_t m = x0_.size();
  std::int32_t bx0 = std::numeric_limits<std::int32_t>::max();
  std::int32_t by0 = std::numeric_limits<std::int32_t>::max();
  std::int32_t bx1 = std::numeric_limits<std::int32_t>::min();
  std::int32_t by1 = std::numeric_limits<std::int32_t>::min();

  std::uint64_t span_off = 0;
  if (m != 0) {
    // Sort primitives by (y0, x0, x1) for a deterministic, query-friendly order.
    std::vector<std::uint32_t> idx(m);
    std::iota(idx.begin(), idx.end(), 0u);
    std::sort(idx.begin(), idx.end(), [&](std::uint32_t a, std::uint32_t c) {
      if (y0_[a] != y0_[c]) return y0_[a] < y0_[c];
      if (x0_[a] != x0_[c]) return x0_[a] < x0_[c];
      return x1_[a] < x1_[c];
    });

    std::vector<std::int32_t> sx0(m), sy0(m), sx1(m), sy1(m), scl(m), scr(m);
    for (std::size_t i = 0; i < m; ++i) {
      const std::uint32_t j = idx[i];
      sx0[i] = x0_[j];
      sy0[i] = y0_[j];
      sx1[i] = x1_[j];
      sy1[i] = y1_[j];
      scl[i] = cl_[j];
      scr[i] = cr_[j];
      bx0 = std::min(bx0, x0_[j]);
      by0 = std::min(by0, y0_[j]);
      bx1 = std::max(bx1, x1_[j]);
      by1 = std::max(by1, y1_[j]);
    }

    b.align8();
    span_off = b.size();
    b.put_array(sx0);
    b.put_array(sy0);
    b.put_array(sx1);
    b.put_array(sy1);
    b.put_array(scl);
    b.put_array(scr);
  }

  if (bx0 > bx1) {  // no primitives at all
    bx0 = by0 = bx1 = by1 = 0;
  }

  // Manifest: a small STRUCTURAL JSON blob (read once), length-prefixed. Format
  // identity + geometric facts only; no source/semantic info.
  std::string mf = "{\"format\":\"FDCG\",\"version\":0,\"generator\":\"fastdrc\"";
  mf += ",\"dbu_per_micron\":" + std::to_string(dbu_);
  mf += ",\"bbox\":[" + std::to_string(bx0) + "," + std::to_string(by0) + "," +
        std::to_string(bx1) + "," + std::to_string(by1) + "]}";

  b.align8();
  const std::uint64_t manifest_off = b.size();
  const std::uint64_t mf_len = mf.size();
  b.put(&mf_len, sizeof(mf_len));
  b.put(mf.data(), mf.size());

  b.align8();
  Footer ft{};  // zero-init: edge/stripe sections absent
  ft.span_off = span_off;
  ft.span_count = m;
  ft.manifest_off = manifest_off;
  ft.file_size = b.size() + sizeof(Footer);
  ft.bbox[0] = bx0;
  ft.bbox[1] = by0;
  ft.bbox[2] = bx1;
  ft.bbox[3] = by1;
  ft.dbu_per_micron = dbu_;
  ft.version = kVersion;
  ft.flags = 1;
  ft.reserved = 0;
  std::memcpy(ft.magic, kMagic, 4);
  std::memcpy(ft.end_magic, kEndMagic, 8);
  b.put(&ft, sizeof(ft));

  // Fingerprint everything after the 16-byte header, then patch it into the
  // header's content_hash field (offset 8). Deterministic geometry -> identical
  // file -> identical hash, so a header read tells layers apart fast.
  const std::uint64_t hash = fnv1a64(b.d.data() + 16, b.d.size() - 16);
  std::memcpy(b.d.data() + 8, &hash, sizeof(hash));

  std::ofstream f(path, std::ios::binary | std::ios::trunc);
  if (!f) return false;
  f.write(b.d.data(), static_cast<std::streamsize>(b.d.size()));
  f.close();
  return !f.fail();
}

}  // namespace fastdrc::geom
