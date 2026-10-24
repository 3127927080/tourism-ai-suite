import json
import sys
from pathlib import Path

path = Path(sys.argv[1] if len(sys.argv) > 1 else "/root/autodl-tmp/tourism_triple/finetuned/tourism_sft_4b/trainer_state.json")
d = json.loads(path.read_text(encoding="utf-8"))
print("global_step:", d.get("global_step"))
for x in d.get("log_history", []):
    if "eval_loss" in x:
        print(f"step {x['step']:>4}  eval_loss {x['eval_loss']:.4f}  "
              f"token_acc {x.get('eval_mean_token_accuracy', 0):.4f}")
    elif "loss" in x and x["step"] % 50 == 0:
        print(f"step {x['step']:>4}  train_loss {x['loss']:.4f}  lr {x.get('learning_rate', 0):.2e}")
