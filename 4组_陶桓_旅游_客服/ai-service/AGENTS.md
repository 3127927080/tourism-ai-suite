# AGENTS.md — AI Service（小满电商智能客服·AI 服务）

本文件供 AI 编码助手在参与本项目开发时阅读，请先完整阅读再动手写代码。

> 姊妹项目 Customer Service（独立仓库，`E:\LLM_student_project\customer-service`）有自己的 AGENTS.md，开发那边时请阅读那份文档。

## 1. 项目背景

本服务是小满电商智能客服系统的**AI 能力服务（AI Service，端口 8002）**。Customer Service 已经负责用户会话、消息、Turn、人工工单和实时事件；如果继续把模型调用、Agent 执行和运行监控都放进 Customer Service，会让会话业务与 AI 能力紧密耦合。因此项目把 AI 能力拆分为独立的 AI Service：

- Customer Service 管理会话事实，决定某个 AI 结果是否仍然有效；
- AI Service 管理 AgentRun，负责后续的模型执行和结果生成；
- 两个服务通过内部 HTTP 接口和统一事件协议协作。

## 2. 服务边界（重要）

**本仓库只开发 AI Service 一个服务，不修改 Customer Service / Ecommerce Service / 前端。**

AI Service 职责：

- 为一次 Turn 创建 AgentRun；
- 保存本次运行的输入上下文；
- 调用 AgentExecutor（模型 + 工具）生成结果；
- 保存执行结果、状态和监控数据；
- 根据 AgentRun 状态构建响应，交给 Customer Service 解析。

**AI Service 绝不做的事**（业务红线）：

1. 不持有 Customer Service 的完整 Conversation，也不直接决定某次输入是否仍然有效——这是 Customer Service 的职责，AI Service 只管自己的 AgentRun 状态机；
2. 不实现任何电商业务写操作（取消订单、修改地址、申请售后），即便 AI 判断需要执行，也只能在结果中返回操作引导，由用户在前端确认后调用 Ecommerce Service；
3. 不直接接触用户会话消息的持久化，`messages` / `conversations` 等表始终只存在于 Customer Service 的 PostgreSQL 中。

## 3. 与 Customer Service 的协作契约

### 3.1 调用流程（两阶段确认）

```text
Customer Service                          AI Service
      |  start_run(request)                    |
      |--------------------------------------->|
      |        run_decision_prepared + run_id  |
      |<----------------------------------------|
      | 比较 Turn 快照与 Conversation 当前版本   |
      |                                          |
      | 输入仍然有效 -> confirm_run(run_id)      |
      |--------------------------------------->|
      |     run_completed / run_handoff_requested |
      |<----------------------------------------|
      |                                          |
      | 输入已经过期 -> cancel_run(run_id)       |
      |--------------------------------------->|
      |              204 No Content            |
      |<----------------------------------------|
```

Customer Service 掌握最新的 Conversation 版本，因此由它判断输入是否仍然有效，AI Service 不重复做这个判断。

### 3.2 接口一览

| 接口 | 方法 | 说明 |
|------|------|------|
| `/internal/v1/agent/runs` | POST | 启动一次 AgentRun（对应 `start_run`） |
| `/internal/v1/agent/runs/{run_id}/confirm` | POST | 确认并发布已准备好的结果（对应 `commit_run`） |
| `/internal/v1/agent/runs/{run_id}/cancel` | POST | 取消待发布结果，幂等操作 |

> `confirm` 这个接口名对应的是 Customer Service 里"提交/确认"这一步的语义，调用方内部方法名可能叫 `commit_run`，属于跨仓库措辞差异，不代表接口本身有问题；新增或修改这三个接口路径时，必须同步检查 Customer Service 的 `atguigu/worker/ai/gateway.py` 是否需要同步调整调用路径。

### 3.3 双重认证

三个接口都要求两种凭证：

```text
X-Internal-Service-Token: <INTERNAL_SERVICE_TOKEN>
Authorization: Bearer <user-jwt>
```

- 内部服务令牌（`require_internal_service` 依赖）证明请求来自 Customer Service，而不是任意来源；
- 用户 JWT 由 `AuthService.get_current_customer()` 解析，且**角色必须是 `customer`**——AI Service 只服务客户身份的请求，agent/admin 身份不允许调用这几个内部接口。

### 3.4 请求体与事件协议

请求体（`AgentRunRequest`）包含 `conversation_id`、`turn_id`、`messages`（当前消息，至少 1 条）、`history`（历史消息，可为空）。不携带 `user_id`（已在 JWT 中），不携带 `input_revision` / `request_id`（版本与重试由 Customer Service 管理）。

响应事件类型（由 AgentRun 状态映射得到）：

```text
COMPLETED         -> run_completed
HANDED_OFF        -> run_handoff_requested
DECISION_PREPARED -> run_decision_prepared
FAILED            -> run_failed
```

`run_id` 必须包含在事件中，Customer Service 会用它调用 `confirm`/`cancel`，并写入 `ConversationTurn.run_id`。`RUNNING` 状态还没有结果，不构建事件；`SUPERSEDED` 由取消接口用 204 表示，不生成事件体。

## 4. AgentRun 状态机

`AgentRunState`（`atguigu/models/models.py`）定义六个状态：

```text
RUNNING            Agent 正在执行
DECISION_PREPARED  结果已生成，等待 Customer Service 确认
COMPLETED          普通回复已发布
HANDED_OFF         转人工结果已发布
FAILED             执行失败
SUPERSEDED         结果被取消，不再允许发布
```

流转关系：

```text
[*] --> RUNNING
RUNNING --> COMPLETED           # 直接生成普通回复
RUNNING --> HANDED_OFF          # 直接生成转人工结果
RUNNING --> DECISION_PREPARED   # 结果等待确认
RUNNING --> FAILED              # 执行失败
RUNNING --> SUPERSEDED          # 执行被取消
DECISION_PREPARED --> COMPLETED     # confirm 普通结果
DECISION_PREPARED --> HANDED_OFF    # confirm 转人工结果
DECISION_PREPARED --> SUPERSEDED    # cancel_run
```

## 5. 技术栈与代码规范

### 5.1 技术栈

Python `3.12+`，uv 管理依赖；FastAPI + Uvicorn、Pydantic 2 + pydantic-settings、SQLAlchemy 2（AsyncSession）+ PostgreSQL + psycopg 3、PyJWT。

> 当前不依赖 httpx、Redis：AI Service 目前不主动调用外部服务（LLM/Ecommerce 的调用会在实现 AgentExecutor 时加入相应依赖），也不接触 Pub/Sub（那是 Customer Service 的职责）。引入新依赖前请确认是否真的需要。

### 5.2 目录结构

```text
ai-service/
├── atguigu/
│   ├── main.py               # 进程启动入口
│   ├── common/                # 配置读取、事件循环、通用工具
│   ├── infrastructure/        # 数据库引擎、Session 工厂
│   ├── models/                # AgentRun 表结构
│   ├── harness/                # Agent 执行与生命周期协调
│   │   ├── agent/run/
│   │   │   └── coordinator.py # AgentRunCoordinator：生命周期与事务
│   │   └── llm/                # 模型调用封装（当前为空，待实现）
│   └── app/
│       ├── app.py             # FastAPI 应用实例 + 路由注册 + CORS
│       ├── dependencies.py    # 依赖注入组装
│       ├── schemas/            # 请求/响应模型、内部身份模型
│       ├── services/           # AuthService 等横切服务
│       ├── routers/            # 接口路由
│       └── repositories/       # 数据访问
├── .env
└── pyproject.toml
```

请求处理顺序：**路由 → Coordinator（harness）→ 仓储/数据库**。`AuthService` 属于横切关注点，任何层在需要时都可以通过 `dependencies.py` 注入，但业务判断逻辑必须留在 Coordinator，不能下沉到路由或上移到仓储。

### 5.3 分层依赖规则（必须遵守，禁止反向依赖）

依赖只允许自上而下单向流动：`routers → services / harness → repositories → models / infrastructure`。`common/` 与 `app/schemas/` 是公共契约，任何层均可引用。

- **禁止反向依赖**：models 不得 import harness/services/repositories；repositories 不得 import harness/services/routers；
- **禁止跨层依赖**：router 不得直接 import repository；repository 不得组装响应字典；
- `harness/agent/run/coordinator.py` 之所以放在 `harness` 而不是普通的 `app/services`，是因为它负责的不是一次性的业务规则判断，而是 AgentRun **从创建到终态的完整执行生命周期**（创建 Run、调用 AgentExecutor、状态校验、结果保存、事件构建），这条生命周期未来会牵涉模型调用、工具调用等更重的执行逻辑，用独立的 `harness` 层与偏 CRUD 性质的 `app/services`（如 `AuthService`）区分开。

各层职责边界（"只做 / 绝不做"）：

| 层 | 只做 | 绝不做 |
|------|------|------|
| `routers/` | 路径与方法、读取 Header、依赖注入、**内部服务令牌校验 + 用户 JWT 解析**、调用 Coordinator | 业务规则判断、SQL、直接操作 AgentRun 实体 |
| `harness/agent/run/coordinator.py` | AgentRun 生命周期、状态机校验、**控制事务提交时机**、调用 AgentExecutor、构建响应事件 | 解析 HTTP Header、编写 SQLAlchemy 查询语句、返回 HTTPException（应交给上层处理，或在校验失败时抛出语义化异常） |
| `app/repositories/` | 封装查询条件、查询与 `add` AgentRun 实体 | **`commit()`**、业务规则判断、组装响应 |
| `models/` | AgentRun 表字段、状态枚举、索引 | 任何行为逻辑 |
| `schemas/` | 接口契约：`AgentRunRequest`、`CurrentUser` 等允许暴露的字段 | 暴露 `input_context`、监控字段等内部专有数据 |

补充规则：

- **事务边界**：Coordinator 的公开业务入口方法（`start_run` / `confirm_run` / `cancel_run`）统一负责 `commit()`；Repository 方法只负责 `add()` / 查询，不提交事务；
- **依赖注入只走 `dependencies.py`**：具体类工厂函数 + `Annotated` 别名（沿用现有 `AgentRunCoordinatorDep`、`AuthServiceDep` 风格），禁止引入接口层/抽象基类/DI 框架；
- **新增/修改接口标准流程（按序）**：schemas 契约 → models（如有新表/字段）→ repositories → harness/coordinator（业务规则 + 事务）→ `dependencies.py` 注册 → routers（内部令牌校验在前、用户角色校验在前）→ `app.py` 挂载 → 正向/越权各自测一次。

## 6. 认证模块

- 用户令牌从 `Authorization: Bearer <token>` 请求头读取，由 `AuthService.get_current_customer()` 解码为 `CurrentUser(user_id, role)`，并强制要求 `role == "customer"`，否则返回 403；
- 内部服务令牌通过 `require_internal_service`（`dependencies.py`）依赖校验 `X-Internal-Service-Token` 请求头，与 `Settings.internal_service_token` 比较，不一致返回 403；
- `JWT_SECRET` / `JWT_ALGORITHM` 必须与 Customer Service、Ecommerce Service 保持一致，否则用户令牌无法互认；`INTERNAL_SERVICE_TOKEN` 必须与 Customer Service 的对应配置保持一致，否则内部调用会被拒绝。

## 7. Windows 兼容规范（本地在 Windows 上开发）

- Windows 默认事件循环与异步数据库驱动不兼容，统一走 `common/event_loop.py` 封装的 `run_async`；
- 服务启动不用命令行一键启动，在代码中创建 `uvicorn.Server` 并 `loop="none"` 复用当前事件循环；
- 启动方式：项目根目录执行 `uv run python -m atguigu.main`；建表命令与服务入口走同一套 `run_async`。

## 8. 已知技术债 / 待实现清单（新接手时请先确认现状，不要假设已完成）

1. **`AgentRunCoordinator` 三个方法目前都是空实现**（`start_run` / `confirm_run` / `cancel_run` 均直接 `return {}`，`cancel_run` 甚至没有 return 语句），尚未创建、查询或更新任何 `AgentRun` 记录；
2. **`AgentRunRepository` 目前是空类**，只保存了 `session`，没有 `add()` / `find_by_id()` 等任何方法；
3. **`get_agent_coordinator()` 依赖注入未接数据库 Session**（`AgentRunCoordinator()` 是无参构造），Coordinator 目前拿不到 Session 也拿不到 Repository，数据库落库链路还没有打通；
4. **`Settings.ai_database_url` 默认库名与 `.env.example` 不一致**：`common/config.py` 默认是 `ai_customer_service_v1`，`.env.example` 里是 `ai_customer_service`。新建/切换环境时请显式确认 `.env` 里的库名，不要依赖默认值；
5. **`llm_model` 配置项当前是空字符串**，`harness/llm/` 目录当前为空目录，AgentExecutor（模型调用、工具调用、Prompt 组装）尚未实现，`start_run` 目前无法真正生成结果；
6. **AgentRun 的监控字段**（`input_tokens` / `output_tokens` / `latency_ms` / `error`）已经在模型中定义，但没有任何写入路径，需要在实现 `AgentExecutor` 调用时一并补上。

## 9. 环境变量

对照 `.env.example`：

| 变量 | 说明 | 示例 |
|------|------|------|
| `AI_DATABASE_URL` | AI Service 数据库连接串（异步 psycopg） | `postgresql+psycopg://customer_service:customer_service@localhost:5432/ai_customer_service` |
| `API_HOST` / `API_PORT` | 服务监听地址与端口 | `0.0.0.0` / `8002` |
| `INTERNAL_SERVICE_TOKEN` | 内部服务令牌，须与 Customer Service 一致 | `customer-internal-token` |
| `JWT_SECRET` / `JWT_ALGORITHM` | JWT 密钥/算法，三服务保持一致 | `ecommerce-secret` / `HS256` |

`common/config.py` 中还定义了 `llm_model`（默认空字符串）和 `prompt_version`（默认 `customer-support-v1`），但 `.env.example` 目前没有列出对应条目；后续接入真实模型时应补充到 `.env.example`，避免只靠代码默认值。

## 10. 分支工作流（必须遵守）

- `master` + `dev` 双分支：`dev` 为日常开发分支，所有代码在这里编写、提交、测试；`master` 为稳定分支，只接受来自 `dev` 的合并；
- **未经用户明确同意，禁止推送或合并到 `master`**。用户明确说"可以合并/推送 master"才执行 `dev → master`；仅说"推送"默认指推送 `dev`；
- 每完成一个功能点在 `dev` 上提交（信息写清做了什么）；紧急修复同样走 `dev`，不允许绕过。
