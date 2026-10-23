"""旅游三元组抽取 DPO，起点为 SFT LoRA adapter。

思路：加载 base + SFT adapter（可训练），ref_model 留空，
TRL 会自动用「禁用 adapter」的同一模型作为参考策略（PEFT+DPO 标准做法）。

用法:
    python QuCo_tourism_dpo.py --base-model ./model/Qwen3-4B --sft-adapter finetuned/tourism_sft_4b \
        --train data/dpo/train.jsonl --dev data/dpo/train_dev.jsonl --output finetuned/tourism_dpo_4b \
        --max-steps 300
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch
from datasets import load_dataset
from peft import PeftModel, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import DPOConfig, DPOTrainer

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-model", default=str(SCRIPT_DIR / "model" / "Qwen3-1.7B"))
    p.add_argument("--sft-adapter", required=True)
    p.add_argument("--train", default=str(SCRIPT_DIR / "data" / "dpo" / "train.jsonl"))
    p.add_argument("--dev", default=str(SCRIPT_DIR / "data" / "dpo" / "train_dev.jsonl"))
    p.add_argument("--output", default=str(SCRIPT_DIR / "finetuned" / "tourism_dpo"))
    p.add_argument("--max-steps", type=int, default=300)
    p.add_argument("--max-length", type=int, default=1024)
    p.add_argument("--batch", type=int, default=1)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--lr", type=float, default=5e-6)
    p.add_argument("--beta", type=float, default=0.1)
    p.add_argument("--eval-steps", type=int, default=100)
    p.add_argument("--save-steps", type=int, default=100)
    p.add_argument("--tensorboard", action="store_true")
    p.add_argument("--4bit", dest="four_bit", action="store_true", help="4bit QLoRA（8GB 显存必需）")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    use_bf16 = torch.cuda.is_bf16_supported()
    dtype = torch.bfloat16 if use_bf16 else torch.float16
    print(f"bf16={use_bf16} dtype={dtype}")

    model_kwargs = {"dtype": dtype, "device_map": {"": 0}}
    if args.four_bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype,
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    model.config.use_cache = False
    if args.four_bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    model = PeftModel.from_pretrained(model, args.sft_adapter, is_trainable=True)

    # TRL 0.24 会访问 model.warnings_issued；PeftModel 未透传该属性，手动补齐
    for obj in (model,
                getattr(model, "base_model", None),
                getattr(getattr(model, "base_model", None), "model", None)):
        if obj is not None and not hasattr(obj, "warnings_issued"):
            try:
                obj.warnings_issued = {}
            except Exception:
                pass

    data = load_dataset("json", data_files={"train": args.train, "test": args.dev})

    tb_dir = os.environ.get("TENSORBOARD_LOGGING_DIR", "/root/tf-logs/dpo")
    config = DPOConfig(
        output_dir=args.output,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        max_steps=args.max_steps,
        learning_rate=args.lr,
        beta=args.beta,
        max_length=args.max_length,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        logging_steps=10,
        logging_dir=tb_dir,
        report_to="tensorboard" if args.tensorboard else "none",
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=1,
        bf16=use_bf16,
        fp16=not use_bf16,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        remove_unused_columns=False,
    )

    trainer = DPOTrainer(
        model=model,
        ref_model=None,          # 用禁用 adapter 的同一模型作参考
        args=config,
        train_dataset=data["train"],
        eval_dataset=data["test"],
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"✅ DPO 完成: {args.output}")


if __name__ == "__main__":
    main()
