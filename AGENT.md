# AGENT.md — 旅游智能客服三项目协作指南

本文件供 AI 编码助手在 `C:\Users\th\Desktop\4组_陶桓_实战` 目录下开发时阅读，动手前请先完整读完。

---

## 0. 最重要的工作方式

**源仓库只读。所有改造都在本目录下的三个新项目目录内进行；只允许复制代码过来，绝不修改原仓库。**

| 新项目（工作副本） | 只读源仓库 |
|---|---|
| `4组_陶桓_旅游_知识库` | `D:\pycharm-0515-th\kb_0515` |
| `4组_陶桓_旅游_客服\customer-service` | `E:\LLM_student_project\customer-service` |
| `4组_陶桓_旅游_客服\ai-service` | `E:\LLM_student_project\ai-service` |
| `4组_陶桓_旅游_客服\tourism-service` | `E:\项目一_电商小二\...\后端电商服务部署\ecommerce-service` |
| `4组_陶桓_三元组提取微调` | `D:\pycharm-0515-th\LLM_student\src\test\day07` |

复制时排除 `.git`、`.venv`、`__pycache__`、`node_modules`；只复制 `.env.example`，不复制真实 `.env`。

---

## 1. 项目地图

```text
4组_陶桓_旅游_知识库/      GraphRAG 知识库（8003 查询 / 8004 入库 / 8005 内部检索）
4组_陶桓_旅游_客服/
  ├─ customer-service/     会话中枢（8000）
  ├─ ai-service/           AgentRun 编排（8002）
  └─ tourism-service/      旅游业务数据（8001，包名仍为 ecommerce_service，待 fork）
4组_陶桓_三元组提取微调/   离线训练（QuCo-extractor SFT/DPO）
根目录 *.md                规划与设计文档
```

各子项目已有自己的 `README.md` / `AGENT(S).md` / `.claude/skills/*`，在子项目内工作时优先阅读对应文件。

---

## 2. 硬性约束（不可违反）

1. **源仓库只读**：只复制，不修改。
2. **环境隔离**：kb_0515（transformers `<5.0`）与三元组项目（transformers 5.2.0）**不得共用 venv**；评估依赖（ragas、deepeval）单独 venv。
3. **AI 只读**：ai-service 不调用任何写接口；退款/工单/取消/改出行人由前端确认后调用 tourism-service。
4. **不改 customer-service 状态机**：Conversation/Turn/Worker 领取逻辑保持原样。
5. **TaskState 分层**：CS 只存不透明 JSON（`Conversation.task_state` JSONB），语义与 Reducer 归 AI。
6. **旧数据保留**：新 Milvus 集合（`chunks_tourism_v1` / `item_name_tourism_v1`）与旧集合并存，先回归再切换。
7. **三元组模型进程隔离**：不 import 进知识库查询进程；走离线批脚本或 8006 服务。
8. **关系词表三处同源**：抽取 Prompt / `RELATION_TYPES` / gold 标注必须一致（12 类）。
9. **数据缺口延后**：文化民俗、线路预算、适合人群/最佳游览时间不在本期验收。
10. **Git**：改动在 `dev` 分支提交并推 `origin/dev`；`main` 仅管理员可推，合并走 PR。

---

## 3. 共享配置与契约（必须三处一致）

| 项 | 值 |
|---|---|
| `JWT_SECRET` / `JWT_ALGORITHM` | `ecommerce-secret` / `HS256`（三服务共享互认） |
| `INTERNAL_SERVICE_TOKEN` | `customer-internal-token`（CS → AI） |
| `KNOWLEDGE_SERVICE_TOKEN` | `knowledge-internal-token`（AI → 8005） |
| 数据库 | `customer_service` / `ai_customer_service` / `tourism` |
| Milvus 集合 | `chunks_tourism_v1` / `item_name_tourism_v1` |
| 业务对象消息 | `{type, id, fields}` |
| 时间 | UTC 存储，前端本地化 |

---

## 4. 各项目技术要点

### 知识库（kb_0515）

- Python 3.12 / FastAPI / LangGraph 1.2 / LangChain 1.3 / BGE-M3 / Milvus / Neo4j / Mongo / MinIO / DashScope。
- **节点开发模式**：所有节点继承 `NodeBase`（`import_process/base.py` / `query_process/base.py`），设置 `name`，实现 `process(state)`；`__call__` 统一处理日志、任务状态、耗时、异常。
- 状态用 `TypedDict`（`ImportGraphState` / `QueryGraphState`），新增字段必须补注释。
- 配置集中在 `config/config.py` 的 Config 类；提示词在 `config/prompt.py`；关系词表在 `config/relation_schema.py`。
- **改图节点/边后，必须同步检查条件路由**（`after_entry_router` / `after_confirm_router`），避免死路径。
- 已完成的改造：元数据解析节点 `node_metadata_parse.py`、`TourismConfig`、新集合名、关系 schema。
- 查询侧四路召回 + RRF + Rerank 是本项目核心；8005 只返回片段，不做网络搜索与答案生成。

### 客服（customer-service + ai-service + tourism-service）

- customer-service：FastAPI + SQLAlchemy 2（AsyncSession）+ PostgreSQL + Redis Pub/Sub + WebSocket；三进程（API / AI Worker / Outbox Worker）。**基本零改动**。
- ai-service：Harness 分层 `AgentRunCoordinator → AgentExecutor → (ContextCompiler / ModelAdapter / Validator / Rules)`；Agent 用 `create_agent` + `ToolStrategy(AgentOutput)` + `ToolCallLimitMiddleware` + `SkillScopeMiddleware`。
  - 工具分类：`BUSINESS`（只读业务查询）/ `KNOWLEDGE`（`search_knowledge` → 8005）/ `GUIDANCE`（`load_skill`）。
  - 两阶段确认：`start_run` → `run_decision_prepared` → CS 校验版本 → `confirm` / `cancel`。
  - 依赖注入只走 `dependencies.py`；repo 不 `commit`，Coordinator/Service 入口统一 `commit`。
  - **注意**：`ai-service/AGENTS.md` 的「待实现清单」已过时，以代码为准。
- tourism-service：fork 课程电商服务，保留 JWT、幂等键、Docker、种子模式；业务实体改酒店/景区/票务/接送/订单/退款；库名 `tourism`；端口 8001。当前包名仍为 `ecommerce_service`（未完成 fork）。

### 三元组微调（day07）

- 脚本风格：顶部 import + 常量，底部 `#1. #2.` 分节平铺；相对路径；TensorBoard 记 `logs/`。
- 训练：LoRA r=8、`assistant_only_loss=True`、`new_chat_template.jinja`；基座 `model/QuCo-extractor-0.5B`。
- 训练格式：`messages=[system,user,assistant]`，assistant 为多行 `(h, r, t)`，无三元组写 `无三元组`。
- 评估：`eval_triples.py --manifest <gold>.jsonl --responses a.jsonl b.jsonl ...`，输出 exact_match / micro P/R/F1 / format_error_rate。
- GPU：BGE-M3 与 QuCo-0.5B 尽量分卡；单卡则串行。

---

## 5. 常用命令

```powershell
# 知识库
cd 4组_陶桓_旅游_知识库
uv sync
uv run python -m atguigu.web.api.import_service      # 8004
uv run python -m atguigu.web.api.knowledge_service   # 8005
uv run python -m atguigu.web.api.query_service       # 8003
uv run python tools/batch_import.py                  # 批量导入 36 份文档
uv run python tools/inspect_milvus.py                # 检查集合
uv run python tools/smoke_query.py                   # 冒烟查询

# 客服 / AI（各自目录内）
uv run python -m atguigu.main                        # customer-service 8000 / ai-service 8002
uv run python eval/run_eval.py                       # ai-service 评测

# 三元组
uv run python convert.py
uv run python eval_triples.py --manifest <gold>.jsonl --responses <r1>.jsonl <r2>.jsonl
```

---

## 6. 常见坑

- **transformers 冲突**：kb 与三元组项目环境绝不能合并。
- **Milvus `VARCHAR.max_length` 按字节计**：中文按 3 倍估算。
- **`import_service.py` 的上传目录**：已改为 `ImportConfig.upload_root`（默认项目内 `output/`），不再硬编码 `D:\output`。
- **`NodeItemNameConfirm` 写 Mongo 伪历史**：检索侧 `session_id` 加 `aisvc-kb-` 前缀。
- **关系词表漂移**：新增关系必须同时改抽取 prompt、`RELATION_TYPES`、gold。
- **旧 HAK 商品数据污染**：用新集合，旧库保留做回归。
- **Ragas 依赖**：必须独立 venv（需 `langchain-community<0.4`）。
- **文档与代码不一致**：ai-service `AGENTS.md`、customer-service `README.md` 的进度描述偏旧，以代码为准。

---

## 7. 文档索引

| 文档 | 用途 |
|---|---|
| `总开工包.md` | 统一入口、开窗提示词、全局检查单 |
| `三项目实战计划.md` | 阶段路线图与依赖、验收标准 |
| `概要设计总纲.md` | 架构、中间件、核心流程、开发规范 |
| `技术选型总表.md` | 版本、模型、存储、端口、环境隔离 |
| `知识库元数据与入库设计.md` / `知识库RAG旅游化改造文档.md` | 知识库改造 |
| `客服项目改造设计.md` / `任务状态与多轮槽位设计.md` | 客服改造与多轮槽位 |
| `三元组项目改造设计.md` | 三元组改造 |
| `跨项目验收清单.md` | 需求逐条映射、缺口、验收口径 |

---

## 8. 当前实现进度

| 子项目 | 状态 |
|---|---|
| 旅游知识库 | 导入与查询链路改造完成；36 份文档入库（`chunks_tourism_v1` 1271 条）；查询链路 8 项冒烟验证通过；待换 50 题旅游 QA 并跑 Ragas |
| 旅游智能客服 | 代码已复制；`ai-service` 工具仍为电商语义（`search_products` 等）、`tourism-service` 仍为 `ecommerce_service` 包，旅游化未开始 |
| 三元组微调 | 数据集（train/dev/sft/dpo/eval）与训练评估脚本齐备，已有 SFT/DPO 结果产物 |
