export type ExperimentGroup = 'A' | 'B' | 'C' | 'D'
export type WorkflowMode = 'baseline' | 'schema_only' | 'repair_only' | 'full'
export type Theme = 'dark' | 'light'
export interface Experiment {
  group: ExperimentGroup
  name: string
  mode: WorkflowMode
  runId: string | null
  officialCorrect: number | null
  generated: number | null
  executed: number | null
  proxyCorrect: number | null
  tokens: number | null
  requests: number | null
  latency: number | null
  p50: number | null
  p95: number | null
}
export interface PairedCase { caseId: number; dbId: string; difficulty: string; a: boolean; b: boolean }
export interface DemoCase {
  id: string; name: string; database: string; question: string; evidence: string
  schema: string; sql: string; columns: string[]; rows: Record<string, string | number>[]
  source: 'DEMO'; error: string | null
}
export interface WorkflowNodeInfo { id: string; label: string; subtitle: string; description: string; tool: string; state: string[] }
