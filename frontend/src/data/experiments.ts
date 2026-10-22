import type { Experiment } from '../types'

// Hand-curated aggregate-only snapshot of the published report. No raw logs or Gold SQL.
export const provenance = {
  source: 'docs/EXPERIMENT_RESULTS.md',
  url: 'https://github.com/pcwl22/Text-to-SQL-Agent/blob/92f019345f27fb90926fa8a7a3738acf1fe0ecbe/docs/EXPERIMENT_RESULTS.md',
  commit: '92f019345f27fb90926fa8a7a3738acf1fe0ecbe',
  manifest: 'bird-dev33-v1',
  manifestSha256: '7136f72aa9e0b78ccab8b29dfb077247a082e541604b4f8841fede2a63ba6dd8',
  model: 'deepseek-4.1-flash',
  denominator: 33,
  databases: 11,
} as const
export const experiments: Experiment[] = [
  { group: 'A', name: 'Baseline', mode: 'baseline', runId: '343c45833caa427c892f7a7887a29daf', officialCorrect: 19, generated: 31, executed: 31, proxyCorrect: 19, tokens: 45773, requests: 33, latency: 8.201, p50: 5.806, p95: 18.875 },
  { group: 'B', name: 'Schema-only', mode: 'schema_only', runId: '0529e0de668a4b3aa6acad2cb6bcb6a2', officialCorrect: 18, generated: 33, executed: 33, proxyCorrect: 18, tokens: 84432, requests: 66, latency: 45.007, p50: 39.663, p95: 87.310 },
  { group: 'C', name: 'Repair-only', mode: 'repair_only', runId: null, officialCorrect: null, generated: null, executed: null, proxyCorrect: null, tokens: null, requests: null, latency: null, p50: null, p95: null },
  { group: 'D', name: 'Full Graph', mode: 'full', runId: null, officialCorrect: null, generated: null, executed: null, proxyCorrect: null, tokens: null, requests: null, latency: null, p50: null, p95: null },
]
export const schemaStats = { tablesFound: 68, tablesRequired: 68, columnsFound: 163, columnsRequired: 165, valid: 32, fallback: 1, compression: 78.04, generatorTokenReduction: 64.48, linkingTokens: 61352 }
export const repairDiagnostic = { assertions: 8, cases: 8, requests: 8, tokens: 7848, runId: '40d2955515ef45f7b5bcf573d3d43f42' }
export const percent = (n: number | null, d = 33): string => n === null ? '未运行' : `${(n / d * 100).toFixed(2)}%`
export const number = (n: number | null): string => n === null ? '—' : n.toLocaleString('en-US')
