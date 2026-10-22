# atguigu/query_process/nodes/node_web_search_mcp.py
import json

from agents.mcp import MCPServerStreamableHttp

from atguigu.config.config import McpConfig
from atguigu.query_process.base import NodeBase
from atguigu.query_process.state import QueryGraphState
from atguigu.tool.json_format_tool import json_format
from atguigu.tool.logger import logger

class NodeWebSearchMcp(NodeBase):
    """
    节点功能，调用外部搜索引擎补充信息
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_web_search_mcp"

    def process(self, state: QueryGraphState):
        """
        节点功能，调用外部搜索引擎补充信息
        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """
        logger.info(f"【{self.name}】节点逻辑")
        # 第一步,获取上步的改写问题
        rewritten_query = state.get("rewritten_query", "")
        if not rewritten_query:
            logger.error("请输入要检索的问题")
            raise Exception("请输入要检索的问题")

        #第二步,调用云百炼的mcp网络查询服务
        import asyncio
        res = asyncio.run(self.mcp_run(rewritten_query))
        datas = json.loads(res.content[0].text).get("pages")
        # logger.info(json_format(datas))

        return {
            "web_search_docs": [
                {
                    "content": item.get("snippet", ""),
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "source": "web"
                }
                for item in datas
            ]
        }

    async def mcp_run(self, query) -> None:
        token = McpConfig.api_key
        url = McpConfig.mcp_base_url
        async with MCPServerStreamableHttp(
                name="Streamable HTTP Python Server",
                params={
                    "url": url,
                    "headers": {"Authorization": f"Bearer {token}"},
                    "timeout": 10,
                },
                cache_tools_list=True,
                max_retry_attempts=3,
                client_session_timeout_seconds=30
        ) as server:
            result = await server.call_tool("bailian_web_search", arguments={"query": query, "count": 10})
            return result



if __name__ == "__main__":

    init_state = {
        "rewritten_query": "三亚亚龙湾适合什么人去"
    }

    # 执行节点的业务调用
    node_web_search_mcp = NodeWebSearchMcp()
    result = node_web_search_mcp(init_state)
    logger.info(json_format(result))