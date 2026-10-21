---
name: tourism-ai-project
description: 旅游智能客服三项目（知识库 RAG、客服系统、三元组抽取微调）的项目上下文与实施规范。当在本目录下开发、改造、排查这三个项目，或需要查阅项目路径、文档索引、实施顺序、共享配置与验收口径时使用。
---

# 旅游智能客服三项目 · 项目上下文

## 项目概览

三个来自课程的既有项目统一到「旅游」业务下，先各自独立改造，再集成。

**工作方式：原仓库只读；代码复制到 `C:\Users\th\Desktop\4组_陶桓_实战` 下的三个新项目目录后再改造，绝不修改原仓库。**

| 项目 | 新项目（工作副本） | 只读源仓库 | 职责 |
|---|---|---|---|
| 知识库 RAG | `4组_陶桓_旅游_知识库` | `D:\pycharm-0515-th\kb_0515` | GraphRAG 旅游知识库：入库、检索、8003/8004/8005 |
| 客服（会话中枢） | `4组_陶桓_旅游_客服\customer-service` | `E:\LLM_student_project\customer-service` | 会话、Turn、消息、Outbox、WebSocket、工单（8000） |
| 客服（AI 编排） | `4组_陶桓_旅游_客服\ai-service` | `E:\LLM_student_project\ai-service` | AgentRun、工具、Skill、Validator（8002） |
| 旅游业务数据 | `4组_陶桓_旅游_客服\tourism-service` | `E:\项目一_电商小二\...\后端电商服务部署\ecommerce-service` | fork 为 tourism-service（8001） |
| 三元组微调 | `4组_陶桓_三元组提取微调` | `D:\pycharm-0515-th\LLM_student\src\test\day07` | QuCo-extractor 三元组抽取，SFT/DPO |

旅游数据：`C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库\旅游数据`
（6 目的地 × 5 类 = 30 份 Markdown）

需求文档：
- `4组_陶桓_旅游_知识库\需求说明.md`
- `4组_陶桓_旅游_客服\需求说明.md`

---

## 9 份规划文档（本目录）

| 文档 | 何时读 |
|---|---|
| `总开工包.md` | 开始任一窗口前 |
| `三项目实战计划.md` | 了解阶段与依赖 |
| `概要设计总纲.md` | 了解架构、中间件、规范 |
| `技术选型总表.md` | 配环境、选版本、查端口 |
| `知识库元数据与入库设计.md` | 做知识库窗口 |
| `客服项目改造设计.md` | 做客服窗口 |
| `任务状态与多轮槽位设计.md` | 做多轮任务 |
| `三元组项目改造设计.md` | 做三元组窗口 |
| `跨项目验收清单.md` | 验收与查缺口 |

---

## 实施顺序

```text
窗口一 知识库 → 窗口二 客服 → 窗口三 三元组
                 ↗（8005 可用即可开始）
窗口三可在关系 schema 冻结后与窗口二并行
```

---

## 共享配置与契约（必须一致）

- `JWT_SECRET=ecommerce-secret`，`JWT_ALGORITHM=HS256`
- `INTERNAL_SERVICE_TOKEN=customer-internal-token`（CS → AI）
- `KNOWLEDGE_SERVICE_TOKEN=knowledge-internal-token`（AI → 8005）
- 端口：8000 CS / 8001 tourism / 8002 AI / 8003-8005 KB / 8006 Triple
- 数据库：`customer_service`、`ai_customer_service`、`tourism`
- Milvus 集合：`chunks_tourism_v1`、`item_name_tourism_v1`
- 业务对象消息：`{type, id, fields}`
- 三元组关系：12 类，抽取 Prompt / `RELATION_TYPES` / gold 三处同源

---

## 硬性约束

0. **源仓库只读**：所有改造在新项目目录内进行；只允许复制，绝不在原仓库上直接修改。
1. **环境隔离**：kb_0515（transformers <5.0）与 LLM_student（transformers 5.2.0）不得共用 venv；评估依赖（ragas 0.4.3）单独环境。
2. **AI 只读**：AI 不调用写接口；退款/工单/取消/改出行人由前端确认后调用 tourism-service。
3. **不改 customer-service 状态机**：Conversation/Turn/Worker 领取逻辑保持。
4. **TaskState**：CS 只存不透明 JSON，AI 负责语义与 Reducer。
5. **旧数据保留**：新 Milvus 集合与旧集合并存，先回归再切换。
6. **三元组模型**：不 import 进知识库查询进程；离线批处理或 8006 服务。
7. **数据缺口延后**：文化民俗、线路预算、适合人群/最佳游览时间不在本期验收。

---

## 各窗口首批任务（速查）

### 知识库
修 `import_service.py` 的 `D:\output` → 新增 `NodeMetadataParse` → 改 `document_split` / `item_name_recognition` / `import_milvus` → 查询侧透传元数据 → 入库 30 份 → 跑 `eval/eval.py`。

### 客服
fork ecommerce-service → tourism-service（8001）→ ai-service 客户端/工具/模型/Skill/Prompt/规则旅游化 → P0/P1 收口 → TaskState → 业务对象 schema → token 级流式 → 评测。

### 三元组
冻结 12 类关系 → RAG 切片 + 通用数据 + 飞轮构造数据集 → 数据校验 → SFT（0.5B/1.7B/4B）→ model ladder 对比 → DPO → 离线建图 + 接入开关。

---

## 使用建议

- 开始任一窗口前，先读 `总开工包.md` 对应章节与该项目主设计文档。
- 动手前先跑一次现有冒烟测试，记录基线。
- 涉及跨项目契约变更时，同步更新 `概要设计总纲.md` 与 `跨项目验收清单.md`。
