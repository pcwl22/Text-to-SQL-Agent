# 实验可观测性与离线错误分析 V1

本阶段补强新实验的记录与解释能力，不改变生成/Linking/Repair Prompt、正式 A/B 配置、只读 SQL 限额或评分定义。旧 A/B 和独立 Repair 诊断保持冻结。尚未开展 Linking 算法优化、正式 C/D、500 题实验、CI、MCP Client 或在线推理。

## 新实验记录

`scripts/run_ablation.py` 继续使用原接口。新 `batch.config.json` 增加 `observability_version` 和生成链路相关源码的 `source_sha256`。每题新增以下字段：

| 字段 | 含义 |
| --- | --- |
| `prompt_versions` | Generation、Linking、Repair 模板版本，与冻结配置校验一致 |
| `schema_observation.selection` | 完整有效表列、原始请求的合法表列、补全关系和新增表 |
| `schema_observation.selected_schema` | 准备给生成器的实际 Schema 文本，保留合法 JOIN 提示 |
| `selected_schema_sha256` / `persisted_schema_sha256` | 准备文本与脱敏后落盘文本各自的 UTF-8 SHA256 |
| `persistence_status` | `EXACT` 或 `REDACTED`；后者不能逐字恢复准备文本 |
| `fallback_reason` | 原有回退/禁用/dry-run 原因，不保存非法响应正文 |
| `node_metrics` / `trace` | 沿用 Graph 节点信息，Baseline 增加生成调用/用量及执行时间 |

Baseline 直接函数入口的记录包含完整 DDL；批量实验在记录侧补全同一固定数据库元数据的完整表列选择。新日志体积随合法 Schema 大小增加。结果可能包含数据库结构和模型 SQL，必须留在 ignored `outputs/`，不作为浏览器或公开数据源。前端白名单摘要接口保持原样。

记录证明的是 Schema **准备内容**。只有相应生成节点的调用指标与请求审计能说明是否发生请求尝试；即使 Schema 为 EXACT，dry-run、Prompt 限额或预算拒绝也不能被描述为模型已收到该输入。未知 Tokens 保持 null。结果写入仍沿用既有逐题 flush/fsync；进程在结果落盘前崩溃时，该题 Schema 可能缺失，本阶段不提供请求前 Schema WAL 或自动重放。

没有记录完整 Prompt、问题/Evidence、任意原始 Linking JSON、Gold 或执行结果具体行。合法选择与筛选 DDL足以解释列裁剪和补全结果；它们不能恢复非法模型响应的原文，也不能单凭其内容证明模型错误的因果来源。脱敏优先于逐字重现。

## 零模型请求离线报告

从项目根目录运行；历史私有文件存在时可执行，缺失时明确失败。输出必须是 `outputs/` 下全新目录：

```powershell
$ProjectPython = Join-Path $PWD '.venv/Scripts/python.exe'
$ErrorReport = 'outputs/error_review_' + [guid]::NewGuid().ToString('N')
& $ProjectPython scripts/analyze_errors.py `
  --run A=outputs/phase3c_live/A_v1 `
  --run B=outputs/phase3c_live/B_v1 `
  --official A=outputs/phase3c_live/A_v1/official_ex/summary.json `
  --official B=outputs/phase3c_live/B_v1/official_ex/summary.json `
  --out-dir $ErrorReport
```

不传 `--official` 也能分析生成/执行结果，但官方得分保持 null，不能把 Proxy False 当作官方语义错误。`--suite-config` 可指定另一份已冻结配置，默认沿用 `configs/phase3c/ablation_v1.json`。

分析器不接受题库或数据库参数，不执行 SQL、不构造 Model/Agent、不新算官方 EX。读取既有配置、Manifest 身份、请求审计、结果和可选评分，只写新的 `analysis.json` / `analysis.md`；输出存在则拒绝。读取前后核对输入哈希，包括结果缺失时的文件存在性。

官方评分必须匹配结果文件 SHA256、Manifest、measurement kind、固定官方 commit/源码 SHA256、评分协议及全部题目的顺序和身份；只对总分相同的文件不能通过检查。缺失结果保留在计划分母，请求审计未归属/未知结局继续标记，不能推导免费重试机会。哈希绑定提供一致性检查，不是数字签名或评分真实性的独立证明。

## 错误分类与人工归因

报告优先识别未运行、dry-run、生成截断、生成失败、结果截断、语法错误、超时、结构错误和安全拒绝。成功修复后的题目按最终结果分类，同时保留 Repair 触发/次数与节点开销。

- 完整成功执行且官方记录为 `MISMATCH`：`semantic_mismatch_unattributed`。
- 官方 SQL/worker/超时等失败：`official_evaluation_failure_or_unknown`，不自动认定模型语义错误。
- 仅有 Proxy False：`proxy_unmatched_or_evaluator_error`，因为历史 evaluator 合并了两种情况。
- 旧日志没有实际 Schema：`UNKNOWN_NOT_RECORDED`，不从当前源码、哈希或 Gold 反推历史列投影。
- 缺表/列的离线 Recall 证据只标为“存在缺失”，不能自动升级为充分原因。

可用 `--annotations PATH` 加载人工归因。JSON 顶层为 `annotations` 数组，每项必须有 `experiment_group`、`case_id`、`db_id`、对应组结果的 `source_result_sha256`、`category`、`confidence`、`evidence` 和 `reviewer`。类别为 `schema_missing`、`join_error`、`filter_omission`、`filter_extra`、`aggregation_error` 或 `unknown`；置信等级为 `CONFIRMED`、`LIKELY`、`UNKNOWN`。证据和审阅人必填，重复/错组/错哈希拒绝。

人工语义归因仅能附在完整执行的官方 mismatch 上，作为独立审阅声明保存，不覆盖观测分类；没有人工记录的错误保持未归因。不要在注释中粘贴 Gold、密钥或原始请求。报告位于私有 outputs，不写回 Agent State、Prompt、修复路由或前端。输出包含输入哈希、分母、逐题类别、人工分类计数、节点用量完整性与延迟分布。

现有 33 题已多次查看，应视作开发集。下一轮算法验证需预先冻结新的验证样本、参数和预算；不能在现有题目上调参后继续称其为无偏最终评测。
