# third_party — 需自行引入的依赖

Ubuntu 22.04 的 apt 源里**没有** `cppzmq-dev`（只有 C 库 `libzmq3-dev`）。
cppzmq 是**单头文件**，因此按“vendored 头文件”处理，并把版本与摘要固定下来，
以便从干净环境复现构建（`开发规范.md §5` 与 TL3588 资料对交付可重建性的要求）。

## cppzmq

| 项 | 值 |
| --- | --- |
| 文件 | `cppzmq/zmq.hpp` |
| 上游 | https://github.com/zeromq/cppzmq |
| 版本 | **v4.10.0**（`CPPZMQ_VERSION_MAJOR/MINOR/PATCH = 4/10/0`） |
| sha256 | `1f8b641161dcf12641ae4951c2c49552de425be88d66c988ec5e85046f1320f6` |
| 许可 | MIT（见文件头） |
| 引入日期 | 2026-10-13 |

版本选择依据：与 apt 提供的 `libzmq3-dev`（4.3.4）兼容。
**升级 libzmq 前不要单独升级本头文件**——两者 ABI 相关。

校验方式：

```sh
sha256sum -c <<'EOF'
1f8b641161dcf12641ae4951c2c49552de425be88d66c988ec5e85046f1320f6  device/third_party/cppzmq/zmq.hpp
EOF
```

使用：编译时加 `-I device/third_party/cppzmq`，`#include <zmq.hpp>`。

## 其余依赖

全部走 apt（见 `tools/install-deps.sh`）：protobuf、spdlog、fmt、nlohmann-json、
yaml-cpp、gtest、sqlite3、OpenSSL、cpp-httplib。
