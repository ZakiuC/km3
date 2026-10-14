<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { logsApi } from '@/api/logs'
import { useLogStream } from '@/composables/useLogStream'
import { usePagedTable } from '@/composables/usePagedTable'
import type { LogEntry } from '@/api/types'
import ErrorState from '@/components/ErrorState.vue'

const deviceId = ref('')
/**
 * 「全部」用哨兵值而非 undefined。
 * 原因：el-option 的 value 属性不接受 undefined（其类型是
 * string | number | boolean | Record<string, unknown>），
 * 传 undefined 会在模板类型检查里报 TS2322。
 * 用 -1 作哨兵，查询时再映射回"不带该筛选条件"，语义显式且无类型妥协。
 */
const LEVEL_ALL = -1
const levelMin = ref<number>(LEVEL_ALL)
const module = ref('')

const { items, total, page, size, loading, error, load, reload } = usePagedTable<LogEntry>(
  (q) =>
    logsApi.search({
      // 条件展开而非 `x: undefined`：exactOptionalPropertyTypes 下后者是类型错误，
      // 且"未填的筛选项"本就不该出现在查询串里。
      ...(deviceId.value ? { device_id: deviceId.value } : {}),
      ...(levelMin.value !== LEVEL_ALL ? { level_min: levelMin.value } : {}),
      ...(module.value ? { module: module.value } : {}),
      page: q.page,
      size: q.size,
    }),
)

const LEVELS: { label: string; value: number }[] = [
  { label: '全部', value: LEVEL_ALL },
  { label: 'WARN 及以上', value: 3 },
  { label: 'ERROR 及以上', value: 4 },
]

// 实时流按当前环境下的设备订阅；未选定设备时只做历史查询。
const stream = useLogStream(deviceId.value ? `log.${deviceId.value}` : 'log._unset')

onMounted(() => {
  void load()
  // 只在选定具体设备后连接实时流：否则订阅的是一个不存在的 topic，
  // 服务端会因权限或不存在而关闭连接，表现为"连上又断"。
  if (deviceId.value) stream.connect()
})
</script>

<template>
  <div class="page">
    <h3>日志</h3>

    <el-form inline>
      <el-form-item label="设备编号">
        <el-input v-model="deviceId" placeholder="留空则跨设备检索" style="width: 220px" />
      </el-form-item>
      <el-form-item label="级别">
        <el-select v-model="levelMin" style="width: 160px">
          <el-option v-for="(l, i) in LEVELS" :key="i" :label="l.label" :value="l.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="模块">
        <el-input v-model="module" style="width: 160px" />
      </el-form-item>
      <el-button type="primary" @click="reload">查询</el-button>
    </el-form>

    <ErrorState v-if="error" :message="error" @retry="load" />

    <template v-else>
      <div class="stream-hint">
        实时流：
        <el-tag :type="stream.connected.value ? 'success' : 'info'" size="small">
          {{ stream.connected.value ? '已连接' : '未连接' }}
        </el-tag>
        <el-button link type="primary" @click="stream.connect()">连接</el-button>
        <el-button link @click="stream.close()">断开</el-button>
      </div>

      <el-table v-loading="loading" :data="[...stream.entries.value, ...items]" size="small">
        <el-table-column prop="ts" label="时间" width="210" />
        <el-table-column prop="level" label="级别" width="70" />
        <el-table-column prop="module" label="模块" width="140" />
        <!-- 日志正文可能包含设备上报的任意字节，一律按文本渲染，禁止 v-html -->
        <el-table-column prop="message" label="内容" show-overflow-tooltip />
      </el-table>

      <el-pagination
        class="pager"
        layout="total, prev, pager, next"
        :total="total"
        :page-size="size"
        :current-page="page"
        @current-change="(p: number) => { page = p; load() }"
      />
    </template>
  </div>
</template>

<style scoped>
.stream-hint { margin: 12px 0; display: flex; align-items: center; gap: 8px; }
.pager { margin-top: 12px; }
</style>
