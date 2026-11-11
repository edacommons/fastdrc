#include "fastdrc/zones_writer.hpp"

#include <algorithm>
#include <cassert>
#include <cstring>
#include <fstream>
#include <limits>
#include <numeric>
#include <string>

namespace fastdrc::zones {

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

std::string json_escape(const std::string& s) {
  std::string out;
  out.reserve(s.size() + 2);
  for (char c : s) {
    if (c == '"' || c == '\\') out.push_back('\\');
    out.push_back(c);
  }
  return out;
}

}  // namespace

void Writer::begin_set(std::string rule, std::string name, std::string severity) {
  Set s;
  s.rule = std::move(rule);
  s.name = std::move(name);
  s.severity = std::move(severity);
  sets_.push_back(std::move(s));
}

void Writer::zone(std::int32_t x0, std::int32_t y0, std::int32_t x1,
                  std::int32_t y1, std::int64_t value, std::uint8_t kind) {
  assert(!sets_.empty() && "begin_set() first");
  Set& s = sets_.back();
  s.x0.push_back(x0);
  s.y0.push_back(y0);
  s.x1.push_back(x1);
  s.y1.push_back(y1);
  s.value.push_back(value);
  s.kind.push_back(kind);
}

bool Writer::write(const std::string& path) const {
  Buf b;

  Header h{};
  std::memcpy(h.magic, kMagic, 4);
  h.version = kVersion;
  h.flags = 1;  // little-endian
  h.reserved = 0;
  b.put(&h, sizeof(h));

  std::vector<ZoneSetDesc> dir(sets_.size());
  std::int32_t bx0 = std::numeric_limits<std::int32_t>::max();
  std::int32_t by0 = std::numeric_limits<std::int32_t>::max();
  std::int32_t bx1 = std::numeric_limits<std::int32_t>::min();
  std::int32_t by1 = std::numeric_limits<std::int32_t>::min();

  for (std::size_t si = 0; si < sets_.size(); ++si) {
    const Set& s = sets_[si];
    const std::size_t m = s.x0.size();

    ZoneSetDesc& d = dir[si];
    std::memset(&d, 0, sizeof(d));
    d.count = m;  // set id == si; rule/name/severity go to the manifest

    if (m == 0) continue;

    // Sort zones by (y0, x0) for a deterministic, query-friendly order.
    std::vector<std::uint32_t> idx(m);
    std::iota(idx.begin(), idx.end(), 0u);
    std::sort(idx.begin(), idx.end(), [&](std::uint32_t a, std::uint32_t c) {
      if (s.y0[a] != s.y0[c]) return s.y0[a] < s.y0[c];
      return s.x0[a] < s.x0[c];
    });

    std::vector<std::int32_t> sx0(m), sy0(m), sx1(m), sy1(m);
    std::vector<std::int64_t> sval(m);
    std::vector<std::uint8_t> skind(m);
    for (std::size_t i = 0; i < m; ++i) {
      const std::uint32_t j = idx[i];
      sx0[i] = s.x0[j];
      sy0[i] = s.y0[j];
      sx1[i] = s.x1[j];
      sy1[i] = s.y1[j];
      sval[i] = s.value[j];
      skind[i] = s.kind[j];
      bx0 = std::min(bx0, s.x0[j]);
      by0 = std::min(by0, s.y0[j]);
      bx1 = std::max(bx1, s.x1[j]);
      by1 = std::max(by1, s.y1[j]);
    }

    b.align8();
    d.box_off = b.size();
    b.put_array(sx0);
    b.put_array(sy0);
    b.put_array(sx1);
    b.put_array(sy1);

    b.align8();
    d.value_off = b.size();
    b.put_array(sval);

    d.kind_off = b.size();  // u8, no alignment needed before footer sections
    b.put_array(skind);
  }

  if (bx0 > bx1) {  // no zones at all
    bx0 = by0 = bx1 = by1 = 0;
  }

  // Manifest: a JSON blob (read once), length-prefixed.
  std::string mf = "{\"format\":\"FDCZ\",\"version\":0,\"generator\":\"fastdrc\"";
  mf += ",\"dbu_per_micron\":" + std::to_string(dbu_);
  mf += ",\"bbox\":[" + std::to_string(bx0) + "," + std::to_string(by0) + "," +
        std::to_string(bx1) + "," + std::to_string(by1) + "]";
  mf += ",\"sets\":[";
  for (std::size_t si = 0; si < sets_.size(); ++si) {
    const Set& s = sets_[si];
    if (si) mf += ",";
    mf += "{\"id\":" + std::to_string(si);
    mf += ",\"rule\":\"" + json_escape(s.rule) + "\"";
    mf += ",\"name\":\"" + json_escape(s.name) + "\"";
    mf += ",\"severity\":\"" + json_escape(s.severity) + "\"";
    mf += ",\"count\":" + std::to_string(dir[si].count) + "}";
  }
  mf += "]}";

  b.align8();
  const std::uint64_t manifest_off = b.size();
  const std::uint64_t mf_len = mf.size();
  b.put(&mf_len, sizeof(mf_len));
  b.put(mf.data(), mf.size());

  b.align8();
  const std::uint64_t set_dir_off = b.size();
  b.put(dir.data(), dir.size() * sizeof(ZoneSetDesc));

  b.align8();
  Footer ft{};
  ft.set_dir_off = set_dir_off;
  ft.manifest_off = manifest_off;
  ft.set_count = sets_.size();
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
  assert(b.size() == ft.file_size);

  std::ofstream f(path, std::ios::binary | std::ios::trunc);
  if (!f) return false;
  f.write(b.d.data(), static_cast<std::streamsize>(b.d.size()));
  f.close();
  return !f.fail();
}

}  // namespace fastdrc::zones
