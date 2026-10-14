#pragma once
#ifndef __linux__
#error "SocketCAN requires Linux. Windows USB-CAN needs its vendor SDK transport."
#endif
#include "canopen_servo.hpp"
#include <algorithm>
#include <cerrno>
#include <fcntl.h>
#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>

namespace nimotion {
class SocketCan final : public CanBus {
    int fd_ = -1;
    [[noreturn]] static void error(const char* operation) {
        throw std::runtime_error(std::string(operation) + ": " + std::strerror(errno));
    }
public:
    explicit SocketCan(const std::string& interface = "can0") {
        if (interface.empty() || interface.size() >= IFNAMSIZ) throw std::invalid_argument("Invalid CAN interface");
        fd_ = socket(PF_CAN, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, CAN_RAW);
        if (fd_ < 0) error("socket");
        try {
            unsigned index = if_nametoindex(interface.c_str());
            if (!index) error("if_nametoindex");
            sockaddr_can addr{}; addr.can_family = AF_CAN; addr.can_ifindex = int(index);
            if (bind(fd_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) error("bind");
            // Only standard data frames. Receive all nodes; SDO client matches COB-ID/index/subindex.
            can_filter filter{}; filter.can_mask = CAN_EFF_FLAG | CAN_RTR_FLAG;
            if (setsockopt(fd_, SOL_CAN_RAW, CAN_RAW_FILTER, &filter, sizeof(filter)) < 0) error("filter");
        } catch (...) { close(fd_); fd_ = -1; throw; }
    }
    ~SocketCan() override { if (fd_ >= 0) close(fd_); }
    SocketCan(const SocketCan&) = delete;
    SocketCan& operator=(const SocketCan&) = delete;
    void send(const Frame& f) override {
        if (f.id > CAN_SFF_MASK || f.size > 8) throw std::invalid_argument("Invalid classic CAN frame");
        can_frame native{}; native.can_id = f.id; native.can_dlc = f.size;
        std::copy_n(f.data.begin(), f.size, native.data);
        const auto end = Clock::now() + std::chrono::milliseconds(500);
        while (true) {
            auto n = ::write(fd_, &native, sizeof(native));
            if (n == ssize_t(sizeof(native))) return;
            if (n >= 0) throw std::runtime_error("Short CAN write");
            if (errno == EINTR) {
                if (Clock::now() >= end) throw std::runtime_error("CAN write interrupted/timed out");
                continue;
            }
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != ENOBUFS) error("CAN write");
            if (Clock::now() >= end) throw std::runtime_error("CAN transmit queue timeout");
            pollfd p{fd_, POLLOUT, 0};
            if (poll(&p, 1, 10) < 0 && errno != EINTR) error("CAN write poll");
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }
    bool receive(Frame& f, int timeout_ms) override {
        auto end = Clock::now() + std::chrono::milliseconds(std::max(0, timeout_ms));
        while (true) {
            int remaining = timeout_ms == 0 ? 0 : std::max(0, int(std::chrono::duration_cast<std::chrono::milliseconds>(end - Clock::now()).count()));
            pollfd p{fd_, POLLIN, 0};
            int ready = poll(&p, 1, remaining);
            if (ready < 0) {
                if (errno == EINTR) return false;
                error("CAN receive poll");
            }
            if (!ready) return false;
            if (p.revents & (POLLERR | POLLHUP | POLLNVAL)) throw std::runtime_error("CAN socket error");
            can_frame native{};
            auto n = ::read(fd_, &native, sizeof(native));
            if (n < 0) {
                if (errno == EINTR) return false;
                if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    if (Clock::now() >= end) return false;
                    continue;
                }
                error("CAN read");
            }
            if (n != ssize_t(sizeof(native))) throw std::runtime_error("Short CAN read");
            if ((native.can_id & (CAN_EFF_FLAG | CAN_RTR_FLAG | CAN_ERR_FLAG)) || native.can_dlc > 8) {
                if (Clock::now() >= end) return false;
                continue;
            }
            f.id = native.can_id & CAN_SFF_MASK; f.size = native.can_dlc;
            std::copy_n(native.data, 8, f.data.begin()); return true;
        }
    }
};
} // namespace nimotion
