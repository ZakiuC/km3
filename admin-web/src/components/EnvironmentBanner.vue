<script setup lang="ts">
import { computed } from 'vue'
import { useEnvironmentStore } from '@/stores/environment'
import { useSessionStore } from '@/stores/session'

const env = useEnvironmentStore()
const session = useSessionStore()

const cls = computed(() => {
  if (!env.current) return 'none'
  return env.current.code === 'prod' ? 'prod' : 'demo'
})

const label = computed(() => {
  if (env.error) return `环境列表加载失败：${env.error}`
  if (!env.current) {
    return env.available.length > 1 ? '未选择环境' : '无可用环境'
  }
  return env.current.code === 'prod'
    ? `生产环境：${env.current.name}`
    : `演示环境：${env.current.name}`
})

function onChange(id: number): void {
  const found = env.available.find((e) => e.id === id)
  if (found) env.select(found)
}
</script>

<!--
  环境标识**常驻**，任何页面都不例外（管理端Web程序架构.md §六）。
  它不是装饰：固件下发与配置修改的目标环境由它决定，
  而"以为在演示环境、实际推到生产"是这个系统里代价最高的一类误操作。
-->
<template>
  <div v-if="session.user" class="env-banner" :class="cls">
    <strong>{{ label }}</strong>
    <template v-if="session.has('switch_environment') && env.available.length > 1">
      <el-select
        :model-value="env.current?.id"
        placeholder="选择环境"
        size="small"
        style="width: 200px"
        @update:model-value="onChange"
      >
        <el-option v-for="e in env.available" :key="e.id" :label="e.name" :value="e.id" />
      </el-select>
      <span class="hint">刷新后需重新选择（刻意不持久化）</span>
    </template>
    <span class="spacer" />
  </div>
</template>

<style scoped>
.hint { font-size: 12px; opacity: 0.85; }
</style>
