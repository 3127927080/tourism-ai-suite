from atguigu.agent.harness.skills.definition import SkillCode, SkillDefinition


class SkillCatalog:
    """集中保存 Agent 可以按需加载的领域技能。"""

    def __init__(
            self,
            definitions: tuple[SkillDefinition, ...]
    ):
        self.definitions = {
            definition.code: definition
            for definition in definitions
        }

    def get_skill(self, skill_code: SkillCode) -> SkillDefinition:
        return self.definitions[skill_code]

    def get_skills(self) -> tuple[SkillDefinition, ...]:
        return tuple(self.definitions.values())

    def render_index(self) -> str:
        """渲染供模型选择的精简技能索引"""
        return "\n".join(
            f"- {skill.code}: {skill.description}"
            for skill in self.definitions.values()
        )


SKILL_CATALOG = SkillCatalog(
    (
        SkillDefinition(
            code=SkillCode.TRAVEL_PRODUCT_SERVICE,
            description="旅游产品搜索、详情、价格、实时余量（房态/余票），覆盖酒店、景区门票、机票火车票汽车票、接送服务",
            guidance=(
                "查询入口：用户没有提供产品编号时，先使用 search_travel_products 搜索产品，可用 product_type 过滤 hotel/scenic/ticket/transfer。",
                "工具选择：产品基础信息使用 get_travel_product，实时余量/房态使用 get_product_availability。",
                "工具选择：产品用法、退改政策、目的地攻略使用 search_knowledge，query 必须写上已确认的城市或产品名。",
                "可信依据：产品信息和余量结论只能来自对应工具的成功结果。",
                "可信依据：旅游知识和攻略只能来自 search_knowledge 中来源为知识库文档或知识图谱的条目。",
                "边界处理：工具失败或没有结果时不得猜测产品信息。",
                "边界处理：search_knowledge 的 message 是可以转述给用户的提示，不是知识条目。城市或产品名无法确认时转述该 message。"
            ),
            tools=(
                "search_travel_products",
                "get_travel_product",
                "get_product_availability",
                "search_knowledge"
            )
        ),
        SkillDefinition(
            code=SkillCode.ORDER_SERVICE,
            description="订单列表、订单详情、订单状态、金额和订单页面引导",
            guidance=(
                "查询入口：用户没有提供订单编号时，先使用 list_orders 查询订单列表。",
                "工具选择：查询具体订单的状态、金额和产品信息时使用 get_order。",
                "可信依据：订单结论和具体订单页面动作必须来自 get_order 的成功结果。",
                "边界处理：只有用户明确要求页面引导或提出必须在页面完成的操作时才能生成订单页面动作；取消预订或修改出行人不能声称已经执行。"
            ),
            tools=("list_orders", "get_order")
        ),
        SkillDefinition(
            code=SkillCode.TRIP_SERVICE,
            description="具体订单的出行日期、入住/退房、出发/到达时间、班次、旅客信息和集合点",
            guidance=(
                "查询入口：用户没有提供订单编号时，先使用 list_orders 查询订单列表。",
                "工具选择：订单基础信息使用 get_order，出行安排和行程信息使用 get_trip_info。",
                "可信依据：出行日期、入住/退房、班次、旅客信息和集合点只能来自 get_trip_info 的成功结果。",
                "边界处理：出行信息工具失败或没有结果时不得猜测行程安排。"
            ),
            tools=("list_orders", "get_order", "get_trip_info")
        ),
        SkillDefinition(
            code=SkillCode.POLICY_SERVICE,
            description="退改政策、签证材料、出票时效及平台规则说明，不查询用户的实际退款记录",
            guidance=(
                "查询入口：用户询问平台规则、退改政策、签证材料或出票时效时必须先查询知识内容。",
                "工具选择：使用 search_knowledge 查询与用户问题直接相关的知识。",
                "可信依据：平台规则只能来自 search_knowledge 返回的平台规则条目。",
                "边界处理：引用规则时保留原文期限、条件与范围，不改写数字。",
                "边界处理：知识内容不用于判断用户实际退款申请状态。",
                "边界处理：search_knowledge 的 message 是知识提示。回答平台规则时不要转述，以结果列表中的平台规则条目为准。"
            ),
            tools=("search_knowledge",)
        ),
        SkillDefinition(
            code=SkillCode.REFUND_SERVICE,
            description="用户实际退款申请记录、处理状态、进度和退款页面引导，不解释平台通用规则",
            guidance=(
                "查询入口：用户询问实际退款进度时，使用 list_refunds 查询退款记录。",
                "工具选择：用户提供订单编号时按订单查询；生成具体订单页面动作前使用 get_order 确认订单。",
                "可信依据：退款状态和处理进度只能来自 list_refunds 的成功结果。",
                "边界处理：退款记录不用于解释平台通用退改规则。"
            ),
            tools=("get_order", "list_refunds")
        )
    )
)
