import { http } from './client'
import type { Firmware, OtaDeployment, Paged } from './types'

export const otaApi = {
  firmwareList: (envId: number, query?: { page?: number; size?: number }) =>
    http.get<Paged<Firmware>>('/firmware', { env_id: envId, ...query }),
  /** 下发。服务端会校验 env_id 一致性与 min_version 兼容（服务器端程序架构.md §4.8）。 */
  deploy: (firmwareId: number, deviceIds: string[]) =>
    http.post<OtaDeployment[]>(`/firmware/${firmwareId}/deploy`, { device_ids: deviceIds }),
  deviceDeployments: (deviceId: string) =>
    http.get<Paged<OtaDeployment>>(`/devices/${deviceId}/ota`),
  cancel: (deviceId: string) => http.post<null>(`/devices/${deviceId}/ota/cancel`),
}
