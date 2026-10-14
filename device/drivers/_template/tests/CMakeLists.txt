# _model_ 驱动测试的独立入口。主 CMakeLists.txt 已直接注册测试目标；
# 本文件供只想单独配置 tests/ 的场合，以及在驱动目录被单独拷走时使用。
#
# 与主 CMakeLists 用**同一套**占位：替换脚本对 *.txt 一视同仁，
# 因此这里不需要任何特例。
if(NOT TARGET km3_drv_<代号>)
  message(STATUS "km3: 本文件需在 device/drivers/_model_/CMakeLists.txt 之后 add_subdirectory，跳过")
  return()
endif()

add_executable(test_<代号> test_<代号>.cpp)
target_include_directories(test_<代号> PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${CMAKE_CURRENT_SOURCE_DIR}/../../../tests   # device/tests/test_harness.hpp
)
target_link_libraries(test_<代号> PRIVATE km3_drv_<代号>)
add_test(NAME _model_ COMMAND test_<代号>)
