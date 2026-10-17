#include <atomic>
#include <cstdlib>
#include <new>
#include <poll.h>
#include <thread>

#include "../drivers/pmp4010/driver/pdo_config.hpp"
#include "km3/canopen_pdo.hpp"
#include "test_harness.hpp"

namespace {
std::atomic<bool> measuring{false};
std::atomic<unsigned> allocations{0};
std::uint64_t now = 0;
std::uint64_t Clock() noexcept {
    return now;
}
class Probe final : public km3::ICanDevice {
public:
    const std::string& LogicalName() const noexcept override { return name_; }
    km3::Status Open() noexcept override { return km3::Status::Ok(); }
    km3::Status Close() noexcept override { return km3::Status::Ok(); }
    bool IsOpen() const noexcept override { return true; }
    km3::DeviceStatus StatusOf() const override { return {}; }
    km3::CanBackend Backend() const noexcept override { return km3::CanBackend::SocketCan; }
    km3::CanCapabilities Capabilities() const override { return {}; }
    km3::Status Send(const km3::CanFrame&) noexcept override {
        ++unconfirmed;
        return km3::Status::Ok();
    }
    bool SupportsTxConfirmation() const noexcept override { return supports; }
    km3::Status SendConfirmed(const km3::CanFrame& f, std::uint64_t) noexcept override {
        ids[calls % ids.size()] = f.id;
        ++calls;
        if (calls == fail_at)
            return km3::Status(km3::ErrorCode::ECommandTimeout);
        if (advance)
            now += advance;
        ++confirmed;
        return km3::Status::Ok();
    }
    km3::Result<km3::CanFrame> Receive(int timeout) override {
        if (timeout != 0)
            ++blocking;
        if (rx_count) {
            --rx_count;
            return km3::Result<km3::CanFrame>::Ok(rx);
        }
        return km3::Result<km3::CanFrame>::Err(km3::ErrorCode::ECommandTimeout);
    }
    km3::Status SetFilters(const std::vector<km3::CanFilter>&) override {
        return km3::Status::Ok();
    }
    km3::CanBusStats Stats() const override { return {}; }
    km3::Status RecoverFromBusOff() override {
        return km3::Status(km3::ErrorCode::ECommandRejected);
    }
    std::array<std::uint32_t, 256> ids{};
    unsigned calls = 0, confirmed = 0, fail_at = 0, unconfirmed = 0, blocking = 0, rx_count = 0;
    std::uint64_t advance = 0;
    bool supports = true;
    km3::CanFrame rx{};

private:
    std::string name_{"fake-pdo"};
};
km3::canopen::PdoConfig Config() {
    km3::canopen::PdoConfig c{};
    c.rpdo_count = c.tpdo_count = 4;
    c.sync_cob_id = 0x80;
    for (unsigned i = 0; i < 4; ++i) {
        const auto& r = km3::pmp4010::od::kRpdoLayouts[i];
        const auto& t = km3::pmp4010::od::kTpdoLayouts[i];
        c.rpdo[i] = {static_cast<std::uint32_t>(r.cob_base + 7), r.entry_count, {}};
        c.tpdo[i] = {static_cast<std::uint32_t>(t.cob_base + 7), t.entry_count, {}};
        std::copy_n(r.entries, 8, c.rpdo[i].entries.begin());
        std::copy_n(t.entries, 8, c.tpdo[i].entries.begin());
    }
    return c;
}
km3::canopen::PdoBatch Batch(const km3::canopen::PdoConfig& c) {
    km3::canopen::PdoBatch b{};
    b.count = c.rpdo_count;
    for (std::size_t i = 0; i < b.count; ++i) b.values[i].count = c.rpdo[i].entry_count;
    return b;
}
struct WireFixture {
    std::uint8_t length;
    std::uint8_t count;
    std::array<std::uint32_t, 8> values;
    std::array<std::uint8_t, 8> bytes;
};
// Handwritten wire oracles, in RPDO1..4 then TPDO1..4 order. Neither codec
// produces the other's input/expectation; distinct high bytes expose swaps.
constexpr WireFixture kWireFixtures[] = {
    {3, 2, {0xfedcu, 0xfbu}, {0xdc, 0xfe, 0xfb}},
    {8, 2, {0x607a0020u, 0x89abcdefu}, {0x20, 0x00, 0x7a, 0x60, 0xef, 0xcd, 0xab, 0x89}},
    {6, 2, {0xfedcu, 0x12345678u}, {0xdc, 0xfe, 0x78, 0x56, 0x34, 0x12}},
    {2, 1, {0xabcdu}, {0xcd, 0xab}},
    {3, 2, {0x1234u, 0xf9u}, {0x34, 0x12, 0xf9}},
    {8, 2, {0x89abcdefu, 0x76543210u}, {0xef, 0xcd, 0xab, 0x89, 0x10, 0x32, 0x54, 0x76}},
    {4, 1, {0xfedcba98u}, {0x98, 0xba, 0xdc, 0xfe}},
    {2, 1, {0x8badu}, {0xad, 0x8b}},
};
}  // namespace
void* operator new(std::size_t size) {
    if (measuring.load())
        ++allocations;
    if (auto* p = std::malloc(size ? size : 1))
        return p;
    throw std::bad_alloc();
}
void* operator new[](std::size_t n) {
    return ::operator new(n);
}
void operator delete(void* p) noexcept {
    std::free(p);
}
void operator delete[](void* p) noexcept {
    std::free(p);
}
void operator delete(void* p, std::size_t) noexcept {
    std::free(p);
}
void operator delete[](void* p, std::size_t) noexcept {
    std::free(p);
}

KM3_TEST(generated_mapping_lengths_and_little_endian_raw_signed_roundtrips) {
    const auto c = Config();
    const unsigned lengths[2][4]{{3, 8, 6, 2}, {3, 8, 4, 2}};
    for (unsigned d = 0; d < 2; ++d) {
        const auto& maps = d ? c.tpdo : c.rpdo;
        for (unsigned i = 0; i < 4; ++i) {
            KM3_REQUIRE(km3::canopen::PdoLength(maps[i]).ok());
            KM3_CHECK_EQ(km3::canopen::PdoLength(maps[i]).value(), lengths[d][i]);
            km3::canopen::PdoValues values{};
            values.count = maps[i].entry_count;
            for (unsigned j = 0; j < values.count; ++j) {
                const auto bits = maps[i].entries[j] & 255;
                values.values[j] = bits == 32 ? 0x89abcdefu : bits == 16 ? 0xfedcu : 0xfbu;
            }
            auto encoded = km3::canopen::EncodePdo(maps[i], values);
            KM3_REQUIRE(encoded.ok());
            KM3_CHECK_EQ(encoded.value().data[0],
                         lengths[d][i] == 3 || lengths[d][i] == 2 || lengths[d][i] == 6 ? 0xdcu
                                                                                        : 0xefu);
            const auto decoded = km3::canopen::DecodePdo(maps[i], encoded.value());
            KM3_REQUIRE(decoded.ok());
            KM3_CHECK_EQ(decoded.value().values, values.values);
            for (unsigned length = 0; length < 256; ++length) {
                encoded.value().dlc = static_cast<std::uint8_t>(length);
                KM3_CHECK_EQ(km3::canopen::DecodePdo(maps[i], encoded.value()).ok(),
                             length == lengths[d][i]);
            }
        }
    }
}
KM3_TEST(encode_all_mappings_matches_handwritten_wire_bytes) {
    const auto c = Config();
    // RPDO2 field 0 maps target position; its chosen raw value 0x607A0020
    // must appear on the wire as 20 00 7A 60, independently of DecodePdo.
    KM3_CHECK_EQ(c.rpdo[1].entries[0], 0x607a0020u);
    for (unsigned i = 0; i < 8; ++i) {
        const auto& mapping = i < 4 ? c.rpdo[i] : c.tpdo[i - 4];
        const auto& expected = kWireFixtures[i];
        km3::canopen::PdoValues values{};
        values.count = expected.count;
        values.values = expected.values;
        const auto encoded = km3::canopen::EncodePdo(mapping, values);
        KM3_REQUIRE(encoded.ok());
        KM3_CHECK_EQ(encoded.value().id, mapping.cob_id);
        KM3_CHECK_EQ(encoded.value().dlc, expected.length);
        for (unsigned b = 0; b < expected.length; ++b)
            KM3_CHECK_EQ(encoded.value().data[b], expected.bytes[b]);
    }
}
KM3_TEST(decode_all_mappings_from_handwritten_wire_bytes) {
    const auto c = Config();
    for (unsigned i = 0; i < 8; ++i) {
        const auto& mapping = i < 4 ? c.rpdo[i] : c.tpdo[i - 4];
        const auto& expected = kWireFixtures[i];
        km3::CanFrame frame{};
        frame.id = mapping.cob_id;
        frame.dlc = expected.length;
        std::copy(expected.bytes.begin(), expected.bytes.end(), frame.data.begin());
        const auto decoded = km3::canopen::DecodePdo(mapping, frame);
        KM3_REQUIRE(decoded.ok());
        KM3_CHECK_EQ(decoded.value().count, expected.count);
        KM3_CHECK_EQ(decoded.value().values, expected.values);
    }
}
KM3_TEST(mapping_count_width_flags_and_values_are_rejected) {
    auto m = Config().rpdo[0];
    auto v = Batch(Config()).values[0];
    v.values[1] = 256;
    KM3_CHECK(!km3::canopen::EncodePdo(m, v));
    for (auto count : {0, 9, 255}) {
        m.entry_count = static_cast<std::uint8_t>(count);
        KM3_CHECK(!km3::canopen::PdoLength(m));
    }
    m = Config().rpdo[0];
    m.entries[0] = 0x6040000f;
    KM3_CHECK(!km3::canopen::PdoLength(m));
    m.entry_count = 3;
    m.entries = {0x60400020, 0x60600020, 0x60700008};
    KM3_CHECK(!km3::canopen::PdoLength(m));
    m = Config().rpdo[0];
    v.values[1] = 0;
    const auto frame = km3::canopen::EncodePdo(m, v).value();
    for (unsigned flag = 0; flag < 4; ++flag) {
        auto f = frame;
        if (flag == 0)
            f.fd = true;
        if (flag == 1)
            f.remote = true;
        if (flag == 2)
            f.extended = true;
        if (flag == 3)
            f.bitrate_switch = true;
        KM3_CHECK(!km3::canopen::DecodePdo(m, f));
    }
}
KM3_TEST(rpdo_completion_precedes_sync_and_empty_tpdo_is_normal) {
    Probe can;
    const auto c = Config();
    km3::canopen::PdoSession session(can, c);
    KM3_REQUIRE(session.Validate().ok());
    auto batch = Batch(c);
    KM3_REQUIRE(session.Submit(batch).ok());
    now = 0;
    km3::canopen::SyncProducer sync(can, 4000, &Clock, c.sync_cob_id, &session);
    KM3_REQUIRE(sync.Start().ok());
    now = 4000;
    sync.Tick();
    KM3_CHECK_EQ(can.calls, 5u);
    KM3_CHECK_EQ(can.confirmed, 5u);
    KM3_CHECK_EQ(can.unconfirmed, 0u);
    for (unsigned i = 0; i < 4; ++i) KM3_CHECK_EQ(can.ids[i], c.rpdo[i].cob_id);
    KM3_CHECK_EQ(can.ids[4], c.sync_cob_id);
    for (unsigned i = 0; i < 100; ++i) session.ReceiveAvailable();
    KM3_CHECK_EQ(session.Stats().rx_errors, 0u);
    KM3_CHECK_EQ(can.blocking, 0u);
    km3::canopen::TpdoSample sample{};
    KM3_CHECK(!session.Pop(sample));
}
KM3_TEST(partial_failed_or_overdue_batch_never_allows_a_later_sync) {
    for (unsigned fail = 1; fail <= 5; ++fail) {
        Probe can;
        can.fail_at = fail;
        const auto c = Config();
        km3::canopen::PdoSession session(can, c);
        KM3_REQUIRE(session.Submit(Batch(c)).ok());
        now = 0;
        km3::canopen::SyncProducer sync(can, 4000, &Clock, c.sync_cob_id, &session);
        KM3_REQUIRE(sync.Start().ok());
        now = 4000;
        sync.Tick();
        KM3_CHECK(!sync.Active());
        for (unsigned i = 0; i < 10; ++i) {
            now += 4000;
            sync.Tick();
        }
        KM3_CHECK_EQ(can.calls, fail);
    }
    Probe can;
    can.advance = 1500;
    const auto c = Config();
    km3::canopen::PdoSession session(can, c);
    KM3_REQUIRE(session.Submit(Batch(c)).ok());
    now = 0;
    km3::canopen::SyncProducer sync(can, 4000, &Clock, c.sync_cob_id, &session);
    KM3_REQUIRE(sync.Start().ok());
    now = 4000;
    sync.Tick();
    KM3_CHECK(!sync.Active());
    KM3_CHECK_EQ(can.calls, 4u);
    now += 4000;
    sync.Tick();
    KM3_CHECK_EQ(can.calls, 4u);
}
KM3_TEST(tpdo_receive_queue_overflow_and_no_allocations_on_runtime_paths) {
    Probe can;
    const auto c = Config();
    km3::canopen::PdoSession session(can, c);
    const auto b = Batch(c);
    can.rx = km3::canopen::EncodePdo(c.tpdo[1], b.values[1]).value();
    allocations.store(0);
    measuring.store(true);
    bool ok = true;
    for (unsigned i = 0; i < 1000; ++i) {
        ok = ok && session.Submit(b).ok();
        ok = ok && session.BeforeSync(km3::MonotonicUs() + 100000).ok();
        can.rx_count = 1;
        session.ReceiveAvailable();
    }
    measuring.store(false);
    KM3_CHECK(ok);
    KM3_CHECK_EQ(allocations.load(), 0u);
    KM3_CHECK_EQ(session.FeedbackDrops(), 936u);
    KM3_CHECK_EQ(session.Stats().tpdo_received, 1000u);
    km3::canopen::TpdoSample sample{};
    unsigned count = 0;
    while (session.Pop(sample)) {
        ++count;
        KM3_CHECK_EQ(sample.cob_id, c.tpdo[1].cob_id);
    }
    KM3_CHECK_EQ(count, 64u);
}
KM3_TEST(model_factory_preserves_od_guard_and_config_collisions_are_refused) {
    KM3_CHECK(!km3::pmp4010::MakePdoConfig(7, km3::RtParams{}));
    Probe can;
    auto c = Config();
    c.tpdo[0].cob_id = c.rpdo[0].cob_id;
    km3::canopen::PdoSession bad(can, c);
    KM3_CHECK(!bad.Validate());
    can.supports = false;
    km3::canopen::PdoSession unsupported(can, Config());
    KM3_CHECK(!unsupported.Validate());
}
KM3_TEST(worker_uses_same_non_rt_owner_and_spsc_paths_allocate_nothing) {
    Probe can;
    const auto c = Config();
    km3::canopen::PdoSession session(can, c);
    const auto b = Batch(c);
    km3::canopen::SyncWorker worker(can, 10000, c.sync_cob_id, &session);
    allocations.store(0);
    measuring.store(true);
    const auto started = worker.Start();
    bool submitted = true;
    for (unsigned i = 0; i < 8; ++i) submitted = session.Submit(b).ok() && submitted;
    ::poll(nullptr, 0, 35);
    worker.Stop();
    measuring.store(false);
    KM3_REQUIRE(started.ok());
    KM3_CHECK(submitted);
    KM3_CHECK_EQ(allocations.load(), 0u);
    KM3_CHECK_EQ(worker.ActualPolicy(), SCHED_OTHER);
    KM3_CHECK(can.calls >= 5);
    KM3_CHECK_EQ(can.unconfirmed, 0u);
    KM3_CHECK_EQ(can.ids[0], c.rpdo[0].cob_id);
    KM3_CHECK_EQ(can.ids[4], c.sync_cob_id);
}
KM3_TEST_MAIN()
