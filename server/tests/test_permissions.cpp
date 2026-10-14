#include "km3/server/permission.hpp"
#include "test_harness.hpp"

using km3::server::Action;
using km3::server::HasPermission;
using km3::server::Role;
using km3::server::RoleFromString;

KM3_TEST(viewer_can_read_but_not_control) {
    KM3_CHECK(HasPermission(Role::Viewer, Action::ViewDeviceStatus));
    KM3_CHECK(HasPermission(Role::Viewer, Action::ViewVideo));
    KM3_CHECK(!HasPermission(Role::Viewer, Action::SendControlCommand));
    KM3_CHECK(!HasPermission(Role::Viewer, Action::EditLimitedConfig));
    KM3_CHECK(!HasPermission(Role::Viewer, Action::UseDebugEntry));
}

KM3_TEST(operator_controls_but_cannot_manage) {
    KM3_CHECK(HasPermission(Role::Operator, Action::SendControlCommand));
    KM3_CHECK(HasPermission(Role::Operator, Action::EditLimitedConfig));
    KM3_CHECK(!HasPermission(Role::Operator, Action::EditSafetyConfig));
    KM3_CHECK(!HasPermission(Role::Operator, Action::ManageFirmware));
    KM3_CHECK(!HasPermission(Role::Operator, Action::ManageUsers));
    // F08 隐藏功能/调试入口对 operator 不可见
    KM3_CHECK(!HasPermission(Role::Operator, Action::UseDebugEntry));
}

KM3_TEST(admin_manages_but_cannot_switch_environment) {
    KM3_CHECK(HasPermission(Role::Admin, Action::ManageUsers));
    KM3_CHECK(HasPermission(Role::Admin, Action::ManageFirmware));
    KM3_CHECK(HasPermission(Role::Admin, Action::EditSafetyConfig));
    KM3_CHECK(HasPermission(Role::Admin, Action::UseDebugEntry));
    KM3_CHECK(!HasPermission(Role::Admin, Action::SwitchEnvironment));
}

KM3_TEST(superadmin_has_everything) {
    const Action all[] = {
        Action::ViewDeviceStatus, Action::ViewVideo, Action::SendControlCommand,
        Action::EditLimitedConfig, Action::EditSafetyConfig, Action::ManageFirmware,
        Action::ManageUsers, Action::ViewAuditLog, Action::UseDebugEntry,
        Action::SwitchEnvironment,
    };
    for (Action a : all) {
        KM3_CHECK(HasPermission(Role::SuperAdmin, a));
    }
}

KM3_TEST(unknown_role_string_defaults_to_viewer) {
    // 未知角色必须退化为最小权限，而不是最大权限。
    KM3_CHECK(RoleFromString("nonexistent") == Role::Viewer);
    KM3_CHECK(RoleFromString("") == Role::Viewer);
    KM3_CHECK(RoleFromString("operator") == Role::Operator);
}

KM3_TEST_MAIN()
