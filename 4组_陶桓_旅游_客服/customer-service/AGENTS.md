# AGENTS.md — Customer Service（旅游智能客服·会话服务）

本文件供 AI 编码助手在参与本项目开发时阅读，请先完整阅读再动手写代码。

> 姊妹项目 AI Service（`ai-service/`）、Tourism Service（`tourism-service/`）各有自己的 AGENTS.md，开发那边时请阅读对应文档。

## 1. 项目背景

本服务是在线旅游平台智能客服系统的**会话中枢（Customer Service，端口 8000）**，整体方案为"AI 优先、人工兜底、关键操作由用户确认"：接收并保存旅客消息、调度 AI 或人工处理；退款/取消预订/修改出行人等关键操作 AI 只返回"页面引导"，由用户在前端确认后直接调用 Tourism Service 执行；AI 无法可靠处理时转人工（REQUEST_HANDOFF），本服务创建工单并通知管理端。

## 2. 开发范围（重要）

**本仓库只开发 Customer Service 一个服务。**

| 项目 | 端口 | 关系 |
|------|------|------|
| **Customer Service（本仓库）** | `8000` | 会话、消息持久化、AI 调度（AI Worker）、人工工单、实时通知（Outbox Worker + Redis + WebSocket）、多轮任务状态存取 |
| AI Service | `8002` | 独立仓库，本服务通过 HTTP/NDJSON 流式调用，不修改它 |
| Tourism Service | `8001` | 旅游业务数据源，只调用不修改 |
| 知识库 Knowledge Service | `8005` | 旅游知识检索 |
| User / Admin Frontend | `5173` / `5174` | 前端，只对接不修改 |

## 3. 分支工作流（必须遵守）

- `master` + `dev` 双分支：`dev` 为日常开发分支；`master` 只接受来自 `dev` 的合并；
- **未经用户明确同意，禁止推送或合并到 `master`**；
- 每完成一个功能点在 `dev` 上提交（信息写清做了什么）。

## 4. 系统架构

### 4.1 协作链路与通信契约

```
用户消息 → User Frontend → 本服务（保存消息 + 创建 Turn）
        → AI Worker → AI Service（NDJSON 流式：理解 → 查业务数据/知识库 → 生成 → 校验）
        → 回答 / 追问 / 操作引导 / REQUEST_HANDOFF
        → 本服务（保存回复，写 Outbox：message_delta + message_created）→ Outbox Worker → Redis Pub/Sub → WebSocket → 前端

转人工链路：AI Service 请求转人工 → 本服务创建 Handoff 工单 → 通知 Admin Frontend → 人工接入回复 → 结束后恢复 AI 模式
```

| 通信 | 方式 | 要点 |
|------|------|------|
| 前端 → 本服务 | REST + JWT / WebSocket + JWT | 登录、会话、消息、工单操作；WS 推送消息、处理状态、工单事件 |
| 本服务 AI Worker → AI Service | NDJSON（流式） | 携带用户消息 + 会话历史 + 用户 JWT + 内部服务令牌，边收增量边写 Outbox |
| AI Service → Tourism | REST 只读 | AI 以当前用户身份透传 JWT，与本服务无关 |
| 本服务 → Redis → 前端 | Outbox 模式 | 业务事件先写 `realtime_outbox`，Worker 发布到 Redis，经 WebSocket 送达 |

注意：User Frontend 不直接调用 AI Service，聊天统一经过本服务。

### 4.2 认证与角色

JWT 由 Tourism Service（或课程鉴权源）签发，跨服务共享 `JWT_SECRET` / `JWT_ALGORITHM`；解码得到 `user_id` 与 `role`（`customer` / `agent` / `admin`）。本服务负责解析令牌并按角色鉴权，不负责登录发号。

### 4.3 基础设施与进程模型

- **PostgreSQL**（虚拟机 `192.168.200.188:5432`，库 `customer_service`）：核心表 `conversations`（含 `task_state` JSONB）/ `messages` / `conversation_turns` / `handoffs` / `realtime_outbox`；
- **Redis**（`192.168.200.188:6379`）：仅用于 Pub/Sub 发布实时事件，不作为会话或业务数据缓存；不使用 Kafka/RabbitMQ，异步任务靠 PostgreSQL + Worker 实现；
- 本服务由三个进程组成（共享 PostgreSQL 状态，职责独立）：**API 进程**（FastAPI）、**AI Worker**（领取到期 Turn，调用 AI Service）、**Outbox Worker**（读取待发布事件，经 Redis Pub/Sub 推送前端）；
- 可靠性要点：消息与业务写作用唯一标识避免重复提交；Worker 领取任务避免重复处理；AI 回复写回做**版本检查**避免旧回复覆盖新消息；业务事件走 Outbox 模式。

## 5. 核心业务红线（必须遵守）

1. 本服务**不实现任何旅游业务写操作**（退款、取消预订、修改出行人），均由用户在前端确认后直接调用 Tourism Service；
2. **Redis 只做 Pub/Sub**，不得写入会话或业务数据；
3. 用户会话和聊天消息只保存在本服务的 PostgreSQL 中，不存到 AI Service 或 Redis；
4. 调用 AI Service 必须同时携带用户 JWT 和内部服务令牌；
5. AI 回复写回时必须做版本检查，防止旧回复覆盖用户新消息；
6. **TaskState 只做不透明存取**：本服务不解析任务语义，只负责随请求透传与按版本原子写回。

## 6. 技术栈与代码规范

### 6.1 技术栈

Python `3.12+`，uv 管理依赖；FastAPI + Uvicorn、Pydantic 2 + pydantic-settings、SQLAlchemy 2（AsyncSession）+ PostgreSQL + psycopg 3、PyJWT、Redis AsyncIO（Pub/Sub）、WebSocket、HTTPX（流式调用 AI Service）。

### 6.2 分层结构

```text
atguigu/
├── main.py               # API 进程启动入口
├── common/               # 配置读取、事件循环等跨层工具
├── infrastructure/       # 数据库引擎、会话工厂、建表命令（含 task_state 迁移）
├── models/               # 表结构定义
├── worker/
│   ├── ai/               # AI Worker：gateway / parser / result / worker
│   └── realtime.py       # Outbox Worker
└── app/
    ├── app.py            # FastAPI 应用实例 + 路由注册 + CORS
    ├── dependencies.py   # 依赖注入组装
    ├── schemas/          # 请求与响应模型
    ├── services/         # 业务规则与流程编排
    ├── routers/          # 接口路由
    └── repositories/     # 数据访问
```

请求处理顺序：**路由 → 服务 → 仓储/数据库**。先写 schema，再写 service，再挂 router，最后在 `dependencies.py` 注册注入。

#### 6.2.1 分层依赖硬性规则（必须遵守，禁止反向依赖）

依赖只允许自上而下单向流动：`routers → services → repositories → models / infrastructure`。`common/` 与 `app/schemas/` 是公共契约，任何层均可引用。

- **禁止反向依赖**：models 不得 import services/repositories；repositories 不得 import services/routers；services 不得 import routers；
- **禁止跨层依赖**：router 不得直接 import repository；repository 不得组装响应模型；
- 发现需要反向调用时，说明职责放错了层：先把职责移到正确的层，而不是加反向 import 绕过。

各层职责边界（"只做 / 绝不做"）：

| 层 | 只做 | 绝不做 |
|------|------|------|
| `routers/` | 路径与方法、读取 Header、依赖注入、鉴权与角色校验、声明 `response_model` | 业务规则判断、SQL、返回数据库实体 |
| `services/` | 业务规则、组织多个 repository、控制事务（统一 commit） | 解析 Request 对象、编写 SQLAlchemy 查询语句 |
| `repositories/` | 封装查询条件、查询与 `add` 实体、排序在数据库侧完成 | `commit()`、业务规则判断、组装响应 |
| `models/` | 表字段、外键、唯一约束、索引、ORM 关系 | 任何行为逻辑 |
| `schemas/` | 接口契约：允许返回的字段与值域 | 暴露租约、审计标识等持久化专有字段 |

补充规则：

- **角色校验必须在调用业务方法之前**（router 层完成）；
- **事务边界**：内部方法只 `flush()`，公开业务入口统一 `commit()`；
- **模型与契约分离**：实体 → service 筛选组装 → schemas 校验 → JSON；
- **依赖注入只走 `dependencies.py`**：具体类工厂函数 + `Annotated` 别名；
- 同类排序/过滤一律在数据库查询中完成（repository）；
- **新增接口标准流程（按序）**：schemas 契约 → models → repositories → services → `dependencies.py` 注册 → routers → `app.py` 挂载 → 正向/越权各自测一次。

### 6.3 配置管理

- 使用 pydantic-settings，`.env` 通过基于 `__file__` 的绝对路径定位项目根；`get_settings()` 带 `lru_cache`。

### 6.4 Windows 兼容规范（本地在 Windows 上开发）

- Windows 默认事件循环与异步数据库驱动不兼容，统一走 `common/event_loop.py` 封装的 `run_async`；
- 服务启动在代码中创建 `uvicorn.Server` 并 `loop="none"` 复用当前事件循环；
- 启动方式：`uv run python -m atguigu.main`；建表命令与服务入口走同一套 `run_async`。

### 6.5 认证模块

- 令牌从 `Authorization: Bearer <token>` 读取，解码为 `CurrentUser(user_id, role)`；
- 认证服务提供 `get_authorized_user(authorization, required_role)`：格式错/无效 → 未授权，角色不符 → 无权限。

### 6.6 数据库

- 异步引擎 `create_async_engine(url, pool_pre_ping=True)`；`async_sessionmaker(expire_on_commit=False)`；请求级会话通过 `get_db()` 生成器提供，异常时回滚；
- 建表命令 `uv run python -m atguigu.infrastructure.init_db`：`create_all` + 幂等 `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS task_state JSONB`。

### 6.7 实时与流式

- 事件类型：`message_delta`（token 增量）、`message_created`（完整消息）、`handoff_changed`（工单状态）；
- 仅“直接完成”的文本回复在完整消息前按序写 `message_delta`；页面引导/转人工在确认后再发完整消息；
- Worker 消费 AI 的 NDJSON：`run_delta` 回调累积，最终事件触发结算。

### 6.8 环境变量

关键变量：`DATABASE_URL`、`REDIS_URL`、`AI_SERVICE_URL`、`JWT_SECRET`、`JWT_ALGORITHM`、`API_HOST`、`API_PORT`、`CORS_ORIGINS`，模板见 `.env.example`。

注意：`JWT_SECRET` / `JWT_ALGORITHM` 必须与 Tourism Service、AI Service 保持一致，否则跨服务令牌无法互认。

## 7. 环境与部署

- 本地（Windows）独立启动三个进程：`uv run python -m atguigu.main` / `-m atguigu.worker.ai.worker` / `-m atguigu.worker.realtime`；
- 虚拟机（`192.168.200.188`）运行 PostgreSQL（`5432`）、Redis（`6379`）、Tourism Service（`8001`）；
- 验证：Tourism `http://192.168.200.188:8001/docs`（含 `/api/v1/auth/demo-token`）；本服务 `http://127.0.0.1:8000/docs`。

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **customer-service** (497 symbols, 1011 relationships, 40 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/customer-service/context` | Codebase overview, check index freshness |
| `gitnexus://repo/customer-service/clusters` | All functional areas |
| `gitnexus://repo/customer-service/processes` | All execution flows |
| `gitnexus://repo/customer-service/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
