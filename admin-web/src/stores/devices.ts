import { defineStore } from 'pinia'
import { ref } from 'vue'
import { devicesApi } from '@/api/devices'
import type { Device } from '@/api/types'
import { useEnvironmentStore } from './environment'

export const useDevicesStore = defineStore('devices', () => {
  const items = ref<Device[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const selectedId = ref<number | null>(null)

  /** 始终按当前环境过滤——不存在"默认全部环境"的视图（§六）。 */
  async function load(page = 1, size = 50): Promise<void> {
    const env = useEnvironmentStore()
    if (!env.current) {
      items.value = []
      total.value = 0
      return
    }
    loading.value = true
    error.value = null
    try {
      const res = await devicesApi.list(env.current.id, { page, size })
      items.value = res.items
      total.value = res.total
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载设备列表失败'
      items.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  return { items, total, loading, error, selectedId, load }
})
