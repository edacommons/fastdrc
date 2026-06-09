#pragma once

#include <memory>

namespace fastdrc {

struct Point;
struct Line;
struct Edge;

/// Canonical, immutable, shared handles to interned geometry.
using PointPtr = std::shared_ptr<const Point>;
using LinePtr = std::shared_ptr<const Line>;
using EdgePtr = std::shared_ptr<const Edge>;

}  // namespace fastdrc
