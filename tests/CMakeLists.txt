# The tests check with assert(); the default build type is Release (-DNDEBUG),
# which would compile every assertion out and make each test pass vacuously.
# Keep assertions live in the test executables whatever the build type.
foreach(t drc line pool edge region sweep boolean plane geom design zones)
  add_executable(test_${t} test_${t}.cpp)
  target_link_libraries(test_${t} PRIVATE fastdrc::core)
  target_compile_options(test_${t} PRIVATE -UNDEBUG)
  add_test(NAME test_${t} COMMAND test_${t})
endforeach()
