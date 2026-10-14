import { http } from './client'
import type { Device, Paged, SensorPoint } from './types'

export const devicesApi = {
  list: (envId: number, query?: { page?: number; size?: number }) =>
    http.get<Paged<Device>>('/devices', { env_id: envId, ...query }),
  get: (id: number) => http.get<Device>(`/devices/${id}`),
  status: (id: number) => http.get<Record<string, unknown>>(`/devices/${id}/status`),
  sensorHistory: (
    id: number,
    type: string,
    query: { from: string; to: string; page?: number; size?: number },
  ) => http.get<Paged<SensorPoint>>(`/devices/${id}/sensors/${type}/history`, query),
}
