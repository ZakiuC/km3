<script setup lang="ts">
import { useSessionStore } from '@/stores/session'
import type { Capability } from '@/permissions'

const props = defineProps<{ capability: Capability }>()
const session = useSessionStore()
</script>

<!--
  仅用于隐藏入口，**不是安全边界**（管理端Web程序架构.md §4.3）。
  服务端仍逐请求校验；任何人绕过前端直接调 API 都会被拒绝。
-->
<template>
  <slot v-if="session.has(props.capability)" />
</template>
