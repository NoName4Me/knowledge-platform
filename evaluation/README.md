# V1 固定质量与并发验收

本目录保存固定题集和执行工具。评测只能连接已配置真实 DeepSeek 生成模型与 Ollama `bge-m3-local` 的本地环境；开发替身结果不能作为质量证据。

## 固定合同

- 60 题，三个场景各 20 题；无依据题总体约占三分之一，并覆盖每个场景。
- `corpus/` 中 8 份公开资料摘录及来源说明必须全部上传。
- 单并发跑完整 60 题形成盲审材料；5 并发单独运行同一批题形成延迟结果。
- 人工评审按资料逐题判断正确性和引用支持。有依据答案正确率、引用支持率、无依据说明不足率均不低于 90%。
- 首个可见内容 p95 不超过 10 秒，完整回答 p95 不超过 45 秒；异常和超时计入结果。
- 评测只使用专用知识库和会话标题 `__kplatform_eval__`；结果确认后由管理员清理，不能把资料或历史当作正式团队材料。

## 环境变量

```bash
export KP_EVAL_EMAIL='demo@example.com'
export KP_EVAL_PASSWORD='...'
export KP_EVAL_TEAM_ID='...'
export KP_EVAL_GIT_COMMIT="$(git rev-parse HEAD)"
export KP_EVAL_GENERATE_MODEL='deepseek-flash'
export KP_EVAL_EMBEDDING_MODEL='bge-m3-local'
```

密码只通过环境变量传入，运行记录不保存凭据。使用开发者自己的本地 API 地址即可；默认
`http://127.0.0.1:5173` 通过 Vite 代理访问。

## 执行

```bash
python3 evaluation/quality_tool.py validate
python3 evaluation/quality_tool.py run --jobs 1 \
  --results evaluation/results/single/results.jsonl
python3 evaluation/quality_tool.py run --jobs 5 \
  --results evaluation/results/concurrent/results.jsonl
```

每次运行会创建独立知识库、上传 8 份资料，等待 worker 处理完成后创建本人会话并逐题调用
`/turns/stream`。成功结束后默认逻辑删除资料；排查时用 `--keep-resources` 保留。结果、
盲审文件和运行元数据保留在各自目录。

`review-blind.jsonl` 不含自动判定、模型答案和引用，只能给评审人提供问题与预期依据。
内部复核可参考 `results.jsonl`，但人工结论必须填写以下字段：

```json
{"human_correct": true, "human_citation_supported": true, "human_note": "依据摘录说明"}
```

无依据题只填 `human_correct`，不要给 `human_citation_supported` 伪造值。将完整带字段文件保存为
`review-reviewed.jsonl` 后汇总：

```bash
python3 evaluation/quality_tool.py score \
  --source evaluation/results/concurrent/results.jsonl \
  --reviewed evaluation/results/concurrent/review-reviewed.jsonl \
  --output evaluation/results/concurrent/review-score.json
```

结果记录必须同时保留：

- `run-metadata.json`：提交、模型、语料哈希、团队和知识库 ID。
- `results.jsonl`：每题流式耗时、终态、答案、引用及自动结构检查。
- `review-reviewed.jsonl` 与 `review-score.json`：人工质量结论。
- GitHub Actions 运行链接与提交 SHA：CI 证据。

最终记录写入 `docs/operations/`，不要把未完成的盲审文件描述为通过。
