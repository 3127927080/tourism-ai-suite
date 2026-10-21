# atguigu/import_process/nodes/node_document_split.py
import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeDocumentSplit(NodeBase):
    """
    文档切分节点：智能文档切片
    """

    name = "node_document_split"

    def is_code_block_marker(self,line: str) -> bool:
        """
        判断是否是独立的代码块标记行
        有效格式：
          - ~~~
          - ~~~python
          - ```
          - ```python
        无效格式：
          - foo~~~bar  (有额外字符)
          - ~~~ python  (空格在中间)
        """
        line = line.strip()
        pattern = r'^(~~~|```)(\s*[a-zA-Z0-9_\-]*)?$'
        match = re.match(pattern, line)
        return match is not None

    def get_md_content(self, state: ImportGraphState) :
        # md_coutent = state.get("md_coutent", "")
        file_title = state.get("file_title", "")
        # 现在是读取文件获取
        md_path = state.get("md_path", "")
        if not md_path:
            logger.error("md_path必须提供")
            raise ValueError("md_path必须提供")

        md_path_obj = Path(md_path)
        if not md_path_obj.exists():
            logger.error("文件不存在")
            raise ValueError("文件不存在")

        with open(md_path_obj, "r", encoding="utf-8") as f:
            md_content = f.read()

        if not md_content:
            logger.error("文件内容为空")
            raise ValueError("文件内容为空")
        # 为了防止不同操作系统的换行符不同,直接统一换行符
        md_content = md_content.replace("\r\n", "\n").replace("\r", "\n")
        return file_title, md_content,md_path_obj

    def get_section_list(self, file_title: str, md_content: str):
        # 2.1第一步先按行去切分
        md_line_list = md_content.split("\n")
        # 定义代码块的正则
        block_pattern = r"^(```{3})|(~~~{3})"
        # 定义代码块的标识
        marker = None
        # 定义一个标识符,用以确定代码块是开始,还是结束
        is_block = False
        # 定义标题的正则
        title_pattern = r'^\s*#{1,6}\s+.+'
        # 定义一个数字,用来表示标题的位置
        current_idx = 0
        # 定义一个列表,用来保存切分后的块
        section_list = []
        # 循环列表,判断该行是否是标题
        for idx, line in enumerate(md_line_list):
            # 4.1：拿到每个line，需要去除两边空白
            line = line.strip()
            # 4.2：判断这个line是不是在代码块当中（代码围栏的判断）
            match = re.match(block_pattern, line)
            # ✅ 使用更精确的判断
            if self.is_code_block_marker(line):
                marker_symbol = line[:3]  # "~~~" 或 "```"

                if not is_block:
                    is_block = True
                    marker = marker_symbol
                    logger.info(f"开始代码块：{marker}")
                else:
                    if marker_symbol == marker:
                        is_block = False
                        marker = None
                        logger.info(f"结束代码块")
                continue  # ← 关键：标记行不参与后续处理
            # 出了if判断,说明不是代码块的开始或者结束行,根据is_block表示判断是否在代码块内
            if not is_block and re.match(title_pattern, line):
                # 既满足不在代码块内,又符合标题的正则,则是标题行,准备切分,切上一个标题到这个标题的行
                temp_list = md_line_list[current_idx:idx]
                content = "\n".join(temp_list)
                # 准备字典,对于标题可能是没有的，所以这里要判断
                section_dict = {
                    "title": temp_list[0] if content.startswith("#") else "无标题",
                    "content": content,
                    "file_title": file_title
                }
                section_list.append(section_dict)
                # 切分完成,将当前行索引更新为idx
                current_idx = idx

        # 走出大循环之后,因为最后一个段落不会切分，所以需要单独处理
        section_dict = {
            "title": md_line_list[current_idx],
            "content": '\n'.join(md_line_list[current_idx:]),
            "file_title": file_title
        }
        section_list.append(section_dict)
        return section_list

    def get_final_section_list(self, file_title, section_list) :
        # 定义一个最终的列表去存切分后的块
        max_length = 500
        over_lap = 50
        final_section_list = []
        # 定义一个切词器
        splitter = RecursiveCharacterTextSplitter(
            separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "],
            chunk_size=max_length,
            chunk_overlap=over_lap,
        )
        for section_dict in section_list:
            # 获取上一步的标题和内容
            title = section_dict.get("title")
            content = section_dict.get("content")
            # 对内容进行切分,去掉标题,因为后面每一个块我们都要加上标题,保证每个块的内容完整性
            real_content = content[len(title):] if content.startswith('#') else content
            # 判断块的大小,对于小于500的块,直接返回
            if len(real_content) < max_length:
                final_section_list.append({
                    **section_dict,
                    "part": 0  # 0表示没有被切分,如果被切分了,就从1开始
                })
                continue
            # 判断块里面有没有表格,有表格也是不切分
            if "<table" in real_content:
                final_section_list.append({
                    **section_dict,
                    "part": 0
                })
                continue
            # 对于大于500的块,进行长切短合
            splite_chunk_list = splitter.split_text(real_content)
            # 将上面的列表进行迭代,将每个块放进最后的字典里面,加上part字段
            for idx, chunk in enumerate(splite_chunk_list, start=1):
                final_section_list.append({
                    "title": title,
                    "content": title + "\n\n" + chunk,
                    "file_title": file_title,
                    "part": idx
                })
        return final_section_list

    def process(self, state: ImportGraphState):
        #第一步,获取文件名字和内容,后面节点串联起来后可以直接state里面get
        file_title, md_content,md_path_obj = self.get_md_content(state)

        #第二步,初切分,按标题切分为大块
        section_list = self.get_section_list(file_title, md_content)

        #第三步,对每个块进行长切短合
        final_section_list = self.get_final_section_list(file_title, section_list)

        #因为下一个节点需要,所以这里还是找个文件保存处理好的列表
        # 备份chunks列表到json文件
        with open(md_path_obj.parent / "chunks.json", 'w', encoding='utf-8') as f:
            f.write(json_format(final_section_list))

        return {
            "chunks": final_section_list,
        }




if __name__ == "__main__":
    node = NodeDocumentSplit()
    state = {
        "md_path":r"D:\output\doc\parse\hak180产品安全手册\hak180产品安全手册_new.md",
        "file_title":"hak180产品安全手册",
    }
    res = node(state)
    logger.info(json_format(res))