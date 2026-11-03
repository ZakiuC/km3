#ifndef PMP4010_VELOCITY_C_H
#define PMP4010_VELOCITY_C_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* ABI 1: pure arithmetic only. No CAN, OD override or device dependency.
 * Decimal signed numerator/denominator magnitudes <= 2^128-1; denominator
 * cannot be zero. Output is reduced, denominator positive. Never rounds.
 * All buffers include a trailing NUL. Failure leaves outputs unchanged.
 */
enum pmp4010_velocity_error {
    PMP4010_VELOCITY_OK = 0, PMP4010_VELOCITY_INVALID_ARGUMENT = 1,
    PMP4010_VELOCITY_INVALID_CALIBRATION = 2, PMP4010_VELOCITY_BUFFER_TOO_SMALL = 3,
    PMP4010_VELOCITY_INTERNAL_ERROR = 4, PMP4010_VELOCITY_NONINTEGRAL = 5,
    PMP4010_VELOCITY_OVERFLOW = 6
};
uint32_t pmp4010_velocity_abi_version(void);
/* direction: 0 = user units/s -> motor rpm; 1 = algebraic inverse. */
int pmp4010_velocity_convert(int direction, const char* numerator, const char* denominator,
    uint32_t encoder_increments, uint32_t encoder_motor_revolutions,
    uint32_t gear_motor_revolutions, uint32_t gear_output_revolutions,
    char* out_num, size_t out_num_capacity, char* out_den, size_t out_den_capacity);
int pmp4010_velocity_require_int32(const char* numerator, const char* denominator, int32_t* out);
#ifdef __cplusplus
}
#endif
#endif
