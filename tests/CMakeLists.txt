add_executable(test_drc test_drc.cpp)
target_link_libraries(test_drc PRIVATE fastdrc::core)
add_test(NAME test_drc COMMAND test_drc)

add_executable(test_line test_line.cpp)
target_link_libraries(test_line PRIVATE fastdrc::core)
add_test(NAME test_line COMMAND test_line)

add_executable(test_pool test_pool.cpp)
target_link_libraries(test_pool PRIVATE fastdrc::core)
add_test(NAME test_pool COMMAND test_pool)

add_executable(test_edge test_edge.cpp)
target_link_libraries(test_edge PRIVATE fastdrc::core)
add_test(NAME test_edge COMMAND test_edge)

add_executable(test_region test_region.cpp)
target_link_libraries(test_region PRIVATE fastdrc::core)
add_test(NAME test_region COMMAND test_region)

add_executable(test_sweep test_sweep.cpp)
target_link_libraries(test_sweep PRIVATE fastdrc::core)
add_test(NAME test_sweep COMMAND test_sweep)

add_executable(test_boolean test_boolean.cpp)
target_link_libraries(test_boolean PRIVATE fastdrc::core)
add_test(NAME test_boolean COMMAND test_boolean)

add_executable(test_plane test_plane.cpp)
target_link_libraries(test_plane PRIVATE fastdrc::core)
add_test(NAME test_plane COMMAND test_plane)

add_executable(test_geom test_geom.cpp)
target_link_libraries(test_geom PRIVATE fastdrc::core)
add_test(NAME test_geom COMMAND test_geom)

add_executable(test_design test_design.cpp)
target_link_libraries(test_design PRIVATE fastdrc::core)
add_test(NAME test_design COMMAND test_design)

add_executable(test_zones test_zones.cpp)
target_link_libraries(test_zones PRIVATE fastdrc::core)
add_test(NAME test_zones COMMAND test_zones)
