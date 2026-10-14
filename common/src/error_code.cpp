#include "km3/error_code.hpp"

namespace km3 {

const char* ToString(ErrorCode code) {
    switch (code) {
        case ErrorCode::Ok:                   return "EOK";
        case ErrorCode::EInternal:            return "EINTERNAL";
        case ErrorCode::EInvalidRequest:      return "EINVALID_REQUEST";
        case ErrorCode::EUnauthorized:        return "EUNAUTHORIZED";
        case ErrorCode::EPermission:          return "EPERMISSION";
        case ErrorCode::ENotFound:            return "ENOT_FOUND";
        case ErrorCode::ERateLimit:           return "ERATE_LIMIT";
        case ErrorCode::EDeviceOffline:       return "EDEVICE_OFFLINE";
        case ErrorCode::EDeviceBusy:          return "EDEVICE_BUSY";
        case ErrorCode::EDeviceError:         return "EDEVICE_ERROR";
        case ErrorCode::ECommandTimeout:      return "ECOMMAND_TIMEOUT";
        case ErrorCode::ECommandRejected:     return "ECOMMAND_REJECTED";
        case ErrorCode::EBusyLocalAuthority:  return "EBUSY_LOCAL_AUTHORITY";
        case ErrorCode::EFirmwareNotFound:    return "EFIRMWARE_NOT_FOUND";
        case ErrorCode::EIncompatibleVersion: return "EINCOMPATIBLE_VERSION";
        case ErrorCode::EDownloadFailed:      return "EDOWNLOAD_FAILED";
        case ErrorCode::EInstallFailed:       return "EINSTALL_FAILED";
        case ErrorCode::EVerifyFailed:        return "EVERIFY_FAILED";
        case ErrorCode::ERollbackDenied:      return "EROLLBACK_DENIED";
        case ErrorCode::EInvalidConfig:       return "EINVALID_CONFIG";
        case ErrorCode::EConfigConflict:      return "ECONFIG_CONFLICT";
        case ErrorCode::EInviteExpired:       return "EINVITE_EXPIRED";
        case ErrorCode::EInviteUsed:          return "EINVITE_USED";
        case ErrorCode::EEmailExists:         return "EEMAIL_EXISTS";
        case ErrorCode::ETokenInvalid:        return "ETOKEN_INVALID";
    }
    return "EUNKNOWN";
}

}  // namespace km3
