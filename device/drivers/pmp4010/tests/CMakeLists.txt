cmake_minimum_required(VERSION 3.20)

# 可单独配置，也可由 device/CMakeLists.txt 注册；只链接隔离假设备。
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  project(km3_pmp4010_mock_tests LANGUAGES CXX)
  enable_testing()
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  set(CMAKE_CXX_EXTENSIONS OFF)
  set(KM3_SANITIZER "address" CACHE STRING "Sanitizer: address | thread | none")
  set_property(CACHE KM3_SANITIZER PROPERTY STRINGS address thread none)
  add_compile_options(-Wall -Wextra -Wpedantic -Wshadow -Wconversion)
  if(KM3_SANITIZER STREQUAL "address")
    add_compile_options(-g -O0 -fsanitize=address,undefined -fno-omit-frame-pointer)
    add_link_options(-fsanitize=address,undefined)
  elseif(KM3_SANITIZER STREQUAL "thread")
    add_compile_options(-g -O1 -fsanitize=thread -fno-omit-frame-pointer)
    add_link_options(-fsanitize=thread)
  elseif(NOT KM3_SANITIZER STREQUAL "none")
    message(FATAL_ERROR "Unknown KM3_SANITIZER: ${KM3_SANITIZER}")
  endif()
endif()

get_filename_component(PMP4010_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../../.." ABSOLUTE)
find_package(Threads REQUIRED)

# 旁路宏只作用于隔离 mock 目标，不传播到生产 km3_drv_pmp4010。
# OD 运行时守卫仍然为 false；不需要真实 CAN 后端。
add_library(km3_pmp4010_mock STATIC
  ../driver/pmp4010_driver.cpp
  ${PMP4010_REPO_ROOT}/device/src/hal/mock_devices.cpp
  ${PMP4010_REPO_ROOT}/device/src/common/device_kind.cpp
  ${PMP4010_REPO_ROOT}/device/src/common/clock.cpp
  ${PMP4010_REPO_ROOT}/common/src/error_code.cpp
)
target_include_directories(km3_pmp4010_mock PUBLIC
  ${PMP4010_REPO_ROOT}/device/include
  ${PMP4010_REPO_ROOT}/common/include
)
target_compile_definitions(km3_pmp4010_mock PRIVATE KM3_ALLOW_UNVERIFIED_OD=1)
target_link_libraries(km3_pmp4010_mock PUBLIC Threads::Threads)

add_executable(test_pmp4010 test_pmp4010.cpp)
target_include_directories(test_pmp4010 PRIVATE ${PMP4010_REPO_ROOT}/device/tests)
target_compile_definitions(test_pmp4010 PRIVATE KM3_ALLOW_UNVERIFIED_OD=1)
target_link_libraries(test_pmp4010 PRIVATE km3_pmp4010_mock)
add_test(NAME pmp4010_mock_contract COMMAND test_pmp4010)

# 独立编译进程同时检查：生产默认必须被 OD 闸门挡住；mock 旁路只允许编译。
add_test(NAME pmp4010_production_od_guard
  COMMAND ${CMAKE_COMMAND}
    "-DPMP4010_CXX_COMPILER=${CMAKE_CXX_COMPILER}"
    "-DPMP4010_REPO_ROOT=${PMP4010_REPO_ROOT}"
    -P ${CMAKE_CURRENT_SOURCE_DIR}/test_od_guard.cmake
)
set_tests_properties(pmp4010_mock_contract pmp4010_production_od_guard
  PROPERTIES LABELS "pmp4010;mock" TIMEOUT 30)

# A separate binary exercises the real protocol/state paths with a fixed-memory
# fake. This macro must never propagate to the mock guard or production target.
add_executable(test_pmp4010_verified
  test_pmp4010_verified.cpp
  ../driver/pmp4010_driver.cpp
  ${PMP4010_REPO_ROOT}/device/src/common/clock.cpp
)
target_include_directories(test_pmp4010_verified PRIVATE
  ${PMP4010_REPO_ROOT}/device/include
  ${PMP4010_REPO_ROOT}/common/include
  ${PMP4010_REPO_ROOT}/device/tests
)
target_compile_definitions(test_pmp4010_verified PRIVATE PMP4010_OD_VERIFIED=1)
target_link_libraries(test_pmp4010_verified PRIVATE Threads::Threads)
add_test(NAME pmp4010_verified_fixture COMMAND test_pmp4010_verified)
set_tests_properties(pmp4010_verified_fixture PROPERTIES LABELS "pmp4010;mock" TIMEOUT 30)

# Fail CI when either language wire metadata/golden fixture or OD access metadata drifts.
find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_test(NAME pmp4010_protocol_generated_check
  COMMAND ${Python3_EXECUTABLE} ${PMP4010_REPO_ROOT}/tools/hwdriver/gen-canopen.py --check)
add_test(NAME pmp4010_access_generated_check
  COMMAND ${Python3_EXECUTABLE} ${PMP4010_REPO_ROOT}/tools/hwdriver/gen-pmp4010-access.py --check)
set_tests_properties(pmp4010_protocol_generated_check pmp4010_access_generated_check
  PROPERTIES LABELS "pmp4010;mock" TIMEOUT 30)
