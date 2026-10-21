# AI Service — 小满电商智能客服·AI 服务

小满电商智能客服的 AI 能力服务（学习项目）。它负责管理 AgentRun 的完整生命周期：接收 Customer Service 的调用请求，后续会驱动模型和工具执行，保存结果和监控数据，再把状态转换为统一事件返回给 Customer Service。

> 姊妹项目：Customer Service（`E:\LLM_student_project\customer-service`，独立仓库）。

## 系统中的位置

整套智能客服系统由两个前端和三个后端组成，本仓库只包含 AI Service：

| 项目 | 端口 | 职责 | 仓库 |
|------|------|------|------|
| Customer Service | 8000 | 会话/消息持久化、AI 调度、人工工单、实时通知 | `customer-service` |
| **AI Service（本仓库）** | **8002** | 管理 AgentRun 生命周期，后续驱动模型执行与结果生成 | `ai-service` |
| Ecommerce Service | 8001 | 电商业务数据源（课程提供，Docker 部署） | — |
| User Frontend / Admin Frontend | 5173 / 5174 | 用户端 / 客服管理端（课程提供） | — |

## 服务边界

1. **不持有完整会话**：AI Service 只保存 AgentRun 自己的输入快照和结果，不保存 Customer Service 的 Conversation 全量数据；
2. **不判断输入是否过期**：Customer Service 掌握会话的最新版本，由它决定某次 AI 结果是否仍然有效（调用 `confirm` 还是 `cancel`），AI Service 不重复做这个判断；
3. **不做电商业务写操作**：取消订单、修改地址、申请售后等操作，AI Service 只能在结果中返回操作引导，由用户在前端确认后调用 Ecommerce Service 执行。

## 调用流程

```text
Customer Service --start_run--> AI Service --run_decision_prepared/run_id--> Customer Service
Customer Service 比较 Turn 快照与 Conversation 当前版本
  输入仍然有效 --confirm_run--> AI Service --run_completed / run_handoff_requested--> Customer Service
  输入已过期   --cancel_run-->  AI Service --204 No Content--> Customer Service
```

## 技术栈

- Python 3.12+ / uv 包管理
- FastAPI + Uvicorn、Pydantic 2 + pydantic-settings
- SQLAlchemy 2（AsyncSession）+ PostgreSQL + psycopg 3
- PyJWT（与 Customer Service、Ecommerce Service 共享密钥互认）

> 当前不依赖 httpx、Redis：尚未接入真实模型调用或外部只读工具，也不接触 Pub/Sub。

## 项目结构

```text
ai-service/
├── .env                  # 本地环境变量（不提交）
├── .env.example          # 环境变量模板
├── pyproject.toml
├── uv.lock
└── atguigu/              # 代码包
    ├── main.py           # 进程启动入口
    ├── common/           # 配置、事件循环
    ├── infrastructure/   # 数据库引擎、Session 工厂
    ├── models/           # AgentRun 表结构
    ├── harness/          # Agent 执行与生命周期协调
    │   ├── agent/run/coordinator.py  # AgentRunCoordinator
    │   └── llm/                       # 模型调用封装（待实现）
    └── app/
        ├── app.py            # FastAPI 应用实例 + 路由注册 + CORS
        ├── dependencies.py   # 依赖注入组装
        ├── schemas/          # 请求与响应模型
        ├── services/         # AuthService 等横切服务
        ├── routers/          # 接口路由
        └── repositories/     # 数据访问
```

## 快速开始

### 前置条件

- 本地：Python 3.12+、[uv](https://docs.astral.sh/uv/)
- PostgreSQL 可用（本地或虚拟机），需要一个独立于 Customer Service 的数据库

### 启动

```bash
# 1. 复制环境变量模板并按需修改（数据库地址、密钥）
cp .env.example .env

# 2. 安装依赖
uv sync

# 3. 启动服务
uv run python -m atguigu.main
```

启动后访问 <http://127.0.0.1:8002/docs> 查看接口文档。

## 已实现接口

| 接口 | 方法 | 认证要求 | 当前状态 |
|------|------|----------|----------|
| `/internal/v1/agent/runs` | POST | 内部服务令牌 + customer 角色 JWT | 路由与认证已打通，`AgentRunCoordinator.start_run()` 目前返回占位空字典 `{}` |
| `/internal/v1/agent/runs/{run_id}/confirm` | POST | 内部服务令牌 + customer 角色 JWT | 同上，`confirm_run()` 返回占位空字典 |
| `/internal/v1/agent/runs/{run_id}/cancel` | POST，204 | 内部服务令牌 + customer 角色 JWT | 路由已声明，`cancel_run()` 尚未实现状态修改逻辑 |

## 与 Customer Service 联调注意事项

- `.env` 中的 `JWT_SECRET` / `JWT_ALGORITHM` 必须与 Customer Service、Ecommerce Service 保持一致；
- `INTERNAL_SERVICE_TOKEN` 必须与 Customer Service 侧配置的内部服务令牌一致，否则内部调用会被 `require_internal_service` 拒绝；
- Customer Service 的 `AIServiceGateway` 通过 `AI_SERVICE_URL` 指向本服务地址（默认 `http://127.0.0.1:8002`）。

## 环境变量说明

| 变量 | 说明 | 示例 |
|------|------|------|
| `AI_DATABASE_URL` | AI Service 数据库连接串（异步 psycopg） | `postgresql+psycopg://customer_service:customer_service@localhost:5432/ai_customer_service` |
| `API_HOST` / `API_PORT` | 服务监听地址与端口 | `0.0.0.0` / `8002` |
| `INTERNAL_SERVICE_TOKEN` | 内部服务令牌，须与 Customer Service 一致 | `customer-internal-token` |
| `JWT_SECRET` / `JWT_ALGORITHM` | JWT 密钥/算法，三服务保持一致 | `ecommerce-secret` / `HS256` |

> `common/config.py` 中还有 `llm_model`（默认空字符串）、`prompt_version`（默认 `customer-support-v1`），当前 `.env.example` 未列出，接入真实模型后应补充。

## 开发进度

- [x] 项目初始化：配置管理、异步数据库接入、服务启动器
- [x] JWT 认证：解析用户身份、限制角色为 customer；内部服务令牌校验
- [x] 三个内部接口的路由骨架（`/runs`、`/runs/{id}/confirm`、`/runs/{id}/cancel`）
- [ ] `AgentRunCoordinator` 具体业务逻辑（创建/查询/更新 AgentRun，状态机校验）
- [ ] `AgentRunRepository` 数据访问方法实现
- [ ] 依赖注入接入数据库 Session
- [ ] AgentExecutor：模型调用、Prompt 组装、只读工具调用
- [ ] 监控字段（Token 用量、耗时、错误）写入
- [ ] 与 Customer Service 完整联调闭环

## 相关文档

- `AGENTS.md` — 本项目的架构约束、服务边界与开发规范（AI 编码助手必读）

---
*课程学习项目，仅用于演示电商智能客服的完整协作流程，不是完整的生产级 AI 服务。*
