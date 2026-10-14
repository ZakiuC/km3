# pmp4010 驱动测试的独立入口。主 CMakeLists.txt 已直接注册测试目标；
# 本文件供只想单独配置 tests/ 的场合，以及在驱动目录被单独拷走时使用。
if(NOT TARGET km3_drv_pmp4010)
  message(STATUS "km3: 本文件需在 device/drivers/pmp4010/CMakeLists.txt 之后 add_subdirectory，跳过")
  return()
endif()

add_executable(test_pmp4010_standalone test_pmp4010.cpp)
target_include_directories(test_pmp4010_standalone PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../tests   # device/tests/test_harness.hpp
)
target_link_libraries(test_pmp4010_standalone PRIVATE km3_drv_pmp4010)
add_test(NAME pmp4010_standalone COMMAND test_pmp4010_standalone)
