"""
对于三元组提取任务的模型QuCo-extractor的GRPO强化学习训练。
手写GRPO训练循环：同一问题的多条候选回答做组内优势标准化（DeepSeek-R1式），
策略更新采用PPO裁剪目标 + k3 KL锚定（参考模型为合并SFT后的基座，通过禁用LoRA零显存获得）。
奖励函数覆盖五个维度：正确性（权重最高）、格式、过程/步骤、长度、风格与多样性。
起点权重：base模型 + 合并SFT LoRA（与DPO脚本的加载方式一致），再套一层新的GRPO LoRA。

v2改动（针对第一轮"提升不大"的诊断）：
1. 采样温度1.0→1.2、组大小4→8：增加探索，让"全错组"有机会采到正确答案（显存已估算，见常量注释）；
2. 动态采样（DAPO式）：过采样一批问题后过滤全对/全错的组——这些组标准化后优势为0，不产生梯度；
3. 奖励口径放宽：关系词归一（去"的/了"等）+ 包含匹配 + 编辑相似度匹配，缓解开放域关系
   "合理表述不唯一"被误判为全错的问题（如 辞职/辞去职务）；实体对正确但关系不符给0.5档部分分；
4. 更激进的更新：学习率1e-5→2e-5，KL系数0.02→0.005（上一轮全程KL仅~6e-4，策略挪动过于保守）。
注意：脚本内的路径均为相对路径，需要在day07目录下运行；MICRO_BATCH_SIZE是OOM主因，勿随意调大。
"""

import json
import os
import random
import re
import time
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import torch
from peft import LoraConfig, PeftModel, get_peft_model
from torch.utils.tensorboard import SummaryWriter
from transformers import AutoModelForCausalLM, AutoTokenizer

# -------------------- 1. 超参数与路径常量 --------------------
BASE_MODEL_PATH = "./model/QuCo-extractor-0.5B/"
SFT_LORA_PATH = "./finetuned/01_sft_lora_demo"          # GRPO的起点：合并SFT LoRA
CHAT_TEMPLATE_PATH = "./new_chat_template.jinja"        # 与SFT/评估脚本相同的对话模板
TRAIN_DATA_PATH = "./data/train_triple_converted.jsonl"
DEV_DATA_PATH = "./data/dev_triple_converted.jsonl"
OUTPUT_DIR = "./finetuned/03_grpo_dir_demo"
FINAL_LORA_PATH = "./finetuned/03_grpo_lora_demo"
LOG_DIR = "./logs/03_grpo_dir_demo"
REPORT_PATH = "./data/grpo_training_report.json"

GROUP_SIZE = 8              # 每个问题采样的候选回答数（GRPO组大小）；4→8只增加rollout的
                            # scores缓冲约0.12GB（96步×8×151936×2B≈0.23GB），更新阶段不受影响
PROMPTS_PER_STEP = 8        # 每步参与更新的目标组数（动态过滤后最多取这么多组）
OVERSAMPLE_RATIO = 2        # 动态采样过采样倍数：每步实际采样 PROMPTS_PER_STEP*该值 个问题，
                            # 过滤全对/全错组后参与更新；不影响显存（逐组顺序生成），只影响每步耗时
MICRO_BATCH_SIZE = 2        # 策略更新时的微批次大小（跨微批次梯度累积）。
                            # OOM主因！logits张量 B×序列长×151936×2B，反向传播还要存softmax
                            # 中间量约×3；MB=8时峰值约2GB+必爆8GB卡，MB=2峰值约1.5~2GB，勿随意调大
MAX_STEPS = 500            # 总训练步数（每步实际生成 PROMPTS_PER_STEP*OVERSAMPLE_RATIO 组）
MAX_NEW_TOKENS = 96        # 生成的最大新token数（金标输出都很短）
SAMPLING_TEMPERATURE = 1.2  # 采样温度：1.0→1.2 增加探索，让全错组有机会采到正确答案产生梯度
LEARNING_RATE = 2e-5
CLIP_EPSILON = 0.2          # PPO裁剪系数
KL_COEFFICIENT = 0.005      # KL锚定系数（k3估计）：0.02→0.005，上一轮全程KL仅~6e-4，策略挪动过保守
REL_MATCH_THRESHOLD = 0.6   # 关系模糊匹配的编辑相似度阈值（缓解关系表述不唯一被误判）
MAX_GRAD_NORM = 1.0
EVAL_EVERY_STEPS = 50       # 每多少步在dev上评估一次
EVAL_SAMPLES = 100          # 每次评估使用的dev样本数
EVAL_BATCH_SIZE = 4         # 评估时批量贪心生成的批大小
SAVE_EVERY_STEPS = 100      # 每多少步保存一次checkpoint
SEED = 42

# 奖励权重：正确性最高，格式适中，过程/长度/风格为辅助调节
REWARD_WEIGHTS = {
    "correctness": 1.0,
    "format": 0.5,
    "process": 0.4,
    "length": 0.3,
    "style": 0.2,
}

SYSTEM_PROMPT = "你是一个信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"

# -------------------- 2. 三元组解析工具 --------------------
# 从 eval_triples.py 精简移植（NFKC规范化 + 括号深度扫描 + 前两个逗号切分），
# 保证奖励打分与离线评估的口径完全一致。
Triple = tuple[str, str, str]


@dataclass
class ParsedTriples:
    triples: set[Triple]        # 规范化后的三元组集合
    format_valid: bool          # 是否完全合规（可解析、无畸形括号、无多余文本）
    total_segments: int = 0     # 括号片段总数
    valid_occurrences: int = 0  # 成功解析为(头,关系,尾)的片段数


def normalize_field(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", value).strip(" \t\r\n,，")


def strip_wrappers(text: str) -> str:
    """去掉 <think>、代码块围栏、对话特殊标记等外壳，保留三元组正文。"""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"```(?:\w+)?", "", text)
    return text.replace("<|im_end|>", "").replace("<|im_start|>", "").strip()


def _parenthesized_segments(text: str) -> tuple[list[str], str, bool]:
    """按括号深度扫描出所有 (...) 片段；返回 (片段列表, 括号外文本, 是否畸形)。"""
    segments: list[str] = []
    outside: list[str] = []
    depth = 0
    inner_depth = 0
    start = -1
    malformed = False
    for index, char in enumerate(text):
        if char == "(":
            if depth == 0:
                start = index + 1
                depth = 1
            elif text[start:index].count(",") >= 2:
                # 尾实体可能包含括注；两次字段分隔符之后的括号属于实体正文。
                inner_depth += 1
            else:
                depth += 1
        elif char == ")":
            if depth == 0:
                malformed = True
                outside.append(char)
            elif inner_depth:
                inner_depth -= 1
            elif depth > 1:
                depth -= 1
            else:
                depth = 0
                segments.append(text[start:index])
                outside.append(" ")
        elif depth == 0:
            outside.append(char)
    if depth:
        # 有些标注的尾实体含中文括号，但外层三元组只以最后一个 ')' 闭合。
        # 只要可恢复出三个非空字段，就交给字段解析判断内容。
        malformed = False
    return segments, "".join(outside).strip(), malformed


def parse_triples(text: str) -> ParsedTriples:
    """解析一行或多行三元组；关系/实体内部可以包含普通标点。"""
    cleaned = strip_wrappers(text)
    if cleaned in {"", "无三元组", "无"}:
        return ParsedTriples(set(), cleaned in {"无三元组", "无"}, 0, 0)

    segments, outside, malformed = _parenthesized_segments(cleaned)
    if not segments and cleaned.startswith("("):
        segments = [cleaned[1:].rstrip(")）")]
    triples: set[Triple] = set()
    valid_occurrences = 0
    for segment in segments:
        # 数据格式没有转义机制，因此按前两个逗号切分，尾实体保留后续逗号。
        parts = re.split(r"\s*[,，]\s*", segment, maxsplit=2)
        normalized = tuple(normalize_field(part) for part in parts)
        if len(normalized) != 3 or any(not part for part in normalized):
            malformed = True
            continue
        valid_occurrences += 1
        triples.add(normalized)  # type: ignore[arg-type]

    extra = re.sub(r"[\s;；。]+", "", outside)
    format_valid = bool(triples) and not malformed and not extra
    return ParsedTriples(triples, format_valid, len(segments), valid_occurrences)


# -------------------- 3. 奖励函数（五个维度） --------------------
def format_reward(response: str) -> float:
    """格式奖励 ∈ [-0.5, 0.5]：输出可解析为若干(头实体, 关系, 尾实体)行且无多余文本。
    完全合规给0.5；部分行合法按比例给分；完全不可解析给强惩罚-0.5。
    （理论上的硬性约束可用-inf，但组内优势标准化对极端值会出NaN，故用大幅负值替代。）"""
    parsed = parse_triples(response)
    if parsed.format_valid:
        return 0.5
    if parsed.total_segments > 0:
        return 0.5 * parsed.valid_occurrences / parsed.total_segments
    return -0.5


def _set_f1(predicted: set, gold: set) -> float:
    """集合层面的micro F1。"""
    if not predicted and not gold:
        return 1.0
    tp = len(predicted & gold)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(gold) if gold else 0.0
    if precision + recall == 0.0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# 关系词常见辅助成分，归一时去除（出生于/收购了/辞去了 → 出生/收购/辞去→辞），
# 剥离后借助包含匹配对上"辞职/上任"等核心动词，覆盖大部分表述差异
_REL_STRIP_SUFFIXES = ("的", "了", "着", "之", "得", "去", "到", "于", "在", "成", "为")


def _normalize_relation(rel: str) -> str:
    """关系词归一：去首尾空白与小写化后，去掉尾部常见辅助成分。"""
    rel = normalize_field(rel).lower()
    while len(rel) > 1 and rel.endswith(_REL_STRIP_SUFFIXES):
        rel = rel[:-1]
    return rel


def _rel_match(rel_a: str, rel_b: str) -> bool:
    """关系模糊匹配：归一后相等、一方包含另一方（出生于/出生）、
    或编辑相似度≥REL_MATCH_THRESHOLD（辞职/辞去职务）均视为匹配。
    缓解开放域关系"合理表述不唯一"被奖励误判为全错的问题。"""
    a, b = _normalize_relation(rel_a), _normalize_relation(rel_b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= REL_MATCH_THRESHOLD


def _fuzzy_relation_f1(pred_rels: list[str], gold_rels: list[str]) -> float:
    """关系集合F1：用模糊匹配贪心计TP（每个金标关系只被匹配一次）。"""
    if not pred_rels and not gold_rels:
        return 1.0
    used = [False] * len(gold_rels)
    best_for_pred = []
    for pred_rel in pred_rels:
        best_index = -1
        for gi, gold_rel in enumerate(gold_rels):
            if used[gi]:
                continue
            if _rel_match(pred_rel, gold_rel):
                best_index = gi
                break
        best_for_pred.append((1.0 if best_index >= 0 else 0.0, best_index))
    tp = 0.0
    for score, gi in sorted(best_for_pred, key=lambda item: -item[0]):
        if score > 0 and not used[gi]:
            used[gi] = True
            tp += score
    precision = tp / len(pred_rels) if pred_rels else 0.0
    recall = tp / len(gold_rels) if gold_rels else 0.0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def correctness_reward(response: str, gold_text: str) -> tuple[float, dict[str, float]]:
    """正确性奖励 ∈ [0, 1]（权重最高的核心维度），返回(分数, F1明细)：
    - 精确匹配（规范化三元组集合相等且格式合法）→ 1.0；
    - 否则用宽松连续信号（缓解开放域关系"合理表述不唯一"被误判为全错）：
      三元组F1按贪心模糊匹配计TP——头尾实体相同且关系模糊匹配→1.0；
      头尾相同但关系不符→0.5（实体对正确的部分分，原口径约0.4且混入其他因素）；
      头尾之一相同→0.25（实体边界/配对错误）；
      关系F1也用模糊匹配；实体F1仍用严格集合；
      score = 0.5*三元组F1 + 0.3*实体F1 + 0.2*关系F1；
    - 解析失败 → 0。"""
    prediction = parse_triples(response)
    gold = parse_triples(gold_text)
    if prediction.triples == gold.triples and prediction.format_valid:
        return 1.0, {"triple_f1": 1.0, "entity_f1": 1.0, "relation_f1": 1.0}
    pred_list = sorted(prediction.triples)
    gold_list = sorted(gold.triples)

    # 三元组级TP：每条预测找最高得分的未匹配金标，再按得分降序贪心占用，避免低分抢占高分
    used = [False] * len(gold_list)
    best_for_pred = []
    for head, rel, tail in pred_list:
        best_score, best_index = 0.0, -1
        for gi, (g_head, g_rel, g_tail) in enumerate(gold_list):
            if used[gi]:
                continue
            head_eq = normalize_field(head) == normalize_field(g_head)
            tail_eq = normalize_field(tail) == normalize_field(g_tail)
            if head_eq and tail_eq:
                score = 1.0 if _rel_match(rel, g_rel) else 0.5
            elif head_eq or tail_eq:
                score = 0.25
            else:
                score = 0.0
            if score > best_score:
                best_score, best_index = score, gi
        best_for_pred.append((best_score, best_index))
    triple_tp = 0.0
    for score, gi in sorted(best_for_pred, key=lambda item: -item[0]):
        if score > 0 and gi >= 0 and not used[gi]:
            used[gi] = True
            triple_tp += score
    precision = triple_tp / len(pred_list) if pred_list else 0.0
    recall = triple_tp / len(gold_list) if gold_list else 0.0
    triple_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    pred_entities = {h for h, _, _ in prediction.triples} | {t for _, _, t in prediction.triples}
    gold_entities = {h for h, _, _ in gold.triples} | {t for _, _, t in gold.triples}
    info = {
        "triple_f1": triple_f1,
        "entity_f1": _set_f1(pred_entities, gold_entities),
        "relation_f1": _fuzzy_relation_f1(sorted({r for _, r, _ in prediction.triples}),
                                          sorted({r for _, r, _ in gold.triples})),
    }
    score = 0.5 * info["triple_f1"] + 0.3 * info["entity_f1"] + 0.2 * info["relation_f1"]
    return score, info


def process_reward(response: str, sentence: str) -> float:
    """过程/步骤奖励 ∈ [0, 0.3]（轻量PRM的确定性替代）：
    把每条预测三元组当作一个中间步骤，头/尾实体（规范化并去空白后）
    都能在原句中找到才算该步"落地"。奖励 = 0.3 × 落地步骤比例，
    监督"实体抽取"这一前置中间步骤，抑制幻觉实体。"""
    prediction = parse_triples(response)
    if not prediction.triples:
        return 0.0
    compact_sentence = re.sub(r"\s+", "", unicodedata.normalize("NFKC", sentence))
    grounded = 0
    for head, _, tail in prediction.triples:
        head_key = re.sub(r"\s+", "", head)
        tail_key = re.sub(r"\s+", "", tail)
        if head_key and tail_key and head_key in compact_sentence and tail_key in compact_sentence:
            grounded += 1
    return 0.3 * grounded / len(prediction.triples)


def length_reward(response: str, gold_text: str, is_exact: bool) -> float:
    """长度奖励 ∈ [-0.1, 0.5]（辅助调节，主体分只在答案正确时激活，避免为凑长度牺牲正确性）：
    - 精确匹配时：更短的正确答案得分更高——不超过金标1.2倍给满0.5，超出按比例递减；
    - 不正确时为0；但对明显超长（>400字符，对应截断或啰嗦）给-0.1防刷长度。"""
    text = strip_wrappers(response)
    if is_exact:
        budget = 1.2 * max(len(strip_wrappers(gold_text)), 1)
        if len(text) <= budget:
            return 0.5
        return 0.5 * budget / len(text)
    if len(text) > 400:
        return -0.1
    return 0.0


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    compact = re.sub(r"\s+", "", strip_wrappers(text))
    return {compact[i:i + n] for i in range(len(compact) - n + 1)}


def style_diversity_reward(response: str, other_group_responses: list[str] | None = None) -> float:
    """风格与多样性奖励 ∈ [-0.2, 0.2]：
    - 重复惩罚：字符3-gram重复率越高罚得越多（最多-0.2），抑制复读机式输出；
    - 组内多样性：与同问题其他候选（other_group_responses，不含自身）的平均
      3-gram Jaccard 距离越高奖励越多（最多+0.2），激励探索不同抽取路径；
      单条评估时传None，则该分量为0。"""
    compact = re.sub(r"\s+", "", strip_wrappers(response))
    total = max(len(compact) - 2, 0)
    unique = len({compact[i:i + 3] for i in range(total)})
    repetition = 1 - unique / total if total > 0 else 0.0
    score = -0.2 * repetition

    if other_group_responses:
        grams = _char_ngrams(response)
        distances = []
        for other in other_group_responses:
            other_grams = _char_ngrams(other)
            union = grams | other_grams
            similarity = len(grams & other_grams) / len(union) if union else 1.0
            distances.append(1.0 - similarity)
        score += 0.2 * (sum(distances) / len(distances))
    return score


def total_reward(
    response: str,
    gold_text: str,
    sentence: str,
    other_group_responses: list[str] | None = None,
) -> tuple[float, dict[str, float]]:
    """总奖励 = 1.0*正确性 + 0.5*格式 + 0.4*过程 + 0.3*长度 + 0.2*风格多样性，
    返回(总奖励, 各分量明细)，分量明细会逐步记录到TensorBoard。"""
    prediction = parse_triples(response)
    gold = parse_triples(gold_text)
    is_exact = prediction.triples == gold.triples and prediction.format_valid
    correctness, correctness_info = correctness_reward(response, gold_text)
    info: dict[str, float] = {
        "correctness": correctness,
        "format": format_reward(response),
        "process": process_reward(response, sentence),
        "length": length_reward(response, gold_text, is_exact),
        "style": style_diversity_reward(response, other_group_responses),
        **correctness_info,
    }
    total = sum(REWARD_WEIGHTS[name] * info[name] for name in REWARD_WEIGHTS)
    return total, info


# -------------------- 4. 模型加载 --------------------
def load_policy(device: torch.device) -> tuple[PeftModel, AutoTokenizer]:
    """加载base模型并合并SFT LoRA（与DPO脚本一致），再套一层新的GRPO LoRA。"""
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL_PATH,
        dtype=torch.bfloat16 if device.type == "cuda" else torch.float32,
        trust_remote_code=True,
    )
    if os.path.exists(os.path.join(SFT_LORA_PATH, "adapter_config.json")):
        peft_model = PeftModel.from_pretrained(base, SFT_LORA_PATH)
        model = peft_model.merge_and_unload()  # 合并为完整模型
        print("✅ SFT LoRA 已合并为完整模型")
    else:
        model = base
        print("ℹ️ 未找到 SFT LoRA，直接使用基础模型")

    policy = get_peft_model(model, LoraConfig(
        r=8,
        lora_alpha=8,
        target_modules=["q_proj", "v_proj"],
        lora_dropout=0.05,
        task_type="CAUSAL_LM",
    ))
    policy.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    policy.to(device)

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_PATH, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    # 用带 {% generation %} 标记的自定义模板覆盖（与推理评估脚本一致）
    tokenizer.chat_template = Path(CHAT_TEMPLATE_PATH).read_text(encoding="utf-8")
    return policy, tokenizer


# -------------------- 5. 数据加载 --------------------
def load_examples(path: str) -> list[dict]:
    """读取messages格式数据，抽取sentence(原句)与gold(金标三元组文本)。"""
    examples = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            messages = json.loads(line)["messages"]
            user_content = next(m["content"] for m in messages if m["role"] == "user")
            examples.append({
                "user_content": user_content,
                "sentence": user_content.split("句子：")[-1],
                "gold": next(m["content"] for m in messages if m["role"] == "assistant"),
            })
    return examples


def build_prompt(tokenizer: AutoTokenizer, user_content: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)


# -------------------- 6. rollout / 策略更新 / 评估 --------------------
@dataclass
class Episode:
    """一条完整的采样轨迹：prompt + 回答 + 采样时的对数概率 + 奖励。"""
    example_index: int
    prompt_ids: torch.Tensor
    response_ids: torch.Tensor
    old_log_probs: torch.Tensor
    response_text: str
    reward: float
    reward_info: dict


@torch.no_grad()
def rollout_group(
    policy: PeftModel,
    tokenizer: AutoTokenizer,
    example: dict,
    example_index: int,
    device: torch.device,
) -> list[Episode]:
    """对同一个问题采样GROUP_SIZE条回答。组内prompt完全相同，可一次generate批量生成；
    多样性奖励分量需要整组回答一起计算。"""
    prompt = build_prompt(tokenizer, example["user_content"])
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    input_ids = inputs["input_ids"].repeat(GROUP_SIZE, 1)
    attention_mask = inputs["attention_mask"].repeat(GROUP_SIZE, 1)
    output = policy.generate(
        input_ids=input_ids,
        attention_mask=attention_mask,
        max_new_tokens=MAX_NEW_TOKENS,
        do_sample=True,
        temperature=SAMPLING_TEMPERATURE,
        top_p=1.0,
        top_k=0,
        repetition_penalty=1.0,  # 关闭重复惩罚：保证scores与更新时的原始logits口径一致
        output_scores=True,
        return_dict_in_generate=True,
        pad_token_id=tokenizer.pad_token_id,
        eos_token_id=tokenizer.eos_token_id,
    )
    prompt_length = input_ids.shape[1]
    if not output.scores:
        raise RuntimeError("模型没有生成任何token")
    response_ids_all = output.sequences[:, prompt_length:]  # (G, gen_len)

    # 逐步gather采样时的对数概率（每步只做一次小log_softmax，避免整包占显存）
    gathered = []
    for step, step_scores in enumerate(output.scores):
        log_probs = torch.log_softmax(step_scores.float(), dim=-1)
        tokens = response_ids_all[:, step].unsqueeze(1)
        gathered.append(log_probs.gather(1, tokens).squeeze(1))
    gathered = torch.stack(gathered, dim=0)  # (gen_len, G)

    # 每条回答截断到首个EOS（含EOS），之后的pad部分不计入
    ends = []
    group_responses = []
    for row in range(GROUP_SIZE):
        eos_positions = (response_ids_all[row] == tokenizer.eos_token_id).nonzero()
        end = eos_positions[0].item() + 1 if len(eos_positions) > 0 else response_ids_all.shape[1]
        ends.append(end)
        group_responses.append(tokenizer.decode(response_ids_all[row][:end], skip_special_tokens=True))

    episodes = []
    for row in range(GROUP_SIZE):
        others = group_responses[:row] + group_responses[row + 1:]  # 组内其余候选，用于多样性分量
        reward, info = total_reward(group_responses[row], example["gold"], example["sentence"], others)
        episodes.append(Episode(
            example_index=example_index,
            prompt_ids=inputs["input_ids"][0].cpu(),
            response_ids=response_ids_all[row][:ends[row]].cpu(),
            old_log_probs=gathered[:ends[row], row].cpu(),
            response_text=group_responses[row],
            reward=reward,
            reward_info=info,
        ))
    return episodes


def rollout(
    policy: PeftModel,
    tokenizer: AutoTokenizer,
    examples: list[dict],
    start_index: int,
    device: torch.device,
) -> list[Episode]:
    policy.eval()
    policy.config.use_cache = True
    episodes = []
    for offset, example in enumerate(examples):
        episodes.extend(rollout_group(policy, tokenizer, example, start_index + offset, device))
    return episodes


def normalize_rewards_per_group(episodes: list[Episode], device: torch.device) -> torch.Tensor:
    """GRPO核心：同一问题的候选回答做组内优势标准化 (r - mean) / (std + 1e-4)。"""
    advantages = torch.zeros(len(episodes), dtype=torch.float32, device=device)
    groups: dict[int, list[int]] = {}
    for index, episode in enumerate(episodes):
        groups.setdefault(episode.example_index, []).append(index)
    for indices in groups.values():
        rewards = torch.tensor([episodes[i].reward for i in indices], dtype=torch.float32, device=device)
        advantages[indices] = (rewards - rewards.mean()) / (rewards.std(unbiased=False) + 1e-4)
    return advantages


def filter_informative_groups(episodes: list[Episode], max_groups: int) -> tuple[list[Episode], int, int]:
    """动态采样（DAPO式）：过滤组内奖励完全相同的组——全对/全错组标准化后优势全为0，
    不产生任何梯度，白白消耗更新算力。最多保留max_groups组（按采样顺序取前N个），
    返回(保留的episode列表, 保留组数, 总组数)。"""
    groups: dict[int, list[Episode]] = {}
    for episode in episodes:
        groups.setdefault(episode.example_index, []).append(episode)
    kept: list[Episode] = []
    kept_groups = 0
    for group in groups.values():
        rewards = [episode.reward for episode in group]
        if max(rewards) - min(rewards) <= 1e-6:
            continue
        kept.extend(group)
        kept_groups += 1
        if kept_groups >= max_groups:
            break
    return kept, kept_groups, len(groups)


def prepare_batch(
    episodes: list[Episode],
    pad_token_id: int,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """把一组episode拼成左对齐的批量张量；response_mask标出需要计算损失的位置。"""
    max_length = max(len(episode.prompt_ids) + len(episode.response_ids) for episode in episodes)
    input_ids = torch.full((len(episodes), max_length), pad_token_id, dtype=torch.long)
    attention_mask = torch.zeros_like(input_ids)
    response_mask = torch.zeros((len(episodes), max_length - 1), dtype=torch.float32)
    old_log_probs = torch.zeros_like(response_mask)

    for row, episode in enumerate(episodes):
        sequence = torch.cat([episode.prompt_ids, episode.response_ids])
        sequence_length = len(sequence)
        response_start = len(episode.prompt_ids) - 1  # 预测第一个回答token的位置
        response_end = response_start + len(episode.response_ids)
        input_ids[row, :sequence_length] = sequence
        attention_mask[row, :sequence_length] = 1
        response_mask[row, response_start:response_end] = 1
        old_log_probs[row, response_start:response_end] = episode.old_log_probs

    return (
        input_ids.to(device),
        attention_mask.to(device),
        response_mask.to(device),
        old_log_probs.to(device),
    )


def token_log_probs(logits: torch.Tensor, input_ids: torch.Tensor) -> torch.Tensor:
    """取每个位置实际token的对数概率；逐行转float32做log_softmax，兼顾精度与显存。"""
    shifted_logits = logits[:, :-1, :]
    target_ids = input_ids[:, 1:]
    per_token = []
    for row_logits, row_targets in zip(shifted_logits, target_ids):
        row_logps = torch.log_softmax(row_logits.float(), dim=-1)
        per_token.append(row_logps.gather(1, row_targets.unsqueeze(1)).squeeze(1))
    return torch.stack(per_token)


def update_policy(
    policy: PeftModel,
    optimizer: torch.optim.Optimizer,
    episodes: list[Episode],
    pad_token_id: int,
    device: torch.device,
) -> dict[str, float]:
    """策略更新：PPO裁剪目标 + k3 KL锚定。
    参考模型=合并SFT后的基座，通过disable_adapter零显存获得；
    episode按序列长度排序后切微批次做梯度累积，最后统一裁剪梯度并step。"""
    policy.train()
    policy.config.use_cache = False
    advantages = normalize_rewards_per_group(episodes, device)

    order = sorted(range(len(episodes)), key=lambda i: len(episodes[i].prompt_ids) + len(episodes[i].response_ids))
    micro_batches = [order[i:i + MICRO_BATCH_SIZE] for i in range(0, len(order), MICRO_BATCH_SIZE)]
    optimizer.zero_grad(set_to_none=True)
    loss_sum = 0.0
    kl_sum = 0.0
    token_count = 0
    for batch_indices in micro_batches:
        batch = [episodes[i] for i in batch_indices]
        batch_advantages = advantages[torch.tensor(batch_indices, device=device)]
        input_ids, attention_mask, response_mask, old_log_probs = prepare_batch(batch, pad_token_id, device)

        new_log_probs = token_log_probs(
            policy(input_ids=input_ids, attention_mask=attention_mask, use_cache=False).logits, input_ids
        )
        with torch.no_grad(), policy.disable_adapter():
            reference_logits = policy(input_ids=input_ids, attention_mask=attention_mask, use_cache=False).logits
        reference_log_probs = token_log_probs(reference_logits, input_ids)

        ratios = torch.exp(new_log_probs - old_log_probs)
        unclipped = ratios * batch_advantages.unsqueeze(1)
        clipped = torch.clamp(ratios, 1.0 - CLIP_EPSILON, 1.0 + CLIP_EPSILON) * batch_advantages.unsqueeze(1)
        policy_objective = torch.minimum(unclipped, clipped)
        log_ratio = reference_log_probs - new_log_probs
        token_kl = torch.exp(log_ratio) - log_ratio - 1.0  # k3估计，非负
        token_loss = -policy_objective + KL_COEFFICIENT * token_kl
        mask_count = response_mask.sum()
        loss = (token_loss * response_mask).sum() / mask_count.clamp_min(1)
        (loss / len(micro_batches)).backward()

        loss_sum += float(loss.item()) / len(micro_batches)
        kl_sum += float((token_kl * response_mask).sum().item())
        token_count += int(mask_count.item())

    grad_norm = torch.nn.utils.clip_grad_norm_(policy.parameters(), max_norm=MAX_GRAD_NORM)
    optimizer.step()
    policy.config.use_cache = True
    return {"loss": loss_sum, "kl": kl_sum / max(token_count, 1), "grad_norm": float(grad_norm.item())}


@torch.no_grad()
def evaluate(policy: PeftModel, tokenizer: AutoTokenizer, dev_examples: list[dict], device: torch.device) -> dict:
    """在dev子集上批量贪心生成（左padding），按eval_triples.py同口径算exact与micro P/R/F1。
    贪心解码沿用模型generation_config的重复惩罚，与离线评估脚本保持一致。"""
    policy.eval()
    policy.config.use_cache = True
    tokenizer.padding_side = "left"
    tp = fp = fn = exact = 0
    for start in range(0, len(dev_examples), EVAL_BATCH_SIZE):
        batch = dev_examples[start:start + EVAL_BATCH_SIZE]
        prompts = [build_prompt(tokenizer, example["user_content"]) for example in batch]
        inputs = tokenizer(prompts, return_tensors="pt", padding=True).to(device)
        output = policy.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
        responses = tokenizer.batch_decode(output[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        for example, response in zip(batch, responses):
            prediction = parse_triples(response)
            gold = parse_triples(example["gold"])
            tp += len(gold.triples & prediction.triples)
            fp += len(prediction.triples - gold.triples)
            fn += len(gold.triples - prediction.triples)
            exact += int(prediction.triples == gold.triples and prediction.format_valid)
    tokenizer.padding_side = "right"
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "exact_match": exact / len(dev_examples)}


# -------------------- 7. 训练主循环 --------------------
def main() -> None:
    random.seed(SEED)
    torch.manual_seed(SEED)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    start_time = time.time()

    train_examples = load_examples(TRAIN_DATA_PATH)
    dev_examples = load_examples(DEV_DATA_PATH)[:EVAL_SAMPLES]
    print(f"设备: {device}; 训练样本: {len(train_examples)}; 评估样本: {len(dev_examples)}")

    policy, tokenizer = load_policy(device)
    policy.print_trainable_parameters()
    optimizer = torch.optim.AdamW(policy.parameters(), lr=LEARNING_RATE, betas=(0.9, 0.999), weight_decay=0.0)
    writer = SummaryWriter(LOG_DIR)

    order = list(range(len(train_examples)))
    random.shuffle(order)
    cursor = 0
    history = []
    final_eval: dict = {}
    for step in range(1, MAX_STEPS + 1):
        # 动态采样：过采样一批问题（PROMPTS_PER_STEP×OVERSAMPLE_RATIO），
        # rollout后过滤全对/全错的组，只把组内奖励有差异的组送去更新
        batch_examples = []
        for _ in range(PROMPTS_PER_STEP * OVERSAMPLE_RATIO):
            if cursor >= len(order):
                random.shuffle(order)  # 进入下一个epoch
                cursor = 0
            batch_examples.append(train_examples[order[cursor]])
            cursor += 1
        episodes = rollout(policy, tokenizer, batch_examples, 0, device)
        kept_episodes, kept_groups, total_groups = filter_informative_groups(episodes, PROMPTS_PER_STEP)
        informative_ratio = kept_groups / total_groups if total_groups else 0.0
        writer.add_scalar("有效组比例", informative_ratio, step)

        mean_reward = sum(episode.reward for episode in episodes) / len(episodes)
        history_entry: dict = {"step": step, "平均奖励": mean_reward, "有效组比例": informative_ratio}
        metrics: dict[str, float] | None = None
        component_means: dict[str, float] = {}
        exact_rate = 0.0
        if not kept_episodes:
            print(f"步骤 {step}/{MAX_STEPS}: {total_groups} 组全部全对/全错，本步跳过更新, "
                  f"平均奖励: {mean_reward:.3f}")
        else:
            metrics = update_policy(policy, optimizer, kept_episodes, tokenizer.pad_token_id, device)
            component_means = {
                name: sum(episode.reward_info[name] for episode in kept_episodes) / len(kept_episodes)
                for name in REWARD_WEIGHTS
            }
            exact_rate = sum(1 for episode in kept_episodes if episode.reward_info["correctness"] >= 1.0) / len(kept_episodes)
            history_entry.update({**component_means, **metrics})

        history.append(history_entry)
        if metrics is not None:
            writer.add_scalar("损失", metrics["loss"], step)
            for name, value in component_means.items():
                writer.add_scalar(f"奖励分量/{name}", value, step)
            writer.add_scalar("精确匹配率", exact_rate, step)
            writer.add_scalar("kl", metrics["kl"], step)
            writer.add_scalar("梯度范数", metrics["grad_norm"], step)
        writer.add_scalar("平均奖励", mean_reward, step)
        if metrics is not None:
            print(f"步骤 {step}/{MAX_STEPS}, 有效组: {kept_groups}/{total_groups}, 平均奖励: {mean_reward:.3f}, "
                  f"正确性: {component_means['correctness']:.3f}, 精确匹配率: {exact_rate:.2%}, "
                  f"loss: {metrics['loss']:.5f}, kl: {metrics['kl']:.5f}")

        if step % EVAL_EVERY_STEPS == 0 or step == MAX_STEPS:
            final_eval = evaluate(policy, tokenizer, dev_examples, device)
            for name, value in final_eval.items():
                writer.add_scalar(f"评估/{name}", value, step)
            print(f"  评估: exact={final_eval['exact_match']:.4f}, "
                  f"P/R/F1={final_eval['precision']:.4f}/{final_eval['recall']:.4f}/{final_eval['f1']:.4f}")
        if step % SAVE_EVERY_STEPS == 0:
            checkpoint_dir = os.path.join(OUTPUT_DIR, f"checkpoint-{step:06d}")
            policy.save_pretrained(checkpoint_dir)
            print(f"  已保存checkpoint: {checkpoint_dir}")

    policy.save_pretrained(FINAL_LORA_PATH)
    tokenizer.save_pretrained(FINAL_LORA_PATH)
    writer.close()

    report = {
        "超参数": {
            "group_size": GROUP_SIZE,
            "prompts_per_step": PROMPTS_PER_STEP,
            "oversample_ratio": OVERSAMPLE_RATIO,
            "micro_batch_size": MICRO_BATCH_SIZE,
            "max_steps": MAX_STEPS,
            "max_new_tokens": MAX_NEW_TOKENS,
            "temperature": SAMPLING_TEMPERATURE,
            "learning_rate": LEARNING_RATE,
            "clip_epsilon": CLIP_EPSILON,
            "kl_coefficient": KL_COEFFICIENT,
            "rel_match_threshold": REL_MATCH_THRESHOLD,
            "reward_weights": REWARD_WEIGHTS,
            "train_data": TRAIN_DATA_PATH,
            "dev_data": DEV_DATA_PATH,
            "base_model": BASE_MODEL_PATH,
            "sft_lora": SFT_LORA_PATH,
            "总耗时秒": round(time.time() - start_time, 1),
        },
        "history": history,
        "final_eval": final_eval,
    }
    Path(REPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(f"✅ GRPO LoRA 已保存到 {FINAL_LORA_PATH}, 训练报告已保存到 {REPORT_PATH}")


if __name__ == "__main__":
    main()
