#include "km3/rt_types.hpp"

#include <cstring>

namespace km3 {

DeviceTag DeviceTag::From(const char* src) noexcept {
    DeviceTag tag;
    if (src == nullptr) {
        return tag;
    }
    // 有意截断：定长字段，保证可平凡拷贝。保证以 NUL 结尾。
    std::strncpy(tag.name, src, kLen - 1);
    tag.name[kLen - 1] = '\0';
    return tag;
}

}  // namespace km3
