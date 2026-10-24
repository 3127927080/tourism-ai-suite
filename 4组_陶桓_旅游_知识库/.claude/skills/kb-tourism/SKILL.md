---
name: kb-tourism
description: 旅游知识库（4组_陶桓_旅游_知识库）的开发、运行与排障 Skill。当在本目录改造入库链路、元数据解析、实体抽取、Milvus 集合、8003/8004/8005 接口、PDF/Markdown 导入或排查导入失败时使用。
---

# 旅游知识库开发 Skill

## 一、基本事实

| 项 | 值 |
|---|---|
| 工作副本 | `C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库` |
| 源仓库（只读） | `D:\pycharm-0515-th\kb_0515` |
| 集合 | `chunks_tourism_v1` / `item_name_tourism_v1`（旧 `kb_chunks` / `kb_item_names` 保留对照） |
| 端口 | 8003 查询 / 8004 导入 / 8005 知识检索 |
| 数据 | `旅游数据/` 6 城市 × 6 类 = 36 份 Markdown |
| 中间件 | Milvus / Mongo / MinIO / Neo4j 在 VM `192.168.253.22` |

**本项目没有 `.venv`**。本地跑脚本用源仓库解释器：

```powershell
& "D:\pycharm-0515-th\kb_0515\.venv\Scripts\python.exe" <script>
```

## 二、入库链路

```text
Entry → PDF→MD（仅 pdf）→ MDImg → MetadataParse → DocumentSplit
      → ItemNameRecognition ─┬─→ BgeEmbedding → ImportMilvus
                             └─→ GraphExtract → GraphImport（需开开关）
```

开关（`.env`）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `ENABLE_GRAPH_EXTRACT` | false | 入库时抽三元组并写 Neo4j |
| `ENABLE_GRAPH` | false | 查询时启用 Neo4j 图检索 |
| `ENABLE_HYDE` | false | 查询时启用 HyDE |
| `ENABLE_WEB_SEARCH` | false | 8003 启用 MCP 网页搜索 |

**注意**：开 `ENABLE_GRAPH` 前必须先用旅游数据重建 Neo4j，否则召回的是旧商品图。

## 三、元数据规则（数据本身的坑）

1. `美食介绍` 要归一为 `美食推荐`（与需求枚举不一致）
2. `城市：云南多城市` 展开为 `昆明、大理、丽江`，`city` 仍为 `云南`
3. 元数据里的「景点名称/住宿名称」是**文档级名称**（如「三亚景点推荐」），不是真实景点
4. 真实实体必须从 `###` 标题用 LLM 抽取，且：
   - 类型白名单：景点/片区/古镇/酒店/餐厅/线路/**民俗项目**
   - 结构标题黑名单：第 N 层、核心景点、适合人群、优先级、景点安排、停留时长、
     决策点、文化线、体验方式、体验项目、礼仪、常见问题、目的地概览、可体验的
   - 实体必须出现在正文中（防幻觉）
5. `cities` 不含主城市；主城市单独存 `city`
6. 线路：`duration_days` 从「N 日」取上限；`budget_ref` 由 LLM 归纳并强制标注「估算」
7. 城市兜底必须校验正文出现，否则置空（避免非旅游文档被硬塞城市）
8. Milvus 里 `entities` 存**字符串数组**，便于 `json_contains`；类型信息在 `metadata_json`
9. 关系词表只有一处：`atguigu/config/relation_schema.py`

## 四、排障清单（真实踩过）

| 现象 | 根因 | 处理 |
|---|---|---|
| `InvalidAccessKeyId` | MinIO 密钥与 VM 不一致 | 更新 `.env`；md 已跳过对象存储，不阻断入库 |
| MinerU 下载 zip `SSL: UNEXPECTED_EOF` | 本机 `127.0.0.1:7890` 代理劫持 CDN TLS | `node_pdf_to_md.mineru_session()` 用 `trust_env=False`；`.env` 配 `NO_PROXY` |
| `Model not exist` | `qwen3-vl-flash` 不存在 | 视觉模型用 `qwen3.8-omni-flash`（qwen3-vl-* 免费额度已耗尽） |
| `insufficient_quota` 403 | 模型免费额度用尽 | 换有额度的模型，见上 |
| 前端「网络请求失败」 | 后端 500 被前端统一话术吞掉 | 上传页已显示真实 `HTTP 状态码 + 响应体` |
| 任务永远 `processing` | `NodeBase` 原实现吞异常 | 已改为记录后 `raise`，任务会标 failed 并带 traceback |
| 查询报 `invalid max query result window` | Milvus 查询上限 16384 | `tools/inspect_milvus.py` 已 clamp |
| `get_collection_stats` 恒为 0 | 该版本不刷新统计 | 用 `count(*)` 聚合 |
| 删除后条数不变 | Milvus 删除异步 | `flush()` 后再统计 |
| 单张图片失败导致整篇失败 | md_img 无容错 | 图片理解/上传失败只跳过该图 |
| 导入新文档导致旧文档图片失效 | 每次清空 `upload-images/` | 改为 `upload-images/<文档名>/` 按前缀删除 |
| 脚本 `can't open file` | cwd 在项目根，用了相对 `tools/...` | 用绝对路径调用 |
| 民俗文档抽不到实体 | 类型白名单无「民俗项目」 | 已加入白名单并重导 |

## 五、常用命令

```powershell
# 导入服务
& "<venv>\python.exe" -m atguigu.web.api.import_service      # 8004
& "<venv>\python.exe" -m atguigu.web.api.knowledge_service   # 8005
& "<venv>\python.exe" -m atguigu.web.api.query_service       # 8003

# 批量导入（离线直跑图，不依赖 8004）
& "<venv>\python.exe" tools/batch_import.py --local
& "<venv>\python.exe" tools/batch_import.py --local --only 文化民俗

# 只读检查库内数据
& "<venv>\python.exe" tools/inspect_milvus.py
```

## 六、验收自检

- [ ] 36 份文档全部 `completed`，`(city, content_type)` 6×6 无空洞
- [ ] 线路类 `duration_days>0` 且 `budget_ref` 含「估算」
- [ ] 别名行覆盖城市 + 真实实体，无「XX文化线」这类噪声
- [ ] 8005 无 token 401、有 token 200，返回含 `content_type/city/source_file`
- [ ] 旧集合 `kb_chunks` 未被修改
- [ ] 8003 回答末尾带【参考来源】，SSE 协议不变

## 七、工作纪律

- 源仓库只读，所有改动在本目录
- 改图节点/边后检查条件路由（`after_entry_router` / `after_item_name_router`）
- 关系词表只改 `config/relation_schema.py`，不要在别处复制一份
- 每次改动后 `py_compile` + 跑 `%TEMP%\kb_query_check.py` 自检
