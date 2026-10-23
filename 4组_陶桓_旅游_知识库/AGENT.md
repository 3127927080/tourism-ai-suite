# AGENT.md — 旅游知识库（kb_0515）

本文件供 AI 编码助手在本目录开发时阅读，动手前请完整读完。

---

## 0. 工作方式（最重要）

**源仓库 `D:\pycharm-0515-th\kb_0515` 只读。所有改造都在本目录 `4组_陶桓_旅游_知识库` 内进行，只允许复制代码过来，绝不修改源仓库。**

- 环境隔离：本项目 `transformers>=4.45,<5.0`，**不得与三元组项目（transformers 5.2.0）共用 venv**；Ragas 评估需另建环境。
- Git：改动在 `dev` 分支提交并推 `origin/dev`；`main` 仅管理员可推，合并走 PR。

---

## 1. 项目定位

GraphRAG 旅游知识库。为客服系统（8002）提供 8005 检索接口，与三元组项目共用 12 类关系词表。三服务：8003 查询问答、8004 入库、8005 内部检索。

---

## 2. 代码地图与职责

| 路径 | 职责 |
|---|---|
| `atguigu/config/config.py` | 各类 Config；`TourismConfig`（目的地白名单/内容类型枚举/别名映射）、`RetrievalConfig`（检索开关）、`ImportConfig`（`UPLOAD_ROOT`、`enable_graph_extract`） |
| `atguigu/config/prompt.py` | 旅游域提示词：主体识别、三元组抽取、答案生成、元数据兜底、标题分类、预算归纳 |
| `atguigu/config/relation_schema.py` | `RELATION_TYPES` 12 类关系（**单一事实源**） |
| `atguigu/import_process/base.py` | `NodeBase`：`__call__` 统一日志/任务状态/耗时/异常 |
| `atguigu/import_process/state.py` | `ImportGraphState`，含 `doc_metadata` |
| `atguigu/import_process/import_main_graph.py` | 入库图组装与条件路由 |
| `nodes/node_metadata_parse.py` | **新增**：元数据归一、城市兜底、标题实体分类、线路预算 |
| `nodes/node_document_split.py` | 剔除元数据段、标题修复、元数据与实体透传 |
| `nodes/node_item_name_recognition.py` | `item_name=city`、别名行、按 `source_path` 幂等删除 |
| `nodes/node_import_milvus.py` | 新 schema、标量索引、字段投影、`source_path` 幂等删除 |
| `query_process/retrieval_main_graph.py` | 8005 检索子图，fanout 受开关控制 |
| `query_process/query_main_graph.py` | 8003 全链路，fanout 受开关控制 |
| `nodes/node_item_name_confirm.py` | 槽位提取（cities/entities/content_type/rewritten_query），返回 `parent_item_name` |
| `nodes/node_search_embedding.py` | 组合过滤 + 全局兜底 + `output_fields` |
| `nodes/node_rerank.py` | 合并时透传 `META_FIELDS`（否则 8005 拿不到引用字段） |
| `nodes/node_answer_output.py` | 答案末尾追加【参考来源】，不改 SSE 协议 |
| `web/api/*.py` | 8003 / 8004 / 8005 |
| `tools/` | `batch_import.py`、`inspect_milvus.py`、`smoke_query.py` |

---

## 3. 开发规范

- **节点模式**：继承 `NodeBase`，设置 `name`，实现 `process(self, state)`；日志、任务状态、耗时、异常由 `__call__` 统一处理，不要重复。
- **状态**：`TypedDict`，所有节点读写同一 dict；新增字段必须写注释。
- **配置**：集中在 `config/config.py` 的 Config 类，从 `.env` 读取；新增配置同步更新 `.env.example`（占位符，不写真实密钥）。
- **密钥**：只能引用变量名，不读取、不回显真实值。
- **改图必查路由**：调整节点/边后同步检查 `after_entry_router` / `after_item_name_router` / `after_confirm_router`，避免死路径。
- **异步与 SSE**：接口用 `BackgroundTasks` 触发图，`task_utils.py` 队列 + `/stream/{task_id}` 推送。

---

## 4. 关键约束

1. **新集合与旧集合并存**：用 `chunks_tourism_v1` / `item_name_tourism_v1`，旧 `kb_chunks` / `kb_item_names` 保留做回归，不删除、不写入。
2. **元数据缺失不阻断入库**：解析失败只记 warning 并降级（LLM 兜底 → 全局检索）。
3. **关系词表三处同源**：`relation_schema.RELATION_TYPES`、抽取 prompt、gold 标注必须一致（12 类）。
4. **图谱本期冻结**：默认 `ENABLE_GRAPH=false`，不重抽、不重建 Neo4j；`node_search_graph.py` 不得残留 HAK 180 特判。
5. **多城市用别名行解决**：chunk 的 `item_name` 单值；云南等多城市文档通过 `cities` 展开 + `parent_item_name` 归并，不复制 chunk。
6. **Milvus 字段长度按字节计**：中文约 3 倍，`content` / `metadata_json` 留 8000，超长截断并 warning。
7. **不改 8003 的 SSE 协议**：引用区块走 `delta` 事件追加。

---

## 5. 元数据归一规则（`node_metadata_parse`）

- `内容类型` → `content_type`：`美食介绍` 归一为 `美食推荐`；非枚举值记 warning 置 `未知`。
- `城市` / `目的地` → `city` / `cities`：含顿号/逗号拆入 `cities`；`云南多城市` 展开为 `["昆明","大理","丽江"]`；文件名前缀命中 6 目的地白名单时优先作为 `city`。
- `景点名称`→`scenic_name`；`线路名称`→`route_name`；`住宿名称`/`酒店名称`→`hotel_name`；`餐厅名称`→`restaurant_name`；`文档名称`（美食类）→`restaurant_name`，其余存 `metadata_json`。
- `duration_days`：从 `route_name` 正则取天数，范围取上限。
- `budget_ref`：仅线路类，LLM 归纳并标注「估算、不含往返大交通」，`estimated=true`。
- `entities`：LLM 分类 `###` 标题，过滤结构性标题（如「核心景点」「适合人群」「不同时长安排」），校验实体名在正文出现，去重，上限 50。

---

## 6. 常用命令

```powershell
uv sync
uv run python -m atguigu.web.api.import_service      # 8004
uv run python -m atguigu.web.api.knowledge_service   # 8005
uv run python -m atguigu.web.api.query_service       # 8003
uv run python tools/batch_import.py [--local|--dry-run|--only 线路]
uv run python tools/inspect_milvus.py
uv run python tools/smoke_query.py [--with-rerank|--with-llm|--all]
uv run python eval/eval.py --limit 50
```

各 `*_main_graph.py` 均有 `if __name__ == "__main__"`，可直接跑通整条图调试。

---

## 7. 常见坑

- **元数据在 rerank 丢失**：`node_rerank.py` 必须透传 `META_FIELDS`，否则 8005 无 `source_file/content_type`。
- **`content_type` 过滤过严**：过滤为空时自动回退全局检索并记日志。
- **`item_names` 为空**：`node_search_embedding.py` 去掉 expr 做全局检索，不抛异常。
- **`source_path` 才是幂等删除键**：不要按 `file_title` 删，会误删同城其他文档。
- **插入前字段投影**：MilvusClient 对多余字段敏感，`doc_metadata` 等必须丢弃。
- **代理劫持**：本机代理可能劫持 MinerU CDN，需 `NO_PROXY` / 强制直连。
- **`eval/qa.csv` 已是 50 题旅游 QA**（2026-10-22 替换）；旧电商产物在 `eval/archive_ecommerce/`。
- **图/网结果不带文档元数据**：`node_search_graph` 会按实体反查 Milvus 补 `source_file/city/content_type`，城市级实体走 `item_name` 兜底。
- **三元组实体必须原文出现**：prompt 已约束，`node_graph_extract` 另有代码兜底（`dropped_hallucination`），改 prompt 时别把这条约束删掉。

---

## 8. 当前状态

- ✅ 入库链路全部节点已改造；36 份文档入库，`chunks_tourism_v1` 1271 条，`item_name_tourism_v1` 219 行别名。
- ✅ 查询侧四路已打通：向量（稠密+稀疏）/ HyDE / 图谱 / MCP 网络搜索，`smoke_query.py --with-rerank --with-routes` 10/10 PASS。
- ✅ 图谱已重建：旧电商图（3049 节点 / 5075 关系 / 721 类）清空，重建后 381 节点 / 629 关系 / 12 类；小样本六项硬指标 6/6 PASS。
- ✅ Ragas：50 题基线（10-22）+ 10 题多路对比（10-23），见 `eval/REPORT.md` §11。
- ⚠️ 保持分层融合：图/网不进 RRF，各自截断后在 Rerank 汇合；名额走 `RRF_TOP_N` / `GRAPH_TOP_N` / `WEB_TOP_N`。

---

## 9. 文档索引

| 文档 | 用途 |
|---|---|
| `../知识库RAG旅游化改造文档.md` | **主**：文件级改造清单、8005 契约、任务清单、实施进度 |
| `../知识库元数据与入库设计.md` | 元数据 schema、入库图改造 |
| `../跨项目验收清单.md` | KB-01~31 验收项 |
| `../技术选型总表.md` | 版本、端口、环境隔离 |
| `../概要设计总纲.md` | 三项目架构与统一规范 |
| `README.md` | 项目说明与启动方式 |
