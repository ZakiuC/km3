#pragma once
// 错误码：协议规范 §六。三种传输共用同一份定义。
namespace km3 {

enum class ErrorCode : int {
    Ok = 0,

    // 通用 1xxx
    EInternal        = 1000,
    EInvalidRequest  = 1001,
    EUnauthorized    = 1002,
    EPermission      = 1003,
    ENotFound        = 1004,
    ERateLimit       = 1005,

    // 设备 2xxx
    EDeviceOffline   = 2001,
    EDeviceBusy      = 2002,
    EDeviceError     = 2003,
    ECommandTimeout  = 2004,
    ECommandRejected = 2005,
    EBusyLocalAuthority = 2006,

    // OTA 3xxx
    EFirmwareNotFound    = 3001,
    EIncompatibleVersion = 3002,
    EDownloadFailed      = 3003,
    EInstallFailed       = 3004,
    EVerifyFailed        = 3005,
    ERollbackDenied      = 3006,

    // 配置 4xxx
    EInvalidConfig   = 4001,
    EConfigConflict  = 4002,

    // 账号 5xxx
    EInviteExpired   = 5001,
    EInviteUsed      = 5002,
    EEmailExists     = 5003,
    ETokenInvalid    = 5004,
};

const char* ToString(ErrorCode code);

}  // namespace km3
