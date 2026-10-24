from typing import Annotated
from urllib.parse import quote

from langchain.tools import ToolRuntime, tool

from atguigu.agent.harness.knowledge.output import KnowledgeItem
from atguigu.agent.harness.knowledge.service import KnowledgeQueryService
from atguigu.agent.harness.run.runtime import AgentRuntimeContext
from atguigu.agent.harness.tools.executor import ToolExecutor
from atguigu.agent.harness.tools.output import (
    AvailabilityData,
    OrderData,
    RefundData,
    ToolResult,
    TravelProductData,
    TripInfoData,
)
from atguigu.infrastructure.client import TourismDataClient


def _normalize_resource_id(resource_id: str) -> str:
    """统一订单和产品编号的格式。"""
    # 1. 清理空格、统一大小写并规范连接符
    return resource_id.strip().upper().replace("-", "_")


@tool
async def search_travel_products(
        query: Annotated[str, "目的地城市、产品类型、酒店/景区/票务/接送需求或用户描述，例如 三亚五星酒店"],
        runtime: ToolRuntime[AgentRuntimeContext],
        product_type: Annotated[
            str | None,
            "可选的产品类型过滤：hotel（酒店）、scenic（景区门票）、ticket（机票/火车票/汽车票）、transfer（接送服务）"
        ] = None
) -> str:
    """
    根据用户需求搜索当前在售的旅游产品。
    用户询问某类旅游产品、酒店、景区、票务或接送服务，但没有明确产品编号时调用。
    返回匹配产品的名称、类型、城市、价格、库存、状态、描述和属性。
    """
    # 1. 确认 LangChain 为本次调用生成的工具调用ID
    if runtime.tool_call_id is None:
        raise RuntimeError("旅游产品搜索工具缺少调用编号")

    # 2. 整理搜索参数并创建当前用户的旅游数据服务客户端
    query_params: dict[str, str] = {"query": query}
    if product_type:
        query_params["type"] = product_type.strip().lower()
    tourism_client = TourismDataClient(runtime.context.access_token)

    # 3. 定义本次工具需要执行的旅游产品搜索请求
    async def request_products() -> object:
        return await tourism_client.get_json(
            "/products",
            query_params=query_params
        )

    # 4. 交给工具执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="search_travel_products",
        arguments={"query": query, "product_type": product_type},
        operation=request_products,
        output_schema=list[TravelProductData]
    )


@tool
async def get_travel_product(
        product_id: Annotated[str, "用户明确提供或搜索结果返回的旅游产品编号，例如 HOTEL_SANYA_01"],
        runtime: ToolRuntime[AgentRuntimeContext]
) -> str:
    """
    查询一个旅游产品的名称、类型、城市、价格、描述和属性。
    用户已经明确产品编号，并询问该产品的基础信息时调用。
    实时余量/房态必须使用 get_product_availability 查询。
    """
    # 1. 确认 LangChain 为本次调用生成的工具调用ID
    if runtime.tool_call_id is None:
        raise RuntimeError("旅游产品详情工具缺少调用编号")

    # 2. 规范产品编号并创建当前用户的旅游数据服务客户端
    normalized_product_id = _normalize_resource_id(product_id)
    tourism_client = TourismDataClient(runtime.context.access_token)

    # 3. 定义本次工具需要执行的产品详情请求
    async def request_product() -> object:
        return await tourism_client.get_json(
            f"/products/{quote(normalized_product_id, safe='')}"
        )

    # 4. 交给工具执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="get_travel_product",
        arguments={"product_id": normalized_product_id},
        operation=request_product,
        output_schema=TravelProductData
    )


@tool
async def get_product_availability(
        product_id: Annotated[str, "用户明确提供或搜索结果返回的旅游产品编号，例如 HOTEL_SANYA_01"],
        runtime: ToolRuntime[AgentRuntimeContext]
) -> str:
    """
    查询一个旅游产品的实时余量（房态/余票）和可售状态。
    用户询问具体产品是否还有房/票或剩余数量时调用，不用于查询其他产品详情。
    """
    # 1. 确认 LangChain 为本次调用生成的工具调用ID
    if runtime.tool_call_id is None:
        raise RuntimeError("余量查询工具缺少调用编号")

    # 2. 规范产品编号并创建当前用户的旅游数据服务客户端
    normalized_product_id = _normalize_resource_id(product_id)
    tourism_client = TourismDataClient(runtime.context.access_token)

    # 3. 定义本次工具需要执行的余量查询请求
    async def request_product_availability() -> object:
        return await tourism_client.get_json(
            f"/products/{quote(normalized_product_id, safe='')}/availability"
        )

    # 4. 交给工具执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="get_product_availability",
        arguments={"product_id": normalized_product_id},
        operation=request_product_availability,
        output_schema=AvailabilityData
    )


@tool
async def list_orders(
        runtime: ToolRuntime[AgentRuntimeContext]
) -> str:
    """
    查询当前用户的订单列表。
    用户询问自己的订单但没有明确订单编号时调用。
    返回订单编号、状态、金额、出行日期和产品信息，只读取数据，不执行订单操作。
    """
    # 1. 确认 LangChain 为本次调用生成的工具调用ID
    if runtime.tool_call_id is None:
        raise RuntimeError("订单列表工具缺少调用编号")

    # 2. 创建当前用户的旅游数据服务客户端
    tourism_client = TourismDataClient(runtime.context.access_token)

    # 3. 定义本次工具需要执行的订单列表请求
    async def request_orders() -> object:
        return await tourism_client.get_json("/orders")

    # 4. 工具执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="list_orders",
        arguments={},
        operation=request_orders,
        output_schema=list[OrderData]
    )


@tool
async def get_order(
        order_id: Annotated[str, "用户明确提供或订单列表返回的订单编号，例如 ORD_SANYA_001"],
        runtime: ToolRuntime[AgentRuntimeContext]
) -> str:
    """
    查询当前用户的一条订单详情。
    用户已经明确订单编号，并询问该订单的状态、金额、出行日期或产品信息时调用。
    只读取数据，不取消预订、退款或修改出行人。
    """
    # 1. 确认 LangChain 为本次调用生成的工具调用ID
    if runtime.tool_call_id is None:
        raise RuntimeError("订单详情工具缺少调用编号")

    # 2. 规范订单编号并创建当前用户的旅游数据服务客户端
    normalized_order_id = _normalize_resource_id(order_id)
    tourism_client = TourismDataClient(runtime.context.access_token)

    # 3. 定义本次工具需要执行的订单详情请求
    async def request_order() -> object:
        return await tourism_client.get_json(
            f"/orders/{quote(normalized_order_id, safe='')}"
        )

    # 4. 执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="get_order",
        arguments={"order_id": normalized_order_id},
        operation=request_order,
        output_schema=OrderData
    )


@tool
async def get_trip_info(
        order_id: Annotated[str, "用户明确提供或订单列表返回的订单编号，例如 ORD_SANYA_001"],
        runtime: ToolRuntime[AgentRuntimeContext]
) -> str:
    """
    查询一个订单的出行信息：出行日期、入住/退房、出发/到达时间、班次、旅客信息和集合点。
    用户已经明确订单编号，并询问该订单的具体出行安排时调用。
    订单基础信息应使用 get_order 查询。
    """
    # 1. 确认 LangChain 为本次调用生成的工具调用ID
    if runtime.tool_call_id is None:
        raise RuntimeError("出行信息查询工具缺少调用编号")

    # 2. 规范订单编号并创建当前用户的旅游数据服务客户端
    normalized_order_id = _normalize_resource_id(order_id)
    tourism_client = TourismDataClient(runtime.context.access_token)

    # 3. 定义本次工具需要执行的出行信息请求
    async def request_trip_info() -> object:
        return await tourism_client.get_json(
            f"/orders/{quote(normalized_order_id, safe='')}/trip"
        )

    # 4. 工具执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="get_trip_info",
        arguments={"order_id": normalized_order_id},
        operation=request_trip_info,
        output_schema=TripInfoData
    )


@tool
async def list_refunds(
        runtime: ToolRuntime[AgentRuntimeContext],
        order_id: Annotated[str | None, "可选的订单编号；提供后只查询该订单的退款记录"] = None
) -> str:
    """
    查询当前用户的退款申请记录。
    用户询问退款进度或退款记录时调用。
    已经明确订单编号时按订单查询，否则返回当前客户的全部退款记录。
    """
    # 1. 确认 LangChain 为本次调用生成的工具调用ID
    if runtime.tool_call_id is None:
        raise RuntimeError("退款查询工具缺少调用编号")

    # 2. 规范可选订单编号并构建查询参数
    normalized_order_id = (
        _normalize_resource_id(order_id)
        if order_id is not None
        else None
    )
    query_params = (
        {"order_id": normalized_order_id}
        if normalized_order_id is not None
        else None
    )
    # 3. 创建当前用户的旅游数据服务客户端
    tourism_client = TourismDataClient(runtime.context.access_token)

    # 4. 定义本次工具需要执行的退款记录请求
    async def request_refunds() -> object:
        return await tourism_client.get_json(
            "/refunds",
            query_params=query_params
        )

    # 5. 工具执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="list_refunds",
        arguments=query_params or {},
        operation=request_refunds,
        output_schema=list[RefundData]
    )


@tool
async def search_knowledge(
        query: Annotated[
            str,
            "平台规则、服务政策、帮助说明，或退改政策、签证材料、出票时效等旅游知识。"
            "查询旅游知识时必须写上已确认的目的地或产品名"
        ],
        runtime: ToolRuntime[AgentRuntimeContext]
) -> str:
    """查询平台规则和旅游知识。

    用户询问退改政策、签证材料、出票时效、目的地攻略或平台帮助说明时调用。
    查询目的地或产品相关旅游知识时也调用，query 必须包含已确认的城市或产品名。
    该工具不用于查询订单、产品编号、余量/房态、出行信息等实时业务数据。
    返回的 message 是可以转述给用户的提示，不是知识条目，也不会出现在结果列表中。
    城市或产品名无法确认时，message 是澄清话术，应转述给用户。
    message 为“暂时无法查询旅游知识”时，只说明知识暂不可用，不得编造内容。
    """
    # 1. 确认 LangChain 已经为本次调用分配调用编号
    if runtime.tool_call_id is None:
        raise RuntimeError("知识查询工具缺少调用编号")

    # 2. 清理模型生成的查询内容
    normalized_query = query.strip()

    # 3. 定义本次工具需要执行的知识查询
    async def request_knowledge() -> object:
        knowledge_result = await knowledge_query_service.search(
            normalized_query
        )
        if knowledge_result.message:
            result_message = knowledge_result.message
        elif knowledge_result.items:
            result_message = "已找到相关知识内容"
        else:
            result_message = "未找到相关知识内容"
        return ToolResult[list[KnowledgeItem]](
            success=True,
            code="KNOWLEDGE_SEARCH_COMPLETED",
            message=result_message,
            data=knowledge_result.items
        ).model_dump(mode="python")

    # 4. 交给统一执行器记录、执行并校验结果
    return await tool_executor.execute(
        runtime_context=runtime.context,
        tool_call_id=runtime.tool_call_id,
        tool_name="search_knowledge",
        arguments={"query": normalized_query},
        operation=request_knowledge,
        output_schema=list[KnowledgeItem]
    )


tool_executor = ToolExecutor()
knowledge_query_service = KnowledgeQueryService()
