#pragma once

#include <cstddef>
#include <map>

#include "fastdrc/layer.hpp"

namespace fastdrc {

/// A single, self-contained check configuration.
///
/// TODO: design the schema. It is intended to bundle three sections:
///   - a layout description (layers / derived layers the rule operates on),
///   - the measurements to take,
///   - a list of checks to apply to those measurements.
/// Empty placeholder for now.
struct CheckConfig {};

/// The geometry database: a set of layers. The CLI loads a GDS via rawast and
/// feeds shapes into the appropriate layer; then it runs a check.
///
/// NOTE: the check method and the CheckConfig schema are still unimplemented
/// stubs.
class Layout {
 public:
  /// Get the layer with this id, creating an empty one if it doesn't exist.
  Layer& layer(int id);

  /// Whether a layer with this id exists.
  [[nodiscard]] bool has_layer(int id) const;

  /// Number of layers.
  [[nodiscard]] std::size_t layer_count() const { return layers_.size(); }

  /// Run the check described by `config`.
  /// TODO: implement. Result model still undecided (returns nothing for now).
  void check(const CheckConfig& config) const;

 private:
  std::map<int, Layer> layers_;
};

}  // namespace fastdrc
