# 4组_陶桓_实战 · 旅游智能客服三项目

把三个课程既有项目统一到「旅游」业务下的实战交付：

- **旅游知识库（GraphRAG）**：旅游文档入库、多路检索、知识问答
- **旅游智能客服**：会话中枢 + AI 编排 + 旅游业务数据服务
- **三元组抽取微调**：用旅游语料微调 Qwen3（1.7B/4B），并与直调大模型做量化对比，产物以 8006 服务接入入库链路

工作方式：**源仓库只读，代码复制到本目录下的三个新项目目录后再改造**，绝不直接修改原仓库。

仓库：[Gitee](https://gitee.com/tao-huan333/tourism-ai-suite) · [GitHub](https://github.com/3127927080/tourism-ai-suite)

---

## 目录结构

```text
4组_陶桓_实战/
├── 4组_陶桓_旅游_知识库/        # 子项目一：GraphRAG 知识库（8003/8004/8005）
│   ├── atguigu/                 # 入库图、查询图、工具、FastAPI 接口
│   ├── eval/                    # Ragas 评估
│   ├── 旅游数据/                # 6 目的地 × 6 类 + 平台规则 = 41 份 Markdown
│   ├── tools/                   # 批量导入、集合检查、冒烟查询
│   └── README.md / AGENT.md
├── 4组_陶桓_旅游_客服/          # 子项目二：客服系统
│   ├── customer-service/        # 会话中枢（8000）
│   ├── ai-service/              # Agent 编排（8002）
│   ├── tourism-service/         # 旅游业务数据（8001，fork 自课程电商服务）
│   ├── 前端服务/                # user-frontend 5173 / admin-frontend 5174
│   └── 需求说明.md / 交付说明.md
├── 4组_陶桓_三元组提取微调/      # 子项目三：三元组抽取微调 + 8006 推理服务
│   ├── data/                    # 数据集与评估产物
│   ├── finetuned/               # LoRA adapter（不入库）
│   ├── scripts/triple_service.py# 8006 FastAPI 推理服务
│   ├── QuCo-extractor_*.py      # SFT / DPO / GRPO 训练脚本
│   ├── convert.py               # 数据格式转换
│   └── eval_triples.py          # 三元组评估
├── 联调证据/                    # 跨项目联调脚本与运行证据
├── 跨项目联调验收.py            # INT-01~04/06 自动化验收
└── *.md                         # 规划与设计文档（见下方「文档索引」）
```

---

## 系统架构

```text
User/Admin Frontend :5173 / :5174
        │ REST + JWT / WebSocket
        ▼
Customer Service :8000 ──NDJSON+Token──► AI Service :8002
        │ Outbox → Redis Pub/Sub                │ HTTP 内部 Token
        ▼                                       ▼
      WebSocket                       知识库 Knowledge :8005
                                                │
                            ┌───────────────────┼───────────────────┐
                            ▼                   ▼                   ▼
                        Milvus              Neo4j               Mongo/MinIO
                     (chunk 向量)        (实体关系图)        (历史/图片存储)

AI Service ──只读 REST──► Tourism Data Service :8001（酒店/景区/票务/订单/退款）

知识库 Import :8004 ──HTTP──► Triple Extractor :8006（微调模型，入库时抽取三元组）
```

### 服务与端口

| 服务 | 端口 | 职责 |
|---|---|---|
| Customer Service | 8000 | 会话、消息、Turn、工单、实时推送 |
| Tourism Data Service | 8001 | 旅游业务只读查询 + 受控写接口 |
| AI Service | 8002 | AgentRun 生命周期、Agent 执行、工具/知识调用 |
| 知识库 Query | 8003 | 对话式查询 + SSE 流式 |
| 知识库 Import | 8004 | 文档上传与入库 |
| 知识库 Knowledge | 8005 | 内部检索接口（只返回片段 + 元数据） |
| Triple Extractor | 8006 | 三元组抽取推理（Qwen3-1.7B + LoRA），供 8004 入库时调用 |

### 中间件

| 中间件 | 用途 |
|---|---|
| PostgreSQL | 会话/Turn/工单/Outbox；AgentRun/ToolCall（两库） |
| Redis | 仅 Pub/Sub 实时事件 |
| Milvus | 向量库：`chunks_tourism_v1`、`item_name_tourism_v1` |
| Neo4j | 知识图谱 `(:Entity)-[:RELATES {type}]->(:Entity)` |
| MongoDB | 知识库对话历史 `kb_message` |
| MinIO | 文档图片存储 |
| BGE-M3 | 本地 embedding（dense + sparse） |
| DashScope / DeepSeek | LLM、rerank、网页搜索、数据生成与评估 |

---

## 三个子项目

### 一、旅游知识库（`4组_陶桓_旅游_知识库`）

GraphRAG 文档问答。两条 LangGraph 状态图：

- **入库图**：`Entry → PDF/MD → 图片理解 → 元数据解析 → 切片 → 主体识别 → [BGE 向量化 → Milvus] ∥ [三元组抽取 → Neo4j]`
- **查询图**：`主体确认 → [向量 ∥ HyDE ∥ 图检索 ∥ 网络搜索] → RRF → Rerank → 答案生成(SSE)`

改造要点：元数据归一化（`content_type/city/scenic_name/route_name/hotel_name/restaurant_name/source_file`）、12 类关系词表三处同源、新 Milvus 集合。详见 [README](4组_陶桓_旅游_知识库/README.md)。

**启动**：`uv sync` → 复制 `.env.example` 为 `.env` → 启动 8004 入库 / 8005 检索 / 8003 查询。

### 二、旅游智能客服（`4组_陶桓_旅游_客服`）

| 服务 | 职责 | 绝不做的 |
|---|---|---|
| customer-service | 会话/消息/Turn 持久化、AI 调度、工单、实时推送 | 调用 LLM、执行业务写操作 |
| ai-service | AgentRun、Agent 执行、工具/知识调用、结果校验 | 持有完整会话、业务写操作 |
| tourism-service | 旅游业务数据只读查询 + 受控写接口 | 被 AI 直接调用写接口 |

- 两阶段确认：`start_run` → `run_decision_prepared` → CS 校验输入版本 → `confirm` / `cancel`
- AI 只读，退款/工单/取消/改出行人由前端确认后调用 tourism-service

### 三、三元组抽取微调（`4组_陶桓_三元组提取微调`）

**离线训练 + 在线推理服务**：

```text
旅游 41 份文档 / 1082 chunk
  → 数据飞轮 + LLM 标注（3 票一致）→ train/dev/sft/dpo/eval
  → SFT LoRA（1.7B / 4B，r=8、assistant_only_loss）
  → DPO 偏好对齐（1395 对）
  → eval_triples.py（exact_match / micro P/R/F1 / format_error_rate）
  → scripts/triple_service.py（8006，供知识库入库链路调用）
```

结论（详见 `实验报告.md`）：4B SFT/DPO 宽松 F1 约 **0.395–0.41**，为 DeepSeek 直调基线的 **65–67%**，但格式错误率低 5 倍、边际成本近零。1.7B → 4B 提升 12.8%。

知识库侧通过 `TRIPLE_EXTRACTOR_MODE=http` 切换到 8006；服务不可用时的 LLM 回退尚未实现。

---

## 快速开始

各子项目均为独立 `uv` 环境，**禁止合并虚拟环境**（transformers 版本冲突）。

```powershell
# 知识库（8003/8004/8005）
cd 4组_陶桓_旅游_知识库
uv sync
Copy-Item .env.example .env   # 填 ALIYUN_API_KEY / MINIO_* / MILVUS_URL / NEO4J_* / BGE_M3_PATH 等
uv run python -m atguigu.web.api.import_service    # 8004
uv run python -m atguigu.web.api.knowledge_service # 8005
uv run python -m atguigu.web.api.query_service     # 8003

# 三元组推理服务（8006，需 GPU；显存不足时 8003/8004 可设 BGE_DEVICE=cpu）
cd 4组_陶桓_三元组提取微调
python scripts/triple_service.py --port 8006

# 客服（三个服务各自独立）
cd 4组_陶桓_旅游_客服\customer-service ; uv sync ; Copy-Item .env.example .env
cd 4组_陶桓_旅游_客服\ai-service       ; uv sync ; Copy-Item .env.example .env
cd 4组_陶桓_旅游_客服\tourism-service  ; uv sync ; Copy-Item .env.example .env
# 启动顺序 8001 → 8002 → 8000(API + AI Worker + Outbox Worker)，详见 交付说明.md

# 前端
cd 4组_陶桓_旅游_客服\前端服务\user-frontend  ; npm install ; npm run dev                  # 5173
cd 4组_陶桓_旅游_客服\前端服务\admin-frontend ; npm install ; npm run dev -- --port 5174    # 5174

# 跨项目联调验收（需 8000/8001/8002/8005/8006 已启动）
python 跨项目联调验收.py
```

---

## 文档索引

| 文档 | 何时读 |
|---|---|
| `总开工包.md` | 开始任一窗口前（统一入口、开窗提示词） |
| `三项目实战计划.md` | 阶段路线图、依赖顺序、验收标准 |
| `概要设计总纲.md` | 架构、中间件、核心流程、开发规范 |
| `技术选型总表.md` | 版本、模型、存储、端口、环境隔离 |
| `知识库元数据与入库设计.md` | 知识库窗口 |
| `知识库RAG旅游化改造文档.md` | 知识库改造细化 |
| `客服项目改造设计.md` | 客服窗口 |
| `任务状态与多轮槽位设计.md` | 多轮任务与槽位 |
| `三元组项目改造设计.md` | 三元组窗口 |
| `跨项目验收清单.md` | 需求逐条映射、缺口、验收口径 |
| `联调报告.md` | 跨项目联调结果、证据、已知问题与复跑方式 |

---

## 开发规范

- **Git**：所有改动在 `dev` 分支进行并推送到 `origin/dev`（Gitee）；GitHub 镜像用 `git push github dev`；`main` 仅管理员可推送，合并走 PR。
- **分层**：`routers → services/编排 → repositories → models/infrastructure`，禁止反向依赖。
- **事务**：仓储只 `flush`/`add`，服务层统一 `commit`。
- **依赖注入**：只走 `dependencies.py`。
- **配置**：`pydantic-settings` / Config 类 + `.env`，密钥不硬编码、不回显。
- **Windows**：统一 `common/event_loop.py` 的 `run_async`，启动 `uv run python -m atguigu.main`。

## 当前进度

| 子项目 | 状态 |
|---|---|
| 旅游知识库 | **已完成**。41 份文档入库（1320 切片）；四路检索（向量/HyDE/图谱/网络）打通，图谱 381 节点/629 关系/12 类；50 题 Ragas faithfulness 0.79，10 题多路复跑 0.89 |
| 旅游智能客服 | **已完成**。三服务 + 双前端；TaskState 多轮槽位、token 级流式、退款/工单受控写接口；INT-02/03/04/06 端到端验证；规则评测 22 例通过 18（81.8%） |
| 三元组微调 | **已完成**。数据构建 / SFT / DPO / 评估 + 8006 推理服务；4B 宽松 F1 0.395–0.41（DeepSeek 基线 65–67%） |
| 跨项目联调 | **INT-01~06 全部通过**（2026-10-24），见 [联调报告.md](联调报告.md) |

已知限制与未覆盖项见 [联调报告](联调报告.md) 第五、六节。
