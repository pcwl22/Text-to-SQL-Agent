# Vue 3 前端交付验收

状态：PASS（本轮 Vue 3 离线前端范围）。已实现六页展示台，最终测试与哈希结果见下。分支 feat/vue3-dashboard，基于已发布 main 的 92f019345f27fb90926fa8a7a3738acf1fe0ecbe。开发验收时未提交、未推送、未合并；后续发布准备复审见末节。

## 实现范围

- `/`：ECharts 官方 EX、Tokens、延迟，固定 33 题 / 11 库来源标记。
- `/playground`：三条手写 DEMO 快照、数据库选择、问题输入、Evidence、Schema、只读 SQL / 懒加载 Monaco、结果表、错误快照、步骤回放。不是 BIRD 数据，不执行用户输入。
- `/workflow`：Vue Flow 四种架构、Linking / Repair 开关、节点详情、State 示例、条件边与至多一次修复回路；放大/缩小/适应画布。
- `/experiments`：A/B 实测指标与 33 条白名单逐题得分；case 1317 CONFIRMED / LIKELY / UNKNOWN；C/D 未运行。
- `/audit`：AST、SQLite 只读、Gold 隔离、共享预算、请求准备落盘与结局追踪，静态机制展示。
- `/about`：技术栈、成果、来源与限制。

Pinia 管理组别、DEMO 案例、Workflow 模式、主题和回放状态。Repository 隔离数据访问；Axios 仅有默认拒绝请求的预留工厂。没有 React、模型 SDK、Web 后端或在线 SQL 功能。

## 环境与依赖

Windows，PowerShell 7，Node 24.11.0、npm 11.6.1；Python 使用项目 .venv（3.13.5）。实际依赖以 frontend/package-lock.json 为准。

首次 npm install 因受限网络等待而中止，切换到允许访问 npm registry 的安装后完成。初始版本审计 5 项告警（含开发工具的 2 项 critical）；更新 Vitest 4.1.12、Monaco 0.57.0，并为其精确钉住的 DOMPurify 使用兼容的 3.4.17 补丁 override 后，npm install 报告 0 vulnerabilities。Vue Test Utils 固定 2.4.6，避免较新传递依赖的 Node engine 不兼容警告。ESLint 为 10.x。没有使用 npm audit fix --force。

## 实际命令与结果

均无真实模型调用；命令在相应目录执行。

| 命令 | 实际结果 |
| --- | --- |
| frontend: npm install --no-fund --fetch-retries=0 --fetch-timeout=60000 | 退出 0；最终 430 包审计，0 vulnerabilities |
| frontend: npm run build | 退出 0；vue-tsc + Vite 生产构建成功 |
| frontend: npm run type-check | 退出 0；strict / unused 检查通过 |
| frontend: npm run lint | 退出 0；0 warnings |
| frontend: npm run test | 退出 0；32 passed，4 文件 |
| .venv/Scripts/python.exe -m pytest -q --tb=short | 退出 0；604 passed，0 skipped，422.21 秒 |
| .venv/Scripts/python.exe -m pytest -q -m bird_integration --tb=short | 退出 0；8 passed，596 deselected，308.15 秒 |
| .venv/Scripts/python.exe -m pytest -q tests/test_gold_isolation.py --tb=short | 退出 0；5 passed |
| .venv/Scripts/python.exe -m compileall bird_starter run_eval.py | 退出 0 |
| .venv/Scripts/python.exe -m compileall scripts | 退出 0 |
| .venv/Scripts/python.exe -m pip check | 退出 0；No broken requirements found |

完整 Python 与独立集成设置 BIRD_RUN_INTEGRATION=1，真实库路径 data/minidev/MINIDEV/dev_databases，JSON data/mini_dev_data/mini_dev_sqlite.json。BIRD_*_REPORT* 使用 outputs/frontend_delivery 下的独立目录，避免覆盖历史输出。conftest 收集前清除真实认证变量、隔离 dotenv 并禁止网络，未使用生产 Key。

首轮前端检查确实发现图标导出名、新版 Monaco exports 路径和 4 条模板格式警告，已修复。安装过程中保持运行的旧 Vite 进程缓存了旧包 exports，造成 Playground 500；安装完成后重启开发服务，实际复验已消除。没有把这些中间失败记录为通过。

## 浏览器实际验收

使用 Playwright CLI + 本机 Edge，对 http://127.0.0.1:5173 开发服务与 http://127.0.0.1:4173 生产预览验证。实际打开六页、Dashboard 三个 Canvas 图表，完成浅深主题切换、手机菜单、固定案例回放、改写问题禁用、Monaco 只读懒加载、Vue Flow Optional Repair 节点点击和 Baseline 模式切换。

六页 × 1440 / 768 / 390 / 320 宽度，共 24 组 document.scrollWidth ≤ innerWidth 断言通过。表格超宽只在自身容器滚动。生产浏览器验收脚本未捕获外部请求或控制台异常，Monaco 本地 worker 正常加载。

本地截图和 CLI 验收脚本在 ignored frontend/output/playwright；命令轨迹在 ignored frontend/.playwright-cli。截图仅为 UI / DEMO，不公开任何请求内容。

## 保护与来源

保护快照在 ignored outputs/frontend_delivery/protected_before.json，覆盖 231 个文件：Python / 配置 / 已发布文档、A/B/独立诊断和既有 Phase 2 产物、11 个 SQLite。结束时逐文件 SHA256 对账，结果记录在 protected_after.json。README 与 .gitignore 为本轮允许修改项，独立排除；Python 核心与既有测试未改。

实际对账：231/231 SHA256 一致，11/11 SQLite 一致，changed=[]。42 个候选变更文件经 Gitleaks 8.30.1 默认规则、无 allowlist、redact=100 扫描：退出 0、0 findings；仅复制候选公开源码和文档到 ignored public_snapshot，不读取生产 .env。包、dist、浏览器快照和截图确认受 gitignore 排除；frontend/src/data 明确允许追踪，避免根目录 data/ 规则误排前端静态数据。git diff --check 退出 0，HEAD 仍为发布提交。

逐题数据从 A_v1 / B_v1 official_ex/results.jsonl 只读白名单提取，逐条核对 Manifest 顺序和 db_id，总分分别 19 / 18。浏览器只收到五字段，不收到原始日志、题目、Gold 或结果行。

不读取 .env；frontend 未创建 dotenv。public 仅 SVG。Vite 隔离 envDir，无模型接口 URL；测试阻止 fetch/XHR；未来 Axios 也拒绝发送。真实模型请求 0，C/D 未运行。

## 局限与 PR 状态

开发验收已具备前端 PR 的代码与离线基础；用户随后另行授权本地提交，推送、创建 PR 和合并仍未授权。模型实际推理和 SQL 执行尚未接入 Web；C/D 未真实运行、自然 Repair 收益未知、真实 MCP Client 未集成。

Monaco 懒加载模块约 2.73 MB（gzip 约 708 KB）及本地 worker；构建有该 chunk 超过 700 KB 的体积提示，非构建错误。首次增强预览有加载成本，默认使用轻量代码块。后续可评估精简编辑器模块，不能为了减包削弱安全限制。

静态生产部署需 history fallback 与 HTTP 安全头（frame-ancestors 不能靠 meta），开发/预览仅绑定 localhost。未部署或发布到 GitHub Pages。生产部署与 Live Inference 需另行明确范围。


## 发布准备最终复审（本地提交范围）

本节为开发验收之后的独立复审，覆盖以下 42 个文件；没有改动 Python 核心、既有 pytest、历史报告或真实数据库。feat/vue3-dashboard 基于发布提交，main 与 GitHub 远程 main 仍为 92f019345f27fb90926fa8a7a3738acf1fe0ecbe，无历史重写。没有配置自定义 Git hooksPath，没有活动 hooks。

### 本轮实际回归

| 命令 | 结果 |
| --- | --- |
| frontend: npm run build | 退出 0，Vite 7.3.7；保留 Monaco 核心 chunk 的体积提示 |
| frontend: npm run type-check | 退出 0，TypeScript strict |
| frontend: npm run lint | 退出 0，0 warnings |
| frontend: npm run test | 退出 0，32 passed / 4 文件 |
| .venv/Scripts/python.exe -m pytest -q --tb=short | 退出 0，604 passed / 0 skipped，487.92 秒 |
| .venv/Scripts/python.exe -m compileall bird_starter run_eval.py | 退出 0 |
| .venv/Scripts/python.exe -m compileall scripts | 退出 0 |
| .venv/Scripts/python.exe -m pip check | 退出 0，No broken requirements found |
| npm audit --json | 退出 0，0 vulnerabilities（全部生产及开发依赖） |
| Gitleaks 候选快照扫描 | 退出 0，0 findings；默认规则，无 allowlist，redact=100 |

Python 继续使用项目 .venv，BIRD_RUN_INTEGRATION=1，报告写入全新的 outputs/frontend_pr_prepare/tests，既有 conftest 清除认证变量、隔离生产 dotenv、禁止网络。因本轮只修改发布文档，没有新增功能行为或裁剪编辑器，不为已有实现增加重复性测试。锁文件 v3 根依赖范围与 package.json 一致，npm ls 确认安装依赖完整；所有 resolved 地址均为官方 npm registry。

### Monaco 实际模块与加载审查

通过临时 Vite/Rollup 审计插件读取 chunk.modules、静态依赖与动态入口，报告仅存 ignored outputs/frontend_pr_prepare/bundle-before.json / bundle-after.json。审计工具在 ignored frontend/output，不随代码提交。

| 构建产物 | 审核前字节 | 本轮构建后字节 |
| --- | ---: | ---: |
| Monaco 核心 editor.api | 2,728,712 | 2,728,712 |
| 通用编辑器 Worker | 275,665 | 275,665 |
| SQL 语法 | 10,159 | 10,159 |
| SQL 注册入口 | 881 | 881 |
| 编辑器 CSS | 99,224 | 99,224 |

核心 gzip 约 707.68 KB（Vite 默认压缩统计口径）。没有减小体积的虚假声明。699 个编辑器核心模块主要为配置、TextModel、DOMPurify、树/列表、渲染等；语言定义仅 SQL register、SQL grammar 与通用注册辅助 3 项。未引入完整 monaco-editor index、全部语言、LSP、TypeScript / JSON / HTML 语言 Worker。

入口与六个路由的静态依赖闭包均不含 Monaco chunk；Playground 的 SqlViewer 只在点击增强预览时动态引入 API、SQL 注册器和 Worker。生产浏览器实测六路由直接访问及刷新 HTTP 200，编辑器请求均为 0；点击后才观察到 editor.api、SQL、编辑器 CSS 和本地 Worker。编辑器浅深主题、固定 DEMO 回放、任意问题禁用、手机菜单和 Vue Flow 节点点击通过；24 组宽度断言通过，控制台 errors=0、warnings=0，外部请求=0。

现有动态边界已经合理；进一步移除编辑器核心需要依赖不稳定内部实现或改变功能，本轮保持功能及按需加载方案不变，没有人为拆包掩盖总大小，没有提高 chunkSizeWarningLimit（仍为 700）。保留首次点击加载成本这一风险。相关 API 依据为 [Monaco 官方 ESM 集成说明](https://github.com/microsoft/monaco-editor/blob/main/docs/integrate-esm.md) 及已安装 0.57.0 包的实际 exports，未复制其源码。

### 数据与安全清单

- 逐条核对前端 33 条 caseId / dbId / difficulty / a / b 与固定 Manifest 和冻结 A/B 官方评分；A=19、B=18，唯一差异 1317。
- A Tokens 45,773 / 8.201 秒；B Tokens 84,432 / 45.007 秒；独立 Repair 8/8 不是官方 EX。
- C/D 保持未运行，未宣称完整 500 题成绩；费用 UNKNOWN；1317 保留 CONFIRMED / LIKELY / UNKNOWN，不确认 Linking 因果。
- 文件清单人工审查并配合自动检查：无生产 dotenv、认证凭据、私人绝对路径、真实 SQLite、Gold、原始模型日志、包缓存或 dist。public 只有项目自行绘制的 SVG favicon。DEMO 均为独立手写界面示意。
- 第三方依赖仅声明与锁定，不提交 node_modules / dist / vendored 源码；BIRD 选择元数据继续指向既有来源及 CC BY-SA 说明。本轮不添加项目 LICENSE。
- 扫描零命中不代表绝对安全；结论结合人工清单、白名单数据契约、默认拒绝的 Axios、浏览器网络检查和 Git 排除验证。
- 提交前按下列明确清单暂存，导出暂存区后再次 Gitleaks 扫描并检查 cached diff、路径和大小；不使用 git add -A。
- 本轮只创建获授权的本地提交。完整 Commit SHA 以最终 Git 核验与汇报为准；不 push、不创建 PR、不合并 main、不调用模型、不启动 C/D。

### 明确提交清单（42 文件）

```text
.gitignore
README.md
docs/FRONTEND_DESIGN.md
docs/FRONTEND_STATUS.md
frontend/README.md
frontend/eslint.config.js
frontend/index.html
frontend/package-lock.json
frontend/package.json
frontend/public/favicon.svg
frontend/src/App.vue
frontend/src/api/futureClient.ts
frontend/src/api/repository.ts
frontend/src/components/ComparisonChart.vue
frontend/src/components/MetricCard.vue
frontend/src/components/PageHeading.vue
frontend/src/components/SourceNote.vue
frontend/src/components/SqlViewer.vue
frontend/src/data/demo.ts
frontend/src/data/experiments.ts
frontend/src/data/pairedCases.ts
frontend/src/data/workflow.ts
frontend/src/env.d.ts
frontend/src/layout/AppLayout.vue
frontend/src/main.ts
frontend/src/router/index.ts
frontend/src/stores/observatory.ts
frontend/src/styles/main.css
frontend/src/types/index.ts
frontend/src/views/AboutView.vue
frontend/src/views/AuditView.vue
frontend/src/views/DashboardView.vue
frontend/src/views/ExperimentsView.vue
frontend/src/views/PlaygroundView.vue
frontend/src/views/WorkflowView.vue
frontend/tests/data.test.ts
frontend/tests/security.test.ts
frontend/tests/setup.ts
frontend/tests/store.test.ts
frontend/tests/views.test.ts
frontend/tsconfig.json
frontend/vite.config.ts
```
