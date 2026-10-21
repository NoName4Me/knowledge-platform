# 智能文档与知识工作台

面向通用文档场景的团队知识工作台。V1 目标：上传文档、解析、检索、问答并核对原文依据，支持多团队数据与权限隔离。

当前进度：V1（身份隔离、可靠任务、上传与删除闭环、解析检索、本人持久会话、流式问答）、V2（多文档信息提取、批量与文档对比、有依据结论）及 V3（连续追问、资料范围、知识笔记、报告生成与审阅、资料导入与换版复核、DOCX/PDF/XLSX/CSV 导出）主流程已落地。V1 已接入本地 Ollama bge-m3、Garage S3 兼容存储与 DeepSeek flash，并完成固定 60 题质量评测与 5 并发延迟验收。V3 已用真实模型和浏览器走通文档到报告的完整流程；正式质量评测与发布部署验收仍不提前开展。

V3 工作入口：资料可粘贴文字或导入公开网页单页；问答可指定资料并连续追问；分析可用自然语言建议字段；笔记保存问答或人工理解；报告可从分析、资料或笔记生成，随后编辑章节、核对缺口、检查换版来源并导出。报告 PDF 导出依赖 `reportlab`，表格导出使用 `openpyxl`，均已在后端依赖中声明。

V3 当前边界：笔记和分析记录仍仅本人可见；资料换版会持久标记相关报告待复核，笔记与分析显示影响数量并由用户按需重新检查；OCR、复杂版式与图表理解、全站网页抓取、多人评论审批和模板平台暂缓。公开网页导入只处理选定单页，不执行脚本或递归抓取。

## 目录结构

```
backend/   FastAPI 后端、数据库迁移、后台任务入口、测试
frontend/  React + TypeScript 前端
```

## 本地启动与重启

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
取值并将其注入容器，不会自动读取 `backend/.env`。两份文件互不自动同步。

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
模型首次准备与独立检查见 `~/jonge/infra/README.md` 的“Ollama 与嵌入模型”一节。
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
- 基础服务不可用：先检查 `~/jonge/infra` 是否已启动 PostgreSQL、Garage 与 Ollama；
  当前项目应用本身不需要启动 Docker 容器。

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

## 未来发布阶段：Compose 部署验收

本节不是当前开发启动方式。V2 功能开发完成并准备正式发布时，再用项目根目录的
Compose 验证镜像构建、容器部署、迁移、持久化和重启。届时执行：

```bash
cp .env.example .env   # 填写 KP_SECRET_KEY 与数据库密码
docker compose up -d --build
KP_BOOTSTRAP_PASSWORD='强密码' docker compose exec -e KP_BOOTSTRAP_PASSWORD api \
  python -m app.cli --email you@example.com --name 你的名字 --team 团队名 \
  --password-env KP_BOOTSTRAP_PASSWORD
```

访问 `http://<主机>:${KP_HTTP_PORT:-8080}`。数据保存在命名卷 `db-data`，容器重建后仍保留。

发布阶段的本地 HTTP 部署需保持 `KP_SESSION_COOKIE_SECURE` 为 false；启用 HTTPS 后再设为 true。

发布前执行步骤见 [Compose 发布验收清单](docs/operations/ACCEPTANCE-RUNBOOK.md)；
清单在当前阶段尚未执行，不作为本地开发前置条件。

固定 60 题质量与并发结果见 [质量验收记录](docs/operations/V1-QUALITY-ACCEPTANCE.md)，
评测入口与盲审步骤见 [`evaluation/README.md`](evaluation/README.md)。

## 已实现的能力

- 上传 PDF、Word（DOC/DOCX）、TXT、Markdown、HTML，文件写入配置的对象存储，处理任务入队。旧版 DOC 由 LibreOffice headless 转为 DOCX 后复用现有解析。
- 后台 worker 使用租约令牌领取和续租，限制重试次数，并周期对账中断上传；删除资料会取消旧任务。
- 上传使用幂等键、团队额度锁与逻辑删除，删除或迟到写入不能让资料复活。
- 身份请求具有 CSRF 校验、成员与最后管理员保护、复合归属约束及 PostgreSQL RLS。
- 团队管理员可生成一次性成员邀请；新成员通过链接设置账号并加入，已有账号登录后接受；
  邀请绑定邮箱、默认 72 小时有效，重复使用、过期和非受邀邮箱均被拒绝。
- 按团队与知识库范围检索，回答带可核对的原文引用；可限定指定资料，支持结合前文的连续追问，资料不足时明确说明。
- 问答按知识库保留，仅创建者本人可见；轮次使用客户端请求标识避免重复提交，回答通过 SSE 增量呈现，断流后可恢复已保存的草稿或终态。
- 登录和问答接口具有请求限流；请求日志只记录请求标识、方法、路径、状态码与耗时，不记录正文、查询参数或凭据。
- 首页提供知识库和团队入口；进入知识库后使用左侧资料树、右侧问答/分析视图，顶部切换知识库和个人菜单。
- 知识库地址支持直接访问、刷新和浏览器返回；工作模式按账号与知识库保存，本人历史会话持久化。
- 正文引用使用可点击上标，原文默认收起在右侧抽屉；悬浮或聚焦引用时高亮回答对应句段。
- 资料目录支持多级文件夹、树内新建/改名/删除，并可将目录或资料拖动到其他目录或根级。
- 管理员通过顶部“管理知识库”改名或删除知识库；删除非空知识库会连同资料、目录、问答记录和分析结果一并清理。
- V2 分析在工作台“分析”模式中：单文档或多文档提取字段、按维度对比，结果与结论均附可核对原文依据，仅创建者本人可见。
- 分析使用独立后台任务路径，具有租约、续跑、并发限额与撤权/删除保护；批量失败不覆盖成功结果，重新运行保留旧记录。
- 删除资料、目录或知识库时同步清理对象存储原件；目录删除递归处理其下全部子目录与资料。
- 分析任务记录 `provider`/`model`，界面明确区分替身与真实模型来源。

## 未完成

- 每日异地备份与恢复演练暂缓。
- 模型重排尚未接入；当前基线为向量召回与中英文词项召回的混合检索。
- 固定题集的人工盲审尚未完成，自动结构检查结果不能替代人工结论。
- 本地开发已启用生成与嵌入模型的真实适配器；自动化测试仍使用确定性替身。
- V2 真实模型适配已完成且本地配置可接入模型，但真实质量评测、容量测试与人工盲审尚未执行；替身验证只证明流程与权限，不代表提取或对比质量。详见 [V2 质量验收状态](docs/operations/V2-QUALITY-ACCEPTANCE.md)。

## 界面设计指导

界面修改以 [设计规范](design-system/k-platform/MASTER.md) 为准，页面差异见其 `pages/` 目录。颜色与字体等公共变量位于 `frontend/src/styles/tokens.css`。

## 后续实施

- V1：先读 [实施指导](docs/architecture/README.md)，再按 [任务清单](docs/implementation/V1-TASKS.md) 完成小切片。文档团队共享，问答历史仅本人可见；目标设计与已验证能力需分别记录。
- V2：功能闭环与替身验收已完成，真实模型质量验收待配置分析模型后执行。先读 [范围与验收基线](V2-SCOPE.md)、[分析实施指导](docs/architecture/V2-ANALYSIS.md) 与 [V2 任务清单](docs/implementation/V2-TASKS.md)；量化质量目标仍待确认。
- V3：主流程已完成，范围、验证结果与保留边界见 [阶段规划与完成记录](docs/implementation/V3-PLAN.md)。正式质量评测与发布部署验收不作为开发前置，现有未验收项继续保留记录。
