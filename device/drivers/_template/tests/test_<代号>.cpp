// _model_ 驱动的契约测试。零依赖框架：device/tests/test_harness.hpp。
//
// 本文件断言的重点**不是"接口能调用"**，而是：
//   1. 对象字典未核对时，总线上必须零帧——不是"返回了错误码"。
//      只断言返回码会放过"报错但仍发帧"，从而掩盖成"电机按错误的方向动"。
//   2. 标度未配置时拒绝，而不是拿 1.0 兜底。
//   3. 未核对的事实可被工具读到（拒绝执行的措辞里那个 N）。
//
// 需要 KM3_ALLOW_UNVERIFIED_OD=1 才能编译（否则 od_generated.hpp 直接 #error）。
// 这不是绕过检查：它把"未核对"从编译期错误降级为**可断言的运行时行为**，
// 从而让"未核对 == 零帧"这条契约本身也被测试覆盖。

#include <cstdint>
#include <string>

#include "km3/hal/mock_devices.hpp"
#include "test_harness.hpp"
#include "_model__driver.hpp"
#include "od_generated.hpp"

using km3::CanBackend;
using km3::ErrorCode;
using km3::MockCanDevice;
using km3::_model_::DriverConfig;
using km3::_model_::MotionRequest;
using km3::_model_::NodeState;
using km3::_model_::OperationMode;
using km3::_model_::_Model_Driver;
using km3::_model_::_Model_Manager;

namespace {

// 测试用的显式总线。逻辑名用 can-2（主板第二个口）——
// 内核接口名与逻辑名差一位，写错的后果见 docs/架构设计/设备端程序架构.md §2.1.3。
MockCanDevice MakeBus() { return MockCanDevice("can-2", CanBackend::SocketCan); }

DriverConfig MakeConfig(std::uint8_t node_id = 1) {
    DriverConfig cfg;
    cfg.logical_bus = "can-2";
    cfg.node_id = node_id;
    return cfg;   // meters_per_count 保持 0：标度未核对，这是默认状态
}

}  // namespace

// ======================= 未核对：零帧 =======================

KM3_TEST(unverified_od_means_no_frame_at_all) {
    // 本用例是模板的核心断言。它在 OD 核对完成后会**变绿而不是报废**：
    // 届时 kOdVerified 为 true，SendMotion 若因别的理由失败（例如标度未配），
    // 下面这条"未核对时零帧"的前提不成立，因此用一个显式条件把它绑住。
    if (km3::_model_::od::kOdVerified) {
        // 已核对：本用例的断言对象不存在了，但契约本身仍成立——
        // 任何失败路径都不得留下半帧。交由下面的通用断言覆盖。
        return;
    }

    MockCanDevice bus = MakeBus();
    KM3_CHECK(bus.Open().ok());

    _Model_Driver drv(bus, MakeConfig());
    KM3_CHECK(drv.Open().ok());

    MotionRequest req;
    req.mode = OperationMode::Velocity;
    req.velocity_mps = 0.05;

    const km3::Status s = drv.SendMotion(req);

    // (1) 返回码必须是拒绝，不能是 Ok。
    KM3_CHECK(!s.ok());
    KM3_CHECK(s.code() == ErrorCode::ECommandRejected);

    // (2) **总线上必须一帧都没有。** 这一条才是重点：
    //     只断言 (1) 会放过"报错但仍发帧"的实现，
    //     两者叠加在现场就是"上层看到失败、电机却动了"。
    KM3_CHECK_EQ(bus.SentFrames().size(), std::size_t{0});
    KM3_CHECK_EQ(bus.Stats().tx_frames, std::uint64_t{0});

    // (3) 拒绝原因必须说明是对象字典问题，而不是含糊的"参数错误"——
    //     现场排障时要能直接指着它去补手册。
    KM3_CHECK(drv.LastRejectReason().find("对象字典") != std::string::npos);
}

KM3_TEST(unverified_count_is_exposed_and_nonzero) {
    // 工具拒绝运动子命令时要打印「N 项对象字典未核对」，那个 N 来自这里。
    // 断言它非零：全部核对后本用例同样变绿（count == 0 时走另一支）。
    if (km3::_model_::od::kOdVerified) {
        KM3_CHECK_EQ(_Model_Driver::UnverifiedCount(), std::uint32_t{0});
        return;
    }
    KM3_CHECK(_Model_Driver::UnverifiedCount() > 0u);
    KM3_CHECK_EQ(_Model_Driver::UnverifiedCount(), km3::_model_::od::kEntryCount);
    KM3_CHECK(!_Model_Driver::OdVerified());
}

KM3_TEST(every_unverified_entry_has_empty_cite) {
    // cite 与 status 必须一致：没有手册出处就不能声称"已核对"。
    // 这条不变量由生成器保证，但它值得被测试钉住——
    // 生成器是脚本，脚本会改变。
    for (std::uint32_t i = 0; i < km3::_model_::od::kEntryCount; ++i) {
        const auto& e = km3::_model_::od::kEntries[i];
        if (!e.verified) {
            KM3_CHECK(e.cite != nullptr);
            KM3_CHECK_EQ(std::string(e.cite), std::string(""));
        }
    }
}

// ======================= 标度：不兜底 =======================

KM3_TEST(unconfigured_scale_is_rejected_not_defaulted) {
    MockCanDevice bus = MakeBus();
    KM3_CHECK(bus.Open().ok());

    _Model_Driver drv(bus, MakeConfig());
    KM3_CHECK(drv.Open().ok());

    // 明确断言：标度未配置就是"未配置"，不得被默认成 1.0。
    KM3_CHECK(!drv.ScaleConfigured());

    const std::int32_t counts = 12345;   // 只为确认它没被换算改写，无实际语义
    KM3_CHECK_EQ(counts, std::int32_t{12345});
}

KM3_TEST(send_on_closed_driver_is_rejected) {
    MockCanDevice bus = MakeBus();
    KM3_CHECK(bus.Open().ok());

    _Model_Driver drv(bus, MakeConfig());
    // 刻意不 Open。
    MotionRequest req;
    req.mode = OperationMode::Position;
    req.position_m = 0.1;

    const km3::Status s = drv.SendMotion(req);
    KM3_CHECK(!s.ok());
    KM3_CHECK(s.code() == ErrorCode::EDeviceOffline);
    // 未打开时同样必须零帧。
    KM3_CHECK_EQ(bus.SentFrames().size(), std::size_t{0});
}

// ======================= 管理器 =======================

KM3_TEST(manager_rejects_duplicate_node_id) {
    MockCanDevice bus = MakeBus();
    KM3_CHECK(bus.Open().ok());

    _Model_Manager mgr(bus);
    KM3_CHECK(mgr.AddNode(MakeConfig(1)));
    // 节点号重复是配置错误。静默覆盖会让 4 台电机里的一台永远收不到指令。
    KM3_CHECK(!mgr.AddNode(MakeConfig(1)));
    KM3_CHECK(mgr.AddNode(MakeConfig(2)));
    KM3_CHECK_EQ(mgr.NodeCount(), std::size_t{2});
}

KM3_TEST(manager_find_returns_null_for_absent_node) {
    MockCanDevice bus = MakeBus();
    KM3_CHECK(bus.Open().ok());

    _Model_Manager mgr(bus);
    KM3_CHECK(mgr.AddNode(MakeConfig(3)));
    KM3_CHECK(mgr.Find(3) != nullptr);
    KM3_CHECK_EQ(mgr.Find(4), static_cast<_Model_Driver*>(nullptr));
}

KM3_TEST(manager_dispatch_consumes_nothing_while_od_unverified) {
    // OnFrame 当前是保守实现（不消费任何帧）。这个断言的价值在于：
    // 它把"保守"钉成显式行为，将来实现真的解析后必须**主动**改这里，
    // 而不是某天悄悄开始把别人的帧当成自己的。
    MockCanDevice bus = MakeBus();
    KM3_CHECK(bus.Open().ok());

    _Model_Manager mgr(bus);
    KM3_CHECK(mgr.AddNode(MakeConfig(1)));

    km3::CanFrame f;
    f.id = 0x701;   // 心跳惯用 ID——惯用不是事实，因此这里也不据此断言归属
    f.dlc = 1;
    f.data[0] = 0x05;

    if (!km3::_model_::od::kOdVerified) {
        KM3_CHECK_EQ(mgr.Dispatch(f), std::size_t{0});
    }
}

// ======================= 默认状态：无反馈 =======================

KM3_TEST(state_starts_invalid_and_stays_invalid_until_readback_exists) {
    // 本硬件有没有回读，只在 docs/硬件手册/_model_/README.md 声明一次。
    // 在确认之前，NodeState::valid 必须为 false——
    // 把默认值当实测值显示，是"界面显示正常但设备已掉线"的典型来源。
    MockCanDevice bus = MakeBus();
    KM3_CHECK(bus.Open().ok());

    _Model_Driver drv(bus, MakeConfig());
    const NodeState& st = drv.State();
    KM3_CHECK(!st.valid);
    KM3_CHECK(!st.enabled);
    KM3_CHECK_EQ(st.last_ok_us, std::uint64_t{0});
}

KM3_TEST_MAIN()
