#!/usr/bin/env bash
# 4B 泛化测试：SFT 与 DPO v2 在 100 条通用数据上的域外行为
set -euo pipefail
cd /root/autodl-tmp/tourism_triple
export PYTHONIOENCODING=utf-8
PY=/root/miniconda3/bin/python
BASE=/root/autodl-tmp/model/Qwen3-4B
SENT=data/generalization/general_100.jsonl

echo "=== [1/2] 4B SFT 泛化 $(date +%H:%M:%S) ==="
$PY scripts/eval_generalization.py --base-model "$BASE" --no-4bit \
  --adapter finetuned/tourism_sft_4b --tag sft_4b --sentences-file "$SENT"

echo "=== [2/2] 4B DPO v2 泛化 $(date +%H:%M:%S) ==="
$PY scripts/eval_generalization.py --base-model "$BASE" --no-4bit \
  --adapter finetuned/tourism_dpo_4b_v2 --tag dpo_4b_v2 --sentences-file "$SENT"

echo "=== DONE $(date +%H:%M:%S) ==="
