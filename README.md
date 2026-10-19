# 智能文档与知识工作台

面向通用文档场景的团队知识工作台。V1 目标：上传文档、解析、检索、问答并核对原文依据，支持多团队数据与权限隔离。

当前进度：已完成登录与团队隔离的纵向闭环，并通过 Mac mini 本地部署验收。文档处理、检索与问答将在下一阶段接入。

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

## 未完成

- 文档上传、解析、检索、问答尚未实现。
- 数据库行级安全（RLS）待作为独立加固步骤加入。
- 每日异地备份与恢复演练暂缓。
