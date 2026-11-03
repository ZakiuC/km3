cmake_minimum_required(VERSION 3.20)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  project(pmp4010_velocity_native LANGUAGES CXX)
endif()
# Standalone arithmetic target: deliberately has no OD, CAN or HAL dependency.
if(NOT TARGET pmp4010_velocity)
  add_library(pmp4010_velocity SHARED
    ../driver/pmp4010_velocity.cpp
    ../driver/pmp4010_velocity_c.cpp)
  target_compile_features(pmp4010_velocity PUBLIC cxx_std_17)
  target_compile_options(pmp4010_velocity PRIVATE -Wall -Wextra -Wpedantic -Wshadow -Wconversion)
  target_include_directories(pmp4010_velocity PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/../driver")
  set_target_properties(pmp4010_velocity PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
    VERSION 1.0.0 SOVERSION 1)
  install(TARGETS pmp4010_velocity LIBRARY DESTINATION lib)
  install(FILES ../driver/pmp4010_velocity_c.h DESTINATION include/km3/pmp4010)
endif()
