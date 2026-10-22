# 技术作品集：可审计的 Text-to-SQL Agent

岗位方向：Agent Engineer / LLM Engineer。范围：Python CLI、LangGraph 受控工作流、真实 SQLite、离线评估和有限授权的模型实验。没有前端、生产部署、分布式多 Agent 或 MCP Client 实现。

## 项目问题与结果

我希望验证 Schema Linking 和 SQL Repair 是否值得加入一个 Text-to-SQL 系统，而不仅是展示模型能生成 SQL。项目使用真实 BIRD Mini-Dev，固定 33 题覆盖 11 库，把执行成功、结果正确、模型调用成本与安全性分别记录。

实际结果有明确边界：Baseline A 官方 EX 19/33，Schema-only B 为 18/33；B 的 Generator 输入 Token 减少，但 Linking 自身开销使整批 Tokens 从 45,773 增至 84,432，平均 Agent 延迟从 8.201 增至 45.007 秒。独立 8 例结构错误诊断的真实 Repair 全部满足冻结断言，但 A/B 的自然轨迹没有结构修复触发。C/D 尚未真实运行，不能声称完整消融或 Repair 提升官方 EX。

入口材料：[技术报告](FINAL_TECHNICAL_REPORT.md)、[离线复现手册](REPRODUCIBILITY_GUIDE.md)、[C/D 决策](PHASE3C_REPAIR_DECISION.md)。原始身份：A `343c45833caa427c892f7a7887a29daf`；B `0529e0de668a4b3aa6acad2cb6bcb6a2`；诊断 `40d2955515ef45f7b5bcf573d3d43f42`。

## 设计决策与证据

### 为什么选择 LangGraph

需求是显式控制节点、State 和错误路由：筛选可开关、Repair 最多一次、共享请求预算、每题重置状态、节点输入输出可追溯。LangGraph 的条件边和 TypedDict 能直接表达这些约束，便于在同一套工具与模板上准备四组消融。

Baseline 仍是直接函数，不强行包成 Graph。正式图为 inspect → optional linking → generate → validate/execute/classify → optional repair，C/D 只完成离线机制验收。这里是一个进程内工作流，不是多个自主 Agent 的分布式协作；框架也不自动提供 SQL 安全、正确率或持久化 Exactly-Once。

### 为什么需要确定性的 SQL 安全层

LLM 输出是未受信任输入，提示词不能替代执行器权限。使用 sqlglot 的单语句 AST、Repair 词法门、SQLite URI mode=ro、query_only、authorizer、危险函数/外部库拒绝和时间/行数/字节限制。生成和修复 SQL 都重新校验，不给修复节点特殊权限。

验证包括危险 SQL、扩展、权限、超时、边界及真实库哈希。安全拒绝不会被“修复”成绕过限制的重试；成功执行但答案错误也不进入结构性 Repair。局限是缺少企业行列权限和完整 OS 沙箱，不能把本地只读测试平台说成生产安全平台。

### 为什么审计必须先于 HTTP 持久化

历史 V1 留下空 JSONL 和配置但没有 Summary，无法判断已经发出了多少请求。若在响应后才记调用数，进程可能在发送后、写结果前崩溃，空文件就不能证明零消费。

因此所有节点共享 RequestLog：ATTEMPT_PREPARED → write/flush/fsync → SDK → 追加结局。持久化失败阻止发送；失败和未知结局均占预算。SDK/app 重试 0、重定向关闭，未知请求不补发，已有输出不可覆盖/续跑。Mock 硬退出测试覆盖发送期间、响应落审计后但 JSONL 前崩溃。

这解决本地追溯与预算保守性，不承诺供应商 Exactly-Once 或计费确定性。PREPARED 不代表已发送；旧 V1 次数仍 UNKNOWN。实际费用没有账单时也保持 UNKNOWN，不能为报告完整而填零。

### Schema Linking 为什么没有提升 EX

能确认的是这组结果没有提升：B 的表 Recall 68/68、字段 Recall 163/165、字符压缩 78.04%，但官方 EX 仍比 A 少 1 题。高 Recall 只说明依赖保留程度，不能约束生成器是否增加错误业务谓词。

唯一 A 对 B 错的 case 1317，B 多了 `member.position = 'Student_Club'`。原 SQL 只读复执行为 A=7、B=0，移除额外谓词后得到 7，这是 CONFIRMED 的 SQL 层原因。问题措辞可能诱发该过滤是 LIKELY；模型为什么这样生成、Schema 缩减的因果贡献为 UNKNOWN。没有保存精确筛选列投影或反事实模型响应，不能声称根因已锁定为 Linking。

Generator 输入节省 19,656 Tokens，但 Linking 自身 61,352 Tokens，整体净增加 38,659。字符压缩率、Tokenizer 输入量、整体成本是三个不同指标。一次小样本不能证明所有 Schema Linking 都无效。

### Repair 能力与真实触发率有何区别

在 A/B 的 66 题次中，可修复语法/表列结构错误为 0；64 条已生成 SQL 能执行，另 2 条 length 不属于修复白名单。结果截断 3 条、完整执行但答案不符 24 条，也不能用 Gold 或官方 EX 在运行时触发 Repair。

独立诊断不是从 Gold SQL 制造错误：按真实 Schema 手写 8 例，结构错误各 2 个，预先冻结意图和断言，共用正式 Repair Prompt/Model/审计/执行器，真实请求 8 次，断言通过 8/8。它验证“有明确结构错误时能修复”，不验证“自然问题常出现错误”或“完整业务答案更准确”。四个不可修复负例只验证本地拒绝路由，不调用模型。

因此暂缓 C/D 是研究预算取舍；代价是不能给出完整四组结论。未来若补齐，C≤66、D≤99，须新的独立授权，不能重用已有请求许可或为了制造收益改样本。

### 如何确保消融公平

固定同一 33 题 Manifest、case_id/db_id/顺序、数据和库指纹、模型、Evidence、温度、2048 输出上限、Generator Prompt、SQL 限额与官方评分协议。A/B 独立 Run ID 和输出，结果按全部 33 题计分，包括失败和缺失；不只评成功题，不根据 A 错题修改 B 的筛选策略。

官方 EX 通过固定 commit 的原始比较函数在 Windows 独立进程执行，使用临时数据库副本与多层超时，检查原库不变；Proxy 单独报告。temperature=0 不构成跨调用确定性保证，33 题也没有统计显著性或总体代表性承诺。C/D 未运行的位置保持空白，不以 Mock 或 Replay 填充。

## 最大工程取舍

| 选择 | 获得的能力 | 保留的代价或限制 |
| --- | --- | --- |
| 保留直接 Baseline，Graph 增量接入 | 清楚比较增量模块，避免框架重写基线 | 两条执行入口需要持续回归 |
| 单次、白名单 Repair | 有限预算及清楚错误因果链 | 不处理执行成功的业务语义错误 |
| 全 Schema 回退与关系补全 | 筛选失败不会无 Schema，JOIN 依赖保守保留 | 更多 Tokens，不能保证最优压缩 |
| 请求前 fsync、独占目录 | 崩溃证据、失败计数和防重跑 | 增加 I/O；没有 Exactly-Once，未知结果不可自动恢复 |
| 官方比较逻辑不改，Windows 包装隔离 | 可核对 EX、超时与原库保护 | 依赖缓存/平台，复制和子进程开销 |
| 暂缓无明显机会的 C/D | 控制追加真实预算，不制造效果 | 四组真实消融仍未完成 |

## 面试展示顺序（无需模型 API）

1. 用 README 说明真实分数、未完成实验和项目边界。
2. 展示 schema_workflow.py 的 State、分类路由与单次 Repair 条件，对照 Baseline。
3. 展示 tools.py 的权限边界，运行 Gold 隔离和 SQL 安全 Mock 测试。
4. 打开 A/B Config、结果、请求日志和独立官方 Summary，按 case_id 对齐 case 1317。
5. 展示诊断 8/8 与自然路由 0 的差异，解释为何不能合并成绩。
6. 用 Dry Run 或现有产物离线重评结束；不为演示调用模型，也不启动 C/D。

## 可使用的作品集表述

- 基于 Python、LangGraph、SQLite 和 sqlglot 构建可切换 Schema Linking/结构错误 Repair 的 Text-to-SQL 实验系统，独立实现只读执行、Gold 输入白名单、预算审计及崩溃后禁止自动重放。
- 在固定 33 题、11 库子集完成真实 A/B：官方 EX 19/33 与 18/33；分析发现 Generator 输入缩减未抵消 Linking 的整体 Token/延迟成本，保留反例和不确定归因。
- 复用正式 Repair 链路完成 8 个预设结构错误的独立真实诊断，冻结语义断言 8/8；明确该结果不是 BIRD EX，自然轨迹暂无结构修复机会，C/D 未真实运行。

不使用“全量 BIRD 达到 100%”“显著提升准确率”“完整四组优于 Baseline”“生产级多 Agent/MCP 系统”等超出证据的表述。当前材料展示的是问题分解、保守契约、可核验结果与预算决策能力。


公开发布说明：本文件保留技术结论，但机器专用路径与私有状态文档链接已改为便携说明/公开摘要。原工作区文档备份和原始实验数据仍保留本地，不公开；当前仓库许可状态及来源见 [第三方与许可说明](THIRD_PARTY_NOTICES.md)。
