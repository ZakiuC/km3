#include "pmp4010_velocity.hpp"
#include <array>
#include <limits>
#include <utility>

namespace km3::pmp4010 {
namespace {
#if !defined(__SIZEOF_INT128__)
#error "PMP4010 exact velocity conversion requires compiler unsigned 128-bit integers"
#endif
__extension__ typedef unsigned __int128 Wide;
constexpr Wide kWideMax = ~Wide{0};
struct Ratio { Wide numerator = 0, denominator = 1; bool negative = false; };
Wide Gcd(Wide a, Wide b) noexcept {
    while (b != 0) { const auto next = a % b; a = b; b = next; }
    return a;
}
VelocityError Parse(std::string_view text, Wide& magnitude, bool& negative) noexcept {
    if (text.empty()) return VelocityError::InvalidArgument;
    negative = text.front() == '-';
    if (text.front() == '-' || text.front() == '+') text.remove_prefix(1);
    if (text.empty()) return VelocityError::InvalidArgument;
    magnitude = 0;
    for (const char c : text) {
        if (c < '0' || c > '9') return VelocityError::InvalidArgument;
        const auto digit = static_cast<unsigned>(c - '0');
        if (magnitude > (kWideMax - digit) / 10) return VelocityError::Overflow;
        magnitude = magnitude * 10 + digit;
    }
    return VelocityError::Ok;
}
VelocityError ParseRatio(std::string_view numerator, std::string_view denominator, Ratio& out) noexcept {
    bool numerator_negative = false, denominator_negative = false;
    auto error = Parse(numerator, out.numerator, numerator_negative);
    if (error != VelocityError::Ok) return error;
    error = Parse(denominator, out.denominator, denominator_negative);
    if (error != VelocityError::Ok) return error;
    if (out.denominator == 0) return VelocityError::InvalidArgument;
    const auto gcd = Gcd(out.numerator, out.denominator);
    out.numerator /= gcd; out.denominator /= gcd;
    out.negative = out.numerator != 0 && numerator_negative != denominator_negative;
    return VelocityError::Ok;
}
std::string Decimal(Wide value) {
    char buffer[40];
    auto* end = buffer + sizeof(buffer);
    auto* begin = end;
    do { *--begin = static_cast<char>('0' + static_cast<unsigned>(value % 10)); value /= 10; } while (value != 0);
    return std::string(begin, end);
}
bool Multiply(Wide& value, Wide factor) noexcept {
    if (factor != 0 && value > kWideMax / factor) return false;
    value *= factor;
    return true;
}
VelocityError Convert(VelocityDirection direction, Ratio& value, const VelocityCalibration& cal) noexcept {
    if (direction != VelocityDirection::UnitsToRpm && direction != VelocityDirection::RpmToUnits)
        return VelocityError::InvalidArgument;
    if (cal.encoder_increments == 0 || cal.encoder_motor_revolutions == 0 ||
        cal.gear_motor_revolutions == 0 || cal.gear_output_revolutions == 0)
        return VelocityError::InvalidCalibration;
    // E06 section 5.2 supplies ONLY the forward units/s -> motor rpm formula.
    // The reverse direction here is its algebraic inverse, not a manual quote.
    std::array<Wide, 4> numerators{value.numerator, cal.gear_motor_revolutions,
        cal.encoder_motor_revolutions, 60};
    std::array<Wide, 4> denominators{value.denominator, cal.gear_output_revolutions,
        cal.encoder_increments, 1};
    if (direction == VelocityDirection::RpmToUnits) {
        numerators = {value.numerator, cal.encoder_increments, cal.gear_output_revolutions, 1};
        denominators = {value.denominator, cal.gear_motor_revolutions, cal.encoder_motor_revolutions, 60};
    }
    for (auto& n : numerators) for (auto& d : denominators) {
        const auto gcd = Gcd(n, d); n /= gcd; d /= gcd;
    }
    value.numerator = value.denominator = 1;
    for (const auto factor : numerators) if (!Multiply(value.numerator, factor)) return VelocityError::Overflow;
    for (const auto factor : denominators) if (!Multiply(value.denominator, factor)) return VelocityError::Overflow;
    return VelocityError::Ok;
}
}  // namespace
VelocityError ConvertVelocity(VelocityDirection direction, std::string_view numerator,
    std::string_view denominator, const VelocityCalibration& cal, VelocityFraction& result) noexcept {
    try {
        Ratio value;
        auto error = ParseRatio(numerator, denominator, value);
        if (error != VelocityError::Ok) return error;
        error = Convert(direction, value, cal);
        if (error != VelocityError::Ok) return error;
        VelocityFraction converted{(value.negative ? "-" : "") + Decimal(value.numerator), Decimal(value.denominator)};
        result = std::move(converted);
        return VelocityError::Ok;
    } catch (...) { return VelocityError::InternalError; }
}
VelocityError RequireVelocityInt32(std::string_view numerator, std::string_view denominator,
    std::int32_t& result) noexcept {
    Ratio value;
    const auto error = ParseRatio(numerator, denominator, value);
    if (error != VelocityError::Ok) return error;
    if (value.denominator != 1) return VelocityError::NonIntegral;
    const Wide limit = value.negative ? Wide{2147483648u} : Wide{2147483647u};
    if (value.numerator > limit) return VelocityError::Overflow;
    result = value.negative ? static_cast<std::int32_t>(-static_cast<std::int64_t>(value.numerator)) :
        static_cast<std::int32_t>(value.numerator);
    return VelocityError::Ok;
}
VelocityError UserVelocityWithinRpmLimit(std::int32_t units, const VelocityCalibration& cal,
    std::uint32_t maximum) noexcept {
    const auto signed_units = static_cast<std::int64_t>(units);
    Ratio value{static_cast<Wide>(signed_units < 0 ? -signed_units : signed_units), 1, false};
    const auto error = Convert(VelocityDirection::UnitsToRpm, value, cal);
    if (error != VelocityError::Ok) return error;
    // Compare without a potentially overflowing maximum * denominator product.
    const auto quotient = value.numerator / value.denominator;
    return quotient > maximum || (quotient == maximum && value.numerator % value.denominator != 0) ?
        VelocityError::Overflow : VelocityError::Ok;
}
}  // namespace km3::pmp4010
