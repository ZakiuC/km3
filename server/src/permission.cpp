#include "km3/server/permission.hpp"

namespace km3::server {

Role RoleFromString(std::string_view name) {
    if (name == "superadmin") return Role::SuperAdmin;
    if (name == "admin")      return Role::Admin;
    if (name == "operator")   return Role::Operator;
    return Role::Viewer;
}

std::string_view ToString(Role role) {
    switch (role) {
        case Role::SuperAdmin: return "superadmin";
        case Role::Admin:      return "admin";
        case Role::Operator:   return "operator";
        case Role::Viewer:     return "viewer";
    }
    return "viewer";
}

bool HasPermission(Role role, Action action) {
    switch (action) {
        // 只读能力：四类角色都具备
        case Action::ViewDeviceStatus:
        case Action::ViewVideo:
            return true;

        // 控制：viewer 不可
        case Action::SendControlCommand:
            return role != Role::Viewer;

        // 受限配置：operator 及以上
        case Action::EditLimitedConfig:
            return role != Role::Viewer;

        // 安全相关配置：仅 superadmin / admin
        case Action::EditSafetyConfig:
            return role == Role::SuperAdmin || role == Role::Admin;

        // 固件、用户、审计、调试入口：仅 superadmin / admin
        case Action::ManageFirmware:
        case Action::ManageUsers:
        case Action::ViewAuditLog:
        case Action::UseDebugEntry:
            return role == Role::SuperAdmin || role == Role::Admin;

        // 跨环境切换：仅 superadmin
        case Action::SwitchEnvironment:
            return role == Role::SuperAdmin;
    }
    return false;
}

}  // namespace km3::server
