<script setup lang="ts">
import { Lock, DocumentChecked, Key, Timer, Connection, CircleCheck } from '@element-plus/icons-vue'
import PageHeading from '../components/PageHeading.vue'
const layers = [
  { icon: DocumentChecked, title: 'SQL AST 安全校验', subtitle: 'DETERMINISTIC GUARD', text: '单条 SELECT / CTE，禁止写入、DDL、PRAGMA、ATTACH 和危险扩展；修复 SQL 必须再次校验。' },
  { icon: Lock, title: 'SQLite 只读执行', subtitle: 'READ-ONLY BY DEFAULT', text: 'URI mode=ro + query_only=ON + authorizer；执行时间、结果行数与字节数均有上限。' },
  { icon: Key, title: 'Gold SQL 隔离', subtitle: 'EVALUATION ONLY', text: 'AgentInput 白名单；生成、Linking、Repair 的 State 和工具返回不接收 Gold，仅独立评测器访问。' },
  { icon: Timer, title: '共享 HTTP 预算', subtitle: 'FAILED ATTEMPTS COUNT', text: '各节点共用预算。失败、超时、未知结局均保守占用额度，SDK / 应用自动重试为 0。' },
]
const events = [
  { id: '01', title: '预算检查与唯一标识', code: 'run_id / case_id / attempt_id', text: '达到请求上限即停止。独占运行锁防止同一实验并发，输出文件不得复用。', tone: 'blue' },
  { id: '02', title: '持久化请求准备事件', code: 'ATTEMPT_PREPARED', text: '请求前写入独立日志。准备事件仅证明已预约，不是 HTTP 已发生的证据。', tone: 'blue' },
  { id: '03', title: '等待写入落盘', code: 'flush → fsync → confirm', text: '只有确认持久化成功才可调用 SDK；写入失败立即禁止发送。', tone: 'cyan' },
  { id: '04', title: '一次 SDK 调用', code: 'max_retries = 0 / no redirects', text: '串行请求，不隐式重试，不切模型；失败也消耗预算。', tone: 'blue' },
  { id: '05', title: '持久化结果事件', code: 'SUCCEEDED / FAILED / INDETERMINATE', text: '保存状态码、Request ID、可用 Token 和完成时间；不保存认证秘密。未知结局不自动重放。', tone: 'cyan' },
]
</script>
<template>
  <PageHeading eyebrow="TRUST & OBSERVABILITY" title="安全来自边界，而不是承诺。" description="展示 Python 已实现的安全契约与审计机制；本页不加载真实请求日志或密钥。"><span class="status-pill success"><ElIcon><Lock /></ElIcon>仅静态机制展示</span></PageHeading>
  <section class="security-grid"><article v-for="l in layers" :key="l.title" class="panel security-card"><span class="security-icon"><ElIcon><component :is="l.icon" /></ElIcon></span><span class="small-label">{{ l.subtitle }}</span><h2>{{ l.title }}</h2><p>{{ l.text }}</p></article></section>
  <div class="audit-layout"><section class="panel audit-timeline"><div class="panel-heading"><h2>一次请求的持久化生命周期</h2><span class="small-label">CONTRACT / NO LIVE EVENTS</span></div><ol><li v-for="event in events" :key="event.id"><span class="timeline-index" :class="event.tone">{{ event.id }}</span><div><h3>{{ event.title }}</h3><code>{{ event.code }}</code><p>{{ event.text }}</p></div></li></ol></section><aside class="audit-side"><section class="panel"><span class="security-icon"><ElIcon><Connection /></ElIcon></span><h2>崩溃恢复，不等于自动续跑。</h2><p>未完成请求标记 INDETERMINATE，保守占用预算。任何新请求都需要新的授权，不能用准备事件倒推发送次数。</p><div class="notice compact">不承诺供应商未保证的 Exactly-Once 语义。</div></section><section class="panel"><h2>Repair 的准入边界</h2><ul class="check-list"><li><ElIcon><CircleCheck /></ElIcon>语法错误、缺表、缺列、歧义列</li><li><ElIcon><Lock /></ElIcon>危险 SQL、超时、权限拒绝：不修复</li><li><ElIcon><Lock /></ElIcon>length、结果截断：不修复</li><li><ElIcon><Lock /></ElIcon>执行成功但答案错误：不触发</li></ul><p>最多修复一次；不读取官方 EX 或 Gold 反馈。</p></section></aside></div>
</template>
