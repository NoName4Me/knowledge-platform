# 智能文档与知识工作台

面向通用文档场景的团队知识工作台。V1 目标：上传文档、解析、检索、问答并核对原文依据，支持多团队数据与权限隔离。

当前进度：V1（身份隔离、可靠任务、上传与删除闭环、解析检索、本人持久会话、流式问答）与 V2（多文档信息提取、批量与文档对比、有依据结论）功能闭环已落地并通过自动化测试。V1 已接入本地 Ollama bge-m3、Garage S3 兼容存储与 DeepSeek flash，并完成固定 60 题质量评测与 5 并发延迟验收。V2 当前在部署中用确定性替身验证链路与权限，真实模型质量评测尚未执行。

## 目录结构

```
backend/   FastAPI 后端、数据库迁移、后台任务入口、测试
frontend/  React + TypeScript 前端
```

## 本地启动与重启

本地开发时，API、worker、前端分别在自己的终端前台运行，日志直接显示在对应终端。
PostgreSQL、Garage 等基础服务继续由 `~/jonge/infra` 的 Docker Compose 提供。
以下路径按当前本机目录编写；更换仓库位置时相应替换。

### 配置文件：本地用 backend/.env

| 启动方式 | 配置文件 | 页面地址 |
| --- | --- | --- |
| 本地 API + worker + Vite | `backend/.env` | `http://localhost:5173` |
| 项目 Docker Compose 部署 | 项目根目录 `.env` | `http://localhost:8080`（默认） |

本地后端从**启动时的工作目录**读取 `.env`，所以 API、worker、迁移与管理员命令
都必须先进入 `backend/`。Compose 从项目根目录 `.env` 取值，并将其配置中声明的变量
注入容器；不会自动读取 `backend/.env`。两份文件互不自动同步，进程环境变量优先于文件。

已有 `backend/.env` 时直接编辑，不要覆盖。首次配置可执行：

```bash
cd /Users/liuhongmei/jonge/learn/k-platform/backend
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
cd /Users/liuhongmei/jonge/learn/k-platform/backend
uv venv --python 3.12 .venv
uv pip install -e ".[dev]"

cd /Users/liuhongmei/jonge/learn/k-platform/frontend
npm install
```

### 日常启动

先确认 OrbStack 已运行，通过 `local-infra` 统一启动基础服务与本机 Ollama：

```bash
cd ~/jonge/infra
./scripts/init.sh
```

初始化会检查固定的 `bge-m3-local` 模型版本，并验证真实嵌入接口返回 1024 维向量。
模型首次准备与独立检查见 [基础环境说明](../../infra/README.md#ollama-与嵌入模型)。
上传解析与检索需要嵌入服务可用。

**终端一：API。** 更新代码后先升级数据库，再启动服务：

```bash
cd /Users/liuhongmei/jonge/learn/k-platform/backend
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

**终端二：worker。** 负责文档解析、信息提取与对比，必须与 API 使用同一份本地配置：

```bash
cd /Users/liuhongmei/jonge/learn/k-platform/backend
.venv/bin/python -u -m app.worker
```

**终端三：前端。** 固定端口，避免端口占用时自动切换而与允许来源配置不一致：

```bash
cd /Users/liuhongmei/jonge/learn/k-platform/frontend
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

访问 <http://localhost:5173>。前端通过 Vite 将 `/api` 代理到 `http://localhost:8000`，
无需另配 API 地址。三个终端保持打开，即可分别看到 API、后台任务、前端开发服务器日志；
页面 JavaScript 错误在浏览器开发者工具 Console 中查看。

新数据库首次使用时，在另一个终端创建管理员，按提示输入密码；已有账号无需重复创建：

```bash
cd /Users/liuhongmei/jonge/learn/k-platform/backend
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
- 本地与 Docker 页面账号或资料不同：比较两边数据库和存储配置。当前本机本地连接
  infra 的 `kplatform` 库与 Garage；项目 Compose 使用自己的 `kplatform_dev` 库与存储卷，
  数据不会自动同步。新环境以实际 `.env` 配置为准。

如暂时只用本地应用，可在项目目录停止 Docker 版应用，保留其数据库与数据卷：

```bash
cd /Users/liuhongmei/jonge/learn/k-platform
docker compose stop api worker web
```

Docker 版日志可用 `docker compose logs -f api worker web` 查看。
修改根目录 `.env` 后，Docker 版需执行 `docker compose up -d --force-recreate api worker`
重新创建容器以加载配置；单纯 `docker compose restart` 不更新容器环境变量。

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
npm test
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
- 首页提供知识库和团队入口；进入知识库后使用左侧资料树、右侧问答/分析视图，顶部切换知识库和个人菜单。
- 知识库地址支持直接访问、刷新和浏览器返回；工作模式按账号与知识库保存，本人历史会话持久化。
- 正文引用使用可点击上标，原文默认收起在右侧抽屉；悬浮或聚焦引用时高亮回答对应句段。
- 资料目录支持多级文件夹、树内新建/改名/删除，并可将目录或资料拖动到其他目录或根级。
- 管理员通过顶部“管理知识库”改名或删除知识库；删除非空知识库会连同资料、目录、问答记录和分析结果一并清理。
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
