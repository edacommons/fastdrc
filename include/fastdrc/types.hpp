#pragma once

#include <cstdint>

namespace fastdrc {

/// A 1-D coordinate or length, in GDS database units (DBU). Signed.
///
/// 32-bit is sufficient: GDSII stores coordinates as INT32, so any value read
/// from a GDS file fits here. Keeping points small (8 bytes) matters for a
/// bandwidth-bound geometry engine.
using Coord1D = std::int32_t;

/// A value one "step" wider than Coord1D: a sum or difference of coordinates,
/// such as the implicit line value a*x + b*y + c or an octilinear distance.
/// These reach ~2^33 and so don't fit in Coord1D, but never need more than 64
/// bits once geometry is restricted to 0/45/90 degrees (no coordinate*
/// coordinate products survive — see Line).
using LineValue = std::int64_t;

/// Wide accumulator for the region/area engine (Engine 1). A tile's area is a
/// product of coordinate spans (up to ~2^64), and 45-degree tiles have
/// half-integer area, so that engine works in exact *doubled* area (2 * area)
/// held in 128 bits. Only the region/area engine needs this; the edge engine
/// stays int64 (see LineValue / Line).
using Area = __int128;

/// A point in the plane: two coordinates.
struct Coord2D {
  Coord1D x{};
  Coord1D y{};

  friend bool operator==(const Coord2D&, const Coord2D&) = default;
};

}  // namespace fastdrc
