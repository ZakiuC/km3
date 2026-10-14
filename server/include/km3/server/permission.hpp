#pragma once
// 权限矩阵。docs/架构设计/服务器端程序架构.md §4.4 是权威定义。
//
// 早期文档只调用了 hasPermission 却从未定义它（编译不过），并且用单值字符串
// 比较代替权限矩阵。这里把矩阵变成数据，并把判定收敛到一个函数。

#include <string_view>

namespace km3::server {

enum class Role { SuperAdmin, Admin, Operator, Viewer };

enum class Action {
    ViewDeviceStatus,
    ViewVideo,
    SendControlCommand,
    EditLimitedConfig,
    EditSafetyConfig,
    ManageFirmware,
    ManageUsers,
    ViewAuditLog,
    UseDebugEntry,
    SwitchEnvironment,
};

Role RoleFromString(std::string_view name);
std::string_view ToString(Role role);

// 唯一判定入口。不得在业务代码里散落 if 判断。
bool HasPermission(Role role, Action action);

}  // namespace km3::server
