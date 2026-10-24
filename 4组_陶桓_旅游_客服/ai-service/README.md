# AI Service — 旅游智能客服·AI 服务

在线旅游平台的 AI 能力服务（端口 8002）。管理 AgentRun 的完整生命周期：接收 Customer Service 的调用，驱动模型与只读工具执行，做事实/页面动作校验与多轮任务状态归约，再生成回复并流式返回。

> 姊妹项目：Customer Service（`customer-service/`）、Tourism Service（`tourism-service/`，只读业务数据源）。

## 系统中的位置

| 项目 | 端口 | 职责 |
|------|------|------|
| Customer Service | 8000 | 会话/消息持久化、AI 调度、人工工单、实时通知 |
| **AI Service（本仓库）** | **8002** | 意图理解、只读工具、知识检索、回复校验、任务槽位归约 |
| Tourism Service | 8001 | 旅游业务数据源（酒店/景区/票务/接送/订单/出行/退款） |
| 知识库 Knowledge Service | 8005 | 旅游知识检索（只返回片段 + 元数据） |
| User Frontend / Admin Frontend | 5173 / 5174 | 旅客端 / 客服管理端 |

## 服务边界

1. **不持有完整会话**：只保存 AgentRun 的输入快照与结果，不保存 Customer Service 的 Conversation 全量数据；
2. **不判断输入是否过期**：由 Customer Service 依据会话版本决定 confirm 还是 cancel；
3. **不做旅游业务写操作**：退款、取消预订、修改出行人只能返回页面动作引导，由用户在前端确认后调用 Tourism Service。
4. **任务语义在此归约**：Customer Service 只保存不透明 `task_state` JSON，槽位语义与状态迁移由本服务负责。

## 能力概览

### 只读工具（`agent/harness/tools/read.py`）

| 工具 | 作用 | Tourism Service 端点 |
|---|---|---|
| `search_travel_products` | 搜索旅游产品（hotel/scenic/ticket/transfer） | `GET /products?query=&type=` |
| `get_travel_product` | 产品详情 | `GET /products/{id}` |
| `get_product_availability` | 余量/房态/余票 | `GET /products/{id}/availability` |
| `list_orders` | 当前用户订单列表 | `GET /orders` |
| `get_order` | 订单详情 | `GET /orders/{id}` |
| `get_trip_info` | 出行信息（日期/入住/班次/旅客/集合点） | `GET /orders/{id}/trip` |
| `list_refunds` | 退款记录 | `GET /refunds?order_id=` |
| `search_knowledge` | 平台规则 + 旅游知识检索 | 8005（失败时平台规则兜底） |

### Skill（`agent/harness/skills/`）

`travel-product-service` / `order-service` / `trip-service` / `policy-service` / `refund-service`。

### 校验与治理

- `validator/answer.py`：业务事实校验；业务工具全失败→安全回复；知识工具全失败→DECLINE；
- `validator/action.py`：页面动作必须引用成功查询过的订单；
- `rules/fact.py`：编号/数字/状态事实抽取；`rules/action.py`：旅游页面动作白名单；
- `factory.py`：工具调用上限 `AGENT_TOOL_CALL_LIMIT`、Run 总超时 `AGENT_RUN_TIMEOUT_SECONDS`。

### 多轮任务状态（TaskState）

- `agent/harness/tasks/{schema,state,reducer}.py`：任务类型/槽位校验 + 确定性 Reducer（START/SWITCH/UPDATE_SLOTS/COMPLETE/CANCEL/RESUME/CANCEL_ALL）；
- `AgentOutput.task_update` 承载模型输出，`coordinator` 归约后写入 `run.result.task_state`。

### 流式输出

- `/internal/v1/agent/runs` 以 **NDJSON** 返回：`run_delta`（按 `STREAM_CHUNK_SIZE` 分块文本）+ 最终事件；
- 仅“直接完成”的文本回复分块流式；`DECISION_PREPARED`/`HANDOFF` 在确认后再发。

## 技术栈

- Python 3.12+ / uv 包管理
- FastAPI + Uvicorn、Pydantic 2 + pydantic-settings
- SQLAlchemy 2（AsyncSession）+ PostgreSQL + psycopg 3
- LangChain / LangGraph（Agent、结构化输出、工具调用）、OpenAI/DeepSeek 兼容客户端
- PyJWT（与 Customer Service、Tourism Service 共享密钥互认）

## 项目结构

```text
ai-service/
├── atguigu/
│   ├── main.py
│   ├── common/              # 配置、事件循环、工具函数
│   ├── infrastructure/      # 数据库、TourismDataClient、KnowledgeClient
│   ├── models/              # AgentRun / AgentToolCall
│   ├── agent/
│   │   ├── factory.py       # 构建 Agent
│   │   ├── llm/             # 模型适配、输出模型、Prompt
│   │   └── harness/
│   │       ├── tools/       # 只读工具、目录、执行器、快照
│   │       ├── skills/      # Skill 目录与中间件
│   │       ├── tasks/       # TaskState schema/state/reducer
│   │       ├── rules/       # 事实/页面动作/纠错
│   │       ├── validator/   # 回答与动作校验
│   │       ├── knowledge/   # 知识查询服务
│   │       └── run/         # coordinator/executor/context/events/streaming
│   └── app/                 # app、dependencies、schemas、routers、repositories、services
├── eval/                    # 离线评测（规则层 + RAGAS/DeepEval）
└── tests/                   # TaskState/流式/评测等单测
```

## 快速开始

```bash
cp .env.example .env          # 填 LLM_API_KEY、数据库、TOURISM_DATA_BASE_URL、KNOWLEDGE_*
uv sync
uv run python -m atguigu.infrastructure.init_db   # 建表（幂等）
uv run python -m atguigu.main                     # 8002
```

启动后访问 <http://127.0.0.1:8002/docs>。

## 内部接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/internal/v1/agent/runs` | POST | 启动 Run；**NDJSON 流式**返回 `run_delta` + 最终事件 |
| `/internal/v1/agent/runs/{run_id}/confirm` | POST | 确认已准备好的决策 |
| `/internal/v1/agent/runs/{run_id}/cancel` | POST (204) | 取消待发布结果，幂等 |

三个接口均要求 `X-Internal-Service-Token` + customer 角色 JWT。

## 环境变量

| 变量 | 说明 |
|---|---|
| `AI_DATABASE_URL` | AI 库连接串（`ai_customer_service`） |
| `TOURISM_DATA_BASE_URL` / `TOURISM_DATA_TIMEOUT_SECONDS` | 旅游数据服务地址与超时（兼容旧 `ECOMMERCE_BASE_URL`） |
| `KNOWLEDGE_BASE_URL` / `KNOWLEDGE_SERVICE_TOKEN` / `KNOWLEDGE_TIMEOUT_SECONDS` | 知识库检索 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_PROVIDER` / `LLM_THINKING_MODE` | 模型配置（`LLM_THINKING_MODE=disabled` 关闭思考） |
| `AGENT_TOOL_CALL_LIMIT` / `AGENT_RUN_TIMEOUT_SECONDS` / `STREAM_CHUNK_SIZE` | Agent 运行约束与流式分块 |
| `JWT_SECRET` / `JWT_ALGORITHM` | 三服务共享 |

## 评测

```bash
# 规则层冒烟（跳过语义层）
uv run python -m eval.run_eval --limit 3 --skip-ragas --skip-deepeval
```

数据集：`eval/datasets/knowledge_qa.csv`（旅游平台规则/知识缺失）、`service_qa.csv`（旅游业务 + 行为红线）。

## 相关文档

- `AGENTS.md` — 架构约束、服务边界与开发规范（AI 编码助手必读）
- `knowledge_integration_plan.md` — 知识库接入方案与 P0/P1

---
*旅游智能客服项目，演示“AI 优先、人工兜底、只读 + 页面引导”的完整协作流程。*
