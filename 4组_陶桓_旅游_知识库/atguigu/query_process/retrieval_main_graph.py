from langgraph.constants import END, START
from langgraph.graph import StateGraph

from atguigu.query_process.nodes.node_rerank import NodeRerank
from atguigu.query_process.nodes.node_rrf import NodeRrf
from atguigu.query_process.nodes.node_search_embedding import NodeSearchEmbedding
from atguigu.query_process.nodes.node_search_embedding_hyde import NodeSearchEmbeddingHyde
from atguigu.query_process.nodes.node_search_graph import NodeSearchGraph
from atguigu.query_process.state import QueryGraphState


class RetrievalMainGraphRunner:
    """商品名确认之后的多路召回、融合和重排。"""

    def __init__(self):
        self.builder = StateGraph(state_schema=QueryGraphState)
        self.add_nodes()
        self.add_edges()
        self.graph = None

    def add_nodes(self):
        self.builder.add_node(NodeSearchEmbedding.name, NodeSearchEmbedding())
        self.builder.add_node(NodeSearchEmbeddingHyde.name, NodeSearchEmbeddingHyde())
        self.builder.add_node(NodeSearchGraph.name, NodeSearchGraph())
        self.builder.add_node(NodeRrf.name, NodeRrf())
        self.builder.add_node(NodeRerank.name, NodeRerank())

    def add_edges(self):
        search_nodes = [
            NodeSearchEmbedding.name,
            NodeSearchEmbeddingHyde.name,
            NodeSearchGraph.name,
        ]
        self.builder.add_conditional_edges(START, self.fanout_search, search_nodes)
        self.builder.add_edge(NodeSearchEmbedding.name, NodeRrf.name)
        self.builder.add_edge(NodeSearchEmbeddingHyde.name, NodeRrf.name)
        self.builder.add_edge(NodeSearchGraph.name, NodeRrf.name)
        self.builder.add_conditional_edges(
            NodeRrf.name,
            self.route_after_rrf,
            [NodeRerank.name, END],
        )
        self.builder.add_edge(NodeRerank.name, END)

    @staticmethod
    def fanout_search(state: QueryGraphState):
        return [
            NodeSearchEmbedding.name,
            NodeSearchEmbeddingHyde.name,
            NodeSearchGraph.name,
        ]

    @staticmethod
    def route_after_rrf(state: QueryGraphState):
        # 三路都没有候选时跳过重排，避免空文档调用 Rerank 接口
        if not state.get("rrf_chunks") and not state.get("graph_results"):
            return END
        return NodeRerank.name

    def run(self, state):
        if not self.graph:
            self.graph = self.builder.compile()
        return self.graph.invoke(state)

    @classmethod
    def create_and_run(cls, state):
        return cls().run(state)
