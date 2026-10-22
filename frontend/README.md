# BIRD Agent Observatory · Vue 3

现有 Python 项目的独立前端，只显示审核后的聚合统计、逐题官方得分标记和明确标注的 DEMO。没有后端、模型请求或 SQL 执行功能。

## 本地运行（PowerShell 7）

```powershell
Set-Location frontend
npm install
npm run dev
# http://127.0.0.1:5173
```

Node.js 验证版本 24.11.0，npm 11.6.1。首次安装需要 npm registry 网络，安装与模型 API 无关。提交 package-lock.json，后续可用 npm ci 复现依赖。

```powershell
npm run build
npm run type-check
npm run lint
npm run test
npm run preview
# http://127.0.0.1:4173
```

## 页面

| 路由 | 内容 |
| --- | --- |
| `/` | A/B 官方 EX、Token、延迟 ECharts；独立 Repair 诊断 |
| `/playground` | 三个 DEMO 固定快照回放，包含结构错误示例 |
| `/workflow` | Vue Flow 四模式架构图、开关、节点契约和 State 白名单 |
| `/experiments` | A/B 指标、33 题配对表、1317 分级归因；C/D 未运行 |
| `/audit` | AST、只读执行、Gold 隔离、请求持久化生命周期 |
| `/about` | 技术栈、成果、来源、项目局限 |

## 目录与数据

`src/data/experiments.ts` 是公开 `docs/EXPERIMENT_RESULTS.md` 的静态聚合快照，记录源 commit。
`src/data/pairedCases.ts` 只包含 33 条 caseId / dbId / difficulty / a / b，经独立离线白名单导出，与固定 Manifest 对齐；不包含 SQL、题目、结果行或原始请求。
`src/data/demo.ts` 为手写界面示意，不是 BIRD 样本或真实模型输出。所有预设 SQL 和行仅用于展示，没有执行器。

`src/api/repository.ts` 将数据访问与页面解耦。`futureClient.ts` 预留 Axios 工厂，请求拦截器始终拒绝，页面不导入它。未来 Web API / Live Inference 必须另行设计、授权和验收。

Monaco 仅在点击“增强预览”后加载，包含本地 worker；只读。ECharts 按需注册模块，路由懒加载。深浅主题仅保存一个无敏感信息的 localStorage 值。

## 安全与部署

- 不创建 frontend/.env，不使用生产 API Key，不读取父目录 `.env`；Vite envDir 指向不存在的隔离目录。
- public 仅有本地 SVG favicon。无数据库、Gold 或原始日志。
- CSP 仅允许同源资源及本地开发 WebSocket；静态部署应通过 HTTP header 设置 `frame-ancestors 'none'`，该指令不能通过 meta 生效。
- 服务仅绑定 127.0.0.1。不要把开发服务器作为生产服务公开；生产只部署 dist。
- Vue Router 使用 history；静态托管需将未知路径回退到 index.html。GitHub 仓库不是已部署的网站。
- 图中 Validation 对 Baseline / Schema-only 是执行器内部安全层，只有 Repair 模式是显式 Graph 节点；关闭 Linking 时 Graph 内仍有 pass-through 节点。
- 没有可执行的真实模型命令。历史授权不延续到本前端。

设计及实际验收见根目录 `docs/FRONTEND_DESIGN.md`、`docs/FRONTEND_STATUS.md`。

## 发布审核说明

生产构建的 Monaco 核心约 2.73 MB（gzip 约 708 KB），属于点击后加载的独立模块；SQL 语法约 10.16 KB，通用编辑器 Worker 约 275.67 KB。构建模块审核确认没有全部语言入口或 TypeScript / JSON / HTML Worker。六个路由的直接访问和刷新均不加载编辑器，只有 Playground 回放后点击“增强预览”才加载。保留真实体积警告，未提高 Vite 阈值或用人为分包掩盖总量；未使用不稳定的内部 API 来裁剪核心。

代码、favicon 与 DEMO 为本项目实现；依赖由 package-lock.json 引用，未提交 node_modules、dist 或复制的第三方源码。BIRD 选择元数据沿用项目已有的 [来源与许可说明](../docs/THIRD_PARTY_NOTICES.md)，逐题得分标记来自已冻结的本项目独立评分，不包含 Gold。项目原创代码仍未选择 LICENSE，本次不新增许可证。

静态托管应配置 SPA history fallback；本次只验证本机 Vite 生产预览，不代表已部署公共站点。推送分支、创建 PR 与合并都需要各自的明确授权。
