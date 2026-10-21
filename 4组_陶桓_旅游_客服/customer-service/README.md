# Customer Service — 小满电商智能客服·会话服务

小满电商智能客服的客服会话服务（学习项目）。它是整个客服系统的**会话中枢**：向前承接用户端和管理端的请求，向后调度 AI Service，并通过 PostgreSQL、Redis 和 WebSocket 保证消息持久化与实时送达。

> 姊妹项目：AI Service（`E:\LLM_student_project\ai-service`，独立仓库，待课程推进后创建）。

## 系统中的位置

整套智能客服系统由两个前端和三个后端组成，本仓库只包含 Customer Service：

| 项目 | 端口 | 职责 | 仓库 |
|------|------|------|------|
| **Customer Service（本仓库）** | **8000** | 会话/消息持久化、AI 调度、人工工单、实时通知 | `customer-service` |
| AI Service | 8002 | 问题理解、只读工具、知识检索、回复校验 | `ai-service`（独立项目） |
| Ecommerce Service | 8001 | 电商业务数据源（课程提供，Docker 部署） | — |
| User Frontend / Admin Frontend | 5173 / 5174 | 用户端 / 客服管理端（课程提供） | — |

主链路：

```
用户发消息 → 本服务（保存消息 + 创建 Turn）
          → AI Worker → AI Service（NDJSON 流式，携带用户JWT + 内部服务令牌）
          → 回答 / 追问 / 操作引导 / 转人工
          → 本服务写 Outbox
          → Outbox Worker → Redis → WebSocket → 前端实时收到回复
```

三条关键约束：

1. **事实驱动**：AI 通过只读 Tool 查询 Ecommerce Service 的真实数据，不靠模型凭空生成；
2. **写操作受控**：AI 绝不直接取消订单、修改地址、申请售后，只返回操作引导，由用户在前端确认；
3. **AI 优先、人工兜底**：AI 无法可靠处理时转人工，本服务创建工单并实时通知管理端接入。

## 进程模型

本服务由三个进程组成（共享 PostgreSQL 状态，职责独立）：

1. **API 进程**：FastAPI，承接会话、消息、工单请求；
2. **AI Worker**：领取到期 Turn，调用 AI Service，把结果写回消息系统；
3. **Outbox Worker**：读取 `realtime_outbox`，经 Redis Pub/Sub → WebSocket 推送前端。

## 技术栈

- Python 3.12+ / uv 包管理
- FastAPI + Uvicorn、Pydantic 2 + pydantic-settings
- SQLAlchemy 2（AsyncSession）+ PostgreSQL + psycopg 3
- PyJWT（三服务共享密钥互认）、Redis AsyncIO（Pub/Sub）、WebSocket

## 项目结构

```text
customer-service/
├── .env                  # 本地环境变量（不提交）
├── .env.example          # 环境变量模板
├── pyproject.toml
├── uv.lock
└── atguigu/              # 代码包
    ├── main.py           # 进程启动入口
    ├── common/           # 配置、事件循环
    ├── infrastructure/   # 数据库引擎、会话工厂、建表
    ├── models/           # ORM 模型
    ├── worker/           # 工作流
    └── app/
        ├── app.py            # FastAPI 应用实例 + 路由注册 + CORS
        ├── dependencies.py   # 依赖注入组装
        ├── schemas/          # 请求与响应模型
        ├── services/         # 业务规则与流程编排
        ├── routers/          # 接口路由
        └── repositories/     # 数据访问
```

## 快速开始

### 前置条件

- 本地：Python 3.12+、[uv](https://docs.astral.sh/uv/)
- 虚拟机（Docker Compose）：Ecommerce Service（`8001`）、PostgreSQL（`5432`，含 `customer_service` 库）、Redis（`6379`），部署见课程《电商后台项目部署》文档

### 启动

```bash
# 1. 复制环境变量模板并按需修改（数据库/Redis 地址改为你的虚拟机 IP）
cp .env.example .env

# 2. 安装依赖
uv sync

# 3. 启动服务
uv run python -m atguigu.main
```

启动后访问 <http://127.0.0.1:8000/docs> 查看接口文档。

### 联调测试

1. 用电商服务签发 Demo 用户令牌：`POST http://<虚拟机IP>:8001/api/v1/auth/demo-token`
2. 携带 `Authorization: Bearer <token>` 调用客服接口，例如获取当前会话：
   `POST http://127.0.0.1:8000/conversations/current`

> 注意：`.env` 中的 `JWT_SECRET` / `JWT_ALGORITHM` 必须与 Ecommerce Service、AI Service 一致，否则令牌无法互认。

## 环境变量说明

| 变量 | 说明 | 示例 |
|------|------|------|
| `DATABASE_URL` | 客服库连接串（异步 psycopg） | `postgresql+psycopg://customer_service:customer_service@192.168.200.188:5432/customer_service` |
| `REDIS_URL` | Redis 连接（仅 Pub/Sub） | `redis://192.168.200.188:6379/0` |
| `AI_SERVICE_URL` | AI Service 地址 | `http://127.0.0.1:8002` |
| `JWT_SECRET` / `JWT_ALGORITHM` | JWT 密钥/算法，三服务保持一致 | `ecommerce-secret` / `HS256` |
| `API_HOST` / `API_PORT` | 服务监听地址与端口 | `0.0.0.0` / `8000` |
| `CORS_ORIGINS` | 允许的前端跨域来源 | `["http://localhost:5173", ...]` |

## 开发进度

- [x] 项目初始化：配置管理、异步数据库接入、服务启动器、JWT 认证
- [x] `POST /conversations/current` 最小响应接口（认证 → 服务 → 最小响应链路跑通）
- [ ] 会话/消息持久化、历史消息、发送消息、WebSocket 实时推送
- [ ] AI Worker、Outbox Worker、人工工单模块
- [ ] 与 AI Service、前端联调完整业务闭环

## 相关文档

- `AGENTS.md` — 本项目的架构约束、业务红线与开发规范（AI 编码助手必读）
- 课程资料：`day01_电商客服_项目背景以及架构.md`、`day01_客服服务项目初始化.md`、`电商后台项目部署.md`

---
*课程学习项目，仅用于演示电商智能客服的完整协作流程，不是完整电商交易平台。*
