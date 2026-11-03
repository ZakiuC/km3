#pragma once
#include <cstdint>
#include <string>
#include <string_view>

namespace km3::pmp4010 {
// Pure, non-real-time arithmetic; no OD override, bus, HAL or CAN capability.
struct VelocityCalibration {
    std::uint32_t encoder_increments;
    std::uint32_t encoder_motor_revolutions;
    std::uint32_t gear_motor_revolutions;
    std::uint32_t gear_output_revolutions;
};
enum class VelocityError : int {
    Ok = 0, InvalidArgument = 1, InvalidCalibration = 2, BufferTooSmall = 3,
    InternalError = 4, NonIntegral = 5, Overflow = 6,
};
enum class VelocityDirection : int { UnitsToRpm = 0, RpmToUnits = 1 };
struct VelocityFraction { std::string numerator; std::string denominator; };
// Decimal integer numerator/denominator magnitudes and the reduced result must
// fit unsigned 128 bits. Cross-cancellation precedes checked multiplication.
// This covers ALL Int32 user velocities and positive Uint32 calibrations,
// including their exact rpm result and inverse. No rounding is performed.
VelocityError ConvertVelocity(VelocityDirection direction, std::string_view numerator,
    std::string_view denominator, const VelocityCalibration& calibration,
    VelocityFraction& result) noexcept;
VelocityError RequireVelocityInt32(std::string_view numerator, std::string_view denominator,
    std::int32_t& result) noexcept;
VelocityError UserVelocityWithinRpmLimit(std::int32_t units_per_second,
    const VelocityCalibration& calibration, std::uint32_t max_motor_rpm) noexcept;
}  // namespace km3::pmp4010
