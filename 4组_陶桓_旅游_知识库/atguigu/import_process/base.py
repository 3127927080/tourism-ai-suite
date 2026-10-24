# atguigu/import_process/base.py

"""
查询流程节点基类
定义统一的节点接口规范，提供通用功能
"""
import time
from abc import ABC,abstractmethod
from atguigu.tool.logger import logger
from atguigu.tool.task_utils import add_running_task, add_done_task, add_node_duration


class NodeBase(ABC):
    name="node_base"

    def __init__(self):
        """
        强制子类设置 name
        """
        if self.name == "node_base":
            raise Exception(f"子类节点{self.__class__.__name__} 必须重写 name 类属性")

    @abstractmethod
    def process(self,state):
        pass

    def __call__(self, state):
        try:
            logger.info(f"--- {self.name} 节点开始执行了 ---")
            # 修改每个节点执行中的状态
            task_id = state.get("task_id")
            add_running_task(task_id, self.name)
            start_time = time.time()
            # 这个call是后期所有的子类对象在当函数使用的时候，都会自动调用这个方法
            result = self.process(state)
            logger.info(f"{self.name}执行结束了")

            # 修改每个节点执行完成的状态
            add_done_task(task_id, self.name)
            end_time = time.time()

            # 计算每个节点执行所用的时间
            add_node_duration(task_id, self.name, end_time - start_time)
            return result
        except Exception as e:
            logger.error(f"--- {self.name} 节点执行异常 ---")
            logger.error(e)
            # 不吞异常：交给上层把任务标记为 failed，
            # 否则前端会一直看到任务停在 processing。
            raise