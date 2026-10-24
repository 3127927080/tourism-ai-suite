#!/usr/bin/env bash
set -euo pipefail
cd /root/autodl-tmp/tourism_triple
export PYTHONIOENCODING=utf-8
PY=/root/miniconda3/bin/python

echo "=== 等待 4B DPO 训练结束 $(date +%H:%M:%S) ==="
while pgrep -f "QuCo_tourism_dpo.py" > /dev/null; do sleep 10; done
echo "=== 训练已结束，开始推理 $(date +%H:%M:%S) ==="

$PY scripts/eval_smoke.py \
  --base-model /root/autodl-tmp/model/Qwen3-4B --no-4bit \
  --adapter finetuned/tourism_dpo_4b \
  --gold data/sft/gold.jsonl \
  --responses data/sft/responses_dpo_4b.jsonl

echo "=== 严格评估 $(date +%H:%M:%S) ==="
$PY eval_triples.py --manifest data/sft/gold_vote.jsonl \
  --responses data/sft/responses_dpo_4b.jsonl \
  --output data/sft/eval_dpo_4b_vote.json || true

echo "=== 宽松评估（vs SFT）==="
$PY scripts/eval_lenient.py --responses data/sft/responses_dpo_4b.jsonl data/sft/responses_sft_4b.jsonl || true

echo "=== DONE $(date +%H:%M:%S) ==="
