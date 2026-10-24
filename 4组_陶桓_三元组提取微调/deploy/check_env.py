import sys

import torch
import transformers
import trl
import peft
import datasets

from trl.trainer.sft_config import SFTConfig
from trl.trainer.sft_trainer import SFTTrainer

print("python", sys.version.split()[0])
print("transformers", transformers.__version__, "| trl", trl.__version__)
print("peft", peft.__version__, "| datasets", datasets.__version__)
print("torch", torch.__version__, "| cuda", torch.cuda.is_available(), "| bf16", torch.cuda.is_bf16_supported())
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0), round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1), "GB")

fields = SFTConfig.__dataclass_fields__
for k in ["max_length", "assistant_only_loss", "chat_template_path", "logging_dir", "report_to"]:
    print(f"SFTConfig.{k}:", "OK" if k in fields else "MISSING")
