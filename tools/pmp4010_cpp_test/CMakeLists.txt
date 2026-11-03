cmake_minimum_required(VERSION 3.16)
project(pmp4010_cpp_manual_test LANGUAGES CXX)

if(NOT CMAKE_SYSTEM_NAME STREQUAL "Linux")
  message(FATAL_ERROR "This standalone tool requires Linux SocketCAN.")
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(PMP4010_SANITIZER "none" CACHE STRING "Local instrumentation: none, address, thread")
set_property(CACHE PMP4010_SANITIZER PROPERTY STRINGS none address thread)
if(NOT PMP4010_SANITIZER MATCHES "^(none|address|thread)$")
  message(FATAL_ERROR "PMP4010_SANITIZER must be none, address or thread")
endif()
if(CMAKE_CROSSCOMPILING AND NOT PMP4010_SANITIZER STREQUAL "none")
  message(FATAL_ERROR "Cross builds require PMP4010_SANITIZER=none")
endif()

get_filename_component(KM3_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(PMP4010_EXAMPLE_DIR "${KM3_ROOT}/docs/硬件手册/PMP4010/示例")
set(PMP4010_DRIVER_DIR "${KM3_ROOT}/device/drivers/pmp4010/driver")
find_package(Threads REQUIRED)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
  "${PMP4010_EXAMPLE_DIR}/canopen_servo.hpp"
  "${PMP4010_EXAMPLE_DIR}/socketcan.hpp"
  "${PMP4010_DRIVER_DIR}/od.json")
file(SHA256 "${PMP4010_EXAMPLE_DIR}/canopen_servo.hpp" PMP4010_SERVO_SHA256)
file(SHA256 "${PMP4010_EXAMPLE_DIR}/socketcan.hpp" PMP4010_SOCKETCAN_SHA256)
file(SHA256 "${PMP4010_DRIVER_DIR}/od.json" PMP4010_OD_SHA256)

# Deliberately omit the production driver/HAL and original example executable.
# Only exact arithmetic is shared with the existing driver implementation.
add_library(pmp4010_manual_backend STATIC
  controller.cpp
  socketcan_checked.cpp
  "${PMP4010_DRIVER_DIR}/pmp4010_velocity.cpp")
target_include_directories(pmp4010_manual_backend PUBLIC
  "${CMAKE_CURRENT_SOURCE_DIR}" "${KM3_ROOT}"
  "${PMP4010_EXAMPLE_DIR}" "${PMP4010_DRIVER_DIR}")
target_link_libraries(pmp4010_manual_backend PUBLIC Threads::Threads)
target_compile_definitions(pmp4010_manual_backend PUBLIC
  PMP4010_SERVO_SHA256="${PMP4010_SERVO_SHA256}"
  PMP4010_SOCKETCAN_SHA256="${PMP4010_SOCKETCAN_SHA256}"
  PMP4010_OD_SHA256="${PMP4010_OD_SHA256}")
target_compile_options(pmp4010_manual_backend PRIVATE -Wall -Wextra -Wpedantic)
if(NOT PMP4010_SANITIZER STREQUAL "none")
  if(NOT CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    message(FATAL_ERROR "Sanitizers require GCC or Clang")
  endif()
  target_compile_options(pmp4010_manual_backend PUBLIC
    "-fsanitize=${PMP4010_SANITIZER}" -fno-omit-frame-pointer -g)
  target_link_options(pmp4010_manual_backend PUBLIC "-fsanitize=${PMP4010_SANITIZER}")
endif()

add_executable(pmp4010_manual_test main.cpp ui.cpp)
target_link_libraries(pmp4010_manual_test PRIVATE pmp4010_manual_backend)
target_compile_options(pmp4010_manual_test PRIVATE -Wall -Wextra -Wpedantic)

include(CTest)
if(BUILD_TESTING)
  add_executable(backend_tests tests/backend_tests.cpp)
  target_link_libraries(backend_tests PRIVATE pmp4010_manual_backend)
  target_compile_options(backend_tests PRIVATE -Wall -Wextra -Wpedantic)
  add_test(NAME pmp4010_manual_backend COMMAND backend_tests)
  set_tests_properties(pmp4010_manual_backend PROPERTIES TIMEOUT 120)
  # The injected preflight tests include their implementation directly, so do
  # not link the backend library (which would duplicate that implementation).
  add_executable(bus_preflight_tests tests/bus_preflight_tests.cpp)
  target_include_directories(bus_preflight_tests PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}" "${PMP4010_EXAMPLE_DIR}")
  target_link_libraries(bus_preflight_tests PRIVATE Threads::Threads)
  target_compile_options(bus_preflight_tests PRIVATE -Wall -Wextra -Wpedantic)
  if(NOT PMP4010_SANITIZER STREQUAL "none")
    target_compile_options(bus_preflight_tests PRIVATE
      "-fsanitize=${PMP4010_SANITIZER}" -fno-omit-frame-pointer -g)
    target_link_options(bus_preflight_tests PRIVATE "-fsanitize=${PMP4010_SANITIZER}")
  endif()
  add_test(NAME pmp4010_bus_preflight COMMAND bus_preflight_tests)
  set_tests_properties(pmp4010_bus_preflight PROPERTIES TIMEOUT 30)
endif()
