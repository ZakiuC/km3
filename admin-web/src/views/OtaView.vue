<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { otaApi } from '@/api/ota'
import { useDevicesStore } from '@/stores/devices'
import { useEnvironmentStore } from '@/stores/environment'
import type { Firmware } from '@/api/types'
import ErrorState from '@/components/ErrorState.vue'
import EmptyState from '@/components/EmptyState.vue'

const env = useEnvironmentStore()
const devices = useDevicesStore()

const firmware = ref<Firmware[]>([])
const error = ref<string | null>(null)
const loading = ref(false)
const selectedFw = ref<number | null>(null)
const targetDevices = ref<string[]>([])

async function load(): Promise<void> {
  if (!env.current) return
  loading.value = true
  error.value = null
  try {
    firmware.value = (await otaApi.firmwareList(env.current.id)).items
    await devices.load()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载固件列表失败'
  } finally {
    loading.value = false
  }
}

async function onDeploy(): Promise<void> {
  if (selectedFw.value === null || targetDevices.value.length === 0) {
    ElMessage.warning('请选择固件与目标设备')
    return
  }
  const fw = firmware.value.find((f) => f.id === selectedFw.value)
  try {
    // 二次确认必须显示**目标环境名**：这是防止"以为在演示环境、
    // 实际推到生产"的最后一道人工停顿（管理端Web程序架构.md §六）。
    await ElMessageBox.confirm(
      `即将向【${env.current?.name ?? '未知环境'}】的 ${targetDevices.value.length} 台设备下发固件 ${fw?.version ?? ''}。此操作不可撤销。`,
      '确认下发',
      { type: 'warning', confirmButtonText: '确认下发', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }

  try {
    await otaApi.deploy(selectedFw.value, targetDevices.value)
    ElMessage.success('下发已提交，进度可在设备详情查看')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '下发失败')
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <h3>固件与升级</h3>

    <ErrorState v-if="error" :message="error" @retry="load" />

    <template v-else>
      <el-alert type="warning" :closable="false" show-icon>
        固件签名在开发机离线完成，服务器只分发、不持私钥。
        下发前请确认右上角环境标识与目标一致。
      </el-alert>

      <h4>可用固件</h4>
      <EmptyState v-if="!loading && firmware.length === 0" description="当前环境没有固件" />
      <el-table
        v-else
        v-loading="loading"
        :data="firmware"
        @row-click="(r: Firmware) => (selectedFw = r.id)"
        highlight-current-row
      >
        <el-table-column prop="version" label="版本" width="120" />
        <el-table-column prop="model" label="型号" width="140" />
        <el-table-column prop="file_size" label="大小" width="120" />
        <el-table-column prop="file_hash" label="SHA256" show-overflow-tooltip />
        <el-table-column label="稳定版" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.is_stable" type="success" size="small">是</el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
      </el-table>

      <h4>下发</h4>
      <el-form inline>
        <el-form-item label="目标设备">
          <el-select v-model="targetDevices" multiple style="width: 420px">
            <el-option
              v-for="d in devices.items"
              :key="d.device_id"
              :label="`${d.name}（${d.device_id}）`"
              :value="d.device_id"
            />
          </el-select>
        </el-form-item>
        <el-button type="primary" :disabled="selectedFw === null" @click="onDeploy">
          下发固件
        </el-button>
      </el-form>
    </template>
  </div>
</template>

<style scoped>
h4 { margin: 20px 0 12px; }
</style>
