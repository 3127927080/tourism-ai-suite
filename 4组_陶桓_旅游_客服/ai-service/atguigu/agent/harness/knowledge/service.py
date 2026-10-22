import logging

from atguigu.agent.harness.knowledge.output import KnowledgeItem, KnowledgeSearchResult
from atguigu.common.config import get_settings
from atguigu.infrastructure.knowledge_client import KnowledgeClient

logger = logging.getLogger(__name__)


class KnowledgeQueryService:
    """提供 Agent 使用的知识查询能力。"""

    def __init__(self):
        self.knowledge_client = KnowledgeClient()

    async def search(self, query: str) -> KnowledgeSearchResult:
        """返回平台规则，并追加旅游知识库检索结果。"""
        # 1. 空查询不返回知识内容
        if not query.strip():
            return KnowledgeSearchResult(items=[])

        # 2. 平台规则保持原有固定内容
        items = self._platform_rules()
        message = ""

        # 3. 旅游知识查询失败时保留平台规则，并把失败提示交给调用方转述
        try:
            payload = await self.knowledge_client.search(
                query.strip(),
                get_settings().knowledge_top_k
            )
        except Exception:
            logger.exception("旅游知识查询失败")
            message = "暂时无法查询旅游知识"
        else:
            message = str(payload.get("message") or "").strip()
            items.extend(self._to_product_items(payload.get("items")))

        return KnowledgeSearchResult(items=items, message=message)

    @staticmethod
    def _platform_rules() -> list[KnowledgeItem]:
        """返回旅游平台规则。"""
        return [
            KnowledgeItem(
                knowledge_id="KNOWLEDGE_POLICY_001",
                title="退改政策",
                content="不同旅游产品的退改规则不同，以订单页展示的退改政策为准；特价或促销产品可能不支持退改。",
                source="平台退改规则"
            ),
            KnowledgeItem(
                knowledge_id="KNOWLEDGE_POLICY_002",
                title="签证材料",
                content="出境行程的签证材料需按目的地使领馆的要求准备，平台仅提供签证代办服务说明，不保证签证结果。",
                source="平台签证规则"
            ),
            KnowledgeItem(
                knowledge_id="KNOWLEDGE_POLICY_003",
                title="出票时效",
                content="机票/火车票的出票时间受航空公司或铁路系统影响，通常在订单支付后 2 小时内完成，请以订单状态为准。",
                source="平台出票规则"
            )
        ]

    @staticmethod
    def _to_product_items(raw_items: object) -> list[KnowledgeItem]:
        """跳过缺正文或编号的条目，避免一条坏数据导致整次查询失败。"""
        if not isinstance(raw_items, list):
            return []

        items: list[KnowledgeItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            knowledge_id = str(raw.get("knowledge_id") or "").strip()
            title = str(raw.get("title") or "").strip() or "无标题"
            # 单条正文截断到 1000 字，避免上下文膨胀
            content = str(raw.get("content") or "").strip()[:1000]
            source = str(raw.get("source") or "").strip() or "知识库文档"
            if not knowledge_id or not content:
                continue
            items.append(
                KnowledgeItem(
                    knowledge_id=knowledge_id,
                    title=title,
                    content=content,
                    source=source
                )
            )
        return items
