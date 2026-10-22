# Customer Service — 旅游智能客服·会话服务

在线旅游平台的智能客服**会话中枢**：向前承接旅客端与管理端请求，向后调度 AI Service，并通过 PostgreSQL、Redis、WebSocket 保证消息持久化与实时送达。

> 姊妹项目：AI Service（本仓库同级 `ai-service/`）、Tourism Service（`tourism-service/`，只读业务数据源）。

## 系统中的位置

整套旅游智能客服由两个前端和三个后端组成，本仓库只包含 Customer Service：

| 项目 | 端口 | 职责 |
|------|------|------|
| **Customer Service（本仓库）** | **8000** | 会话/消息持久化、AI 调度、人工工单、实时通知、多轮任务状态 |
| AI Service | 8002 | 意图理解、只读工具、知识检索、回复校验、任务槽位归约 |
| Tourism Service | 8001 | 旅游业务数据源（酒店/景区/票务/接送/订单/出行/退款） |
| User Frontend / Admin Frontend | 5173 / 5174 | 旅客端 / 客服管理端 |
| 知识库 Knowledge Service | 8005 | 旅游知识检索（只返回片段 + 元数据） |

主链路：

```
用户发消息 → 本服务（保存消息 + 创建 Turn）
          → AI Worker → AI Service（NDJSON 流式：token 增量 + 最终事件；携带用户 JWT + 内部服务令牌）
          → 回答 / 追问 / 操作引导 / 转人工
          → 本服务写 Outbox（message_delta 增量 + message_created 完整消息）
          → Outbox Worker → Redis Pub/Sub → WebSocket → 前端实时收到回复
```

三条关键约束：

1. **事实驱动**：AI 通过只读 Tool 查询 Tourism Service 的真实数据，不靠模型凭空生成；
2. **写操作受控**：AI 绝不直接退款、取消预订、修改出行人，只返回页面引导，由用户在前端确认后调用 Tourism Service 写接口；
3. **AI 优先、人工兜底**：AI 无法可靠处理时转人工，本服务创建工单并实时通知管理端接入。

## 进程模型

本服务由三个进程组成（共享 PostgreSQL 状态，职责独立）：

1. **API 进程**：FastAPI，承接会话、消息、工单请求；
2. **AI Worker**：领取到期 Turn，调用 AI Service（消费 NDJSON 流式），把结果与任务状态写回；
3. **Outbox Worker**：读取 `realtime_outbox`，经 Redis Pub/Sub → WebSocket 推送前端。

## 技术栈

- Python 3.12+ / uv 包管理
- FastAPI + Uvicorn、Pydantic 2 + pydantic-settings
- SQLAlchemy 2（AsyncSession）+ PostgreSQL + psycopg 3
- PyJWT（三服务共享密钥互认）、Redis AsyncIO（Pub/Sub）、WebSocket、HTTPX（调用 AI Service，流式）

## 项目结构

```text
customer-service/
├── .env                  # 本地环境变量（不提交）
├── .env.example          # 环境变量模板
├── pyproject.toml
├── uv.lock
└── atguigu/
    ├── main.py           # API 进程入口
    ├── common/           # 配置、事件循环
    ├── infrastructure/   # 数据库引擎、会话工厂、建表（含 task_state 列）
    ├── models/           # ORM 模型（Conversation 含 task_state JSONB）
    ├── worker/
    │   ├── ai/           # AI Worker：gateway / parser / result / worker
    │   └── realtime.py   # Outbox Worker
    └── app/
        ├── app.py            # FastAPI 应用实例 + 路由注册 + CORS
        ├── dependencies.py   # 依赖注入组装
        ├── schemas/          # 请求与响应模型
        ├── services/         # 业务规则与流程编排（含实时事件）
        ├── routers/          # 接口路由
        └── repositories/     # 数据访问
```

## 快速开始

### 前置条件

- 本地：Python 3.12+、[uv](https://docs.astral.sh/uv/)
- 虚拟机：PostgreSQL（`5432`，含 `customer_service` 库）、Redis（`6379`）、Tourism Service（`8001`）
- AI Service（`8002`）已启动

### 启动

```bash
# 1. 复制环境变量模板并按需修改（数据库/Redis 地址指向你的虚拟机 IP）
cp .env.example .env

# 2. 安装依赖
uv sync

# 3. 建表（首次或结构升级后执行，幂等）
uv run python -m atguigu.infrastructure.init_db

# 4. 三个进程分别启动
uv run python -m atguigu.main            # API 进程（8000）
uv run python -m atguigu.worker.ai.worker  # AI Worker
uv run python -m atguigu.worker.realtime   # Outbox Worker
```

启动后访问 <http://127.0.0.1:8000/docs> 查看接口文档。

### 联调测试

1. 用 Tourism Service 签发 Demo 用户令牌：`POST http://<虚拟机IP>:8001/api/v1/auth/demo-token`
2. 携带 `Authorization: Bearer <token>` 调用客服接口，例如获取当前会话：
   `POST http://127.0.0.1:8000/api/v1/conversations/current`
3. 发送消息：`POST http://127.0.0.1:8000/api/v1/chat/messages`
   ```json
   {"message_id": "m_001", "type": "text", "content": {"text": "帮我查订单 ORD_SANYA_001 的状态"}}
   ```

> 注意：`.env` 中的 `JWT_SECRET` / `JWT_ALGORITHM` 必须与 Tourism Service、AI Service 一致，否则令牌无法互认。

## 多轮任务状态（TaskState）

- `Conversation.task_state`（JSONB）保存多轮任务的 active / stack / history；
- Customer Service 只做**不透明存取**与版本校验，任务语义由 AI Service 负责；
- AI 成功结算时与消息在同一事务原子写回；会话空闲超时关闭时清空；
- `POST /api/v1/conversations/current` 返回 `current_task`（task_type / status / slots / missing_slots）。

## 实时推送与流式

- 事件统一走 Outbox → Redis → WebSocket；
- 事件类型：`message_delta`（token 增量，打字机效果）、`message_created`（完整消息）、`handoff_changed`（工单状态）；
- 仅“直接完成”的文本回复下发增量；页面引导/转人工在确认后再发完整消息。

## 环境变量说明

| 变量 | 说明 | 示例 |
|------|------|------|
| `DATABASE_URL` | 客服库连接串（异步 psycopg） | `postgresql+psycopg://customer_service:customer_service@192.168.200.188:5432/customer_service` |
| `REDIS_URL` | Redis 连接（仅 Pub/Sub） | `redis://192.168.200.188:6379/0` |
| `AI_SERVICE_URL` | AI Service 地址 | `http://127.0.0.1:8002` |
| `JWT_SECRET` / `JWT_ALGORITHM` | JWT 密钥/算法，三服务保持一致 | `ecommerce-secret` / `HS256` |
| `API_HOST` / `API_PORT` | 服务监听地址与端口 | `0.0.0.0` / `8000` |
| `CORS_ORIGINS` | 允许的前端跨域来源 | `["http://localhost:5173", ...]` |

## 相关文档

- `AGENTS.md` — 架构约束、业务红线与开发规范（AI 编码助手必读）
- 设计文档：仓库根目录 `客服项目改造设计.md`、`任务状态与多轮槽位设计.md`

---
*旅游智能客服项目，用于演示“AI 优先、人工兜底、关键操作由用户确认”的完整协作流程。*
