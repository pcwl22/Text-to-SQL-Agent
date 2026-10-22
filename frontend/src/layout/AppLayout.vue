<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { DataAnalysis, Monitor, Connection, Histogram, Lock, InfoFilled, Sunny, Moon, Menu, ArrowRight, TopRight, Close, Check } from '@element-plus/icons-vue'
import { useObservatory } from '../stores/observatory'

const route = useRoute()
const store = useObservatory()
store.initializeTheme()
const mobileOpen = ref(false)
const nav = [
  { to: '/', label: '实验总览', icon: DataAnalysis, code: '01' },
  { to: '/playground', label: 'SQL 演示台', icon: Monitor, code: '02' },
  { to: '/workflow', label: 'Agent 工作流', icon: Connection, code: '03' },
  { to: '/experiments', label: '消融实验', icon: Histogram, code: '04' },
  { to: '/audit', label: '安全与审计', icon: Lock, code: '05' },
  { to: '/about', label: '关于项目', icon: InfoFilled, code: '06' },
]
</script>
<template>
  <a class="skip-link" href="#main-content">跳转到主要内容</a>
  <button v-if="mobileOpen" class="sidebar-backdrop" aria-label="关闭导航" @click="mobileOpen = false"></button>
  <aside class="sidebar" :class="{ open: mobileOpen }">
    <RouterLink to="/" class="brand" @click="mobileOpen = false">
      <span class="brand-mark">B<span>_</span></span><span>BIRD<span class="brand-sub">AGENT OBSERVATORY</span></span><span class="version">V1</span>
    </RouterLink>
    <button class="mobile-close icon-button" aria-label="关闭导航" @click="mobileOpen = false"><ElIcon><Close /></ElIcon></button>
    <div class="workspace-label"><span class="status-dot"></span>Text-to-SQL Workspace<span class="workspace-id">本地</span></div>
    <div class="nav-heading">工作空间 / WORKSPACE</div>
    <nav aria-label="主导航">
      <RouterLink v-for="item in nav" :key="item.to" :to="item.to" class="nav-item" :class="{ active: route.path === item.to }" @click="mobileOpen = false">
        <ElIcon><component :is="item.icon" /></ElIcon><span>{{ item.label }}</span><span class="nav-code">{{ item.code }}</span>
      </RouterLink>
    </nav>
    <div class="sidebar-bottom">
      <div class="offline-card"><span class="offline-icon"><ElIcon><Lock /></ElIcon></span><b>Offline by design</b><p>安全快照 · 零远程模型请求</p><span class="tiny-chip">LIVE INFERENCE 未开放</span></div>
      <a href="https://github.com/pcwl22/Text-to-SQL-Agent" target="_blank" rel="noopener noreferrer" class="repo-link">查看 GitHub 源码<ElIcon><TopRight /></ElIcon></a>
      <div class="sidebar-footer"><span class="avatar">B</span><div><b>Research Workspace</b><small>经典 BIRD Mini-Dev</small></div><ElIcon><Check /></ElIcon></div>
    </div>
  </aside>
  <div class="app-shell">
    <header class="topbar">
      <div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开导航" @click="mobileOpen = true"><ElIcon><Menu /></ElIcon></button><span>Workspace</span><ElIcon><ArrowRight /></ElIcon><b>{{ route.meta.title }}</b></div>
      <div class="topbar-actions"><span class="connection-status"><span class="status-dot"></span>离线演示环境</span><span class="topbar-separator"></span><button class="icon-button theme-button" :aria-label="store.theme === 'dark' ? '切换浅色主题' : '切换深色主题'" @click="store.toggleTheme"><ElIcon><Sunny v-if="store.theme === 'dark'" /><Moon v-else /></ElIcon></button><span class="top-avatar">研究</span></div>
    </header>
    <main id="main-content" class="main-content"><slot /></main>
    <footer class="page-footer"><span>BIRD Agent Observatory<span class="footer-dot">·</span>可审计的 Text-to-SQL 研究平台</span><span>离线快照 / V1.0</span></footer>
  </div>
</template>
