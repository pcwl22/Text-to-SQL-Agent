<script setup lang="ts">
import { computed, ref } from 'vue'
import { VueFlow, useVueFlow, MarkerType, type Node, type Edge } from '@vue-flow/core'
import { useObservatory } from '../stores/observatory'
import PageHeading from '../components/PageHeading.vue'
import { nodeInfo, modeNames, modeFlags, flagsMode } from '../data/workflow'
import type { WorkflowMode } from '../types'

const store = useObservatory()
const selected = ref('inspect')
const { zoomIn, zoomOut, fitView } = useVueFlow()
const flags = computed(() => modeFlags(store.mode))
const active = computed(() => nodeInfo.find(n => n.id === selected.value)!)
const enabled = (id: string) => id === 'link' ? flags.value.linking : ['classify', 'repair'].includes(id) ? flags.value.repair : true
const nodes = computed<Node[]>(() => nodeInfo.map((n, i) => ({ id: n.id, label: `${n.label}\n${n.subtitle}`, position: { x: i < 4 ? 50 + i * 235 : 755 - (i - 4) * 235, y: i < 4 ? 65 : 260 }, class: `workflow-node ${enabled(n.id) ? 'enabled' : 'disabled'} ${selected.value === n.id ? 'selected' : ''}`, selectable: true, draggable: false, data: { label: n.label } })))
const edges = computed<Edge[]>(() => {
  const pairs: [string, string, string?][] = [['inspect', flags.value.linking ? 'link' : 'generate'], ...(flags.value.linking ? [['link', 'generate'] as [string, string]] : []), ['generate', 'validate'], ['validate', 'execute']]
  if (flags.value.repair) pairs.push(['execute', 'classify'], ['validate', 'classify', '校验失败'], ['classify', 'repair', '结构错误且首次'], ['repair', 'validate', '再校验 · 最多一次'])
  return pairs.map(([source, target, label], i) => ({ id: `e${i}`, source, target, label, animated: true, type: 'smoothstep', markerEnd: MarkerType.ArrowClosed, style: { stroke: '#537fff', strokeWidth: 2 }, labelStyle: { fill: '#9caac0', fontSize: 10 }, labelBgStyle: { fill: '#172132' } }))
})
const state = computed(() => ({ source: 'DEMO · 架构契约示意，非真实执行状态', mode: store.mode, question: '演示问题', evidence: '演示业务口径', chosen_schema: flags.value.linking ? '[经过验证的 Schema]' : '[完整 Schema]', sql_repair_enabled: flags.value.repair, repair_attempts: 0, http_attempts: 0 }))
function chooseMode(mode: WorkflowMode) { store.mode = mode }
</script>
<template>
  <PageHeading eyebrow="AGENT ARCHITECTURE" title="可观察，也可解释。" description="四种架构视图，映射现有 Python 执行契约；这里只展示结构，不运行 Agent。"><span class="status-pill demo">交互架构图 · 零请求</span></PageHeading>
  <div class="panel workflow-controls"><div class="segmented" aria-label="架构模式"><button v-for="(name, mode) in modeNames" :key="mode" :class="{ selected: store.mode === mode }" :aria-pressed="store.mode === mode" @click="chooseMode(mode)">{{ name }}</button></div><div class="switches"><span>Schema Linking<ElSwitch :model-value="flags.linking" aria-label="启用 Schema Linking" @update:model-value="store.mode = flagsMode(Boolean($event), flags.repair)" /></span><span>SQL Repair<ElSwitch :model-value="flags.repair" aria-label="启用 SQL Repair" @update:model-value="store.mode = flagsMode(flags.linking, Boolean($event))" /></span></div></div>
  <div class="workflow-layout">
<section class="panel graph-panel"><div class="panel-heading"><h2>{{ modeNames[store.mode] }}</h2><span class="small-label">点击节点查看契约</span></div><div class="flow-canvas"><div class="flow-tools"><button aria-label="放大工作流" @click="zoomIn()">＋</button><button aria-label="缩小工作流" @click="zoomOut()">−</button><button aria-label="适应画布" @click="fitView()">适应画布</button></div><VueFlow :nodes="nodes" :edges="edges" :fit-view-on-init="true" :min-zoom="0.2" :max-zoom="1.5" :nodes-draggable="false" :nodes-connectable="false" :zoom-on-scroll="false" :pan-on-drag="true" @node-click="selected = $event.node.id" /></div><div class="graph-legend"><span><i class="legend-dot blue"></i>当前架构启用</span><span><i class="legend-dot muted-dot"></i>未启用</span><span>拖动平移 · 控件不触发请求</span></div><p class="graph-footnote">{{ store.mode === 'baseline' ? 'Baseline 为直接函数调用；图中 Validation 是 execute_select 内部安全层，不是独立 Graph 节点。' : !flags.repair ? 'Schema-only 的 Validation 位于 execute_select 内部；执行后直接结束，不经过 classify 或 repair。' : 'Repair 分支为显式 validate → execute → classify；校验失败直接 classify，修复后返回 validate，至多一次。' }}<br v-if="!flags.linking" /><span v-if="!flags.linking && store.mode !== 'baseline'">Linking 关闭时 Graph 保留 pass-through 节点，不调用模型。</span> inspect / linking / generation / repair 的系统异常可直接结束。</p></section>
    <aside class="panel node-detail"><span class="eyebrow">NODE INSPECTOR</span><div class="node-index">{{ String(nodeInfo.findIndex(n => n.id === selected) + 1).padStart(2, '0') }}</div><h2>{{ active.label }}</h2><span :class="enabled(active.id) ? 'status-pill success' : 'status-pill pending'">{{ enabled(active.id) ? '当前架构启用' : '当前架构关闭' }}</span><p>{{ active.description }}</p><span class="small-label">PYTHON CONTRACT</span><code class="contract-code">{{ active.tool }}</code><span class="small-label">STATE FIELDS</span><div class="state-tags"><span v-for="field in active.state" :key="field">{{ field }}</span></div></aside>
</div>
  <section class="panel state-panel"><div class="panel-heading"><h2>Agent State 白名单示意</h2><span class="status-pill success">Gold SQL 不在 State 中</span></div><pre>{{ JSON.stringify(state, null, 2) }}</pre></section>
</template>
