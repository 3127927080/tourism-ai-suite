import json
import os
import time
from openai import OpenAI

# ========== 配置区域（请根据你的情况修改）==========
# 输入 SFT 数据文件（JSONL 格式）
INPUT_FILE = "./data/test_triple_converted.jsonl"
# 输出 DPO 数据文件
OUTPUT_FILE = "./data/dpo_test_data.jsonl"

# API 配置
API_BASE = "https://api.deepseek.com/v1"        # OpenAI 或兼容接口
API_KEY = os.environ.get("DEEPSEEK_API_KEY")
MODEL_NAME = "deepseek-v4-flash"                  # 模型名称，如 gpt-4, deepseek-chat

# 生成参数
TEMPERATURE = 1.4        # 越高越随机，越容易出错，建议 1.2~1.5
DELAY = 0.5              # 每条请求间隔（秒），避免限流
# ==================================================

# 系统提示
SYSTEM_PROMPT = "你是一个信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"

# 生成“差”答案的提示词（引导模型犯典型错误）
BAD_INSTRUCTION = """
请根据以下句子抽取实体关系三元组，但请注意：你的回答需要包含至少一个明显的错误，例如：
- 实体识别错误（将句子中的某个实体替换为另一个不存在的词或错误词）
- 关系识别错误（将关系词换成意思相反或不相关的词）
- 输出不完整（只输出部分三元组，或遗漏重要实体）
- 增加一个完全虚构的、句子中不存在的三元组（幻觉）
请确保你的输出格式仍然符合“(头实体, 关系, 尾实体)”的样式，但内容必须是不准确或有缺陷的。
句子：{sentence}
你的回答（有错误的三元组）：
"""

# ---------- 核心逻辑 ----------
def generate_bad_triple(client, model, sentence, temperature):
    """调用 API 生成一个包含错误的三元组回答"""
    user_prompt = BAD_INSTRUCTION.format(sentence=sentence)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            extra_body={
                "thinking": {"type": "disabled"}  # 关键：关闭思考模式
            },
            temperature=temperature,
            max_tokens=256,
            top_p=0.9,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"API 调用失败: {e}")
        # 降级：返回一个明显的错误格式
        return "（错误实体，错误关系，错误尾实体）"

def main():
    if not API_KEY:
        raise RuntimeError("请先设置环境变量 DEEPSEEK_API_KEY")
    client = OpenAI(base_url=API_BASE, api_key=API_KEY)

    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    dpo_samples = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        data = json.loads(line)

        # ----- 提取 prompt 和 chosen -----
        if "messages" in data:
            # 格式1：{"messages": [...]}
            user_content = None
            assistant_content = None
            for msg in data["messages"]:
                if msg["role"] == "user":
                    user_content = msg["content"]
                elif msg["role"] == "assistant":
                    assistant_content = msg["content"]
            prompt = user_content
            chosen = assistant_content
        elif "prompt" in data and "chosen" in data:
            # 格式2：{"prompt": ..., "chosen": ...}
            prompt = data["prompt"]
            chosen = data["chosen"]
        else:
            print(f"跳过第 {i+1} 行，未知格式: {list(data.keys())}")
            continue

        if prompt is None or chosen is None:
            print(f"跳过第 {i+1} 行，缺少 prompt 或 chosen")
            continue

        # 从 prompt 中提取原始句子（假设包含“句子：”）
        if "句子：" in prompt:
            sentence = prompt.split("句子：")[-1].strip()
        else:
            sentence = prompt  # 如果 prompt 本身就是句子

        # 生成差的答案
        rejected = generate_bad_triple(client, MODEL_NAME, sentence, TEMPERATURE)

        # 构建 DPO 样本
        new_sample = {
            "prompt": prompt,
            "chosen": chosen,
            "rejected": rejected
        }
        dpo_samples.append(new_sample)

        if (i+1) % 20 == 0:
            print(f"已处理 {i+1} 条")
        time.sleep(DELAY)

    # 写入输出文件
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for item in dpo_samples:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"✅ 完成！共生成 {len(dpo_samples)} 条 DPO 数据，保存至 {OUTPUT_FILE}")

if __name__ == "__main__":
    main()