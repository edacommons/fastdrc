#pragma once

#include <string>

#include "fastdrc/sweep.hpp"  // BoolOp

namespace fastdrc {

/// Read two single-layer `.tiles` files, apply `A op B`, and write the result as
/// a new single-layer `.tiles`. Layer algebra over compiled geometry: the output
/// is the same pure format, so results chain (boolean of a boolean). Returns the
/// number of tiles written, -1 if either input could not be read, or -2 if the
/// output could not be written.
long boolean_files(const std::string& a_path, const std::string& b_path,
                   BoolOp op, const std::string& out_path);

/// Read a single-layer `.tiles`, grow it by `d` (a halo), write a new `.tiles`.
/// (`d` < 0 shrinks.) Returns the number of tiles written, -1 if the input could
/// not be read, or -2 if the output could not be written.
long inflate_file(const std::string& in_path, Coord1D d,
                  const std::string& out_path);

}  // namespace fastdrc
