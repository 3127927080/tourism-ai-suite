#!/usr/bin/env bash
# 云端一键：装依赖 -> 下载模型 -> 训练 -> 推理 -> 双口径评估
# 用法（在解压目录内）:
#   bash run_cloud.sh                # 默认 Qwen3-4B / 600 步 / bf16 LoRA
#   BASE_MODEL=Qwen/Qwen3-8B bash run_cloud.sh
#   USE_4BIT=1 bash run_cloud.sh     # 改用量化（8B 或小显存时）
set -euo pipefail

ROOT="${ROOT:-$(cd "$(dirname "$0")" && pwd)}"
cd "$ROOT"
export PYTHONIOENCODING=utf-8
export TOKENIZERS_PARALLELISM=false

# AutoDL 学术加速：默认关闭（ModelScope 国内直连更快）；
# 需要拉 HuggingFace/GitHub 时用 USE_PROXY=1 开启
if [ "${USE_PROXY:-0}" = "1" ] && [ -f /etc/network_turbo ]; then
  echo "[proxy] 启用 AutoDL 学术加速"
  # shellcheck disable=SC1091
  source /etc/network_turbo || true
fi

# pip 国内源兜底（AutoDL 通常已配置，失效时手动启用）
PIP_INDEX="${PIP_INDEX:-}"
PIP_ARGS=""
if [ -n "$PIP_INDEX" ]; then PIP_ARGS="-i $PIP_INDEX"; fi

MODEL_DIR="${MODEL_DIR:-/root/autodl-tmp/model}"
BASE_MODEL="${BASE_MODEL:-Qwen/Qwen3-4B}"
MODEL_PATH="$MODEL_DIR/$(basename "$BASE_MODEL")"
MAX_STEPS="${MAX_STEPS:-600}"
BATCH="${BATCH:-4}"
GRAD_ACCUM="${GRAD_ACCUM:-4}"
MAX_LEN="${MAX_LEN:-1024}"
OUT="${OUT:-finetuned/$(basename "$BASE_MODEL")_sft}"
RESP="${RESP:-data/sft/responses_$(basename "$BASE_MODEL").jsonl}"
USE_4BIT="${USE_4BIT:-0}"

QUANT_FLAG="--no-4bit"
if [ "$USE_4BIT" = "1" ]; then QUANT_FLAG=""; fi

echo "================ 配置 ================"
echo "模型      : $BASE_MODEL -> $MODEL_PATH"
echo "步数      : $MAX_STEPS   batch=$BATCH  grad_accum=$GRAD_ACCUM  max_len=$MAX_LEN"
echo "量化      : $([ -z "$QUANT_FLAG" ] && echo 4bit || echo bf16)"
echo "输出      : $OUT"
echo "====================================="

echo "[1/5] 安装依赖（torch 使用镜像自带版本）"
python -m pip install -q --upgrade pip $PIP_ARGS
python -m pip install -q -r requirements.txt $PIP_ARGS

echo "[2/5] 检查 GPU"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
python -c "import torch;print('torch',torch.__version__,'cuda',torch.cuda.is_available())"

echo "[3/5] 下载模型"
if [ -d "$MODEL_PATH" ] && [ -f "$MODEL_PATH/config.json" ]; then
  echo "已存在，跳过: $MODEL_PATH"
else
  mkdir -p "$MODEL_DIR"
  python - "$BASE_MODEL" "$MODEL_PATH" <<'PY'
import sys
from modelscope import snapshot_download
repo, target = sys.argv[1], sys.argv[2]
print(snapshot_download(repo, local_dir=target))
PY
fi

echo "[4/5] 训练"
# TensorBoard：AutoDL 面板默认读 /root/tf-logs，按模型分目录避免多次训练混淆
export TENSORBOARD_LOGGING_DIR="${TENSORBOARD_LOGGING_DIR:-/root/tf-logs/$(basename "$BASE_MODEL")}"
python QuCo_tourism_sft.py \
  --base-model "$MODEL_PATH" $QUANT_FLAG --tensorboard \
  --train data/sft_clean/train.jsonl \
  --dev data/sft_clean/dev.jsonl \
  --output "$OUT" \
  --max-steps "$MAX_STEPS" \
  --max-length "$MAX_LEN" \
  --batch "$BATCH" \
  --grad-accum "$GRAD_ACCUM" \
  --eval-steps 100 \
  --save-steps 100

echo "[5/5] 推理 + 评估"
python scripts/eval_smoke.py \
  --base-model "$MODEL_PATH" $QUANT_FLAG \
  --adapter "$OUT" \
  --gold data/sft/gold.jsonl \
  --responses "$RESP"

echo "--- 严格匹配（gold_vote）---"
python eval_triples.py --manifest data/sft/gold_vote.jsonl --responses "$RESP" \
  --output "data/sft/eval_$(basename "$BASE_MODEL")_vote.json" || true
echo "--- 宽松匹配 ---"
python scripts/eval_lenient.py --responses "$RESP" || true

echo "✅ 全部完成。产物: $OUT ; 预测: $RESP"
