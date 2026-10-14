cmake_minimum_required(VERSION 3.20)
project(km3_server VERSION 0.1.0 LANGUAGES CXX)

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

# 全部 QUIET：缺依赖时跳过对应目标而不是配置失败。
find_package(Boost QUIET COMPONENTS system)
find_package(nlohmann_json QUIET)
find_package(PostgreSQL QUIET)
find_package(libpqxx QUIET)
find_package(spdlog QUIET)
find_package(fmt QUIET)
find_package(OpenSSL QUIET)
find_package(Protobuf QUIET)
find_package(Threads REQUIRED)

add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../common ${CMAKE_CURRENT_BINARY_DIR}/common)

add_library(km3_server_lib
  src/permission.cpp
)
target_include_directories(km3_server_lib PUBLIC include)
target_link_libraries(km3_server_lib PUBLIC km3_common Threads::Threads)

add_executable(km3-server src/main.cpp)
target_link_libraries(km3-server PRIVATE km3_server_lib)

if(Boost_FOUND)
  target_link_libraries(km3-server PRIVATE Boost::system)
  target_compile_definitions(km3-server PRIVATE KM3_HAVE_BOOST=1)
else()
  message(STATUS "km3: Boost not found -> server link layer stays stubbed "
                 "(install libboost-all-dev)")
endif()

if(nlohmann_json_FOUND)
  target_link_libraries(km3-server PRIVATE nlohmann_json::nlohmann_json)
  target_compile_definitions(km3-server PRIVATE KM3_HAVE_JSON=1)
endif()

if(libpqxx_FOUND)
  target_link_libraries(km3-server PRIVATE libpqxx::pqxx)
  target_compile_definitions(km3-server PRIVATE KM3_HAVE_PQXX=1)
elseif(PostgreSQL_FOUND)
  target_link_libraries(km3-server PRIVATE PostgreSQL::PostgreSQL)
  target_compile_definitions(km3-server PRIVATE KM3_HAVE_LIBPQ=1)
else()
  message(STATUS "km3: no PostgreSQL client found -> storage layer stubbed "
                 "(install libpq-dev libpqxx-dev)")
endif()

if(spdlog_FOUND)
  target_link_libraries(km3-server PRIVATE spdlog::spdlog)
  target_compile_definitions(km3-server PRIVATE KM3_HAVE_SPDLOG=1)
endif()

if(OpenSSL_FOUND)
  target_link_libraries(km3-server PRIVATE OpenSSL::SSL OpenSSL::Crypto)
  target_compile_definitions(km3-server PRIVATE KM3_HAVE_OPENSSL=1)
endif()

if(KM3_BUILD_TESTS)
  enable_testing()
  add_executable(test_permissions tests/test_permissions.cpp)
  target_include_directories(test_permissions PRIVATE tests)
  target_link_libraries(test_permissions PRIVATE km3_server_lib)
  add_test(NAME permissions COMMAND test_permissions)
endif()

message(STATUS "km3 server configuration:")
message(STATUS "  build type : ${CMAKE_BUILD_TYPE}")
message(STATUS "  boost      : ${Boost_FOUND}")
message(STATUS "  nlohmann   : ${nlohmann_json_FOUND}")
message(STATUS "  pqxx       : ${libpqxx_FOUND}")
message(STATUS "  spdlog     : ${spdlog_FOUND}")
message(STATUS "  openssl    : ${OpenSSL_FOUND}")
