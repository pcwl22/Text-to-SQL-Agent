<script setup lang="ts">
import { computed, ref } from 'vue'
import PageHeading from '../components/PageHeading.vue'
import SourceNote from '../components/SourceNote.vue'
import { experiments, number, percent, schemaStats } from '../data/experiments'
import { pairedCases } from '../data/pairedCases'
import { useObservatory } from '../stores/observatory'

const store = useObservatory()
const filter = ref('all')
const rows = computed(() => pairedCases.filter(c => filter.value === 'all' || (filter.value === 'changed' ? c.a !== c.b : filter.value === 'both' ? c.a && c.b : !c.a && !c.b)))
const metrics = [
  { label: '官方 EX / 固定分母', get: (i: number) => `${experiments[i]!.officialCorrect} / 33 · ${percent(experiments[i]!.officialCorrect)}` },
  { label: 'Local Proxy / 固定分母', get: (i: number) => `${experiments[i]!.proxyCorrect} / 33` },
  { label: 'SQL 生成成功率', get: (i: number) => percent(experiments[i]!.generated) },
  { label: 'SQL 执行成功率', get: (i: number) => percent(experiments[i]!.executed) },
  { label: '模型 HTTP 尝试', get: (i: number) => number(experiments[i]!.requests) },
  { label: '总 Tokens', get: (i: number) => number(experiments[i]!.tokens) },
  { label: '平均 / P50 / P95 延迟（秒）', get: (i: number) => `${experiments[i]!.latency!.toFixed(3)} / ${experiments[i]!.p50!.toFixed(3)} / ${experiments[i]!.p95!.toFixed(3)}` },
]
</script>
<template>
  <PageHeading eyebrow="CONTROLLED ABLATION" title="用同一把尺，比较两种架构。" description="固定模型、33 道题、Evidence 与执行协议。公开证据来自已冻结的真实运行。"><RouterLink to="/workflow" class="secondary-link">查看四组架构 ↗</RouterLink></PageHeading>
  <SourceNote />
  <section class="experiment-selector"><button v-for="e in experiments" :key="e.group" class="panel experiment-tile" :class="{ selected: store.group === e.group }" @click="store.group = e.group"><div><span class="group-symbol" :class="e.group">{{ e.group }}</span><span class="status-pill" :class="e.runId ? 'success' : 'pending'">{{ e.runId ? '已验收' : '未真实运行' }}</span></div><h3>{{ e.name }}</h3><b>{{ percent(e.officialCorrect) }}</b><span class="small-label">{{ e.runId ? 'OFFICIAL EX · 33 CASES' : '无真实分数 / 无运行记录' }}</span></button></section>
  <p class="selected-run mono">{{ store.currentExperiment.runId ? `Run ID · ${store.currentExperiment.runId}` : `${store.currentExperiment.name} 尚未真实运行；架构视图不代表实验结果。` }}</p>
  <section class="panel"><div class="panel-heading"><h2>A / B 统一条件下的真实结果</h2><span class="small-label">FIXED DENOMINATOR = 33</span></div><div class="table-scroll"><table class="data-table comparison-table"><thead><tr><th>指标</th><th><span class="group-symbol A">A</span>Baseline</th><th><span class="group-symbol B">B</span>Schema-only</th></tr></thead><tbody><tr v-for="m in metrics" :key="m.label"><td>{{ m.label }}</td><td class="mono">{{ m.get(0) }}</td><td class="mono">{{ m.get(1) }}</td></tr><tr><td>表 / 字段 Recall</td><td>完整 Schema · 未单独计分</td><td>{{ schemaStats.tablesFound }}/68 · {{ schemaStats.columnsFound }}/165</td></tr><tr><td>Schema 字符压缩 / 合法选择</td><td>未筛选</td><td>78.04% · 32/33（case 195 回退）</td></tr></tbody></table></div></section>
  <div class="analysis-grid"><section class="panel case-analysis"><div class="panel-heading"><h2>Case 1317<span class="count-chip">唯一退步案例</span></h2><span class="status-pill warning">A ✓ → B ✕</span></div><p class="analysis-lead">额外的 position 谓词改变了结果；Schema Linking 的因果贡献仍无法确定。</p><div class="evidence-row"><span class="evidence-level confirmed">CONFIRMED</span><p>B 增加 position 条件；独立只读复验 A 为 7、B 为 0，仅移除此条件得到 7。该题表列 Recall 完整。</p></div><div class="evidence-row"><span class="evidence-level likely">LIKELY</span><p>问题措辞可能诱发额外字段映射，有支持证据但不足以确定。</p></div><div class="evidence-row"><span class="evidence-level unknown">UNKNOWN</span><p>Schema 缩减与供应商波动的各自贡献；历史 Linking JSON、精确列投影和结果行未落盘，不能恢复。</p></div></section><aside class="panel cost-note"><span class="small-label">TOKEN ECONOMICS</span><h2>压缩字符 ≠ 降低总成本</h2><div class="cost-stat"><span>Generator 输入减少</span><b class="cyan-text">64.48%</b></div><div class="cost-stat"><span>Linking 自身消耗</span><b>61,352 Tokens</b></div><div class="cost-stat"><span>整体 Tokens 增加</span><b class="amber-text">84.46%</b></div><p>字符压缩为 78.04%，但新增 Linking 调用抵消了 Generator 节省。无可靠账单依据，实际费用为 UNKNOWN。</p><p>18 题双方正确 · 14 题双方错误 · 1 题 A 对 B 错。小样本不作显著性或一般归因结论。</p></aside></div>
  <section class="panel"><div class="panel-heading"><h2>逐题配对结果<span class="count-chip">{{ rows.length }} / 33</span></h2><ElSelect v-model="filter" aria-label="筛选逐题对比" class="case-filter"><ElOption label="全部题目" value="all" /><ElOption label="正确性发生变化" value="changed" /><ElOption label="双方正确" value="both" /><ElOption label="双方错误" value="neither" /></ElSelect></div><p class="panel-caption">只公开 case_id、db_id、难度和官方得分标记；不包含题目、SQL、Gold 或原始日志。</p><div class="table-scroll paired-scroll"><table class="data-table"><thead><tr><th>Case ID</th><th>数据库</th><th>难度</th><th>A 官方 EX</th><th>B 官方 EX</th><th>变化</th></tr></thead><tbody><tr v-for="r in rows" :key="r.caseId" :class="{ 'changed-row': r.a !== r.b }"><td class="mono">{{ r.caseId }}</td><td class="mono">{{ r.dbId }}</td><td>{{ r.difficulty }}</td><td><span :class="r.a ? 'cyan-text' : 'muted'">{{ r.a ? '✓ 正确' : '✕ 错误' }}</span></td><td><span :class="r.b ? 'cyan-text' : 'muted'">{{ r.b ? '✓ 正确' : '✕ 错误' }}</span></td><td>{{ r.a === r.b ? '—' : '↓ 退步' }}</td></tr></tbody></table></div></section>
</template>
