# Compose 发布验收清单

状态：待发布阶段执行。当前开发和功能验收使用本地 API、worker、Vite 前台进程，
不启动项目应用容器。本清单只用于未来发布前在 Mac mini 核验“构建、迁移、持久化、
服务重启和发布产物”，不代替真实模型质量或公网验收。

## 前置条件

- 当前开发与功能验收已经完成，准备进入发布阶段。
- 项目根目录 `.env` 已填写 `KP_SECRET_KEY`、数据库密码和未被占用的 `KP_HTTP_PORT`。

## 验收步骤

1. 运行 `docker compose up -d --build`，等待 API 健康。
2. 执行 `docker compose exec api python -m alembic current`，确认输出唯一头版本。
3. 执行 `docker compose exec db psql -U "$KP_DB_USER" -d "$KP_DB_NAME" -Atc "select rolname from pg_roles where rolname = 'kp_app'"`，确认输出 `kp_app`。
4. 访问 `/api/health`，应返回 `{"status":"ok"}`；访问 Web 根路径应返回页面。
5. 用 `python -m app.cli --password-env KP_BOOTSTRAP_PASSWORD` 创建验收管理员和独立团队。
6. 登录后创建知识库，上传一份 TXT 或 Markdown 资料，等待文档变为 `ready`。
7. 创建会话并提问，核对流式正文、持久化终态和原文引用。
8. 创建分析任务，核对任务详情、结果、依据与 `provider`/`model`；确认不是替身来源。
9. 运行 `docker compose restart api worker web`，等待健康。
10. 重新登录，确认资料、会话、轮次、分析任务、结论和依据仍可读取。
11. 重建 API 容器（`docker compose up -d --force-recreate api`），确认 Web 的 `/api/health` 仍返回 `200`。
12. 检查 `docker compose logs api worker web` 无迁移失败、重复任务或 CSRF/RLS 异常。

## 判断标准

- 重启前后记录的资料 ID、会话 ID 与轮次 ID 必须一致；不能只凭页面“看起来还在”判定通过。
- SSE 响应必须包含 `text/event-stream`、`Cache-Control: no-cache` 和 `X-Accel-Buffering: no`；成功顺序为首个 `turn`、零到多个 `delta`、终态 `turn`，失败则发送 `error`。
- 断流测试必须验证服务端草稿已持久化；客户端重新进入会话时读取该轮次，不能从头伪造流式文本。
- 数据使用命名卷 `db-data` 和 `doc-storage`；仅重建容器不能删除卷。
- API/worker 每次取用数据库连接都会切换到受限运行角色；仅检查表所有者启动成功不足以证明 RLS 生效。
- Nginx 通过 Docker DNS 在请求时解析 API 服务名；只重启 API 也应保持 `/api` 可达。
- 出现迁移失败、对象缺失或会话被清空即验收失败。保留失败日志与当时版本号。

## 已知边界

- 当前本地运行已连接 Garage S3 与 Ollama 嵌入服务；发布阶段需重新核对 Compose
  使用的外部服务地址、凭据和健康状态。
- 问答与嵌入模型已完成 V1 题集与延迟验收；V2 真实分析模型仍需执行质量评测和人工盲审。
- 每日异地备份与恢复演练暂缓，不计入当前门槛。
