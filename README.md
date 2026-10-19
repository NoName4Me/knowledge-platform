# 智能文档与知识工作台

面向通用文档场景的团队知识工作台。V1 目标：上传文档、解析、检索、问答并核对原文依据，支持多团队数据与权限隔离。

当前进度：身份隔离、可靠任务、上传与删除闭环、解析检索和本人持久会话已落地并通过自动化测试，模型与存储仍使用开发替身。真实依赖质量、流式问答、固定题集和 Mac mini 重启验收尚未完成。

## 目录结构

```
backend/   FastAPI 后端、数据库迁移、后台任务入口、测试
frontend/  React + TypeScript 前端
```

## 本地开发

后端（需要一个可用的 PostgreSQL + pgvector）：

```bash
cd backend
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"
KP_DATABASE_URL="postgresql+psycopg://kp:kp@localhost:55432/kp_test" .venv/bin/python -m alembic upgrade head
KP_DATABASE_URL="postgresql+psycopg://kp:kp@localhost:55432/kp_test" .venv/bin/python -m uvicorn app.main:app --port 8000
```

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
KP_TEST_DATABASE_URL="postgresql+psycopg://kp:kp@localhost:55432/kp_test" .venv/bin/python -m pytest

cd ../frontend
npm run lint
npm run build
```

后端测试需要一个独立的 PostgreSQL 数据库，测试会重建该库的 schema，不要指向正在使用的库。

## 部署（Mac mini 模拟服务器）

```bash
cp .env.example .env   # 填写 KP_SECRET_KEY 与数据库密码
docker compose up -d --build
docker compose exec api python -m app.cli --email you@example.com --name 你的名字 --team 团队名 --password '强密码'
```

访问 `http://<主机>:${KP_HTTP_PORT:-8080}`。数据保存在命名卷 `db-data`，容器重建后仍保留。

本地 HTTP 部署需保持 `KP_SESSION_COOKIE_SECURE` 为 false；将来启用 HTTPS 时再设为 true。

## 已实现的能力

- 上传 PDF、DOCX、TXT、Markdown，文件写入本地对象存储，处理任务入队。
- 后台 worker 使用租约令牌领取和续租，限制重试次数，并周期对账中断上传；删除资料会取消旧任务。
- 上传使用幂等键、团队额度锁与逻辑删除，删除或迟到写入不能让资料复活。
- 身份请求具有 CSRF 校验、成员与最后管理员保护、复合归属约束及 PostgreSQL RLS。
- 按团队与知识库范围检索，回答带可核对的原文引用；资料不足时明确说明。
- 问答按知识库保留，仅创建者本人可见；轮次使用客户端请求标识避免重复提交，引用在查询时隐藏已删除资料的摘录。
- 工作台使用“文档阅览台”视觉规范，可在本人历史会话间切换并刷新恢复。

## 未完成

- 流式问答与断流续读尚未完成；当前问答接口为同步 JSON。频繁打断的长问题可依靠已落库的进行中状态等待续读接口。
- 每日异地备份与恢复演练暂缓。
- 中文分词、混合检索与重排尚未接入；当前为向量检索基线。
- 生成与嵌入模型的真实接入尚未配置，现用确定性替身运行。

## 界面设计指导

界面修改以 [设计规范](design-system/k-platform/MASTER.md) 为准，页面差异见其 `pages/` 目录。颜色与字体等公共变量位于 `frontend/src/styles/tokens.css`。

## 后续实施

先读 [V1 实施指导](docs/architecture/README.md)，再按 [任务清单](docs/implementation/V1-TASKS.md) 完成小切片。文档团队共享，问答历史仅本人可见；目标设计与已验证能力需分别记录。
