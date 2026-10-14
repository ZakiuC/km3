<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { analyticsApi, type Overview } from '@/api/analytics'
import { useDevicesStore } from '@/stores/devices'
import { useEnvironmentStore } from '@/stores/environment'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

const env = useEnvironmentStore()
const devices = useDevicesStore()
const router = useRouter()

const overview = ref<Overview | null>(null)
const error = ref<string | null>(null)

async function load(): Promise<void> {
  error.value = null
  await devices.load()
  if (devices.error) {
    error.value = devices.error
    return
  }
  if (!env.current) return
  try {
    overview.value = await analyticsApi.overview(env.current.id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载总览失败'
  }
}

onMounted(load)
// 切换环境必须重新取数。若不监听，页面会停留在上一个环境的数字上，
// 而横幅已经显示新环境——两者不一致时，人看到的是横幅，用的是旧数据。
watch(() => env.current?.id, load)

const STATUS_TEXT: Record<string, string> = {
  online: '在线',
  offline: '离线',
  error: '故障',
}
</script>

<template>
  <div class="page">
    <h3>总览</h3>

    <ErrorState v-if="error" :message="error" @retry="load" />

    <template v-else>
      <el-row :gutter="16">
        <el-col :span="6">
          <el-card><div class="kpi">{{ overview?.devices_online ?? '—' }}</div><div class="kpi-label">在线设备</div></el-card>
        </el-col>
        <el-col :span="6">
          <el-card><div class="kpi">{{ overview?.devices_total ?? devices.total }}</div><div class="kpi-label">设备总数</div></el-card>
        </el-col>
        <el-col :span="6">
          <el-card><div class="kpi">{{ overview?.alarms_recent ?? '—' }}</div><div class="kpi-label">近期告警</div></el-card>
        </el-col>
        <el-col :span="6">
          <el-card><div class="kpi">{{ overview?.ota_in_progress ?? '—' }}</div><div class="kpi-label">进行中升级</div></el-card>
        </el-col>
      </el-row>

      <h4>设备</h4>
      <EmptyState v-if="!devices.loading && devices.items.length === 0" description="当前环境没有设备" />
      <el-table v-else v-loading="devices.loading" :data="devices.items" @row-click="(r: any) => router.push(`/devices/${r.id}/sensors`)">
        <el-table-column prop="device_id" label="编号" width="160" />
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="model" label="型号" width="140" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">{{ STATUS_TEXT[row.status] ?? row.status }}</template>
        </el-table-column>
        <el-table-column prop="firmware_version" label="固件版本" width="140" />
        <el-table-column prop="last_seen_at" label="最后在线" width="200" />
      </el-table>
    </template>
  </div>
</template>

<style scoped>
.kpi { font-size: 26px; font-weight: 600; }
.kpi-label { color: #909399; font-size: 13px; margin-top: 4px; }
h4 { margin: 24px 0 12px; }
</style>
