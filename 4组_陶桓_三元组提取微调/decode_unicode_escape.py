import json
import sys
import os
import ast

def parse_content(content):
    """
    尝试多种方式解析内容，兼容标准 JSON 和 Python 字面量（单引号格式）
    """
    # 1. 首先尝试标准 JSON 解析（双引号格式）
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # 2. 如果失败，尝试 Python 字面量解析（兼容单引号、None、True/False 等）
    try:
        return ast.literal_eval(content)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"文件内容解析失败，请确认格式为 JSON 或 Python 字面量。\n错误详情：{e}")

def convert_unicode_escape_to_chinese(input_file, output_file=None):
    if output_file is None:
        output_file = input_file

    # 检查输入文件是否存在
    if not os.path.isfile(input_file):
        print(f"❌ 错误：文件不存在 -> {input_file}")
        return

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 智能解析内容
        data = parse_content(content)

        # 写回文件，ensure_ascii=False 让中文正常显示
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"✅ 转换成功！文件已保存：{output_file}")

    except Exception as e:
        print(f"❌ 转换失败：{e}")


if __name__ == "__main__":
    # ========== 在这里直接定义你要转换的文件名或完整路径 ==========
    # 如果你的 json 和脚本在同一个文件夹，直接写文件名即可
    input_file_path = "./data/train_triple.json"

    # 如果文件在桌面，也可以直接把绝对路径粘在这里
    # input_file_path = r"C:\Users\th\Desktop\数据集\dev_triple.json"
    # =============================================================

    # 覆盖原文件前确认一下
    confirm = input(f"⚠️ 即将转换文件：{input_file_path}\n确认继续？(y/N): ")
    if confirm.lower() == 'y':
        convert_unicode_escape_to_chinese(input_file_path)
    else:
        print("已取消操作。")