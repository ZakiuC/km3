#include "pmp4010_velocity.hpp"
#include "pmp4010_velocity_c.h"
#include "velocity_vectors_generated.hpp"
#include "test_harness.hpp"
#include <array>
#include <cstring>
#include <limits>
using namespace km3::pmp4010;
KM3_TEST(one_shared_exact_vector_set_exercises_cpp_and_c_abi) {
    for (const auto& test : velocity_vectors::cases) {
        const VelocityCalibration cal{test.calibration[0], test.calibration[1], test.calibration[2], test.calibration[3]};
        VelocityFraction result;
        const auto error = ConvertVelocity(static_cast<VelocityDirection>(test.direction), test.numerator, test.denominator, cal, result);
        KM3_CHECK_EQ(static_cast<int>(error), test.error);
        char n[64]{}, d[64]{};
        KM3_CHECK_EQ(pmp4010_velocity_convert(test.direction, test.numerator, test.denominator,
            test.calibration[0], test.calibration[1], test.calibration[2], test.calibration[3], n, sizeof(n), d, sizeof(d)), test.error);
        if (test.error == 0) {
            KM3_CHECK_EQ(result.numerator, std::string(test.expected_numerator));
            KM3_CHECK_EQ(result.denominator, std::string(test.expected_denominator));
            KM3_CHECK_EQ(std::string(n), result.numerator);
            KM3_CHECK_EQ(std::string(d), result.denominator);
            if (test.int32_error >= 0) {
                std::int32_t integer = 123;
                KM3_CHECK_EQ(pmp4010_velocity_require_int32(n, d, &integer), test.int32_error);
                if (test.int32_error != 0) KM3_CHECK_EQ(integer, 123);
            }
            std::printf("VECTOR %s = %s/%s; Int32 rc=%d\n", test.name, n, d, test.int32_error);
        } else std::printf("VECTOR %s rc=%d\n", test.name, test.error);
    }
}
KM3_TEST(integer_user_velocities_roundtrip_including_extreme_uint32_calibrations) {
    constexpr auto maximum = std::numeric_limits<std::uint32_t>::max();
    const VelocityCalibration calibrations[] = {{10000,1,1,1}, {131072,1,1,1}, {10000,1,3,1},
        {1234567,17,33,19}, {1,maximum,maximum,1}, {maximum,1,1,maximum}, {maximum,maximum,maximum,maximum}};
    const std::int32_t values[] = {std::numeric_limits<std::int32_t>::min(),-1000001,-65536,-1,0,1,65536,1000001,std::numeric_limits<std::int32_t>::max()};
    for (const auto& cal : calibrations) for (const auto value : values) {
        VelocityFraction rpm, units;
        KM3_REQUIRE(ConvertVelocity(VelocityDirection::UnitsToRpm, std::to_string(value), "1", cal, rpm) == VelocityError::Ok);
        KM3_REQUIRE(ConvertVelocity(VelocityDirection::RpmToUnits, rpm.numerator, rpm.denominator, cal, units) == VelocityError::Ok);
        KM3_CHECK_EQ(units.numerator, std::to_string(value));
        KM3_CHECK_EQ(units.denominator, std::string("1"));
    }
}
KM3_TEST(c_abi_validation_never_changes_outputs_on_failure) {
    KM3_CHECK_EQ(pmp4010_velocity_abi_version(), 1u);
    char n[64] = "unchanged", d[64] = "unchanged";
    for (const char* invalid : {"", "nan", "inf", "1.2", "1e2", " 1", "1 ", "+", "--1"}) {
        KM3_CHECK_EQ(pmp4010_velocity_convert(0, invalid, "1", 10000,1,1,1,n,sizeof(n),d,sizeof(d)), 1);
        KM3_CHECK_EQ(std::string(n), std::string("unchanged"));
        KM3_CHECK_EQ(std::string(d), std::string("unchanged"));
    }
    KM3_CHECK_EQ(pmp4010_velocity_convert(2,"1","1",10000,1,1,1,n,sizeof(n),d,sizeof(d)),1);
    KM3_CHECK_EQ(pmp4010_velocity_convert(0,nullptr,"1",10000,1,1,1,n,sizeof(n),d,sizeof(d)),1);
    KM3_CHECK_EQ(pmp4010_velocity_convert(0,"1","0",10000,1,1,1,n,sizeof(n),d,sizeof(d)),1);
    KM3_CHECK_EQ(pmp4010_velocity_convert(0,"1","1",10000,1,1,1,nullptr,0,d,sizeof(d)),1);
    KM3_CHECK_EQ(pmp4010_velocity_convert(0,"50000","1",10000,1,1,1,n,3,d,sizeof(d)),3);
    KM3_CHECK_EQ(std::string(n), std::string("unchanged"));
    KM3_CHECK_EQ(std::string(d), std::string("unchanged"));
    std::int32_t result = 987;
    KM3_CHECK_EQ(pmp4010_velocity_require_int32("nan","1",&result),1);
    KM3_CHECK_EQ(pmp4010_velocity_require_int32("1","0",&result),1);
    KM3_CHECK_EQ(pmp4010_velocity_require_int32("1","1",nullptr),1);
    KM3_CHECK_EQ(result,987);
}
KM3_TEST(signed_exact_rpm_limits_do_not_overflow_or_round_down) {
    const VelocityCalibration cal{10000,1,1,1};
    KM3_CHECK(UserVelocityWithinRpmLimit(50000,cal,300) == VelocityError::Ok);
    KM3_CHECK(UserVelocityWithinRpmLimit(-50000,cal,300) == VelocityError::Ok);
    KM3_CHECK(UserVelocityWithinRpmLimit(50001,cal,300) == VelocityError::Overflow);
    KM3_CHECK(UserVelocityWithinRpmLimit(-50001,cal,300) == VelocityError::Overflow);
    KM3_CHECK(UserVelocityWithinRpmLimit(0,cal,0) == VelocityError::Ok);
    KM3_CHECK(UserVelocityWithinRpmLimit(1,cal,0) == VelocityError::Overflow);
    KM3_CHECK(UserVelocityWithinRpmLimit(std::numeric_limits<std::int32_t>::min(),cal,12884902) == VelocityError::Ok);
    KM3_CHECK(UserVelocityWithinRpmLimit(std::numeric_limits<std::int32_t>::min(),cal,12884901) == VelocityError::Overflow);
}
KM3_TEST_MAIN()
