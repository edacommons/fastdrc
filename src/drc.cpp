#include "fastdrc/drc.hpp"

namespace fastdrc {

Layer& Layout::layer(int id) { return layers_[id]; }  // creates if absent

bool Layout::has_layer(int id) const {
  return layers_.find(id) != layers_.end();
}

void Layout::check(const CheckConfig& /*config*/) const {
  // TODO: apply the layout description, take measurements, and run the
  // configured checks.
}

}  // namespace fastdrc
