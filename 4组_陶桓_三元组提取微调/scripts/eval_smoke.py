"""最小评估闭环：对 gold 集逐条推理 → 落 responses → 复用 eval_triples 出分。

用法:
    python scripts/eval_smoke.py --base-model ./model/Qwen3-1.7B \
        --adapter finetuned/smoke --gold data/smoke/gold.jsonl \
        --responses data/smoke/responses.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

SCRIPT_DIR = Path(__file__).resolve().parent.parent
JINJA = SCRIPT_DIR / "new_chat_template.jinja"

SYSTEM_PROMPT = (
    "你是旅游领域信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"
    "可选关系：位于、属于、包含、途经、推荐、适合、需要、提供、邻近、以…闻名、建议、对比。"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-model", default=str(SCRIPT_DIR / "model" / "Qwen3-1.7B"))
    p.add_argument("--adapter", default=str(SCRIPT_DIR / "finetuned" / "smoke"))
    p.add_argument("--gold", default=str(SCRIPT_DIR / "data" / "smoke" / "gold.jsonl"))
    p.add_argument("--responses", default=str(SCRIPT_DIR / "data" / "smoke" / "responses.jsonl"))
    p.add_argument("--max-new-tokens", type=int, default=160)
    p.add_argument("--limit", type=int, default=0, help="0 表示全量")
    p.add_argument("--no-4bit", action="store_true", help="关闭 4bit（大显存时用 bf16）")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    chat_template = JINJA.read_text(encoding="utf-8")

    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model_kwargs = {"dtype": compute_dtype, "device_map": {"": 0}}
    if not args.no_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()

    rows = [json.loads(line) for line in open(args.gold, encoding="utf-8") if line.strip()]
    if args.limit:
        rows = rows[: args.limit]

    out = []
    for r in rows:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": r["query"]},
        ]
        prompt = tokenizer.apply_chat_template(
            messages, chat_template=chat_template,
            tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            gen = model.generate(
                **inputs, max_new_tokens=args.max_new_tokens,
                do_sample=False, temperature=None, top_p=None,
                pad_token_id=tokenizer.pad_token_id,
            )
        text = tokenizer.decode(gen[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        out.append({"id": r["id"], "query": r["query"], "response": text.strip()})
        print(f"[{r['id']}] {text.strip()[:80]!r}")

    Path(args.responses).parent.mkdir(parents=True, exist_ok=True)
    with open(args.responses, "w", encoding="utf-8") as f:
        for row in out:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"✅ 推理完成: {args.responses} ({len(out)} 条)")


if __name__ == "__main__":
    main()
