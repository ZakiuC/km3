// PMP4010 驱动骨架的验证。
//
// 重点不是"接口能调用"，而是**未知即不发帧**这条纪律被强制：
// 厂家手册未到，对象字典每条 index 都是 null。此时驱动唯一正确的行为是
// 拒绝发帧并计数。若某天有人"先让电机转起来再说"，把守卫改成恒真，
// 本文件的断言会立刻失败 —— 这就是它们的用途。
//
// 注意这里**只 include 驱动头文件，不 include od_generated.hpp**。
//
// 原因是一个已踩到的坑：两个头文件都曾在同一命名空间里定义同一个常量
// `kOdVerified`，同时 include 会直接 "redefinition of kOdVerified" 编不过。
// 事实只有一份，定义也只能有一处。在此稳定下来的是：
//   - driver/pmp4010_driver.hpp 是编译单元里 `kOdVerified` 的唯一定义处；
//   - driver/od_generated.hpp 由 od.json 生成，带 #error 守卫，供校验工具
//     与生成器使用，不并入驱动编译单元。
// 代价写在这里，不粉饰：**测试无法拿生成物的常量来和驱动对账**。
// 这条脱节风险由 od_generated.hpp 的 #error 承担 —— 它还带着
// `ObjectDictionary::kEntryCount`，手册到达后接进驱动时两者必须一起更新。
//
// 生成物的守卫是**编译期**行为，单测里无法表达（能跑起来的二进制就说明它没拦住），
// 因此它由 README 里那条"必须编不过"的命令验收，见 od_guard_* 用例的注释。
//
// 编译方式（与 device/CMakeLists.txt 里 test_hal 的做法一致，本目录不并入主阶梯）：
//   g++ -std=c++17 -O0 -I device/include -I common/include -I device/tests
//     -I device/drivers/pmp4010/driver
//     device/drivers/pmp4010/tests/test_pmp4010.cpp
//     device/drivers/pmp4010/driver/pmp4010_driver.cpp
//     device/src/hal/mock_devices.cpp device/src/common/device_kind.cpp
//     device/src/common/clock.cpp common/src/error_code.cpp
//     -o /tmp/test_pmp4010 -lpthread && /tmp/test_pmp4010

#include <cstdint>
#include <string>
#include <vector>

#include "../driver/pmp4010_driver.hpp"
#include "km3/hal/mock_devices.hpp"
#include "test_harness.hpp"

using km3::CanBackend;
using km3::ErrorCode;
using km3::MockCanDevice;
using km3::pmp4010::Direction;
using km3::pmp4010::DriverConfig;
using km3::pmp4010::od::kEntries;
using km3::pmp4010::od::kIndexUnset;
using km3::pmp4010::kOdVerified;
using km3::pmp4010::NmtCommand;
using km3::pmp4010::od::OdStatus;
using km3::pmp4010::od::pmp4010_index;
using km3::pmp4010::Pmp4010Driver;

namespace {

// 节点号 1..4 只是**测试内的自洽取值**，不是硬件事实。
// 分配方式（手册条目 D9）未确认，真实值必须来自配置。
DriverConfig MakeConfig() {
    DriverConfig cfg;
    cfg.logical_bus = "can-2";   // 逻辑名；物理接口在 device.json
    for (std::size_t i = 0; i < cfg.nodes.size(); ++i) {
        cfg.nodes[i].node_id = static_cast<std::uint8_t>(i + 1);
    }
    return cfg;
}

// 一个已打开、节点号完整的驱动 + 已打开的模拟设备。
struct Fixture {
    MockCanDevice device{"can-2", CanBackend::SocketCan};
    Pmp4010Driver driver{device, MakeConfig()};
};

}  // namespace

// ======================= 对象字典：未验证是当前事实 =======================

KM3_TEST(od_is_unverified_now) {
    // 本用例在手册到达后**应当被改写**：那时 kOdVerified 变 true，
    // 而改写本身就是"手册到了"的代码侧证据。
    KM3_CHECK(!kOdVerified);
    // 驱动不带旁路开关时根本编不出来，所以能跑到这里就说明本次构建
    // 带了 KM3_ALLOW_UNVERIFIED_OD=1。这一条断言把该事实钉住，
    // 免得将来有人给生产构建也加上那个开关却无人察觉。
    KM3_CHECK(PMP4010_OD_VERIFIED == 0);
}

KM3_TEST(od_counts_come_from_the_generated_header) {
    // N 必须与 od.json 同源，不能各处自己再数一遍 —— 那正是「两份真相」。
    // 生成物把它写成 kUnverifiedCount / kEntryCount，驱动只转发。
    KM3_CHECK_EQ(Pmp4010Driver::OdEntryCount(), std::size_t{25});
    KM3_CHECK_EQ(Pmp4010Driver::UnverifiedOdCount(), std::size_t{25});
    // 全部未核对 <=> 整体置信度为假。两者不自洽就是生成器或 od.json 坏了。
    KM3_CHECK_EQ(Pmp4010Driver::UnverifiedOdCount() == 0, kOdVerified);
}

KM3_TEST(od_entries_are_all_declared_unusable) {
    // 生成物里每条都带 verified 标志。逐条断言，而不是"抽查几条"——
    // 漏掉的那一条正是会被拿去发帧的那一条。
    for (std::size_t i = 0; i < Pmp4010Driver::OdEntryCount(); ++i) {
        KM3_CHECK(kEntries[i].status == OdStatus::Unverified);
        KM3_CHECK(!kEntries[i].verified);
        // 未核对条目的取值必须是哨兵，**不能**是 0：
        // 0 是合法的对象字典索引，拿它当「未填」会让漏填的条目
        // 看起来像一条指向 0x0000 的真实条目。
        KM3_CHECK_EQ(kEntries[i].index, kIndexUnset);
        // 取索引的辅助函数在 unusable 时必须返回哨兵，不替调用方兜底。
        KM3_CHECK_EQ(pmp4010_index(i), kIndexUnset);
    }
    // 越界也要安全返回哨兵，而不是读数组外。
    KM3_CHECK_EQ(pmp4010_index(Pmp4010Driver::OdEntryCount()), kIndexUnset);
}

KM3_TEST(od_guard_blocks_compilation_without_override) {
    // od_generated.hpp 的闸门是**编译期**行为，单测里无法表达，
    // 因此这里显式记录验收方式（README「测试方式」第 3 条同款）：
    //
    //   $ g++ -std=c++17 -I device/drivers/pmp4010/driver -fsyntax-only -x c++ - <<'INNER'
    //     #include "od_generated.hpp"
    //     int main() { return 0; }
    //     INNER
    //   driver/od_generated.hpp:41:2: error: #error "pmp4010 对象字典未验证..."
    //
    // 定义 KM3_ALLOW_UNVERIFIED_OD=1（或 PMP4010_OD_VERIFIED=1）才能编过。
    // 手册到达、od.json 填实后，本用例随之删除 —— 那时守卫的职责已经完成。
    //
    // 能断言的是驱动侧取值：它必须与 od.json 的现状（全部 unverified）一致。
    KM3_CHECK(!kOdVerified);
    KM3_CHECK(Pmp4010Driver::kOdGuardError == ErrorCode::EDeviceError);
}

// ======================= 生命周期 =======================

KM3_TEST(open_requires_open_device) {
    MockCanDevice dev("can-2", CanBackend::SocketCan);
    Pmp4010Driver drv(dev, MakeConfig());
    // HAL 不替调用方打开设备：持有者决定何时打开。
    KM3_CHECK(!drv.Open().ok());
    KM3_CHECK(dev.Open().ok());
    KM3_CHECK(drv.Open().ok());
    KM3_CHECK(drv.IsOpen());
}

KM3_TEST(open_rejects_usbcan_backend) {
    // CANopen 设备走主板 CAN；把 CAN FD 后端交给本驱动是接线/选型错误。
    MockCanDevice dev("usb-can-1", CanBackend::UsbCan);
    KM3_CHECK(dev.Open().ok());
    Pmp4010Driver drv(dev, MakeConfig());
    const km3::Status s = drv.Open();
    KM3_CHECK(!s.ok());
    KM3_CHECK(s.code() == ErrorCode::EInvalidRequest);
    KM3_CHECK(!drv.IsOpen());
}

KM3_TEST(open_rejects_empty_or_duplicate_node_ids) {
    MockCanDevice dev("can-2", CanBackend::SocketCan);
    KM3_CHECK(dev.Open().ok());

    {
        DriverConfig cfg = MakeConfig();
        cfg.logical_bus.clear();
        Pmp4010Driver drv(dev, cfg);
        KM3_CHECK(drv.Open().code() == ErrorCode::EInvalidConfig);
    }
    {
        DriverConfig cfg = MakeConfig();
        cfg.nodes[1].node_id = cfg.nodes[0].node_id;   // 重复
        Pmp4010Driver drv(dev, cfg);
        KM3_CHECK(drv.Open().code() == ErrorCode::EInvalidConfig);
    }
    {
        DriverConfig cfg = MakeConfig();
        cfg.nodes[3].node_id = 0;                      // 0 是广播地址，不是节点地址
        Pmp4010Driver drv(dev, cfg);
        KM3_CHECK(drv.Open().code() == ErrorCode::EInvalidConfig);
    }
}

// ======================= 核心：未验证 → 零字节发出 =======================

KM3_TEST(no_motion_call_sends_a_single_byte) {
    Fixture f;
    KM3_CHECK(f.device.Open().ok());
    KM3_CHECK(f.driver.Open().ok());

    const std::uint8_t node = 1;
    // 逐个方法过一遍 —— 漏一个就是一条能发帧的旁路。
    KM3_CHECK(f.driver.Enable(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver.Disable(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver.MoveTo(node, 1000).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver
                  .SetVelocity(node, 400, Direction::Raise)
                  .code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver.Stop(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver.Home(node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver.SendNmt(NmtCommand::Start, node).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver.ProbeNode(node).code() == Pmp4010Driver::kOdGuardError);

    // 这里读写的索引用 CiA 301 的**公共对象**（0x1018 identity object），
    // 不用任何厂家索引：本用例验的是守卫，不该顺手引入一个没依据的数字。
    // 公共对象由规范固定，tools/check-manual-citations.sh 也因此放行它们。
    constexpr std::uint16_t kCia301Identity = 0x1018;
    std::vector<std::uint8_t> buf{0xAA};
    KM3_CHECK(f.driver.SdoRead(node, kCia301Identity, 0, buf)
                  .code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK(f.driver.SdoWrite(node, kCia301Identity, 0, {0x01})
                  .code() == Pmp4010Driver::kOdGuardError);

    // 这就是全篇的核心断言。
    KM3_CHECK_EQ(f.device.SentFrames().size(), std::size_t{0});
    KM3_CHECK_EQ(f.device.Stats().tx_frames, std::uint64_t{0});
    KM3_CHECK_EQ(f.driver.Stats().rejected_by_od_guard, std::uint64_t{10});
    // 被挡下不是"错误累积"：总线本身没有出过错。
    KM3_CHECK_EQ(f.device.Stats().tx_errors, std::uint64_t{0});
}

KM3_TEST(unknown_node_is_also_guarded_before_validation) {
    // 未知节点号也是守卫错误，而**不是** EInvalidRequest：
    // OD 未验证时，判据是"能不能发帧"，节点号校验根本轮不到。
    // 这个优先级顺序是刻意的：若先校验节点号，现场日志会误导人以为
    // 问题出在节点号上，而去改配置。
    Fixture f;
    KM3_CHECK(f.device.Open().ok());
    KM3_CHECK(f.driver.Open().ok());
    const km3::Status s = f.driver.MoveTo(99, 1);
    KM3_CHECK(s.code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK_EQ(f.driver.Stats().rejected_by_validation, std::uint64_t{0});
    KM3_CHECK_EQ(f.device.SentFrames().size(), std::size_t{0});
}

KM3_TEST(guard_holds_even_when_closed) {
    // 未打开也不是"先放行"的理由。
    MockCanDevice dev("can-2", CanBackend::SocketCan);
    Pmp4010Driver drv(dev, MakeConfig());
    KM3_CHECK(dev.Open().ok());
    KM3_CHECK(drv.Enable(1).code() == Pmp4010Driver::kOdGuardError);
    KM3_CHECK_EQ(dev.SentFrames().size(), std::size_t{0});
}

KM3_TEST(close_sends_nothing) {
    // 关闭时不得补发 NMT Stop / 关使能：停机报文同样需要已确认的控制字索引。
    Fixture f;
    KM3_CHECK(f.device.Open().ok());
    KM3_CHECK(f.driver.Open().ok());
    KM3_CHECK(f.driver.Close().ok());
    KM3_CHECK(!f.driver.IsOpen());
    KM3_CHECK_EQ(f.device.SentFrames().size(), std::size_t{0});
}

KM3_TEST(sdo_read_does_not_touch_out_buffer_when_guarded) {
    // 被拒时输出参数**不改动**。这是已知的待办，不是"修好了"：
    // 真实实现落地时必须明确"失败时 out 的语义"（清空 vs 不改动），
    // 并在此处断言。现在写下事实。
    Fixture f;
    KM3_CHECK(f.device.Open().ok());
    KM3_CHECK(f.driver.Open().ok());
    std::vector<std::uint8_t> buf{0xDE, 0xAD, 0xBE};
    // 同样用公共对象，不用编造的 0x1234 —— 一个编造的索引会让人以为
    // "这个索引大概是可以用的"，而事实是它根本不属于任何一份资料。
    constexpr std::uint16_t kCia301Identity = 0x1018;
    KM3_CHECK(!f.driver.SdoRead(1, kCia301Identity, 0, buf).ok());
    KM3_CHECK_EQ(buf.size(), std::size_t{3});
    KM3_CHECK_EQ(buf[0], std::uint8_t{0xDE});
    KM3_CHECK_EQ(f.device.SentFrames().size(), std::size_t{0});
}

KM3_TEST(logical_bus_name_is_not_a_device_path) {
    // 逻辑名规则（device/include/km3/hal/device.hpp）：业务代码不得出现
    // /dev/ttyS3、can0 之类的物理标识。物理映射只在 device/config/device.json。
    Fixture f;
    KM3_CHECK_EQ(f.driver.LogicalBus(), std::string("can-2"));
    KM3_CHECK_EQ(f.device.LogicalName(), std::string("can-2"));
}

KM3_TEST(nmt_payload_shape_is_defined_but_guarded) {
    // NMT 的载荷形状（命令 + 节点号）是 CANopen 预定义连接集，可以写死，
    // 因此这里用反射式检查确认 NmtCommand 的取值没被人改过 ——
    // 改了它，将来守卫放开的第一帧就会发错命令。
    KM3_CHECK(static_cast<std::uint8_t>(NmtCommand::Start) == 0x01);
    KM3_CHECK(static_cast<std::uint8_t>(NmtCommand::Stop) == 0x02);
    KM3_CHECK(static_cast<std::uint8_t>(NmtCommand::PreOperational) == 0x80);
    KM3_CHECK(static_cast<std::uint8_t>(NmtCommand::ResetNode) == 0x81);
    KM3_CHECK(static_cast<std::uint8_t>(NmtCommand::ResetCommunication) == 0x82);
    // 但发不出去 —— 守卫在形状之前。
    Fixture f;
    KM3_CHECK(f.device.Open().ok());
    KM3_CHECK(f.driver.Open().ok());
    KM3_CHECK(!f.driver.SendNmt(NmtCommand::ResetNode, 0).ok());
    KM3_CHECK_EQ(f.device.SentFrames().size(), std::size_t{0});
}

KM3_TEST_MAIN()
