# 第三方来源、许可边界与发布范围

本文件记录来源与排除策略，**不为本项目原创代码选择开源许可证**。当前没有项目 LICENSE，许可方案留待仓库所有者决定；公开可见不表示原创代码已按某种开源许可授权。

## BIRD Mini-Dev

来源：[bird-bench/mini_dev 固定 commit](https://github.com/bird-bench/mini_dev/tree/abd11b6db92a1c9f809b32f7564c7c71b34d67f0) 与 [birdsql/bird_mini_dev 固定数据 revision](https://huggingface.co/datasets/birdsql/bird_mini_dev/tree/f65faf4ae3b638c1fa6df1d3370c8d92c8366301)。上游固定 README 明示 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)；本次没有发现单独的根 LICENSE 文件，因此不把 README 标识推断为任意文件的额外授权保证。

BIRD/Mini-Dev 作者及维护者包括 Jinyang Li、Binyuan Hui、Ge Qu、Xiaolong Li 等，完整贡献者与论文信息以固定上游 README 的 Acknowledgement/Citation 为准。论文：Jinyang Li et al., *Can LLM Already Serve as A Database Interface? A BIG Bench for Large-Scale Database Grounded Text-to-SQLs*, NeurIPS 36。

公开配置中的 case_id/db_id/difficulty/数据哈希是版本化评测元数据；configs/phase3c/repair_diagnostic_v1.json 的 Schema DDL 片段由用户已下载的 SQLite 元数据提取。对这部分来源于数据集的 Schema/选择元数据保留上游署名、来源、固定版本及 CC BY-SA 4.0 同许可说明，不将它归为本项目独有数据库设计；整理为 JSON 并用于独立诊断配置是本项目的格式变更。手写诊断问题、错误 SQL 和断言没有从 BIRD Gold 派生。

本提交不含原始题目记录、Gold 答案文件、CSV 描述、数据库行、SQLite 二进制或官方评测器源码。各数据库原始来源可能另有条件；使用者取得完整数据时仍须自行核对其来源许可。官方源码必须单独准备和验证，不能将缓存目录加入 Git；本次不再分发许可边界未独立确认的第三方源码。

## 运行依赖与工具

requirements.txt / requirements-eval.txt 仅声明依赖，不捆绑其源码、wheel 或二进制。各依赖许可证由其发行包及上游维护者提供；不以本项目许可覆盖它们。Gitleaks 仅用于本地发布前扫描，临时二进制和扫描日志位于 ignored outputs，不随仓库提交。

本文件是来源记录与保守分发边界，不代替对未来数据/代码再分发的许可核查。若后续新增 vendored 代码、数据或选择项目 LICENSE，需单独审核，不能从本次发布准备自动推定授权。
