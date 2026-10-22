# Phase 3C：Repair 成果冻结与 C/D 决策审核

## [Stage] 离线审核结论

**建议采用方案 B：保留 A/B 与独立 Repair Diagnostic V1，暂缓正式 C/D。** 已有轨迹没有自然结构错误触发机会；独立诊断确认模型能处理本诊断集的预设结构错误，但两者不能合并为端到端收益证据。若交付要求是完整四组真实消融，仍须在保持冻结协议的前提下另行申请 C/D 授权；目前不能宣称四组真实消融已完成。

本轮只做指纹核查、独立只读 SQL 重放、路由分析和离线测试；真实 API 请求及新请求预约均为 0，不调用 Generator、Schema Linking 或 Repair，不计算任何新的官方 EX，不启动 C/D，不修改历史结果。

## [Evidence] 冻结产物与逐例核对

| 历史运行 | Run ID | 已有真实结果 | 已有 HTTP 尝试 |
| --- | --- | --- | ---: |
| A / Baseline | `343c45833caa427c892f7a7887a29daf` | 官方 EX 19/33（57.58%）；Tokens 45,773 | 33 |
| B / Schema-only | `0529e0de668a4b3aa6acad2cb6bcb6a2` | 官方 EX 18/33（54.55%）；Tokens 84,432 | 66 |
| 独立 Repair Diagnostic V1 | `40d2955515ef45f7b5bcf573d3d43f42` | 有效响应/安全/执行/冻结语义断言均 8/8；Tokens 7,848 | 8 |

A/B 题目顺序、case_id/db_id、33 题分母及 11 库覆盖一致；读取原始评分产物，没有重评或替换。相同官方协议 `complete-agent-results-only-failed-zero-v1`，官方 commit `abd11b6db92a1c9f809b32f7564c7c71b34d67f0`，源码 SHA256 映射一致。Manifest SHA256 仍为 `7136f72aa9e0b78ccab8b29dfb077247a082e541604b4f8841fede2a63ba6dd8`。

对照上一轮已保存的指纹重新计算，诊断核心产物全部匹配：

| 原始文件 | SHA256 |
| --- | --- |
| `outputs/repair_diagnostic_live/v1/results.jsonl` | `051a70cccb8e93b7927879ea3c5ae019248d9d82655d8d853f9ef7ecc3399f82` |
| 同目录 `summary.json` | `8467f528497162a903b403977a7e1186e057e45b59798f7ea6e9eaa81d1b469d` |
| 同目录 `requests.attempts.jsonl` | `2837930bca8e015cfbd91d3b1c871973fbb7b3e05a81d014f042ed8799f25987` |
| 同目录 `requests.config.json` | `33a8d2bf0bf0f887cfbbe1c463dd8c67b27395ff583bf453d9f039c43027ee4e` |
| `outputs/repair_diagnostic_live_checks/v1/final_acceptance.json` | `91cd2d136a1f495d3da123993933eefde055dad942c3c54c09ac671ff660fdfa` |

8 个 case_id、唯一 attempt_id、审计顺序 1–8、HTTP 200/SUCCEEDED、每例 Tokens 与 JSONL 对应，Summary 的 8/8 与逐例记录一致。没有未完成或不确定尝试。

| 诊断 ID | 审计序号 | 原始修复 SQL 的本轮只读复验值 | 原始语义断言 / 本轮复验 |
| --- | ---: | ---: | --- |
| RD01 | 1 | 17686 | 通过 / 通过 |
| RD02 | 2 | 56822 | 通过 / 通过 |
| RD03 | 3 | 91966 | 通过 / 通过 |
| RD04 | 4 | 32461 | 通过 / 通过 |
| RD05 | 5 | 4500 | 通过 / 通过 |
| RD06 | 6 | 954 | 通过 / 通过 |
| RD07 | 7 | 28 | 通过 / 通过 |
| RD08 | 8 | 343 | 通过 / 通过 |

原始 JSONL 保存修复 SQL、执行/完整性/断言状态及结果行数，**没有保存历史执行结果的具体行值**。本轮在哈希一致的原库上重执行原始修复 SQL，另存 `offline_reexecution_rows`，与冻结参考、已保存的 `semantic_oracle_results.json` 及预设 AST 断言全部一致。这个复验支持结果一致性，不冒充找回历史未保存的原始响应或结果行；没有改写原始 JSONL。逐例 SQL、attempt_id 与复验结果详见本轮 `analysis.json`。

诊断 8 例是人工预设错误、真实模型修复；RN01–RN04 是离线拒绝控制。它们不是 A/B 自然发生的错误，不能加入自然触发频率的分母或 C/D 的得分。

## [Replay] 自然错误与反事实路由

独立程序 `outputs/phase3c_repair_decision_v1/analyze.py` 不装配 Agent/Graph、不构造 Model 或 Repair Prompt，不读取 BIRD 题目 JSON/Gold SQL。它先验证冻结配置、原始审计和身份，使用正式 `validate_candidate()`、`execute_select()` 只读重放 A/B 原始模型 SQL。只有真实异常才进入冻结 `classify_error()`，可修复白名单保持 syntax/missing_table/missing_column/ambiguous_column。分类器 SHA256：`685d867a22e1b6659955b05e16debc7f26f4458edb45882a2a33fbaba540e700`。

64 条已生成 SQL 重放均成功，完整/截断状态与历史完全一致，因此自然 SQL 没有异常需要传入分类器；不把成功执行或模型 length 人为包装成 SQL 异常。诊断正例初始错误的独立检查则走原分类器，确认 4 类各 2 个；不将这些人工控制计入下表。

| 历史轨迹类别 | A（33 条） | B（33 条） | 合计 |
| --- | ---: | ---: | ---: |
| 可修复语法错误 | 0 | 0 | 0 |
| 缺表 | 0 | 0 | 0 |
| 缺列 | 0 | 0 | 0 |
| 歧义列 | 0 | 0 | 0 |
| 危险 SQL / 其他实际 SQL 异常 | 0 | 0 | 0 |
| 模型 length 截断，未形成可执行预测 | 2 | 0 | 2 |
| 执行成功但结果截断 | 1 | 2 | 3 |
| 完整执行但已有官方 EX=0 | 11 | 13 | 24 |
| 完整执行且已有官方 EX=1 | 19 | 18 | 37 |
| 历史轨迹启用 Repair 开关后的可触发数 | **0** | **0** | **0** |

length：A 的 case 94、198；结果截断：A 的 340，B 的 28、340。两类均不属于结构错误白名单。24 条完整执行但与标准结果不匹配的记录，不能使用官方 EX、Gold 或业务正确性作为运行时 Repair 信号。

对已有轨迹观察，自然 Repair 机会为 **0/66 条记录**，或 **0/64 条已生成 SQL**；2 条 length 没有 SQL 执行机会。这里是描述性样本统计，不是未来触发概率估计：A/B 使用同一 33 题，彼此相关，清单也不是总体的独立随机样本。

程序先仅根据生成/执行信息决定路由，随后才在独立报告侧附加历史官方判定。历史错误字段分析与本轮真实 SQLite 异常路径一致：Repair 反事实调用数 0，实际模型调用数和预约数也为 0。

**此 Replay 只固定已有 SQL、模拟路由，不重新生成 C/D，不产生 C/D 输出或新的官方 EX。** 不能把沿用 A/B 已知 SQL 的路由等同独立 C/D，也不能给 C/D 填入 19/33 或 18/33。没有对原始 SQL 添加错误、改写谓词或针对结果调整筛选/修复策略。

## [Cost] C/D 的预算与不确定性

| 未来正式组 | 完整批次的基础请求 | 额外 Repair 上限 | 总 HTTP 尝试上限 |
| --- | ---: | ---: | ---: |
| C / Repair-only | 33 次 Generation | 33 | **66** |
| D / Full Graph | 33 次 Linking + 33 次 Generation | 33 | **99** |
| 合计 | **99** | **66** | **165** |

上限包含失败和不确定尝试，系统错误可提前终止，不能把它当作必须消耗的目标。即使将来没有触发任何 Repair，独立完成 C/D 仍需基础生成/筛选请求；本轮路由 0 不意味着未来正式实验成本为 0。

未来 C/D 的真实 Token 和延迟 UNKNOWN；当前费用、未来实际费用也均 UNKNOWN，缺少可靠单价和供应商账单。不能按 A/B 或诊断的 Token 简单放大为实测费用；尤其未来 Repair 次数、Prompt 长度及输出本身未知。本轮不查询模型或计费接口。

独立生成可能改变 SQL：即使同模型、temperature=0，未有供应商确定性保证，也没有按历史 SQL 回放生成端。新运行可能出现或消失结构性错误。C/D 若观测到与 A/B 不同得分，也应检查逐题初始/修复轨迹，不能把所有变化都归因于 Repair。

任何未来 C/D 都需新授权、新 Run ID、独立输出目录、SDK/应用重试 0、串行和原持久化审计；A/B 和诊断的历史授权不能复用。本轮不输出已执行的 C/D 命令，也不启动实验。

## [Tests] 596 passed / 8 skipped 与 604 passed

此前离线准备启用 `BIRD_RUN_INTEGRATION=1`，实测 **604 passed、0 skipped**，证据为 `outputs/repair_diagnostic_preflight/final_acceptance.json`。上一轮真实诊断结束后的完整回归显式设为 `BIRD_RUN_INTEGRATION=0`，得到 **596 passed、8 skipped**；随后仅单独启用真实 SQLite 诊断集成，**1 passed**。不是删除 8 项测试，也不是有 8 项失败。

本轮重新收集得到 **8/604 tests collected（596 deselected）**，8 项均有 `@pytest.mark.bird_integration`，函数开头均以 `BIRD_RUN_INTEGRATION != '1'` 主动跳过，仍存在于当前源文件：

| 测试文件 / 函数 | 可选覆盖 |
| --- | --- |
| test_real_bird_data.py / test_real_bird_dataset | 500 题、11 库、兼容性/只读及 Gold 输入隔离 |
| 同文件 / test_real_baseline_preflight_without_model | 真实 10 题零模型预检 |
| test_real_schema_linking.py / test_real_dev33_mock_schema_graph | 真实库 + Mock 筛选及独立 Recall 评估 |
| 同文件 / test_real_manifests_reproduce_without_overwrite | 固定 Manifest 重建一致性及禁止覆盖 |
| test_real_sql_repair.py / test_real_dev33_four_modes_mock_repair | 真实库 + Mock 结构错误注入/修复 |
| test_real_ablation.py / test_real_ablation_four_groups_33 | 真实库 + 四组 Mock 预算/协议对账 |
| test_real_official_ex.py / test_real_bird_official_ex_controls | 真实 33 题官方评测器控制实验 |
| test_real_repair_diagnostic.py / test_real_hand_authored_repair_diagnostic | 真实库 + 8 例 Mock Repair 及负例 |

诊断轮关闭全量 opt-in 是有意避免重跑官方 EX 控制和其它真实库 Graph 集成，诊断对应的一项另行启用。**这一轮回归执行范围确实比 604 全开小，不能声称当轮 604 项均执行通过**。但收集总数仍为 604、8 个测试的显式跳过条件存在，没有删除测试/放宽断言；未发现非预期覆盖下降。未生成 coverage 工具的行/分支覆盖率，不能把测试数量当成该百分比。

本轮在相同隔离条件下重新运行完整离线套件，使用 `-rs` 保存每项跳过原因；不为补数量而运行新的官方控制评分。只读分析本身另行核验 8 个真实修复 SQL 和 64 个真实 A/B SQL；这不是替代所有可选集成测试。

本轮实际命令均使用项目 `.venv/Scripts/python.exe`，Python 3.13.5：

| 命令（省略相同解释器前缀） | 本轮结果 | 退出码 |
| --- | --- | ---: |
| `outputs/phase3c_repair_decision_v1/analyze.py` | 291 项指纹核查；8 例诊断断言复验；64 条 A/B SQL 只读重放与历史执行状态一致；路由 0 | 0 |
| `-m pytest --collect-only -q -m bird_integration` | 8/604 tests collected，596 deselected；8 个集成测试仍存在 | 0 |
| `-m pytest -q -rs --tb=short`，`BIRD_RUN_INTEGRATION=0` | **596 passed、8 skipped、0 failed，100.89 秒**；8 项原因全部来自 opt-in 开关 | 0 |
| `-m compileall bird_starter run_eval.py` | 通过 | 0 |
| `-m pip check` | No broken requirements found. | 0 |

本轮没有删除、改写或新加测试，也未修改任何生产源码、冻结计划、Prompt 或 Manifest。跳过提示里的“下载数据/UNVERIFIED”是既有通用文案，执行条件只检查开关；不能据此推断本轮缺库。11 个原库及对应 Schema 已在只读分析中实际验证。上一次单独诊断集成 1 passed 是历史证据，本轮没有将它计作重新执行。

## [Recommendation] 两个方案的贡献与选择

**方案 A：继续独立真实 C/D。** 价值是补齐四组端到端真实协议，观察新生成轨迹中自然错误发生、实际 Repair 触发、修复恢复及其 Token/延迟成本；即使没有收益也可以形成完整的四组结论。代价是新增最多 165 次 HTTP 尝试，可能没有 Repair 机会，且单次小样本及独立生成差异限制因果归因。只有明确需要完整四组交付、接受零触发/无提分结果，并另行授权预算时，才建议继续。

**方案 B：保留阶段性成果、暂缓 C/D（本轮推荐）。** 当前可以据实陈述：平台工程链路和安全审计已验证；A/B 在同一 33 题真实对照中分别为 19/33、18/33；真实模型在 8 个冻结人工结构错误诊断中完成修复与断言；已有 66 条自然轨迹没有结构修复触发机会。暂停能避免为当前未观察到的机会额外付费，但必须明确缺少 C/D 自然生成、自然触发及端到端收益证据，不能宣称 Repair 提高 BIRD 官方 EX。

8/8 诊断支持“面对明确预设结构错误能修复”，0/66 轨迹支持“当前自然轨迹无触发”，这两条证据回答不同问题。诊断不测复杂业务语义，也不能证明 C/D 将来没有错误或没有价值。建议把阶段性研究结论限制在已观察证据，正式四组结果留空；不改变 33 题 Manifest、2048 Token 上限、Prompt、Evidence、错误白名单或评分分母来制造触发机会。

## [Proof] 安全与证据位置

- 本轮冻结核查 291 项既有文件，包含 A_v1 全部 50 项、B_v1 全部 51 项、11 个原始 SQLite、诊断 7 项原始产物、最终验收证据及测试源文件。分析前后和完整回归结束后的字节及历史目录文件清单均一致。
- 只读重放复用原 mode=ro、query_only=ON、AST/词法检查、authorizer、8 秒及 1000 行/原字节上限；不修改、移动或复制原库。
- 不读取生产 `.env`，不保留认证变量；独立分析入口禁用 dotenv/tracing 并阻止 socket 连接，网络尝试 0；pytest 收集前清除凭据，临时 dotenv fixture 隔离且网络被禁止。
- 没有构造 Model、Graph、Repair Prompt 或发送 Gold/参考答案到 Agent；诊断参考及已有官方判定仅在独立评估分析侧使用，不反馈路由。
- 既有请求审计 SHA256 不变，无新增真实请求或预约；V1 历史请求数仍为 UNKNOWN，没有使用新证据倒推历史数量。C_v1/D_v1 不存在。
- 分析、逐例路由与哈希证据均新增在 `outputs/phase3c_repair_decision_v1/`，不替代旧产物。核心文件：`analysis.json`、`oracle_crosscheck.json`、`before.json`、`integrity.json`、`integration_collection.txt`、`pytest.stdout.txt`；最终对账见 `final_acceptance.json`。

完成本轮审核后停止；本报告是决策建议，不构成任何新模型调用授权。
