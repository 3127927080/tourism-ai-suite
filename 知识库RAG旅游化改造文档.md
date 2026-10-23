# 知识库 RAG 旅游化改造文档（kb_0515 → 旅游域）

> 版本：v1 · 2026年10月21日
> 范围：仅 `4组_陶桓_旅游_知识库`（8003 / 8004 / 8005），不覆盖客服与三元组项目。
> 工作副本：`C:\Users\th\Desktop\4组_陶桓_实战\4组_陶桓_旅游_知识库`
> 源仓库：`D:\pycharm-0515-th\kb_0515`（只读，绝不修改）

---

## 0. 文档定位

本文是**文件级改造清单**：每个待改文件给出「现状 → 改法 → 关键片段 → 验收点」，开发时可直接对照执行。

与现有文档的关系：

| 文档 | 关系 |
|---|---|
| `知识库元数据与入库设计.md` | 本文在其基础上补齐**标题实体抽取、别名行、content_type 过滤、预算字段、8005 契约、任务清单**；冲突处以本文为准 |
| `三项目实战计划.md` | 本文只覆盖其中 P1（知识库旅游化），P2/P3/P4/P5 不在范围 |
| `跨项目验收清单.md` | 本文细化 KB-01~31 的落地方式与本期 DoD |

---

## 1. 现状盘点（已核对代码）

| 项目 | 现状 | 问题 |
|---|---|---|
| `config/prompt.py` | 商品名识别、HAK180 三元组抽取、商品问答 prompt | 全部需旅游化 |
| `config/config.py` | 集合名从 `.env` 读取，无目的地白名单、无检索开关 | 需新增 `TourismConfig` / `RetrievalConfig` / `ImportConfig` |
| `.env.example` | `CHUNKS_COLLECTION=kb_chunks`、`ITEM_NAME_COLLECTION=kb_item_names` | 与设计不一致，需改旅游集合名 |
| `node_document_split.py` | 按标题切分 + 500/50 细分；`title` 取值可能取到空行；`## 元数据` 会变成 chunk | 需剔除元数据段、修标题提取、透传元数据 |
| `node_item_name_recognition.py` | LLM 自由识别单个 `item_name`；按 `item_name` 删除；集合只有 `item_name/file_title/vector` | 改为优先取 `city`、写别名行、按 `source_path` 删除 |
| `node_import_milvus.py` | 只有 `title/file_title/item_name/content/part/vector`，直接插入整个 chunk 对象 | 需新 schema、标量索引、字段投影、`source_path` 幂等删除 |
| `node_item_name_confirm.py` | 商品名识别；0.85/0.60 对齐；失败直接返回「无法识别」 | 改旅游 prompt、输出城市+实体+内容类型、失败降级为全局检索 |
| `node_search_embedding.py` | `item_names` 为空直接抛异常；只按 `item_name` 过滤 | 需空值全局检索、`content_type`/`entities` 组合过滤、输出元数据 |
| `node_rerank.py` | 合并结果时只保留 `title/content/source/url` | 元数据在重排阶段被丢弃，8005 拿不到引用字段 |
| `retrieval_main_graph.py` | 强制三路 fanout：向量 + HyDE + 图 | 本期需可关闭 HyDE 与图检索 |
| `query_main_graph.py` | 8003 固定 fanout 含网络搜索 | 旅游评估时需可关闭网络搜索 |
| `knowledge_service.py` | 只返回 `knowledge_id/title/content/source/score` | 需补元数据字段与可选过滤参数 |
| `import_service.py` | `D:\output` 硬编码 | 需配置化 `UPLOAD_ROOT` |
| 数据 | 30 份 Markdown，元数据字段不统一 | 见第 4 节映射表 |

---

## 2. 已确认决策

| # | 议题 | 决议 |
|---|---|---|
| 1 | 文档范围 | 只做知识库 RAG（8003/8004/8005） |
| 2 | 文档粒度 | 文件级改造清单 + 关键代码片段 |
| 3 | 元数据粒度 | **文档级元数据透传 + `###` 标题实体抽取** |
| 4 | 标题实体识别 | **LLM 批量分类标题**，输出实体名与类型 |
| 5 | 实体存储 | chunk 新增 `entities` JSON 字段；`item_name_tourism_v1` 为城市与实体各写别名行 |
| 6 | `item_name` 粒度 | 仍为**城市/目的地**；实体通过 `parent_item_name` 归并 |
| 7 | 数据缺口 | **补 6 份文化民俗文档；线路预算由 LLM 归纳并标记估算** |
| 8 | 适合人群/最佳游览时间 | 不抽字段，靠正文引用 |
| 9 | 意图过滤 | **本期做 `content_type` 过滤，过滤后为空自动回退全局检索** |
| 10 | 8005 契约 | 响应加元数据字段；请求加可选 `content_type` / `city` / `entities`，向后兼容 |
| 11 | 主体确认失败 | **不澄清，空 item_names 走全局检索** |
| 12 | 8003 引用 | 答案文本末尾追加「参考来源」区块，不改 SSE 协议 |
| 13 | 图谱 | **本期只冻结关系词表与抽取 prompt，不重建 Neo4j** |
| 14 | 集合 | 新建 `chunks_tourism_v1` / `item_name_tourism_v1`，旧集合保留 |
| 15 | 切分 | 保持 500/50，只修 bug 与元数据段 |
| 16 | 8005 检索链路 | ~~向量 + RRF + Rerank，默认关闭 HyDE / 图谱 / 网络搜索~~ → **2026-10-23 修订：本地三路（稠密+稀疏 / HyDE / 图谱）+ 分层融合；8003 额外开网络搜索** |
| 17 | 批量入库 | 新增脚本调用 8004 `/upload`，不改服务接口 |
| 18 | 评估集 | 我生成 50 QA（5 类 × 10），你抽检后写入 `eval/qa.csv` |
| 19 | 完成标准 | 30 份全量入库 + 5 类各 2 条人工验证 + Ragas 出分存档 |
| 20 | 基线 | 先跑一次真实电商基线；**当前 VM 未启动，基线为阻塞项** |

---

## 3. 目标链路

### 3.1 入库链路（改造后）

```text
8004 /upload
  → Entry（md / pdf）
  → PDF→MD / 图片理解
  → NodeMetadataParse（新增）
        解析 ## 元数据 → 归一化 → 城市白名单兜底 → LLM 兜底
        LLM 分类 ### 标题 → entities
        线路文档：解析 duration_days，LLM 归纳 budget_ref（estimated=true）
  → NodeDocumentSplit（剔除元数据段；按标题切；500/50；实体匹配到 section）
  → NodeItemNameRecognition（item_name=city；写城市行 + 实体别名行）
  → NodeBgeEmbedding
  → NodeImportMilvus（chunks_tourism_v1，字段投影）
  → （三元组抽取 / Neo4j 入库本期不启用）
```

### 3.2 查询链路（改造后）

**8005（客服检索，本地三路，不含网络搜索）**

```text
NodeItemNameConfirm
  → LLM 输出 {cities, entities, content_type, rewritten_query}
  → 对齐 item_name_tourism_v1，得到 parent_item_name 集合
  → 失败（空）→ 不澄清，继续
┌ NodeSearchEmbedding    混合检索 dense + sparse（item_name / content_type / entities 过滤）
├ NodeSearchEmbeddingHyde HyDE：LLM 生成假设性答案后检索（带 content_type/实体两级回退）
└ NodeSearchGraph         槽位实体精确匹配 → 正则兜底 → 城市兜底（5s 超时）
                              ↓
NodeRrf（只融合前两路，倒排融合，取 RRF_TOP_N 条）
                              ↓
NodeRerank（RRF 结果 + 图谱 GRAPH_TOP_N 条，统一重排）
→ 返回 items[]
```

**8003（对话问答，四路）**

```text
NodeItemNameConfirm
  → 向量 / HyDE / 图谱 / MCP 网络搜索 四路并行
  → NodeRrf（只融合 Milvus 两路）
  → NodeRerank（RRF + 网页 WEB_TOP_N 条 + 图谱 GRAPH_TOP_N 条）
  → NodeAnswerOutput → 答案末尾追加【参考来源】→ SSE 推送（协议不变）
```

**分层融合原则**：只有 Milvus 出来的结果结构适合做 RRF 倒排融合；图谱路径与网页片段结构异构，不进 RRF，各自截断后在 Rerank 汇合，既省时间又避免为异构结果构造伪排名。

### 3.3 开关与名额

| 开关 | 默认 | 作用 |
|---|---|---|
| `ENABLE_HYDE` | `false` | 是否启用 HyDE 路 |
| `ENABLE_GRAPH` | `false` | 是否启用 Neo4j 图检索路 |
| `ENABLE_WEB_SEARCH` | `false` | 8003 是否启用 MCP 网络搜索 |
| `TRIPLE_EXTRACTOR_MODE` | `off` | 三元组抽取（本期 `off`） |

| 名额 / 超时 | 默认 | 作用 |
|---|---|---|
| `RRF_TOP_N` | `10` | RRF 融合后进入 Rerank 的条数 |
| `GRAPH_TOP_N` | `10` | 图谱进入 Rerank 的条数 |
| `WEB_TOP_N` | `10` | 网络搜索进入 Rerank 的条数 |
| `GRAPH_TIMEOUT` | `5` | 图检索超时（秒），超时只丢该路 |
| `WEB_TIMEOUT` | `8` | 网络搜索超时（秒），超时只丢该路 |

> 三个开关必须同时作用于 `retrieval_main_graph.py` 与 `query_main_graph.py` 的 fanout，否则关闭的节点仍会被调用。
> 任一路异常/超时只降级该路为空，其余路照常进融合与重排。

---

## 4. 统一元数据 Schema

### 4.1 Milvus `chunks_tourism_v1` 字段

| 字段 | 类型 | 长度 | 来源 | 说明 |
|---|---|---|---|---|
| `id` | INT64 | — | 自增 | 主键 |
| `title` | VARCHAR | 200 | 切片标题 | 标题行原文 |
| `file_title` | VARCHAR | 200 | 文件名词干 | 兼容旧字段 |
| `item_name` | VARCHAR | 100 | `city` | 查询过滤键 |
| `content` | VARCHAR | 8000 | 切片正文 | 中文按字节计，留足 |
| `part` | INT64 | — | 切片序号 | 0 表示未细分 |
| `content_type` | VARCHAR | 32 | 归一化枚举 | 景点介绍/线路推荐/酒店信息/美食推荐/交通指南/文化民俗/**平台规则** |
| `city` | VARCHAR | 50 | 城市/目的地/文件名前缀 | 主目的地 |
| `cities` | VARCHAR | 512 | 目的地列表 | JSON 数组字符串 |
| `scenic_name` | VARCHAR | 200 | 景点名称 | 文档级，缺失为 `""` |
| `route_name` | VARCHAR | 200 | 线路名称 | 仅线路类 |
| `hotel_name` | VARCHAR | 200 | 住宿名称 | 仅酒店类 |
| `restaurant_name` | VARCHAR | 200 | 餐厅名称/文档名称（美食） | 仅美食类 |
| `source_file` | VARCHAR | 200 | 文件名 | 如 `三亚景点推荐.md` |
| `source_path` | VARCHAR | 500 | 相对路径 | 幂等删除键 |
| `theme` | VARCHAR | 500 | 主题 | 自由文本 |
| `entities` | JSON | — | LLM 标题分类 | **仅实体名数组** `["亚龙湾","西岛"]`，便于 `json_contains` 过滤；类型信息存在 `metadata_json` |
| `entity_type` | VARCHAR | 32 | 主实体类型 | 兼容单值过滤 |
| `duration_days` | INT64 | — | 线路名解析 | 非线路类为 0 |
| `budget_ref` | VARCHAR | 200 | LLM 归纳 | 如「2–3 天，人均约 1500–2500 元（估算，不含往返大交通）」 |
| `estimated` | BOOL | — | 是否估算 | 预算非原文时 true |
| `metadata_json` | VARCHAR | 8000 | 全量归一结果 | 原始别名、告警、覆盖字段 |
| `dense_vector` | FLOAT_VECTOR | 1024 | BGE-M3 | COSINE |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | — | BGE-M3 | IP |

**索引**

```python
# 标量索引用 INVERTED，便于 expr 过滤
for field in ["item_name", "content_type", "city", "source_path"]:
    index_params.add_index(field_name=field, index_type="INVERTED")
index_params.add_index(
    field_name="dense_vector", index_type="IVF_FLAT",
    metric_type="COSINE", params={"nlist": 128, "nprobe": 10},
)
index_params.add_index(
    field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX",
    metric_type="IP",
    params={"inverted_index_algo": "DAAT_MAXSCORE", "normalize": True, "quantization": "none"},
)
```

### 4.2 `item_name_tourism_v1` 字段

| 字段 | 类型 | 长度 | 说明 |
|---|---|---|---|
| `id` | INT64 | — | 主键 |
| `item_name` | VARCHAR | 100 | 城市或实体名（别名行） |
| `parent_item_name` | VARCHAR | 100 | 城市；城市行指向自身 |
| `content_type` | VARCHAR | 32 | 该行所属内容类型 |
| `source_file` | VARCHAR | 200 | 来源文件 |
| `source_path` | VARCHAR | 500 | 幂等删除键 |
| `dense_vector` | FLOAT_VECTOR | 1024 | 别名文本向量 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | — | 别名文本稀疏向量 |

**别名行规则**（以「云南景点推荐」为例）

```text
云南   → parent 云南   （主城市）
昆明   → parent 云南   （cities 展开）
大理   → parent 云南
丽江   → parent 云南
大理古城 → parent 云南 （实体）
洱海     → parent 云南
丽江古城 → parent 云南
玉龙雪山 → parent 云南
```

删除时按 `source_path` 删除该文档所有行，避免误删同城其他文档。

### 4.3 归一化规则

1. `内容类型`
   - `美食介绍` → `美食推荐`（数据与需求枚举不一致）
   - 去空格、去全角空格；非枚举值记录 warning，置为 `未知`
2. 城市字段
   - `城市` / `目的地` → `city` / `cities`
   - 值含顿号/逗号 → 拆入 `cities`
   - 值为 `云南多城市` → `city=云南`，`cities=[昆明,大理,丽江]`
   - 文件名前缀命中 6 个目的地白名单 → 优先作为 `city`
   - 线路文档以「目的地」为准；`city` 取文件名前缀（云南/杭州）
3. 名称字段
   - `景点名称` → `scenic_name`
   - `线路名称` → `route_name`
   - `住宿名称` / `酒店名称` → `hotel_name`
   - `餐厅名称` → `restaurant_name`
   - `文档名称` → 美食类映射为 `restaurant_name`，其余存入 `metadata_json`
4. `主题` → `theme`
5. `duration_days`：从 `route_name` 正则 `(\d+)\s*日` 取；范围「2 至 3 日」取上限并记录原文
6. `budget_ref`：仅线路类，由 LLM 依据天数、城市、行程强度归纳区间，**必须带「估算」与假设说明**，`estimated=true`
7. `entities`：LLM 分类 `###` 标题，输出 `{name, type}`；校验实体名在正文出现；去重；单文档上限 50

### 4.4 30 份文档映射表

> `城市字段` 为文件原文；`city`/`cities` 为归一结果；`名称字段` 为文档级元数据。
> 新增 6 份文化民俗文档按同一模板。

| # | 文件 | 原文内容类型 | content_type | 城市字段 | city | cities | 名称字段 → 归一 |
|---|---|---|---|---|---|---|---|
| 1 | 景点攻略/三亚景点推荐.md | 景点介绍 | 景点介绍 | 三亚 | 三亚 | [三亚] | 景点名称 → scenic_name（文档级） |
| 2 | 景点攻略/云南景点推荐.md | 景点介绍 | 景点介绍 | 云南多城市 | 云南 | [昆明,大理,丽江] | 景点名称 → scenic_name |
| 3 | 景点攻略/厦门景点推荐.md | 景点介绍 | 景点介绍 | 厦门 | 厦门 | [厦门] | 景点名称 → scenic_name |
| 4 | 景点攻略/张家界景点推荐.md | 景点介绍 | 景点介绍 | 张家界 | 张家界 | [张家界] | 景点名称 → scenic_name |
| 5 | 景点攻略/成都景点推荐.md | 景点介绍 | 景点介绍 | 成都 | 成都 | [成都] | 景点名称 → scenic_name |
| 6 | 景点攻略/杭州景点推荐.md | 景点介绍 | 景点介绍 | 杭州 | 杭州 | [杭州] | 景点名称 → scenic_name |
| 7 | 线路推荐/三亚线路推荐.md | 线路推荐 | 线路推荐 | 三亚 | 三亚 | [三亚] | 线路名称 → route_name；duration_days=5 |
| 8 | 线路推荐/云南线路推荐.md | 线路推荐 | 线路推荐 | 昆明、大理、丽江 | 云南 | [昆明,大理,丽江] | 线路名称 → route_name；duration_days=6 |
| 9 | 线路推荐/厦门线路推荐.md | 线路推荐 | 线路推荐 | 厦门 | 厦门 | [厦门] | 线路名称 → route_name；duration_days=3 |
| 10 | 线路推荐/张家界线路推荐.md | 线路推荐 | 线路推荐 | 张家界 | 张家界 | [张家界] | 线路名称 → route_name；duration_days=3 |
| 11 | 线路推荐/成都线路推荐.md | 线路推荐 | 线路推荐 | 成都 | 成都 | [成都] | 线路名称 → route_name；duration_days=3 |
| 12 | 线路推荐/杭州线路推荐.md | 线路推荐 | 线路推荐 | 杭州、乌镇 | 杭州 | [杭州,乌镇] | 线路名称 → route_name；duration_days=3 |
| 13 | 酒店信息/三亚住宿推荐.md | 酒店信息 | 酒店信息 | 三亚 | 三亚 | [三亚] | 住宿名称 → hotel_name |
| 14 | 酒店信息/云南住宿推荐.md | 酒店信息 | 酒店信息 | 云南多城市 | 云南 | [昆明,大理,丽江] | 住宿名称 → hotel_name |
| 15 | 酒店信息/厦门住宿推荐.md | 酒店信息 | 酒店信息 | 厦门 | 厦门 | [厦门] | 住宿名称 → hotel_name |
| 16 | 酒店信息/张家界住宿推荐.md | 酒店信息 | 酒店信息 | 张家界 | 张家界 | [张家界] | 住宿名称 → hotel_name |
| 17 | 酒店信息/成都住宿推荐.md | 酒店信息 | 酒店信息 | 成都 | 成都 | [成都] | 住宿名称 → hotel_name |
| 18 | 酒店信息/杭州住宿推荐.md | 酒店信息 | 酒店信息 | 杭州 | 杭州 | [杭州] | 住宿名称 → hotel_name |
| 19 | 美食推荐/三亚美食推荐.md | 美食介绍 | **美食推荐** | 三亚 | 三亚 | [三亚] | 文档名称 → restaurant_name |
| 20 | 美食推荐/云南美食推荐.md | 美食介绍 | **美食推荐** | 云南多城市 | 云南 | [昆明,大理,丽江] | 文档名称 → restaurant_name |
| 21 | 美食推荐/厦门美食推荐.md | 美食介绍 | **美食推荐** | 厦门 | 厦门 | [厦门] | 文档名称 → restaurant_name |
| 22 | 美食推荐/张家界美食推荐.md | 美食介绍 | **美食推荐** | 张家界 | 张家界 | [张家界] | 文档名称 → restaurant_name |
| 23 | 美食推荐/成都美食推荐.md | 美食介绍 | **美食推荐** | 成都 | 成都 | [成都] | 文档名称 → restaurant_name |
| 24 | 美食推荐/杭州美食推荐.md | 美食介绍 | **美食推荐** | 杭州 | 杭州 | [杭州] | 文档名称 → restaurant_name |
| 25 | 交通指南/三亚交通指南.md | 交通指南 | 交通指南 | 三亚 | 三亚 | [三亚] | 无名称字段 → 全部留空 |
| 26 | 交通指南/云南交通指南.md | 交通指南 | 交通指南 | 云南多城市 | 云南 | [昆明,大理,丽江] | 无名称字段 |
| 27 | 交通指南/厦门交通指南.md | 交通指南 | 交通指南 | 厦门 | 厦门 | [厦门] | 无名称字段 |
| 28 | 交通指南/张家界交通指南.md | 交通指南 | 交通指南 | 张家界 | 张家界 | [张家界] | 无名称字段 |
| 29 | 交通指南/成都交通指南.md | 交通指南 | 交通指南 | 成都 | 成都 | [成都] | 无名称字段 |
| 30 | 交通指南/杭州交通指南.md | 交通指南 | 交通指南 | 杭州 | 杭州 | [杭州] | 无名称字段 |
| 31–36 | 文化民俗/云南\|三亚\|厦门\|张家界\|成都\|杭州文化民俗.md | 文化民俗 | 文化民俗 | 对应城市 | 对应城市 | [城市] | 文档名称 → metadata_json |

**实体抽取示例（LLM 输出）**

```json
// 三亚景点推荐.md
{"entities": [
  {"name": "亚龙湾", "type": "景点"},
  {"name": "蜈支洲岛", "type": "景点"},
  {"name": "天涯海角", "type": "景点"},
  {"name": "南山文化旅游区", "type": "景点"},
  {"name": "海棠湾", "type": "片区"},
  {"name": "鹿回头", "type": "景点"},
  {"name": "西岛", "type": "景点"}
]}

// 成都景点推荐.md
{"entities": [
  {"name": "熊猫基地", "type": "景点"},
  {"name": "春熙路和太古里", "type": "片区"},
  {"name": "宽窄巷子和人民公园", "type": "片区"},
  {"name": "武侯祠和锦里", "type": "片区"}
]}
```

**必须被过滤掉的结构性标题**：`第一层核心景点`、`第二层核心片区`、`适合人群`、`常见决策点`、`不同时长安排建议`、`第一优先级`、`3 天景点安排`、`第一次到云南不适合贪多` 等。

---

## 5. 入库链路改造（文件级）

### 5.1 `atguigu/config/config.py`

**现状**：只有 `MilvusConfig` 读取集合名，没有旅游域常量与检索开关。

**改法**：新增 `TourismConfig`、`RetrievalConfig`、`ImportConfig`；集合名给默认值。

```python
class MilvusConfig:
    milvus_url = os.getenv("MILVUS_URL")
    chunks_collection = os.getenv("CHUNKS_COLLECTION", "chunks_tourism_v1")
    item_name_collection = os.getenv("ITEM_NAME_COLLECTION", "item_name_tourism_v1")


class TourismConfig:
    destinations = ["云南", "三亚", "厦门", "张家界", "成都", "杭州"]
    content_types = ["景点介绍", "线路推荐", "酒店信息", "美食推荐", "交通指南", "文化民俗"]
    # 数据里写作「云南多城市」的文档，统一展开
    multi_city_defaults = {"云南": ["昆明", "大理", "丽江"]}


class RetrievalConfig:
    enable_hyde = os.getenv("ENABLE_HYDE", "false").lower() == "true"
    enable_graph = os.getenv("ENABLE_GRAPH", "false").lower() == "true"
    enable_web_search = os.getenv("ENABLE_WEB_SEARCH", "false").lower() == "true"
    content_type_fallback = True


class ImportConfig:
    upload_root = os.getenv("UPLOAD_ROOT", os.path.join(os.getcwd(), "output"))
```

**验收**：`.env` 不配置集合名时，默认使用 `chunks_tourism_v1` / `item_name_tourism_v1`。

---

### 5.2 `atguigu/config/prompt.py`

**现状**：商品名、HAK180 三元组、商品问答 prompt。

**改法**：替换 4 组 prompt；新增元数据兜底 prompt、标题分类 prompt、预算归纳 prompt。

**关键片段（主体识别）**

```python
ITEM_NAME_SYSTEM_PROMPT = "你是旅游知识库的主体识别模型，负责从文档中识别目的地城市、景点/片区实体与内容类型。"

ITEM_NAME_USER_PROMPT_TEMPLATE = """
文件名：{file_title}

正文切片：
{context}

请识别：
1. 主目的地城市（只能是：云南、三亚、厦门、张家界、成都、杭州 之一；云南的文档默认云南）；
2. 文档涉及的景点/片区/古镇/酒店/餐厅实体（只要正文明确出现的真实名称，不要结构性标题）；
3. 内容类型（景点介绍/线路推荐/酒店信息/美食推荐/交通指南/文化民俗）。

只返回 JSON：
{{"city": "三亚", "cities": ["三亚"], "entities": [{{"name": "亚龙湾", "type": "景点"}}], "content_type": "景点介绍"}}
"""
```

**关键片段（查询侧主体提取）**

```python
ITEM_NAME_EXTRACT_TEMPLATE = """
历史会话：
{history_text}

当前用户问题：
{original_query}

请提取并只返回 JSON：
{{
  "cities": ["三亚"],
  "entities": ["亚龙湾"],
  "content_type": "景点介绍",
  "rewritten_query": "三亚亚龙湾适合什么人去"
}}

规则：
1. cities 只能取 6 个目的地或其下属城市（昆明/大理/丽江/乌镇等），别名归并到主目的地；
2. entities 只放真实景点/片区/酒店/餐厅名；问题里没有就返回空数组；
3. content_type 只能取 7 个枚举之一（含 **平台规则**，供客服子查询验证退改/出票/退款/签证等政策）；无法判断返回空字符串；
4. rewritten_query 必须能独立成立，不含代词。
"""
```

**新增 prompt**

- `METADATA_FALLBACK_PROMPT`：输入 `file_title + 正文前 3000 字`，输出统一元数据 JSON
- `ENTITY_TITLE_CLASSIFY_PROMPT`：输入标题列表 + 文档元数据，输出 `{"entities":[...]}`
- `BUDGET_ESTIMATE_PROMPT`：输入线路名、天数、途经城市、行程强度，输出 `{"budget_ref":"...","assumed":true}`；必须声明「估算、不含往返大交通」

**验收**：grep 代码中不再出现 `商品名称`、`hak180`、`型号`。

---

### 5.3 `atguigu/import_process/state.py`

**改法**：新增两个字段。

```python
class ImportGraphState(TypedDict, total=False):
    ...
    doc_metadata: dict   # 统一元数据（含 entities / duration_days / budget_ref / warnings）
```

`doc_metadata` 结构：

```python
{
    "content_type": "线路推荐",
    "city": "云南",
    "cities": ["昆明", "大理", "丽江"],
    "scenic_name": "",
    "route_name": "昆大丽 6 日经典游",
    "hotel_name": "",
    "restaurant_name": "",
    "source_file": "云南线路推荐.md",
    "source_path": "线路推荐/云南线路推荐.md",
    "theme": "云南初次自由行、古城、洱海、雪山、城市串联",
    "entities": [{"name": "大理古城", "type": "景点"}],
    "duration_days": 6,
    "budget_ref": "6 天，人均约 3500–5000 元（估算，不含往返大交通）",
    "estimated": True,
    "raw": {...},
    "warnings": []
}
```

**验收**：新增字段有注释；旧代码读取不受影响。

---

### 5.4 `atguigu/import_process/nodes/node_metadata_parse.py`（新增）

**职责**：

1. 读取 `md_path`，提取 `## 元数据` 段（到下一个 `## ` 为止）
2. 兼容全角冒号、缺项、空行；按 4.3 规则归一化
3. `city` 缺失 → 文件名前缀 → 仍缺失 → LLM 兜底
4. 线路类解析 `duration_days`，调用 LLM 归纳 `budget_ref`
5. 提取全部 `### ` 标题，调用 LLM 分类，得到 `entities`
6. 返回 `{"doc_metadata": {...}}`，不抛异常；失败写 warning 并降级

**关键片段**

```python
class NodeMetadataParse(NodeBase):
    name = "node_metadata_parse"

    def process(self, state: ImportGraphState):
        md_path = state.get("md_path")
        raw = self.read_metadata_block(md_path)
        meta = self.normalize(raw, Path(md_path))
        meta["entities"] = self.classify_titles(md_path, meta)
        if meta["content_type"] == "线路推荐":
            meta["duration_days"] = self.parse_duration(meta.get("route_name", ""))
            meta["budget_ref"], meta["estimated"] = self.estimate_budget(meta)
        return {"doc_metadata": meta}

    def classify_titles(self, md_path, meta):
        titles = [
            line.lstrip("#").strip()
            for line in Path(md_path).read_text(encoding="utf-8").splitlines()
            if line.startswith("### ")
        ]
        # LLM 只返回真实实体；解析失败返回 []
        return self._llm_classify(titles, meta)
```

**验收**：

- 30 份文档批量跑解析，`content_type` 与 `city` 非空率 100%
- 美食类 `content_type` 全部为 `美食推荐`
- 6 份线路文档 `duration_days` 与 `budget_ref` 均有值，`estimated=true`
- 解析失败只产生 warning，不阻断入库

---

### 5.5 `atguigu/import_process/nodes/node_document_split.py`

**现状问题**：

1. `title` 取值 `temp_list[0]`，内容以空行开头时会取到空串
2. `## 元数据` 段会被切成 chunk
3. chunk 不携带元数据与实体

**改法**：

```python
# 1. 切分前剔除元数据段
def strip_metadata_block(self, md_content: str) -> str:
    return re.sub(r"^## 元数据\s*$.*?(?=^## |\Z)", "", md_content, flags=re.M | re.S)

# 2. 标题取真正的标题行
title_line = next((ln.strip() for ln in temp_list if ln.strip().startswith("#")), "无标题")

# 3. 切分后继承文档元数据；按 section 标题匹配实体
meta = state.get("doc_metadata", {})
entity_map = {e["name"]: e for e in meta.get("entities", [])}
for chunk in final_section_list:
    chunk.update({k: meta.get(k, "") for k in (
        "content_type", "city", "cities", "scenic_name", "route_name",
        "hotel_name", "restaurant_name", "source_file", "source_path",
        "theme", "duration_days", "budget_ref", "estimated",
    )})
    section_title = chunk["title"].lstrip("#").strip()
    matched = [e for name, e in entity_map.items() if name in section_title]
    chunk["entities"] = [e.get("name") for e in matched]  # Milvus 侧只存实体名
    chunk["entity_type"] = matched[0]["type"] if matched else ""
    chunk["metadata_json"] = json.dumps(meta, ensure_ascii=False)
```

**验收**：

- 30 份文档的 chunk 中不出现 `## 元数据` 内容
- 所有 chunk 的 `content_type` / `city` / `source_path` 非空
- 实体章节（如 `### 亚龙湾`）的 chunk `entities` 含该实体；结构性章节 `entities=[]`

---

### 5.6 `atguigu/import_process/nodes/node_item_name_recognition.py`

**现状**：LLM 自由识别一个 `item_name`；按 `item_name` 删除；集合字段只有 3 个。

**改法**：

1. `item_name` 优先取 `state["doc_metadata"]["city"]`，取不到才调 LLM
2. 为每个城市和实体写别名行
3. 按 `source_path` 幂等删除
4. 集合 schema 增加 `parent_item_name/content_type/source_file/source_path`

```python
def get_item_name(self, state):
    meta = state.get("doc_metadata") or {}
    return meta.get("city") or self.get_item_name_by_llm(state)

def build_alias_rows(self, meta, item_name):
    rows = [{"item_name": item_name, "parent_item_name": item_name}]
    for city in json.loads(meta.get("cities") or "[]"):
        if city != item_name:
            rows.append({"item_name": city, "parent_item_name": item_name})
    for ent in meta.get("entities", []):
        rows.append({"item_name": ent["name"], "parent_item_name": item_name})
    for row in rows:
        row.update({
            "content_type": meta.get("content_type", ""),
            "source_file": meta.get("source_file", ""),
            "source_path": meta.get("source_path", ""),
        })
    return rows

def insert_into_milvus(self, chunks, collection_name, meta):
    milvus_client.delete(
        collection_name=collection_name,
        filter=f"source_path == '{escape(meta['source_path'])}'",
    )
    rows = self.build_alias_rows(meta, meta["city"])
    embeddings = get_embeddings([r["item_name"] for r in rows])
    data = [{**row,
             "dense_vector": embeddings["dense"][i],
             "sparse_vector": embeddings["sparse"][i]}
            for i, row in enumerate(rows)]
    milvus_client.insert(collection_name=collection_name, data=data)
```

**验收**：

- 「云南景点推荐」写入 4 行以上：云南、昆明、大理、丽江、大理古城、洱海、丽江古城、玉龙雪山
- 重复上传同一文档，行数不翻倍
- 查询「大理」能通过别名行拿到 `parent_item_name=云南`

---

### 5.7 `atguigu/import_process/nodes/node_import_milvus.py`

**现状**：直接把 `chunks` 插入 Milvus，chunk 中的 `doc_metadata` 等多余键会导致写入失败；按 `file_title` 删除。

**改法**：

```python
CHUNK_FIELDS = [
    "title", "file_title", "item_name", "content", "part",
    "content_type", "city", "cities", "scenic_name", "route_name",
    "hotel_name", "restaurant_name", "source_file", "source_path",
    "theme", "entities", "entity_type", "duration_days",
    "budget_ref", "estimated", "metadata_json",
    "dense_vector", "sparse_vector",
]

def project(self, chunks):
    out = []
    for c in chunks:
        row = {k: c[k] for k in CHUNK_FIELDS if k in c}
        row["entities"] = json.dumps(row.get("entities", []), ensure_ascii=False)
        out.append(row)
    return out

def process(self, state):
    chunks = self.project(state["chunks"])
    ...
    source_path = chunks[0]["source_path"]
    milvus_client.delete(
        collection_name=collection_name,
        filter=f"source_path == '{escape(source_path)}'",
    )
    milvus_client.insert(collection_name=collection_name, data=chunks)
```

**验收**：

- 新集合字段与第 4.1 节一致
- `entities` 以 JSON 字符串写入，Milvus `json_contains` 可查询
- 旧集合 `kb_chunks` / `kb_item_names` 无任何写入

---

### 5.8 `atguigu/import_process/import_main_graph.py`

**改法**：在 `MDImg` 与 `DocumentSplit` 之间插入 `MetadataParse`。

```text
Entry → PDF→MD → MDImg → MetadataParse → DocumentSplit
      → ItemNameRecognition → BgeEmbedding → ImportMilvus
```

**注意**：新增节点后检查条件路由 `after_entry_router`，确保 md/pdf 两条路径都经过 `MetadataParse`。

**验收**：8004 上传一份 md，日志依次出现 `node_metadata_parse` → `node_document_split`。

---

### 5.9 `atguigu/web/api/import_service.py`

**改法**：`D:\output` → `ImportConfig.upload_root`。

```python
local_dir = str(Path(ImportConfig.upload_root) / datetime.now().strftime("%Y%m%d"))
```

**验收**：Windows 上不配置 `UPLOAD_ROOT` 时使用项目内 `output/`；上传文件落在该目录。

---

### 5.10 `tools/batch_import.py`（新增）

**职责**：遍历 `旅游数据/`，逐份调用 8004，轮询状态，输出统计。

```python
# 伪代码
for path in sorted(ROOT.rglob("*.md")):
    files = {"file": (path.name, path.open("rb"), "text/markdown")}
    task_id = requests.post(f"{BASE}/upload", files=files).json()["task_id"]
    wait_until_done(task_id)          # 轮询 /status/{task_id}
    record(path, task_id, status)
print_stats(record)                   # 成功/失败、按 (city, content_type) 统计
```

**验收**：

- 36 份（30 现有 + 6 文化民俗）全部 success
- 输出 `(city, content_type)` 计数矩阵，6 × 6 无空洞
- 失败文档单独列出，可重跑

---

## 6. 查询链路改造（文件级）

### 6.1 `atguigu/query_process/nodes/node_item_name_confirm.py`

**改法**：

1. 使用 5.2 的查询侧 prompt，输出 `cities / entities / content_type / rewritten_query`
2. 对齐 `item_name_tourism_v1` 时读取 `parent_item_name`
3. 失败不再返回「无法识别」话术，返回空列表继续检索
4. 关闭图检索后，`vector_weight` 固定 1.0 / `graph_weight` 0.0

```python
def align_item_names(self, final_search_item_names):
    confirmed = [i["parent_item_name"] for i in final_search_item_names if i["score"] >= 0.85]
    optional = [i["parent_item_name"] for i in final_search_item_names if 0.60 <= i["score"] < 0.85]
    parents = list(dict.fromkeys(confirmed or optional))
    return parents          # 空列表 = 全局检索

def process(self, state):
    ...
    parsed = self.get_query_slots(history_content, original_query)
    parents, entities, content_type = self.align(parsed)
    return {
        "item_names": parents,
        "query_entities": entities,
        "query_content_type": content_type,
        "rewritten_query": parsed["rewritten_query"],
        "answer": "",                    # 不再用 answer 触发澄清分支
        "vector_weight": 1.0,
        "graph_weight": 0.0,
        ...
    }
```

**验收**：

- 问「三亚有哪些必去景点」→ `item_names=["三亚"]`、`content_type=景点介绍`
- 问「大理怎么玩」→ `item_names=["云南"]`（别名行归并）
- 问「今天天气怎么样」→ `item_names=[]`、`content_type=""`，继续检索不报错

---

### 6.2 `atguigu/query_process/nodes/node_search_embedding.py`

**改法**：

1. `item_names` 为空不抛异常，去掉 `expr` 做全局检索
2. 支持组合过滤：`item_name in [...]` + `content_type == '...'` + `json_contains(entities, '"亚龙湾"')`
3. `output_fields` 增加全部元数据字段
4. `content_type` 过滤无结果时，自动去掉该条件重试一次

```python
def build_expr(self, item_names, content_type, entities):
    parts = []
    if item_names:
        parts.append(f"item_name in {json.dumps(item_names, ensure_ascii=False)}")
    if content_type:
        parts.append(f"content_type == '{escape(content_type)}'")
    if entities:
        parts.append("(" + " or ".join(
            f"json_contains(entities, '\"{escape(e)}\"')" for e in entities
        ) + ")")
    return " and ".join(parts)

def search(self, expr):
    ...
    res = search_hybird(..., expr=expr,
        output_fields=["id", "file_title", "title", "content", "item_name",
                       "content_type", "city", "source_file", "source_path",
                       "entities", "route_name", "duration_days",
                       "budget_ref", "estimated"])

def process(self, state):
    res = self.search(self.build_expr(...))
    if not res and content_type:
        res = self.search(self.build_expr(item_names, "", entities))  # 回退
    return {"embedding_chunks": res}
```

**验收**：

- 空 `item_names` 返回结果且无异常
- `content_type=酒店信息` 时结果全部为酒店类
- 过滤为空时自动回退，日志记录 `content_type fallback`

---

### 6.3 `atguigu/query_process/nodes/node_rerank.py`

**现状**：合并时只保留 4 个字段，元数据丢失。

**改法**：合并时透传元数据。

```python
META_FIELDS = ("content_type", "city", "source_file", "source_path",
               "entities", "route_name", "duration_days", "budget_ref", "estimated")

merged_chunks.append({
    "title": doc.get("title") or doc.get("item_name", ""),
    "content": doc.get("content", "") or "无内容",
    "source": doc.get("source", "local"),
    "url": doc.get("url", ""),
    **{k: doc.get(k) for k in META_FIELDS if k in doc},
})
```

**注意**：`title` 改为优先用 chunk 的 `title`（如「### 亚龙湾」），不要把 `item_name`（城市）当标题。

**验收**：`reranked_docs` 中每条都含 `source_file` 与 `content_type`。

---

### 6.4 `atguigu/query_process/retrieval_main_graph.py`

**改法**：fanout 受开关控制；节点保留注册，条件为 false 时不进入。

```python
@staticmethod
def fanout_search(state):
    nodes = [NodeSearchEmbedding.name]
    if RetrievalConfig.enable_hyde:
        nodes.append(NodeSearchEmbeddingHyde.name)
    if RetrievalConfig.enable_graph:
        nodes.append(NodeSearchGraph.name)
    return nodes
```

**验收**：默认配置下日志只有 `node_search_embedding` 一路；打开 `ENABLE_HYDE=true` 后出现 HyDE 路。

---

### 6.5 `atguigu/query_process/query_main_graph.py`

**改法**：同样按开关控制 fanout；网络搜索默认关。

```python
def after_confirm_router(self, state):
    if state.get("answer"):
        return NodeAnswerOutput.name
    nodes = [NodeSearchEmbedding.name]
    if RetrievalConfig.enable_hyde:
        nodes.append(NodeSearchEmbeddingHyde.name)
    if RetrievalConfig.enable_graph:
        nodes.append(NodeSearchGraph.name)
    if RetrievalConfig.enable_web_search:
        nodes.append(NodeWebSearchMcp.name)
    return nodes
```

**注意**：`NodeRrf` 对 `web_search_docs` 的依赖保持不变；关闭时不进该节点即可。

**验收**：`eval/eval.py` 默认不再调用网络搜索与图检索。

---

### 6.6 `atguigu/query_process/nodes/node_answer_output.py`

**改法**：答案末尾追加参考来源；不改 SSE 事件。

```python
def build_citation_block(self, chunks):
    lines, seen = [], set()
    for doc in chunks:
        src = (doc or {}).get("source_file")
        if not src or src in seen:
            continue
        seen.add(src)
        meta = "·".join(filter(None, [doc.get("content_type"), doc.get("city")]))
        lines.append(f"- {src}" + (f"（{meta}）" if meta else ""))
    return "\n\n【参考来源】\n" + "\n".join(lines) if lines else ""

# generat_answer 流式结束后
answer += self.build_citation_block(chunks)
put_data(task_id, "delta", {"delta": citation})
```

**注意**：引用区块在流式结束后追加，保证 `put_data` 的 `final` 与历史记录一致。

**验收**：

- 8003 回答末尾出现【参考来源】
- `eval/result.csv` 的 `contexts` 含 `source_file`
- SSE 事件类型未变化

---

### 6.7 `atguigu/web/api/knowledge_service.py`

**改法**：

```python
class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=10)
    content_type: str | None = None
    city: str | None = None
    entities: list[str] | None = None

class KnowledgeSearchItem(BaseModel):
    knowledge_id: str
    title: str
    content: str
    source: str
    score: float | None = None
    content_type: str | None = None
    city: str | None = None
    source_file: str | None = None
    source_path: str | None = None
    entities: list[str] | None = None
    route_name: str | None = None    duration_days: int | None = None
    budget_ref: str | None = None
    estimated: bool | None = None
```

`adapt_docs` 读取并返回上述字段；`search_product_knowledge` 重命名为 `search_travel_knowledge`。

```python
def search_travel_knowledge(query, top_k, content_type=None, city=None, entities=None):
    ...
    confirm_update = NodeItemNameConfirm()(state)
    # item_names 为空也继续检索
    state["query_content_type"] = content_type or state.get("query_content_type") or ""
    state["query_entities"] = entities or state.get("query_entities") or []
    if city:
        state["item_names"] = [city]
    result = RetrievalMainGraphRunner.create_and_run(state)
    return KnowledgeSearchResponse(items=adapt_docs(result.get("reranked_docs") or [], top_k))
```

**验收**：

- 旧调用 `{query, top_k}` 仍返回 200
- 新调用带 `content_type` / `city` 能精确过滤
- 每条 item 含 `source_file` 与 `content_type`

---

### 6.8 多路检索（2026-10-23 新增）

| 文件 | 改动 |
|---|---|
| `config/config.py` | `RetrievalConfig` 新增 `rrf_top_n` / `graph_top_n` / `web_top_n` / `graph_timeout` / `web_timeout` |
| `query_process/nodes/node_rrf.py` | 删掉 `TOTAL_RERANK=30 / WEB_FIXED=10` 旧常量与 `vector_weight` 反推；只融合 Milvus 两路，条数取 `rrf_top_n` |
| `query_process/nodes/node_rerank.py` | 三路全空直接返回，不再用空列表调用 Rerank 接口 |
| `query_process/nodes/node_search_graph.py` | 槽位优先匹配 + 正则/城市兜底；名额取 `graph_top_n`；整路超时与异常隔离 |
| `query_process/nodes/node_search_embedding_hyde.py` | 单路异常降级为空；补 content_type / 实体两级回退 |
| `query_process/nodes/node_web_search_mcp.py` | 超时与结果条数走配置；异常降级为空 |
| `query_process/retrieval_main_graph.py` | `route_after_rrf` 按「RRF / 图 / 网任一非空」判定是否进 Rerank |
| `import_process/nodes/node_graph_extract.py` | 头尾实体必须出现在本批正文，否则丢弃（`dropped_hallucination`） |
| `config/prompt.py` | 批量抽取 prompt 要求实体原样照抄，禁止把描述性从句当实体 |
| `tools/inspect_graph.py`（新增） | Neo4j 只读计数 / 关系分布 / 实体查询，`--wipe --yes` 清库 |
| `tools/check_graph_sample.py`（新增） | 6 类各 1 份小样本 + 六项硬指标 |
| `tools/smoke_query.py` | 新增 `--with-routes`：逐路断言 + RRF 名额上限校验 |
| `web/api/knowledge_service.py` | `SOURCE_LABELS` 补 `web → 网络搜索` |

**验收**：`smoke_query.py --with-routes` 四路各有命中；RRF 输出不超过 `RRF_TOP_N`；单路失败只降级该路。

---

### 7.1 请求

```json
{
  "query": "三亚有哪些必去景点",
  "top_k": 5,
  "content_type": "景点介绍",
  "city": "三亚",
  "entities": ["亚龙湾"]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `query` | 是 | 用户问题 |
| `top_k` | 否 | 1–10，默认 5 |
| `content_type` | 否 | 显式覆盖意图识别结果 |
| `city` | 否 | 显式覆盖城市识别结果 |
| `entities` | 否 | 显式实体过滤 |

### 7.2 响应

```json
{
  "items": [
    {
      "knowledge_id": "KB_4f3a9c2d1e8b",
      "title": "### 亚龙湾",
      "content": "亚龙湾更适合作为海边停留和度假场景来理解……",
      "source": "知识库文档",
      "score": 0.92,
      "content_type": "景点介绍",
      "city": "三亚",
      "source_file": "三亚景点推荐.md",
      "source_path": "景点攻略/三亚景点推荐.md",
      "entities": ["亚龙湾"],
      "route_name": null,
      "duration_days": null,
      "budget_ref": null,
      "estimated": null
    }
  ],
  "message": ""
}
```

**兼容性**：`items` 原有 5 个字段不变，新增字段均可选；`message` 只在无结果时承载说明，不进入 `items`。

**验收**：

- 无 token → 401
- 正确 token → 200 且字段齐全
- 线路问题返回 `route_name` / `duration_days` / `budget_ref` / `estimated=true`

---

## 8. 图谱与关系词表（本期冻结，不重建）

### 8.1 关系词表提案（12 类，需评审冻结）

| # | 关系 | 方向示例 | 说明 |
|---|---|---|---|
| 1 | 位于 | 亚龙湾 → 三亚 | 景点/酒店/餐厅 → 城市 |
| 2 | 属于 | 海棠湾 → 三亚 | 片区/古镇 → 城市 |
| 3 | 包含 | 昆大丽 6 日 → 大理古城 | 线路 → 景点/城市 |
| 4 | 途经 | 昆大丽 6 日 → 昆明 | 线路 → 城市/节点 |
| 5 | 推荐 | 三亚 → 亚龙湾 | 城市/攻略 → 景点/美食 |
| 6 | 适合 | 亚龙湾 → 亲子家庭 | 景点/线路 → 人群 |
| 7 | 需要 | 玉龙雪山 → 提前预约 | 景点/线路 → 门票/证件/预约 |
| 8 | 提供 | 海棠湾酒店 → 接送服务 | 酒店 → 设施/服务 |
| 9 | 邻近 | 天涯海角 → 南山文化旅游区 | 景点 → 景点/机场/车站 |
| 10 | 以…闻名 | 杭州 → 西湖 | 城市/景点 → 特色/美食 |
| 11 | 建议 | 蜈支洲岛 → 单独安排一天 | 攻略 → 时长/注意事项 |
| 12 | 对比 | 蜈支洲岛 ↔ 西岛 | 同类景点对比/替代 |

冻结后三处必须同源：

```python
# node_search_graph.py
RELATION_TYPES = ["位于", "属于", "包含", "途经", "推荐",
                  "适合", "需要", "提供", "邻近", "以…闻名", "建议", "对比"]
```

- `config/prompt.py` 的 `TRIPLE_EXTRACTION_USER_TEMPLATE`
- gold 标注文件

### 8.2 本期动作

1. 把 `RELATION_TYPES` 改为上述 12 类
2. 把三元组抽取 prompt 改为旅游域，并明确「只允许这 12 类关系」
3. 删除 `node_search_graph.py` 中的 HAK 180 特判
4. ~~不重抽、不重建 Neo4j；`ENABLE_GRAPH=false`~~ → **2026-10-23 已重建**：清空旧库（3049 节点 / 5075 关系 / 721 类关系词，含 HAK180 电商残留）后，用旅游数据全量重抽并写图（`ENABLE_GRAPH_EXTRACT=true`）
5. 抽取口径：头尾实体必须原样出现在所在正文（prompt 约束 + `node_graph_extract` 代码兜底），否则丢弃
6. 图检索：槽位实体精确匹配 → 正则 `CONTAINS` 兜底 → 城市兜底，5s 超时、异常隔离

**验收**：`grep -r "HAK 180\|HAK180" atguigu/` 无结果；`tools/inspect_graph.py` 节点数 > 0 且关系类型全部落在 12 类内；`tools/check_graph_sample.py` 6 份样本六项硬指标全 PASS。

---

## 9. 评估与验收

### 9.1 评估集（`eval/qa.csv`）

- 规模：50 题，5 类 × 10 题
- 列：`question,ground_truth`（与 `eval/eval.py` 对齐）
- 覆盖：
  - 景点：三亚/成都/云南/杭州/厦门/张家界
  - 线路：天数、预算、行程顺序
  - 酒店：区域选择、住宿类型
  - 美食：地方菜、小吃、觅食区域
  - 交通：机场/高铁/市内接驳
- 每题 `ground_truth` 必须能在对应文档中找到依据
- 我生成初稿 → 你抽检 → 定稿

### 9.2 DoD

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| 1 | 36 份文档（30 + 6 文化民俗）全部入库成功，无 failed | ✅ | `chunks_tourism_v1` 1271 条；6 城市 × 6 类无空缺 |
| 2 | `(city, content_type)` 统计 6 × 6 无空洞 | ✅ | 36 份文档矩阵完整 |
| 3 | 5 类问题各 2 条验证通过，返回片段带 `content_type/city/source_file` | ✅ | `tools/smoke_query.py` 8 项断言全 PASS；8005 实测返回元数据齐全 |
| 4 | `eval/eval.py --limit 50` 跑完，5 项指标写入 `result.csv` | ✅ | 50/50 success，见 `eval/REPORT.md` |
| 5 | 旧集合 `kb_chunks` / `kb_item_names` 未被修改 | ✅ | 实测 1241 / 34 行；代码无旧集合读写路径 |
| 6 | 8003 `/query` 与 SSE 行为不退化 | ✅ | SSE 实测：progress × 11、delta × 280、final × 1，答案含【参考来源】；事件类型未变 |
| 6b | 8004 `/upload` 行为不退化 | ⏸ 未验 | 本期未跑上传链路（依赖 MinerU/视觉额度），留待后续 |
| 7 | 8005 无 token 401、有 token 200，新旧请求均可用 | ✅ | 401 / 200 实测；新请求 `city + content_type` 过滤生效，`duration_days/estimated` 字段正常 |
| 8 | 多路检索：向量 / HyDE / 图谱 / 网络各自可用，单路失败只降级该路 | ✅ | `smoke_query.py --with-routes` 10/10 PASS（向量 1、HyDE 1+RRF 1、图谱 8、网搜 10）；图 5s / 网 8s 超时降级 |
| 9 | 图谱重建：旧电商图清空后用旅游数据重建，关系全在 12 类内 | ✅ | 旧库 3049 节点/5075 关系/721 类已清空；重建后 381 节点 / 629 关系 / 12 类；小样本 6/6 六项硬指标 PASS |
| 10 | 多路检索对指标的影响已评估并归档 | ✅ | 10 题子集：faithfulness 0.80→0.89、relevancy 0.63→0.91、recall 0.77→0.86；correctness 0.58→0.43，原因见 `eval/REPORT.md` §11.5 |

### 9.3 验收命令

```bash
# 8004
uv run python -m atguigu.web.api.import_service

# 8005
uv run python -m atguigu.web.api.knowledge_service

# 批量入库
uv run python tools/batch_import.py --root 旅游数据 --base-url http://127.0.0.1:8004

# 8005 冒烟
curl -X POST http://127.0.0.1:8005/api/v1/knowledge/search \
  -H "Content-Type: application/json" \
  -H "X-Internal-Service-Token: <KNOWLEDGE_SERVICE_TOKEN>" \
  -d "{\"query\":\"三亚有哪些必去景点\",\"top_k\":5}"

# Ragas 评估
uv run python eval/eval.py --limit 50
```

---

## 10. 任务清单（按顺序）

| # | 任务 | 产出 | 验收 |
|---|---|---|---|
| 0 | **启动 VM 与中间件**（Milvus/Neo4j/Mongo/MinIO）、加载 BGE-M3、配置 `.env` | 环境自检通过 | 各组件连通；`BGE_M3_PATH` 可加载 |
| 1 | **跑电商基线**：HAK180 入库 + `eval/eval.py` | `result.csv` 基线存档 | 记录 5 项指标均值，改造后对比 |
| 2 | 配置改造：`config.py` / `.env.example` / 新集合名 | 新配置类 | 默认集合为 `*_tourism_v1` |
| 3 | 新增 `NodeMetadataParse` + 元数据归一 | 解析节点 | 30 份解析报告：`content_type`/`city` 100% |
| 4 | 标题实体抽取（LLM 分类） | `entities` | 每份文档实体数 3–20；无结构性标题 |
| 5 | 线路预算与天数 | `duration_days`/`budget_ref`/`estimated` | 6 份线路均有值且标注估算 |
| 6 | 切分节点改造 | 剔除元数据段、标题修复、字段透传 | 无元数据 chunk；实体章节带 `entities` |
| 7 | 主体识别 + 别名行 + item_name 集合 | 新 schema 与写入逻辑 | 云南写入 ≥4 行；重复上传不翻倍 |
| 8 | chunks 集合 schema + 投影 + 幂等删除 | 新 schema | 写入无多余字段；`source_path` 删除生效 |
| 9 | 入库图串联 + 8004 路径配置化 | 新图 | 上传一份 md 全链路成功 |
| 10 | 补 6 份文化民俗文档 | 6 份 md | 元数据段齐全，内容与城市匹配 |
| 11 | 批量入库 36 份 | 统计报告 | 全部 success，6×6 无空洞 |
| 12 | 查询侧：主体确认/过滤/全局兜底/rerank 透传 | 新查询逻辑 | 城市、实体、内容类型三类过滤可用 |
| 13 | 8005 契约 + 8003 引用 | 新接口字段与引用区块 | 新旧请求均可用；回答带来源 |
| 14 | 50 QA + Ragas | `qa.csv` / `result.csv` | 5 项指标跑完并存档 |
| 15 | 回归：旧集合、8003 SSE、8004 | 回归记录 | 无退化 |

**依赖关系**：0 → 1 → 2 → 3 → 4/5 → 6 → 7/8 → 9 → 10 → 11 → 12/13 → 14 → 15。

**可并行**：任务 4 与 5 可并行；任务 10（写文化民俗）可在任务 3 之后任意时间做。

---

## 11. 风险与回滚

| 风险 | 影响 | 应对 |
|---|---|---|
| VM/中间件未就绪 | 基线跑不了，任务 0 阻塞 | 文档开头列入前置；先做纯代码改造（任务 2–9） |
| 元数据字段不统一 | 过滤失效 | 归一规则 + warning + LLM 兜底，不阻断入库 |
| `美食介绍` 枚举不一致 | 意图过滤漏召 | 归一映射为 `美食推荐` |
| 云南「多城市」 | `item_name` 单值限制 | `multi_city_defaults` 展开 + 别名行 |
| 实体标题误判 | 过滤噪声 | LLM 分类 + 正文出现校验 + 人工抽检 |
| `content_type` 过滤过严 | 无结果 | 自动回退全局检索并记日志 |
| Milvus 中文长度 | 写入截断/失败 | `VARCHAR` 按字节留足；超长截断并 warning |
| 元数据在 rerank 丢失 | 8005 无引用字段 | 合并时显式透传 `META_FIELDS` |
| 评估被网络搜索污染 | 指标不稳定 | `ENABLE_WEB_SEARCH=false` |
| 新集合 schema 变更 | 需重建集合 | 开发期直接 drop 新集合重跑；旧集合不受影响 |

**回滚方式**：

1. `.env` 把 `CHUNKS_COLLECTION` / `ITEM_NAME_COLLECTION` 改回 `kb_chunks` / `kb_item_names`
2. 代码在 `dev` 分支，按提交回退
3. 新集合保留不删，便于对比排查

---

## 12. 本期不做

- 不重建 Neo4j 图谱（只冻结词表）
- 不接入三元组微调模型（`TRIPLE_EXTRACTOR_MODE=off`）
- 不生成「适合人群 / 最佳游览时间」结构化字段
- 不改 customer-service / ai-service / 三元组项目
- 不改 8003 的 SSE 事件协议与 `/query` 请求结构
- 不删除旧集合与旧数据
- 不做客服侧联调（8005 契约变更只保证向后兼容）

---

## 附录 A：开工前环境自检

- [ ] 虚拟机已启动，Milvus / Neo4j / MongoDB / MinIO 端口可连
- [ ] `BGE_M3_PATH` 指向本机模型目录，`BGE_DEVICE=cuda:0` 可加载
- [ ] `ALIYUN_API_KEY` / `ALIYUN_API_BASE` 有效
- [ ] `KNOWLEDGE_SERVICE_TOKEN` 已设置
- [ ] `UV` 环境已 `uv sync`
- [ ] `UPLOAD_ROOT` 可写
- [ ] 端口 8003 / 8004 / 8005 未被占用
- [ ] 旧集合 `kb_chunks` / `kb_item_names` 存在（回归基线）
- [ ] 30 份旅游文档路径可访问
- [ ] 基线评估产物已存档（改造前）

## 附录 B：改造后目录变化

```text
4组_陶桓_旅游_知识库/
├── atguigu/
│   ├── config/
│   │   ├── config.py              # + TourismConfig / RetrievalConfig / ImportConfig
│   │   └── prompt.py              # 4 组 prompt 旅游化 + 3 组新增
│   ├── import_process/
│   │   ├── state.py               # + doc_metadata
│   │   ├── import_main_graph.py   # + MetadataParse
│   │   └── nodes/
│   │       ├── node_metadata_parse.py   # 新增
│   │       ├── node_document_split.py   # 改
│   │       ├── node_item_name_recognition.py  # 改
│   │       └── node_import_milvus.py    # 改
│   ├── query_process/
│   │   ├── retrieval_main_graph.py  # 开关
│   │   ├── query_main_graph.py      # 开关
│   │   └── nodes/
│   │       ├── node_item_name_confirm.py  # 改
│   │       ├── node_search_embedding.py   # 改
│   │       ├── node_rerank.py             # 改
│   │       └── node_answer_output.py      # 改
│   └── web/api/
│       ├── import_service.py      # 路径配置化
│       └── knowledge_service.py   # 契约扩展
├── tools/
│   └── batch_import.py            # 新增
├── eval/
│   ├── qa.csv                     # 50 题旅游 QA
│   └── result.csv                 # 评估结果
└── 旅游数据/
    └── 文化民俗/                  # 新增 6 份
        ├── 云南文化民俗.md
        ├── 三亚文化民俗.md
        ├── 厦门文化民俗.md
        ├── 张家界文化民俗.md
        ├── 成都文化民俗.md
        └── 杭州文化民俗.md
```

---

## 13. 实施进度（2026年10月21日）

### 已完成：入库链路全部节点

| # | 节点 / 模块 | 状态 |
|---|---|---|
| 1 | `node_entry` | 无需改 |
| 2 | `node_pdf_to_md` | 已改：MinerU 请求强制直连、下载重试 3 次、轮询加 2 秒间隔 |
| 3 | `node_md_img` | 已改：图片按文档前缀存储、单图失败不中断、失败不生成空链接 |
| 4 | `node_metadata_parse` | 新增：元数据归一、城市兜底校验、标题实体分类、线路预算归纳 |
| 5 | `node_document_split` | 已改：剔除元数据段、标题提取修复、元数据与实体透传 |
| 6 | `node_item_name_recognition` | 已重写：城市为主、别名行、按 source_path 幂等删除 |
| 7 | `node_bge_embedding` | 无需改 |
| 8 | `node_import_milvus` | 已重写：新 schema、标量索引、字段投影、source_path 幂等删除 |
| 9 | `node_graph_extract` | 已改：旅游批量模板、12 类关系白名单、越界丢弃统计 |
| 10 | `node_graph_import` | 无需改（Cypher 与检索侧一致），演示数据已清理 |

配套：`config/relation_schema.py`（关系词表单一源）、`tools/batch_import.py`（支持 `--local`）、`tools/inspect_milvus.py`、`.claude/skills/kb-tourism/SKILL.md`。

### 数据现状

```text
chunks_tourism_v1       1271 条（已清理 36 条空正文脏 chunk，原 1307）
item_name_tourism_v1     228 行别名
文档                     36 份（6 城市 × 6 内容类型，无空缺）
线路字段                 6/6 有天数、预算、estimated=true
```

PDF 链路实测通过：MinerU 转换 → 图片理解 → 元数据 → 切分 → 入库，64 秒完成。

### 今日修掉的环境问题

1. MinIO 密钥失效 → 换新密钥；md 跳过对象存储，失败不再阻断
2. 本机 7890 代理劫持 MinerU CDN → 强制直连 + `NO_PROXY`
3. `qwen3-vl-flash` 不存在 / `qwen3-vl-*` 额度耗尽 → 视觉模型改 `qwen3.8-omni-flash`
4. 实体类型缺「民俗项目」→ 民俗文档抽不到实体，已补并入白名单

### 已完成（2026年10月22日：查询链路验证 + 评估）

1. **检索层冒烟**：新增 `tools/smoke_query.py`，8 项断言全 PASS —— 城市过滤、`content_type` 过滤、实体过滤、两级回退、全局检索、槽位识别（三亚→三亚、大理→云南、无目的地降级）、RRF+Rerank 元数据透传。
2. **修掉 2 个真实缺陷**：
   - `node_search_embedding`：`json_contains(entities, '"亚龙湾"')` 多包一层双引号导致实体过滤永不命中（改为 `'亚龙湾'`，实测 0 → 7 条）；
   - `node_document_split`：首个标题在首行时切出空正文 chunk（36 份文档各 1 条），已修切分逻辑并清理存量（1307 → 1271）。
3. **演示数据旅游化**：`node_rrf.py` / `node_web_search_mcp.py` 清除 HAK180 残留。
4. **语言模型凭据分离**：新增 `LLM_API_KEY` / `LLM_API_BASE`（未配置回退阿里云），视觉走 `VL_API_*`；rerank / MCP 仍走阿里云。8 个调用点已切换。
5. **评估集与校验**：`eval/qa.csv` 换为 50 题旅游 QA（5 类 × 10，6 城市轮转）；新增 `eval/validate_qa.py`，50/50 可溯源。
6. **Ragas 全量评估（50/50 success）**，判分模型 `deepseek-v4.1-flash`（关闭思考）：

| faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
|---|---|---|---|---|
| 0.7901 | 0.8104 | 0.8221 | 0.7400 | 0.5756 |

7. **答案提示词收紧**：一句结论 + 2–3 条要点、≤200 字、禁小标题/表格/嵌套列表；实测回答均值 95 字（改造前 700+），`answer_correctness` 由 0.47–0.50 升至 0.58。
8. **eval 支持补跑**：`--only-ids 31-50` / `--resume`，中断后无需整批重来。
9. **历史批次归档**：`eval/archive_ecommerce/`（电商 QA）、`eval/archive_qwen38/`（qwen 判分批次，额度耗尽，含 README 说明）。

详情见 `4组_陶桓_旅游_知识库/eval/REPORT.md`。

### 仍待处理

1. 线路类召回偏弱（`answer_relevancy` 0.68、`context_recall` 0.63）：多天行程压缩成单条 chunk，覆盖不全；可考虑线路问题强过滤 `content_type=线路推荐`。
2. 多要素并列题（厦门觅食分工、三亚机场接驳等）`answer_correctness` 偏低（0.20–0.30）：属回答长度约束与题型张力，可放宽字数或拆题。
3. 图谱链路默认关闭，Neo4j 仍是旧商品图；如需启用要先重放入库。