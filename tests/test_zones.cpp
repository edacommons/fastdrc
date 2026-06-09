// A checker emits zones (interest + violation) -> write -> read back. Verifies
// the sidecar round-trips and a downstream filter can consume the boxes/values.

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <string_view>

#include "fastdrc/zones_reader.hpp"
#include "fastdrc/zones_writer.hpp"

using namespace fastdrc;

int main() {
  const std::string path = "test_zones_v0.bin";

  {
    zones::Writer w(1000);

    // Set 0: a spacing checker emits two confirmed violations (value = gap).
    w.begin_set("M1.S.1", "metal1 min spacing", "error");
    w.zone(10, 0, 12, 20, /*value=*/2, zones::kViolation);
    w.zone(40, 5, 41, 25, /*value=*/1, zones::kViolation);

    // Set 1: a coarse stage emits interest zones for a downstream filter.
    w.begin_set("M2.W.candidates", "narrow-metal candidates", "info");
    w.zone(0, 0, 100, 100, /*value=*/0, zones::kInterest);

    w.write(path);
  }

  zones::Reader r(path);
  assert(r.valid());
  assert(r.set_count() == 2);
  assert(r.dbu_per_micron() == 1000);

  const auto v0 = r.zones(0);
  assert(v0.count == 2);
  // Sorted by (y0, x0): the y0=0 zone comes first.
  assert(v0.y0[0] == 0 && v0.x0[0] == 10);
  assert(v0.value[0] == 2 && v0.kind[0] == zones::kViolation);
  assert(v0.y0[1] == 5 && v0.value[1] == 1);

  const auto v1 = r.zones(1);
  assert(v1.count == 1);
  assert(v1.kind[0] == zones::kInterest);
  assert(v1.x1[0] == 100 && v1.y1[0] == 100);

  // Union bbox spans both sets.
  assert(r.bbox()[0] == 0 && r.bbox()[1] == 0);
  assert(r.bbox()[2] == 100 && r.bbox()[3] == 100);

  // Manifest carries rule ids and severities.
  assert(r.manifest().find("M1.S.1") != std::string_view::npos);
  assert(r.manifest().find("error") != std::string_view::npos);

  std::remove(path.c_str());
  std::puts("all zones tests passed");
  return 0;
}
