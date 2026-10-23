# 4组_陶桓_旅游_知识库 · GraphRAG 旅游知识库

面向旅游场景的 GraphRAG 知识库：导入景区攻略、线路、酒店、美食、交通、文化民俗文档，提供**带元数据的多路检索**与**可追溯的知识问答**。

本目录是三项目实战中的**子项目一（旅游事实源）**，为客服系统提供 8005 检索接口，并与三元组项目共用一套旅游关系词表。

> 源仓库 `D:\pycharm-0515-th\kb_0515` **只读**；本目录是复制后的改造工作副本，所有改动都在这里进行。

---

## 1. 在整体系统中的位置

```text
AI Service :8002 ──X-Internal-Service-Token──► 知识库 Knowledge :8005（本服务）
                                                       │ 向量 + RRF + Rerank
                                              chunks_tourism_v1 / item_name_tourism_v1
                                                       │
                                    ┌──────────────────┼──────────────────┐
                                  Milvus             Neo4j             Mongo/MinIO
```

| 服务 | 端口 | 职责 |
|---|---|---|
| Query Service | 8003 | 对话式问答 + SSE 流式 + 历史记录 |
| Import Service | 8004 | 文档上传、入库任务状态 |
| Knowledge Service | 8005 | 内部检索接口，**只返回片段 + 元数据**，不做答案生成 |

---

## 2. 目录结构

```text
4组_陶桓_旅游_知识库/
├── atguigu/
│   ├── config/
│   │   ├── config.py            # LLM/Milvus/Mongo/MinIO/Embedding/Rerank/MCP/Neo4j 配置
│   │   │                        # + TourismConfig / RetrievalConfig / ImportConfig
│   │   ├── prompt.py            # 旅游域提示词（主体识别/三元组/答案/元数据兜底/标题分类/预算）
│   │   └── relation_schema.py   # 12 类旅游关系词表（单一事实源）
│   ├── import_process/          # 入库图（LangGraph）
│   │   ├── base.py              # NodeBase：统一日志/耗时/异常
│   │   ├── state.py             # ImportGraphState（含 doc_metadata）
│   │   ├── import_main_graph.py # 图节点与边组装
│   │   └── nodes/               # entry / pdf_to_md / md_img / metadata_parse /
│   │                            # document_split / item_name_recognition / bge_embedding /
│   │                            # import_milvus / graph_extract / graph_import
│   ├── query_process/           # 查询图（LangGraph）
│   │   ├── query_main_graph.py      # 8003 全链路（含网络搜索，可关）
│   │   ├── retrieval_main_graph.py  # 8005 检索子图（向量 + 可选 HyDE/图）
│   │   └── nodes/               # item_name_confirm / search_embedding / search_embedding_hyde /
│   │                            # search_graph / web_search_mcp / rrf / rerank / answer_output
│   ├── tool/                    # Milvus/Neo4j/Mongo/MinIO 客户端、embedding、rerank、任务、日志
│   └── web/api/                 # query_service(8003) / import_service(8004) / knowledge_service(8005)
├── tools/                       # batch_import / inspect_milvus / smoke_query
├── eval/                        # Ragas 评估（qa.csv / result.csv / eval.py）
├── 旅游数据/                    # 6 目的地 × 6 类 = 36 份 + 平台规则 5 份 Markdown
├── output/                      # 上传文件与解析产物（UPLOAD_ROOT）
├── 测试截图/
├── .env.example
├── pyproject.toml / uv.lock
└── README.md / AGENT.md
```

---

## 3. 核心链路

### 3.1 入库图

```text
Entry（md / pdf 分支）
  → PDF→MD / 图片理解
  → NodeMetadataParse      解析 ## 元数据 → 归一化 → 城市白名单兜底 → LLM 兜底
                            LLM 分类 ### 标题 → entities；线路类解析天数并归纳预算
  → NodeDocumentSplit      剔除元数据段；按标题切；500/50 细分；元数据与实体透传
  → NodeItemNameRecognition item_name = city；写城市行 + 城市别名行 + 实体别名行
  → NodeBGEEmbedding
  → NodeImportMilvus       chunks_tourism_v1；字段投影；source_path 幂等删除
  → （可选）NodeGraphExtract → NodeGraphImport   默认关闭
```

### 3.2 查询链路

**8005 检索子图**（客服使用，默认只开向量路）：

```text
NodeItemNameConfirm
  → LLM 提取 {cities, entities, content_type, rewritten_query}
  → 对齐 item_name_tourism_v1，得到 parent_item_name 集合（空 = 全局检索）
NodeSearchEmbedding
  → item_name in cities（为空不加 expr）
  → content_type == X（可选，可自动回退）
  → json_contains(entities, ...)（可选）
NodeRrf → NodeRerank（透传元数据）→ items[]
```

**8003 问答**：`主体确认 → 向量检索 → RRF → Rerank → 答案生成(SSE) → 追加【参考来源】`

### 3.3 检索开关

| 开关 | 默认 | 作用 |
|---|---|---|
| `ENABLE_HYDE` | `false` | 是否启用 HyDE 路 |
| `ENABLE_GRAPH` | `false` | 是否启用 Neo4j 图检索路 |
| `ENABLE_WEB_SEARCH` | `false` | 8003 是否启用 MCP 网络搜索 |
| `ENABLE_GRAPH_EXTRACT` | `false` | 入库时是否同时抽三元组入 Neo4j |

> 开关同时作用于 `retrieval_main_graph.py` 与 `query_main_graph.py`，关闭的节点不会被调用。

---

## 4. 元数据 Schema（`chunks_tourism_v1`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `title` / `file_title` / `content` / `part` | 原有 | 切片标题、文件名词干、正文、序号 |
| `item_name` | VARCHAR(100) | = `city`，查询过滤键 |
| `content_type` | VARCHAR(32) | 景点介绍/线路推荐/酒店信息/美食推荐/交通指南/文化民俗/**平台规则** |
| `city` / `cities` | VARCHAR | 主目的地 / 目的地列表（JSON） |
| `scenic_name` / `route_name` / `hotel_name` / `restaurant_name` | VARCHAR | 文档级名称字段 |
| `source_file` / `source_path` | VARCHAR | 来源文件名 / 相对路径（**幂等删除键**） |
| `theme` | VARCHAR | 主题 |
| `entities` | JSON | 实体名数组，用于 `json_contains` 过滤 |
| `entity_type` | VARCHAR | 主实体类型 |
| `duration_days` / `budget_ref` / `estimated` | INT/VARCHAR/BOOL | 线路天数、预算（估算标注） |
| `metadata_json` | VARCHAR(8000) | 全量归一结果、原始别名、告警 |
| `dense_vector` / `sparse_vector` | 1024 / SPARSE | BGE-M3 |

`item_name_tourism_v1` 存城市与实体的**别名行**：`item_name`（城市或实体名）→ `parent_item_name`（主目的地），按 `source_path` 幂等删除。

---

## 5. 快速开始

### 5.1 环境

```powershell
cd 4组_陶桓_旅游_知识库
uv sync
Copy-Item .env.example .env
```

`.env` 需配置：`ALIYUN_API_KEY` / `ALIYUN_API_BASE`、`MINIO_*`、`BGE_M3_PATH`、`MILVUS_URL`、`MONGO_URL`、`NEO4J_*`、`RERANK_*`、`MINERU_TOKEN`、`KNOWLEDGE_SERVICE_TOKEN`、`UPLOAD_ROOT`。

语言模型可独立于阿里云：设置 `LLM_API_KEY` / `LLM_API_BASE` 后，答案生成、槽位识别、元数据解析、三元组抽取与 Ragas 判分改走该供应商；留空则回退 `ALIYUN_*`。Rerank、视觉理解、MCP 网络搜索不受影响，仍走阿里云凭据。

前置中间件：Milvus、Neo4j、MongoDB、MinIO 全部连通；BGE-M3 可加载（GPU）。

### 5.2 启动服务

```powershell
uv run python -m atguigu.web.api.import_service      # 8004
uv run python -m atguigu.web.api.knowledge_service   # 8005
uv run python -m atguigu.web.api.query_service       # 8003
```

### 5.3 常用工具

```powershell
uv run python tools/batch_import.py                 # 调 8004 批量导入旅游数据
uv run python tools/batch_import.py --local         # 不走服务，直接跑入库图（需 GPU/LLM）
uv run python tools/batch_import.py --dry-run       # 只列出将导入的文件
uv run python tools/inspect_milvus.py               # 只读检查集合与数据量
uv run python tools/smoke_query.py                  # 检索层冒烟（城市/content_type/实体/回退）
uv run python tools/smoke_query.py --with-rerank    # 追加 RRF + Rerank 验证
uv run python tools/smoke_query.py --with-llm         # 追加槽位识别（LLM + Mongo）
uv run python eval/eval.py --limit 50               # Ragas 评估
```

---

## 6. 8005 接口契约

**请求**（旧调用 `{query, top_k}` 仍兼容）：

```json
{ "query": "三亚有哪些必去景点", "top_k": 5,
  "content_type": "景点介绍", "city": "三亚", "entities": ["亚龙湾"] }
```

**响应**：

```json
{ "items": [{
    "knowledge_id": "KB_4f3a9c2d1e8b", "title": "### 亚龙湾",
    "content": "……", "source": "知识库文档", "score": 0.92,
    "content_type": "景点介绍", "city": "三亚",
    "source_file": "三亚景点推荐.md", "source_path": "景点攻略/三亚景点推荐.md",
    "entities": ["亚龙湾"], "route_name": null,
    "duration_days": 0, "budget_ref": null, "estimated": null }],
  "message": "" }
```

鉴权：`X-Internal-Service-Token: <KNOWLEDGE_SERVICE_TOKEN>`，缺失或错误返回 401。

---

## 7. 当前进度

| 项 | 状态 |
|---|---|
| 入库链路改造 | ✅ 完成：元数据解析、切分、主体识别、Milvus 入库、新集合与标量索引 |
| 数据规模 | ✅ 36 份旅游文档（6 城市 × 6 内容类型）+ 5 份平台规则；`chunks_tourism_v1` **1320 条**、`item_name_tourism_v1` **224 行**别名 |
| 线路字段 | ✅ 6/6 有 `duration_days`、`budget_ref`、`estimated=true` |
| 查询链路改造 | ✅ 四路检索（稠密+稀疏 / HyDE / 图谱 / 网络）+ 分层融合；`tools/smoke_query.py --with-rerank --with-routes` 10/10 PASS（含平台规则断言） |
| `eval/qa.csv` | ✅ 55 题旅游 QA（5 类各 10 + 平台规则 5）；基线见 `eval/REPORT.md` |
| 图谱（Neo4j） | ✅ 已用旅游数据重建：381 节点 / 629 关系 / 12 类关系词；`ENABLE_GRAPH=true` |

---

## 8. 验收口径（本期）

1. 36 份旅游文档 + 5 份平台规则全部入库成功，`(city, content_type)` 统计无空洞；
2. 五类问题（景点/线路/酒店/美食/交通）各 2 条人工验证，返回片段带 `content_type/city/source_file`；平台规则类问题命中 `content_type=平台规则` 且不受城市过滤；
3. `eval/eval.py` 跑完，5 项指标写入 `result.csv`（当前基线 50 题 + 多路 10/5 题对比）；
4. 旧集合 `kb_chunks` / `kb_item_names` 未被修改；
5. 8003 `/query` + SSE、8004 `/upload` 行为不退化；
6. 8005 无 token 401、有 token 200，新旧请求均可用；
7. 四路检索（向量/HyDE/图谱/网络）各自可用，单路异常只降级该路；图谱关系全部落在 12 类内。

## 9. 本期不做

- ~~不重建 Neo4j 图谱~~ → 已用旅游数据重建（381 节点 / 629 关系 / 12 类）；
- 不接入三元组微调模型：抽取固定走 LLM 接口，代码中无 `TRIPLE_EXTRACTOR_MODE` 开关（原文档所记为规划笔误）；
- 不把「适合人群 / 最佳游览时间」做成结构化字段（适合人群取自正文，最佳游览时间由网页兜底）；
- 不改 8003 的 SSE 事件协议与 `/query` 请求结构。

## 10. 相关文档

- `../知识库RAG旅游化改造文档.md`（文件级改造清单与实施进度）
- `../知识库元数据与入库设计.md`（元数据 schema 与入库图改造）
- `../跨项目验收清单.md`（KB-01~31）
- `../技术选型总表.md`（版本与端口）
- `AGENT.md`（AI 协作约定与开发规范）
