# 项目名称：GraphRAG 智能问答系统

## 项目简介

GraphRAG 智能问答系统是一个基于知识图谱的企业级文档问答平台。
系统能够理解文档中的实体关系，回答“A 和 B 有什么关系”、“A 包含哪些部件”等复杂问题，
弥补了传统向量检索只能回答“是什么”的局限性。

目前已支持商品说明书领域的知识库构建与智能问答。
## 快速开始
### 环境要求
- Python 3.12+
- Docker & Docker Compose

### 安装与配置

1. 克隆仓库
2. 安装依赖：`uv add ...`
3. 复制环境变量模板文件，并填入真实的配置信息：
   ```bash
   cp .env.example .env

### 启动服务
- 启动 Milvus、Neo4j、MongoDB 等中间件
- 启动 FastAPI 服务

# 项目结构

```text
kb_0515/
├── atguigu/                              # 核心代码目录
│   ├── config/                           # 配置文件
│   │   └── config.py                     # 全局配置类
│   │   └── prompt.py                     # 提示词配置类
│   ├── import_process/                   # 存库阶段（Ingestion Graph）
│   │   ├── base.py                       # 节点基类
│   │   ├── state.py                      # 状态定义
│   │   ├── import_main_graph.py          # 存库图构建
│   │   └── nodes/                        # 存库节点
│   │       ├── node_entry.py             # 任务分发
│   │       ├── node_pdf_to_md.py         # PDF转Markdown
│   │       ├── node_md_img.py            # 图片理解
│   │       ├── node_document_split.py    # 文档切片
│   │       ├── node_item_name_recognition.py  # 主体识别
│   │       ├── node_bge_embedding.py     # 向量化
│   │       ├── node_import_milvus.py     # Milvus入库
│   │       ├── node_graph_extract.py     # 三元组提取
│   │       └── node_graph_import.py      # Neo4j入库
│   ├── query_process/                    # 查询阶段（Query Graph）
│   │   ├── base.py
│   │   ├── state.py
│   │   ├── query_main_graph.py           # 查询图构建
│   │   └── nodes/                        # 查询节点
│   │       ├── node_item_name_confirm.py      # 意图确认
│   │       ├── node_search_embedding.py       # 向量检索
│   │       ├── node_search_embedding_hyde.py  # HyDE检索
│   │       ├── node_search_graph.py           # 图检索
│   │       ├── node_web_search_mcp.py         # 网络搜索
│   │       ├── node_rrf.py                    # RRF融合
│   │       ├── node_rerank.py                 # 精排
│   │       └── node_answer_output.py          # 答案生成
│   ├── tool/                             # 公共工具类
│   │   ├── milvus_client_tool.py         # Milvus连接
│   │   ├── neo4j_client_tool.py          # Neo4j连接
│   │   ├── mongo_client_tool.py          # MongoDB连接
│   │   ├── minio_client_tool.py          # MinIO连接
│   │   ├── embedding_utils.py            # 向量化工具
│   │   ├── rerank_tool.py                # Rerank工具
│   │   ├── task_utils.py                 # 任务状态管理
│   │   ├── logger.py                     # 日志工具
│   │   └── json_format_tool.py           # JSON格式化
│   ├── web/                              # FastAPI接口
│   │   └── api/
│   │       ├── query_service.py          # 查询接口
│   │       └── import_service.py         # 存库接口
│   └── test_fastapi/                     # API测试
├── .env.example                          # 环境变量模板
├── .gitignore
├── pyproject.toml                        # 项目依赖
└── uv.lock                               # 依赖锁定
```