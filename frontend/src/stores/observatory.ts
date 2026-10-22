import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { offlineRepository } from '../api/repository'
import type { ExperimentGroup, Theme, WorkflowMode } from '../types'

export const useObservatory = defineStore('observatory', () => {
  const experiments = offlineRepository.getExperiments()
  const cases = offlineRepository.getDemoCases()
  const group = ref<ExperimentGroup>('A')
  const caseId = ref(cases[0]!.id)
  const mode = ref<WorkflowMode>('full')
  const theme = ref<Theme>('dark')
  const phase = ref<'idle' | 'playing' | 'done'>('idle')
  const step = ref(0)
  const demo = computed(() => cases.find(c => c.id === caseId.value) ?? cases[0]!)
  const currentExperiment = computed(() => experiments.find(e => e.group === group.value)!)
  function selectCase(id: string) {
    if (!cases.some(c => c.id === id)) return
    caseId.value = id; phase.value = 'idle'; step.value = 0
  }
  function initializeTheme() {
    try { if (localStorage.getItem('bird-theme') === 'light') theme.value = 'light' } catch { /* Storage may be blocked. */ }
    document.documentElement.dataset.theme = theme.value
    document.documentElement.classList.toggle('dark', theme.value === 'dark')
  }
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = theme.value
    document.documentElement.classList.toggle('dark', theme.value === 'dark')
    try { localStorage.setItem('bird-theme', theme.value) } catch { /* In-memory theme still works. */ }
  }
  return { experiments, cases, group, caseId, mode, theme, phase, step, demo, currentExperiment, selectCase, initializeTheme, toggleTheme }
})
