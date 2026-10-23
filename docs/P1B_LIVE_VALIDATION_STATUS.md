# P1B 真实模型验证验收记录

记录日期：2026-10-23。[Stage] P1B 真实模型验证，已完成并停止。

本批 10 题 B/E 配对验证中，两组官方 BIRD EX 均为 **6/10**；E 总 Tokens 从 22,756 降至 20,688（减少 9.09%），模型 HTTP 请求从 20 降至 15，平均 Agent 延迟从 32.588 秒降至 16.879 秒。Calculated Cost 和 Billed Cost 均为 **UNKNOWN**。这些是本批实测结果，不代表统计显著性、普遍准确率保证或费用节省证明。

## 授权、代码与变更范围

[Authorization] 用户明确授权一次真实评测，费用不设上限；不设置 USD 预算、报价前置条件或费用停止条件。请求预约上限仍为 40。用户另行确认保留此前 15 项未提交的 P1B 离线准备文件，以当前工作区继续。此前文件已逐字节备份，未清理或覆盖历史输出。

[Git] 本地分支 `experiment/p1b-real-model-validation`；基线 HEAD 为 `92ac44a9001191aedc5494b36ea825ac9a6c4400`，包含 P0/P1A。P1B 实现尚未提交，不能声称基线 Commit 已包含本次代码；真实运行使用的源码逐文件 SHA256 已在冻结产物及 `final_report.json` 中保存，运行后核验一致。

主要变更为专用评测入口、冻结配置/样本、预算和持久化审计、一次性连接检查、独立评分导出及对比报告；新增显式 `UNLIMITED_RECORD_ONLY` 授权模式。默认仍离线，原有限额模式仍保留计费保护。未改动原有模型适配器、SQL 安全工具、请求审计核心、Gate 阈值或 Prompt；原 A/B/C/D CLI 和历史配置保持原样。

本批使用新增 `configs/p1b/real_model_validation_v1.json`；原 `configs/p1b/adaptive_live_v1.json` 未改写。前端未修改，未重跑浏览器验收。

## 样本与预冻结协议

[Sample] 从真实 BIRD Mini-Dev 500 题排除历史 33 题，固定种子 `20261023`，在读取 Gold 评分之前冻结 10 道新题及相邻配对交错顺序；5 对 B 先运行，5 对 E 先运行。两组题目、顺序、Evidence、模型和安全参数一致，不按中途结果换题。题目 ID、数据库、难度、数据库哈希及完整执行顺序仅保存在本地明细。

| 冻结项 | 值 |
| --- | --- |
| 数据集 SHA256 | `88ceb0710163cae46a256ecea8f0a8c98286599530b60587fda5c3cfe57d45d2` |
| 排除的开发集 Manifest SHA256 | `7136f72aa9e0b78ccab8b29dfb077247a082e541604b4f8841fede2a63ba6dd8` |
| 本批 Manifest SHA256 | `186d928bbd0ab70d3353c05debe085609909e65e96fe251cbe3fafb0f6a46487` |
| 本批源配置 SHA256 | `4d333f3a36036730b00a85a9cb550b654abbb40d1fb073b9dcedff0bdf1ce98a` |
| 路由分层 | 5 题 SKIP_LINKING、5 题 USE_LINKING |
| E 路由阈值 | 完整 Schema ≤1,014 UTF-8 字节时跳过 Linking，边界包含等号 |
| B / E | B Gate 关闭；E Gate 开启；两组均关闭 Repair |

剩余 467 道可用题中，SKIP 为 64、USE 为 403。本批特意采用 50% SKIP 的分层样本，高于剩余题池约 13.70% 的比例；不能直接把本批整体请求或 Token 降幅外推到全题池。这是在既有数据库上的新题验证，不是跨数据库泛化证明。

[Model] LunaRoute，Base URL `https://gw.lunaroute.com/v1`，请求模型与全部 35 个响应中的模型均为 `deepseek-4.1-flash`。实验前一次独立、已持久化审计的模型目录 GET 返回 HTTP 200，确认模型存在；没有替换服务商或模型。

两组共同参数为 temperature=0、n=1、`max_completion_tokens=2048`、模型请求超时 300 秒、SDK/应用重试 0、并发 1、HTTP 重定向关闭、Evidence 开启。Prompt 版本为 `baseline-v1` / `schema-link-v1`。Agent SQL 执行超时 8 秒、最大 1,000 行。每题至多 2 次模型请求，每组至多 20 次，Repair 请求为 0。

## 核心实测结果

[Groups] B 原始 Schema-only / E Adaptive Schema-only，固定分母各 10 题。

| 指标 | B 原始 Schema-only | E Adaptive | E 相对 B |
| --- | ---: | ---: | ---: |
| 官方 BIRD EX | 6/10（60%） | 6/10（60%） | 0 个百分点 |
| Prompt Tokens | 16,130 | 13,881 | -2,249（-13.94%） |
| Completion Tokens | 6,626 | 6,807 | +181（+2.73%） |
| Total Tokens | 22,756 | 20,688 | -2,068（-9.09%） |
| Agent 平均延迟（秒） | 32.588 | 16.879 | -15.709（-48.21%） |
| Agent P50（秒） | 26.651 | 14.723 | -11.928（-44.76%） |
| Agent P95（秒） | 69.419 | 30.246 | -39.173（-56.43%） |
| Linking 请求 | 10 | 5 | -5（-50%） |
| Generation 请求 | 10 | 10 | 0 |
| 模型 HTTP 尝试 | 20 | 15 | -5（-25%） |
| SQL 执行成功 | 10/10 | 10/10 | 0 |
| 结果行数截断 / Generation 截断 | 0 / 0 | 0 / 0 | 0 |
| Usage 缺失调用 | 0 | 0 | 0 |
| HTTP 错误 / 未知结局 | 0 / 0 | 0 / 0 | 0 |
| 模型内容失败 | 0 | 1（Linking 截断） | +1 |
| Schema fallback | 0 | 1 | +1 |
| Calculated Cost | UNKNOWN | UNKNOWN | UNKNOWN |
| Billed Cost | UNKNOWN | UNKNOWN | UNKNOWN |

SQL 执行成功不等于语义正确。两组各有 4 题官方结果不匹配，目前仅标记 `semantic_mismatch_unattributed`，未将其归因为漏召回或其他未经证实的原因。

[Tokens] 所有 35 次模型响应均包含有效 Provider Usage，包括 E 的截断 Linking 请求。Total 为供应商返回的 Prompt 与 Completion 之和；未另存供应商独立 `total_tokens` 字段，也没有用字符数或离线估算填补实测字段。

| 节点 | B Prompt | B Completion | B Total | E Prompt | E Completion | E Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Linking | 13,306 | 3,534 | 16,840 | 10,003 | 3,196 | 13,199 |
| Generation | 2,824 | 3,092 | 5,916 | 3,878 | 3,611 | 7,489 |

Linking 减少 3,641 Tokens，Generation 增加 1,573 Tokens，抵消部分收益。不能只报告省掉的 Linking Tokens 而忽略 Generator 变化。

缓存输入 Tokens 为 B=6,528、E=2,944；Reasoning Tokens 为 B=5,212、E=5,816，各类别缺失数均为 0。缓存是 Prompt 的子集，Reasoning 是 Completion 的子集，均不重复相加；对应账户计费单价尚不可核算。

[Latency] 模型节点计时取适配器调用，包含持久化审计；Agent 计时包含输入准备及 Graph 执行，截止 Graph 返回，不包括之后的结果文件写入和独立官方评分。下表 Linking 只统计实际调用，E 的 5 个跳过节点不当作 0 秒模型调用混入。

| 节点/范围 | B n | B 平均 / P50 / P95（秒） | E n | E 平均 / P50 / P95（秒） |
| --- | ---: | --- | ---: | --- |
| Linking 模型调用 | 10 | 10.389 / 9.462 / 19.465 | 5 | 9.279 / 5.146 / 19.703 |
| Generation 模型调用 | 10 | 21.478 / 15.973 / 57.903 | 10 | 11.521 / 8.138 / 27.932 |
| Agent 端到端 | 10 | 32.588 / 26.651 / 69.419 | 10 | 16.879 / 14.723 / 30.246 |

分位数采用线性插值，包含全部已处理题及实际失败调用，不按正确性过滤。未采集的独立 Graph 节点墙钟统计在明细中保留 null/n=0，不补造。上述实测延迟还受到供应商调度、缓存、输出长度等因素影响，不能仅归因于 Gate。

## 请求审计与回退

[Max HTTP Reservations] 40：一次性授权将最多 39 次模型请求与 1 次模型目录请求分配在该总上限内；每组 20 次、每题 2 次的模型上限继续生效。

[Actual HTTP Attempts] **36 = 35 次模型 HTTP + 1 次模型目录 HTTP**。只调用一次 Live 批次；没有第二批、失败重跑、断点自动恢复、应用/SDK 自动重试或重定向。剩余配额未使用。

模型审计共 35 个 Prepared 预约及 35 个 HTTP Completed 响应，全部 HTTP 200；34 次内容成功，1 次已完成响应的内容失败。Failed 内容、HTTP 错误和 Indeterminate 分开记录，未知结局为 0。请求前预算检查、预约持久化及 flush/fsync 均早于传输；跨 B/E 共享预算，审计写入失败会阻止请求。

[Gate] B 全部 10 题 USE；E 为 5 SKIP、5 USE，省去 5 次 Linking。E 有 1 次已收到 HTTP 200 的 Linking 响应因输出达到 2,048 Tokens 被判定 `model_output_truncated`，按冻结的既有规则回退到完整 Schema，再执行本题原定的 Generation。未重新调用 Linking，也没有把内容失败伪装成网络未知结局或重试许可。

全部请求 ID、预约/完成时间、组别、case_id、节点、HTTP/内容状态、Provider Usage、真实 Schema 投影及回退原因只写入 ignored 本地产物，不自动向前端或 GitHub 公开。20 条实际 Schema 观察均可由保存的元数据及真实选择结果重建。

## 独立官方 EX

[Official EX] B=6/10，E=6/10。模型生成与保存结束后才启动独立 evaluator 导出 Gold 和评分；未使用本地 Proxy，也未借用历史 A/B 分数。

固定官方源码 Commit：`abd11b6db92a1c9f809b32f7564c7c71b34d67f0`。

- `evaluation_ex.py` SHA256：`da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89`。
- `evaluation_utils.py` SHA256：`f6943d249caac5aeaef9bce21d43dbf29dcef85a0c965a76df032a9542f308bf`。

沿用 Windows spawn、私有数据库副本和只读执行；评分 SQL 超时 30 秒、worker 超时 40 秒、外层超时 600 秒。两组评分进程均正常退出，10 个固定槽位均有结果，worker 已回收，无外层硬超时，评分逻辑未修改，副本和原数据库哈希未变。失败、缺失、超时、生成截断和结果行数截断依预冻结协议计零；本批无这类最终失败槽位。Linking 截断后成功回退的完整 Agent 结果正常进入评分。

| 配对结果 | 题数 |
| --- | ---: |
| B 正确 / E 正确 | 6 |
| B 正确 / E 错误 | 0 |
| B 错误 / E 正确 | 0 |
| 两组均错误 | 4 |

逐题明细保存在本地 `paired_results.json`，不含 Gold SQL。本样本不能证明统计显著性或准确率非劣效。

## 费用

[Cost] Calculated Cost=UNKNOWN；Billed Cost=UNKNOWN；两组费用差异=UNKNOWN。没有可信的本账户输入、输出、缓存及额外项目单价，也没有本批供应商账单。不得把这些 UNKNOWN 写成 0，不得用其他供应商报价或离线 Token 估算冒充实际费用。

2026-10-23 核查的供应商公开材料：[Usage and Billing](https://docs.lunaroute.com/concepts/usage-and-billing/) 与 [Models](https://docs.lunaroute.com/models/)。公开材料不足以确定本账户此次调用的有效单价和账单；模型目录检查只证明可用性，不能替代账单。

`UNLIMITED_RECORD_ONLY` 的私有 cost journal 中零报价/已知费用小计只是停用金额保护时的内部占位，不是供应商价格、Calculated Cost 或 Billed Cost。请求量和 Tokens 的下降不能直接解释为已证实的货币节省。报价未知没有阻止已授权的真实调用、EX 或延迟评测。

## 安全、完整性与测试

[Security] Gold Isolation=PASS；SQL Read Only=PASS；请求预算和持久化审计=PASS。样本选择和 Schema 路由不使用 Gold；Gold 只由生成结束后的 evaluator 访问。生成输入允许名单、Gold sentinel 离线测试、运行源码指纹及独立评分进程共同提供证据。保留 AST 安全校验、SQLite `mode=ro`、`query_only=ON`、执行超时和行数限制。

密钥仅通过既有安全环境加载机制进入进程；没有打印或保存 API Key、Authorization Header 或生产 `.env` 内容。没有公开原始模型响应、问题、SQL 或完整 Prompt。

[Protected Files] 对运行前后 **17,172 个受保护文件**逐项 SHA256 核验，全部一致；其中 **11 个原始 BIRD SQLite** 全部一致。历史 A/B、Repair、P0/P1A、归档与前一轮 P1B 离线产物未覆盖，受保护历史目录文件清单没有意外新增，原 Gold 数据未改写。新 evaluator 的 Gold 导出和 SQLite 副本仅存在本批独占 ignored 目录内。

[Tests] 以下为本轮实际运行结果，非引用历史的 685/8 或 761/8 验收数：

| 检查 | 本轮结果 |
| --- | --- |
| 完整 Python 离线 pytest | **779 passed、8 skipped、0 failed**，154.15 秒 |
| P1B 相关定向测试 | **114 passed、0 skipped、0 failed**，15.32 秒；与完整套件重叠，不额外累加 |
| compileall：bird_starter、run_eval.py、scripts | 退出码 0 |
| pip check | 退出码 0，无损坏依赖 |
| 新样本 Dry Run | B/E 各 10 条，真实请求 0、预约 0 |
| 新样本 Mock 集成 | B/E 各 10 条，35 次假 SDK 预约，真实请求 0 |
| 冻结指纹、Gold、安全、阈值、预算、审计及零重试预检 | PASS |
| 实际模型请求审计核对 | PASS，35/35 有 Usage，未知结局 0 |
| 独立官方评分 | B/E 均退出码 0，各 10 条有效评分记录 |

8 个 skipped 是未显式启用 `BIRD_RUN_INTEGRATION=1` 的既有真实数据集成测试，覆盖四组 Mock 消融、数据、Baseline、官方评测控制、Repair 诊断、Schema Linking、Manifest 重建及 SQL Repair。默认套件保留 opt-in 规则；这些 skipped 不计为 passed，也不代表本地缺少数据。本批专用真实数据 Mock/Dry Run 和之后获授权的 Live/独立评分已分别实际执行。未以默认离线测试触发付费调用。

## 本地产物与结论

公开报告仅包含聚合结果与指纹。私有证据根目录为 `outputs/p1b_live_validation_b71f3cdb40904c27b047c36be6b9b8d9/`，由 `.gitignore` 排除：

| 文件或目录 | 内容 |
| --- | --- |
| `final_report.json` / `final_report.md` | 完整汇总、源码哈希、结果及限制 |
| `frozen/` | 配置、Manifest、源码与数据指纹、固定顺序 |
| `batch/` / `connection/` | 唯一真实批次与一次模型目录检查的持久化审计 |
| `request_reconciliation.json` / `provider_usage.json` | 每次真实请求的预约与完成对账、实测用量 |
| `node_provider_usage.json` / `latency_statistics.json` / `usage_categories.json` | 节点用量、延迟及缓存/Reasoning 分类 |
| `official_inputs_B/` / `official_inputs_E/` | 仅供 evaluator 的导出输入，含私有 Gold |
| `official_B/` / `official_E/` / `paired_results.json` | 独立官方评分及逐题配对结果 |
| `comparison/` / `pricing_evidence.json` | 真实 B/E 比较和费用 UNKNOWN 的依据 |
| `protected_before.json` / `protected_after.json` / `security_verification.json` | 保护范围、哈希核验与安全结论 |
| `pytest_full.txt` / `pytest_targeted.txt` / `offline_preflight.json` | 本轮离线测试与预检证据 |
| `authorized_workspace_before.json` / `workspace_before/` | 用户确认保留的此前未提交工作备份 |

[Real API] 35 次真实模型请求，另有 1 次模型目录 GET，共 36 次 HTTP。真实实验批次=1，重试=0，Repair=0。

[Push] NOT EXECUTED；[PR] NOT CREATED；[Merge] NOT EXECUTED；未自动 Commit 或重写历史。全部 P1B 源码、配置、测试和公开报告保留在当前未提交工作区。

[Recommendation] 本批在逐题 EX 一致时观察到更少请求和 Tokens、更低延迟，值得考虑另行授权、预先登记且限制请求量的更大新题验证。现有 10 题、SKIP 过采样、同库样本、一次 Linking 截断和供应商延迟波动限制了结论；费用收益仍未知。保持默认 Gate 关闭，不依据本批自动改变阈值、增加 Repair、扩大数据集或启动第二批。本轮在完成报告后停止。
