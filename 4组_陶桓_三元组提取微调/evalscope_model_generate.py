"""使用与 SFT 训练一致的聊天模板生成三元组抽取结果。

默认输出到新的 *_fixed.jsonl 文件，不覆盖旧的评估结果。运行目录可以是
本脚本所在目录，也可以是项目根目录。
"""

from __future__ import annotations

import argparse
import gc
import json
import re
from pathlib import Path
from typing import Any, Iterable

import torch
from peft import PeftModel
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_QUESTIONS_FILE = SCRIPT_DIR / "data" / "eval_questions_with_gold.jsonl"
DEFAULT_BASE_MODEL_PATH = SCRIPT_DIR / "model" / "QuCo-extractor-0.5B"
DEFAULT_SFT_MODEL_PATH = SCRIPT_DIR / "finetuned" / "01_sft_lora_demo"
DEFAULT_DPO_MODEL_PATH = SCRIPT_DIR / "finetuned" / "02_dpo_lora_demo"
DEFAULT_BASE_OUTPUT_FILE = SCRIPT_DIR / "data" / "base_responses_fixed.jsonl"
DEFAULT_SFT_OUTPUT_FILE = SCRIPT_DIR / "data" / "sft_responses_fixed.jsonl"
DEFAULT_DPO_OUTPUT_FILE = SCRIPT_DIR / "data" / "dpo_responses_fixed.jsonl"
SYSTEM_PROMPT = "你是一个信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"


def _has_adapter(path: Path) -> bool:
    return (path / "adapter_config.json").is_file() and (
        (path / "adapter_model.safetensors").is_file()
        or (path / "adapter_model.bin").is_file()
    )


def resolve_path(path: str | Path) -> Path:
    """将命令行路径解析为绝对路径；相对路径优先相对脚本目录。"""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return SCRIPT_DIR / candidate


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} 不是 JSON 对象")
            query = value.get("query") or value.get("prompt")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"{path}:{line_number} 缺少 query/prompt")
            # 统一内部字段，但保留其它字段（尤其是 chosen/gold）供后续评估使用。
            record = dict(value)
            record["query"] = query
            records.append(record)
    return records


def make_prompt(tokenizer: Any, query: str) -> str:
    """按训练数据的 system/user 结构构造生成 prompt。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]
    try:
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    except TypeError:
        # 兼容不支持 enable_thinking 参数的旧版 Transformers/tokenizer。
        return tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )


def load_model(
    adapter_path: Path | None,
    base_model_path: Path = DEFAULT_BASE_MODEL_PATH,
    sft_adapter_path: Path | None = None,
) -> tuple[Any, Any]:
    """显式加载 LoRA；DPO adapter 会叠加到已合并的 SFT 权重上。"""
    if adapter_path is not None:
        if not adapter_path.is_dir():
            raise FileNotFoundError(f"模型目录不存在: {adapter_path}")
        if not _has_adapter(adapter_path):
            raise FileNotFoundError(
                f"{adapter_path} 不是可加载的 LoRA 目录（缺少 adapter_config/model 文件）"
            )
    if not base_model_path.is_dir():
        raise FileNotFoundError(f"基础模型目录不存在: {base_model_path}")

    use_cuda = torch.cuda.is_available()
    dtype = torch.bfloat16 if use_cuda else torch.float32
    model_kwargs: dict[str, Any] = {
        "torch_dtype": dtype,
        "trust_remote_code": True,
    }
    if use_cuda:
        model_kwargs["device_map"] = "auto"

    base_model = AutoModelForCausalLM.from_pretrained(str(base_model_path), **model_kwargs)
    if sft_adapter_path is not None:
        if not _has_adapter(sft_adapter_path):
            raise FileNotFoundError(f"DPO 推理依赖的 SFT adapter 无效: {sft_adapter_path}")
        base_model = PeftModel.from_pretrained(base_model, str(sft_adapter_path)).merge_and_unload()
    model = (
        PeftModel.from_pretrained(base_model, str(adapter_path))
        if adapter_path is not None
        else base_model
    )
    model.eval()

    # 推理统一使用 SFT 训练时保存的模板；DPO tokenizer 没有单独的任务契约。
    tokenizer_path = sft_adapter_path or adapter_path or base_model_path
    if not (
        (tokenizer_path / "tokenizer.json").is_file()
        or (tokenizer_path / "tokenizer_config.json").is_file()
    ):
        tokenizer_path = base_model_path
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_path), trust_remote_code=True)
    template_path = SCRIPT_DIR / "new_chat_template.jinja"
    if template_path.is_file():
        tokenizer.chat_template = template_path.read_text(encoding="utf-8")
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:
            raise ValueError("tokenizer 同时缺少 pad_token 和 eos_token")
        tokenizer.pad_token = tokenizer.eos_token
    return model, tokenizer


def _model_device(model: Any) -> torch.device:
    return next(model.parameters()).device


def clean_response(response: str) -> str:
    """去除模板产生的空 thinking 块及特殊结束标记，保留抽取正文。"""
    response = re.sub(r"<think>.*?</think>\s*", "", response,
                      flags=re.IGNORECASE | re.DOTALL)
    response = response.split("<|im_end|>", 1)[0]
    response = response.split("<|im_start|>", 1)[0]
    response = re.sub(r"```(?:\w+)?", "", response)
    return response.strip()


def generate_batch(
    model: Any,
    tokenizer: Any,
    prompts: list[str],
    batch_size: int = 8,
    max_new_tokens: int = 128,
) -> list[str]:
    responses: list[str] = []
    device = _model_device(model)
    for start in tqdm(range(0, len(prompts), batch_size), desc="Generating"):
        batch = prompts[start : start + batch_size]
        inputs = tokenizer(
            batch,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=2048,
        )
        inputs = {key: value.to(device) for key, value in inputs.items()}
        prompt_width = inputs["input_ids"].shape[1]

        with torch.inference_mode():
            outputs = model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                do_sample=False,
                temperature=None,
                top_p=None,
                top_k=None,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )

        # left padding 后每一行的 prompt 都占 prompt_width 列，续写从同一列开始。
        for row in outputs[:, prompt_width:]:
            responses.append(clean_response(tokenizer.decode(row, skip_special_tokens=True)))
    return responses


def write_results(path: Path, records: list[dict[str, Any]], responses: list[str], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise FileExistsError(f"输出文件已存在，为避免覆盖请指定 --overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for record, response in zip(records, responses):
            output = {"query": record["query"], "response": response}
            for key in ("chosen", "gold", "id"):
                if key in record:
                    output[key] = record[key]
            file.write(json.dumps(output, ensure_ascii=False) + "\n")


def generate_for_model(
    model_name: str,
    model_path: Path | None,
    base_model_path: Path,
    records: list[dict[str, Any]],
    output_path: Path,
    batch_size: int,
    max_new_tokens: int,
    overwrite: bool,
    sft_adapter_path: Path | None = None,
) -> None:
    print(f"\n加载 {model_name}: {model_path or base_model_path}")
    model, tokenizer = load_model(model_path, base_model_path, sft_adapter_path)
    prompts = [make_prompt(tokenizer, record["query"]) for record in records]
    responses = generate_batch(model, tokenizer, prompts, batch_size, max_new_tokens)
    write_results(output_path, records, responses, overwrite)
    print(f"✅ {model_name} 结果已保存: {output_path}（{len(responses)} 条）")
    del model, tokenizer
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions-file", type=Path, default=DEFAULT_QUESTIONS_FILE)
    parser.add_argument("--base-model", type=Path, default=DEFAULT_BASE_MODEL_PATH)
    parser.add_argument("--sft-model", type=Path, default=DEFAULT_SFT_MODEL_PATH)
    parser.add_argument("--dpo-model", type=Path, default=DEFAULT_DPO_MODEL_PATH)
    parser.add_argument("--base-output", type=Path, default=DEFAULT_BASE_OUTPUT_FILE)
    parser.add_argument("--sft-output", type=Path, default=DEFAULT_SFT_OUTPUT_FILE)
    parser.add_argument("--dpo-output", type=Path, default=DEFAULT_DPO_OUTPUT_FILE)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--limit", type=int, default=-1, help="只运行前 N 条，-1 表示全部")
    parser.add_argument(
        "--models",
        nargs="+",
        choices=("base", "sft", "dpo"),
        default=("sft", "dpo"),
        help="要生成的 adapter",
    )
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖 fixed 输出文件")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size < 1 or args.max_new_tokens < 1:
        raise ValueError("--batch-size 和 --max-new-tokens 必须为正数")

    questions_file = resolve_path(args.questions_file)
    records = load_records(questions_file)
    if args.limit >= 0:
        records = records[: args.limit]
    if not records:
        raise ValueError(f"没有可生成的样本: {questions_file}")
    print(f"共 {len(records)} 个问题（来源: {questions_file}）")

    base_model_path = resolve_path(args.base_model)
    sft_model_path = resolve_path(args.sft_model)
    jobs = {
        "base": ("Base", None, resolve_path(args.base_output), None),
        "sft": ("SFT", sft_model_path, resolve_path(args.sft_output), None),
        # DPO 训练前先合并了 SFT LoRA，因此推理必须复现相同的模型链。
        "dpo": ("DPO", resolve_path(args.dpo_model), resolve_path(args.dpo_output), sft_model_path),
    }
    for model_key in args.models:
        model_name, model_path, output_path, prerequisite_adapter = jobs[model_key]
        generate_for_model(
            model_name,
            model_path,
            base_model_path,
            records,
            output_path,
            args.batch_size,
            args.max_new_tokens,
            args.overwrite,
            prerequisite_adapter,
        )


if __name__ == "__main__":
    main()
