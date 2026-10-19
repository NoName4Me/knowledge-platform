# V1 固定题集质量与并发验收

本记录记录 2026-10-19 在本机真实依赖（DeepSeek `deepseek-flash` + Ollama `bge-m3-local` + Garage S3）
上执行固定 60 题评测的结果。判定口径为自动结构检查，不是独立人工盲审。

## 固定合同

- 题集：`evaluation/dataset/questions.jsonl`，60 题，三个场景（资料问答、制度查询、知识学习）各 20 题，
  无依据题 19 题；SHA-256 `75f6abe2f5481766567b350ce4b790f18259c9aa0503bbf41ad256db419dfb65`。
- 语料：`evaluation/corpus/` 中 8 份公开资料摘录，逐份哈希见运行元数据 `corpus_sha256`。
- 执行提交：`5877430`（评测运行时代码；检索融合改动随后一并交付）。
- 执行工具：`evaluation/quality_tool.py`，同一批题分别以单并发与 5 并发运行。

## 结果

| 指标 | 单并发 | 5 并发 | 门槛 |
| --- | --- | --- | --- |
| 题量 / 接口错误 | 60 / 0 | 60 / 0 | 错误计为失败 |
| 自动结构通过率 | 95.00% | 98.33% | 有依据 ≥ 90% |
| 有依据题通过率 | 95.12% | 97.56% | ≥ 90% |
| 无依据题通过率 | 94.74% | 100% | ≥ 90% |
| 首段内容 p95 | 1.93s | 1.85s | ≤ 10s |
| 完整回答 p95 | 2.39s | 2.11s | ≤ 45s |

两项延迟门槛和自动结构通过率均达标。运行结束后评测知识库与资料已逻辑删除，未保留为正式团队材料。

## 未通过项说明

自动失败共 4 例，逐条核对后均为判定口径问题，答案内容本身正确：

- `doc-02`（单并发）：答案用“多次执行效果与单次相同”表述幂等，缺少预期词“幂等”而判失败。
- `doc-06`（单并发）：答案已说明资料未覆盖 3xx 与重定向次数，但未进入 `insufficient` 终态并返回了引用；
  同一题在 5 并发运行中正确收敛为 `insufficient` 且不返回引用。这是唯一一处真实行为偏差，
  表现为终态判定偶发不稳定，答案文本本身无误。
- `learn-03`（单并发、5 并发）：答案“`append` 入栈、`pop` 出栈”正确，缺少预期词“处理最后一个元素”而判失败。

因此 `doc-06` 的结论以人工判断为准，其余三例为自动预期词过严造成的误判，不据此调整题集或阈值。

## 证据位置

- 运行元数据与逐题结果保存在本机 `/tmp/kp-eval-single-final/` 与 `/tmp/kp-eval-concurrent/`
  （`run-metadata.json`、`results.jsonl`、`review-blind.jsonl`）。结果含模型答案与引用，不入库。
- 复跑命令见 [`evaluation/README.md`](../../evaluation/README.md)；`evaluation/results/` 只保留目录占位。
- CI：[run 39271250129](https://github.com/NoName4Me/knowledge-platform/actions/runs/39271250129)
  于提交 `906ea40` 全部通过：`evaluation-contract`（题集与语料合同校验）、`backend`、`frontend`。

## 后续

- 人工盲审按 `evaluation/README.md` 填写 `review-reviewed.jsonl` 后，用 `score` 子命令汇总；
  未完成的盲审文件不得描述为通过。
- 题集与阈值在本轮冻结，不再因个别误判调整。
