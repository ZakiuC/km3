#include "km3/hal/device.hpp"

namespace km3 {

std::string_view ToString(DeviceKind kind) {
    switch (kind) {
        case DeviceKind::Can:    return "can";
        case DeviceKind::Rs485:  return "rs485";
        case DeviceKind::Video:  return "video";
        case DeviceKind::Gpio:   return "gpio";
        case DeviceKind::Unknown: break;
    }
    return "unknown";
}

std::string_view ToString(DeviceStatusEnum status) {
    switch (status) {
        case DeviceStatusEnum::Closed:  return "closed";
        case DeviceStatusEnum::Open:    return "open";
        case DeviceStatusEnum::BusOff:  return "bus-off";
        case DeviceStatusEnum::Error:   return "error";
        case DeviceStatusEnum::Unknown: break;
    }
    return "unknown";
}

}  // namespace km3
