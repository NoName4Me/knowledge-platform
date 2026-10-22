# Compose 发布验收清单

状态：当前完整版本待发布阶段执行。开发与功能验证使用本地 API、worker、Vite，本清单只用于发布前核验构建、迁移、持久化、权限及服务重启；不能替代真实模型质量验收。

## 配置前置与已知缺口

- 准备进入发布阶段后，使用项目根目录 `.env`；`backend/.env` 不会自动注入容器。已有配置直接编辑，首次可从 `.env.example` 复制并替换占位密钥与密码。
- **现有 `docker-compose.yml` 尚未向 API/worker 传入 S3、嵌入及问答模型的完整配置。** 仅编辑根 `.env` 不会使未映射变量自动生效；发布前需补 Compose 配置或经核对的覆盖文件，确认两服务使用一致配置。当前配置默认原件在 `doc-storage`，不能声称已使用 Garage 或真实问答模型。
- 容器访问宿主的 Garage/Ollama 需使用实际可达地址；容器内 `127.0.0.1` 指向容器自身。模型和存储凭据只通过部署配置注入，不能写入镜像、仓库或日志。
- Compose 当前 `KP_DB_USER` 同时用作数据库初始化与应用连接账号；发布前明确独立迁移账号和受限运行角色，确认 API/worker 已切换为非所有者、无 BYPASSRLS 的 `kp_app`，不能只凭容器健康宣布角色隔离通过。
- 当前允许来源列表固定在 Compose 中，本机默认端口可用；局域网或 HTTPS 发布需同步实际地址、允许来源及 Cookie Secure 设置。
- 选未占用的 `KP_HTTP_PORT`，使用独立验收账号、团队和公开资料，保留提交 SHA、迁移头版本、配置摘要（不含秘密）及各对象 ID。

## 构建与初始化

配置缺口解决后，在项目根目录执行：

```bash
docker compose up -d --build
docker compose exec api python -m alembic current
docker compose exec -T db sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' <<'SQL'
select rolname from pg_roles where rolname = 'kp_app';
SQL
```

确认迁移为当前唯一头版本，`kp_app` 存在，API 健康，Web 根页和经 Web 代理的 `/api/health` 正常。运行角色的实际限制还需后续测试验证。

另一个终端用交互命令初始化，按提示输入密码：

```bash
docker compose exec api python -m app.cli \
  --email release-admin@example.com --name 验收管理员 --team 发布验收团队 --platform-admin
```

已有邮箱会更新其名称和密码，勿使用正式账号进行重复验收。自动化场景可按 CLI 的 `--password-env` 选项从环境变量读取密码。

## 完整功能与重启

1. 登录后建立团队可见及私人知识库，邀请不同角色；核对可见范围与 `view/edit/manage`。平台身份不自动授予业务内容读取权。
2. 上传公开 TXT/Markdown 和一份实际需要的 PDF/Word 资料，等待 `ready`；检查存储后端、原件访问、解析和嵌入。若验收 DOC，容器需可用 LibreOffice。
3. 提问并核对流式正文、保存终态和原文引用；明确确认真实问答与嵌入配置生效。
4. 创建真实分析，核对完整状态、字段、结论、依据与 `provider/model`；保存笔记，生成并编辑报告，核对导出内容。
5. 创建自动成稿，记录要求、步骤、分析和报告关联；在执行中重启 worker，确认检查点恢复和幂等发布，关闭页面后重新进入。
6. 记录完成/部分成功成果及 ID 后执行 `docker compose restart api worker web`，健康恢复后确认资料、目录、会话、轮次、分析、笔记、报告、执行步骤、授权与人工编辑仍可读取且 ID 一致。
7. 执行 `docker compose up -d --force-recreate api`，确认 Web `/api/health` 仍为 200，Nginx 能在 API 地址变化后继续转发。
8. 验证成员/知识库撤权与账号/团队停用后读取、流式和后台发布均被阻止；私人库、非本人问答和成果不能越权访问。
9. 检查 `docker compose logs api worker web` 无迁移失败、重复成果、对象缺失、CSRF/RLS 异常或正文/凭据泄漏；记录成功和失败的实际证据。

## 判断标准

- 比较重启前后的 ID、内容与状态，不只凭页面存在或健康检查通过。已有失败/部分成功不得在重启后变成虚假成功。
- SSE 为 `text/event-stream`，含 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`；成功为首个 `turn`、若干 `delta`、终态 `turn`、`done`，失败有 `error`。断流需验证保存草稿可重新读取。
- 数据库使用命名卷 `db-data`；本地文件后端使用 `doc-storage`，S3 后端另核对 Garage 数据持久化。重建容器不得删除数据卷。持久卷不等于备份。
- 验证真实数据库运行角色和连接池复用，迁移账号与运行角色职责分开；仅确认角色名称存在不足以证明 RLS 生效。
- 对象、历史、成果或授权丢失，重复发布，越权或未授权发布均为失败。保留失败日志、版本和复现条件，不把历史 `0013` 阶段的验证当作当前完整版本通过。

质量状态见 [V1](V1-QUALITY-ACCEPTANCE.md)、[V2](V2-QUALITY-ACCEPTANCE.md) 与 [实施待办](../implementation/README.md)。每日异地备份、恢复演练和云预算继续暂缓，不作为当前门槛。
