# _model_ 驱动模板。**不参与主构建**——device/CMakeLists.txt 不 add_subdirectory 本目录。
# CI 的 template job 把它复制到 device/drivers/<真实型号>/ 并替换占位后再构建。
#
# 占位约定（三套并存，替换顺序**不可颠倒**）：
#   _MODEL_   大写变体，用于宏名（宏名不可能是 PascalCase 与 snake_case 之外的样子）
#   _Model_   PascalCase，用于类名
#   _model_   小写，用于命名空间、目录名、文件名词干、目标名
# 顺序必须从最长的模式开始：先替 _MODEL_ 再替 _Model_，否则 _Model_ 会吃掉
# _MODEL_ 的前缀，产出残缺标识符。尖括号占位（<型号> 等）与下划线占位并存，
# <大写代号> 必须先于 <代号> 替换，同理。
cmake_minimum_required(VERSION 3.20)

# 占位型号（小写，与目录名一致，用于目标名与测试名后缀）。
set(KM3_DRV_MODEL "_model_" CACHE STRING "本驱动的型号标识（小写）")

set(KM3_DRV_TARGET "km3_drv_${KM3_DRV_MODEL}")
set(KM3_DRV_SRC_DIR "${CMAKE_CURRENT_SOURCE_DIR}")

# ---------------------------------------------------------------------------
# 依赖白名单
# ---------------------------------------------------------------------------
#
# 驱动**只**允许依赖架构层，绝不依赖兄弟驱动：
#
#   km3_core        实时原语、运行时类型、时间源
#   km3_hal         硬件抽象（ICanDevice 等接口 + 模拟设备）
#   km3_canopen     CANopen 协议栈（SDO / PDO / NMT）
#   km3_driver_api  驱动对外接口（上层只认它，不认具体型号）
#
# 为什么禁止链兄弟驱动：一旦 km3_drv_a 链了 km3_drv_b，
# 两者就无法分别测试、分别版本化，且换用一个型号会连带改动另一个的依赖闭包。
# 型号之间要协同，走 km3_driver_api 或上层编排，不走横向链接。
# tools/check-drivers.sh 的第 1 条断言在 CI 里查这一条。
#
# 为什么"某依赖不存在"不当场报错：km3_canopen 与 km3_driver_api 尚未落地，
# 直接 target_link_libraries 会退化成 -lkm3_canopen 并在**链接期**报
# "cannot find -lkm3_canopen"——错误发生在离根因很远的地方，且每个新驱动都要重复踩。
# 因此分两段：存在的就链，不存在的记名。配 -DKM3_DRV_STRICT_DEPS=ON 时缺失即配置期错误。
set(KM3_DRV_DEPENDENCIES km3_core km3_hal km3_canopen km3_driver_api)

set(KM3_DRV_MISSING_DEPS "")
set(KM3_DRV_RESOLVED_DEPS "")
foreach(dep IN LISTS KM3_DRV_DEPENDENCIES)
  if(TARGET ${dep})
    list(APPEND KM3_DRV_RESOLVED_DEPS ${dep})
  else()
    list(APPEND KM3_DRV_MISSING_DEPS ${dep})
  endif()
endforeach()

if(KM3_DRV_MISSING_DEPS)
  set(msg "km3 驱动模板 ${KM3_DRV_MODEL}: 以下依赖目标尚未定义 -> ${KM3_DRV_MISSING_DEPS}")
  if(KM3_DRV_STRICT_DEPS)
    message(FATAL_ERROR "${msg}（KM3_DRV_STRICT_DEPS=ON 时不允许缺失）")
  else()
    message(STATUS "${msg}")
    message(STATUS "  已按存在的依赖继续配置；缺失项的代码路径当前不可用时会在编译期显式失败，")
    message(STATUS "  而不是悄悄退化成空实现。")
  endif()
endif()

# ---------------------------------------------------------------------------
# 对象字典的毒化开关
# ---------------------------------------------------------------------------
#
# od_generated.hpp 由 od.json 生成。od.json 里只要有 status != verified 的条目，
# 生成的头文件就 #error，除非显式定义本开关。
#
# 允许旁路**仅**为了两件事：(1) CI 在无手册时保证骨架可构建；
# (2) 未接电机的控制器回环验证（见 docs/架构设计/硬件驱动开发路径.md 第 6 步）。
# 任何会驱动电机的场合都不允许带它——未核对的对象字典**不可能**编译出一个会动的二进制，
# 这是硬停规则第 2 条。绕过了它，故障就从编译期搬到了现场。
option(KM3_ALLOW_UNVERIFIED_OD "允许未核对的对象字典参与构建（仅 CI 与回环验证）" OFF)

add_library(${KM3_DRV_TARGET}
  driver/<代号>_driver.cpp
)

target_include_directories(${KM3_DRV_TARGET}
  PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/driver
)

target_link_libraries(${KM3_DRV_TARGET} PUBLIC ${KM3_DRV_RESOLVED_DEPS})

if(KM3_ALLOW_UNVERIFIED_OD)
  target_compile_definitions(${KM3_DRV_TARGET} PUBLIC KM3_ALLOW_UNVERIFIED_OD=1)
endif()

# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
#
# 用仓库自带的零依赖框架 device/tests/test_harness.hpp（见开发规范 §3.1），
# 不引入 GoogleTest：开发机与 CI 上的可用性不一致，而这一层不需要它。
if(KM3_BUILD_TESTS)
  enable_testing()

  add_executable(test_${KM3_DRV_MODEL} tests/test_<代号>.cpp)
  target_include_directories(test_${KM3_DRV_MODEL} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/tests
    ${CMAKE_CURRENT_SOURCE_DIR}/../../tests   # test_harness.hpp
  )
  target_link_libraries(test_${KM3_DRV_MODEL} PRIVATE ${KM3_DRV_TARGET})
  add_test(NAME ${KM3_DRV_MODEL} COMMAND test_${KM3_DRV_MODEL})
endif()

# 横向链接的自检：本驱动不得依赖任何兄弟驱动。
# 这条在模板里是空的（模板不链任何 km3_drv_*），但它必须存在于**每个**驱动的
# 构建文件里，否则约束只写在文档里，而文档拦不住一次手快的 copy-paste。
foreach(dep IN LISTS KM3_DRV_DEPENDENCIES)
  if(dep MATCHES "^km3_drv_")
    message(FATAL_ERROR
      "驱动 ${KM3_DRV_MODEL} 依赖了兄弟驱动 ${dep}。"
      "型号之间不得横向链接，协同请走 km3_driver_api 或上层编排（见本文件依赖白名单）。")
  endif()
endforeach()
