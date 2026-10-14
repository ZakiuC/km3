# 设备端与服务器端共用的基础层：错误码、结果类型、协议生成代码。
# 放进共享库的理由见 docs/架构设计/系统总体架构.md §4.2：
# 三端行为一致性的主要手段是共用同一份定义，而不是各写一遍。
add_library(km3_common
  src/error_code.cpp
)
target_include_directories(km3_common PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
target_compile_features(km3_common PUBLIC cxx_std_17)
