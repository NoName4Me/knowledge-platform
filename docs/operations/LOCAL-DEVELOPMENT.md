# 本地开发与测试

当前开发与功能验收的唯一预期应用运行方式是本地前台进程：API、worker、前端分别在
自己的终端运行，日志直接显示在对应终端。PostgreSQL、Garage、Ollama 等基础服务由
`~/jonge/infra` 统一管理。
项目根目录的 Docker Compose 已退出当前日常运行流程，仅保留给未来发布阶段验证构建、
容器部署、数据持久化和服务重启；现阶段不要求启动项目应用容器。
以下路径按当前本机目录编写；更换仓库位置时相应替换。

### 配置文件：本地用 backend/.env

| 运行方式 | 配置文件 | 页面地址 |
| --- | --- | --- |
| 当前本地 API + worker + Vite | `backend/.env` | `http://localhost:5173` |
| 未来发布阶段 Compose 验收 | 项目根目录 `.env` | `http://localhost:8080`（默认） |

本地后端从**启动时的工作目录**读取 `.env`，所以 API、worker、迁移与管理员命令
都必须先进入 `backend/`。未来发布阶段若使用项目 Compose，它从项目根目录 `.env`
取值并按 Compose 中的环境映射注入容器，不会自动读取 `backend/.env`。两份文件互不自动同步。

已有 `backend/.env` 时直接编辑，不要覆盖。首次配置可执行：

```bash
cd ~/jonge/learn/k-platform/backend
cp -n ../.env.example .env
```

在 `backend/.env` 中填写本地连接配置（下面的占位内容需要替换）：

```dotenv
KP_ENVIRONMENT=development
KP_DATABASE_URL=postgresql+psycopg://kp_migrator:数据库密码@127.0.0.1:5432/kplatform
KP_RLS_ROLE=kp_app
KP_SECRET_KEY=固定的随机密钥
KP_SESSION_COOKIE_SECURE=false
KP_ALLOWED_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]

KP_STORAGE_BACKEND=s3
KP_S3_ENDPOINT_URL=http://127.0.0.1:3900
KP_S3_BUCKET=kplatform
KP_S3_ACCESS_KEY=Garage访问密钥
KP_S3_SECRET_KEY=Garage秘密密钥
KP_S3_REGION=garage

KP_EMBEDDING_BASE_URL=http://127.0.0.1:11434/v1
KP_EMBEDDING_MODEL=bge-m3-local
KP_ANSWER_BASE_URL=https://api.deepseek.com
KP_ANSWER_MODEL=deepseek-flash
KP_ANSWER_API_KEY=问答模型密钥

KP_ANALYSIS_MODEL_BASE_URL=https://api.deepseek.com
KP_ANALYSIS_MODEL=deepseek-flash
KP_ANALYSIS_MODEL_API_KEY=分析模型密钥
KP_ANALYSIS_MODEL_TIMEOUT_SECONDS=90
```

`KP_DATABASE_URL` 是本地数据库连接，根目录中的 `KP_DB_*` 不能替代它。
分析模型的 URL 与密钥都配置后才会使用真实模型；未配齐时使用固定规则替身。
问答与分析可使用同一供应商密钥，但各自的变量都要填写。
这两份 `.env` 均被 Git 忽略。

### 首次准备（已有环境可跳过）

在基础设施目录初始化项目数据库、受限角色与存储资源：

```bash
cd ~/jonge/infra
./scripts/init.sh
./scripts/create-project.sh kplatform --pg-prefix kp --rls
```

安装应用依赖：

```bash
cd ~/jonge/learn/k-platform/backend
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"

cd ~/jonge/learn/k-platform/frontend
npm install
```

### 日常启动

先确认 OrbStack 已运行，通过 `local-infra` 统一启动基础服务与本机 Ollama：

```bash
cd ~/jonge/infra
./scripts/init.sh
```

初始化会检查固定的 `bge-m3-local` 模型版本，并验证真实嵌入接口返回 1024 维向量。
模型首次准备与独立检查见 `~/jonge/infra/README.md` 的“Ollama 与嵌入模型”一节。
上传解析与检索需要嵌入服务可用。

**终端一：API。** 更新代码后先升级数据库，再启动服务：

```bash
cd ~/jonge/learn/k-platform/backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**终端二：worker。** 负责文档解析、信息提取与对比，必须与 API 使用同一份本地配置：

```bash
cd ~/jonge/learn/k-platform/backend
.venv/bin/python -u -m app.worker
```

**终端三：前端。** 固定端口，避免端口占用时自动切换而与允许来源配置不一致：

```bash
cd ~/jonge/learn/k-platform/frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

访问 <http://localhost:5173>。前端通过 Vite 将 `/api` 代理到 `http://localhost:8000`，
无需另配 API 地址。三个终端保持打开，即可分别看到 API、后台任务、前端开发服务器日志；
页面 JavaScript 错误在浏览器开发者工具 Console 中查看。

新数据库首次使用时，在另一个终端创建管理员，按提示输入密码；已有账号无需重复创建：

```bash
cd ~/jonge/learn/k-platform/backend
.venv/bin/python -m app.cli --email you@example.com --name 你的名字 --team 本地团队
```

### 如何重启和停止

在要重启的进程所在终端按 **Ctrl+C**，等待退出，然后重新执行该终端的启动命令。

| 改动内容 | 需要操作 |
| --- | --- |
| `backend/.env`（包括模型地址、密钥） | API 和 worker 都按 Ctrl+C 后重新启动 |
| 后端 Python 代码 | API 的 `--reload` 自动加载；worker 手动重启 |
| 前端页面或样式 | Vite 自动更新；必要时刷新浏览器 |
| 前端依赖或启动配置 | 停止 Vite；依赖变化先 `npm install`，再启动 |
| 数据库迁移代码 | 停止 API/worker，执行 `alembic upgrade head`，再启动两者 |

关闭本地应用时，在三个终端各按一次 Ctrl+C。更换模型配置后需要新建分析任务，
已保存的历史结果不会自动重新计算。

如果旧服务由其他终端或工具启动，先查占用端口的进程：

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof -nP -iTCP:5173 -sTCP:LISTEN
```

确认是本项目进程后，执行 `kill <PID>`（替换为上一步的实际 PID），再在自己的终端启动。
本地 worker 没有监听端口，可用 `pgrep -fl 'python.*-m app.worker'` 查找；存在多个项目时，
先确认进程归属再停止。

### 快速检查与常见问题

```bash
curl -f http://localhost:8000/api/health
curl -f http://localhost:5173/api/health
```

两者都应返回 `{"status":"ok"}`；第二条同时检查前端代理。

- 上传或分析一直排队：检查本地 worker 终端是否运行，以及该终端是否报错。
- 端口已占用：按上述方法停止本项目旧进程，再启动；不要重复开启多套同端口服务。
- 修改密钥后仍显示替身：确认修改的是 `backend/.env`，重启 API 与 worker，再创建新任务。
- 基础服务不可用：先检查 `~/jonge/infra` 是否已启动 PostgreSQL、Garage 与 Ollama；
  当前项目应用本身不需要启动 Docker 容器。

## 检查与测试

从仓库根目录执行；测试库密码占位文字须先替换：

```bash
cd backend
.venv/bin/ruff check .
.venv/bin/mypy app
.venv/bin/python -m app.typegen --check
KP_TEST_DATABASE_URL="postgresql+psycopg://kp_migrator:测试库迁移密码@127.0.0.1:5432/kplatform_test" \
KP_TEST_ADMIN_DATABASE_URL="postgresql+psycopg://postgres:测试库管理密码@127.0.0.1:5432/kplatform_test" \
KP_RLS_ROLE=kp_app KP_STORAGE_BACKEND=local .venv/bin/python -m pytest

cd ../frontend
npm run lint
npm test
npm run build

cd ..
backend/.venv/bin/python evaluation/quality_tool.py validate
backend/.venv/bin/python evaluation/v2/validate_cases.py
```

后端测试需要独立的 PostgreSQL 测试库；上例密码是占位文字，运行前替换。测试会删除并重建该库的 public schema，必须同时检查两条测试 URL 都指向测试库。角色初始化方式见 `.github/workflows/ci.yml`；常规测试使用模型替身。

## 平台管理员初始化与管理

首个平台管理员使用显式初始化命令，不会由团队角色自动升级。可只建平台账号，不指定团队：

```bash
cd ~/jonge/learn/k-platform/backend
.venv/bin/python -m app.cli --email admin@example.com --name 平台管理员 --platform-admin
```

同时传 `--team 团队名` 可建立团队管理员成员关系。CLI 按邮箱更新已有账号，会重设显示名称和密码；它只用于初始化，日常管理使用页面入口。平台管理员可创建团队、指定初始管理员，以及停用或恢复团队与账号。

团队管理员从团队设置维护信息、成员、邀请和角色。平台管理员可重置任意账号密码，团队管理员可重置本团队成员密码，临时密码固定为 `user1234`；提交后旧会话全部失效，下次登录必须改密。密码属于全局账号，影响其全部团队。退出、移除、降级和停用必须保留至少一名可登录团队管理员。

## 迁移与故障定位

API 与 worker 启动前均执行 `alembic upgrade head`。知识库授权依赖迁移 `0026_kb_access`（文件 `0026_knowledge_base_access.py`）；缺少 `context_user_id` 或 `app_revoke_inaccessible_knowledge_work()` 时，先停止 API/worker，升级后再启动，不能绕过 worker 预检。头版本以仓库迁移文件为准。

开发库配置与测试库配置分开。模型地址或密钥修改后重启 API 和 worker，再新建任务，通过结果中的 `provider` / `model` 确认；历史替身结果不会自动更新。旧版 `.doc` 解析还需要本机可用的 LibreOffice `soffice`（或配置 `KP_LEGACY_DOC_CONVERTER`）；缺少转换器会明确失败。

发布步骤见 [发布验收清单](ACCEPTANCE-RUNBOOK.md)，真实质量评测见 [V1 评测](../../evaluation/README.md) 与 [V2 评测](../../evaluation/v2/README.md)。
