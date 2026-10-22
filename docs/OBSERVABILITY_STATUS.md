# 实验可观测性与离线错误分析：本轮验收

## [Stage]

P0：补强新消融实验的可观测性，并提供保存结果的离线错误分析。属于既有 Phase 3/5/6 链路的增量补强，不表示原规划的完整 Phase 6 或四组真实消融已完成。本轮完成后停止，后续算法优化、CI、MCP 与在线服务仍为可选阶段。

## [Files]

- `bird_starter/observability.py`：受控 Schema 记录、脱敏状态、原文与落盘文本哈希、共用模型调用指标。
- `baseline.py`、`schema_workflow.py`、`ablation.py`：复用运行时选择，保存完整表列与实际 Schema、回退原因、Prompt 版本和源码指纹；Baseline 补齐节点指标。未修改 Prompt、模型参数或 SQL 安全工具。
- `bird_starter/error_analysis.py`、`scripts/analyze_errors.py`：离线分类、官方评分来源核对、缺失分母保留、可绑定源哈希的人工归因、输入前后完整性检查。
- `tests/test_observability.py`、`tests/test_error_analysis.py`：合成 SQLite/分数夹具，覆盖实际 Schema、Gold 输入隔离、回退、脱敏、篡改拒绝、评分与人工标注错配、缺失记录、未知 Tokens、CLI 及私有输出边界。
- README、`.gitignore` 与 [使用手册](OBSERVABILITY.md)：新接口、边界与阶段说明。前端白名单数据接口保持原样。

## [Tests]

Windows / pwsh，使用既有 `.venv/Scripts/python.exe`，默认测试不联网。

| 实际检查 | 结果 |
| --- | --- |
| 首批相关五文件 pytest | 158 passed，22.22 秒；之后增加的输入漂移/版本检查由完整回归覆盖 |
| `-m pytest -q -rs --tb=short`，`BIRD_RUN_INTEGRATION=0` | **642 passed、8 skipped，118.49 秒，exit 0** |
| `-m pytest -q tests/test_real_ablation.py --tb=short`，显式 opt-in | **1 passed，7.43 秒，exit 0** |
| `-m compileall bird_starter run_eval.py scripts/analyze_errors.py` | exit 0 |
| `-m pip check` | No broken requirements found，exit 0 |
| `git diff --check` | exit 0 |
| 历史 A/B 分析 CLI | 66 条记录，exit 0；不执行 SQL、不请求模型 |
| 真实库 + Mock 新日志分析 CLI | 132 条记录，exit 0；不将 Mock 作为模型成绩 |
| 独立本地验收脚本 | 132 条 Schema 逐字重建、源码/历史输入哈希检查全部通过 |

8 项跳过均为已有 `bird_integration` opt-in 测试；真实四模式 Mock 测试在独立命令中执行，其余 7 项未在本轮另行启用。没有宣称全套 650 项均执行通过。自动测试继续清理继承凭据、隔离 dotenv 并阻断网络。

## [Proof]

- 新四模式 Mock 使用真实 33 题、11 库，132 条记录均有 EXACT Schema 证据；独立重新读取元数据，将每条完整选择经 `render_selection()` 渲染后，与保存文本和哈希逐条一致。生成链路源码 SHA256 与该次运行记录一致。
- 四模式共 242 次 Mock 调用/预约，remote API requests=0。它们只证明工程链路，未产生真实 C/D 或新的官方 EX。
- 历史分析仍为 A=19/33、B=18/33；24 条完整执行的官方 mismatch 仍待归因，2 条生成截断、3 条结果截断。全部 66 条旧记录保持 `UNKNOWN_NOT_RECORDED`，不补造实际列投影。
- 读取前后的 40 项保护文件（含 11 个原始 SQLite、固定配置和公开证据清单引用的历史产物）SHA256 一致。真实 C/D 输出目录仍不存在。
- 新输出均在 ignored `outputs/observability_v1_delivery/`。完整测试原始输出为 `pytest.stdout.txt`，集成输出为 `real_mock_pytest.stdout.txt`；历史与新 Mock 分析分别在 `historical_analysis_final/`、`real_mock_analysis/`；汇总为 `acceptance.json`。历史评分未重算或覆盖。

## [Risks]

完整 Schema 增加新日志体积；详细记录必须留在本地。旧轨迹的信息缺失无法修复，Schema 脱敏后无法逐字重建，崩溃发生在题目结果落盘前时记录仍可能缺失。官方/Proxy 判定仅供离线分析，人工分类仍需可追溯证据，不能反馈给生成或修复。当前改动不证明 EX、Token 或延迟有所改善。

## [Next]

建议下一阶段单独验证 Schema Linking 的总成本优化，先冻结独立验证样本及预算；当前 33 题作为开发集。保留既有 A/B 作历史参照，修改后的新实验使用独立版本和目录，不把历史授权复用为新的付费调用授权。
