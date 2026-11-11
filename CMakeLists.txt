cmake_minimum_required(VERSION 3.26)

project(FastDRC
  VERSION 0.0.1
  DESCRIPTION "Fast design-rule-check (DRC) engine for IC/PCB layout"
  LANGUAGES CXX)

option(FASTDRC_BUILD_PYTHON "Build the nanobind Python module" OFF)
option(FASTDRC_BUILD_TESTS  "Build the C++ tests" ON)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Default to an optimized build for a standalone (non-multi-config) generator.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ---------------------------------------------------------------------------
# Core C++ library
# ---------------------------------------------------------------------------
add_library(fastdrc_core
  src/design.cpp
  src/drc.cpp
  src/geom_reader.cpp
  src/geom_writer.cpp
  src/layer.cpp
  src/line.cpp
  src/zones_reader.cpp
  src/zones_writer.cpp
  src/plane.cpp
  src/layer_ops.cpp
  src/pool.cpp
  src/region.cpp
  src/sweep.cpp)
add_library(fastdrc::core ALIAS fastdrc_core)

target_include_directories(fastdrc_core PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
target_compile_features(fastdrc_core PUBLIC cxx_std_20)
set_target_properties(fastdrc_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

# ---------------------------------------------------------------------------
# Python module (nanobind) — enabled by scikit-build-core during `pip install`
# ---------------------------------------------------------------------------
if(FASTDRC_BUILD_PYTHON)
  find_package(Python 3.11 REQUIRED COMPONENTS Interpreter Development.Module)

  # Locate the nanobind CMake package shipped inside the installed wheel.
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
  find_package(nanobind CONFIG REQUIRED)

  nanobind_add_module(_fastdrc STABLE_ABI NB_STATIC bindings/module.cpp)
  target_link_libraries(_fastdrc PRIVATE fastdrc::core)

  install(TARGETS _fastdrc LIBRARY DESTINATION fastdrc)
endif()

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
if(FASTDRC_BUILD_TESTS)
  enable_testing()
  add_subdirectory(tests)
endif()
