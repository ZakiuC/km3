#include <cstring>
#include "km3/error_code.hpp"
#include "km3/result.hpp"
#include "test_harness.hpp"

KM3_TEST(error_codes_are_stable_and_unique) {
    KM3_CHECK_EQ(static_cast<int>(km3::ErrorCode::ECommandTimeout), 2004);
    KM3_CHECK_EQ(static_cast<int>(km3::ErrorCode::EBusyLocalAuthority), 2006);
    KM3_CHECK_EQ(static_cast<int>(km3::ErrorCode::EVerifyFailed), 3005);
    KM3_CHECK_EQ(static_cast<int>(km3::ErrorCode::EConfigConflict), 4002);
}

KM3_TEST(error_codes_serialize_to_protocol_names) {
    // 协议规范 §六 规定 REST 直接序列化枚举名。
    KM3_CHECK(std::strcmp(km3::ToString(km3::ErrorCode::EBusyLocalAuthority),
                          "EBUSY_LOCAL_AUTHORITY") == 0);
    KM3_CHECK(std::strcmp(km3::ToString(km3::ErrorCode::EVerifyFailed),
                          "EVERIFY_FAILED") == 0);
    KM3_CHECK(std::strcmp(km3::ToString(km3::ErrorCode::Ok), "EOK") == 0);
}

KM3_TEST(result_carries_code_and_value) {
    auto ok = km3::Result<int>::Ok(42);
    KM3_CHECK(ok.ok());
    KM3_CHECK_EQ(ok.value(), 42);

    auto bad = km3::Result<int>::Err(km3::ErrorCode::EDeviceOffline);
    KM3_CHECK(!bad.ok());
    KM3_CHECK(bad.code() == km3::ErrorCode::EDeviceOffline);

    km3::Status s = km3::Status::Ok();
    KM3_CHECK(s.ok());
    km3::Status e(km3::ErrorCode::EInvalidConfig);
    KM3_CHECK(!e.ok());
}

KM3_TEST_MAIN()
