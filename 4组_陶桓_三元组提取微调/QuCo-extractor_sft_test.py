"""
对于三元组提取任务的模型QuCo-extractor的sft监督微调,训练方式采用LoRA,数据集采用业务数据和网络数据混合
"""
from transformers import AutoModelForCausalLM, AutoTokenizer
from datasets import load_dataset
from typing import List
from peft import LoraConfig,get_peft_model
from trl.trainer.sft_config import SFTConfig
import os
from trl.trainer.sft_trainer import SFTTrainer


#1.加载模型和对应的Tokenizer
model = AutoModelForCausalLM.from_pretrained("./model/QuCo-extractor-0.5B/")
tokenzier = AutoTokenizer.from_pretrained("./model/QuCo-extractor-0.5B")

#2数据准备
data = load_dataset("json",data_files={"train":"./data/train_triple_converted.jsonl","test":"./data/dev_triple_converted.jsonl"})


##4.定义LoRA配置类
lora_config = LoraConfig(
    r=8,
    lora_alpha=8,
    # target_modules=["q_proj","v_proj"],
    target_modules="all-linear", # 对所有的线性层 插入LoRA AB 矩阵,
    lora_dropout=0.05,
    task_type="CAUSAL_LM"
)

#将模型加载LoRA,变为LoRA模型
peft_model = get_peft_model(model,lora_config)

#5.定义SFTConfig类
os.environ["TENSORBOARD_LOGGING_DIR"]= "./logs/01_sft_dir_demo"
config = SFTConfig(
    per_device_train_batch_size=3,
    per_device_eval_batch_size=3,
    gradient_accumulation_steps=8,
    max_steps=1000,
    # num_train_epochs=
    logging_strategy="steps",
    logging_steps=20,
    report_to="tensorboard",
    learning_rate=3e-4,
    lr_scheduler_type="cosine",
    warmup_steps=0.1,
    eval_strategy="steps",
    eval_steps=50,
    metric_for_best_model="eval_loss",
    greater_is_better=False,
    load_best_model_at_end=True,
    save_strategy="steps",
    save_steps=50,
    save_total_limit=3,
    output_dir="finetuned/01_sft_dir_demo",
    bf16=True,
    gradient_checkpointing=True,
    activation_offloading=False,
    max_length=1024,

    assistant_only_loss=True,
    chat_template_path="./new_chat_template.jinja"

)


#6.定义Trainer类
trainer = SFTTrainer(
    model=peft_model,
    args=config,
    train_dataset=data["train"],
    eval_dataset=data["test"],
    # response_template="<|im_start|>assistant",
    processing_class=tokenzier
)

#7.开始训练
trainer.train()
trainer.save_model("./finetuned/01_sft_lora_demo")
