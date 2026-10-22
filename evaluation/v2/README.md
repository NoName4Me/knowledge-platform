# V2 固定样例与预算

本目录保存 V2 分析用的固定样例、预期结果与校验入口。样例是公开资料的短摘录，
用于判断实现正确性，不是产品内置知识，也不作为正式质量验收结论。

## 目录

```
corpus/             四种支持格式的样例文档（txt / md / docx / pdf）
dataset/cases.json  固定样例、字段定义与预期结果
build_corpus.py     重新生成 corpus/（需要后端依赖 reportlab）
validate_cases.py   静态校验样例自洽性
evaluate.py         通过运行中的部署执行样例并记录自动检查结果
results/            历次运行结果（每次写入独立子目录）
```

`corpus/` 的生成结果已提交到仓库，校验脚本直接读取；只有需要改语料时才运行
`build_corpus.py`。PDF 使用内置中文字体 STSong-Light，不嵌入字体文件。

## 校验

```bash
backend/.venv/bin/python evaluation/v2/validate_cases.py
```

校验内容：声明的语料文件都存在且能被真实解析器处理；每个样例的 `anchor` 都能在
对应文档的已发布片段中定位；`anchor_absent` 确实不出现；字段名不重复、预期字段
都有定义、对比样例至少两份文档。校验不调用模型，也不连接数据库。

## 执行评测

先准备独立评测账号和目标团队，确认本地 API、worker、Vite 与真实模型配置，再执行（显式传入 5173 以覆盖脚本的历史默认端口 8080）：

```bash
KP_EVAL_EMAIL='...' KP_EVAL_PASSWORD='...' KP_EVAL_TEAM_ID='...' \
  backend/.venv/bin/python evaluation/v2/evaluate.py run \
  --base-url http://127.0.0.1:5173 \
  --results evaluation/v2/results/current/results.json
```

脚本会新建一个临时知识库、上传固定语料、逐个提交样例、等待终态，并校验字段状态、
取值、日期精度、原文依据与对比结论。结果文件记录运行时间、团队/知识库 ID、语料哈希、
每条样例的耗时与失败原因。默认结束后逻辑删除上传的资料。

常用选项：

- `--case <id>`：只跑指定样例，可重复；用于排查单条失败。
- `--keep-resources`：保留评测资料，便于在界面复核。
- `--allow-stub`：仅当明确使用确定性替身做链路检查时加。

## 替身与质量证据

只要结果中出现 `provider=stub`，脚本默认以“本次结果由确定性替身生成，不能作为真实
模型质量证据”失败退出。显式加 `--allow-stub` 才能跑完，结果摘要固定写入
`stub_only: true`、`quality_evidence: false`。

替身只用于验证接口、状态机、权限、依据结构与页面展示。2026-10-20 用
`--allow-stub` 执行 6 个样例时自动检查全部失败，失败原因均为替身按固定规则命中了
错误数值或错误状态（例如把 `9110` 识别成年份），符合“替身不能判断质量”的预期。
真实模型评测需配置 `KP_ANALYSIS_MODEL_BASE_URL` 与 `KP_ANALYSIS_MODEL_API_KEY`，
且不得加 `--allow-stub`。

## 样例覆盖

| 样例 | 覆盖点 |
| --- | --- |
| http-methods-idempotent | 文本列表、同一句重复出现去重、文档内指令不得改变任务 |
| http-status-and-retry-after | 依据位于文末片段、未提及判定 |
| wcag-thresholds-and-precision | 数字与单位、同一字段的条件差异、完整日期精度 |
| css-grid-page2-and-ambiguity | 跨页片段、明确歧义需标记待核实 |
| python-list-operations | DOCX 段落定位、方法名列表 |
| comparison-published-date | 月份精度不得补全、未提及不得构成冲突、差异与待核实结论 |

## 预期结果的口径

- `status` 是期望的字段状态：`extracted`、`not_mentioned`、`needs_review`、`failed`。
  `status_any` 用于同一字段允许两种正确结果（例如同一字段对应不同条件的多个取值）。
- `expected_value` 是期望取值；数字不带单位，日期用 ISO 形式。
- `expected_precision` 是日期精度，必须是 `year`、`month`、`day` 之一；缺省补全视为错误。
- `anchor` / `anchors` 是期望依据的原文片段；`anchor_absent` 是确认不存在的文本。
- `note` 记录该预期的判定理由与常见错误。

这些预期由人工按公开原文标注。正式评测需按 [V2 范围](../../V2-SCOPE.md) 确认规模并扩充，当前短样例不能作为总体质量百分比。

## 预算初值

分析限制在 `backend/app/config.py` 中以 `KP_ANALYSIS_*` 配置，工程初值与理由（当前值以配置文件为准）：

| 配置 | 初值 | 理由 |
| --- | --- | --- |
| `analysis_max_documents` | 10 | 覆盖日常批量核对，单任务可一次读完；再大应分批提交 |
| `analysis_max_fields` | 20 | 单张信息表的常见上限；超过后表格难以核对 |
| `analysis_max_document_chars` | 1,000,000 | 现有解析上限的一半，留出批次切分余量 |
| `analysis_max_total_chars` | 3,000,000 | 约三份满额文档，控制单任务排队时间 |
| `analysis_batch_chars` | 12,000 | 单次模型调用的保守输入量，长片段按此切分 |
| `analysis_max_calls_per_document` | 40 | 12,000 字符/批 × 40 ≈ 48 万字符，覆盖多数文档 |
| `analysis_max_output_chars` | 50,000 | 单次结构化输出上限，超出即失败不截断 |
| `analysis_max_value_chars` | 2,000 | 单个取值/摘录上限，防止整段原文被当作字段值 |
| `analysis_max_list_items` | 50 | 平面文本列表上限 |
| `analysis_max_citations_per_field` | 20 | 单字段依据条数上限 |
| `analysis_timeout_seconds` | 600 | 单文档执行墙钟上限 |
| `analysis_max_attempts` | 2 | 仅对暂时故障自动重试一次 |
| `analysis_user_max_active_tasks` | 2 | 单用户同时排队/执行的分析任务数 |
| `analysis_team_max_active_tasks` | 10 | 单团队同时活跃的分析任务数 |
| `analysis_lease_seconds` | 180 | 分析执行租约，长于单次模型调用超时 |

这些是工程参数，尚无实测容量数据；后续依据真实语料调整。超限一律显式失败，
不截断后继续，也不在结果里把未处理部分当作“未提及”。
