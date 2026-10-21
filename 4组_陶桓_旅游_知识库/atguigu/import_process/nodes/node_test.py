# atguigu/import_process/nodes/node_test.py
from atguigu.import_process.base import NodeBase
from atguigu.tool.logger import logger


class NodeTest(NodeBase):
    name="node_test"

    def process(self,state):
        logger.info(f"{self.name}节点正在执行中")
        return state


if __name__ == "__main__":
    node = NodeTest()
    state = {}
    res=node(state)
    logger.info(res)
