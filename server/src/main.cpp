// km3-server：后端单进程。模块化单体，原生部署（systemd + nginx）。
// 见 docs/架构设计/服务器端程序架构.md §1.1。
//
// 模块：http / user / device / data / log / ota / bus
// 明确不引入：Docker、Redis、InfluxDB、RabbitMQ、gRPC、ORM。

#include <atomic>
#include <csignal>
#include <cstdio>

#include "km3/server/permission.hpp"

namespace {
std::atomic<bool> g_running{true};
void HandleSignal(int) { g_running.store(false); }
}  // namespace

int main() {
    std::signal(SIGINT, HandleSignal);
    std::signal(SIGTERM, HandleSignal);

    std::printf("km3-server started (skeleton)\n");
    std::printf("  storage     : PostgreSQL single instance (partitioned sensor_data)\n");
    std::printf("  deploy      : systemd + nginx, no containers\n");
    std::printf("  dev session : %s\n", km3::server::ToString(km3::server::Role::SuperAdmin).data());

#if !defined(KM3_HAVE_BOOST)
    std::printf("  WARNING: Boost not found, network layer stubbed\n");
#endif
#if !defined(KM3_HAVE_PQXX) && !defined(KM3_HAVE_LIBPQ)
    std::printf("  WARNING: no PostgreSQL client, storage layer stubbed\n");
#endif

    // TODO: Asio io_context + Beast HTTP/WS，见服务器文档 §1.2 / §3
    std::printf("km3-server stopped\n");
    return 0;
}
