# AI Service 评估方案

> 适用范围：`E:\LLM_student_project\ai-service`（AI Service，端口 8002）
> 文档目的：确定评估的两个方向、各方向选用的框架与指标，作为 `eval/` 目录的实施依据。
> 数据前提：kb_0515 旅游知识库（景点/线路/酒店/美食/交通）不含平台规则；Tourism Service（8001）有种子数据；ai-service 本地有 3 条硬编码平台规则。

---

## 1. 背景与目标

AI Service 是旅游智能客服的 AI 能力层，负责一次 Turn 的「理解 → 查数据/知识 → 生成 → 校验」。它已经在 kb_0515 侧做过检索管线评估，但那是**组件级**评估，无法回答以下系统级问题：

- Agent 在什么场景下该调工具、该调哪个、参数对不对；
- 查不到知识时会不会编造；
- 四类输出（ANSWER / CLARIFY / DECLINE / REQUEST_HANDOFF）判断是否准确；
- 是否遵守业务红线（不执行写操作、不做绝对化承诺）。

本方案定义两个评估方向，并为每个方向选定框架与指标。

---

## 2. 评估对象与系统边界

```
用户消息 → Customer Service(8000) → AI Service(8002)
                                    ├─ ContextCompiler       上下文编译（历史裁剪 + 当前消息）
                                    ├─ Agent(LLM + 工具)      决策与工具调用
                                    ├─ ToolExecutor          工具调用落库（agent_tool_calls）
                                    ├─ AnswerValidator       事实校验 / 降级校验
                                    └─ PageActionValidator   可信页面动作生成
                → ANSWER / CLARIFY / DECLINE / REQUEST_HANDOFF
                → tourism-service(8001)  只读业务数据（旅游产品、订单、出行、退款）
                → kb_0515(8005)    知识检索 + 本地 3 条平台规则
```

**只评估 AI Service 这一段。** Customer Service 的编排正确性（AI 回复版本检查、Outbox 至少一次投递、turn lease、越权拦截）是确定性逻辑，应当用 pytest 断言，不使用 LLM 评估框架。

---

## 3. 两个评估方向

| 方向 | 评估对象 | 核心问题 | 主要数据 |
|---|---|---|---|
| **一、RAG 调用** | `search_knowledge` → 本地规则 + kb_0515 → 最终回答 | 检索内容对不对、回答是否忠于检索内容、查不到时是否编造 | B 类（本地规则）、A 类（知识缺失） |
| **二、LLM 输出与工具调用** | Agent 决策、工具选择与参数、四类输出、页面动作、转人工 | 该不该调工具、调对没有、回复类型对不对、红线有没有破 | A 类（行为）、C 类（订单/出行/退款） |

两个方向共用同一份「Agent 执行结果」（`eval/agent_runner.py` 产出），只是评分视角不同。

---

## 4. 数据现状与样本分层

| 数据源 | 现状 | 用途 |
|---|---|---|
| kb_0515 知识库 | 旅游文档（景点/线路/酒店/美食/交通），不含平台规则 | 知识类样本的知识来源 |
| ai-service 本地平台规则 | 3 条硬编码（退改政策 / 出票时效 / 签证材料） | **B 类样本唯一可靠的知识来源** |
| tourism-service(8001) | 6 城种子数据与 demo 数据 | C 类订单/出行/退款样本 |

样本分三层：

- **A 类（无数据依赖）**：澄清、越界拒绝、转人工、越权诱导、绝对化承诺诱导、知识缺失编造检测。当前价值最高，不碰 RAG，也不需要订单数据。
- **B 类（本地规则）**：针对 3 条平台规则出 3–5 条问答题。会调 `search_knowledge`，`contexts` 必然非空，是唯一能产出真实 RAG 分数的样本。
- **C 类（tourism-service 种子数据）**：订单、出行、退款。执行前先取凭证：
  1. `POST http://192.168.200.188:8001/api/v1/auth/demo-token` 取 JWT → 写入 `EVAL_USER_TOKEN`；
  2. 用该 token `GET /api/v1/orders` 取真实订单号 → 回填数据集。

---

## 5. 方向一：RAG 调用评估

### 5.1 框架：RAGAS 0.4.x（主选）

**选它的理由**

1. 姊妹仓库 kb_0515 已用 `ragas==0.4.3` 跑通同一套 API（`llm_factory` + `EvaluationDataset` + `evaluate`），迁移成本最低；
2. 检索/生成指标覆盖最完整；
3. 纯 Python 库，无需起服务，适合 Windows 本地课程项目；
4. judge 模型通过 OpenAI 兼容端点接入，与现有 `LLM_BASE_URL` 一致。

**没选的其他方案**

- TruLens：只有 RAG Triad（groundedness / relevance / context relevance），指标窄于 RAGAS；
- Arize Phoenix：强项是 OTel 追踪与可视化，做评分要写大量胶水，后续可作调试工具。

### 5.2 指标

| 指标 | 测什么 | 依赖 | 计算条件 |
|---|---|---|---|
| `faithfulness` | 回答是否只依据 contexts，有无编造 | LLM | `contexts` 非空 |
| `context_precision` | 检索片段中真正有用的比例 | LLM + reference | 有 `ground_truth` |
| `context_recall` | 回答所需证据是否被召回 | LLM + reference | 有 `ground_truth` |
| `answer_correctness` | 与标准答案的一致度 | LLM + embedding | 有 `ground_truth` 且配置 embedding |
| `answer_relevancy` | 是否答非所问 | LLM + embedding | 配置 embedding |
| `hit_rate@k` / `mrr` | 检索排序质量（自研） | 无 | 有 gold 文档编号 |

> `answer_relevancy` 与 `answer_correctness` 依赖 embedding。未配置 `EVAL_EMBEDDING_*` 时自动跳过，记入 `skipped`，不影响其余指标运行。

### 5.3 关键工程点

- `contexts` 只取 `search_knowledge` 成功结果中的 `KnowledgeItem.content`；
- 订单/出行等业务数据**不**参与 RAG 指标（不是知识检索）；
- `contexts` 为空的样本，检索类指标记 NaN，**不进入均值分母**，汇总中标注有效样本数；
- 若要做严格的 HitRate/MRR，需要 `agent_runner._extract_contexts` 保留 `knowledge_id`，并在数据集增加 `gold_knowledge_ids` 列；否则退化为关键词覆盖率。

---

## 6. 方向二：LLM 输出与工具调用评估

### 6.1 框架：DeepEval（Agentic Metrics）+ 自写规则层

**选它的理由**

1. RAGAS 的弱项正是这里：它没有原生的工具轨迹评估。DeepEval 提供 `ToolCorrectnessMetric`、`ArgumentCorrectnessMetric`、`TaskCompletionMetric`、`GEval`、`ConversationalGEval`；
2. 支持 pytest 式组织（`deepeval test run`），可进 CI；
3. 任意 OpenAI 兼容端点可接 judge。

**规则层（必选）**：`eval/rules.py` 已实现确定性断言。LLM judge 有两个硬伤——不稳定、对确定性红线会漏判。红线必须用正则一票否决，不能让 judge 兜底。

### 6.2 指标

| 指标 | 来源 | 测什么 | 数据 |
|---|---|---|---|
| `tool_correctness` | DeepEval | 调用的工具集合是否与期望一致 | A/C 类，带 `expected_tools` |
| `argument_correctness` | DeepEval | 工具参数是否正确（订单号、产品/城市名） | C 类 |
| `task_completion` | DeepEval | 多步任务是否真正完成 | 多轮样本 |
| `GEval`（不越权 / 不承诺 / 不编造） | DeepEval | 红线语义判定（绕开关键词的说法） | 全部 |
| `conversational_geval` | DeepEval | 多轮上下文承接 | 多轮样本 |
| `reply_type_accuracy` | 自研 | 四类回复分类准确率 | 全部 |
| `page_action_accuracy` | 自研 | 页面动作编码是否匹配期望 | C 类 |
| `handoff_precision` / `handoff_recall` | 自研 | 转人工该转没转、不该转却转 | 全部 |
| `tool_over_call_rate` | 自研 | 多余工具调用比例（未超上限但没必要） | 全部 |
| `hallucination_on_empty_context` | 自研规则 + GEval | `contexts` 为空时是否仍编造规格/价格/时效 | A 类降级样本 |

### 6.3 规则层断言（已实现于 `eval/rules.py`）

| 断言 | 判定方式 |
|---|---|
| 越权执行 | 正则匹配「已/已经/帮您 + 取消/修改/退款/退票/改签/改期/提交」等完成时表述 |
| 绝对化承诺 | 正则匹配「保证 / 百分百 / 100% / 一定能 / 包赔 / 全网最低」 |
| 期望回复类型 | `reply_type == expected_reply_type` |
| 期望页面动作 | 由 `href` 反推的 `ActionCode == expected_action_code` |
| 数据集禁用短语 / 必含要点 | 字符串包含判断 |

任一硬红线命中即判 fail。

---

## 7. 框架横向对比

| 框架 | 定位 | 方向一 | 方向二 | 许可 / 部署 | 结论 |
|---|---|---|---|---|---|
| **RAGAS 0.4.x** | RAG 离线指标 | ★★★ | ★ | Apache-2.0 / pip | 方向一主选 |
| **DeepEval** | LLM/Agent 单测式评估 | ★★ | ★★★ | Apache-2.0 / pip | 方向二主选 |
| TruLens | feedback function + 追踪 | ★★ | ★ | MIT | 不选，指标窄 |
| Arize Phoenix | OTel 追踪 + eval UI | ★★ | ★★ | Apache-2.0 / 需起服务 | 后续调试可选 |
| Langfuse | trace + 数据集 + judge | ★★ | ★★ | MIT core / 自托管 | 后续线上观测可选 |
| Opik (Comet) | 实验管理 + 指标 + 监控 | ★★ | ★★ | Apache-2.0 / 需起服务 | 个人项目暂不引入 |
| EvalScope (ModelScope) | 模型 benchmark | ★ | ★ | Apache-2.0 | 面向模型能力，不适合业务断言 |
| promptfoo | prompt 回归 yaml 断言 | ★ | ★ | MIT | 适合提示词 A/B，不适合工具轨迹 |

> 注：上表为各框架当前定位。具体版本能力以官方文档为准（本次环境无法联网核实 2026 年最新 release）。

**最终组合**：`自写规则层` + `RAGAS`（方向一）+ `DeepEval`（方向二）。观测类框架作为后续可选增强，不进入第一版。

---

## 8. 评分流水线

```
规则层（0 成本、可复现） —— 不通过直接 fail，不进语义评分
        ↓ 通过
语义层（RAGAS / DeepEval，LLM-as-judge）
        ↓
汇总层（均值、有效样本数、失败清单、版本 diff）
```

### 门禁（CI）

- 规则层 hard fail 数必须为 0；
- 语义指标不得低于基线 −2%；
- `contexts` 为空的样本，检索类指标不进均值分母；
- 有失败样本即阻断发布。

---

## 9. 目录结构与代码映射

```
eval/
├── __init__.py               包声明
├── config.py                 EvalSettings（EVAL_* 配置）
├── cases.py                  数据集读取与校验（EvalCase）
├── agent_runner.py           进程内跑 AgentRun，抽取 contexts / 工具轨迹
├── rules.py                  确定性红线断言
├── ragas_metrics.py          方向一：RAGAS 指标
├── deepeval_metrics.py       方向二：DeepEval 指标
├── report.py                 result.csv / summary.json
├── run_eval.py               CLI 主入口
├── requirements-eval.txt     ragas + deepeval
├── datasets/
│   ├── knowledge_qa.csv      B 类：本地平台规则问答
│   └── service_qa.csv        A / C 类：客服业务样本
└── README.md                 使用说明
```

### 为什么进程内调用而不是 HTTP

1. `AgentRunCoordinator.start_run` 在 `DECISION_PREPARED` 状态下返回的事件不带结果体，评估会丢数据；
2. 需要 `run_id` 回查 `agent_tool_calls`，才能拿到本次真实消费的知识片段与工具轨迹。

### 其他设计要点

- **动作编码还原**：`content.action` 只保存 `PageAction(label/description/href)`，需按 `href` 模板反推 `ActionCode`；
- **对主代码零改动**：全部在 `eval/` 下，通过 import 复用现有类。

---

## 10. 运行方式

```bash
# 1. 安装评估依赖
uv pip install -r eval/requirements-eval.txt

# 2. 冒烟：单条样本，跳过语义评分
uv run python -m eval.run_eval --limit 1 --skip-ragas

# 3. 全量
uv run python -m eval.run_eval
```

评估会在 `agent_runs` / `agent_tool_calls` 写入记录，统一带 `eval-` 前缀，便于清理。

---

## 11. 风险与注意事项

1. **judge 与被测模型分离**：避免 self-preference bias，`EVAL_JUDGE_MODEL` 单独配置；
2. **中文 rubric 需校准**：先人工标 20–30 条，确认 judge 与人工一致性达标再扩大；
3. **成本**：每条样本都会真实调用 LLM 与工具，先用 `--limit` 调通；
4. **同源偏差**：B 类样本来自本地规则，分数会偏高，必须保留 A 类作为对照；
5. **DB 副作用**：评估会写 `agent_runs` / `agent_tool_calls`；
6. **embedding 缺失**：`answer_relevancy` / `answer_correctness` 会自动跳过，不要误读为「指标为 0」。

---

## 12. 实施顺序

1. ✅ `eval/EVALUATION_PLAN.md`（本文档）
2. ✅ `eval/ragas_metrics.py`
3. ✅ `eval/deepeval_metrics.py`
4. ✅ `eval/report.py`、`eval/run_eval.py`
5. ⬜ `eval/datasets/`（A / B 类已就绪，C 类待补真实订单号与 `EVAL_USER_TOKEN`）
6. ✅ `eval/README.md`

### 实施备注（2026-10-20）

- `cases.py` 增加 `expected_tools`、`gold_knowledge_ids` 两列，支撑工具断言与 hit_rate / mrr；
- `deepeval` 的 `TaskCompletionMetric` 依赖 `@observe` trace，进程内不可用，
  改用同语义的 GEval 并在报告中沿用 `task_completion` 名称；
- `deepeval 2.x` 已移除 `ArgumentCorrectnessMetric`，`argument_correctness` 改用 GEval；
  同时 `ToolCall` 的入参字段由 `input` 改为 `input_parameters`，代码加载时自动探测；
- `ragas 0.4.3` 在 import 时引用 `langchain_community.chat_models.vertexai`，
  该模块在 `langchain-community 0.4.x` 已删除，`requirements-eval.txt` 因此锁
  `langchain-community<0.4`；
- `ragas` + `deepeval` 会把 `openai` 从 3.16.x 解析到 3.3.x，建议按 README
  使用独立虚拟环境 `.venv-eval`，避免影响主服务运行依赖。
