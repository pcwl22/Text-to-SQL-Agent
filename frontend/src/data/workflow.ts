import type { WorkflowMode, WorkflowNodeInfo } from '../types'

export const nodeInfo: WorkflowNodeInfo[] = [
  { id: 'inspect', label: 'Inspect', subtitle: '读取只读 Schema', description: '读取数据库表、列、PK/FK；完整 Schema 保持不变。Baseline 为直接函数入口，非 LangGraph。', tool: 'read_schema / get_schema', state: ['question', 'evidence', 'full_schema'] },
  { id: 'link', label: 'Schema Linking', subtitle: '结构化选择与回退', description: '验证表列真实存在，补全必要关系；非法选择回退完整 Schema。关闭时不调用模型，Graph 仍保留 pass-through 节点。', tool: 'model.ask → validate_selection', state: ['chosen_schema', 'schema_selection', 'schema_linking'] },
  { id: 'generate', label: 'SQL Generation', subtitle: '只接收白名单输入', description: '由 Question、Evidence、验证后的 Schema 生成候选 SQL；不接收 Gold SQL。', tool: 'generation_prompt → model.ask', state: ['generated_sql', 'generation_success'] },
  { id: 'validate', label: 'SQL Validation', subtitle: '确定性安全边界', description: '单语句、只读 AST 检查。Baseline/Schema-only 在 execute_select 内校验；启用 Repair 时为显式 validate 节点。', tool: 'validate_candidate / execute_select', state: ['current_sql', 'error_category'] },
  { id: 'execute', label: 'SQL Execution', subtitle: 'SQLite 只读 / 有界', description: 'mode=ro、query_only、authorizer、执行时间与结果大小限制；浏览器不连接数据库。', tool: 'execute_select', state: ['execution_ok', 'output_truncated'] },
  { id: 'classify', label: 'Error Classification', subtitle: '确定性错误准入', description: '仅 Repair 模式存在。语法、缺表、缺列、歧义列可修复；危险 SQL、超时、length 和语义错误不触发。验证失败直接进入分类。', tool: 'classify_error / REPAIRABLE', state: ['repair_trigger', 'execution_history'] },
  { id: 'repair', label: 'Optional Repair', subtitle: '至多一次 / 无重试', description: '结构性错误且 repair_attempts=0 时调用一次；返回 validate，再执行和分类。成功执行不等于语义正确。', tool: 'repair_prompt → model.ask', state: ['repaired_sql', 'repair_attempts'] },
]
export const modeNames: Record<WorkflowMode, string> = { baseline: 'A · Baseline', schema_only: 'B · Schema-only', repair_only: 'C · Repair-only', full: 'D · Full Graph' }
export function modeFlags(mode: WorkflowMode) { return { linking: mode === 'schema_only' || mode === 'full', repair: mode === 'repair_only' || mode === 'full' } }
export function flagsMode(linking: boolean, repair: boolean): WorkflowMode { return linking ? (repair ? 'full' : 'schema_only') : (repair ? 'repair_only' : 'baseline') }
