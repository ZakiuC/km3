#pragma once
// 配置快照：实时线程只读，边界线程换指针。
// 依据 docs/架构设计/开发规范.md §1.4：
//   "配置在启动时一次性快照为 POD 结构体，RT 线程只读该结构体的原子快照指针；
//    运行时配置变更只允许在边界线程完成并原子换指针。"
//
// 为什么不直接在 RT 路径读 JSON：json_pointer 解析会分配、要持锁、且异常被吞掉后
// 会静默用默认值继续运动 —— 比崩溃更危险。

#include <atomic>
#include <cstdint>
#include <type_traits>

namespace km3 {

// T 必须是可平凡拷贝的定长 POD。禁止在其中放 std::string / 容器 / 指针。
template <typename T>
class ConfigSnapshot {
    static_assert(std::is_trivially_copyable<T>::value,
                  "ConfigSnapshot<T>: T must be trivially copyable");
    static_assert(std::is_default_constructible<T>::value,
                  "ConfigSnapshot<T>: T must be default constructible");

public:
    explicit ConfigSnapshot(const T& initial) noexcept {
        slots_[0] = initial;
        slots_[1] = initial;
        index_.store(0, std::memory_order_release);
    }

    // 实时线程调用。无锁、无分配、无系统调用。
    const T& Get() const noexcept {
        return slots_[index_.load(std::memory_order_acquire)];
    }

    // 边界线程调用。写入非当前槽后原子切换索引。
    //
    // 使用限制（重要）：读侧可能仍持有旧槽引用。因此**同一配置在
    // 一次实时循环周期内不得连续发布两次**，否则第二次写入会踩到读侧正在用的槽。
    // 现实语义下配置由人触发，远慢于 1kHz 循环，这个约束不构成问题。
    // 若将来需要高频在线调参，应改为 RCU 或三槽轮转。
    void Publish(const T& next) noexcept {
        const std::uint32_t current = index_.load(std::memory_order_acquire);
        const std::uint32_t other = 1U - current;
        slots_[other] = next;
        index_.store(other, std::memory_order_release);
        ++published_;
    }

    std::uint64_t PublishedCount() const noexcept {
        return published_.load(std::memory_order_relaxed);
    }

private:
    T slots_[2] {};
    std::atomic<std::uint32_t> index_{0};
    std::atomic<std::uint64_t> published_{0};
};

}  // namespace km3
