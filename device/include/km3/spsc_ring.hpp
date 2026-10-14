#pragma once
// 实时/非实时边界：预分配的有界无锁单生产者单消费者环形缓冲。
// 依据：docs/架构设计/设备端程序架构.md §2.2.0，开发规范 §1.4。
//
// 使用纪律（违反即失去实时保证）：
//   1. T 必须是平凡可拷贝的 POD（无指针、无 std::string、无容器），定长。
//   2. 构造函数之后不再分配内存；缓冲在构造时一次分配。
//   3. 只有 RT 线程调用 Push，只有边界线程调用 Pop。
//   4. 满时丢弃并计数，绝不阻塞、绝不扩容。
//   5. 丢弃计数必须进入健康指标，不得静默丢弃。

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace km3 {

template <typename T, std::size_t N>
class SpscRing {
    static_assert(N >= 2, "capacity must be at least 2");
    static_assert((N & (N - 1)) == 0, "capacity must be a power of two");
    static_assert(std::is_trivially_copyable<T>::value,
                  "T must be trivially copyable: the RT path cannot copy owning types");
    static_assert(std::is_default_constructible<T>::value,
                  "T must be default constructible: the buffer is preallocated");

public:
    SpscRing() = default;

    SpscRing(const SpscRing&) = delete;
    SpscRing& operator=(const SpscRing&) = delete;

    static constexpr std::size_t Capacity() { return N; }

    // 生产者（RT 线程）。满则返回 false 并计入丢弃，不等待。
    bool Push(const T& value) noexcept {
        const std::uint32_t w = head_.load(std::memory_order_relaxed);
        const std::uint32_t r = tail_.load(std::memory_order_acquire);
        if (w - r >= N) {
            dropped_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        buffer_[w & (N - 1)] = value;
        head_.store(w + 1, std::memory_order_release);
        return true;
    }

    // 消费者（边界线程）。空则返回 false。
    bool Pop(T& out) noexcept {
        const std::uint32_t r = tail_.load(std::memory_order_relaxed);
        const std::uint32_t w = head_.load(std::memory_order_acquire);
        if (r == w) {
            return false;
        }
        out = buffer_[r & (N - 1)];
        tail_.store(r + 1, std::memory_order_release);
        return true;
    }

    bool Empty() const noexcept {
        return tail_.load(std::memory_order_acquire) ==
               head_.load(std::memory_order_acquire);
    }

    // 当前积压（近似值，读侧调用）
    std::size_t Size() const noexcept {
        const std::uint32_t w = head_.load(std::memory_order_acquire);
        const std::uint32_t r = tail_.load(std::memory_order_acquire);
        return static_cast<std::size_t>(w - r);
    }

    // 因缓冲满而丢弃的记录数。这是健康指标，必须被监控。
    std::uint64_t Dropped() const noexcept {
        return dropped_.load(std::memory_order_relaxed);
    }

private:
    // 头尾各占一条缓存行，避免生产者与消费者互相伪共享。
    alignas(64) std::atomic<std::uint32_t> head_{0};
    alignas(64) std::atomic<std::uint32_t> tail_{0};
    alignas(64) std::array<T, N> buffer_{};
    alignas(64) std::atomic<std::uint64_t> dropped_{0};
};

}  // namespace km3
