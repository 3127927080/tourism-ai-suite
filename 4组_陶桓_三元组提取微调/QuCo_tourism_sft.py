"""旅游三元组抽取 SFT（4bit QLoRA）。

最小闭环与正式训练共用；最小闭环用 --max-steps 50 验证管道。

用法（最小闭环）:
    python QuCo_tourism_sft.py --base-model ./model/Qwen3-1.7B \
        --train data/smoke/train.jsonl --dev data/smoke/dev.jsonl \
        --output finetuned/smoke --max-steps 50 --max-length 512
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 避免打印中文/emoji 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl.trainer.sft_config import SFTConfig
from trl.trainer.sft_trainer import SFTTrainer

SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-model", default=str(SCRIPT_DIR / "model" / "Qwen3-1.7B"))
    p.add_argument("--train", default=str(SCRIPT_DIR / "data" / "smoke" / "train.jsonl"))
    p.add_argument("--dev", default=str(SCRIPT_DIR / "data" / "smoke" / "dev.jsonl"))
    p.add_argument("--output", default=str(SCRIPT_DIR / "finetuned" / "smoke"))
    p.add_argument("--max-steps", type=int, default=50)
    p.add_argument("--max-length", type=int, default=512)
    p.add_argument("--batch", type=int, default=2)
    p.add_argument("--grad-accum", type=int, default=8)
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=16)
    p.add_argument("--eval-steps", type=int, default=25)
    p.add_argument("--save-steps", type=int, default=50)
    p.add_argument("--no-4bit", action="store_true", help="关闭 4bit（显存够时用 bf16）")
    p.add_argument("--no-grad-checkpoint", action="store_true",
                   help="关闭梯度检查点（小模型显存充裕时更快）")
    p.add_argument("--tensorboard", action="store_true",
                   help="写 TensorBoard 日志；AutoDL 面板默认读 /root/tf-logs")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print(f"[1/5] 载入 tokenizer: {args.base_model}")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"[2/5] 载入模型 (4bit={not args.no_4bit})")
    use_bf16 = torch.cuda.is_bf16_supported()
    compute_dtype = torch.bfloat16 if use_bf16 else torch.float16
    print(f"      bf16 支持: {use_bf16} -> 计算精度 {compute_dtype}")
    model_kwargs = {"dtype": compute_dtype, "device_map": {"": 0}}
    if not args.no_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    model.config.use_cache = False
    if not args.no_4bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    else:
        # 非量化路径不会走 prepare_model_for_kbit_training，
        # 梯度检查点下必须显式让输入 embedding 参与梯度，否则 LoRA 不更新
        model.enable_input_require_grads()
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

    print("[3/5] 挂载 LoRA")
    lora = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        target_modules="all-linear",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    print("[4/5] 载入数据")
    data = load_dataset("json", data_files={"train": args.train, "test": args.dev})

    os.environ.setdefault("TENSORBOARD_LOGGING_DIR", "/root/tf-logs")
    tb_dir = os.environ.get("TENSORBOARD_LOGGING_DIR", "/root/tf-logs")
    print(f"[4/5] 日志目录: {tb_dir} (TensorBoard={args.tensorboard})")
    config = SFTConfig(
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        max_steps=args.max_steps,
        logging_strategy="steps",
        logging_steps=10,
        logging_dir=tb_dir,
        report_to="tensorboard" if args.tensorboard else "none",
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        load_best_model_at_end=True,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=1,
        output_dir=args.output,
        bf16=use_bf16,
        fp16=not use_bf16,
        gradient_checkpointing=not args.no_grad_checkpoint,
        gradient_checkpointing_kwargs={"use_reentrant": False} if not args.no_grad_checkpoint else None,
        max_length=args.max_length,
        assistant_only_loss=True,
        chat_template_path=str(SCRIPT_DIR / "new_chat_template.jinja"),
    )

    print("[5/5] 开始训练")
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=data["train"],
        eval_dataset=data["test"],
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    print(f"✅ 训练完成，LoRA 已保存: {args.output}")


if __name__ == "__main__":
    main()
