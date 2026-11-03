#include "pmp4010_velocity_c.h"
#include "pmp4010_velocity.hpp"
#include <cstring>
using namespace km3::pmp4010;
static_assert(static_cast<int>(VelocityError::Overflow) == PMP4010_VELOCITY_OVERFLOW);
extern "C" uint32_t pmp4010_velocity_abi_version(void) { return 1; }
extern "C" int pmp4010_velocity_convert(int direction, const char* numerator, const char* denominator,
    uint32_t increments, uint32_t encoder_revs, uint32_t motor_revs, uint32_t output_revs,
    char* out_num, size_t num_capacity, char* out_den, size_t den_capacity) {
    if (!numerator || !denominator || !out_num || !out_den) return PMP4010_VELOCITY_INVALID_ARGUMENT;
    VelocityFraction result;
    const auto error = ConvertVelocity(static_cast<VelocityDirection>(direction), numerator, denominator,
        {increments, encoder_revs, motor_revs, output_revs}, result);
    if (error != VelocityError::Ok) return static_cast<int>(error);
    if (result.numerator.size() >= num_capacity || result.denominator.size() >= den_capacity)
        return PMP4010_VELOCITY_BUFFER_TOO_SMALL;
    std::memcpy(out_num, result.numerator.c_str(), result.numerator.size() + 1);
    std::memcpy(out_den, result.denominator.c_str(), result.denominator.size() + 1);
    return PMP4010_VELOCITY_OK;
}
extern "C" int pmp4010_velocity_require_int32(const char* numerator, const char* denominator, int32_t* out) {
    if (!numerator || !denominator || !out) return PMP4010_VELOCITY_INVALID_ARGUMENT;
    return static_cast<int>(RequireVelocityInt32(numerator, denominator, *out));
}
