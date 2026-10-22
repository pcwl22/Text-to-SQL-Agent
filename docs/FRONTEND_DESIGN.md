# Vue 3 前端设计与边界

前端为用户明确授权的独立开发，不代表 CODEX_PLAN 后续阶段获批。开发分支 feat/vue3-dashboard；Python 核心、历史实验与数据库不修改。开发验收后用户另行授权发布审核与本地提交，尚未授权推送、创建 PR 或合并。

## 架构

Vue 3 SFC / script setup / TypeScript strict → Vue Router 懒加载页面 → Pinia UI 状态 → Offline Repository → 白名单静态数据。

六页共享固定左导航、顶部离线状态、来源标记和深浅主题。手机用抽屉导航，表格在自身容器滚动；图表自适应。Element Plus 提供表单与结果表，Tailwind v4 提供基础工具类，主题和页面布局使用 CSS 变量。ECharts 展示独立坐标的 EX、Tokens、延迟，避免混合单位误导。Vue Flow 负责可点选架构图；Monaco 按需加载且不可编辑。Axios 工厂默认拒绝所有请求，没有在线实现。

## 数据契约

- 已核验统计：A 19/33、45,773 Tokens、8.201 秒；B 18/33、84,432 Tokens、45.007 秒。
- 独立诊断 8/8 为预设语义断言，不属于 BIRD 官方 EX。C/D 所有真实分数保持 null。
- 33 题配对表只含 caseId、dbId、difficulty、a、b 五字段，从本地冻结 official_ex/results.jsonl 只读提取，与 Manifest 逐条对账；不把原日志复制给浏览器。
- 来源指向公开 commit 92f019345f27fb90926fa8a7a3738acf1fe0ecbe 的 EXPERIMENT_RESULTS.md。
- Playground 使用独立 demo-* ID 的固定示意数据；不冒充 BIRD，不进行推理或 SQL 执行。改写问题后回放按钮禁用，重置恢复固定输入。错误案例只展示分类，不发起修复。

## Workflow 对应 Python

基于 bird_starter/schema_workflow.py、baseline.py 和 sql_repair.py。Baseline 是函数入口，不声称通过 LangGraph。Schema-only 图中 Validation 表示 execute_select 内部 AST 安全层，非新增节点；无 classify/repair 分支。Repair 模式为 inspect → linking（可 pass-through）→ generate → validate → execute → classify，结构错误且首次才 repair → validate。校验失败直接 classify，系统错误可提前结束。最多一次修复，不用 Gold/EX 驱动；执行成功不代表语义正确。

图中连线为架构路径高亮，不是实时执行 Trace。节点面板展示契约，State 为 DEMO，不包含 Gold。A/B 四模式中 C/D 架构可看，但标记尚未真实运行。

## 安全

不读取生产 dotenv；Vite 隔离 envDir，不使用 import.meta.env 注入认证。public 仅 favicon。无 HTTP 模型 URL、API Key、Gold、数据库、原始请求响应。CSP 限制连接同源与本地 HMR；生产 frame-ancestors 需 HTTP header。前端不是 SQL 安全执行边界，真正的 AST/只读保障仍在 Python；页面只展示这些契约。

Vitest 禁用 fetch / XHR，测试指标一致性、数据字段白名单、案例回放、改写输入拦截、定时器清理、路由、主题、Graph 契约、Axios 拒绝和泄露边界。Python 既有离线隔离继续生效；实际结果记录在 FRONTEND_STATUS.md。

## 工程取舍

没有增加后端或 Live Inference，不扩展历史实验，也不做正式 C/D。静态快照可在没有 BIRD/凭据的机器展示；不能声称浏览器复算了原始官方 EX。数据未来可替换 repository，但服务端授权、预算和安全校验不能委托给浏览器。

参考官方文档：[Vue TypeScript](https://vuejs.org/guide/typescript/overview)、[Tailwind Vite](https://tailwindcss.com/docs/installation/using-vite)、[Vue Flow](https://vueflow.dev/guide/)。依赖精确解析版本以 frontend/package-lock.json 为准。
