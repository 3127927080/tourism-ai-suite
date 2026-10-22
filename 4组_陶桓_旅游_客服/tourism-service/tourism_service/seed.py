from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from tourism_service.models import (
    Order,
    OrderItem,
    Refund,
    TravelProduct,
    TripInfo,
    User,
)


def _product(
    pid, name, ptype, city, price, stock, description, attributes
) -> TravelProduct:
    return TravelProduct(
        id=pid,
        name=name,
        type=ptype,
        city=city,
        price=Decimal(str(price)),
        stock=stock,
        description=description,
        attributes=attributes,
    )


def _products() -> list[TravelProduct]:
    items = [
        # ---------- 三亚 ----------
        _product(
            "HOTEL_SANYA_01", "三亚亚龙湾五星海景度假酒店", "hotel", "三亚", "1280", 12,
            "五星级海滨度假酒店，坐落亚龙湾，拥有私家沙滩与无边泳池，适合亲子与蜜月。",
            {"星级": "五星级", "房型": ["豪华海景大床房", "家庭套房"], "入住时间": "15:00",
             "退房时间": "12:00", "设施": ["无边泳池", "私家沙滩", "SPA"], "date_range": "全年"},
        ),
        _product(
            "HOTEL_SANYA_02", "三亚湾海景假日酒店", "hotel", "三亚", "680", 20,
            "四星级临海酒店，步行可达三亚湾沙滩，交通便利，性价比高。",
            {"星级": "四星级", "房型": ["高级双床房", "海景大床房"], "入住时间": "14:00",
             "退房时间": "12:00", "设施": ["游泳池", "健身房"], "date_range": "全年"},
        ),
        _product(
            "SCENIC_SANYA_01", "蜈支洲岛景区门票", "scenic", "三亚", "144", 500,
            "国家5A级海岛景区，海水清澈，适合潜水与水上项目。",
            {"评级": "5A", "票种": ["成人票", "儿童票"], "开放时间": "08:00-17:30"},
        ),
        _product(
            "SCENIC_SANYA_02", "南山文化旅游区门票", "scenic", "三亚", "129", 400,
            "国家5A级景区，以南山寺与108米海上观音闻名。",
            {"评级": "5A", "票种": ["成人票", "老人票"], "开放时间": "08:00-17:00"},
        ),
        _product(
            "TICKET_SANYA_01", "海口—三亚 环岛高铁票", "ticket", "三亚", "95", 300,
            "海南环岛高铁，海口东站至三亚站，约1.5小时。",
            {"车次": "C7301", "出发": "海口东 08:00", "到达": "三亚 09:30", "座位": "二等座"},
        ),
        _product(
            "TICKET_SANYA_02", "北京—三亚 机票", "ticket", "三亚", "1680", 60,
            "北京首都国际机场飞往三亚凤凰国际机场。",
            {"航班": "CA1375", "出发": "首都机场 09:20", "到达": "凤凰机场 13:10", "舱位": "经济舱"},
        ),
        _product(
            "TRANSFER_SANYA_01", "三亚凤凰机场接送机服务", "transfer", "三亚", "180", 50,
            "凤凰机场至三亚市区或亚龙湾，专车接送。",
            {"车型": "别克GL8", "载客数": 6, "区域": "三亚市区/亚龙湾"},
        ),
        # ---------- 云南 ----------
        _product(
            "HOTEL_YUNNAN_01", "丽江古城五星庭院酒店", "hotel", "云南", "980", 10,
            "五星级纳西风格庭院酒店，位于丽江古城内，可俯瞰古城全景。",
            {"星级": "五星级", "房型": ["庭院大床房", "古城景观套房"], "入住时间": "14:00",
             "退房时间": "12:00", "设施": ["庭院花园", "茶室", "餐厅"], "date_range": "全年"},
        ),
        _product(
            "HOTEL_YUNNAN_02", "昆明滇池温泉度假酒店", "hotel", "云南", "760", 16,
            "四星级温泉度假酒店，临近滇池，冬季可看海鸥。",
            {"星级": "四星级", "房型": ["温泉大床房", "湖景双床房"], "入住时间": "15:00",
             "退房时间": "12:00", "设施": ["温泉", "健身房"], "date_range": "全年"},
        ),
        _product(
            "SCENIC_YUNNAN_01", "玉龙雪山景区套票", "scenic", "云南", "230", 350,
            "国家5A级景区，含大索道，可登临冰川公园。",
            {"评级": "5A", "票种": ["成人套票", "儿童票"], "开放时间": "07:30-16:00"},
        ),
        _product(
            "SCENIC_YUNNAN_02", "丽江古城维护费", "scenic", "云南", "50", 999,
            "丽江古城景区维护费，进入古城需购。",
            {"评级": "5A", "票种": ["成人票"], "开放时间": "全天"},
        ),
        _product(
            "TICKET_YUNNAN_01", "昆明—丽江 高铁票", "ticket", "云南", "220", 260,
            "昆明站至丽江站动车，约3小时。",
            {"车次": "D8772", "出发": "昆明 09:00", "到达": "丽江 12:00", "座位": "二等座"},
        ),
        _product(
            "TICKET_YUNNAN_02", "成都—昆明 机票", "ticket", "云南", "890", 55,
            "成都双流机场飞往昆明长水机场。",
            {"航班": "MU5846", "出发": "双流机场 10:00", "到达": "长水机场 11:20", "舱位": "经济舱"},
        ),
        _product(
            "TRANSFER_YUNNAN_01", "丽江三义机场接送服务", "transfer", "云南", "160", 40,
            "三义机场至丽江古城/束河古镇专车接送。",
            {"车型": "大众帕萨特", "载客数": 4, "区域": "丽江古城/束河"},
        ),
        # ---------- 厦门 ----------
        _product(
            "HOTEL_XIAMEN_01", "厦门鼓浪屿五星海景酒店", "hotel", "厦门", "1180", 8,
            "五星级海景酒店，正对鼓浪屿，露台可看海上日出。",
            {"星级": "五星级", "房型": ["海景大床房", "行政套房"], "入住时间": "15:00",
             "退房时间": "12:00", "设施": ["海景餐厅", "无边泳池", "SPA"], "date_range": "全年"},
        ),
        _product(
            "HOTEL_XIAMEN_02", "厦门环岛路度假酒店", "hotel", "厦门", "620", 22,
            "四星级酒店，紧邻环岛路沙滩，适合骑行与亲海度假。",
            {"星级": "四星级", "房型": ["高级双床房", "海景房"], "入住时间": "14:00",
             "退房时间": "12:00", "设施": ["泳池", "自行车租赁"], "date_range": "全年"},
        ),
        _product(
            "SCENIC_XIAMEN_01", "鼓浪屿景区往返船票", "scenic", "厦门", "35", 800,
            "厦门邮轮中心至鼓浪屿三丘田码头往返船票。",
            {"评级": "5A", "票种": ["成人票", "儿童票"], "开放时间": "07:10-18:00"},
        ),
        _product(
            "SCENIC_XIAMEN_02", "厦门园林植物园门票", "scenic", "厦门", "30", 600,
            "国家4A级景区，多肉植物区与雨林世界广受欢迎。",
            {"评级": "4A", "票种": ["成人票", "学生票"], "开放时间": "06:30-18:00"},
        ),
        _product(
            "TICKET_XIAMEN_01", "福州—厦门 高铁票", "ticket", "厦门", "85", 320,
            "福州站至厦门北站动车，约1小时。",
            {"车次": "D6401", "出发": "福州 08:30", "到达": "厦门北 09:35", "座位": "二等座"},
        ),
        _product(
            "TICKET_XIAMEN_02", "上海—厦门 机票", "ticket", "厦门", "760", 58,
            "上海虹桥机场飞往厦门高崎机场。",
            {"航班": "MF8102", "出发": "虹桥机场 08:00", "到达": "高崎机场 09:55", "舱位": "经济舱"},
        ),
        _product(
            "TRANSFER_XIAMEN_01", "厦门高崎机场接送服务", "transfer", "厦门", "150", 45,
            "高崎机场至厦门市区/鼓浪屿码头专车接送。",
            {"车型": "本田奥德赛", "载客数": 6, "区域": "厦门市区/鼓浪屿码头"},
        ),
        # ---------- 张家界 ----------
        _product(
            "HOTEL_ZHANGJIAJIE_01", "张家界武陵源山景酒店", "hotel", "张家界", "560", 18,
            "四星级山景酒店，紧邻武陵源核心景区入口。",
            {"星级": "四星级", "房型": ["山景大床房", "家庭房"], "入住时间": "14:00",
             "退房时间": "12:00", "设施": ["餐厅", "观景台"], "date_range": "全年"},
        ),
        _product(
            "HOTEL_ZHANGJIAJIE_02", "张家界市区商务酒店", "hotel", "张家界", "320", 30,
            "三星级市区商务酒店，交通便利，靠近高铁站。",
            {"星级": "三星级", "房型": ["标准双床房", "商务大床房"], "入住时间": "14:00",
             "退房时间": "12:00", "设施": ["停车场", "早餐"], "date_range": "全年"},
        ),
        _product(
            "SCENIC_ZHANGJIAJIE_01", "张家界国家森林公园门票", "scenic", "张家界", "228", 450,
            "世界自然遗产，含袁家界、天子山等，阿凡达取景地。",
            {"评级": "5A", "票种": ["成人票", "优惠票"], "开放时间": "07:00-18:00"},
        ),
        _product(
            "SCENIC_ZHANGJIAJIE_02", "天门山景区门票", "scenic", "张家界", "258", 380,
            "国家5A级景区，含世界最长高山索道与玻璃栈道。",
            {"评级": "5A", "票种": ["成人票", "儿童票"], "开放时间": "08:00-18:00"},
        ),
        _product(
            "TICKET_ZHANGJIAJIE_01", "长沙—张家界 高铁票", "ticket", "张家界", "120", 280,
            "长沙南站至张家界西站动车，约2.5小时。",
            {"车次": "G6401", "出发": "长沙南 09:00", "到达": "张家界西 11:30", "座位": "二等座"},
        ),
        _product(
            "TICKET_ZHANGJIAJIE_02", "广州—张家界 机票", "ticket", "张家界", "690", 50,
            "广州白云机场飞往张家界荷花机场。",
            {"航班": "CZ3388", "出发": "白云机场 08:30", "到达": "荷花机场 10:10", "舱位": "经济舱"},
        ),
        _product(
            "TRANSFER_ZHANGJIAJIE_01", "张家界荷花机场接送服务", "transfer", "张家界", "140", 35,
            "荷花机场至武陵源/市区专车接送。",
            {"车型": "大众途观", "载客数": 4, "区域": "武陵源/市区"},
        ),
        # ---------- 成都 ----------
        _product(
            "HOTEL_CHENGDU_01", "成都春熙路五星酒店", "hotel", "成都", "880", 15,
            "五星级城市酒店，位于春熙路商圈，出行购物便利。",
            {"星级": "五星级", "房型": ["豪华大床房", "行政套房"], "入住时间": "14:00",
             "退房时间": "12:00", "设施": ["健身房", "行政酒廊", "餐厅"], "date_range": "全年"},
        ),
        _product(
            "HOTEL_CHENGDU_02", "成都宽窄巷子精品酒店", "hotel", "成都", "560", 12,
            "四星级川西院落风格精品酒店，步行可达宽窄巷子。",
            {"星级": "四星级", "房型": ["庭院房", "精品大床房"], "入住时间": "15:00",
             "退房时间": "12:00", "设施": ["庭院", "茶室"], "date_range": "全年"},
        ),
        _product(
            "SCENIC_CHENGDU_01", "成都大熊猫繁育研究基地门票", "scenic", "成都", "55", 700,
            "近距离观赏大熊猫，建议上午前往。",
            {"评级": "4A", "票种": ["成人票", "儿童票"], "开放时间": "07:30-18:00"},
        ),
        _product(
            "SCENIC_CHENGDU_02", "都江堰景区门票", "scenic", "成都", "80", 500,
            "世界文化遗产，古代水利工程典范。",
            {"评级": "5A", "票种": ["成人票", "学生票"], "开放时间": "08:00-18:00"},
        ),
        _product(
            "TICKET_CHENGDU_01", "重庆—成都 高铁票", "ticket", "成都", "96", 340,
            "重庆北站至成都东站高铁，约1.5小时。",
            {"车次": "G8501", "出发": "重庆北 08:00", "到达": "成都东 09:30", "座位": "二等座"},
        ),
        _product(
            "TICKET_CHENGDU_02", "北京—成都 机票", "ticket", "成都", "1180", 62,
            "北京首都机场飞往成都天府机场。",
            {"航班": "CA4102", "出发": "首都机场 07:30", "到达": "天府机场 10:40", "舱位": "经济舱"},
        ),
        _product(
            "TRANSFER_CHENGDU_01", "成都双流机场接送服务", "transfer", "成都", "160", 48,
            "双流机场至成都市区专车接送。",
            {"车型": "别克GL8", "载客数": 6, "区域": "成都市区"},
        ),
        # ---------- 杭州 ----------
        _product(
            "HOTEL_HANGZHOU_01", "杭州西湖五星湖景酒店", "hotel", "杭州", "990", 14,
            "五星级湖景酒店，步行可达西湖，房间可览湖光山色。",
            {"星级": "五星级", "房型": ["湖景大床房", "西湖景观套房"], "入住时间": "15:00",
             "退房时间": "12:00", "设施": ["湖景餐厅", "泳池", "SPA"], "date_range": "全年"},
        ),
        _product(
            "HOTEL_HANGZHOU_02", "杭州西溪湿地度假酒店", "hotel", "杭州", "680", 18,
            "四星级湿地度假酒店，环境清幽，适合休闲度假。",
            {"星级": "四星级", "房型": ["湿地景观房", "亲子房"], "入住时间": "14:00",
             "退房时间": "12:00", "设施": ["湿地步道", "餐厅"], "date_range": "全年"},
        ),
        _product(
            "SCENIC_HANGZHOU_01", "西湖游船票", "scenic", "杭州", "55", 900,
            "西湖手划船/画舫游览，含三潭印月等。",
            {"评级": "5A", "票种": ["成人票", "儿童票"], "开放时间": "08:00-17:00"},
        ),
        _product(
            "SCENIC_HANGZHOU_02", "灵隐寺飞来峰门票", "scenic", "杭州", "75", 650,
            "含灵隐寺与飞来峰景区，千年古刹。",
            {"评级": "5A", "票种": ["成人票", "学生票"], "开放时间": "07:00-18:00"},
        ),
        _product(
            "TICKET_HANGZHOU_01", "上海—杭州 高铁票", "ticket", "杭州", "73", 400,
            "上海虹桥站至杭州东站高铁，约1小时。",
            {"车次": "G7301", "出发": "虹桥 08:00", "到达": "杭州东 09:00", "座位": "二等座"},
        ),
        _product(
            "TICKET_HANGZHOU_02", "深圳—杭州 机票", "ticket", "杭州", "980", 60,
            "深圳宝安机场飞往杭州萧山机场。",
            {"航班": "CZ3567", "出发": "宝安机场 09:00", "到达": "萧山机场 11:10", "舱位": "经济舱"},
        ),
        _product(
            "TRANSFER_HANGZHOU_01", "杭州萧山机场接送服务", "transfer", "杭州", "170", 42,
            "萧山机场至杭州市区/西湖专车接送。",
            {"车型": "本田奥德赛", "载客数": 6, "区域": "杭州市区/西湖"},
        ),
    ]
    return items


def seed_demo_data(db: Session) -> None:
    if db.scalar(select(User.id).limit(1)):
        return
    users = [
        User(id="user_001", name="李明", membership="金牌会员"),
        User(id="user_002", name="王芳", membership="普通会员"),
        User(id="agent_001", name="客服小周", membership="员工"),
        User(id="admin_001", name="系统管理员", membership="员工"),
    ]
    db.add_all(users + _products())
    db.flush()

    orders = [
        Order(
            id="ORD_SANYA_001",
            user_id="user_001",
            status="upcoming",
            total_amount=Decimal("1424.00"),
            travel_start_date=date(2026, 11, 10),
            travel_end_date=date(2026, 11, 13),
            items=[
                OrderItem(product_id="HOTEL_SANYA_01", product_name="三亚亚龙湾五星海景度假酒店",
                          product_type="hotel", quantity=1, unit_price=Decimal("1280.00")),
                OrderItem(product_id="SCENIC_SANYA_01", product_name="蜈支洲岛景区门票",
                          product_type="scenic", quantity=1, unit_price=Decimal("144.00")),
            ],
            trip=TripInfo(
                travel_date=date(2026, 11, 10),
                checkin_date=date(2026, 11, 10),
                checkout_date=date(2026, 11, 13),
                traveler_info=[{"姓名": "李明", "证件类型": "身份证", "手机": "138****0001"}],
                meeting_point="三亚亚龙湾五星海景度假酒店大堂",
                status="upcoming",
                events=[
                    {"time": "2026-11-10 15:00", "description": "预计办理入住"},
                    {"time": "2026-11-11 09:00", "description": "蜈支洲岛一日游"},
                ],
            ),
        ),
        Order(
            id="ORD_YUNNAN_001",
            user_id="user_001",
            status="paid",
            total_amount=Decimal("1200.00"),
            travel_start_date=date(2026, 12, 5),
            travel_end_date=date(2026, 12, 8),
            items=[
                OrderItem(product_id="HOTEL_YUNNAN_01", product_name="丽江古城五星庭院酒店",
                          product_type="hotel", quantity=1, unit_price=Decimal("980.00")),
                OrderItem(product_id="TICKET_YUNNAN_01", product_name="昆明—丽江 高铁票",
                          product_type="ticket", quantity=1, unit_price=Decimal("220.00")),
            ],
            trip=TripInfo(
                travel_date=date(2026, 12, 5),
                checkin_date=date(2026, 12, 5),
                checkout_date=date(2026, 12, 8),
                departure_time="09:00",
                arrival_time="12:00",
                transport_no="D8772",
                traveler_info=[{"姓名": "李明", "证件类型": "身份证", "手机": "138****0001"}],
                meeting_point="昆明站 3 号候车厅",
                status="upcoming",
                events=[{"time": "2026-12-05 09:00", "description": "昆明站出发"}],
            ),
        ),
        Order(
            id="ORD_XIAMEN_001",
            user_id="user_001",
            status="completed",
            total_amount=Decimal("655.00"),
            travel_start_date=date(2026, 9, 1),
            travel_end_date=date(2026, 9, 3),
            items=[
                OrderItem(product_id="HOTEL_XIAMEN_02", product_name="厦门环岛路度假酒店",
                          product_type="hotel", quantity=1, unit_price=Decimal("620.00")),
                OrderItem(product_id="SCENIC_XIAMEN_01", product_name="鼓浪屿景区往返船票",
                          product_type="scenic", quantity=1, unit_price=Decimal("35.00")),
            ],
            trip=TripInfo(
                travel_date=date(2026, 9, 1),
                checkin_date=date(2026, 9, 1),
                checkout_date=date(2026, 9, 3),
                traveler_info=[{"姓名": "李明", "证件类型": "身份证", "手机": "138****0001"}],
                meeting_point="厦门邮轮中心码头",
                status="checked_out",
                events=[{"time": "2026-09-03 12:00", "description": "已退房离店"}],
            ),
        ),
        Order(
            id="ORD_CHENGDU_001",
            user_id="user_001",
            status="in_progress",
            total_amount=Decimal("960.00"),
            travel_start_date=date(2026, 10, 21),
            travel_end_date=date(2026, 10, 24),
            items=[
                OrderItem(product_id="HOTEL_CHENGDU_01", product_name="成都春熙路五星酒店",
                          product_type="hotel", quantity=1, unit_price=Decimal("880.00")),
                OrderItem(product_id="SCENIC_CHENGDU_01", product_name="成都大熊猫繁育研究基地门票",
                          product_type="scenic", quantity=1, unit_price=Decimal("55.00")),
            ],
            trip=TripInfo(
                travel_date=date(2026, 10, 21),
                checkin_date=date(2026, 10, 21),
                checkout_date=date(2026, 10, 24),
                traveler_info=[{"姓名": "李明", "证件类型": "身份证", "手机": "138****0001"}],
                meeting_point="成都春熙路五星酒店大堂",
                status="checked_in",
                events=[{"time": "2026-10-21 14:00", "description": "已办理入住"}],
            ),
        ),
        Order(
            id="ORD_HANGZHOU_001",
            user_id="user_001",
            status="refunding",
            total_amount=Decimal("990.00"),
            travel_start_date=date(2026, 11, 20),
            travel_end_date=date(2026, 11, 22),
            items=[
                OrderItem(product_id="HOTEL_HANGZHOU_01", product_name="杭州西湖五星湖景酒店",
                          product_type="hotel", quantity=1, unit_price=Decimal("990.00")),
            ],
            trip=TripInfo(
                travel_date=date(2026, 11, 20),
                checkin_date=date(2026, 11, 20),
                checkout_date=date(2026, 11, 22),
                traveler_info=[{"姓名": "李明", "证件类型": "身份证", "手机": "138****0001"}],
                meeting_point="杭州西湖五星湖景酒店大堂",
                status="upcoming",
                events=[],
            ),
        ),
        Order(
            id="ORD_SANYA_002",
            user_id="user_002",
            status="upcoming",
            total_amount=Decimal(144),
            travel_start_date=date(2026, 11, 15),
            travel_end_date=date(2026, 11, 16),
            items=[
                OrderItem(product_id="SCENIC_SANYA_02", product_name="南山文化旅游区门票",
                          product_type="scenic", quantity=1, unit_price=Decimal("129.00")),
            ],
            trip=TripInfo(
                travel_date=date(2026, 11, 15),
                traveler_info=[{"姓名": "王芳", "证件类型": "身份证", "手机": "139****0002"}],
                meeting_point="南山文化旅游区入口",
                status="upcoming",
                events=[],
            ),
        ),
        Order(
            id="ORD_ZHANGJIAJIE_001",
            user_id="user_002",
            status="cancelled",
            total_amount=Decimal(228),
            travel_start_date=date(2026, 8, 10),
            travel_end_date=date(2026, 8, 12),
            items=[
                OrderItem(product_id="SCENIC_ZHANGJIAJIE_01", product_name="张家界国家森林公园门票",
                          product_type="scenic", quantity=1, unit_price=Decimal("228.00")),
            ],
        ),
    ]
    db.add_all(orders)
    db.flush()

    refunds = [
        Refund(
            id="REFUND_HANGZHOU_001",
            user_id="user_001",
            order_id="ORD_HANGZHOU_001",
            kind="full",
            reason="行程有变，需要全额退款",
            status="pending",
            amount=Decimal("990.00"),
        ),
        Refund(
            id="REFUND_XIAMEN_001",
            user_id="user_001",
            order_id="ORD_XIAMEN_001",
            kind="partial",
            reason="部分服务未使用申请部分退款",
            status="refunded",
            amount=Decimal("35.00"),
        ),
    ]
    db.add_all(refunds)
    db.commit()
