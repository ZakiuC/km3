#!/usr/bin/env bash
# km3 开发主机依赖（Ubuntu 22.04 桌面/服务器）
#
#   sudo bash tools/install-deps.sh
#
# 范围：能构建【设备端（交叉编译 aarch64）】与【服务器端（x86_64 原生）】。
# Android 侧需要 JDK 17 + SDK 包，见 tools/android-setup.sh。
#
# 维护要求：新增 find_package 时必须同步本文件，否则干净环境会配置失败。
# 可执行 tools/check-deps.sh 校验覆盖率。
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "需要 root：sudo bash tools/install-deps.sh" >&2
  exit 1
fi

apt-get update

# ---- 分组安装：逐组报告，单组失败不影响其余 ----
#
# 为什么不写成一条 apt-get 装全部（实测教训）：
#   本机启用了 i386 与 arm64 两个外来架构，而 apt 源里只有 amd64 索引。
#   多架构的 libc6 要求各架构版本一致，于是 clang 依赖的 libc6-dev
#   会拽着 libc6 升级，而 i386 拿不到新版本 —— 整条命令失败，
#   **连与之无关的 protobuf、zeromq 也一个都没装上**。
#   现场表现是"报了一个 i386 的错，结果什么都没装成"。
#   分组后：坏的那组单独失败，其余照常装上，且能一眼看出是哪一组。
FAILED_GROUPS=()
install_group() {
  local name="$1"; shift
  printf '\n==> %s\n' "$name"
  if apt-get install -y "$@"; then
    printf '    OK\n'
  else
    printf '    !! 该组安装失败，其余组继续\n' >&2
    FAILED_GROUPS+=("$name")
  fi
}

# --- 构建工具 ---
install_group "构建工具" \
  build-essential cmake ninja-build pkg-config ccache gdb git

# --- 设备端库 ---
install_group "设备端库（protobuf / zeromq / 日志 / sqlite / openssl）" \
  libzmq3-dev \
  libprotobuf-dev protobuf-compiler \
  libspdlog-dev libfmt-dev nlohmann-json3-dev \
  libsqlite3-dev sqlite3 \
  libssl-dev

# --- 服务器端库（Boost.Asio/Beast、libpqxx、libsodium，见服务器文档 §1.2）---
install_group "服务器端库（boost / libpqxx / libsodium）" \
  libboost-all-dev \
  libpq-dev libpqxx-dev \
  libsodium-dev

# --- TL3588 交叉工具链 ---
install_group "aarch64 交叉工具链" \
  g++-aarch64-linux-gnu gcc-aarch64-linux-gnu

# --- 静态分析与格式化工具（**可选**，不属于 km3 的构建依赖）---
# 单独成组：它们装不上不该阻塞上面任何一组。
# 已知失败情形：多架构 libc6 版本不一致时，clang/clang-tidy 依赖的 libc6-dev
# 要求各架构同步升级，apt 无法解析。修法见脚本结尾的提示。
install_group "静态检查工具（可选：clang-format / clang-tidy / cppcheck）" \
  clang clang-format clang-tidy cppcheck

# 注：当前测试用的是自带的零依赖骨架（device/tests/test_harness.hpp），
#     未链接 GTest，故不再预装 libgtest-dev / libgmock-dev。
# 注：ZeroMQ 的 C++ 绑定 zmq.hpp 不在 apt（无 cppzmq-dev），
#     已 vendored 到 device/third_party/cppzmq/，见该目录 README。
echo
if [ ${#FAILED_GROUPS[@]} -gt 0 ]; then
  printf '以下组安装失败：%s\n' "${FAILED_GROUPS[*]}" >&2
  echo
  echo "若是「静态检查工具」失败，且报错为 libc6 / libcrypt1 之类的多架构依赖："
  echo "  查看本机外来架构： dpkg --print-foreign-architectures"
  echo "  若含 i386，说明 apt 源缺 i386 索引。把 /etc/apt/sources.list 中各行的"
  echo "  [arch=amd64] 改为 [arch=amd64,i386] 后重跑本脚本。"
  echo "  clang 系工具不影响 km3 的构建与测试，可最后再处理。"
  exit 1
fi

echo "完成。自检："
echo "  cmake --version | head -1"
echo "  aarch64-linux-gnu-g++ --version | head -1"
echo "  protoc --version"
echo "  bash tools/check-deps.sh          # 校验与 CMake 需求一致"
