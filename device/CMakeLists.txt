cmake_minimum_required(VERSION 3.20)
project(km3_device VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Debug CACHE STRING "" FORCE)
endif()

add_compile_options(-Wall -Wextra -Wpedantic -Wshadow -Wconversion)

# ASan+UBSan 与 TSan 互斥，必须二选一（同时开启会导致 cc1plus 报 incompatible）。
# 开发规范 §3.3 要求 CI 两遍都跑，所以这里做成可切换选项而非写死。
set(KM3_SANITIZER "address" CACHE STRING "Sanitizer: address | thread | none")
set_property(CACHE KM3_SANITIZER PROPERTY STRINGS address thread none)

if(KM3_SANITIZER STREQUAL "address")
  add_compile_options(-g -O0 -fsanitize=address,undefined -fno-omit-frame-pointer)
  add_link_options(-fsanitize=address,undefined)
elseif(KM3_SANITIZER STREQUAL "thread")
  add_compile_options(-g -O1 -fsanitize=thread -fno-omit-frame-pointer)
  add_link_options(-fsanitize=thread)
else()
  if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    add_compile_options(-g -O0)
  else()
    add_compile_options(-O2)
  endif()
endif()

option(KM3_BUILD_TESTS "Build unit tests" ON)
option(KM3_ENABLE_PROTO "Generate protobuf code (needs protobuf)" ON)

# ---- 可选依赖：一律 QUIET，缺失时跳过对应目标，而不是配置失败 ----
find_package(Protobuf QUIET)
find_package(spdlog QUIET)
find_package(fmt QUIET)
find_package(nlohmann_json QUIET)
if(NOT nlohmann_json_FOUND)
  # 交叉编译下的常见情形：头文件在，但 CMake 包不在。
  # nlohmann-json3-dev 把 .hpp 放在 /usr/include，而这套工具链设了
  # CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY（根 = /usr/aarch64-linux-gnu），
  # 于是 find_path 看不见它——而交叉 g++ 的搜索列表末尾含 /usr/include，
  # 实际能编译。CMake 的路径推断与编译器的事实不一致时，以编译器为准：
  # 直接编译一个 #include 探针，问编译器而不是问文件系统。
  include(CheckIncludeFileCXX)
  check_include_file_cxx("nlohmann/json.hpp" KM3_NLOHMANN_JSON_FROM_COMPILER)
  if(KM3_NLOHMANN_JSON_FROM_COMPILER)
    # 头文件在编译器默认搜索路径内，无需再传 include 目录。
    add_library(km3_nlohmann_json INTERFACE)
    add_library(nlohmann_json::nlohmann_json ALIAS km3_nlohmann_json)
    set(nlohmann_json_FOUND TRUE)
    set(KM3_NLOHMANN_JSON_HEADER_ONLY TRUE)
  endif()
endif()
find_package(ZeroMQ QUIET)
find_package(SQLite3 QUIET)
find_package(OpenSSL QUIET)
find_package(Threads REQUIRED)

# ---- 共享层（协议 / 错误码）----
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../common ${CMAKE_CURRENT_BINARY_DIR}/common)

# ---- 设备端核心：实时原语与运行时类型，无第三方依赖，随时可构建 ----
add_library(km3_core
  src/common/rt_types.cpp
  src/common/clock.cpp
  src/common/deadline.cpp
  src/common/rt_util.cpp
)
target_include_directories(km3_core PUBLIC include)
target_link_libraries(km3_core PUBLIC km3_common Threads::Threads)

# ---- 协议代码生成（可选）----
if(KM3_ENABLE_PROTO AND Protobuf_FOUND)
  set(PROTO_GEN_DIR ${CMAKE_CURRENT_BINARY_DIR}/proto-gen)
  add_custom_command(
    OUTPUT ${PROTO_GEN_DIR}/km3.pb.cc ${PROTO_GEN_DIR}/km3.pb.h
    COMMAND ${CMAKE_COMMAND} -E make_directory ${PROTO_GEN_DIR}
    COMMAND protobuf::protoc
            --cpp_out=${PROTO_GEN_DIR}
            --proto_path=${CMAKE_CURRENT_SOURCE_DIR}/proto
            ${CMAKE_CURRENT_SOURCE_DIR}/proto/km3.proto
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/proto/km3.proto
    COMMENT "Generating protobuf sources from km3.proto"
  )
  add_library(km3_proto ${PROTO_GEN_DIR}/km3.pb.cc)
  target_include_directories(km3_proto PUBLIC ${PROTO_GEN_DIR})
  target_link_libraries(km3_proto PUBLIC protobuf::libprotobuf km3_core)
else()
  message(STATUS "km3: protobuf not found or disabled -> skipping km3_proto "
                 "(install libprotobuf-dev protobuf-compiler)")
endif()

# ---- 配置：加载 + 校验 → RtParams 快照 ----
#
# 单独成库而不是并入 km3_core：km3_core 刻意保持零第三方依赖，
# 这样在一台尚未装全依赖的机器上，实时原语与帧编解码仍可构建与测试。
add_library(km3_config src/common/config.cpp)
target_link_libraries(km3_config PUBLIC km3_core)

# config.cpp 无条件 include <nlohmann/json.hpp>，因此这里的判据是**头文件是否可见**。
# 早期用 KM3_HAVE_JSON 宏做条件编译，但没有任何源码引用它，
# 且 config.cpp 的 include 本就不受它控制——留着只会误导。
if(nlohmann_json_FOUND)
  target_link_libraries(km3_config PUBLIC nlohmann_json::nlohmann_json)
else()
  # 走到这里说明连头文件都没有，km3_config 会编译失败（这正是期望行为：
  # 配置解析缺失时必须显式失败，而不是悄悄退化成一个不读配置的控制进程）。
  message(STATUS "km3: nlohmann/json.hpp not found -> km3_config will not compile"
                 " (install nlohmann-json3-dev)")
endif()

# ---- 硬件抽象层：接口与模拟设备（不依赖硬件，主机可测）----
# 模拟设备是设计的一部分而非权宜：实测中 CAN、RS485、USB-CAN 模块均未接入，
# 没有它们，总线层与上层控制逻辑在硬件到位前完全无法测试。
add_library(km3_hal
  src/hal/mock_devices.cpp
  src/hal/socketcan_device.cpp
  src/common/device_kind.cpp
)
target_link_libraries(km3_hal PUBLIC km3_core)

# ---- 非实时 CANopen 标准层：有界事务，协议定义与 Python 同源生成 ----
add_library(km3_canopen src/canopen/sync.cpp src/canopen/pdo.cpp)
target_include_directories(km3_canopen PUBLIC include)
target_link_libraries(km3_canopen PUBLIC km3_hal km3_core)

# ---- 硬件驱动（由 tools/hwdriver/new-driver.sh 追加）----
#
# 这一区由 tools/hwdriver/new-driver.sh 维护：新增型号时它在**结束标记之前**
# 插一行 add_subdirectory(drivers/<型号>)。手工加驱动也应该写在这里，
# 不要另找地方——tools/check-drivers.sh 的第 (c) 条断言会逐个检查
# device/drivers/ 下的目录能否从**本文件**到达，而它认的就是这个标记区。
#
# 路径的基准是**本文件所在目录**（device/），因此写 drivers/<型号>，
# 不是 device/drivers/<型号>。后者会报
# "add_subdirectory given source ... which is not an existing directory"，
# 而那里的目录名看起来是对的，反查过的人才会知道基准不是仓库根。
#
# 为什么需要这条断言：目录存在但没挂进 build 是**静默**的——配置通过、
# 编译通过、测试通过，板上就是没有这个驱动。这类缺陷没有任何其它信号。
#
# 驱动之间的解耦约束（target_link_libraries 白名单、不得跨型号引用）
# 见 device/drivers/_template/CMakeLists.txt 的注释。
#
# pmp4010 —— 机身抬降电机 A（4 台，CANopen，挂逻辑名 can-2）。
#
# 加 if(TARGET ...) 守卫，而不是裸 add_subdirectory：
# pmp4010 要链 km3_canopen 与 km3_driver_api，后者尚未实现。
# 裸挂会让整个设备端工程在依赖落地前配置失败——一个尚未完成的驱动
# 不该挡住其它模块的构建。
#
# 这条守卫与 pmp4010/CMakeLists.txt 自己那段存在性检查是**两层**，
# 刻意重复：守卫在配置期跳过整个目录，目录内的检查在它被单独配置时
# 仍然给出可读的提示。谁先落地谁去掉自己那层。
if(TARGET km3_canopen AND TARGET km3_driver_api)
  add_subdirectory(drivers/pmp4010)
endif()

# ---- 硬件驱动结束 ----

# ---- 链路层：帧编解码（不依赖 ZeroMQ，主机上即可测试）----
add_library(km3_link
  src/middleware/frame.cpp
)
target_link_libraries(km3_link PUBLIC km3_core)

# ---- 实时控制律：控制律 + 安全监控（不依赖硬件，主机可测）----
add_library(km3_rt_control
  src/rt-control/motion_controller.cpp
  src/rt-control/safety_monitor.cpp
)
target_include_directories(km3_rt_control PUBLIC include)
target_link_libraries(km3_rt_control PUBLIC km3_hal)

# ---- 中间件层（需要 ZeroMQ）----
if(ZeroMQ_FOUND)
  add_library(km3_middleware src/middleware/placeholder.cpp)
  target_link_libraries(km3_middleware PUBLIC km3_core ZeroMQ::ZeroMQ)
else()
  message(STATUS "km3: ZeroMQ not found -> skipping km3_middleware "
                 "(install libzmq3-dev; zmq.hpp is vendored, see third_party/README.md)")
endif()

# ---- 可执行目标 ----
add_executable(km3-rt-controller src/apps/rt-controller/main.cpp)
target_link_libraries(km3-rt-controller PRIVATE km3_core km3_config km3_canopen)

add_executable(km3-core src/apps/core/main.cpp)
target_link_libraries(km3-core PRIVATE km3_core)

add_executable(km3-video src/apps/video/main.cpp)
target_link_libraries(km3-video PRIVATE km3_core)

if(Protobuf_FOUND AND KM3_ENABLE_PROTO)
  target_link_libraries(km3-core PRIVATE km3_proto)
endif()

if(KM3_BUILD_TESTS)
  enable_testing()
  # 生产驱动仍依赖完整 OD 与标准层；隔离 mock 契约测试不能被依赖守卫静默跳过。
  # 目录可移除，保持驱动可选；旁路宏只定义在 tests/ 自己的 mock 目标上。
  if(NOT TARGET test_pmp4010 AND
     EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/drivers/pmp4010/tests/CMakeLists.txt")
    add_subdirectory(drivers/pmp4010/tests)
  endif()
  add_executable(test_spsc_ring tests/test_spsc_ring.cpp)
  target_include_directories(test_spsc_ring PRIVATE tests)
  target_link_libraries(test_spsc_ring PRIVATE km3_core)
  add_test(NAME spsc_ring COMMAND test_spsc_ring)

  add_executable(test_error_code tests/test_error_code.cpp)
  target_include_directories(test_error_code PRIVATE tests)
  target_link_libraries(test_error_code PRIVATE km3_common)
  add_test(NAME error_code COMMAND test_error_code)

  add_executable(test_frame tests/test_frame.cpp)
  target_include_directories(test_frame PRIVATE tests)
  target_link_libraries(test_frame PRIVATE km3_link)
  add_test(NAME frame COMMAND test_frame)

  # 实时原语：时基是实时性缺陷的主要来源，必须有确定性测试覆盖
  if(nlohmann_json_FOUND)
    add_executable(test_config tests/test_config.cpp)
    target_include_directories(test_config PRIVATE tests)
    target_link_libraries(test_config PRIVATE km3_config)
    # 把仓库根编译进测试。有些用例需要读仓库里的真实文件（例如出厂配置模板），
    # 若用相对路径则会依赖运行目录：从仓库根跑通过、从构建目录跑（ctest 的默认行为）失败。
    # 编译期注入绝对路径后，二进制在任何 CWD 下行为一致。
    target_compile_definitions(test_config PRIVATE
      KM3_REPO_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/..")
    add_test(NAME config COMMAND test_config)
  endif()

  add_executable(test_hal tests/test_hal.cpp)
  target_include_directories(test_hal PRIVATE tests)
  target_link_libraries(test_hal PRIVATE km3_hal)
  add_test(NAME hal COMMAND test_hal)

  add_executable(test_socketcan tests/test_socketcan.cpp)
  target_include_directories(test_socketcan PRIVATE tests)
  target_link_libraries(test_socketcan PRIVATE km3_hal)
  # Deterministic Linux syscall seam; production targets keep real syscalls.
  foreach(call socket close bind setsockopt sendto poll recvmsg send recv if_nametoindex)
    target_link_options(test_socketcan PRIVATE "-Wl,--wrap=${call}")
  endforeach()
  add_test(NAME socketcan COMMAND test_socketcan)

  add_executable(test_pdo tests/test_pdo.cpp)
  target_include_directories(test_pdo PRIVATE tests)
  target_compile_definitions(test_pdo PRIVATE KM3_ALLOW_UNVERIFIED_OD=1)
  target_link_libraries(test_pdo PRIVATE km3_canopen)
  add_test(NAME canopen_pdo COMMAND test_pdo)
  set_tests_properties(canopen_pdo PROPERTIES TIMEOUT 15)

  add_executable(test_sync tests/test_sync.cpp)
  target_include_directories(test_sync PRIVATE tests)
  target_link_libraries(test_sync PRIVATE km3_canopen)
  add_test(NAME canopen_sync COMMAND test_sync)
  set_tests_properties(canopen_sync PROPERTIES TIMEOUT 15)
  find_package(Python3 COMPONENTS Interpreter REQUIRED)
  add_test(NAME sync_config_cli COMMAND ${Python3_EXECUTABLE}
    ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_sync_cli.py
    $<TARGET_FILE:km3-rt-controller>
    ${CMAKE_CURRENT_SOURCE_DIR}/config/validate-config.sh)
  set_tests_properties(sync_config_cli PROPERTIES TIMEOUT 45)

  add_executable(test_clock tests/test_clock.cpp)
  target_include_directories(test_clock PRIVATE tests)
  # pthread_* 直接调用，需显式链接
  target_link_libraries(test_clock PRIVATE km3_core Threads::Threads)
  add_test(NAME clock COMMAND test_clock)

  add_executable(test_rt_control tests/test_rt_control.cpp)
  target_include_directories(test_rt_control PRIVATE tests)
  target_link_libraries(test_rt_control PRIVATE km3_rt_control)
  add_test(NAME rt_control COMMAND test_rt_control)
endif()

message(STATUS "km3 device configuration:")
message(STATUS "  build type    : ${CMAKE_BUILD_TYPE}")
message(STATUS "  protobuf      : ${Protobuf_FOUND}")
message(STATUS "  zeromq        : ${ZeroMQ_FOUND}")
message(STATUS "  spdlog        : ${spdlog_FOUND}")
message(STATUS "  sqlite3       : ${SQLite3_FOUND}")
