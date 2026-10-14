import { defineStore } from 'pinia'
import { ref } from 'vue'
import { environmentsApi } from '@/api/environments'
import type { Environment } from '@/api/types'

export const useEnvironmentStore = defineStore('environment', () => {
  const available = ref<Environment[]>([])
  const current = ref<Environment | null>(null)
  const error = ref<string | null>(null)

  // 刻意**不持久化**到 localStorage / sessionStorage。见 管理端Web程序架构.md §六：
  // 一个能跨刷新存活的"当前环境"，最容易在操作员以为还在演示环境时把固件推到生产。
  // 刷新后重新选择，是一次廉价但有效的停顿。

  async function load(): Promise<void> {
    error.value = null
    try {
      available.value = await environmentsApi.list()
      // 只有一个可选环境时直接选中，省掉一次无意义的点击。
      const first = available.value[0]
      if (available.value.length === 1 && first) {
        current.value = first
      }
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载环境列表失败'
      available.value = []
      current.value = null
    }
  }

  /** 切换环境。**仅 superadmin**（权限矩阵"跨环境切换"一行）。 */
  function select(env: Environment): void {
    current.value = env
  }

  return { available, current, error, load, select }
})
