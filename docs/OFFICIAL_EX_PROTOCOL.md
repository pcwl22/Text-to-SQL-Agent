# 官方 EX：公开复现协议

上游为 [bird-bench/mini_dev](https://github.com/bird-bench/mini_dev/tree/abd11b6db92a1c9f809b32f7564c7c71b34d67f0)，commit `abd11b6db92a1c9f809b32f7564c7c71b34d67f0`。本仓库只发布自行实现的适配层与配置，不包含上游源码副本。使用者按上游许可另行将固定文件准备到 configs/official_ex/upstream.json 指定的 ignored 缓存目录。

| 上游文件 | 固定 SHA256 |
| --- | --- |
| evaluation/evaluation_ex.py | da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89 |
| evaluation/evaluation_utils.py | f6943d249caac5aeaef9bce21d43dbf29dcef85a0c965a76df032a9542f308bf |

独立 exporter 检查数据/Manifest/来源结果哈希、审计关联、case_id/db_id、顺序及长度。输出 predictions.json、evaluator-only gold.sql、difficulty.jsonl、bundle.json，全部在 ignored outputs。不能以 zip 静默截断；Gold 不进入任何 Agent 节点。

协议 `complete-agent-results-only-failed-zero-v1` 固定分母 33。失败、缺失、超时、未知完整性、危险 SQL 或结果截断，导出独立评估占位符并计零；不重新请求模型。Agent 执行限额 8 秒/1000 行，官方评分 SQL 30 秒、worker 硬限时 40 秒、外层默认 1500 秒。它是包含 Agent 完整性条件的子集评分，不能描述为无资源限制的原 SQL 重跑。

Windows 适配在独立 CLI 子进程中运行，使用 spawn、freeze_support、顺序单 worker 与 Job Object；只允许临时私有副本的 SQLite 连接，mode=ro/query_only/authorizer/扩展禁用，禁止外部数据库与网络。兼容包装替代不适用的超时边界和连接方式，不改官方结果行集合比较逻辑；项目 Proxy 的多重集比较另行记录。正常回收 worker 后清理本次副本，原库和副本哈希需保持一致；硬超时后允许保留私有证据，不无限重启。

已验证 Python 3.13.5 / Windows。requirements-eval.txt 独立于生产依赖。评测器 Gold/无效/缺失控制分别 33/33、32/33、32/33，只证明评测通路，不是模型成绩；A/B 真实成绩来自独立评分，见 [EXPERIMENT_RESULTS.md](EXPERIMENT_RESULTS.md)。完整离线命令见 [REPRODUCIBILITY_GUIDE.md](REPRODUCIBILITY_GUIDE.md)。
