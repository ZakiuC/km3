<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { useRoute } from 'vue-router'
// ECharts 按需引入：整包引入会让首屏产物多出数百 KB，
// 而管理端只用到折线图这一种图形。
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, DataZoomComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { devicesApi } from '@/api/devices'
import type { SensorPoint } from '@/api/types'
import ErrorState from '@/components/ErrorState.vue'

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, CanvasRenderer])

const route = useRoute()
const deviceId = Number(route.params.id)

type ChartInstance = ReturnType<typeof echarts.init>
const chartEl = ref<HTMLDivElement | null>(null)
const chart = shallowRef<ChartInstance | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)
const sensorType = ref('imu')
// 用 Date[] 而非 [Date, Date]：el-date-picker 的 datetimerange 产出的是数组，
// 声明成元组会在模板绑定处产生一个纯粹的、与业务无关的类型错误。
const range = ref<Date[]>([new Date(Date.now() - 3600_000), new Date()])

async function load(): Promise<void> {
  // noUncheckedIndexedAccess 下 range.value[i] 是 Date | undefined，
  // 因此先取出并显式判空，再进入 try。不写成非空断言：
  // 日期范围控件确实可能被用户清空，那是真实状态而非类型噪音。
  const from = range.value[0]
  const to = range.value[1]
  if (from === undefined || to === undefined) {
    loading.value = false
    return
  }

  loading.value = true
  error.value = null
  try {
    const res = await devicesApi.sensorHistory(deviceId, sensorType.value, {
      from: from.toISOString(),
      to: to.toISOString(),
      size: 2000,
    })
    render(res.items)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载传感器历史失败'
  } finally {
    loading.value = false
  }
}

function render(points: SensorPoint[]): void {
  if (!chart.value) return
  // 只画 value_num；value_json 的结构随传感器而异，硬画会误导。
  const data = points
    .filter((p) => p.value_num !== null)
    .map((p) => [p.ts, p.value_num as number])
  chart.value.setOption({
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'time' },
    yAxis: { type: 'value' },
    dataZoom: [{ type: 'inside' }, { type: 'slider' }],
    series: [{ type: 'line', showSymbol: false, data, name: sensorType.value }],
  })
}

function onResize(): void {
  chart.value?.resize()
}

onMounted(() => {
  if (chartEl.value) {
    chart.value = echarts.init(chartEl.value)
    window.addEventListener('resize', onResize)
  }
  void load()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart.value?.dispose()
})
</script>

<template>
  <div class="page">
    <h3>传感器历史</h3>

    <el-form inline>
      <el-form-item label="类型">
        <el-select v-model="sensorType" style="width: 140px" @change="load">
          <el-option label="IMU" value="imu" />
          <el-option label="超声波" value="ultrasonic" />
          <el-option label="气压" value="pressure" />
          <el-option label="温度" value="temperature" />
          <el-option label="湿度" value="humidity" />
        </el-select>
      </el-form-item>
      <el-form-item label="时间范围">
        <el-date-picker v-model="range" type="datetimerange" @change="load" />
      </el-form-item>
    </el-form>

    <ErrorState v-if="error" :message="error" @retry="load" />
    <div v-else v-loading="loading" ref="chartEl" class="chart" />
  </div>
</template>

<style scoped>
.chart { width: 100%; height: 460px; }
</style>
