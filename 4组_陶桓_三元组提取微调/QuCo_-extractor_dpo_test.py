import os
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from datasets import load_dataset
from peft import LoraConfig, PeftModel, get_peft_model
from trl import DPOTrainer, DPOConfig

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"   # 如需

# -------------------- 1. 加载基础模型并合并 SFT LoRA --------------------
base_model_path = "./model/QuCo-extractor-0.5B/"
sft_lora_path = "./finetuned/01_sft_lora_demo"

base = AutoModelForCausalLM.from_pretrained(
    base_model_path,
    dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

# 如果 SFT 是 LoRA 适配器，则加载并合并
if os.path.exists(os.path.join(sft_lora_path, "adapter_config.json")):
    peft_model = PeftModel.from_pretrained(base, sft_lora_path)
    model = peft_model.merge_and_unload()   # 合并为完整模型
    print("✅ SFT LoRA 已合并为完整模型")
else:
    model = base
    print("ℹ️ 未找到 SFT LoRA，直接使用基础模型")

# 加载 tokenizer
tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

# -------------------- 2. 应用 DPO 的 LoRA（手动包装） --------------------
lora_config = LoraConfig(
    r=8,
    lora_alpha=8,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    task_type="CAUSAL_LM"
)
model = get_peft_model(model, lora_config)   # 现在 model 是 PeftModel

# 关键！手动添加 warnings_issued
model.warnings_issued = {}

# -------------------- 3. 加载数据并转换为对话格式 --------------------
dataset = load_dataset("json", data_files={
    "train": "./data/dpo_test_data.jsonl",
    "test": "./data/dpo_dev_data.jsonl"
})

def to_conversation(example):
    return {
        "prompt": [{"role": "user", "content": example["prompt"]}],
        "chosen": [
            {"role": "user", "content": example["prompt"]},
            {"role": "assistant", "content": example["chosen"]}
        ],
        "rejected": [
            {"role": "user", "content": example["prompt"]},
            {"role": "assistant", "content": example["rejected"]}
        ]
    }

dataset = dataset.map(to_conversation)
# dataset = dataset.remove_columns(["prompt", "chosen", "rejected"])

# -------------------- 4. DPO 配置（不带 response_template） --------------------
os.environ["TENSORBOARD_LOGGING_DIR"]= "./logs/02_dpo_dir_demo"
config = DPOConfig(
    output_dir="./finetuned/02_dpo_dir_demo",
    per_device_train_batch_size=1,
    per_device_eval_batch_size=1,
    gradient_accumulation_steps=8,
    max_steps=1000,
    learning_rate=5e-6,
    lr_scheduler_type="cosine",
    warmup_steps=100,
    logging_steps=20,
    report_to="tensorboard",
    save_steps=50,
    save_total_limit=2,
    eval_strategy="steps",
    eval_steps=50,
    bf16=True,
    gradient_checkpointing=True,
    max_length=1024,
    max_prompt_length=512,
    beta=0.1,
    loss_type="sigmoid",
    # 不传 response_template，对话格式自动推断
)

# -------------------- 5. 初始化 Trainer（不传 peft_config） --------------------
trainer = DPOTrainer(
    model=model,                 # 已手动包装的 PeftModel
    ref_model=None,              # 自动使用基模型作为参考
    args=config,
    train_dataset=dataset["train"],
    eval_dataset=dataset["test"],
    processing_class=tokenizer,
    # 注意：不再传入 peft_config
)

# -------------------- 6. 训练 --------------------
trainer.train()
trainer.save_model("./finetuned/02_dpo_lora_demo")