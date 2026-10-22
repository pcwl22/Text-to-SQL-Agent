# BIRD Text-to-SQL Agent：Codex 分阶段开发计划

> 项目定位：基于 **BIRD Mini-Dev（经典 SQLite SELECT-only 500 题版本）**，构建可复现、可评测、可扩展的 Text-to-SQL Agent。使用 Python 3.11+、LangGraph、sqlglot、SQLite、MCP。普通笔记本 + 在线模型 API 即可开发，不需要自建企业数据库，不需要 GPU。
>
> **起点不是空项目：** 仓库已有 `bird_starter/data.py`、`model.py`、`tools.py`、`workflow.py`、`evaluation.py`、`mcp_server.py`、`run_eval.py` 和基础测试。先审计/增量开发，禁止重写已有可用模块。

## 0. 项目边界与成功定义

**主目标**：可运行 Baseline 和可配置 Agent 工作流，在相同 BIRD 问题上以官方 BIRD EX 评测并出具分组、错误类型和成本报告。可展示一次从自然语言到 SQL、安全执行、错误修复的完整链路。

**MVP 完成标准**：
- 固定经典 BIRD Mini-Dev **数据版本**、SQLite 路径、题目顺序、样本 ID 与 Evidence 策略；数据包只用于本地，不进 Git。
- 无 API 的 `pytest -q` 全绿；至少 10 个 BIRD 样本可分别跑 `baseline`/`full`，结果完整落盘、可按 `case_id` 对齐。
- 有真正的 `stdio` MCP Client ↔ Server 集成测试；不能把 Python 函数直调冒充 MCP 调用。
- 官方 BIRD EX 运行通路可验证，并明确区分 `execution_ok` / `local_result_match_proxy` / `official_ex`。
- 输出完整实验配置和失败分类；只有真实运行数据可以写准确率和提效数值。

**严格约束**：
1. **禁止 Ground Truth 泄漏**：题目里的 `SQL` 只进入离线 evaluator，不进入模型 Prompt、LangGraph State、MCP Server、SQL 修复或在线 Schema 检索。Prompt 保留 `question`、可配置 `evidence`、经允许的 DDL/列说明。
2. **不创建假的 BIRD 数据**：单元测试允许使用 `tmp_path` 自建的微型 SQLite fixture，明确仅用于测试；正式结果来自官方真实数据库。
3. **数据库只读**：SQL AST 校验 + SQLite `mode=ro`/`query_only` + 执行时间/行数限制；不能信任模型输出。
4. **评测公平性**：固定题目集合、模型、版本、Evidence、温度和预算；区别于官方脚本的指标要标识 `proxy`；只在 evaluator 侧比较标准 SQL。
5. **一次只交付一个阶段**：当前阶段完成验收、回归测试、变更总结后再开始下一阶段；别提前引入前端、K8s 或向量数据库。
6. **资源保护**：API 调用默认有 `--limit`/预算上限；模型密钥只走环境变量；大文件、输出结果和 `.env` 不提交 Git。

## 1. 当前仓库基线（已经具备 / 需要补强）

| 模块 | 文件 | 现状 | 当前缺口 |
| --- | --- | --- | --- |
| 数据装载 | `bird_starter/data.py` | `load_cases()`、`find_db()` | 数据版本核验、零样本边界、精确路径/完整性检查 |
| 模型适配 | `bird_starter/model.py` | OpenAI-compatible 同步文本生成 | 重试、用量/成本、结构化错误与模型配置落盘 |
| SQL 工具 | `bird_starter/tools.py` | `sqlglot`、只读连接、超时、截断提示 | 对抗测试、危险输入与异常分类 |
| 工作流 | `bird_starter/workflow.py` | baseline 与 graph；表选择 + 运行报错修复 | 四组消融、状态可观测、可插拔 MCP 工具层 |
| 本地近似分数 | `bird_starter/evaluation.py` | 结果行多重集近似比较 | 官方 EX 适配与对照测试 |
| MCP Server | `bird_starter/mcp_server.py` | 有两个 stdio 工具 | Graph 尚未走真正 MCP Client |
| CLI 评测 | `run_eval.py` | JSONL 和摘要计数 | run manifest、断点续跑、Token、成本、分组报告 |
| 测试 | `tests/test_data_tools.py` | 简单 fixture | Graph Mock 测试、MCP 集成、离线评测格式测试 |

**实验指标与解释**：
- `execution_success_rate`：SQL 能否成功执行，**不是正确率**。
- `official_ex`：官方评测工具输出的执行准确率；必须说明数据版本与评测命令。
- `repair_success_rate`：原始执行失败实例中经修复后执行成功的占比；不要冒充语义正确率。
- `schema_recall`：Schema 筛选是否保留 Gold 所需表（用于**离线分析**，不能反馈到模型）。
- `latency_ms`、`prompt_tokens`、`completion_tokens`、`cost`：每题报告；若供应商不提供真实 Token/费用则记录 `null`，禁止估算成实测值。

## 2. 分阶段执行（建议 10–14 个有效开发日，不含 API 配额/下载等待）

### Phase 0 — 仓库审计与运行基线（半天）

**目标**：确定现有代码可运行的边界，不改架构。

**任务**：
- 阅读 `README.md`、`run_eval.py`、`bird_starter/`、`tests/`，画出当前调用链和状态字段。
- 搭建 Python 3.11+ 虚拟环境，安装 requirements，运行 `pytest -q` 和 `python -m compileall bird_starter run_eval.py`。
- 核查并完善已附带的 `.gitignore`（`.env`、`data/`、`outputs/`、`__pycache__/`、`.venv/`），增加 `docs/BASELINE_STATUS.md` 记录实际成功/失败的命令及原因。
- 对已有代码缺陷列出 issue，优先修复阻碍下一阶段的 bug，不做大重构。

**交付**：`.gitignore`、`docs/BASELINE_STATUS.md`、基础测试绿色状态（若网络不通，标出未安装依赖，不声称通过）。

**验收**：准确记录现有运行情况；代码不读取 Gold SQL 作为生成输入；无凭空 Benchmark 成绩。

### Phase 1 — BIRD 数据接入与版本/完整性校验（1 天）

**目标**：使用**下载好的官方 SQLite**，不创建业务表。

**任务**：
- 固定一个官方经典 500 题 SELECT-only 数据包；记录具体来源 URL、获取日期、文件版本/可得校验信息，不与 780 题 V2 混用。
- 新建 `scripts/validate_dataset.py`，读取 `mini_dev_sqlite.json`，检验 500 条样本、11 个不同 `db_id`、SQL/evidence/question 是否存在，并验证每个 `db_id` 可定位 SQLite 文件且 `PRAGMA integrity_check` 正常。
- 用 `sqlite_master`、`PRAGMA table_info` 及可选 `database_description/*.csv` 输出 schema 预览；所有大库只读。
- 修复 `load_cases(limit=0)` 等输入边界；固定排序/样本 ID，保证相同设置选出同一批题。
- 加 tests：缺字段、坏路径、未下载数据库、重复 ID、非法 DB ID、不同目录结构。

**交付**：`scripts/validate_dataset.py`、数据校验日志样例、`docs/DATASET.md`、增补测试。

**验收命令**：`python scripts/validate_dataset.py --cases data/mini_dev_data/mini_dev_sqlite.json --db-root data/mini_dev_data/dev_databases`；输出明确总样本数、数据库数与异常信息。未下载数据时要给可操作报错，而不是生成假数据。

### Phase 2 — 安全 SQL 工具 + 离线 Baseline（1–2 天）

**目标**：不用 LangGraph，也能稳定从 `question + schema + evidence` 得到 SQL 并安全执行。

**任务**：
- 补强 `tools.py` 单语句校验，禁止写入/DDL/PRAGMA/ATTACH/危险扩展、限制结果行数与执行时长；评估 SQLite `set_authorizer` 和进程资源限额，不要只靠提示词。
- 建立纯函数 Prompt 模板；区分 API 网络/限流/鉴权错误与 SQL 错误，不把 API 错误放入 SQL Repair。
- 保留现有 `baseline`；新增可 Mock 的 Model 测试替身（禁止真实网络调用的默认测试）。
- 新增 `--dry-run` 或 equivalent 离线自检、统一的单题记录结构；确保运行失败也产生 JSONL 记录。
- 在 3–10 条真实 BIRD 题目上手工检查一次 API 调用（仅用户已配置密钥时）。

**交付**：`tests/test_sql_safety.py`、`tests/test_baseline.py`、单题 Demo 记录。

**验收**：破坏性 SQL 被拒绝、只读连接不可写、SQL 超时可退出；`baseline --limit 10` 能记录成功/失败且不崩全批次。

### Phase 3 — LangGraph 可控消融（2–3 天）

**目标**：通过独立实验回答“到底是 Schema 筛选还是 Repair 带来改进”。

**任务**：
- 将现有 `mode=baseline|graph` 扩展成至少四种策略：`baseline`（完整 schema 无 repair）、`schema_only`（schema 筛选无 repair）、`repair_only`（完整 schema + 仅执行错误修复）、`full`（schema 筛选 + 执行错误修复）。兼容旧 `graph` 别名或给迁移提示。
- 保持 LangGraph 状态最小化，明确每个节点输入输出与超时/重试上限；`attempts` 意义统一。
- 模型表筛选输出严格校验，对非法表名/无表/JSON 格式错误提供保守回退；考虑外键关联表召回。
- 修复节点只接收 SQL 的**语法/运行错误**，不读取标准 SQL；执行成功但语义不对的 SQL 不应被错误地判成修复成功。
- 增加 mock Model 单测和错误分支测试：正常、schema 回退、执行报错、修复成功、重试耗尽、模型 API 错误。

**交付**：四模式 CLI、状态流测试、`docs/WORKFLOW.md`（Mermaid 工作流）。

**验收**：相同样本的四种模式输出均可按 `case_id` 对齐；每种模式实际调用节点符合配置；最大修复次数生效、不会无限循环。

### Phase 4 — 真正的 MCP Client ↔ Server（1–2 天）

**目标**：从“函数封装成 MCP”进化到“Graph 真正通过协议访问 MCP 工具”。

**任务**：
- 保留 stdio MCP Server 的 `get_schema(db_id)` / `query_sqlite(db_id, sql)`，严格限制数据库根目录与工具调用结果大小。
- 新建 `bird_starter/mcp_client.py` 或 `bird_starter/backend.py`：提供 `LocalSQLiteBackend` / `MCPBackend` 同一接口，Graph 不依赖具体传输方式。
- 对接官方 MCP Python SDK，复用会话/进程，不允许每题每个工具都新启 MCP Server；明确同步/异步边界。
- 添加本地 stdio 集成测试，真实 `call_tool` 获取 schema 并执行安全 SELECT；加失败/超时测试。
- CLI 添加 `--backend local|mcp`，默认 `local` 便于单元测试；端到端复现 MCP 时指定 `mcp`。

**交付**：`bird_starter/mcp_client.py` / `backend.py`、MCP 集成测试与使用说明。

**验收**：同一个 fixture，local 与 mcp 返回等价数据；Graph 确实经 `ClientSession.call_tool` 访问工具；`mcp` 运行不会暴露 Gold SQL。

### Phase 5 — 官方 EX + 复现实验报告（2 天）

**目标**：有可信成绩，避免“本地 proxy = 官方 EX”的错误。

**任务**：
- 新增 `scripts/export_predictions.py`：按官方评测所需格式和题目顺序导出预测 SQL；无预测/失败按官方要求处理，不能偷偷过滤错题。
- 新增 `scripts/run_official_ex.py` 或清晰 shell/README 调用，适配官方版本对应 `evaluation_ex.py`；不修改官方评分定义。如需兼容补丁，记录原因和 diff。
- 运行 manifest 落盘：数据版本、样本数、题目 ID、模型名、接口/温度、Evidence、Prompt 版本、运行时间、预算与源码版本。
- 新增 `scripts/report.py`：汇总总体 EX、按 difficulty/database、执行成功率、无效 SQL 比例、修复成功率、延迟、Token、费用、未知/缺失数据；有分母和置信解释。
- 固定预实验样本/最终评测样本，避免反复对最终评测集改 Prompt 后仍宣称无偏；在报告中披露调参使用的数据。

**交付**：官方 EX 运行说明、`outputs/<run_id>/predictions.*`、`manifest.json`、`summary.md`、聚合 CSV/JSON。

**验收**：官方脚本能跑出指标；同一配置可复现实验流程；任何报告数字可以追溯到输入日志和官方评分文件。

### Phase 6 — 稳定性、评测消融与案例分析（1–2 天）

**目标**：拿到可解释的改进证据，而非只有一次排行榜成绩。

**任务**：
- 固定一套 10 题烟测 + 50 题开发集 + 最终指定集合，按模型/题目/参数锁定四组 A/B/C/D 实验。
- 归因错误：`schema_missing`、`join_error`、`filter_omission`、`aggregation_error`、`syntax_error`、`timeout`、`unknown`（分类依据和人工抽检记录）。
- 分析至少 5 个真实失败案例与 3 个真正修复成功的案例；不能使用模型编造样本冒充真实数据。
- 对比 EX vs 延迟/Token 花费，判断 Graph 增益是否值得额外成本；如无增益，诚实分析原因。

**交付**：`reports/ABLATION_REPORT.md`、`reports/CASE_STUDIES.md`、可复现实验命令。

**验收**：具备相同题目的四组可核对日志、官方 EX、成本、失败分类；结果不要求必须优于 Baseline。

### Phase 7 — 可演示工程交付（1–2 天）

**目标**：面试官从 clone 到演示能看懂、能复现、能追问。

**任务**：
- 添加一个简洁 FastAPI **或** CLI 单题演示，输入 `db_id + question + evidence`，展示 Schema、候选 SQL、执行结果、错误与修复 Trace（MVP 优先 CLI，不强制前端）。
- `README.md` 加项目架构、安装、BIRD 数据来源、环境变量模板、运行示例、MCP 启动、官方 EX、限制/安全声明。
- 增加 GitHub Actions（纯离线 pytest；不下载 BIRD、不调用付费 API）、`.env.example`、基础日志脱敏。
- 文档写清单节点 Graph 与真正独立部署多智能体的区别，避免包装性技术描述。

**交付**：可展示的 README、架构图、测试 CI、演示脚本、简历表述草案。

**验收**：新的开发者可以凭 README 跑离线单测；准备好 BIRD + API 后跑单题/10 题/正式评测；不会把 token、Gold SQL 或数据库大文件误提交。

## 3. 每个阶段都要执行的质量闸门

交付前 Codex 必须报告：

```text
[Stage] Phase N — 标题
[Files] 新增/修改文件及原因
[Tests] 实际执行的命令和原始结果；未运行的原因
[Proof] 与该阶段验收条件对应的证据
[Risks] 尚存问题、兼容性风险、下一阶段依赖
[Next] 是否建议进入 Phase N+1（只能建议，不能擅自执行）
```

统一验收命令：

```bash
python -m compileall bird_starter run_eval.py
pytest -q
# Phase 1 起（安装了官方数据后）
python scripts/validate_dataset.py --cases data/mini_dev_data/mini_dev_sqlite.json --db-root data/mini_dev_data/dev_databases
# Phase 2 起（用户提供 API 配置后，付费调用前需确认预算）
python run_eval.py --mode baseline --limit 10 --out outputs/baseline_10.jsonl
# Phase 3 起
python run_eval.py --mode full --limit 10 --out outputs/full_10.jsonl
# Phase 4 起（完成接入后）
python run_eval.py --mode full --backend mcp --limit 3 --out outputs/mcp_3.jsonl
```

> 未实现的 CLI 选项**不应在对应阶段之前强行运行**。运行报错时先保留终端输出，再修复；不能通过删除测试或跳过验收伪装成功。

## 4. 推荐分支与提交切分

- `chore/phase-0-repo-audit`
- `feat/phase-1-bird-loader`
- `feat/phase-2-safe-baseline`
- `feat/phase-3-ablation-graph`
- `feat/phase-4-mcp-backend`
- `feat/phase-5-official-evaluation`
- `feat/phase-6-reporting`
- `feat/phase-7-demo`

每阶段尽量单独 PR/commit，提交说明列出测试、输入数据版本与是否调用过付费 API。

## 5. 给 Codex 的统一任务模板（每次只替换 N）

```text
你现在在 BIRD Text-to-SQL Starter 仓库中。先读取 AGENTS.md、CODEX_PLAN.md、README.md，检查现有代码，不要重新造项目。
本轮只实现 CODEX_PLAN.md 的 Phase N，严格遵守该阶段范围、交付和验收标准。

执行要求：
1. 先列出你读到的现状、可复用模块、需要改动的文件及最小实现方案。
2. 直接实现当前阶段，保留已有有效接口，并为改动补充可离线运行的 pytest。
3. 禁止读取或传给 Agent 任何 Gold SQL；仅 evaluator 能使用标准答案。
4. 不下载/提交大型 BIRD 数据集；不索取或写入明文密钥；没有 API 环境时用 Mock 测试并报告局限。
5. 运行当前阶段适用的验收命令。不能运行的写明原因。
6. 结束时按 [Stage]/[Files]/[Tests]/[Proof]/[Risks]/[Next] 报告，不要自动开始 Phase N+1。
```

**首次推荐 Prompt：**

```text
请从 Phase 0 开始。先审计并运行仓库已有测试；只完成 Phase 0 的 `.gitignore` 核查、BASELINE_STATUS、必要的阻断性 bug 修复。不要添加向量库、前端、真实 MCP Client 或官方 EX 适配，那些属于后续阶段。
```

## 6. 里程碑与取舍

| 里程碑 | 阶段 | 价值 | 当前优先级 |
| --- | --- | --- | --- |
| M1：稳定离线 Baseline | 0–2 | 真实数据 + 可信 SQL 安全链路 + 可运行基线 | **P0，先完成** |
| M2：可解释 LangGraph | 3 | 4 组消融，证明 Schema/Repair 的独立影响 | **P0** |
| M3：工业工具调用 | 4 | 真正 MCP 协议边界与集成测试 | **P1** |
| M4：有公信力的评测 | 5–6 | 官方 EX、成本、失败归因与案例 | **P0；排期可早于 MCP** |
| M5：面试演示与工程化 | 7 | 一键运行、演示、CI、简历证据 | **P1** |

**可调整顺序**：如果预算有限或面试临近，可以优先完成 Phase 0→1→2→3→5→6，再补 Phase 4 和 Phase 7。不要为追求“Multi-Agent / MCP”名词牺牲可复现的评测。

## 7. 官方参考

- BIRD Mini-Dev 官方仓库：https://github.com/bird-bench/mini_dev
- BIRD Mini-Dev Hugging Face：https://huggingface.co/datasets/birdsql/bird_mini_dev
- Codex `AGENTS.md` 官方说明：https://developers.openai.com/codex/guides/agents-md
