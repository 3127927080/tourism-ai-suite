# atguigu/query_process/base.py

"""
查询流程节点基类

定义统一的节点接口规范，提供通用功能
"""
from abc import abstractmethod, ABC

from atguigu.query_process.state import QueryGraphState
from atguigu.tool.logger import logger
from atguigu.tool.task_utils import add_running_task, get_task_info, put_data, add_done_task


class NodeBase(ABC):
    name: str = "node_base"

    def __init__(self):
        if self.name == "node_base":
            raise ValueError(f"{self.__class__.__name__} 必须设置 name 属性")

    def __call__(self, state: QueryGraphState):
        """
        节点执行入口
        """
        try:
            logger.info(f"{self.name} 开始执行...")
            task_id = state.get("task_id")
            add_running_task(task_id, self.name)
            put_data(task_id, "progress", get_task_info(task_id))
            # q.put({"event": "progress", "data": get_task_info(task_id)})

            result = self.process(state)

            logger.info(f"{self.name} 结束执行...")
            add_done_task(task_id, self.name)
            put_data(task_id, "progress", get_task_info(task_id))
            # q.put({"event": "progress", "data": get_task_info(task_id)})
            return result
        except Exception as e:
            logger.error(f"{self.name} 执行失败: {e}")
            raise

    @abstractmethod
    def process(self, state: QueryGraphState):
        """
        节点的核心处理逻辑
        :return:
        """
        pass