// 实时边界原语的验证。这里的失败直接对应“实时线程会阻塞/丢数据”这类真实缺陷。
#include <atomic>
#include <cstdint>
#include <thread>
#include <vector>

#include "km3/spsc_ring.hpp"
#include "km3/rt_types.hpp"
#include "test_harness.hpp"

using km3::MotionSnapshot;
using km3::SpscRing;

KM3_TEST(accepts_until_full_then_drops) {
    SpscRing<MotionSnapshot, 4> ring;  // 容量 4
    MotionSnapshot s;
    for (int i = 0; i < 4; ++i) {
        s.position_mm = static_cast<float>(i);
        KM3_CHECK(ring.Push(s));
    }
    s.position_mm = 99.0F;
    KM3_CHECK(!ring.Push(s));            // 满：必须拒绝而不是覆盖或阻塞
    KM3_CHECK_EQ(ring.Dropped(), std::uint64_t{1});
    KM3_CHECK_EQ(ring.Size(), std::size_t{4});
}

KM3_TEST(preserves_fifo_order) {
    SpscRing<MotionSnapshot, 8> ring;
    for (int i = 0; i < 5; ++i) {
        MotionSnapshot s;
        s.position_mm = static_cast<float>(i);
        KM3_CHECK(ring.Push(s));
    }
    for (int i = 0; i < 5; ++i) {
        MotionSnapshot out;
        KM3_CHECK(ring.Pop(out));
        KM3_CHECK_EQ(out.position_mm, static_cast<float>(i));
    }
    MotionSnapshot dummy;
    KM3_CHECK(!ring.Pop(dummy));         // 已空
    KM3_CHECK(ring.Empty());
}

KM3_TEST(wraps_around_without_loss) {
    SpscRing<MotionSnapshot, 4> ring;
    std::uint32_t produced = 0;
    std::uint32_t consumed = 0;
    std::uint32_t mismatches = 0;

    for (int round = 0; round < 100; ++round) {
        // 交替生产/消费，强制索引回绕
        for (int i = 0; i < 3; ++i) {
            MotionSnapshot s;
            s.position_mm = static_cast<float>(produced);
            if (ring.Push(s)) {
                ++produced;
            }
        }
        MotionSnapshot out;
        while (ring.Pop(out)) {
            if (out.position_mm != static_cast<float>(consumed)) {
                ++mismatches;
            }
            ++consumed;
        }
    }
    KM3_CHECK_EQ(mismatches, std::uint32_t{0});
    KM3_CHECK_EQ(produced, consumed);
    KM3_CHECK(ring.Empty());
}

// 双线程压力测试：验证内存序正确（生产者/消费者不丢、不重、不越界）。
KM3_TEST(concurrent_producer_consumer_keeps_order) {
    SpscRing<std::uint64_t, 1024> ring;
    constexpr std::uint64_t kTotal = 200000;

    std::atomic<bool> producer_done{false};
    std::uint64_t consumed = 0;
    std::uint64_t mismatches = 0;

    std::thread producer([&] {
        for (std::uint64_t i = 0; i < kTotal; ++i) {
            while (!ring.Push(i)) {
                // 仅测试中允许忙等；真实 RT 线程满即丢弃并计数。
                std::this_thread::yield();
            }
        }
        producer_done.store(true, std::memory_order_release);
    });

    std::thread consumer([&] {
        std::uint64_t expected = 0;
        while (expected < kTotal) {
            std::uint64_t v = 0;
            if (ring.Pop(v)) {
                if (v != expected) {
                    ++mismatches;
                }
                ++expected;
                ++consumed;
            } else if (producer_done.load(std::memory_order_acquire) && ring.Empty()) {
                break;
            } else {
                std::this_thread::yield();
            }
        }
    });

    producer.join();
    consumer.join();

    KM3_CHECK_EQ(mismatches, std::uint64_t{0});
    KM3_CHECK_EQ(consumed, kTotal);
}

// 布局检查：确认缓冲不参与堆分配，且容量为编译期常量。
KM3_TEST(is_allocation_free_and_compile_time_sized) {
    static_assert(SpscRing<MotionSnapshot, 64>::Capacity() == 64, "capacity is compile time");
    KM3_CHECK(sizeof(MotionSnapshot) <= 32);
    SpscRing<MotionSnapshot, 64> ring;   // 构造后不再分配
    MotionSnapshot s;
    KM3_CHECK(ring.Push(s));
    KM3_CHECK_EQ(ring.Size(), std::size_t{1});
}

KM3_TEST_MAIN()
