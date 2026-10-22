# Text-to-SQL Agent V1：公开发布范围与检查

准备日期：2026-10-22。目标 pcwl22/Text-to-SQL-Agent，main。当前是**本地发布准备**，不是远程发布声明；git push 只能在用户最终确认后单独执行，禁止 force push。没有自动新增开发阶段或模型实验。

## 内容边界

公开：bird_starter 自行实现的 Python 源码、run_eval.py、离线工具脚本、测试及临时夹具生成逻辑、版本化非凭据配置、依赖声明、README 和经过审核的公开文档。公开证据仅为 configs/release/evidence_v1.json 的 Run ID/指标/哈希，不含原始模型记录。

不公开但保留本地：生产 .env/凭据、.venv、data、downloads、outputs、SQLite/压缩包、服务端请求响应、认证头、调试日志、官方评测器源码缓存、含个人机器目录的历史状态文档。采用 .gitignore 和显式暂存白名单，不删除原文件。公开文档已改用当前仓库根目录，不暴露本机用户目录。

项目尚无 LICENSE，所有者未选择原创源码许可证。本次不擅自添加 MIT/Apache 等许可。第三方来源和 Schema 元数据标注见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；上游数据、原库及评测器源码不分发。

## 固定结论

A 官方 EX 19/33，Tokens 45,773；B 官方 EX 18/33，Tokens 84,432。独立 Repair 诊断 8/8 不是 BIRD EX。C/D 没有真实运行，没有完整 500 题成绩、自然 Repair 收益或供应商实付费用结论。详见 [EXPERIMENT_RESULTS.md](EXPERIMENT_RESULTS.md)。

## 安全与可复现性检查方法

- Gitleaks v8.30.1 官方二进制在本地临时目录使用，下载资产 SHA256 对照官方发布元数据校验，扫描器不随提交分发。
- 先对发布候选目录扫描，再对从 Git 暂存区导出的精确文件扫描，创建本地提交后扫描完整本地 Git 历史。日志和报告使用 100% redact，不输出密钥、认证头或环境对象。
- 用本地生成、从未由服务发行的无效 Token 做扫描器阳性控制；控制只在 ignored outputs，不能提交。
- 初筛发现 Mock 脱敏测试的假凭据形态，已把两处测试字面量改为 TEST_ONLY_INVALID_CREDENTIAL，并保留测试功能；没有读取或替换生产密钥。不是通过全局忽略 tests 隐藏扫描结果。
- 补充检查赋值、Bearer/私钥/连接串形态、个人绝对路径、超大文件、SQLite/二进制、JSON Gold 字段、文档链接和模板值。模式扫描没有外部密钥有效性验证，不等同证明任何可能形式的秘密绝不存在。
- 所有真实测试使用项目 .venv，pytest 在收集前清除认证变量并隔离真实 dotenv，网络禁止；真实库集成只使用 Mock SDK 和独立官方控制。无 LunaRoute 或其它模型 API 请求。
- .gitattributes 保留冻结 JSON 原始字节，避免 Git 换行转换破坏 SHA256。暂存快照需在未包含 data/outputs/.env 的干净目录中通过默认离线测试。
- docs/phase3c_live_commands.ps1 是仅定义参数、实际模型调用全部注释的历史模板，静态 AST 测试需要它，因此纳入公开白名单；不执行该文件中的任何模型实验。它不包含个人绝对路径或凭据，保留原字节与原注释。
- 本地提交使用 GitHub 公开 noreply 身份，不写入私人邮箱；仓库初始检查为空，无本地旧历史，远程提交/refs 检查为空。推送前仍应再次检查远程，不能假设一直为空。

本轮实际文件数量、测试结果、扫描结局及本地 commit 以用户收到的最终发布准备汇报和本地 ignored 审计证据为准。未完成的检查不能标记通过，远程可见状态只能在最终确认后的 push 成功后宣布。

工作区完整验证实际为 604 passed（397.59 秒，0 skipped），compileall/pip check 全部 exit 0。第一次纯源码暂存快照因遗漏上述 PowerShell 模板而有 1 failed、576 passed、27 skipped；已补齐模板，不能将该首次失败写成通过。27 项跳过中 19 项是可选官方源码缓存缺失，不将未随仓库分发的上游源码偷偷加入提交以凑通过数量。修复后的干净快照结果另行记录。

修正后的精确暂存副本实测 **577 passed、27 skipped、0 failed，51.54 秒，exit 0**。发布白名单 99 项，候选扫描未检出密钥；暂存源码/配置的原始字节和 source_profiles 全部校验通过。最大的文件为约 37 KB 的诊断配置，没有数据库或超大二进制。最终暂存扫描与提交后 Git 历史扫描结果保留在本地发布审计证据中，push 仍须用户最终确认。
