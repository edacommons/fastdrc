#pragma once

#include <cstdint>

// On-disk layout for the Compiled Layout Geometry format v0.
// See docs/geometry-format-v0.md. Little-endian; structs are packed and written
// verbatim. Section data (arrays) is 8-byte aligned in the file.
//
// A file holds EXACTLY ONE layer of pure geometry — no source/semantic metadata
// (no GDS layer/datatype, no name). This keeps `.tiles` a general geometry
// container: a boolean op between two layer files stores its result as just
// another `.tiles`. Any name / source / derivation mapping lives in an external
// index. The single layer's section offsets are inlined in the footer.

namespace fastdrc::geom {

inline constexpr char kMagic[4] = {'F', 'D', 'C', 'G'};
inline constexpr char kEndMagic[8] = {'F', 'D', 'C', 'G', 'E', 'N', 'D', '\0'};
inline constexpr std::uint16_t kVersion = 0;

// A span primitive is a bbox (x0,y0,x1,y1) plus two signed cut extents
// (cut_left, cut_right) that take a corner triangle off each side — see
// Tile (tile.hpp). The cut's slope is H/extent, so any straight angle is
// representable (45 deg is just |extent| == H). cut == 0 means a vertical edge,
// i.e. a plain rectangle has cut_left == cut_right == 0.

#pragma pack(push, 1)

struct Header {  // 16 B at offset 0
  char magic[4];
  std::uint16_t version;
  std::uint16_t flags;        // bit0: little-endian
  std::uint64_t content_hash;  // FNV-1a-64 of all bytes AFTER this header
                               // (0 if unset). A 16-byte read fingerprints the
                               // whole file — fast change-detect / dedup.
};

// Footer at file end. Holds the (single) layer's section table inline plus the
// file-level structural facts. Last 8 bytes are end_magic.
struct Footer {  // 152 B
  std::uint64_t span_off;        // 0 = none
  std::uint64_t span_count;
  std::uint64_t edge_off[4];     // H,V,D1,D2; reserved (0 in v0)
  std::uint64_t edge_count[4];   // reserved (0)
  std::uint64_t stripe_off;      // reserved (0)
  std::uint64_t stripe_count;    // reserved (0)
  std::uint64_t manifest_off;    // 0 = none; points at a length-prefixed JSON blob
  std::uint64_t file_size;
  std::int32_t bbox[4];
  std::int32_t dbu_per_micron;
  std::uint16_t version;
  std::uint16_t flags;
  std::uint32_t reserved;
  char magic[4];
  char end_magic[8];
};

#pragma pack(pop)

static_assert(sizeof(Header) == 16, "Header must be 16 bytes");
static_assert(sizeof(Footer) == 152, "Footer must be 152 bytes");

}  // namespace fastdrc::geom
