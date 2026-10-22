import { describe, it, expect } from 'vitest'
import { experiments, provenance, repairDiagnostic, percent } from '../src/data/experiments'
import { pairedCases } from '../src/data/pairedCases'
import { demoCases } from '../src/data/demo'
import { offlineRepository } from '../src/api/repository'
import { createDisabledClient } from '../src/api/futureClient'

describe('frozen evidence consistency', () => {
  it('retains a fixed denominator and 11 database coverage', () => {
    expect(pairedCases).toHaveLength(33)
    expect(new Set(pairedCases.map(c => c.caseId)).size).toBe(33)
    expect(new Set(pairedCases.map(c => c.dbId)).size).toBe(11)
    expect(provenance.manifestSha256).toBe('7136f72aa9e0b78ccab8b29dfb077247a082e541604b4f8841fede2a63ba6dd8')
  })
  it('pairs official marks with aggregate A/B totals', () => {
    expect(pairedCases.filter(c => c.a)).toHaveLength(experiments[0]!.officialCorrect!)
    expect(pairedCases.filter(c => c.b)).toHaveLength(experiments[1]!.officialCorrect!)
    expect(pairedCases.filter(c => c.a !== c.b).map(c => c.caseId)).toEqual([1317])
    expect(pairedCases.filter(c => c.a && c.b)).toHaveLength(18)
    expect(pairedCases.filter(c => !c.a && !c.b)).toHaveLength(14)
  })
  it('never invents C/D outcomes', () => {
    for (const e of experiments.slice(2)) {
      expect(e.runId).toBeNull(); expect(e.officialCorrect).toBeNull(); expect(e.requests).toBeNull()
      expect(percent(e.officialCorrect)).toBe('未运行')
    }
  })
  it('keeps repair diagnostic separate and real token counts intact', () => {
    expect(repairDiagnostic.cases).toBe(8)
    expect(experiments.map(e => e.tokens)).toEqual([45773, 84432, null, null])
    expect(percent(19)).toBe('57.58%')
    expect(percent(18)).toBe('54.55%')
  })
  it('exports only allowlisted score metadata, no SQL or question', () => {
    expect(Object.keys(pairedCases[0]!).sort()).toEqual(['a', 'b', 'caseId', 'dbId', 'difficulty'])
    expect(JSON.stringify(pairedCases)).not.toMatch(/question|evidence|SQL|authorization|api_key/i)
  })
  it('labels all walkthrough data DEMO with independent IDs', () => {
    for (const c of demoCases) { expect(c.source).toBe('DEMO'); expect(c.id).toMatch(/^demo-/) }
    expect(demoCases.find(c => c.error)?.rows).toHaveLength(0)
  })
  it('isolates mutable adapter results from the static snapshot', () => {
    const copy = offlineRepository.getExperiments()
    copy[0]!.tokens = 0
    expect(offlineRepository.getExperiments()[0]!.tokens).toBe(45773)
  })
  it('future Axios transport rejects before HTTP, including local requests', async () => {
    const client = createDisabledClient()
    await expect(client.post('/generate', { question: 'test' })).rejects.toThrow('Live Inference 尚未开放')
  })
})
