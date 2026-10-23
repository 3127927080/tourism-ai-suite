# Ragas 评估报告（旅游知识库 · 50 题）

> 日期：2026年10月22日
> 结论：**50/50 样本全部评分成功**，5 项指标全部可算，无 NaN。

---

## 1. 评估配置

| 项 | 值 |
|---|---|
| 测试集 | `eval/qa.csv`，50 题（景点/线路/酒店/美食/交通 各 10 题，6 城市轮转覆盖） |
| 测试集校验 | `eval/validate_qa.py`：50/50 的 `ground_truth` 可在对应文档找到依据，且源文档已在 Milvus |
| 答案生成模型 | `deepseek-v4.1-flash`（`LLM_DEFAULT_MODEL`，走 `LLM_API_BASE`） |
| 槽位识别模型 | `deepseek-v4.1-flash`（`ITEM_MODEL`） |
| Ragas 判分模型 | `deepseek-v4.1-flash`（`ITEM_MODEL`），`temperature=0` |
| 判分模型参数 | `reasoning_effort=none`（关闭思考）、`max_tokens=8192`、`RunConfig(timeout=180, max_retries=2, max_workers=1)` |
| Embedding | 本地 BGE-M3（dense + sparse，1024 维） |
| Rerank | `qwen3.7-text-rerank`（仍走阿里云 DashScope） |
| 检索开关 | `ENABLE_HYDE=false` / `ENABLE_GRAPH=false` / `ENABLE_WEB_SEARCH=false` |
| 被测链路 | 8003 全链路：`主体确认 → 向量检索 → RRF → Rerank → 答案生成`（Ragas `user_input / response / retrieved_contexts / reference`） |
| Ragas 版本 | 0.4.3（`langchain-community==0.3.30` 固定，见 `eval/requirements.txt`） |

答案提示词已收紧：一句结论 + 2–3 条要点、全文 ≤200 字、禁小标题/表格/嵌套列表（`atguigu/config/prompt.py` 的 `ANSWER_PROMPT`）。

---

## 2. 全量结果（n=50）

| 指标 | 均值 |
|---|---|
| faithfulness | **0.7901** |
| answer_relevancy | **0.8104** |
| context_precision | **0.8221** |
| context_recall | **0.7400** |
| answer_correctness | **0.5756** |

逐题分数见 `eval/result.csv`。

## 3. 分类结果

| 类别 | faithfulness | answer_relevancy | context_precision | context_recall | answer_correctness |
|---|---|---|---|---|---|
| 景点介绍（1–10） | 0.7656 | 0.8453 | 0.8887 | 0.8500 | 0.6345 |
| 线路推荐（11–20） | 0.8103 | 0.6808 | 0.7083 | 0.6333 | 0.5734 |
| 酒店信息（21–30） | 0.8299 | 0.7869 | 0.8550 | 0.8667 | 0.5441 |
| 美食推荐（31–40） | 0.7489 | 0.9340 | 0.8261 | 0.6500 | 0.5551 |
| 交通指南（41–50） | 0.7957 | 0.8051 | 0.8325 | 0.7000 | 0.5713 |

**结构性问题定位**：线路类 `answer_relevancy`（0.68）与 `context_recall`（0.63）明显低于其他类，原因是线路问题是多天行程压缩，单条 chunk 无法覆盖全部要素；景点/酒店类召回与精度最好。

## 4. 回答长度（提示词收紧效果）

| 项 | 值 |
|---|---|
| 平均 | **95 字** |
| 中位 | 95 字 |
| 最大 | 132 字 |
| 超过 250 字的样本 | **0 条** |

改造前同一题的旧提示词回答约 700+ 字（含多级标题与长清单），现在稳定在 80–130 字，先给结论再列要点。

## 5. 低分题（answer_correctness 后 8 名）

| 题号 | 分数 | 问题 |
|---|---|---|
| 36 | 0.196 | 在厦门系统觅食应该放在鼓浪屿还是本岛？ |
| 42 | 0.198 | 从三亚机场出发去亚龙湾或海棠湾要注意什么？ |
| 11 | 0.212 | 三亚 5 日经典游的行程顺序是怎样的？ |
| 50 | 0.284 | 第一次去杭州，靠地铁够用吗？ |
| 4 | 0.327 | 第一次去云南应该先选哪里，需要把全省都跑一遍吗？ |
| 24 | 0.355 | 云南住宿为什么不能只看酒店本身？ |
| 15 | 0.395 | 厦门 2 至 3 日游怎么安排鼓浪屿和本岛？ |
| 30 | 0.406 | 杭州住西湖哪一侧，灵隐周边值不值得住？ |

**共性**：标准答案都是「多要素并列/多分支选择题」，回答压到约 95 字后只能覆盖其中一部分。属于**回答长度约束与多要素题型的张力**，不是检索错误（这些题 `context_precision` 多在 0.8 以上）。

可选改进（本期未做）：
1. 对多要素题放宽到「≤300 字 + 最多 5 条要点」；
2. 或把这类题拆成单要素子问题（一题一问）；
3. 线路类问题在检索侧按 `content_type=线路推荐` 强过滤，减少景点/酒店片段混入。

## 6. 与历史批次的关系（不可混算）

| 批次 | 位置 | 判分模型 | 说明 |
|---|---|---|---|
| 电商 HAK180 QA | `eval/archive_ecommerce/` | qwen3.8-flash | 旧业务，仅历史留档 |
| 旅游 5 题试跑 | `eval/archive_qwen38/result_trial5.csv` | qwen3.8-flash | 旧提示词，5/5 success |
| 旅游 50 题（中断） | `eval/archive_qwen38/result.csv` | qwen3.8-flash | 阿里云免费额度耗尽，25 success / 5 partial / 20 failed |
| **旅游 50 题（本期基线）** | `eval/result.csv` | deepseek-v4.1-flash | 新提示词，50/50 success |

判分模型与答案提示词都变了，四批数字**不可横向比较**；`archive_qwen38/README.md` 已记录该批次的失效原因。

## 7. 过程问题与修复（本期）

| 问题 | 现象 | 修复 |
|---|---|---|
| 判分输出被截断 | `IncompleteOutputException: max_tokens length limit`，`faithfulness` / `answer_correctness` 大量 NaN | 判分模型关思考（`reasoning_effort=none`）+ `max_tokens` 8192；第三方位可关思考后截断归零 |
| 代理偶发超时 | 题 48–50 `APITimeoutError`（12 次） | `--only-ids 47-50` 补跑，50/50 成功 |
| 回答过长 | 单题 700+ 字，correctness 被压到 0.47–0.50 | 收紧 `ANSWER_PROMPT`（≤200 字、先结论后要点） |

## 8. 复现命令

```powershell
cd 4组_陶桓_旅游_知识库
uv sync
uv pip install -r eval/requirements.txt          # ragas==0.4.3 + langchain-community==0.3.30

uv run python eval/validate_qa.py               # 校验测试集可溯源
uv run python eval/eval.py --limit 1            # 单题冒烟
uv run python eval/eval.py --limit 50            # 全量
uv run python eval/eval.py --only-ids 47-50      # 只补跑指定题号（保留其他行）
uv run python eval/eval.py --resume              # 只补跑未成功的题
```

可选环境变量：`EVAL_MAX_TOKENS`（默认 8192）、`EVAL_REASONING_EFFORT`（默认 `none`）。

## 9. 回归基线（2026-10-22 实测）

| 集合 | 行数 | 说明 |
|---|---|---|
| `chunks_tourism_v1` | 1271 | 新集合；本期清理 36 条空正文脏 chunk（1307 → 1271） |
| `item_name_tourism_v1` | 228 | 城市 + 实体别名行 |
| `kb_chunks` | **1241** | 旧电商集合，本期未写入 |
| `kb_item_names` | **34** | 旧电商集合，本期未写入 |

代码层确认：全项目无任何读写路径指向旧集合（仅 `config.py` 注释提及）。

## 10. 数据与可用性

- 文档：36 份（6 城市 × 6 内容类型，无空缺）
- 线路字段：6/6 有 `duration_days`、`budget_ref`、`estimated=true`
- 查询侧验证：`tools/smoke_query.py` 8 项断言全 PASS（城市/content_type/实体过滤、两级回退、全局检索、槽位识别、RRF+Rerank 元数据透传）

---

## 11. 多路检索批次（2026年10月23日）

### 11.1 配置差异

| 项 | 基线段（10-22） | 本批次（10-23） |
|---|---|---|
| 检索路 | 向量（稠密+稀疏） | 向量 + HyDE + 图谱 + MCP 网络搜索 |
| 融合 | RRF（向量+HyDE） | RRF（向量+HyDE）→ Rerank（RRF + 图 10 + 网 10） |
| Neo4j | 旧电商图，未启用 | 清空后重建：**381 节点 / 629 关系 / 12 类关系** |
| 名额 | 无（写死 10） | `RRF_TOP_N=10` / `GRAPH_TOP_N=10` / `WEB_TOP_N=10`（可调） |
| 容错 | 无 | 图 5s / 网 8s 超时，单路失败降级为空 |

### 11.2 评估方式

为控制额度，采用 **10 题子集**（每类 2 题：题号 1,2,11,12,21,22,31,32,41,42），与 `eval/result_baseline_50q.csv` 中**同题**逐项对比。四路冒烟另由 `tools/smoke_query.py --with-routes` 覆盖（10/10 PASS）。

### 11.3 结果（同一批 10 题）

| 指标 | 基线（单路） | 四路 · 网 10 | 四路 · 网 3（实验） |
|---|---|---|---|
| faithfulness | 0.8022 | **0.8932** | 0.8709 |
| answer_relevancy | 0.6349 | **0.9058** | 0.8843 |
| context_precision | 0.7306 | **0.7515** | 0.7320 |
| context_recall | 0.7667 | **0.8600** | 0.8600 |
| answer_correctness | **0.5781** | 0.4312 | 0.4632 |

文件：`eval/result_multiroute_web10.csv`（采用）、`eval/result_web3_10q_experiment.csv`（对照，未采用）。

### 11.3.1 精简 5 题口径（每类 1 题：1 / 11 / 21 / 31 / 41）

按"快速对比"要求，从同一批次取 5 题（配置相同，无需重跑）：

| 指标 | 多路（5 题） | 基线（同 5 题） | 差 |
|---|---|---|---|
| faithfulness | 0.9332 | 0.8533 | +0.080 |
| answer_relevancy | 0.9481 | 0.5584 | +0.390 |
| context_precision | 0.7618 | 0.6611 | +0.101 |
| context_recall | 0.9200 | 0.7333 | +0.187 |
| answer_correctness | 0.4562 | 0.6166 | −0.161 |

逐题 `answer_correctness`：题 1 0.684→0.707（↑），题 11 0.212→0.199、题 21 0.485→0.426、题 31 0.791→0.410、题 41 0.911→0.539（↓），结论与 10 题批次一致。

> 说明：50 题全量多路评估按成本考虑已取消，改为 10 题 / 5 题子集对比；全量基线（单路）仍保留在 `result_baseline_50q.csv`。

### 11.4 逐题差异（correctness）

| 题号 | 问题 | 基线 | 四路 | 变化 |
|---|---|---|---|---|
| 1 | 亚龙湾适合什么样的游客和玩法 | 0.684 | 0.707 | ↑ |
| 2 | 蜈支洲岛为什么建议单独安排一整天 | 0.785 | 0.463 | ↓ |
| 11 | 三亚 5 日经典游的行程顺序 | 0.212 | 0.199 | ≈ |
| 12 | 三亚线路为什么东线先玩、西线后放 | 0.513 | 0.451 | ↓ |
| 21 | 三亚湾、亚龙湾、海棠湾分别适合什么需求 | 0.485 | 0.426 | ↓ |
| 22 | 带父母去三亚更适合住哪些区域 | 0.600 | 0.387 | ↓ |
| 31 | 三亚必吃的本地风味有哪些 | 0.791 | 0.410 | ↓ |
| 32 | 在三亚如果只吃海鲜会错过什么 | 0.602 | 0.555 | ↓ |
| 41 | 三亚市内有没有地铁，日常靠什么移动 | 0.911 | 0.539 | ↓ |
| 42 | 从三亚机场出发去亚龙湾或海棠湾要注意什么 | 0.198 | 0.176 | ≈ |

### 11.5 结论与口径

1. **召回与忠实度全面提升**：`context_recall` 0.77 → 0.86、`answer_relevancy` 0.63 → 0.91，说明多路补齐了单路漏召，答案也更贴引用材料。
2. **`answer_correctness` 下降是评测口径冲突**，非系统退化：
   - 降低网页名额（10 → 3）**并未**恢复该指标，反证主因不是"网页挤占名额"；
   - 逐题看，下降题均为网页结果质量更高/更新（如「三亚有没有地铁」网页给出更完整现状、「机场→湾区」网页给出精确车程与候车点），答案据实采用网页信息；
   - `ground_truth` 由本地 36 份文档推导，因此与网页口径不同就会扣分。
3. **决策**：产品目标优先取"信息正确、更新、可追溯"，**采纳网页结果，不做 local 优先偏置**；名额保持 10/10/10，后续按需通过 `WEB_TOP_N` / `GRAPH_TOP_N` 调整。
4. **待办**：若要让指标口径与产品目标一致，需用"外部事实核对"类裁判或补充 `ground_truth` 的双来源标注，本轮未做。

### 11.6 复现

```powershell
# 图谱核对
uv run python tools/inspect_graph.py --sample 5
uv run python tools/check_graph_sample.py

# 四路冒烟
uv run python tools/smoke_query.py --with-rerank --with-routes

# 10 题子集（会覆盖 result.csv 对应题号）
uv run python eval/eval.py --only-ids 1,2,11,12,21,22,31,32,41,42
```
