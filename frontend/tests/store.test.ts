import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useObservatory } from '../src/stores/observatory'
import { modeFlags, flagsMode } from '../src/data/workflow'

beforeEach(() => { setActivePinia(createPinia()); localStorage.clear() })
describe('UI state boundaries', () => {
  it('starts dark and persists a deliberate theme change', () => {
    const s = useObservatory(); s.initializeTheme()
    expect(document.documentElement.dataset.theme).toBe('dark')
    s.toggleTheme()
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('bird-theme')).toBe('light')
    s.toggleTheme(); expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
  it('restores only supported saved themes', () => {
    localStorage.setItem('bird-theme', 'light'); const s = useObservatory(); s.initializeTheme()
    expect(s.theme).toBe('light')
  })
  it('resets replay state when switching to a known case', () => {
    const s = useObservatory(); s.phase = 'done'; s.step = 4
    s.selectCase('demo-error'); expect(s.demo.error).not.toBeNull(); expect(s.phase).toBe('idle'); expect(s.step).toBe(0)
  })
  it('refuses unknown cases without accepting external SQL', () => {
    const s = useObservatory(); const id = s.caseId
    s.selectCase('SELECT anything'); expect(s.caseId).toBe(id)
  })
  it.each(['baseline', 'schema_only', 'repair_only', 'full'] as const)('round-trips mode flags for %s', mode => {
    const flags = modeFlags(mode); expect(flagsMode(flags.linking, flags.repair)).toBe(mode)
  })
})
