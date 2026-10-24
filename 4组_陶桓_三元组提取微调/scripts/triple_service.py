"""三元组抽取 HTTP 服务（8006）。

加载微调模型，提供 /extract 接口供知识库 import 链路调用。
与知识库进程分离，避免与 BGE-M3 争显存。

启动:
    python scripts/triple_service.py --model ./model/Qwen3-1.7B \
        --adapter finetuned/tourism_sft_1.7b_clean --port 8006

接口:
    POST /extract  {"texts": ["文本1", "文本2"]}
        -> {"triples": [[["头","关系","尾"], ...], ...]}   # 与输入一一对应
    GET  /health   -> {"status": "ok", "model": "..."}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import torch
import uvicorn
from fastapi import FastAPI
from peft import PeftModel
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

ROOT = Path(__file__).resolve().parent.parent
JINJA = ROOT / "new_chat_template.jinja"

RELATIONS = ["位于", "属于", "包含", "途经", "推荐", "适合", "需要", "提供", "邻近", "以…闻名", "建议", "对比"]
SYSTEM_PROMPT = (
    "你是旅游领域信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"
    "可选关系：位于、属于、包含、途经、推荐、适合、需要、提供、邻近、以…闻名、建议、对比。"
)

_state: dict = {}


class ExtractRequest(BaseModel):
    texts: list[str]
    max_new_tokens: int = 160


def normalize(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def parse_triples(raw: str, text: str) -> list[list[str]]:
    """解析模型输出，只保留关系合法且实体落在原文的三元组。"""
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S | re.I)
    if "无三元组" in cleaned:
        return []
    norm_text = normalize(text)
    out, seen = [], set()
    for m in re.finditer(r"[（(]\s*([^（()）]+?)\s*[)）]", cleaned):
        parts = re.split(r"\s*[,，]\s*", m.group(1))
        if len(parts) != 3:
            continue
        h, r, t = (p.strip() for p in parts)
        if not h or not t or r not in RELATIONS:
            continue
        if normalize(h) not in norm_text or normalize(t) not in norm_text:
            continue
        key = (h, r, t)
        if key in seen:
            continue
        seen.add(key)
        out.append([h, r, t])
    return out


def load_model(base: str, adapter: str, four_bit: bool) -> None:
    tokenizer = AutoTokenizer.from_pretrained(base)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    kwargs = {"dtype": dtype, "device_map": {"": 0}}
    if four_bit:
        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(base, **kwargs)
    if adapter and Path(adapter).exists():
        model = PeftModel.from_pretrained(model, adapter)
    model.eval()

    _state.update({
        "model": model,
        "tokenizer": tokenizer,
        "template": JINJA.read_text(encoding="utf-8"),
    })
    print(f"[ready] model={base} adapter={adapter} 4bit={four_bit}")


def infer_one(text: str, max_new_tokens: int) -> list[list[str]]:
    tok = _state["tokenizer"]
    model = _state["model"]
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请抽取以下句子中的三元组：\n句子：{text[:800]}"}]
    prompt = tok.apply_chat_template(
        messages, chat_template=_state["template"], tokenize=False,
        add_generation_prompt=True, enable_thinking=False)
    inputs = tok(prompt, return_tensors="pt").to(model.device)
    with torch.no_grad():
        gen = model.generate(**inputs, max_new_tokens=max_new_tokens,
                             do_sample=False, temperature=None, top_p=None,
                             pad_token_id=tok.pad_token_id)
    raw = tok.decode(gen[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return parse_triples(raw, text)


def create_app() -> FastAPI:
    app = FastAPI(title="Triple Extractor", version="1.0")

    @app.get("/health")
    def health():
        return {"status": "ok", "model": _state.get("model_name", "unknown"),
                "gpu": torch.cuda.is_available()}

    @app.post("/extract")
    def extract(req: ExtractRequest):
        t0 = time.time()
        results = [infer_one(t, req.max_new_tokens) for t in req.texts]
        return {"triples": results, "elapsed_ms": int((time.time() - t0) * 1000)}

    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "model" / "Qwen3-1.7B"))
    ap.add_argument("--adapter", default=str(ROOT / "finetuned" / "tourism_sft_1.7b_clean"))
    ap.add_argument("--port", type=int, default=8006)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-4bit", action="store_true")
    args = ap.parse_args()

    load_model(args.model, args.adapter, not args.no_4bit)
    _state["model_name"] = f"{Path(args.model).name}+{Path(args.adapter).name}"
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
