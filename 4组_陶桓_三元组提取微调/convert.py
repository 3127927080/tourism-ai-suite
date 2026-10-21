import json
import re
import ast
from pathlib import Path


def extract_samples_by_regex(content):
    """使用正则表达式从文本中提取每个样本的 text 和 spo_list"""
    # 匹配 {'text': '...', 'spo_list': [...]} 模式
    # 注意：这适用于简单情况，假设文本和列表中没有嵌套复杂结构
    pattern = r"\{'text':\s*'(.*?)',\s*'spo_list':\s*(\[.*?\])\}"
    matches = re.findall(pattern, content, re.DOTALL)
    samples = []
    for text, spo_str in matches:
        # 清理 spo_str 中的单引号，尝试解析为 Python 列表
        try:
            # 将单引号替换为双引号并解析为 JSON
            spo_list = json.loads(spo_str.replace("'", '"'))
        except:
            # 如果失败，使用 eval（数据可信）
            try:
                spo_list = eval(spo_str)
            except:
                spo_list = []
        samples.append({"text": text, "spo_list": spo_list})
    return samples


def safe_parse(content):
    """尝试多种方式解析，失败则使用正则提取"""
    # 1. 去除续行符（反斜杠后跟换行）
    content = content.replace('\\\n', '').replace('\\\r\n', '')

    # 2. 去除外部双引号
    if content.startswith('"') and content.endswith('"'):
        content = content[1:-1]

    # 3. 尝试 ast.literal_eval
    try:
        return ast.literal_eval(content)
    except Exception as e:
        print(f"[ast.literal_eval] 失败: {e}")

    # 4. 尝试 eval
    try:
        return eval(content)
    except Exception as e:
        print(f"[eval] 失败: {e}")

    # 5. 尝试 JSON 解析（先将单引号转双引号，并处理布尔值）
    try:
        json_content = content.replace("'", '"')
        json_content = re.sub(r'\bTrue\b', 'true', json_content)
        json_content = re.sub(r'\bFalse\b', 'false', json_content)
        json_content = re.sub(r'\bNone\b', 'null', json_content)
        return json.loads(json_content)
    except Exception as e:
        print(f"[json.loads] 失败: {e}")

    # 6. 最后的兜底：用正则提取
    print("使用正则表达式提取数据...")
    samples = extract_samples_by_regex(content)
    if samples:
        print(f"正则提取到 {len(samples)} 条样本")
        return samples
    else:
        raise ValueError("所有解析方式均失败，且正则提取未找到任何样本。")


def convert_sample(sample):
    text = sample.get("text", "")
    spo_list = sample.get("spo_list", [])

    system_prompt = "你是一个信息抽取专家，请从句子中抽取实体关系三元组（头实体, 关系, 尾实体）。"
    user_content = f"请抽取以下句子中的三元组：\n句子：{text}"

    if spo_list:
        triple_lines = []
        for triple in spo_list:
            if len(triple) == 3:
                triple_lines.append(f"({triple[0]}, {triple[1]}, {triple[2]})")
            else:
                triple_lines.append(str(triple))
        assistant_content = "\n".join(triple_lines)
    else:
        assistant_content = "无三元组"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": assistant_content}
    ]
    return {"messages": messages}


def convert_file(input_path, output_path, overwrite=False):
    with open(input_path, 'r', encoding='utf-8-sig') as f:
        content = f.read().strip()

    raw_data = safe_parse(content)
    if not isinstance(raw_data, list):
        raise ValueError("解析后的数据必须是一个列表（数组）")

    converted_data = [convert_sample(sample) for sample in raw_data]

    with open(output_path, 'w', encoding='utf-8') as f:
        for item in converted_data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

    print(f"✅ 转换完成，共 {len(converted_data)} 条数据，已写入 {output_path}")


if __name__ == "__main__":
    INPUT_FILE = "./data/train_triple.json"
    OUTPUT_FILE = "./data/train_triple_converted.jsonl"
    OVERWRITE = False

    input_path = Path(INPUT_FILE)
    output_path = Path(OUTPUT_FILE)

    if not input_path.exists():
        print(f"❌ 错误：输入文件 {input_path} 不存在")
        exit(1)

    if output_path.resolve() == input_path.resolve() and not OVERWRITE:
        print(f"❌ 错误：输出文件与输入文件相同，请将 OVERWRITE 设为 True")
        exit(1)

    try:
        convert_file(input_path, output_path, overwrite=OVERWRITE)
    except Exception as e:
        print(f"❌ 转换失败：{e}")
        exit(1)