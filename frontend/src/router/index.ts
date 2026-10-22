import { createRouter, createWebHistory } from 'vue-router'

export const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '实验总览', section: 'OVERVIEW' } },
  { path: '/playground', name: 'playground', component: () => import('../views/PlaygroundView.vue'), meta: { title: 'SQL 演示台', section: 'PLAYGROUND' } },
  { path: '/workflow', name: 'workflow', component: () => import('../views/WorkflowView.vue'), meta: { title: 'Agent 工作流', section: 'WORKFLOW' } },
  { path: '/experiments', name: 'experiments', component: () => import('../views/ExperimentsView.vue'), meta: { title: '消融实验', section: 'EXPERIMENTS' } },
  { path: '/audit', name: 'audit', component: () => import('../views/AuditView.vue'), meta: { title: '安全与审计', section: 'SECURITY' } },
  { path: '/about', name: 'about', component: () => import('../views/AboutView.vue'), meta: { title: '关于项目', section: 'PROJECT' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]
export const router = createRouter({ history: createWebHistory(), routes, scrollBehavior: () => ({ top: 0 }) })
router.afterEach(to => { document.title = `${String(to.meta.title)} · BIRD Observatory` })
