# P1B Adaptive Live Validation：离线准备与授权闸门

## Stage / Authorization

本阶段实现独立 B/E 验证入口，当前交付范围仅为离线准备、Mock、Dry Run 和费用预检。**没有执行 Live，没有新的官方 EX、真实 Tokens、模型延迟或账单。** 不将历史 A/B 或 P1A 估算作为本阶段的模型测量。

用户在本轮补充选择沿用 LunaRoute、`deepseek-4.1-flash`、`https://gw.lunaroute.com/v1`，并明确费用不作为限制。后者已作为“可显式不设金额上限”的授权策略支持，仍不等于允许现在执行 Live，也不取消跨组 40 次请求上限。当前没有创建可用于真实调用的一次性授权文件。

Git 基线为 `92ac44a9001191aedc5494b36ea825ac9a6c4400`，已核对 P0/P1A 为其祖先。工作区开始时干净；在目标分支不存在的条件下，从已 Fetch 的 `origin/main` 创建 `experiment/p1a-live-validation`。本阶段不 Commit、Push、PR、Merge 或改写历史。

## Design / Files

- `adaptive_validation_config.py`：只保留生成白名单字段，校验源 JSON、开发集、全部数据库指纹，确定性分层抽样并冻结 Manifest、源码、参数及 Git HEAD。
- `adaptive_validation.py`：专用 B/E Graph 入口，串行交错执行，默认 Dry Run；Mock 注入 SDK，不接触真实模型。原 Model、Graph、SQL 安全、请求审计实现默认行为保持不变。
- `adaptive_validation_budget.py`：复用同一个 `RequestLog`，在所有组/节点之间共享上限，另限每组 20、每题 2 次；每次请求前费用承诺日志与原 PREPARED 均 flush/fsync。异常、超时、未知结局、计费缺失阻止后续请求，未知承诺不退款。
- `adaptive_validation_reporting.py`：逐节点 Usage、覆盖率、已知小计、完整总量、延迟样本数与费用口径；不能把缺失项计为零。
- `adaptive_validation_evaluation.py`：独立 Evaluator 专用适配器，生成入口不导入它；检查结果、审计和文件哈希后才读取 Gold。既有官方输入导出 CLI 增加 P1B 分发和 E 组选择，旧配置仍用原路径；官方评分逻辑没有修改。
- `scripts/run_adaptive_live_validation.py` / `scripts/compare_adaptive_validation.py`：离线准备/执行与独立官方评分结果配对；`configs/p1b/adaptive_live_v1.json` 为独立候选配置。原 offline-only 分析工具没有增加 Live 开关。

## Sample / Frozen Protocol

使用原已校验经典 Mini-Dev 500 题。排除历史 33 题，按完整 DDL UTF-8 字节数路由，SKIP/USE 各取 5 题。采样算法 `sorted-integer-id-random-sample-skip5-use5-shuffle-v1`：整数 case ID 升序排序两个候选池，使用同一个 `random.Random(20261023)` 依次抽样 SKIP 5、USE 5，再打乱所选顺序。候选不足直接拒绝，不按效果替换题目。采样只使用身份和元数据，不读取或利用 Gold/评分。

两组按同一 10 题顺序记录；实际执行为逐题相邻配对，奇偶位置交替 B/E、E/B，各五对。Graph 只接收 `AgentInput` 的问题、Evidence、数据库路径和模式；另一组的 SQL/结果不进入输入。

| 固定项 | B 原始 Schema-only | E Adaptive |
| --- | --- | --- |
| Linking / Repair | 开启 / 关闭 | 开启 / 关闭 |
| Gate | 关闭 | 完整 Schema ≤1,014 UTF-8 字节时跳过 |
| 样本 / 评分分母 | 相同 10 题 / 10 | 相同 10 题 / 10 |
| 计划 Linking / Generation | 10 / 10 | 5 / 10 |
| 计划总请求 | 20 | 15 |
| 每题 / 每组最大预约 | 2 / 20 | 2 / 20 |

总上限为 40，计划为 35；并非要求花完上限。temperature=0、n=1、输出上限 2048、零 SDK/应用重试、禁止重定向、并发 1。Evidence、Prompt 版本、SQL 超时 8 秒、最多 1,000 行、Prompt 字符上限 200,000 与两组完全相同。

10 个 ID、db_id、difficulty、完整来源指纹、11 库哈希、路由、模型参数和执行顺序保存在 ignored 私有 Frozen Manifest。它是同一批数据库上的新题留出验证，不能证明跨数据库泛化，也不能支持小样本统计显著性结论。

## Live / Cost Gates

真实执行必须同时提供 `--live --allow-api --authorization`，并匹配独立授权文件里的模型、供应商、endpoint、冻结配置/Manifest SHA256、有效期、一次性 ID 和最多 40 次预约。授权不是密钥文件；未知字段被拒绝。一次性 ID 在项目 ignored 输出根目录通过独占文件创建并 fsync 消耗，崩溃后更换输出目录也不能复用。该机制是本地操作约束，不是对恶意手改授权文件的密码学证明。

授权可选择正数 `max_usd` 软限额；根据本轮用户补充，也可显式设置 `no_monetary_cap=true` 且 `max_usd=null`。二者都需要新的明确 Live 许可，不从环境变量、历史授权或配置推断。

无可靠服务端硬消费上限时，授权还必须确认接受本地软限额的局限。请求计数不能绝对限制供应商扣费。按 Token 计费需要当日、同供应商/模型/endpoint 的输入、输出、缓存价格、USD 币种、额外收费规则、Usage 支持以及保守 tokenizer/envelope 边界证据。每次请求前以 UTF-8 字节数加已核验封装上界、输出 Token 上限和最坏输入单价承诺费用；供应商返回超出上界、缺失 Usage 或不明计费类别时停止。

也支持经账户证据确认的 `included_subscription`：必须核实本账户套餐包含该模型、无额外推理收费，且不填写虚构的 Token 单价。此时逐题 Token 费用分摊为 NOT APPLICABLE，订阅成本不分摊到本批，实际账单继续 UNKNOWN。没有账户证据时不能据公开套餐介绍填 0。

2026-10-23 检查的公开资料：[Usage & billing](https://docs.lunaroute.com/concepts/usage-and-billing/)、[Model catalog](https://docs.lunaroute.com/models/)、[Model policy & spend caps](https://docs.lunaroute.com/concepts/model-policy-and-spend-caps/)。公开说明包含自营套餐推理和独立计量类别，不能证明用户账户覆盖、项目硬限额、该模型准确当前费率或真实 Usage 字段。本轮未查询凭据保护的目录/账单 API，未读取生产 `.env`；候选配置价格和账户验证仍为 UNVERIFIED，真实运行保持 STOP。

报告严格区分 Provider Usage、Calculated Cost 和 Billed Cost；完整 Total Tokens 只在所有预约均有输入/输出 Usage 时填入（为二者之和，非另行返回字段），否则总量为 null，另列已知小计与覆盖率。没有可对账发票时 Billed Cost=UNKNOWN。Mock 的 Usage 和时间永远不作为真实供应商测量。

节点统计沿用 P0 的客户端 `Model.ask` 延迟口径，包含请求预约和审计；完整节点 wall time 另存于逐题 trace。Agent 端到端时间从白名单输入准备至 Graph 返回，不包含之后的报告写入。P50/P95 使用线性插值，包含所有已处理的失败/超时题，未运行槽位不填虚构延迟，明确记录有效样本数。

## Offline Use

在具备已校验数据的本地工作区，使用全新的私有目录：

```powershell
$RunRoot = Join-Path $PWD ('outputs/p1b_adaptive_live_validation_' + [guid]::NewGuid().ToString('N'))
.venv/Scripts/python.exe scripts/run_adaptive_live_validation.py --prepare `
  --db-root data/minidev/MINIDEV/dev_databases --out-dir "$RunRoot/frozen"
.venv/Scripts/python.exe scripts/run_adaptive_live_validation.py `
  --frozen-dir "$RunRoot/frozen" --db-root data/minidev/MINIDEV/dev_databases --out-dir "$RunRoot/dry_run"
.venv/Scripts/python.exe scripts/run_adaptive_live_validation.py --mock-llm `
  --frozen-dir "$RunRoot/frozen" --db-root data/minidev/MINIDEV/dev_databases --out-dir "$RunRoot/mock"
```

目录已存在则拒绝，不能通过另换目录自动重放真实批次。Frozen 源码/配置/数据/HEAD 变化也拒绝；任何修订必须留下原产物并重新准备，不能在看过真实输出后调整选题。

## Independent Official EX / Comparison

仅完成经授权真实批次并先对账后，独立运行已有导出器：`scripts/export_official_ex_inputs.py --run-dir <live-dir> --group B|E --suite-config <frozen-dir>/config.json ...`。再调用原 `scripts/run_official_ex.py --manifest <frozen-dir>/manifest.json ...`，固定使用官方 commit `abd11b6db92a1c9f809b32f7564c7c71b34d67f0` 及既有源码 SHA256；原评分器已支持指定 Manifest 和可变分母，不修改其评分定义。

导出器拒绝本阶段 Mock/Dry Run。两组各保留十个评分槽位；未运行、失败、超时、缺失或截断按原固定协议使用失败占位，不能静默删除样本。结果/审计绑定、组别、顺序和调用次数不一致时拒绝评分。评估过程只用私有数据库副本、只读连接和已有硬超时；Gold 只在此独立 Evaluator 访问，不能回流到 Agent。

`scripts/compare_adaptive_validation.py` 接收两个独立评分 Summary，核对结果来源哈希、Manifest、官方 commit/源码和全部十个身份后输出私有配对结果。它从经核对的审计及逐题记录重新汇总 Usage、延迟、Linking 请求和费用，输出 JSON 与 Markdown 的 B/E/差异表，不从历史 A/B 或旧预检小计补填。未执行真实批次时，官方 EX 始终 NOT EVALUATED。

## Acceptance

2026-10-23，使用既有 `.venv/Scripts/python.exe`，未安装新依赖：

| 本轮实际检查 | 结果 |
| --- | --- |
| 最终全部离线 pytest | **761 passed、8 skipped、0 failed；158.82 秒，exit 0** |
| 最初新增入口/预算/隔离/评估格式定向回归 | 75 passed；18.14 秒 |
| 固定官方源码 + 新 10 槽位合成夹具控制 | 1 passed；29.74 秒；不是模型 EX |
| 评估与比较器定向回归 | 10 passed；38.96 秒 |
| compileall（bird_starter、run_eval.py、scripts） | exit 0 |
| pip check | No broken requirements found，exit 0 |
| 最终真实元数据 Dry Run | B/E 各 10 题；预约 0，真实请求 0，exit 0 |
| 最终真实库 + Mock SDK 集成 | B/E 各 10 题；预约 20/15，真实请求 0，exit 0 |
| 最终完整 Schema 轨迹重建 | Mock 20 + Dry Run 20，共 40 条逐字一致 |
| 既有保护文件 SHA256 | 10,524 项不变，含 11 个原始 BIRD SQLite；旧实验目录清单无新增/删除 |
| 差异检查与核心默认实现核对 | whitespace 检查通过；原 Model、审计、SQL 工具、Graph、Gate、Prompt、旧配置及官方评分器未改 |

新增 76 项测试包含于最终全量回归。8 项 skipped 均为原有 `BIRD_RUN_INTEGRATION` opt-in：真实数据校验、Baseline 预检、两项 Schema、Repair、四组消融 Mock、官方控制和 Repair 诊断。未开启这些旧阶段集成，未将 skipped 写成 passed；本阶段新 10 题 Mock/Dry Run 已单独实际执行。前端未修改，未重跑浏览器验收。

开发初期测试曾出现 14 passed / 46 个共享 fixture setup errors，随后检查另有 1 个 setup error。原因是测试会清除凭据命名变量，使 Git 的 `GIT_CONFIG_COUNT` 留下不完整配置，且沙箱用户与工作区所有者不同。已改为对子进程仅传受限环境、仅对本项目目录设置命令级 `safe.directory`，不改全局 Git 配置。修复后各轮及最终回归通过，初始日志仍保留，没有清理以掩盖错误。

本轮私有输出为 `outputs/p1b_adaptive_live_validation_<唯一ID>/`。最终验收使用 `frozen_final/`、`mock_final/`、`dry_run_final/` 和 `preparation_report_final.json`；`budget_preflight_final.json`、`pricing_evidence.json`、`protected_final.json`、`pytest_acceptance.txt` 为对应证据。先前 `frozen/`、`mock/`、`dry_run/` 与初步报告作为中间预检保留，未覆盖。补齐比较器后重新冻结只绑定最终源码，题目、顺序、模型参数和 Manifest SHA256 与初次预检完全一致，没有根据输出调整选题。

**Gold Isolation：PASS（离线测试）。SQL Read Only：PASS（离线测试及原库指纹）。Real API：0。B/E Actual EX：NOT EVALUATED。** 真实 Tokens、真实延迟、Calculated Cost、Billed Cost 均未取得。所有生成阶段输出只保留本地；没有向前端新增详细轨迹接口。

## Next / Stop

工程上值得申请一次固定样本的受控真实验证，当前结果尚不能判断准确率或成本收益。本轮到此停止。进入 Live 仍需新的明确一次性调用许可，以及账户套餐/实际费率、Usage 支持和服务端限制或相应风险接受的核验。根据用户补充，不再要求金额上限；可在新的授权中明确使用“不设金额上限”，但请求预约仍不得超过 40 次。不得自动追加批次、扩大样本、运行真实 C/D、Commit、Push、PR 或 Merge。
