#!/usr/bin/env bash
# 云端 4B：下载模型 -> 训练 -> 推理 -> 评估（依赖已装好，跳过 pip）
set -euo pipefail

ROOT=/root/autodl-tmp/tourism_triple
cd "$ROOT"
export PYTHONIOENCODING=utf-8
export TOKENIZERS_PARALLELISM=false
export TENSORBOARD_LOGGING_DIR=/root/tf-logs/Qwen3-4B

PY=/root/miniconda3/bin/python
MODEL_DIR=/root/autodl-tmp/model
BASE=Qwen/Qwen3-4B
MODEL_PATH=$MODEL_DIR/Qwen3-4B
OUT=finetuned/tourism_sft_4b
RESP=data/sft/responses_sft_4b.jsonl

echo "=== [1/4] 下载模型 $(date +%H:%M:%S) ==="
if [ -f "$MODEL_PATH/config.json" ]; then
  echo "已存在，跳过"
else
  mkdir -p "$MODEL_DIR"
  $PY -c "from modelscope import snapshot_download; print(snapshot_download('$BASE', local_dir='$MODEL_PATH'))"
fi
du -sh "$MODEL_PATH"

echo "=== [2/4] 训练 $(date +%H:%M:%S) ==="
$PY QuCo_tourism_sft.py \
  --base-model "$MODEL_PATH" --no-4bit --tensorboard \
  --train data/sft_clean/train.jsonl \
  --dev data/sft_clean/dev.jsonl \
  --output "$OUT" \
  --max-steps 600 --max-length 1024 \
  --batch 2 --grad-accum 8 \
  --eval-steps 100 --save-steps 100

echo "=== [3/4] 推理 $(date +%H:%M:%S) ==="
$PY scripts/eval_smoke.py \
  --base-model "$MODEL_PATH" --no-4bit \
  --adapter "$OUT" \
  --gold data/sft/gold.jsonl \
  --responses "$RESP"

echo "=== [4/4] 评估 $(date +%H:%M:%S) ==="
$PY eval_triples.py --manifest data/sft/gold_vote.jsonl --responses "$RESP" \
  --output data/sft/eval_sft_4b_vote.json || true
$PY scripts/eval_lenient.py --responses "$RESP" || true

echo "=== ALL DONE $(date +%H:%M:%S) ==="
