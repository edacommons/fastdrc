#pragma once

#include <cstdint>

// On-disk layout for the Compiled Zones format v0 — a filter/checker's output.
// A "zone" is an axis-aligned area of interest or a violation marker; zones are
// grouped into SETS (one per rule/stage). The file is a sidecar: it can be the
// final result of a checker, OR an extra input handed to a downstream filter
// that refines only inside these zones (the flow-of-filters DAG).
//
// Mirrors the geometry format (FDCG): little-endian, packed structs written
// verbatim, section arrays 8-byte aligned, a length-prefixed JSON manifest, a
// directory + footer at the end (streaming write, no seek-back). See
// docs/zones-format-v0.md.

namespace fastdrc::zones {

inline constexpr char kMagic[4] = {'F', 'D', 'C', 'Z'};
inline constexpr char kEndMagic[8] = {'F', 'D', 'C', 'Z', 'E', 'N', 'D', '\0'};
inline constexpr std::uint16_t kVersion = 0;

// Zone kind (the `kind` byte). A stage emits interest zones (look closer here);
// a terminal checker emits violations (confirmed errors).
enum Kind : std::uint8_t {
  kInterest = 0,   // candidate region for a downstream filter to refine
  kViolation = 1,  // confirmed rule violation (error marker)
  // 2..15 reserved (e.g. waiver, warning); >= 16 free for tool-specific use
};

#pragma pack(push, 1)

struct Header {  // 16 B at offset 0
  char magic[4];
  std::uint16_t version;
  std::uint16_t flags;  // bit0: little-endian
  std::uint64_t reserved;
};

// Purely structural — just section offsets/counts. The set's id is its index in
// the directory (auto-assigned); all semantics (rule, name, severity, source
// layers, value meaning, ...) live in the manifest, keyed by that id.
struct ZoneSetDesc {  // 32 B
  std::uint64_t box_off;    // x0[count],y0[count],x1[count],y1[count] (i32)
  std::uint64_t value_off;  // value[count] (i64); 0 = none
  std::uint64_t kind_off;   // kind[count] (u8); 0 = none
  std::uint64_t count;
};

struct Footer {  // 72 B at file end; last 8 bytes are end_magic
  std::uint64_t set_dir_off;
  std::uint64_t manifest_off;  // 0 = none; points at a length-prefixed JSON blob
  std::uint64_t set_count;
  std::uint64_t file_size;
  std::int32_t bbox[4];  // union of all zone boxes
  std::int32_t dbu_per_micron;
  std::uint16_t version;
  std::uint16_t flags;
  std::uint32_t reserved;
  char magic[4];
  char end_magic[8];
};

#pragma pack(pop)

static_assert(sizeof(Header) == 16, "Header must be 16 bytes");
static_assert(sizeof(ZoneSetDesc) == 32, "ZoneSetDesc must be 32 bytes");
static_assert(sizeof(Footer) == 72, "Footer must be 72 bytes");

}  // namespace fastdrc::zones
