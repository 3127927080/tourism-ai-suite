# 可优化点：接入 Jev（System One 决策模型）做用户意图判断

> 状态：**设计完成，代码未开工**（Phase 0 待打通协议）
> 目标项目：`ai-service`（端口 8002）
> 分支：`dev`（按 AGENTS.md §10，`master` 只接受来自 `dev` 的合并）
> 关联文档：`README.md`、`AGENTS.md`、`knowledge_integration_plan.md`

---

## Context

### 为什么这是一个「可优化点」

ai-service 目前是**「一个大 Agent 自由发挥」**的架构：`AgentRunCoordinator.start_run`
（`atguigu/agent/harness/run/coordinator.py:36-129`）创建 AgentRun 后直接交给
`AgentExecutor.execute`（`harness/run/executor.py:42-78`），内部由 LangChain ReAct Agent
一次性 `ainvoke`，**模型自己决定调哪些工具、输出什么 `reply_type` / `task_update`**。

三个由此产生的具体缺口：

1. **工具可见性靠模型自觉**。`SkillScopeMiddleware`（`harness/skills/middleware.py:19-57`）
   在 `active_skill_code` 为空时只放行 `load_skill`，但**是否加载某个 Skill 完全由模型决定**。
   模型不加载就直接回答，工具集被锁死在 `{load_skill}`，回答质量不受控。
2. **意图只能事后知道**。`TaskUpdate.task_type`（`llm/output.py:69-95`）是最终输出的产物，
   调用前拿不到，无法据此收窄工具或预注入上下文。
3. **没有置信度信号**。不知道「模型/系统有多确定这句话是什么意图」，低置信度时
   无法主动澄清，也无法在指标里区分「答错」和「本来就该澄清」。

### Jev / System One 是什么（与本项目的匹配点）

Jev 是 TypeSafe AI 于 2026-09-15 发布的 **System One 决策模型**：

- **不生成文本**，对一个 `state` + 若干 typed question 返回**每个候选选项的概率分布 + confidence**；
- 问题只有三种形态：`noul`（某命题为真的概率）、`choice`（从预定义选项中选一个）、`score`（有序刻度打分）；
- `choice` 的候选**上限 255 个**；
- 官方指标：约 70–500ms/次，$0.042 / 1M input tokens，输出不计费。

阿里云百炼提供了同类模型 **`decision-model-preview`**，走 System One 协议
（`POST {base}/compatible-mode/v1/systemone`），预览期免费，境内节点，**本项目选它**。

> 意图判断本身就是「有限选项 + 要置信度」的判别任务，是决策模型的原生用例，
> 比让生成模型「输出一个 JSON 分类字段」更省、更稳、且概率可校准。

### 定位（必须记牢）

**这是「前置提示层」，不是「替代 LLM 层」。**

决策模型只会判断、不会回答，所以它与现有 Agent **并存**：
不动 `ReplyType`、不动 `AgentOutput`、不动 `TaskUpdate`、不改 `TaskStateReducer`。
最坏情况（模型效果差、接口挂了）必须能一键关闭并回到今天的行为。

---

## 一、可行性速评

| 维度 | 判断 | 依据 |
|---|---|---|
| 任务匹配度 | 高 | 意图分类 = 有限选项 + 要置信度，正是决策模型的设计场景 |
| 成本 | 极低 | 百炼 `decision-model-preview` 预览期免费；Jev 官方 $0.042/1M input tokens，输出不计费 |
| 延迟 | **需控预算** | Jev 约 70–500ms/次；但项目流式是「先跑完再分块」（`harness/run/streaming.py`），分类耗时 100% 落在首字延迟上 |
| 类目容量 | 充足 | 上限 255，我们只需 8 类（占 3%），余量留给细分子类 |
| 能力边界 | 明确 | 只能判断、不能回答 → 必须与原 Agent 并存 |
| 中文效果 | **待实测** | Jev 官方偏英文场景；百炼版本更贴合中文客服，必须用自有评测集验证 |
| 成熟度 | 有风险 | Jev 是 Early Access、百炼是 `-preview`，接口/配额可能变 → 必须可关闭、可降级 |

**结论**：先影子模式跑 1–2 周拿准确率与延迟分布，再决定是否开启影响模式。
模型效果不达预期时，**主流程零风险**。

---

## 二、旅游客服意图体系（8 类）

候选集严格对齐已有的 `TaskType`（`harness/tasks/schema.py:8-16`）与
`SKILL_CATALOG`（`harness/skills/catalog.py:30-98`），**不另造一套词汇**。

| # | intent code | 中文名 | 典型问法 | 对应 Skill | 主要工具 | 现有 TaskType |
|---|---|---|---|---|---|---|
| 1 | `product_consult` | 产品咨询 | 「北京有哪些亲子酒店」「这个门票含不含讲解」「三个人去三亚怎么玩」 | TRAVEL_PRODUCT_SERVICE | search_travel_products / get_travel_product / get_product_availability | PRODUCT_CONSULT |
| 2 | `order_query` | 订单查询 | 「我的订单到哪了」「帮我看看订单金额」「我有几个订单」 | ORDER_SERVICE | list_orders / get_order | ORDER_QUERY |
| 3 | `trip_info` | 行程信息 | 「明天几点出发」「在哪集合」「酒店几点能入住」「航班号多少」 | TRIP_SERVICE | get_trip_info / get_order | TRIP_INFO |
| 4 | `refund_request` | 退款/改期 | 「我要退这个订单」「能改到下周吗」「退款多久到账」 | REFUND_SERVICE | list_refunds / get_order | REFUND_REQUEST |
| 5 | `policy_query` | 规则政策 | 「退改政策是什么」「签证要什么材料」「行李能带多少」 | POLICY_SERVICE | search_knowledge | POLICY_QUERY |
| 6 | `complaint_ticket` | 投诉/售后 | 「我要投诉司机」「酒店和描述不符，要求赔偿」 | —（转人工） | — | COMPLAINT_TICKET |
| 7 | `human_handoff` | 转人工 | 「给我转人工」「叫个真人客服」 | — | — | —（新增） |
| 8 | `chitchat_other` | 闲聊/无关/兜底 | 「你好」「谢谢」「今天天气不错」「？？？」 | — | —（走原流程） | —（新增） |

### 为什么是这 8 类

- 前 5 类 + 投诉，**1:1 对应已有的 6 个 `TaskType` 和 5 个 Skill**，零新增词汇，映射表可机械生成；
- 第 7、8 类**必须有**：否则 argmax 会把「你好」「谢谢」硬贴成「产品咨询」，
  低置信度的样本也会被强行归类，无法与「真的分不清」区分开。

### 易混淆边界（必须写进 choice 的 options 描述）

| 边界 | 判据 |
|---|---|
| `refund_request` vs `policy_query` | 问「我这笔能不能退 / 多久到账」= 退款；问「你们平台一般怎么退」= 政策 |
| `refund_request` vs `order_query` | 诉求是退款/改期，**即使句子里带订单号**，也归退款 |
| `order_query` vs `trip_info` | 问**订单本身**（状态/金额/几个订单）= 订单；问**出行安排**（几点/在哪/住哪）= 行程 |
| `product_consult` vs `policy_query` | 问**具体产品**（某个酒店/门票/线路）= 产品；问**通用规则**（签证/行李/时效）= 政策 |
| `human_handoff` | 优先级最高：出现「转人工 / 真人客服」即命中，不看其他 |

### 255 上限怎么用（留余量，不改总量）

8 个主类只占 3%，剩下 97% 留给扩展：

- **细分子类**：两级命名，如 `product_consult__hotel` / `__ticket` / `__transfer`；
- **或同一次调用发多个问题**：决策模型支持一次传多个 question、各自独立概率分布，
  不需要增加类别总数。

### 同一次调用的附加问题（零额外延迟）

| 问题 | 类型 | 用途 |
|---|---|---|
| Q1 用户意图 | `choice`（8 选项） | 主输出 |
| Q2 是否明确要求转人工 | `noul` | 可提前挂 `REQUEST_HANDOFF` 提示 |
| Q3 用户情绪激烈程度 | `score`（1–5） | 投诉优先级排序（**先只记录，不启用**） |

> **注意**：`IntentCode` 与 `TaskType` 是**两套独立枚举**。
> 映射表只用于提示文案与冲突检测，**不互相污染**。

---

## 三、技术方案

### 3.1 插入点：`AgentExecutor.execute`，两阶段用不同并发策略

在 `harness/run/executor.py:52`（`compile_messages` 之后）插入分类调用。

> **必须在 `executor.py:57` 的 `asyncio.wait_for` 外层。**
> 否则分类超时会被 `executor.py:65-69` 统一转成 `AGENT_EXECUTION_FAILED`，**整轮对话直接失败**。

| 阶段 | 并发策略 | 延迟代价 |
|---|---|---|
| **Phase 1 影子模式** | `asyncio.gather(分类, 现有 _execute_with_correction)` 并发跑，结果**只写日志** | **0ms** |
| **Phase 2 影响模式** | 先分类 → 结果注入 state → 再 `ainvoke`（串行） | ≤ `intent_timeout_seconds`（建议 1.5s） |

**为什么不放 coordinator**：AGENTS.md §5.3 规定 coordinator 只做生命周期/状态机/事务/归约，
不发起模型调用；塞进去还会把模型调用拖进事务层，且此时 AgentRun 已 commit，意图无法随 run 落库。

**为什么不放 LangChain middleware**（Phase 2 备选）：`awrap_model_call` 在每轮纠错重试
都会重复触发，指标分散在图内，单测成本高。

### 3.2 降级与超时（best-effort 硬保证）

| 条件 | 行为 |
|---|---|
| `INTENT_ENABLED=False` | **零 HTTP**，行为与今天完全一致 |
| 分类输入为空/纯空白 | `status=skipped` |
| 超过 `intent_timeout_seconds` | `status=timeout`，不注入，主流程照常 |
| HTTP 4xx/5xx / 非 dict 响应 | `status=error`，不注入，主流程照常 |
| confidence < `intent_confidence_threshold` | **完全不注入**，输出与今天逐字节一致（零回归基线） |

**分类输入口径**：`当前消息文本 + 当前任务状态摘要`。

- 理由是用户会说「那明天呢」「换一个」「就这个吧」，单看这一句无法分类；
- 复用 `ContextCompiler._build_task_context`（`harness/run/context.py:47-71`）渲染任务状态，
  复用 `_build_current_message`（`harness/run/context.py:126-147`）得到当前问句；
- 分类器内部**绝不抛异常**，只返回 `TurnIntent | None`。

**双阈值**：`intent_confidence_threshold`（0.60，进提示门槛）、
`intent_strong_threshold`（0.85，工具收窄门槛）。

### 3.3 分类结果的消费方式（从软到硬）

| 层 | 位置 | 用法 |
|---|---|---|
| **软**（Phase 2 先做） | `harness/run/runtime.py:20-24` 的 `AgentExecutionState` 加 `turn_intent`；`harness/dynamic_prompt.py:39-43` 追加【本轮意图】段 | 只读提示，不改工具集 |
| **中**（Phase 2 可选，开关 `INTENT_TOOL_SCOPE_ENABLED`） | `harness/skills/middleware.py:34-43`，当 `active_skill_code is None` 且 strong 时按 INTENT→SkillCode 预置 | 复用成立：`load_skill` 始终在白名单，模型仍可自行切换 |
| **硬** | —— | **拒绝**：不注入 `TaskUpdate`、不改 `TaskStateReducer` |

### 3.4 冲突裁决（意图 vs 多轮任务）

- `task_state.active` 存在时，**多轮任务优先**；
- 提示词明写：「意图仅供路由参考，与当前任务冲突时以任务为准，除非用户明确切换」；
- `START` / `SWITCH` 仍**只由主 LLM 的 `TaskUpdate`**（`llm/output.py:69-95`）决定，服务端不改写。

---

## 四、影响面（尚未落地，以下为计划改动）

| 项目 | 改动类型 | 具体内容 | 是否影响现有流程 |
|---|---|---|---|
| ai-service | 新增文件 | `infrastructure/intent_client.py` | 否 |
| ai-service | 新增文件 | `harness/intent/{schema,catalog,classifier,prompt,__init__}.py` | 否 |
| ai-service | 新增文件 | `eval/datasets/intent_classification.csv`、`eval/intent_eval.py`、`tests/test_intent_classifier.py` | 否 |
| ai-service | 修改配置 | `common/config.py` 加 intent 字段；`.env.example` 同步 | 否，纯新增字段 |
| ai-service | 修改文件 | `harness/run/executor.py`（`__init__` 增参；`:52-64` 分类调用；`:141-144` 入参带 `turn_intent`） | Phase 1 **否**（并发 + 只记日志）；Phase 2 是 |
| ai-service | 修改文件 | `harness/validator/output.py:10-17` —— `ValidatedAgentOutput` 加 `turn_intent`（**不要污染 `AgentOutput`**，那是 LLM 的 schema） | 否，附加字段 |
| ai-service | 修改文件 | `harness/run/runtime.py:20-24` 加 `turn_intent: NotRequired[TurnIntent]` | 否 |
| ai-service | 修改文件 | `harness/dynamic_prompt.py:39-43`（仅 Phase 2） | Phase 2 是 |
| ai-service | 修改文件 | `app/dependencies.py:35-43` —— 新增 `get_intent_classifier()` + `IntentClassifierDep` 并注入 `get_agent_executor` | 否 |
| ai-service | 修改文件 | `harness/skills/middleware.py:34-43`（仅 Phase 2，且默认开关关闭） | Phase 2 是 |
| customer-service / tourism-service / 前端 | 不动 | —— | 否 |

**分层合规**：`harness/` → `infrastructure/` 是**允许方向**
（`harness/knowledge/service.py` 已有此写法），反向禁止。所以 API client 放 `infrastructure/`。

---

## 五、配置项

`common/config.py`（`Settings`，`env_file=.env`，`get_settings()` 带 `lru_cache`）+ `.env.example`：

```env
INTENT_ENABLED=True
INTENT_BASE_URL=https://dashscope.aliyuncs.com
INTENT_API_KEY=
INTENT_MODEL=decision-model-preview
INTENT_TIMEOUT_SECONDS=1.5          # gt=0
INTENT_CONFIDENCE_THRESHOLD=0.60    # ge=0 le=1
INTENT_STRONG_THRESHOLD=0.85        # ge=0 le=1
INTENT_TOOL_SCOPE_ENABLED=False     # Phase 2 开关
INTENT_MAX_CANDIDATES=10            # le=255
```

`INTENT_ENABLED` 是**总闸**：置 `False` 即完全回到今天的行为，用于应急回滚。

---

## 六、可观测性

`models/models.py` 现有 `input_context` / `result` / `latency_ms` / `error`，**没有 intent 字段**。

| 阶段 | 做法 |
|---|---|
| Phase 1 | `logger.info("intent.classified run_id=%s intent=%s confidence=%.3f latency_ms=%d status=%s")`，**只写日志不建列** |
| Phase 2 | 需要 SQL 聚合时再加**单列 `intent_result JSONB`**（优于拆 3 列），在 `coordinator.py:97` 后与 token 指标一并写入 |

两条红线：

1. **禁止写进 `agent_run.result`** —— 那是给 Customer Service 的事件契约；
2. 项目**无 alembic**，`infrastructure/init_db.py` 只有 `create_all`，**存量表不会自动加列**，
   需补幂等 `ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS intent_result JSONB`。

---

## 七、分阶段落地

### P0：打通协议（必须先做，半天）

用 curl / 小脚本打通 `POST {INTENT_BASE_URL}/compatible-mode/v1/systemone`，确认：

1. `state` + `questions`（`choice` / `noul` / `score`）的**真实字段名**；
2. 响应体里**概率分布与 confidence 的位置**；
3. **中文 option 文案**的实际效果；
4. 单次真实耗时（决定 1.5s 预算是否够）。

> **解析层与 client 分离**，协议变动时只改一处。

### P1：影子模式（1–2 天 + 1–2 周观察）

1. 建 `infrastructure/intent_client.py` + `harness/intent/*`；
2. `executor.py` 用 `asyncio.gather` 与主流程并发跑分类，结果**只落日志**（0ms 延迟代价）；
3. `dependencies.py` 注入；`INTENT_ENABLED` 默认 `True`；
4. 单测 + 评测集；拿真实流量日志统计**准确率与延迟分布**。

**是否进入 P2 的判据**：加权准确率 ≥ 90%、低置信度率 ≤ 10%、P99 ≤ 1.5s。

### P2：影响模式（评测达标后）

5. 串行化：先分类 → 注入 `AgentExecutionState.turn_intent` → `dynamic_prompt` 追加【本轮意图】段；
6. 视效果开 `INTENT_TOOL_SCOPE_ENABLED`，用 `SkillScopeMiddleware` 预置 Skill；
7. 加 `intent_result JSONB` 列 + 幂等 DDL，把 intent 指标随 AgentRun 落库。

---

## 八、测试与验收

### 单元测试（`httpx.MockTransport` 注入 `IntentClient`）

| 用例 | 期望 |
|---|---|
| 正常响应 argmax | `intent` 正确、`distribution` 完整 |
| confidence < 弱阈值 | 不注入，`status=low_confidence` |
| `wait_for` 超时 | **不抛异常**，`status=timeout` |
| HTTP 500 / 非 dict 响应 | `status=error` |
| `INTENT_ENABLED=False` | **0 次 HTTP** |
| 分类器内部抛异常 | executor 仍正常返回 |
| 回归（关键） | `INTENT_ENABLED=False` 与「`True` 但低置信度」两种配置下，最终 `reply_content` / `task_state` **逐字节一致** |

### 离线评测（`eval/`）

- 数据集：`eval/datasets/intent_classification.csv`，列沿用 `eval/cases.py:4-19` 风格
  （`case_id` / `question` / `history` / `expected_intent` / `scene` / `note`），
  8 类各 ≥ 20 条，含多轮追问、寒暄、转人工、投诉难例；
- 脚本：`eval/intent_eval.py`，**只跑分类器**（不经过 `agent_runner.py`），
  输出 `eval/results/<ts>/{result.csv,summary.json}`，含准确率、混淆矩阵、置信度分布、延迟分位。

### 验收指标

| 指标 | 门槛 |
|---|---|
| 加权准确率 | ≥ 90% |
| 低置信度率 | ≤ 10% |
| 分类 P99 延迟 | ≤ 600ms（本地）/ ≤ 1.5s（线上） |
| `tests/` | 全绿 |
| 现有 eval 基线 | 不下降 |

### 端到端验证

1. `cd 4组_陶桓_旅游_客服/ai-service && uv run pytest tests/test_intent_classifier.py -v`
2. `uv run python eval/intent_eval.py --dataset eval/datasets/intent_classification.csv`
3. 起 ai-service（`uv run python -m atguigu.main`，8002；Windows 走 `common/event_loop.py` 的 `run_async`），
   经 Customer Service 发 8 类各 3 条真实问句，核对日志里的 `intent` / `confidence` / `latency_ms`；
4. 对比开/关 `INTENT_ENABLED` 时的 `agent_run.latency_ms` 分布：
   **P1 应无显著差异，P2 增量 ≤ 1.5s**。

---

## 九、明确不做

- 不替换 `ReplyType` / `AgentOutput` / `TaskUpdate`，不改 `TaskStateReducer`；
- 不让意图分类改写 `TaskUpdate.task_type`（服务端不改写主 LLM 的判断）；
- 不把分类调用放进 `AgentCoordinator`，也不放进事务边界；
- 不把 intent 写进 `agent_run.result`（那是 Customer Service 的事件契约）；
- 不在 Phase 1 冻结/收窄工具集（影子模式只记录）；
- 不对非文本业务对象消息做特殊分支之外的处理（复用 `_format_message` 统一渲染）；
- 不引入第二套 SDK（client 用 httpx，与 `knowledge_client.py` 一致）；
- 不改 customer-service / tourism-service / 前端。

---

## 十、风险与未决

| # | 风险 | 应对 |
|---|---|---|
| 1 | **协议未落地**：SystemOne 的 `state` / `questions` 字段名仓库内无既有实现 | 先做 P0 curl 打通，再写解析层；解析与 client 分离 |
| 2 | **首包延迟**：流式是「先跑完再分块」，P2 的 1.5s 直接加在首字延迟上 | P1 并发零代价；P2 下调预算或对长输入跳过分类 |
| 3 | **双轨冲突**：`IntentCode` 与 `TaskType` 两套枚举 | 强优先级提示词 + 「任务进行中问别的」评测样本 |
| 4 | **P2 预置 `active_skill_code` 会抢走模型自主路由**，切换需多一次 `load_skill` | 占用 `agent_tool_call_limit=8` 预算，需评估是否调至 12 |
| 5 | **模型成熟度**：Jev Early Access、百炼 `-preview` | 保留 `INTENT_ENABLED` 一键关闭，`.env.example` 注明 |
| 6 | **无 alembic**：加列需幂等 DDL | 在 `init_db.py` 补 `ADD COLUMN IF NOT EXISTS` |
| 7 | **中文效果未验证** | 评测集必须覆盖中文口语、缩写、错别字 |

---

## 附：与 Day10/Day11 Harness 的交叉影响

1. **纠错重试会重复触发分类**：若做成 middleware 方案，`max_correction_attempts=2` 会让分类跑多次。
   放 executor 层 + 在进入纠错循环之前只调一次，可规避。
2. **`agent_run_timeout_seconds=90` 是整轮上限**：分类预算必须从这 90s 里扣，
   不能另开一个不受控的超时。
3. **`agent_tool_call_limit=8` 含 `load_skill`**：P2 预置 Skill 后模型切换多一次调用，
   跨 Skill 场景更容易顶满。
4. **`AnswerValidator` 只看工具证据，看不懂意图**：意图只影响路由，不参与事实校验，
   两条链路互不干扰。
