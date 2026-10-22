<script setup lang="ts">
import { computed } from 'vue'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import VChart from 'vue-echarts'
import { useObservatory } from '../stores/observatory'
import { experiments } from '../data/experiments'

use([CanvasRenderer, BarChart, GridComponent, TooltipComponent])
const props = defineProps<{ metric: 'accuracy' | 'tokens' | 'latency' }>()
const store = useObservatory()
const values = computed(() => experiments.slice(0, 2).map(e => props.metric === 'accuracy' ? Number((e.officialCorrect! / 33 * 100).toFixed(2)) : props.metric === 'tokens' ? e.tokens! : e.latency!))
const option = computed(() => {
  const text = store.theme === 'dark' ? '#a3afc4' : '#526078'
  const line = store.theme === 'dark' ? '#273144' : '#e4e8f0'
  return {
    animationDuration: 500,
    textStyle: { fontFamily: 'inherit' },
    grid: { left: 54, right: 30, top: 35, bottom: 34 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v: unknown) => `${Number(v).toLocaleString()}${props.metric === 'accuracy' ? '%' : props.metric === 'latency' ? ' 秒' : ' Tokens'}` },
    xAxis: { type: 'category', data: ['A · Baseline', 'B · Schema-only'], axisTick: { show: false }, axisLine: { lineStyle: { color: line } }, axisLabel: { color: text, fontSize: 11, margin: 15 } },
    yAxis: { type: 'value', max: props.metric === 'accuracy' ? 100 : undefined, splitNumber: 4, axisLabel: { color: text, fontSize: 10, formatter: (v: number) => props.metric === 'tokens' ? `${v / 1000}k` : String(v) }, splitLine: { lineStyle: { color: line, type: 'dashed' } } },
    series: [{ type: 'bar', barWidth: 46, data: values.value.map((v, i) => ({ value: v, itemStyle: { color: i === 0 ? '#537fff' : '#42cbbb', borderRadius: [5, 5, 0, 0] } })), label: { show: true, position: 'top', color: text, fontSize: 12, fontWeight: 600, formatter: (p: { value: number }) => `${p.value.toLocaleString()}${props.metric === 'accuracy' ? '%' : ''}` } }],
  }
})
</script>
<template><div class="chart-wrap" role="img" :aria-label="`A/B ${metric} 对比：${values.join(' / ')}`"><VChart :option="option" autoresize /></div></template>
