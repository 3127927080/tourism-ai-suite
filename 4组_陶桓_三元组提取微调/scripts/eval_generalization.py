"""泛化能力测试：用旅游微调模型跑通用开放域数据，观察域外行为。

通用新闻大多不含旅游 12 类关系，理想行为是输出「无三元组」；
若大量强行抽三元组，说明模型在域外会幻觉 / 过度套用 schema。

用法:
    python scripts/eval_generalization.py --base-model ./model/Qwen3-4B \
        --adapter cloud_results/tourism_sft_4b --tag sft_4b
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

ROOT = Path(__file__).resolve().parent.parent
JINJA = ROOT / "new_chat_template.jinja"
GENERIC = ROOT / "data" / "train_triple_converted.jsonl"
OUT_DIR = ROOT / "data" / "generalization"

RELATIONS = ["位于", "属于", "包含", "途经", "推荐", "适合", "需要", "提供", "邻近", "以…闻名", "建议", "对比"]
SYSTEM_PROMPT = (
    "你是旅游领域信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"
    "可选关系：位于、属于、包含、途经、推荐、适合、需要、提供、邻近、以…闻名、建议、对比。"
)


def normalize(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def parse(raw: str, sentence: str):
    """返回 (合法三元组, 状态, 非法关系数, 未落地实体数)"""
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    if "无三元组" in text:
        return [], "none", 0, 0
    norm_s = normalize(sentence)
    triples, illegal_rel, not_grounded = [], 0, 0
    for m in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", text):
        parts = re.split(r"\s*[,，]\s*", m.group(1))
        if len(parts) != 3:
            continue
        h, r, t = (p.strip() for p in parts)
        if r not in RELATIONS:
            illegal_rel += 1
            continue
        if normalize(h) not in norm_s or normalize(t) not in norm_s:
            not_grounded += 1
            continue
        triples.append([h, r, t])
    status = "keep" if triples else "none"
    return triples, status, illegal_rel, not_grounded


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", default=str(ROOT / "model" / "Qwen3-4B"))
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--sample", type=int, default=100)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--max-new-tokens", type=int, default=160)
    ap.add_argument("--sentences-file", default="",
                    help="固定样本文件（每行 {id,sentence}）；不给则从通用数据集抽样")
    ap.add_argument("--no-4bit", action="store_true", help="用 bf16（大显存时更快）")
    args = ap.parse_args()

    if args.sentences_file:
        rows = [json.loads(l)["sentence"]
                for l in open(args.sentences_file, encoding="utf-8") if l.strip()]
        print(f"从固定文件读取 {len(rows)} 条")
    else:
        rows = []
        with GENERIC.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                msgs = rec.get("messages") or []
                if len(msgs) < 3:
                    continue
                user = msgs[1].get("content", "")
                sentence = user.split("句子：", 1)[1].strip() if "句子：" in user else user.strip()
                rows.append(sentence)
        random.Random(args.seed).shuffle(rows)
        rows = rows[: args.sample]
    print(f"通用样本: {len(rows)} 条 | 模型: {args.tag}")

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    chat_template = JINJA.read_text(encoding="utf-8")

    compute_dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model_kwargs = {"dtype": compute_dtype, "device_map": {"": 0}}
    if not args.no_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype, bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    model = PeftModel.from_pretrained(model, args.adapter)
    model.eval()

    out, stats = [], {"none": 0, "keep": 0, "illegal_rel": 0, "not_grounded": 0, "triples": 0}
    for i, sent in enumerate(rows):
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"请抽取以下句子中的三元组：\n句子：{sent}"}]
        prompt = tokenizer.apply_chat_template(
            messages, chat_template=chat_template, tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        with torch.no_grad():
            gen = model.generate(**inputs, max_new_tokens=args.max_new_tokens,
                                 do_sample=False, temperature=None, top_p=None,
                                 pad_token_id=tokenizer.pad_token_id)
        text = tokenizer.decode(gen[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()

        triples, status, bad_rel, bad_ground = parse(text, sent)
        stats[status] += 1
        stats["triples"] += len(triples)
        stats["illegal_rel"] += bad_rel
        stats["not_grounded"] += bad_ground
        out.append({"id": i, "sentence": sent, "response": text,
                    "triples": triples, "status": status})
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(rows)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    resp_path = OUT_DIR / f"responses_{args.tag}.jsonl"
    with resp_path.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n = len(out)
    summary = {
        "model": args.tag,
        "samples": n,
        "none_rate": round(stats["none"] / n, 4),
        "keep_rate": round(stats["keep"] / n, 4),
        "avg_triples_per_sentence": round(stats["triples"] / n, 3),
        "illegal_relation_count": stats["illegal_rel"],
        "not_grounded_count": stats["not_grounded"],
        "responses": str(resp_path),
    }
    (OUT_DIR / f"summary_{args.tag}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
