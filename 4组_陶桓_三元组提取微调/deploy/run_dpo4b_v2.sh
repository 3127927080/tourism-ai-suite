#!/usr/bin/env bash
# 4B DPO v2：去掉「漏抽」负样本后的重训 + 评估
set -euo pipefail
cd /root/autodl-tmp/tourism_triple
export PYTHONIOENCODING=utf-8
export TOKENIZERS_PARALLELISM=false
export TENSORBOARD_LOGGING_DIR=/root/tf-logs/dpo_4b_v2
PY=/root/miniconda3/bin/python

echo "=== [1/4] DPO 训练 $(date +%H:%M:%S) ==="
$PY QuCo_tourism_dpo.py \
  --base-model /root/autodl-tmp/model/Qwen3-4B \
  --sft-adapter finetuned/tourism_sft_4b \
  --train data/dpo_v2/train.jsonl \
  --dev data/dpo_v2/train_dev.jsonl \
  --max-steps 200 --max-length 1024 \
  --batch 1 --grad-accum 8 \
  --eval-steps 50 --save-steps 50 \
  --tensorboard \
  --output finetuned/tourism_dpo_4b_v2

echo "=== [2/4] 推理 $(date +%H:%M:%S) ==="
$PY scripts/eval_smoke.py \
  --base-model /root/autodl-tmp/model/Qwen3-4B --no-4bit \
  --adapter finetuned/tourism_dpo_4b_v2 \
  --gold data/sft/gold.jsonl \
  --responses data/sft/responses_dpo_4b_v2.jsonl

echo "=== [3/4] 严格评估 $(date +%H:%M:%S) ==="
$PY eval_triples.py --manifest data/sft/gold_vote.jsonl \
  --responses data/sft/responses_dpo_4b_v2.jsonl \
  --output data/sft/eval_dpo_4b_v2_vote.json || true

echo "=== [4/4] 宽松评估 ==="
$PY scripts/eval_lenient.py --responses data/sft/responses_dpo_4b_v2.jsonl data/sft/responses_sft_4b.jsonl || true

echo "=== ALL DONE $(date +%H:%M:%S) ==="
