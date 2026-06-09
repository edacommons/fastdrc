#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <new>
#include <stdexcept>
#include <string>

#include "fastdrc/design.hpp"
#include "fastdrc/layer_ops.hpp"
#include "fastdrc/sweep.hpp"
#include "fastdrc/types.hpp"

namespace nb = nanobind;
using namespace nb::literals;
using namespace fastdrc;

NB_MODULE(_fastdrc, m) {
  m.doc() = "FastDRC — fast design-rule-check engine (C++ core via nanobind)";

  nb::class_<Coord2D>(m, "Coord2D", "A point (x, y) in GDS database units.")
      .def(nb::init<>())
      .def(
          "__init__",
          [](Coord2D* self, Coord1D x, Coord1D y) {
            new (self) Coord2D{x, y};
          },
          "x"_a, "y"_a)
      .def_rw("x", &Coord2D::x)
      .def_rw("y", &Coord2D::y)
      .def("__eq__",
           [](const Coord2D& a, const Coord2D& b) { return a == b; })
      .def("__repr__", [](const Coord2D& p) {
        return nb::str("Coord2D({}, {})").format(p.x, p.y);
      });

  // The geometry merger for one layer. Obtained from Design.add_layer(); never
  // constructed directly. add/cut union/subtract into a disjoint tiling.
  nb::class_<Region>(m, "Region",
                     "A layer's geometry merger (union / cut into tiles).")
      .def(
          "add_rect",
          [](Region& r, Coord1D x0, Coord1D y0, Coord1D x1, Coord1D y1) {
            r.add(Rect{x0, y0, x1, y1});
          },
          "x0"_a, "y0"_a, "x1"_a, "y1"_a, "Union an axis-aligned rectangle.")
      .def(
          "cut_rect",
          [](Region& r, Coord1D x0, Coord1D y0, Coord1D x1, Coord1D y1) {
            r.cut(Rect{x0, y0, x1, y1});
          },
          "x0"_a, "y0"_a, "x1"_a, "y1"_a, "Cut a rectangular hole.")
      .def("add_polygon", &Region::add_polygon, "points"_a,
           "Union a rectilinear polygon (list of Coord2D); inner cut-outs OK.")
      .def("cut_polygon", &Region::cut_polygon, "points"_a,
           "Cut a rectilinear polygon (a separately-specified hole).")
      .def("rect_count", &Region::rect_count,
           "Number of disjoint tiles after merging.");

  // Holds everything to serialize: dbu + layers (metadata + merged geometry).
  nb::class_<Design>(
      m, "Design",
      "A design: per-layer geometry plus metadata (dbu, GDS layer/datatype, "
      "name). Serialized via write()/write_split().")
      .def(nb::init<std::int32_t>(), "dbu_per_micron"_a = 1000)
      .def("add_layer", &Design::add_layer, "gds_layer"_a, "gds_datatype"_a,
           "name"_a = "", nb::rv_policy::reference_internal,
           "Add a layer; returns its Region to compose geometry into.")
      .def("dbu", &Design::dbu, "Database units per micron.");

  m.def("write_split", &fastdrc::write_split, "design"_a, "dir"_a,
        "index_name"_a = "index.json",
        "Serialize a Design to one pure single-layer FDCG file per layer in "
        "`dir`, plus a JSON index (id -> file + source mapping, dbu/bbox).");

  m.def(
      "boolean",
      [](const std::string& a, const std::string& b, const std::string& op,
         const std::string& out) {
        BoolOp o;
        if (op == "or")
          o = BoolOp::Or;
        else if (op == "and")
          o = BoolOp::And;
        else if (op == "sub")
          o = BoolOp::Sub;
        else if (op == "xor")
          o = BoolOp::Xor;
        else
          throw std::invalid_argument("op must be one of or/and/sub/xor");
        const long n = boolean_files(a, b, o, out);
        if (n < 0) throw std::runtime_error("cannot read input layer file(s)");
        return n;
      },
      "a"_a, "b"_a, "op"_a, "out"_a,
      "Layer-algebra boolean A op B between two .tiles files -> a new .tiles "
      "(op in {or, and, sub, xor}); for B - A swap a/b. Returns the tile count.");

  m.def(
      "inflate",
      [](const std::string& in, int d, const std::string& out) {
        const long n = inflate_file(in, static_cast<Coord1D>(d), out);
        if (n < 0) throw std::runtime_error("cannot read input layer file");
        return n;
      },
      "in_path"_a, "d"_a, "out"_a,
      "Grow a .tiles layer by `d` on all sides (a halo) -> a new .tiles. "
      "Returns the tile count.");
}
