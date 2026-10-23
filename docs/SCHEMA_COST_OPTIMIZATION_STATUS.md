# [Stage] P1A 离线成本优化

已实现并验证一项优化：**Adaptive Schema Linking Gate**。本轮仅离线分析与预检；没有真实模型请求，没有新 SQL 或 Adaptive 官方 EX，没有启动新的真实 A/B/C/D。P0 未提交工作全部接续保留。

## [Design] 单因素优化与文件

唯一行为变化是在 Linking 模型调用前，用完整 Schema 的 UTF-8 字节数决定是否跳过该调用。Generation / Linking / Repair Prompt、选择 JSON 契约、SQL AST/词法与 SQLite 只读工具均未修改。

- `bird_starter/schema_routing.py`：不可变配置、严格验证、纯元数据路由。
- `schema_workflow.py`：增加构图参数 `adaptive_linking`，默认 `None`（关闭）；增加路由记录，复用原选择、回退、生成及执行路径。
- `ablation.py`：仅增加路由结果字段及新模块源码指纹；预算、请求审计及批次控制流保持原样。旧运行器没有自动启用实验 Gate 的开关，旧 A/B 配置保持原字节。
- `configs/p1a/adaptive_schema_gate_v1.json`：独立实验配置，显式 enabled=true；不会被现有四组 CLI 自动加载。
- `schema_cost_analysis.py` / `scripts/analyze_schema_cost.py`：固定数据指纹、P0 重建、三种零请求预检、估算及历史实际用量分离。
- `tests/test_schema_routing.py`、`tests/test_schema_cost_analysis.py`：新增离线测试；README / `.gitignore` 仅补入口与本报告。

Baseline 源码与本轮开始时的 P0 版本逐字一致。默认 Schema-only、Repair-only、Full Graph 沿用原行为。实验 Gate 显式开启时，仅允许 `schema_linking=True, sql_repair=False`；对 Repair-only / Full Graph 启用会拒绝配置，避免本轮扩展它们的策略。

运行时程序接口为 `build_schema_graph(model, adaptive_linking=load_gate(path))`；不传参数即保留原行为。本轮所有真实库调用均为 `dry_run=True`，不会构造模型 SDK、执行候选 SQL 或请求任何服务。低层图接口仍复用既有 Model/RequestLog，不提供新授权或自动重放能力。

## [Baseline] 历史实际 A/B 成本

以下是冻结真实记录与既有官方报告，**不属于本轮新测量**：

| 历史指标 | A Baseline | B Schema-only |
| --- | ---: | ---: |
| 官方 EX | 19/33 | 18/33 |
| 实际总 Tokens | 45,773 | 84,432 |
| Agent 平均延迟 | 8.201 秒 | 45.007 秒 |
| 已对账 HTTP 尝试 | 33 | 66 |
| Schema fallback | 0/33 | 1/33（3.03%） |

B Linking 实际 61,352 Tokens，Generator 实际 23,080 Tokens，总 Token 比 A 增加 84.46%。本轮从已有逐题用量、延迟与完整审计重新聚合核对，没有重评官方 EX。缺少原始账单与可靠单价，实际费用仍 UNKNOWN。

## [Routing] 策略、阈值及瓶颈依据

只读获取 11 个不同数据库的 DDL，每库只计一次，不用题目、Gold、评分或预测来选择阈值：

| 数据库 | 表 | 可见字段 | 完整 DDL UTF-8 字节 | Adaptive 决策 / 题数 |
| --- | ---: | ---: | ---: | --- |
| toxicology | 4 | 11 | 975 | SKIP / 3 |
| debit_card_specializing | 5 | 21 | 1,014 | SKIP / 3 |
| student_club | 8 | 48 | 2,214 | USE / 3 |
| superhero | 10 | 31 | 2,255 | USE / 3 |
| financial | 8 | 55 | 2,940 | USE / 3 |
| thrombosis_prediction | 3 | 64 | 3,004 | USE / 3 |
| california_schools | 3 | 89 | 3,937 | USE / 3 |
| codebase_community | 8 | 71 | 4,538 | USE / 3 |
| card_games | 6 | 115 | 4,752 | USE / 3 |
| formula_1 | 13 | 94 | 5,380 | USE / 3 |
| european_football_2 | 7 | 199 | 7,260 | USE / 3 |

排序后最大的相邻体积比为 2,214 / 1,014 = 2.1834，采用其较小端点 **1,014 字节（含边界）**作为保守小 Schema 簇的上限。不四舍五入、不按正确率搜索阈值；表/列数量仅作诊断，不形成额外规则。该数据驱动分界是开发集上的候选策略，不是普适或准确率最优阈值。

小 Schema 本身只有约 244–254 个 byte/4 估算 Tokens，Linking 还要重复发送问题、Evidence、JSON 键和外键元数据。6 道跳过题的 Linking 输入内容共估计 3,279 Tokens；在固定 P0 Mock 列选择的情景下，改用完整 Schema 增加 Generator 输入估计 1,268 Tokens，二者相减为 2,011 个输入估算 Tokens。因此先跳过最小簇有离线成本依据；没有修改输出格式或 Prompt 以追求额外收益。

历史 B 记录的完整 Schema 哈希与当前只读元数据一致。P0 已保存的 B 组 33 条完整选择全部可重建；它们是 **Mock 选择**，不能当作真实 Linking 响应。P0 示例每题筛选后 Schema 为 56–679 字节；报告逐题保留表列数量、原/筛选大小及差值所需字段。

Linking 输出结构开销以 P0 合法请求表列和 JOIN 重新序列化到旧 JSON 契约，分别统计标识符与结构字节。这是 canonical 结构估计，不是模型原始响应长度。历史原始 Linking JSON / 精确真实列投影仍 UNKNOWN，不由当前 DDL 或哈希补造。

## [Offline Results] 33 题三种配置

三种配置各有 33 条完整 dry-run 轨迹，共 **99 条**，均未生成 SQL、未调用模型、未预约真实请求：

| 离线规划 | Baseline | 原始 Schema-only | Adaptive |
| --- | ---: | ---: | ---: |
| SKIP / USE Linking | 33 / 0 | 0 / 33 | 6 / 27 |
| 计划 Linking 请求 | 0 | 33 | 27 |
| 计划 Generator 请求 | 33 | 33 | 33 |
| 计划总请求 | 33 | 66 | 60 |
| 本轮实际 Linking / 模型请求 | 0 / 0 | 0 / 0 | 0 / 0 |
| Linking 输入估算 Tokens | 0 | 46,569 | 43,290 |
| Generator 完整 Schema 预检输入估算 | 32,155 | 32,155 | 32,155 |

计划请求假设每题完成一次 Generation，USE 时另有一次 Linking；失败、预算或系统停止可使未来实际数更少。**这些计划数不是新增预算授权，也不是测量到的 HTTP 尝试。** Gate 不修改现有共享预算上限或预约计数。

估算方法统一为 `ceil(sum(message_content_utf8_bytes) / 4)`，不是供应商 tokenizer，未计入 chat envelope，也不含任何输出 Tokens。dry-run 中 Linking 不运行，因此各模式准备的 Schema 都是完整 DDL；USE 题的未来真实筛选 Schema 大小、选择质量和总 Prompt 大小仍未知。

为比较潜在输入成本，另列一个明确的 **P0 Mock 选择固定情景**：USE 题沿用 P0 Mock 的列选择，SKIP 题改为完整 Schema，模型输出长度全部未知：

| 情景输入估计 | Baseline | 原始 Schema-only | Adaptive |
| --- | ---: | ---: | ---: |
| Generator 输入 Tokens | 32,155 | 4,937 | 6,205 |
| Linking + Generator 输入 Tokens | 32,155 | 51,506 | 49,495 |

该情景下 Adaptive 比原始 Schema-only 少 2,011 个输入估算 Tokens（约 3.90%）；仍高于 Baseline。它不是实际消耗预测、价格估计或准确率收益，不能与历史真实总 Tokens 混为同一口径。

额外的**历史用量反事实估算**：6 道跳过题在旧 B 中的 Linking 实际用量合计 6,630 Tokens。如果省去这些调用，最多只能说明这部分历史工作不再安排；不能直接从 84,432 中相减后声称新的实际总成本。新的 Generator 输入、输出与供应商行为可能抵消节省。

数据有效性：500 题源文件 SHA256、固定 33 题 Manifest、11 库 SHA256、P0 / A / B 运行身份及完整审计均核对一致。没有用官方判定制定路由。

回退：跳过 Linking 是主动路由，`fallback=False`；USE 路径保留原 JSON/名称/关系校验、Prompt 上限与完整 Schema 回退。历史 B fallback 为 1/33；新的真实 fallback 比例未测。离线夹具验证无效选择时仍回退完整 Schema，调用数仍受原审计与预算限制。

Schema 召回风险：SKIP 保留完整表列，不引入裁剪遗漏；但更多无关字段可能影响生成，不能保证语义正确。USE 仍有既有筛选风险。P0 Mock 极小投影只能说明工程链路，不证明真实召回率；本轮未计算新的 Gold Recall。

## [Tests] 实际执行

使用现有 Python 3.13.5 虚拟环境、pwsh，未安装新依赖：

| 实际命令 / 检查 | 结果 |
| --- | --- |
| Gate + P0 / Linking / Repair 首批测试 | 127 passed，5.17 秒 |
| 新增两个测试文件 | 43 passed，4.77 秒 |
| 全部 Python 离线测试：`-m pytest -q -rs --tb=short` | **685 passed、8 skipped、0 failed，119.49 秒，exit 0** |
| `-m compileall bird_starter run_eval.py scripts/analyze_schema_cost.py` | exit 0 |
| `-m pip check` | No broken requirements found，exit 0 |
| 固定 33 题真实元数据三模式预检 CLI | exit 0，99 条轨迹，真实 API 0 |
| 独立验收脚本 | 99 条 P0 格式轨迹和路由重建、11 库及全部历史产物哈希检查通过 |
| `git diff --check` | exit 0 |

覆盖开关关闭兼容、大小/UTF-8/边界、非法配置、缺库、无效选择 fallback、调用次数、零额外请求、Gold 输入白名单、破坏性 SQL 拒绝、共享预算、HTTP 前持久化、fsync 失败阻止调用、硬崩溃预约不退还及禁止自动恢复授权。

8 项 skipped 全为原有 `bird_integration` opt-in：真实数据验证、Baseline 预检、两项 Schema、Repair、四组 Mock、官方控制及独立诊断。本轮保留关闭，避免启动与 P1A 无关的既有实验。它们不是失败，也未记为 passed。本轮单独运行的是上述 33 题零请求成本预检；不将旧阶段的集成通过数算入本轮。前端源码未变化，因此未重跑浏览器验收。

## [Gold Isolation] PASS

Gate 仅接受 Schema snapshot 和配置，不接受题目 ID、问题、Gold、EX 或模型输出。离线分析在 JSON 解码时只保留 generation 白名单字段，Graph Input 继续使用 `AgentInput` 白名单。离线分析不构造 evaluator、不执行 Gold、不生成或导出参考答案；测试中的 Gold sentinel 未进入 Prompt、State 或记录。

## [Audit] 新记录完整性

`schema_routing` 记录 `routing_mode`、`routing_decision`、`routing_reason`、表数、列数、原/筛选体积、阈值、`linking_skipped`、`actual_linking_attempts`、attempt IDs 与 `prompt_estimation_method`。体积单位明确为 UTF-8 字节，模式为 LEGACY / ADAPTIVE。

`actual_linking_attempts` 采用既有适配器 SDK 尝试计数，不是 HTTP 送达或账单证明；准备失败仍可能占预约，不能据零 SDK 调用退款。持久化审计继续作为独立权威记录。dry-run 的 USE 是计划决策，actual attempts=0；对应 selected_schema_size 是已准备的完整 DDL，不冒充模型筛选结果。

P0 的完整表列、Schema 文本、原/落盘哈希与脱敏状态保留。新增 99 条记录均可由保存的完整选择逐字重建；33 条 P0 Mock 记录也核验一致。报告保存数据、配置、运行输入及源码指纹。旧记录没有的路由/选择信息继续缺失，未回填历史。详细轨迹仅写本地 ignored `outputs/`，没有提供给前端。

证据目录为 `outputs/p1a_schema_cost_v1/`：`analysis_final/analysis.json`、`analysis_final/preflight.jsonl`、测试/编译日志、`protected_before.json`、`workspace_before/` 以及 `acceptance.json`。`analysis_v1/` 为完善轨迹字段前的中间预检，保留且不覆盖，最终验收使用 `analysis_final/`。

## [Protected Files] 哈希与 Git

开始前快照 **9,493 项**。其中 **9,488 项非根 pytest 缓存文件 SHA256 全部不变**，包含 9,381 个既有输出文件、99 个数据文件（含 11 个 SQLite）和 8 个既有配置文件；历史目录清单无增删，旧 A/B / Repair / P0 输出未改动。SQL 工具、请求审计、模型、Prompt、Schema Linking、Repair 与数据模块亦核对未改。

首批定向 pytest 沿用项目默认缓存，更新了 `outputs/.pytest_cache/v/cache/nodeids` 一项测试节点缓存。该变化单列在验收 JSON 中，不属于历史实验结果，未伪称整个快照全部不变；随后全量回归显式使用本轮独立 cache_dir。没有清理或伪造缓存以掩盖变化。

本轮仍在原 `feat/vue3-dashboard` 分支。开始时已有 P0 的 12 项未提交文件状态；原内容已单独备份，P0 新增文件均保留。仅对共享 Graph / 结果记录及文档作本轮增量修改；P0 Baseline、可观测性与错误分析模块/测试/文档原字节保留。没有 Git reset、清理、提交、Push、PR 或 Merge。

## [Real API] 0

未读取生产 `.env`，未构造真实模型客户端。分析 CLI 没有 live / allow-api 入口，并阻断 socket 连接；单元测试仅使用合成 SQLite 与注入 SDK。没有调用 LunaRoute，没有新的真实请求预约。

## [Actual EX] NOT EVALUATED

Adaptive 没有产生新的真实 SQL，不能计算官方 EX。其输出 Tokens、真实总 Tokens、延迟、实际费用及泛化准确率均 UNKNOWN。历史 A/B 的得分没有复制到新模式。

## [Limitations]

阈值来自既有 11 库开发分布，可能不适用于新数据库；字节体积也不等价于语义难度。仅省去 6/33 Linking 调用，对计划总请求的影响为 9.09%，并不保证同等比例的费用或时间收益。其余 27 题仍承担 Linking 开销，单次小样本和供应商波动限制因果判断。P0 Mock 选择情景、历史用量反事实、内容字节估算与实际 Token 分开报告。

## [Next]

值得申请一轮小预算技术验证：预先冻结新样本、两种策略和模型参数，在原始 Schema-only 与 Adaptive 间逐题比较；例如各 10 题、合计至多 40 次 HTTP 尝试、零自动重试、不开 Repair，并将失败和未知结局计入预算。还需保存官方逐题评分、实际输入/输出 Tokens 与节点延迟，才可判断收益。本建议不构成真实调用授权，本轮到此停止。
