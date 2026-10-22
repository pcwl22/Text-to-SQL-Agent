<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { CopyDocument, Check, FullScreen } from '@element-plus/icons-vue'
import { useObservatory } from '../stores/observatory'
import type * as Monaco from 'monaco-editor/editor/editor.api.js'

const props = defineProps<{ sql: string }>()
const store = useObservatory()
const copied = ref(false)
const editorHost = ref<HTMLDivElement>()
const enhanced = ref(false)
const loading = ref(false)
const editorError = ref('')
let editor: Monaco.editor.IStandaloneCodeEditor | undefined
let monaco: typeof Monaco | undefined
let disposed = false
let copyTimer: ReturnType<typeof setTimeout> | undefined
async function copy() {
  try { await navigator.clipboard.writeText(props.sql); copied.value = true; copyTimer = setTimeout(() => { copied.value = false }, 1500) } catch { editorError.value = '复制不可用，请直接选择 SQL 文本。' }
}
async function enhance() {
  if (loading.value || enhanced.value) return
  loading.value = true
  try {
    const [api, worker] = await Promise.all([import('monaco-editor/editor/editor.api.js'), import('monaco-editor/editor/editor.worker.js?worker')])
    await import('monaco-editor/languages/definitions/sql/register.js')
    if (disposed || !editorHost.value) return
    self.MonacoEnvironment = { getWorker: () => new worker.default() }
    monaco = api
    editor = api.editor.create(editorHost.value, { value: props.sql, language: 'sql', theme: store.theme === 'dark' ? 'vs-dark' : 'vs', readOnly: true, domReadOnly: true, minimap: { enabled: false }, automaticLayout: true, scrollBeyondLastLine: false, fontSize: 13, lineNumbers: 'on', wordWrap: 'on', padding: { top: 16 } })
    enhanced.value = true
  } catch { editorError.value = '增强编辑器加载失败，仍可使用只读 SQL 预览。' }
  finally { loading.value = false }
}
watch(() => props.sql, sql => { editor?.setValue(sql) })
watch(() => store.theme, theme => { monaco?.editor.setTheme(theme === 'dark' ? 'vs-dark' : 'vs') })
onBeforeUnmount(() => { disposed = true; editor?.dispose(); clearTimeout(copyTimer) })
</script>
<template><section class="sql-viewer"><div class="code-toolbar"><span><span class="status-dot"></span>query.sql<span class="mono muted">SQLite</span></span><div><button class="code-action" :disabled="loading || enhanced" @click="enhance"><ElIcon><FullScreen /></ElIcon>{{ loading ? '加载中' : enhanced ? 'Monaco 已启用' : '增强预览' }}</button><button class="code-action" aria-label="复制 SQL" @click="copy"><ElIcon><Check v-if="copied" /><CopyDocument v-else /></ElIcon>{{ copied ? '已复制' : '复制' }}</button></div></div><pre v-show="!enhanced" class="sql-code"><code>{{ sql }}</code></pre><div v-show="enhanced || loading" ref="editorHost" class="monaco-host" aria-label="只读 SQL 编辑器"></div><p v-if="editorError" class="inline-warning">{{ editorError }}</p><div class="code-footer"><span>只读预览 · 不接受 SQL 输入</span><span>无数据库连接</span></div></section></template>
