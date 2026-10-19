# Compose 部署与重启验收

本记录用于在 Mac mini 上核验“构建、迁移、持久化和服务重启”，不代替真实模型质量或公网验收。

## 前置条件

- Docker Compose 服务健康，已填写 `.env` 中的 `KP_SECRET_KEY`、数据库密码和未被占用的 `KP_HTTP_PORT`。
- `docker compose exec api python -m alembic current` 输出唯一头版本。
- `docker compose exec db psql -U "$KP_DB_USER" -d "$KP_DB_NAME" -Atc "select rolname from pg_roles where rolname = 'kp_app'"` 输出 `kp_app`。

## 验收步骤

1. 运行 `docker compose up -d --build`，等待 API 健康。
2. 访问 `/api/health`，应返回 `{"status":"ok"}`；访问 Web 根路径应返回页面。
3. 用 `python -m app.cli --password-env KP_BOOTSTRAP_PASSWORD` 创建验收管理员和独立团队，确认命令不再被运行时 RLS 角色拒绝。
4. 登录后创建知识库，上传一份 TXT 或 Markdown 资料，等待文档变为 `ready`。
5. 创建会话并提问，确认页面先显示“正在检索资料并生成回答”，随后增量出现正文；完成事件后重新读取轮次，确认状态为 `completed`、引用数量和引用摘录均非空。
6. 对同一个 `client_request_id` 再请求一次 `/turns/stream`，确认返回已有轮次且没有新增记录。生成中刷新页面，确认已生成草稿可读取；超过超时时间后应转为 `interrupted` 并允许重新提问。
7. 运行 `docker compose restart api worker web`，等待健康。
8. 重新登录，确认资料仍为 `ready`，会话、轮次和引用仍可读取。
9. 重建 API 容器（`docker compose up -d --force-recreate api`），确认 Web 的 `/api/health` 仍返回 `200`，Nginx 不会继续使用旧容器地址。
10. 检查 `docker compose logs api worker web` 无迁移失败、重复任务或 CSRF/RLS 异常。

## 判断标准

- 重启前后记录的资料 ID、会话 ID 与轮次 ID 必须一致；不能只凭页面“看起来还在”判定通过。
- SSE 响应必须包含 `text/event-stream`、`Cache-Control: no-cache` 和 `X-Accel-Buffering: no`；成功顺序为首个 `turn`、零到多个 `delta`、终态 `turn`，失败则发送 `error`。
- 断流测试必须验证服务端草稿已持久化；客户端重新进入会话时读取该轮次，不能从头伪造流式文本。
- 数据使用命名卷 `db-data` 和 `doc-storage`；仅重建容器不能删除卷。
- API/worker 每次取用数据库连接都会切换到受限运行角色；仅检查表所有者启动成功不足以证明 RLS 生效。
- Nginx 通过 Docker DNS 在请求时解析 API 服务名；只重启 API 也应保持 `/api` 可达。
- 出现迁移失败、对象缺失或会话被清空即验收失败。保留失败日志与当时版本号。

## 已知边界

- 当前存储适配器为本地目录，生产 S3 产品仍待确认。
- 当前生成与嵌入为开发替身；真实模型接入必须另跑固定题集。
- 每日异地备份与恢复演练暂缓，不计入当前门槛。
