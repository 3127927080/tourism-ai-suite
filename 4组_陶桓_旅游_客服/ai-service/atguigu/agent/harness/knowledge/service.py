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
        """返回平台规则，并追加商品知识库检索结果。"""
        # 1. 空查询不返回知识内容
        if not query.strip():
            return KnowledgeSearchResult(items=[])

        # 2. 平台规则保持原有固定内容
        items = self._platform_rules()
        message = ""

        # 3. 商品知识查询失败时保留平台规则，并把失败提示交给调用方转述
        try:
            payload = await self.knowledge_client.search(
                query.strip(),
                get_settings().knowledge_top_k
            )
        except Exception:
            logger.exception("商品知识查询失败")
            message = "暂时无法查询商品说明"
        else:
            message = str(payload.get("message") or "").strip()
            items.extend(self._to_product_items(payload.get("items")))

        return KnowledgeSearchResult(items=items, message=message)

    @staticmethod
    def _platform_rules() -> list[KnowledgeItem]:
        """返回原有平台规则。"""
        return [
            KnowledgeItem(
                knowledge_id="KNOWLEDGE_AFTER_SALE_001",
                title="七天无理由退货规则",
                content="符合平台七天无理由退货范围的商品，可以在签收后七天内申请退货。商品应保持完好",
                source="平台退货规则"
            ),
            KnowledgeItem(
                knowledge_id="KNOWLEDGE_AFTER_SALE_002",
                title="退款到账规则",
                content="退款申请审核通过后，款项将原路退回支付账户。到账时间受支付机构处理进度影响",
                source="平台退款规则"
            ),
            KnowledgeItem(
                knowledge_id="KNOWLEDGE_AFTER_SALE_003",
                title="退货商品寄回规则",
                content="退货申请审核通过后，请在售后页面规定的时间内寄回商品。退货商品应保持完好，逾期未寄回的申请可能自动关闭。",
                source="平台退货规则"
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
            content = str(raw.get("content") or "").strip()
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
