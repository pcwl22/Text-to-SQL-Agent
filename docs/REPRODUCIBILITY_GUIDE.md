# Windows 离线复现手册

本手册默认不调用模型、不加载生产密钥、不启动正式 C/D。历史请求授权均已结束，不能延续。下列输出使用新的 UUID 目录；禁止将目标改成历史实验路径、添加覆盖或自动续跑。命令是复现步骤，本轮实际执行结果在末节及 `outputs/final_delivery_v1/final_acceptance.json` 中另行列出。

## 1. 环境与依赖

已验证 Windows、Python 3.13.5，解释器 `.venv/Scripts/python.exe`。使用 PowerShell 7 的 pwsh，当前实际为 7.6.5；项目约定优先 7.6.3，不使用 Windows PowerShell 5.1。没有为了评测迁移 Python。

```powershell
# 在已克隆的仓库根目录运行下列命令。
$ProjectRoot = (Get-Location).Path
$ProjectPython = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $ProjectPython)) {
  throw '项目 .venv 不存在；先准备指定版本的本地 Python 和依赖，不使用不确定的 PATH。'
}
& $ProjectPython -c 'import sys; print(sys.executable); print(sys.version.split()[0])'
& $ProjectPython -m pip check
```

生产依赖 requirements.txt：openai、LangGraph、sqlglot、MCP、pytest、python-dotenv，受版本范围约束；评测依赖独立 requirements-eval.txt：func-timeout 4.3.5、PyMySQL 1.2.3、psycopg2-binary 2.9.13。当前版本快照另见 `outputs/final_delivery_v1/environment.json`。范围要求不是精确锁文件，不能保证未来重装字节相同。

```powershell
# 仅全新环境、由操作者准备本地安装包后执行；现有 .venv 不重建。
# py -3.13 -m venv .venv
# & $ProjectPython -m pip install --no-index --find-links .\wheelhouse `
#   -r requirements.txt -r requirements-eval.txt
```

若没有离线 wheels，先单独安排依赖获取，不把包安装失败当成模型配置问题，也不盲目换 Python。以上安装示例不包含模型请求。

## 2. 离线隔离、路径与新输出

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
$env:LANGSMITH_TRACING = 'false'
$env:LANGCHAIN_TRACING_V2 = 'false'
$env:BIRD_CASES_PATH = Join-Path $ProjectRoot 'data/mini_dev_data/mini_dev_sqlite.json'
$env:BIRD_DB_ROOT = Join-Path $ProjectRoot 'data/minidev/MINIDEV/dev_databases'
$ReproRoot = Join-Path $ProjectRoot ('outputs/repro_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $ReproRoot | Out-Null
```

11 库路径为 `<db_id>/<db_id>.sqlite`。不要复制大型原库来适配路径，不使用旧的不存在目录。数据配置优先级为显式 CLI > BIRD 环境变量 > 旧默认布局；旧默认 DB 路径仍是 data/mini_dev_data/dev_databases，因此这里必须显式配置现有目录。

题目 SHA256 为 `88ceb0710163cae46a256ecea8f0a8c98286599530b60587fda5c3cfe57d45d2`；33 题 Manifest SHA256 为 `7136f72aa9e0b78ccab8b29dfb077247a082e541604b4f8841fede2a63ba6dd8`。只从官方来源准备用户数据，缺失时停止，不生成替代 BIRD 数据。来源/版本见 [DATA_PREPARATION.md](DATA_PREPARATION.md)。

```powershell
Get-FileHash -LiteralPath $env:BIRD_CASES_PATH -Algorithm SHA256
Get-FileHash -LiteralPath configs/phase3a/bird_dev33_v1.json -Algorithm SHA256
& $ProjectPython scripts/validate_bird_data.py `
  --cases $env:BIRD_CASES_PATH --db-root $env:BIRD_DB_ROOT `
  --check-schema-compatibility
if ($LASTEXITCODE -ne 0) { throw '数据校验失败；停止后续步骤。' }
```

预期 500 题、11 库、500/500 映射，完整性及 Schema 编译检查通过。此步骤只读；可选 EXPLAIN 在独立数据校验/evaluator 侧接触 Gold，不发送任何模型请求，也不计算模型准确率。

## 3. 无 API 测试与 Dry Run

pytest 的 conftest 在收集前清除认证变量，用临时项目根隔离 dotenv，并禁止网络。完整测试中有专门的 fake dotenv 测试，需要暂时移除全局 PYTHON_DOTENV_DISABLED；这不会绕过生产 dotenv 隔离。

```powershell
Remove-Item Env:PYTHON_DOTENV_DISABLED -ErrorAction SilentlyContinue
$env:PYTEST_DEBUG_TEMPROOT = Join-Path $ProjectRoot 'outputs/phase2_test_tmp'
New-Item -ItemType Directory -Force -Path $env:PYTEST_DEBUG_TEMPROOT | Out-Null
$env:BIRD_RUN_INTEGRATION = '0'
& $ProjectPython -m pytest -q -rs --tb=short
if ($LASTEXITCODE -ne 0) { throw '离线测试失败。' }
$env:PYTHON_DOTENV_DISABLED = '1'
```

默认数量取决于本地可选资源：在本次 Windows/pwsh 环境，纯源码副本没有官方评测器缓存时为 577 passed、27 skipped，分别为 8 项真实数据开关和 19 项官方源码/可选依赖门控测试；已有源码缓存与评测依赖时为 596 passed、8 skipped。测试没有删除或放宽断言。安装了真实数据及官方源码缓存后，如需完整 604 项和独立 8 项真实集成，使用新报告路径：

```powershell
Remove-Item Env:PYTHON_DOTENV_DISABLED -ErrorAction SilentlyContinue
$env:BIRD_RUN_INTEGRATION = '1'
$env:BIRD_INTEGRATION_REPORT = Join-Path $ReproRoot 'tests/real_data.json'
$env:BIRD_SCHEMA_REPORT_DIR = Join-Path $ReproRoot 'tests/schema'
$env:BIRD_REPAIR_REPORT_DIR = Join-Path $ReproRoot 'tests/repair'
$env:BIRD_ABLATION_REPORT_DIR = Join-Path $ReproRoot 'tests/ablation'
$env:BIRD_OFFICIAL_EX_REPORT_DIR = Join-Path $ReproRoot 'tests/official'
$env:BIRD_DIAGNOSTIC_REPORT_DIR = Join-Path $ReproRoot 'tests/diagnostic'
& $ProjectPython -m pytest -q --tb=short
if ($LASTEXITCODE -ne 0) { throw '完整验收失败；保留新输出。' }
# 独立集成必须换目录，不能复用上面的独占输出：
$env:BIRD_INTEGRATION_REPORT = Join-Path $ReproRoot 'integration/real_data.json'
$env:BIRD_SCHEMA_REPORT_DIR = Join-Path $ReproRoot 'integration/schema'
$env:BIRD_REPAIR_REPORT_DIR = Join-Path $ReproRoot 'integration/repair'
$env:BIRD_ABLATION_REPORT_DIR = Join-Path $ReproRoot 'integration/ablation'
$env:BIRD_OFFICIAL_EX_REPORT_DIR = Join-Path $ReproRoot 'integration/official'
$env:BIRD_DIAGNOSTIC_REPORT_DIR = Join-Path $ReproRoot 'integration/diagnostic'
& $ProjectPython -m pytest -q -m bird_integration -rs --tb=short
& $ProjectPython -m pytest -q tests/test_gold_isolation.py --tb=short
& $ProjectPython -m compileall bird_starter run_eval.py
& $ProjectPython -m compileall scripts
& $ProjectPython -m pip check
$env:PYTHON_DOTENV_DISABLED = '1'
```

每条检查均应确认退出码 0，不能只看最后的 pip 输出。集成包含四组 **Mock** 错误注入及官方 Gold/错误/缺失控制，没有正式 C/D 或真实模型请求；Gold 控制不是模型 EX。部分集成父目录由各测试自动创建，目标子目录仍须未使用。604 项全开会比默认单元套件慢，并临时复制评测数据库。

```powershell
& $ProjectPython scripts/run_ablation.py --groups A B --dry-run `
  --cases $env:BIRD_CASES_PATH --db-root $env:BIRD_DB_ROOT `
  --out-dir (Join-Path $ReproRoot 'dry_AB')
& $ProjectPython scripts/run_repair_diagnostic.py --dry-run `
  --db-root $env:BIRD_DB_ROOT --out-dir (Join-Path $ReproRoot 'dry_diagnostic')
```

A/B Dry Run 预期各 33 条 dry_run_ok、模型请求和 PREPARED 均 0；无生成 SQL 或 SQL 执行成绩。诊断 Dry Run 检验 8 个初始错误和 4 个负例，模型调用/预约为 0；为验证错误会执行预设安全只读探针，不是模型生成实验。

## 4. 历史结果及请求审计核查

原始目录 A_v1、B_v1、repair_diagnostic_live/v1 一律只读，不能将其作为新 out-dir。每组 Config、结果 JSONL、Summary、请求审计、官方分数/报告分别保留；生成端 Summary 的 official_ex=null 不能手工回填。

```powershell
& $ProjectPython scripts/audit_requests.py --config outputs/phase3c_live/A_v1/batch.config.json
& $ProjectPython scripts/audit_requests.py --config outputs/phase3c_live/B_v1/batch.config.json
& $ProjectPython scripts/audit_requests.py --config outputs/repair_diagnostic_live/v1/requests.config.json
```

预期预约/结果分别对应 33、66、8，未知结局 0，run/case/attempt 能关联。通用 inspector 的 actual_http_attempts 可保守保留 UNKNOWN；结合完整响应、逐题计数、重试 0 和重定向关闭才能完成实际对账，不能把 PREPARED 单独当作已发送证据。历史 V1 次数永远保持 UNKNOWN。

公开哈希索引为 configs/release/evidence_v1.json，只含元数据，没有原始日志内容。只有持有原工作区证据时才能逐项验证；纯源码克隆应明确报告缺失，不能宣称已核验历史文件。

```powershell
@'
import hashlib,json
from pathlib import Path
root=Path.cwd()
m=json.loads((root/'configs/release/evidence_v1.json').read_text(encoding='utf-8'))
missing=[name for name in m['historical_files'] if not (root/name).is_file()]
if missing:
    raise SystemExit('Private historical artifacts unavailable; verification NOT performed.')
for name,expected in m['historical_files'].items():
    with (root/name).open('rb') as f:
        assert hashlib.file_digest(f,'sha256').hexdigest()==expected, 'Historical bytes changed'
print('All available historical artifacts match the public hash index.')
'@ | & $ProjectPython -
```

该索引不是签名或外部时间戳证明，仅供内容核对；不能修改清单来掩盖差异。历史完整日志、机器路径、原始模型响应和数据库不随公开提交分发。软件源码和文档在发布整理中可有明示变更，不把它们误当成不可修改的原始实验数据。

## 5. A/B 历史结果离线重评与官方 EX

需要固定源码缓存 `outputs/official_ex_source/abd11b6db92a1c9f809b32f7564c7c71b34d67f0/`，两个源码文件应匹配 configs/official_ex/upstream.json 的 SHA256。源码缺失时停止，按固定官方 commit 单独准备；本手册不会自动联网下载或直接执行未保护的官方 CLI。

```powershell
foreach ($Group in @('A','B')) {
  $RunDir = "outputs/phase3c_live/${Group}_v1"
  $BundleDir = Join-Path $ReproRoot "inputs_$Group"
  $ScoreDir = Join-Path $ReproRoot "EX_$Group"
  & $ProjectPython scripts/export_official_ex_inputs.py `
    --run-dir $RunDir --group $Group --cases $env:BIRD_CASES_PATH --out-dir $BundleDir
  if ($LASTEXITCODE -ne 0) { throw '输入导出失败。' }
  & $ProjectPython scripts/run_official_ex.py --bundle $BundleDir `
    --db-root $env:BIRD_DB_ROOT --out-dir $ScoreDir `
    --sql-timeout 30 --worker-timeout 40 --outer-timeout 1500
  if ($LASTEXITCODE -ne 0) { throw '离线评分失败；不得重新调用生成模型。' }
}
```

只读取已经生成的 SQL。输入包含 predictions.json、独立 evaluator 的 gold.sql、difficulty.jsonl、bundle.json；不允许把 gold.sql 发回 Agent 或提交 Git。官方执行通过私有 SQLite 副本、只读连接、spawn worker 和硬超时完成；原库哈希应不变，正常结束后副本被清理。失败、中断产生的私有残留先保留，不能删除原库。

预期 A=19/33、B=18/33；验证 summary 的 planned_cases=33、source_result_sha256、Manifest、commit、逐题 case_id/db_id 与固定协议。不可仅因总分相同就略过逐题身份检查。离线重评是新的独立评估产物，绝不替换历史目录中的 official_ex/summary.json。导出器部分旧提示含“engine blocked”，仅表示它不负责执行评分；以独立 scorer 的退出码及新 Summary 为准。

## 6. 逐题报告再生成与官方结果关联

```powershell
& $ProjectPython scripts/report_ablation.py `
  --run A=outputs/phase3c_live/A_v1 --run B=outputs/phase3c_live/B_v1 `
  --out-dir (Join-Path $ReproRoot 'AB_report')
```

输出 report.json、per_case.jsonl、report.md；保留 fixed denominator、真实节点用量、Proxy 和缺失值。**该历史报告器故意不合并官方评分，official_ex 仍为 null/pending，不能把其旧 readiness 文案当成当前官方适配器不可用，也不能改写原报告器以回填成绩。** 官方结果从上一步独立 Summary 读取。下面按索引及唯一 ID 严格关联，没有 zip 截断：

```powershell
@'
import json,sys,hashlib
from pathlib import Path
root=Path(sys.argv[1]); groups={}
for g in 'AB':
    original=Path(f'outputs/phase3c_live/{g}_v1/{g}.jsonl')
    predicted=[json.loads(s) for s in original.read_text(encoding='utf-8').splitlines()]
    scored=json.loads((root/f'EX_{g}/summary.json').read_text(encoding='utf-8'))
    assert len(predicted)==scored['planned_cases']==len(scored['rows'])==33
    assert scored['source_result_sha256']==hashlib.sha256(original.read_bytes()).hexdigest()
    assert len({r['case_id'] for r in predicted})==33
    assert [(r['case_id'],r['db_id']) for r in predicted]==[(r['case_id'],r['db_id']) for r in scored['rows']]
    groups[g]=(predicted,scored)
assert groups['A'][1]['manifest_sha256']==groups['B'][1]['manifest_sha256']
assert groups['A'][1]['protocol']==groups['B'][1]['protocol']
assert groups['A'][1]['official_commit']==groups['B'][1]['official_commit']
assert [(r['case_id'],r['db_id']) for r in groups['A'][0]]==[(r['case_id'],r['db_id']) for r in groups['B'][0]]
paired=[]
for i in range(33):
    a,b=groups['A'][1]['rows'][i],groups['B'][1]['rows'][i]
    paired.append({'case_id':a['case_id'],'db_id':a['db_id'],'A':a['res'],'B':b['res']})
with (root/'paired_official.json').open('x',encoding='utf-8') as f:
    json.dump(paired,f,ensure_ascii=False,indent=2)
print('A:',sum(r['A'] for r in paired),'/33; B:',sum(r['B'] for r in paired),'/33')
'@ | & $ProjectPython - $ReproRoot
```

原始分级案例与逐题官方配对还可通过已有纯离线脚本再生成，须换新输出：

```powershell
& $ProjectPython scripts/analyze_ablation_ab.py `
  --freeze outputs/phase3c_ab_review/before.json `
  --out-dir (Join-Path $ReproRoot 'AB_analysis')
```

它只分析已有 SQL/元数据与历史官方判定，禁止网络；不会生成 C/D 或调用 Repair。精确历史 Linking JSON/列投影未保存时仍为 UNKNOWN。

## 7. 独立 Repair 诊断结果验证

优先查看 `docs/REPAIR_REAL_MODEL_DIAGNOSTIC_RESULT.md`、原始 7 项输出及 `outputs/phase3c_repair_decision_v1/analysis.json`：8 例 ID/SQL/attempt/断言一一对应，本轮只读复验行值与冻结参考相同。原始历史结果行未落盘，不能宣称恢复。

本地重新验证诊断机制可用已授权的离线 Mock 测试，不发真实请求：

```powershell
Remove-Item Env:PYTHON_DOTENV_DISABLED -ErrorAction SilentlyContinue
$env:BIRD_RUN_INTEGRATION='1'
$env:BIRD_DIAGNOSTIC_REPORT_DIR=Join-Path $ReproRoot 'repair_verify_mock'
& $ProjectPython -m pytest -q tests/test_real_repair_diagnostic.py --tb=short
$env:PYTHON_DOTENV_DISABLED='1'
```

这证明真实库 + Mock 的流程/断言，不重新验证模型在线能力。核查原始真实模型产物，应对照冻结指纹和原审计；如要只读重执行原修复 SQL，须拥有原始记录及同哈希数据库；使用正式 validate_candidate/execute_select 和独立 semantic_assertion，输出到新的本地目录，不构造 Model。公开源码缺少这些私有记录时，仅能运行上面的 Mock 集成，不应声称重新验证了 8 次真实响应。实际聚合结果及哈希见 [EXPERIMENT_RESULTS.md](EXPERIMENT_RESULTS.md) 与 configs/release/evidence_v1.json。语义断言不是官方 EX，4 个负例永远不进入真实模型路径。

## 8. 常见故障与授权边界

| 表现 | 检查和处理 |
| --- | --- |
| ModuleNotFoundError: sqlglot / pymysql / psycopg2 | 确认 sys.executable 为项目 .venv，分别核对 runtime/eval 依赖，不换全局 PATH 碰运气 |
| 11 库全缺失 | 显式指定 data/minidev/MINIDEV/dev_databases；旧默认目录不同，不重复下载/搬库 |
| JSON/Manifest/source/hash 不匹配 | 停止，核对冻结版本，不能混用题目/数据库或改哈希绕过 |
| 8 skipped | 当前 BIRD_RUN_INTEGRATION 未设 1；明确报告；启用后路径/源码缺失应失败而非伪造成功 |
| fake dotenv 测试失败 | 测试前移除 PYTHON_DOTENV_DISABLED，由 conftest 隔离真实 .env；不要展示环境对象 |
| 输出目录已存在 | 换全新离线输出路径，保留原产物；真实实验不能因此擅自重跑 |
| 官方 worker/外层超时 | 保留新 stderr/退出码/结果，确认本次子进程已回收；不增加模型调用 |
| 报告 official_ex=null | 读独立 scorer Summary；不要用 Proxy 或 Mock 填充 |
| Token Usage 或账单缺失 | 保留 null/UNKNOWN，不填 0 或凭经验估价 |

任何未来真实模型实验都必须重新明确组别/案例、HTTP 尝试上限、零重试、独占目录、凭据及历史指纹后获得新授权。当前手册不提供可直接执行的真实模型命令，不调用 /models，也不把 `.env` 存在视作授权；不自动运行 C/D 或后续功能。

## 本次交付实测索引

本轮实际使用新根目录 `outputs/final_delivery_v1/`：full、integration 为隔离测试；reproduce 包含 A/B 零请求 Dry Run、新官方输入和重评结果、逐题报告、诊断 Dry Run 与专项日志；before.json/environment.json/final_acceptance.json 是原交付的本地证据，不随公开提交分发。

实测：完整 604 passed（430.49 秒）、独立 BIRD 8 passed/596 deselected（323.32 秒），均无失败/跳过；Gold 隔离专项 5 passed（1.90 秒）；编译及 pip check 通过。新官方重评 A=19/33、B=18/33，逐题与旧评分一致；两组 Dry Run 共 66 条、模型/预约均 0；诊断 Dry Run 8 正例/4 负例、模型/预约均 0。原诊断 SQL 只读复验 8/8。full/integration 的 Gold/无效/缺失控制各为 33/33、32/33、32/33，不计入模型成绩。

以上命令均 exit 0；文档本地链接/凭据形式扫描通过，337 项历史/源码/数据指纹不变。完整原始日志、逐题官方关联 paired_official.json 和独立分析保留在新目录，未覆盖历史产物。数据校验也在两次真实集成中执行；没有把手册里的依赖安装或未来真实实验示例说成已执行。


公开发布说明：本文件保留技术结论，但机器专用路径与私有状态文档链接已改为便携说明/公开摘要。原工作区文档备份和原始实验数据仍保留本地，不公开；当前仓库许可状态及来源见 [第三方与许可说明](THIRD_PARTY_NOTICES.md)。
