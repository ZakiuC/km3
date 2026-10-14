# server — 服务器端（阿里云 VPS）

C++17，**原生部署**（systemd + nginx）。**不用 Docker、不用 Redis/InfluxDB/RabbitMQ**。
架构见 `docs/架构设计/服务器端程序架构.md`。

## 构建

```sh
cmake -S server -B build-server -DCMAKE_BUILD_TYPE=Debug
cmake --build build-server -j
ctest --test-dir build-server --output-on-failure
```

## 结构

| 路径 | 内容 |
| --- | --- |
| `include/km3/server/permission.hpp` | 权限矩阵（四级角色 × 十项能力） |
| `src/permission.cpp` | 矩阵实现，`HasPermission` 是唯一判定入口 |
| `src/main.cpp` | 进程入口（Asio/Beast 接入待实现） |
| `migrations/0001_init.sql` | 初始架构；时序数据用 PostgreSQL 分区表 |
| `deploy/systemd/km3-server.service` | systemd 单元（含 `StartLimitBurst` 限流） |
| `config/config.json` | 配置模板 |

## 关键设计

- **数据库**：PostgreSQL 单实例，关系 + 时序（`sensor_data` 按月分区）+ 会话。
- **认证**：设备用 Ed25519 挑战应答；用户用服务端会话票据（非 JWT），可即时吊销。
- **命令**：设备离线**入队等待**而非丢弃；超时由数据库 `timeout_at` 驱动，
  不给每条命令挂 goroutine。
- **紧急停止**：独立通道，允许插队，**先发后记**。
- **健康检查分级**：`/healthz` 永远 200；`/readyz` 才查数据库。

## 依赖

```sh
sudo apt-get install -y build-essential cmake ninja-build libboost-all-dev \
  libssl-dev libpq-dev libpqxx-dev libsodium-dev libprotobuf-dev \
  protobuf-compiler nlohmann-json3-dev libspdlog-dev libfmt-dev postgresql-16 nginx certbot
```

## 状态

骨架可构建、可测（权限矩阵有测试覆盖）。网络、存储、命令队列待实现。
