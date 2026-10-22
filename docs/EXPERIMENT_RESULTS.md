# 实验结果与证据索引（公开版）

本页只公开经过核验的聚合结果与内容哈希，不分发原始请求、响应、题目、Gold SQL、数据库或私有调试日志。历史文件仍保留在原工作区；只有公开源码的使用者不能假定拥有这些证据，也不能用 Mock 替代真实实验。

## A/B：固定 33 题、11 库

| 指标 | A Baseline | B Schema-only |
| --- | --- | --- |
| Run ID | `343c45833caa427c892f7a7887a29daf` | `0529e0de668a4b3aa6acad2cb6bcb6a2` |
| HTTP 尝试 | 33 | 66 |
| 官方 EX | **19/33（57.58%）** | **18/33（54.55%）** |
| SQL 生成及执行成功 | 31/33 | 33/33 |
| Local Proxy 匹配 / 固定分母 | 19/33 | 18/33 |
| Prompt Tokens | 30,485 | 60,465 |
| Completion Tokens | 15,288 | 23,967 |
| 总 Tokens | **45,773** | **84,432** |
| Agent 平均 / P50 / P95 秒 | 8.201 / 5.806 / 18.875 | 45.007 / 39.663 / 87.310 |

共同配置：LunaRoute，deepseek-4.1-flash，temperature=0、Evidence=true、max_completion_tokens=2048、请求超时 300 秒、SDK/app 重试 0、并发 1、重定向关闭。配置为 configs/phase3c/ablation_v1.json；模型名称与公开 Base URL 不属于凭据。

B 表 Recall 68/68，字段 Recall 163/165，选择合法 32/33，case 195 按 invalid_contract 回退完整 Schema。字符压缩率 78.04%；B Generator 输入少 19,656 Tokens（64.48%），但 Linking 自身 61,352 Tokens，整体净增 38,659（84.46%）。实际账单、单价和费用 UNKNOWN。

固定分母协议：失败、缺失、超时和不完整 Agent 结果计 0，不放宽本地执行限额后救回题目。A 的 94/198 为 length；结果截断为 A 的 340、B 的 28/340。18 题双方正确、14 题双方错误、仅 1317 从对变错；无显著性结论。

1317 的 SQL 层原因 CONFIRMED：B 添加了额外 position 谓词，已有 SQL 的只读复验从 A 的 7 变成 B 的 0，仅移除该条件得到 7。问题措辞诱发映射为 LIKELY；Schema 缩减或供应商波动各自贡献 UNKNOWN。该题 Recall 完整，不能仅凭 A 对 B 错锁定 Linking 为根因。历史原始 Linking JSON、精确列投影和结果行没有落盘，无法恢复。

## Phase 2 的独立历史烟测

V3 Run ID `eaf5d4877d3c44ba95b4a3846318b72f`：10/10 生成和执行，Local Proxy 8/10。它不是 A/B 的 33 题正式组，也没有当作官方 EX。

## 独立 Repair Diagnostic V1

Run ID `40d2955515ef45f7b5bcf573d3d43f42`，8 次真实 HTTP 200，8/8 安全、执行和冻结语义断言通过；7,386 Prompt + 462 Completion = 7,848 Tokens。模型延迟平均/P50/P95 为 27.280/29.449/42.962 秒。4 个负例只离线拒绝，模型请求 0。

RD01–RD08 是基于真实 Schema 手写的计数结构错误，各类两例，不从 Gold 构造。8/8 不是 BIRD 官方 EX，也不代表自然业务语义修复率。历史结果行未保存；只读复验另行验证，没有冒充找回历史原始行。

## 自然机会与未完成实验

A/B 共 66 题次：可修复结构错误 0、length 2、结果截断 3、完整执行但官方不匹配 24；64 条已生成 SQL 只读复验成功。历史反事实路由 Repair=0，不产生 C/D 分数。C/D **尚未真实运行**，无 500 题完整成绩。暂缓决策和限制见 [PHASE3C_REPAIR_DECISION.md](PHASE3C_REPAIR_DECISION.md)。

configs/release/evidence_v1.json 是公开的 Run ID/聚合指标/哈希清单，不含私有日志内容。持有原工作区的审阅者可核对原文件；缺少私有证据时应报告“不可在当前副本验证”，不得伪造结果。部署/复现方法见 [REPRODUCIBILITY_GUIDE.md](REPRODUCIBILITY_GUIDE.md)。
