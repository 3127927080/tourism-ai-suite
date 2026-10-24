# AGENTS.md — AI Service（旅游智能客服·AI 服务）

本文件供 AI 编码助手在参与本项目开发时阅读，请先完整阅读再动手写代码。

> 姊妹项目 Customer Service（`customer-service/`）与 Tourism Service（`tourism-service/`）各有自己的 AGENTS.md，开发那边时请阅读对应文档。

## 1. 项目背景

本服务是在线旅游平台智能客服系统的**AI 能力服务（AI Service，端口 8002）**。Customer Service 负责用户会话、消息、Turn、人工工单和实时事件；AI Service 负责一次 Turn 的理解、只读工具调用、知识检索、结果校验与多轮任务状态归约。两个服务通过内部 HTTP 接口和统一事件协议协作，AI Service 以 NDJSON 流式返回 token 增量与最终事件。

## 2. 服务边界（重要）

**本仓库只开发 AI Service 一个服务，不修改 Customer Service / Tourism Service / 前端。**

AI Service 职责：

- 为一次 Turn 创建 AgentRun；
- 保存本次运行的输入上下文；
- 调用 AgentExecutor（模型 + 只读工具）生成结果；
- 做业务事实/页面动作校验，归约多轮任务状态；
- 保存执行结果、状态和监控数据；
- 根据 AgentRun 状态构建响应事件，交给 Customer Service 解析。

**AI Service 绝不做的事**（业务红线）：

1. 不持有 Customer Service 的完整 Conversation，也不直接决定某次输入是否仍然有效——这是 Customer Service 的职责；
2. 不实现任何旅游业务写操作（退款、取消预订、修改出行人），即便 AI 判断需要执行，也只能返回页面动作引导，由用户在前端确认后调用 Tourism Service；
3. 不直接接触用户会话消息的持久化，`messages` / `conversations` 等表只存在于 Customer Service 的 PostgreSQL；
4. 多轮任务状态 `task_state` 的**语义**在本服务归约，但**存取**由 Customer Service 负责（不透明 JSON）。

## 3. 与 Customer Service 的协作契约

### 3.1 调用流程（两阶段确认）

```text
Customer Service                          AI Service
      |  start_run(request)                    |
      |--------------------------------------->|  NDJSON: run_delta... + 最终事件
      |        run_decision_prepared + run_id  |
      |<----------------------------------------|
      | 比较 Turn 快照与 Conversation 当前版本   |
      |                                          |
      | 输入仍然有效 -> confirm_run(run_id)      |
      |--------------------------------------->|
      |     run_completed / run_handoff_requested |
      |<----------------------------------------|
      |                                          |
      | 输入已经过期 -> cancel_run(run_id)      |
      |--------------------------------------->|
      |              204 No Content            |
      |<----------------------------------------|
```

### 3.2 接口一览

| 接口 | 方法 | 说明 |
|------|------|------|
| `/internal/v1/agent/runs` | POST | 启动一次 AgentRun；**NDJSON 流式**（`run_delta` + 最终事件） |
| `/internal/v1/agent/runs/{run_id}/confirm` | POST | 确认并发布已准备好的结果 |
| `/internal/v1/agent/runs/{run_id}/cancel` | POST (204) | 取消待发布结果，幂等 |

### 3.3 双重认证

```text
X-Internal-Service-Token: <INTERNAL_SERVICE_TOKEN>
Authorization: Bearer <user-jwt>
```

- 内部服务令牌（`require_internal_service`）证明请求来自 Customer Service；
- 用户 JWT 由 `AuthService.get_current_customer()` 解析，**角色必须是 `customer`**。

### 3.4 请求体与事件协议

`AgentRunRequest` 包含 `conversation_id`、`turn_id`、`messages`、`history`、`task_state`（多轮任务状态，可为 null）。

响应事件类型：`COMPLETED→run_completed`、`HANDED_OFF→run_handoff_requested`、`DECISION_PREPARED→run_decision_prepared`、`FAILED→run_failed`；`run_id` 必须包含在事件中。

流式事件：`run_delta`（`{run_id,index,delta}`）逐块下发文本，最终再下发上述结构化事件。

## 4. AgentRun 状态机

```text
[*] --> RUNNING
RUNNING --> COMPLETED | HANDED_OFF | DECISION_PREPARED | FAILED | SUPERSEDED
DECISION_PREPARED --> COMPLETED | HANDED_OFF | SUPERSEDED
```

## 5. 技术栈与代码规范

### 5.1 技术栈

Python `3.12+`，uv 管理依赖；FastAPI + Uvicorn、Pydantic 2 + pydantic-settings、SQLAlchemy 2（AsyncSession）+ PostgreSQL + psycopg 3、PyJWT、HTTPX（调用 Tourism Service / 知识库）、LangChain / LangGraph（Agent、结构化输出、工具调用）、OpenAI/DeepSeek 兼容客户端。

### 5.2 目录结构

```text
ai-service/
├── atguigu/
│   ├── main.py
│   ├── common/              # 配置读取、事件循环、通用工具
│   ├── infrastructure/      # 数据库引擎、Session 工厂、TourismDataClient、KnowledgeClient
│   ├── models/              # AgentRun / AgentToolCall
│   ├── agent/
│   │   ├── factory.py       # 构建 Agent（工具 + 中间件 + 工具上限）
│   │   ├── llm/             # 模型适配、AgentOutput、BASE_PROMPT
│   │   └── harness/
│   │       ├── tools/       # 只读工具、目录、执行器、快照
│   │       ├── skills/      # Skill 目录与可见性中间件
│   │       ├── tasks/       # TaskState schema/state/reducer
│   │       ├── rules/       # 事实/页面动作/纠错
│   │       ├── validator/   # 回答与页面动作校验
│   │       ├── knowledge/   # 知识查询服务（平台规则 + 8005）
│   │       └── run/         # coordinator/executor/context/events/streaming
│   └── app/                 # app、dependencies、schemas、routers、repositories、services
├── eval/                    # 离线评测
└── tests/                   # 单元测试
```

请求处理顺序：**路由 → Coordinator（harness）→ 仓储/数据库**。业务判断逻辑必须留在 Coordinator，不能下沉到路由或上移到仓储。

### 5.3 分层依赖规则（必须遵守，禁止反向依赖）

依赖只允许自上而下单向流动：`routers → services / harness → repositories → models / infrastructure`。`common/` 与 `app/schemas/` 是公共契约，任何层均可引用。

- **禁止反向依赖**：models 不得 import harness/services/repositories；repositories 不得 import harness/services/routers；
- **禁止跨层依赖**：router 不得直接 import repository；repository 不得组装响应字典；
- `harness/agent/run/coordinator.py` 负责 AgentRun 从创建到终态的完整执行生命周期，用独立的 `harness` 层与偏 CRUD 的 `app/services` 区分。

各层职责边界（"只做 / 绝不做"）：

| 层 | 只做 | 绝不做 |
|------|------|------|
| `routers/` | 路径与方法、读取 Header、依赖注入、内部令牌 + 用户 JWT 校验、调用 Coordinator | 业务规则判断、SQL、直接操作实体 |
| `harness/.../coordinator.py` | AgentRun 生命周期、状态机校验、控制事务提交时机、调用 AgentExecutor、归约 TaskState、构建事件 | 解析 HTTP Header、写 SQLAlchemy 查询、返回 HTTPException |
| `app/repositories/` | 封装查询、`add` 实体 | `commit()`、业务判断、组装响应 |
| `models/` | 表字段、状态枚举、索引 | 行为逻辑 |
| `schemas/` | 接口契约：`AgentRunRequest`、`CurrentUser` 等 | 暴露内部专有数据 |

补充规则：

- **事务边界**：Coordinator 的公开入口方法（`start_run` / `confirm_run` / `cancel_run`）统一负责 `commit()`；Repository 只 `add()` / 查询；
- **依赖注入只走 `dependencies.py`**：具体类工厂函数 + `Annotated` 别名，禁止引入 DI 框架；
- **新增/修改接口标准流程（按序）**：schemas 契约 → models → repositories → harness/coordinator → `dependencies.py` 注册 → routers → `app.py` 挂载 → 正向/越权各自测一次。

## 6. 认证模块

- 用户令牌从 `Authorization: Bearer <token>` 读取，由 `AuthService` 解码为 `CurrentUser(user_id, role)`，强制 `role == "customer"`；
- 内部服务令牌通过 `require_internal_service` 校验 `X-Internal-Service-Token`；
- `JWT_SECRET` / `JWT_ALGORITHM` 必须与 Customer Service、Tourism Service 一致；`INTERNAL_SERVICE_TOKEN` 必须与 Customer Service 一致。

## 7. Windows 兼容规范（本地在 Windows 上开发）

- Windows 默认事件循环与异步数据库驱动不兼容，统一走 `common/event_loop.py` 封装的 `run_async`；
- 服务启动在代码中创建 `uvicorn.Server` 并 `loop="none"` 复用当前事件循环；
- 启动方式：`uv run python -m atguigu.main`；建表命令与服务入口走同一套 `run_async`。

## 8. 当前实现状态（新接手时请先确认现状）

1. `AgentRunCoordinator` / `AgentRunRepository` / `AgentExecutor` / `AgentOutputValidator` 均已实现，数据库落库链路已打通；
2. `harness/llm/` 已实现（模型适配、结构化输出、Prompt）；
3. 只读工具已旅游化（`search_travel_products` / `get_travel_product` / `get_product_availability` / `list_orders` / `get_order` / `get_trip_info` / `list_refunds` / `search_knowledge`）；
4. TaskState（`harness/tasks/`）与 token 级流式（`harness/run/streaming.py`）已实现；
5. 监控字段（`input_tokens` / `output_tokens` / `latency_ms` / `error`）已在 coordinator/executor 写入；
6. 知识库 8005 未就绪时，`search_knowledge` 返回平台规则并附降级提示，不影响业务类问答。

## 9. 环境变量

对照 `.env.example`：

| 变量 | 说明 |
|------|------|
| `AI_DATABASE_URL` | AI Service 数据库连接串 |
| `API_HOST` / `API_PORT` | 监听地址与端口（`0.0.0.0` / `8002`） |
| `TOURISM_DATA_BASE_URL` / `TOURISM_DATA_TIMEOUT_SECONDS` | 旅游数据服务地址与超时（兼容旧 `ECOMMERCE_BASE_URL`） |
| `KNOWLEDGE_BASE_URL` / `KNOWLEDGE_SERVICE_TOKEN` / `KNOWLEDGE_TIMEOUT_SECONDS` / `KNOWLEDGE_TOP_K` | 知识库检索 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_PROVIDER` / `LLM_THINKING_MODE` | 模型配置 |
| `AGENT_TOOL_CALL_LIMIT` / `AGENT_RUN_TIMEOUT_SECONDS` / `STREAM_CHUNK_SIZE` | Agent 运行约束与流式分块 |
| `INTERNAL_SERVICE_TOKEN` / `JWT_SECRET` / `JWT_ALGORITHM` | 跨服务令牌 |

## 10. 分支工作流（必须遵守）

- `master` + `dev` 双分支：`dev` 为日常开发分支；`master` 只接受来自 `dev` 的合并；
- **未经用户明确同意，禁止推送或合并到 `master`**；
- 每完成一个功能点在 `dev` 上提交（信息写清做了什么）。
