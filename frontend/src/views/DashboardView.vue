<script setup lang="ts">
import { ArrowRight, Connection, DocumentChecked, Lock } from '@element-plus/icons-vue'
import PageHeading from '../components/PageHeading.vue'
import MetricCard from '../components/MetricCard.vue'
import SourceNote from '../components/SourceNote.vue'
import ComparisonChart from '../components/ComparisonChart.vue'
import { experiments, number, percent, provenance } from '../data/experiments'
</script>
<template>
  <PageHeading eyebrow="RESEARCH OVERVIEW" title="从问题到 SQL，每一步都有证据。" description="在真实数据上理解 Agent 的能力、成本与边界。">
    <RouterLink to="/playground" class="primary-link">打开 SQL 演示台<ElIcon><ArrowRight /></ElIcon></RouterLink>
  </PageHeading>
  <SourceNote />
  <section class="metric-grid" aria-label="核心指标">
    <MetricCard label="A · Baseline 官方 EX" value="57.58" unit="%" detail="19 / 33 题 · 完整 Schema" />
    <MetricCard label="B · Schema-only 官方 EX" value="54.55" unit="%" detail="18 / 33 题 · 验证后的 Schema" tone="cyan" />
    <MetricCard label="独立 Repair 语义断言" value="8 / 8" detail="结构错误诊断 · 不属于 BIRD 官方 EX" tone="amber" badge="DIAGNOSTIC" />
    <MetricCard label="评测覆盖范围" value="33" unit="题 / 11 库" detail="固定开发子集 · 不是 500 题全量评测" badge="FROZEN V1" />
  </section>
  <section class="panel overview-strip"><div><span class="small-label">实验条件</span><b>{{ provenance.model }}</b><span>LunaRoute · temperature 0</span></div><div><span class="small-label">生成预算</span><b>2,048 <small>输出 Tokens</small></b><span>Evidence 开启 · 串行执行</span></div><div><span class="small-label">可靠性约束</span><b>0 <small>自动重试</small></b><span>请求前持久化 · 只读执行</span></div><RouterLink to="/audit" class="text-link"><ElIcon><Lock /></ElIcon>安全机制<ElIcon><ArrowRight /></ElIcon></RouterLink></section>
  <div class="section-heading"><h2>效果与成本<span class="count-chip">A / B</span></h2><RouterLink to="/experiments" class="text-link">查看完整对比<ElIcon><ArrowRight /></ElIcon></RouterLink></div>
  <section class="chart-grid">
    <article v-for="chart in ([{ metric: 'accuracy', title: '官方执行准确率', unit: 'OFFICIAL EX · %', note: '固定 33 题分母，失败与缺失计零' }, { metric: 'tokens', title: '模型 Token 消耗', unit: 'TOTAL TOKENS', note: 'B 总 Token 增加 84.46%，费用未知' }, { metric: 'latency', title: '每题平均延迟', unit: 'AGENT LATENCY · S', note: '包含 Agent 流程，不代表模型 SLA' }] as const)" :key="chart.metric" class="panel chart-panel"><div class="panel-heading"><h3>{{ chart.title }}</h3><span class="small-label">{{ chart.unit }}</span></div><ComparisonChart :metric="chart.metric" /><p class="chart-note">{{ chart.note }}</p></article>
  </section>
  <div class="dashboard-bottom">
    <section class="panel"><div class="panel-heading"><h2>已冻结的实验</h2><span class="live-label"><span class="status-dot"></span>真实历史记录</span></div><div class="table-scroll"><table class="data-table"><thead><tr><th>实验组</th><th>官方 EX</th><th>Tokens</th><th>HTTP 尝试</th><th>状态</th></tr></thead><tbody><tr v-for="e in experiments" :key="e.group"><td><span class="group-symbol" :class="e.group">{{ e.group }}</span><b>{{ e.name }}</b></td><td>{{ percent(e.officialCorrect) }}</td><td class="mono">{{ number(e.tokens) }}</td><td class="mono">{{ number(e.requests) }}</td><td><span :class="e.runId ? 'status-pill success' : 'status-pill pending'">{{ e.runId ? '已验收' : '未真实运行' }}</span></td></tr></tbody></table></div></section>
    <aside class="insight-panel"><span class="insight-icon"><ElIcon><Connection /></ElIcon></span><span class="small-label">RESEARCH NOTE / 01</span><h2>更少的 Schema，<br>不一定更高的准确率。</h2><p>Schema 字符压缩 78.04%，Generator 输入减少 64.48%，但整体成本更高，官方 EX 少 1 题。</p><RouterLink to="/experiments" class="text-link">理解 case 1317<ElIcon><ArrowRight /></ElIcon></RouterLink><div class="insight-bottom"><ElIcon><DocumentChecked /></ElIcon>观察结果，不作一般因果推断</div></aside>
  </div>
</template>
