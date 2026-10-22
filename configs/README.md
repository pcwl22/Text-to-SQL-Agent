# 配置与数据边界

本目录保存固定实验参数、题目身份/难度、内容哈希和独立诊断配置，不包含 API Key、原始 BIRD 问答记录、Gold SQL 数据集或 SQLite 文件。

phase3c/repair_diagnostic_v1.json 含从本地 BIRD SQLite 提取的 Schema DDL 元数据，按上游来源保留 CC BY-SA 4.0 标注；手写诊断问题、错误 SQL、参考断言独立创建。详细署名、来源和许可边界见 [第三方说明](../docs/THIRD_PARTY_NOTICES.md)。语义断言只能在独立评估侧读取，不得送入 Repair Prompt。

配置中的 source_profiles 和 SHA256 固定实际实验所用实现，不能为了让不同版本通过检查而随意更新。release/evidence_v1.json 仅是公开的聚合指标与历史文件哈希索引，不分发对应的私有原始产物。
