# 将 kb_0515 (GraphRAG) 知识库接入 ai-service 客服系统 —— 计划与落地状态

## Context

ai-service 客服 Agent 的知识查询工具 `search_knowledge`
（`atguigu/agent/harness/tools/read.py`）依赖
`KnowledgeQueryService.search()`
（`atguigu/agent/harness/knowledge/service.py`）。接入前该方法只返回 3 条硬编码平台规则，
用于打通 Agent 工具链路。独立 GraphRAG 项目 `kb_0515`
（`D:\pycharm-0515-th\kb_0515`）具备向量检索 + 图检索 + RRF 融合 + Rerank 精排。

两个项目以独立服务方式部署，通过 HTTP 对接：kb_0515 新增同步检索接口，只返回检索片段
（不生成最终答案）；ai-service 通过 HTTP 客户端调用，再适配成 `KnowledgeItem`。
平台规则仍由 ai-service 本地提供，旅游知识走 kb_0515。

---

## 当前落地状态总览

| 状态 | 说明 |
|---|---|
| **接入主干已完成** | 独立检索服务、鉴权、客户端、混合知识查询、Skill/Prompt 约束均已接通 |
| **Harness 交叉治理未完成** | 知识失败降级、知识忠实度、数据新鲜度、`confirm_run` 发布前重查、Run 总超时、调用次数配置化仍缺 |
| **模型可用性未完成** | 主模型宕机时没有备用模型降级 |
| **可观测与隔离未完成** | MongoDB 伪历史隔离、`rewritten_query` 回传、内容截断仍缺 |

---

## 对现有项目的影响面（已落地）

**结论：不影响 kb_0515 现有 RAG 对话流程，不影响 ai-service 现有旅游查询流程。**

| 项目 | 改动类型 | 具体内容 | 是否影响现有流程 | 状态 |
|---|---|---|---|---|
| kb_0515 | 新增文件 | `query_process/retrieval_main_graph.py`（检索子图，不含城市/产品名确认节点） | 否 | **已完成** |
| kb_0515 | 新增文件 | `web/api/knowledge_service.py`（独立检索 API，端口 8005） | 否 | **已完成** |
| kb_0515 | 新增配置类 | `config/config.py` 的 `InternalAuthConfig.knowledge_service_token` | 否 | **已完成** |
| kb_0515 | 修改端口号 | `query_service.py` → 8003；`import_service.py` → 8004 | 是，本地/脚本若写死旧端口需改 | **已完成** |
| ai-service | 新增文件 | `infrastructure/knowledge_client.py` | 否 | **已完成** |
| ai-service | 修改文件 | `common/config.py` 新增 `knowledge_base_url` / `knowledge_timeout_seconds` / `knowledge_top_k` / `knowledge_service_token` | 否，纯新增字段 | **已完成** |
| ai-service | 修改文件 | `KnowledgeQueryService.search()`：本地平台规则 + HTTP 旅游知识 | 是，这是接入目的 | **已完成** |
| ai-service | 修改文件 | `search_knowledge` 工具描述、`KnowledgeSearchResult`、Skill guidance、全局 Prompt | 否，契约向后兼容 | **已完成** |

**不涉及（仍成立）**：`QueryMainGraphRunner`、`MainGraphRunner`、旅游查询工具、
`ToolExecutor` 主体、数据库模型。这些代码本次没有为接入而改行为。

---

## 一、已经完成的修改

### 1. 端口：独立第三个进程，方案 B

三个 kb_0515 进程各自 `uvicorn.run()`，不合并进 `query_service`。

| 服务 | 原端口 | 现端口 |
|---|---|---|
| kb_0515 查询服务 `query_service.py`（对话式 `/query` + SSE） | 8001 | **8003** |
| kb_0515 存库服务 `import_service.py`（`/upload`） | 8000 | **8004** |
| kb_0515 知识检索服务 `knowledge_service.py`（本次新增） | 无 | **8005** |
| ecommerce 旅游服务 | 8001 | 不变 |
| ai-service 客服后端 | 8002 | 不变 |

ai-service 默认 `KNOWLEDGE_BASE_URL=http://127.0.0.1:8005/api/v1`。

### 2. kb_0515 检索子图与接口

**检索子图** `RetrievalMainGraphRunner`：

- 节点从现有模块 import，不复制。
- 图内只有三路召回 + RRF + Rerank，不含 `NodeWebSearchMcp`、`NodeAnswerOutput`、`NodeItemNameConfirm`。
- `NodeItemNameConfirm` 在接口层先单独执行；`item_names` 为空则短路，不进入向量检索（避免抛「请输入要检索的主体名」）。
- 三路都没有候选时跳过 Rerank，避免空文档调用精排接口。

**接口** `POST /api/v1/knowledge/search`：

```
Header: X-Internal-Service-Token: <KNOWLEDGE_SERVICE_TOKEN>
Body:   {"query": "...", "top_k": 5}
Resp:   {"items": [...], "message": ""}
```

- Token 用 `secrets.compare_digest` 比对，不匹配返回 401。
- 城市/产品名无法确认时：`items=[]`，`message` 为澄清话术（或兜底「请补充要咨询的城市/产品名称」）。
- 检索成功时：`message=""`，`items` 来自 `reranked_docs`，按 `top_k` 截断条数。
- `source` 映射：`local` → `知识库文档`，`graph` → `知识图谱`。
- 每次调用生成独立 `task_id` / `session_id`（uuid），并 `create_queue`，结束时弹出，避免节点内部阻塞。

### 3. `knowledge_id`：去掉 index，只用正文哈希

```text
knowledge_id = "KB_" + sha256(content)[:12]
```

同一正文每次检索结果一致，排名变化不会改 id。正文因重新入库而改变时，id 会变。
口径：**内容指纹，不是文档主键**。可展示、可写入本次 `AgentToolCall`；不能当跨入库版本的长期审计主键。

### 4. 鉴权 Token 拆分

不再复用 ai-service 的 `INTERNAL_SERVICE_TOKEN`。

| 调用方向 | 配置项 | Header |
|---|---|---|
| Customer Service → ai-service | `INTERNAL_SERVICE_TOKEN` | `X-Internal-Service-Token` |
| ai-service → kb_0515 | `KNOWLEDGE_SERVICE_TOKEN` | `X-Internal-Service-Token` |

两套值独立。kb_0515 令牌泄露不会直接打进 ai-service 内部接口。
`.env.example` 已列出 `KNOWLEDGE_SERVICE_TOKEN=knowledge-internal-token`。
kb_0515 未配置或空令牌时，接口直接 401。

### 5. ai-service 配置

`Settings` 已落地字段：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `knowledge_base_url` | `http://127.0.0.1:8005/api/v1` | 知识检索服务地址 |
| `knowledge_timeout_seconds` | `45.0` | 单次 HTTP 超时（GraphRAG 需要长于旅游 8 秒） |
| `knowledge_top_k` | `5`（1–10） | 返回条数上限 |
| `knowledge_service_token` | `""` | 调用 kb_0515 的独立令牌；空则客户端直接失败 |

### 6. KnowledgeClient

- 使用 `knowledge_service_token`，空令牌抛 `RuntimeError("未配置旅游知识服务令牌")`。
- Header 为 `X-Internal-Service-Token`。
- 超时走 `knowledge_timeout_seconds`。
- 只接受对象响应；非对象抛 `RuntimeError`。
- 返回完整 JSON（含 `items` / `message`），由 Service 层拆字段。

### 7. KnowledgeQueryService：混合查询 + 旅游知识失败软降级

外部契约从 `list[KnowledgeItem]` 扩展为 `KnowledgeSearchResult(items, message)`。
`search_knowledge` 仍返回 `ToolResult`，调用方结构兼容。

实际行为：

1. 空 query → `items=[]`。
2. **始终先附带 3 条本地平台规则**（退改政策、出票时效、签证材料），不依赖 kb_0515。
3. 再请求 kb_0515 旅游知识；成功则把条目追加到平台规则后面。
4. kb_0515 超时/连接失败/4xx/5xx：**在 Service 内捕获**，保留平台规则，
   `message="暂时无法查询旅游产品说明"`，工具结果仍是 `success=True`。
5. 城市/产品名无法确认：kb_0515 返回的 `message`（澄清话术）原样交给工具层转述。
6. 缺 `knowledge_id` 或正文的条目跳过，避免一条坏数据打垮整次查询。

这与初版计划「异常全部交给 ToolExecutor」不同：平台规则在知识库宕机时仍可用。

### 8. 工具、Skill、Prompt 约束（已加，服务端未强制）

- `search_knowledge`：`message` 可转述、不是知识条目；城市/产品名无法确认时转述澄清话术；
  `message` 为「暂时无法查询旅游产品说明」时不得编造用法或产品说明。
- `product-service`：用法/产品说明只能来自来源为「知识库文档」或「知识图谱」的条目。
- `policy-service`：平台规则只能来自结果列表中的平台规则条目；不要把旅游知识 `message` 当平台规则。
- 全局 Prompt：规则和产品说明必须来自成功知识查询；工具失败不得猜测。

### 9. 已确认并落地的设计决策

1. 新接口只返回检索片段，不调用 LLM 生成最终答案。
2. 检索子图去掉网络搜索，只保留向量 + HyDE + 图三路召回。
3. 接口层单独跑 `NodeItemNameConfirm`，识别失败短路返回澄清 `message`。
4. 两个项目独立部署，知识检索占用独立端口 8005。
5. Token 拆分，内网明文密钥，不做双向 TLS。
6. `ToolCategory.KNOWLEDGE` 暂不拆成政策/产品说明/FAQ。
7. `confirm_run` 重复调用保持 **409**（`cancel_run` 才是幂等）。重复确认的 HTTP 语义不变；**发布前是否重查业务数据**见下方 P0-3，当前代码只推进状态，尚未做新鲜度校验。

---

## 二、目前还欠缺的部分

按优先级排列。接入主干可用，但和 Day10/Day11 Harness 交叉后，下列缺口会在生产路径上暴露。

### P0：正确性，必须处理

#### P0-1 `search_knowledge` 失败不触发 AnswerValidator 降级

**现状**：`AnswerValidator` 只用 `TOOL_CATALOG.get_tool_category == BUSINESS` 筛选。
KNOWLEDGE 类调用全部失败时，不会进入「全失败 → 固定 DECLINE」分支，模型可以继续组织回答。

叠加当前 Service 的软降级后，缺口分成两条：

| 场景 | 工具结果 | 期望 | 现状 |
|---|---|---|---|
| kb_0515 未启动，问退改政策 | `success=True`，仍有 3 条平台规则 | 可用本地规则作答 | 已满足 |
| kb_0515 未启动，问目的地攻略 | `success=True`，`message="暂时无法查询旅游产品说明"` | 转述不可用，禁止编产品说明 | 只有 Prompt，Validator 不拦 |
| `search_knowledge` 全部 `success=False`（异常漏到 ToolExecutor） | `SERVICE_CALL` | 强制 `DECLINE` | **不会降级** |

**待做**：

1. `AnswerValidator` 对 KNOWLEDGE 同样做「该类全部失败 → 固定 DECLINE」
   （文案：暂时无法查询相关知识内容，请稍后重试）。
2. 旅游知识失败但平台规则仍成功时：若回复像在讲目的地攻略/产品说明，
   而成功结果里没有「知识库文档 / 知识图谱」来源条目，则纠错或降级。
3. 空结果 + 澄清 `message` 按「查无」转述，不走服务失败降级。

验证不能只写「Agent 不崩溃」，必须覆盖上表三行，尤其是「不编造」。

#### P0-2 知识内容忠实度（产品决策已定方向，代码未做）

`FactChecker` 只提取业务编号、带单位数字、订单/退款状态。知识里的「七天」「15 天」
进不了这套规则。跨 Skill（`get_order` + `search_knowledge`）时，订单号会被校验，
规则数字不会。

**接入阶段决策**：不上引用片段匹配，不强制回复带 `knowledge_id`。
接受 Prompt 约束。待补的最小代码是给 `policy-service` guidance 加一句：
引用规则时保留原文中的期限、条件和范围，不要改写数字。

引用片段匹配（回答中的关键短语必须出现在知识片段中）留到后续专项。

#### P0-3 数据新鲜度：系统只保证「回答与 T1 快照一致」

工具查询发生在 T1，回答生成与校验发生在 T3/T5，客户端确认发生在 T6。
当前架构只保证 **T5 与 T1 一致**（`AnswerValidator` / `PageActionValidator` 用工具快照），
不保证 **T6 与 T1 一致**，也不保证 **T5 与当前真实业务状态一致**。

现状：

| 时间点 | 目的 | 当前用什么数据 |
|---|---|---|
| T1 工具调用 | 给模型提供依据 | 实时查询 Tourism Service / kb_0515 |
| T3 输出校验 | 验证回答与依据一致 | T1 快照（`AgentToolCall.result`） |
| T6 `confirm_run` | 发布页面动作 | **不重查，只把 `DECISION_PREPARED` 推进为 `COMPLETED`** |

`ToolResult` 与 `AgentToolCall` 都没有 `as_of`。`PageActionValidator` 只检查
「当前 Run 是否成功调用过 `get_order`」，不检查订单现在是否仍允许取消/改地址。
因此会出现「查询时订单可取消，确认时已经发货」仍被发布。

**不要在 T3 校验阶段改成重查业务服务。** AI Service 并不直连 Tourism Service 数据库，
所谓「查库」实际是再发一次 `TourismDataClient.get_json` HTTP。单次约 20–150ms，
相对一次 Run 的 5–70 秒可以忽略。真正的问题是逻辑错位：

- 模型回答基于 T1 的 `pending_shipment`；
- 若 T3 重查得到 `shipped`，校验无法判定该回答该不该过；
- 要求「回答必须和实时数据一致」会在数据变化时大量拒答；
- 要求「回答必须和 T1 快照一致」则重查没有意义。

正确分层：

| 时间点 | 目的 | 应该用什么数据 |
|---|---|---|
| T1 工具调用 | 给模型提供依据 | 实时查询（保持现状） |
| T3 校验 | 回答与依据是否一致 | **T1 快照（保持现状）** |
| T6 `confirm_run` | 发布前数据是否仍然有效 | **对有副作用的页面动作实时重查** |

**待做（两步，做完即可系统性拦住「查询时有效、使用时失效」）：**

1. 给 `ToolResult` 和 `AgentToolCall` 加 `as_of`（UTC 时间戳，工具返回瞬间写入）。
   页面动作结果里同时写入 `action_as_of`，供 confirm 判断快照年龄。
2. 让 `confirm_run` 成为发布前的最后一次数据校验，而不只是状态推进：
   - 仅当存在页面动作且该动作绑定具体资源时才重查；
   - 通过用户 JWT 再调一次 Tourism Service（与 T1 同一条 HTTP 路径，不是直连库）；
   - 用新鲜数据重跑页面动作合法性（订单是否仍允许取消/改地址等）；
   - 失败则 `SUPERSEDED`，`result.superseded_reason = "数据已变化"`，接口仍返回 **409**；
   - 纯文本 `ANSWER` / `CLARIFY` / `DECLINE` / `REQUEST_HANDOFF` / `BROWSE_ORDERS` 不重查。

重查粒度：

| 动作 | 是否重查 | 重查什么 |
|---|---|---|
| `ANSWER`（纯文本） | 否 | 无副作用 |
| `CLARIFY` / `DECLINE` | 否 | 无副作用 |
| `REQUEST_HANDOFF` | 否 | 转人工由 Customer Service 处理 |
| `BROWSE_ORDERS` | 否 | 无具体资源 |
| `CANCEL_BOOKING` | **是** | `get_order`：状态是否仍允许取消 |
| `EDIT_TRAVELER` | **是** | `get_order`：是否仍可改地址 |
| `OPEN_ORDER_DETAIL` / `APPLY_REFUND` / `VIEW_REFUNDS` | 视产品是否视为有副作用再定；默认有 `resource_id` 的订单动作一并重查状态 | `get_order` |

可选优化（懒惰重查）：快照仍在 TTL 内则信任 `as_of`，超 TTL 才 HTTP 重查。
建议 TTL：`CANCEL_BOOKING` 30 秒，`EDIT_TRAVELER` 60 秒。正常路径零开销。

实现时注意：

- `confirm_run` 目前只有 `user_id` / `run_id`，重查需要把用户 JWT 从路由传入 Coordinator（与 `start_run` 一样）。
- 校验逻辑留在 Coordinator / Validator，Repository 不发 HTTP。
- T3 校验继续用快照，禁止改成「校验时重查」。

### P1：生产可用，应该处理

#### P1-1 NodeItemNameConfirm 写 MongoDB 的副作用

每次检索仍调用 `add_or_update_history`。当前每次用新 uuid 作为 `session_id`，
不会写入某个真实用户会话，但会往 `kb_message` 插入「伪对话」。
高并发下会撑大历史表，并污染按全库计数的运营看板。

**待做**：

1. 必须请 kb_0515 维护者确认历史记录是否被其他模块消费。
2. 无论结论如何，检索侧 `session_id` 加固定前缀（如 `aisvc-kb-{uuid}`），便于过滤。
3. 若历史被对话统计消费，再加 `is_system=True` 或独立集合。

#### P1-2 单次知识超时 45 秒 × `run_limit=8`

`knowledge_timeout_seconds=45` 对 GraphRAG 合理，但 `ToolCallLimitMiddleware(run_limit=8)`
写死在 `factory.py`。`load_skill` 和 `search_knowledge` **都计入**这 8 次。
最坏情况：8 次知识检索 × 45 秒，再加上 LLM，单次 Run 可达数分钟。

**待做**：

1. 新增配置 `agent_tool_call_limit`（默认 8），替换写死的 `run_limit`。
2. 给整个 Run 加总超时（建议 90–120 秒），超时走现有 `FAILED`。
3. 计数口径写进 Day11：一次 Run 内所有工具调用都计数，不把 `load_skill` / `search_knowledge` 摘出去。
4. 跨 Skill 不够用时再把默认值调到 12，而不是改中间件语义。

#### P1-3 知识结果过长

`knowledge_top_k=5`，kb_0515 与 ai-service 都没有单条 `content` 长度上限。
长 chunk 会进入 LLM 上下文，并写入 `AgentToolCall.result`。

**待做**：在 `KnowledgeQueryService._to_product_items` 对单条 `content` 截到 800–1000 字，并打日志。

#### P1-4 状态机并发与 DECISION_PREPARED 超时清理

下列会在知识检索变慢、页面动作等待确认变长后被放大：

- AgentRun 状态机并发保护（行锁），避免两次 `confirm` / `cancel` 交叉写入。
- `DECISION_PREPARED` 超时清理：超时未确认则 `SUPERSEDED`，避免过期页面动作被后来的 confirm 发布。
- `confirm_run` 对**非法状态**仍返回 409；对**数据已变化**也返回 409（见 P0-3），两者都是 409，用 `detail` / `superseded_reason` 区分。

#### P1-5 主模型失败后的备用模型降级

现状：`ModelAdapter.create_model()` 只按 `llm_provider` 创建**一个**模型
（DeepSeek / Qwen / OpenAI 兼容）。`AgentExecutor` 在模型 HTTP 超时、连接失败时
直接进入 `FAILED`，没有备用端点。

**待做**：

```text
主模型（DeepSeek / Qwen）因网络波动或服务宕机失败
  → 自动切备用模型（GPT-4o-mini / Claude Haiku 等 OpenAI 兼容端点）
  → 备用也失败才 FAILED
```

设计约束：

1. 主备都走 OpenAI 兼容接口（`ChatOpenAI` + `base_url` / `api_key` / `model`），
   不引入第二套 SDK；Qwen 已是这条路径，DeepSeek 也可改用兼容端点以统一降级。
2. 新增配置，例如 `llm_fallback_base_url` / `llm_fallback_api_key` / `llm_fallback_model`；
   未配置备用则行为与现在相同。
3. 只对**传输层失败**降级（超时、连接错误、5xx），不对结构化输出校验失败、
   工具失败、事实校验失败降级——那些是纠错循环，不是换模型。
4. 一次 Run 内切换后，`AgentRun.model_name` 写成实际成功的那个模型，便于观测。
5. 备用模型与主模型共用同一套工具、Skill、输出协议和 Validator，禁止备用路径绕过 Harness。

### P2：可观测，可以稍后

| 项 | 现状 | 待做 |
|---|---|---|
| `rewritten_query` 回传 | kb_0515 内部会改写 query，响应只有 `items`/`message`；改写失败时 ai-service 只看到空列表 | 响应增加可选 `rewritten_query`，ai-service 只打日志，不把该字段交给模型 |
| KNOWLEDGE 类调用统计 | 工具记录里有 `tool_name`，没有按类别聚合 | 管理端观测按 `ToolCategory` 统计成功/失败 |
| trace / 告警 | 仅有 `logger.exception("旅游知识查询失败")` | kb_0515 连续失败时告警 |
| `ToolCategory` 拆分 | 只有一个 `KNOWLEDGE` | 现在不拆；政策/产品说明/FAQ 用 Skill 与 `source` 区分 |

---

## 三、明确不做（接入阶段）

- 不把 kb_0515 作为模块 import 进 ai-service 同进程。
- 不改 kb_0515 `/query`、`/upload` 的行为（只改了启动端口）。
- 不给 kb_0515 现有 `/query`、`/upload`、`/status` 补鉴权。
- 不改旅游查询工具的查询语义、`ToolExecutor` 的统一异常分类。
- 为 P0-3 允许给 `ToolResult` / `AgentToolCall` 增加 `as_of`，这是数据新鲜度所需的模型字段，不是业务表重构。
- 不把 `knowledge_service` 合并进 `query_service`。
- 不上知识引用片段匹配、不强制用户可见回复携带 `knowledge_id`。
- 不把 `confirm_run` 改成幂等成功（非法状态与数据变化都返回 409）。
- 不拆 `ToolCategory.KNOWLEDGE`。
- T3 输出校验不改为重查 Tourism Service；新鲜度检查只放在 T6 `confirm_run`。

---

## 四、与 Day11 Harness 的交叉影响（必须记住）

1. **KNOWLEDGE 不参与事实校验**：`FactChecker` 只管业务编号/数字/状态。知识数字改写拦不住。
2. **失败降级只看 BUSINESS**：KNOWLEDGE 全失败时模型可能编造。见 P0-1。
3. **跨 Skill**：`get_order`（BUSINESS）有事实校验，`search_knowledge`（KNOWLEDGE）没有；
   「订单能不能退改」里的规则部分完全依赖模型自觉。
4. **`run_limit=8` 含 `load_skill` 与 `search_knowledge`**：跨 Skill 容易顶满。见 P1-2。
5. **软降级让工具保持成功**：kb_0515 宕机时 `search_knowledge` 仍是 `success=True`，
   只靠 Prompt 禁止编产品说明，服务端未强制。
6. **数据新鲜度只覆盖到校验，覆盖不到发布**：T3 用快照是对的；T6 `confirm_run`
   不重查，有副作用的页面动作可能在查询后、确认前失效。见 P0-3。
7. **主模型不可用即整次 Run 失败**：没有 OpenAI 兼容备用端点。见 P1-5。

---

## 五、验证方式

### 已应用的验证（接入主干）

1. kb_0515：启动 `knowledge_service.py`（8005），正确/错误 Token 各测一次，确认 200/401。
2. 回归：`query_service` 的 `/query` + SSE、`import_service` 的 `/upload` 行为与改端口前一致。
3. ai-service：退款规则 / 旅游知识提问能拿到 kb_0515 真实片段（平台规则仍在结果前部）。
4. 回归：订单/产品/出行/退款工具行为不受影响。

### 仍缺的验证（对应未完成项）

5. kb_0515 未启动 + 问退改政策：应用本地平台规则作答。
6. kb_0515 未启动 + 问目的地攻略：转述「暂时无法查询旅游产品说明」，回复中无编造的产品说明内容。
7. `search_knowledge` 全部 `success=False`：Validator 强制 `DECLINE`。
8. 超长 `content`：截断后仍能完成 Run，数据库中的工具结果长度受控。
9. 一次 Run 内 `load_skill` + 多次 `search_knowledge` + 业务工具：确认计入 `run_limit`，
   并在配置化后可调整。
10. 数据新鲜度：T1 `get_order` 为可取消 → T3 生成 `CANCEL_BOOKING` → 订单在 T1–T6 间发货
    → T6 `confirm_run` 重查后 `SUPERSEDED` + 409，页面动作不得发布。
11. 快照仍在 TTL 内的 `confirm_run`：不发 HTTP 重查，直接 `COMPLETED`。
12. 纯文本回答（无页面动作）的 `confirm` 路径：零重查。
13. 主模型超时/连接失败：自动切备用 OpenAI 兼容模型，`model_name` 记录实际模型；
    备用也失败才 `FAILED`。结构化输出错误仍走纠错，不换模型。

---

## 六、旅游化落地状态更新（2026-10-22）

本项目在旅游改造中已完成以下原计划缺口（对应上文 P 项）：

- **P0-1 知识失败降级** ✅：`AnswerValidator` 已对 KNOWLEDGE 类工具「全部失败 → DECLINE」。
- **P0-2 知识忠实度（Prompt 层）** ✅：`policy-service` guidance 已要求「引用规则时保留原文期限、条件与范围，不改写数字」。
- **P1-2 工具上限与 Run 超时** ✅：`AGENT_TOOL_CALL_LIMIT` / `AGENT_RUN_TIMEOUT_SECONDS` 已配置化并接入 factory/executor。
- **P1-3 知识单条截断** ✅：`KnowledgeQueryService._to_product_items` 单条正文截断到 1000 字。
- **平台规则旅游化** ✅：本地 3 条规则改为「退改政策 / 出票时效 / 签证材料」。

仍未完成（留待后续）：P0-3 confirm 前重查、P1-1 Mongo 历史隔离、P1-4 状态机并发/超时清理、P1-5 备用模型降级、P2 可观测项。

> 说明：8005 知识库未就绪时，`search_knowledge` 返回平台规则并附「暂时无法查询旅游知识」降级提示。
