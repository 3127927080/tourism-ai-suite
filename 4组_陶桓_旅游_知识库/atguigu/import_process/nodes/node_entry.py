# atguigu/import_process/nodes/node_entry.py
from pathlib import Path

from atguigu.import_process.base import NodeBase
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger


class NodeEntry(NodeBase):
    """
    入口节点：任务分发
    """

    name = "node_entry"

    def process(self, state: ImportGraphState):
        local_file_path=state.get("local_file_path","")
        #判断文件是否存在,就是地址有没有给
        if not local_file_path:
            logger.error("文件路径为空")
            raise ValueError("local_file_path路径必须提供")

        local_file_path_obj=Path(local_file_path)
        #判断文件地址是否正确,给了一个地址,但是地址是不是存在,有没有文件
        if not local_file_path_obj.exists():
            logger.error(f"文件{local_file_path}不存在")
            raise ValueError(f"文件{local_file_path}不存在")

        #文件存在,获取文件的名和后缀,如果要获取完整的文件名用name方法
        file_title = local_file_path_obj.stem
        suffix = local_file_path_obj.suffix

        #根据后缀取判断文件类型
        if suffix.lower() == ".pdf":
            return {
                "file_title": file_title,
                "pdf_path": str(local_file_path_obj),
                "is_pdf_read_enabled": True
            }
        elif suffix.lower() == ".md":
            return {
                "file_title": file_title,
                "md_path": local_file_path,
                "is_md_read_enabled": True
            }
        else:
            logger.error(f"不支持的文件类型{suffix}")
            raise ValueError(f"不支持的文件类型{suffix}")

if __name__ == "__main__":
    node = NodeEntry()
    state = {
        "local_file_path":r"D:\output\doc\hak180产品安全手册.pdf"
    }
    res = node(state)
    logger.info(json_format( res))