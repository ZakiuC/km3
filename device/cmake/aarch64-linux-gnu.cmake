# Cross-compilation toolchain for TL3588 (RK3588, aarch64, Ubuntu 22.04)
#
# Usage:
#   cmake -S device -B build-arm64 \
#         -DCMAKE_TOOLCHAIN_FILE=device/cmake/aarch64-linux-gnu.cmake \
#         -DCMAKE_BUILD_TYPE=Release
#
# The board runs Ubuntu 22.04 aarch64 with glibc 2.35. The host's
# aarch64-linux-gnu toolchain is GCC 11.4.0, which matches the on-board
# compiler version documented for TL3588 application builds. Keep them
# aligned so native extensions and shared libraries load.

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(TRIPLE aarch64-linux-gnu)

set(CMAKE_C_COMPILER   ${TRIPLE}-gcc)
set(CMAKE_CXX_COMPILER ${TRIPLE}-g++)
set(CMAKE_AR           ${TRIPLE}-ar)
set(CMAKE_RANLIB       ${TRIPLE}-ranlib)
set(CMAKE_STRIP        ${TRIPLE}-strip)

# Search the cross sysroot before host paths when resolving libraries,
# but still allow host tools (protoc) to run during the build.
set(CMAKE_FIND_ROOT_PATH /usr/${TRIPLE})
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# RK3588: 4x Cortex-A55 + 4x Cortex-A76. Do not target a newer ISA than the
# board's cores support, and do not enable -march=native.
set(CMAKE_C_FLAGS_INIT   "-O2 -pipe")
set(CMAKE_CXX_FLAGS_INIT "-O2 -pipe")
