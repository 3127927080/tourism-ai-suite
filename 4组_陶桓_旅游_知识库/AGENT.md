# AGENT.md

本文件为在 `kb_0515` 项目中协作的 AI Agent 提供上下文说明，帮助其快速理解项目结构、约定和工作方式。

## 项目概述

GraphRAG 智能问答系统：基于知识图谱（Neo4j）+ 向量检索（Milvus）的企业级文档问答平台。除传统向量检索的"是什么"类问题外，还能回答"A 和 B 有什么关系""A 包含哪些部件"等关系型问题。目前支持商品说明书领域的知识库构建与问答。

技术栈：Python 3.12+ / FastAPI / LangGraph / LangChain / Milvus / Neo4j / MongoDB / MinIO / BGE-M3 (Embedding+Rerank) / DashScope(阿里云) LLM & MCP。

## 项目结构

```
atguigu/
├── config/                  # 全局配置（config.py 读取 .env，prompt.py 存放提示词）
├── import_process/          # 存库流程（Ingestion Graph，LangGraph 状态图）
│   ├── base.py               # NodeBase 抽象基类，统一节点执行/日志/耗时统计
│   ├── state.py               # ImportGraphState（TypedDict）
│   ├── import_main_graph.py  # 图的节点与边的组装、编译、运行入口
│   └── nodes/                # 各处理节点：PDF转MD、图片理解、切片、主体识别、
│                              # 向量化、Milvus入库、三元组提取、Neo4j入库
├── query_process/           # 查询流程（Query Graph）
│   ├── base.py / state.py（QueryGraphState）/ query_main_graph.py
│   └── nodes/                # 意图确认、向量检索、HyDE检索、图检索、网络搜索、
│                              # RRF融合、Rerank精排、答案生成
├── tool/                    # 公共工具：各中间件客户端(Milvus/Neo4j/Mongo/MinIO)、
│                              # embedding/rerank 工具、任务状态管理、日志、JSON格式化
├── web/api/                 # FastAPI 接口：import_service.py（存库）、query_service.py（查询）
└── test / test_fastapi/     # 脚本级测试与 FastAPI 用法练习
```

## 核心架构：两条 LangGraph 状态图

### 1. 存库流程（`import_main_graph.py`，状态 `ImportGraphState`）

`NodeEntry → (MD/PDF分支) → NodeMDImg → NodeDocumentSplit → NodeItemNameRecognition → [NodeBGEEmbedding → NodeImportMilvus] 并行 [NodeGraphExtract → NodeGraphImport]`

- 入口按 `is_md_read_enabled`/`is_pdf_read_enabled` 条件路由。
- 主体识别之后分叉：一路做向量化入 Milvus，另一路做三元组抽取入 Neo4j。

### 2. 查询流程（`query_main_graph.py`，状态 `QueryGraphState`）

`NodeItemNameConfirm →`（若已直接得到答案则跳到输出，否则并行触发）`[NodeSearchEmbedding, NodeSearchEmbeddingHyde, NodeWebSearchMcp, NodeSearchGraph] → NodeRrf → NodeRerank → NodeAnswerOutput`

- 图检索节点 `node_search_graph.py` 是较新加入的关系检索能力，负责实体识别、Neo4j 图查询并输出 `graph_results`。
- RRF 融合多路召回结果，Rerank 精排后生成最终答案。

## 关键约定

- **节点开发模式**：所有节点继承 `NodeBase`（`import_process/base.py` 与 `query_process/base.py`），必须设置类属性 `name`，实现 `process(self, state)`。`__call__` 统一负责日志打印、任务状态更新（`task_utils.py`）、耗时统计和异常捕获，新节点无需重复这些逻辑。
- **状态管理**：图状态用 `TypedDict` 定义（`ImportGraphState` / `QueryGraphState`），所有节点读写同一个 dict，字段含义集中写在 state.py 的注释里，新增字段务必补充注释。
- **配置管理**：`config/config.py` 按用途拆成多个 Config 类（`LLMConfig`、`MilvusConfig`、`Neo4jConfig`、`MongoConfig`、`MinIoConfig`、`EmbeddingConfig`、`RerankConfig`、`McpConfig`），统一从 `.env` 读取，新增配置项要同步更新 `.env.example`（注意用占位符，不写真实密钥）。
- **异步任务与 SSE**：`web/api/` 下的接口用 `BackgroundTasks` 触发图执行，通过 `task_utils.py` 的队列机制 + `/stream/{task_id}` SSE 接口向前端推送进度和最终结果。
- **多路召回融合**：查询侧同时用普通向量检索、HyDE 检索、图检索、网络搜索四路召回，RRF 融合后再 Rerank，是本项目区别于普通 RAG 的核心设计。

## 运行与调试

- 依赖管理用 `uv`（`pyproject.toml` + `uv.lock`），新增依赖用 `uv add xxx`。
- 各 `xxx_main_graph.py` 文件都有 `if __name__ == "__main__":` 可直接跑通整条图链路进行调试。
- 启动 API：`atguigu/web/api/import_service.py`（存库接口，端口 8004）、`query_service.py`（查询接口，端口 8003）、`knowledge_service.py`（商品知识检索，端口 8005）。
- 依赖中间件：Milvus、Neo4j、MongoDB、MinIO，需提前用 Docker 等方式启动，并在 `.env` 中配置连接信息。

## 给 Agent 的注意事项

- 修改/新增节点时，先看同目录下已有节点的写法（如 `node_search_graph.py` 是最近新增的参考范例），保持风格一致。
- 涉及 `.env` 中的密钥、Token（`MINERU_TOKEN`、`ALIYUN_API_KEY`、`MINIO_SECRET_KEY` 等），只能引用变量名，不要读取或回显真实值。
- 图的节点/边一旦调整，务必同步检查 `after_entry_router` / `after_confirm_router` 等条件路由函数，避免状态机出现死路径。
- 项目当前处于持续迭代中（图检索、RRF、Rerank 相关节点近期改动频繁），改动前建议先跑一次对应 `main_graph.py` 的 `__main__` 示例，确认现状行为。
