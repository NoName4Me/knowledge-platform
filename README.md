# 智能文档与知识工作台

面向通用文档场景的团队知识工作台。V1 目标：上传文档、解析、检索、问答并核对原文依据，支持多团队数据与权限隔离。

当前进度：V1（身份隔离、可靠任务、上传与删除闭环、解析检索、本人持久会话、流式问答）与 V2（多文档信息提取、批量与文档对比、有依据结论）功能闭环已落地并通过自动化测试。V1 已接入本地 Ollama bge-m3、Garage S3 兼容存储与 DeepSeek flash，并完成固定 60 题质量评测与 5 并发延迟验收。V2 当前在部署中用确定性替身验证链路与权限，真实模型质量评测尚未执行。

## 目录结构

```
backend/   FastAPI 后端、数据库迁移、后台任务入口、测试
frontend/  React + TypeScript 前端
```

## 本地开发

后端（基础服务由本机 `~/jonge/infra` 统一提供）：

```bash
# 首次在 infra 目录启动并创建项目资源：
cd ~/jonge/infra
./scripts/init.sh
./scripts/create-project.sh kplatform --pg-prefix kp --rls

cd backend
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
cp ../.env.example .env  # 填数据库、模型与 S3 配置
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --port 8000
```

后台 worker（另开一个终端）：

```bash
cd backend
.venv/bin/python -m app.worker
```

基础服务由 OrbStack 统一托管，Ollama 保留本机应用：

```bash
cd ~/jonge/infra && docker compose up -d
```

PostgreSQL、MySQL、Redis 和 Garage 由本地基础设施 compose 管理；本项目和其他项目共享实例，
但各自使用独立数据库、账号与 Garage 桶。初始管理员可用
`backend/.venv/bin/python -m app.cli` 创建。真实配置写入 `backend/.env`，该文件不会提交。
如果 Ollama 下载 bge-m3 被本机代理的 fake-IP 拦住，可从
[`CompendiumLabs/bge-m3-gguf`](https://huggingface.co/CompendiumLabs/bge-m3-gguf) 下载
`bge-m3-f16.gguf`，以 `FROM <本地 gguf 路径>` 作为 Modelfile 执行
`ollama create bge-m3-local -f Modelfile`；该文件为 MIT 许可，已验证输出 1024 维向量。

前端：

```bash
cd frontend
npm install
npm run dev
```

前端开发服务器把 `/api` 代理到 `http://localhost:8000`。

## 检查与测试

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/mypy app
KP_TEST_DATABASE_URL="postgresql+psycopg://kp_migrator:Aa123456@127.0.0.1:5432/kplatform_test" \
KP_TEST_ADMIN_DATABASE_URL="postgresql+psycopg://postgres:Aa123456@127.0.0.1:5432/kplatform_test" \
KP_RLS_ROLE=kp_app KP_STORAGE_BACKEND=local .venv/bin/python -m pytest

cd ../frontend
npm run lint
npm run build
```

后端测试需要一个独立的 PostgreSQL 数据库，测试会重建该库的 schema，不要指向正在使用的库。

## 部署（Mac mini 模拟服务器）

```bash
cp .env.example .env   # 填写 KP_SECRET_KEY 与数据库密码
docker compose up -d --build
KP_BOOTSTRAP_PASSWORD='强密码' docker compose exec -e KP_BOOTSTRAP_PASSWORD api \
  python -m app.cli --email you@example.com --name 你的名字 --team 团队名 \
  --password-env KP_BOOTSTRAP_PASSWORD
```

访问 `http://<主机>:${KP_HTTP_PORT:-8080}`。数据保存在命名卷 `db-data`，容器重建后仍保留。

本地 HTTP 部署需保持 `KP_SESSION_COOKIE_SECURE` 为 false；将来启用 HTTPS 时再设为 true。

部署、数据持久化和服务重启的可重复验收步骤见 [Compose 验收记录](docs/operations/ACCEPTANCE-RUNBOOK.md)。

固定 60 题质量与并发结果见 [质量验收记录](docs/operations/V1-QUALITY-ACCEPTANCE.md)，
评测入口与盲审步骤见 [`evaluation/README.md`](evaluation/README.md)。

## 已实现的能力

- 上传 PDF、DOCX、TXT、Markdown，文件写入本地对象存储，处理任务入队。
- 后台 worker 使用租约令牌领取和续租，限制重试次数，并周期对账中断上传；删除资料会取消旧任务。
- 上传使用幂等键、团队额度锁与逻辑删除，删除或迟到写入不能让资料复活。
- 身份请求具有 CSRF 校验、成员与最后管理员保护、复合归属约束及 PostgreSQL RLS。
- 团队管理员可生成一次性成员邀请；新成员通过链接设置账号并加入，已有账号登录后接受；
  邀请绑定邮箱、默认 72 小时有效，重复使用、过期和非受邀邮箱均被拒绝。
- 按团队与知识库范围检索，回答带可核对的原文引用；资料不足时明确说明。
- 问答按知识库保留，仅创建者本人可见；轮次使用客户端请求标识避免重复提交，回答通过 SSE 增量呈现，断流后可恢复已保存的草稿或终态。
- 登录和问答接口具有请求限流；请求日志只记录请求标识、方法、路径、状态码与耗时，不记录正文、查询参数或凭据。
- 工作台使用“文档阅览台”视觉规范，可在本人历史会话间切换并刷新恢复。
- V2 分析在工作台“分析”模式中：单文档或多文档提取字段、按维度对比，结果与结论均附可核对原文依据，仅创建者本人可见。
- 分析使用独立后台任务路径，具有租约、续跑、并发限额与撤权/删除保护；批量失败不覆盖成功结果，重新运行保留旧记录。
- 分析任务记录 `provider`/`model`，界面明确区分替身与真实模型来源。

GitHub Actions 在每次推送及拉取请求执行后端静态检查、全量测试和前端 lint/构建。

## 未完成

- 每日异地备份与恢复演练暂缓。
- 重排尚未接入；当前基线为向量召回与中英文词项召回的混合检索。
- 固定题集的人工盲审尚未完成，自动结构检查结果不能替代人工结论。
- 本地开发已启用生成与嵌入模型的真实适配器；自动化测试仍使用确定性替身。
- V2 真实分析模型未配置，尚未执行真实质量评测；当前部署的分析结果来自确定性替身，只证明流程与权限，不代表提取或对比质量。详见 [V2 质量验收状态](docs/operations/V2-QUALITY-ACCEPTANCE.md)。

## 界面设计指导

界面修改以 [设计规范](design-system/k-platform/MASTER.md) 为准，页面差异见其 `pages/` 目录。颜色与字体等公共变量位于 `frontend/src/styles/tokens.css`。

## 后续实施

- V1：先读 [实施指导](docs/architecture/README.md)，再按 [任务清单](docs/implementation/V1-TASKS.md) 完成小切片。文档团队共享，问答历史仅本人可见；目标设计与已验证能力需分别记录。
- V2：功能闭环与替身验收已完成，真实模型质量验收待配置分析模型后执行。先读 [范围与验收基线](V2-SCOPE.md)、[分析实施指导](docs/architecture/V2-ANALYSIS.md) 与 [V2 任务清单](docs/implementation/V2-TASKS.md)；量化质量目标仍待确认。
