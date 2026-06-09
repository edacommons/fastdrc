// Round-trip test for the v0 compiled-geometry writer: write a single-layer
// file, then parse it back via the on-disk structs and the zero-copy Reader.

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "fastdrc/geom_format.hpp"
#include "fastdrc/geom_reader.hpp"
#include "fastdrc/geom_writer.hpp"

using namespace fastdrc::geom;

int main() {
  const std::string path = "test_geom_v0.bin";

  // --- write one layer of pure geometry ---
  {
    Writer w(1000);
    w.rect(0, 0, 100, 100);
    w.rect(200, 0, 300, 50);
    w.tile(50, 50, 60, 60, /*cut_left=*/10, /*cut_right=*/0);  // a half-rect
    w.write(path);
  }

  // --- read the whole file ---
  std::ifstream f(path, std::ios::binary);
  assert(f);
  std::vector<char> buf((std::istreambuf_iterator<char>(f)),
                        std::istreambuf_iterator<char>());
  const char* base = buf.data();
  const std::size_t size = buf.size();

  // Header at offset 0.
  Header h{};
  std::memcpy(&h, base, sizeof(h));
  assert(std::memcmp(h.magic, kMagic, 4) == 0);
  assert(h.version == kVersion);

  // Footer: last sizeof(Footer) bytes; end_magic is the final 8. Holds the
  // single layer's section table inline.
  Footer ft{};
  std::memcpy(&ft, base + size - sizeof(Footer), sizeof(ft));
  assert(std::memcmp(ft.end_magic, kEndMagic, 8) == 0);
  assert(std::memcmp(ft.magic, kMagic, 4) == 0);
  assert(ft.file_size == size);
  assert(ft.span_count == 3);
  assert(ft.edge_off[0] == 0 && ft.stripe_off == 0);  // reserved, absent in v0
  assert(ft.bbox[0] == 0 && ft.bbox[1] == 0 && ft.bbox[2] == 300 &&
         ft.bbox[3] == 100);

  // Spans (SoA, sorted by y0 then x0): (0,0,100,100), (200,0,300,50),
  // (50,50,60,60).
  {
    const std::size_t m = ft.span_count;
    const auto* x0 = reinterpret_cast<const std::int32_t*>(base + ft.span_off);
    const auto* y0 = x0 + m;
    const auto* x1 = y0 + m;
    const auto* y1 = x1 + m;
    const auto* cl = y1 + m;
    const auto* cr = cl + m;
    assert(x0[0] == 0 && y0[0] == 0 && x1[0] == 100 && y1[0] == 100 &&
           cl[0] == 0 && cr[0] == 0);
    assert(x0[1] == 200 && y0[1] == 0 && x1[1] == 300 && y1[1] == 50 &&
           cl[1] == 0);
    assert(x0[2] == 50 && y0[2] == 50 && x1[2] == 60 && y1[2] == 60 &&
           cl[2] == 10 && cr[2] == 0);
  }

  // Manifest: length-prefixed JSON, STRUCTURAL only — format id + geometric
  // facts (dbu, bbox). No source/semantic info (no names, no GDS layer) — the
  // file is pure geometry.
  {
    assert(ft.manifest_off != 0);
    std::uint64_t mf_len = 0;
    std::memcpy(&mf_len, base + ft.manifest_off, sizeof(mf_len));
    const std::string mf(base + ft.manifest_off + sizeof(mf_len), mf_len);
    assert(mf.find("\"format\":\"FDCG\"") != std::string::npos);
    assert(mf.find("\"dbu_per_micron\":1000") != std::string::npos);
    assert(mf.find("\"name\"") == std::string::npos);
    assert(mf.find("\"gds_layer\"") == std::string::npos);
  }

  // --- read via the zero-copy Reader (the checker-facing API) ---
  {
    Reader r(path);
    assert(r.valid());
    assert(r.bbox()[2] == 300 && r.bbox()[3] == 100);
    assert(r.dbu_per_micron() == 1000);

    const auto s = r.spans();
    assert(s.count == 3);
    assert(s.x0[0] == 0 && s.y1[0] == 100 && s.cut_left[0] == 0);
    assert(s.x0[2] == 50 && s.cut_left[2] == 10 && s.cut_right[2] == 0);
  }

  // --- content_hash: nonzero, deterministic, and content-sensitive ---
  {
    Reader r(path);
    const std::uint64_t h = r.content_hash();
    assert(h != 0);

    const std::string p2 = "test_geom_v0_b.bin";
    {
      Writer w(1000);  // identical geometry -> identical file -> same hash
      w.rect(0, 0, 100, 100);
      w.rect(200, 0, 300, 50);
      w.tile(50, 50, 60, 60, 10, 0);
      w.write(p2);
    }
    Reader r2(p2);
    assert(r2.content_hash() == h);

    const std::string p3 = "test_geom_v0_c.bin";
    {
      Writer w(1000);  // different geometry -> different hash
      w.rect(0, 0, 100, 100);
      w.write(p3);
    }
    Reader r3(p3);
    assert(r3.content_hash() != h);

    std::remove(p2.c_str());
    std::remove(p3.c_str());
  }

  std::remove(path.c_str());
  std::puts("all geom-format tests passed");
  return 0;
}
