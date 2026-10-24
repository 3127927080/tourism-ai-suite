# 归档：qwen3.8-flash 评估结果（额度耗尽，未完成）

> 归档日期：2026年10月22日
> 状态：**不作为最终基线**，仅保留作过程记录与问题追踪
> 原因：阿里云百炼免费额度耗尽（HTTP 403 `insufficient_quota`），50 题全量跑到第 30 题左右中断

## 运行配置

| 项 | 值 |
|---|---|
| 测试集 | `eval/qa.csv`（50 题：景点/线路/酒店/美食/交通 各 10 题） |
| 答案生成模型 | `qwen3.8-flash`（`LLM_DEFAULT_MODEL`） |
| 槽位识别模型 | `qwen3.8-flash`（`ITEM_MODEL`） |
| Ragas 判分模型 | `qwen3.8-flash`（`ITEM_MODEL`） |
| Embedding | 本地 BGE-M3（dense + sparse） |
| Rerank | `qwen3.7-text-rerank` |
| 检索开关 | `ENABLE_HYDE=false` / `ENABLE_GRAPH=false` / `ENABLE_WEB_SEARCH=false` |
| 集合 | `chunks_tourism_v1`（1271 条） / `item_name_tourism_v1`（228 行别名） |

## 文件说明

| 文件 | 内容 |
|---|---|
| `result.csv` | 50 题全量跑，25 成功 / 5 部分指标缺失（3、15、17、29、30）/ 20 失败（31–50，全部 403） |
| `result_partial_qwen38.csv` | 同 `result.csv` 的中断时副本，当时另存；内容一致 |
| `result_trial5.csv` | 正式全量之前的 5 题试跑（题 1–5，5/5 成功），用于验证链路与指标可算 |

## 中断时的部分指标（有效样本 27–30 条，仅供参考）

| 指标 | 均值 |
|---|---|
| faithfulness | 0.8940 |
| answer_relevancy | 0.7726 |
| context_precision | 0.8348 |
| context_recall | 0.7529 |
| answer_correctness | 0.4721 |

5 题试跑（result_trial5.csv）的均值为：faithfulness 0.9011 / answer_relevancy 0.7823 / context_precision 0.9747 / context_recall 0.9000 / answer_correctness 0.5039。

## 观察与结论

1. 检索侧表现稳定：`context_precision` 0.78–0.97、`context_recall` 0.75–0.90，城市与 `content_type` 过滤、RRF、Rerank 链路正常。
2. `answer_correctness` 系统性偏低（0.47–0.50）：低分题集中在「标准答案是一句判断、模型回答写成结构化清单」的题型，属指标口径问题，非检索或事实错误。
3. 额度中断导致数据不完整，且后续切换为 DeepSeek 判分，**与新版结果不可混算**；最终基线以 `eval/result.csv`（DeepSeek 判分、50 题完整）为准。
