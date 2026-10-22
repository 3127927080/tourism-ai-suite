# AI Service 离线评估

评估 AI Service 的**整条 Agent 链路**（上下文编译 → Agent 决策 → 工具调用 → 输出校验），
不是 kb_0515 的检索管线。方案设计见 [EVALUATION_PLAN.md](EVALUATION_PLAN.md)。

## 两个方向

| 方向 | 评估内容 | 指标来源 |
|---|---|---|
| 一、RAG 调用 | `search_knowledge` 的检索内容与最终回答 | RAGAS + 本地 hit_rate / mrr |
| 二、输出与工具调用 | 工具选择、参数、四类回复、页面动作、转人工 | 规则层 + DeepEval |

规则层先跑：确定性红线（越权执行、绝对化承诺、回复类型、页面动作、必备工具）
不通过直接判失败，不进入语义评分。

## 安装

```powershell
uv pip install -r eval/requirements-eval.txt
```

> **依赖冲突提醒**：`deepeval` 与 `ragas` 的解析结果会把 `openai` 从 3.16.x
> 降到 3.3.x，并调整 `jiter` / `tenacity`。如果不想动主服务虚拟环境，
> 推荐单独建一个评估环境：
>
> ```powershell
> uv venv .venv-eval
> uv pip install --python .venv-eval\Scripts\python.exe `
>   fastapi uvicorn httpx pydantic-settings langchain langchain-openai `
>   langchain-deepseek "psycopg[binary]" pyjwt sqlalchemy `
>   -r eval/requirements-eval.txt
> .venv-eval\Scripts\python.exe -m eval.run_eval --limit 1
> ```
>
> `requirements-eval.txt` 里锁了 `langchain-community<0.4`：`ragas 0.4.3`
> 在 `import ragas` 时会引用 `langchain_community.chat_models.vertexai`，
> 该模块在 0.4.x 已被移除，不锁版本会直接抛
> `ModuleNotFoundError: langchain_community.chat_models`。

未安装语义依赖时，`--skip-ragas --skip-deepeval` 仍可完整运行规则层。

## 配置

复用项目 `.env`，额外支持以下变量：

| 变量 | 说明 |
|---|---|
| `EVAL_USER_ID` | 样本默认用户身份，默认 `eval-customer` |
| `EVAL_USER_TOKEN` | 真实用户 JWT；订单 / 出行 / 退款类样本必需 |
| `EVAL_JUDGE_MODEL` | judge 模型，留空复用 `LLM_MODEL` |
| `EVAL_JUDGE_BASE_URL` | judge 端点，留空复用 `LLM_BASE_URL` |
| `EVAL_JUDGE_API_KEY` | judge 密钥，留空复用 `LLM_API_KEY` |
| `EVAL_EMBEDDING_MODEL` | embedding 模型名；不配置则跳过依赖 embedding 的指标 |
| `EVAL_EMBEDDING_BASE_URL` | embedding 端点 |
| `EVAL_EMBEDDING_API_KEY` | embedding 密钥，留空复用 `LLM_API_KEY` |
| `EVAL_TIMEOUT_SECONDS` | 单次 judge 调用超时，默认 180 |

建议让 judge 使用与被测模型不同的模型，降低 self-preference bias。

## 数据集

`eval/datasets/*.csv`，一行一条样本：

| 列 | 说明 |
|---|---|
| `case_id` | 唯一编号 |
| `scene` | 场景标签：`policy` / `knowledge_missing` / `handoff` / `red_line` / `order` / `trip` / `refund` / `product` ... |
| `question` | 用户本轮问题 |
| `ground_truth` | 参考回答；为空则跳过需要 reference 的指标 |
| `expected_reply_type` | 期望回复类型，留空不校验 |
| `expected_action_code` | 期望页面动作编码，留空不校验 |
| `expected_tools` | 必须调用的工具，`\|` 分隔；缺任一项规则层失败 |
| `gold_knowledge_ids` | 期望召回的知识编号，`\|` 分隔；用于 hit_rate / mrr |
| `forbidden_phrases` | 禁止出现的短语，`\|` 分隔 |
| `must_include` | 必须出现的要点，`\|` 分隔 |
| `history` | 多轮历史 JSON：`[{"role":"user","content":"..."}]` |
| `user_id` | 样本用户身份，留空用 `EVAL_USER_ID` |
| `note` | 备注 |

### 样本分层

- **A 类（无数据依赖）**：越界、投诉转人工、越权诱导、绝对化承诺、提示词注入、知识缺失。
- **B 类（本地平台规则）**：3 条硬编码规则（退改政策 / 出票时效 / 签证材料），
  `contexts` 必然非空，是唯一能产出真实 RAG 分数的样本。
- **C 类（tourism-service 种子数据）**：订单、出行、退款、页面动作。执行前需要：

```powershell
# 1. 取 demo 用户 token
curl -X POST http://192.168.200.188:8001/api/v1/auth/demo-token -H "Content-Type: application/json" -d '{"user_id":"user_001","role":"customer"}'
# 2. 写入 .env
EVAL_USER_TOKEN=<上一步返回的 JWT>
EVAL_USER_ID=user_001
# 3. 取真实订单号，替换 service_qa.csv 中的 ORD_SANYA_001
curl -H "Authorization: Bearer <token>" http://192.168.200.188:8001/api/v1/orders
```

未配置 `EVAL_USER_TOKEN` 时 C 类样本会因工具 401 降级为拒绝回复，规则层会失败——
这是预期行为，不是评估脚本的 bug。

## 运行

```powershell
# 冒烟：一条样本，跳过语义评分
uv run python -m eval.run_eval --limit 1 --skip-ragas --skip-deepeval

# 规则层全量
uv run python -m eval.run_eval --skip-ragas --skip-deepeval

# 只跑知识类
uv run python -m eval.run_eval --scene policy --scene knowledge_missing

# 全量（含 RAGAS + DeepEval）
uv run python -m eval.run_eval
```

参数：`--datasets`、`--scene`、`--case-id`、`--limit`、`--skip-ragas`、
`--skip-deepeval`、`--output-dir`。

退出码：规则层有任何失败返回 `1`，全部通过返回 `0`，可直接用于 CI 门禁。

## 产物

输出到 `eval/results/<时间戳>/`（已加入 `.gitignore`）：

- `result.csv`：一行一条样本，含规则明细、工具轨迹、检索分数、语义分数；
- `summary.json`：规则通过率、按场景分组、各指标均值 / 有效样本数、
  转人工 precision / recall、多余工具调用比例、失败清单。

语义指标的聚合口径：NaN 与异常不计入均值分母；`contexts` 为空的样本不进入
检索类指标分母；未配置 embedding 时 `answer_relevancy` / `answer_correctness` 不出现。

## 实现说明与已知限制

1. **进程内执行而不是 HTTP**：`start_run` 在 `DECISION_PREPARED` 时事件不带结果体，
   且评估需要 `run_id` 回查 `agent_tool_calls`，因此直接复用
   `AgentRunCoordinator` / `AgentExecutor` / `AgentOutputValidator`。
2. **动作编码还原**：结果里只有 `PageAction(label/description/href)`，
   `agent_runner.action_code_from_href` 按路径模板反推 `ActionCode`。
3. **TaskCompletion 用 GEval 代替**：DeepEval 新版的 `TaskCompletionMetric` 是轨迹指标，
   依赖 `@observe` 追踪；进程内调用拿不到 trace。这里用同语义的 GEval，
   报告里仍叫 `task_completion`。
4. **参数正确性用 GEval 代替**：DeepEval 2.x 已移除 `ArgumentCorrectnessMetric`，
   `argument_correctness` 改由 GEval 评判 `tools_called` 的入参是否贴合用户诉求；
   `tool_correctness` 仍是 DeepEval 的 `ToolCorrectnessMetric`（纯规则计算，不调模型）。
5. **多轮承接**：历史消息会拼进 `input`（`【对话历史】… 【当前用户消息】…`），
   让 GEval 能看到上下文；没有使用 `ConversationalGEval`，避免依赖完整对话 trace。
6. **版本适配**：已在 `ragas==0.4.3` + `deepeval==2.9.3` 下验证指标装配与
   `LLMTestCase` / `ToolCall` 字段（2.x 的工具入参字段是 `input_parameters`，
   旧版是 `input`，`deepeval_metrics` 加载时自动探测）。
7. **知识缺失样本**：`kb_0515` 只含 hak180 文档，因此商品知识类样本以
   「明确说明无法确认」为正确口径，而不是「答对规格」。
8. **同源偏差**：B 类样本来自本地 3 条平台规则，分数会偏高，必须保留 A 类对照。
9. **DB 副作用**：评估会真实写入 `agent_runs` / `agent_tool_calls`，
   `conversation_id` 统一为 `eval-conv-<case_id>`，便于清理：

```sql
DELETE FROM agent_tool_calls
WHERE run_id IN (SELECT id FROM agent_runs WHERE conversation_id LIKE 'eval-conv-%');
DELETE FROM agent_runs WHERE conversation_id LIKE 'eval-conv-%';
```
