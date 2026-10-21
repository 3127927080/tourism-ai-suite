# atguigu/import_process/import_main_graph.py

"""
graph的调用主接口
"""

from langgraph.constants import START, END
from langgraph.graph import StateGraph

from atguigu.import_process.nodes.node_bge_embedding import NodeBGEEmbedding
from atguigu.import_process.nodes.node_document_split import NodeDocumentSplit
from atguigu.import_process.nodes.node_entry import NodeEntry
from atguigu.import_process.nodes.node_import_milvus import NodeImportMilvus
from atguigu.import_process.nodes.node_item_name_recognition import NodeItemNameRecognition
from atguigu.import_process.nodes.node_md_img import NodeMDImg
from atguigu.import_process.nodes.node_pdf_to_md import NodePDFToMD
from atguigu.import_process.nodes.node_graph_extract import NodeGraphExtract
from atguigu.import_process.nodes.node_graph_import import NodeGraphImport
from atguigu.import_process.state import ImportGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger
class MainGraphRunner:
    def __init__(self):
        self.builder=StateGraph(state_schema=ImportGraphState)
        self.add_nodes()
        self.add_edges()
        self.graph=None


    def after_entry_router(self,state: ImportGraphState):
        is_md_read_enabled = state.get("is_md_read_enabled", False)
        is_pdf_read_enabled = state.get("is_pdf_read_enabled", False)
        if is_md_read_enabled:
            return NodeMDImg.name
        elif is_pdf_read_enabled:
            return NodePDFToMD.name
        else:
            raise END

    def add_nodes(self):
        self.builder.add_node(NodeEntry.name,NodeEntry())
        self.builder.add_node(NodePDFToMD.name,NodePDFToMD())
        self.builder.add_node(NodeMDImg.name,NodeMDImg())
        self.builder.add_node(NodeDocumentSplit.name,NodeDocumentSplit())
        self.builder.add_node(NodeBGEEmbedding.name,NodeBGEEmbedding())
        self.builder.add_node(NodeItemNameRecognition.name,NodeItemNameRecognition())
        self.builder.add_node(NodeImportMilvus.name,NodeImportMilvus())
        #新增的两个节点
        self.builder.add_node(NodeGraphExtract.name,NodeGraphExtract())
        self.builder.add_node(NodeGraphImport.name,NodeGraphImport())

    def add_edges(self):
        self.builder.add_edge(START,NodeEntry.name)
        self.builder.add_conditional_edges(NodeEntry.name,self.after_entry_router)
        self.builder.add_edge(NodePDFToMD.name,NodeMDImg.name)
        self.builder.add_edge(NodeMDImg.name,NodeDocumentSplit.name)
        self.builder.add_edge(NodeDocumentSplit.name,NodeItemNameRecognition.name)
        self.builder.add_edge(NodeItemNameRecognition.name,NodeBGEEmbedding.name)
        self.builder.add_edge(NodeBGEEmbedding.name,NodeImportMilvus.name)
        self.builder.add_edge(NodeImportMilvus.name,END)
        #新加的两个节点的三条边
        self.builder.add_edge(NodeItemNameRecognition.name, NodeGraphExtract.name)
        self.builder.add_edge( NodeGraphExtract.name, NodeGraphImport.name)
        self.builder.add_edge(NodeGraphImport.name, END)

    def run(self, state: ImportGraphState):
        if not self.graph:
            self.graph = self.builder.compile()
        return self.graph.invoke(state)

    @classmethod
    def create_and_run(cls, state):
        return cls().run(state)

if __name__ == "__main__":
    state = {
        "local_file_path":r"D:\output\doc\hak180产品安全手册.pdf",
        "local_dir":r"D:\output\doc\parse"
    }
    res = MainGraphRunner.create_and_run(state)
    logger.info(json_format(res))