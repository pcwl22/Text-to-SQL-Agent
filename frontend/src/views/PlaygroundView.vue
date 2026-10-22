<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { VideoPlay, RefreshRight, Lock, CircleCheck, Warning, Right } from '@element-plus/icons-vue'
import PageHeading from '../components/PageHeading.vue'
import SqlViewer from '../components/SqlViewer.vue'
import { useObservatory } from '../stores/observatory'

const store = useObservatory()
const question = ref(store.demo.question)
const steps = ['读取固定 Schema', '回放 SQL 快照', '展示安全约束', '展示预设结果']
let timer: ReturnType<typeof setInterval> | undefined
function stop() { clearInterval(timer) }
function reset() { stop(); store.phase = 'idle'; store.step = 0; question.value = store.demo.question }
watch(() => store.caseId, reset)
function replay() {
  if (question.value !== store.demo.question || store.phase === 'playing') return
  store.phase = 'playing'; store.step = 0
  timer = setInterval(() => { store.step++; if (store.step >= steps.length) { stop(); store.phase = 'done' } }, 350)
}
onBeforeUnmount(() => { stop(); if (store.phase === 'playing') { store.phase = 'idle'; store.step = 0 } })
</script>
<template>
  <PageHeading eyebrow="OFFLINE PLAYGROUND" title="看见一次安全的 SQL 旅程。" description="选择固定演示案例，回放输入、SQL 与结果；浏览器不会执行查询。"><span class="status-pill demo">DEMO · 预设快照</span></PageHeading>
  <div class="notice"><ElIcon><Lock /></ElIcon><span>本页全部内容为界面演示数据，不是 BIRD 题目或真实模型输出。无远程 API、无 SQL 执行；修改问题后不可回放。</span></div>
  <div class="playground-grid">
    <section class="panel input-panel"><div class="panel-heading"><h2>问题与上下文</h2><span class="small-label">01 / INPUT</span></div><label class="field-label" for="database-select">演示数据库 / 案例</label><ElSelect id="database-select" :model-value="store.caseId" aria-label="选择演示案例" class="w-full" :disabled="store.phase === 'playing'" @update:model-value="store.selectCase(String($event))"><ElOption v-for="item in store.cases" :key="item.id" :label="`${item.database} · ${item.name}`" :value="item.id" /></ElSelect><label class="field-label" for="question-input">自然语言问题</label><ElInput id="question-input" v-model="question" type="textarea" :rows="4" :maxlength="500" aria-label="自然语言问题" :disabled="store.phase === 'playing'" /><p v-if="question !== store.demo.question" class="inline-warning">仅支持冻结案例。请恢复原始问题后回放，不会生成新 SQL。</p><div class="evidence-box"><span class="small-label">EVIDENCE / 演示业务口径</span><p>{{ store.demo.evidence }}</p></div><div class="input-actions"><ElButton type="primary" :loading="store.phase === 'playing'" :disabled="question !== store.demo.question" @click="replay"><ElIcon><VideoPlay /></ElIcon>回放离线案例</ElButton><ElButton aria-label="重置演示" @click="reset"><ElIcon><RefreshRight /></ElIcon></ElButton></div><div class="schema-preview"><h3><span class="status-dot"></span>Schema 预览<span class="small-label">DEMO</span></h3><pre>{{ store.demo.schema }}</pre></div></section>
    <div class="playground-output">
<section class="panel trace-panel"><div class="panel-heading"><h2>回放步骤</h2><span class="mono small-label">HTTP 0 / SQL 0</span></div><ol class="replay-steps"><li v-for="(s, i) in steps" :key="s" :class="{ complete: store.step > i, current: store.step === i && store.phase === 'playing' }"><span>{{ store.step > i ? '✓' : String(i + 1).padStart(2, '0') }}</span>{{ s }}<ElIcon v-if="i < steps.length - 1"><Right /></ElIcon></li></ol></section>
    <template v-if="store.phase === 'done'"><SqlViewer :sql="store.demo.sql" /><section class="panel result-panel"><div class="panel-heading"><h2>预设执行结果</h2><span :class="store.demo.error ? 'status-pill warning' : 'status-pill success'"><ElIcon><Warning v-if="store.demo.error" /><CircleCheck v-else /></ElIcon>{{ store.demo.error ? '错误快照' : 'SELECT · 只读示意' }}</span></div><p v-if="store.demo.error" class="error-box">{{ store.demo.error }}</p><ElTable v-else :data="store.demo.rows" style="width: 100%"><ElTableColumn v-for="column in store.demo.columns" :key="column" :prop="column" :label="column" :min-width="140" /></ElTable><p class="result-footnote">{{ store.demo.rows.length }} 行预设演示数据 · 未执行 SQLite · 安全状态仅为设计示意</p></section></template>
    <div v-else class="panel empty-state"><div class="terminal-symbol">&gt;_</div><h3>{{ store.phase === 'playing' ? '正在回放固定步骤…' : '准备好探索了吗？' }}</h3><p>选择案例并开始回放，查看 SQL 和预设结果。</p><span class="tiny-chip">不调用模型 · 不执行用户输入</span></div>
</div>
  </div>
</template>
