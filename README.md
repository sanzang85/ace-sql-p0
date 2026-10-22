# ACE-SQL P0

面向工业生产质量数据的自然语言到 SQL 查询系统。主要流程为：模式候选构建、数据库名称值探测、正向证据补表、查询计划与 SQL 生成、语义复核、静态校验、预执行及有限反馈重试。

生成链路使用四份私有知识文件：`qs.json`、`qe.json`、`siames信息.md`、`数据表信息.md`。不启用模板库或模板生命周期演化。历史问答参与检索；生成完成后才进行标准答案评分。

## 安装与配置

需要 Python 3.10+、兼容 OpenAI API 的对话和向量服务、可访问的 MySQL 数据库。

```bash
./setup.sh
cp .env.example .env
```

填写 `.env` 中的数据库参数，建议使用只读账户。数据库密码必须显式配置，没有内置密码。

修改 `portable_template_llm_module/data/config/model_profiles.json` 中的模型地址和模型名。默认示例为 localhost 上 8002 端口的 Qwen3.8-27B 和 8000 端口的 Qwen3-Embedding-8B；profile 显式设置优先于同名环境变量。

## 私有数据

本仓库不提交业务知识原文、题库、标准答案、历史结果或运行日志。克隆后请自行准备以下文件，知识目录中必须恰好为四份文件：

```text
portable_template_llm_module/data/
├── knowledge/
│   ├── qs.json
│   ├── qe.json
│   ├── siames信息.md
│   └── 数据表信息.md
├── inputs/q_0623.csv
└── ground_truth/a_0623.xlsx
```

最后两项用于评测；标准答案不作为生成知识。私有输入目录均已加入 `.gitignore`。本机发布副本保留被忽略的原始输入以方便本地验证，GitHub 克隆不会附带这些文件。

## 运行

```bash
# 准备好上述私有文件后检查依赖及输入，不访问模型或数据库
./check_environment.sh
./run_tests.sh

# 模型列表及 SELECT 1 连通性检查
./check_environment.sh --online

# 单题
./run_query.sh '统计各生产线本月的质量检测记录数量。'

# 批量评测，默认 4 题并发
RUN_DIR=outputs/p0_run1 ./run_benchmark.sh

# 外部新题，先检查题目与答案映射
INPUT_FILE=/path/questions.csv GT_FILE=/path/answers.xlsx ./run_new_questions.sh --check-only
INPUT_FILE=/path/questions.csv GT_FILE=/path/answers.xlsx RUN_DIR=outputs/new_run1 ./run_new_questions.sh

# 汇总已有结果
./summarize.sh outputs/p0_run1
```

启动器打印绝对输出目录。批量结果在 `analysis/问答结果汇总.csv`、同名 Excel、`analysis/实验报告.md`；实时日志在 `launcher.log` 及每题 `questions/qNNN/console.log`，节点输入输出位于 `trace/`。输入、配置或代码变化后请使用新的输出目录。

## 代码定位

- `portable_template_llm_module/nl2sql_template_core/workflow.py`：工作流、错误分流和重试。
- `domain.py`、`schema_catalog.py`：真实 DDL 目录、候选表选择与反馈合并。
- `query_evidence.py`：名称值探测、正向证据补表、结构化证据与语义复核。
- `planning.py`、`generation.py`：计划、SQL 生成与修复。
- `validation.py`、`execution.py`、`readonly_db.py`：校验、预执行和只读访问。
- `knowledge_boundary.py`：生成期文件访问边界。
- `evaluation.py`：工作流结束后的结果评分。
- `scripts/`：评测调度、导出、汇总及审计。

上述简写文件名均位于核心模块目录。通过内部校验不等同于答案语义正确，实际准确率以每轮评测输出为准。

## 发布副本说明

本副本从 `ace_sql_p0_clean` 提取，不修改原运行版。仅调整部署默认值、移除内置数据库密码和机器专用路径、重写发布说明并忽略私有数据；模型生成、候选更新、复核、校验和评分算法保持原实现。精确差异及文件哈希见 `SOURCE_MANIFEST.json`。本仓库不附带专利申请材料。

推送目标为 GitHub 账号 `sanzang85` 下的私有仓库 `ace-sql-p0`。完成 GitHub CLI 授权后，运行 `./push_to_github.sh`；脚本会核对登录账号，遇到已有远端仓库则停止，避免改写已有内容。
