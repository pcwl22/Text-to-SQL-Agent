# 数据准备与版本边界（公开版）

本仓库不分发 BIRD 题目、Gold SQL、数据库行、SQLite 文件或下载压缩包。测试夹具仅为 tests 中临时创建的合成数据库，不能用于报告 BIRD 成绩。

## 已验证的数据版本

- 经典 BIRD Mini-Dev SQLite：500 题、11 库，不与 780 题 V2 混用。
- 题目来源：[birdsql/bird_mini_dev](https://huggingface.co/datasets/birdsql/bird_mini_dev/tree/f65faf4ae3b638c1fa6df1d3370c8d92c8366301)，revision `f65faf4ae3b638c1fa6df1d3370c8d92c8366301`。
- JSON SHA256：`88ceb0710163cae46a256ecea8f0a8c98286599530b60587fda5c3cfe57d45d2`。
- 数据库下载入口：[官方 Mini-Dev 项目](https://github.com/bird-bench/mini_dev)。由使用者按上游许可另行准备，不自动下载。
- 固定开发清单 configs/phase3a/bird_dev33_v1.json：33 题、每库 3 题、每种难度各 11 题，SHA256 `7136f72aa9e0b78ccab8b29dfb077247a082e541604b4f8841fede2a63ba6dd8`。

```text
data/mini_dev_data/mini_dev_sqlite.json
data/minidev/MINIDEV/dev_databases/<db_id>/<db_id>.sqlite
```

使用 pathlib，优先级为 CLI 参数 > BIRD_CASES_PATH/BIRD_DB_ROOT > 旧默认路径。旧默认 DB 目录为 data/mini_dev_data/dev_databases，使用上述既有布局时应显式配置，不能因默认路径错误判断数据丢失。

```powershell
# 在仓库根目录运行；只读，不加载生产 .env，不调用模型。
$ProjectPython=Join-Path $PWD '.venv/Scripts/python.exe'
$env:PYTHON_DOTENV_DISABLED='1'
$env:BIRD_CASES_PATH=Join-Path $PWD 'data/mini_dev_data/mini_dev_sqlite.json'
$env:BIRD_DB_ROOT=Join-Path $PWD 'data/minidev/MINIDEV/dev_databases'
& $ProjectPython scripts/validate_bird_data.py --cases $env:BIRD_CASES_PATH `
  --db-root $env:BIRD_DB_ROOT --check-schema-compatibility
```

预期 500/500 题目匹配、11 库只读连接及完整性通过。Gold 的 EXPLAIN 编译只发生在独立数据校验侧，不进入 Agent。原始本地字节指纹可在固定配置中核对；数据库没有独立官方 checksum 佐证，不能把本地固定哈希称为上游签名。

| 数据库 | 表数 |
| --- | ---: |
| california_schools | 3 |
| card_games | 6 |
| codebase_community | 8 |
| debit_card_specializing | 5 |
| european_football_2 | 7 |
| financial | 8 |
| formula_1 | 13 |
| student_club | 8 |
| superhero | 10 |
| thrombosis_prediction | 3 |
| toxicology | 4 |

configs 只保留身份/难度/哈希及诊断 Schema 元数据；诊断问题、错误 SQL 和断言由项目独立编写，不是 benchmark Gold。来源及适用于派生 Schema 元数据的标注见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。本地完整状态日志包含机器信息，保留在本地而不作为公开仓库内容。
